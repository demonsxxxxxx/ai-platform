from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone

import pytest
from redis.asyncio import Redis

from app.streaming.api import stream_key

from app.streaming.contracts import canonical_json_bytes
from app.streaming.redis import (
    RedisStreamBridge,
    SseAuthorityConflictError,
    StreamAuthority,
    StreamContractError,
    create_or_get_stream_admission_v4,
)
from app.streaming.v4 import (
    V4RedisStreamBridge,
    build_v4_control,
    project_public_envelope_v4,
    recover_v4_and_resume,
    opaque_message_id,
    stream_end_event_id,
)


class FakeRedis:
    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []
        self.rows: list[tuple[str, dict[str, str]]] = []

    async def eval(self, *args: object) -> str:
        self.calls.append(args)
        redis_id = f"1-{len(self.rows)}"
        self.rows.append((redis_id, {"envelope": str(args[7])}))
        return redis_id

    async def xrange(
        self,
        _key: object,
        min: str = "-",
        max: str = "+",
        count: int | None = None,
    ):
        rows = self.rows
        if min.startswith("("):
            rows = [item for item in rows if item[0] > min[1:]]
        elif min not in {"-", "+"}:
            rows = [item for item in rows if item[0] == min]
        if max not in {"+", "-"}:
            rows = [item for item in rows if item[0] <= max]
        return rows[:count] if count is not None else rows

    async def xrevrange(
        self,
        _key: object,
        max: str = "+",
        min: str = "-",
        count: int | None = None,
    ):
        rows = list(reversed(self.rows))
        return rows[:count] if count is not None else rows

    async def publish(self, *_args: object) -> int:
        return 1


class Result:
    def __init__(self, rows: tuple[dict[str, object], ...]) -> None:
        self.rows = rows

    async def fetchall(self) -> tuple[dict[str, object], ...]:
        return self.rows


class Connection:
    def __init__(self, rows: tuple[dict[str, object], ...]) -> None:
        self.rows = rows
        self.statements: list[str] = []

    async def execute(self, statement: str, _params: object) -> Result:
        self.statements.append(statement)
        return Result(self.rows)


