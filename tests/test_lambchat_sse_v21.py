import app.runs.infrastructure.creation_postgres as _owner_runs_infrastructure_creation_postgres
from contextlib import asynccontextmanager
import asyncio
import logging
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.auth import AuthPrincipal
from app.routes import lambchat_compat as route
from app.streaming.api import (
    StreamCursor,
    V4StreamEntry,
    build_v4_control,
    live_redis_id_is_after,
)
from app.streaming.domain.protocol_v4 import STREAM_DESIGN_ID as STREAM_DESIGN_ID_V4
from app.streaming.redis import (
    SseAuthorityConflictError,
    SseAuthorityLease,
    StreamAuthority,
    StreamTransportUnavailable,
)


@asynccontextmanager
async def transaction():
    yield object()


def authority(*, state="confirmed"):
    return StreamAuthority(
        "tenant-a",
        "run-a",
        "attempt-a",
        "scope-a",
        1,
        state,
        "sev-open",
        json.dumps(open_entry().envelope),
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


class FrameLeaseGate:
    def __init__(self, *, valid=True, allowed_calls=None):
        self.lease_id = "lease-a"
        self.tenant_id = "tenant-a"
        self.run_id = "run-a"
        self.api_instance_id = "api-a"
        self.connection_id = "connection-a"
        self.authorization_epoch = 1
        self.lease_not_after = datetime.now(timezone.utc) + timedelta(seconds=15)
        self.valid = valid
        self.allowed_calls = allowed_calls

    def allows_frame(self, *, now):
        if self.allowed_calls is not None:
            if self.allowed_calls == 0:
                return False
            self.allowed_calls -= 1
        return self.valid


def entry(redis_id, event_id, event_type, payload):
    if event_type.startswith("stream."):
        envelope = build_v4_control(
            event_id=event_id,
            tenant_scope="scope-a",
            run_id="run-a",
            attempt_id="attempt-a",
            stream_incarnation=1,
            event_type=event_type,
            payload=payload,
            source={"kind": "stream_authority", "authority_id": event_id},
            emitted_at="2026-08-09T00:00:00Z",
        )
    else:
        sequence = int(redis_id.partition("-")[0])
        message_types = (
            "message.",
            "thinking.",
            "model.",
            "tool.",
            "subagent.",
        )
        envelope = {
            "schema": "ai-platform.stream-event.v4",
            "event_id": event_id,
            "tenant_scope": "scope-a",
            "run_id": "run-a",
            "attempt_id": "attempt-a",
            "message_id": "msg_run_a" if event_type.startswith(message_types) else None,
            "seq": sequence,
            "event_type": event_type,
            "stream_incarnation": 1,
            "replayable": True,
            "trace_ref": None,
            "causation_event_id": None,
            "emitted_at": "2026-08-09T00:00:00Z",
            "projection_version": "public-stream-v4",
            "payload": payload,
            "source": {
                "kind": "run_event",
                "run_event_id": event_id,
                "sequence": sequence,
            },
        }
    return V4StreamEntry(StreamCursor("run-a", 1, redis_id), envelope)


def open_entry(redis_id="1-0"):
    return entry(
        redis_id,
        "sev-open",
        "stream.open",
        {"design_id": STREAM_DESIGN_ID_V4},
    )


class FailingReader:
    async def read_stream(self, **kwargs):
        raise RuntimeError("private read failure")


class ClosedReader:
    async def read_stream(self, **kwargs):
        raise StreamTransportUnavailable("test_stream_closed")


class BlockingReader:
    def __init__(self):
        self.started = asyncio.Event()
        self.cancelled = False

    async def read_stream(self, **kwargs):
        self.started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise


class ExpiringReader:
    def __init__(self, lease_gate, *, entries=()):
        self.lease_gate = lease_gate
        self.entries = tuple(entries)

    async def read_stream(self, **kwargs):
        self.lease_gate.valid = False
        return self.entries


class SequencedReader:
    def __init__(self, batches):
        self.batches = list(batches)

    async def read_stream(self, **kwargs):
        return self.batches.pop(0) if self.batches else ()


class FakeBridge:
    def __init__(
        self,
        rows,
        *,
        on_read=None,
    ):
        self.rows = list(rows)
        self.on_read = on_read
        self._read_hook_called = False
        self.reader = None
        self.calls = []

    async def read_live_page(self, **kwargs):
        self.calls.append(
            f"read:{kwargs['after_redis_id']}:{kwargs['count']}:{kwargs['block_ms']}"
        )
        if self.on_read is not None and not self._read_hook_called:
            self._read_hook_called = True
            self.on_read()
        if self.reader is not None:
            received = await self.reader.read_stream(**kwargs)
            self.rows.extend(e for e in received if e not in self.rows)
            return received
        after_redis_id = kwargs["after_redis_id"]
        return tuple(
            row
            for row in self.rows
            if live_redis_id_is_after(row.cursor.redis_id, after_redis_id)
        )




_active_bridge = None

def request_for(bridge, *, reader=None):
    global _active_bridge
    _active_bridge = bridge
    bridge.reader = ClosedReader() if reader is None and bridge.on_read is None else reader
    runtime = SimpleNamespace(bridge=bridge)
    return SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(run_stream_runtime=runtime))
    )


