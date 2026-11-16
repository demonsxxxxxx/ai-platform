from __future__ import annotations

import asyncio
from pathlib import Path

import anyio
import httpx
import pytest

from app.files.application.profile_drive import ProfileDriveTransferError
from app.files.infrastructure import profile_drive as transfer_module
from app.files.transport.profile_drive import profile_drive_streaming_response


class Stream(httpx.AsyncByteStream):
    def __init__(self, chunks=(b"x",), *, delay=0, close_error=False, close_delay=0):
        self.chunks = chunks
        self.delay = delay
        self.close_error = close_error
        self.close_delay = close_delay
        self.closed = False

    async def __aiter__(self):
        for chunk in self.chunks:
            await anyio.sleep(self.delay)
            yield chunk

    async def aclose(self):
        self.closed = True
        await anyio.sleep(self.close_delay)
        if self.close_error:
            raise RuntimeError("synthetic cleanup secret must not be logged")


def client_factory(monkeypatch, handler):
    clients = []
    real_client = httpx.AsyncClient

    def create(**kwargs):
        client = real_client(transport=httpx.MockTransport(handler), **kwargs)
        clients.append(client)
        return client

    monkeypatch.setattr(transfer_module.httpx, "AsyncClient", create)
    return clients


async def open_file(**kwargs):
    return await transfer_module.open_profile_drive_file(
        upstream="https://synthetic.invalid", ca_cert_file="", jwt="synthetic",
        path="sample.txt", max_bytes=16, require_nonempty=True,
        not_found_status=404, too_large_detail="file_too_large", **kwargs,
    )


@pytest.mark.asyncio
async def test_header_cancellation_closes_the_acquired_client(monkeypatch):
    entered = asyncio.Event()

    async def handler(request):
        entered.set()
        await asyncio.Event().wait()

    clients = client_factory(monkeypatch, handler)
    task = asyncio.create_task(open_file())
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert clients[0].is_closed
    assert clients[0].timeout.read == 60
    assert clients[0].timeout.connect == 30


@pytest.mark.asyncio
async def test_header_overall_deadline_closes_client(monkeypatch):
    async def handler(request):
        await asyncio.Event().wait()

    clients = client_factory(monkeypatch, handler)
    with pytest.raises(ProfileDriveTransferError, match="profile_drive_transfer_timeout"):
        await open_file(total_timeout_seconds=0.01)
    assert clients[0].is_closed


@pytest.mark.asyncio
@pytest.mark.parametrize("status,headers,detail", [
    (403, {"Content-Length": "1"}, "profile_drive_transfer_denied"),
    (200, {}, "profile_drive_transfer_invalid"),
    (200, {"Content-Length": "1", "Content-Encoding": "gzip"}, "profile_drive_transfer_invalid"),
    (200, {"Content-Length": "17"}, "file_too_large"),
    (200, {"Content-Length": "0"}, "empty_file_not_supported"),
])
async def test_admission_error_survives_response_close_failure(monkeypatch, caplog, status, headers, detail):
    stream = Stream(close_error=True)
    clients = client_factory(monkeypatch, lambda request: httpx.Response(status, headers=headers, stream=stream))
    with pytest.raises(ProfileDriveTransferError, match=detail):
        await open_file()
    assert stream.closed and clients[0].is_closed
    assert "synthetic cleanup secret" not in caplog.text
    assert "profile_drive_cleanup_failed" in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("read,total,delay,chunks", [
    (0.01, 1, 0.1, (b"x",)),
    (1, 0.025, 0.015, (b"x", b"y", b"z")),
])
async def test_idle_and_continuous_body_overall_deadlines(monkeypatch, read, total, delay, chunks):
    stream = Stream(chunks, delay=delay)
    clients = client_factory(monkeypatch, lambda request: httpx.Response(
        200, headers={"Content-Length": str(len(chunks))}, stream=stream))
    transfer = await open_file(read_timeout_seconds=read, total_timeout_seconds=total)
    with pytest.raises(ProfileDriveTransferError, match="profile_drive_transfer_timeout"):
        async for _ in transfer.iter_bytes():
            pass
    assert stream.closed and clients[0].is_closed


