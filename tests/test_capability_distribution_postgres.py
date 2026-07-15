import asyncio
import os
from pathlib import Path
import uuid

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
import pytest

from app import repositories


POSTGRES_DSN_ENV = "AI_PLATFORM_CAPABILITY_DISTRIBUTION_TEST_DSN"


def _postgres_dsn() -> str:
    dsn = os.getenv(POSTGRES_DSN_ENV, "").strip()
    if not dsn:
        pytest.skip(f"{POSTGRES_DSN_ENV} is not configured")
    return dsn


async def _set_search_path(conn: psycopg.AsyncConnection, schema_name: str) -> None:
    await conn.execute(sql.SQL("set search_path to {}").format(sql.Identifier(schema_name)))


async def _wait_for_lock_blocker(
    conn: psycopg.AsyncConnection,
    *,
    waiter_pid: int,
    blocker_pid: int,
    timeout_seconds: float = 2.0,
) -> None:
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while True:
        cursor = await conn.execute(
            """
            select
              state,
              wait_event_type,
              %s = any(pg_blocking_pids(pid)) as blocked_by_expected_backend
            from pg_stat_activity
            where pid = %s
            """,
            (blocker_pid, waiter_pid),
        )
        row = await cursor.fetchone()
        if (
            row is not None
            and row["state"] == "active"
            and row["wait_event_type"] == "Lock"
            and row["blocked_by_expected_backend"] is True
        ):
            return
        if asyncio.get_running_loop().time() >= deadline:
            raise AssertionError("second backfill did not enter the expected marker lock wait")
        await asyncio.sleep(0.02)


