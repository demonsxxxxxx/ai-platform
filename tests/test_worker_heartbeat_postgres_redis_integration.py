import asyncio
from contextlib import asynccontextmanager
from dataclasses import fields
from datetime import datetime, timedelta, timezone
import json
import os
import uuid

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
import pytest
from redis.asyncio import Redis

from app import queue, schema_migrations
from app.models import QueueRunPayload
from app.platform.postgres.errors import RepositoryConflictError
from app.runs.api import heartbeat_worker_run_attempt
from app.runs.application import attempt_lifecycle as attempt_lifecycle_application
from app.runs.application.attempt_lifecycle import RunAttemptLifecycleService
from app.runs.domain.execution_spec import (
    EXECUTION_SPEC_SCHEMA_VERSION,
    compile_execution_spec,
)
from app.runs.infrastructure import postgres as run_attempt_persistence
from app.runs.infrastructure.postgres import (
    create_run_attempt,
    transition_run_attempt,
)
import app.worker_main as worker_main


POSTGRES_DSN_ENV = "AI_PLATFORM_S0A_SCHEMA_TEST_DSN"
REDIS_URL_ENV = "AI_PLATFORM_SSE_REDIS_TEST_URL"

LEGACY_REQUEUE_IDENTITY_PROBE_SCRIPT = """
-- Exact ownership behavior used by the v1 reclaimer before queue mutation.
local metadata_json = redis.call("hget", KEYS[1], ARGV[1])
if not metadata_json then
  metadata_json = redis.call("hget", KEYS[2], ARGV[1])
end
local ok, metadata = pcall(cjson.decode, metadata_json or "")
if not ok or type(metadata) ~= "table"
  or tostring(metadata["message_id"] or "") ~= ARGV[1]
  or tostring(metadata["attempt_id"] or "") ~= ARGV[2]
  or tostring(metadata["owner_token"] or "") ~= ARGV[3] then
  return cjson.encode({status = "stale_owner"})
end
redis.call("lrem", KEYS[3], 1, ARGV[4])
redis.call("rpush", KEYS[4], ARGV[4])
return cjson.encode({status = "requeued"})
"""


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        pytest.skip(f"{name} is not configured")
    return value


async def _redis_epoch(redis: Redis) -> float:
    seconds, microseconds = await redis.time()
    return float(seconds) + (float(microseconds) / 1_000_000)


def _transaction_factory(dsn: str, schema_name: str):
    @asynccontextmanager
    async def factory():
        conn = await psycopg.AsyncConnection.connect(
            dsn,
            options=f"-c search_path={schema_name}",
            row_factory=dict_row,
        )
        try:
            async with conn.transaction():
                yield conn
        finally:
            await conn.close()

    return factory


def _index_connection_factory(dsn: str, schema_name: str):
    async def factory():
        return await psycopg.AsyncConnection.connect(
            dsn,
            autocommit=True,
            options=f"-c search_path={schema_name}",
            row_factory=dict_row,
        )

    return factory


def _execution_spec(*, tenant_id: str, run_id: str, suffix: str):
    return compile_execution_spec(
        {
            "schema_version": EXECUTION_SPEC_SCHEMA_VERSION,
            "run_payload_schema_version": "ai-platform.run-payload.v1",
            "tenant_id": tenant_id,
            "workspace_id": f"workspace-{suffix}",
            "user_id": f"user-{suffix}",
            "session_id": f"session-{suffix}",
            "run_id": run_id,
            "agent_id": f"agent-{suffix}",
            "execution_kind": "skill",
            "skill_id": f"skill-{suffix}",
            "file_ids": [],
            "input": {"message": "heartbeat integration"},
            "executor_type": "fake",
            "trace_id": f"trace-{suffix}",
            "skill_version": "version-a",
            "release_decision": {
                "schema_version": "ai-platform.skill-release-decision.v1",
                "selected_version": "version-a",
            },
            "skill_manifests": [
                {
                    "skill_id": f"skill-{suffix}",
                    "content_hash": "version-a",
                }
            ],
            "context_snapshot_id": f"context-{suffix}",
            "context_snapshot": {
                "context_snapshot_id": f"context-{suffix}",
            },
            "context_pack": {},
            "model_id": "model-a",
            "model_value": "model-a",
            "agent_profile": {},
        }
    )


