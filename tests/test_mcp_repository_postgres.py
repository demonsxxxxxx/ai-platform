from contextlib import asynccontextmanager
import os
from pathlib import Path
from types import SimpleNamespace
import uuid

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
import pytest

from app import repositories
from app.mcp import repository as mcp_repository
from app.mcp.catalog import (
    MCP_TOOL_ANNOTATION_READ_ONLY,
    MCP_TOOL_ANNOTATION_UNKNOWN,
    MCP_TOOL_ANNOTATION_WRITE_CAPABLE,
    McpDiscoveredTool,
)


POSTGRES_DSN_ENV = "AI_PLATFORM_MCP_CATALOG_TEST_DSN"


def _postgres_dsn() -> str:
    dsn = os.getenv(POSTGRES_DSN_ENV, "").strip()
    if not dsn:
        pytest.skip(f"{POSTGRES_DSN_ENV} is not configured")
    return dsn


async def _set_search_path(conn: psycopg.AsyncConnection, schema_name: str) -> None:
    await conn.execute(sql.SQL("set search_path to {}").format(sql.Identifier(schema_name)))


async def _insert_server(
    conn: psycopg.AsyncConnection,
    *,
    tenant_id: str,
    name: str,
    generation: int,
    attempt: int = 0,
    catalog_status: str = "available",
    expired_lease: bool = False,
) -> None:
    lease_sql = "now() - interval '1 second'" if expired_lease else "now() + interval '5 minutes'"
    await conn.execute(
        f"""
        insert into mcp_servers(
          id, tenant_id, name, transport, status, credential_state,
          catalog_generation, catalog_sync_attempt, catalog_status,
          catalog_sync_lease_expires_at
        ) values (%s, %s, %s, 'streamable_http', 'active', 'not_configured', %s, %s, %s, {lease_sql})
        """,
        (f"server-{tenant_id}-{name}", tenant_id, name, generation, attempt, catalog_status),
    )


async def _insert_tool(
    conn: psycopg.AsyncConnection,
    *,
    tenant_id: str,
    tool_id: str,
    server_name: str,
    catalog_generation: int | None = None,
) -> None:
    await conn.execute(
        """
        insert into mcp_tools(
          id, server_id, name, description, transport_type, endpoint, auth_mode,
          allowed_tools, status, write_capable, risk_level, visible_to_user
        ) values (
          %s, %s, %s, 'test tool', 'streamable_http', '', 'none',
          '["query"]'::jsonb, 'active', false, 'low', true
        )
        """,
        (tool_id, server_name, tool_id),
    )
    await conn.execute(
        """
        insert into tool_policies(
          tenant_id, tool_id, status, write_capable, risk_level, visible_to_user, reason, updated_by
        ) values (%s, %s, 'active', false, 'low', true, 'test', %s)
        """,
        (tenant_id, tool_id, f"admin-{tenant_id}"),
    )
    if catalog_generation is not None:
        await conn.execute(
            """
            insert into mcp_tool_catalog_entries(
              tool_id, tenant_id, server_name, remote_tool_name, catalog_generation, schema_hash, status
            ) values (%s, %s, %s, 'query', %s, 'schema-hash', 'active')
            """,
            (tool_id, tenant_id, server_name, catalog_generation),
        )


