import asyncio
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.runtime.kernel_contracts import AgentEvent
from app.runtime.sandbox.contracts import ExecutorTaskRequest
from app.runtime.sandbox.executor_app import create_executor_app


EXECUTOR_AUTH_TOKEN = "executor-secret"
TRUSTED_CALLBACK_BASE_URL = "http://ai-platform.test"
TRUSTED_CALLBACK_URL = f"{TRUSTED_CALLBACK_BASE_URL}/api/ai/runtime/callbacks/executor"


def task_payload(
    callback_url: str = TRUSTED_CALLBACK_URL,
    *,
    callback_base_url: str = TRUSTED_CALLBACK_BASE_URL,
) -> dict[str, object]:
    return {
        "session_id": "session-a",
        "run_id": "run-a",
        "prompt": "hello executor",
        "callback_url": callback_url,
        "callback_token_id": "cbt_run-a",
        "callback_token": "secret",
        "callback_base_url": callback_base_url,
        "sdk_session_id": None,
        "permission_mode": "default",
        "config": {
            "model": "deepseek-v4-flash",
            "browser_enabled": False,
            "resource_limits": {"max_seconds": 60},
            "skill_ids": [],
            "mcp_tool_ids": [],
            "input_files": [],
        },
    }


def sensitive_task_payload(callback_url: str = TRUSTED_CALLBACK_URL) -> dict[str, object]:
    payload = task_payload(callback_url)
    payload["config"] = {
        "model": "deepseek-v4-flash",
        "browser_enabled": False,
        "resource_limits": {
            "max_seconds": 60,
            "headers": {"Authorization": "Bearer nested-secret"},
            "host_path": "/runtime/tenants/nested",
        },
        "skill_ids": ["safe-skill"],
        "mcp_tool_ids": [],
        "input_files": ["file-a", "/runtime/tenants/input-path"],
        "env_overrides": {"OPENAI_API_KEY": "secret-key"},
        "headers": {"Authorization": "Bearer secret"},
        "host_path": "/runtime/tenants/tenant-a/workspaces/workspace-a",
    }
    return payload


def auth_headers(token: str = EXECUTOR_AUTH_TOKEN) -> dict[str, str]:
    return {"X-AI-Platform-Executor-Credential": token}


def create_test_client(tmp_path, **kwargs) -> TestClient:
    return TestClient(
        create_executor_app(
            workspace_root=tmp_path,
            executor_auth_token=EXECUTOR_AUTH_TOKEN,
            expected_session_id="session-a",
            expected_run_id="run-a",
            trusted_callback_base_url=TRUSTED_CALLBACK_BASE_URL,
            **kwargs,
        )
    )


def test_executor_health_returns_ready(tmp_path):
    client = create_test_client(tmp_path)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


def test_executor_runtime_identity_requires_lease_credential_and_returns_only_effective_ids(tmp_path, monkeypatch):
    monkeypatch.setattr("app.runtime.sandbox.executor_app.os.geteuid", lambda: 10001, raising=False)
    monkeypatch.setattr("app.runtime.sandbox.executor_app.os.getegid", lambda: 10001, raising=False)
    client = TestClient(create_executor_app(workspace_root=tmp_path, executor_auth_token="lease-secret"))

    assert client.get("/health/runtime-identity").status_code == 401
    assert client.get(
        "/health/runtime-identity",
        headers={"X-AI-Platform-Executor-Credential": "wrong"},
    ).status_code == 401
    response = client.get(
        "/health/runtime-identity",
        headers={"X-AI-Platform-Executor-Credential": "lease-secret"},
    )

    assert response.status_code == 200
    assert response.json() == {"uid": 10001, "gid": 10001}