async def _seed_running_attempt(
    conn: psycopg.AsyncConnection,
    *,
    suffix: str,
    queue_attempt_id: str,
    queue_message_id: str,
    worker_id: str,
    heartbeat_at: datetime,
    lease_expires_at: datetime,
) -> tuple[str, str, str]:
    tenant_id = f"tenant-{suffix}"
    run_id = f"run-{suffix}"
    await conn.execute(
        "insert into tenants(id, name) values (%s, %s)",
        (tenant_id, f"Tenant {suffix}"),
    )
    await conn.execute(
        "insert into workspaces(id, tenant_id, name) values (%s, %s, %s)",
        (f"workspace-{suffix}", tenant_id, f"Workspace {suffix}"),
    )
    await conn.execute(
        "insert into users(id, tenant_id, display_name) values (%s, %s, %s)",
        (f"user-{suffix}", tenant_id, f"User {suffix}"),
    )
    await conn.execute(
        "insert into agents(id, tenant_id, name, agent_type) values (%s, %s, %s, 'chat')",
        (f"agent-{suffix}", tenant_id, f"Agent {suffix}"),
    )
    await conn.execute(
        "insert into skills(id, name, version, executor_type) values (%s, %s, '1', 'fake')",
        (f"skill-{suffix}", f"Skill {suffix}"),
    )
    await conn.execute(
        """
        insert into sessions(
          id, tenant_id, workspace_id, user_id, agent_id, title
        ) values (%s, %s, %s, %s, %s, %s)
        """,
        (
            f"session-{suffix}",
            tenant_id,
            f"workspace-{suffix}",
            f"user-{suffix}",
            f"agent-{suffix}",
            f"Session {suffix}",
        ),
    )
    await conn.execute(
        """
        insert into runs(
          id, tenant_id, workspace_id, session_id, user_id, agent_id,
          skill_id, status
        ) values (%s, %s, %s, %s, %s, %s, %s, 'queued')
        """,
        (
            run_id,
            tenant_id,
            f"workspace-{suffix}",
            f"session-{suffix}",
            f"user-{suffix}",
            f"agent-{suffix}",
            f"skill-{suffix}",
        ),
    )
    attempt_id = f"attempt-{suffix}"
    await create_run_attempt(
        conn,
        tenant_id=tenant_id,
        run_id=run_id,
        attempt_id=attempt_id,
        ordinal=1,
        owner_kind="queue_worker",
        owner_id=worker_id,
        queue_attempt_id=queue_attempt_id,
        execution_spec=_execution_spec(
            tenant_id=tenant_id,
            run_id=run_id,
            suffix=suffix,
        ),
    )
    await transition_run_attempt(
        conn,
        tenant_id=tenant_id,
        run_id=run_id,
        attempt_id=attempt_id,
        expected_status="created",
        requested_status="queued",
        expected_owner_kind="queue_worker",
        expected_owner_id=worker_id,
        expected_owner_generation=1,
        queue_message_id=queue_message_id,
        last_heartbeat_at=heartbeat_at,
        lease_expires_at=lease_expires_at,
    )
    await transition_run_attempt(
        conn,
        tenant_id=tenant_id,
        run_id=run_id,
        attempt_id=attempt_id,
        expected_status="queued",
        requested_status="claimed",
        expected_owner_kind="queue_worker",
        expected_owner_id=worker_id,
        expected_owner_generation=2,
    )
    await transition_run_attempt(
        conn,
        tenant_id=tenant_id,
        run_id=run_id,
        attempt_id=attempt_id,
        expected_status="claimed",
        requested_status="running",
        expected_owner_kind="queue_worker",
        expected_owner_id=worker_id,
        expected_owner_generation=3,
    )
    return tenant_id, run_id, attempt_id


