from contextlib import asynccontextmanager
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.auth import AuthPrincipal
from app.routes import lambchat_compat as route
from app.streaming.api import (
    ResumeDecision,
    StreamCursor,
    StreamGap,
    V4StreamEntry,
    build_v4_control,
    live_redis_id_is_after,
)
from app.streaming.events import STREAM_DESIGN_ID_V4
from app.streaming.redis import (
    SseAuthorityConflictError,
    SseAuthorityLease,
    StreamAuthority,
    StreamContractError,
    StreamTransportUnavailable,
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
        resume=None,
        resolve_error=None,
        replay_error=None,
        on_read=None,
    ):
        self.rows = list(rows)
        self.resume = resume
        self.resolve_error = resolve_error
        self.replay_error = replay_error
        self.on_read = on_read
        self._read_hook_called = False
        self.reader = None
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

    async def build_gap(self, **kwargs):
        self.calls.append("gap")
        requested = kwargs["requested_event_id"]
        if requested is None:
            requested_redis_id = None
        else:
            try:
                requested_redis_id = StreamCursor.parse(
                    requested, run_id="run-a"
                ).redis_id
            except StreamContractError:
                requested_redis_id = requested
        last = self.rows[-1].cursor.redis_id if self.rows else None
        envelope = build_v4_control(
            event_id=kwargs["event_id"],
            tenant_scope=kwargs["tenant_scope_value"],
            run_id=kwargs["run_id"],
            attempt_id=kwargs["attempt_id"],
            stream_incarnation=kwargs["current_stream_incarnation"],
            event_type="stream.gap",
            payload={
                "reason": kwargs["reason"],
                "recovery": "reload_durable_state",
                "requested_event_id": requested_redis_id,
                "requested_stream_incarnation": kwargs[
                    "requested_stream_incarnation"
                ],
                "current_stream_incarnation": kwargs[
                    "current_stream_incarnation"
                ],
                "earliest_available_event_id": (
                    self.rows[0].cursor.redis_id if self.rows else None
                ),
                "latest_available_event_id": last,
            },
            source={"kind": "stream_authority", "authority_id": kwargs["event_id"]},
            emitted_at="2026-08-09T00:00:00Z",
        )
        return envelope, StreamCursor("run-a", 1, last or "0-0").event_id

    async def replay_page(self, *, after_redis_id, through_redis_id, **kwargs):
        self.calls.append(f"replay:{after_redis_id}:{through_redis_id}")
        if self.replay_error is not None:
            raise self.replay_error
        return tuple(
            row
            for row in self.rows
            if live_redis_id_is_after(row.cursor.redis_id, after_redis_id)
            and not live_redis_id_is_after(row.cursor.redis_id, through_redis_id)
        )

    async def read_stream(self, **kwargs):
        self.calls.append(
            f"read:{kwargs['after_redis_id']}:{kwargs['count']}:{kwargs['block_ms']}"
        )
        if self.on_read is not None and not self._read_hook_called:
            self._read_hook_called = True
            self.on_read()
        if self.reader is not None:
            return await self.reader.read_stream(**kwargs)
        after_redis_id = kwargs["after_redis_id"]
        return tuple(
            row
            for row in self.rows
            if live_redis_id_is_after(row.cursor.redis_id, after_redis_id)
        )




