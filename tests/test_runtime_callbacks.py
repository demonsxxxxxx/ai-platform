import hashlib
import hmac
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import repositories
from app.auth import AuthPrincipal
from app.main import create_app
from app.routes import lambchat_compat
from app.runtime.sandbox.callback_tokens import (
    CallbackTokenBinding,
    callback_token_id_for_binding,
)
from app.runtime.sandbox.contracts import (
    ExecutorCallbackEvent,
    ExecutorToolPermissionRequest,
)


def derived_callback_token(secret: str, token_id: str = "cbt:run-a:attempt-a") -> str:
    return hmac.new(secret.encode("utf-8"), token_id.encode("utf-8"), hashlib.sha256).hexdigest()


def callback_payload(**overrides):
    payload = {
        "session_id": "session-a",
        "run_id": "run-a",
        "attempt_id": "attempt-a",
        "callback_token_id": "cbt:run-a:attempt-a",
        "status": "running",
        "progress": 20,
        "new_message": {"type": "assistant", "delta": "hello"},
        "state_patch": {"current_step": "thinking"},
        "sdk_session_id": "sdk-session-a",
        "error_message": None,
    }
    payload.update(overrides)
    if payload["status"] in {"completed", "failed", "cancelled"} and "terminal_result" not in payload:
        payload["terminal_result"] = {
            "status": payload["status"],
            "run_id": payload["run_id"],
            "message": "done" if payload["status"] == "completed" else "",
            **(
                {"error_code": "executor_failed", "error_message": "Executor failed"}
                if payload["status"] == "failed"
                else {}
            ),
        }
    return payload


def callback_settings(token: str, *, lease_ttl_seconds: int = 1800):
    return type(
        "S",
        (),
        {
            "sandbox_callback_token": token,
            "sandbox_lease_ttl_seconds": lease_ttl_seconds,
        },
    )()


def patch_callback_settings(monkeypatch, settings_obj):
    try:
        from app.routes import runtime_callbacks
    except ModuleNotFoundError:
        monkeypatch.setattr("app.settings.get_settings", lambda: settings_obj)
    else:
        monkeypatch.setattr(runtime_callbacks, "get_settings", lambda: settings_obj)


def patch_active_attempt(
    monkeypatch,
    runtime_callbacks,
    attempt_id="attempt-a",
    lease_id: str | None = None,
):
    active_attempt = attempt_id

    async def list_current_leases(conn, *, tenant_id, run_id, attempt_id):
        if attempt_id != active_attempt:
            return []
        lease = {"lease_payload_json": {"attempt_id": active_attempt}}
        if lease_id is not None:
            lease["id"] = lease_id
        return [lease]

    async def ignore_terminal_signal(**_kwargs):
        return None

    monkeypatch.setattr(
        runtime_callbacks.repositories,
        "list_current_sandbox_runtime_leases_for_attempt",
        list_current_leases,
    )
    monkeypatch.setattr(
        runtime_callbacks,
        "publish_executor_terminal_signal",
        ignore_terminal_signal,
    )


def test_parallel_same_run_attempts_each_use_their_exact_lease_and_token(monkeypatch):
    patch_callback_settings(monkeypatch, callback_settings("secret"))
    lease_checks = []
    events = []

    class FakeTransaction:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, exc_type, exc, traceback):
            return None

    async def get_run_identity(conn, *, run_id, for_update=False):
        return {"tenant_id": "tenant-a", "session_id": "session-a", "status": "running"}

    async def exact_lease(conn, *, tenant_id, run_id, attempt_id):
        lease_checks.append((tenant_id, run_id, attempt_id))
        if attempt_id not in {"attempt-a", "attempt-b"}:
            return []
        return [{"lease_payload_json": {"attempt_id": attempt_id}}]

    async def append_event(conn, **kwargs):
        events.append(kwargs)
        return f"evt-{len(events)}"

    from app.routes import runtime_callbacks

    monkeypatch.setattr(runtime_callbacks, "transaction", lambda: FakeTransaction())
    monkeypatch.setattr(runtime_callbacks.repositories, "get_run_identity", get_run_identity)
    monkeypatch.setattr(runtime_callbacks.repositories, "list_current_sandbox_runtime_leases_for_attempt", exact_lease)
    monkeypatch.setattr(runtime_callbacks.repositories, "append_event", append_event)
    client = TestClient(create_app())

    first = client.post(
        "/api/ai/runtime/callbacks/executor",
        headers={"X-AI-Platform-Callback-Token": derived_callback_token("secret")},
        json=callback_payload(new_message=None, state_patch={}),
    )
    second_token_id = "cbt:run-a:attempt-b"
    second = client.post(
        "/api/ai/runtime/callbacks/executor",
        headers={"X-AI-Platform-Callback-Token": derived_callback_token("secret", second_token_id)},
        json=callback_payload(
            attempt_id="attempt-b",
            callback_token_id=second_token_id,
            new_message=None,
            state_patch={},
        ),
    )
    crossed = client.post(
        "/api/ai/runtime/callbacks/executor",
        headers={"X-AI-Platform-Callback-Token": derived_callback_token("secret")},
        json=callback_payload(attempt_id="attempt-b", callback_token_id="cbt:run-a:attempt-a"),
    )

    assert first.status_code == second.status_code == 200
    assert crossed.status_code == 401
    assert lease_checks == [
        ("tenant-a", "run-a", "attempt-a"),
        ("tenant-a", "run-a", "attempt-a"),
        ("tenant-a", "run-a", "attempt-b"),
        ("tenant-a", "run-a", "attempt-b"),
    ]
    assert [event["payload"]["attempt_id"] for event in events] == ["attempt-a", "attempt-b"]


@pytest.mark.asyncio
async def test_current_runtime_lease_query_locks_only_the_exact_attempt():
    observed = []

    class Cursor:
        async def fetchall(self):
            return [{"id": "lease-attempt-b"}]

    class Connection:
        async def execute(self, query, parameters):
            observed.append((query, parameters))
            return Cursor()

    rows = await repositories.list_current_sandbox_runtime_leases_for_attempt(
        Connection(),
        tenant_id="tenant-a",
        run_id="run-a",
        attempt_id="attempt-b",
    )

    assert rows == [{"id": "lease-attempt-b"}]
    query, parameters = observed[0]
    assert "lease_payload_json ->> 'attempt_id' = %s" in query
    assert "status = 'active'" in query and "for update" in query
    assert parameters == ("tenant-a", "run-a", "attempt-b")