@pytest.mark.asyncio
async def test_capability_distribution_schema_backfill_and_completed_marker_concurrency():
    dsn = _postgres_dsn()
    schema_name = f"capdist_test_{uuid.uuid4().hex}"
    schema_sql = Path("app/schema.sql").read_text(encoding="utf-8")
    admin_conn = await psycopg.AsyncConnection.connect(
        dsn,
        autocommit=True,
        row_factory=dict_row,
    )
    try:
        await admin_conn.execute(sql.SQL("create schema {}").format(sql.Identifier(schema_name)))
        await _set_search_path(admin_conn, schema_name)
        await admin_conn.execute(schema_sql)
        await admin_conn.execute(schema_sql)

        constraint_cursor = await admin_conn.execute(
            """
            select conname, convalidated
            from pg_constraint
            where conname in (
                'tenant_capability_distributions_allowed_roles_array',
                'tenant_capability_distributions_allowed_roles_strings',
                'tenant_capability_distributions_department_ids_nonblank'
            )
              and conrelid = to_regclass(%s)
            order by conname
            """,
            (f"{schema_name}.tenant_capability_distributions",),
        )
        constraints = await constraint_cursor.fetchall()
        assert constraints == [
            {
                "conname": "tenant_capability_distributions_allowed_roles_array",
                "convalidated": True,
            },
            {
                "conname": "tenant_capability_distributions_allowed_roles_strings",
                "convalidated": True,
            },
            {
                "conname": "tenant_capability_distributions_department_ids_nonblank",
                "convalidated": True,
            },
        ]

        with pytest.raises(psycopg.errors.CheckViolation):
            await admin_conn.execute(
                """
                insert into tenant_capability_distributions(
                  id, tenant_id, capability_kind, capability_id, allowed_roles
                ) values (%s, %s, 'mcp_server', %s, '{}'::jsonb)
                """,
                ("capdist-malformed", "default", "malformed-authority"),
            )
        with pytest.raises(psycopg.errors.CheckViolation):
            await admin_conn.execute(
                """
                insert into tenant_capability_distributions(
                  id, tenant_id, capability_kind, capability_id, allowed_roles
                ) values (%s, %s, 'mcp_server', %s, '[1,"qa"]'::jsonb)
                """,
                ("capdist-malformed-array", "default", "malformed-array-authority"),
            )
        with pytest.raises(psycopg.errors.CheckViolation):
            await admin_conn.execute(
                """
                insert into tenant_capability_distributions(
                  id, tenant_id, capability_kind, capability_id, allowed_roles
                ) values (%s, %s, 'mcp_server', %s, '[""]'::jsonb)
                """,
                ("capdist-empty-role", "default", "empty-role-authority"),
            )
        with pytest.raises(psycopg.errors.CheckViolation):
            await admin_conn.execute(
                """
                insert into tenant_capability_distributions(
                  id, tenant_id, capability_kind, capability_id, allowed_roles
                ) values (%s, %s, 'mcp_server', %s, '["   "]'::jsonb)
                """,
                ("capdist-blank-role", "default", "blank-role-authority"),
            )
        with pytest.raises(psycopg.errors.CheckViolation):
            await admin_conn.execute(
                """
                insert into tenant_capability_distributions(
                  id, tenant_id, capability_kind, capability_id, department_ids
                ) values (%s, %s, 'mcp_server', %s, array['QA', '   ']::text[])
                """,
                ("capdist-blank-department", "default", "blank-department-authority"),
            )
        with pytest.raises(psycopg.errors.CheckViolation):
            await admin_conn.execute(
                """
                insert into tenant_capability_distributions(
                  id, tenant_id, capability_kind, capability_id, department_ids
                ) values (%s, %s, 'mcp_server', %s, array['QA', null]::text[])
                """,
                ("capdist-null-department", "default", "null-department-authority"),
            )

        tenant_id = f"tenant-{uuid.uuid4().hex}"
        await admin_conn.execute(
            "insert into tenants(id, name) values (%s, %s)",
            (tenant_id, "Capability Distribution Test"),
        )
        await admin_conn.execute(
            """
            insert into mcp_servers(
              tenant_id, name, status, allowed_roles, department_ids
            ) values (%s, %s, 'active', '[1,"QA-Operator"]'::jsonb, array['QA']::text[])
            """,
            (tenant_id, "malformed-legacy-mcp"),
        )
        await admin_conn.execute(
            """
            insert into mcp_servers(
              tenant_id, name, status, allowed_roles, department_ids
            ) values (
              %s, %s, 'active', '[" QA-Operator ","qa-operator","Reviewer"]'::jsonb,
              array['QA']::text[]
            )
            """,
            (tenant_id, "normalized-legacy-mcp"),
        )
        await admin_conn.execute(
            """
            insert into mcp_servers(
              tenant_id, name, status, allowed_roles, department_ids
            ) values (
              %s, %s, 'active', '["Reviewer"]'::jsonb,
              array['QA', '   ']::text[]
            )
            """,
            (tenant_id, "malformed-department-legacy-mcp"),
        )

        await repositories.ensure_tenant_capability_distribution_backfill(
            admin_conn,
            tenant_id=tenant_id,
        )
        await repositories.ensure_tenant_capability_distribution_backfill(
            admin_conn,
            tenant_id=tenant_id,
        )
        distribution_cursor = await admin_conn.execute(
            """
            select status, department_ids, allowed_roles, metadata_json
            from tenant_capability_distributions
            where tenant_id = %s
              and capability_kind = 'mcp_server'
              and capability_id = 'malformed-legacy-mcp'
            """,
            (tenant_id,),
        )
        distribution = await distribution_cursor.fetchone()
        assert distribution == {
            "status": "disabled",
            "department_ids": ["QA"],
            "allowed_roles": [],
            "metadata_json": {
                "legacy_source": "mcp_servers",
                "legacy_scope_invalid": True,
            },
        }
        normalized_cursor = await admin_conn.execute(
            """
            select status, department_ids, allowed_roles, metadata_json
            from tenant_capability_distributions
            where tenant_id = %s
              and capability_kind = 'mcp_server'
              and capability_id = 'normalized-legacy-mcp'
            """,
            (tenant_id,),
        )
        normalized = await normalized_cursor.fetchone()
        assert normalized == {
            "status": "active",
            "department_ids": ["QA"],
            "allowed_roles": ["qa-operator", "reviewer"],
            "metadata_json": {
                "legacy_source": "mcp_servers",
                "legacy_scope_invalid": False,
            },
        }
        malformed_department_cursor = await admin_conn.execute(
            """
            select status, department_ids, allowed_roles, metadata_json
            from tenant_capability_distributions
            where tenant_id = %s
              and capability_kind = 'mcp_server'
              and capability_id = 'malformed-department-legacy-mcp'
            """,
            (tenant_id,),
        )
        malformed_department = await malformed_department_cursor.fetchone()
        assert malformed_department == {
            "status": "disabled",
            "department_ids": [],
            "allowed_roles": ["reviewer"],
            "metadata_json": {
                "legacy_source": "mcp_servers",
                "legacy_scope_invalid": True,
            },
        }
        count_cursor = await admin_conn.execute(
            """
            select count(*) as count
            from tenant_capability_distributions
            where tenant_id = %s
              and capability_kind = 'mcp_server'
              and capability_id = 'malformed-legacy-mcp'
            """,
            (tenant_id,),
        )
        assert await count_cursor.fetchone() == {"count": 1}

        concurrent_tenant_id = f"tenant-concurrent-{uuid.uuid4().hex}"
        await admin_conn.execute(
            "insert into tenants(id, name) values (%s, %s)",
            (concurrent_tenant_id, "Capability Distribution Concurrency Test"),
        )
        await admin_conn.execute(
            """
            insert into mcp_servers(
              tenant_id, name, status, allowed_roles, department_ids
            ) values (%s, %s, 'active', '["Reviewer"]'::jsonb, array['QA']::text[])
            """,
            (concurrent_tenant_id, "concurrent-legacy-mcp"),
        )
        await admin_conn.execute(
            """
            insert into tenant_capability_distribution_backfills(tenant_id, completed_at)
            values (%s, null)
            """,
            (concurrent_tenant_id,),
        )

        first_conn = await psycopg.AsyncConnection.connect(dsn, row_factory=dict_row)
        second_conn = await psycopg.AsyncConnection.connect(dsn, row_factory=dict_row)
        second_task = None
        try:
            await _set_search_path(first_conn, schema_name)
            await first_conn.execute("set local statement_timeout = '5s'")
            first_pid_cursor = await first_conn.execute("select pg_backend_pid() as pid")
            first_pid = int((await first_pid_cursor.fetchone())["pid"])
            await repositories.ensure_tenant_capability_distribution_backfill(
                first_conn,
                tenant_id=concurrent_tenant_id,
            )
            await _set_search_path(second_conn, schema_name)
            await second_conn.execute("set local statement_timeout = '5s'")
            second_pid_cursor = await second_conn.execute("select pg_backend_pid() as pid")
            second_pid = int((await second_pid_cursor.fetchone())["pid"])
            second_task = asyncio.create_task(
                repositories.ensure_tenant_capability_distribution_backfill(
                    second_conn,
                    tenant_id=concurrent_tenant_id,
                )
            )
            await _wait_for_lock_blocker(
                admin_conn,
                waiter_pid=second_pid,
                blocker_pid=first_pid,
            )
            assert not second_task.done()
            await admin_conn.execute(
                """
                insert into mcp_servers(
                  tenant_id, name, status, allowed_roles, department_ids
                ) values (%s, %s, 'active', '["Reviewer"]'::jsonb, array['QA']::text[])
                """,
                (concurrent_tenant_id, "late-legacy-mcp"),
            )

            await first_conn.commit()
            await asyncio.wait_for(second_task, timeout=2.0)
            await second_conn.commit()

            concurrent_distribution_cursor = await admin_conn.execute(
                """
                select capability_id
                from tenant_capability_distributions
                where tenant_id = %s
                  and capability_kind = 'mcp_server'
                  and capability_id in ('concurrent-legacy-mcp', 'late-legacy-mcp')
                order by capability_id
                """,
                (concurrent_tenant_id,),
            )
            assert await concurrent_distribution_cursor.fetchall() == [
                {"capability_id": "concurrent-legacy-mcp"}
            ]
            concurrent_marker_cursor = await admin_conn.execute(
                """
                select completed_at is not null as completed
                from tenant_capability_distribution_backfills
                where tenant_id = %s
                """,
                (concurrent_tenant_id,),
            )
            assert await concurrent_marker_cursor.fetchone() == {"completed": True}
        finally:
            if second_task is not None and not second_task.done():
                second_task.cancel()
                try:
                    await second_task
                except asyncio.CancelledError:
                    pass
            await first_conn.rollback()
            await second_conn.rollback()
            await first_conn.close()
            await second_conn.close()
    finally:
        await admin_conn.execute(sql.SQL("drop schema if exists {} cascade").format(sql.Identifier(schema_name)))
        await admin_conn.close()


