import asyncio

import httpx
import pytest

from app.runtime.sandbox.contracts import ContainerLease, ExecutorCallbackEvent, ExecutorTaskRequest
from app.runtime.sandbox.event_normalizer import callback_event_to_run_events, container_started_event
from app.runtime.sandbox.executor_client import SandboxExecutorClient
from app.tool_permission_lifecycle import tool_permission_budget


def lease() -> ContainerLease:
    return ContainerLease(
        container_id="exec-run-a",
        container_name="executor-exec-run-a",
        provider="fake",
        executor_url="http://executor.test",
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        session_id="session-a",
        run_id="run-a",
        sandbox_mode="ephemeral",
        browser_enabled=False,
        workspace_host_path="C:/host/runtime/workspace-a",
    )


def test_container_started_event_is_admin_only_and_sanitized():
    event = container_started_event(lease())

    assert event.type == "runtime_container_started"
    assert event.admin_only is True
    assert event.payload == {
        "container_id": "exec-run-a",
        "container_name": "executor-exec-run-a",
        "provider": "fake",
        "sandbox_mode": "ephemeral",
        "browser_enabled": False,
    }
    assert "C:/host/runtime/workspace-a" not in str(event.payload)


def test_callback_running_new_message_maps_to_assistant_delta():
    callback = ExecutorCallbackEvent(
        session_id="session-a",
        run_id="run-a",
        callback_token_id="cbt_run-a",
        status="running",
        progress=20,
        new_message={"type": "assistant", "delta": "hello"},
        state_patch={},
    )

    events = callback_event_to_run_events(callback)

    assert len(events) == 1
    assert events[0].type == "assistant_delta"
    assert events[0].payload["delta"] == "hello"


def test_callback_current_step_maps_to_tool_call_delta():
    callback = ExecutorCallbackEvent(
        session_id="session-a",
        run_id="run-a",
        callback_token_id="cbt_run-a",
        status="running",
        progress=35,
        new_message=None,
        state_patch={"current_step": "reading workspace"},
    )

    events = callback_event_to_run_events(callback)

    assert len(events) == 1
    assert events[0].type == "tool_call_delta"
    assert events[0].payload["current_step"] == "reading workspace"


@pytest.mark.parametrize("status", ["completed", "failed", "cancelled"])
def test_callback_terminal_status_does_not_map_to_authoritative_run_event(status):
    callback = ExecutorCallbackEvent(
        session_id="session-a",
        run_id="run-a",
        callback_token_id="cbt_run-a",
        status=status,
        progress=100 if status == "completed" else 60,
        new_message=None,
        state_patch={},
        error_message="boom" if status == "failed" else None,
    )

    events = callback_event_to_run_events(callback)

    assert events == []


def test_callback_typed_events_are_appended_after_compatibility_events():
    callback = ExecutorCallbackEvent(
        session_id="session-a",
        run_id="run-a",
        callback_token_id="cbt_run-a",
        status="running",
        progress=45,
        new_message={"type": "assistant", "delta": "hello"},
        state_patch={},
        events=[
            {
                "type": "checkpoint_created",
                "message": "checkpoint saved",
                "payload": {"checkpoint_id": "checkpoint-a", "step_key": "code"},
            },
            {
                "type": "subagent_completed",
                "message": "reviewer completed",
                "payload": {"subagent_id": "reviewer-1", "step_key": "review"},
            },
        ],
    )

    events = callback_event_to_run_events(callback)

    assert [event.type for event in events] == [
        "assistant_delta",
        "checkpoint_created",
        "subagent_completed",
    ]
    assert events[1].payload["checkpoint_id"] == "checkpoint-a"
    assert events[2].payload["subagent_id"] == "reviewer-1"


@pytest.mark.asyncio
async def test_executor_client_posts_task_request(monkeypatch):
    calls = []

    async def post_json(url, payload, timeout, headers=None):
        calls.append((url, payload, timeout))
        return {"status": "accepted", "session_id": "session-a"}

    monkeypatch.setattr(
        "app.runtime.sandbox.executor_client.get_settings",
        lambda: type("S", (), {"claude_agent_sdk_timeout_seconds": 120.0})(),
    )
    client = SandboxExecutorClient(post_json=post_json)
    request = ExecutorTaskRequest(
        session_id="session-a",
        run_id="run-a",
        prompt="hello",
        callback_url="http://callback",
        callback_token_id="cbt_run-a",
        callback_token="secret",
        callback_base_url="http://callback-base",
        config={"model": "deepseek-v4-flash"},
    )

    result = await client.execute("http://executor.test", request)

    assert result == {"status": "accepted", "session_id": "session-a"}
    assert calls == [
        (
            "http://executor.test/v1/tasks/execute",
            request.model_dump(),
            tool_permission_budget(120.0).normal_outer_executor_timeout_seconds,
        )
    ]


