import asyncio

import httpx
import pytest

import app.runtime.sandbox.executor_client as executor_client_module
from app.runtime.sandbox.contracts import ContainerLease, ExecutorCallbackEvent, ExecutorTaskRequest
from app.runtime.sandbox.event_normalizer import callback_event_to_run_events, container_started_event
from app.runtime.sandbox.executor_client import SandboxExecutorClient, SandboxExecutorHttpError


def callback_event(**kwargs) -> ExecutorCallbackEvent:
    status = kwargs.get("status")
    if status in {"completed", "failed", "cancelled"}:
        terminal_result = {
            "status": status,
            "run_id": kwargs["run_id"],
            "message": "completed" if status == "completed" else "",
        }
        if status != "completed":
            terminal_result.update(
                error_code="executor_failed",
                error_message=f"executor {status}",
            )
        kwargs.setdefault("terminal_result", terminal_result)
    return ExecutorCallbackEvent(**kwargs)


def executor_identity() -> dict[str, str]:
    return {
        "tenant_id": "tenant-a",
        "workspace_id": "workspace-a",
        "user_id": "user-a",
    }


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
    callback = callback_event(
        session_id="session-a",
        run_id="run-a",
        attempt_id="attempt-a",
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


def test_callback_batch_id_is_optional_safe_and_serialized():
    callback = callback_event(
        session_id="session-a",
        run_id="run-a",
        attempt_id="attempt-a",
        callback_token_id="cbt_run-a",
        batch_id="batch-a.1",
        status="running",
        progress=20,
    )
    missing = callback_event(
        session_id="session-a",
        run_id="run-a",
        attempt_id="attempt-a",
        callback_token_id="cbt_run-a",
        status="running",
        progress=20,
    )

    assert callback.model_dump()["batch_id"] == "batch-a.1"
    assert missing.batch_id is None
    assert missing.model_dump()["batch_id"] is None
    for hostile_batch_id in ("", "batch/id", "batch id", "x" * 129):
        with pytest.raises(ValueError, match="batch_id"):
            callback_event(
                session_id="session-a",
                run_id="run-a",
                attempt_id="attempt-a",
                callback_token_id="cbt_run-a",
                batch_id=hostile_batch_id,
                status="running",
                progress=20,
            )


def test_callback_completed_new_message_preserves_final_assistant_delta():
    callback = callback_event(
        session_id="session-a",
        run_id="run-a",
        attempt_id="attempt-a",
        callback_token_id="cbt_run-a",
        status="completed",
        progress=100,
        new_message={"type": "assistant", "delta": "final answer"},
    )

    events = callback_event_to_run_events(callback)

    assert [(event.type, event.message, event.payload) for event in events] == [
        ("assistant_delta", "final answer", {"delta": "final answer"})
    ]


@pytest.mark.parametrize("value", ["", 7, True, [], {}, None])
def test_callback_rejects_empty_or_non_string_explicit_delta(value):
    callback = callback_event(
        session_id="session-a",
        run_id="run-a",
        attempt_id="attempt-a",
        callback_token_id="cbt_run-a",
        status="completed",
        progress=100,
        new_message={"delta": value},
    )

    with pytest.raises(ValueError, match="executor_callback_new_message_delta_invalid"):
        callback_event_to_run_events(callback)


@pytest.mark.parametrize("value", ["", 7, True, [], {}, None])
def test_callback_rejects_empty_or_non_string_text_fallback(value):
    callback = callback_event(
        session_id="session-a",
        run_id="run-a",
        attempt_id="attempt-a",
        callback_token_id="cbt_run-a",
        status="running",
        progress=20,
        new_message={"text": value},
    )

    with pytest.raises(ValueError, match="executor_callback_new_message_text_invalid"):
        callback_event_to_run_events(callback)


def test_callback_rejects_invalid_explicit_delta_without_falling_back_to_text():
    callback = callback_event(
        session_id="session-a",
        run_id="run-a",
        attempt_id="attempt-a",
        callback_token_id="cbt_run-a",
        status="completed",
        progress=100,
        new_message={"delta": 7, "text": "must not become a fallback"},
    )

    with pytest.raises(ValueError, match="executor_callback_new_message_delta_invalid"):
        callback_event_to_run_events(callback)


def test_callback_uses_valid_text_only_when_delta_is_absent():
    callback = callback_event(
        session_id="session-a",
        run_id="run-a",
        attempt_id="attempt-a",
        callback_token_id="cbt_run-a",
        status="completed",
        progress=100,
        new_message={"text": "text fallback"},
    )

    assert [(event.type, event.message, event.payload) for event in callback_event_to_run_events(callback)] == [
        ("assistant_delta", "text fallback", {"delta": "text fallback"})
    ]


@pytest.mark.parametrize("status", ["failed", "cancelled"])
def test_callback_non_success_terminal_new_message_does_not_synthesize_delta(status):
    callback = callback_event(
        session_id="session-a",
        run_id="run-a",
        attempt_id="attempt-a",
        callback_token_id="cbt_run-a",
        status=status,
        progress=60,
        new_message={"type": "assistant", "delta": "must not publish"},
    )

    assert callback_event_to_run_events(callback) == []


def test_callback_current_step_maps_to_tool_call_delta():
    callback = callback_event(
        session_id="session-a",
        run_id="run-a",
        attempt_id="attempt-a",
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


def test_callback_current_step_remains_running_only():
    callback = callback_event(
        session_id="session-a",
        run_id="run-a",
        attempt_id="attempt-a",
        callback_token_id="cbt_run-a",
        status="completed",
        progress=100,
        state_patch={"current_step": "must not become a terminal event"},
    )

    assert callback_event_to_run_events(callback) == []


@pytest.mark.parametrize("status", ["completed", "failed", "cancelled"])
def test_callback_terminal_status_does_not_map_to_authoritative_run_event(status):
    callback = callback_event(
        session_id="session-a",
        run_id="run-a",
        attempt_id="attempt-a",
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
    callback = callback_event(
        session_id="session-a",
        run_id="run-a",
        attempt_id="attempt-a",
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


def test_callback_collapses_only_one_exact_cross_representation_delta_mirror():
    callback = callback_event(
        session_id="session-a",
        run_id="run-a",
        attempt_id="attempt-a",
        callback_token_id="cbt_run-a",
        status="completed",
        progress=100,
        new_message={"delta": "answer"},
        events=[
            {"type": "assistant_delta", "message": "answer", "payload": {"delta": "answer"}},
            {"type": "assistant_delta", "message": "answer", "payload": {"delta": "answer"}},
            {"type": "checkpoint_created", "message": "checkpoint", "payload": {"checkpoint_id": "checkpoint-a"}},
        ],
    )

    events = callback_event_to_run_events(callback)

    assert [(event.type, event.message) for event in events] == [
        ("assistant_delta", "answer"),
        ("assistant_delta", "answer"),
        ("checkpoint_created", "checkpoint"),
    ]
    assert events[0] is not callback.events[0]
    assert events[1] is callback.events[1]


@pytest.mark.parametrize(
    "typed_event",
    [
        {"type": "assistant_delta", "message": "answer", "payload": {"delta": "answer with suffix"}},
        {"type": "assistant_delta", "message": "answer with suffix", "payload": {"delta": "answer"}},
        {
            "type": "assistant_delta",
            "message": "answer",
            "payload": {"delta": "answer"},
            "admin_only": True,
        },
    ],
)
def test_callback_does_not_collapse_non_exact_or_admin_only_delta_mirrors(typed_event):
    callback = callback_event(
        session_id="session-a",
        run_id="run-a",
        attempt_id="attempt-a",
        callback_token_id="cbt_run-a",
        status="running",
        progress=50,
        new_message={"delta": "answer"},
        events=[typed_event],
    )

    events = callback_event_to_run_events(callback)

    assert len(events) == 2
    assert events[0].type == "assistant_delta"
    assert events[1].type == "assistant_delta"
    assert events[1].admin_only is bool(typed_event.get("admin_only", False))


def test_callback_suppresses_executor_terminal_facts_without_reordering_surrounding_events():
    callback = callback_event(
        session_id="session-a",
        run_id="run-a",
        attempt_id="attempt-a",
        callback_token_id="cbt_run-a",
        status="completed",
        progress=100,
        new_message={"delta": "final"},
        events=[
            {"type": "checkpoint_created", "message": "before", "payload": {"checkpoint_id": "checkpoint-a"}},
            {"type": "run_completed", "message": "not authoritative"},
            {"type": "run_failed", "message": "not authoritative"},
            {"type": "run_cancelled", "message": "not authoritative"},
            {"type": "subagent_completed", "message": "after", "payload": {"subagent_id": "reviewer-a"}},
        ],
    )

    events = callback_event_to_run_events(callback)

    assert [(event.type, event.message) for event in events] == [
        ("assistant_delta", "final"),
        ("checkpoint_created", "before"),
        ("subagent_completed", "after"),
    ]


def test_executor_client_default_timeout_uses_short_dispatch_budget(monkeypatch):
    monkeypatch.setattr(
        executor_client_module,
        "get_settings",
        lambda: type("S", (), {"sandbox_executor_dispatch_timeout_seconds": 30.0})(),
    )

    assert executor_client_module._default_timeout_seconds() == 30.0


def test_executor_client_uses_configured_dispatch_timeout(monkeypatch):
    monkeypatch.setattr(
        executor_client_module,
        "get_settings",
        lambda: type("S", (), {"sandbox_executor_dispatch_timeout_seconds": 7.0})(),
    )

    assert executor_client_module._default_timeout_seconds() == 7.0


@pytest.mark.asyncio
async def test_executor_client_posts_task_request(monkeypatch):
    calls = []

    async def post_json(url, payload, timeout, headers=None):
        calls.append((url, payload, timeout))
        return {
            "status": "accepted",
            "run_id": "run-a",
            "attempt_id": "attempt-a",
        }

    monkeypatch.setattr(
        "app.runtime.sandbox.executor_client.get_settings",
        lambda: type("S", (), {"claude_agent_sdk_timeout_seconds": 120.0})(),
    )
    client = SandboxExecutorClient(post_json=post_json)
    request = ExecutorTaskRequest(
        **executor_identity(),
        session_id="session-a",
        run_id="run-a",
        attempt_id="attempt-a",
        prompt="hello",
        callback_url="http://callback",
        callback_token_id="cbt_run-a",
        callback_token="secret",
        callback_base_url="http://callback-base",
        config={"model": "deepseek-v4-flash"},
    )

    result = await client.execute("http://executor.test", request)

    assert result == {
        "status": "accepted",
        "run_id": "run-a",
        "attempt_id": "attempt-a",
    }
    assert calls == [
        (
            "http://executor.test/v2/tasks",
            request.model_dump(),
            30.0,
        )
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response_payload", "response_content", "expected_code", "expected_detail"),
    [
        (
            {"error_code": "invalid_executor_credential", "detail": "invalid_executor_credential"},
            b"bounded-json",
            "invalid_executor_credential",
            "invalid_executor_credential",
        ),
        (
            {"error_code": "executor_health_timeout", "detail": "executor_health_timeout"},
            b"bounded-json",
            "executor_health_timeout",
            "executor_health_timeout",
        ),
        (
            {
                "error_code": "token_private-value",
                "detail": "<html>prompt=private-prompt</html>",
                "url": "https://executor.test/run?token=private-token",
            },
            b"bounded-json",
            "executor_http_failure",
            None,
        ),
        (
            {"error_code": "x" * 65, "detail": "x" * 65},
            b"bounded-json",
            "executor_http_failure",
            None,
        ),
        (
            {"error_code": "invalid_executor_credential"},
            b"x" * 4097,
            "executor_http_failure",
            None,
        ),
    ],
)
async def test_executor_client_non_2xx_error_identity_is_bounded_and_secret_safe(
    monkeypatch,
    response_payload,
    response_content,
    expected_code,
    expected_detail,
):
    class StubResponse:
        status_code = 401
        content = response_content

        def json(self):
            return response_payload

    class StubAsyncClient:
        def __init__(self, *, timeout):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return None

        async def request(self, method, url, *, json, headers=None):
            assert method == "POST"
            return StubResponse()

    monkeypatch.setattr(executor_client_module.httpx, "AsyncClient", StubAsyncClient)

    with pytest.raises(SandboxExecutorHttpError) as raised:
        await executor_client_module._default_post_json(
            "https://executor.test/v2/tasks?token=private-query-token",
            {"prompt": "private-prompt", "callback_token": "private-callback-token"},
            3.0,
            {"Authorization": "Bearer private-header-token"},
        )

    assert raised.value.status_code == 401
    assert raised.value.error_code == expected_code
    assert raised.value.detail == expected_detail
    projected = str(raised.value)
    assert len(projected) <= 96
    for secret in (
        "private-value",
        "private-prompt",
        "private-token",
        "private-query-token",
        "private-callback-token",
        "private-header-token",
        "<html>",
    ):
        assert secret not in projected


@pytest.mark.asyncio
async def test_executor_client_rejects_http_200_reported_failure_as_invalid_protocol():
    async def post_json(url, payload, timeout, headers=None):
        return {
            "status": "failed",
            "error_code": "executor_health_timeout",
            "error_message": "Executor health timeout",
        }

    request = ExecutorTaskRequest(
        **executor_identity(),
        session_id="session-a",
        run_id="run-a",
        attempt_id="attempt-a",
        prompt="hello",
        callback_url="http://callback",
        callback_token_id="cbt_run-a",
        callback_token="secret",
        callback_base_url="http://callback-base",
    )

    with pytest.raises(SandboxExecutorHttpError) as raised:
        await SandboxExecutorClient(post_json=post_json, timeout_seconds=3.0).execute(
            "http://executor.test",
            request,
        )

    assert raised.value.status_code == 502
    assert raised.value.error_code == "executor_protocol_invalid"


def test_executor_failure_normalizer_drops_unknown_private_fields():
    private = "https://executor.test/run?token=private-token"

    normalized = executor_client_module.normalize_executor_reported_failure(
        {
            "status": "failed",
            "run_id": "run-a",
            "error_code": "executor_health_timeout",
            "message": private,
            "sdk_error": private,
            "detail": private,
            "sdk_used": True,
            "requested_max_seconds": 0.05,
            "timeout_elapsed_ms": 51,
            "url": private,
            "path": "/private/workspace",
            "token": "private-token",
            "nested": {"prompt": "private-prompt"},
        },
        expected_run_id="run-a",
    )

    assert normalized == {
        "status": "failed",
        "run_id": "run-a",
        "error_code": "executor_health_timeout",
        "error_message": "Executor health timeout",
        "message": "Executor health timeout",
        "sdk_error": "executor_health_timeout",
        "sdk_used": True,
        "requested_max_seconds": 0.05,
        "timeout_elapsed_ms": 51,
    }
    assert "private" not in str(normalized)


@pytest.mark.asyncio
async def test_executor_client_uses_short_dispatch_deadline(monkeypatch):
    calls = []

    async def post_json(url, payload, timeout, headers=None):
        calls.append(timeout)
        return {"status": "accepted"}

    monkeypatch.setattr(
        "app.runtime.sandbox.executor_client.get_settings",
        lambda: type("S", (), {"opensandbox_request_timeout_seconds": 30.0})(),
    )
    request = ExecutorTaskRequest(
        **executor_identity(),
        session_id="session-a",
        run_id="run-a",
        attempt_id="attempt-a",
        prompt="hello",
        callback_url="http://callback",
        callback_token_id="cbt_run-a",
        callback_token="secret",
        callback_base_url="http://callback-base",
        governed_permission_wait=False,
    )

    await SandboxExecutorClient(post_json=post_json).execute("http://executor.test", request)

    assert calls == [30.0]


@pytest.mark.asyncio
async def test_executor_client_connects_to_pinned_ip_without_transmitting_private_metadata():
    calls = []

    async def post_json(url, payload, timeout, headers=None):
        calls.append((url, dict(headers or {})))
        return {"status": "accepted"}

    client = SandboxExecutorClient(post_json=post_json, timeout_seconds=3.0)
    request = ExecutorTaskRequest(
        **executor_identity(),
        session_id="session-a",
        run_id="run-a",
        attempt_id="attempt-a",
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
            "http://172.17.0.1:43123/v2/tasks",
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
        **executor_identity(),
        session_id="session-a",
        run_id="run-a",
        attempt_id="attempt-a",
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
        **executor_identity(),
        session_id="session-a",
        run_id="run-a",
        attempt_id="attempt-a",
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
        **executor_identity(),
        session_id="session-a",
        run_id="run-a",
        attempt_id="attempt-a",
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
