from __future__ import annotations

import json
import os

from redis.asyncio import Redis
import pytest

from app.streaming.api import (
    V4ProjectionError,
    build_v4_control,
    project_public_envelope_v4,
    stream_key,
)
from app.streaming.redis import (
    RedisStreamBridge,
    StreamContractError,
    StreamTransportUnavailable,
)
from app.streaming.infrastructure.v4 import V4RedisStreamBridge


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
    bridge = V4RedisStreamBridge(RedisStreamBridge(publish_client=client))
    await bridge.append(build_v4_control(
        event_id="evt4_open", tenant_scope="scope_v4_evidence", run_id="run-v4-evidence",
        attempt_id="attempt-v4-evidence", stream_incarnation=3, event_type="stream.open",
        payload={"design_id": "ai-platform.redis-streams-sse-event-channel.v4"},
        source={"kind": "stream_authority", "authority_id": "evt4_open"},
    ))
    return client, key, state_key, bridge


@pytest.mark.asyncio
async def test_real_redis_rejects_v4_append_to_legacy_v3_phase_without_mutation():
    client, key, state_key, _legacy_bridge = await _stream()
    bridge = V4RedisStreamBridge(RedisStreamBridge(publish_client=client))
    try:
        await client.hset(state_key, mapping={"phase": "open", "open_protocol": "v3"})
        before_state = await client.hgetall(state_key)
        with pytest.raises(StreamContractError, match="stream_protocol_conflict"):
            await bridge.append(_envelope(event_id="evt4_on_v3"))
        assert await client.xlen(key) == 1
        assert await client.hgetall(state_key) == before_state
    finally:
        await client.delete(key, state_key)
        await client.aclose()


@pytest.mark.asyncio
async def test_real_missing_stream_is_not_recreated_by_terminal_publication():
    client, key, state_key, bridge = await _stream()
    try:
        await client.delete(key)
        before = await client.hgetall(state_key)
        with pytest.raises(StreamContractError, match="stream_missing"):
            await bridge.append(_envelope(event_id="evt4_missing", event_type="run.succeeded"))
        assert await client.xlen(key) == 0
        assert await client.hgetall(state_key) == before
    finally:
        await client.delete(key, state_key)
        await client.aclose()


@pytest.mark.asyncio
async def test_real_redis_replay_rejects_deleted_predecessor_instead_of_skipping():
    client, key, state_key, bridge = await _stream()
    try:
        first = await bridge.append(_envelope(event_id="evt4_replay_predecessor"))
        second = await bridge.append(
            _envelope(event_id="evt4_replay_successor", seq=2)
        )
        await client.xdel(key, first)

        with pytest.raises(
            StreamContractError,
            match="stream_replay_continuity_unproven",
        ):
            await bridge.replay_page(
                tenant_scope_value="scope_v4_evidence",
                run_id="run-v4-evidence",
                attempt_id="attempt-v4-evidence",
                stream_incarnation=3,
                after_redis_id=first,
                through_redis_id=second,
            )
    finally:
        await client.delete(key, state_key)
        await client.aclose()


@pytest.mark.asyncio
async def test_real_redis_append_reuses_one_receipt_across_semantic_retry():
    client, key, state_key, bridge = await _stream()
    event = _envelope(event_id="evt4_retry")
    try:
        first = await bridge.append(event)
        second = await bridge.append(event)
        assert second == first
        rows = await client.xrange(key, min="-", max="+")
        assert len(rows) == 2
        decoded = [json.loads(fields["envelope"]) for _, fields in rows]
        assert [item["event_id"] for item in decoded] == ["evt4_open", "evt4_retry"]
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
        decoded = [json.loads(fields["envelope"]) for _, fields in rows]
        assert [item["event_id"] for item in decoded[1:3]] == [
            "evt4_before_terminal",
            "evt4_terminal",
        ]
        assert len(decoded) == 4
        assert decoded[-1]["event_type"] == "stream.end"
        assert decoded[-1]["payload"]["terminal_event_id"] == "evt4_terminal"
        public = project_public_envelope_v4(decoded[-2])
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
            "evt4_open", "evt4_outage_retry"
        ]
    finally:
        await unavailable.aclose()
        await unavailable_client.aclose()
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
        assert await client.xlen(key) == 1
    finally:
        await client.delete(key, state_key)
        await client.aclose()


