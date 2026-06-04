from pathlib import Path

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
    assert response.json() == {"status": "accepted", "run_id": "run-a"}
    assert [item[1]["status"] for item in callbacks] == ["running", "completed"]
    assert {item[2] for item in callbacks} == {"secret"}
    assert {item[1]["callback_token_id"] for item in callbacks} == {"cbt_run-a"}
    assert callbacks[0][1]["progress"] == 5
    assert callbacks[1][1]["progress"] == 100


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
    assert response.json() == {
        "status": "accepted",
        "run_id": "run-a",
        "callback_errors": ["completed"],
    }
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
