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


async def _scoped_connection(dsn: str, schema_name: str) -> psycopg.AsyncConnection:
    return await psycopg.AsyncConnection.connect(
        dsn,
        options=f"-c search_path={schema_name}",
        row_factory=dict_row,
    )


@pytest.mark.asyncio
async def test_snapshot_member_locks_prevent_concurrent_retention_cleanup():
    dsn = _postgres_dsn()
    schema_name = f"retention_race_{uuid.uuid4().hex}"
    admin = await psycopg.AsyncConnection.connect(dsn, autocommit=True, row_factory=dict_row)
    snapshot_conn = None
    retention_conn = None
    try:
        await admin.execute(sql.SQL("create schema {}").format(sql.Identifier(schema_name)))
        await admin.execute(sql.SQL("set search_path to {}").format(sql.Identifier(schema_name)))
        await admin.execute(Path("app/schema.sql").read_text(encoding="utf-8"))
        await admin.execute("insert into tenants(id, name) values ('tenant-a', 'Tenant A')")
        await admin.execute("insert into workspaces(id, tenant_id, name) values ('workspace-a', 'tenant-a', 'A')")
        await admin.execute("insert into users(id, tenant_id, display_name) values ('user-a', 'tenant-a', 'A')")
        await admin.execute(
            "insert into agents(id, tenant_id, name, agent_type) values ('agent-a', 'tenant-a', 'A', 'chat')"
        )
        await admin.execute(
            "insert into skills(id, name, version, executor_type) values ('skill-a', 'A', '1', 'fake')"
        )
        await admin.execute(
            """
            insert into sessions(id, tenant_id, workspace_id, user_id, agent_id, title, status)
            values ('session-a', 'tenant-a', 'workspace-a', 'user-a', 'agent-a', 'A', 'archived')
            """
        )
        await admin.execute(
            """
            insert into runs(id, tenant_id, workspace_id, session_id, user_id, agent_id, skill_id, status)
            values ('run-a', 'tenant-a', 'workspace-a', 'session-a', 'user-a', 'agent-a', 'skill-a', 'succeeded')
            """
        )
        await admin.execute(
            """
            insert into messages(id, tenant_id, session_id, run_id, role, content, created_at)
            values (
              'message-old', 'tenant-a', 'session-a', 'run-a', 'user', 'old',
              clock_timestamp() - interval '8 days'
            )
            """
        )
        await admin.execute(
            """
            insert into artifacts(
              id, tenant_id, run_id, artifact_type, label, content_type,
              storage_key, size_bytes, expires_at
            ) values (
              'artifact-a', 'tenant-a', 'run-a', 'text', 'A', 'text/plain',
              'artifacts/a', 1, clock_timestamp() + interval '0.5 seconds'
            )
            """
        )
        await admin.execute(
            """
            insert into memory_records(
              id, tenant_id, workspace_id, user_id, agent_id, session_id,
              record_type, content, status, deleted_at
            ) values
              (
                'memory-deleted', 'tenant-a', 'workspace-a', 'user-a', 'agent-a', 'session-a',
                'note', 'deleted', 'deleted', clock_timestamp() - interval '8 days'
              ),
              (
                'memory-active', 'tenant-a', 'workspace-a', 'user-a', 'agent-a', 'session-a',
                'note', 'active', 'active', null
              )
            """
        )

        snapshot_conn = await _scoped_connection(dsn, schema_name)
        retention_conn = await _scoped_connection(dsn, schema_name)
        async with snapshot_conn.transaction():
            await repositories.create_context_snapshot(
                snapshot_conn,
                tenant_id="tenant-a",
                workspace_id="workspace-a",
                user_id="user-a",
                session_id="session-a",
                run_id="run-a",
                trace_id="trace-a",
                context_kind="executor",
                included_message_ids=[],
                included_file_ids=[],
                included_artifact_ids=["artifact-a"],
                included_memory_record_ids=[],
                redaction_summary_json={},
                payload_json={},
            )
            await asyncio.sleep(0.6)
            async with retention_conn.transaction():
                assert await repositories.queue_expired_artifacts_for_deletion(retention_conn) == []

        async with retention_conn.transaction():
            assert await repositories.queue_expired_artifacts_for_deletion(retention_conn) == []
            purged = await repositories.purge_deleted_memory_records(
                retention_conn,
                grace_days=7,
            )
            assert [row["id"] for row in purged] == ["memory-deleted"]

        retention_started = asyncio.Event()

        async def soft_delete_and_purge_memory() -> list[dict]:
            async with retention_conn.transaction():
                retention_started.set()
                await retention_conn.execute(
                    """
                    update memory_records
                    set status = 'deleted', deleted_at = clock_timestamp() - interval '8 days'
                    where id = 'memory-active'
                    """
                )
                return await repositories.purge_deleted_memory_records(
                    retention_conn,
                    grace_days=7,
                )

        async with snapshot_conn.transaction():
            await repositories.create_context_snapshot(
                snapshot_conn,
                tenant_id="tenant-a",
                workspace_id="workspace-a",
                user_id="user-a",
                session_id="session-a",
                run_id="run-a",
                trace_id="trace-memory",
                context_kind="executor",
                included_message_ids=[],
                included_file_ids=[],
                included_artifact_ids=[],
                included_memory_record_ids=["memory-active"],
                redaction_summary_json={},
                payload_json={},
            )
            retention_task = asyncio.create_task(soft_delete_and_purge_memory())
            await retention_started.wait()
            await asyncio.sleep(0.1)
            assert not retention_task.done()

        assert await retention_task == []
        backlog = await repositories.get_data_retention_backlog(
            retention_conn,
            retention_days={"messages": 7},
        )
        assert backlog["messages_age_eligible"] == 1
        assert backlog["run_events_age_eligible"] == 0
        cursor = await retention_conn.execute(
            """
            select artifacts.lifecycle_state,
                   (select count(*) from object_deletion_outbox) as outbox_count,
                   (select count(*) from run_context_snapshots) as snapshot_count,
                   (select count(*) from memory_records) as memory_count,
                   (select status from memory_records where id = 'memory-active') as memory_status
            from artifacts where id = 'artifact-a'
            """
        )
        assert await cursor.fetchone() == {
            "lifecycle_state": "active",
            "outbox_count": 0,
            "snapshot_count": 2,
            "memory_count": 1,
            "memory_status": "deleted",
        }
    finally:
        if snapshot_conn is not None:
            await snapshot_conn.close()
        if retention_conn is not None:
            await retention_conn.close()
        await admin.execute(sql.SQL("drop schema if exists {} cascade").format(sql.Identifier(schema_name)))
        await admin.close()


