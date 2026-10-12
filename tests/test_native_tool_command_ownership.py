"""Command lifecycle ownership with synthetic processes and pipes only."""

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.runtime.sandbox import native_tool_app


class _Pipe:
    def __init__(self, data):
        self.data = data
        self.eof = asyncio.Event()
        self.started = asyncio.Event()
        self.task = None
        self.error = None

    async def read(self, size):
        self.task = asyncio.current_task()
        self.started.set()
        if self.data:
            chunk, self.data = self.data[:size], self.data[size:]
            return chunk
        if self.error is not None:
            raise self.error
        await self.eof.wait()
        return b""


@pytest.fixture
async def command_owner(monkeypatch, tmp_path):
    state = SimpleNamespace(
        stdout=_Pipe(b"stdout data"),
        stderr=_Pipe(b"stderr data"),
        wait_started=asyncio.Event(),
        exited=asyncio.Event(),
        group_started=asyncio.Event(),
        group_release=asyncio.Event(),
        uid_started=asyncio.Event(),
        uid_release=asyncio.Event(),
        uid_finished=asyncio.Event(),
        wait_error=None,
        group_error=None,
        uid_error=None,
        calls=[],
        cleanup_tasks=[],
        owners=[],
    )
    state.group_release.set()
    state.uid_release.set()

    class Process:
        pid = 123
        returncode = None
        stdout = state.stdout
        stderr = state.stderr

        async def wait(self):
            state.wait_started.set()
            await state.exited.wait()
            if state.wait_error is not None:
                raise state.wait_error
            self.returncode = 7
            return self.returncode

    state.process = Process()

    async def spawn(*_args, **_kwargs):
        state.calls.append("spawn")
        return state.process

    async def terminate_group(process, *, include_orphans=False):
        assert process is state.process
        state.calls.append(("group", include_orphans))
        state.cleanup_tasks.append(asyncio.current_task())
        state.group_started.set()
        await state.group_release.wait()
        if state.group_error is not None:
            raise state.group_error
        if process.returncode is None:
            process.returncode = -15

    async def terminate_uid(uid):
        state.calls.append(("uid", uid))
        state.cleanup_tasks.append(asyncio.current_task())
        state.uid_started.set()
        await state.uid_release.wait()
        if state.uid_error is not None:
            raise state.uid_error
        state.uid_finished.set()

    def forbid_signal(*_args, **_kwargs):
        pytest.fail("synthetic command ownership tests must never signal real processes")

    monkeypatch.setattr(native_tool_app, "_require_native_tool_identity", lambda *_args: None)
    monkeypatch.setattr(native_tool_app.asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(native_tool_app, "_terminate_process_group", terminate_group)
    monkeypatch.setattr(native_tool_app, "_terminate_uid_processes", terminate_uid)
    monkeypatch.setattr(native_tool_app.os, "kill", forbid_signal)
    monkeypatch.setattr(native_tool_app.os, "killpg", forbid_signal)
    state.arguments = dict(command="synthetic command", workspace=tmp_path, uid=10001, gid=10001)

    def start(**kwargs):
        owner = asyncio.create_task(native_tool_app._run_command(**(state.arguments | kwargs)))
        state.owners.append(owner)
        return owner

    state.start = start
    try:
        yield state
    finally:
        state.exited.set()
        state.group_release.set()
        state.uid_release.set()
        state.stdout.eof.set()
        state.stderr.eof.set()
        tasks = [*state.owners, state.stdout.task, state.stderr.task]
        tasks = [task for task in tasks if task is not None]
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=1)


async def _ready(event):
    await asyncio.wait_for(event.wait(), timeout=1)


def _assert_settled(state):
    assert state.stdout.task.done()
    assert state.stderr.task.done()
    assert len(state.cleanup_tasks) == 2
    assert state.cleanup_tasks[0] is state.cleanup_tasks[1]
    assert state.cleanup_tasks[0].done()
    assert not state.cleanup_tasks[0].cancelled()
    assert state.calls.count(("uid", 10001)) == 1


