from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.streaming.redis import RedisStreamBridge, StreamAuthority, StreamContractError
from app.streaming.v4 import (
    V4RedisStreamBridge,
    build_v4_control,
    project_public_envelope_v4,
    recover_v4_and_resume,
    opaque_message_id,
)


class FakeRedis:
    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []

    async def eval(self, *args: object) -> str:
        self.calls.append(args)
        return "1-0"

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


def authority() -> StreamAuthority:
    return StreamAuthority(
        tenant_id="tenant-a",
        run_id="run-a",
        attempt_id="attempt-a",
        tenant_scope="scope-a",
        stream_incarnation=2,
        state="confirmed",
        open_event_id="open-a",
        open_payload_bytes="{}",
        open_payload_digest="digest",
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
