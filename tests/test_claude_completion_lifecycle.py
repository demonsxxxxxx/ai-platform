"""Completion ordering using the installed SDK Query, without a provider."""

import asyncio
import hashlib
import sys
from types import SimpleNamespace

import pytest
from claude_agent_sdk._internal import transcript_mirror_batcher
from claude_agent_sdk._internal.query import Query
from claude_agent_sdk._internal.transcript_mirror_batcher import TranscriptMirrorBatcher
from claude_agent_sdk import ProcessError

from app.execution.infrastructure.harness.claude_client_lifecycle import ClaudeClientCloseBoundary
from app.executors.claude_agent_sdk_runner import run_claude_agent_sdk
from tests.test_claude_agent_sdk_runner import _fake_sdk, _settings
from tests.support.model_text import response_events


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


@pytest.mark.parametrize("error_result", [True, False])
async def test_installed_query_error_exit_keeps_error_result_and_final_flush(
    monkeypatch, tmp_path, error_result,
):
    import claude_agent_sdk as sdk

    written, tail_started, release_tail = asyncio.Event(), asyncio.Event(), asyncio.Event()
    transport = ClosingTransport()
    cleanup_tasks = set()

    async def write(_data):
        written.set()

    async def end_input():
        pass

    async def read_messages():
        await written.wait()
        path = str(tmp_path / "project" / "stable-provider-id.jsonl")
        yield {"type": "transcript_mirror", "filePath": path, "entries": [{"uuid": "initial"}]}
        yield {
            "type": "result", "subtype": "error_max_turns" if error_result else "success",
            "is_error": error_result, "duration_ms": 1, "duration_api_ms": 1, "num_turns": 1,
            "session_id": "stable-provider-id", "result": "done", "usage": {"input_tokens": 11},
            "errors": ["Reached maximum number of turns (1)"] if error_result else [],
            "stop_reason": "max_turns" if error_result else "end_turn", "uuid": "result",
        }
        yield {"type": "transcript_mirror", "filePath": path, "entries": [{"uuid": "tail"}]}
        raise ProcessError("synthetic CLI exit", exit_code=1)

    transport.write, transport.end_input, transport.read_messages = write, end_input, read_messages

    class Store:
        accepted_final_sequence = None

        async def load(self, _key):
            return None

        async def append(self, _key, entries):
            if entries[0]["uuid"] == "tail":
                tail_started.set()
                await release_tail.wait()
                self.accepted_final_sequence = 2
            else:
                self.accepted_final_sequence = 1

    async def connect_inner(client, _prompt, actual_prompt):
        client._transport = transport
        query = Query(transport, is_streaming_mode=True,
                      hooks=client._convert_hooks_to_internal_format(client.options.hooks))
        client._query = query

        async def mirror_error(_key, _error):
            pass

        query.set_transcript_mirror_batcher(TranscriptMirrorBatcher(
            store=client.options.session_store, projects_dir=str(tmp_path), on_error=mirror_error,
        ))
        await query.start()
        query.spawn_task(query.stream_input(actual_prompt))

    monkeypatch.setattr(sdk.ClaudeSDKClient, "_connect_inner", connect_inner)
    monkeypatch.setattr("app.executors.claude_agent_sdk_runner.get_settings", _settings)
    task = asyncio.create_task(run_claude_agent_sdk(
        prompt="hello", cwd=tmp_path, skill_id=None, session_id="stable-provider-id",
        session_store=Store(), provider_session_resume_required=False, cleanup_tasks=cleanup_tasks,
    ))
    try:
        await asyncio.wait_for(tail_started.wait(), 2)
        if error_result:
            assert not task.done()
        release_tail.set()
        transport.release.set()
        result = await asyncio.wait_for(task, 2)
        if error_result:
            assert result.error == "claude_agent_sdk_turn_limit_exceeded"
            assert result.session_id == "stable-provider-id" and result.usage == {"input_tokens": 11}
            assert result.runtime_diagnostics["failure_source"] == "sdk_result_error"
        else:
            assert result.error == "claude_agent_sdk_execution_failed"
    finally:
        release_tail.set()
        transport.release.set()
        await asyncio.gather(task, *list(cleanup_tasks), return_exceptions=True)


