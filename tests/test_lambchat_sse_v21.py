from contextlib import asynccontextmanager
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.auth import AuthPrincipal
from app.routes import lambchat_compat as route
from app.streaming.api import LiveSubscriptionClosed, live_redis_id_is_after
from app.streaming.events import STREAM_DESIGN_ID
from app.streaming.redis import (
    ResumeDecision,
    SseAuthorityConflictError,
    SseAuthorityLease,
    StreamAuthority,
    StreamCursor,
    StreamEntry,
    StreamEnvelope,
    StreamGap,
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


def open_entry(redis_id="1-0"):
    return entry(
        redis_id,
        "sev-open",
        "stream_open",
        {"design_id": STREAM_DESIGN_ID},
    )


class ClosedSubscription:
    def __init__(self):
        self.closed = False

    async def next(self, *, timeout_seconds=None):
        raise LiveSubscriptionClosed("test_stream_closed")

    async def aclose(self):
        self.closed = True


class BlockingSubscription:
    def __init__(self):
        self.started = asyncio.Event()
        self.closed = False

    async def next(self, *, timeout_seconds=None):
        self.started.set()
        await asyncio.Event().wait()

    async def aclose(self):
        self.closed = True


class FakeBridge:
    def __init__(self, rows, *, resume=None, resolve_error=None):
        self.rows = list(rows)
        self.resume = resume
        self.resolve_error = resolve_error
        self.calls = []

    async def resolve_resume(self, **kwargs):
        self.calls.append("resolve")
        if self.resolve_error:
            raise self.resolve_error
        if self.resume is not None:
            return self.resume
        last_event_id = kwargs["last_event_id"]
        if last_event_id:
            cursor = StreamCursor.parse(last_event_id, run_id="run-a")
            return ResumeDecision(cursor.redis_id, None)
        return ResumeDecision("0-0", None)

    async def retained_bounds(self, **kwargs):
        self.calls.append("bounds")
        return self.rows[0], self.rows[-1]

    async def replay_page(self, *, after_redis_id, through_redis_id, **kwargs):
        self.calls.append(f"replay:{after_redis_id}:{through_redis_id}")
        return tuple(
            row
            for row in self.rows
            if live_redis_id_is_after(row.cursor.redis_id, after_redis_id)
            and not live_redis_id_is_after(row.cursor.redis_id, through_redis_id)
        )

    def decode_live_publication(self, **kwargs):
        raise AssertionError("finite replay tests must not decode live publications")


class FakeHub:
    def __init__(self, bridge, *, on_subscribe=None, subscription=None):
        self.bridge = bridge
        self.on_subscribe = on_subscribe
        self.calls = []
        self.subscription = subscription or ClosedSubscription()

    async def subscribe(self, channel):
        self.calls.append("subscribe")
        self.bridge.calls.append("subscribe")
        if self.on_subscribe:
            self.on_subscribe()
        return self.subscription


def request_for(bridge, *, on_subscribe=None, subscription=None):
    runtime = SimpleNamespace(
        bridge=bridge,
        hub=FakeHub(
            bridge, on_subscribe=on_subscribe, subscription=subscription
        ),
    )
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(run_stream_runtime=runtime)))


def patch_authority(monkeypatch, *, run=None, close_result=True):
    async def get_run(conn, *, tenant_id, user_id, run_id):
        return run or {"id": run_id, "session_id": "session-a", "status": "running"}

    async def get_authority(conn, *, tenant_id, run_id):
        return authority()

    async def acquire(conn, **kwargs):
        return lease()

    async def close(conn, **kwargs):
        return close_result

    async def get_intent(conn, *, tenant_id, run_id):
        return None

    monkeypatch.setattr(route, "transaction", transaction)
    monkeypatch.setattr(route.repositories, "get_authorized_run", get_run)
    monkeypatch.setattr(route, "get_stream_authority", get_authority)
    monkeypatch.setattr(route, "acquire_sse_authority_lease", acquire)
    monkeypatch.setattr(route, "close_sse_authority_lease", close)
    monkeypatch.setattr(route, "get_terminal_intent", get_intent)


async def connect(bridge, *, last_event_id=None, on_subscribe=None):
    response = await route.chat_session_stream(
        "session-a",
        "run-a",
        request_for(bridge, on_subscribe=on_subscribe),
        last_event_id=last_event_id,
        principal=AuthPrincipal(
            user_id="user-a", display_name="User", tenant_id="tenant-a"
        ),
    )
    body = "".join([chunk async for chunk in response.body_iterator])
    return response, body


