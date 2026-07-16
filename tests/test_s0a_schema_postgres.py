import os
from pathlib import Path
import uuid

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
import pytest

from app import repositories


POSTGRES_DSN_ENV = "AI_PLATFORM_S0A_SCHEMA_TEST_DSN"


def _postgres_dsn() -> str:
    dsn = os.getenv(POSTGRES_DSN_ENV, "").strip()
    if not dsn:
        pytest.skip(f"{POSTGRES_DSN_ENV} is not configured")
    return dsn


async def _set_search_path(conn: psycopg.AsyncConnection, schema_name: str) -> None:
    await conn.execute(sql.SQL("set search_path to {}").format(sql.Identifier(schema_name)))


@pytest.mark.asyncio
async def test_s0a_schema_workspace_scope_and_runtime_handle_apply_idempotently():
    dsn = _postgres_dsn()
    schema_name = f"s0a_schema_{uuid.uuid4().hex}"
    schema_sql = Path("app/schema.sql").read_text(encoding="utf-8")
    conn = await psycopg.AsyncConnection.connect(dsn, autocommit=True, row_factory=dict_row)
    try:
        await conn.execute(sql.SQL("create schema {}").format(sql.Identifier(schema_name)))
        await _set_search_path(conn, schema_name)
        await conn.execute(schema_sql)
        await conn.execute(schema_sql)

        constraint_cursor = await conn.execute(
            """
            select conname, convalidated
            from pg_constraint
            where conname in ('fk_sessions_workspace_scope', 'fk_runs_workspace_scope')
              and conrelid in (to_regclass(%s), to_regclass(%s))
            order by conname
            """,
            (f"{schema_name}.sessions", f"{schema_name}.runs"),
        )
        assert await constraint_cursor.fetchall() == [
            {"conname": "fk_runs_workspace_scope", "convalidated": True},
            {"conname": "fk_sessions_workspace_scope", "convalidated": True},
        ]

        column_cursor = await conn.execute(
            """
            select column_name
            from information_schema.columns
            where table_schema = %s
              and table_name = 'sandbox_leases'
              and column_name in (
                'runtime_container_id',
                'runtime_container_name',
                'runtime_executor_url',
                'runtime_workspace_container_path',
                'runtime_handle_verified_at'
              )
            order by column_name
            """,
            (schema_name,),
        )
        assert [row["column_name"] for row in await column_cursor.fetchall()] == [
            "runtime_container_id",
            "runtime_container_name",
            "runtime_executor_url",
            "runtime_handle_verified_at",
            "runtime_workspace_container_path",
        ]

        await conn.execute("insert into tenants(id, name) values ('tenant-a', 'Tenant A'), ('tenant-b', 'Tenant B')")
        await conn.execute(
            "insert into workspaces(id, tenant_id, name) values ('workspace-a', 'tenant-a', 'A'), ('workspace-b', 'tenant-b', 'B')"
        )
        await conn.execute("insert into users(id, tenant_id, display_name) values ('user-a', 'tenant-a', 'User A')")
        await conn.execute(
            "insert into agents(id, tenant_id, name, agent_type) values ('agent-a', 'tenant-a', 'Agent A', 'chat')"
        )
        await conn.execute(
            "insert into skills(id, name, version, executor_type) values ('skill-a', 'Skill A', '1.0.0', 'fake')"
        )

        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            await conn.execute(
                """
                insert into sessions(id, tenant_id, workspace_id, user_id, agent_id, title)
                values ('session-wrong', 'tenant-a', 'workspace-b', 'user-a', 'agent-a', 'Wrong')
                """
            )

        await conn.execute(
            """
            insert into sessions(id, tenant_id, workspace_id, user_id, agent_id, title)
            values ('session-a', 'tenant-a', 'workspace-a', 'user-a', 'agent-a', 'Valid')
            """
        )
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            await conn.execute(
                """
                insert into runs(
                  id, tenant_id, workspace_id, session_id, user_id, agent_id, skill_id, status
                ) values ('run-wrong', 'tenant-a', 'workspace-b', 'session-a', 'user-a', 'agent-a', 'skill-a', 'queued')
                """
            )

        # A first authenticated principal has no pre-existing users row. The
        # ledger's immediate user FK must therefore be provisioned before its
        # first claim, in the exact tenant scope of the principal.
        await repositories.ensure_submission_principal(
            conn,
            tenant_id="tenant-a",
            user_id="user-first-submission",
            display_name="First Submission User",
        )
        submission, created = await repositories.claim_chat_submission(
            conn,
            tenant_id="tenant-a",
            user_id="user-first-submission",
            submission_id=str(uuid.uuid4()),
            workspace_id="workspace-a",
            request_fingerprint_sha256="a" * 64,
        )
        assert created is True
        assert submission["user_id"] == "user-first-submission"
    finally:
        await conn.execute(sql.SQL("drop schema if exists {} cascade").format(sql.Identifier(schema_name)))
        await conn.close()
