from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from unittest.mock import patch
import uuid

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
import pytest
from redis.asyncio import Redis

from tests.support.db_transactions import event_loop_policy as event_loop_policy

from app import repositories, schema_migrations
from app.bootstrap import run_lifecycle
from app.bootstrap.run_attempt_lifecycle import build_run_attempt_lifecycle_service
from app.run_admission_terminalization import terminalize_enqueue_failure_with_v4
from app.runs.infrastructure.postgres import load_current_terminal_event_fact
from app.platform.public_payload import sanitize_public_payload, sanitize_public_text
from app.routes import runtime_callbacks
from app.runtime.sandbox.contracts import ExecutorCallbackEvent
from app.tool_permission_lifecycle import (
    cancel_run_with_v4,
    complete_run_with_v4,
    fail_run_with_v4,
)
from app.streaming.application.worker_publication_v4 import WorkerV4Capabilities, admit_v4_stream, publish_run_event
from app.streaming.api import build_v4_control, stream_key
from app.streaming.redis import RedisStreamBridge, StreamAuthority, StreamTransportUnavailable
from app.streaming.infrastructure.v4 import (
    V4CallbackItem,
    V4ProjectionError,
    V4RedisStreamBridge,
    append_callback_v4_rows,
    append_run_terminal_v4_row,
    opaque_message_id,
    project_public_envelope_v4,
    project_public_v4,
)
from app.streaming.domain.transport import canonical_json_bytes
from app.streaming.infrastructure.worker_v4 import (
    PostgresV4PendingAdmissions,
    PostgresWorkerEventPersistence,
    RedisV4PublicationTransport,
)


POSTGRES_DSN_ENV = "AI_PLATFORM_S0A_SCHEMA_TEST_DSN"
REDIS_URL_ENV = "AI_PLATFORM_SSE_REDIS_TEST_URL"
_MESSAGE_EVENT_TYPES = frozenset(
    {
        "message.started",
        "message.delta",
        "message.completed",
        "thinking.started",
        "thinking.completed",
        "model.completed",
        "tool.started",
        "tool.completed",
        "tool.failed",
        "tool.denied",
        "subagent.started",
        "subagent.progress",
        "subagent.completed",
        "subagent.failed",
        "subagent.cancelled",
    }
)


def _dsn() -> str:
    value = os.getenv(POSTGRES_DSN_ENV, "").strip()
    if not value:
        pytest.skip(f"{POSTGRES_DSN_ENV} is not configured")
    return value


def _redis_url() -> str:
    value = os.getenv(REDIS_URL_ENV, "").strip()
    if not value:
        pytest.skip(f"{REDIS_URL_ENV} is not configured")
    return value


def _callback_capabilities(dsn: str, schema_name: str, *, bridge=None) -> WorkerV4Capabilities:
    def factory():
        return _connection_factory(dsn, schema_name)

    return WorkerV4Capabilities(
        pending_admissions=object(),
        event_persistence=PostgresWorkerEventPersistence(
            factory,
            append_event=repositories.append_event,
            is_cancel_requested=repositories.is_cancel_requested,
            load_terminal_event_fact=load_current_terminal_event_fact,
        ),
        publication_transport=RedisV4PublicationTransport(bridge) if bridge is not None else object(),
    )


def _answer_callback(run, attempt, batch_id):
    from app.execution.api import ClaudeSdkAgentEventAdapter
    from app.runtime.kernel_contracts import AgentEvent

    adapter = ClaudeSdkAgentEventAdapter(run_id=run, attempt_id=attempt, sanitizer=sanitize_public_text, payload_sanitizer=sanitize_public_payload)
    return ExecutorCallbackEvent(
        session_id=f"s_{run[2:]}", run_id=run, attempt_id=attempt,
        callback_token_id=f"cbt:{run}:{attempt}", batch_id=batch_id,
        status="running", progress=20, new_message=None, state_patch={},
        events=[AgentEvent(**event.as_agent_event_fields()) for event in adapter.accept_answer_text("answer")],
    )


def _production_cancellation_use_case(conn):
    @asynccontextmanager
    async def transaction_factory():
        async with conn.transaction():
            yield conn

    settings = type(
        "CancellationSettings",
        (),
        {"ai_session_secret": "test-v4-authority-secret"},
    )()
    with (
        patch.object(run_lifecycle, "transaction", transaction_factory),
        patch.object(run_lifecycle, "get_settings", return_value=settings),
    ):
        return run_lifecycle.build_run_cancellation_use_case(
            attempt_lifecycle=build_run_attempt_lifecycle_service()
        )