def terminal_rows():
    return (
        open_entry(),
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


async def connect_expect_conflict():
    with pytest.raises(HTTPException) as exc_info:
        await route.chat_session_stream(
            "session-a",
            "run-a",
            request_for(FakeBridge([])),
            principal=AuthPrincipal(
                user_id="user-a", display_name="User", tenant_id="tenant-a"
            ),
        )
    return exc_info.value


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("code", "retryable"),
    [
        ("sse_stream_not_confirmed", True),
        ("sse_authority_revoked", False),
    ],
)
async def test_v3_authority_conflict_has_stable_retry_classification(
    monkeypatch, code, retryable
):
    patch_authority(monkeypatch)

    async def conflicting_authority(conn, *, tenant_id, run_id):
        raise SseAuthorityConflictError(code)

    monkeypatch.setattr(route, "get_stream_authority", conflicting_authority)

    error = await connect_expect_conflict()

    assert error.status_code == 409
    assert error.detail == {"code": code, "retryable": retryable}
    assert error.headers == {
        "X-SSE-Error-Code": code,
        "X-SSE-Retryable": str(retryable).lower(),
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("run_status", "code", "retryable"),
    [
        ("running", "sse_stream_not_admitted", True),
        ("succeeded", "sse_run_already_terminal", False),
    ],
)
async def test_v3_missing_authority_distinguishes_startup_from_terminal_run(
    monkeypatch, run_status, code, retryable
):
    patch_authority(
        monkeypatch,
        run={"id": "run-a", "session_id": "session-a", "status": run_status},
    )

    async def missing_authority(conn, *, tenant_id, run_id):
        return None

    monkeypatch.setattr(route, "get_stream_authority", missing_authority)

    error = await connect_expect_conflict()

    assert error.status_code == 409
    assert error.detail == {"code": code, "retryable": retryable}
    assert error.headers == {
        "X-SSE-Error-Code": code,
        "X-SSE-Retryable": str(retryable).lower(),
    }


@pytest.mark.asyncio
async def test_v3_replay_uses_native_cursor_and_schema_event(monkeypatch):
    patch_authority(monkeypatch)

    async def forbidden(*args, **kwargs):
        raise AssertionError("PG run_events must not drive live SSE")

    monkeypatch.setattr(route.repositories, "list_run_events", forbidden)
    bridge = FakeBridge(terminal_rows())
    response, body = await connect(bridge)

    assert response.headers["cache-control"] == "no-cache, no-transform"
    assert response.headers["x-accel-buffering"] == "no"
    assert "id: run-a:1:2-0" in body
    assert '"schema": "ai-platform.public-run-stream-event.v3"' in body
    assert '"payload": {"delta": "hello"}' in body
    assert body.index("id: run-a:1:3-0") < body.index("id: run-a:1:4-0")


@pytest.mark.asyncio
async def test_v3_subscribes_before_capturing_replay_tail(monkeypatch):
    patch_authority(monkeypatch)
    bridge = FakeBridge([open_entry()])

    def append_after_subscribe():
        bridge.rows.extend(terminal_rows()[1:])

    _, body = await connect(bridge, on_subscribe=append_after_subscribe)

    assert bridge.calls[:3] == ["subscribe", "resolve", "bounds"]
    assert '"delta": "hello"' in body
    assert "event: end\n" in body


@pytest.mark.asyncio
async def test_v3_resume_rebuilds_split_identifier_projection_state(monkeypatch):
    patch_authority(
        monkeypatch,
        run={
            "id": "run-a",
            "session_id": "session-a",
            "status": "running",
            "agent_id": "qa-word-review",
            "skill_id": "general-chat",
        },
    )
    rows = (
        open_entry(),
        entry("2-0", "safe", "assistant_text_delta", {"delta": "已开始处理，"}),
        entry("3-0", "prefix", "assistant_text_delta", {"delta": "general-"}),
        entry(
            "4-0",
            "progress",
            "semantic_stage",
            {"event": "run_event", "data": {"content": "处理中"}},
        ),
        entry("5-0", "suffix", "assistant_text_delta", {"delta": "chat 已完成。"}),
        entry(
            "6-0",
            "terminal-after-resume",
            "terminal",
            {
                "event_id": "terminal-after-resume",
                "hydrate_required": True,
                "status": "succeeded",
            },
        ),
        entry(
            "7-0",
            "end-after-resume",
            "end",
            {"terminal_event_id": "terminal-after-resume"},
        ),
    )
    bridge = FakeBridge(rows)
    _, body = await connect(bridge, last_event_id="run-a:1:4-0")

    assert "general-chat" not in body
    assert "qa-word-review" not in body
    assert "已开始处理" not in body
    assert '"delta": "general-agent 已完成。"' in body


