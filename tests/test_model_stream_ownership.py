from __future__ import annotations

import asyncio
import logging
import threading
from contextlib import asynccontextmanager
from types import SimpleNamespace

import httpx
import pytest
from starlette.requests import ClientDisconnect

from app.execution.application import model_control_plane as control
from app.execution.application import model_stream_ownership as ownership
from app.execution.application.model_response_evidence import observe_anthropic_text
from app.execution.infrastructure.model_upstream import UpstreamStream
from app.execution.transport.model_management import _OwnedModelStreamingResponse


class Stream:
    status = 200
    content_type = "text/event-stream"

    def __init__(self, body=None, close_error=None):
        self._body = body if body is not None else [b"event"]
        self.close_error = close_error
        self.closed = threading.Event()
        self.close_count = 0

    def body(self):
        return iter(self._body)

    def close(self):
        if not self.closed.is_set():
            self.close_count += 1
            self.closed.set()
        if self.close_error:
            raise self.close_error


async def _wait(event):
    async with asyncio.timeout(3):
        while not event.is_set():
            await asyncio.sleep(.001)


@pytest.mark.asyncio
async def test_open_cancelled_before_worker_starts_skips_operation(monkeypatch):
    submitted = []
    calls = []
    entered = asyncio.Event()

    async def queued(operation):
        submitted.append(operation)
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(ownership.asyncio, "to_thread", queued)
    task = asyncio.create_task(ownership.open_owned_model_stream(lambda: calls.append(True)))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    submitted[0]()
    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("close_error", [None, OSError("synthetic-private-close-detail")])
async def test_cancelled_open_late_result_closed_by_original_worker(close_error, caplog):
    started, release = threading.Event(), threading.Event()
    stream = Stream(close_error=close_error)
    worker_threads = []
    close_threads = []
    original_close = stream.close

    def close():
        close_threads.append(threading.get_ident())
        original_close()

    stream.close = close

    def open_stream():
        worker_threads.append(threading.get_ident())
        started.set()
        assert release.wait(timeout=3)
        return stream

    task = asyncio.create_task(ownership.open_owned_model_stream(open_stream))
    try:
        await _wait(started)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        release.set()
    await _wait(stream.closed)
    assert stream.close_count == 1 and worker_threads == close_threads
    assert "synthetic-private-close-detail" not in caplog.text


@pytest.mark.asyncio
async def test_cancel_after_delivery_before_await_resumes_closes_once(monkeypatch):
    actual_to_thread = asyncio.to_thread
    delivered = asyncio.Event()
    stream = Stream()

    async def paused_delivery(operation):
        await actual_to_thread(operation)
        delivered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(ownership.asyncio, "to_thread", paused_delivery)
    task = asyncio.create_task(ownership.open_owned_model_stream(lambda: stream))
    await delivered.wait()
    assert not stream.closed.is_set()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert stream.close_count == 1


@pytest.mark.asyncio
async def test_successful_open_transfers_close_responsibility_to_caller():
    stream = Stream()
    received = await ownership.open_owned_model_stream(lambda: stream)
    assert received is stream and stream.close_count == 0
    received.close()
    assert stream.close_count == 1


@pytest.mark.asyncio
async def test_open_failure_preserves_original_error():
    failure = OSError("synthetic open failure")

    def fail():
        raise failure

    with pytest.raises(OSError) as caught:
        await ownership.open_owned_model_stream(fail)
    assert caught.value is failure


class BlockingIterator:
    def __init__(self, *, fail=False):
        self.started = threading.Event()
        self.release = threading.Event()
        self.iterator_closed = threading.Event()
        self.resource_closed = threading.Event()
        self.fail = fail
        self.close_count = 0
        self.resource_close_count = 0
        self.in_next = False

    def __iter__(self):
        return self

    def __next__(self):
        self.in_next = True
        try:
            self.started.set()
            assert self.release.wait(timeout=3)
            if self.fail:
                raise OSError("synthetic read failure")
            return b"late chunk"
        finally:
            self.in_next = False

    def close(self):
        assert not self.in_next, "closed an executing iterator"
        self.close_count += 1
        self.iterator_closed.set()

    def close_resource(self):
        self.resource_close_count += 1
        self.resource_closed.set()