def test_executor_execute_posts_running_and_completed_callbacks(tmp_path, monkeypatch):
    callbacks = []

    class StubSettings:
        claude_agent_sdk_enabled = True

    async def fake_run_claude_agent_sdk(**kwargs):
        return type(
            "SdkResult",
            (),
            {
                "used_sdk": True,
                "message": "sdk final",
                "session_id": "sdk-session-a",
                "usage": {"input_tokens": 1, "output_tokens": 1},
                "error": None,
                "used_skills": [],
                "used_skills_source": "",
            },
        )()

    def callback_sender(url, payload, token):
        callbacks.append((url, payload, token))
        return {"accepted": True}

    # keep this focused on the default happy path instead of the disabled fail-closed branch
    monkeypatch.setattr("app.runtime.sandbox.executor_app.get_settings", lambda: StubSettings())
    monkeypatch.setattr("app.runtime.sandbox.executor_app.run_claude_agent_sdk", fake_run_claude_agent_sdk)
    client = create_test_client(tmp_path, callback_sender=callback_sender)

    response = client.post("/v1/tasks/execute", json=task_payload(), headers=auth_headers())

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "accepted"
    assert body["run_id"] == "run-a"
    assert isinstance(body["executor_model_latency_ms"], int)
    assert isinstance(body["document_processing_latency_ms"], int)
    assert [item[1]["status"] for item in callbacks] == ["running", "completed"]
    assert {item[2] for item in callbacks} == {"secret"}
    assert {item[1]["callback_token_id"] for item in callbacks} == {"cbt_run-a"}
    assert callbacks[0][1]["progress"] == 5
    assert callbacks[1][1]["progress"] == 100


def test_executor_execute_streams_runner_events_and_phase_timings(tmp_path):
    callbacks = []

    async def executor_runner(request, workspace_root, emit_event):
        assert request.run_id == "run-a"
        assert workspace_root == Path(tmp_path)
        await emit_event(
            AgentEvent(type="assistant_delta", message="partial", payload={"delta": "partial"})
        )
        await emit_event(
            AgentEvent(
                type="tool_call_started",
                message="Bash started",
                payload={"tool_name": "Bash", "tool_call_id": "tool-a"},
                admin_only=True,
            )
        )
        await emit_event(
            AgentEvent(
                type="artifact_created",
                message="Artifact uploaded",
                payload={"artifact_id": "artifact-a", "label": "result.txt"},
            )
        )
        return {
            "status": "completed",
            "message": "done",
            "sdk_session_id": "sdk-session-a",
            "sdk_usage": {"input_tokens": 2, "output_tokens": 3},
        }

    def callback_sender(url, payload, token):
        callbacks.append((url, payload, token))
        return {"accepted": True}

    client = create_test_client(
        tmp_path,
        callback_sender=callback_sender,
        executor_runner=executor_runner,
    )

    response = client.post("/v1/tasks/execute", json=task_payload(), headers=auth_headers())

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "accepted"
    assert body["sdk_session_id"] == "sdk-session-a"
    assert body["sdk_usage"] == {"input_tokens": 2, "output_tokens": 3}
    assert isinstance(body["executor_first_token_latency_ms"], int)
    assert isinstance(body["executor_tool_call_latency_ms"], int)
    assert isinstance(body["artifact_upload_latency_ms"], int)
    assert [item[1]["status"] for item in callbacks] == [
        "running",
        "running",
        "running",
        "running",
        "completed",
    ]
    assert callbacks[1][1]["events"][0]["type"] == "assistant_delta"
    assert callbacks[2][1]["events"][0]["type"] == "tool_call_started"
    assert callbacks[3][1]["events"][0]["type"] == "artifact_created"
    assert callbacks[-1][1]["sdk_session_id"] == "sdk-session-a"