@pytest.mark.asyncio
@pytest.mark.parametrize("attempts", [1, 3])
async def test_real_redis_reclaim_rechecks_current_lease_activity(
    monkeypatch,
    attempts,
):
    redis_url = _required_env(REDIS_URL_ENV)
    suffix = uuid.uuid4().hex[:12]
    queue_prefix = f"ai-platform:test:reclaim-heartbeat:{suffix}"
    worker_id = f"worker-{suffix}"

    class Settings:
        queue_key_prefix = queue_prefix

    async def get_redis():
        return Redis.from_url(redis_url, decode_responses=True)

    monkeypatch.setattr(queue, "get_settings", lambda: Settings())
    monkeypatch.setattr(queue, "get_redis", get_redis)
    redis = Redis.from_url(redis_url, decode_responses=True)
    keys = queue.get_queue_keys()
    redis_keys = tuple(getattr(keys, field.name) for field in fields(keys))
    payload = QueueRunPayload(
        tenant_id=f"tenant-{suffix}",
        workspace_id=f"workspace-{suffix}",
        user_id=f"user-{suffix}",
        session_id=f"session-{suffix}",
        run_id=f"run-{suffix}",
        agent_id=f"agent-{suffix}",
        skill_id=f"skill-{suffix}",
        file_ids=[],
        input={"message": "reclaim integration"},
        executor_type="fake",
        skill_version="version-a",
        release_decision={
            "schema_version": "ai-platform.skill-release-decision.v1",
            "selected_version": "version-a",
        },
        skill_manifests=[
            {
                "skill_id": f"skill-{suffix}",
                "content_hash": "version-a",
            }
        ],
    )
    raw = payload.model_dump_json()
    message_id = queue.message_id_for_raw(raw)
    queue_attempt_id = f"qat_{'a' * 64}"
    owner_token = f"qown_{'b' * 64}"
    try:
        await redis.delete(*redis_keys)
        initial_epoch = await _redis_epoch(redis) - 120
        metadata = {
            "message_id": message_id,
            "raw": raw,
            "attempts": attempts,
            "attempt_id": queue_attempt_id,
            "owner_token": owner_token,
            "leased_at": initial_epoch,
            "heartbeat_at": initial_epoch,
            "worker_id": worker_id,
            "tenant_id": payload.tenant_id,
            "user_id": payload.user_id,
            "run_id": payload.run_id,
        }
        encoded_metadata = json.dumps(metadata, sort_keys=True)
        await redis.lpush(keys.processing, raw)
        await redis.hset(keys.processing_meta, message_id, encoded_metadata)
        await redis.hset(keys.retry_meta, message_id, encoded_metadata)

        heartbeat = await queue.heartbeat_run(
            queue._lease_handle(message_id, queue_attempt_id, owner_token),
            worker_id=worker_id,
        )

        assert heartbeat.status == "heartbeat"
        assert heartbeat.heartbeat_at is not None
        result = await queue.reclaim_expired_leases(
            visibility_timeout_seconds=60,
            max_attempts=3,
            now=heartbeat.heartbeat_at + 3_600,
        )

        assert result == {"reclaimed": 0, "dead_lettered": 0}
        assert await redis.lrange(keys.processing, 0, -1) == [raw]
        assert await redis.lrange(keys.queued, 0, -1) == []
        assert await redis.lrange(keys.dead_letter, 0, -1) == []
        current_metadata = json.loads(
            await redis.hget(keys.processing_meta, message_id)
        )
        assert current_metadata["heartbeat_at"] == pytest.approx(
            heartbeat.heartbeat_at,
            rel=0,
            abs=0.001,
        )
    finally:
        await redis.delete(*redis_keys)
        await redis.aclose()


@pytest.mark.asyncio
async def test_real_redis_protocol_v2_lease_is_invisible_to_v1_reclaimer(
    monkeypatch,
):
    redis_url = _required_env(REDIS_URL_ENV)
    suffix = uuid.uuid4().hex[:12]
    queue_prefix = f"ai-platform:test:mixed-reclaimer:{suffix}"

    class Settings:
        queue_key_prefix = queue_prefix

    async def get_redis():
        return Redis.from_url(redis_url, decode_responses=True)

    monkeypatch.setattr(queue, "get_settings", lambda: Settings())
    monkeypatch.setattr(queue, "get_redis", get_redis)
    redis = Redis.from_url(redis_url, decode_responses=True)
    keys = queue.get_queue_keys()
    redis_keys = tuple(getattr(keys, field.name) for field in fields(keys))
    payload = QueueRunPayload(
        tenant_id=f"tenant-{suffix}",
        workspace_id=f"workspace-{suffix}",
        user_id=f"user-{suffix}",
        session_id=f"session-{suffix}",
        run_id=f"run-{suffix}",
        agent_id=f"agent-{suffix}",
        skill_id=f"skill-{suffix}",
        file_ids=[],
        input={"message": "mixed reclaimer integration"},
        executor_type="fake",
        skill_version="version-a",
        release_decision={
            "schema_version": "ai-platform.skill-release-decision.v1",
            "selected_version": "version-a",
        },
        skill_manifests=[
            {
                "skill_id": f"skill-{suffix}",
                "content_hash": "version-a",
            }
        ],
    )
    raw = payload.model_dump_json()
    try:
        await redis.delete(*redis_keys)
        await redis.rpush(keys.queued, raw)
        message = await queue.lease_run(
            timeout_seconds=1,
            worker_id=f"worker-{suffix}",
        )

        assert message is not None
        metadata = json.loads(
            await redis.hget(keys.processing_meta, message.queue_message_id)
        )
        assert metadata["lease_protocol_version"] == 2
        assert metadata["owner_token_v2"] == message.owner_token
        assert "owner_token" not in metadata

        legacy_result = json.loads(
            await redis.eval(
                LEGACY_REQUEUE_IDENTITY_PROBE_SCRIPT,
                4,
                keys.processing_meta,
                keys.retry_meta,
                keys.processing,
                keys.queued,
                message.queue_message_id,
                message.attempt_id,
                message.owner_token,
                raw,
            )
        )

        assert legacy_result == {"status": "stale_owner"}
        assert await redis.lrange(keys.processing, 0, -1) == [raw]
        assert await redis.lrange(keys.queued, 0, -1) == []
    finally:
        await redis.delete(*redis_keys)
        await redis.aclose()