def test_executor_callback_rejects_duplicate_exact_attempt_leases(monkeypatch):
    patch_callback_settings(monkeypatch, callback_settings("secret"))

    class FakeTransaction:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, exc_type, exc, traceback):
            return None

    async def get_run_identity(conn, *, run_id, for_update=False):
        return {"tenant_id": "tenant-a", "session_id": "session-a", "status": "running"}

    async def duplicate_leases(conn, *, tenant_id, run_id, attempt_id):
        lease = {"lease_payload_json": {"attempt_id": attempt_id}}
        return [lease, dict(lease)]

    async def fail_append_event(*args, **kwargs):
        raise AssertionError("ambiguous exact attempt must not append events")

    from app.routes import runtime_callbacks

    monkeypatch.setattr(runtime_callbacks, "transaction", lambda: FakeTransaction())
    monkeypatch.setattr(runtime_callbacks.repositories, "get_run_identity", get_run_identity)
    monkeypatch.setattr(
        runtime_callbacks.repositories,
        "list_current_sandbox_runtime_leases_for_attempt",
        duplicate_leases,
    )
    monkeypatch.setattr(runtime_callbacks.repositories, "append_event", fail_append_event)

    response = TestClient(create_app()).post(
        "/api/ai/runtime/callbacks/executor",
        headers={"X-AI-Platform-Callback-Token": derived_callback_token("secret")},
        json=callback_payload(),
    )

    assert response.status_code == 409
    assert response.json() == {"detail": "sandbox_runtime_attempt_inactive"}


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
        headers={"X-AI-Platform-Callback-Token": derived_callback_token("secret", "cbt:other-run:attempt-a")},
        json=callback_payload(callback_token_id="cbt:run-a:attempt-a"),
    )

    assert response.status_code == 401
    assert response.json() == {"detail": "invalid_callback_token"}


def test_executor_callback_rejects_valid_foreign_run_token_pair(monkeypatch):
    patch_callback_settings(monkeypatch, callback_settings("secret"))

    from app.routes import runtime_callbacks

    async def fail_record_executor_callback(callback):
        raise AssertionError("foreign run token must be rejected before recording")

    monkeypatch.setattr(runtime_callbacks, "record_executor_callback", fail_record_executor_callback)
    client = TestClient(create_app())

    response = client.post(
        "/api/ai/runtime/callbacks/executor",
        headers={"X-AI-Platform-Callback-Token": derived_callback_token("secret", "cbt:run-a:attempt-a")},
        json=callback_payload(run_id="run-b", session_id="session-b", callback_token_id="cbt:run-a:attempt-a"),
    )

    assert response.status_code == 401
    assert response.json() == {"detail": "invalid_callback_token"}


def test_executor_callback_rejects_valid_token_for_prefix_extended_binding(monkeypatch):
    patch_callback_settings(monkeypatch, callback_settings("secret"))
    extended = "cbt:run-a:attempt-a:container-a"

    response = TestClient(create_app()).post(
        "/api/ai/runtime/callbacks/executor",
        headers={"X-AI-Platform-Callback-Token": derived_callback_token("secret", extended)},
        json=callback_payload(callback_token_id=extended),
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


def test_executor_callback_requires_attempt_id(monkeypatch):
    patch_callback_settings(monkeypatch, callback_settings("secret"))
    payload = callback_payload()
    payload.pop("attempt_id")

    response = TestClient(create_app()).post(
        "/api/ai/runtime/callbacks/executor",
        headers={"X-AI-Platform-Callback-Token": derived_callback_token("secret")},
        json=payload,
    )

    assert response.status_code == 422


def test_callback_token_id_rotates_for_each_exact_attempt():
    first = callback_token_id_for_binding(CallbackTokenBinding(run_id="run-a", attempt_id="attempt-a"))
    second = callback_token_id_for_binding(CallbackTokenBinding(run_id="run-a", attempt_id="attempt-b"))

    assert first == "cbt:run-a:attempt-a"
    assert second == "cbt:run-a:attempt-b"
    assert first != second
    assert derived_callback_token("secret", first) != derived_callback_token("secret", second)


def test_executor_callback_rejects_stale_attempt_before_event_action(monkeypatch):
    patch_callback_settings(monkeypatch, callback_settings("secret"))
    calls = []

    class FakeTransaction:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, exc_type, exc, traceback):
            return None

    async def get_run_identity(conn, *, run_id, for_update=False):
        return {"tenant_id": "tenant-a", "session_id": "session-a", "status": "running"}

    async def list_current_leases(conn, *, tenant_id, run_id, attempt_id):
        calls.append((tenant_id, run_id, attempt_id))
        return [{"lease_payload_json": {"attempt_id": "attempt-b"}}]

    async def fail_append_event(*args, **kwargs):
        raise AssertionError("stale attempt must not append an event")

    from app.routes import runtime_callbacks

    monkeypatch.setattr(runtime_callbacks, "transaction", lambda: FakeTransaction())
    monkeypatch.setattr(runtime_callbacks.repositories, "get_run_identity", get_run_identity)
    monkeypatch.setattr(
        runtime_callbacks.repositories,
        "list_current_sandbox_runtime_leases_for_attempt",
        list_current_leases,
    )
    monkeypatch.setattr(runtime_callbacks.repositories, "append_event", fail_append_event)

    response = TestClient(create_app()).post(
        "/api/ai/runtime/callbacks/executor",
        headers={"X-AI-Platform-Callback-Token": derived_callback_token("secret")},
        json=callback_payload(),
    )

    assert response.status_code == 409
    assert response.json() == {"detail": "sandbox_runtime_attempt_mismatch"}
    assert calls == [("tenant-a", "run-a", "attempt-a")]


def test_executor_callback_rejects_released_attempt_before_event_action(monkeypatch):
    patch_callback_settings(monkeypatch, callback_settings("secret"))

    class FakeTransaction:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, exc_type, exc, traceback):
            return None

    async def get_run_identity(conn, *, run_id, for_update=False):
        return {"tenant_id": "tenant-a", "session_id": "session-a", "status": "running"}

    async def no_current_leases(conn, *, tenant_id, run_id, attempt_id):
        return []

    async def fail_append_event(*args, **kwargs):
        raise AssertionError("released attempt must not append an event")

    from app.routes import runtime_callbacks

    monkeypatch.setattr(runtime_callbacks, "transaction", lambda: FakeTransaction())
    monkeypatch.setattr(runtime_callbacks.repositories, "get_run_identity", get_run_identity)
    monkeypatch.setattr(
        runtime_callbacks.repositories,
        "list_current_sandbox_runtime_leases_for_attempt",
        no_current_leases,
    )
    monkeypatch.setattr(runtime_callbacks.repositories, "append_event", fail_append_event)

    response = TestClient(create_app()).post(
        "/api/ai/runtime/callbacks/executor",
        headers={"X-AI-Platform-Callback-Token": derived_callback_token("secret")},
        json=callback_payload(),
    )

    assert response.status_code == 409
    assert response.json() == {"detail": "sandbox_runtime_attempt_inactive"}


def test_executor_callback_rejects_when_token_not_configured(monkeypatch):
    patch_callback_settings(monkeypatch, callback_settings(""))

    from app.routes import runtime_callbacks

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
        from app.routes import runtime_callbacks
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
    assert recorded[0].attempt_id == "attempt-a"
    assert recorded[0].callback_token_id == "cbt:run-a:attempt-a"


