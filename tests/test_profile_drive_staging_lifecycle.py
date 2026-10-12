"""Executor-local ProfileDrive staging resource ownership regressions."""

import asyncio
import hashlib
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from app.context.retrieval import ContextRetrievalDenied
from app.runtime.sandbox import context_retrieval_client as staging
from app.runtime.sandbox.contracts import PROFILE_DRIVE_STAGE_TOOL, ContextRetrievalScope


class _StageStream(httpx.AsyncByteStream):
    def __init__(self, chunks=(b"partial",), *, error=None, pause=False, pause_close=False):
        self.chunks = chunks
        self.error = error
        self.pause = pause
        self.pause_close = pause_close
        self.body_waiting = asyncio.Event()
        self.close_started = asyncio.Event()
        self.close_calls = 0

    async def __aiter__(self):
        for chunk in self.chunks:
            yield chunk
        if self.pause:
            self.body_waiting.set()
            await asyncio.Future()
        if self.error is not None:
            raise self.error

    async def aclose(self):
        self.close_calls += 1
        self.close_started.set()
        if self.pause_close:
            await asyncio.Future()


def _stage_client(monkeypatch, tmp_path, stream, *, headers=None, status=200):
    real_client = httpx.AsyncClient
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            status,
            headers=headers if headers is not None else {"Content-Length": "7"},
            stream=stream,
        )
    )
    monkeypatch.setattr(staging.httpx, "AsyncClient", lambda **kwargs: real_client(transport=transport, **kwargs))
    scope = ContextRetrievalScope(
        tenant_id="tenant-a", workspace_id="workspace-a", user_id="user-a",
        session_id="session-a", run_id="run-a", agent_id="agent-a",
    )
    retrieval = staging.PlatformContextRetrievalClient(
        callback_url="https://platform.test/context-retrieval",
        callback_token_id="cbt:run-a:attempt-a", callback_token="test-callback-token",
        attempt_id="attempt-a", scope=scope, workspace_root=tmp_path,
    )

    async def execute():
        return await retrieval.execute(
            PROFILE_DRIVE_STAGE_TOOL, SimpleNamespace(**scope.model_dump()), {"path": "S24/source.bin"},
        )

    target = tmp_path / "context" / "profile-drive" / hashlib.sha256(b"S24/source.bin").hexdigest()[:16] / "source.bin"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"previous-complete-file")
    foreign = target.with_name(".staging-foreign.part")
    foreign.write_bytes(b"another-transfer")
    return execute, target, foreign


def _assert_only_previous_files(target, foreign):
    assert target.read_bytes() == b"previous-complete-file"
    assert foreign.read_bytes() == b"another-transfer"
    assert set(target.parent.iterdir()) == {target, foreign}


def _patch_output(monkeypatch, *, write_error=None, close_error=None):
    real_open = Path.open
    outputs = []

    class Output:
        def __init__(self, file):
            self.file = file
            self.close_calls = 0

        def write(self, chunk):
            self.file.write(chunk)
            if write_error is not None:
                raise write_error

        def close(self):
            self.close_calls += 1
            self.file.close()
            if close_error is not None:
                raise close_error

    def open_output(path, mode="r", *args, **kwargs):
        file = real_open(path, mode, *args, **kwargs)
        if mode == "xb":
            output = Output(file)
            outputs.append(output)
            return output
        return file

    monkeypatch.setattr(Path, "open", open_output)
    return outputs


