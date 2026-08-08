from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.auth import AuthPrincipal
from app.routes import lambchat_compat as route
from app.streaming.redis import (
    ResumeDecision,
    SseAuthorityLease,
    StreamCursor,
    StreamEntry,
    StreamEnvelope,
    StreamGap,
    StreamAuthority,
    StreamTransportUnavailable,
    committed_public_stream_event,
)


@asynccontextmanager
async def transaction():
    yield object()


def authority():
    return StreamAuthority(
        "tenant-a",
        "run-a",
        "attempt-a",
        "scope-a",
        1,
        "confirmed",
        "sev-open",
        "{}",
        "digest",
        1,
        "active",
    )


def lease():
    return SseAuthorityLease(
        "lease-a",
        "tenant-a",
        "run-a",
        "api-a",
        "connection-a",
        1,
        datetime.now(timezone.utc) + timedelta(seconds=15),
    )


def entry(redis_id, event_id, event_type, payload):
    envelope = StreamEnvelope(
        event_id,
        "scope-a",
        "run-a",
        "attempt-a",
        1,
        event_type,
        payload,
        "2026-08-09T00:00:00Z",
    )
    return StreamEntry(StreamCursor("run-a", 1, redis_id), envelope)


def patch_authority(monkeypatch):
    async def get_run(conn, *, tenant_id, user_id, run_id):
        return {"id": run_id, "session_id": "session-a", "status": "running"}

    async def get_authority(conn, *, tenant_id, run_id):
        return authority()

    async def acquire(conn, **kwargs):
        return lease()

    async def close(conn, **kwargs):
        return True

    async def get_intent(conn, *, tenant_id, run_id):
        return None

    monkeypatch.setattr(route, "transaction", transaction)
    monkeypatch.setattr(route.repositories, "get_authorized_run", get_run)
    monkeypatch.setattr(route, "get_stream_authority", get_authority)
    monkeypatch.setattr(route, "acquire_sse_authority_lease", acquire)
    monkeypatch.setattr(route, "close_sse_authority_lease", close)
    monkeypatch.setattr(route, "get_terminal_intent", get_intent)
    monkeypatch.setattr(
        route, "get_settings", lambda: SimpleNamespace(sse_authority_lease_seconds=15)
    )


@pytest.mark.asyncio
async def test_v21_stream_uses_native_redis_cursor_and_never_reads_pg_deltas(
    monkeypatch,
):
    patch_authority(monkeypatch)
    rows = (
        entry(
            "1-0",
            "sev-open",
            "stream_open",
            {"design_id": "ai-platform.redis-streams-sse-event-channel.v2.1"},
        ),
        entry("2-0", "sev-delta", "assistant_text_delta", {"delta": "hello"}),
        entry(
            "3-0",
            "sev-terminal",
            "terminal",
            {
                "event_id": "sev-terminal",
                "hydrate_required": True,
                "status": "succeeded",
            },
        ),
        entry("4-0", "sev-end", "end", {"terminal_event_id": "sev-terminal"}),
    )

    class Bridge:
        async def resolve_resume(self, **kwargs):
            assert kwargs["last_event_id"] is None
            return ResumeDecision("0-0", None)

        async def read(self, **kwargs):
            return rows

        async def aclose(self):
            return None

    async def forbidden(*args, **kwargs):
        raise AssertionError("PG run_events must not drive live SSE")

    monkeypatch.setattr(route, "RedisStreamBridge", Bridge)
    monkeypatch.setattr(route.repositories, "list_run_events", forbidden)
    response = await route.chat_session_stream(
        "session-a",
        "run-a",
        principal=AuthPrincipal(
            user_id="user-a", display_name="User", tenant_id="tenant-a"
        ),
    )
    body = "".join([chunk async for chunk in response.body_iterator])
    assert response.headers["cache-control"] == "no-cache, no-transform"
    assert response.headers["x-accel-buffering"] == "no"
    assert "id: run-a:1:2-0" in body and '"content": "hello"' in body
    assert body.index("id: run-a:1:3-0") < body.index("id: run-a:1:4-0")