@pytest.mark.asyncio
async def test_mcp_catalog_postgres_expiry_tenant_scope_and_rollback(monkeypatch):
    """Exercise the lease fence and catalog authority with isolated real PostgreSQL connections."""

    dsn = _postgres_dsn()
    schema_name = f"mcp_catalog_test_{uuid.uuid4().hex}"
    schema_sql = Path("app/schema.sql").read_text(encoding="utf-8")
    admin_conn = await psycopg.AsyncConnection.connect(dsn, autocommit=True, row_factory=dict_row)
    first_conn = None
    second_conn = None
    try:
        await admin_conn.execute(sql.SQL("create schema {}").format(sql.Identifier(schema_name)))
        await _set_search_path(admin_conn, schema_name)
        await admin_conn.execute(schema_sql)

        for tenant_id in ("tenant-a", "tenant-b"):
            await admin_conn.execute("insert into tenants(id, name) values (%s, %s)", (tenant_id, tenant_id))
            await admin_conn.execute(
                "insert into users(id, tenant_id, display_name) values (%s, %s, %s)",
                (f"admin-{tenant_id}", tenant_id, f"admin-{tenant_id}"),
            )

        await _insert_server(
            admin_conn,
            tenant_id="tenant-a",
            name="lease-server",
            generation=7,
            attempt=2,
            catalog_status="syncing",
            expired_lease=True,
        )
        await _insert_server(admin_conn, tenant_id="tenant-a", name="shared-server", generation=3)
        await _insert_server(admin_conn, tenant_id="tenant-b", name="shared-server", generation=3)
        await _insert_server(admin_conn, tenant_id="tenant-a", name="legacy-server", generation=3)
        await _insert_tool(
            admin_conn,
            tenant_id="tenant-b",
            tool_id="mcpt-tenant-b",
            server_name="shared-server",
            catalog_generation=3,
        )
        await _insert_tool(
            admin_conn,
            tenant_id="tenant-a",
            tool_id="legacy-untrusted",
            server_name="legacy-server",
        )

        first_conn = await psycopg.AsyncConnection.connect(dsn, row_factory=dict_row)
        second_conn = await psycopg.AsyncConnection.connect(dsn, row_factory=dict_row)
        await _set_search_path(first_conn, schema_name)
        await _set_search_path(second_conn, schema_name)
        await first_conn.commit()
        await second_conn.commit()
        first_pid = int((await (await first_conn.execute("select pg_backend_pid() as pid")).fetchone())["pid"])
        second_pid = int((await (await second_conn.execute("select pg_backend_pid() as pid")).fetchone())["pid"])
        await first_conn.commit()
        assert first_pid != second_pid

        async with first_conn.transaction():
            expired = await mcp_repository.record_mcp_catalog_sync_outcome(
                first_conn,
                tenant_id="tenant-a",
                server_name="lease-server",
                observed_generation=7,
                observed_attempt=2,
                reason="transport_failure",
                actor_id="admin-tenant-a",
            )
        assert expired["catalog_unavailable_reason"] == "stale_generation"

        lease_state = await (
            await admin_conn.execute(
                """
                select catalog_sync_attempt, catalog_status, catalog_unavailable_reason
                from mcp_servers
                where tenant_id = 'tenant-a' and name = 'lease-server'
                """
            )
        ).fetchone()
        assert lease_state == {
            "catalog_sync_attempt": 2,
            "catalog_status": "syncing",
            "catalog_unavailable_reason": "",
        }

        async with second_conn.transaction():
            takeover = await mcp_repository.begin_mcp_catalog_sync(
                second_conn,
                tenant_id="tenant-a",
                server_name="lease-server",
                observed_generation=7,
                actor_id="admin-tenant-a",
            )
        assert takeover["started"] is True
        assert takeover["catalog_sync_attempt"] == 3

        async with first_conn.transaction():
            stale_publication = await mcp_repository.publish_mcp_tool_catalog(
                first_conn,
                tenant_id="tenant-a",
                server_name="lease-server",
                observed_generation=7,
                observed_attempt=2,
                tools=(McpDiscoveredTool("stale_tool", "stale-schema", True),),
                actor_id="admin-tenant-a",
            )
        assert stale_publication["catalog_unavailable_reason"] == "stale_generation"
        stale_entries = await (
            await admin_conn.execute(
                "select count(*) as count from mcp_tool_catalog_entries where remote_tool_name = 'stale_tool'"
            )
        ).fetchone()
        assert stale_entries == {"count": 0}

        assert await mcp_repository.get_mcp_tool_registry_entry(
            first_conn,
            tenant_id="tenant-a",
            tool_id="mcpt-tenant-b",
        ) is None
        assert await mcp_repository.get_mcp_tool_registry_entry(
            first_conn,
            tenant_id="tenant-a",
            tool_id="legacy-untrusted",
        ) is None
        assert await mcp_repository.list_workbench_mcp_tools(first_conn, tenant_id="tenant-a") == []
        assert await repositories.list_chat_mcp_tool_catalog_entries(first_conn, tenant_id="tenant-a") == []
        assert await repositories.list_admin_tool_policies(first_conn, tenant_id="tenant-a") == []
        with pytest.raises(repositories.RepositoryNotFoundError, match="mcp_tool_not_found"):
            await repositories.upsert_admin_tool_policy(
                first_conn,
                tenant_id="tenant-a",
                tool_id="mcpt-tenant-b",
                status="active",
                risk_level="low",
                write_capable=False,
                visible_to_user=True,
                reason="foreign opaque id",
                updated_by="admin-tenant-a",
            )
        with pytest.raises(repositories.RepositoryNotFoundError, match="mcp_tool_not_found"):
            await repositories.upsert_admin_tool_policy(
                first_conn,
                tenant_id="tenant-a",
                tool_id="legacy-untrusted",
                status="active",
                risk_level="low",
                write_capable=False,
                visible_to_user=True,
                reason="unknown legacy row",
                updated_by="admin-tenant-a",
            )
        await first_conn.commit()

        await admin_conn.execute(
            """
            create function reject_mcp_catalog_finalization()
            returns trigger language plpgsql as $$
            begin
              if old.catalog_status = 'syncing' and new.catalog_status <> 'syncing' then
                return null;
              end if;
              return new;
            end
            $$;
            """
        )
        await admin_conn.execute(
            """
            create trigger reject_mcp_catalog_finalization_trigger
            before update on mcp_servers
            for each row execute function reject_mcp_catalog_finalization();
            """
        )

        @asynccontextmanager
        async def first_connection_transaction():
            async with first_conn.transaction():
                yield first_conn

        monkeypatch.setattr(mcp_repository, "transaction", first_connection_transaction)
        rollback_result = await mcp_repository.PostgresMcpCatalogStore().publish(
            SimpleNamespace(
                tenant_id="tenant-a",
                server_name="lease-server",
                observed_generation=7,
                actor_id="admin-tenant-a",
                endpoint="https://mcp.example.test/v1",
            ),
            observed_attempt=3,
            tools=(McpDiscoveredTool("rollback_tool", "rollback-schema", True),),
        )
        assert rollback_result["catalog_unavailable_reason"] == "stale_generation"
        assert rollback_result["published"] is False
        rollback_entries = await (
            await admin_conn.execute(
                """
                select count(*) as count
                from mcp_tool_catalog_entries
                where tenant_id = 'tenant-a'
                  and server_name = 'lease-server'
                  and remote_tool_name = 'rollback_tool'
                """
            )
        ).fetchone()
        assert rollback_entries == {"count": 0}
    finally:
        for conn in (first_conn, second_conn):
            if conn is not None:
                await conn.rollback()
                await conn.close()
        await admin_conn.execute(sql.SQL("drop schema if exists {} cascade").format(sql.Identifier(schema_name)))
        await admin_conn.close()


