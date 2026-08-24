from __future__ import annotations

import json
import os

from redis.asyncio import Redis
import pytest

from app.streaming.api import (
    V4ProjectionError,
    project_public_envelope_v4,
    stream_key,
)
from app.streaming.redis import (
    RedisStreamBridge,
    StreamContractError,
    StreamEnvelope,
    StreamTransportUnavailable,
)
from app.streaming.v4 import V4RedisStreamBridge


REDIS_URL_ENV = "AI_PLATFORM_SSE_REDIS_TEST_URL"


def _redis_url() -> str:
    value = os.getenv(REDIS_URL_ENV, "").strip()
    if not value:
        pytest.skip(f"{REDIS_URL_ENV} is not configured")
    return value


def _envelope(
    *,
    event_id: str,
    event_type: str = "message.delta",
    seq: int = 1,
    payload: dict[str, object] | None = None,
) -> dict[str, object]:
    if payload is None:
        payload = {"delta": "hello"}
    message_types = (
        "message.",
        "thinking.",
        "model.",
        "tool.",
        "subagent.",
    )
    message_id = "msg4_" + "a" * 64 if event_type.startswith(message_types) else None
    if event_type.startswith("run.") and payload == {"delta": "hello"}:
        payload = {"terminal_event_id": event_id, "hydrate_required": True}
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


async def _stream():
    client = Redis.from_url(_redis_url(), decode_responses=True)
    key = stream_key(
        tenant_scope_value="scope_v4_evidence",
        run_id="run-v4-evidence",
        stream_incarnation=3,
    )
    state_key = f"{key}:state"
    await client.delete(key, state_key)
    await client.hset(state_key, mapping={"phase": "open", "open_protocol": "v4"})
    return client, key, state_key, V4RedisStreamBridge(RedisStreamBridge(publish_client=client))


@pytest.mark.asyncio
async def test_real_redis_rejects_v4_append_to_legacy_v3_phase_without_mutation():
    client, key, state_key, _legacy_bridge = await _stream()
    bridge = V4RedisStreamBridge(RedisStreamBridge(publish_client=client))
    try:
        await client.hset(state_key, mapping={"phase": "open", "open_protocol": "v3"})
        before_state = await client.hgetall(state_key)
        with pytest.raises(StreamContractError, match="stream_protocol_conflict"):
            await bridge.append(_envelope(event_id="evt4_on_v3"))
        assert await client.xlen(key) == 0
        assert await client.hgetall(state_key) == before_state
    finally:
        await client.delete(key, state_key)
        await client.aclose()


@pytest.mark.asyncio
async def test_real_redis_rejects_v3_append_to_v4_phase_without_mutation():
    client, key, state_key, _v4_bridge = await _stream()
    bridge = RedisStreamBridge(publish_client=client)
    legacy = StreamEnvelope(
        event_id="evt3_on_v4",
        tenant_scope="scope_v4_evidence",
        run_id="run-v4-evidence",
        attempt_id="attempt-v4-evidence",
        stream_incarnation=3,
        event_type="assistant_text_delta",
        emitted_at="2026-08-20T00:00:00Z",
        payload={"delta": "legacy"},
    )
    try:
        await client.hset(state_key, mapping={"phase": "open", "open_protocol": "v4"})
        before_state = await client.hgetall(state_key)
        with pytest.raises(StreamContractError, match="stream_protocol_conflict"):
            await bridge.append(legacy)
        assert await client.xlen(key) == 0
        assert await client.hgetall(state_key) == before_state
    finally:
        await client.delete(key, state_key)
        await client.aclose()


@pytest.mark.asyncio
async def test_real_redis_append_keeps_same_event_identity_across_semantic_retry():
    client, key, state_key, bridge = await _stream()
    event = _envelope(event_id="evt4_retry")
    try:
        first = await bridge.append(event)
        second = await bridge.append(event)
        assert first != second
        rows = await client.xrange(key, min="-", max="+")
        assert len(rows) == 2
        decoded = [json.loads(fields["envelope"]) for _, fields in rows]
        assert [item["event_id"] for item in decoded] == ["evt4_retry", "evt4_retry"]
        assert decoded[0] == decoded[1]
        for item in decoded:
            public = project_public_envelope_v4(item)
            assert public is not None
            assert "tenant_scope" not in public
            assert "attempt_id" not in public
            assert "source" not in public
    finally:
        await client.delete(key, state_key)
        await client.aclose()


@pytest.mark.asyncio
async def test_real_redis_terminal_authority_blocks_late_frames_and_preserves_public_projection():
    client, key, state_key, bridge = await _stream()
    terminal = _envelope(
        event_id="evt4_terminal",
        event_type="run.succeeded",
        seq=2,
        payload={"terminal_event_id": "evt4_terminal", "hydrate_required": True},
    )
    try:
        await bridge.append(_envelope(event_id="evt4_before_terminal"))
        assert await bridge.append(terminal)
        with pytest.raises(StreamContractError, match="stream_terminal_closed"):
            await bridge.append(_envelope(event_id="evt4_after_terminal", seq=3))
        rows = await client.xrange(key, min="-", max="+")
        assert [json.loads(fields["envelope"])["event_id"] for _, fields in rows] == [
            "evt4_before_terminal",
            "evt4_terminal",
        ]
        public = project_public_envelope_v4(json.loads(rows[-1][1]["envelope"]))
        assert public is not None
        assert public["event_type"] == "run.succeeded"
        assert "tenant_scope" not in public
    finally:
        await client.delete(key, state_key)
        await client.aclose()


@pytest.mark.asyncio
async def test_real_redis_transient_outage_is_retryable_for_same_event():
    client, key, state_key, healthy = await _stream()
    unavailable_client = Redis.from_url(
        "redis://127.0.0.1:1",
        decode_responses=True,
        socket_connect_timeout=0.05,
        socket_timeout=0.05,
    )
    unavailable = V4RedisStreamBridge(RedisStreamBridge(publish_client=unavailable_client))
    event = _envelope(event_id="evt4_outage_retry")
    try:
        with pytest.raises(StreamTransportUnavailable):
            await unavailable.append(event)
        redis_id = await healthy.append(event)
        assert redis_id
        rows = await client.xrange(key, min="-", max="+")
        assert [json.loads(fields["envelope"])["event_id"] for _, fields in rows] == [
            "evt4_outage_retry"
        ]
    finally:
        await unavailable.aclose()
        await client.delete(key, state_key)
        await client.aclose()


@pytest.mark.asyncio
async def test_real_redis_rejects_private_fields_unknown_event_codes_and_cross_version_values():
    client, key, state_key, bridge = await _stream()
    try:
        private = _envelope(event_id="evt4_private", payload={"delta": "ok", "raw_command": "secret"})
        with pytest.raises(V4ProjectionError):
            await bridge.append(private)
        unknown = _envelope(event_id="evt4_unknown", event_type="private.executor.raw", payload={})
        with pytest.raises(V4ProjectionError):
            await bridge.append(unknown)
        cross_version = _envelope(event_id="evt4_v3", event_type="message.delta")
        cross_version["schema"] = "ai-platform.public-run-stream-event.v3"
        with pytest.raises(V4ProjectionError):
            await bridge.append(cross_version)
        assert await client.xlen(key) == 0
    finally:
        await client.delete(key, state_key)
        await client.aclose()