def test_executor_execute_uses_claude_sdk_runner_when_enabled(tmp_path, monkeypatch):
    callbacks = []
    calls = {}

    class StubSettings:
        claude_agent_sdk_enabled = True

    async def fake_run_claude_agent_sdk(**kwargs):
        calls["cwd"] = kwargs["cwd"]
        calls["skill_id"] = kwargs["skill_id"]
        calls["model_id"] = kwargs["model_id"]
        calls["skills"] = kwargs["skills"]
        await kwargs["on_text"]("sdk partial")
        permission = await kwargs["on_tool_permission"](
            {
                "tool_name": "Bash",
                "tool_call_id": "tool-a",
                "tool_input_keys": ["command"],
                "risk_level": "high",
                "write_capable": True,
                "action": "execute",
                "reason": "needs shell",
            }
        )
        calls["permission"] = permission
        return type(
            "SdkResult",
            (),
            {
                "used_sdk": True,
                "message": "sdk final",
                "session_id": "sdk-session-a",
                "usage": {"input_tokens": 1, "output_tokens": 1},
                "error": None,
                "used_skills": [],
                "used_skills_source": "",
            },
        )()

    def callback_sender(url, payload, token):
        callbacks.append(payload)
        return {"accepted": True}

    monkeypatch.setattr("app.runtime.sandbox.executor_app.get_settings", lambda: StubSettings())
    monkeypatch.setattr("app.runtime.sandbox.executor_app.run_claude_agent_sdk", fake_run_claude_agent_sdk)

    client = create_test_client(tmp_path, callback_sender=callback_sender)

    response = client.post("/v1/tasks/execute", json=task_payload(), headers=auth_headers())

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "accepted"
    assert body["sdk_session_id"] == "sdk-session-a"
    assert calls["cwd"] == Path(tmp_path)
    assert calls["skill_id"] == "general-chat"
    assert calls["model_id"] == "deepseek-v4-flash"
    assert calls["skills"] == ["general-chat"]
    assert calls["permission"]["allowed"] is False
    assert any(
        event["type"] == "assistant_delta"
        for callback in callbacks
        for event in callback.get("events", [])
    )
    assert any(
        event["type"] == "tool_call_started"
        for callback in callbacks
        for event in callback.get("events", [])
    )


def test_executor_execute_fails_when_claude_sdk_disabled(tmp_path, monkeypatch):
    class StubSettings:
        claude_agent_sdk_enabled = False

    monkeypatch.setattr("app.runtime.sandbox.executor_app.get_settings", lambda: StubSettings())

    client = create_test_client(tmp_path)

    response = client.post("/v1/tasks/execute", json=task_payload(), headers=auth_headers())

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "failed"
    assert body["error_code"] == "claude_agent_sdk_disabled"
    assert body["executor_mode"] == "claude_agent_sdk_disabled"


def test_executor_execute_rehydrates_context_retrieval_for_manifest(tmp_path, monkeypatch):
    captured = {}

    class StubSettings:
        claude_agent_sdk_enabled = True

    async def fake_run_claude_agent_sdk(**kwargs):
        captured["context_retrieval"] = kwargs["context_retrieval"]
        captured["context_retrieval_identity"] = kwargs["context_retrieval_identity"]
        return type(
            "SdkResult",
            (),
            {
                "used_sdk": True,
                "message": "sdk final",
                "session_id": "sdk-session-a",
                "usage": {"input_tokens": 1, "output_tokens": 1},
                "error": None,
                "used_skills": [],
                "used_skills_source": "",
            },
        )()

    monkeypatch.setattr("app.runtime.sandbox.executor_app.get_settings", lambda: StubSettings())
    monkeypatch.setattr("app.runtime.sandbox.executor_app.run_claude_agent_sdk", fake_run_claude_agent_sdk)

    payload = task_payload()
    payload["config"]["context_manifest"] = {
        "schema_version": "ai-platform.context-manifest.v1",
        "available_retrieval_tools": ["read_context_file"],
    }
    payload["config"]["context_retrieval_scope"] = {
        "tenant_id": "tenant-a",
        "workspace_id": "workspace-a",
        "user_id": "user-a",
        "session_id": "session-a",
        "run_id": "run-a",
        "agent_id": "general-agent",
    }

    client = create_test_client(tmp_path)

    response = client.post("/v1/tasks/execute", json=payload, headers=auth_headers())

    assert response.status_code == 200
    assert response.json()["status"] == "accepted"
    assert captured["context_retrieval"] is not None
    assert captured["context_retrieval_identity"].tenant_id == "tenant-a"
    assert captured["context_retrieval_identity"].workspace_id == "workspace-a"
    assert captured["context_retrieval_identity"].user_id == "user-a"


