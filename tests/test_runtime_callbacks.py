import hashlib
import hmac

from fastapi.testclient import TestClient

from app.main import create_app
from app.runtime.sandbox.contracts import ExecutorCallbackEvent


def derived_callback_token(secret: str, token_id: str = "cbt_run-a") -> str:
    return hmac.new(secret.encode("utf-8"), token_id.encode("utf-8"), hashlib.sha256).hexdigest()


def callback_payload(**overrides):
    payload = {
        "session_id": "session-a",
        "run_id": "run-a",
        "callback_token_id": "cbt_run-a",
        "status": "running",
        "progress": 20,
        "new_message": {"type": "assistant", "delta": "hello"},
        "state_patch": {"current_step": "thinking"},
        "sdk_session_id": "sdk-session-a",
        "error_message": None,
    }
    payload.update(overrides)
    return payload


def callback_settings(token: str):
    return type("S", (), {"sandbox_callback_token": token})()


def patch_callback_settings(monkeypatch, settings_obj):
    try:
        import app.routes.runtime_callbacks as runtime_callbacks
    except ModuleNotFoundError:
        monkeypatch.setattr("app.settings.get_settings", lambda: settings_obj)
    else:
        monkeypatch.setattr(runtime_callbacks, "get_settings", lambda: settings_obj)


def test_executor_callback_requires_valid_token(monkeypatch):
    patch_callback_settings(monkeypatch, callback_settings("secret"))
    client = TestClient(create_app())

    response = client.post("/api/ai/runtime/callbacks/executor", json=callback_payload())

    assert response.status_code == 401
    assert response.json() == {"detail": "invalid_callback_token"}


def test_executor_callback_rejects_wrong_token(monkeypatch):
    patch_callback_settings(monkeypatch, callback_settings("secret"))
    client = TestClient(create_app())

    response = client.post(
        "/api/ai/runtime/callbacks/executor",
        headers={"X-AI-Platform-Callback-Token": "wrong"},
        json=callback_payload(),
    )

    assert response.status_code == 401
    assert response.json() == {"detail": "invalid_callback_token"}


def test_executor_callback_rejects_cross_run_token_id(monkeypatch):
    patch_callback_settings(monkeypatch, callback_settings("secret"))
    client = TestClient(create_app())

    response = client.post(
        "/api/ai/runtime/callbacks/executor",
        headers={"X-AI-Platform-Callback-Token": derived_callback_token("secret", "cbt_other_run")},
        json=callback_payload(callback_token_id="cbt_run-a"),
    )

    assert response.status_code == 401
    assert response.json() == {"detail": "invalid_callback_token"}


def test_executor_callback_rejects_valid_foreign_run_token_pair(monkeypatch):
    patch_callback_settings(monkeypatch, callback_settings("secret"))

    import app.routes.runtime_callbacks as runtime_callbacks

    async def fail_record_executor_callback(callback):
        raise AssertionError("foreign run token must be rejected before recording")

    monkeypatch.setattr(runtime_callbacks, "record_executor_callback", fail_record_executor_callback)
    client = TestClient(create_app())

    response = client.post(
        "/api/ai/runtime/callbacks/executor",
        headers={"X-AI-Platform-Callback-Token": derived_callback_token("secret", "cbt_run-a")},
        json=callback_payload(run_id="run-b", session_id="session-b", callback_token_id="cbt_run-a"),
    )

    assert response.status_code == 401
    assert response.json() == {"detail": "invalid_callback_token"}


def test_executor_callback_requires_callback_token_id(monkeypatch):
    patch_callback_settings(monkeypatch, callback_settings("secret"))
    payload = callback_payload()
    payload.pop("callback_token_id")
    client = TestClient(create_app())

    response = client.post(
        "/api/ai/runtime/callbacks/executor",
        headers={"X-AI-Platform-Callback-Token": derived_callback_token("secret")},
        json=payload,
    )

    assert response.status_code == 422


def test_executor_callback_rejects_when_token_not_configured(monkeypatch):
    patch_callback_settings(monkeypatch, callback_settings(""))

    import app.routes.runtime_callbacks as runtime_callbacks

    async def fail_record_executor_callback(callback):
        raise AssertionError("callback must fail closed when token is not configured")

    monkeypatch.setattr(runtime_callbacks, "record_executor_callback", fail_record_executor_callback)
    client = TestClient(create_app())

    response = client.post(
        "/api/ai/runtime/callbacks/executor",
        headers={"X-AI-Platform-Callback-Token": "provided"},
        json=callback_payload(),
    )

    assert response.status_code == 503
    assert response.json() == {"detail": "callback_token_not_configured"}