def test_runtime_tool_permission_callback_is_retired_without_resolver_access():
    client = TestClient(create_app())
    response = client.post("/api/ai/runtime/callbacks/tool-permission", json={"tool_name": "Bash"})

    assert response.status_code == 410
    assert response.json()["detail"] == "tool_permission_runtime_approval_removed"
    assert ExecutorToolPermissionRequest.model_fields["attempt_id"].is_required()

def test_executor_callback_persists_terminal_receipt_without_public_terminal_event(monkeypatch):
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

    async def fake_record_terminal(conn, **kwargs):
        calls.append(("terminal", kwargs))
        return {"id": kwargs["lease_id"]}

    from app.routes import runtime_callbacks

    monkeypatch.setattr(runtime_callbacks, "transaction", lambda: FakeTransaction())
    monkeypatch.setattr(runtime_callbacks.repositories, "get_run_identity", fake_get_run_identity)
    monkeypatch.setattr(runtime_callbacks.repositories, "append_event", fake_append_event)
    monkeypatch.setattr(
        runtime_callbacks.sandbox_lease_repository,
        "record_sandbox_executor_terminal",
        fake_record_terminal,
    )
    signaled = []

    async def record_signal(**kwargs):
        signaled.append(kwargs)

    patch_active_attempt(monkeypatch, runtime_callbacks, lease_id="lease-a")
    monkeypatch.setattr(
        runtime_callbacks,
        "publish_executor_terminal_signal",
        record_signal,
    )
    client = TestClient(create_app())

    response = client.post(
        "/api/ai/runtime/callbacks/executor",
        headers={"X-AI-Platform-Callback-Token": derived_callback_token("secret")},
        json=callback_payload(
            status="completed",
            progress=100,
            new_message=None,
            state_patch={},
        ),
    )

    assert response.status_code == 200
    assert response.json() == {"accepted": True, "event_count": 1}
    terminal_calls = [item for item in calls if item[0] == "terminal"]
    assert len(terminal_calls) == 1
    assert terminal_calls[0][1]["lease_id"] == "lease-a"
    assert terminal_calls[0][1]["terminal_result"]["status"] == "completed"
    assert signaled == [{}]


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

    async def fake_record_terminal(conn, **kwargs):
        return {"id": kwargs["lease_id"]}

    from app.routes import runtime_callbacks

    monkeypatch.setattr(runtime_callbacks, "transaction", lambda: FakeTransaction())
    monkeypatch.setattr(runtime_callbacks.repositories, "get_run_identity", fake_get_run_identity)
    monkeypatch.setattr(runtime_callbacks.repositories, "append_event", fake_append_event)
    monkeypatch.setattr(
        runtime_callbacks.sandbox_lease_repository,
        "record_sandbox_executor_terminal",
        fake_record_terminal,
    )
    patch_active_attempt(monkeypatch, runtime_callbacks, lease_id="lease-a")
    monkeypatch.setattr(runtime_callbacks, "create_container_provider", lambda: FakeProvider(), raising=False)
    client = TestClient(create_app())

    response = client.post(
        "/api/ai/runtime/callbacks/executor",
        headers={"X-AI-Platform-Callback-Token": derived_callback_token("secret")},
        json=callback_payload(
            status="completed", progress=100, new_message=None, state_patch={}
        ),
    )

    assert response.status_code == 200
    assert response.json() == {"accepted": True, "event_count": 1}
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

    from app.routes import runtime_callbacks

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

    from app.routes import runtime_callbacks

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

    from app.routes import runtime_callbacks

    monkeypatch.setattr(runtime_callbacks, "transaction", lambda: FakeTransaction())
    monkeypatch.setattr(runtime_callbacks.repositories, "get_run_identity", fake_get_run_identity)
    monkeypatch.setattr(runtime_callbacks.repositories, "append_event", fake_append_event)
    patch_active_attempt(monkeypatch, runtime_callbacks)
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
    assert response.json() == {"accepted": True, "event_count": 4}
    persisted = [call for call in calls if call[0] != "identity"]
    assert [item[0:3] for item in persisted] == [
        ("executor_callback", "executor", "Executor callback: running"),
        ("executor_private_event", "executor", "Executor event withheld from public projection"),
        ("executor_private_event", "executor", "Executor event withheld from public projection"),
        ("executor_private_event", "executor", "Executor event withheld from public projection"),
        ("executor_private_event", "executor", "Executor event withheld from public projection"),
    ]
    private_payloads = [item[3] for item in persisted[1:]]
    assert all(payload["visible_to_user"] is False for payload in private_payloads)
    assert all("checkpoint_id" not in payload for payload in private_payloads)
    assert all("reviewer started" not in str(payload) for payload in private_payloads)


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

    from app.routes import runtime_callbacks

    monkeypatch.setattr(runtime_callbacks, "transaction", lambda: FakeTransaction())
    monkeypatch.setattr(runtime_callbacks.repositories, "get_run_identity", fake_get_run_identity)
    monkeypatch.setattr(runtime_callbacks.repositories, "append_event", fake_append_event)
    patch_active_attempt(monkeypatch, runtime_callbacks)
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
    browser_event = next(call for call in calls if call[0] == "executor_private_event")
    assert browser_event[1] == "executor"
    assert browser_event[3] == {
        "source": "executor_callback",
        "source_event_type": "browser_snapshot",
        "source_class": "rejected",
        "visible_to_user": False,
    }


def test_executor_callback_persists_exact_timeline_for_chat_and_history(monkeypatch):
    patch_callback_settings(monkeypatch, callback_settings("secret"))
    persisted = []

    class FakeTransaction:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, exc_type, exc, traceback):
            return None

    async def fake_get_run_identity(conn, *, run_id, for_update=False):
        return {"tenant_id": "tenant-a", "id": run_id, "session_id": "session-a", "status": "running"}

    async def fake_append_event(conn, **event):
        persisted.append(event)
        return f"evt_{len(persisted)}"

    from app.routes import runtime_callbacks

    monkeypatch.setattr(runtime_callbacks, "transaction", lambda: FakeTransaction())
    monkeypatch.setattr(runtime_callbacks.repositories, "get_run_identity", fake_get_run_identity)
    monkeypatch.setattr(runtime_callbacks.repositories, "append_event", fake_append_event)
    patch_active_attempt(monkeypatch, runtime_callbacks)
    response = TestClient(create_app()).post(
        "/api/ai/runtime/callbacks/executor",
        headers={"X-AI-Platform-Callback-Token": derived_callback_token("secret")},
        json=callback_payload(
            new_message=None,
            state_patch={},
            events=[{
                "type": "execution_step",
                "message": "",
                "payload": {
                    "step_id": "pex_public_1",
                    "kind": "processing",
                    "stage": "execution",
                    "status": "running",
                    "title": "Process request",
                    "summary": "Running controlled processing",
                    "progress": {"current": 0, "total": 1},
                    "safe_file_name": None,
                    "artifact_public_id": None,
                },
            }],
        ),
    )

    assert response.status_code == 200
    execution = persisted[1]
    assert execution["event_type"] == "executor_private_event"
    assert execution["payload"] == {
        "source": "executor_callback",
        "source_event_type": "execution_step",
        "source_class": "rejected",
        "visible_to_user": False,
    }
    row = {
        "id": "evt_execution",
        "sequence": 2,
        "trace_id": "trace-timeline",
        "schema_version": "ai-platform.event-envelope.v1",
        "event_type": execution["event_type"],
        "stage": execution["stage"],
        "message": execution["message"],
        "severity": "info",
        "visible_to_user": False,
        "payload_json": execution["payload"],
        "created_at": "2026-07-27T00:00:00Z",
    }
    run = {
        "id": "run-a",
        "trace_id": "trace-timeline",
        "agent_id": "general-agent",
        "skill_id": "general-chat",
        "status": "running",
        "result_json": {},
        "error_code": None,
        "error_message": None,
    }
    principal = AuthPrincipal(
        user_id="user-a", display_name="User", tenant_id="tenant-a", roles=["user"]
    )

    records = lambchat_compat._compatibility_events_for_run(run, [row], [], principal)

    assert records == []