@pytest.mark.asyncio
async def test_v3_terminal_relies_on_final_hydrate_instead_of_cursor_reuse(monkeypatch):
    patch_authority(
        monkeypatch,
        run={
            "id": "run-a",
            "session_id": "session-a",
            "status": "running",
            "skill_id": "general-chat",
        },
    )
    rows = (
        open_entry(),
        entry("2-0", "safe", "assistant_text_delta", {"delta": "已完成，"}),
        entry("3-0", "pending", "assistant_text_delta", {"delta": "general-chat"}),
        entry(
            "4-0",
            "terminal-after-pending",
            "terminal",
            {
                "event_id": "terminal-after-pending",
                "hydrate_required": True,
                "status": "succeeded",
            },
        ),
        entry(
            "5-0",
            "end-after-pending",
            "end",
            {"terminal_event_id": "terminal-after-pending"},
        ),
    )
    _, body = await connect(FakeBridge(rows))

    assert "general-chat" not in body
    assert '"delta": "已完成，"' in body
    assert body.count("id: run-a:1:3-0") == 0
    assert '"hydrate_required": true' in body


@pytest.mark.asyncio
async def test_v3_maps_committed_execution_projection(monkeypatch):
    patch_authority(monkeypatch)
    projection = committed_public_stream_event(
        {
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
    )
    assert projection is not None
    envelope_type, payload = projection
    rows = (open_entry(), entry("2-0", "evt-execution-1", envelope_type, payload))
    bridge = FakeBridge(rows)
    _, body = await connect(bridge)

    assert "event: semantic_progress\n" in body
    assert "id: run-a:1:2-0\n" in body
    assert '"event_id": "evt-execution-1"' in body
    assert '"sequence": 11' in body


@pytest.mark.asyncio
async def test_v3_trim_gap_is_idless_and_requests_durable_hydration(monkeypatch):
    patch_authority(monkeypatch)
    bridge = FakeBridge(
        [open_entry()],
        resume=ResumeDecision(
            None, StreamGap("retained_history_unavailable", "run-a:1:1-0", 1, 1)
        ),
    )
    _, body = await connect(bridge, last_event_id="run-a:1:1-0")

    assert body.startswith("event: gap\n")
    assert "id:" not in body
    assert '"recovery": "reload_durable_state"' in body


@pytest.mark.asyncio
async def test_v3_end_before_terminal_closes_without_synthetic_error(monkeypatch):
    patch_authority(monkeypatch)
    rows = (
        open_entry(),
        entry("2-0", "sev-end", "end", {"terminal_event_id": "sev-terminal"}),
    )
    _, body = await connect(FakeBridge(rows))

    assert "event: end\n" not in body
    assert "event: error\n" not in body


@pytest.mark.asyncio
async def test_v3_redis_admission_outage_fails_before_response(monkeypatch):
    patch_authority(monkeypatch)
    bridge = FakeBridge(
        [open_entry()],
        resolve_error=StreamTransportUnavailable("down"),
    )
    with pytest.raises(HTTPException) as exc:
        await route.chat_session_stream(
            "session-a",
            "run-a",
            request_for(bridge),
            principal=AuthPrincipal(
                user_id="user-a", display_name="User", tenant_id="tenant-a"
            ),
        )
    assert exc.value.status_code == 503


@pytest.mark.asyncio
async def test_v3_admitted_terminal_body_records_one_safe_exit_with_lease_result(
    monkeypatch, caplog
):
    patch_authority(monkeypatch, close_result=False)
    caplog.set_level(logging.INFO, logger=route.logger.name)

    response, body = await connect(FakeBridge(terminal_rows()))

    assert "event: end\n" in body
    records = [record for record in caplog.records if record.msg == "sse_stream_exit"]
    assert len(records) == 1
    assert records[0].reason == "terminal_completed"
    assert records[0].lease_released is False
    assert records[0].run_id_prefix == "run-a"
    assert records[0].attempt_id_prefix == "attempt-a"
    assert response.body_iterator is not None


@pytest.mark.asyncio
async def test_v3_admitted_body_cancellation_closes_subscription_and_records_once(
    monkeypatch, caplog
):
    patch_authority(monkeypatch)
    caplog.set_level(logging.INFO, logger=route.logger.name)
    subscription = BlockingSubscription()
    response = await route.chat_session_stream(
        "session-a",
        "run-a",
        request_for(FakeBridge([open_entry()]), subscription=subscription),
        principal=AuthPrincipal(
            user_id="user-a", display_name="User", tenant_id="tenant-a"
        ),
    )
    iterator = response.body_iterator
    assert "event: stream_open" in await iterator.__anext__()
    pending = asyncio.create_task(iterator.__anext__())
    await subscription.started.wait()
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending

    records = [record for record in caplog.records if record.msg == "sse_stream_exit"]
    assert len(records) == 1
    assert records[0].reason == "client_disconnected"
    assert subscription.closed is True
