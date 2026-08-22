from __future__ import annotations

import json
import os

from redis.asyncio import Redis
import pytest

from app.streaming.api import stream_key
from app.streaming.v4 import V4RedisStreamBridge, project_public_envelope_v4
from app.streaming.redis import RedisStreamBridge, StreamContractError, StreamTransportUnavailable


REDIS_URL_ENV = "AI_PLATFORM_SSE_REDIS_TEST_URL"


def _redis_url() -> str:
    value = os.getenv(REDIS_URL_ENV, "").strip()
    if not value:
        pytest.skip(f"{REDIS_URL_ENV} is not configured")
    return value


def _envelope(*, event_id: str, event_type: str = "message.delta", seq: int = 1, payload: dict[str, object] | None = None) -> dict[str, object]:
    if payload is None:
        payload = {"delta": "hello"}
    message_id = "msg4_" + "a" * 64 if event_type.startswith(("message.", "thinking.", "model.", "tool.", "subagent.")) else None
    return {
        "schema": "ai-platform.stream-event.v4",
        "event_id": event_id,
        "tenant_scope": "scope_v4_evidence",
        "run_id": "run-v4-evidence",
        "attempt_id": "attempt-v4-evidence",
        "message_id": message_id,
        "seq": seq,
        "event_type": event_type,
        "stream_incarnation": 3,
        "replayable": True,
        "trace_ref": None,
        "causation_event_id": None,
        "emitted_at": "2026-08-20T00:00:00Z",
        "projection_version": "public-stream-v4",
        "payload": payload,
        "source": {"kind": "run_event", "run_event_id": event_id, "sequence": seq},
    }


@pytest.mark.asyncio
async def test_real_redis_duplicate_event_retry_has_one_semantic_identity_per_cursor():
    url = _redis_url()
    client = Redis.from_url(url, decode_responses=True)
    key = stream_key(
        tenant_scope_value="scope_v4_evidence",
        run_id="run-v4-evidence",
        stream_incarnation=3,
    )
    state_key = f"{key}:state"
    bridge = RedisStreamBridge(publish_client=client)
    v4_bridge = V4RedisStreamBridge(bridge)
    event = _envelope(event_id="evt4_retry")
    try:
        await client.delete(key, state_key)
        await client.hset(state_key, mapping={"phase": "open"})
        first = await v4_bridge.append(event)
        second = await v4_bridge.append(event)
        assert first != second
        rows = await client.xrange(key, min="-", max="+")
        assert len(rows) == 2
        decoded = [json.loads(fields["envelope"]) for _, fields in rows]
        assert [item["event_id"] for item in decoded] == ["evt4_retry", "evt4_retry"]
        assert decoded[0] == decoded[1]
        cursors = {
            f"run-v4-evidence:3:{redis_id}" for redis_id, _fields in rows
        }
        assert len(cursors) == 2
        public = project_public_envelope_v4(decoded[0])
        assert public is not None
        assert "tenant_scope" not in public
        assert "attempt_id" not in public
        assert "source" not in public
    finally:
        await client.delete(key, state_key)
        await client.aclose()


@pytest.mark.asyncio
async def test_real_redis_terminal_authority_blocks_late_frames_and_preserves_public_projection():
    url = _redis_url()
    client = Redis.from_url(url, decode_responses=True)
    key = stream_key(
        tenant_scope_value="scope_v4_evidence",
        run_id="run-v4-evidence",
        stream_incarnation=3,
    )
    state_key = f"{key}:state"
    bridge = RedisStreamBridge(publish_client=client)
    v4_bridge = V4RedisStreamBridge(bridge)
    terminal = _envelope(
        event_id="evt4_terminal",
        event_type="run.succeeded",
        seq=2,
        payload={"terminal_event_id": "evt4_terminal", "hydrate_required": True},
    )
    try:
        await client.delete(key, state_key)
        await client.hset(state_key, mapping={"phase": "open"})
        await v4_bridge.append(_envelope(event_id="evt4_before_terminal"))
        terminal_cursor = await v4_bridge.append(terminal)
        assert terminal_cursor
        with pytest.raises(StreamContractError, match="stream_terminal_closed"):
            await v4_bridge.append(_envelope(event_id="evt4_after_terminal", seq=3))
        rows = await client.xrange(key, min="-", max="+")
        assert [json.loads(fields["envelope"])["event_id"] for _, fields in rows] == [
            "evt4_before_terminal",
            "evt4_terminal",
        ]
    finally:
        await client.delete(key, state_key)
        await client.aclose()


@pytest.mark.asyncio
async def test_real_redis_outage_is_retryable_before_terminalization():
    url = _redis_url()
    client = Redis.from_url(url, decode_responses=True)
    key = stream_key(
        tenant_scope_value="scope_v4_evidence",
        run_id="run-v4-evidence",
        stream_incarnation=3,
    )
    state_key = f"{key}:state"
    healthy = V4RedisStreamBridge(RedisStreamBridge(publish_client=client))
    unavailable_client = Redis.from_url(
        "redis://127.0.0.1:1",
        decode_responses=True,
        socket_connect_timeout=0.05,
        socket_timeout=0.05,
    )
    unavailable = V4RedisStreamBridge(RedisStreamBridge(publish_client=unavailable_client))
    terminal = _envelope(
        event_id="evt4_terminal_after_outage",
        event_type="run.succeeded",
        seq=2,
        payload={"terminal_event_id": "evt4_terminal_after_outage", "hydrate_required": True},
    )
    try:
        await client.delete(key, state_key)
        await client.hset(state_key, mapping={"phase": "open"})
        with pytest.raises(StreamTransportUnavailable):
            await unavailable.append(_envelope(event_id="evt4_during_outage"))
        await healthy.append(_envelope(event_id="evt4_after_retry"))
        assert await healthy.append(terminal)
        rows = await client.xrange(key, min="-", max="+")
        assert [json.loads(fields["envelope"])["event_id"] for _, fields in rows] == [
            "evt4_after_retry",
            "evt4_terminal_after_outage",
        ]
    finally:
        await unavailable.aclose()
        await client.delete(key, state_key)
        await client.aclose()
