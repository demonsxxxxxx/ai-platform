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


def patch_callback_stream(monkeypatch, runtime_callbacks, published):
    authority = SimpleNamespace(
        tenant_scope="scope-a",
        run_id="run-a",
        attempt_id="attempt-a",
        stream_incarnation=1,
        state="confirmed",
    )

    async def get_authority(conn, *, tenant_id, run_id, for_update=False):
        return authority

    class Bridge:
        async def append(self, envelope):
            published.append(envelope)

        async def aclose(self):
            return None

    monkeypatch.setattr(runtime_callbacks, "get_stream_authority", get_authority)
    monkeypatch.setattr(runtime_callbacks, "RedisStreamBridge", Bridge)


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
    browser_event = next(call for call in calls if call[0] == "browser_snapshot")
    assert browser_event[1] == "browser"
    assert browser_event[3]["visible_to_user"] is False
    assert browser_event[3]["admin_only"] is True
    assert browser_event[3]["source"] == "executor_callback"


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


def test_executor_callback_is_the_ordered_assistant_delta_ingress(monkeypatch):
    patch_callback_settings(monkeypatch, callback_settings("secret"))
    persisted = []
    published = []

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

    async def fake_append_batch(conn, **receipt):
        persisted.extend(receipt["events"])
        return {"callback_received_at": "2026-08-09T00:00:00Z"}

    from app.routes import runtime_callbacks

    monkeypatch.setattr(runtime_callbacks, "transaction", lambda: FakeTransaction())
    monkeypatch.setattr(runtime_callbacks.repositories, "get_run_identity", fake_get_run_identity)
    monkeypatch.setattr(runtime_callbacks.repositories, "append_event", fake_append_event)
    monkeypatch.setattr(runtime_callbacks.repositories, "append_event_batch", fake_append_batch)
    patch_active_attempt(monkeypatch, runtime_callbacks)
    patch_callback_stream(monkeypatch, runtime_callbacks, published)
    client = TestClient(create_app())
    responses = [
        client.post(
            "/api/ai/runtime/callbacks/executor",
            headers={"X-AI-Platform-Callback-Token": derived_callback_token("secret")},
            json=callback_payload(
                batch_id="batch-a",
                new_message=None,
                state_patch={},
                events=[
                    {
                        "type": "assistant_delta",
                        "message": "executor message must not persist",
                        "payload": {
                            "delta": "safe ",
                            "command": "private command",
                            "path": "/private/path",
                            "token": "private-token",
                            "tool_name": "private-tool",
                            "stdout": "private stdout",
                            "stderr": "private stderr",
                        },
                    }
                ],
            ),
        ),
        client.post(
            "/api/ai/runtime/callbacks/executor",
            headers={"X-AI-Platform-Callback-Token": derived_callback_token("secret")},
            json=callback_payload(
                batch_id="batch-b",
                new_message=None,
                state_patch={},
                events=[
                    {
                        "type": "assistant_delta",
                        "message": "executor message must not persist",
                        "payload": {"delta": "answer"},
                    }
                ],
            ),
        ),
    ]

    assert [response.status_code for response in responses] == [200, 200]
    assert [response.json() for response in responses] == [
        {"accepted": True, "batch_id": "batch-a", "event_count": 2},
        {"accepted": True, "batch_id": "batch-b", "event_count": 2},
    ]
    assert [event["event_type"] for event in persisted] == [
        "executor_callback",
        "assistant_delta",
        "executor_callback",
        "assistant_delta",
    ]
    persisted_deltas = [
        event["payload"]
        for event in persisted
        if event["event_type"] == "assistant_delta"
    ]
    assert persisted_deltas == [
        {
            "delta": "safe ",
            "source": "worker_answer_delta_v1",
            "visible_to_user": True,
            "severity": "info",
        },
        {
            "delta": "answer",
            "source": "worker_answer_delta_v1",
            "visible_to_user": True,
            "severity": "info",
        },
    ]
    assert [event.event_type for event in published] == [
        "assistant_text_delta",
        "assistant_text_delta",
    ]
    assert [event.payload for event in published] == [
        {"delta": "safe "},
        {"delta": "answer"},
    ]
    assert published[0].event_id != published[1].event_id
    assert "private command" not in "".join(
        event.canonical_bytes.decode() for event in published
    )