def _arbitrary_v2_step(
    *,
    event_type="execution_step",
    status="running",
    progress=None,
    safe_label="Caller selected Skill",
):
    payload = {
        "schema_version": "ai-platform.public-execution-event.v2",
        "step_id": "pex_caller_reused",
        "presentation_kind": "skill",
        "kind": "capability",
        "stage": "execution",
        "status": status,
        "progress": progress or {"current": 0, "total": 1},
    }
    if safe_label is not ...:
        payload["safe_label"] = safe_label
    return {"type": event_type, "message": "", "payload": payload}


@pytest.mark.parametrize(
    "events",
    [
        [_arbitrary_v2_step()],
        [
            _arbitrary_v2_step(
                event_type="execution_step_completed",
                status="completed",
                progress={"current": 1, "total": 1},
            )
        ],
        [
            _arbitrary_v2_step(
                event_type="execution_step_completed",
                status="completed",
                progress={"current": 1, "total": 1},
            ),
            _arbitrary_v2_step(
                event_type="execution_step_failed",
                status="failed",
                progress={"current": 1, "total": 1},
            ),
        ],
        [_arbitrary_v2_step(), _arbitrary_v2_step()],
        [_arbitrary_v2_step(safe_label=None)],
    ],
    ids=[
        "caller-selected-skill-label-and-kind",
        "terminal-without-start",
        "completed-then-failed",
        "step-replay-reuse",
        "null-safe-label",
    ],
)
def test_executor_callback_rejects_arbitrary_v2_lifecycles_without_public_persistence(
    monkeypatch,
    events,
):
    patch_callback_settings(monkeypatch, callback_settings("secret"))
    persisted = []

    class FakeTransaction:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, exc_type, exc, traceback):
            return None

    async def fake_get_run_identity(conn, *, run_id, for_update=False):
        return {
            "tenant_id": "tenant-a",
            "id": run_id,
            "session_id": "session-a",
            "status": "running",
        }

    async def fake_append_event(conn, **event):
        persisted.append(event)
        return f"evt_{len(persisted)}"

    from app.routes import runtime_callbacks

    monkeypatch.setattr(runtime_callbacks, "transaction", lambda: FakeTransaction())
    monkeypatch.setattr(
        runtime_callbacks.repositories,
        "get_run_identity",
        fake_get_run_identity,
    )
    monkeypatch.setattr(
        runtime_callbacks.repositories,
        "append_event",
        fake_append_event,
    )
    patch_active_attempt(monkeypatch, runtime_callbacks)

    response = TestClient(create_app()).post(
        "/api/ai/runtime/callbacks/executor",
        headers={"X-AI-Platform-Callback-Token": derived_callback_token("secret")},
        json=callback_payload(new_message=None, state_patch={}, events=events),
    )

    assert response.status_code == 200
    assert [event["event_type"] for event in persisted] == [
        "executor_callback",
        *["executor_private_event" for _event in events],
    ]
    assert not {
        "execution_step",
        "execution_step_completed",
        "execution_step_failed",
    } & {event["event_type"] for event in persisted}
    assert "Caller selected Skill" not in str(persisted)


def test_executor_callback_uses_adapter_events_and_pending_publisher(monkeypatch):
    patch_callback_settings(monkeypatch, callback_settings("secret"))
    persisted = []
    v4_rows = []
    publish_limits = []

    class FakeTransaction:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, exc_type, exc, traceback):
            return None

    async def fake_get_run_identity(conn, *, run_id, for_update=False):
        return {"tenant_id": "tenant-a", "id": run_id, "session_id": "session-a", "status": "running"}

    async def fake_append_batch(conn, **receipt):
        persisted.extend(receipt["events"])
        return {"callback_received_at": "2026-08-09T00:00:00Z"}

    async def fake_append_v4_rows(conn, **kwargs):
        v4_rows.append(kwargs)
        return ()

    async def fake_publish_pending(transaction_factory, *, limit):
        publish_limits.append(limit)

    from app.executors.claude.agent_events import ClaudeSdkAgentEventAdapter
    from app.routes import runtime_callbacks

    adapter = ClaudeSdkAgentEventAdapter(run_id="run-a", attempt_id="attempt-a")
    sdk_events = tuple(event.to_agent_event() for event in adapter.accept_answer_text("answer"))
    authority = SimpleNamespace(attempt_id="attempt-a", state="confirmed")
    async def fake_get_authority(conn, *, tenant_id, run_id, for_update=False):
        return authority

    monkeypatch.setattr(runtime_callbacks, "transaction", lambda: FakeTransaction())
    monkeypatch.setattr(runtime_callbacks.repositories, "get_run_identity", fake_get_run_identity)
    monkeypatch.setattr(runtime_callbacks.repositories, "append_event_batch", fake_append_batch)
    monkeypatch.setattr(runtime_callbacks, "append_callback_v4_rows", fake_append_v4_rows)
    monkeypatch.setattr(runtime_callbacks, "publish_pending_v4_events", fake_publish_pending)
    monkeypatch.setattr(runtime_callbacks, "get_stream_authority", fake_get_authority)
    patch_active_attempt(monkeypatch, runtime_callbacks)

    response = TestClient(create_app()).post(
        "/api/ai/runtime/callbacks/executor",
        headers={"X-AI-Platform-Callback-Token": derived_callback_token("secret")},
        json=callback_payload(
            batch_id="batch-a",
            new_message=None,
            state_patch={},
            events=[
                {
                    "type": "assistant_delta",
                    "message": "private callback text",
                    "payload": {"delta": "private callback payload"},
                },
                *[event.model_dump() for event in sdk_events],
            ],
        ),
    )

    assert response.status_code == 200
    assert response.json() == {"accepted": True, "batch_id": "batch-a", "event_count": 4}
    assert [event["event_type"] for event in persisted] == [
        "executor_callback",
        "executor_private_event",
        "executor_private_event",
        "executor_private_event",
    ]
    assert all(event["payload"]["visible_to_user"] is False for event in persisted[1:])
    assert all("delta" not in event["payload"] for event in persisted[1:])
    assert "private callback text" not in str(persisted)
    assert "private callback payload" not in str(persisted)
    assert len(v4_rows) == 1
    items = v4_rows[0]["items"]
    assert [item.callback_index for item in items] == [1, 2]
    assert [item.batch_index for item in items] == [1, 2]
    assert {item.message_id for item in items} == {adapter.message_id}
    assert adapter.message_id.startswith("msg_")
    assert publish_limits == [2]