def test_executor_callback_accepts_valid_event_and_records_callback(monkeypatch):
    patch_callback_settings(monkeypatch, callback_settings("secret"))
    recorded = []

    try:
        import app.routes.runtime_callbacks as runtime_callbacks
    except ModuleNotFoundError:
        runtime_callbacks = None
    else:
        async def fake_record_executor_callback(callback):
            recorded.append(callback)
            return {"accepted": True, "event_count": 1}

        monkeypatch.setattr(runtime_callbacks, "record_executor_callback", fake_record_executor_callback)

    client = TestClient(create_app())

    response = client.post(
        "/api/ai/runtime/callbacks/executor",
        headers={"X-AI-Platform-Callback-Token": derived_callback_token("secret")},
        json=callback_payload(),
    )

    assert response.status_code == 200
    assert response.json() == {"accepted": True, "event_count": 1}
    assert len(recorded) == 1
    assert isinstance(recorded[0], ExecutorCallbackEvent)
    assert recorded[0].session_id == "session-a"
    assert recorded[0].run_id == "run-a"
    assert recorded[0].callback_token_id == "cbt_run-a"


def test_runtime_tool_permission_callback_accepts_valid_request(monkeypatch):
    patch_callback_settings(monkeypatch, callback_settings("secret"))
    calls = []

    class FakeTransaction:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, exc_type, exc, traceback):
            return None

    async def fake_get_run_identity(conn, *, run_id, for_update=False):
        calls.append(("identity", run_id, for_update))
        return {"tenant_id": "tenant-a", "id": run_id, "session_id": "session-a", "status": "running"}

    async def fake_get_run(conn, *, tenant_id, run_id, for_update=False):
        calls.append(("run", tenant_id, run_id, for_update))
        return {
            "id": run_id,
            "tenant_id": tenant_id,
            "workspace_id": "workspace-a",
            "user_id": "user-a",
            "session_id": "session-a",
            "agent_id": "general-agent",
            "skill_id": "general-chat",
            "trace_id": "trace-run-a",
        }

    async def fake_resolve(**kwargs):
        calls.append(("resolve", kwargs))
        return {
            "allowed": False,
            "reason": "tool_permission_required",
            "risk_level": "high",
            "write_capable": True,
            "decision": "ask",
            "permission_request_id": "tpr-sdk",
        }

    import app.routes.runtime_callbacks as runtime_callbacks

    monkeypatch.setattr(runtime_callbacks, "transaction", lambda: FakeTransaction())
    monkeypatch.setattr(runtime_callbacks.repositories, "get_run_identity", fake_get_run_identity)
    monkeypatch.setattr(runtime_callbacks.repositories, "get_run", fake_get_run)
    monkeypatch.setattr(runtime_callbacks, "resolve_claude_sdk_tool_permission", fake_resolve)
    client = TestClient(create_app())

    response = client.post(
        "/api/ai/runtime/callbacks/tool-permission",
        headers={"X-AI-Platform-Callback-Token": derived_callback_token("secret")},
        json={
            "session_id": "session-a",
            "run_id": "run-a",
            "callback_token_id": "cbt_run-a",
            "tool_name": "Bash",
            "tool_input": {"command": "python write_business_system.py"},
            "tool_call_id": "tool-a",
            "action": "execute",
            "risk_level": "high",
            "write_capable": True,
            "reason": "needs shell",
            "permission_wait_seconds": 130,
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "allowed": False,
        "reason": "tool_permission_required",
        "risk_level": "high",
        "write_capable": True,
        "decision": "ask",
        "permission_request_id": "tpr-sdk",
    }
    assert calls[0] == ("identity", "run-a", True)
    assert calls[1] == ("run", "tenant-a", "run-a", True)
    resolve_call = calls[2][1]
    assert resolve_call["tenant_id"] == "tenant-a"
    assert resolve_call["workspace_id"] == "workspace-a"
    assert resolve_call["user_id"] == "user-a"
    assert resolve_call["session_id"] == "session-a"
    assert resolve_call["run_id"] == "run-a"
    assert resolve_call["wait_timeout_seconds"] == 130.0
    assert resolve_call["agent_id"] == "general-agent"
    assert resolve_call["skill_id"] == "general-chat"
    assert resolve_call["request"]["tool_input"] == {"command": "python write_business_system.py"}


def test_executor_callback_persists_callback_status_event(monkeypatch):
    patch_callback_settings(monkeypatch, callback_settings("secret"))
    calls = []

    class FakeTransaction:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, exc_type, exc, traceback):
            return None

    async def fake_get_run_identity(conn, *, run_id, for_update=False):
        calls.append(("identity", run_id, for_update))
        return {"tenant_id": "tenant-a", "id": run_id, "session_id": "session-a", "status": "running"}

    async def fake_append_event(conn, *, tenant_id, run_id, event_type, stage, message, payload):
        calls.append((event_type, stage, message, payload))
        return f"evt_{len(calls)}"

    import app.routes.runtime_callbacks as runtime_callbacks

    monkeypatch.setattr(runtime_callbacks, "transaction", lambda: FakeTransaction())
    monkeypatch.setattr(runtime_callbacks.repositories, "get_run_identity", fake_get_run_identity)
    monkeypatch.setattr(runtime_callbacks.repositories, "append_event", fake_append_event)
    client = TestClient(create_app())

    response = client.post(
        "/api/ai/runtime/callbacks/executor",
        headers={"X-AI-Platform-Callback-Token": derived_callback_token("secret")},
        json=callback_payload(status="completed", progress=100),
    )

    assert response.status_code == 200
    assert response.json() == {"accepted": True, "event_count": 2}
    assert calls[0] == ("identity", "run-a", True)
    assert calls[1][0:3] == ("executor_callback", "executor", "Executor callback: completed")
    assert calls[1][3]["callback_status"] == "completed"
    assert calls[1][3]["callback_token_id"] == "cbt_run-a"
    assert calls[1][3]["progress"] == 100
    assert calls[2][0:3] == ("run_completed", "runtime", "Executor completed")


