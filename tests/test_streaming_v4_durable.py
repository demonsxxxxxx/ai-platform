from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

import pytest

from app.streaming.redis import StreamAuthority
from app.streaming.v4 import (
    V4RedisStreamBridge,
    opaque_message_id,
    project_public_envelope_v4,
    project_public_v4,
    list_pending_v4_rows,
)


def _authority() -> StreamAuthority:
    return StreamAuthority(
        tenant_id="tenant-a",
        run_id="run-a",
        attempt_id="attempt-a",
        tenant_scope="tenant-a",
        stream_incarnation=2,
        state="confirmed",
        open_event_id="open-a",
        open_payload_bytes="{}",
        open_payload_digest="digest",
        authorization_epoch=4,
        revocation_state="active",
    )


def _row(payload: dict[str, object], **overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "id": "evt4_a",
        "tenant_id": "tenant-a",
        "run_id": "run-a",
        "sequence": 7,
        "event_type": "message.delta",
        "visible_to_user": True,
        "payload_json": {
            **payload,
            "__stream_v4": {
                "attempt_id": "attempt-a",
                "version": 1,
                "stream_incarnation": 2,
                "authorization_epoch": 4,
                "message_id": opaque_message_id("tenant-a", "run-a"),
                "publication_state": "pending",
            },
        },
        "stream_publication_state": "pending",
        "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
    }
    row.update(overrides)
    return row


def test_v4_projection_is_internal_and_public_projection_strips_authority_fields() -> None:
    internal = project_public_v4(_row({"delta": "hello"}), authority=_authority())

    assert internal is not None
    assert internal["schema"] == "ai-platform.stream-event.v4"
    assert internal["tenant_scope"] == "tenant-a"
    assert internal["attempt_id"] == "attempt-a"
    public = project_public_envelope_v4(internal)
    assert public is not None
    assert public["schema"] == "ai-platform.public-run-stream-event.v4"
    assert "tenant_scope" not in public
    assert "attempt_id" not in public
    assert public["message_id"] == opaque_message_id("tenant-a", "run-a")


def test_v4_projection_rejects_unknown_payload_keys() -> None:
    assert project_public_v4(
        _row({"delta": "hello", "raw_output": "secret"}), authority=_authority()
    ) is None


def test_v4_projection_rejects_invalid_payload_types_and_nonopaque_message_ids() -> None:
    assert project_public_v4(_row({"delta": 3}), authority=_authority()) is None
    row = _row({"delta": "hello"})
    row["payload_json"]["__stream_v4"]["message_id"] = "attempt-a"
    assert project_public_v4(row, authority=_authority()) is None


def test_v4_gateway_rejects_internal_envelope_extensions() -> None:
    internal = project_public_v4(_row({"delta": "hello"}), authority=_authority())
    assert internal is not None
    extended = {**internal, "executor_private": "secret"}
    assert project_public_envelope_v4(extended) is None


@pytest.mark.asyncio
async def test_v4_pending_query_is_exact_visible_due_ordered_skip_locked() -> None:
    class FakeCursor:
        async def fetchall(self) -> list[dict[str, object]]:
            return []

    class FakeConnection:
        statement = ""
        params: tuple[object, ...] | None = None

        async def execute(self, statement: str, params: tuple[object, ...]) -> FakeCursor:
            self.statement = statement
            self.params = params
            return FakeCursor()

    conn = FakeConnection()
    assert await list_pending_v4_rows(conn, limit=3) == ()
    normalized = " ".join(conn.statement.lower().split())
    assert "visible_to_user = true" in normalized
    assert "stream_publication_state = 'pending'" in normalized
    assert "stream_publication_next_attempt_at <= now()" in normalized
    assert "order by stream_publication_next_attempt_at asc nulls first, created_at asc, id asc" in normalized
    assert "limit %s for update skip locked" in normalized
    assert conn.params == (3,)


def test_v4_projection_rejects_authority_mismatch() -> None:
    assert project_public_v4(
        _row({"delta": "hello"}, stream_publication_state="published"),
        authority=replace(_authority(), authorization_epoch=5),
    ) is None


@pytest.mark.asyncio
async def test_v4_bridge_uses_existing_atomic_append_boundary() -> None:
    calls: list[dict[str, object]] = []

    class FakeBridge:
        async def append_canonical(self, **kwargs: object) -> str:
            calls.append(kwargs)
            return "11-3"

        async def aclose(self) -> None:
            return None

    internal = project_public_v4(_row({"delta": "hello"}), authority=_authority())
    assert internal is not None
    bridge = V4RedisStreamBridge(FakeBridge())
    assert await bridge.append(internal) == "11-3"
    assert calls[0]["event_type"] == "message.delta"
    assert b"attempt-a" in calls[0]["envelope_bytes"]
    assert b"tenant-a" in calls[0]["envelope_bytes"]
