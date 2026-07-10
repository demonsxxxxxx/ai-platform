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
            select convalidated
            from pg_constraint
            where conname = 'tenant_capability_distributions_allowed_roles_array'
              and conrelid = to_regclass(%s)
            """,
            (f"{schema_name}.tenant_capability_distributions",),
        )
        constraint = await constraint_cursor.fetchone()
        assert constraint == {"convalidated": True}

        with pytest.raises(psycopg.errors.CheckViolation):
            await admin_conn.execute(
                """
                insert into tenant_capability_distributions(
                  id, tenant_id, capability_kind, capability_id, allowed_roles
                ) values (%s, %s, 'mcp_server', %s, '{}'::jsonb)
                """,
                ("capdist-malformed", "default", "malformed-authority"),
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
            ) values (%s, %s, 'active', '{"unexpected":"object"}'::jsonb, array['QA']::text[])
            """,
            (tenant_id, "malformed-legacy-mcp"),
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

        first_conn = await psycopg.AsyncConnection.connect(dsn, row_factory=dict_row)
        second_conn = await psycopg.AsyncConnection.connect(dsn, row_factory=dict_row)
        try:
            await _set_search_path(first_conn, schema_name)
            await repositories.ensure_tenant_capability_distribution_backfill(
                first_conn,
                tenant_id=tenant_id,
            )
            await _set_search_path(second_conn, schema_name)
            await second_conn.execute("set local statement_timeout = '500ms'")
            await repositories.ensure_tenant_capability_distribution_backfill(
                second_conn,
                tenant_id=tenant_id,
            )
        finally:
            await first_conn.rollback()
            await second_conn.rollback()
            await first_conn.close()
            await second_conn.close()
    finally:
        await admin_conn.execute(sql.SQL("drop schema if exists {} cascade").format(sql.Identifier(schema_name)))
        await admin_conn.close()
