from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timedelta, timezone
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

from app import repositories, schema_migrations
from app.bootstrap import run_lifecycle
from app.repositories import complete_run
from app.platform.public_payload import sanitize_public_payload, sanitize_public_text
from app.routes import runtime_callbacks
from app.routes.lambchat_compat import _recover_v4_attach_gap
from app.runtime.sandbox.contracts import ExecutorCallbackEvent
from app.streaming.api import (
    build_v4_control,
    prepare_v4_successor_rebuild,
    publish_claimed_v4_events,
    stream_key,
    successor_stream_open_event_id,
)
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
from app.streaming.domain.transport import canonical_json_bytes
from app.streaming.infrastructure.postgres_v4 import (
    PostgresV4PublicationClaims,
    PostgresV4SuccessorRebuilds,
    V4PublicationAuthorityError,
    V4SuccessorRebuildAuthorityError,
)
from app.streaming.worker_projection import V4RedisPublicationTransport


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


def _production_cancellation_use_case(conn):
    @asynccontextmanager
    async def transaction_factory():
        async with conn.transaction():
            yield conn

    with patch.object(run_lifecycle, "transaction", transaction_factory):
        return run_lifecycle.build_run_cancellation_use_case()


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
    open_event_id = successor_stream_open_event_id(
        tenant_scope=tenant_scope,
        run_id=run,
        attempt_id=attempt,
        stream_incarnation=2,
    )
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
          revocation_state
        ) values (%s, %s, %s, 'ai-platform.redis-streams-sse-event-channel.v4',
                  'public-stream-v4', %s, 2, 'confirmed', %s, %s, %s, 4, 'active')
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