@pytest.mark.asyncio
@pytest.mark.parametrize("fail", [False, True])
async def test_reading_close_defers_iterator_close_and_never_yields_late_chunk(fail):
    source = BlockingIterator(fail=fail)
    body = ownership.OwnedModelResponseBody(source, close=source.close_resource)

    def consume():
        try:
            return next(body)
        except StopIteration:
            return "closed"

    reader = asyncio.create_task(asyncio.to_thread(consume))
    try:
        await _wait(source.started)
        body.close()
        body.close()
        assert source.resource_closed.is_set() and not source.iterator_closed.is_set()
        assert not reader.done()  # Resource close does not promise to wake a read.
    finally:
        source.release.set()
    if fail:
        with pytest.raises(OSError, match="synthetic read failure"):
            await reader
    else:
        assert await reader == "closed"
    assert source.close_count == source.resource_close_count == 1


@pytest.mark.parametrize("broken_logger", [False, True])
def test_iterator_cleanup_failure_cannot_block_resource_close(caplog, monkeypatch, broken_logger):
    closed = []
    failure = OSError("primary-private-read")
    if broken_logger:
        def log_failure(*_a):
            raise RuntimeError("synthetic logger failure")
        monkeypatch.setattr(ownership._logger, "warning", log_failure)

    class BrokenClose:
        def __iter__(self):
            return self

        def __next__(self):
            raise failure

        def close(self):
            closed.append("iterator")
            raise ValueError("secondary-private-close")

    body = ownership.OwnedModelResponseBody(BrokenClose(), close=lambda: closed.append("resource"))
    with pytest.raises(OSError) as caught:
        next(body)
    assert caught.value is failure
    assert closed == ["iterator", "resource"]
    if not broken_logger:
        assert "model_proxy_stream_cleanup_failed" in caplog.text
    assert "private" not in caplog.text


class HttpBody(httpx.SyncByteStream):
    def __init__(self, *, blocked=False):
        self.blocked = blocked
        self.started = threading.Event()
        self.release = threading.Event()
        self.closed = 0

    def __iter__(self):
        self.started.set()
        if self.blocked:
            assert self.release.wait(timeout=3)
        yield b'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"synthetic"}}\n\n'
        yield b"data: later\n\n"

    def close(self):
        self.closed += 1


def _response(*, blocked=False, observe=False):
    wire = HttpBody(blocked=blocked)
    client = httpx.Client(transport=httpx.MockTransport(
        lambda _r: httpx.Response(200, stream=wire)
    ), trust_env=False)
    response = client.send(client.build_request("POST", "https://synthetic.example/", content=b"{}"), stream=True)
    stream = UpstreamStream(200, "text/event-stream", response, client, 1024)
    source = stream.body()
    if observe:
        source = observe_anthropic_text(source, run_id="run-synthetic", attempt_id="attempt-synthetic")
    body = ownership.OwnedModelResponseBody(source, close=stream.close)
    return wire, stream, body, _OwnedModelStreamingResponse(body, close=body.close)


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["start", "body"])
@pytest.mark.parametrize("observe", [False, True])
async def test_asgi_send_failure_always_closes_owner(stage, observe):
    wire, stream, _body, response = _response(observe=observe)

    async def receive():
        raise AssertionError("ASGI2.4 does not listen for disconnect")

    async def send(message):
        if message["type"] == f"http.response.{stage}":
            raise OSError("synthetic downstream disconnect")

    with pytest.raises(ClientDisconnect):
        await response({"type": "http", "asgi": {"spec_version": "2.4"}}, receive, send)
    assert wire.closed == 1 and stream.response.is_closed and stream.client.is_closed
    assert wire.started.is_set() is (stage == "body")