def _open_payload() -> str:
    return canonical_json_bytes(
        build_v4_control(
            event_id="open-a",
            tenant_scope="scope-a",
            run_id="run-a",
            attempt_id="attempt-a",
            stream_incarnation=2,
            event_type="stream.open",
            payload={"design_id": "ai-platform.redis-streams-sse-event-channel.v4"},
            source={"kind": "stream_authority", "authority_id": "open-a"},
            emitted_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
    ).decode()


def authority() -> StreamAuthority:
    payload = _open_payload()
    return StreamAuthority(
        tenant_id="tenant-a",
        run_id="run-a",
        attempt_id="attempt-a",
        tenant_scope="scope-a",
        stream_incarnation=2,
        state="confirmed",
        open_event_id="open-a",
        open_payload_bytes=payload,
        open_payload_digest=hashlib.sha256(payload.encode()).hexdigest(),
        authorization_epoch=3,
        revocation_state="active",
    )


def control(event_type: str, payload: dict[str, object]) -> dict[str, object]:
    return build_v4_control(
        event_id=f"evt-{event_type.replace('.', '-')}",
        tenant_scope="scope-a",
        run_id="run-a",
        attempt_id="attempt-a",
        stream_incarnation=2,
        event_type=event_type,
        payload=payload,
        source={"kind": "stream_authority", "authority_id": "open-a"},
        emitted_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


def row() -> dict[str, object]:
    return {
        "id": "evt4_delta",
        "tenant_id": "tenant-a",
        "run_id": "run-a",
        "sequence": 1,
        "event_type": "message.delta",
        "visible_to_user": True,
        "payload_json": {
            "delta": "hello",
            "__stream_v4": {
                "attempt_id": "attempt-a",
                "version": 1,
                "stream_incarnation": 2,
                "authorization_epoch": 3,
                "message_id": opaque_message_id("tenant-a", "run-a"),
                "publication_state": "published",
            },
        },
        "stream_publication_state": "published",
        "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
    }


def test_v4_controls_are_strict_and_public_projection_preserves_replayability() -> None:
    controls = (
        control("stream.open", {"design_id": "ai-platform.redis-streams-sse-event-channel.v4"}),
        control("stream.heartbeat", {"status": "running"}),
        control(
            "stream.gap",
            {
                "reason": "retained_history_unavailable",
                "recovery": "reload_durable_state",
                "requested_event_id": "2-0",
                "requested_stream_incarnation": 2,
                "current_stream_incarnation": 2,
                "earliest_available_event_id": "3-0",
                "latest_available_event_id": "9-0",
            },
        ),
        control("stream.end", {"terminal_event_id": "terminal-a"}),
    )
    assert [project_public_envelope_v4(item)["replayable"] for item in controls] == [True, False, False, True]
    assert project_public_envelope_v4(controls[0])["schema"] == "ai-platform.public-run-stream-control.v4"


@pytest.mark.asyncio
async def test_v4_dot_controls_use_existing_lua_authority_and_heartbeat_has_no_cursor() -> None:
    client = FakeRedis()
    bridge = V4RedisStreamBridge(RedisStreamBridge(publish_client=client))
    await bridge.append(control("stream.open", {"design_id": "ai-platform.redis-streams-sse-event-channel.v4"}))
    assert client.calls[-1][9] == "stream_open"
    with pytest.raises(StreamContractError, match="v4_control_not_replayable"):
        await bridge.append(control("stream.heartbeat", {"status": "running"}))
    await bridge.publish_non_replayable(control("stream.heartbeat", {"status": "running"}))


@pytest.mark.asyncio
async def test_v4_recovery_reads_published_rows_only() -> None:
    conn = Connection((row(),))
    published: list[dict[str, object]] = []

    class Bridge:
        async def ensure_open(self, _payload: str) -> str:
            return "6-0"

        async def append(self, envelope: dict[str, object]) -> str:
            published.append(envelope)
            return "7-0"

    recovery = await recover_v4_and_resume(
        conn,
        bridge=Bridge(),
        tenant_id="tenant-a",
        run_id="run-a",
        authority=authority(),
        after_sequence=0,
        limit=8,
    )
    assert "stream_publication_state = 'published'" in conn.statements[0]
    assert len(published) == 1
    assert recovery.transport_cursors == ("7-0",)
    assert project_public_envelope_v4(published[0]) is not None


@pytest.mark.asyncio
async def test_terminal_append_requires_the_deterministic_end_pair_before_success() -> None:
    class PairRedis(FakeRedis):
        def __init__(self) -> None:
            super().__init__()
            self.fail_end_once = True
            self.attempts: list[tuple[object, ...]] = []

        async def eval(self, *args: object) -> str:
            self.attempts.append(args)
            if args[9] == "end" and self.fail_end_once:
                self.fail_end_once = False
                raise RuntimeError("end unavailable")
            return await super().eval(*args)

    client = PairRedis()
    bridge = V4RedisStreamBridge(RedisStreamBridge(publish_client=client))
    await bridge.append(
        control(
            "stream.open",
            {"design_id": "ai-platform.redis-streams-sse-event-channel.v4"},
        )
    )
    terminal = {
        "schema": "ai-platform.stream-event.v4",
        "event_id": "evt4_terminal_pair",
        "tenant_scope": "scope-a",
        "run_id": "run-a",
        "attempt_id": "attempt-a",
        "message_id": None,
        "seq": 2,
        "event_type": "run.succeeded",
        "stream_incarnation": 2,
        "replayable": True,
        "trace_ref": None,
        "causation_event_id": None,
        "emitted_at": "2026-01-01T00:00:00Z",
        "projection_version": "public-stream-v4",
        "payload": {"terminal_event_id": "evt4_terminal_pair", "hydrate_required": True},
        "source": {"kind": "terminal_intent", "terminal_event_id": "evt4_terminal_pair"},
    }
    with pytest.raises(Exception, match="stream_append_unavailable"):
        await bridge.append(terminal)
    result = await bridge.append(terminal)
    assert result == "1-3"
    assert [call[9] for call in client.calls] == ["stream_open", "terminal", "terminal", "end"]
    assert [call[6] for call in client.attempts] == [
        "evt-stream-open",
        "evt4_terminal_pair",
        stream_end_event_id("evt4_terminal_pair"),
        "evt4_terminal_pair",
        stream_end_event_id("evt4_terminal_pair"),
    ]


def test_v4_decode_rejects_foreign_tenant_and_attempt() -> None:
    bridge = V4RedisStreamBridge(RedisStreamBridge(publish_client=FakeRedis()))
    envelope = control("stream.open", {"design_id": "ai-platform.redis-streams-sse-event-channel.v4"})
    for field, value in (("tenant_scope", "scope-foreign"), ("attempt_id", "attempt-foreign")):
        foreign = dict(envelope)
        foreign[field] = value
        with pytest.raises(StreamContractError, match="v4_stream_authority_mismatch"):
            bridge.decode_live_publication(
                redis_id="1-0",
                envelope_json=json.dumps(foreign),
                tenant_scope_value="scope-a",
                run_id="run-a",
                attempt_id="attempt-a",
                stream_incarnation=2,
            )


@pytest.mark.asyncio
async def test_v4_heartbeat_and_gap_use_real_retained_cursor_bounds() -> None:
    client = FakeRedis()
    bridge = V4RedisStreamBridge(RedisStreamBridge(publish_client=client))
    await bridge.append(
        control(
            "stream.open",
            {"design_id": "ai-platform.redis-streams-sse-event-channel.v4"},
        )
    )
    heartbeat, heartbeat_cursor = await bridge.build_heartbeat(
        event_id="hb4_a",
        tenant_scope_value="scope-a",
        run_id="run-a",
        attempt_id="attempt-a",
        stream_incarnation=2,
        status="running",
    )
    gap, gap_cursor = await bridge.build_gap(
        event_id="gap4_a",
        tenant_scope_value="scope-a",
        run_id="run-a",
        attempt_id="attempt-a",
        requested_event_id="run-a:2:1-0",
        requested_stream_incarnation=2,
        current_stream_incarnation=2,
        reason="retained_history_unavailable",
    )
    assert heartbeat_cursor == "run-a:2:1-0"
    assert gap_cursor == heartbeat_cursor
    assert heartbeat["payload"] == {"status": "running"}
    assert gap["payload"]["earliest_available_event_id"] == "1-0"
    assert gap["payload"]["latest_available_event_id"] == "1-0"
    assert gap["payload"]["requested_event_id"] == "1-0"


@pytest.mark.asyncio
async def test_existing_v4_authority_requires_canonical_digest_and_identity() -> None:
    payload = _open_payload()
    base = {
        "tenant_id": "tenant-a",
        "run_id": "run-a",
        "attempt_id": "attempt-a",
        "tenant_scope": "scope-a",
        "stream_incarnation": 2,
        "state": "confirmed",
        "open_event_id": "open-a",
        "open_payload_bytes": payload,
        "open_payload_digest": hashlib.sha256(payload.encode()).hexdigest(),
        "authorization_epoch": 3,
        "revocation_state": "active",
        "design_id": "ai-platform.redis-streams-sse-event-channel.v4",
        "projection_version": "public-stream-v4",
    }

    class Result:
        async def fetchone(self):
            return dict(base)

    class Connection:
        async def execute(self, *_args: object):
            return Result()

    assert (await create_or_get_stream_admission_v4(
        Connection(),
        tenant_id="tenant-a",
        run_id="run-a",
        attempt_id="attempt-a",
        tenant_scope="scope-a",
    )).open_event_id == "open-a"

    for override in ({"open_payload_digest": "wrong"}, {"open_event_id": "other"}):
        base.update(override)
        with pytest.raises(SseAuthorityConflictError, match="sse_stream_protocol_conflict"):
            await create_or_get_stream_admission_v4(
                Connection(),
                tenant_id="tenant-a",
                run_id="run-a",
                attempt_id="attempt-a",
                tenant_scope="scope-a",
            )
        base.update({key: value for key, value in {
            "open_payload_digest": hashlib.sha256(payload.encode()).hexdigest(),
            "open_event_id": "open-a",
        }.items()})


@pytest.mark.asyncio
async def test_real_redis_rebuilds_open_after_event_stream_loss() -> None:
    redis_url = os.getenv("AI_PLATFORM_SSE_REDIS_TEST_URL", "").strip()
    if not redis_url:
        pytest.skip("AI_PLATFORM_SSE_REDIS_TEST_URL is not configured")
    client = Redis.from_url(redis_url, decode_responses=True)
    key = stream_key(
        tenant_scope_value="scope-a",
        run_id="run-a",
        stream_incarnation=2,
    )
    bridge = V4RedisStreamBridge(RedisStreamBridge(publish_client=client))
    opening = control(
        "stream.open",
        {"design_id": "ai-platform.redis-streams-sse-event-channel.v4"},
    )
    try:
        await client.delete(key, f"{key}:state")
        await bridge.append(opening)
        await client.delete(key)
        await bridge.ensure_open(canonical_json_bytes(opening))
        rows = await client.xrange(key, min="-", max="+")
        assert len(rows) == 1
        assert json.loads(rows[0][1]["envelope"])["event_type"] == "stream.open"
    finally:
        await client.delete(key, f"{key}:state")
        await client.aclose()