@pytest.mark.parametrize("stage", ["running", "group", "uid"])
@pytest.mark.parametrize("cancellations", [1, 3])
async def test_command_cancellation_joins_cleanup_and_nonempty_readers(command_owner, stage, cancellations):
    state = command_owner
    gate = state.uid_release if stage == "uid" else state.group_release
    gate.clear()
    if stage != "running":
        state.exited.set()
    owner = state.start()
    await _ready(state.wait_started)
    await _ready(state.stdout.started)
    await _ready(state.stderr.started)
    if stage != "running":
        await _ready(state.uid_started if stage == "uid" else state.group_started)
    owner.cancel("original cancellation")
    await _ready(state.uid_started if stage == "uid" else state.group_started)
    for _ in range(cancellations - 1):
        owner.cancel("repeated cancellation")
        await asyncio.sleep(0)
    assert not owner.done()
    assert not state.stdout.task.done()
    assert not state.stderr.task.done()
    gate.set()
    with pytest.raises(asyncio.CancelledError, match="original cancellation"):
        await asyncio.wait_for(owner, timeout=1)
    assert state.uid_finished.is_set()
    assert state.calls == ["spawn", ("group", stage != "running"), ("uid", 10001)]
    _assert_settled(state)


async def test_command_cancellation_during_final_output_drain_joins_both_readers(command_owner):
    state = command_owner
    state.exited.set()
    owner = state.start()
    await _ready(state.uid_finished)
    assert not owner.done()
    owner.cancel("cancel output drain")
    with pytest.raises(asyncio.CancelledError, match="cancel output drain"):
        await asyncio.wait_for(owner, timeout=1)
    _assert_settled(state)


@pytest.mark.parametrize("failure", ["wait", "group", "uid", "stdout", "stderr"])
async def test_command_failure_harvests_both_readers_with_sanitized_error(command_owner, failure):
    state = command_owner
    error = OSError("synthetic private command/path/token detail")
    if failure in {"stdout", "stderr"}:
        getattr(state, failure).error = error
    else:
        setattr(state, f"{failure}_error", error)
    state.exited.set()
    owner = state.start()
    with pytest.raises(RuntimeError) as caught:
        await asyncio.wait_for(owner, timeout=1)
    expected = (
        "native_tool_process_cleanup_failed"
        if failure == "uid"
        else "native_tool_stage=post_spawn;class=failure"
    )
    assert str(caught.value) == expected
    assert caught.value.__context__ is None
    assert caught.value.__cause__ is None
    _assert_settled(state)


@pytest.mark.parametrize("failure", ["group", "uid"])
async def test_command_cancellation_remains_primary_after_cleanup_failure(command_owner, failure):
    state = command_owner
    setattr(state, f"{failure}_error", OSError("synthetic private cleanup detail"))
    state.uid_release.clear()
    owner = state.start()
    await _ready(state.wait_started)
    owner.cancel("original cancellation")
    await _ready(state.uid_started)
    owner.cancel("repeated cancellation")
    await asyncio.sleep(0)
    assert not owner.done()
    state.uid_release.set()
    with pytest.raises(asyncio.CancelledError, match="original cancellation"):
        await asyncio.wait_for(owner, timeout=1)
    _assert_settled(state)


@pytest.mark.parametrize("timed_out", [False, True])
async def test_command_success_and_timeout_preserve_bounded_output(command_owner, monkeypatch, timed_out):
    state = command_owner
    monkeypatch.setattr(native_tool_app, "NATIVE_TOOL_MAX_OUTPUT_BYTES", 32)
    state.stdout.data = b"x" * 40
    state.stderr.data = b"error\xff"
    state.stdout.eof.set()
    state.stderr.eof.set()
    if not timed_out:
        state.exited.set()
    result = await asyncio.wait_for(state.start(timeout_ms=1 if timed_out else 1000), timeout=1)
    assert result.model_dump() == {
        "returncode": 124 if timed_out else 7,
        "stdout": "x" * 32,
        "stderr": "error\ufffd",
        "output_truncated": True,
        "timed_out": timed_out,
    }
    assert state.calls == ["spawn", ("group", not timed_out), ("uid", 10001)]
    _assert_settled(state)