def test_executor_execute_fails_closed_for_manifest_without_valid_scope(tmp_path, monkeypatch):
    class StubSettings:
        claude_agent_sdk_enabled = True

    monkeypatch.setattr("app.runtime.sandbox.executor_app.get_settings", lambda: StubSettings())

    payload = task_payload()
    payload["config"]["context_manifest"] = {
        "schema_version": "ai-platform.context-manifest.v1",
        "available_retrieval_tools": ["read_context_file"],
    }
    payload["config"]["context_retrieval_scope"] = {"tenant_id": "tenant-a"}

    client = create_test_client(tmp_path)

    response = client.post("/v1/tasks/execute", json=payload, headers=auth_headers())

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "failed"
    assert body["error_code"] == "context_retrieval_scope_invalid"


def test_executor_execute_uses_platform_tool_permission_broker(tmp_path, monkeypatch):
    callbacks = []
    calls = {}

    class StubSettings:
        claude_agent_sdk_enabled = True

    async def fake_run_claude_agent_sdk(**kwargs):
        calls["cwd"] = kwargs["cwd"]
        calls["execution_policy"] = kwargs.get("execution_policy")
        permission = await kwargs["on_tool_permission"](
            {
                "tool_name": "mcp__knowledge__search",
                "tool_input": {"query": "approved knowledge"},
                "tool_call_id": "tool-mcp-a",
                "risk_level": "high",
                "write_capable": True,
                "action": "execute",
                "reason": "needs governed MCP access",
            }
        )
        calls["permission"] = permission
        return type(
            "SdkResult",
            (),
            {
                "used_sdk": True,
                "message": "sdk final",
                "session_id": "sdk-session-a",
                "usage": {"input_tokens": 1, "output_tokens": 1},
                "error": None if permission["allowed"] else permission["reason"],
                "used_skills": [],
                "used_skills_source": "",
            },
        )()

    def callback_sender(url, payload, token):
        callbacks.append((url, payload, token))
        if url.endswith("/api/ai/runtime/callbacks/tool-permission"):
            return {
                "allowed": True,
                "reason": "tool_permission_allowed",
                "risk_level": "high",
                "write_capable": True,
                "decision": "allow_for_run",
                "permission_request_id": "tpr-sdk",
            }
        return {"accepted": True}

    monkeypatch.setattr("app.runtime.sandbox.executor_app.get_settings", lambda: StubSettings())
    monkeypatch.setattr("app.runtime.sandbox.executor_app.run_claude_agent_sdk", fake_run_claude_agent_sdk)

    client = create_test_client(tmp_path, callback_sender=callback_sender)

    response = client.post("/v1/tasks/execute", json=task_payload(), headers=auth_headers())

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "accepted"
    assert calls["cwd"] == Path(tmp_path)
    assert calls["execution_policy"] == "sandbox_brokered"
    assert calls["permission"] == {
        "allowed": True,
        "reason": "tool_permission_allowed",
        "risk_level": "high",
        "write_capable": True,
        "decision": "allow_for_run",
        "permission_request_id": "tpr-sdk",
    }
    broker_call = next(item for item in callbacks if item[0].endswith("/api/ai/runtime/callbacks/tool-permission"))
    assert sum(1 for item in callbacks if item[0].endswith("/api/ai/runtime/callbacks/tool-permission")) == 1
    assert broker_call[1]["run_id"] == "run-a"
    assert broker_call[1]["callback_token_id"] == "cbt_run-a"
    assert broker_call[1]["tool_name"] == "mcp__knowledge__search"
    assert broker_call[1]["tool_input"] == {"query": "approved knowledge"}
    assert broker_call[1]["tool_call_id"] == "tool-mcp-a"
    permission_events = [
        event
        for _, callback_payload, _ in callbacks
        for event in callback_payload.get("events", [])
        if event.get("payload", {}).get("tool_call_id") == "tool-mcp-a"
    ]
    assert [event["type"] for event in permission_events] == ["tool_call_started", "tool_call_completed"]
    assert permission_events[-1]["payload"]["allowed"] is True
    assert permission_events[-1]["payload"]["reason"] == "tool_permission_allowed"