async def _clear_seeded_stream_authority_for_cancellation(
    conn, *, tenant_id: str, run_id: str, attempt_id: str
) -> None:
    await conn.execute(
        "delete from sse_stream_authorities where tenant_id = %s and run_id = %s",
        (tenant_id, run_id),
    )
    run_cursor = await conn.execute(
        """
        select workspace_id, user_id, session_id, agent_id, skill_id, execution_kind
        from runs where tenant_id = %s and id = %s
        """,
        (tenant_id, run_id),
    )
    run_values = await run_cursor.fetchone()
    assert run_values is not None
    spec_json = json.dumps(
        {
            "schema_version": "ai-platform.execution-spec.v1",
            "tenant_id": tenant_id,
            "run_id": run_id,
            **run_values,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    await conn.execute(
        """
        update runs
        set status = 'queued', started_at = null, finished_at = null
        where tenant_id = %s and id = %s
        """,
        (tenant_id, run_id),
    )
    await conn.execute(
        """
        insert into run_attempts(
          id, tenant_id, run_id, ordinal, status, owner_kind, owner_id,
          queue_attempt_id, execution_spec_schema_version, execution_spec_json,
          execution_spec_canonical_json, execution_spec_sha256
        ) values (%s, %s, %s, 1, 'created', 'queue_worker', 'worker-cancel',
                  %s, 'ai-platform.execution-spec.v1', %s::jsonb, %s, %s)
        """,
        (
            attempt_id,
            tenant_id,
            run_id,
            f"queue_{attempt_id}",
            spec_json,
            spec_json,
            hashlib.sha256(spec_json.encode("utf-8")).hexdigest(),
        ),
    )
    await conn.execute(
        """
        update run_attempts
        set status = 'queued', owner_generation = owner_generation + 1,
            queue_message_id = 'message-cancel'
        where tenant_id = %s and id = %s
        """,
        (tenant_id, attempt_id),
    )


async def _request_owner_cancel(
    use_case, *, tenant_id: str, user_id: str, run_id: str
):
    result = await use_case.request_owner_cancel(
        tenant_id=tenant_id,
        owner_user_id=user_id,
        run_id=run_id,
    )
    return result.as_route_result() if result is not None else None


async def _request_admin_cancel(
    use_case, *, tenant_id: str, admin_user_id: str, run_id: str
):
    result = await use_case.request_admin_cancel(
        tenant_id=tenant_id,
        admin_user_id=admin_user_id,
        run_id=run_id,
    )
    return result.as_route_result() if result is not None else None


@asynccontextmanager
async def _connection_factory(dsn: str, schema_name: str):
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


async def _index_connection(dsn: str, schema_name: str):
    return await psycopg.AsyncConnection.connect(
        dsn,
        autocommit=True,
        options=f"-c search_path={schema_name}",
        row_factory=dict_row,
    )


async def _seed_run(conn: psycopg.AsyncConnection, suffix: str) -> tuple[str, str, str]:
    tenant = f"t_{suffix}"
    workspace = f"w_{suffix}"
    user = f"u_{suffix}"
    agent = f"a_{suffix}"
    skill = f"sk_{suffix}"
    session = f"s_{suffix}"
    run = f"r_{suffix}"
    attempt = f"att_{suffix}"
    await conn.execute("insert into tenants(id, name) values (%s, %s)", (tenant, tenant))
    await conn.execute(
        "insert into workspaces(id, tenant_id, name) values (%s, %s, %s)",
        (workspace, tenant, workspace),
    )
    await conn.execute(
        "insert into users(id, tenant_id, display_name) values (%s, %s, %s)",
        (user, tenant, user),
    )
    await conn.execute(
        "insert into agents(id, tenant_id, name, agent_type) values (%s, %s, %s, 'chat')",
        (agent, tenant, agent),
    )
    await conn.execute(
        "insert into skills(id, name, version, executor_type) values (%s, %s, '1', 'fake')",
        (skill, skill),
    )
    await conn.execute(
        "insert into sessions(id, tenant_id, workspace_id, user_id, agent_id, title) values (%s, %s, %s, %s, %s, %s)",
        (session, tenant, workspace, user, agent, session),
    )
    await conn.execute(
        "insert into runs(id, tenant_id, workspace_id, session_id, user_id, agent_id, skill_id, status) values (%s, %s, %s, %s, %s, %s, %s, 'running')",
        (run, tenant, workspace, session, user, agent, skill),
    )
    await conn.execute(
        """
        insert into sandbox_leases(
          id, tenant_id, workspace_id, user_id, session_id, run_id, attempt_id,
          trace_id, sandbox_mode, provider, status, expires_at, lease_payload_json
        ) values ('lease', %s, %s, %s, %s, %s, %s, %s, 'chat', 'fake', 'active',
                  now() + interval '15 minutes', jsonb_build_object('attempt_id', %s::text))
        """,
        (tenant, workspace, user, session, run, attempt, f"trace_{run}", attempt),
    )
    tenant_scope = f"scope_{suffix}"
    open_event_id = f"evt4_open_{suffix}"
    opening = build_v4_control(
        event_id=open_event_id,
        tenant_scope=tenant_scope,
        run_id=run,
        attempt_id=attempt,
        stream_incarnation=2,
        event_type="stream.open",
        payload={"design_id": "ai-platform.redis-streams-sse-event-channel.v4"},
        source={"kind": "stream_authority", "authority_id": open_event_id},
        emitted_at=datetime(2026, 8, 24, tzinfo=timezone.utc),
    )
    open_payload_bytes = canonical_json_bytes(opening).decode("utf-8")
    open_payload_digest = hashlib.sha256(open_payload_bytes.encode("utf-8")).hexdigest()
    await conn.execute(
        """
        insert into sse_stream_authorities(
          tenant_id, run_id, attempt_id, design_id, projection_version,
          tenant_scope, stream_incarnation, state, open_event_id,
          open_payload_bytes, open_payload_digest, authorization_epoch,
          revocation_state, admission_confirmed_at
        ) values (%s, %s, %s, 'ai-platform.redis-streams-sse-event-channel.v4',
                  'public-stream-v4', %s, 2, 'confirmed', %s, %s, %s, 4, 'active',
                  clock_timestamp())
        """,
        (
            tenant,
            run,
            attempt,
            tenant_scope,
            open_event_id,
            open_payload_bytes,
            open_payload_digest,
        ),
    )
    return tenant, run, attempt


@asynccontextmanager
async def _schema():
    dsn = _dsn()
    schema_name = f"streaming_v4_evidence_{uuid.uuid4().hex}"
    admin = await psycopg.AsyncConnection.connect(dsn, autocommit=True, row_factory=dict_row)
    try:
        await admin.execute(sql.SQL("create schema {}").format(sql.Identifier(schema_name)))

        def factory():
            return _connection_factory(dsn, schema_name)

        def index_factory():
            return _index_connection(dsn, schema_name)

        await schema_migrations.apply_migrations(
            transaction_factory=factory,
            index_connection_factory=index_factory,
        )
        async with factory() as conn:
            async with conn.transaction():
                ids = await _seed_run(conn, uuid.uuid4().hex[:12])
        yield dsn, schema_name, ids
    finally:
        await admin.execute(sql.SQL("drop schema if exists {} cascade").format(sql.Identifier(schema_name)))
        await admin.close()


def _authority(tenant: str, run: str, attempt: str, *, incarnation: int = 2) -> StreamAuthority:
    return StreamAuthority(
        tenant_id=tenant,
        run_id=run,
        attempt_id=attempt,
        tenant_scope=f"scope_{tenant[2:]}",
        stream_incarnation=incarnation,
        state="confirmed",
        open_event_id="open",
        open_payload_bytes="{}",
        open_payload_digest="digest",
        authorization_epoch=4,
        revocation_state="active",
    )


def _metadata(
    tenant: str,
    run: str,
    attempt: str,
    event_type: str,
    *,
    incarnation: int = 2,
) -> dict[str, object]:
    return {
        "version": 1,
        "attempt_id": attempt,
        "stream_incarnation": incarnation,
        "authorization_epoch": 4,
        "message_id": opaque_message_id(tenant, run) if event_type in _MESSAGE_EVENT_TYPES else None,
        "execution_lease_id": "lease",
    }


async def _insert_v4_row(
    conn,
    *,
    tenant: str,
    run: str,
    attempt: str,
    sequence: int,
    event_id: str,
    event_type: str = "message.delta",
    payload: dict[str, object] | None = None,
    incarnation: int = 2,
) -> None:
    if payload is None:
        payload = (
            {"delta": event_id}
            if event_type == "message.delta"
            else {
                "terminal_event_id": event_id,
                "hydrate_required": True,
            }
        )
    payload = {**payload, "__stream_v4": _metadata(tenant, run, attempt, event_type, incarnation=incarnation)}
    await conn.execute(
        """
        insert into run_events(
          id, tenant_id, run_id, trace_id, schema_version, sequence,
          event_type, stage, message, severity, visible_to_user, payload_json
        ) values (%s, %s, %s, %s, 'ai-platform.event-envelope.v1', %s,
                  %s, 'agent_kernel', '', 'info', true, %s::jsonb)
        """,
        (event_id, tenant, run, f"trace_{run}", sequence, event_type, json.dumps(payload)),
    )


async def _redis_stream(tenant: str, run: str, *, incarnation: int = 2):
    client = Redis.from_url(_redis_url(), decode_responses=True)
    key = stream_key(
        tenant_scope_value=f"scope_{tenant[2:]}",
        run_id=run,
        stream_incarnation=incarnation,
    )
    state_key = f"{key}:state"
    await client.delete(key, state_key)
    bridge = V4RedisStreamBridge(RedisStreamBridge(publish_client=client))
    open_event_id = f"evt4_open_{tenant[2:]}"
    await bridge.append(build_v4_control(
        event_id=open_event_id, tenant_scope=f"scope_{tenant[2:]}", run_id=run,
        attempt_id=f"att_{tenant[2:]}", stream_incarnation=incarnation,
        event_type="stream.open",
        payload={"design_id": "ai-platform.redis-streams-sse-event-channel.v4"},
        source={"kind": "stream_authority", "authority_id": open_event_id},
        emitted_at=datetime(2026, 8, 24, tzinfo=timezone.utc),
    ))
    return client, key, bridge


@pytest.mark.asyncio
async def test_enqueue_failure_creates_authority_and_terminal_row_atomically():
    async with _schema() as (dsn, schema_name, (tenant, run, _attempt)):
        def factory():
            return _connection_factory(dsn, schema_name)

        user_id = f"u_{tenant[2:]}"
        admissions = PostgresV4PendingAdmissions(
            factory,
            authority_secret="test-v4-authority-secret",
        )

        async with factory() as conn:
            await conn.execute(
                "update runs set status = 'queued', finished_at = null, error_code = null, error_message = null where tenant_id = %s and id = %s",
                (tenant, run),
            )
            await conn.execute(
                "delete from run_events where tenant_id = %s and run_id = %s",
                (tenant, run),
            )
            await conn.execute(
                "delete from sse_stream_authorities where tenant_id = %s and run_id = %s",
                (tenant, run),
            )

        class MissingTerminalRow:
            async def append_terminal_row(self, *_args, **_kwargs):
                return None

        rollback_capabilities = WorkerV4Capabilities(
            pending_admissions=admissions,
            event_persistence=MissingTerminalRow(),
            publication_transport=object(),
        )
        with pytest.raises(RuntimeError, match="enqueue_failure_v4_terminal_row_missing"):
            async with factory() as conn:
                await terminalize_enqueue_failure_with_v4(
                    rollback_capabilities,
                    conn,
                    tenant_id=tenant,
                    user_id=user_id,
                    run_id=run,
                    trace_id=f"trace_{run}",
                )

        async with factory() as conn:
            run_row = await (
                await conn.execute(
                    "select status from runs where tenant_id = %s and id = %s",
                    (tenant, run),
                )
            ).fetchone()
            authority_row = await (
                await conn.execute(
                    "select 1 from sse_stream_authorities where tenant_id = %s and run_id = %s",
                    (tenant, run),
                )
            ).fetchone()
        assert run_row == {"status": "queued"}
        assert authority_row is None

        event_persistence = PostgresWorkerEventPersistence(
            factory,
            append_event=repositories.append_event,
            is_cancel_requested=repositories.is_cancel_requested,
            load_terminal_event_fact=load_current_terminal_event_fact,
        )
        capabilities = WorkerV4Capabilities(
            pending_admissions=admissions,
            event_persistence=event_persistence,
            publication_transport=object(),
        )
        async with factory() as conn:
            progress = await terminalize_enqueue_failure_with_v4(
                capabilities,
                conn,
                tenant_id=tenant,
                user_id=user_id,
                run_id=run,
                trace_id=f"trace_{run}",
            )
        assert progress.did_transition is True

        async with factory() as conn:
            committed = await (
                await conn.execute(
                    """
                    select r.status, a.attempt_id, a.state,
                           count(distinct e.id) filter (
                             where e.event_type = 'run.failed'
                           ) as terminal_rows
                    from runs r
                    join sse_stream_authorities a
                      on a.tenant_id = r.tenant_id and a.run_id = r.id
                    left join run_events e
                      on e.tenant_id = r.tenant_id and e.run_id = r.id
                    where r.tenant_id = %s and r.id = %s
                    group by r.status, a.attempt_id, a.state
                    """,
                    (tenant, run),
                )
            ).fetchone()
        assert committed == {
            "status": "failed",
            "attempt_id": f"enqueue_failure_{run}",
            "state": "admission_pending",
            "terminal_rows": 1,
        }

        assert await publish_run_event(capabilities, tenant_id=tenant, run_id=run) is False


@pytest.mark.asyncio
async def test_pending_admission_rolls_back_then_immediate_admission_confirms():
    async with _schema() as (dsn, schema_name, (tenant, run, attempt)):
        def factory():
            return _connection_factory(dsn, schema_name)

        admissions = PostgresV4PendingAdmissions(
            factory,
            authority_secret="test-v4-authority-secret",
        )
        async with factory() as conn:
            await conn.execute(
                "delete from sse_stream_authorities where tenant_id = %s and run_id = %s",
                (tenant, run),
            )

        with pytest.raises(RuntimeError, match="rollback-after-admission"):
            async with factory() as conn:
                await admissions.prepare_pending_authority_in_transaction(
                    conn,
                    tenant_id=tenant,
                    run_id=run,
                    attempt_id=attempt,
                )
                raise RuntimeError("rollback-after-admission")

        async with factory() as conn:
            absent = await conn.execute(
                "select 1 from sse_stream_authorities where tenant_id = %s and run_id = %s",
                (tenant, run),
            )
            assert await absent.fetchone() is None

        async with factory() as conn:
            pending = await admissions.prepare_pending_authority_in_transaction(
                conn,
                tenant_id=tenant,
                run_id=run,
                attempt_id=attempt,
            )

        client = Redis.from_url(_redis_url(), decode_responses=True)
        key = stream_key(
            tenant_scope_value=pending.tenant_scope,
            run_id=run,
            stream_incarnation=pending.stream_incarnation,
        )
        state_key = f"{key}:state"
        await client.delete(key, state_key)
        try:
            capabilities = WorkerV4Capabilities(
                pending_admissions=admissions,
                event_persistence=object(),
                publication_transport=RedisV4PublicationTransport(
                    V4RedisStreamBridge(RedisStreamBridge(publish_client=client))
                ),
            )
            for _ in range(2):
                admitted = await admit_v4_stream(capabilities, tenant_id=tenant, run_id=run, attempt_id=attempt)
                assert admitted.open_event_id == pending.open_event_id
                assert admitted.state == "confirmed"
            assert await client.xlen(key) == 1

            async with factory() as conn:
                row = await conn.execute(
                    """
                    select state, attempt_id, stream_incarnation, open_event_id,
                           open_payload_digest
                    from sse_stream_authorities
                    where tenant_id = %s and run_id = %s
                    """,
                    (tenant, run),
                )
                authority = await row.fetchone()
            assert authority == {
                "state": "confirmed",
                "attempt_id": attempt,
                "stream_incarnation": pending.stream_incarnation,
                "open_event_id": pending.open_event_id,
                "open_payload_digest": pending.open_payload_digest,
            }
        finally:
            await client.delete(key, state_key)
            await client.aclose()


@pytest.mark.asyncio
async def test_real_callback_append_commits_facts_without_publication_state():
    async with _schema() as (dsn, schema_name, (tenant, run, attempt)):
        item = V4CallbackItem(callback_index=0, batch_index=0, event_type="message.delta", payload={"delta": "answer"}, message_id=opaque_message_id(tenant, run))
        async with _connection_factory(dsn, schema_name) as conn:
            rows = await append_callback_v4_rows(conn, tenant_id=tenant, run_id=run, attempt_id=attempt, batch_id="batch-real", items=(item,), authority=_authority(tenant, run, attempt), execution_lease_id="lease")
        async with _connection_factory(dsn, schema_name) as conn:
            row = await (await conn.execute("select * from run_events where id = %s", (rows[0]["id"],))).fetchone()
        assert row["event_type"] == "message.delta" and row["visible_to_user"] is True
        assert row["payload_json"]["delta"] == "answer"
        assert not any(key.startswith("stream_publication_") for key in row)
        assert "publication_state" not in row["payload_json"]["__stream_v4"]


@pytest.mark.asyncio
async def test_real_callback_handler_rolls_back_receipt_and_v4_rows_together(monkeypatch):
    from fastapi import HTTPException

    from app.execution.api import ClaudeSdkAgentEventAdapter
    from app.runtime.kernel_contracts import AgentEvent

    async with _schema() as (dsn, schema_name, (tenant, run, attempt)):
        async with _connection_factory(dsn, schema_name) as conn:
            async with conn.transaction():
                await conn.execute(
                    "update sandbox_leases set lease_payload_json = jsonb_build_object('attempt_id', %s::text) where id = 'lease'",
                    (attempt,),
                )

        adapter = ClaudeSdkAgentEventAdapter(
            run_id=run,
            attempt_id=attempt,
            sanitizer=sanitize_public_text,
            payload_sanitizer=sanitize_public_payload,
        )
        callback = ExecutorCallbackEvent(
            session_id=f"s_{run[2:]}",
            run_id=run,
            attempt_id=attempt,
            callback_token_id=f"cbt:{run}:{attempt}",
            batch_id="batch-handler-rollback",
            status="running",
            progress=20,
            new_message=None,
            state_patch={},
            events=[AgentEvent(**adapter.accept_answer_text("answer")[0].as_agent_event_fields())],
        )
        original_list_leases = runtime_callbacks.repositories.list_current_sandbox_runtime_leases_for_attempt
        lease_checks = 0

        async def list_leases_with_final_loss(conn, **kwargs):
            nonlocal lease_checks
            lease_checks += 1
            if lease_checks == 1:
                return await original_list_leases(conn, **kwargs)
            return []

        monkeypatch.setattr(runtime_callbacks, "transaction", lambda: _connection_factory(dsn, schema_name))
        monkeypatch.setattr(
            runtime_callbacks.repositories,
            "list_current_sandbox_runtime_leases_for_attempt",
            list_leases_with_final_loss,
        )

        with pytest.raises(HTTPException) as exc_info:
            await runtime_callbacks.record_executor_callback(
                callback,
                capabilities=_callback_capabilities(dsn, schema_name),
            )

        assert exc_info.value.detail == "sandbox_runtime_attempt_inactive"
        assert lease_checks == 2
        async with _connection_factory(dsn, schema_name) as conn:
            rows = await conn.execute(
                "select event_type from run_events where tenant_id = %s and run_id = %s order by sequence",
                (tenant, run),
            )
            assert await rows.fetchall() == []


@pytest.mark.asyncio
async def test_real_callback_handler_duplicate_reuses_facts_and_stream_receipt(monkeypatch):
    async with _schema() as (dsn, schema_name, (tenant, run, attempt)):
        client, key, bridge = await _redis_stream(tenant, run)
        try:
            callback = _answer_callback(run, attempt, "batch-handler-duplicate")
            monkeypatch.setattr(runtime_callbacks, "transaction", lambda: _connection_factory(dsn, schema_name))
            capabilities = _callback_capabilities(dsn, schema_name, bridge=bridge)
            first = await runtime_callbacks.record_executor_callback(callback, capabilities=capabilities)
            first_stream = await client.xrange(key)
            async with _connection_factory(dsn, schema_name) as conn:
                first_rows = await (await conn.execute("select * from run_events order by sequence")).fetchall()
            second = await runtime_callbacks.record_executor_callback(callback, capabilities=capabilities)
            async with _connection_factory(dsn, schema_name) as conn:
                rows = await (await conn.execute("select * from run_events order by sequence")).fetchall()
                receipts = await (await conn.execute("select count(*) as count from run_event_batches")).fetchone()
            assert first == {"accepted": True, "batch_id": callback.batch_id, "event_count": 3}
            assert second == {**first, "deduplicated": True}
            assert rows == first_rows
            assert receipts["count"] == 1
            assert await client.xrange(key) == first_stream
            envelopes = [json.loads(fields["envelope"]) for _, fields in first_stream]
            assert [event["event_type"] for event in envelopes] == ["stream.open", "message.started", "message.delta"]
            assert envelopes[2]["payload"]["delta"] == "answer"
            assert [event["event_id"] for event in envelopes[1:]] == [row["id"] for row in rows if row["visible_to_user"] and "__stream_v4" in row["payload_json"]]
        finally:
            await client.delete(key, f"{key}:state")
            await client.aclose()


@pytest.mark.asyncio
async def test_real_callback_handler_commits_facts_before_redis_outage_and_retries(monkeypatch):
    from fastapi import HTTPException

    async with _schema() as (dsn, schema_name, (tenant, run, attempt)):
        observed = []
        class FailingRedis:
            async def eval(self, *args):
                async with _connection_factory(dsn, schema_name) as observer:
                    rows = await (await observer.execute("select id from run_events where visible_to_user = true and payload_json ? '__stream_v4' order by sequence for update nowait")).fetchall()
                    observed.append([row["id"] for row in rows])
                raise StreamTransportUnavailable("synthetic private diagnostic")

        callback = _answer_callback(run, attempt, "batch-handler-outage")
        monkeypatch.setattr(runtime_callbacks, "transaction", lambda: _connection_factory(dsn, schema_name))
        with pytest.raises(HTTPException) as caught:
            await runtime_callbacks.record_executor_callback(callback, capabilities=_callback_capabilities(dsn, schema_name, bridge=V4RedisStreamBridge(RedisStreamBridge(publish_client=FailingRedis()))))
        assert caught.value.status_code == 503
        assert caught.value.detail == "callback_stream_unavailable"
        assert len(observed) == 1 and len(observed[0]) == 2
        async with _connection_factory(dsn, schema_name) as conn:
            before = await (await conn.execute("select * from run_events order by sequence")).fetchall()
        client, key, bridge = await _redis_stream(tenant, run)
        try:
            accepted = await runtime_callbacks.record_executor_callback(callback, capabilities=_callback_capabilities(dsn, schema_name, bridge=bridge))
            assert accepted["deduplicated"] is True
            async with _connection_factory(dsn, schema_name) as conn:
                assert await (await conn.execute("select * from run_events order by sequence")).fetchall() == before
            stream_rows = await client.xrange(key)
            assert [json.loads(fields["envelope"])["event_id"] for _, fields in stream_rows[1:]] == observed[0]
        finally:
            await client.delete(key, f"{key}:state")
            await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ("succeeded", "cancelled", "cancel_requested"))
async def test_run_event_cannot_overtake_a_committed_callback_before_redis_append(monkeypatch, status):
    import asyncio

    async with _schema() as (dsn, schema_name, (tenant, run, attempt)):
        client, key, bridge = await _redis_stream(tenant, run)
        committed, release = asyncio.Event(), asyncio.Event()
        publish_callback = runtime_callbacks.publish_callback_rows
        task = None

        async def paused_publish(*args, **kwargs):
            committed.set()
            await release.wait()
            return await publish_callback(*args, **kwargs)

        monkeypatch.setattr(runtime_callbacks, "transaction", lambda: _connection_factory(dsn, schema_name))
        monkeypatch.setattr(runtime_callbacks, "publish_callback_rows", paused_publish)
        capabilities = _callback_capabilities(dsn, schema_name, bridge=bridge)
        try:
            task = asyncio.create_task(runtime_callbacks.record_executor_callback(
                _answer_callback(run, attempt, "batch-terminal-race"), capabilities=capabilities,
            ))
            await asyncio.wait_for(committed.wait(), 2)
            async with _connection_factory(dsn, schema_name) as conn:
                before = await (await conn.execute("select * from run_events order by sequence")).fetchall()
                if status == "cancel_requested":
                    from app.streaming.infrastructure.v4 import append_run_cancel_requested_v4_row

                    await conn.execute("update runs set cancel_requested_at = now() where id = %s", (run,))
                    await append_run_cancel_requested_v4_row(
                        conn, tenant_id=tenant, run_id=run, source="user", trace_ref=None,
                    )
                else:
                    await conn.execute("update runs set status = %s where id = %s", (status, run))
            assert await publish_run_event(capabilities, tenant_id=tenant, run_id=run) is False
            assert await client.xlen(key) == 1
            assert await client.hget(f"{key}:state", "phase") == "open"
            release.set()
            assert (await asyncio.wait_for(task, 2))["accepted"] is True
            assert await publish_run_event(capabilities, tenant_id=tenant, run_id=run) is True
            entries = await client.xrange(key)
            assert [json.loads(fields["envelope"])["event_type"] for _, fields in entries] == [
                "stream.open", "message.started", "message.delta", f"run.{status}",
            ] + ([] if status == "cancel_requested" else ["stream.end"])
            async with _connection_factory(dsn, schema_name) as conn:
                rows = await (await conn.execute("select * from run_events order by sequence")).fetchall()
                assert rows[:len(before)] == before
                run_row = await (await conn.execute("select status, cancel_requested_at from runs where id = %s", (run,))).fetchone()
                assert run_row["status"] == ("running" if status == "cancel_requested" else status)
                if status == "cancel_requested":
                    assert run_row["cancel_requested_at"] is not None
            if status == "cancel_requested":
                frozen = await capabilities.event_persistence.load_latest_run_event(tenant_id=tenant, run_id=run)
                async with _connection_factory(dsn, schema_name) as conn:
                    await _insert_v4_row(
                        conn, tenant=tenant, run=run, attempt=attempt,
                        sequence=rows[-1]["sequence"] + 1, event_id="evt4_later_callback",
                    )
                assert await capabilities.event_persistence.load_latest_run_event(tenant_id=tenant, run_id=run) == frozen
        finally:
            release.set()
            if task is not None:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            await client.delete(key, f"{key}:state")
            await client.aclose()


@pytest.mark.asyncio
async def test_callback_publishes_a_committed_cancellation_before_its_later_batch():
    from app.streaming.application.worker_publication_v4 import publish_callback_rows
    from app.streaming.infrastructure.v4 import append_run_cancel_requested_v4_row

    async with _schema() as (dsn, schema_name, (tenant, run, attempt)):
        client, key, bridge = await _redis_stream(tenant, run)
        capabilities = _callback_capabilities(dsn, schema_name, bridge=bridge)
        try:
            async with _connection_factory(dsn, schema_name) as conn:
                await conn.execute("update runs set cancel_requested_at = now() where id = %s", (run,))
                await append_run_cancel_requested_v4_row(conn, tenant_id=tenant, run_id=run, source="user")
                await _insert_v4_row(conn, tenant=tenant, run=run, attempt=attempt, sequence=2, event_id="evt4_after_cancel")
                row = await (await conn.execute("select * from run_events where id = 'evt4_after_cancel'")).fetchone()
            await publish_callback_rows(capabilities, (row,), authority=_authority(tenant, run, attempt))
            assert [json.loads(fields["envelope"])["event_type"] for _, fields in await client.xrange(key)] == [
                "stream.open", "run.cancel_requested", "message.delta",
            ]
            assert await publish_run_event(capabilities, tenant_id=tenant, run_id=run) is True
            assert await client.xlen(key) == 3
        finally:
            await client.delete(key, f"{key}:state")
            await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ("succeeded", "cancelled", "failed"))
