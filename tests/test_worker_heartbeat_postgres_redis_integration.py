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
from app.runs.api import heartbeat_worker_run_attempt
from app.runs.domain.execution_spec import (
    EXECUTION_SPEC_SCHEMA_VERSION,
    compile_execution_spec,
)
from app.runs.infrastructure.postgres import (
    create_run_attempt,
    transition_run_attempt,
)
import app.worker_main as worker_main


POSTGRES_DSN_ENV = "AI_PLATFORM_S0A_SCHEMA_TEST_DSN"
REDIS_URL_ENV = "AI_PLATFORM_SSE_REDIS_TEST_URL"


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        pytest.skip(f"{name} is not configured")
    return value


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
    initial_heartbeat_at = datetime(2026, 8, 30, 7, 0, tzinfo=timezone.utc)
    advanced_epoch = initial_heartbeat_at.timestamp() + 10
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
        monkeypatch.setattr(queue, "_now", lambda: advanced_epoch)
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

        @asynccontextmanager
        async def transaction_that_cannot_commit():
            async with transaction_factory() as conn:
                yield conn
                raise OSError("postgres commit unavailable")

        monkeypatch.setattr(worker_main, "transaction", transaction_that_cannot_commit)
        ownership_lost = asyncio.Event()

        await worker_main._heartbeat_until_done(
            message,
            worker_id,
            0,
            visibility_timeout_seconds,
            ownership_lost,
        )

        assert ownership_lost.is_set()
        redis_metadata = json.loads(
            await redis.hget(keys.processing_meta, queue_message_id)
        )
        assert redis_metadata["heartbeat_at"] == advanced_epoch
        assert (
            float(await redis.hget(keys.worker_heartbeat, worker_id)) == advanced_epoch
        )
        await redis.hset(
            keys.worker_heartbeat,
            worker_id,
            str(advanced_epoch + 5),
        )
        monkeypatch.setattr(queue, "_now", lambda: advanced_epoch - 1)
        late_heartbeat = await queue.heartbeat_run(
            message.message_id,
            worker_id=worker_id,
        )
        assert late_heartbeat == queue.QueueHeartbeatOutcome(
            "heartbeat",
            heartbeat_at=advanced_epoch,
        )
        assert (
            json.loads(await redis.hget(keys.processing_meta, queue_message_id))[
                "heartbeat_at"
            ]
            == advanced_epoch
        )
        assert (
            float(await redis.hget(keys.worker_heartbeat, worker_id))
            == advanced_epoch + 5
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
            redis_metadata["heartbeat_at"],
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