@pytest.mark.parametrize("delta", ["", 7])
def test_executor_callback_rejects_empty_or_non_string_assistant_delta(monkeypatch, delta):
    patch_callback_settings(monkeypatch, callback_settings("secret"))
    persisted = []

    class FakeTransaction:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, exc_type, exc, traceback):
            return None

    async def fake_get_run_identity(conn, *, run_id, for_update=False):
        return {"tenant_id": "tenant-a", "id": run_id, "session_id": "session-a", "status": "running"}

    async def fake_append_event(conn, **event):
        persisted.append(event)
        return f"evt_{len(persisted)}"

    from app.routes import runtime_callbacks

    monkeypatch.setattr(runtime_callbacks, "transaction", lambda: FakeTransaction())
    monkeypatch.setattr(runtime_callbacks.repositories, "get_run_identity", fake_get_run_identity)
    monkeypatch.setattr(runtime_callbacks.repositories, "append_event", fake_append_event)
    patch_active_attempt(monkeypatch, runtime_callbacks)
    response = TestClient(create_app()).post(
        "/api/ai/runtime/callbacks/executor",
        headers={"X-AI-Platform-Callback-Token": derived_callback_token("secret")},
        json=callback_payload(
            new_message=None,
            state_patch={},
            events=[{"type": "assistant_delta", "message": "private", "payload": {"delta": delta}}],
        ),
    )

    assert response.status_code == 200
    assert response.json() == {"accepted": True, "event_count": 2}
    assert [event["event_type"] for event in persisted] == [
        "executor_callback",
        "executor_private_event",
    ]
    assert persisted[1]["payload"] == {
        "source": "executor_callback",
        "source_event_type": "assistant_delta",
        "source_class": "rejected",
        "visible_to_user": False,
    }
    assert "private" not in str(persisted[1]["payload"])


def test_heartbeat_callback_renews_lease_with_settings_ttl(monkeypatch):
    patch_callback_settings(
        monkeypatch,
        callback_settings("secret", lease_ttl_seconds=731),
    )
    heartbeat_calls = []

    class FakeTransaction:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, exc_type, exc, traceback):
            return None

    async def get_run_identity(conn, *, run_id, for_update=False):
        return {"tenant_id": "tenant-a", "session_id": "session-a", "status": "running"}

    async def exact_lease(conn, *, tenant_id, run_id, attempt_id):
        return [{"id": f"lease-{attempt_id}", "lease_payload_json": {"attempt_id": attempt_id}}]

    async def fake_heartbeat(conn, **kwargs):
        heartbeat_calls.append(kwargs)
        return {"id": kwargs["lease_id"], "executor_status": "running"}

    async def fake_append(conn, **kwargs):
        return f"evt-{len(heartbeat_calls)}"

    from app.routes import runtime_callbacks

    monkeypatch.setattr(runtime_callbacks, "transaction", lambda: FakeTransaction())
    monkeypatch.setattr(runtime_callbacks.repositories, "get_run_identity", get_run_identity)
    monkeypatch.setattr(runtime_callbacks.repositories, "list_current_sandbox_runtime_leases_for_attempt", exact_lease)
    monkeypatch.setattr(runtime_callbacks.repositories, "append_event", fake_append)
    monkeypatch.setattr(
        runtime_callbacks.sandbox_lease_repository,
        "record_sandbox_executor_heartbeat",
        fake_heartbeat,
    )
    response = TestClient(create_app()).post(
        "/api/ai/runtime/callbacks/executor",
        headers={"X-AI-Platform-Callback-Token": derived_callback_token("secret")},
        json=callback_payload(new_message=None, state_patch={}),
    )
    assert response.status_code == 200
    assert heartbeat_calls
    assert heartbeat_calls[0]["ttl_seconds"] == 731
    assert heartbeat_calls[0]["executor_status"] == "running"