async def test_real_terminal_publish_and_restart_need_no_live_execution_lease(status):
    async with _schema() as (dsn, schema_name, (tenant, run, attempt)):
        async with _connection_factory(dsn, schema_name) as conn:
            await conn.execute("update runs set status = %s where id = %s", (status, run))
            await conn.execute("update sandbox_leases set status = 'released', expires_at = now() where run_id = %s", (run,))
        client, key, bridge = await _redis_stream(tenant, run)
        try:
            for _ in range(2):
                capabilities = _callback_capabilities(dsn, schema_name, bridge=bridge)
                assert await publish_run_event(capabilities, tenant_id=tenant, run_id=run) is True
            entries = await client.xrange(key)
            assert [json.loads(fields["envelope"])["event_type"] for _, fields in entries] == ["stream.open", f"run.{status}", "stream.end"]
            async with _connection_factory(dsn, schema_name) as conn:
                rows = await (await conn.execute("select * from run_events where event_type = %s", (f"run.{status}",))).fetchall()
            assert len(rows) == 1
            assert rows[0]["id"].startswith("evt4_run_")
            assert "stream_publication_state" not in rows[0]
            await client.delete(key)
            assert await publish_run_event(capabilities, tenant_id=tenant, run_id=run) is False
            assert await client.xlen(key) == 0
        finally:
            await client.delete(key, f"{key}:state")
            await client.aclose()


