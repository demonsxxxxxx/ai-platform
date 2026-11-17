from __future__ import annotations
import app.streaming.infrastructure.run_events_postgres as _owner_streaming_infrastructure_run_events_postgres

import os
import asyncio
import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from redis.asyncio import Redis

from app.streaming.application.worker_publication_v4 import publish_callback_rows
from app.streaming.domain.public_events_v4 import V4ProjectionError, build_v4_control
from app.streaming.domain.live import stream_key
from app.streaming.infrastructure.callback_stream import append_callback_batch
from app.streaming.infrastructure.v4 import V4RedisStreamBridge
from app.streaming.redis import RedisStreamBridge, StreamTransportUnavailable
import app.sandbox.infrastructure.leases_postgres as sandbox_lease_reader
from tests.test_streaming_v4_durable import _authority, _row
from tests.test_streaming_v4_redis_integration import _envelope


@pytest.mark.asyncio
async def test_committed_callback_batch_uses_direct_transport_without_claims():
    authority = _authority()
    row = _row({"delta": "hello"})
    row["stream_publication_state"] = None
    row["payload_json"]["__stream_v4"].pop("publication_state", None)
    calls = []

    class Transport:
        async def publish_callback_batch(self, envelopes):
            calls.append(envelopes)
            return "1-0"

    # No publication claim capability is provided: this path cannot use it.
    capabilities = SimpleNamespace(
        publication_transport=Transport(),
        event_persistence=SimpleNamespace(load_latest_run_event=AsyncMock(return_value=None)),
    )
    await publish_callback_rows(capabilities, (row,), authority=authority)
    assert len(calls) == 1
    assert calls[0][0]["payload"] == {"delta": "hello"}
    row["tenant_id"] = "foreign-tenant"
    with pytest.raises(V4ProjectionError):
        await publish_callback_rows(capabilities, (row,), authority=authority)
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [None, "commit", "redis", "timeout", "cancel", "projection"])
async def test_callback_ack_requires_commit_not_live_transport(monkeypatch, failure):
    from app.routes import runtime_callbacks as route
    from app.runtime.kernel_contracts import AgentEvent
    from app.runtime.sandbox.contracts import ExecutorCallbackEvent
    from app.streaming.application.durable_v4 import V4PublicationTransportUnavailable
    from tests.test_runtime_callbacks import callback_payload

    committed = False
    delivered = []
    authority = _authority()
    row = _row({"delta": "hello"})
    row["stream_publication_state"] = None
    row["payload_json"]["__stream_v4"].pop("publication_state", None)

    @asynccontextmanager
    async def transaction():
        nonlocal committed
        yield object()
        if failure == "commit":
            raise RuntimeError("synthetic commit failure")
        committed = True

    class Transport:
        async def publish(self, payload):
            raise AssertionError("callback must not republish predecessors")

        async def publish_callback_batch(self, envelopes):
            assert committed
            delivered.append(envelopes)
            if failure == "redis":
                raise V4PublicationTransportUnavailable("synthetic Redis outage")
            if failure == "timeout":
                await asyncio.sleep(10)
            if failure == "cancel":
                raise asyncio.CancelledError
            return "1-0"

    capabilities = SimpleNamespace(
        event_persistence=SimpleNamespace(
            append_callback_rows=AsyncMock(return_value=(row,)),
            load_latest_run_event=AsyncMock(side_effect=AssertionError("no predecessor lookup")),
        ),
        publication_transport=Transport(),
    )
    monkeypatch.setattr(route, "transaction", transaction)
    from app.runs.infrastructure import postgres as runs_postgres
    from app.sandbox.infrastructure import leases_postgres

    monkeypatch.setattr(runs_postgres, "get_run_identity", AsyncMock(return_value={
        "tenant_id": "tenant-a", "session_id": "session-a", "status": "running",
    }))
    monkeypatch.setattr(leases_postgres, "list_current_sandbox_runtime_leases_for_attempt", AsyncMock(return_value=[{
        "attempt_id": "attempt-a", "lease_payload_json": {"attempt_id": "attempt-a"},
    }]))
    monkeypatch.setattr(route, "get_stream_authority", AsyncMock(return_value=authority))
    monkeypatch.setattr(_owner_streaming_infrastructure_run_events_postgres, 'append_event_batch', AsyncMock(return_value={"duplicate": False}))
    monkeypatch.setattr(route, "callback_event_to_run_events", lambda _: [AgentEvent(type="message.delta")])
    monkeypatch.setattr(route, "callback_thinking_summary_to_v4", lambda *_, **__: ())
    monkeypatch.setattr(route, "agent_event_to_executor_event", lambda _: {})
    monkeypatch.setattr(route, "callback_item_to_v4", lambda *_, **__: SimpleNamespace(source_run_id="run-a", event_type="message.delta"))
    callback = ExecutorCallbackEvent.model_validate(callback_payload(batch_id="batch-direct"))
    if failure == "commit":
        with pytest.raises(RuntimeError, match="synthetic commit failure"):
            await route.record_executor_callback(callback, capabilities=capabilities, lease_port=sandbox_lease_reader)
        assert not committed and not delivered
    elif failure == "cancel":
        with pytest.raises(asyncio.CancelledError):
            await route.record_executor_callback(callback, capabilities=capabilities, lease_port=sandbox_lease_reader)
        assert committed and len(delivered) == 1
    elif failure == "projection":
        row["tenant_id"] = "foreign-tenant"
        with pytest.raises(V4ProjectionError):
            await route.record_executor_callback(callback, capabilities=capabilities, lease_port=sandbox_lease_reader)
        assert committed and not delivered
    else:
        receipt = await route.record_executor_callback(callback, capabilities=capabilities, lease_port=sandbox_lease_reader)
        assert receipt["accepted"] is True
        assert committed and len(delivered) == 1