@pytest.mark.asyncio
async def test_mcp_catalog_postgres_preserves_annotation_classification_distribution_and_admin_policy():
    """Publish exact annotation classifications without bypassing Chat distribution or an admin policy."""

    dsn = _postgres_dsn()
    schema_name = f"mcp_catalog_annotations_{uuid.uuid4().hex}"
    schema_sql = Path("app/schema.sql").read_text(encoding="utf-8")
    admin_conn = await psycopg.AsyncConnection.connect(dsn, autocommit=True, row_factory=dict_row)
    catalog_conn = None
    try:
        await admin_conn.execute(sql.SQL("create schema {}").format(sql.Identifier(schema_name)))
        await _set_search_path(admin_conn, schema_name)
        await admin_conn.execute(schema_sql)
        await admin_conn.execute("insert into tenants(id, name) values ('tenant-a', 'tenant-a')")
        await admin_conn.execute(
            "insert into users(id, tenant_id, display_name) values ('admin-a', 'tenant-a', 'admin-a')"
        )
        await _insert_server(
            admin_conn,
            tenant_id="tenant-a",
            name="compatible-server",
            generation=9,
            attempt=1,
            catalog_status="syncing",
        )

        catalog_conn = await psycopg.AsyncConnection.connect(dsn, row_factory=dict_row)
        await _set_search_path(catalog_conn, schema_name)
        await catalog_conn.commit()
        tools = (
            McpDiscoveredTool("read_tool", "schema-read", True, MCP_TOOL_ANNOTATION_READ_ONLY),
            McpDiscoveredTool("write_tool", "schema-write", False, MCP_TOOL_ANNOTATION_WRITE_CAPABLE),
            McpDiscoveredTool("unknown_tool", "schema-unknown", False, MCP_TOOL_ANNOTATION_UNKNOWN),
        )
        async with catalog_conn.transaction():
            published = await mcp_repository.publish_mcp_tool_catalog(
                catalog_conn,
                tenant_id="tenant-a",
                server_name="compatible-server",
                observed_generation=9,
                observed_attempt=1,
                tools=tools,
                actor_id="admin-a",
            )
            await repositories.upsert_capability_distribution_row(
                catalog_conn,
                tenant_id="tenant-a",
                capability_kind="mcp_server",
                capability_id="compatible-server",
                status="active",
                visible_to_user=True,
                scope_mode="allowlist",
                department_ids=["qa"],
                allowed_roles=["user"],
                metadata_json={},
                updated_by="admin-a",
            )
            authorized = await mcp_repository.list_authorized_chat_mcp_tools(
                catalog_conn,
                tenant_id="tenant-a",
                principal_department_id="qa",
                principal_roles=["user"],
                is_admin=False,
                permissions=[],
            )
            unauthorized = await mcp_repository.list_authorized_chat_mcp_tools(
                catalog_conn,
                tenant_id="tenant-a",
                principal_department_id="rd",
                principal_roles=["user"],
                is_admin=False,
                permissions=[],
            )

        assert published["catalog_status"] == "available"
        assert published["catalog_selectable_count"] == 3
        assert {tool["allowed_tools"][0] for tool in authorized} == {"read_tool", "write_tool", "unknown_tool"}
        assert unauthorized == []

        classifications = await (
            await catalog_conn.execute(
                """
                select entries.remote_tool_name, tools.write_capable, tools.risk_level,
                  policies.status as policy_status, policies.reason as policy_reason
                from mcp_tool_catalog_entries entries
                join mcp_tools tools on tools.id = entries.tool_id
                join tool_policies policies
                  on policies.tenant_id = entries.tenant_id
                 and policies.tool_id = entries.tool_id
                where entries.tenant_id = 'tenant-a'
                  and entries.server_name = 'compatible-server'
                order by entries.remote_tool_name asc
                """
            )
        ).fetchall()
        assert [dict(row) for row in classifications] == [
            {
                "remote_tool_name": "read_tool",
                "write_capable": False,
                "risk_level": "low",
                "policy_status": "active",
                "policy_reason": "mcp_catalog_read_only",
            },
            {
                "remote_tool_name": "unknown_tool",
                "write_capable": True,
                "risk_level": "high",
                "policy_status": "active",
                "policy_reason": "mcp_catalog_annotation_unknown",
            },
            {
                "remote_tool_name": "write_tool",
                "write_capable": True,
                "risk_level": "high",
                "policy_status": "active",
                "policy_reason": "mcp_catalog_write_capable",
            },
        ]
        await catalog_conn.commit()

        async with catalog_conn.transaction():
            await catalog_conn.execute(
                """
                update mcp_servers
                set catalog_sync_attempt = 2,
                    catalog_status = 'syncing',
                    catalog_sync_lease_expires_at = now() + interval '5 minutes'
                where tenant_id = 'tenant-a' and name = 'compatible-server'
                """
            )
            await catalog_conn.execute(
                """
                update tool_policies
                set status = 'disabled', visible_to_user = false, reason = 'admin_owned_policy'
                where tenant_id = 'tenant-a'
                  and tool_id = (
                    select tool_id
                    from mcp_tool_catalog_entries
                    where tenant_id = 'tenant-a'
                      and server_name = 'compatible-server'
                      and remote_tool_name = 'unknown_tool'
                  )
                """
            )
            republished = await mcp_repository.publish_mcp_tool_catalog(
                catalog_conn,
                tenant_id="tenant-a",
                server_name="compatible-server",
                observed_generation=9,
                observed_attempt=2,
                tools=(tools[2],),
                actor_id="admin-a",
            )
            policy = await (
                await catalog_conn.execute(
                    """
                    select status, visible_to_user, reason
                    from tool_policies
                    where tenant_id = 'tenant-a'
                      and tool_id = (
                        select tool_id
                        from mcp_tool_catalog_entries
                        where tenant_id = 'tenant-a'
                          and server_name = 'compatible-server'
                          and remote_tool_name = 'unknown_tool'
                      )
                    """
                )
            ).fetchone()

        assert republished["catalog_status"] == "available"
        assert republished["published"] is True
        assert republished["catalog_revision"] == published["catalog_revision"] + 1
        assert policy == {
            "status": "disabled",
            "visible_to_user": False,
            "reason": "admin_owned_policy",
        }

        async with catalog_conn.transaction():
            await catalog_conn.execute(
                """
                update mcp_servers
                set catalog_sync_attempt = 3,
                    catalog_status = 'syncing',
                    catalog_sync_lease_expires_at = now() + interval '5 minutes'
                where tenant_id = 'tenant-a' and name = 'compatible-server'
                """
            )
            stable_republish = await mcp_repository.publish_mcp_tool_catalog(
                catalog_conn,
                tenant_id="tenant-a",
                server_name="compatible-server",
                observed_generation=9,
                observed_attempt=3,
                tools=(tools[2],),
                actor_id="admin-a",
            )

        assert stable_republish["catalog_revision"] == republished["catalog_revision"]
        assert stable_republish["published"] is False
    finally:
        if catalog_conn is not None:
            await catalog_conn.rollback()
            await catalog_conn.close()
        await admin_conn.execute(sql.SQL("drop schema if exists {} cascade").format(sql.Identifier(schema_name)))
        await admin_conn.close()