@pytest.mark.asyncio
async def test_capability_distribution_lifecycle_lock_serializes_missing_row_archive(monkeypatch):
    dsn = _postgres_dsn()
    schema_name = f"capdist_lifecycle_lock_{uuid.uuid4().hex}"
    schema_sql = Path("app/schema.sql").read_text(encoding="utf-8")
    admin_conn = await psycopg.AsyncConnection.connect(dsn, autocommit=True, row_factory=dict_row)
    first_conn = None
    second_conn = None
    first_task = None
    second_task = None
    try:
        await admin_conn.execute(sql.SQL("create schema {}").format(sql.Identifier(schema_name)))
        await _set_search_path(admin_conn, schema_name)
        await admin_conn.execute(schema_sql)
        tenant_id = f"tenant-lifecycle-{uuid.uuid4().hex}"
        capability_id = "missing-row-race"
        await admin_conn.execute(
            "insert into tenants(id, name) values (%s, %s)",
            (tenant_id, "Lifecycle Lock Test"),
        )
        await admin_conn.execute(
            "insert into tenant_capability_distribution_backfills(tenant_id, completed_at) values (%s, now())",
            (tenant_id,),
        )

        first_conn = await psycopg.AsyncConnection.connect(dsn, row_factory=dict_row)
        second_conn = await psycopg.AsyncConnection.connect(dsn, row_factory=dict_row)
        await _set_search_path(first_conn, schema_name)
        await _set_search_path(second_conn, schema_name)
        await first_conn.execute("set local statement_timeout = '5s'")
        await second_conn.execute("set local statement_timeout = '5s'")
        first_pid = int((await (await first_conn.execute("select pg_backend_pid() as pid")).fetchone())["pid"])
        second_pid = int((await (await second_conn.execute("select pg_backend_pid() as pid")).fetchone())["pid"])

        original_require_unarchived = repositories._require_unarchived_capability_distribution
        negative_lookup_complete = asyncio.Event()
        release_first_writer = asyncio.Event()

        async def pause_first_writer(conn, **kwargs):
            await original_require_unarchived(conn, **kwargs)
            if conn is first_conn:
                negative_lookup_complete.set()
                await release_first_writer.wait()

        monkeypatch.setattr(repositories, "_require_unarchived_capability_distribution", pause_first_writer)

        async def upsert_active(conn, *, updated_by):
            return await repositories.upsert_capability_distribution_row(
                conn,
                tenant_id=tenant_id,
                capability_kind="mcp_server",
                capability_id=capability_id,
                status="active",
                visible_to_user=True,
                scope_mode="allowlist",
                department_ids=[],
                allowed_roles=[],
                metadata_json={},
                updated_by=updated_by,
            )

        async def second_writer():
            await upsert_active(second_conn, updated_by="writer-b")
            return await repositories.archive_capability_distribution_row(
                second_conn,
                tenant_id=tenant_id,
                capability_kind="mcp_server",
                capability_id=capability_id,
                archived_by="writer-b",
            )

        first_task = asyncio.create_task(upsert_active(first_conn, updated_by="writer-a"))
        await asyncio.wait_for(negative_lookup_complete.wait(), timeout=2.0)
        second_task = asyncio.create_task(second_writer())
        await _wait_for_lock_blocker(admin_conn, waiter_pid=second_pid, blocker_pid=first_pid)
        assert not second_task.done()

        release_first_writer.set()
        await asyncio.wait_for(first_task, timeout=2.0)
        await first_conn.commit()
        archived = await asyncio.wait_for(second_task, timeout=2.0)
        await second_conn.commit()

        assert archived["status"] == "disabled"
        final_cursor = await admin_conn.execute(
            """
            select status, visible_to_user, metadata_json
            from tenant_capability_distributions
            where tenant_id = %s and capability_kind = 'mcp_server' and capability_id = %s
            """,
            (tenant_id, capability_id),
        )
        final_row = await final_cursor.fetchone()
        assert final_row is not None
        assert final_row["status"] == "disabled"
        assert final_row["visible_to_user"] is False
        assert repositories.is_capability_distribution_archived(final_row) is True
        assert final_row["metadata_json"]["archived_by"] == "writer-b"
    finally:
        for task in (first_task, second_task):
            if task is not None and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        for conn in (first_conn, second_conn):
            if conn is not None:
                await conn.rollback()
                await conn.close()
        await admin_conn.execute(sql.SQL("drop schema if exists {} cascade").format(sql.Identifier(schema_name)))
        await admin_conn.close()
