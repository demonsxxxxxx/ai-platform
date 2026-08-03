"""Opt-in PostgreSQL evidence for the A3 schema and repository ledger facade."""

import asyncio
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


async def _seed_run(conn: psycopg.AsyncConnection) -> None:
    await conn.execute("insert into tenants(id, name) values ('tenant-a', 'Tenant A'), ('tenant-b', 'Tenant B')")
    await conn.execute("insert into workspaces(id, tenant_id, name) values ('workspace-a', 'tenant-a', 'A')")
    await conn.execute("insert into users(id, tenant_id, display_name) values ('user-a', 'tenant-a', 'User A')")
    await conn.execute("insert into agents(id, tenant_id, name, agent_type) values ('agent-a', 'tenant-a', 'Agent A', 'chat')")
    await conn.execute("insert into skills(id, name, version, executor_type) values ('skill-a', 'Skill A', '1.0.0', 'fake')")
    await conn.execute(
        """
        insert into sessions(id, tenant_id, workspace_id, user_id, agent_id, title)
        values ('session-a', 'tenant-a', 'workspace-a', 'user-a', 'agent-a', 'A')
        """
    )
    await conn.execute(
        """
        insert into runs(id, tenant_id, workspace_id, session_id, user_id, agent_id, skill_id, status)
        values ('run-a', 'tenant-a', 'workspace-a', 'session-a', 'user-a', 'agent-a', 'skill-a', 'running')
        """
    )