@pytest.mark.asyncio
async def test_private_or_unknown_event_values_fail_closed():
    async with _schema() as (dsn, schema_name, (tenant, run, attempt)):
        authority = _authority(tenant, run, attempt)
        async with _connection_factory(dsn, schema_name) as conn:
            async with conn.transaction():
                await _insert_v4_row(
                    conn,
                    tenant=tenant,
                    run=run,
                    attempt=attempt,
                    sequence=1,
                    event_id="evt4_private",
                    payload={"delta": "ok", "raw_command": "secret"},
                )
                private = await conn.execute(
                    "select id, tenant_id, run_id, sequence, event_type, visible_to_user, payload_json, created_at from run_events where id = 'evt4_private'"
                )
                row = await private.fetchone()
                assert project_public_v4(row, authority=authority) is None
        unknown = {
            "schema": "ai-platform.stream-event.v4",
            "event_id": "evt4_unknown",
            "tenant_scope": authority.tenant_scope,
            "run_id": run,
            "attempt_id": attempt,
            "message_id": None,
            "seq": 1,
            "event_type": "private.executor.raw",
            "stream_incarnation": 2,
            "replayable": True,
            "trace_ref": None,
            "causation_event_id": None,
            "emitted_at": "2026-08-20T00:00:00Z",
            "projection_version": "public-stream-v4",
            "payload": {},
            "source": {"kind": "run_event", "run_event_id": "evt4_unknown", "sequence": 1},
        }
        assert project_public_envelope_v4(unknown) is None


