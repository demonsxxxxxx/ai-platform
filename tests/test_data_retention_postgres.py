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
async def test_file_deletion_is_owner_reference_and_row_lock_safe():
    dsn = _postgres_dsn()
    schema_name = f"file_deletion_{uuid.uuid4().hex}"
    admin = await psycopg.AsyncConnection.connect(dsn, autocommit=True, row_factory=dict_row)
    primary = None
    secondary = None
    try:
        await admin.execute(sql.SQL("create schema {}").format(sql.Identifier(schema_name)))
        await admin.execute(sql.SQL("set search_path to {}").format(sql.Identifier(schema_name)))
        await admin.execute(Path("app/schema.sql").read_text(encoding="utf-8"))
        await admin.execute(
            """
            insert into tenants(id, name) values
              ('tenant-a', 'Tenant A'), ('tenant-b', 'Tenant B')
            """
        )
        await admin.execute(
            """
            insert into workspaces(id, tenant_id, name) values
              ('workspace-a', 'tenant-a', 'A'),
              ('workspace-b', 'tenant-a', 'B'),
              ('workspace-c', 'tenant-b', 'C')
            """
        )
        await admin.execute(
            """
            insert into users(id, tenant_id, display_name) values
              ('user-a', 'tenant-a', 'A'),
              ('user-other', 'tenant-a', 'Other'),
              ('user-b', 'tenant-b', 'B')
            """
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
            values ('session-a', 'tenant-a', 'workspace-a', 'user-a', 'agent-a', 'A', 'active')
            """
        )
        await admin.execute(
            """
            insert into runs(
              id, tenant_id, workspace_id, session_id, user_id, agent_id, skill_id, status, input_json
            ) values
              (
                'run-bind', 'tenant-a', 'workspace-a', 'session-a', 'user-a', 'agent-a',
                'skill-a', 'queued', '{}'::jsonb
              ),
              (
                'run-delete-first', 'tenant-a', 'workspace-a', 'session-a', 'user-a', 'agent-a',
                'skill-a', 'queued', '{}'::jsonb
              ),
              (
                'run-reference', 'tenant-a', 'workspace-a', 'session-a', 'user-a', 'agent-a',
                'skill-a', 'succeeded', '{"file_ids":["file-run-reference"]}'::jsonb
              )
            """
        )
        await admin.execute(
            """
            insert into files(
              id, tenant_id, workspace_id, user_id, session_id, run_id,
              original_name, content_type, size_bytes, storage_key, sha256
            ) values
              ('file-delete', 'tenant-a', 'workspace-a', 'user-a', null, null, 'delete.txt', 'text/plain', 1, 'files/delete', 'a'),
              ('file-bind', 'tenant-a', 'workspace-a', 'user-a', null, null, 'bind.txt', 'text/plain', 1, 'files/bind', 'b'),
              ('file-delete-first', 'tenant-a', 'workspace-a', 'user-a', null, null, 'first.txt', 'text/plain', 1, 'files/first', 'c'),
              ('file-run-reference', 'tenant-a', 'workspace-a', 'user-a', null, null, 'run.txt', 'text/plain', 1, 'files/run', 'd'),
              ('file-snapshot-reference', 'tenant-a', 'workspace-a', 'user-a', null, null, 'snapshot.txt', 'text/plain', 1, 'files/snapshot', 'e'),
              ('file-message-reference', 'tenant-a', 'workspace-a', 'user-a', null, null, 'message.txt', 'text/plain', 1, 'files/message', 'h'),
              ('file-artifact-reference', 'tenant-a', 'workspace-a', 'user-a', null, null, 'artifact.txt', 'text/plain', 1, 'files/artifact', 'f'),
              ('file-session-bound', 'tenant-a', 'workspace-a', 'user-a', 'session-a', null, 'session.txt', 'text/plain', 1, 'files/session', 'g')
            """
        )
        await admin.execute(
            """
            insert into messages(
              id, tenant_id, session_id, run_id, role, content, metadata_json
            ) values (
              'message-file-ref', 'tenant-a', 'session-a', 'run-reference', 'user', 'file',
              '{"file_ids":["file-message-reference"]}'::jsonb
            )
            """
        )
        await admin.execute(
            """
            insert into run_context_snapshots(
              id, tenant_id, workspace_id, user_id, session_id, run_id, included_file_ids
            ) values (
              'snapshot-file-ref', 'tenant-a', 'workspace-a', 'user-a', 'session-a',
              'run-reference', '["file-snapshot-reference"]'::jsonb
            )
            """
        )
        await admin.execute(
            """
            insert into artifacts(
              id, tenant_id, run_id, artifact_type, label, content_type,
              storage_key, size_bytes, manifest_json
            ) values (
              'artifact-file-ref', 'tenant-a', 'run-reference', 'text', 'A', 'text/plain',
              'artifacts/ref', 1, '{"source_file_id":"file-artifact-reference"}'::jsonb
            )
            """
        )
        await admin.execute(
            """
            insert into files(
              id, tenant_id, workspace_id, user_id, original_name, content_type,
              size_bytes, storage_key, sha256, lifecycle_state, delete_requested_at
            ) values (
              'file-mismatch', 'tenant-a', 'workspace-a', 'user-a', 'mismatch.txt',
              'text/plain', 1, 'files/mismatch', 'i', 'delete_pending', now()
            )
            """
        )
        await admin.execute(
            """
            insert into object_deletion_outbox(
              id, tenant_id, target_type, artifact_id, file_id, storage_key, state
            ) values (
              'objdel_file-mismatch', 'tenant-a', 'file', null, 'file-mismatch',
              'files/wrong-object', 'pending'
            )
            """
        )
        primary = await _scoped_connection(dsn, schema_name)
        secondary = await _scoped_connection(dsn, schema_name)

        for scope in (
            {"tenant_id": "tenant-a", "workspace_id": "workspace-b", "user_id": "user-a"},
            {"tenant_id": "tenant-a", "workspace_id": "workspace-a", "user_id": "user-other"},
            {"tenant_id": "tenant-b", "workspace_id": "workspace-c", "user_id": "user-b"},
        ):
            async with primary.transaction():
                assert await repositories.queue_unbound_file_for_deletion(
                    primary,
                    file_id="file-delete",
                    **scope,
                ) is None

        for file_id, reason in (
            ("file-session-bound", "file_session_or_run_bound"),
            ("file-run-reference", "file_run_input_referenced"),
            ("file-snapshot-reference", "file_context_snapshot_referenced"),
            ("file-message-reference", "file_message_referenced"),
            ("file-artifact-reference", "file_artifact_referenced"),
        ):
            async with primary.transaction():
                with pytest.raises(repositories.FileDeletionBlockedError, match=reason):
                    await repositories.queue_unbound_file_for_deletion(
                        primary,
                        tenant_id="tenant-a",
                        workspace_id="workspace-a",
                        user_id="user-a",
                        file_id=file_id,
                    )

        first_queued = asyncio.Event()
        release_first = asyncio.Event()

        async def queue_first() -> dict:
            async with primary.transaction():
                result = await repositories.queue_unbound_file_for_deletion(
                    primary,
                    tenant_id="tenant-a",
                    workspace_id="workspace-a",
                    user_id="user-a",
                    file_id="file-delete",
                )
                assert result is not None
                first_queued.set()
                await release_first.wait()
                return result

        async def queue_duplicate() -> dict:
            await first_queued.wait()
            async with secondary.transaction():
                result = await repositories.queue_unbound_file_for_deletion(
                    secondary,
                    tenant_id="tenant-a",
                    workspace_id="workspace-a",
                    user_id="user-a",
                    file_id="file-delete",
                )
                assert result is not None
                return result

        first_task = asyncio.create_task(queue_first())
        duplicate_task = asyncio.create_task(queue_duplicate())
        await first_queued.wait()
        await asyncio.sleep(0.1)
        assert not duplicate_task.done()
        release_first.set()
        first_result, duplicate_result = await asyncio.gather(first_task, duplicate_task)
        assert first_result["created"] is True
        assert duplicate_result["created"] is False
        cursor = await primary.execute(
            "select count(*) as count from object_deletion_outbox where file_id = 'file-delete'"
        )
        assert (await cursor.fetchone())["count"] == 1
        await primary.commit()

        async def attempt_delete_bound_file():
            async with secondary.transaction():
                return await repositories.queue_unbound_file_for_deletion(
                    secondary,
                    tenant_id="tenant-a",
                    workspace_id="workspace-a",
                    user_id="user-a",
                    file_id="file-bind",
                )

        async with primary.transaction():
            await repositories.authorize_files_for_run(
                primary,
                tenant_id="tenant-a",
                workspace_id="workspace-a",
                user_id="user-a",
                session_id="session-a",
                run_id="run-bind",
                file_ids=["file-bind"],
            )
            blocked_delete = asyncio.create_task(attempt_delete_bound_file())
            await asyncio.sleep(0.1)
            assert not blocked_delete.done()
            await repositories.bind_files_to_run(
                primary,
                tenant_id="tenant-a",
                workspace_id="workspace-a",
                user_id="user-a",
                session_id="session-a",
                run_id="run-bind",
                file_ids=["file-bind"],
            )
        with pytest.raises(repositories.FileDeletionBlockedError, match="file_session_or_run_bound"):
            await blocked_delete

        async def attempt_bind_tombstoned_file():
            async with secondary.transaction():
                await repositories.authorize_files_for_run(
                    secondary,
                    tenant_id="tenant-a",
                    workspace_id="workspace-a",
                    user_id="user-a",
                    session_id="session-a",
                    run_id="run-delete-first",
                    file_ids=["file-delete-first"],
                )

        async with primary.transaction():
            delete_first = await repositories.queue_unbound_file_for_deletion(
                primary,
                tenant_id="tenant-a",
                workspace_id="workspace-a",
                user_id="user-a",
                file_id="file-delete-first",
            )
            assert delete_first is not None and delete_first["created"] is True
            blocked_bind = asyncio.create_task(attempt_bind_tombstoned_file())
            await asyncio.sleep(0.1)
            assert not blocked_bind.done()
        with pytest.raises(repositories.RepositoryNotFoundError, match="file_not_found"):
            await blocked_bind

        async with primary.transaction():
            claimed = await repositories.claim_object_deletions(primary, limit=10, max_attempts=5)
            assert all(item["id"] != "objdel_file-mismatch" for item in claimed)
            delete_claim = next(item for item in claimed if item["file_id"] == "file-delete")
            assert await repositories.complete_object_deletion(
                primary,
                outbox_id=delete_claim["id"],
                tenant_id="tenant-a",
                lease_generation=delete_claim["lease_generation"],
            )
        cursor = await primary.execute(
            """
            select state, reconcile_required, last_error_code
            from object_deletion_outbox where id = 'objdel_file-mismatch'
            """
        )
        assert await cursor.fetchone() == {
            "state": "dead_letter",
            "reconcile_required": True,
            "last_error_code": "object_delete_target_invariant",
        }
        await primary.commit()
        async with primary.transaction():
            replay = await repositories.queue_unbound_file_for_deletion(
                primary,
                tenant_id="tenant-a",
                workspace_id="workspace-a",
                user_id="user-a",
                file_id="file-delete",
            )
        assert replay is not None
        assert (replay["lifecycle_state"], replay["deletion_state"], replay["created"]) == (
            "deleted",
            "deleted",
            False,
        )
    finally:
        if primary is not None:
            await primary.close()
        if secondary is not None:
            await secondary.close()
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
        claims = {row["id"]: row for row in claimed}

        async with conn.transaction():
            assert await repositories.fail_object_deletion(
                conn,
                outbox_id="out-bad",
                tenant_id="tenant-a",
                lease_generation=claims["out-bad"]["lease_generation"],
                error_code="object_delete_permanent",
                max_attempts=3,
                retry_base_seconds=60,
                retry_cap_seconds=300,
            ) == "dead_letter"
            assert await repositories.fail_object_deletion(
                conn,
                outbox_id="out-retry",
                tenant_id="tenant-a",
                lease_generation=claims["out-retry"]["lease_generation"],
                error_code="object_delete_transient",
                max_attempts=3,
                retry_base_seconds=60,
                retry_cap_seconds=300,
            ) == "failed"
            assert await repositories.complete_object_deletion(
                conn,
                outbox_id="out-good",
                tenant_id="tenant-a",
                lease_generation=claims["out-good"]["lease_generation"],
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
                tenant_id="tenant-a",
                lease_generation=claimed_retry[0]["lease_generation"],
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
        stale_generation = first_unknown_claim[0]["lease_generation"]

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
            assert not await repositories.complete_object_deletion(
                conn,
                outbox_id="out-bad",
                tenant_id="tenant-a",
                lease_generation=stale_generation,
            )
            assert await repositories.complete_object_deletion(
                conn,
                outbox_id="out-bad",
                tenant_id="tenant-a",
                lease_generation=retried_unknown[0]["lease_generation"],
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