@pytest.mark.asyncio
async def test_real_redis_reclaim_normalizes_abandoned_future_leases_then_expires_them(
    monkeypatch,
):
    redis_url = _required_env(REDIS_URL_ENV)
    suffix = uuid.uuid4().hex[:12]
    queue_prefix = f"ai-platform:test:future-reclaim:{suffix}"

    class Settings:
        queue_key_prefix = queue_prefix

    async def get_redis():
        return Redis.from_url(redis_url, decode_responses=True)

    monkeypatch.setattr(queue, "get_settings", lambda: Settings())
    monkeypatch.setattr(queue, "get_redis", get_redis)
    redis = Redis.from_url(redis_url, decode_responses=True)
    keys = queue.get_queue_keys()
    redis_keys = tuple(getattr(keys, field.name) for field in fields(keys))
    seeded: list[tuple[str, str, int]] = []
    try:
        await redis.delete(*redis_keys)
        future_epoch = await _redis_epoch(redis) + 3_600
        for attempts in (1, 3):
            marker = str(attempts)
            payload = QueueRunPayload(
                tenant_id=f"tenant-{suffix}-{marker}",
                workspace_id=f"workspace-{suffix}-{marker}",
                user_id=f"user-{suffix}-{marker}",
                session_id=f"session-{suffix}-{marker}",
                run_id=f"run-{suffix}-{marker}",
                agent_id=f"agent-{suffix}-{marker}",
                skill_id=f"skill-{suffix}-{marker}",
                file_ids=[],
                input={"message": "future reclaim integration"},
                executor_type="fake",
                skill_version="version-a",
                release_decision={
                    "schema_version": "ai-platform.skill-release-decision.v1",
                    "selected_version": "version-a",
                },
                skill_manifests=[
                    {
                        "skill_id": f"skill-{suffix}-{marker}",
                        "content_hash": "version-a",
                    }
                ],
            )
            raw = payload.model_dump_json()
            message_id = queue.message_id_for_raw(raw)
            metadata = {
                "message_id": message_id,
                "raw": raw,
                "attempts": attempts,
                "attempt_id": f"qat_{marker * 64}",
                "lease_protocol_version": 2,
                "owner_token_v2": f"qown_{marker * 64}",
                "leased_at": future_epoch,
                "heartbeat_at": future_epoch,
                "worker_id": f"worker-{suffix}-{marker}",
                "tenant_id": payload.tenant_id,
                "user_id": payload.user_id,
                "run_id": payload.run_id,
            }
            encoded = json.dumps(metadata, sort_keys=True)
            await redis.lpush(keys.processing, raw)
            await redis.hset(keys.processing_meta, message_id, encoded)
            await redis.hset(keys.retry_meta, message_id, encoded)
            seeded.append((raw, message_id, attempts))

        normalized_before = await _redis_epoch(redis)
        normalized = await queue.reclaim_expired_leases(
            visibility_timeout_seconds=1,
            max_attempts=3,
            now=future_epoch + 3_600,
        )
        normalized_after = await _redis_epoch(redis)

        assert normalized == {"reclaimed": 0, "dead_lettered": 0}
        for _raw, message_id, _attempts in seeded:
            for metadata_key in (keys.processing_meta, keys.retry_meta):
                repaired = json.loads(await redis.hget(metadata_key, message_id))
                assert normalized_before - 0.001 <= repaired["heartbeat_at"] <= normalized_after + 0.001
                assert normalized_before - 0.001 <= repaired["leased_at"] <= normalized_after + 0.001

        await asyncio.sleep(1.1)
        expired = await queue.reclaim_expired_leases(
            visibility_timeout_seconds=1,
            max_attempts=3,
            now=await _redis_epoch(redis),
        )

        assert expired == {"reclaimed": 1, "dead_lettered": 1}
        assert len(await redis.lrange(keys.queued, 0, -1)) == 1
        assert len(await redis.lrange(keys.dead_letter, 0, -1)) == 1
    finally:
        await redis.delete(*redis_keys)
        await redis.aclose()