@pytest.mark.asyncio
async def test_executor_client_uses_the_nested_outer_deadline_only_for_governed_permission_runs(monkeypatch):
    calls = []

    async def post_json(url, payload, timeout, headers=None):
        calls.append(timeout)
        return {"status": "accepted"}

    monkeypatch.setattr(
        "app.runtime.sandbox.executor_client.get_settings",
        lambda: type("S", (), {"claude_agent_sdk_timeout_seconds": 120.0})(),
    )
    request = ExecutorTaskRequest(
        session_id="session-a",
        run_id="run-a",
        prompt="hello",
        callback_url="http://callback",
        callback_token_id="cbt_run-a",
        callback_token="secret",
        callback_base_url="http://callback-base",
        governed_permission_wait=True,
    )

    await SandboxExecutorClient(post_json=post_json).execute("http://executor.test", request)

    assert calls == [tool_permission_budget(120.0).outer_executor_timeout_seconds]


@pytest.mark.asyncio
async def test_executor_client_connects_to_pinned_ip_without_transmitting_private_metadata():
    calls = []

    async def post_json(url, payload, timeout, headers=None):
        calls.append((url, dict(headers or {})))
        return {"status": "accepted"}

    client = SandboxExecutorClient(post_json=post_json, timeout_seconds=3.0)
    request = ExecutorTaskRequest(
        session_id="session-a",
        run_id="run-a",
        prompt="hello",
        callback_url="http://callback",
        callback_token_id="cbt_run-a",
        callback_token="secret",
        callback_base_url="http://callback-base",
        config={"model": "deepseek-v4-flash"},
    )
    private_metadata_key = "X-AI-Platform-Internal-Executor-Connect-Base-Url"

    await client.execute(
        "http://host.docker.internal:43123",
        request,
        executor_headers={
            "X-AI-Platform-Executor-Credential": "executor-secret",
            private_metadata_key: "http://172.17.0.1:43123",
        },
    )

    assert calls == [
        (
            "http://172.17.0.1:43123/v1/tasks/execute",
            {
                "X-AI-Platform-Executor-Credential": "executor-secret",
                "Host": "host.docker.internal:43123",
            },
        )
    ]
    assert private_metadata_key not in calls[0][1]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("logical_url", "connect_base_url"),
    [
        ("https://host.docker.internal:43123", "https://172.17.0.1:43123"),
        ("http://user@host.docker.internal:43123", "http://172.17.0.1:43123"),
        ("http://host.docker.internal:43123", "http://8.8.8.8:43123"),
        ("http://host.docker.internal:43123", "http://0.0.0.0:43123"),
    ],
)
async def test_executor_client_rejects_unsafe_private_connect_metadata_without_dispatch(
    logical_url,
    connect_base_url,
):
    calls = []

    async def post_json(*args, **kwargs):
        calls.append((args, kwargs))
        return {"status": "accepted"}

    client = SandboxExecutorClient(post_json=post_json, timeout_seconds=3.0)
    request = ExecutorTaskRequest(
        session_id="session-a",
        run_id="run-a",
        prompt="hello",
        callback_url="http://callback",
        callback_token_id="cbt_run-a",
        callback_token="secret",
        callback_base_url="http://callback-base",
        config={"model": "deepseek-v4-flash"},
    )

    with pytest.raises(ValueError, match="invalid executor connect metadata"):
        await client.execute(
            logical_url,
            request,
            executor_headers={
                "X-AI-Platform-Executor-Credential": "executor-secret",
                "X-AI-Platform-Internal-Executor-Connect-Base-Url": connect_base_url,
            },
        )

    assert calls == []


@pytest.mark.asyncio
async def test_executor_client_allows_explicit_timeout_override():
    calls = []

    async def post_json(url, payload, timeout, headers=None):
        calls.append(timeout)
        return {"status": "accepted"}

    client = SandboxExecutorClient(post_json=post_json, timeout_seconds=3.0)
    request = ExecutorTaskRequest(
        session_id="session-a",
        run_id="run-a",
        prompt="hello",
        callback_url="http://callback",
        callback_token_id="cbt_run-a",
        callback_token="secret",
        callback_base_url="http://callback-base",
        config={"model": "deepseek-v4-flash"},
    )

    await client.execute("http://executor.test", request)

    assert calls == [3.0]


@pytest.mark.asyncio
async def test_executor_client_deadline_and_cancellation_never_return_an_accepted_result():
    request = ExecutorTaskRequest(
        session_id="session-a",
        run_id="run-a",
        prompt="hello",
        callback_url="http://callback",
        callback_token_id="cbt_run-a",
        callback_token="secret",
        callback_base_url="http://callback-base",
        config={"model": "deepseek-v4-flash"},
    )

    async def deadline_post_json(*args, **kwargs):
        raise httpx.TimeoutException("executor deadline elapsed")

    async def cancelled_post_json(*args, **kwargs):
        raise asyncio.CancelledError()

    with pytest.raises(httpx.TimeoutException):
        await SandboxExecutorClient(post_json=deadline_post_json).execute("http://executor.test", request)
    with pytest.raises(asyncio.CancelledError):
        await SandboxExecutorClient(post_json=cancelled_post_json).execute("http://executor.test", request)