def request_without_runtime():
    return SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(run_stream_runtime=None))
    )


def patch_authority(monkeypatch, *, run=None, close_result=True, lease_value=None):
    async def get_run(conn, *, tenant_id, user_id, run_id):
        return run or {"id": run_id, "session_id": "session-a", "status": "running"}

    async def get_authority(conn, *, tenant_id, run_id):
        return authority()

    async def admit(conn, *, on_authority_observed=None, **kwargs):
        current = authority()
        if on_authority_observed is not None:
            on_authority_observed(current)
        return current, lease_value or lease()

    async def acquire(conn, **kwargs):
        return lease_value or lease()

    async def close(conn, **kwargs):
        return close_result

    async def bounds(conn, **kwargs):
        return {"run-a": max((e.envelope.get("seq") or 0 for e in _active_bridge.rows), default=0)}

    async def pages(conn, *, after_sequence, through_sequence, limit, **kwargs):
        result = []
        for e in _active_bridge.rows:
            env = e.envelope
            seq = env.get("seq")
            if seq is None or not after_sequence < seq <= through_sequence:
                continue
            payload = dict(env["payload"])
            event_id = "evt4_" + env["event_id"]
            if "terminal_event_id" in payload:
                payload["terminal_event_id"] = event_id
            payload["__stream_v4"] = {
                "version": 1, "attempt_id": env["attempt_id"], "authorization_epoch": 1,
                "stream_incarnation": env["stream_incarnation"], "message_id": env["message_id"],
            }
            result.append({"id": event_id, "tenant_id": "tenant-a", "run_id": "run-a",
                "sequence": seq, "event_type": env["event_type"], "visible_to_user": True,
                "created_at": env["emitted_at"], "payload_json": payload})
        return result[:limit]

    monkeypatch.setattr(route.streaming_run_events, "list_run_event_sequence_bounds", bounds)
    monkeypatch.setattr(route.streaming_run_events, "list_run_events", pages)
    monkeypatch.setattr(route, "transaction", transaction)
    monkeypatch.setattr(_owner_runs_infrastructure_creation_postgres, 'get_authorized_run', get_run)
    monkeypatch.setattr(route, "acquire_sse_admission_lease", admit)
    monkeypatch.setattr(route, "acquire_sse_authority_lease", acquire)
    monkeypatch.setattr(route, "close_sse_authority_lease", close)