@pytest.mark.asyncio
async def test_deadline_includes_time_after_acquisition_before_body(monkeypatch):
    stream = Stream()
    clients = client_factory(monkeypatch, lambda request: httpx.Response(
        200, headers={"Content-Length": "1"}, stream=stream))
    transfer = await open_file(total_timeout_seconds=0.01)
    await anyio.sleep(0.02)
    with pytest.raises(ProfileDriveTransferError, match="profile_drive_transfer_timeout"):
        async for _ in transfer.iter_bytes():
            pass
    assert stream.closed and clients[0].is_closed


@pytest.mark.asyncio
async def test_anyio_cancel_scope_and_bounded_hanging_close_release_client(monkeypatch):
    stream = Stream(delay=100, close_delay=100)
    clients = client_factory(monkeypatch, lambda request: httpx.Response(
        200, headers={"Content-Length": "1"}, stream=stream))
    monkeypatch.setattr(transfer_module, "_CLEANUP_TIMEOUT_SECONDS", 0.01)
    transfer = await open_file()
    with anyio.move_on_after(0.01) as scope:
        async for _ in transfer.iter_bytes():
            pass
    assert scope.cancel_called
    assert stream.closed and clients[0].is_closed


@pytest.mark.asyncio
@pytest.mark.parametrize("declared,expect_error", [(1, False), (2, True)])
async def test_temp_ownership_survives_close_error(monkeypatch, tmp_path, declared, expect_error):
    stream = Stream(close_error=True)
    # This fake deliberately leaves closing to the resource owner. HTTPX's
    # own EOF auto-close failure is tested separately below.
    class Response:
        status_code = 200
        headers = httpx.Headers({"Content-Length": str(declared)})
        def aiter_raw(self):
            return stream.__aiter__()
        async def aclose(self):
            await stream.aclose()

    class Client:
        is_closed = False
        def build_request(self, *args, **kwargs):
            return object()
        async def send(self, *args, **kwargs):
            return Response()
        async def aclose(self):
            self.is_closed = True

    client = Client()
    monkeypatch.setattr(transfer_module.httpx, "AsyncClient", lambda **kwargs: client)
    clients = [client]
    real_mkstemp = transfer_module.tempfile.mkstemp
    monkeypatch.setattr(transfer_module.tempfile, "mkstemp", lambda **kwargs: real_mkstemp(dir=tmp_path, **kwargs))
    transfer = await open_file()
    if expect_error:
        with pytest.raises(ProfileDriveTransferError, match="profile_drive_transfer_invalid"):
            await transfer_module.download_profile_drive_file(transfer=transfer)
        assert list(tmp_path.iterdir()) == []
    else:
        path, _, size = await transfer_module.download_profile_drive_file(transfer=transfer)
        assert size == 1 and Path(path).read_bytes() == b"x"
        Path(path).unlink()
    assert clients[0].is_closed


@pytest.mark.asyncio
async def test_asgi_failure_before_iteration_closes_transfer(monkeypatch):
    stream = Stream()
    clients = client_factory(monkeypatch, lambda request: httpx.Response(
        200, headers={"Content-Length": "1"}, stream=stream))
    transfer = await open_file()
    response = profile_drive_streaming_response(transfer)

    original = RuntimeError("synthetic downstream failure")

    async def send(message):
        raise original

    async def receive():
        return {"type": "http.disconnect"}

    with pytest.raises(RuntimeError, match="synthetic downstream failure") as caught:
        await response({"type": "http", "asgi": {"spec_version": "2.4"}}, receive, send)
    assert caught.value is original
    assert stream.closed and clients[0].is_closed


