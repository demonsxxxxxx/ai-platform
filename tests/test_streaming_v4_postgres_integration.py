from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import json
import os
import uuid

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
import pytest
from redis.asyncio import Redis

from app import repositories, schema_migrations
from app.repositories import complete_run
from app.platform.public_payload import sanitize_public_payload, sanitize_public_text
from app.routes import runtime_callbacks
from app.bootstrap.run_lifecycle import PostgresRunCancellationEventWriter
from app.runs.application.cancellation import RunCancellationUseCase
from app.runs.infrastructure.postgres import PostgresRunCancellationPersistence
from app.routes.lambchat_compat import _recover_v4_attach_gap
from app.runtime.sandbox.contracts import ExecutorCallbackEvent
from app.streaming.api import stream_key
from app.streaming.redis import RedisStreamBridge, StreamAuthority, StreamTransportUnavailable
from app.streaming.v4 import (
    V4CallbackItem,
    V4ProjectionError,
    V4RedisStreamBridge,
    append_callback_v4_rows,
    append_run_terminal_v4_row,
    opaque_message_id,
    project_public_envelope_v4,
    project_public_v4,
    recover_v4_rows,
)
from app.streaming.worker_projection import publish_pending_v4_events


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


async def _request_owner_cancel(conn, *, tenant_id, user_id, run_id):
    @asynccontextmanager
    async def transaction_factory():
        async with conn.transaction():
            yield conn

    use_case = RunCancellationUseCase(
        transaction_factory=transaction_factory,
        persistence=PostgresRunCancellationPersistence(
            append_event=repositories.append_event,
            append_audit_log=repositories.append_audit_log,
            list_active_sandbox_leases=repositories.list_active_sandbox_leases_for_run,
        ),
        event_writer=PostgresRunCancellationEventWriter(),
        progress_terminalization=repositories.progress_run_tool_permission_terminalization,
    )
    result = await use_case.request_owner_cancel(
        tenant_id=tenant_id,
        owner_user_id=user_id,
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
          trace_id, sandbox_mode, provider, status, expires_at
        ) values ('lease', %s, %s, %s, %s, %s, %s, %s, 'chat', 'fake', 'active', now() + interval '15 minutes')
        """,
        (tenant, workspace, user, session, run, attempt, f"trace_{run}"),
    )
    await conn.execute(
        """
        insert into sse_stream_authorities(
          tenant_id, run_id, attempt_id, design_id, projection_version,
          tenant_scope, stream_incarnation, state, open_event_id,
          open_payload_bytes, open_payload_digest, authorization_epoch,
          revocation_state
        ) values (%s, %s, %s, 'ai-platform.redis-streams-sse-event-channel.v4',
                  'public-stream-v4', %s, 2, 'confirmed', %s, '{}', 'digest', 4, 'active')
        """,
        (tenant, run, attempt, f"scope_{suffix}", f"open_{suffix}"),
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
        "publication_state": "pending",
        "publication_attempts": 0,
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
    state: str = "pending",
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
          event_type, stage, message, severity, visible_to_user, payload_json,
          stream_publication_state, stream_publication_attempts,
          stream_publication_next_attempt_at
        ) values (%s, %s, %s, %s, 'ai-platform.event-envelope.v1', %s,
                  %s, 'agent_kernel', '', 'info', true, %s::jsonb,
                  %s, 0, now())
        """,
        (event_id, tenant, run, f"trace_{run}", sequence, event_type, json.dumps(payload), state),
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
    await client.hset(state_key, mapping={"phase": "open"})
    return client, key, V4RedisStreamBridge(RedisStreamBridge(publish_client=client))