@pytest.mark.parametrize("stop_mode", ["cancel", "timeout"])
async def test_execution_stop_transfers_slow_public_disconnect_to_cleanup_owner(
    monkeypatch, tmp_path, stop_mode,
):
    sdk = _fake_sdk({}, hook_invocations=[])
    transport = ClosingTransport()
    started, interrupted = asyncio.Event(), asyncio.Event()
    cleanup_tasks = set()

    class Client(sdk.ClaudeSDKClient):
        async def connect(self, prompt):
            await super().connect(prompt)
            self._query = Query(transport, is_streaming_mode=True)

        async def receive_messages(self):
            started.set()
            await asyncio.Event().wait()
            yield sdk.ResultMessage()

        async def interrupt(self):
            interrupted.set()

        async def disconnect(self):
            try:
                await self._query.close()
            finally:
                self._query.close_receive_stream()
                await super().disconnect()

    settings = _settings()
    settings.claude_agent_sdk_timeout_seconds = 0.03 if stop_mode == "timeout" else 10
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", sdk)
    monkeypatch.setattr("app.executors.claude_agent_sdk_runner.get_settings", lambda: settings)
    task = asyncio.create_task(run_claude_agent_sdk(
        prompt="hello", cwd=tmp_path, skill_id=None, client_fn=Client, cleanup_tasks=cleanup_tasks,
    ))
    try:
        await asyncio.wait_for(started.wait(), 1)
        if stop_mode == "cancel":
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 1)
        else:
            result = await asyncio.wait_for(task, 1)
            assert result.error == "claude_agent_sdk_timeout"
        await asyncio.wait_for(transport.started.wait(), 1)
        assert interrupted.is_set() and not transport.closed.is_set() and len(cleanup_tasks) == 1
    finally:
        transport.release.set()
        await asyncio.gather(task, *list(cleanup_tasks), return_exceptions=True)
    assert transport.closed.is_set() and not cleanup_tasks