@pytest.mark.asyncio
async def test_v21_stream_maps_committed_execution_projection_from_redis(monkeypatch):
    patch_authority(monkeypatch)
    committed = {
        "id": "evt-execution-1",
        "run_id": "run-a",
        "sequence": 11,
        "event_type": "execution_step",
        "visible_to_user": True,
        "created_at": "2026-08-09T00:00:00Z",
        "payload_json": {
            "step_id": "pex_execution_1",
            "kind": "processing",
            "stage": "execution",
            "status": "running",
            "title": "Process request",
            "summary": "Running controlled processing",
            "progress": {"current": 0, "total": 1},
        },
    }
    projection = committed_public_stream_event(committed)
    assert projection is not None
    envelope_type, payload = projection
    rows = (
        entry(
            "1-0",
            "sev-open",
            "stream_open",
            {"design_id": "ai-platform.redis-streams-sse-event-channel.v2.1"},
        ),
        entry("2-0", "evt-execution-1", envelope_type, payload),
    )

    class Bridge:
        reads = 0

        async def resolve_resume(self, **kwargs):
            return ResumeDecision("0-0", None)

        async def read(self, **kwargs):
            self.reads += 1
            if self.reads == 1:
                return rows
            raise StreamTransportUnavailable("stop")

        async def aclose(self):
            return None

    monkeypatch.setattr(route, "RedisStreamBridge", Bridge)
    response = await route.chat_session_stream(
        "session-a",
        "run-a",
        principal=AuthPrincipal(
            user_id="user-a",
            display_name="User",
            tenant_id="tenant-a",
        ),
    )
    body = "".join([chunk async for chunk in response.body_iterator])

    assert "event: execution_step\n" in body
    assert "id: run-a:1:2-0\n" in body
    assert '"event_id": "evt-execution-1"' in body
    assert '"sequence": 11' in body


@pytest.mark.asyncio
async def test_v21_trim_gap_is_idless_and_requests_durable_hydration(monkeypatch):
    patch_authority(monkeypatch)

    class Bridge:
        async def resolve_resume(self, **kwargs):
            return ResumeDecision(
                None, StreamGap("retained_history_unavailable", "run-a:1:1-0", 1, 1)
            )

        async def read(self, **kwargs):
            raise AssertionError("gap must close before XREAD")

        async def aclose(self):
            return None

    monkeypatch.setattr(route, "RedisStreamBridge", Bridge)
    response = await route.chat_session_stream(
        "session-a",
        "run-a",
        last_event_id="run-a:1:1-0",
        principal=AuthPrincipal(
            user_id="user-a", display_name="User", tenant_id="tenant-a"
        ),
    )
    body = "".join([chunk async for chunk in response.body_iterator])
    assert body.startswith("event: gap\n")
    assert "id:" not in body and '"recovery": "reload_durable_state"' in body


@pytest.mark.asyncio
async def test_v21_end_before_terminal_fails_closed_without_emitting_end(monkeypatch):
    patch_authority(monkeypatch)
    rows = (
        entry(
            "1-0",
            "sev-open",
            "stream_open",
            {"design_id": "ai-platform.redis-streams-sse-event-channel.v2.1"},
        ),
        entry("2-0", "sev-end", "end", {"terminal_event_id": "sev-terminal"}),
    )

    class Bridge:
        async def resolve_resume(self, **kwargs):
            return ResumeDecision("0-0", None)

        async def read(self, **kwargs):
            return rows

        async def aclose(self):
            return None

    monkeypatch.setattr(route, "RedisStreamBridge", Bridge)
    response = await route.chat_session_stream(
        "session-a",
        "run-a",
        principal=AuthPrincipal(
            user_id="user-a",
            display_name="User",
            tenant_id="tenant-a",
        ),
    )
    body = "".join([chunk async for chunk in response.body_iterator])

    assert "event: end\n" not in body
    assert "event: error\n" in body


@pytest.mark.asyncio
async def test_v21_redis_admission_outage_fails_before_response(monkeypatch):
    patch_authority(monkeypatch)

    class Bridge:
        async def resolve_resume(self, **kwargs):
            raise StreamTransportUnavailable("down")

        async def aclose(self):
            return None

    monkeypatch.setattr(route, "RedisStreamBridge", Bridge)
    with pytest.raises(HTTPException) as exc:
        await route.chat_session_stream(
            "session-a",
            "run-a",
            principal=AuthPrincipal(
                user_id="user-a", display_name="User", tenant_id="tenant-a"
            ),
        )
    assert exc.value.status_code == 503