@pytest.mark.asyncio
async def test_real_worker_heartbeat_fails_closed_then_converges_after_postgres_rollback(
    monkeypatch,
):
    dsn = _required_env(POSTGRES_DSN_ENV)
    redis_url = _required_env(REDIS_URL_ENV)
    suffix = uuid.uuid4().hex[:12]
    schema_name = f"worker_heartbeat_{suffix}"
    queue_prefix = f"ai-platform:test:worker-heartbeat:{suffix}"
    worker_id = f"worker-{suffix}"
    queue_message_id = "b" * 64
    queue_attempt_id = f"qat_{'a' * 64}"
    owner_token = f"qown_{'c' * 64}"
    visibility_timeout_seconds = 60
    admin = await psycopg.AsyncConnection.connect(
        dsn,
        autocommit=True,
        row_factory=dict_row,
    )
    redis = Redis.from_url(redis_url, decode_responses=True)
    transaction_factory = _transaction_factory(dsn, schema_name)
    redis_keys: tuple[str, ...] = ()
    try:
        initial_heartbeat_at = datetime.fromtimestamp(
            await _redis_epoch(redis) - visibility_timeout_seconds,
            tz=timezone.utc,
        )
        await admin.execute(
            sql.SQL("create schema {}").format(sql.Identifier(schema_name))
        )
        await schema_migrations.apply_migrations(
            transaction_factory=transaction_factory,
            index_connection_factory=_index_connection_factory(dsn, schema_name),
        )
        async with transaction_factory() as conn:
            tenant_id, run_id, attempt_id = await _seed_running_attempt(
                conn,
                suffix=suffix,
                queue_attempt_id=queue_attempt_id,
                queue_message_id=queue_message_id,
                worker_id=worker_id,
                heartbeat_at=initial_heartbeat_at,
                lease_expires_at=initial_heartbeat_at
                + timedelta(seconds=visibility_timeout_seconds),
            )

        class Settings:
            queue_key_prefix = queue_prefix

        async def get_redis():
            return Redis.from_url(redis_url, decode_responses=True)

        monkeypatch.setattr(queue, "get_settings", lambda: Settings())
        monkeypatch.setattr(queue, "get_redis", get_redis)
        monkeypatch.setattr(
            queue,
            "_now",
            lambda: initial_heartbeat_at.timestamp() + 86_400,
        )
        monkeypatch.setattr(
            attempt_lifecycle_application,
            "_service",
            RunAttemptLifecycleService(persistence=run_attempt_persistence),
        )
        keys = queue.get_queue_keys()
        redis_keys = tuple(getattr(keys, field.name) for field in fields(keys))
        await redis.delete(*redis_keys)
        await redis.hset(
            keys.processing_meta,
            queue_message_id,
            json.dumps(
                {
                    "message_id": queue_message_id,
                    "attempt_id": queue_attempt_id,
                    "owner_token": owner_token,
                    "worker_id": worker_id,
                    "tenant_id": tenant_id,
                    "run_id": run_id,
                    "leased_at": initial_heartbeat_at.timestamp(),
                    "heartbeat_at": initial_heartbeat_at.timestamp(),
                },
                sort_keys=True,
            ),
        )
        message = queue.QueueMessage(
            raw="real-heartbeat-run",
            payload={"tenant_id": tenant_id, "run_id": run_id},
            message_id=queue._lease_handle(
                queue_message_id,
                queue_attempt_id,
                owner_token,
            ),
            queue_message_id=queue_message_id,
            attempt_id=queue_attempt_id,
            owner_token=owner_token,
            leased_at=initial_heartbeat_at.timestamp(),
            delivery_attempt=1,
        )

        uncommitted_write: dict[str, datetime] = {}

        @asynccontextmanager
        async def transaction_that_cannot_commit():
            async with transaction_factory() as conn:
                yield conn
                staged = await (
                    await conn.execute(
                        """
                        select last_heartbeat_at, lease_expires_at
                        from run_attempts
                        where tenant_id = %s and id = %s
                        """,
                        (tenant_id, attempt_id),
                    )
                ).fetchone()
                assert staged is not None
                uncommitted_write.update(staged)
                raise OSError("postgres commit unavailable")

        monkeypatch.setattr(worker_main, "transaction", transaction_that_cannot_commit)
        ownership_lost = asyncio.Event()
        heartbeat_before = await _redis_epoch(redis)

        await worker_main._heartbeat_until_done(
            message,
            worker_id,
            0,
            visibility_timeout_seconds,
            ownership_lost,
        )
        heartbeat_after = await _redis_epoch(redis)

        assert ownership_lost.is_set()
        accepted_heartbeat_at = uncommitted_write["last_heartbeat_at"]
        accepted_epoch = accepted_heartbeat_at.timestamp()
        assert heartbeat_before - 0.001 <= accepted_epoch <= heartbeat_after + 0.001
        assert uncommitted_write["lease_expires_at"] == accepted_heartbeat_at + timedelta(
            seconds=visibility_timeout_seconds
        )
        redis_metadata = json.loads(
            await redis.hget(keys.processing_meta, queue_message_id)
        )
        assert redis_metadata["heartbeat_at"] == pytest.approx(
            accepted_epoch,
            rel=0,
            abs=0.001,
        )
        assert (
            float(await redis.hget(keys.worker_heartbeat, worker_id))
            == pytest.approx(accepted_epoch, rel=0, abs=0.001)
        )
        future_epoch = heartbeat_after + 3_600
        redis_metadata["leased_at"] = future_epoch
        redis_metadata["heartbeat_at"] = future_epoch
        await redis.hset(
            keys.processing_meta,
            queue_message_id,
            json.dumps(redis_metadata, sort_keys=True),
        )
        await redis.hset(
            keys.worker_heartbeat,
            worker_id,
            str(future_epoch),
        )
        repair_before = await _redis_epoch(redis)
        late_heartbeat = await queue.heartbeat_run(
            message.message_id,
            worker_id=worker_id,
        )
        repair_after = await _redis_epoch(redis)
        assert late_heartbeat.status == "heartbeat"
        assert late_heartbeat.heartbeat_at is not None
        assert (
            repair_before - 0.001
            <= late_heartbeat.heartbeat_at
            <= repair_after + 0.001
        )
        repaired_metadata = json.loads(
            await redis.hget(keys.processing_meta, queue_message_id)
        )
        assert (
            repaired_metadata["heartbeat_at"]
            == pytest.approx(late_heartbeat.heartbeat_at, rel=0, abs=0.001)
        )
        assert (
            float(await redis.hget(keys.worker_heartbeat, worker_id))
            == pytest.approx(late_heartbeat.heartbeat_at, rel=0, abs=0.001)
        )
        assert repaired_metadata["leased_at"] == pytest.approx(
            late_heartbeat.heartbeat_at,
            rel=0,
            abs=0.001,
        )
        async with transaction_factory() as conn:
            stored = await (
                await conn.execute(
                    """
                    select last_heartbeat_at, lease_expires_at
                    from run_attempts
                    where tenant_id = %s and id = %s
                    """,
                    (tenant_id, attempt_id),
                )
            ).fetchone()
        assert stored == {
            "last_heartbeat_at": initial_heartbeat_at,
            "lease_expires_at": initial_heartbeat_at
            + timedelta(seconds=visibility_timeout_seconds),
        }

        durable_heartbeat_at = datetime.fromtimestamp(
            late_heartbeat.heartbeat_at,
            tz=timezone.utc,
        )
        async with transaction_factory() as conn:
            await heartbeat_worker_run_attempt(
                conn,
                tenant_id=tenant_id,
                run_id=run_id,
                queue_attempt_id=queue_attempt_id,
                queue_message_id=queue_message_id,
                worker_id=worker_id,
                last_heartbeat_at=durable_heartbeat_at,
                lease_expires_at=durable_heartbeat_at
                + timedelta(seconds=visibility_timeout_seconds),
            )
        async with transaction_factory() as conn:
            converged = await (
                await conn.execute(
                    """
                    select last_heartbeat_at, lease_expires_at
                    from run_attempts
                    where tenant_id = %s and id = %s
                    """,
                    (tenant_id, attempt_id),
                )
            ).fetchone()
        assert converged == {
            "last_heartbeat_at": durable_heartbeat_at,
            "lease_expires_at": durable_heartbeat_at
            + timedelta(seconds=visibility_timeout_seconds),
        }
        regression_conn = await psycopg.AsyncConnection.connect(
            dsn,
            autocommit=True,
            options=f"-c search_path={schema_name}",
            row_factory=dict_row,
        )
        try:
            with pytest.raises(
                psycopg.errors.CheckViolation,
                match="run_attempt_heartbeat_regression",
            ):
                await regression_conn.execute(
                    """
                    update run_attempts
                    set last_heartbeat_at = %s
                    where tenant_id = %s and id = %s
                    """,
                    (
                        durable_heartbeat_at - timedelta(seconds=1),
                        tenant_id,
                        attempt_id,
                    ),
                )
            with pytest.raises(
                psycopg.errors.CheckViolation,
                match="run_attempt_lease_expiry_regression",
            ):
                await regression_conn.execute(
                    """
                    update run_attempts
                    set lease_expires_at = null
                    where tenant_id = %s and id = %s
                    """,
                    (tenant_id, attempt_id),
                )
            with pytest.raises(
                psycopg.errors.CheckViolation,
                match="run_attempt_heartbeat_regression",
            ):
                await regression_conn.execute(
                    """
                    update run_attempts
                    set status = 'failed',
                        owner_generation = owner_generation + 1,
                        last_heartbeat_at = %s,
                        finished_at = now(),
                        terminal_reason = 'regressing_terminal_transition'
                    where tenant_id = %s and id = %s
                    """,
                    (
                        durable_heartbeat_at - timedelta(seconds=1),
                        tenant_id,
                        attempt_id,
                    ),
                )
            unchanged = await (
                await regression_conn.execute(
                    """
                    select status, last_heartbeat_at, lease_expires_at
                    from run_attempts
                    where tenant_id = %s and id = %s
                    """,
                    (tenant_id, attempt_id),
                )
            ).fetchone()
            assert unchanged == {
                "status": "running",
                "last_heartbeat_at": durable_heartbeat_at,
                "lease_expires_at": durable_heartbeat_at
                + timedelta(seconds=visibility_timeout_seconds),
            }
        finally:
            await regression_conn.close()

        future_durable_heartbeat = durable_heartbeat_at + timedelta(hours=1)
        async with transaction_factory() as conn:
            await conn.execute(
                """
                update run_attempts
                set last_heartbeat_at = %s,
                    lease_expires_at = %s
                where tenant_id = %s and id = %s
                """,
                (
                    future_durable_heartbeat,
                    future_durable_heartbeat
                    + timedelta(seconds=visibility_timeout_seconds),
                    tenant_id,
                    attempt_id,
                ),
            )
        with pytest.raises(
            schema_migrations.SchemaMigrationError,
            match="run_attempt_future_heartbeat_requires_remediation",
        ):
            async with transaction_factory() as conn:
                await schema_migrations._require_no_future_open_attempt_heartbeats(
                    conn
                )
        with pytest.raises(
            RepositoryConflictError,
            match="run_attempt_worker_heartbeat_conflict",
        ):
            async with transaction_factory() as conn:
                await heartbeat_worker_run_attempt(
                    conn,
                    tenant_id=tenant_id,
                    run_id=run_id,
                    queue_attempt_id=queue_attempt_id,
                    queue_message_id=queue_message_id,
                    worker_id=worker_id,
                    last_heartbeat_at=durable_heartbeat_at,
                    lease_expires_at=durable_heartbeat_at
                    + timedelta(seconds=visibility_timeout_seconds),
                )
    finally:
        if redis_keys:
            await redis.delete(*redis_keys)
        await redis.aclose()
        await admin.execute(
            sql.SQL("drop schema if exists {} cascade").format(
                sql.Identifier(schema_name)
            )
        )
        await admin.close()