@pytest.mark.parametrize("disconnect", [False, True])
async def test_request_repeated_cancellation_retains_lock_until_command_cleanup(command_owner, monkeypatch, disconnect):
    state = command_owner
    state.uid_release.clear()
    token = "synthetic-internal-token-" * 2
    monkeypatch.setenv("AI_PLATFORM_NATIVE_TOOL_TOKEN", token)
    monkeypatch.setenv("AI_PLATFORM_NATIVE_TOOL_WORKSPACE", str(state.arguments["workspace"]))
    app = native_tool_app.create_native_tool_app()
    execute = next(route.endpoint for route in app.routes if route.path == "/execute")

    class Request:
        async def is_disconnected(self):
            await state.wait_started.wait()
            if disconnect:
                return True
            await asyncio.Event().wait()

    async def request():
        return await execute(native_tool_app.NativeToolRequest(command="synthetic"), Request(), token)

    first = asyncio.create_task(request())
    state.owners.append(first)
    await _ready(state.wait_started)
    if disconnect:
        await _ready(state.uid_started)
    first.cancel("original request cancellation")
    await _ready(state.uid_started)
    second = asyncio.create_task(request())
    state.owners.append(second)
    for _ in range(3):
        first.cancel("repeated request cancellation")
        await asyncio.sleep(0)
    assert not first.done()
    assert state.calls.count("spawn") == 1
    assert not second.done()
    second.cancel()
    with pytest.raises(asyncio.CancelledError):
        await second
    state.uid_release.set()
    with pytest.raises(asyncio.CancelledError, match="original request cancellation"):
        await asyncio.wait_for(first, timeout=1)
    _assert_settled(state)


async def test_disconnect_returns_499_only_after_cleanup_and_reader_settlement(command_owner):
    state = command_owner
    state.uid_release.clear()

    class Request:
        async def is_disconnected(self):
            await state.wait_started.wait()
            return True

    request = asyncio.create_task(native_tool_app._execute_with_disconnect_cancellation(
        http_request=Request(), timeout_ms=1000, **state.arguments,
    ))
    state.owners.append(request)
    await _ready(state.uid_started)
    assert not request.done()
    state.uid_release.set()
    with pytest.raises(HTTPException) as caught:
        await asyncio.wait_for(request, timeout=1)
    assert caught.value.status_code == 499
    assert caught.value.detail == "native_tool_client_disconnected"
    _assert_settled(state)


async def test_request_joins_disconnect_watcher_when_cancelled_during_its_close(command_owner):
    state = command_owner
    closing = asyncio.Event()
    release = asyncio.Event()
    closed = []

    class Request:
        async def is_disconnected(self):
            try:
                await asyncio.Event().wait()
            finally:
                closing.set()
                await release.wait()
                closed.append(True)

    request = asyncio.create_task(native_tool_app._execute_with_disconnect_cancellation(
        http_request=Request(), timeout_ms=1000, **state.arguments,
    ))
    state.owners.append(request)
    try:
        await _ready(state.wait_started)
        request.cancel("original request cancellation")
        await _ready(closing)
        await _ready(state.uid_finished)
        for _ in range(3):
            request.cancel("repeated request cancellation")
            await asyncio.sleep(0)
        assert not request.done()
        assert closed == []
        release.set()
        with pytest.raises(asyncio.CancelledError, match="original request cancellation"):
            await asyncio.wait_for(request, timeout=1)
        assert closed == [True]
        _assert_settled(state)
    finally:
        release.set()


@pytest.mark.parametrize("simultaneous", [False, True])
async def test_caller_cancellation_stays_primary_when_cleanup_is_directly_cancelled(command_owner, simultaneous):
    state = command_owner
    state.uid_release.clear()
    if simultaneous:
        state.exited.set()
    owner = state.start()
    await _ready(state.wait_started)
    if not simultaneous:
        owner.cancel("original caller cancellation")
    await _ready(state.uid_started)
    cleanup = state.cleanup_tasks[-1]
    cleanup.cancel("direct cleanup cancellation")
    if simultaneous:
        owner.cancel("original caller cancellation")
    with pytest.raises(asyncio.CancelledError, match="original caller cancellation"):
        await asyncio.wait_for(owner, timeout=1)
    assert owner.cancelling() == 1
    assert cleanup.cancelled()
    assert not state.uid_finished.is_set()
    assert state.stdout.task.done()
    assert state.stderr.task.done()


async def test_direct_cleanup_cancellation_propagates_without_caller_cancel_request(command_owner):
    state = command_owner
    state.exited.set()
    state.uid_release.clear()
    owner = state.start()
    await _ready(state.uid_started)
    cleanup = state.cleanup_tasks[-1]
    cleanup.cancel("direct cleanup cancellation")
    with pytest.raises(asyncio.CancelledError, match="direct cleanup cancellation"):
        await asyncio.wait_for(owner, timeout=1)
    assert owner.cancelling() == 0
    assert cleanup.cancelled()
    assert not state.uid_finished.is_set()
    assert state.stdout.task.done()
    assert state.stderr.task.done()