@pytest.mark.asyncio
async def test_complete_run_writes_one_v4_terminal_row_from_the_run_fact():
    async with _schema() as (_dsn_value, schema_name, ids):
        tenant, run, _attempt = ids
        async with _connection_factory(_dsn(), schema_name) as conn:
            assert await complete_run_with_v4(
                conn,
                capabilities=_callback_capabilities(_dsn_value, schema_name),
                tenant_id=tenant,
                run_id=run,
                result_json={"message": "done"},
            ) is True
            events_cursor = await conn.execute(
                """
                select id, sequence, event_type, payload_json
                from run_events
                where tenant_id = %s and run_id = %s
                order by sequence
                """,
                (tenant, run),
            )
            events = await events_cursor.fetchall()
            terminal_events = [
                event for event in events if event["event_type"] == "run.succeeded"
            ]
            assert len(terminal_events) == 1
            terminal = terminal_events[0]
            assert terminal["id"] == terminal["payload_json"]["terminal_event_id"]
            assert terminal["payload_json"]["hydrate_required"] is True
            metadata = terminal["payload_json"]["__stream_v4"]
            assert "terminal_intent_id" not in metadata
            assert metadata["execution_lease_id"] is None
            assert len({event["sequence"] for event in terminal_events}) == 1


@pytest.mark.parametrize("status", ["failed", "cancelled"])
@pytest.mark.asyncio
async def test_failed_and_cancelled_run_producers_are_exact_once_and_conflict_closed(status):
    async with _schema() as (_dsn_value, schema_name, ids):
        tenant, run, attempt = ids
        async with _connection_factory(_dsn(), schema_name) as conn:
            if status == "failed":
                progress = await fail_run_with_v4(
                    conn,
                    capabilities=_callback_capabilities(_dsn_value, schema_name),
                    tenant_id=tenant,
                    run_id=run,
                    error_code="executor_private_exception",
                    error_message="private path C:/tenant/secret",
                )
            else:
                progress = await cancel_run_with_v4(
                    conn,
                    capabilities=_callback_capabilities(_dsn_value, schema_name),
                    tenant_id=tenant,
                    run_id=run,
                )
            assert progress.did_transition is True
            assert progress.status == status

            cursor = await conn.execute(
                """
                select id, sequence, event_type, payload_json
                from run_events
                where tenant_id = %s and run_id = %s and event_type = %s
                order by sequence
                """,
                (tenant, run, f"run.{status}"),
            )
            rows = await cursor.fetchall()
            assert len(rows) == 1
            terminal = rows[0]
            assert terminal["id"] == terminal["payload_json"]["terminal_event_id"]
            metadata = terminal["payload_json"]["__stream_v4"]
            assert "terminal_intent_id" not in metadata
            assert metadata["execution_lease_id"] is None
            if status == "failed":
                assert terminal["payload_json"]["code"] == "run_failed"
                assert terminal["payload_json"]["detail"] is None
                assert "executor_private_exception" not in str(terminal["payload_json"])
                assert "C:/tenant/secret" not in str(terminal["payload_json"])

            retried = await append_run_terminal_v4_row(
                conn,
                tenant_id=tenant,
                run_id=run,
                attempt_id=attempt,
                status=status,
                terminal_event_id=terminal["id"],
                error_code="executor_private_exception" if status == "failed" else None,
                reason_code="user_cancelled" if status == "cancelled" else None,
            )
            assert retried is not None
            assert retried["sequence"] == terminal["sequence"]

            with pytest.raises(V4ProjectionError, match="v4_callback_existing_row_conflict"):
                await append_run_terminal_v4_row(
                    conn,
                    tenant_id=tenant,
                    run_id=run,
                    attempt_id=attempt,
                    status="cancelled" if status == "failed" else "failed",
                    terminal_event_id=terminal["id"],
                    error_code="run_failed" if status == "cancelled" else None,
                    reason_code="user_cancelled" if status == "failed" else None,
                )

            count_cursor = await conn.execute(
                """
                select count(*) as event_count
                from run_events
                where tenant_id = %s and run_id = %s and event_type = %s
                """,
                (tenant, run, f"run.{status}"),
            )
            assert (await count_cursor.fetchone())["event_count"] == 1