def test_executor_callback_uses_real_adapter_lifecycle_and_excludes_platform_events(monkeypatch):
    patch_callback_settings(monkeypatch, callback_settings("secret"))
    persisted = []
    v4_rows = []

    class FakeTransaction:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, exc_type, exc, traceback):
            return None

    async def get_run_identity(conn, *, run_id, for_update=False):
        return {"tenant_id": "tenant-a", "id": run_id, "session_id": "session-a", "status": "running"}

    async def append_batch(conn, **receipt):
        persisted.extend(receipt["events"])
        return {"callback_received_at": "2026-08-09T00:00:00Z"}

    async def append_v4(conn, **kwargs):
        v4_rows.append(kwargs)
        return ()

    async def publish_pending(transaction_factory, *, limit):
        return {"published": 0, "pending": len(v4_rows[-1]["items"])}

    from app.executors.claude.agent_events import ClaudeSdkAgentEventAdapter
    from app.routes import runtime_callbacks
    from app.runtime.kernel_contracts import AgentEvent

    adapter = ClaudeSdkAgentEventAdapter(
        run_id="run-a",
        attempt_id="attempt-a",
        authorized_capabilities={"Read": {"category": "read", "display_name": "Read"}},
    )
    ThinkingBlock = type("ThinkingBlock", (), {})
    thinking = adapter.accept_content_block(ThinkingBlock(), block_index=0, message_identity="message-a")
    ToolUseBlock = type("ToolUseBlock", (), {})
    tool = ToolUseBlock()
    tool.id, tool.name, tool.input = "tool-1", "Read", {}
    adapter.accept_content_block(tool)
    tool_events = (
        *adapter.accept_hook("PreToolUse", {"tool_use_id": "tool-1", "tool_name": "Read"}, tool_use_id="tool-1"),
        *adapter.accept_hook("PostToolUse", {"tool_use_id": "tool-1", "tool_name": "Read"}, tool_use_id="tool-1"),
    )
    TaskStartedMessage = type("TaskStartedMessage", (), {})
    task_started = TaskStartedMessage()
    task_started.task_id, task_started.tool_use_id = "task-1", "tool-1"
    TaskNotificationMessage = type("TaskNotificationMessage", (), {})
    task_done = TaskNotificationMessage()
    task_done.task_id, task_done.status = "task-1", "completed"
    subagent = (*adapter.accept_task_message(task_started), *adapter.accept_task_message(task_done))
    Result = type("Result", (), {})
    result = Result()
    result.duration_ms, result.num_turns, result.stop_reason = 12, 1, "end_turn"
    model = adapter.accept_result(result)
    policy = adapter.accept_policy_decision(tool_name="Read", tool_input={}, allowed=True, tool_use_id="tool-1")
    artifact = adapter.accept_artifact_reference(
        {"artifact_id": "artifact-1", "filename": "report.txt", "media_type": "text/plain", "size_bytes": 4, "status": "ready"}
    )
    run_event = AgentEvent(
        type="run.succeeded",
        event_id="run-event-1",
        run_id="run-a",
        payload={"terminal_event_id": "terminal-1", "hydrate_required": True},
    )
    candidates = (*thinking, *tool_events, *subagent, *model, *policy, *artifact)
    events = tuple(
        candidate.to_agent_event() if hasattr(candidate, "to_agent_event") else candidate
        for candidate in (*candidates, run_event)
    )

    authority = SimpleNamespace(attempt_id="attempt-a", state="confirmed")
    async def get_authority(conn, *, tenant_id, run_id, for_update=False):
        return authority

    monkeypatch.setattr(runtime_callbacks, "transaction", lambda: FakeTransaction())
    monkeypatch.setattr(runtime_callbacks.repositories, "get_run_identity", get_run_identity)
    monkeypatch.setattr(runtime_callbacks.repositories, "append_event_batch", append_batch)
    monkeypatch.setattr(runtime_callbacks, "append_callback_v4_rows", append_v4)
    monkeypatch.setattr(runtime_callbacks, "publish_pending_v4_events", publish_pending)
    monkeypatch.setattr(runtime_callbacks, "get_stream_authority", get_authority)
    patch_active_attempt(monkeypatch, runtime_callbacks)

    response = TestClient(create_app()).post(
        "/api/ai/runtime/callbacks/executor",
        headers={"X-AI-Platform-Callback-Token": derived_callback_token("secret")},
        json=callback_payload(batch_id="batch-lifecycle", new_message=None, state_patch={}, events=[event.model_dump() for event in events]),
    )

    assert response.status_code == 200
    assert response.json()["accepted"] is True
    assert response.json()["event_count"] == len(events) + 1
    assert len(v4_rows) == 1
    assert {item.event_type for item in v4_rows[0]["items"]} == {
        "thinking.started",
        "thinking.completed",
        "tool.started",
        "tool.completed",
        "subagent.started",
        "subagent.completed",
        "model.completed",
    }
    assert not {"artifact.ready", "policy.checking", "policy.allowed", "run.succeeded"} & {
        item.event_type for item in v4_rows[0]["items"]
    }
    assert response.json()["accepted"] is True


def test_executor_callback_propagates_v4_correctness_errors(monkeypatch):
    patch_callback_settings(monkeypatch, callback_settings("secret"))

    class FakeTransaction:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, exc_type, exc, traceback):
            return None

    async def get_run_identity(conn, *, run_id, for_update=False):
        return {"tenant_id": "tenant-a", "session_id": "session-a", "status": "running"}

    async def append_batch(conn, **receipt):
        return {"callback_received_at": "2026-08-09T00:00:00Z"}

    async def append_v4(conn, **kwargs):
        return (SimpleNamespace(event_type="message.delta"),)

    async def correctness_error(*_args, **_kwargs):
        raise RuntimeError("v4 correctness failure")

    from app.routes import runtime_callbacks
    from app.executors.claude.agent_events import ClaudeSdkAgentEventAdapter

    adapter = ClaudeSdkAgentEventAdapter(run_id="run-a", attempt_id="attempt-a")
    event = adapter.accept_answer_text("answer")[0].to_agent_event()
    authority = SimpleNamespace(attempt_id="attempt-a", state="confirmed")

    async def get_authority(conn, *, tenant_id, run_id, for_update=False):
        return authority

    monkeypatch.setattr(runtime_callbacks, "transaction", lambda: FakeTransaction())
    monkeypatch.setattr(runtime_callbacks.repositories, "get_run_identity", get_run_identity)
    monkeypatch.setattr(runtime_callbacks.repositories, "append_event_batch", append_batch)
    monkeypatch.setattr(runtime_callbacks, "append_callback_v4_rows", append_v4)
    monkeypatch.setattr(runtime_callbacks, "get_stream_authority", get_authority)
    monkeypatch.setattr(runtime_callbacks, "publish_pending_v4_events", correctness_error)
    patch_active_attempt(monkeypatch, runtime_callbacks)

    with pytest.raises(RuntimeError, match="v4 correctness failure"):
        TestClient(create_app()).post(
            "/api/ai/runtime/callbacks/executor",
            headers={"X-AI-Platform-Callback-Token": derived_callback_token("secret")},
            json=callback_payload(batch_id="batch-error", new_message=None, state_patch={}, events=[event.model_dump()]),
        )