def deny_lease_renewal(monkeypatch):
    async def deny(conn, **kwargs):
        raise SseAuthorityConflictError("sse_authority_revoked")

    monkeypatch.setattr(route, "acquire_sse_authority_lease", deny)


async def open_response(
    bridge, *, last_event_id=None, reader=None
):
    return await route.chat_session_stream(
        "session-a",
        "run-a",
        request_for(bridge, reader=reader),
        last_event_id=last_event_id,
        principal=AuthPrincipal(
            user_id="user-a", display_name="User", tenant_id="tenant-a"
        ),
    )


async def connect(bridge, *, last_event_id=None, reader=None):
    response = await open_response(
        bridge,
        last_event_id=last_event_id,
        reader=reader,
    )
    body = "".join([chunk async for chunk in response.body_iterator])
    return response, body


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal_status", ["succeeded", "failed", "cancelled"])
async def test_idle_stream_waits_for_terminal_event_after_authority_observes_terminal_run(monkeypatch, terminal_status):
    run = {"id": "run-a", "session_id": "session-a", "status": "running"}
    gate = FrameLeaseGate()
    patch_authority(monkeypatch, run=run, lease_value=gate)
    acquired = []
    closed = []

    async def acquire(conn, **kwargs):
        acquired.append(kwargs)
        return gate if len(acquired) == 1 else lease()

    async def admit(conn, *, on_authority_observed=None, **kwargs):
        current = authority()
        if on_authority_observed is not None:
            on_authority_observed(current)
        acquired.append(kwargs)
        return current, gate

    async def close(conn, **kwargs):
        closed.append(kwargs)
        return True

    def complete_run():
        run["status"] = terminal_status
        gate.valid = False

    monkeypatch.setattr(route, "acquire_sse_admission_lease", admit)
    monkeypatch.setattr(route, "acquire_sse_authority_lease", acquire)
    monkeypatch.setattr(route, "close_sse_authority_lease", close)
    bridge = FakeBridge([open_entry()], on_read=complete_run)
    response = await open_response(bridge, reader=SequencedReader([()]))
    iterator = response.body_iterator
    assert "stream.open" in await iterator.__anext__()
    assert ": heartbeat" in await iterator.__anext__()
    terminal = entry("2-0", "evt4_done", f"run.{terminal_status}", {
        "terminal_event_id": "evt4_done", "hydrate_required": True,
        **({"reason_code": "user_cancelled"} if terminal_status == "cancelled" else {}),
        **({"projection_version": "ai-platform.chat-public-projection.v1", "code": "failed", "default_message": "Run failed", "detail": None} if terminal_status == "failed" else {}),
    })
    bridge.rows.append(terminal)
    body = "".join([frame async for frame in iterator])
    assert f"event: run.{terminal_status}" in body
    assert "event: stream.end" in body
    assert len(acquired) == 2
    assert closed == [{"lease_id": "lease-a", "reason": "terminal_completed"}]