@pytest.mark.asyncio
async def test_asgi23_disconnect_waits_for_read_then_closes_before_return():
    wire, stream, _body, response = _response(blocked=True, observe=True)
    disconnected = asyncio.Event()

    async def receive():
        await _wait(wire.started)
        disconnected.set()
        return {"type": "http.disconnect"}

    async def send(_message):
        await asyncio.sleep(0)

    task = asyncio.create_task(response({"type": "http", "asgi": {"spec_version": "2.3"}}, receive, send))
    try:
        await disconnected.wait()
        await asyncio.sleep(.02)
        assert not task.done() and not stream.client.is_closed
    finally:
        wire.release.set()
    await asyncio.wait_for(task, timeout=3)
    assert wire.closed == 1 and stream.response.is_closed and stream.client.is_closed


@pytest.mark.asyncio
async def test_external_cancel_closes_resources_and_reader_owns_deferred_finish(caplog):
    wire, stream, body, response = _response(blocked=True, observe=True)
    sent = []

    async def receive():
        raise AssertionError("unexpected receive")

    async def send(message):
        sent.append(message["type"])
        await asyncio.sleep(0)

    task = asyncio.create_task(response({"type": "http", "asgi": {"spec_version": "2.4"}}, receive, send))
    try:
        await _wait(wire.started)
        task.cancel()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert wire.closed == 1 and stream.client.is_closed
        assert sent == ["http.response.start"]
    finally:
        wire.release.set()
    async with asyncio.timeout(3):
        while "final=True" not in caplog.text:
            await asyncio.sleep(.001)
    assert not body._reading and body._iterator_closed and wire.closed == 1
    assert "generator already executing" not in caplog.text


def test_closing_anthropic_body_finishes_observer_and_closes_resource(caplog):
    wire, stream, body, _response_object = _response(observe=True)
    with caplog.at_level(logging.WARNING):
        next(body)
        body.close()
        body.close()
    assert "final=True" in caplog.text
    assert wire.closed == 1 and stream.client.is_closed