@pytest.mark.parametrize("stop_mode", ["cancel", "timeout"])
@pytest.mark.parametrize("forced_close", [False, True])
async def test_stop_settles_pending_session_store_tail_before_terminal_result(
    monkeypatch, tmp_path, stop_mode, forced_close,
):
    import claude_agent_sdk as sdk

    written, receiving, interrupted = asyncio.Event(), asyncio.Event(), asyncio.Event()
    tail_started, release_tail = asyncio.Event(), asyncio.Event()
    transport = ClosingTransport()
    cleanup_tasks = set()
    checkpoints, public_chunks = [], []
    before_text = "Visible prefix. " * 1024
    tail_text = "Suppressed drain tail. " * 512
    before_published = asyncio.Event()
    events = response_events("drained-model-response", [before_text, tail_text])
    events.insert(3, {"type": "ping"})

    async def on_text(text):
        public_chunks.append(text)
        before_published.set()

    def stream_event(index, event):
        return {
            "type": "stream_event", "event": event, "uuid": f"drain-{index}",
            "session_id": "stable-provider-id", "parent_tool_use_id": None,
        }

    async def write(_data):
        written.set()

    async def end_input():
        pass

    async def read_messages():
        await written.wait()
        path = str(tmp_path / "project" / "stable-provider-id.jsonl")
        yield {"type": "transcript_mirror", "filePath": path, "entries": [{"uuid": "initial"}]}
        if not forced_close:
            for index, event in enumerate(events[:4]):
                yield stream_event(index, event)
            receiving.set()
            await interrupted.wait()
            for index, event in enumerate(events[4:], 4):
                yield stream_event(index, event)
        yield {"type": "transcript_mirror", "filePath": path, "entries": [{"uuid": "tail"}]}
        if forced_close:
            receiving.set()
            await asyncio.Event().wait()

    transport.write, transport.end_input, transport.read_messages = write, end_input, read_messages

    class Store:
        accepted_final_sequence = None

        def __init__(self):
            self.entries = []

        async def load(self, _key):
            return None

        async def append(self, _key, entries):
            if any(entry["uuid"] == "tail" for entry in entries):
                tail_started.set()
                await release_tail.wait()
                self.accepted_final_sequence = 2
            else:
                self.accepted_final_sequence = 1
            self.entries.extend(entry["uuid"] for entry in entries)

    store = Store()

    async def connect_inner(client, _prompt, actual_prompt):
        client._transport = transport
        query = Query(transport, is_streaming_mode=True,
                      hooks=client._convert_hooks_to_internal_format(client.options.hooks))
        client._query = query

        async def mirror_error(_key, _error):
            pass

        query.set_transcript_mirror_batcher(TranscriptMirrorBatcher(
            store=client.options.session_store, projects_dir=str(tmp_path), on_error=mirror_error,
        ))
        await query.start()
        query.spawn_task(query.stream_input(actual_prompt))

    async def interrupt(_client):
        interrupted.set()

    settings = _settings()
    settings.claude_agent_sdk_timeout_seconds = 0.03 if stop_mode == "timeout" else 10
    monkeypatch.setattr(sdk.ClaudeSDKClient, "_connect_inner", connect_inner)
    monkeypatch.setattr(sdk.ClaudeSDKClient, "interrupt", interrupt)
    monkeypatch.setattr("app.executors.claude_agent_sdk_runner.get_settings", lambda: settings)
    monkeypatch.setattr("app.executors.claude_agent_sdk_runner._SDK_CLEANUP_TIMEOUT_SECONDS", 0.1)
    task = asyncio.create_task(run_claude_agent_sdk(
        prompt="hello", cwd=tmp_path, skill_id=None, session_id="stable-provider-id",
        session_store=store, provider_session_resume_required=False, cleanup_tasks=cleanup_tasks,
        run_id="run", attempt_id="attempt", on_sdk_text=checkpoints.append, on_text=on_text,
        execution_policy="sandbox_brokered",
    ))
    try:
        await asyncio.wait_for(receiving.wait(), 1)
        if not forced_close:
            await asyncio.wait_for(before_published.wait(), 1)
        if stop_mode == "cancel":
            task.cancel()
        await asyncio.wait_for(tail_started.wait(), 1)
        assert interrupted.is_set() and not task.done() and not transport.started.is_set()
        release_tail.set()
        await asyncio.wait_for(transport.started.wait(), 1)
        assert "tail" in store.entries
        if forced_close:
            # Without public EOF the SDK exposes flush and teardown only as a
            # combined disconnect. Do not claim settlement before it finishes.
            assert not task.done()
            transport.release.set()
        if stop_mode == "cancel":
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 1)
        else:
            result = await asyncio.wait_for(task, 1)
            assert result.error == "claude_agent_sdk_timeout"
        if not forced_close:
            assert not transport.closed.is_set() and len(cleanup_tasks) == 1
            assert [item["events"] for item in checkpoints] == [1, 2]
            final = checkpoints[-1]
            assert final["final"] is True and final["complete"] is True
            assert final["coverage"] == "text_delta"
            assert final["chars"] == len(before_text + tail_text)
            assert final["sha256"] == hashlib.sha256((before_text + tail_text).encode()).hexdigest()
            assert len(final["call_ref"]) == 32
            public_text = "".join(public_chunks)
            assert before_text[:100] in public_text
            assert "Suppressed drain tail." not in public_text
    finally:
        release_tail.set()
        transport.release.set()
        await asyncio.gather(task, *list(cleanup_tasks), return_exceptions=True)
    assert transport.closed.is_set() and not cleanup_tasks