@pytest.mark.parametrize(
    ("broker_mode", "expected_reason"),
    [
        ("timeout", "tool_permission_broker_failed"),
        ("malformed", "tool_permission_malformed_response"),
    ],
)
def test_executor_permission_broker_failures_emit_controlled_denial_event(
    tmp_path,
    monkeypatch,
    broker_mode,
    expected_reason,
):
    callbacks = []
    calls = {}

    class StubSettings:
        claude_agent_sdk_enabled = True

    async def fake_run_claude_agent_sdk(**kwargs):
        permission = await kwargs["on_tool_permission"](
            {
                "tool_name": "mcp__knowledge__search",
                "tool_input": {"query": "governed knowledge"},
                "tool_call_id": "tool-mcp-denied",
                "risk_level": "high",
                "write_capable": True,
                "action": "execute",
                "reason": "needs governed MCP access",
            }
        )
        calls["permission"] = permission
        return type(
            "SdkResult",
            (),
            {
                "used_sdk": True,
                "message": "",
                "session_id": "sdk-session-a",
                "usage": {},
                "error": permission["reason"],
                "used_skills": [],
                "used_skills_source": "",
            },
        )()

    def callback_sender(url, payload, token):
        callbacks.append((url, payload, token))
        if url.endswith("/api/ai/runtime/callbacks/tool-permission"):
            if broker_mode == "timeout":
                raise TimeoutError("permission callback timed out")
            return {"allowed": "false", "reason": "truthy malformed value"}
        return {"accepted": True}

    monkeypatch.setattr("app.runtime.sandbox.executor_app.get_settings", lambda: StubSettings())
    monkeypatch.setattr("app.runtime.sandbox.executor_app.run_claude_agent_sdk", fake_run_claude_agent_sdk)
    client = create_test_client(tmp_path, callback_sender=callback_sender)

    response = client.post("/v1/tasks/execute", json=task_payload(), headers=auth_headers())

    assert response.status_code == 200
    assert response.json()["status"] == "failed"
    assert calls["permission"] == {"allowed": False, "reason": expected_reason}
    permission_events = [
        event
        for _, callback_payload, _ in callbacks
        for event in callback_payload.get("events", [])
        if event.get("payload", {}).get("tool_call_id") == "tool-mcp-denied"
    ]
    assert [event["type"] for event in permission_events] == ["tool_call_started", "tool_call_completed"]
    assert permission_events[-1]["payload"]["allowed"] is False
    assert permission_events[-1]["payload"]["reason"] == expected_reason


def test_executor_execute_reports_platform_timeout_probe_as_failed_callback(tmp_path):
    callbacks = []
    payload = task_payload()
    payload["config"]["resource_limits"] = {"max_seconds": 0}

    def callback_sender(url, payload, token):
        callbacks.append((url, payload, token))
        return {"accepted": True}

    client = create_test_client(tmp_path, callback_sender=callback_sender)

    response = client.post("/v1/tasks/execute", json=payload, headers=auth_headers())

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "failed"
    assert body["run_id"] == "run-a"
    assert body["error_code"] == "executor_health_timeout"
    assert body["error_message"] == "Executor health timeout"
    assert body["requested_max_seconds"] == 0
    assert isinstance(body["timeout_elapsed_ms"], int)
    assert [item[1]["status"] for item in callbacks] == ["running", "failed"]
    assert callbacks[-1][1]["error_message"] == "Executor health timeout"
    assert callbacks[-1][1]["state_patch"] == {
        "error_code": "executor_health_timeout",
        "requested_max_seconds": 0,
        "timeout_elapsed_ms": body["timeout_elapsed_ms"],
    }
    assert str(tmp_path) not in str(body)