@pytest.mark.asyncio
async def test_real_callback_append_registers_pending_v4_row():
    async with _schema() as (dsn, schema_name, (tenant, run, attempt)):
        authority = _authority(tenant, run, attempt)
        item = V4CallbackItem(
            callback_index=0,
            batch_index=0,
            event_type="message.delta",
            payload={"delta": "adapter answer"},
            message_id="msg_run_attempt",
        )
        async with _connection_factory(dsn, schema_name) as conn:
            async with conn.transaction():
                rows = await append_callback_v4_rows(
                    conn,
                    tenant_id=tenant,
                    run_id=run,
                    attempt_id=attempt,
                    batch_id="batch-real",
                    items=(item,),
                    authority=authority,
                    execution_lease_id="lease",
                )
        assert rows[0]["stream_publication_state"] == "pending"
        async with _connection_factory(dsn, schema_name) as conn:
            result = await conn.execute(
                "select event_type, visible_to_user, stream_publication_state from run_events where id = %s",
                (rows[0]["id"],),
            )
            stored = await result.fetchone()
        assert stored == {
            "event_type": "message.delta",
            "visible_to_user": True,
            "stream_publication_state": "pending",
        }


@pytest.mark.asyncio
async def test_real_callback_handler_rolls_back_receipt_and_v4_rows_together(monkeypatch):
    from fastapi import HTTPException

    from app.execution.api import ClaudeSdkAgentEventAdapter
    from app.runtime.kernel_contracts import AgentEvent

    async with _schema() as (dsn, schema_name, (tenant, run, attempt)):
        async with _connection_factory(dsn, schema_name) as conn:
            async with conn.transaction():
                await conn.execute(
                    "update sandbox_leases set lease_payload_json = jsonb_build_object('attempt_id', %s) where id = 'lease'",
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

        async def unexpected_publish(*_args, **_kwargs):
            raise AssertionError("publication must not run after the transaction rolls back")

        monkeypatch.setattr(runtime_callbacks, "transaction", lambda: _connection_factory(dsn, schema_name))
        monkeypatch.setattr(
            runtime_callbacks.repositories,
            "list_current_sandbox_runtime_leases_for_attempt",
            list_leases_with_final_loss,
        )
        monkeypatch.setattr(runtime_callbacks, "publish_pending_v4_events", unexpected_publish)

        with pytest.raises(HTTPException) as exc_info:
            await runtime_callbacks.record_executor_callback(callback)

        assert exc_info.value.detail == "sandbox_runtime_attempt_inactive"
        assert lease_checks == 2
        async with _connection_factory(dsn, schema_name) as conn:
            rows = await conn.execute(
                "select event_type from run_events where tenant_id = %s and run_id = %s order by sequence",
                (tenant, run),
            )
            assert await rows.fetchall() == []


@pytest.mark.asyncio
async def test_real_callback_handler_duplicate_reuses_published_v4_row(monkeypatch):
    async with _schema() as (dsn, schema_name, (tenant, run, attempt)):
        client, key, bridge = await _redis_stream(tenant, run)
        try:
            from app.execution.api import ClaudeSdkAgentEventAdapter
            from app.runtime.kernel_contracts import AgentEvent

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
                batch_id="batch-handler-duplicate",
                status="running",
                progress=20,
                new_message=None,
                state_patch={},
                events=[AgentEvent(**adapter.accept_answer_text("answer")[0].as_agent_event_fields())],
            )

            async def publish_pending(transaction_factory, *, limit):
                return await publish_pending_v4_events(
                    transaction_factory,
                    limit=limit,
                    bridge=bridge,
                )

            monkeypatch.setattr(
                runtime_callbacks,
                "transaction",
                lambda: _connection_factory(dsn, schema_name),
            )
            monkeypatch.setattr(runtime_callbacks, "publish_pending_v4_events", publish_pending)

            first = await runtime_callbacks.record_executor_callback(callback)
            async with _connection_factory(dsn, schema_name) as conn:
                first_row_cursor = await conn.execute(
                    """
                    select id, sequence, stream_publication_state, stream_publication_attempts
                    from run_events
                    where tenant_id = %s and run_id = %s and event_type = 'message.started'
                    """,
                    (tenant, run),
                )
                first_rows = await first_row_cursor.fetchall()
            assert first == {
                "accepted": True,
                "batch_id": "batch-handler-duplicate",
                "event_count": 2,
            }
            assert len(first_rows) == 1
            first_row = first_rows[0]
            assert first_row["stream_publication_state"] == "published"
            assert first_row["stream_publication_attempts"] == 1

            second = await runtime_callbacks.record_executor_callback(callback)
            async with _connection_factory(dsn, schema_name) as conn:
                row_cursor = await conn.execute(
                    """
                    select id, sequence, stream_publication_state, stream_publication_attempts
                    from run_events
                    where tenant_id = %s and run_id = %s and event_type = 'message.started'
                    order by sequence
                    """,
                    (tenant, run),
                )
                rows = await row_cursor.fetchall()
                receipt_cursor = await conn.execute(
                    """
                    select count(*) as count
                    from run_events
                    where tenant_id = %s and run_id = %s and event_type = 'executor_callback'
                    """,
                    (tenant, run),
                )
                receipts = await receipt_cursor.fetchone()
            stream_rows = await client.xrange(key, min="-", max="+")

            assert second == {
                "accepted": True,
                "batch_id": "batch-handler-duplicate",
                "event_count": 2,
                "deduplicated": True,
            }
            assert rows == [first_row]
            assert receipts["count"] == 1
            assert len(stream_rows) == 1
            assert json.loads(stream_rows[0][1]["envelope"])["event_id"] == first_row["id"]
        finally:
            await client.delete(key, f"{key}:state")
            await client.aclose()