def test_executor_callback_does_not_stop_runtime_container_from_callback(monkeypatch):
    patch_callback_settings(monkeypatch, callback_settings("secret"))
    calls = []

    class FakeTransaction:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, exc_type, exc, traceback):
            return None

    class FakeProvider:
        async def list_runtime_containers(self, filters):
            calls.append(("list", filters))
            return []

    async def fake_get_run_identity(conn, *, run_id, for_update=False):
        return {"tenant_id": "tenant-a", "id": run_id, "session_id": "session-a", "status": "running"}

    async def fake_append_event(conn, *, tenant_id, run_id, event_type, stage, message, payload):
        return "evt-a"

    import app.routes.runtime_callbacks as runtime_callbacks

    monkeypatch.setattr(runtime_callbacks, "transaction", lambda: FakeTransaction())
    monkeypatch.setattr(runtime_callbacks.repositories, "get_run_identity", fake_get_run_identity)
    monkeypatch.setattr(runtime_callbacks.repositories, "append_event", fake_append_event)
    monkeypatch.setattr(runtime_callbacks, "create_container_provider", lambda: FakeProvider(), raising=False)
    client = TestClient(create_app())

    response = client.post(
        "/api/ai/runtime/callbacks/executor",
        headers={"X-AI-Platform-Callback-Token": derived_callback_token("secret")},
        json=callback_payload(status="completed", progress=100),
    )

    assert response.status_code == 200
    assert response.json() == {"accepted": True, "event_count": 2}
    assert calls == []


def test_executor_callback_rejects_session_mismatch(monkeypatch):
    patch_callback_settings(monkeypatch, callback_settings("secret"))
    calls = []

    class FakeTransaction:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, exc_type, exc, traceback):
            return None

    async def fake_get_run_identity(conn, *, run_id, for_update=False):
        calls.append(("identity", run_id, for_update))
        return {"tenant_id": "tenant-a", "id": run_id, "session_id": "session-b", "status": "running"}

    async def fail_append_event(*args, **kwargs):
        raise AssertionError("mismatched callback must not append events")

    import app.routes.runtime_callbacks as runtime_callbacks

    monkeypatch.setattr(runtime_callbacks, "transaction", lambda: FakeTransaction())
    monkeypatch.setattr(runtime_callbacks.repositories, "get_run_identity", fake_get_run_identity)
    monkeypatch.setattr(runtime_callbacks.repositories, "append_event", fail_append_event)
    client = TestClient(create_app())

    response = client.post(
        "/api/ai/runtime/callbacks/executor",
        headers={"X-AI-Platform-Callback-Token": derived_callback_token("secret")},
        json=callback_payload(session_id="session-a", run_id="run-a", status="running"),
    )

    assert response.status_code == 409
    assert response.json() == {"detail": "callback_session_mismatch"}
    assert calls == [("identity", "run-a", True)]


def test_executor_callback_rejects_late_callback_for_terminal_run(monkeypatch):
    patch_callback_settings(monkeypatch, callback_settings("secret"))
    calls = []

    class FakeTransaction:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, exc_type, exc, traceback):
            return None

    async def fake_get_run_identity(conn, *, run_id, for_update=False):
        calls.append(("identity", run_id, for_update))
        return {"tenant_id": "tenant-a", "id": run_id, "session_id": "session-a", "status": "succeeded"}

    async def fail_append_event(*args, **kwargs):
        raise AssertionError("late callback must not append events")

    import app.routes.runtime_callbacks as runtime_callbacks

    monkeypatch.setattr(runtime_callbacks, "transaction", lambda: FakeTransaction())
    monkeypatch.setattr(runtime_callbacks.repositories, "get_run_identity", fake_get_run_identity)
    monkeypatch.setattr(runtime_callbacks.repositories, "append_event", fail_append_event)
    client = TestClient(create_app())

    response = client.post(
        "/api/ai/runtime/callbacks/executor",
        headers={"X-AI-Platform-Callback-Token": derived_callback_token("secret")},
        json=callback_payload(session_id="session-a", run_id="run-a", status="completed", progress=100),
    )

    assert response.status_code == 409
    assert response.json() == {"detail": "run_already_terminal"}
    assert calls == [("identity", "run-a", True)]