@pytest.mark.asyncio
async def test_production_cancel_composition_preserves_owner_fences_order_and_retry():
    async with _schema() as (_dsn_value, schema_name, ids):
        tenant, run, attempt = ids
        suffix = tenant[2:]
        user = f"u_{suffix}"
        async with _connection_factory(_dsn(), schema_name) as conn:
            await _clear_seeded_stream_authority_for_cancellation(
                conn,
                tenant_id=tenant,
                run_id=run,
                attempt_id=attempt,
            )
            use_case = _production_cancellation_use_case(conn)
            assert await _request_owner_cancel(
                use_case,
                tenant_id=tenant,
                user_id="different-user",
                run_id=run,
            ) is None
            assert await _request_admin_cancel(
                use_case,
                tenant_id="different-tenant",
                admin_user_id=user,
                run_id=run,
            ) is None
            untouched_cursor = await conn.execute(
                """
                select
                  (select count(*) from run_events where tenant_id = %s and run_id = %s) as event_count,
                  (select count(*) from audit_logs where tenant_id = %s and target_id = %s) as audit_count
                """,
                (tenant, run, tenant, run),
            )
            assert await untouched_cursor.fetchone() == {
                "event_count": 0,
                "audit_count": 0,
            }

            first = await _request_owner_cancel(
                use_case,
                tenant_id=tenant,
                user_id=user,
                run_id=run,
            )
            assert first is not None
            assert first["status"] == "cancelled"

            await conn.execute(
                """
                insert into run_tool_permission_requests(
                  id, tenant_id, workspace_id, user_id, session_id, run_id,
                  tool_id, tool_call_id, status
                ) values (%s, %s, %s, %s, %s, %s, 'tool', 'call-retry', 'pending')
                """,
                (
                    f"permission_{suffix}",
                    tenant,
                    f"w_{suffix}",
                    user,
                    f"s_{suffix}",
                    run,
                ),
            )
            second = await _request_owner_cancel(
                use_case,
                tenant_id=tenant,
                user_id=user,
                run_id=run,
            )
            assert second is not None
            assert second["status"] == "cancelled"

            permission_cursor = await conn.execute(
                """
                select status from run_tool_permission_requests
                where tenant_id = %s and run_id = %s and tool_call_id = 'call-retry'
                """,
                (tenant, run),
            )
            assert await permission_cursor.fetchone() == {"status": "cancelled"}

            cursor = await conn.execute(
                """
                select sequence, event_type, payload_json
                from run_events
                where tenant_id = %s and run_id = %s
                  and event_type in (
                    'run_cancel_requested', 'run.cancel_requested',
                    'run_cancelled', 'run.cancelled'
                  )
                order by sequence
                """,
                (tenant, run),
            )
            rows = await cursor.fetchall()
            assert [row["event_type"] for row in rows] == [
                "run.cancel_requested",
                "run_cancelled",
                "run.cancelled",
            ]
            assert [row["sequence"] for row in rows] == sorted(
                row["sequence"] for row in rows
            )
            assert rows[0]["payload_json"]["source"] == "user"

            audit_cursor = await conn.execute(
                """
                select user_id, action, target_id, payload_json
                from audit_logs
                where tenant_id = %s and target_id = %s and action = 'run.cancel'
                order by created_at, id
                """,
                (tenant, run),
            )
            audits = await audit_cursor.fetchall()
            expected_audit = {
                "user_id": user,
                "action": "run.cancel",
                "target_id": run,
                "payload_json": {
                    "run_id": run,
                    "result_status": "cancelled",
                    "requested_by_role": "owner",
                },
            }
            assert audits == [expected_audit, expected_audit]


