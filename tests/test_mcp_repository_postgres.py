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
from app.mcp.catalog import McpDiscoveredTool


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
          %s, %s, %s, 'test tool', 'streamable_http', 'https://mcp.example.test/v1', 'none',
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
                endpoint="https://mcp.example.test/v1",
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