def test_executor_execute_enforces_fractional_positive_timeout_and_cancels_runner(tmp_path):
    callbacks = []
    runner_cancelled = threading.Event()
    late_side_effect = threading.Event()
    payload = task_payload()
    payload["config"]["resource_limits"] = {"max_seconds": 0.03}

    async def executor_runner(request, workspace_root, emit_event):
        try:
            await asyncio.sleep(0.2)
            late_side_effect.set()
            return {"status": "completed"}
        except asyncio.CancelledError:
            runner_cancelled.set()
            raise

    def callback_sender(url, payload, token):
        callbacks.append((url, payload, token))
        return {"accepted": True}

    client = create_test_client(
        tmp_path,
        callback_sender=callback_sender,
        executor_runner=executor_runner,
    )

    started_at = time.monotonic()
    response = client.post("/v1/tasks/execute", json=payload, headers=auth_headers())
    elapsed = time.monotonic() - started_at

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "failed"
    assert body["error_code"] == "executor_deadline_exceeded"
    assert body["error_message"] == "Executor deadline exceeded"
    assert body["requested_max_seconds"] == 0.03
    assert 0 <= body["timeout_elapsed_ms"] < 250
    assert elapsed < 0.25
    assert runner_cancelled.wait(timeout=0.1)
    time.sleep(0.1)
    assert not late_side_effect.is_set()
    assert [item[1]["status"] for item in callbacks] == ["running", "failed"]
    assert callbacks[-1][1]["state_patch"] == {
        "error_code": "executor_deadline_exceeded",
        "requested_max_seconds": 0.03,
        "timeout_elapsed_ms": body["timeout_elapsed_ms"],
    }
    assert str(tmp_path) not in str(body)


def test_executor_execute_allows_runner_with_larger_fractional_deadline(tmp_path):
    callbacks = []
    payload = task_payload()
    payload["config"]["resource_limits"] = {"max_seconds": 0.2}

    async def executor_runner(request, workspace_root, emit_event):
        await asyncio.sleep(0.01)
        return {"status": "completed", "message": "done"}

    def callback_sender(url, payload, token):
        callbacks.append(payload)
        return {"accepted": True}

    client = create_test_client(tmp_path, callback_sender=callback_sender, executor_runner=executor_runner)

    response = client.post("/v1/tasks/execute", json=payload, headers=auth_headers())

    assert response.status_code == 200
    assert response.json()["status"] == "accepted"
    assert [item["status"] for item in callbacks] == ["running", "completed"]


def test_executor_execute_does_not_rewrite_runner_timeout_error_as_deadline(tmp_path):
    async def executor_runner(request, workspace_root, emit_event):
        raise TimeoutError("runner dependency timed out")

    client = create_test_client(
        tmp_path,
        callback_sender=lambda url, payload, token: {"accepted": True},
        executor_runner=executor_runner,
    )

    response = client.post("/v1/tasks/execute", json=task_payload(), headers=auth_headers())

    assert response.status_code == 200
    assert response.json()["status"] == "failed"
    assert response.json()["error_code"] == "executor_runner_failed"
    assert response.json()["error_message"] == "runner dependency timed out"
    assert "requested_max_seconds" not in response.json()
    assert "timeout_elapsed_ms" not in response.json()


@pytest.mark.asyncio
async def test_executor_execute_preserves_caller_cancellation(tmp_path):
    runner_started = asyncio.Event()
    runner_cancelled = asyncio.Event()

    async def executor_runner(request, workspace_root, emit_event):
        runner_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            runner_cancelled.set()
            raise

    app = create_executor_app(
        workspace_root=tmp_path,
        callback_sender=lambda url, payload, token: {"accepted": True},
        executor_runner=executor_runner,
        executor_auth_token=EXECUTOR_AUTH_TOKEN,
        expected_session_id="session-a",
        expected_run_id="run-a",
        trusted_callback_base_url=TRUSTED_CALLBACK_BASE_URL,
    )
    endpoint = next(route.endpoint for route in app.routes if route.path == "/v1/tasks/execute")
    request = ExecutorTaskRequest.model_validate(task_payload())

    execute_task = asyncio.create_task(endpoint(request, executor_credential=EXECUTOR_AUTH_TOKEN))
    await asyncio.wait_for(runner_started.wait(), timeout=0.2)
    execute_task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await execute_task
    assert runner_cancelled.is_set()