@pytest.mark.asyncio
async def test_production_cancel_composition_preserves_admin_self_cancel_facts():
    async with _schema() as (_dsn_value, schema_name, ids):
        tenant, run, attempt = ids
        user = f"u_{tenant[2:]}"
        async with _connection_factory(_dsn(), schema_name) as conn:
            await _clear_seeded_stream_authority_for_cancellation(
                conn,
                tenant_id=tenant,
                run_id=run,
                attempt_id=attempt,
            )
            use_case = _production_cancellation_use_case(conn)
            result = await _request_admin_cancel(
                use_case,
                tenant_id=tenant,
                admin_user_id=user,
                run_id=run,
            )
            assert result is not None
            assert result["status"] == "cancelled"

            event_cursor = await conn.execute(
                """
                select payload_json
                from run_events
                where tenant_id = %s and run_id = %s
                  and event_type = 'run.cancel_requested'
                """,
                (tenant, run),
            )
            event = await event_cursor.fetchone()
            assert event is not None
            assert event["payload_json"]["source"] == "system"

            audit_cursor = await conn.execute(
                """
                select user_id, action, target_id, payload_json
                from audit_logs
                where tenant_id = %s and target_id = %s
                  and action = 'admin.run.cancel'
                """,
                (tenant, run),
            )
            audit = await audit_cursor.fetchone()
            assert audit == {
                "user_id": user,
                "action": "admin.run.cancel",
                "target_id": run,
                "payload_json": {
                    "run_id": run,
                    "target_user_id": user,
                    "result_status": "cancelled",
                },
            }