def terminal_rows():
    return (
        open_entry(),
        entry("2-0", "sev-delta", "message.delta", {"delta": "hello "}),
        entry(
            "3-0",
            "sev-terminal",
            "run.succeeded",
            {
                "terminal_event_id": "sev-terminal",
                "hydrate_required": True,
            },
        ),
        entry(
            "4-0",
            "sev-end",
            "stream.end",
            {"terminal_event_id": "sev-terminal"},
        ),
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
async def test_v4_authority_conflict_has_stable_retry_classification(
    monkeypatch, code, retryable
):
    patch_authority(monkeypatch)

    async def conflicting_authority(conn, **kwargs):
        raise SseAuthorityConflictError(code)

    monkeypatch.setattr(route, "acquire_sse_admission_lease", conflicting_authority)

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
async def test_v4_missing_authority_distinguishes_startup_from_terminal_run(
    monkeypatch, run_status, code, retryable
):
    patch_authority(
        monkeypatch,
        run={"id": "run-a", "session_id": "session-a", "status": run_status},
    )
    monkeypatch.setattr(route, "_SSE_STARTUP_ADMISSION_WAIT_SECONDS", 0.001)

    async def missing_authority(conn, *, on_authority_observed=None, **kwargs):
        if on_authority_observed is not None:
            on_authority_observed(None)
        return None, None

    monkeypatch.setattr(route, "acquire_sse_admission_lease", missing_authority)

    error = await connect_expect_conflict()

    assert error.status_code == 409
    assert error.detail == {"code": code, "retryable": retryable}
    assert error.headers == {
        "X-SSE-Error-Code": code,
        "X-SSE-Retryable": str(retryable).lower(),
        **({"Retry-After": "1"} if run_status == "running" else {}),
    }


@pytest.mark.asyncio
async def test_v4_pending_authority_waits_before_acquiring_lease(monkeypatch):
    patch_authority(monkeypatch)
    monkeypatch.setattr(route, "_SSE_STARTUP_ADMISSION_WAIT_SECONDS", 1)
    monkeypatch.setattr(route, "_SSE_STARTUP_ADMISSION_POLL_SECONDS", 0)
    pending = authority(state="admission_pending")
    states = [pending, authority()]
    observed_states = []
    acquire_calls = []

    async def admit(conn, *, on_authority_observed=None, **kwargs):
        current = states.pop(0)
        observed_states.append(current.state)
        if on_authority_observed is not None:
            on_authority_observed(current)
        if current.state == "admission_pending":
            return current, None
        acquire_calls.append(kwargs)
        assert observed_states[-1] == "confirmed"
        return current, lease()

    monkeypatch.setattr(route, "acquire_sse_admission_lease", admit)

    _, body = await connect(FakeBridge(terminal_rows()))

    assert "event: stream.open\n" in body
    assert observed_states == ["admission_pending", "confirmed"]
    assert len(acquire_calls) == 1


@pytest.mark.asyncio
async def test_v4_pending_admission_releases_transaction_before_poll_sleep(monkeypatch):
    patch_authority(monkeypatch)
    monkeypatch.setattr(route, "_SSE_STARTUP_ADMISSION_WAIT_SECONDS", 1)
    monkeypatch.setattr(route, "_SSE_STARTUP_ADMISSION_POLL_SECONDS", 0.001)
    transaction_active = False
    states = [authority(state="admission_pending"), authority()]
    real_sleep = asyncio.sleep

    @asynccontextmanager
    async def tracked_transaction():
        nonlocal transaction_active
        transaction_active = True
        try:
            yield object()
        finally:
            transaction_active = False

    async def admit(conn, *, on_authority_observed=None, **kwargs):
        assert transaction_active
        current = states.pop(0)
        if on_authority_observed is not None:
            on_authority_observed(current)
        return current, None if current.state == "admission_pending" else lease()

    async def sleep_after_transaction(delay):
        assert transaction_active is False
        await real_sleep(0)

    monkeypatch.setattr(route, "transaction", tracked_transaction)
    monkeypatch.setattr(route, "acquire_sse_admission_lease", admit)
    monkeypatch.setattr(route.asyncio, "sleep", sleep_after_transaction)

    run, admitted_authority, admitted_lease = await route._await_sse_admission(
        session_id="session-a",
        run_id="run-a",
        principal=AuthPrincipal(
            user_id="user-a", display_name="User", tenant_id="tenant-a"
        ),
        api_instance_id="api-test",
        connection_id="connection-test",
    )

    assert run["session_id"] == "session-a"
    assert admitted_authority.state == "confirmed"
    assert admitted_lease.lease_id == "lease-a"
    assert transaction_active is False


@pytest.mark.asyncio
async def test_v4_pending_authority_timeout_is_retryable_with_retry_after(monkeypatch):
    patch_authority(monkeypatch)
    monkeypatch.setattr(route, "_SSE_STARTUP_ADMISSION_WAIT_SECONDS", 0.001)

    async def pending_authority(conn, *, on_authority_observed=None, **kwargs):
        current = authority(state="admission_pending")
        if on_authority_observed is not None:
            on_authority_observed(current)
        return current, None

    monkeypatch.setattr(route, "acquire_sse_admission_lease", pending_authority)

    with pytest.raises(HTTPException) as exc_info:
        await route.chat_session_stream(
            "session-a",
            "run-a",
            request_for(FakeBridge([])),
            principal=AuthPrincipal(
                user_id="user-a", display_name="User", tenant_id="tenant-a"
            ),
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == {
        "code": "sse_stream_not_confirmed",
        "retryable": True,
    }
    assert exc_info.value.headers["Retry-After"] == "1"


@pytest.mark.asyncio
async def test_v4_terminal_run_with_pending_authority_fails_closed(monkeypatch):
    patch_authority(
        monkeypatch,
        run={"id": "run-a", "session_id": "session-a", "status": "succeeded"},
    )
    monkeypatch.setattr(route, "_SSE_STARTUP_ADMISSION_WAIT_SECONDS", 1)

    async def pending_authority(conn, *, on_authority_observed=None, **kwargs):
        current = authority(state="admission_pending")
        if on_authority_observed is not None:
            on_authority_observed(current)
        return current, None

    monkeypatch.setattr(route, "acquire_sse_admission_lease", pending_authority)

    with pytest.raises(HTTPException) as exc_info:
        await route.chat_session_stream(
            "session-a",
            "run-a",
            request_for(FakeBridge([])),
            principal=AuthPrincipal(
                user_id="user-a", display_name="User", tenant_id="tenant-a"
            ),
        )

    assert exc_info.value.detail == {
        "code": "sse_run_already_terminal",
        "retryable": False,
    }


@pytest.mark.asyncio
async def test_v4_slow_lease_admission_respects_startup_deadline(monkeypatch):
    patch_authority(monkeypatch)
    monkeypatch.setattr(route, "_SSE_STARTUP_ADMISSION_WAIT_SECONDS", 0.001)
    cancelled = False
    completed = False

    async def slow_acquire(conn, *, on_authority_observed=None, **kwargs):
        nonlocal cancelled, completed
        current = authority()
        if on_authority_observed is not None:
            on_authority_observed(current)
        try:
            await asyncio.sleep(0.05)
        except asyncio.CancelledError:
            cancelled = True
            raise
        completed = True
        return current, lease()

    monkeypatch.setattr(route, "acquire_sse_admission_lease", slow_acquire)

    with pytest.raises(HTTPException) as exc_info:
        await route.chat_session_stream(
            "session-a",
            "run-a",
            request_for(FakeBridge([])),
            principal=AuthPrincipal(
                user_id="user-a", display_name="User", tenant_id="tenant-a"
            ),
        )

    assert exc_info.value.detail == {
        "code": "sse_stream_not_confirmed",
        "retryable": True,
    }
    assert exc_info.value.headers["Retry-After"] == "1"
    assert cancelled is True
    assert completed is False


@pytest.mark.asyncio
async def test_v4_locked_authority_read_timeout_keeps_confirmation_classification(monkeypatch):
    patch_authority(monkeypatch)
    monkeypatch.setattr(route, "_SSE_STARTUP_ADMISSION_WAIT_SECONDS", 0.001)

    async def locked_read_waits_before_observing_authority(conn, **kwargs):
        await asyncio.sleep(0.05)

    monkeypatch.setattr(
        route,
        "acquire_sse_admission_lease",
        locked_read_waits_before_observing_authority,
    )

    error = await connect_expect_conflict()

    assert error.detail == {
        "code": "sse_stream_not_confirmed",
        "retryable": True,
    }
    assert error.headers["Retry-After"] == "1"


@pytest.mark.asyncio
async def test_v4_missing_authority_terminal_run_does_not_wait(monkeypatch):
    patch_authority(
        monkeypatch,
        run={"id": "run-a", "session_id": "session-a", "status": "succeeded"},
    )
    monkeypatch.setattr(route, "_SSE_STARTUP_ADMISSION_WAIT_SECONDS", 1)

    async def missing_authority(conn, *, on_authority_observed=None, **kwargs):
        if on_authority_observed is not None:
            on_authority_observed(None)
        return None, None

    monkeypatch.setattr(route, "acquire_sse_admission_lease", missing_authority)

    with pytest.raises(HTTPException) as exc_info:
        await route.chat_session_stream(
            "session-a",
            "run-a",
            request_for(FakeBridge([])),
            principal=AuthPrincipal(
                user_id="user-a", display_name="User", tenant_id="tenant-a"
            ),
        )

    assert exc_info.value.detail == {
        "code": "sse_run_already_terminal",
        "retryable": False,
    }


@pytest.mark.asyncio
async def test_v4_missing_run_fails_closed_before_admission_wait(monkeypatch):
    patch_authority(monkeypatch)

    async def missing_run(conn, *, tenant_id, user_id, run_id):
        return None

    monkeypatch.setattr(_owner_runs_infrastructure_creation_postgres, 'get_authorized_run', missing_run)

    with pytest.raises(HTTPException) as exc_info:
        await route.chat_session_stream(
            "session-a",
            "run-a",
            request_for(FakeBridge([])),
            principal=AuthPrincipal(
                user_id="user-a", display_name="User", tenant_id="tenant-a"
            ),
        )

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "run_not_found"












@pytest.mark.asyncio
async def test_v4_replay_frame_checks_lease_immediately_before_write(monkeypatch):
    lease_gate = FrameLeaseGate(allowed_calls=0)
    patch_authority(monkeypatch, lease_value=lease_gate)
    response = await open_response(FakeBridge([open_entry()]))
    deny_lease_renewal(monkeypatch)

    body = "".join([chunk async for chunk in response.body_iterator])

    assert body == ""


@pytest.mark.asyncio
async def test_v4_replay_frame_rejects_lease_expired_during_renewal(monkeypatch):
    lease_gate = FrameLeaseGate(allowed_calls=0)
    patch_authority(monkeypatch, lease_value=lease_gate)
    response = await open_response(FakeBridge([open_entry()]))

    async def renew_with_expired_lease(conn, **kwargs):
        return FrameLeaseGate(valid=False)

    monkeypatch.setattr(
        route,
        "acquire_sse_authority_lease",
        renew_with_expired_lease,
    )

    body = "".join([chunk async for chunk in response.body_iterator])

    assert body == ""




@pytest.mark.asyncio
async def test_v4_heartbeat_rechecks_lease_after_blocking_read(monkeypatch):
    lease_gate = FrameLeaseGate()
    reader = ExpiringReader(lease_gate)
    patch_authority(monkeypatch, lease_value=lease_gate)
    response = await open_response(
        FakeBridge([open_entry()]),
        reader=reader,
    )
    deny_lease_renewal(monkeypatch)
    iterator = response.body_iterator

    assert "event: stream.open" in await iterator.__anext__()
    with pytest.raises(StopAsyncIteration):
        await iterator.__anext__()


@pytest.mark.asyncio
async def test_v4_empty_blocking_read_emits_heartbeat_before_next_batch(monkeypatch):
    patch_authority(monkeypatch)
    reader = SequencedReader([(), terminal_rows()[1:]])
    _, body = await connect(
        FakeBridge([open_entry()]),
        reader=reader,
    )

    assert ": heartbeat\n\n" in body
    assert "event: stream.end\n" in body


@pytest.mark.asyncio
async def test_v4_live_frame_rechecks_lease_after_blocking_read(monkeypatch):
    lease_gate = FrameLeaseGate()
    live_entry = entry("2-0", "sev-delta", "message.delta", {"delta": "late"})
    reader = ExpiringReader(lease_gate, entries=(live_entry,))
    patch_authority(monkeypatch, lease_value=lease_gate)
    response = await open_response(
        FakeBridge([open_entry()]),
        reader=reader,
    )
    deny_lease_renewal(monkeypatch)
    iterator = response.body_iterator

    assert "event: stream.open" in await iterator.__anext__()
    with pytest.raises(StopAsyncIteration):
        await iterator.__anext__()







@pytest.mark.asyncio
async def test_v4_admitted_terminal_body_records_one_safe_exit_with_lease_result(
    monkeypatch, caplog
):
    patch_authority(monkeypatch, close_result=False)
    caplog.set_level(logging.INFO, logger=route.logger.name)

    response, body = await connect(FakeBridge(terminal_rows()))

    assert "event: stream.end\n" in body
    records = [record for record in caplog.records if record.msg == "sse_stream_exit"]
    assert len(records) == 1
    assert records[0].reason == "terminal_completed"
    assert records[0].lease_released is False
    assert records[0].run_id_prefix == "run-a"
    assert records[0].attempt_id_prefix == "attempt-a"
    assert response.body_iterator is not None


@pytest.mark.asyncio
async def test_v4_admitted_body_cancellation_cancels_blocking_read_and_records_once(
    monkeypatch, caplog
):
    patch_authority(monkeypatch)
    caplog.set_level(logging.INFO, logger=route.logger.name)
    reader = BlockingReader()
    response = await route.chat_session_stream(
        "session-a",
        "run-a",
        request_for(FakeBridge([open_entry()]), reader=reader),
        principal=AuthPrincipal(
            user_id="user-a", display_name="User", tenant_id="tenant-a"
        ),
    )
    iterator = response.body_iterator
    assert "event: stream.open" in await iterator.__anext__()
    pending = asyncio.create_task(iterator.__anext__())
    await reader.started.wait()
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending

    records = [record for record in caplog.records if record.msg == "sse_stream_exit"]
    assert len(records) == 1
    assert records[0].reason == "client_disconnected"
    assert reader.cancelled is True


@pytest.mark.asyncio
async def test_v4_missing_runtime_after_admission_records_setup_exit_and_releases_lease(
    monkeypatch, caplog
):
    patch_authority(monkeypatch)
    caplog.set_level(logging.INFO, logger=route.logger.name)
    with pytest.raises(HTTPException) as exc:
        await route.chat_session_stream(
            "session-a",
            "run-a",
            request_without_runtime(),
            principal=AuthPrincipal(
                user_id="user-a", display_name="User", tenant_id="tenant-a"
            ),
        )
    assert exc.value.status_code == 503
    records = [record for record in caplog.records if record.msg == "sse_stream_exit"]
    assert len(records) == 1
    assert records[0].reason == "stream_setup_failure"
    assert records[0].lease_released is True
    assert "private" not in caplog.text
    assert "tenant-a" not in caplog.text




@pytest.mark.asyncio
async def test_v4_generic_generator_failure_records_transport_exit(monkeypatch, caplog):
    patch_authority(monkeypatch)
    caplog.set_level(logging.INFO, logger=route.logger.name)
    reader = FailingReader()
    response = await route.chat_session_stream(
        "session-a",
        "run-a",
        request_for(FakeBridge([open_entry()]), reader=reader),
        principal=AuthPrincipal(
            user_id="user-a", display_name="User", tenant_id="tenant-a"
        ),
    )
    with pytest.raises(RuntimeError, match="private read failure"):
        "".join([chunk async for chunk in response.body_iterator])
    records = [record for record in caplog.records if record.msg == "sse_stream_exit"]
    assert len(records) == 1
    assert records[0].reason == "transport_failure"
    assert records[0].lease_released is True
    assert "private read failure" not in caplog.text