@pytest.mark.asyncio
async def test_real_callback_handler_commits_pending_row_before_redis_outage(monkeypatch):
    async with _schema() as (dsn, schema_name, (tenant, run, attempt)):
        from app.execution.api import ClaudeSdkAgentEventAdapter
        from app.runtime.kernel_contracts import AgentEvent

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
            batch_id="batch-handler-transport",
            status="running",
            progress=20,
            new_message=None,
            state_patch={},
            events=[AgentEvent(**adapter.accept_answer_text("answer")[0].as_agent_event_fields())],
        )
        observed_at_bridge: list[dict[str, object]] = []

        class FailingPublisher:
            async def append(self, envelope):
                del envelope
                async with _connection_factory(dsn, schema_name) as observer:
                    result = await observer.execute(
                        """
                        select count(*) as count, min(stream_publication_state) as state
                        from run_events
                        where tenant_id = %s and run_id = %s and event_type = 'message.started'
                        """,
                        (tenant, run),
                    )
                    observed_at_bridge.append(await result.fetchone())
                raise StreamTransportUnavailable("redis unavailable")

        async def publish_pending(transaction_factory, *, limit):
            return await publish_pending_v4_events(
                transaction_factory,
                limit=limit,
                bridge=FailingPublisher(),
            )

        monkeypatch.setattr(
            runtime_callbacks,
            "transaction",
            lambda: _connection_factory(dsn, schema_name),
        )
        monkeypatch.setattr(runtime_callbacks, "publish_pending_v4_events", publish_pending)

        result = await runtime_callbacks.record_executor_callback(callback)

        assert result == {
            "accepted": True,
            "batch_id": "batch-handler-transport",
            "event_count": 2,
        }
        assert observed_at_bridge == [{"count": 1, "state": "pending"}]
        async with _connection_factory(dsn, schema_name) as conn:
            row_cursor = await conn.execute(
                """
                select id, sequence, stream_publication_state, stream_publication_attempts,
                       stream_publication_last_error, stream_publication_redis_id
                from run_events
                where tenant_id = %s and run_id = %s and event_type = 'message.started'
                """,
                (tenant, run),
            )
            rows = await row_cursor.fetchall()
            receipt_cursor = await conn.execute(
                """
                select count(*) as count
                from run_events
                where tenant_id = %s and run_id = %s and event_type = 'executor_callback'
                """,
                (tenant, run),
            )
            receipts = await receipt_cursor.fetchone()
        assert len(rows) == 1
        assert rows[0]["sequence"] > 0
        assert rows[0]["stream_publication_state"] == "pending"
        assert rows[0]["stream_publication_attempts"] == 1
        assert rows[0]["stream_publication_last_error"] == "StreamTransportUnavailable"
        assert rows[0]["stream_publication_redis_id"] is None
        assert receipts["count"] == 1