@pytest.mark.asyncio
async def test_executor_execute_preserves_caller_cancellation_when_runner_cleanup_fails(tmp_path):
    runner_started = asyncio.Event()

    async def executor_runner(request, workspace_root, emit_event):
        runner_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError as exc:
            raise RuntimeError("runner cancellation cleanup failed") from exc

    app = create_executor_app(
        workspace_root=tmp_path,
        callback_sender=lambda url, payload, token: {"accepted": True},
        executor_runner=executor_runner,
        executor_auth_token=EXECUTOR_AUTH_TOKEN,
        expected_session_id="session-a",
        expected_run_id="run-a",
        trusted_callback_base_url=TRUSTED_CALLBACK_BASE_URL,
    )
    endpoint = next(route.endpoint for route in app.routes if route.path == "/v1/tasks/execute")
    request = ExecutorTaskRequest.model_validate(task_payload())

    execute_task = asyncio.create_task(endpoint(request, executor_credential=EXECUTOR_AUTH_TOKEN))
    await asyncio.wait_for(runner_started.wait(), timeout=0.2)
    execute_task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await execute_task


def test_executor_execute_fails_closed_for_sync_runner_with_positive_deadline(tmp_path):
    invoked = False

    def executor_runner(request, workspace_root, emit_event):
        nonlocal invoked
        invoked = True
        return {"status": "completed"}

    client = create_test_client(
        tmp_path,
        callback_sender=lambda url, payload, token: {"accepted": True},
        executor_runner=executor_runner,
    )

    response = client.post("/v1/tasks/execute", json=task_payload(), headers=auth_headers())

    assert response.status_code == 200
    assert response.json()["status"] == "failed"
    assert response.json()["error_code"] == "executor_deadline_requires_async_runner"
    assert invoked is False


def test_executor_execute_writes_runtime_marker_without_host_path(tmp_path):
    client = create_test_client(
        tmp_path,
        callback_sender=lambda url, payload, token: {},
    )

    response = client.post("/v1/tasks/execute", json=task_payload(), headers=auth_headers())

    assert response.status_code == 200
    marker = Path(tmp_path) / "runtime" / "run-a.json"
    content = marker.read_text(encoding="utf-8")
    assert "prompt_length" in content
    assert "hello executor" not in content
    assert str(tmp_path) not in content


def test_executor_marker_redacts_unapproved_config_and_tokens(tmp_path):
    client = create_test_client(
        tmp_path,
        callback_sender=lambda url, payload, token: {},
    )

    response = client.post("/v1/tasks/execute", json=sensitive_task_payload(), headers=auth_headers())

    assert response.status_code == 200
    content = (Path(tmp_path) / "runtime" / "run-a.json").read_text(encoding="utf-8")
    assert "secret-key" not in content
    assert "Authorization" not in content
    assert "/runtime/tenants" not in content
    assert "nested-secret" not in content
    assert "safe-skill" in content
    assert "deepseek-v4-flash" in content
    assert "secret" not in content


def test_executor_execute_reports_callback_errors_without_raising(tmp_path, monkeypatch):
    callbacks = []

    class StubSettings:
        claude_agent_sdk_enabled = True

    async def fake_run_claude_agent_sdk(**kwargs):
        return type(
            "SdkResult",
            (),
            {
                "used_sdk": True,
                "message": "sdk final",
                "session_id": "sdk-session-a",
                "usage": {"input_tokens": 1, "output_tokens": 1},
                "error": None,
                "used_skills": [],
                "used_skills_source": "",
            },
        )()

    def callback_sender(url, payload, token):
        callbacks.append(payload["status"])
        if payload["status"] == "completed":
            raise RuntimeError("callback failed")
        return {"accepted": True}

    monkeypatch.setattr("app.runtime.sandbox.executor_app.get_settings", lambda: StubSettings())
    monkeypatch.setattr("app.runtime.sandbox.executor_app.run_claude_agent_sdk", fake_run_claude_agent_sdk)
    client = create_test_client(tmp_path, callback_sender=callback_sender)

    response = client.post("/v1/tasks/execute", json=task_payload(), headers=auth_headers())

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "accepted"
    assert body["run_id"] == "run-a"
    assert body["callback_errors"] == ["completed"]
    assert isinstance(body["executor_model_latency_ms"], int)
    assert isinstance(body["document_processing_latency_ms"], int)
    assert callbacks == ["running", "completed"]