def request_for(bridge, *, reader=None):
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

    async def acquire(conn, **kwargs):
        return lease_value or lease()

    async def close(conn, **kwargs):
        return close_result

    monkeypatch.setattr(route, "transaction", transaction)
    monkeypatch.setattr(route.repositories, "get_authorized_run", get_run)
    monkeypatch.setattr(route, "get_stream_authority", get_authority)
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
async def test_idle_stream_closes_when_authority_refresh_observes_terminal_run(monkeypatch, terminal_status):
    run = {"id": "run-a", "session_id": "session-a", "status": "running"}
    gate = FrameLeaseGate()
    patch_authority(monkeypatch, run=run, lease_value=gate)
    acquired = []
    closed = []

    async def acquire(conn, **kwargs):
        acquired.append(kwargs)
        return gate if len(acquired) == 1 else lease()

    async def close(conn, **kwargs):
        closed.append(kwargs)
        return True

    def complete_run():
        run["status"] = terminal_status
        gate.valid = False

    monkeypatch.setattr(route, "acquire_sse_authority_lease", acquire)
    monkeypatch.setattr(route, "close_sse_authority_lease", close)
    bridge = FakeBridge([open_entry()], on_read=complete_run)
    _, body = await asyncio.wait_for(connect(bridge, reader=SequencedReader([()])), timeout=1)
    assert "stream.open" in body
    assert "stream.end" not in body  # No fabricated transport receipt.
    assert ": heartbeat" not in body
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
async def test_v4_missing_authority_distinguishes_startup_from_terminal_run(
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
async def test_v4_replay_uses_native_cursor_and_schema_event(monkeypatch):
    patch_authority(monkeypatch)

    async def forbidden(*args, **kwargs):
        raise AssertionError("PG run_events must not drive live SSE")

    monkeypatch.setattr(route.repositories, "list_run_events", forbidden)
    bridge = FakeBridge(terminal_rows())
    response, body = await connect(bridge)

    assert response.headers["cache-control"] == "no-cache, no-transform"
    assert response.headers["x-accel-buffering"] == "no"
    assert "id: run-a:1:2-0" in body
    assert '"schema": "ai-platform.public-run-stream-event.v4"' in body
    assert '"payload": {"delta": "hello "}' in body
    assert body.index("id: run-a:1:3-0") < body.index("id: run-a:1:4-0")


@pytest.mark.asyncio
async def test_v4_reads_entries_added_after_replay_tail(monkeypatch):
    patch_authority(monkeypatch)
    bridge = FakeBridge(
        [open_entry()],
        on_read=lambda: bridge.rows.extend(terminal_rows()[1:]),
    )

    _, body = await connect(bridge)

    assert bridge.calls[:4] == [
        "resolve",
        "bounds",
        "replay:0-0:1-0",
        "read:1-0:128:5000",
    ]
    assert '"delta": "hello "' in body
    assert "event: stream.end\n" in body


@pytest.mark.asyncio
async def test_v4_trim_gap_is_control_and_requests_durable_hydration(monkeypatch):
    patch_authority(monkeypatch)
    bridge = FakeBridge(
        [open_entry()],
        resume=ResumeDecision(
            None, StreamGap("retained_history_unavailable", "run-a:1:1-0", 1, 1)
        ),
    )
    _, body = await connect(bridge, last_event_id="run-a:1:1-0")

    assert body.startswith("id: run-a:1:1-0\nevent: stream.gap\n")
    assert '"schema": "ai-platform.public-run-stream-control.v4"' in body
    assert '"recovery": "reload_durable_state"' in body


@pytest.mark.asyncio
async def test_v4_trim_between_resume_and_replay_emits_gap_instead_of_omitting_rows(
    monkeypatch,
):
    patch_authority(monkeypatch)
    bridge = FakeBridge(
        [open_entry()],
        replay_error=StreamContractError("stream_replay_continuity_unproven"),
    )

    _, body = await connect(bridge)

    assert "event: stream.gap\n" in body
    assert '"reason": "stream_continuity_unproven"' in body
    assert "event: stream.open\n" not in body


@pytest.mark.asyncio
async def test_v4_trim_gap_checks_lease_immediately_before_write(monkeypatch):
    lease_gate = FrameLeaseGate(valid=False)
    patch_authority(monkeypatch, lease_value=lease_gate)
    bridge = FakeBridge(
        [open_entry()],
        resume=ResumeDecision(
            None, StreamGap("retained_history_unavailable", "run-a:1:1-0", 1, 1)
        ),
    )
    response = await open_response(bridge, last_event_id="run-a:1:1-0")
    deny_lease_renewal(monkeypatch)

    body = "".join([chunk async for chunk in response.body_iterator])

    assert body == ""


@pytest.mark.asyncio
async def test_v4_replay_frame_checks_lease_immediately_before_write(monkeypatch):
    lease_gate = FrameLeaseGate(allowed_calls=1)
    patch_authority(monkeypatch, lease_value=lease_gate)
    response = await open_response(FakeBridge([open_entry()]))
    deny_lease_renewal(monkeypatch)

    body = "".join([chunk async for chunk in response.body_iterator])

    assert body == ""


@pytest.mark.asyncio
async def test_v4_replay_frame_rejects_lease_expired_during_renewal(monkeypatch):
    lease_gate = FrameLeaseGate(allowed_calls=1)
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
async def test_v4_replay_gap_checks_lease_immediately_before_write(monkeypatch):
    lease_gate = FrameLeaseGate(allowed_calls=1)
    patch_authority(monkeypatch, lease_value=lease_gate)
    response = await open_response(
        FakeBridge(
            [open_entry()],
            replay_error=StreamContractError("stream_replay_continuity_unproven"),
        )
    )
    deny_lease_renewal(monkeypatch)

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
async def test_v4_end_before_terminal_closes_without_synthetic_error(monkeypatch):
    patch_authority(monkeypatch)
    rows = (
        open_entry(),
        entry(
            "2-0",
            "sev-end",
            "stream.end",
            {"terminal_event_id": "sev-terminal"},
        ),
    )
    _, body = await connect(FakeBridge(rows))

    assert "event: stream.end\n" not in body
    assert "event: error\n" not in body


@pytest.mark.asyncio
async def test_v4_redis_admission_outage_fails_before_response(monkeypatch):
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
async def test_v4_blocking_read_transport_failure_records_safe_exit(monkeypatch, caplog):
    patch_authority(monkeypatch)
    caplog.set_level(logging.INFO, logger=route.logger.name)
    response = await route.chat_session_stream(
        "session-a",
        "run-a",
        request_for(FakeBridge([open_entry()]), reader=ClosedReader()),
        principal=AuthPrincipal(
            user_id="user-a", display_name="User", tenant_id="tenant-a"
        ),
    )
    body = "".join([chunk async for chunk in response.body_iterator])
    assert "event: stream.open" in body
    records = [record for record in caplog.records if record.msg == "sse_stream_exit"]
    assert len(records) == 1
    assert records[0].reason == "transport_failure"
    assert records[0].lease_released is True


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