@pytest.mark.asyncio
async def test_real_pending_publisher_claims_and_orders_concurrent_run_rows():
    async with _schema() as (dsn, schema_name, (tenant, run, attempt)):
        async with _connection_factory(dsn, schema_name) as conn:
            async with conn.transaction():
                for sequence in range(1, 4):
                    await _insert_v4_row(
                        conn,
                        tenant=tenant,
                        run=run,
                        attempt=attempt,
                        sequence=sequence,
                        event_id=f"evt4_order_{sequence}",
                    )
        client, key, bridge = await _redis_stream(tenant, run)
        try:
            results = await asyncio.gather(
                *(
                    publish_pending_v4_events(
                        lambda: _connection_factory(dsn, schema_name),
                        limit=3,
                        bridge=bridge,
                    )
                    for _ in range(4)
                )
            )
            assert sum(results) == 3
            stream_rows = await client.xrange(key, min="-", max="+")
            assert [json.loads(fields["envelope"])["seq"] for _, fields in stream_rows] == [1, 2, 3]
            async with _connection_factory(dsn, schema_name) as conn:
                rows = await conn.execute(
                    "select sequence, stream_publication_state from run_events where run_id = %s order by sequence",
                    (run,),
                )
                assert await rows.fetchall() == [
                    {"sequence": 1, "stream_publication_state": "published"},
                    {"sequence": 2, "stream_publication_state": "published"},
                    {"sequence": 3, "stream_publication_state": "published"},
                ]
        finally:
            await client.delete(key, f"{key}:state")
            await client.aclose()