@pytest.mark.asyncio
async def test_httpx_eof_auto_close_failure_is_failed_transfer_and_removes_temp(monkeypatch, tmp_path):
    stream = Stream(close_error=True)
    clients = client_factory(monkeypatch, lambda request: httpx.Response(
        200, headers={"Content-Length": "1"}, stream=stream))
    real_mkstemp = transfer_module.tempfile.mkstemp
    monkeypatch.setattr(transfer_module.tempfile, "mkstemp", lambda **kwargs: real_mkstemp(dir=tmp_path, **kwargs))
    transfer = await open_file()
    with pytest.raises(ProfileDriveTransferError, match="profile_drive_transfer_failed"):
        await transfer_module.download_profile_drive_file(transfer=transfer)
    assert clients[0].is_closed
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_constructor_failure_and_broken_logger_preserve_primary_and_close_both(monkeypatch):
    stream = Stream(close_error=True)
    clients = client_factory(monkeypatch, lambda request: httpx.Response(
        200, headers={"Content-Length": "1"}, stream=stream))
    primary = ProfileDriveTransferError(503, "synthetic_primary")

    def broken_constructor(*args, **kwargs):
        raise primary

    def broken_logger(*args, **kwargs):
        raise RuntimeError("synthetic log handler failure")

    monkeypatch.setattr(transfer_module, "_ProfileDriveFile", broken_constructor)
    monkeypatch.setattr(transfer_module._LOGGER, "warning", broken_logger)
    with pytest.raises(ProfileDriveTransferError) as caught:
        await open_file()
    assert caught.value is primary
    assert stream.closed and clients[0].is_closed


@pytest.mark.asyncio
async def test_temp_and_close_logging_failures_do_not_mask_primary(monkeypatch, tmp_path):
    primary = ProfileDriveTransferError(503, "synthetic_primary")
    class Transfer:
        closed = False
        async def iter_bytes(self):
            raise primary
            yield b"unreachable"
        async def aclose(self):
            self.closed = True
            raise asyncio.CancelledError()

    def fail(*args, **kwargs):
        raise OSError("synthetic failure")

    real_mkstemp = transfer_module.tempfile.mkstemp
    transfer = Transfer()
    with monkeypatch.context() as patches:
        patches.setattr(transfer_module.tempfile, "mkstemp", lambda **kwargs: real_mkstemp(dir=tmp_path, **kwargs))
        patches.setattr(Path, "unlink", fail)
        patches.setattr(transfer_module._LOGGER, "warning", fail)
        with pytest.raises(ProfileDriveTransferError) as caught:
            await transfer_module.download_profile_drive_file(transfer=transfer)
    assert caught.value is primary
    assert transfer.closed
    for path in tmp_path.iterdir():
        path.unlink()


@pytest.mark.asyncio
async def test_operation_timeout_is_not_reclassified_as_budget_expiry(monkeypatch):
    original = TimeoutError("synthetic operation timeout")
    async def handler(request):
        raise original
    clients = client_factory(monkeypatch, handler)
    with pytest.raises(ProfileDriveTransferError, match="profile_drive_transfer_failed") as caught:
        await open_file()
    assert caught.value.__cause__ is original
    assert clients[0].is_closed


@pytest.mark.asyncio
async def test_owner_budget_preserves_independent_operation_timeout_identity(monkeypatch):
    stream = Stream()
    clients = client_factory(monkeypatch, lambda request: httpx.Response(
        200, headers={"Content-Length": "1"}, stream=stream))
    transfer = await open_file()
    original = TimeoutError("synthetic database operation timeout")
    with pytest.raises(TimeoutError) as caught:
        async with transfer:
            with transfer.budget():
                raise original
    assert caught.value is original
    assert clients[0].is_closed


@pytest.mark.asyncio
@pytest.mark.parametrize("blocked_message", ["http.response.start", "http.response.body"])
async def test_asgi_overall_timeout_after_start_never_sends_second_status(monkeypatch, blocked_message):
    stream = Stream()
    clients = client_factory(monkeypatch, lambda request: httpx.Response(
        200, headers={"Content-Length": "1"}, stream=stream))
    transfer = await open_file(total_timeout_seconds=0.01)
    response = profile_drive_streaming_response(transfer)
    messages = []

    async def send(message):
        messages.append(message)
        if message["type"] == blocked_message:
            await anyio.sleep(100)

    async def receive():
        return {"type": "http.disconnect"}

    with pytest.raises(ProfileDriveTransferError, match="profile_drive_transfer_timeout"):
        await response({"type": "http", "asgi": {"spec_version": "2.4"}}, receive, send)
    assert [message["status"] for message in messages if message["type"] == "http.response.start"] == [200]
    assert not any(message.get("more_body") is False for message in messages)
    assert stream.closed and clients[0].is_closed