@pytest.mark.asyncio
async def test_run_event_ledger_schema_and_repository_facade_in_postgres():
    """Validate A3's adapter protocol, not a shared product schema or credential."""

    dsn = _postgres_dsn()
    schema_name = f"streaming_a3_{uuid.uuid4().hex}"
    schema_sql = Path("app/schema.sql").read_text(encoding="utf-8")
    admin = await psycopg.AsyncConnection.connect(dsn, autocommit=True, row_factory=dict_row)
    connections: list[psycopg.AsyncConnection] = []
    try:
        await admin.execute(sql.SQL("create schema {}").format(sql.Identifier(schema_name)))
        await _set_search_path(admin, schema_name)
        await admin.execute(schema_sql)
        await admin.execute(schema_sql)
        batch_timestamp_columns = await admin.execute(
            """
            select column_name
            from information_schema.columns
            where table_schema = %s
              and table_name = 'run_event_batches'
              and column_name in ('callback_received_at', 'durable_committed_at')
            order by column_name
            """,
            (schema_name,),
        )
        assert [row["column_name"] for row in await batch_timestamp_columns.fetchall()] == [
            "callback_received_at",
            "durable_committed_at",
        ]
        await _seed_run(admin)

        await admin.execute("drop index uq_run_events_tenant_run_sequence")
        await admin.execute(
            """
            insert into run_events(id, tenant_id, run_id, sequence, event_type, stage, message, created_at)
            values
              ('legacy-a', 'tenant-a', 'run-a', 0, 'assistant_delta', 'streaming', 'a', '2024-01-01T00:00:00Z'),
              ('legacy-b', 'tenant-a', 'run-a', 0, 'assistant_delta', 'streaming', 'b', '2024-01-01T00:00:01Z'),
              ('legacy-c', 'tenant-a', 'run-a', 3, 'assistant_delta', 'streaming', 'c', '2024-01-01T00:00:02Z')
            """
        )
        await admin.execute(schema_sql)
        await admin.execute(schema_sql)

        repaired = await admin.execute(
            "select id, sequence from run_events where tenant_id = 'tenant-a' and run_id = 'run-a' order by sequence"
        )
        assert await repaired.fetchall() == [
            {"id": "legacy-a", "sequence": 1},
            {"id": "legacy-b", "sequence": 2},
            {"id": "legacy-c", "sequence": 3},
        ]
        cursor = await admin.execute("select next_sequence from run_event_cursors where tenant_id = 'tenant-a' and run_id = 'run-a'")
        assert await cursor.fetchone() == {"next_sequence": 4}

        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            await admin.execute(
                """
                insert into run_events(id, tenant_id, run_id, sequence, event_type, stage)
                values ('wrong-scope', 'tenant-b', 'run-a', 1, 'assistant_delta', 'streaming')
                """
            )

        first = await psycopg.AsyncConnection.connect(dsn, row_factory=dict_row)
        second = await psycopg.AsyncConnection.connect(dsn, row_factory=dict_row)
        connections.extend((first, second))
        await _set_search_path(first, schema_name)
        await _set_search_path(second, schema_name)

        async def append_one(conn: psycopg.AsyncConnection, message: str) -> str:
            async with conn.transaction():
                return await repositories.append_event(
                    conn,
                    tenant_id="tenant-a",
                    run_id="run-a",
                    event_type="assistant_delta",
                    stage="streaming",
                    message=message,
                    payload={"delta": message},
                )

        event_ids = await asyncio.gather(append_one(first, "one"), append_one(second, "two"))
        assert len(set(event_ids)) == 2
        allocated = await admin.execute(
            "select sequence from run_events where id = any(%s) order by sequence",
            (event_ids,),
        )
        assert [row["sequence"] for row in await allocated.fetchall()] == [4, 5]

        async with first.transaction():
            initial_receipt = await repositories.append_event_batch(
                first,
                tenant_id="tenant-a",
                run_id="run-a",
                attempt_id="attempt-a",
                batch_id="batch-a",
                events=[{"event_type": "assistant_delta", "stage": "streaming", "message": "batch", "payload": {}}],
            )
        async with second.transaction():
            replay_receipt = await repositories.append_event_batch(
                second,
                tenant_id="tenant-a",
                run_id="run-a",
                attempt_id="attempt-a",
                batch_id="batch-a",
                events=[{"event_type": "assistant_delta", "stage": "streaming", "message": "ignored", "payload": {}}],
            )
        assert initial_receipt["duplicate"] is False
        assert replay_receipt == {**initial_receipt, "duplicate": True}

        before_rollback = await admin.execute("select next_sequence from run_event_cursors where tenant_id = 'tenant-a' and run_id = 'run-a'")
        with pytest.raises(RuntimeError, match="rollback"):
            async with first.transaction():
                await repositories.append_event_batch(
                    first,
                    tenant_id="tenant-a",
                    run_id="run-a",
                    attempt_id="attempt-a",
                    batch_id="batch-rollback",
                    events=[{"event_type": "assistant_delta", "stage": "streaming", "message": "rollback", "payload": {}}],
                )
                raise RuntimeError("rollback")
        no_receipt = await admin.execute("select id from run_event_batches where batch_id = 'batch-rollback'")
        after_rollback = await admin.execute("select next_sequence from run_event_cursors where tenant_id = 'tenant-a' and run_id = 'run-a'")
        assert await no_receipt.fetchone() is None
        assert await after_rollback.fetchone() == await before_rollback.fetchone()

        async with first.transaction():
            initial_fence = await repositories.acquire_run_event_terminal_drain_fence(
                first, tenant_id="tenant-a", run_id="run-a", attempt_id="attempt-a", batch_id="batch-a"
            )
        async with second.transaction():
            replay_fence = await repositories.acquire_run_event_terminal_drain_fence(
                second, tenant_id="tenant-a", run_id="run-a", attempt_id="attempt-a", batch_id="batch-a"
            )
        assert initial_fence == {"accepted": True, "duplicate": False}
        assert replay_fence == {"accepted": True, "duplicate": True}
        async with first.transaction():
            with pytest.raises(repositories.RepositoryConflictError, match="terminal_drain_already_consumed"):
                await repositories.acquire_run_event_terminal_drain_fence(
                    first, tenant_id="tenant-a", run_id="run-a", attempt_id="attempt-a", batch_id="batch-other"
                )
        async with second.transaction():
            isolated_fence = await repositories.acquire_run_event_terminal_drain_fence(
                second, tenant_id="tenant-a", run_id="run-a", attempt_id="attempt-b", batch_id="batch-other"
            )
        assert isolated_fence == {"accepted": True, "duplicate": False}
    finally:
        for conn in connections:
            await conn.close()
        await admin.execute(sql.SQL("drop schema if exists {} cascade").format(sql.Identifier(schema_name)))
        await admin.close()