@pytest.mark.asyncio
async def test_real_pending_publisher_retries_same_event_after_pg_disposition_failure():
    async with _schema() as (dsn, schema_name, (tenant, run, attempt)):
        async with _connection_factory(dsn, schema_name) as conn:
            async with conn.transaction():
                await _insert_v4_row(
                    conn,
                    tenant=tenant,
                    run=run,
                    attempt=attempt,
                    sequence=1,
                    event_id="evt4_disposition",
                )
                await conn.execute(
                    """
                    create function fail_v4_disposition() returns trigger
                    language plpgsql as $$
                    begin
                      raise exception 'v4 disposition failure';
                    end;
                    $$
                    """
                )
                await conn.execute(
                    """
                    create trigger fail_v4_disposition_trigger
                    before update of stream_publication_state on run_events
                    for each row when (new.id = 'evt4_disposition')
                    execute function fail_v4_disposition()
                    """
                )
        client, key, bridge = await _redis_stream(tenant, run)
        try:
            with pytest.raises(psycopg.DatabaseError, match="v4 disposition failure"):
                await publish_pending_v4_events(
                    lambda: _connection_factory(dsn, schema_name),
                    limit=1,
                    bridge=bridge,
                )
            async with _connection_factory(dsn, schema_name) as conn:
                row = await conn.execute(
                    "select stream_publication_state from run_events where id = 'evt4_disposition'"
                )
                assert (await row.fetchone())["stream_publication_state"] == "pending"
            redis_rows = await client.xrange(key, min="-", max="+")
            assert len(redis_rows) == 1

            async with _connection_factory(dsn, schema_name) as conn:
                async with conn.transaction():
                    await conn.execute("drop trigger fail_v4_disposition_trigger on run_events")
                    await conn.execute("drop function fail_v4_disposition()")
            assert await publish_pending_v4_events(
                lambda: _connection_factory(dsn, schema_name),
                limit=1,
                bridge=bridge,
            ) == 1
            redis_rows = await client.xrange(key, min="-", max="+")
            assert [json.loads(fields["envelope"])["event_id"] for _, fields in redis_rows] == [
                "evt4_disposition",
                "evt4_disposition",
            ]
        finally:
            await client.delete(key, f"{key}:state")
            await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "event_type", "payload"),
    [
        (
            "succeeded",
            "run.succeeded",
            {"terminal_event_id": "evt4_terminal_restart", "hydrate_required": True},
        ),
        (
            "cancelled",
            "run.cancelled",
            {
                "terminal_event_id": "evt4_terminal_restart",
                "hydrate_required": True,
                "reason_code": "user_cancelled",
            },
        ),
    ],
)
async def test_real_terminal_publish_and_restart_need_no_live_execution_lease(
    status, event_type, payload
):
    async with _schema() as (dsn, schema_name, (tenant, run, attempt)):
        async with _connection_factory(dsn, schema_name) as conn:
            async with conn.transaction():
                await conn.execute("update runs set status = %s where id = %s", (status, run))
                await conn.execute(
                    "update sse_stream_authorities set state = 'terminal' where tenant_id = %s and run_id = %s",
                    (tenant, run),
                )
                await conn.execute(
                    "update sandbox_leases set status = 'released', expires_at = now() where run_id = %s",
                    (run,),
                )
                await _insert_v4_row(
                    conn,
                    tenant=tenant,
                    run=run,
                    attempt=attempt,
                    sequence=1,
                    event_id="evt4_terminal_restart",
                    event_type=event_type,
                    payload=payload,
                )
        client, key, bridge = await _redis_stream(tenant, run)
        try:
            assert await publish_pending_v4_events(
                lambda: _connection_factory(dsn, schema_name),
                limit=1,
                bridge=bridge,
            ) == 1
            async with _connection_factory(dsn, schema_name) as conn:
                async with conn.transaction():
                    await conn.execute(
                        """
                        update run_events
                        set stream_publication_state = 'pending', stream_publication_redis_id = null,
                            stream_publication_attempts = 0, stream_publication_next_attempt_at = now(),
                            payload_json = jsonb_set(
                              jsonb_set(payload_json, '{__stream_v4,publication_state}', to_jsonb('pending'::text)),
                              '{__stream_v4,publication_attempts}', to_jsonb(0)
                            )
                        where id = 'evt4_terminal_restart'
                        """
                    )
            assert await publish_pending_v4_events(
                lambda: _connection_factory(dsn, schema_name),
                limit=1,
                bridge=bridge,
            ) == 1
            rows = await client.xrange(key, min="-", max="+")
            assert len(rows) == 1
            assert json.loads(rows[0][1]["envelope"])["event_type"] == event_type
        finally:
            await client.delete(key, f"{key}:state")
            await client.aclose()


@pytest.mark.asyncio
async def test_recovery_route_rebinds_each_frame_cursor_and_exposes_public_only_data():
    async with _schema() as (dsn, schema_name, (tenant, run, attempt)):
        async with _connection_factory(dsn, schema_name) as conn:
            async with conn.transaction():
                await _insert_v4_row(
                    conn,
                    tenant=tenant,
                    run=run,
                    attempt=attempt,
                    sequence=1,
                    event_id="evt4_old_incarnation",
                    incarnation=1,
                )
                await _insert_v4_row(
                    conn,
                    tenant=tenant,
                    run=run,
                    attempt=attempt,
                    sequence=2,
                    event_id="evt4_current_incarnation",
                    incarnation=1,
                )
        client, key, bridge = await _redis_stream(tenant, run, incarnation=2)
        try:
            authority = _authority(tenant, run, attempt, incarnation=2)
            async with _connection_factory(dsn, schema_name) as conn:
                recovery = await _recover_v4_attach_gap(
                    conn,
                    bridge=bridge,
                    tenant_id=tenant,
                    run_id=run,
                    authority=authority,
                    after_sequence=0,
                    limit=8,
                )
            assert [item["seq"] for item in recovery.rows] == [1, 2]
            assert len(recovery.transport_cursors) == 2
            assert recovery.transport_cursors[0] != recovery.transport_cursors[1]
            for item in recovery.rows:
                assert item["schema"] == "ai-platform.public-run-stream-event.v4"
                assert "tenant_scope" not in item
                assert "attempt_id" not in item
                assert "source" not in item
            redis_rows = await client.xrange(key, min="-", max="+")
            assert [json.loads(fields["envelope"])["seq"] for _, fields in redis_rows] == [1, 2]
            for _, fields in redis_rows:
                public = project_public_envelope_v4(json.loads(fields["envelope"]))
                assert public is not None
                assert "tenant_scope" not in public
                assert "attempt_id" not in public
                assert "source" not in public
        finally:
            await client.delete(key, f"{key}:state")
            await client.aclose()


