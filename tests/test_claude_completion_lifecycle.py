"""Completion ordering using the installed SDK Query, without a provider."""

import asyncio
import sys
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from claude_agent_sdk._internal import transcript_mirror_batcher
from claude_agent_sdk._internal.query import Query
from claude_agent_sdk._internal.transcript_mirror_batcher import TranscriptMirrorBatcher

from app.executors.claude.client_lifecycle import ClaudeClientCloseBoundary
from app.executors.claude_agent_sdk_runner import run_claude_agent_sdk
from tests.test_claude_agent_sdk_runner import _fake_sdk, _settings


class ClosingTransport:
    def __init__(self):
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.closed = asyncio.Event()
        self.error = False

    async def close(self):
        self.started.set()
        try:
            await self.release.wait()
            if self.error:
                raise RuntimeError("synthetic cleanup failure")
        finally:
            self.closed.set()


@pytest.mark.asyncio
async def test_installed_query_joins_reader_and_control_callbacks_before_completion():
    transport = ClosingTransport()
    query = Query(transport, is_streaming_mode=True)
    control_started, control_drained = asyncio.Event(), asyncio.Event()
    release_control, protocol_closed = asyncio.Event(), asyncio.Event()
    reader_drained = asyncio.Event()
    owner = asyncio.current_task()

    async def control():
        control_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            await release_control.wait()
            control_drained.set()

    async def reader():
        try:
            await asyncio.Event().wait()
        finally:
            reader_drained.set()

    from claude_agent_sdk._internal._task_compat import spawn_detached

    query.spawn_task(control())
    query._read_task = spawn_detached(reader())
    await control_started.wait()

    async def disconnect():
        await query.close()
        query.close_receive_stream()

    client = SimpleNamespace(_query=query, disconnect=disconnect)

    async def completed(failed):
        assert not failed
        assert reader_drained.is_set() and control_drained.is_set()
        assert asyncio.current_task() is cleanup
        assert asyncio.current_task() is not owner
        protocol_closed.set()

    boundary = ClaudeClientCloseBoundary(client, completed)
    boundary.bind()
    cleanup = asyncio.create_task(boundary.disconnect())
    try:
        await reader_drained.wait()
        assert not protocol_closed.is_set()
        release_control.set()
        await asyncio.wait_for(protocol_closed.wait(), 1)
        assert not cleanup.done()
    finally:
        release_control.set()
        transport.release.set()
        await cleanup


@pytest.mark.asyncio
@pytest.mark.parametrize("tail_failure", [False, True])
@pytest.mark.parametrize("cleanup_failure", [False, True])
async def test_runner_completes_after_final_mirror_before_slow_teardown(
    monkeypatch, tmp_path, tail_failure, cleanup_failure
):
    captured = {}
    sdk = _fake_sdk(captured, hook_invocations=[])
    transport = ClosingTransport()
    transport.error = cleanup_failure
    tail_started, release_tail = asyncio.Event(), asyncio.Event()
    mcp_closed = asyncio.Event()
    cleanup_tasks = set()
    lifecycle_owner = []

    class Store:
        accepted_final_sequence = None

        async def load(self, _key):
            return None

        async def append(self, _key, entries):
            if entries[0]["uuid"] == "tail":
                tail_started.set()
                await release_tail.wait()
                if tail_failure:
                    raise ValueError("synthetic final append failure")
                self.accepted_final_sequence = 2
            else:
                self.accepted_final_sequence = 1

    class Client(sdk.ClaudeSDKClient):
        async def connect(self):
            lifecycle_owner.append(asyncio.current_task())
            await super().connect()
            self._query = Query(transport, is_streaming_mode=True)

            async def mirror_error(_key, _error):
                pass  # SDK reports an event after the consumer's Result.

            self._query.set_transcript_mirror_batcher(TranscriptMirrorBatcher(
                store=self.options.session_store,
                projects_dir=str(tmp_path),
                on_error=mirror_error,
            ))

        async def disconnect(self):
            assert asyncio.current_task() is lifecycle_owner[0]
            self._query._transcript_mirror_batcher.enqueue(
                str(tmp_path / "project" / "stable-provider-id.jsonl"),
                [{"uuid": "tail"}],
            )
            try:
                await self._query.close()
            finally:
                self._query.close_receive_stream()
                await super().disconnect()

    @asynccontextmanager
    async def activate(_options):
        try:
            yield
        finally:
            assert transport.closed.is_set()
            assert asyncio.current_task() is lifecycle_owner[0]
            mcp_closed.set()

    monkeypatch.setitem(sys.modules, "claude_agent_sdk", sdk)
    monkeypatch.setattr("app.executors.claude_agent_sdk_runner.get_settings", _settings)
    monkeypatch.setattr("app.executors.claude_agent_sdk_runner.prepare_claude_mcp", lambda *_: SimpleNamespace(
        configs={}, aliases={}, sdk_names={}, activate=activate, check_message=lambda _: None,
        canonical_identity=lambda value: value,
    ))
    monkeypatch.setattr(transcript_mirror_batcher, "MIRROR_APPEND_BACKOFF_S", [0, 0])
    task = asyncio.create_task(run_claude_agent_sdk(
        prompt="hello", cwd=tmp_path, skill_id=None,
        session_id="stable-provider-id", session_store=Store(),
        provider_session_resume_required=False,
        client_fn=Client, cleanup_tasks=cleanup_tasks,
    ))
    try:
        await asyncio.wait_for(tail_started.wait(), 1)
        assert not task.done() and not transport.started.is_set()
        release_tail.set()
        result = await asyncio.wait_for(task, 1)
        assert result.error == ("claude_agent_sdk_provider_session_failed" if tail_failure else None)
        assert result.provider_final_sequence == (None if tail_failure else 2)
        assert not mcp_closed.is_set()
        assert not transport.closed.is_set()
        assert len(cleanup_tasks) == 1
    finally:
        release_tail.set()
        transport.release.set()
        await asyncio.gather(task, *list(cleanup_tasks), return_exceptions=True)
    assert mcp_closed.is_set()
    assert not cleanup_tasks