def test_executor_completed_callback_marker_path_is_container_path(tmp_path, monkeypatch):
    callbacks = []

    class StubSettings:
        claude_agent_sdk_enabled = True

    async def fake_run_claude_agent_sdk(**kwargs):
        return type(
            "SdkResult",
            (),
            {
                "used_sdk": True,
                "message": "sdk final",
                "session_id": "sdk-session-a",
                "usage": {"input_tokens": 1, "output_tokens": 1},
                "error": None,
                "used_skills": [],
                "used_skills_source": "",
            },
        )()

    def callback_sender(url, payload, token):
        callbacks.append(payload)
        return {"accepted": True}

    monkeypatch.setattr("app.runtime.sandbox.executor_app.get_settings", lambda: StubSettings())
    monkeypatch.setattr("app.runtime.sandbox.executor_app.run_claude_agent_sdk", fake_run_claude_agent_sdk)
    client = create_test_client(tmp_path, callback_sender=callback_sender)

    response = client.post("/v1/tasks/execute", json=task_payload(), headers=auth_headers())

    assert response.status_code == 200
    marker_path = callbacks[-1]["state_patch"]["marker_path"]
    assert marker_path == "/workspace/runtime/run-a.json"
    assert str(tmp_path) not in marker_path


def test_executor_execute_rejects_missing_executor_credential(tmp_path):
    client = create_test_client(tmp_path)

    response = client.post("/v1/tasks/execute", json=task_payload())

    assert response.status_code == 401
    assert response.json() == {"detail": "invalid_executor_credential"}


def test_executor_execute_rejects_wrong_executor_credential(tmp_path):
    client = create_test_client(tmp_path)

    response = client.post(
        "/v1/tasks/execute",
        json=task_payload(),
        headers=auth_headers("wrong-token"),
    )

    assert response.status_code == 401
    assert response.json() == {"detail": "invalid_executor_credential"}


def test_executor_execute_rejects_replay_after_first_dispatch(tmp_path, monkeypatch):
    class StubSettings:
        claude_agent_sdk_enabled = True

    async def fake_run_claude_agent_sdk(**kwargs):
        return type(
            "SdkResult",
            (),
            {
                "used_sdk": True,
                "message": "sdk final",
                "session_id": "sdk-session-a",
                "usage": {"input_tokens": 1, "output_tokens": 1},
                "error": None,
                "used_skills": [],
                "used_skills_source": "",
            },
        )()

    monkeypatch.setattr("app.runtime.sandbox.executor_app.get_settings", lambda: StubSettings())
    monkeypatch.setattr("app.runtime.sandbox.executor_app.run_claude_agent_sdk", fake_run_claude_agent_sdk)
    client = create_test_client(tmp_path, callback_sender=lambda url, payload, token: {"accepted": True})

    first = client.post("/v1/tasks/execute", json=task_payload(), headers=auth_headers())
    second = client.post("/v1/tasks/execute", json=task_payload(), headers=auth_headers())

    assert first.status_code == 200
    assert second.status_code == 409
    assert second.json() == {"detail": "executor_request_replayed"}


def test_executor_execute_rejects_untrusted_callback_target(tmp_path):
    client = create_test_client(tmp_path)

    response = client.post(
        "/v1/tasks/execute",
        json=task_payload(
            "http://169.254.169.254/latest/meta-data",
            callback_base_url="http://169.254.169.254",
        ),
        headers=auth_headers(),
    )

    assert response.status_code == 400
    assert response.json() == {"detail": "invalid_callback_target"}


def test_executor_execute_rejects_missing_executor_scope_binding(tmp_path):
    client = TestClient(
        create_executor_app(
            workspace_root=tmp_path,
            executor_auth_token=EXECUTOR_AUTH_TOKEN,
            trusted_callback_base_url=TRUSTED_CALLBACK_BASE_URL,
        )
    )

    response = client.post("/v1/tasks/execute", json=task_payload(), headers=auth_headers())

    assert response.status_code == 503
    assert response.json() == {"detail": "executor_scope_not_configured"}


def test_executor_execute_rejects_wrong_executor_scope(tmp_path):
    client = create_test_client(tmp_path)

    response = client.post(
        "/v1/tasks/execute",
        json=task_payload(callback_url=TRUSTED_CALLBACK_URL) | {"session_id": "session-b"},
        headers=auth_headers(),
    )

    assert response.status_code == 401
    assert response.json() == {"detail": "invalid_executor_scope"}