@pytest.mark.asyncio
async def test_recovery_scope_and_private_or_unknown_event_values_fail_closed():
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
                    "select id, tenant_id, run_id, sequence, event_type, visible_to_user, payload_json, stream_publication_state, created_at from run_events where id = 'evt4_private'"
                )
                row = await private.fetchone()
                assert project_public_v4(row, authority=authority) is None
            with pytest.raises(V4ProjectionError, match="authority_scope"):
                await recover_v4_rows(
                    conn,
                    tenant_id=tenant,
                    run_id=run,
                    authority=_authority(tenant, "other", attempt),
                )
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
async def test_migration_applies_exact_scoped_constraint_and_index_then_rolls_back_facts_only():
    async with _schema() as (dsn, schema_name, (tenant, run, attempt)):
        async with _connection_factory(dsn, schema_name) as conn:
            status = await schema_migrations.schema_status(conn)
            assert status["ready"] is True
            async with conn.transaction():
                await _insert_v4_row(
                    conn,
                    tenant=tenant,
                    run=run,
                    attempt=attempt,
                    sequence=1,
                    event_id="evt4_migration_fact",
                )
                index = await conn.execute(
                    """
                    select indexdef from pg_indexes
                    where schemaname = current_schema()
                      and indexname = 'idx_run_events_stream_publication_retry'
                    """
                )
                indexdef = (await index.fetchone())["indexdef"]
                normalized_index = " ".join(
                    indexdef.lower().replace("(", " ").replace(")", " ").split()
                )
                assert "run_events" in normalized_index
                assert "using btree" in normalized_index
                for column in ("stream_publication_next_attempt_at", "created_at", "id"):
                    assert column in normalized_index
                assert "visible_to_user = true" in normalized_index
                assert "stream_publication_state = 'pending'::text" in normalized_index
                constraint = await conn.execute(
                    """
                    select pg_get_constraintdef(oid, true) as definition
                    from pg_constraint
                    where conrelid = 'run_events'::regclass
                      and conname = 'chk_run_events_stream_publication_state'
                    """
                )
                definition = (await constraint.fetchone())["definition"]
                assert "pending" in definition and "published" in definition and "suppressed" in definition
                await schema_migrations.rollback_v4_publication_migration(conn)
                columns = await conn.execute(
                    """
                    select column_name from information_schema.columns
                    where table_schema = current_schema() and table_name = 'run_events'
                      and column_name like 'stream_publication_%'
                    """
                )
                assert await columns.fetchall() == []
                index_after = await conn.execute(
                    "select to_regclass('idx_run_events_stream_publication_retry') as name"
                )
                assert (await index_after.fetchone())["name"] is None
                fact = await conn.execute("select id, sequence from run_events where id = 'evt4_migration_fact'")
                assert await fact.fetchone() == {"id": "evt4_migration_fact", "sequence": 1}
        await schema_migrations.apply_migrations(
            transaction_factory=lambda: _connection_factory(dsn, schema_name),
            index_connection_factory=lambda: _index_connection(dsn, schema_name),
        )
        async with _connection_factory(dsn, schema_name) as conn:
            status = await schema_migrations.schema_status(conn)
            assert status["ready"] is True
            columns = await conn.execute(
                "select column_name from information_schema.columns where table_schema = current_schema() and table_name = 'run_events' and column_name = 'stream_publication_state'"
            )
            assert await columns.fetchone() == {"column_name": "stream_publication_state"}