@pytest.mark.asyncio
async def test_record_executor_callback_rolls_back_receipt_and_v4_rows_after_final_attempt_recheck(monkeypatch):
    from fastapi import HTTPException

    from app.executors.claude.agent_events import ClaudeSdkAgentEventAdapter
    from app.routes import runtime_callbacks

    patch_callback_settings(monkeypatch, callback_settings("secret"))
    adapter = ClaudeSdkAgentEventAdapter(run_id="run-a", attempt_id="attempt-a")
    event = adapter.accept_answer_text("answer")[0].to_agent_event()
    callback = ExecutorCallbackEvent(
        session_id="session-a",
        run_id="run-a",
        attempt_id="attempt-a",
        callback_token_id="cbt:run-a:attempt-a",
        batch_id="batch-rollback",
        status="running",
        progress=20,
        new_message=None,
        state_patch={},
        events=[event],
    )
    state = {"receipts": [], "v4_rows": [], "lease_calls": 0, "rolled_back": False, "committed": False}

    class FakeTransaction:
        async def __aenter__(self):
            state["receipt_snapshot"] = len(state["receipts"])
            state["v4_snapshot"] = len(state["v4_rows"])
            return object()

        async def __aexit__(self, exc_type, exc, traceback):
            if exc_type is not None:
                del state["receipts"][state["receipt_snapshot"] :]
                del state["v4_rows"][state["v4_snapshot"] :]
                state["rolled_back"] = True
            else:
                state["committed"] = True
            return False

    async def get_run_identity(conn, *, run_id, for_update=False):
        return {"tenant_id": "tenant-a", "id": run_id, "session_id": "session-a", "status": "running"}

    async def list_current_leases(conn, *, tenant_id, run_id, attempt_id):
        state["lease_calls"] += 1
        if state["lease_calls"] == 1:
            return [{"lease_payload_json": {"attempt_id": attempt_id}}]
        return []

    async def append_batch(conn, **kwargs):
        state["receipts"].append(kwargs)
        return {"callback_received_at": "2026-08-09T00:00:00Z"}

    async def append_v4(conn, **kwargs):
        state["v4_rows"].append(kwargs)
        return tuple(kwargs["items"])

    async def get_authority(conn, *, tenant_id, run_id, for_update=False):
        return SimpleNamespace(attempt_id="attempt-a", state="confirmed")

    async def unexpected_publish(*_args, **_kwargs):
        raise AssertionError("publication must not run after the transaction rolls back")

    monkeypatch.setattr(runtime_callbacks, "transaction", lambda: FakeTransaction())
    monkeypatch.setattr(runtime_callbacks.repositories, "get_run_identity", get_run_identity)
    monkeypatch.setattr(
        runtime_callbacks.repositories,
        "list_current_sandbox_runtime_leases_for_attempt",
        list_current_leases,
    )
    monkeypatch.setattr(runtime_callbacks.repositories, "append_event_batch", append_batch)
    monkeypatch.setattr(runtime_callbacks, "append_callback_v4_rows", append_v4)
    monkeypatch.setattr(runtime_callbacks, "get_stream_authority", get_authority)
    monkeypatch.setattr(runtime_callbacks, "publish_pending_v4_events", unexpected_publish)

    with pytest.raises(HTTPException) as exc_info:
        await runtime_callbacks.record_executor_callback(callback)

    assert exc_info.value.detail == "sandbox_runtime_attempt_inactive"
    assert state["lease_calls"] == 2
    assert state["rolled_back"] is True
    assert state["committed"] is False
    assert state["receipts"] == []
    assert state["v4_rows"] == []


@pytest.mark.asyncio
async def test_record_executor_callback_duplicate_reuses_v4_identity_after_publication(monkeypatch):
    from app.executors.claude.agent_events import ClaudeSdkAgentEventAdapter
    from app.routes import runtime_callbacks

    patch_callback_settings(monkeypatch, callback_settings("secret"))
    adapter = ClaudeSdkAgentEventAdapter(run_id="run-a", attempt_id="attempt-a")
    event = adapter.accept_answer_text("answer")[0].to_agent_event()
    callback = ExecutorCallbackEvent(
        session_id="session-a",
        run_id="run-a",
        attempt_id="attempt-a",
        callback_token_id="cbt:run-a:attempt-a",
        batch_id="batch-duplicate",
        status="running",
        progress=20,
        new_message=None,
        state_patch={},
        events=[event],
    )
    state = {"receipt_calls": 0, "v4_calls": [], "publication": "pending"}
    row_identity = {"id": "evt4_handler_duplicate"}

    class FakeTransaction:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, exc_type, exc, traceback):
            return False

    async def get_run_identity(conn, *, run_id, for_update=False):
        return {"tenant_id": "tenant-a", "id": run_id, "session_id": "session-a", "status": "running"}

    async def list_current_leases(conn, *, tenant_id, run_id, attempt_id):
        return [{"lease_payload_json": {"attempt_id": attempt_id}}]

    async def append_batch(conn, **kwargs):
        state["receipt_calls"] += 1
        return {
            "callback_received_at": "2026-08-09T00:00:00Z",
            "duplicate": state["receipt_calls"] > 1,
        }

    async def append_v4(conn, **kwargs):
        state["v4_calls"].append(kwargs)
        assert state["publication"] == ("pending" if len(state["v4_calls"]) == 1 else "published")
        return (row_identity,)

    async def get_authority(conn, *, tenant_id, run_id, for_update=False):
        return SimpleNamespace(attempt_id="attempt-a", state="confirmed")

    async def publish_pending(transaction_factory, *, limit):
        state["publication"] = "published"
        return 1

    monkeypatch.setattr(runtime_callbacks, "transaction", lambda: FakeTransaction())
    monkeypatch.setattr(runtime_callbacks.repositories, "get_run_identity", get_run_identity)
    monkeypatch.setattr(
        runtime_callbacks.repositories,
        "list_current_sandbox_runtime_leases_for_attempt",
        list_current_leases,
    )
    monkeypatch.setattr(runtime_callbacks.repositories, "append_event_batch", append_batch)
    monkeypatch.setattr(runtime_callbacks, "append_callback_v4_rows", append_v4)
    monkeypatch.setattr(runtime_callbacks, "get_stream_authority", get_authority)
    monkeypatch.setattr(runtime_callbacks, "publish_pending_v4_events", publish_pending)

    first = await runtime_callbacks.record_executor_callback(callback)
    second = await runtime_callbacks.record_executor_callback(callback)

    assert first == {"accepted": True, "batch_id": "batch-duplicate", "event_count": 2}
    assert second == {
        "accepted": True,
        "batch_id": "batch-duplicate",
        "event_count": 2,
        "deduplicated": True,
    }
    assert state["receipt_calls"] == 2
    assert state["v4_calls"][0]["items"] == state["v4_calls"][1]["items"]
    assert state["publication"] == "published"