@pytest.mark.asyncio
async def test_production_cancel_composition_rolls_back_run_events_and_audit():
    async with _schema() as (_dsn_value, schema_name, ids):
        tenant, run, attempt = ids
        user = f"u_{tenant[2:]}"
        async with _connection_factory(_dsn(), schema_name) as conn:
            await _clear_seeded_stream_authority_for_cancellation(
                conn,
                tenant_id=tenant,
                run_id=run,
                attempt_id=attempt,
            )
            real_append_audit_log = run_lifecycle.repositories.append_audit_log

            async def fail_after_audit_write(*args, **kwargs):
                await real_append_audit_log(*args, **kwargs)
                raise RuntimeError("force_cancel_rollback")

            with patch.object(
                run_lifecycle.repositories,
                "append_audit_log",
                fail_after_audit_write,
            ):
                use_case = _production_cancellation_use_case(conn)
            with pytest.raises(RuntimeError, match="force_cancel_rollback"):
                await _request_owner_cancel(
                    use_case,
                    tenant_id=tenant,
                    user_id=user,
                    run_id=run,
                )

            run_cursor = await conn.execute(
                """
                select status, cancel_requested_at, permission_terminalization_target
                from runs where tenant_id = %s and id = %s
                """,
                (tenant, run),
            )
            run_row = await run_cursor.fetchone()
            assert run_row == {
                "status": "queued",
                "cancel_requested_at": None,
                "permission_terminalization_target": None,
            }
            evidence_cursor = await conn.execute(
                """
                select
                  (select count(*) from run_events
                   where tenant_id = %s and run_id = %s
                     and event_type in ('run_cancel_requested', 'run.cancel_requested',
                                        'run_cancelled', 'run.cancelled')) as event_count,
                  (select count(*) from audit_logs
                   where tenant_id = %s and target_id = %s) as audit_count
                """,
                (tenant, run, tenant, run),
            )
            assert await evidence_cursor.fetchone() == {
                "event_count": 0,
                "audit_count": 0,
            }


@pytest.mark.asyncio
async def test_real_answer_receipt_reconstructs_committed_facts_without_a_stream():
    from app.streaming.infrastructure.v4 import load_answer_by_receipt
    from tests.test_streaming_answer_receipt import _answer_fixture

    async with _schema() as (dsn, schema, (tenant, run, attempt)):
        rows, receipt = _answer_fixture()
        message_id = opaque_message_id(tenant, run)
        receipt["message_id"] = message_id
        async with _connection_factory(dsn, schema) as conn:
            for row in rows:
                metadata = row["payload_json"]["__stream_v4"]
                metadata.update(attempt_id=attempt, message_id=message_id)
                await conn.execute(
                    "insert into run_events(id, tenant_id, run_id, sequence, event_type, stage, visible_to_user, payload_json) values (%s, %s, %s, %s, %s, 'agent_kernel', true, %s::jsonb)",
                    (row["id"], tenant, run, row["sequence"], row["event_type"], json.dumps(row["payload_json"])),
                )
        async with _connection_factory(dsn, schema) as conn:
            answer = await load_answer_by_receipt(conn, tenant_id=tenant, run_id=run, attempt_id=attempt, receipt=receipt)
            assert answer.text == "hello world"