@pytest.mark.asyncio
async def test_real_redis_replay_decodes_production_lua_field_value_rows():
    client, key, state_key, bridge = await _stream()
    try:
        first = await bridge.append(_envelope(event_id="evt4_replay_first"))
        second = await bridge.append(
            _envelope(event_id="evt4_replay_second", seq=2)
        )

        replayed = await bridge.replay_page(
            tenant_scope_value="scope_v4_evidence",
            run_id="run-v4-evidence",
            attempt_id="attempt-v4-evidence",
            stream_incarnation=3,
            after_redis_id=first,
            through_redis_id=second,
        )

        assert [entry.cursor.redis_id for entry in replayed] == [second]
        assert [entry.envelope["event_id"] for entry in replayed] == [
            "evt4_replay_second"
        ]
    finally:
        await client.delete(key, state_key)
        await client.aclose()


@pytest.mark.asyncio
async def test_real_redis_exact_resume_reports_terminal_and_end_state_after_trim():
    client, key, state_key, bridge = await _stream()
    scope = dict(
        tenant_scope_value="scope_v4_evidence", run_id="run-v4-evidence",
        attempt_id="attempt-v4-evidence", stream_incarnation=3,
    )
    try:
        cursors = [await bridge.append(_envelope(event_id=f"evt4_retained_{seq}", seq=seq))
                   for seq in range(1, 141)]
        await client.xtrim(key, minid=cursors[2], approximate=False)
        resume = await bridge.resolve_resume(
            tenant_scope_value=scope["tenant_scope_value"], run_id=scope["run_id"],
            attempt_id=scope["attempt_id"], current_stream_incarnation=3,
            last_event_id=f"run-v4-evidence:3:{cursors[-2]}",
        )
        assert resume.gap is None
        assert resume.terminal_event_id is None
        assert resume.ended is False
        with pytest.raises(StreamContractError, match="v4_stream_authority_mismatch"):
            await bridge.resolve_resume(
                tenant_scope_value=scope["tenant_scope_value"],
                run_id=scope["run_id"],
                attempt_id="attempt-v4-other",
                current_stream_incarnation=3,
                last_event_id=f"run-v4-evidence:3:{cursors[-2]}",
            )
        remaining = await bridge.replay_page(
            **scope, after_redis_id=resume.after_redis_id, through_redis_id=cursors[-1],
        )
        assert [item.cursor.redis_id for item in remaining] == [cursors[-1]]

        await client.xtrim(key, minid=cursors[-1], approximate=False)
        trimmed = await bridge.resolve_resume(
            tenant_scope_value=scope["tenant_scope_value"], run_id=scope["run_id"],
            attempt_id=scope["attempt_id"], current_stream_incarnation=3,
            last_event_id=f"run-v4-evidence:3:{cursors[-2]}",
        )
        assert trimmed.gap is not None
        assert trimmed.gap.reason == "retained_history_unavailable"

        end = await bridge.append(_envelope(
            event_id="evt4_trim_terminal", event_type="run.succeeded", seq=141,
        ))
        terminal_rows = await client.xrevrange(key, count=2)
        assert [json.loads(fields["envelope"])["event_type"]
                for _, fields in terminal_rows] == ["stream.end", "run.succeeded"]
        assert terminal_rows[0][0] == end
        terminal = terminal_rows[1][0]
        await client.xtrim(key, minid=terminal, approximate=False)
        terminal_resume = await bridge.resolve_resume(
            tenant_scope_value=scope["tenant_scope_value"], run_id=scope["run_id"],
            attempt_id=scope["attempt_id"], current_stream_incarnation=3,
            last_event_id=f"run-v4-evidence:3:{terminal}",
        )
        assert terminal_resume.gap is None
        assert terminal_resume.terminal_event_id == "evt4_trim_terminal"
        assert terminal_resume.ended is False
        await client.xtrim(key, minid=end, approximate=False)
        end_resume = await bridge.resolve_resume(
            tenant_scope_value=scope["tenant_scope_value"], run_id=scope["run_id"],
            attempt_id=scope["attempt_id"], current_stream_incarnation=3,
            last_event_id=f"run-v4-evidence:3:{end}",
        )
        assert end_resume.gap is None
        assert end_resume.terminal_event_id == "evt4_trim_terminal"
        assert end_resume.ended is True
    finally:
        await client.delete(key, state_key)
        await client.aclose()