@pytest.mark.asyncio
async def test_object_delete_outbox_dead_letter_backoff_and_unknown_outcome_reconcile():
    dsn = _postgres_dsn()
    schema_name = f"retention_outbox_{uuid.uuid4().hex}"
    admin = await psycopg.AsyncConnection.connect(dsn, autocommit=True, row_factory=dict_row)
    conn = None
    try:
        await admin.execute(sql.SQL("create schema {}").format(sql.Identifier(schema_name)))
        await admin.execute(sql.SQL("set search_path to {}").format(sql.Identifier(schema_name)))
        await admin.execute(Path("app/schema.sql").read_text(encoding="utf-8"))
        await admin.execute("insert into tenants(id, name) values ('tenant-a', 'Tenant A')")
        await admin.execute(
            "insert into workspaces(id, tenant_id, name) values ('workspace-a', 'tenant-a', 'A')"
        )
        await admin.execute(
            "insert into users(id, tenant_id, display_name) values ('user-a', 'tenant-a', 'A')"
        )
        await admin.execute(
            "insert into agents(id, tenant_id, name, agent_type) values ('agent-a', 'tenant-a', 'A', 'chat')"
        )
        await admin.execute(
            "insert into skills(id, name, version, executor_type) values ('skill-a', 'A', '1', 'fake')"
        )
        await admin.execute(
            """
            insert into sessions(id, tenant_id, workspace_id, user_id, agent_id, title, status)
            values ('session-a', 'tenant-a', 'workspace-a', 'user-a', 'agent-a', 'A', 'archived')
            """
        )
        await admin.execute(
            """
            insert into runs(id, tenant_id, workspace_id, session_id, user_id, agent_id, skill_id, status)
            values ('run-a', 'tenant-a', 'workspace-a', 'session-a', 'user-a', 'agent-a', 'skill-a', 'succeeded')
            """
        )
        await admin.execute(
            """
            insert into artifacts(
              id, tenant_id, run_id, artifact_type, label, content_type,
              storage_key, size_bytes, lifecycle_state, delete_requested_at
            ) values
              ('artifact-bad', 'tenant-a', 'run-a', 'text', 'Bad', 'text/plain', 'bad', 1, 'delete_pending', now()),
              ('artifact-good', 'tenant-a', 'run-a', 'text', 'Good', 'text/plain', 'good', 1, 'delete_pending', now()),
              ('artifact-retry', 'tenant-a', 'run-a', 'text', 'Retry', 'text/plain', 'retry', 1, 'delete_pending', now())
            """
        )
        await admin.execute(
            """
            insert into object_deletion_outbox(
              id, tenant_id, artifact_id, storage_key, state, attempts, available_at
            ) values
              ('out-bad', 'tenant-a', 'artifact-bad', 'bad', 'pending', 2, now()),
              ('out-good', 'tenant-a', 'artifact-good', 'good', 'pending', 0, now()),
              ('out-retry', 'tenant-a', 'artifact-retry', 'retry', 'pending', 0, now())
            """
        )
        conn = await _scoped_connection(dsn, schema_name)

        async with conn.transaction():
            claimed = await repositories.claim_object_deletions(
                conn,
                limit=10,
                max_attempts=3,
            )
        assert {row["id"] for row in claimed} == {"out-bad", "out-good", "out-retry"}

        async with conn.transaction():
            assert await repositories.fail_object_deletion(
                conn,
                outbox_id="out-bad",
                error_code="object_delete_permanent",
                max_attempts=3,
                retry_base_seconds=60,
                retry_cap_seconds=300,
            ) == "dead_letter"
            assert await repositories.fail_object_deletion(
                conn,
                outbox_id="out-retry",
                error_code="object_delete_transient",
                max_attempts=3,
                retry_base_seconds=60,
                retry_cap_seconds=300,
            ) == "failed"
            assert await repositories.complete_object_deletion(
                conn,
                outbox_id="out-good",
                tenant_id="tenant-a",
                artifact_id="artifact-good",
            )

        cursor = await conn.execute(
            """
            select id, state, attempts, reconcile_required,
                   extract(epoch from available_at - now())::integer as retry_after_seconds
            from object_deletion_outbox order by id
            """
        )
        rows = {row["id"]: row for row in await cursor.fetchall()}
        assert rows["out-bad"]["state"] == "dead_letter"
        assert rows["out-bad"]["reconcile_required"] is True
        assert rows["out-good"]["state"] == "deleted"
        assert rows["out-retry"]["state"] == "failed"
        assert 55 <= rows["out-retry"]["retry_after_seconds"] <= 60
        await conn.commit()

        await admin.execute(
            "update object_deletion_outbox set available_at = now() - interval '1 second' where id = 'out-retry'"
        )
        async with conn.transaction():
            claimed_retry = await repositories.claim_object_deletions(
                conn,
                limit=10,
                max_attempts=3,
            )
            assert [row["id"] for row in claimed_retry] == ["out-retry"]
            assert await repositories.fail_object_deletion(
                conn,
                outbox_id="out-retry",
                error_code="object_delete_transient",
                max_attempts=3,
                retry_base_seconds=60,
                retry_cap_seconds=300,
            ) == "failed"
        cursor = await conn.execute(
            "select extract(epoch from available_at - now())::integer as retry_after_seconds from object_deletion_outbox where id = 'out-retry'"
        )
        assert 115 <= (await cursor.fetchone())["retry_after_seconds"] <= 120
        await conn.commit()

        async with conn.transaction():
            assert await repositories.requeue_dead_letter_object_deletion(
                conn,
                outbox_id="out-bad",
                tenant_id="tenant-a",
            )
            first_unknown_claim = await repositories.claim_object_deletions(
                conn,
                limit=1,
                max_attempts=3,
            )
        assert [row["id"] for row in first_unknown_claim] == ["out-bad"]

        await admin.execute(
            "update object_deletion_outbox set leased_at = now() - interval '6 minutes' where id = 'out-bad'"
        )
        async with conn.transaction():
            retried_unknown = await repositories.claim_object_deletions(
                conn,
                limit=1,
                max_attempts=3,
            )
            assert [row["id"] for row in retried_unknown] == ["out-bad"]
            assert await repositories.complete_object_deletion(
                conn,
                outbox_id="out-bad",
                tenant_id="tenant-a",
                artifact_id="artifact-bad",
            )

        backlog = await repositories.get_data_retention_backlog(conn)
        assert backlog["object_delete_dead_letter"] == 0
        assert backlog["object_delete_reconcile_required"] == 0
        cursor = await conn.execute(
            """
            select outbox.state, outbox.attempts, outbox.receipt_at is not null as receipted,
                   artifacts.lifecycle_state
            from object_deletion_outbox outbox
            join artifacts on artifacts.id = outbox.artifact_id
            where outbox.id = 'out-bad'
            """
        )
        assert await cursor.fetchone() == {
            "state": "deleted",
            "attempts": 2,
            "receipted": True,
            "lifecycle_state": "deleted",
        }
    finally:
        if conn is not None:
            await conn.close()
        await admin.execute(sql.SQL("drop schema if exists {} cascade").format(sql.Identifier(schema_name)))
        await admin.close()