def test_executor_callback_persists_typed_events_with_standard_stages(monkeypatch):
    patch_callback_settings(monkeypatch, callback_settings("secret"))
    calls = []

    class FakeTransaction:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, exc_type, exc, traceback):
            return None

    async def fake_get_run_identity(conn, *, run_id, for_update=False):
        calls.append(("identity", run_id, for_update))
        return {"tenant_id": "tenant-a", "id": run_id, "session_id": "session-a", "status": "running"}

    async def fake_append_event(conn, *, tenant_id, run_id, event_type, stage, message, payload):
        calls.append((event_type, stage, message, payload))
        return f"evt_{len(calls)}"

    import app.routes.runtime_callbacks as runtime_callbacks

    monkeypatch.setattr(runtime_callbacks, "transaction", lambda: FakeTransaction())
    monkeypatch.setattr(runtime_callbacks.repositories, "get_run_identity", fake_get_run_identity)
    monkeypatch.setattr(runtime_callbacks.repositories, "append_event", fake_append_event)
    client = TestClient(create_app())

    response = client.post(
        "/api/ai/runtime/callbacks/executor",
        headers={"X-AI-Platform-Callback-Token": derived_callback_token("secret")},
        json=callback_payload(
            status="running",
            progress=50,
            new_message=None,
            state_patch={"current_step": "accepted"},
            events=[
                {
                    "type": "checkpoint_created",
                    "message": "checkpoint saved",
                    "payload": {"checkpoint_id": "checkpoint-a", "step_key": "code"},
                },
                {
                    "type": "subagent_started",
                    "message": "reviewer started",
                    "payload": {"subagent_id": "reviewer-1", "step_key": "review"},
                },
                {
                    "type": "agent_step_completed",
                    "message": "code agent completed",
                    "payload": {"step_key": "code", "step_index": 1, "output": "done"},
                },
            ],
        ),
    )

    assert response.status_code == 200
    assert response.json() == {"accepted": True, "event_count": 5}
    persisted = [call for call in calls if call[0] != "identity"]
    assert [item[0:3] for item in persisted] == [
        ("executor_callback", "executor", "Executor callback: running"),
        ("tool_call_delta", "tool", "accepted"),
        ("checkpoint_created", "checkpoint", "checkpoint saved"),
        ("subagent_started", "subagent", "reviewer started"),
        ("agent_step_completed", "agent", "code agent completed"),
    ]
    assert persisted[2][3]["checkpoint_id"] == "checkpoint-a"
    assert persisted[2][3]["source"] == "executor_callback"
    assert persisted[4][3]["visible_to_user"] is True


def test_executor_callback_typed_admin_only_event_stays_hidden(monkeypatch):
    patch_callback_settings(monkeypatch, callback_settings("secret"))
    calls = []

    class FakeTransaction:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, exc_type, exc, traceback):
            return None

    async def fake_get_run_identity(conn, *, run_id, for_update=False):
        return {"tenant_id": "tenant-a", "id": run_id, "session_id": "session-a", "status": "running"}

    async def fake_append_event(conn, *, tenant_id, run_id, event_type, stage, message, payload):
        calls.append((event_type, stage, message, payload))
        return f"evt_{len(calls)}"

    import app.routes.runtime_callbacks as runtime_callbacks

    monkeypatch.setattr(runtime_callbacks, "transaction", lambda: FakeTransaction())
    monkeypatch.setattr(runtime_callbacks.repositories, "get_run_identity", fake_get_run_identity)
    monkeypatch.setattr(runtime_callbacks.repositories, "append_event", fake_append_event)
    client = TestClient(create_app())

    response = client.post(
        "/api/ai/runtime/callbacks/executor",
        headers={"X-AI-Platform-Callback-Token": derived_callback_token("secret")},
        json=callback_payload(
            status="running",
            progress=50,
            new_message=None,
            state_patch={},
            events=[
                {
                    "type": "browser_snapshot",
                    "message": "browser state captured",
                    "payload": {"url": "https://example.test", "visible_to_user": True},
                    "admin_only": True,
                }
            ],
        ),
    )

    assert response.status_code == 200
    browser_event = next(call for call in calls if call[0] == "browser_snapshot")
    assert browser_event[1] == "browser"
    assert browser_event[3]["visible_to_user"] is False
    assert browser_event[3]["admin_only"] is True
    assert browser_event[3]["source"] == "executor_callback"