@pytest.mark.asyncio
async def test_real_initial_replay_rejects_origin_trim_after_resume():
    client, key, state_key, bridge = await _stream()
    try:
        origin = (await client.xrange(key, count=1))[0][0]
        tail = await bridge.append(_envelope(event_id="evt4_initial_trim", seq=3))
        identity = dict(tenant_scope_value="scope_v4_evidence", run_id="run-v4-evidence",
                        attempt_id="attempt-v4-evidence")
        resume = await bridge.resolve_resume(
            **identity, current_stream_incarnation=3, last_event_id=None,
        )
        assert resume.after_redis_id == "0-0" and resume.gap is None
        await client.xtrim(key, minid=tail, approximate=False)
        assert await client.xrange(key, min=origin, max=origin) == []
        assert (await client.xrange(key, count=1))[0][0] == tail
        with pytest.raises(StreamContractError, match="stream_replay_continuity_unproven"):
            await bridge.replay_page(
                **identity, stream_incarnation=3,
                after_redis_id="0-0", through_redis_id=tail,
            )
    finally:
        await client.delete(key, state_key)
        await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("trim_after_xread", [False, True])
async def test_real_live_read_rejects_trimmed_accepted_anchor_and_unread_row(monkeypatch, trim_after_xread):
    client, key, state_key, bridge = await _stream()
    try:
        accepted = await bridge.append(_envelope(event_id="evt4_accepted", seq=1))
        unread = await bridge.append(_envelope(event_id="evt4_unread_trimmed", seq=3))
        tail = await bridge.append(_envelope(event_id="evt4_retained_tail", seq=7))
        before = [item[0] for item in await client.xrange(key)]
        assert accepted in before and unread in before and tail in before
        original_xread = client.xread
        if trim_after_xread:
            async def read_then_trim(*args, **kwargs):
                result = await original_xread(*args, **kwargs)
                await client.xtrim(key, minid=tail, approximate=False)
                return result
            monkeypatch.setattr(client, "xread", read_then_trim)
        else:
            await client.xtrim(key, minid=tail, approximate=False)
        with pytest.raises(StreamContractError, match="stream_replay_continuity_unproven"):
            await bridge.read_stream(
                tenant_scope_value="scope_v4_evidence", run_id="run-v4-evidence",
                attempt_id="attempt-v4-evidence", stream_incarnation=3,
                after_redis_id=accepted, block_ms=1,
            )
        # Exact retained rows, not numeric sequence adjacency, prove the gap.
        assert await client.xrange(key, min=accepted, max=accepted) == []
        assert await client.xrange(key, min=unread, max=unread) == []
        assert [item[0] for item in await client.xrange(key)] == [tail]
    finally:
        await client.delete(key, state_key)
        await client.aclose()


@pytest.mark.asyncio
async def test_real_live_retained_anchor_survives_open_trim_and_missing_is_not_idle():
    client, key, state_key, bridge = await _stream()
    identity = dict(tenant_scope_value="scope_v4_evidence", run_id="run-v4-evidence",
                    attempt_id="attempt-v4-evidence", stream_incarnation=3)
    try:
        accepted = await bridge.append(_envelope(event_id="evt4_idle_anchor", seq=3))
        await client.xtrim(key, minid=accepted, approximate=False)
        assert await bridge.replay_page(
            **identity, after_redis_id=accepted, through_redis_id=accepted,
        ) == ()
        assert await bridge.read_stream(**identity, after_redis_id=accepted, block_ms=1) == ()
        first = await bridge.append(_envelope(event_id="evt4_bounded_first", seq=7))
        second = await bridge.append(_envelope(event_id="evt4_bounded_second", seq=11))
        page = await bridge.read_stream(**identity, after_redis_id=accepted, count=1, block_ms=1)
        assert [item.cursor.redis_id for item in page] == [first]
        page = await bridge.read_stream(**identity, after_redis_id=first, count=1, block_ms=1)
        assert [item.cursor.redis_id for item in page] == [second]
        await client.delete(key, state_key)
        with pytest.raises(StreamContractError, match="stream_missing"):
            await bridge.read_stream(**identity, after_redis_id=second, block_ms=1)
        assert await client.exists(key) == 0
    finally:
        await client.delete(key, state_key)
        await client.aclose()