@pytest.mark.asyncio
async def test_real_callback_batch_receipts_deduplicate_retries_and_reject_closed_streams():
    url = os.getenv("AI_PLATFORM_SSE_REDIS_TEST_URL", "").strip()
    if not url:
        pytest.skip("AI_PLATFORM_SSE_REDIS_TEST_URL is not configured")
    client = Redis.from_url(url, decode_responses=True)
    bridge = RedisStreamBridge(publish_client=client)
    transport = V4RedisStreamBridge(bridge)
    run_id = "run-callback-test-" + uuid.uuid4().hex
    scope = "scope_callback_test"
    attempt_id = "attempt-callback-test"
    key = stream_key(tenant_scope_value=scope, run_id=run_id, stream_incarnation=1)
    opened = build_v4_control(
        event_id="evt4_callback_open", tenant_scope=scope, run_id=run_id,
        attempt_id=attempt_id, stream_incarnation=1, event_type="stream.open",
        payload={"design_id": "ai-platform.redis-streams-sse-event-channel.v4"},
        source={"kind": "stream_authority", "authority_id": "test-authority"},
    )
    events = [
        {**_envelope(event_id=f"evt4_callback_{sequence}", seq=sequence),
         "run_id": run_id, "attempt_id": attempt_id, "tenant_scope": scope,
         "stream_incarnation": 1}
        for sequence in (2, 5, 8)
    ]
    try:
        await transport.append(opened)
        receipt = await append_callback_batch(bridge, events[:2])
        assert await append_callback_batch(bridge, events[:2]) == receipt
        assert await client.xlen(key) == 3
        cancellation = {
            **events[0], "event_id": "evt4_cancel_request", "event_type": "run.cancel_requested",
            "message_id": None, "seq": 6, "payload": {"source": "user"},
            "source": {"kind": "run_event", "run_event_id": "evt4_cancel_request", "sequence": 6, "callback_sequence": 5},
        }
        cancel_receipt = await transport.append(cancellation)
        await append_callback_batch(bridge, events[2:])
        await append_callback_batch(bridge, events[:2])
        assert await transport.append(cancellation) == cancel_receipt
        assert await client.xlen(key) == 5
        terminal = {
            **events[-1], "event_id": "evt4_callback_done", "event_type": "run.succeeded",
            "message_id": None, "seq": 9,
            "payload": {"terminal_event_id": "evt4_callback_done", "hydrate_required": True},
            "source": {"kind": "run_event", "run_event_id": "evt4_callback_done", "sequence": 9},
        }
        await transport.append(terminal)
        late = {**events[-1], "event_id": "evt4_callback_late", "seq": 10,
                "source": {"kind": "run_event", "run_event_id": "evt4_callback_late", "sequence": 10}}
        with pytest.raises(StreamTransportUnavailable):
            await append_callback_batch(bridge, (late,))
        await append_callback_batch(bridge, events[:2])
        assert await client.xlen(key) == 7
        # A stream that loses its authority must not be silently recreated.
        await client.delete(f"{key}:state")
        with pytest.raises(StreamTransportUnavailable):
            await append_callback_batch(bridge, events)
        assert await client.xlen(key) == 7
    finally:
        await client.delete(key, f"{key}:state")
        await client.aclose()
