from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.runtime.sandbox.executor_app import create_executor_app


def task_payload(callback_url: str) -> dict[str, object]:
    return {
        "session_id": "session-a",
        "run_id": "run-a",
        "prompt": "hello executor",
        "callback_url": callback_url,
        "callback_token_id": "cbt_run-a",
        "callback_token": "secret",
        "callback_base_url": "http://ai-platform.test",
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


def sensitive_task_payload(callback_url: str) -> dict[str, object]:
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


def test_executor_health_returns_ready(tmp_path):
    client = TestClient(create_executor_app(workspace_root=tmp_path))

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


def test_executor_execute_posts_running_and_completed_callbacks(tmp_path):
    callbacks = []

    def callback_sender(url, payload, token):
        callbacks.append((url, payload, token))
        return {"accepted": True}

    client = TestClient(
        create_executor_app(workspace_root=tmp_path, callback_sender=callback_sender)
    )

    response = client.post("/v1/tasks/execute", json=task_payload("http://platform/callback"))

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


def test_executor_execute_records_claude_sdk_evidence_when_enabled(tmp_path, monkeypatch):
    callbacks = []
    calls = []

    async def sdk_runner(**kwargs):
        calls.append(kwargs)
        assert [item[1]["status"] for item in callbacks] == ["running"]
        await kwargs["on_text"]("sandbox sdk completed")
        return SimpleNamespace(
            used_sdk=True,
            session_id="sdk-session-a",
            usage={"input_tokens": 1, "output_tokens": 2},
            error=None,
            message="sandbox sdk completed",
            used_skills=["general-chat"],
            used_skills_source="executor_hook",
        )

    monkeypatch.setattr(
        "app.runtime.sandbox.executor_app.get_settings",
        lambda: SimpleNamespace(claude_agent_sdk_enabled=True),
    )

    def callback_sender(url, payload, token):
        callbacks.append((url, payload, token))
        return {"accepted": True}

    client = TestClient(
        create_executor_app(
            workspace_root=tmp_path,
            callback_sender=callback_sender,
            sdk_runner=sdk_runner,
        )
    )
    payload = task_payload("http://platform/callback")
    payload["config"]["skill_ids"] = ["general-chat"]

    response = client.post("/v1/tasks/execute", json=payload)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "accepted"
    assert body["sdk_used"] is True
    assert body["executor_mode"] == "claude_agent_sdk"
    assert body["sdk_session_id"] == "sdk-session-a"
    assert body["used_skill_ids"] == ["general-chat"]
    assert calls[0]["prompt"] == "hello executor"
    assert calls[0]["cwd"] == tmp_path
    assert calls[0]["skill_id"] == "general-chat"
    assert calls[0]["skills"] == ["general-chat"]
    assert calls[0]["model_id"] == "deepseek-v4-flash"
    assert [item[1]["status"] for item in callbacks] == ["running", "completed"]
    assert callbacks[-1][1]["sdk_session_id"] == "sdk-session-a"
    assert callbacks[-1][1]["new_message"] is None
    assert callbacks[-1][1]["state_patch"]["sdk_used"] is True
    assert callbacks[-1][1]["state_patch"]["executor_mode"] == "claude_agent_sdk"


def test_executor_execute_reports_claude_sdk_error_without_leaking_details(tmp_path, monkeypatch):
    callbacks = []

    async def sdk_runner(**kwargs):
        return SimpleNamespace(
            used_sdk=True,
            session_id=None,
            usage={},
            error="provider token=secret failed at /tmp/workspace",
            message="",
            used_skills=[],
            used_skills_source="",
        )

    monkeypatch.setattr(
        "app.runtime.sandbox.executor_app.get_settings",
        lambda: SimpleNamespace(claude_agent_sdk_enabled=True),
    )

    def callback_sender(url, payload, token):
        callbacks.append(payload)
        return {"accepted": True}

    client = TestClient(
        create_executor_app(
            workspace_root=tmp_path,
            callback_sender=callback_sender,
            sdk_runner=sdk_runner,
        )
    )

    response = client.post("/v1/tasks/execute", json=task_payload("http://platform/callback"))

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "failed"
    assert body["sdk_used"] is True
    assert body["executor_mode"] == "claude_agent_sdk"
    assert body["error_code"] == "claude_agent_sdk_runtime_error"
    assert body["error_message"] == "Claude Agent SDK execution failed"
    assert "secret" not in str(body)
    assert str(tmp_path) not in str(body)
    assert [item["status"] for item in callbacks] == ["running", "failed"]
    assert callbacks[-1]["error_message"] == "Claude Agent SDK execution failed"
    assert "secret" not in str(callbacks)
    assert str(tmp_path) not in str(callbacks)


def test_executor_execute_reports_platform_timeout_probe_as_failed_callback(tmp_path):
    callbacks = []
    payload = task_payload("http://platform/callback")
    payload["config"]["resource_limits"] = {"max_seconds": 0}

    def callback_sender(url, payload, token):
        callbacks.append((url, payload, token))
        return {"accepted": True}

    client = TestClient(create_executor_app(workspace_root=tmp_path, callback_sender=callback_sender))

    response = client.post("/v1/tasks/execute", json=payload)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "failed"
    assert body["run_id"] == "run-a"
    assert body["error_code"] == "executor_health_timeout"
    assert body["error_message"] == "Executor health timeout"
    assert [item[1]["status"] for item in callbacks] == ["running", "failed"]
    assert callbacks[-1][1]["error_message"] == "Executor health timeout"
    assert callbacks[-1][1]["state_patch"] == {"error_code": "executor_health_timeout"}
    assert str(tmp_path) not in str(body)


def test_executor_execute_does_not_truncate_fractional_positive_timeout(tmp_path):
    callbacks = []
    payload = task_payload("http://platform/callback")
    payload["config"]["resource_limits"] = {"max_seconds": 0.5}

    def callback_sender(url, payload, token):
        callbacks.append((url, payload, token))
        return {"accepted": True}

    client = TestClient(create_executor_app(workspace_root=tmp_path, callback_sender=callback_sender))

    response = client.post("/v1/tasks/execute", json=payload)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "accepted"
    assert "error_code" not in body
    assert [item[1]["status"] for item in callbacks] == ["running", "completed"]


def test_executor_execute_writes_runtime_marker_without_host_path(tmp_path):
    client = TestClient(
        create_executor_app(
            workspace_root=tmp_path,
            callback_sender=lambda url, payload, token: {},
        )
    )

    response = client.post("/v1/tasks/execute", json=task_payload("http://platform/callback"))

    assert response.status_code == 200
    marker = Path(tmp_path) / "runtime" / "run-a.json"
    content = marker.read_text(encoding="utf-8")
    assert "prompt_length" in content
    assert "hello executor" not in content
    assert str(tmp_path) not in content


def test_executor_marker_redacts_unapproved_config_and_tokens(tmp_path):
    client = TestClient(
        create_executor_app(
            workspace_root=tmp_path,
            callback_sender=lambda url, payload, token: {},
        )
    )

    response = client.post("/v1/tasks/execute", json=sensitive_task_payload("http://platform/callback"))

    assert response.status_code == 200
    content = (Path(tmp_path) / "runtime" / "run-a.json").read_text(encoding="utf-8")
    assert "secret-key" not in content
    assert "Authorization" not in content
    assert "/runtime/tenants" not in content
    assert "nested-secret" not in content
    assert "safe-skill" in content
    assert "deepseek-v4-flash" in content
    assert "secret" not in content


def test_executor_execute_reports_callback_errors_without_raising(tmp_path):
    callbacks = []

    def callback_sender(url, payload, token):
        callbacks.append(payload["status"])
        if payload["status"] == "completed":
            raise RuntimeError("callback failed")
        return {"accepted": True}

    client = TestClient(create_executor_app(workspace_root=tmp_path, callback_sender=callback_sender))

    response = client.post("/v1/tasks/execute", json=task_payload("http://platform/callback"))

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "accepted"
    assert body["run_id"] == "run-a"
    assert body["callback_errors"] == ["completed"]
    assert isinstance(body["executor_model_latency_ms"], int)
    assert isinstance(body["document_processing_latency_ms"], int)
    assert callbacks == ["running", "completed"]


def test_executor_completed_callback_marker_path_is_container_path(tmp_path):
    callbacks = []

    def callback_sender(url, payload, token):
        callbacks.append(payload)
        return {"accepted": True}

    client = TestClient(create_executor_app(workspace_root=tmp_path, callback_sender=callback_sender))

    response = client.post("/v1/tasks/execute", json=task_payload("http://platform/callback"))

    assert response.status_code == 200
    marker_path = callbacks[-1]["state_patch"]["marker_path"]
    assert marker_path == "/workspace/runtime/run-a.json"
    assert str(tmp_path) not in marker_path