@pytest.mark.asyncio
@pytest.mark.parametrize("repeat_cancel", [False, True])
@pytest.mark.parametrize("close_fails", [False, True])
async def test_cancelled_profile_stage_removes_only_owned_partial(
    monkeypatch, tmp_path, caplog, repeat_cancel, close_fails,
):
    stream = _StageStream(pause=True, pause_close=repeat_cancel)
    execute, target, foreign = _stage_client(monkeypatch, tmp_path, stream)
    outputs = _patch_output(monkeypatch, close_error=OSError("private-close-path") if close_fails else None)
    task = asyncio.create_task(execute())
    try:
        await asyncio.wait_for(stream.body_waiting.wait(), timeout=1)
        assert outputs[0].file.tell() == 7
        assert len(list(target.parent.glob(".staging-*.part"))) == 2
        task.cancel("first-cancellation")
        if repeat_cancel:
            await asyncio.wait_for(stream.close_started.wait(), timeout=1)
            task.cancel("second-cancellation")
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=1)
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert task.cancelled()
    assert outputs[0].file.closed
    assert outputs[0].close_calls == 1
    assert stream.close_calls == 1
    _assert_only_previous_files(target, foreign)
    assert "private-close-path" not in caplog.text
    assert ("profile_drive_staging_close_failed" in caplog.text) is close_fails


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["write", "close", "replace"])
async def test_profile_stage_io_failure_keeps_previous_target(monkeypatch, tmp_path, operation):
    execute, target, foreign = _stage_client(monkeypatch, tmp_path, _StageStream())
    failure = OSError(f"synthetic-{operation}-failure")
    outputs = _patch_output(
        monkeypatch,
        write_error=failure if operation == "write" else None,
        close_error=failure if operation == "close" else None,
    )
    if operation == "replace":
        def replace(source, destination):
            assert outputs[0].file.closed
            assert Path(source).read_bytes() == b"partial"
            assert destination == target
            raise failure
        monkeypatch.setattr(staging.os, "replace", replace)
    with pytest.raises(RuntimeError, match="^context_retrieval_callback_failed$") as raised:
        await execute()
    assert raised.value.__cause__ is failure
    assert outputs[0].file.closed
    assert outputs[0].close_calls == 1
    _assert_only_previous_files(target, foreign)


class _StageAbort(BaseException):
    pass


@pytest.mark.asyncio
@pytest.mark.parametrize("primary", [
    RuntimeError("context_retrieval_callback_invalid"),
    ContextRetrievalDenied("profile_drive_file_too_large"),
    OSError("synthetic-body-error"),
    asyncio.CancelledError("synthetic-cancellation"),
    _StageAbort("synthetic-abort"),
])
@pytest.mark.parametrize("broken_logging", [False, True])
async def test_profile_stage_cleanup_failures_preserve_primary(monkeypatch, tmp_path, caplog, primary, broken_logging):
    if broken_logging:
        def fail_warning(*_args, **_kwargs):
            raise RuntimeError("synthetic-logging-failure")
        monkeypatch.setattr(staging._logger, "warning", fail_warning)
    execute, target, foreign = _stage_client(monkeypatch, tmp_path, _StageStream(error=primary))
    outputs = _patch_output(monkeypatch, close_error=OSError("private-output-path"))
    unlink_calls = []

    def fail_unlink(path, *, missing_ok=False):
        unlink_calls.append(path)
        assert outputs[0].file.closed
        assert path not in {target, foreign}
        assert missing_ok
        raise OSError("private-temporary-path test-callback-token")

    monkeypatch.setattr(Path, "unlink", fail_unlink)
    # ContextRetrievalDenied is a PermissionError; denial keeps priority over
    # the generic I/O-to-callback-failure mapping.
    maps_callback_failure = isinstance(primary, OSError) and not isinstance(primary, ContextRetrievalDenied)
    expected_type = RuntimeError if maps_callback_failure else type(primary)
    with pytest.raises(expected_type) as raised:
        await execute()
    if maps_callback_failure:
        assert str(raised.value) == "context_retrieval_callback_failed"
        assert raised.value.__cause__ is primary
    else:
        assert raised.value is primary
    assert len(unlink_calls) == 1
    assert unlink_calls[0].read_bytes() == b"partial"
    assert target.read_bytes() == b"previous-complete-file"
    assert foreign.read_bytes() == b"another-transfer"
    assert [record.getMessage() for record in caplog.records if record.name == staging.__name__] == (
        [] if broken_logging else [
            "profile_drive_staging_close_failed", "profile_drive_staging_cleanup_failed",
        ]
    )
    assert "private-output-path" not in caplog.text
    assert "private-temporary-path" not in caplog.text
    assert "test-callback-token" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("open_fails", ["collision", "permission"])
