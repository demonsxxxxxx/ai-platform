"""Opt-in PostgreSQL coverage for #511's immutable session-context authority."""

import asyncio
import json
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


async def _insert_run(
    conn: psycopg.AsyncConnection,
    *,
    run_id: str,
    input_json: dict[str, object],
    context_snapshot_id: str | None = None,
) -> None:
    await conn.execute(
        """
        insert into runs(
          id, tenant_id, workspace_id, session_id, user_id, agent_id, skill_id,
          status, input_json, context_snapshot_id
        ) values (%s, 'tenant-a', 'workspace-a', 'session-a', 'user-a', 'agent-a', 'skill-a',
                  'queued', %s::jsonb, %s)
        """,
        (run_id, json.dumps(input_json), context_snapshot_id),
    )


async def _insert_executor_snapshot(conn: psycopg.AsyncConnection, *, snapshot_id: str, run_id: str) -> None:
    await conn.execute(
        """
        insert into run_context_snapshots(
          id, tenant_id, workspace_id, user_id, session_id, run_id, context_kind, payload_json
        ) values (%s, 'tenant-a', 'workspace-a', 'user-a', 'session-a', %s, 'executor', '{}'::jsonb)
        """,
        (snapshot_id, run_id),
    )


@pytest.mark.asyncio
async def test_issue_511_schema_backfill_binding_and_allocator_race_in_postgres():
    """Apply twice to populated rows and exercise binding/trigger/FK authority."""

    dsn = _postgres_dsn()
    schema_name = f"issue511_{uuid.uuid4().hex}"
    schema_sql = Path("app/schema.sql").read_text(encoding="utf-8")
    admin = await psycopg.AsyncConnection.connect(dsn, autocommit=True, row_factory=dict_row)
    try:
        await admin.execute(sql.SQL("create schema {}").format(sql.Identifier(schema_name)))
        await _set_search_path(admin, schema_name)
        await admin.execute(schema_sql)
        await admin.execute(
            "insert into tenants(id, name) values ('tenant-a', 'Tenant A')"
        )
        await admin.execute(
            "insert into workspaces(id, tenant_id, name) values ('workspace-a', 'tenant-a', 'Workspace A')"
        )
        await admin.execute(
            "insert into users(id, tenant_id, display_name) values ('user-a', 'tenant-a', 'User A')"
        )
        await admin.execute(
            "insert into agents(id, tenant_id, name, agent_type) values ('agent-a', 'tenant-a', 'Agent A', 'chat')"
        )
        await admin.execute(
            "insert into skills(id, name, version, executor_type) values ('skill-a', 'Skill A', '1.0.0', 'fake')"
        )
        await admin.execute(
            """
            insert into sessions(id, tenant_id, workspace_id, user_id, agent_id, title)
            values ('session-a', 'tenant-a', 'workspace-a', 'user-a', 'agent-a', 'Session A')
            """
        )

        # Simulate populated legacy JSON mirrors after a previous application
        # wrote them, including mismatch and missing-snapshot rows that must
        # remain null rather than infer an authority.
        await _insert_run(
            admin,
            run_id="run-backfill",
            input_json={
                "context_snapshot_id": "ctx-backfill",
                "context_snapshot": {"context_snapshot_id": "ctx-backfill"},
            },
        )
        await _insert_executor_snapshot(admin, snapshot_id="ctx-backfill", run_id="run-backfill")
        await _insert_run(
            admin,
            run_id="run-ambiguous",
            input_json={
                "context_snapshot_id": "ctx-left",
                "context_snapshot": {"context_snapshot_id": "ctx-right"},
            },
        )
        await _insert_run(
            admin,
            run_id="run-missing",
            input_json={
                "context_snapshot_id": "ctx-missing",
                "context_snapshot": {"context_snapshot_id": "ctx-missing"},
            },
        )

        await admin.execute(schema_sql)
        await admin.execute(schema_sql)
        backfill_rows = await (
            await admin.execute(
                """
                select id, context_snapshot_id
                from runs
                where id in ('run-backfill', 'run-ambiguous', 'run-missing')
                order by id
                """
            )
        ).fetchall()
        assert backfill_rows == [
            {"id": "run-ambiguous", "context_snapshot_id": None},
            {"id": "run-backfill", "context_snapshot_id": "ctx-backfill"},
            {"id": "run-missing", "context_snapshot_id": None},
        ]

        async def allocate_once() -> int:
            conn = await psycopg.AsyncConnection.connect(dsn, autocommit=True, row_factory=dict_row)
            try:
                await _set_search_path(conn, schema_name)
                return await repositories.allocate_session_run_generation(
                    conn,
                    tenant_id="tenant-a",
                    workspace_id="workspace-a",
                    user_id="user-a",
                    session_id="session-a",
                    agent_id="agent-a",
                )
            finally:
                await conn.close()

        allocated = await asyncio.gather(allocate_once(), allocate_once())
        assert sorted(allocated) == [1, 2]

        await _insert_run(admin, run_id="run-binding", input_json={})
        await _insert_executor_snapshot(admin, snapshot_id="ctx-binding", run_id="run-binding")
        bound_ref = {"context_snapshot_id": "ctx-binding"}
        await repositories.update_run_context_snapshot_ref(
            admin,
            tenant_id="tenant-a",
            run_id="run-binding",
            context_snapshot_id="ctx-binding",
            context_snapshot=bound_ref,
        )
        await repositories.update_run_context_snapshot_ref(
            admin,
            tenant_id="tenant-a",
            run_id="run-binding",
            context_snapshot_id="ctx-binding",
            context_snapshot=bound_ref,
        )
        await _insert_executor_snapshot(admin, snapshot_id="ctx-other", run_id="run-binding")
        with pytest.raises(repositories.RepositoryConflictError, match="context_snapshot_binding_invalid"):
            await repositories.update_run_context_snapshot_ref(
                admin,
                tenant_id="tenant-a",
                run_id="run-binding",
                context_snapshot_id="ctx-other",
                context_snapshot={"context_snapshot_id": "ctx-other"},
            )
        with pytest.raises(psycopg.errors.RaiseException, match="runs_context_snapshot_id_immutable"):
            await admin.execute(
                """
                update runs
                set context_snapshot_id = 'ctx-other',
                    input_json = '{"context_snapshot_id":"ctx-other","context_snapshot":{"context_snapshot_id":"ctx-other"}}'::jsonb
                where id = 'run-binding'
                """
            )

        deferred = await psycopg.AsyncConnection.connect(dsn, autocommit=False, row_factory=dict_row)
        try:
            await _set_search_path(deferred, schema_name)
            async with deferred.transaction():
                await _insert_run(
                    deferred,
                    run_id="run-deferred",
                    context_snapshot_id="ctx-deferred",
                    input_json={
                        "context_snapshot_id": "ctx-deferred",
                        "context_snapshot": {"context_snapshot_id": "ctx-deferred"},
                    },
                )
                await _insert_executor_snapshot(deferred, snapshot_id="ctx-deferred", run_id="run-deferred")
        finally:
            await deferred.close()
    finally:
        await admin.execute(sql.SQL("drop schema if exists {} cascade").format(sql.Identifier(schema_name)))
        await admin.close()