async def _terminal_rebuild_source(
    conn,
    *,
    tenant: str,
    run: str,
    attempt: str,
    status: str = "succeeded",
) -> None:
    run_row = await conn.execute(
        """
        select workspace_id, user_id, session_id, agent_id, skill_id,
               execution_kind
        from runs where tenant_id = %s and id = %s
        """,
        (tenant, run),
    )
    run_values = await run_row.fetchone()
    assert run_values is not None
    spec_json = json.dumps(
        {
            "schema_version": "ai-platform.execution-spec.v1",
            "tenant_id": tenant,
            "run_id": run,
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
        (tenant, run),
    )
    await conn.execute(
        """
        insert into run_attempts(
          id, tenant_id, run_id, ordinal, status, owner_kind, owner_id,
          queue_attempt_id, execution_spec_schema_version, execution_spec_json,
          execution_spec_canonical_json, execution_spec_sha256
        ) values (%s, %s, %s, 1, 'created', 'queue_worker', 'worker-a',
                  %s, 'ai-platform.execution-spec.v1', %s::jsonb, %s, %s)
        """,
        (
            attempt,
            tenant,
            run,
            f"queue_{attempt}",
            spec_json,
            spec_json,
            hashlib.sha256(spec_json.encode("utf-8")).hexdigest(),
        ),
    )
    if status == "cancelled":
        await conn.execute(
            """
            update run_attempts
            set status = 'cancelled', owner_generation = owner_generation + 1,
                finished_at = clock_timestamp()
            where tenant_id = %s and id = %s
            """,
            (tenant, attempt),
        )
    else:
        for next_status in ("queued", "claimed"):
            await conn.execute(
                """
                update run_attempts
                set status = %s, owner_generation = owner_generation + 1,
                    queue_message_id = case
                      when %s = 'queued' then 'message-a'
                      else queue_message_id
                    end
                where tenant_id = %s and id = %s
                """,
                (next_status, next_status, tenant, attempt),
            )
        if status == "succeeded":
            await conn.execute(
                """
                update run_attempts
                set status = 'running', owner_generation = owner_generation + 1,
                    started_at = clock_timestamp()
                where tenant_id = %s and id = %s
                """,
                (tenant, attempt),
            )
        await conn.execute(
            """
            update run_attempts
            set status = %s, owner_generation = owner_generation + 1,
                finished_at = clock_timestamp()
            where tenant_id = %s and id = %s
            """,
            (status, tenant, attempt),
        )
    await conn.execute(
        """
        update sse_stream_authorities
        set state = 'terminal', updated_at = clock_timestamp()
        where tenant_id = %s and run_id = %s
        """,
        (tenant, run),
    )
    await _insert_v4_row(
        conn,
        tenant=tenant,
        run=run,
        attempt=attempt,
        sequence=1,
        event_id="evt4_rebuild_delta",
    )
    await _insert_v4_row(
        conn,
        tenant=tenant,
        run=run,
        attempt=attempt,
        sequence=2,
        event_id=f"evt4_rebuild_{status}",
        event_type=f"run.{status}",
    )
    await conn.execute(
        """
        insert into run_events(
          id, tenant_id, run_id, trace_id, schema_version, sequence,
          event_type, stage, message, severity, visible_to_user, payload_json
        ) values ('evt4_rebuild_audit', %s, %s, %s,
                  'ai-platform.event-envelope.v1', 3, 'run.audit',
                  'run', '', 'info', false, '{}'::jsonb)
        """,
        (tenant, run, f"trace_{run}"),
    )
    await conn.execute(
        """
        insert into run_event_cursors(tenant_id, run_id, next_sequence)
        values (%s, %s, 4)
        on conflict (tenant_id, run_id) do update set next_sequence = excluded.next_sequence
        """,
        (tenant, run),
    )


async def _successor_source_facts(
    conn: psycopg.AsyncConnection,
    *,
    tenant: str,
    run: str,
) -> dict[str, object]:
    cursor = await conn.execute(
        """
        select
          (select to_jsonb(run_record) from runs as run_record
           where run_record.tenant_id = %s and run_record.id = %s) as run,
          (select jsonb_agg(to_jsonb(attempt_record) order by attempt_record.ordinal)
           from run_attempts as attempt_record
           where attempt_record.tenant_id = %s and attempt_record.run_id = %s) as attempts,
          (select to_jsonb(authority_record)
           from sse_stream_authorities as authority_record
           where authority_record.tenant_id = %s and authority_record.run_id = %s) as authority,
          (select jsonb_agg(to_jsonb(lease_record) order by lease_record.id)
           from sandbox_leases as lease_record
           where lease_record.tenant_id = %s and lease_record.run_id = %s) as leases,
          (select to_jsonb(cursor_record)
           from run_event_cursors as cursor_record
           where cursor_record.tenant_id = %s and cursor_record.run_id = %s) as cursor,
          (select jsonb_agg(to_jsonb(event_record) order by event_record.sequence, event_record.id)
           from run_events as event_record
           where event_record.tenant_id = %s and event_record.run_id = %s) as events
        """,
        (
            tenant,
            run,
            tenant,
            run,
            tenant,
            run,
            tenant,
            run,
            tenant,
            run,
            tenant,
            run,
        ),
    )
    facts = await cursor.fetchone()
    assert facts is not None
    return facts


async def _redis_stream(tenant: str, run: str, *, incarnation: int = 2):
    client = Redis.from_url(_redis_url(), decode_responses=True)
    key = stream_key(
        tenant_scope_value=f"scope_{tenant[2:]}",
        run_id=run,
        stream_incarnation=incarnation,
    )
    state_key = f"{key}:state"
    await client.delete(key, state_key)
    await client.hset(state_key, mapping={"phase": "open", "open_protocol": "v4"})
    return client, key, V4RedisStreamBridge(RedisStreamBridge(publish_client=client))


async def _publish_claimed(
    dsn: str,
    schema_name: str,
    *,
    tenant: str,
    run: str,
    attempt: str,
    bridge: V4RedisStreamBridge,
    limit: int,
    claim_ttl: timedelta = timedelta(seconds=30),
) -> int:
    claims = PostgresV4PublicationClaims(
        lambda: _connection_factory(dsn, schema_name)
    )
    return await publish_claimed_v4_events(
        claims,
        V4RedisPublicationTransport(bridge),
        tenant_id=tenant,
        run_id=run,
        attempt_id=attempt,
        stream_incarnation=2,
        limit=limit,
        claim_ttl=claim_ttl,
        retry_delay=timedelta(seconds=5),
    )


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

        monkeypatch.setattr(runtime_callbacks, "transaction", lambda: _connection_factory(dsn, schema_name))
        monkeypatch.setattr(
            runtime_callbacks.repositories,
            "list_current_sandbox_runtime_leases_for_attempt",
            list_leases_with_final_loss,
        )

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

            monkeypatch.setattr(
                runtime_callbacks,
                "transaction",
                lambda: _connection_factory(dsn, schema_name),
            )

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
            assert first_row["stream_publication_state"] == "pending"
            assert first_row["stream_publication_attempts"] == 0

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
            assert stream_rows == []

            assert await _publish_claimed(
                dsn,
                schema_name,
                tenant=tenant,
                run=run,
                attempt=attempt,
                limit=1,
                bridge=bridge,
            ) == 1
            async with _connection_factory(dsn, schema_name) as conn:
                published_cursor = await conn.execute(
                    """
                    select stream_publication_state, stream_publication_attempts
                    from run_events where id = %s
                    """,
                    (first_row["id"],),
                )
                published_row = await published_cursor.fetchone()
            assert published_row == {
                "stream_publication_state": "published",
                "stream_publication_attempts": 1,
            }
            stream_rows = await client.xrange(key, min="-", max="+")
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
                        select stream_publication_state as state
                        from run_events
                        where tenant_id = %s and run_id = %s and event_type = 'message.started'
                        for update nowait
                        """,
                        (tenant, run),
                    )
                    observed_at_bridge.append(await result.fetchone())
                raise StreamTransportUnavailable("redis unavailable")

        monkeypatch.setattr(
            runtime_callbacks,
            "transaction",
            lambda: _connection_factory(dsn, schema_name),
        )

        result = await runtime_callbacks.record_executor_callback(callback)
        claims = PostgresV4PublicationClaims(
            lambda: _connection_factory(dsn, schema_name)
        )
        assert await publish_claimed_v4_events(
            claims,
            V4RedisPublicationTransport(FailingPublisher()),
            tenant_id=tenant,
            run_id=run,
            attempt_id=attempt,
            stream_incarnation=2,
            limit=1,
            claim_ttl=timedelta(seconds=30),
            retry_delay=timedelta(seconds=5),
        ) == 0

        assert result == {
            "accepted": True,
            "batch_id": "batch-handler-transport",
            "event_count": 2,
        }
        assert observed_at_bridge == [{"state": "pending"}]
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
                    _publish_claimed(
                        dsn,
                        schema_name,
                        tenant=tenant,
                        run=run,
                        attempt=attempt,
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
async def test_real_claimed_publisher_converges_after_pg_disposition_failure():
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
                await _publish_claimed(
                    dsn,
                    schema_name,
                    tenant=tenant,
                    run=run,
                    attempt=attempt,
                    limit=1,
                    bridge=bridge,
                    claim_ttl=timedelta(seconds=1),
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
            await asyncio.sleep(1.1)
            assert await _publish_claimed(
                dsn,
                schema_name,
                tenant=tenant,
                run=run,
                attempt=attempt,
                limit=1,
                bridge=bridge,
            ) == 1
            redis_rows = await client.xrange(key, min="-", max="+")
            assert [json.loads(fields["envelope"])["event_id"] for _, fields in redis_rows] == [
                "evt4_disposition"
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
            assert await _publish_claimed(
                dsn,
                schema_name,
                tenant=tenant,
                run=run,
                attempt=attempt,
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
            assert await _publish_claimed(
                dsn,
                schema_name,
                tenant=tenant,
                run=run,
                attempt=attempt,
                limit=1,
                bridge=bridge,
            ) == 1
            rows = await client.xrange(key, min="-", max="+")
            assert [json.loads(fields["envelope"])["event_type"] for _, fields in rows] == [
                event_type,
                "stream.end",
            ]
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
async def test_successor_rebuild_prepares_terminal_snapshot_without_activation():
    async with _schema() as (dsn, schema_name, (tenant, run, attempt)):
        async with _connection_factory(dsn, schema_name) as conn:
            await _terminal_rebuild_source(
                conn, tenant=tenant, run=run, attempt=attempt
            )
            source_facts_before = await _successor_source_facts(
                conn,
                tenant=tenant,
                run=run,
            )
        rebuilds = PostgresV4SuccessorRebuilds(
            lambda: _connection_factory(dsn, schema_name),
            claim_token_factory=lambda: "rebuild-token-one",
        )
        claim = await prepare_v4_successor_rebuild(
            rebuilds,
            tenant_id=tenant,
            run_id=run,
            attempt_id=attempt,
            source_incarnation=2,
            claim_ttl=timedelta(seconds=30),
        )
        assert claim is not None
        assert claim.source_incarnation == 2
        assert claim.source_authorization_epoch == 4
        assert claim.successor_incarnation == 3
        assert claim.successor_authorization_epoch == 5
        assert claim.source_cursor_sequence == 3
        assert claim.source_through_sequence == 2
        assert [item.sequence for item in claim.items] == [1, 2]
        assert [item.event_type for item in claim.items] == [
            "message.delta",
            "run.succeeded",
        ]
        assert all(
            json.loads(item.canonical_envelope_bytes)["stream_incarnation"] == 3
            for item in claim.items
        )
        assert json.loads(claim.successor_open_bytes)["event_type"] == "stream.open"

        async with _connection_factory(dsn, schema_name) as conn:
            operation = await conn.execute(
                """
                select state, successor_incarnation, successor_authorization_epoch,
                       claim_token_digest, item_count, built_through_sequence
                from sse_stream_rebuilds where id = %s
                """,
                (claim.rebuild_id,),
            )
            row = await operation.fetchone()
            assert row == {
                "state": "building",
                "successor_incarnation": 3,
                "successor_authorization_epoch": 5,
                "claim_token_digest": hashlib.sha256(
                    b"rebuild-token-one"
                ).hexdigest(),
                "item_count": 2,
                "built_through_sequence": 0,
            }
            assert "rebuild-token-one" not in json.dumps(row)
            source_rows = await conn.execute(
                """
                select sequence, stream_publication_state
                from run_events where id like 'evt4_rebuild_%'
                order by sequence
                """
            )
            assert await source_rows.fetchall() == [
                {"sequence": 1, "stream_publication_state": "pending"},
                {"sequence": 2, "stream_publication_state": "pending"},
                {"sequence": 3, "stream_publication_state": None},
            ]
            authority = await conn.execute(
                """
                select stream_incarnation, authorization_epoch, state
                from sse_stream_authorities
                where tenant_id = %s and run_id = %s
                """,
                (tenant, run),
            )
            assert await authority.fetchone() == {
                "stream_incarnation": 2,
                "authorization_epoch": 4,
                "state": "terminal",
            }
            assert await _successor_source_facts(
                conn,
                tenant=tenant,
                run=run,
            ) == source_facts_before


@pytest.mark.asyncio
async def test_successor_rebuild_locks_run_before_stream_authority():
    async with _schema() as (dsn, schema_name, (tenant, run, attempt)):
        async with _connection_factory(dsn, schema_name) as conn:
            await _terminal_rebuild_source(
                conn, tenant=tenant, run=run, attempt=attempt
            )

        claim_connected = asyncio.Event()
        claim_backend_pid: int | None = None

        @asynccontextmanager
        async def claim_factory():
            nonlocal claim_backend_pid
            conn = await psycopg.AsyncConnection.connect(
                dsn,
                options=f"-c search_path={schema_name}",
                row_factory=dict_row,
            )
            claim_backend_pid = conn.info.backend_pid
            claim_connected.set()
            try:
                async with conn.transaction():
                    yield conn
            finally:
                await conn.close()

        rebuilds = PostgresV4SuccessorRebuilds(
            claim_factory,
            claim_token_factory=lambda: "rebuild-token-lock-order",
        )
        blocker = await psycopg.AsyncConnection.connect(
            dsn,
            options=f"-c search_path={schema_name}",
            row_factory=dict_row,
        )
        claim_task: asyncio.Task[object] | None = None
        try:
            async with blocker.transaction():
                await blocker.execute(
                    "select id from runs where tenant_id = %s and id = %s for update",
                    (tenant, run),
                )
                claim_task = asyncio.create_task(
                    prepare_v4_successor_rebuild(
                        rebuilds,
                        tenant_id=tenant,
                        run_id=run,
                        attempt_id=attempt,
                        source_incarnation=2,
                        claim_ttl=timedelta(seconds=30),
                    )
                )
                await asyncio.wait_for(claim_connected.wait(), timeout=5)
                assert claim_backend_pid is not None
                async with _connection_factory(dsn, schema_name) as observer:
                    for _ in range(250):
                        waiting = await observer.execute(
                            """
                            select wait_event_type
                            from pg_stat_activity
                            where pid = %s
                            """,
                            (claim_backend_pid,),
                        )
                        row = await waiting.fetchone()
                        if row is not None and row["wait_event_type"] == "Lock":
                            break
                        await asyncio.sleep(0.02)
                    else:
                        pytest.fail("successor claim did not block on the Run lock")
                    authority = await observer.execute(
                        """
                        select stream_incarnation
                        from sse_stream_authorities
                        where tenant_id = %s and run_id = %s
                        for update nowait
                        """,
                        (tenant, run),
                    )
                    assert await authority.fetchone() == {"stream_incarnation": 2}
                    assert claim_task.done() is False
            claim = await claim_task
        finally:
            if claim_task is not None and not claim_task.done():
                claim_task.cancel()
                with suppress(asyncio.CancelledError):
                    await claim_task
            await blocker.close()
        assert claim is not None
        assert claim.successor_incarnation == 3


@pytest.mark.asyncio
async def test_successor_rebuild_serializes_and_never_reuses_expired_incarnation():
    async with _schema() as (dsn, schema_name, (tenant, run, attempt)):
        async with _connection_factory(dsn, schema_name) as conn:
            await _terminal_rebuild_source(
                conn, tenant=tenant, run=run, attempt=attempt
            )
        tokens = iter(("rebuild-token-one", "rebuild-token-two", "rebuild-token-three"))
        rebuilds = PostgresV4SuccessorRebuilds(
            lambda: _connection_factory(dsn, schema_name),
            claim_token_factory=lambda: next(tokens),
        )
        concurrent = await asyncio.gather(
            *(
                prepare_v4_successor_rebuild(
                    rebuilds,
                    tenant_id=tenant,
                    run_id=run,
                    attempt_id=attempt,
                    source_incarnation=2,
                    claim_ttl=timedelta(seconds=30),
                )
                for _ in range(2)
            )
        )
        first = next(item for item in concurrent if item is not None)
        assert sum(item is not None for item in concurrent) == 1
        async with _connection_factory(dsn, schema_name) as conn:
            await conn.execute(
                """
                update sse_stream_rebuilds
                set claim_expires_at = clock_timestamp() - interval '1 second'
                where id = %s
                """,
                (first.rebuild_id,),
            )
        takeover = await prepare_v4_successor_rebuild(
            rebuilds,
            tenant_id=tenant,
            run_id=run,
            attempt_id=attempt,
            source_incarnation=2,
            claim_ttl=timedelta(seconds=30),
        )
        assert takeover is not None
        assert takeover.rebuild_id != first.rebuild_id
        assert takeover.successor_incarnation == first.successor_incarnation + 1
        async with _connection_factory(dsn, schema_name) as conn:
            states = await conn.execute(
                """
                select successor_incarnation, state
                from sse_stream_rebuilds
                where tenant_id = %s and run_id = %s
                order by successor_incarnation
                """,
                (tenant, run),
            )
            assert await states.fetchall() == [
                {"successor_incarnation": 3, "state": "expired"},
                {"successor_incarnation": 4, "state": "building"},
            ]


@pytest.mark.asyncio
async def test_successor_rebuild_rejects_nonterminal_or_malformed_source_atomically():
    async with _schema() as (dsn, schema_name, (tenant, run, attempt)):
        rebuilds = PostgresV4SuccessorRebuilds(
            lambda: _connection_factory(dsn, schema_name),
            claim_token_factory=lambda: "rebuild-token",
        )
        with pytest.raises(
            V4SuccessorRebuildAuthorityError, match="terminal_authority_missing"
        ):
            await prepare_v4_successor_rebuild(
                rebuilds,
                tenant_id=tenant,
                run_id=run,
                attempt_id=attempt,
                source_incarnation=2,
                claim_ttl=timedelta(seconds=30),
            )
        async with _connection_factory(dsn, schema_name) as conn:
            await _terminal_rebuild_source(
                conn, tenant=tenant, run=run, attempt=attempt
            )
            await conn.execute(
                """
                update run_events
                set payload_json = jsonb_set(
                  payload_json, '{__stream_v4,attempt_id}', '"wrong"'::jsonb
                )
                where id = 'evt4_rebuild_delta'
                """
            )
        with pytest.raises(ValueError, match="v4_successor_source_invalid"):
            await prepare_v4_successor_rebuild(
                rebuilds,
                tenant_id=tenant,
                run_id=run,
                attempt_id=attempt,
                source_incarnation=2,
                claim_ttl=timedelta(seconds=30),
            )
        async with _connection_factory(dsn, schema_name) as conn:
            count = await conn.execute("select count(*) as count from sse_stream_rebuilds")
            assert await count.fetchone() == {"count": 0}


@pytest.mark.asyncio
async def test_successor_rebuild_rejects_corrupt_source_open_authority():
    async with _schema() as (dsn, schema_name, (tenant, run, attempt)):
        async with _connection_factory(dsn, schema_name) as conn:
            await _terminal_rebuild_source(
                conn,
                tenant=tenant,
                run=run,
                attempt=attempt,
            )
            await conn.execute(
                """
                update sse_stream_authorities
                set open_payload_bytes = '{}'::text
                where tenant_id = %s and run_id = %s
                """,
                (tenant, run),
            )
        rebuilds = PostgresV4SuccessorRebuilds(
            lambda: _connection_factory(dsn, schema_name),
            claim_token_factory=lambda: "rebuild-token-corrupt-open",
        )
        with pytest.raises(
            V4SuccessorRebuildAuthorityError,
            match="source_authority_invalid",
        ):
            await prepare_v4_successor_rebuild(
                rebuilds,
                tenant_id=tenant,
                run_id=run,
                attempt_id=attempt,
                source_incarnation=2,
                claim_ttl=timedelta(seconds=30),
            )
        async with _connection_factory(dsn, schema_name) as conn:
            count = await conn.execute(
                "select count(*) as count from sse_stream_rebuilds"
            )
            assert await count.fetchone() == {"count": 0}


@pytest.mark.asyncio
async def test_successor_rebuild_rejects_noncurrent_terminal_attempt():
    async with _schema() as (dsn, schema_name, (tenant, run, attempt)):
        async with _connection_factory(dsn, schema_name) as conn:
            await _terminal_rebuild_source(
                conn,
                tenant=tenant,
                run=run,
                attempt=attempt,
            )
            await conn.execute(
                """
                insert into run_attempts(
                  id, tenant_id, run_id, ordinal, status, owner_kind, owner_id,
                  owner_generation, queue_attempt_id, queue_message_id,
                  execution_spec_schema_version, execution_spec_json,
                  execution_spec_canonical_json, execution_spec_sha256,
                  started_at, finished_at
                )
                select 'attempt-current', tenant_id, run_id, ordinal + 1,
                       status, owner_kind, owner_id, owner_generation,
                       'queue-attempt-current', 'message-current',
                       execution_spec_schema_version, execution_spec_json,
                       execution_spec_canonical_json, execution_spec_sha256,
                       started_at, finished_at
                from run_attempts
                where tenant_id = %s and run_id = %s and id = %s
                """,
                (tenant, run, attempt),
            )
        rebuilds = PostgresV4SuccessorRebuilds(
            lambda: _connection_factory(dsn, schema_name),
            claim_token_factory=lambda: "rebuild-token-old-attempt",
        )
        with pytest.raises(
            V4SuccessorRebuildAuthorityError,
            match="terminal_authority_missing",
        ):
            await prepare_v4_successor_rebuild(
                rebuilds,
                tenant_id=tenant,
                run_id=run,
                attempt_id=attempt,
                source_incarnation=2,
                claim_ttl=timedelta(seconds=30),
            )
        async with _connection_factory(dsn, schema_name) as conn:
            count = await conn.execute(
                "select count(*) as count from sse_stream_rebuilds"
            )
            assert await count.fetchone() == {"count": 0}


@pytest.mark.asyncio
async def test_successor_rebuild_rolls_back_parent_and_prior_item_on_late_write_failure():
    async with _schema() as (dsn, schema_name, (tenant, run, attempt)):
        async with _connection_factory(dsn, schema_name) as conn:
            await _terminal_rebuild_source(
                conn,
                tenant=tenant,
                run=run,
                attempt=attempt,
            )
            source_facts_before = await _successor_source_facts(
                conn,
                tenant=tenant,
                run=run,
            )
            await conn.execute(
                """
                alter table sse_stream_rebuild_items
                add constraint chk_test_rebuild_second_item_failure
                check (sequence <> 2)
                """
            )
        rebuilds = PostgresV4SuccessorRebuilds(
            lambda: _connection_factory(dsn, schema_name),
            claim_token_factory=lambda: "rebuild-token-late-write",
        )
        with pytest.raises(psycopg.errors.CheckViolation):
            await prepare_v4_successor_rebuild(
                rebuilds,
                tenant_id=tenant,
                run_id=run,
                attempt_id=attempt,
                source_incarnation=2,
                claim_ttl=timedelta(seconds=30),
            )
        async with _connection_factory(dsn, schema_name) as conn:
            counts = await conn.execute(
                """
                select
                  (select count(*) from sse_stream_rebuilds) as rebuilds,
                  (select count(*) from sse_stream_rebuild_items) as items
                """
            )
            assert await counts.fetchone() == {"rebuilds": 0, "items": 0}
            assert await _successor_source_facts(
                conn,
                tenant=tenant,
                run=run,
            ) == source_facts_before


@pytest.mark.asyncio
async def test_publication_claims_serialize_takeover_and_fence_stale_tokens():
    async with _schema() as (dsn, schema_name, (tenant, run, attempt)):
        async with _connection_factory(dsn, schema_name) as conn:
            await _insert_v4_row(
                conn,
                tenant=tenant,
                run=run,
                attempt=attempt,
                sequence=1,
                event_id="evt4_claim_first",
            )
            await _insert_v4_row(
                conn,
                tenant=tenant,
                run=run,
                attempt=attempt,
                sequence=2,
                event_id="evt4_claim_second",
            )
        tokens = iter(("claim-one", "claim-two", "claim-three", "claim-four"))
        claims = PostgresV4PublicationClaims(
            lambda: _connection_factory(dsn, schema_name),
            claim_token_factory=lambda: next(tokens),
        )

        concurrent = await asyncio.gather(
            *(
                claims.claim_next(
                    tenant_id=tenant,
                    run_id=run,
                    attempt_id=attempt,
                    stream_incarnation=2,
                )
                for _ in range(2)
            )
        )
        first = next(item for item in concurrent if item is not None)
        assert first.event_id == "evt4_claim_first"
        assert first.tenant_scope == f"scope_{tenant[2:]}"
        assert first.authorization_epoch == 4
        assert sum(item is not None for item in concurrent) == 1
        assert b"__stream_v4" not in first.canonical_envelope_bytes

        async with _connection_factory(dsn, schema_name) as conn:
            await conn.execute(
                """
                update run_events
                set stream_publication_claim_expires_at = clock_timestamp() - interval '1 second'
                where id = %s
                """,
                (first.event_id,),
            )
        takeover = await claims.claim_next(
            tenant_id=tenant,
            run_id=run,
            attempt_id=attempt,
            stream_incarnation=2,
        )
        assert takeover is not None
        assert takeover.event_id == first.event_id
        assert takeover.claim_token != first.claim_token
        assert await claims.mark_published(first, redis_id="1-0") is False
        assert await claims.mark_published(takeover, redis_id="2-0") is True

        second = await claims.claim_next(
            tenant_id=tenant,
            run_id=run,
            attempt_id=attempt,
            stream_incarnation=2,
        )
        assert second is not None
        assert second.event_id == "evt4_claim_second"
        assert await claims.schedule_retry(
            second,
            error="redis_unavailable",
            delay=timedelta(seconds=5),
        ) is True
        async with _connection_factory(dsn, schema_name) as conn:
            rows = await conn.execute(
                """
                select id, stream_publication_state, stream_publication_attempts,
                       stream_publication_redis_id,
                       payload_json -> '__stream_v4' ->> 'publication_attempts' as metadata_attempts
                from run_events
                where id in ('evt4_claim_first', 'evt4_claim_second')
                order by sequence
                """
            )
            assert await rows.fetchall() == [
                {
                    "id": "evt4_claim_first",
                    "stream_publication_state": "published",
                    "stream_publication_attempts": 1,
                    "stream_publication_redis_id": "2-0",
                    "metadata_attempts": "1",
                },
                {
                    "id": "evt4_claim_second",
                    "stream_publication_state": "pending",
                    "stream_publication_attempts": 1,
                    "stream_publication_redis_id": None,
                    "metadata_attempts": "1",
                },
            ]


@pytest.mark.asyncio
async def test_publication_claim_preserves_predecessor_order_and_release_is_noncounting():
    async with _schema() as (dsn, schema_name, (tenant, run, attempt)):
        async with _connection_factory(dsn, schema_name) as conn:
            await _insert_v4_row(
                conn,
                tenant=tenant,
                run=run,
                attempt=attempt,
                sequence=1,
                event_id="evt4_delayed_predecessor",
            )
            await _insert_v4_row(
                conn,
                tenant=tenant,
                run=run,
                attempt=attempt,
                sequence=2,
                event_id="evt4_ready_successor",
            )
            await conn.execute(
                """
                update run_events
                set stream_publication_next_attempt_at = clock_timestamp() + interval '1 hour'
                where id = 'evt4_delayed_predecessor'
                """
            )
        claims = PostgresV4PublicationClaims(
            lambda: _connection_factory(dsn, schema_name),
            claim_token_factory=lambda: "claim-order",
        )
        assert (
            await claims.claim_next(
                tenant_id=tenant,
                run_id=run,
                attempt_id=attempt,
                stream_incarnation=2,
            )
            is None
        )
        async with _connection_factory(dsn, schema_name) as conn:
            await conn.execute(
                """
                update run_events
                set stream_publication_next_attempt_at = clock_timestamp()
                where id = 'evt4_delayed_predecessor'
                """
            )
        predecessor = await claims.claim_next(
            tenant_id=tenant,
            run_id=run,
            attempt_id=attempt,
            stream_incarnation=2,
        )
        assert predecessor is not None
        assert predecessor.event_id == "evt4_delayed_predecessor"
        assert await claims.release(predecessor) is True
        async with _connection_factory(dsn, schema_name) as conn:
            row = await conn.execute(
                """
                select stream_publication_attempts,
                       payload_json -> '__stream_v4' ->> 'publication_attempts' as metadata_attempts
                from run_events where id = 'evt4_delayed_predecessor'
                """
            )
            assert await row.fetchone() == {
                "stream_publication_attempts": 0,
                "metadata_attempts": "0",
            }


@pytest.mark.asyncio
async def test_publication_claim_rejects_wrong_authority_and_rolls_back_invalid_projection():
    async with _schema() as (dsn, schema_name, (tenant, run, attempt)):
        claims = PostgresV4PublicationClaims(
            lambda: _connection_factory(dsn, schema_name),
            claim_token_factory=lambda: "claim-invalid",
        )
        with pytest.raises(V4PublicationAuthorityError, match="authority_conflict"):
            await claims.claim_next(
                tenant_id=tenant,
                run_id=run,
                attempt_id=attempt,
                stream_incarnation=1,
            )
        async with _connection_factory(dsn, schema_name) as conn:
            await _insert_v4_row(
                conn,
                tenant=tenant,
                run=run,
                attempt=attempt,
                sequence=1,
                event_id="evt4_invalid_projection",
                payload={"delta": ""},
            )
        with pytest.raises(RuntimeError, match="projection_invalid"):
            await claims.claim_next(
                tenant_id=tenant,
                run_id=run,
                attempt_id=attempt,
                stream_incarnation=2,
            )
        async with _connection_factory(dsn, schema_name) as conn:
            row = await conn.execute(
                """
                select stream_publication_claim_token,
                       stream_publication_claim_expires_at
                from run_events where id = 'evt4_invalid_projection'
                """
            )
            assert await row.fetchone() == {
                "stream_publication_claim_token": None,
                "stream_publication_claim_expires_at": None,
            }


@pytest.mark.asyncio
async def test_migration_applies_exact_scoped_constraint_and_index_then_rolls_back_facts_only():
    async with _schema() as (dsn, schema_name, (tenant, run, attempt)):
        async with _connection_factory(dsn, schema_name) as conn:
            status = await schema_migrations.schema_status(conn)
            assert status["ledger_current"] is True
            assert status["index_ledger_current"] is True
            assert status["columns_current"] is True
            assert status["indexes_current"] is True
            assert status["static_index_definitions_current"] is True
            async with conn.transaction():
                await _insert_v4_row(
                    conn,
                    tenant=tenant,
                    run=run,
                    attempt=attempt,
                    sequence=1,
                    event_id="evt4_migration_fact",
                )
                source_facts_before = await _successor_source_facts(
                    conn,
                    tenant=tenant,
                    run=run,
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
                claim_constraint = await conn.execute(
                    """
                    select pg_get_constraintdef(oid, true) as definition
                    from pg_constraint
                    where conrelid = 'run_events'::regclass
                      and conname = 'chk_run_events_stream_publication_claim'
                    """
                )
                assert "stream_publication_claim_token" in (
                    await claim_constraint.fetchone()
                )["definition"]
                claim_index = await conn.execute(
                    """
                    select indexdef from pg_indexes
                    where schemaname = current_schema()
                      and indexname = 'idx_run_events_stream_publication_claim'
                    """
                )
                normalized_claim_index = " ".join(
                    (await claim_index.fetchone())["indexdef"].lower().split()
                )
                assert "tenant_id, run_id, sequence, id" in normalized_claim_index
                assert "payload_json ? '__stream_v4'::text" in normalized_claim_index
                successor_constraints = await conn.execute(
                    """
                    select conrelid::regclass::text as relation_name,
                           conname as constraint_name,
                           contype::text as constraint_type,
                           pg_get_constraintdef(oid, true) as definition
                    from pg_constraint
                    where conrelid in (
                      to_regclass('sse_stream_rebuilds'),
                      to_regclass('sse_stream_rebuild_items')
                    )
                    """
                )
                actual_constraints = {
                    (
                        row["relation_name"],
                        row["constraint_name"],
                        row["constraint_type"],
                        "".join(row["definition"].lower().split()),
                    )
                    for row in await successor_constraints.fetchall()
                }
                expected_constraints = {
                    (relation_name, name, kind, "".join(definition.lower().split()))
                    for relation_name, name, kind, definition in (
                        schema_migrations.CRITICAL_CONSTRAINT_DEFINITIONS
                    )
                    if relation_name.startswith("sse_stream_rebuild")
                }
                assert expected_constraints <= actual_constraints
                await schema_migrations.rollback_v4_successor_rebuild_migration(conn)
                assert await _successor_source_facts(
                    conn,
                    tenant=tenant,
                    run=run,
                ) == source_facts_before
                successor_tables = await conn.execute(
                    """
                    select to_regclass('sse_stream_rebuilds') as rebuilds,
                           to_regclass('sse_stream_rebuild_items') as items
                    """
                )
                assert await successor_tables.fetchone() == {
                    "rebuilds": None,
                    "items": None,
                }
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
                    """
                    select to_regclass('idx_run_events_stream_publication_retry') as retry,
                           to_regclass('idx_run_events_stream_publication_claim') as claim
                    """
                )
                assert await index_after.fetchone() == {"retry": None, "claim": None}
                schema_ledger = await conn.execute(
                    "select version from schema_migrations where version = %s",
                    (schema_migrations.V4_SUCCESSOR_REBUILD_SCHEMA_VERSION,),
                )
                assert await schema_ledger.fetchone() is None
                index_ledger = await conn.execute(
                    """
                    select index_name from schema_index_migrations
                    where index_name in (
                      'idx_run_events_stream_publication_retry',
                      'idx_run_events_stream_publication_claim'
                    )
                    order by index_name
                    """
                )
                assert await index_ledger.fetchall() == []
                fact = await conn.execute("select id, sequence from run_events where id = 'evt4_migration_fact'")
                assert await fact.fetchone() == {"id": "evt4_migration_fact", "sequence": 1}
        await schema_migrations.apply_migrations(
            transaction_factory=lambda: _connection_factory(dsn, schema_name),
            index_connection_factory=lambda: _index_connection(dsn, schema_name),
        )
        async with _connection_factory(dsn, schema_name) as conn:
            status = await schema_migrations.schema_status(conn)
            assert status["ledger_current"] is True
            assert status["index_ledger_current"] is True
            assert status["columns_current"] is True
            assert status["indexes_current"] is True
            assert status["static_index_definitions_current"] is True
            columns = await conn.execute(
                "select column_name from information_schema.columns where table_schema = current_schema() and table_name = 'run_events' and column_name = 'stream_publication_state'"
            )
            assert await columns.fetchone() == {"column_name": "stream_publication_state"}


@pytest.mark.asyncio
async def test_schema_status_requires_successor_item_primary_key():
    async with _schema() as (dsn, schema_name, _ids):
        async with _connection_factory(dsn, schema_name) as conn:
            before = await schema_migrations.schema_status(conn)
            assert before["constraints_current"] is True
            await conn.execute(
                """
                alter table sse_stream_rebuild_items
                drop constraint sse_stream_rebuild_items_pkey
                """
            )
            after = await schema_migrations.schema_status(conn)
            assert after["constraints_current"] is False
            assert after["ready"] is False


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
async def test_production_cancel_composition_preserves_owner_fences_order_and_retry():
    async with _schema() as (_dsn_value, schema_name, ids):
        tenant, run, _attempt = ids
        suffix = tenant[2:]
        user = f"u_{suffix}"
        async with _connection_factory(_dsn(), schema_name) as conn:
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
                "run_cancel_requested",
                "run.cancel_requested",
                "run_cancelled",
                "run.cancelled",
            ]
            assert [row["sequence"] for row in rows] == sorted(
                row["sequence"] for row in rows
            )
            assert rows[1]["payload_json"]["source"] == "user"

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
        tenant, run, _attempt = ids
        user = f"u_{tenant[2:]}"
        async with _connection_factory(_dsn(), schema_name) as conn:
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
        tenant, run, _attempt = ids
        user = f"u_{tenant[2:]}"
        async with _connection_factory(_dsn(), schema_name) as conn:
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
                "status": "running",
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