@pytest.mark.asyncio
async def test_record_executor_callback_accepts_committed_rows_when_redis_transport_is_unavailable(monkeypatch):
    from app.executors.claude.agent_events import ClaudeSdkAgentEventAdapter
    from app.routes import runtime_callbacks
    from app.streaming import worker_projection
    from app.streaming.redis import StreamTransportUnavailable

    patch_callback_settings(monkeypatch, callback_settings("secret"))
    adapter = ClaudeSdkAgentEventAdapter(run_id="run-a", attempt_id="attempt-a")
    event = adapter.accept_answer_text("answer")[0].to_agent_event()
    callback = ExecutorCallbackEvent(
        session_id="session-a",
        run_id="run-a",
        attempt_id="attempt-a",
        callback_token_id="cbt:run-a:attempt-a",
        batch_id="batch-transport",
        status="running",
        progress=20,
        new_message=None,
        state_patch={},
        events=[event],
    )
    state = {"receipts": [], "rows": [], "pending_rows": [], "retry_errors": []}

    class FakeCursor:
        async def fetchone(self):
            return {"id": "lease-a"}

    class FakeConnection:
        async def execute(self, statement, params):
            return FakeCursor()

    class FakeTransaction:
        async def __aenter__(self):
            return FakeConnection()

        async def __aexit__(self, exc_type, exc, traceback):
            return False

    async def get_run_identity(conn, *, run_id, for_update=False):
        return {"tenant_id": "tenant-a", "id": run_id, "session_id": "session-a", "status": "running"}

    async def list_current_leases(conn, *, tenant_id, run_id, attempt_id):
        return [{"id": "lease-a", "lease_payload_json": {"attempt_id": attempt_id}}]

    async def heartbeat(conn, **kwargs):
        return {"id": kwargs["lease_id"]}

    async def append_batch(conn, **kwargs):
        state["receipts"].append(kwargs)
        return {"callback_received_at": "2026-08-09T00:00:00Z"}

    async def append_v4(conn, **kwargs):
        row = {
            "id": "evt4_transport_pending",
            "tenant_id": "tenant-a",
            "run_id": "run-a",
            "sequence": 1,
            "event_type": kwargs["items"][0].event_type,
            "visible_to_user": True,
            "payload_json": {
                "delta": "answer",
                "__stream_v4": {
                    "attempt_id": "attempt-a",
                    "stream_incarnation": 2,
                    "authorization_epoch": 4,
                    "execution_lease_id": "lease-a",
                    "publication_state": "pending",
                },
            },
            "stream_publication_state": "pending",
        }
        state["rows"].append(row)
        state["pending_rows"][:] = [row]
        return (row,)

    authority = SimpleNamespace(
        tenant_id="tenant-a",
        run_id="run-a",
        attempt_id="attempt-a",
        state="confirmed",
        stream_incarnation=2,
        authorization_epoch=4,
        revocation_state="active",
    )

    async def get_authority(conn, *, tenant_id, run_id, for_update=False):
        return authority

    class FailingPublisher:
        async def append(self, envelope):
            raise StreamTransportUnavailable("redis unavailable")

    async def pending_rows(conn, *, limit):
        if state["pending_rows"]:
            return (state["pending_rows"].pop(0),)
        return ()

    async def mark_attempt(conn, *, event_id):
        state["attempted"] = event_id

    async def mark_retry(conn, *, event_id, error):
        state["retry_errors"].append((event_id, error))

    async def publish_pending(transaction_factory, *, limit):
        return await worker_projection.publish_pending_v4_events(
            transaction_factory,
            limit=limit,
            bridge=FailingPublisher(),
        )

    monkeypatch.setattr(runtime_callbacks, "transaction", lambda: FakeTransaction())
    monkeypatch.setattr(runtime_callbacks.repositories, "get_run_identity", get_run_identity)
    monkeypatch.setattr(
        runtime_callbacks.repositories,
        "list_current_sandbox_runtime_leases_for_attempt",
        list_current_leases,
    )
    monkeypatch.setattr(
        runtime_callbacks.sandbox_lease_repository,
        "record_sandbox_executor_heartbeat",
        heartbeat,
    )
    monkeypatch.setattr(runtime_callbacks.repositories, "append_event_batch", append_batch)
    monkeypatch.setattr(runtime_callbacks, "append_callback_v4_rows", append_v4)
    monkeypatch.setattr(runtime_callbacks, "get_stream_authority", get_authority)
    monkeypatch.setattr(runtime_callbacks, "publish_pending_v4_events", publish_pending)
    monkeypatch.setattr(worker_projection, "list_pending_v4_rows", pending_rows)
    monkeypatch.setattr(worker_projection, "mark_v4_attempt", mark_attempt)
    monkeypatch.setattr(worker_projection, "mark_v4_retry_error", mark_retry)
    monkeypatch.setattr(worker_projection.repositories, "get_run_identity", get_run_identity)
    monkeypatch.setattr(worker_projection, "get_stream_authority", get_authority)

    async def no_cancel(*_args, **_kwargs):
        return False

    monkeypatch.setattr(worker_projection.repositories, "is_cancel_requested", no_cancel)
    monkeypatch.setattr(
        worker_projection,
        "project_public_v4",
        lambda row, authority: {"event_type": row["event_type"]},
    )

    result = await runtime_callbacks.record_executor_callback(callback)

    assert result == {"accepted": True, "batch_id": "batch-transport", "event_count": 2}
    assert len(state["receipts"]) == 1
    assert len(state["rows"]) == 1
    assert state["rows"][0]["stream_publication_state"] == "pending"
    assert state["retry_errors"] == [("evt4_transport_pending", "StreamTransportUnavailable")]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure", "expected_detail"),
    [
        ("missing_batch", "callback_batch_id_required"),
        ("wrong_authority", "sse_stream_attempt_inactive"),
        ("wrong_attempt", "sandbox_runtime_attempt_mismatch"),
        ("missing_lease", "sandbox_runtime_attempt_inactive"),
    ],
)
async def test_record_executor_callback_enforces_v4_batch_authority_attempt_and_lease_fences(
    monkeypatch, failure, expected_detail
):
    from fastapi import HTTPException

    from app.executors.claude.agent_events import ClaudeSdkAgentEventAdapter
    from app.routes import runtime_callbacks

    patch_callback_settings(monkeypatch, callback_settings("secret"))
    adapter = ClaudeSdkAgentEventAdapter(run_id="run-a", attempt_id="attempt-a")
    event = adapter.accept_answer_text("answer")[0].to_agent_event()
    callback = ExecutorCallbackEvent(
        session_id="session-a",
        run_id="run-a",
        attempt_id="attempt-a",
        callback_token_id="cbt:run-a:attempt-a",
        batch_id=None if failure == "missing_batch" else "batch-fence",
        status="running",
        progress=20,
        new_message=None,
        state_patch={},
        events=[event],
    )

    class FakeTransaction:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, exc_type, exc, traceback):
            return False

    async def get_run_identity(conn, *, run_id, for_update=False):
        return {"tenant_id": "tenant-a", "id": run_id, "session_id": "session-a", "status": "running"}

    async def list_current_leases(conn, *, tenant_id, run_id, attempt_id):
        if failure == "missing_lease":
            return []
        if failure == "wrong_attempt":
            return [{"lease_payload_json": {"attempt_id": "attempt-other"}}]
        return [{"lease_payload_json": {"attempt_id": attempt_id}}]

    async def get_authority(conn, *, tenant_id, run_id, for_update=False):
        if failure == "wrong_authority":
            return SimpleNamespace(attempt_id="attempt-other", state="confirmed")
        return SimpleNamespace(attempt_id="attempt-a", state="confirmed")

    async def unexpected_append(*_args, **_kwargs):
        raise AssertionError("fenced callbacks must not append receipt or public rows")

    monkeypatch.setattr(runtime_callbacks, "transaction", lambda: FakeTransaction())
    monkeypatch.setattr(runtime_callbacks.repositories, "get_run_identity", get_run_identity)
    monkeypatch.setattr(
        runtime_callbacks.repositories,
        "list_current_sandbox_runtime_leases_for_attempt",
        list_current_leases,
    )
    monkeypatch.setattr(runtime_callbacks.repositories, "append_event_batch", unexpected_append)
    monkeypatch.setattr(runtime_callbacks, "append_callback_v4_rows", unexpected_append)
    monkeypatch.setattr(runtime_callbacks, "get_stream_authority", get_authority)

    with pytest.raises(HTTPException) as exc_info:
        await runtime_callbacks.record_executor_callback(callback)

    assert exc_info.value.detail == expected_detail