@pytest.mark.asyncio
async def test_complete_run_writes_one_v4_terminal_row_with_existing_intent_identity():
    async with _schema() as (_dsn_value, schema_name, ids):
        tenant, run, _attempt = ids
        async with _connection_factory(_dsn(), schema_name) as conn:
            assert await complete_run(
                conn,
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
            assert metadata["terminal_intent_id"] == terminal["id"]
            assert metadata["execution_lease_id"] is None
            assert len({event["sequence"] for event in terminal_events}) == 1


@pytest.mark.parametrize("status", ["failed", "cancelled"])
@pytest.mark.asyncio
async def test_failed_and_cancelled_run_producers_are_exact_once_and_conflict_closed(status):
    async with _schema() as (_dsn_value, schema_name, ids):
        tenant, run, attempt = ids
        async with _connection_factory(_dsn(), schema_name) as conn:
            if status == "failed":
                progress = await repositories.fail_run(
                    conn,
                    tenant_id=tenant,
                    run_id=run,
                    error_code="executor_private_exception",
                    error_message="private path C:/tenant/secret",
                )
            else:
                progress = await repositories.cancel_run(
                    conn,
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
            assert metadata["terminal_intent_id"] == terminal["id"]
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
async def test_cancel_request_precedes_cancelled_terminal_and_is_exact_once():
    async with _schema() as (_dsn_value, schema_name, ids):
        tenant, run, _attempt = ids
        user = f"u_{tenant[2:]}"
        async with _connection_factory(_dsn(), schema_name) as conn:
            first = await _request_owner_cancel(
                conn,
                tenant_id=tenant,
                user_id=user,
                run_id=run,
            )
            second = await _request_owner_cancel(
                conn,
                tenant_id=tenant,
                user_id=user,
                run_id=run,
            )
            assert first is not None and second is not None
            progress = await repositories.cancel_run(conn, tenant_id=tenant, run_id=run)
            assert progress.did_transition is True
            assert progress.status == "cancelled"

            cursor = await conn.execute(
                """
                select sequence, event_type
                from run_events
                where tenant_id = %s and run_id = %s
                  and event_type in ('run.cancel_requested', 'run.cancelled')
                order by sequence
                """,
                (tenant, run),
            )
            rows = await cursor.fetchall()
            assert [row["event_type"] for row in rows] == [
                "run.cancel_requested",
                "run.cancelled",
            ]
            assert rows[0]["sequence"] < rows[1]["sequence"]


@pytest.mark.asyncio
async def test_cancel_request_and_terminal_rows_roll_back_with_run_state():
    async with _schema() as (_dsn_value, schema_name, ids):
        tenant, run, _attempt = ids
        user = f"u_{tenant[2:]}"
        async with _connection_factory(_dsn(), schema_name) as conn:
            with pytest.raises(RuntimeError, match="force_cancel_rollback"):
                async with conn.transaction():
                    assert await _request_owner_cancel(
                        conn,
                        tenant_id=tenant,
                        user_id=user,
                        run_id=run,
                    ) is not None
                    progress = await repositories.cancel_run(
                        conn,
                        tenant_id=tenant,
                        run_id=run,
                    )
                    assert progress.did_transition is True
                    raise RuntimeError("force_cancel_rollback")

            run_cursor = await conn.execute(
                """
                select status, cancel_requested_at, permission_terminalization_target
                from runs where tenant_id = %s and id = %s
                """,
                (tenant, run),
            )
            run_row = await run_cursor.fetchone()
            assert run_row == {
                "status": "running",
                "cancel_requested_at": None,
                "permission_terminalization_target": None,
            }
            event_cursor = await conn.execute(
                """
                select count(*) as event_count
                from run_events
                where tenant_id = %s and run_id = %s
                  and event_type in ('run.cancel_requested', 'run.cancelled')
                """,
                (tenant, run),
            )
            assert (await event_cursor.fetchone())["event_count"] == 0