@pytest.mark.asyncio
@pytest.mark.parametrize("tail_failure", [False, True])
@pytest.mark.parametrize("cleanup_failure", [False, True])
async def test_runner_completes_after_final_mirror_before_slow_teardown(
    monkeypatch, tmp_path, tail_failure, cleanup_failure
):
    import claude_agent_sdk as sdk

    transport = ClosingTransport()
    transport.error = cleanup_failure
    written, input_ended = asyncio.Event(), asyncio.Event()
    tail_started, release_tail = asyncio.Event(), asyncio.Event()
    cleanup_tasks = set()

    async def write(_data):
        written.set()

    async def end_input():
        input_ended.set()

    async def read_messages():
        await written.wait()
        path = str(tmp_path / "project" / "stable-provider-id.jsonl")
        yield {"type": "transcript_mirror", "filePath": path, "entries": [{"uuid": "initial"}]}
        yield {
            "type": "result", "subtype": "success", "is_error": False,
            "duration_ms": 1, "duration_api_ms": 1, "num_turns": 1,
            "session_id": "stable-provider-id", "result": "done", "usage": {},
            "stop_reason": "end_turn", "uuid": "result",
        }
        yield {"type": "transcript_mirror", "filePath": path, "entries": [{"uuid": "tail"}]}
        await input_ended.wait()

    transport.write, transport.end_input, transport.read_messages = write, end_input, read_messages

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

    async def connect_inner(client, _prompt, actual_prompt):
        assert client._custom_transport is None
        client._transport = transport
        query = Query(transport, is_streaming_mode=True,
                      hooks=client._convert_hooks_to_internal_format(client.options.hooks))
        client._query = query

        async def mirror_error(_key, _error):
            pass

        query.set_transcript_mirror_batcher(TranscriptMirrorBatcher(
            store=client.options.session_store, projects_dir=str(tmp_path), on_error=mirror_error,
        ))
        await query.start()
        query.spawn_task(query.stream_input(actual_prompt))

    monkeypatch.setattr(sdk.ClaudeSDKClient, "_connect_inner", connect_inner)
    monkeypatch.setattr("app.executors.claude_agent_sdk_runner.get_settings", _settings)
    monkeypatch.setattr(transcript_mirror_batcher, "MIRROR_APPEND_BACKOFF_S", [0, 0])
    task = asyncio.create_task(run_claude_agent_sdk(
        prompt="hello", cwd=tmp_path, skill_id=None, session_id="stable-provider-id",
        session_store=Store(), provider_session_resume_required=False,
        cleanup_tasks=cleanup_tasks,
    ))
    try:
        await asyncio.wait_for(tail_started.wait(), 2)
        assert not task.done() and not transport.started.is_set()
        release_tail.set()
        result = await asyncio.wait_for(task, 2)
        assert result.error == ("claude_agent_sdk_provider_session_failed" if tail_failure else None)
        assert result.provider_final_sequence == (None if tail_failure else 2)
        assert not transport.closed.is_set() and len(cleanup_tasks) == 1
    finally:
        release_tail.set()
        transport.release.set()
        await asyncio.gather(task, *list(cleanup_tasks), return_exceptions=True)
    assert transport.closed.is_set() and not cleanup_tasks


@pytest.mark.asyncio
async def test_runner_execution_deadline_does_not_include_resource_cleanup(monkeypatch, tmp_path):
    captured = {}
    sdk = _fake_sdk(captured, hook_invocations=[])
    transport = ClosingTransport()
    cleanup_tasks = set()

    class Client(sdk.ClaudeSDKClient):
        async def connect(self, prompt):
            await super().connect(prompt)
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
    resumed = []

    async def cleanup():
        materialized_cleanup.set()

    async def materialize(options):
        resumed.append(options.resume)
        return SimpleNamespace(cleanup=cleanup)

    async def connect_inner(client, _prompt, _actual_prompt):
        assert client._custom_transport is None
        client._query = Query(transport, is_streaming_mode=True)
        client._transport = transport

    monkeypatch.setattr(session_resume, "materialize_resume_session", materialize)
    monkeypatch.setattr(sdk.ClaudeSDKClient, "_connect_inner", connect_inner)
    client = sdk.ClaudeSDKClient(sdk.ClaudeAgentOptions(resume="native-session"))
    await client.connect()
    boundary = ClaudeClientCloseBoundary(client)
    boundary.bind()
    task = asyncio.create_task(boundary.disconnect())
    try:
        await asyncio.wait_for(transport.started.wait(), 1)
        assert resumed == ["native-session"]
        assert not materialized_cleanup.is_set()
    finally:
        transport.release.set()
        await task
    assert materialized_cleanup.is_set()




@pytest.mark.asyncio
async def test_eof_without_result_preserves_missing_terminal_before_teardown(monkeypatch, tmp_path):
    sdk = _fake_sdk({}, hook_invocations=[])
    transport = ClosingTransport()
    cleanup_tasks = set()

    class Client(sdk.ClaudeSDKClient):
        async def connect(self, prompt):
            await super().connect(prompt)
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