@pytest.mark.asyncio
@pytest.mark.parametrize("operation_timeout", [False, True])
async def test_import_claim_budget_rolls_back_and_reconciles_without_masking_error(monkeypatch, operation_timeout):
    from types import SimpleNamespace
    from fastapi import HTTPException
    import app.routes.files as files

    stream = Stream()
    clients = client_factory(monkeypatch, lambda request: httpx.Response(
        200, headers={"Content-Length": "1"}, stream=stream))
    transfer = await open_file(total_timeout_seconds=0.01 if not operation_timeout else 1)
    exits, reconciled = [], []
    original = TimeoutError("synthetic database timeout")

    class Transaction:
        async def __aenter__(self):
            return object()
        async def __aexit__(self, exc_type, exc, traceback):
            exits.append(exc)

    async def noop(*args, **kwargs):
        return None

    async def acquired(**kwargs):
        return transfer

    async def usage(*args, **kwargs):
        if operation_timeout:
            raise original
        await anyio.sleep(100)

    async def reconcile(**kwargs):
        assert clients[0].is_closed  # release transport before DB compensation
        reconciled.append(kwargs)
        raise RuntimeError("synthetic compensation failure")

    monkeypatch.setattr(files, "transaction", Transaction)
    monkeypatch.setattr(files, "ensure_workspace", noop)
    monkeypatch.setattr(files, "ensure_user", noop)
    monkeypatch.setattr(files, "_require_upload_permissions", lambda principal: None)
    monkeypatch.setattr(files, "_cleanup_expired_upload_sessions", noop)
    monkeypatch.setattr(files, "ObjectStorage", lambda: object())
    monkeypatch.setattr(files, "_open_profile_drive_file", acquired)
    monkeypatch.setattr(files, "get_file_storage_usage", usage)
    monkeypatch.setattr(files, "_reconcile_profile_drive_import", reconcile)
    principal = SimpleNamespace(tenant_id="tenant-a", user_id="user-a", display_name="Synthetic")
    with pytest.raises(HTTPException) as caught:
        await files._store_profile_drive_file(None, {"path": "report.txt"}, principal)
    assert caught.value.status_code == 503
    if operation_timeout:
        assert caught.value.detail == "profile_drive_import_failed"
        assert caught.value.__cause__ is original
        assert exits[-1] is original
    else:
        assert caught.value.detail == "profile_drive_transfer_timeout"
        assert isinstance(exits[-1], asyncio.CancelledError)
    assert len(reconciled) == 1
    assert stream.closed and clients[0].is_closed


def test_transfer_timeout_configuration_has_finite_positive_bounds():
    from pydantic import ValidationError
    from app.settings import Settings

    settings = Settings(_env_file=None)
    assert settings.profile_drive_transfer_read_timeout_seconds == 60
    assert settings.profile_drive_transfer_total_timeout_seconds == 1800
    for field, value in [
        ("profile_drive_transfer_read_timeout_seconds", 0),
        ("profile_drive_transfer_read_timeout_seconds", 3601),
        ("profile_drive_transfer_total_timeout_seconds", 0),
        ("profile_drive_transfer_total_timeout_seconds", 10801),
        ("profile_drive_transfer_total_timeout_seconds", float("inf")),
    ]:
        with pytest.raises(ValidationError):
            Settings(_env_file=None, **{field: value})


@pytest.mark.asyncio
async def test_opted_in_http_upstream_still_sends_the_user_bearer_to_the_connector(monkeypatch):
    seen = []

    def handler(request):
        seen.append((str(request.url).split("/api/")[0], request.headers["authorization"]))
        return httpx.Response(200, headers={"Content-Length": "1"}, stream=Stream())

    client_factory(monkeypatch, handler)
    transfer = await transfer_module.open_profile_drive_file(
        upstream="http://profile-drive-mcp:5201", ca_cert_file="", jwt="synthetic",
        path="sample.txt", max_bytes=16, require_nonempty=True,
        not_found_status=404, too_large_detail="file_too_large",
    )
    await transfer.aclose()
    assert seen == [("http://profile-drive-mcp:5201", "Bearer synthetic")]