async def test_profile_stage_failed_exclusive_open_does_not_own_existing_file(monkeypatch, tmp_path, open_fails):
    execute, target, foreign = _stage_client(monkeypatch, tmp_path, _StageStream())
    monkeypatch.setattr(staging.uuid, "uuid4", lambda: SimpleNamespace(hex="foreign"))
    if open_fails == "permission":
        real_open = Path.open

        def denied_open(path, mode="r", *args, **kwargs):
            if mode == "xb":
                raise PermissionError("synthetic-open-denied")
            return real_open(path, mode, *args, **kwargs)

        monkeypatch.setattr(Path, "open", denied_open)
    with pytest.raises(RuntimeError, match="^context_retrieval_callback_failed$") as raised:
        await execute()
    assert isinstance(raised.value.__cause__, FileExistsError if open_fails == "collision" else PermissionError)
    _assert_only_previous_files(target, foreign)


@pytest.mark.asyncio
@pytest.mark.parametrize("content", [b"", b"complete-new-body"])
async def test_profile_stage_success_hands_off_closed_complete_file_atomically(monkeypatch, tmp_path, content):
    execute, target, foreign = _stage_client(
        monkeypatch, tmp_path, _StageStream(chunks=(content,)), headers={"Content-Length": str(len(content))},
    )
    outputs = _patch_output(monkeypatch)
    real_replace = staging.os.replace
    replacements = []

    def replace(source, destination):
        assert outputs[0].file.closed
        assert Path(source).read_bytes() == content
        assert target.read_bytes() == b"previous-complete-file"
        replacements.append((source, destination))
        real_replace(source, destination)
        # A reused name after handoff is no longer this transfer's resource.
        Path(source).write_bytes(b"next-owner")

    monkeypatch.setattr(staging.os, "replace", replace)
    result = await execute()
    assert len(replacements) == 1
    handed_off_path, destination = replacements[0]
    assert destination == target
    assert target.read_bytes() == content
    assert handed_off_path.read_bytes() == b"next-owner"
    assert foreign.read_bytes() == b"another-transfer"
    assert result["workspace_path"] == target.relative_to(tmp_path).as_posix()
    assert result["bytes_staged"] == len(content)
    assert result["audit"]["result"] == "staged"
    assert result["redaction"] == {"source_path_removed": True}


@pytest.mark.asyncio
@pytest.mark.parametrize(("headers", "chunks", "error_type", "error_code"), [
    ({"Content-Length": "8"}, (b"partial",), RuntimeError, "context_retrieval_callback_invalid"),
    ({"Content-Length": "6"}, (b"partial",), ContextRetrievalDenied, "profile_drive_file_too_large"),
    ({"Content-Length": "7", "Content-Encoding": "gzip"}, (), RuntimeError, "context_retrieval_callback_invalid"),
    ({}, (), RuntimeError, "context_retrieval_callback_invalid"),
    ({"Content-Length": "invalid"}, (), RuntimeError, "context_retrieval_callback_invalid"),
    ({"Content-Length": "-1"}, (), ContextRetrievalDenied, "profile_drive_file_too_large"),
])
async def test_profile_stage_validation_keeps_existing_files(
    monkeypatch, tmp_path, headers, chunks, error_type, error_code,
):
    execute, target, foreign = _stage_client(monkeypatch, tmp_path, _StageStream(chunks), headers=headers)
    with pytest.raises(error_type, match=f"^{error_code}$"):
        await execute()
    _assert_only_previous_files(target, foreign)


@pytest.mark.asyncio
@pytest.mark.parametrize(("status", "error_type", "error_code"), [
    (401, ContextRetrievalDenied, "context_scope_denied"),
    (403, ContextRetrievalDenied, "context_scope_denied"),
    (409, ContextRetrievalDenied, "context_scope_denied"),
    (413, ContextRetrievalDenied, "profile_drive_file_too_large"),
    (422, ContextRetrievalDenied, "context_scope_denied"),
    (500, RuntimeError, "context_retrieval_callback_failed"),
])
async def test_profile_stage_callback_mapping_does_not_touch_unowned_temp(
    monkeypatch, tmp_path, status, error_type, error_code,
):
    execute, target, foreign = _stage_client(monkeypatch, tmp_path, _StageStream(()), status=status)
    monkeypatch.setattr(staging.uuid, "uuid4", lambda: SimpleNamespace(hex="foreign"))
    with pytest.raises(error_type, match=f"^{error_code}$"):
        await execute()
    _assert_only_previous_files(target, foreign)
