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


def _run_event_repair_statements(schema_sql: str) -> tuple[str, str]:
    repair_start = schema_sql.index("do $$\ndeclare\n  unique_index_present boolean;")
    index_start = schema_sql.index("create unique index if not exists uq_run_events_tenant_run_sequence", repair_start)
    section_end = schema_sql.index("\n\ncreate table if not exists run_tool_permission_requests", index_start)
    return schema_sql[repair_start:index_start], schema_sql[index_start:section_end]


async def _wait_for_blocker(
    observer: psycopg.AsyncConnection,
    *,
    blocked_pid: int,
    blocker_pid: int,
) -> None:
    for _ in range(100):
        result = await observer.execute(
            "select %s = any(pg_blocking_pids(%s)) as blocked",
            (blocker_pid, blocked_pid),
        )
        if (await result.fetchone())["blocked"]:
            return
        # The lock view is the proof; this only yields to the writer task.
        await asyncio.sleep(0)
    raise AssertionError("legacy_writer_not_blocked_by_migration_lock")


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
              ('legacy-c', 'tenant-a', 'run-a', 0, 'assistant_delta', 'streaming', 'c', '2024-01-01T00:00:02Z')
            """
        )
        repair_sql, index_and_cursor_sql = _run_event_repair_statements(schema_sql)
        migrator = await psycopg.AsyncConnection.connect(dsn, row_factory=dict_row)
        legacy_writer = await psycopg.AsyncConnection.connect(dsn, row_factory=dict_row)
        connections.extend((migrator, legacy_writer))
        await _set_search_path(migrator, schema_name)
        await _set_search_path(legacy_writer, schema_name)
        migrator_pid = (await (await migrator.execute("select pg_backend_pid() as pid")).fetchone())["pid"]
        writer_pid = (await (await legacy_writer.execute("select pg_backend_pid() as pid")).fetchone())["pid"]
        writer_selected_sequence = asyncio.Event()

        async def append_with_legacy_max_sequence() -> None:
            async with legacy_writer.transaction():
                selected = await legacy_writer.execute(
                    "select coalesce(max(sequence), 0) + 1 as sequence from run_events where tenant_id = %s and run_id = %s",
                    ("tenant-a", "run-a"),
                )
                sequence = (await selected.fetchone())["sequence"]
                writer_selected_sequence.set()
                await legacy_writer.execute(
                    """
                    insert into run_events(id, tenant_id, run_id, sequence, event_type, stage, message)
                    values ('legacy-max', 'tenant-a', 'run-a', %s, 'assistant_delta', 'streaming', 'legacy')
                    """,
                    (sequence,),
                )

        async with migrator.transaction():
            await migrator.execute("lock table run_events in share row exclusive mode")
            lock = await admin.execute(
                """
                select locks.granted
                from pg_locks locks
                join pg_class relation on relation.oid = locks.relation
                join pg_namespace namespace on namespace.oid = relation.relnamespace
                where locks.pid = %s
                  and locks.mode = 'ShareRowExclusiveLock'
                  and namespace.nspname = %s
                  and relation.relname = 'run_events'
                """,
                (migrator_pid, schema_name),
            )
            assert await lock.fetchone() == {"granted": True}
            writer_task = asyncio.create_task(append_with_legacy_max_sequence())
            await writer_selected_sequence.wait()
            await _wait_for_blocker(admin, blocked_pid=writer_pid, blocker_pid=migrator_pid)
            await migrator.execute(repair_sql)
            await migrator.execute(index_and_cursor_sql)
        with pytest.raises(psycopg.errors.UniqueViolation):
            await writer_task

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
        assert initial_receipt["duplicate"] is False
        initial_timestamps = await admin.execute(
            """
            select callback_received_at, durable_committed_at
            from run_event_batches
            where tenant_id = 'tenant-a' and run_id = 'run-a' and attempt_id = 'attempt-a' and batch_id = 'batch-a'
            """
        )
        first_timestamps = await initial_timestamps.fetchone()
        assert first_timestamps["callback_received_at"] is not None
        assert first_timestamps["durable_committed_at"] is not None
        assert first_timestamps["callback_received_at"] <= first_timestamps["durable_committed_at"]
        async with second.transaction():
            replay_receipt = await repositories.append_event_batch(
                second,
                tenant_id="tenant-a",
                run_id="run-a",
                attempt_id="attempt-a",
                batch_id="batch-a",
                events=[{"event_type": "assistant_delta", "stage": "streaming", "message": "ignored", "payload": {}}],
            )
        assert replay_receipt == {**initial_receipt, "duplicate": True}
        replay_timestamps = await admin.execute(
            """
            select callback_received_at, durable_committed_at
            from run_event_batches
            where tenant_id = 'tenant-a' and run_id = 'run-a' and attempt_id = 'attempt-a' and batch_id = 'batch-a'
            """
        )
        assert await replay_timestamps.fetchone() == first_timestamps

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

        fence_contenders = 0
        fence_ready = asyncio.Event()

        async def compete_for_terminal_fence(conn: psycopg.AsyncConnection, batch_id: str) -> tuple[str, str, object]:
            nonlocal fence_contenders
            async with conn.transaction():
                fence_contenders += 1
                if fence_contenders == 2:
                    fence_ready.set()
                await fence_ready.wait()
                try:
                    receipt = await repositories.acquire_run_event_terminal_drain_fence(
                        conn,
                        tenant_id="tenant-a",
                        run_id="run-a",
                        attempt_id="attempt-a",
                        batch_id=batch_id,
                    )
                except repositories.RepositoryConflictError as exc:
                    return "conflict", batch_id, str(exc)
                return "accepted", batch_id, receipt

        competition = await asyncio.gather(
            compete_for_terminal_fence(first, "batch-a"),
            compete_for_terminal_fence(second, "batch-other"),
        )
        accepted = [outcome for outcome in competition if outcome[0] == "accepted"]
        conflicts = [outcome for outcome in competition if outcome[0] == "conflict"]
        assert len(accepted) == 1
        assert accepted[0][2] == {"accepted": True, "duplicate": False}
        assert conflicts == [("conflict", "batch-a" if accepted[0][1] == "batch-other" else "batch-other", "terminal_drain_already_consumed")]
        winning_batch_id = accepted[0][1]
        async with first.transaction():
            replay_fence = await repositories.acquire_run_event_terminal_drain_fence(
                first, tenant_id="tenant-a", run_id="run-a", attempt_id="attempt-a", batch_id=winning_batch_id
            )
        assert replay_fence == {"accepted": True, "duplicate": True}
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