@pytest.mark.asyncio
async def test_normal_response_and_finite_count_noop_close():
    wire, stream, _body, response = _response()
    sent = []

    async def receive():
        raise AssertionError("unexpected receive")

    async def send(message):
        sent.append(message)

    await response({"type": "http", "asgi": {"spec_version": "2.4"}}, receive, send)
    assert wire.closed == 1 and stream.client.is_closed
    finite = control.RuntimeProxyResponse(200, "application/json", (b"{}",))
    assert finite.close is None
    await _OwnedModelStreamingResponse(finite.body, close=finite.close)(
        {"type": "http", "asgi": {"spec_version": "2.4"}}, receive, send,
    )
    assert sent[-1]["more_body"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("broken_logger", [False, True])
async def test_cleanup_failure_never_masks_cancel(caplog, monkeypatch, broken_logger):
    failure = asyncio.CancelledError()
    if broken_logger:
        def log_failure(*_a):
            raise RuntimeError("synthetic logger failure")
        monkeypatch.setattr(ownership._logger, "warning", log_failure)

    async def receive():
        raise AssertionError("unexpected receive")

    async def send(_message):
        raise failure

    def close():
        raise OSError("private-cleanup-detail")

    response = _OwnedModelStreamingResponse(iter([b"event"]), close=close)
    with pytest.raises(asyncio.CancelledError) as caught:
        await response({"type": "http", "asgi": {"spec_version": "2.4"}}, receive, send)
    assert caught.value is failure
    if not broken_logger:
        assert "model_proxy_stream_cleanup_failed" in caplog.text
    assert "private-cleanup-detail" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_stage", ["body", "observer", "response"])
async def test_proxy_assembly_failure_closes_already_accepted_stream(monkeypatch, failure_stage):
    stream = Stream()
    failure = ValueError("synthetic assembly failure")

    def fail(*_a, **_kw):
        raise failure

    if failure_stage == "body":
        stream.body = fail
    elif failure_stage == "observer":
        monkeypatch.setattr(control, "observe_anthropic_text", fail)
    else:
        monkeypatch.setattr(control, "RuntimeProxyResponse", fail)

    @asynccontextmanager
    async def transaction():
        yield object()

    async def connection(*_a, **_kw):
        return SimpleNamespace(base_url="https://synthetic.example", api_key="synthetic",
                               auto_compact_window=200_000, max_output_tokens=2048, conversation_mode="empty_start")

    service = control.ModelControlPlaneService(
        transaction_factory=transaction,
        settings_provider=lambda: SimpleNamespace(model_proxy_internal_token="synthetic-internal",
            model_connection_encryption_key="synthetic", model_connection_allowed_internal_hosts=""),
        repository=SimpleNamespace(run_connection=connection), security=SimpleNamespace(),
        upstream=SimpleNamespace(open_stream=lambda **_kw: stream),
        attempt_capability_verifier=lambda **_kw: True,
    )
    with pytest.raises(ValueError) as caught:
        await service.proxy(provider="anthropic", upstream_path="v1/messages",
            body=b'{"model":"synthetic","max_tokens":32}', headers={"anthropic-version":"2023-06-01"},
            run_id="run-synthetic", attempt_id="attempt-synthetic", internal_token="synthetic-internal",
            model_proxy_capability="synthetic-capability", query="")
    assert caught.value is failure and stream.close_count == 1


def test_cleanup_logger_failure_is_secondary(monkeypatch):
    def fail():
        raise OSError("synthetic")

    monkeypatch.setattr(ownership._logger, "warning", lambda *_a: fail())
    ownership.close_model_stream(fail)


@pytest.mark.asyncio
async def test_transport_construction_failure_closes_projection_owner(monkeypatch):
    from starlette.requests import Request
    from app.execution.transport import model_management as transport

    closed = []
    failure = ValueError("synthetic response construction")
    upstream = control.RuntimeProxyResponse(200, "text/event-stream", iter([b"event"]), close=lambda: closed.append(True))

    async def proxy(**_kwargs):
        return upstream

    def fail(*_a, **_kw):
        raise failure

    monkeypatch.setattr(transport, "configured_model_control_plane", lambda: SimpleNamespace(proxy=proxy))
    monkeypatch.setattr(transport, "_OwnedModelStreamingResponse", fail)

    async def receive():
        return {"type": "http.request", "body": b"{}", "more_body": False}

    request = Request({"type": "http", "method": "POST", "path": "/synthetic", "headers": [], "query_string": b""}, receive)
    with pytest.raises(ValueError) as caught:
        await transport.proxy_model_request("openai", "v1/responses", request,
            x_ai_platform_run_id="run", x_ai_platform_attempt_id="attempt", x_ai_platform_internal_token="synthetic",
            x_ai_platform_model_authorization="", x_ai_platform_model_api_key="")
    assert caught.value is failure and closed == [True]


@pytest.mark.asyncio
async def test_cancelled_open_late_failure_is_collected_without_private_logs(caplog):
    started, release, finished = threading.Event(), threading.Event(), threading.Event()
    loop = asyncio.get_running_loop()
    previous_handler = loop.get_exception_handler()
    unhandled = []
    loop.set_exception_handler(lambda _loop, context: unhandled.append(context))

    def fail_late():
        started.set()
        try:
            assert release.wait(timeout=3)
            raise OSError("synthetic-private-late-open-error")
        finally:
            finished.set()

    task = asyncio.create_task(ownership.open_owned_model_stream(fail_late))
    try:
        await _wait(started)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        release.set()
        await _wait(finished)
        # Allow the executor completion to settle on the original event loop.
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert unhandled == []
        assert "synthetic-private-late-open-error" not in caplog.text
    finally:
        release.set()
        await _wait(finished)
        loop.set_exception_handler(previous_handler)