@pytest.mark.asyncio
async def test_runner_execution_deadline_does_not_include_resource_cleanup(monkeypatch, tmp_path):
    captured = {}
    sdk = _fake_sdk(captured, hook_invocations=[])
    transport = ClosingTransport()
    cleanup_tasks = set()

    class Client(sdk.ClaudeSDKClient):
        async def connect(self):
            await super().connect()
            self._query = Query(transport, is_streaming_mode=True)

        async def disconnect(self):
            try:
                await self._query.close()
            finally:
                self._query.close_receive_stream()

    settings = _settings()
    settings.claude_agent_sdk_timeout_seconds = 0.02
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", sdk)
    monkeypatch.setattr("app.executors.claude_agent_sdk_runner.get_settings", lambda: settings)
    try:
        result = await run_claude_agent_sdk(
            prompt="hello", cwd=tmp_path, skill_id=None,
            client_fn=Client, cleanup_tasks=cleanup_tasks,
        )
        # Let the old execution deadline pass while physical close is blocked.
        await asyncio.sleep(0.04)
        assert result.error is None and result.message == "done"
        assert len(cleanup_tasks) == 1
        assert not transport.closed.is_set()
    finally:
        transport.release.set()
        await asyncio.gather(*list(cleanup_tasks), return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal_frame", ["notification", "updated"])
async def test_background_agent_defers_first_result_until_followup(monkeypatch, tmp_path, terminal_frame):
    sdk = _fake_sdk({}, hook_invocations=[])
    first_result, release_task = asyncio.Event(), asyncio.Event()

    class Started:
        task_id = "agent-task"
        task_type = "local_agent"

    class Notification:
        task_id = "agent-task"

    class Updated:
        task_id = "agent-task"
        patch = {"status": "completed"}

    sdk.TaskStartedMessage = Started
    sdk.TaskNotificationMessage = Notification
    sdk.TaskUpdatedMessage = Updated

    class Client(sdk.ClaudeSDKClient):
        async def receive_messages(self):
            yield Started()
            async for message in super().receive_messages():
                yield message
            first_result.set()
            await release_task.wait()
            yield Notification() if terminal_frame == "notification" else Updated()
            yield sdk.ResultMessage()

    monkeypatch.setitem(sys.modules, "claude_agent_sdk", sdk)
    monkeypatch.setattr("app.executors.claude_agent_sdk_runner.get_settings", _settings)
    task = asyncio.create_task(run_claude_agent_sdk(
        prompt="delegate", cwd=tmp_path, skill_id=None, client_fn=Client,
    ))
    try:
        await asyncio.wait_for(first_result.wait(), 1)
        assert not task.done()
        release_task.set()
        result = await task
        assert result.error is None and result.message == "done"
    finally:
        release_task.set()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_installed_client_keeps_native_resume_and_materialized_cleanup(monkeypatch):
    import claude_agent_sdk as sdk
    from claude_agent_sdk._internal import session_resume

    transport = ClosingTransport()
    materialized_cleanup = asyncio.Event()
    resumed, ready = [], []

    async def cleanup():
        materialized_cleanup.set()

    async def materialize(options):
        resumed.append(options.resume)
        return SimpleNamespace(cleanup=cleanup)

    async def connect_inner(client, _prompt, _actual_prompt):
        assert client._custom_transport is None
        client._query = Query(transport, is_streaming_mode=True)
        client._transport = transport

    async def completed(failed):
        ready.append(failed)

    monkeypatch.setattr(session_resume, "materialize_resume_session", materialize)
    monkeypatch.setattr(sdk.ClaudeSDKClient, "_connect_inner", connect_inner)
    client = sdk.ClaudeSDKClient(sdk.ClaudeAgentOptions(resume="native-session"))
    await client.connect()
    boundary = ClaudeClientCloseBoundary(client, completed)
    boundary.bind()
    task = asyncio.create_task(boundary.disconnect())
    try:
        await asyncio.wait_for(transport.started.wait(), 1)
        assert resumed == ["native-session"] and ready == [False]
        assert not materialized_cleanup.is_set()
    finally:
        transport.release.set()
        await task
    assert materialized_cleanup.is_set()


@pytest.mark.asyncio
async def test_failed_child_does_not_abandon_other_control_callbacks():
    transport = ClosingTransport()
    transport.release.set()
    query = Query(transport, is_streaming_mode=True)
    slow_started, release_slow, quiet = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def failing():
        try:
            await asyncio.Event().wait()
        finally:
            raise RuntimeError("synthetic control failure")

    async def slow():
        slow_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            await release_slow.wait()
            quiet.set()

    query.spawn_task(failing())
    query.spawn_task(slow())
    await slow_started.wait()

    async def disconnect():
        try:
            await query.close()
        finally:
            query.close_receive_stream()

    async def completed(_failed):
        raise AssertionError("a failed control callback cannot announce completion")

    boundary = ClaudeClientCloseBoundary(SimpleNamespace(_query=query, disconnect=disconnect), completed)
    boundary.bind()
    task = asyncio.create_task(boundary.disconnect())
    try:
        await asyncio.sleep(0.01)
        assert not task.done() and not transport.started.is_set()
        release_slow.set()
        with pytest.raises(RuntimeError, match="synthetic control failure"):
            await task
        assert quiet.is_set()
    finally:
        release_slow.set()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_eof_without_result_preserves_missing_terminal_before_teardown(monkeypatch, tmp_path):
    sdk = _fake_sdk({}, hook_invocations=[])
    transport = ClosingTransport()
    cleanup_tasks = set()

    class Client(sdk.ClaudeSDKClient):
        async def connect(self):
            await super().connect()
            self._query = Query(transport, is_streaming_mode=True)

        async def receive_messages(self):
            async for message in super().receive_messages():
                if not isinstance(message, sdk.ResultMessage):
                    yield message

        async def disconnect(self):
            try:
                await self._query.close()
            finally:
                self._query.close_receive_stream()

    monkeypatch.setitem(sys.modules, "claude_agent_sdk", sdk)
    monkeypatch.setattr("app.executors.claude_agent_sdk_runner.get_settings", _settings)
    try:
        result = await asyncio.wait_for(run_claude_agent_sdk(
            prompt="hello", cwd=tmp_path, skill_id=None,
            client_fn=Client, cleanup_tasks=cleanup_tasks,
        ), 1)
        assert result.error == "claude_agent_sdk_missing_structured_terminal"
        assert not transport.closed.is_set()
    finally:
        transport.release.set()
        await asyncio.gather(*list(cleanup_tasks), return_exceptions=True)


@pytest.mark.asyncio
async def test_executor_sends_terminal_sequence_before_owned_cleanup_finishes(monkeypatch, tmp_path):
    from app.runtime.sandbox import executor_app
    from app.runtime.sandbox.contracts import ExecutorTaskRequest
    from tests.test_sandbox_executor_app import (
        EXECUTOR_AUTH_TOKEN, TRUSTED_CALLBACK_BASE_URL, callback_ack, task_payload,
    )

    release, closed, terminal_sent = asyncio.Event(), asyncio.Event(), asyncio.Event()
    terminals = []

    async def physical_cleanup():
        await release.wait()
        closed.set()

    async def runner(_request, _root, _emit, *, callback_sender, sdk_cleanup_tasks):
        cleanup = asyncio.create_task(physical_cleanup())
        sdk_cleanup_tasks.add(cleanup)
        cleanup.add_done_callback(sdk_cleanup_tasks.discard)
        return {"status": "completed", "message": "done", "provider_session_final_sequence": 7}

    async def send(_url, payload, _token):
        if payload.get("status") == "completed":
            terminals.append(payload)
            terminal_sent.set()
        return callback_ack(payload)

    monkeypatch.setattr(executor_app, "_default_executor_runner", runner)
    app = executor_app.create_executor_app(
        workspace_root=tmp_path, callback_sender=send,
        executor_auth_token=EXECUTOR_AUTH_TOKEN,
        expected_session_id="session-a", expected_run_id="run-a", expected_attempt_id="qat-attempt-a",
        trusted_callback_base_url=TRUSTED_CALLBACK_BASE_URL,
    )
    lifespan = app.router.lifespan_context(app)
    await lifespan.__aenter__()
    shutdown = None
    try:
        dispatch = next(route.endpoint for route in app.routes if route.path == "/v2/tasks")
        await dispatch(ExecutorTaskRequest.model_validate(task_payload()), executor_credential=EXECUTOR_AUTH_TOKEN)
        await asyncio.wait_for(terminal_sent.wait(), 1)
        assert terminals[0]["terminal_result"]["provider_session_final_sequence"] == 7
        assert not closed.is_set()
        shutdown = asyncio.create_task(lifespan.__aexit__(None, None, None))
        await asyncio.sleep(0.01)
        assert not shutdown.done()
        release.set()
        await shutdown
        assert closed.is_set() and len(terminals) == 1
    finally:
        release.set()
        if shutdown is None:
            await lifespan.__aexit__(None, None, None)
        else:
            await shutdown