def test_executor_callback_suppresses_delta_if_run_terminalizes_after_receipt_commit(monkeypatch):
    patch_callback_settings(monkeypatch, callback_settings("secret"))
    persisted = []
    published = []
    identity_reads = 0

    class FakeTransaction:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, exc_type, exc, traceback):
            return None

    async def get_run_identity(conn, *, run_id, for_update=False):
        nonlocal identity_reads
        identity_reads += 1
        return {
            "tenant_id": "tenant-a",
            "id": run_id,
            "session_id": "session-a",
            "status": "running" if identity_reads == 1 else "succeeded",
        }

    async def append_batch(conn, **receipt):
        persisted.extend(receipt["events"])
        return {"callback_received_at": "2026-08-09T00:00:00Z"}

    from app.routes import runtime_callbacks

    monkeypatch.setattr(runtime_callbacks, "transaction", lambda: FakeTransaction())
    monkeypatch.setattr(runtime_callbacks.repositories, "get_run_identity", get_run_identity)
    monkeypatch.setattr(runtime_callbacks.repositories, "append_event_batch", append_batch)
    patch_active_attempt(monkeypatch, runtime_callbacks)
    patch_callback_stream(monkeypatch, runtime_callbacks, published)

    response = TestClient(create_app()).post(
        "/api/ai/runtime/callbacks/executor",
        headers={"X-AI-Platform-Callback-Token": derived_callback_token("secret")},
        json=callback_payload(batch_id="batch-a", new_message={"type": "assistant", "delta": "late"}, state_patch={}),
    )

    assert response.status_code == 200
    assert [event["event_type"] for event in persisted] == [
        "executor_callback",
        "assistant_delta",
    ]
    assert identity_reads == 2
    assert published == []


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
    assert [event["event_type"] for event in persisted] == ["executor_callback"]


@pytest.mark.parametrize(
    "new_message",
    [
        {"type": "assistant", "delta": ""},
        {"type": "assistant", "delta": 7},
        {"type": "assistant", "delta": "", "text": "non-authoritative fallback"},
        {"type": "assistant", "delta": 7, "text": "non-authoritative fallback"},
    ],
)
def test_executor_callback_rejects_empty_or_non_string_new_message_delta(monkeypatch, new_message):
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
        json=callback_payload(new_message=new_message, state_patch={}),
    )

    assert response.status_code == 200
    assert response.json() == {"accepted": True, "event_count": 1}
    assert [event["event_type"] for event in persisted] == ["executor_callback"]


def test_executor_callback_uses_text_when_delta_is_absent(monkeypatch):
    patch_callback_settings(monkeypatch, callback_settings("secret"))
    persisted = []
    published = []

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

    async def fake_append_batch(conn, **receipt):
        persisted.extend(receipt["events"])
        return {"callback_received_at": "2026-08-09T00:00:00Z"}

    from app.routes import runtime_callbacks

    monkeypatch.setattr(runtime_callbacks, "transaction", lambda: FakeTransaction())
    monkeypatch.setattr(runtime_callbacks.repositories, "get_run_identity", fake_get_run_identity)
    monkeypatch.setattr(runtime_callbacks.repositories, "append_event", fake_append_event)
    monkeypatch.setattr(runtime_callbacks.repositories, "append_event_batch", fake_append_batch)
    patch_active_attempt(monkeypatch, runtime_callbacks)
    patch_callback_stream(monkeypatch, runtime_callbacks, published)
    response = TestClient(create_app()).post(
        "/api/ai/runtime/callbacks/executor",
        headers={"X-AI-Platform-Callback-Token": derived_callback_token("secret")},
        json=callback_payload(
            batch_id="batch-a",
            new_message={"type": "assistant", "text": "text fallback"},
            state_patch={},
        ),
    )

    assert response.status_code == 200
    assert response.json() == {
        "accepted": True,
        "batch_id": "batch-a",
        "event_count": 1,
    }
    assert [event["event_type"] for event in persisted] == [
        "executor_callback",
        "assistant_delta",
    ]
    assert persisted[1]["payload"] == {
        "delta": "text fallback",
        "source": "worker_answer_delta_v1",
        "visible_to_user": True,
        "severity": "info",
    }
    assert published[0].payload == {"delta": "text fallback"}


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
