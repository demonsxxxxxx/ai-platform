import base64
import hashlib
import hmac

import pytest
from fastapi.testclient import TestClient

from app.context_retrieval import ContextRetrieval, ContextRetrievalDenied, InMemoryContextRetrievalRepository
from app.main import create_app
from app.routes.runtime_callbacks import _run_context_retrieval_action
from app.runtime.sandbox.context_retrieval_client import PlatformContextRetrievalClient
from app.runtime.sandbox.contracts import ContextRetrievalScope


def _token(secret: str, token_id: str = "cbt:run-a:attempt-a") -> str:
    return hmac.new(secret.encode(), token_id.encode(), hashlib.sha256).hexdigest()


def _payload(action: str = "read_run_artifact", arguments=None, **overrides):
    payload = {
        "session_id": "session-a",
        "run_id": "run-a",
        "attempt_id": "attempt-a",
        "callback_token_id": "cbt:run-a:attempt-a",
        "action": action,
        "arguments": arguments or {"artifact_id": "artifact-a"},
    }
    payload.update(overrides)
    return payload


class _Transaction:
    async def __aenter__(self):
        return object()

    async def __aexit__(self, exc_type, exc, traceback):
        return None


def _patch_route(
    monkeypatch,
    *,
    status="running",
    tools=None,
    action_result=None,
    lease_attempt="attempt-a",
    active_lease=True,
):
    import app.routes.runtime_callbacks as callbacks

    calls = []
    tools = tools or ["read_run_artifact"]

    async def get_run_identity(conn, *, run_id, for_update=False):
        return {
            "id": run_id,
            "tenant_id": "tenant-a",
            "workspace_id": "workspace-a",
            "user_id": "user-a",
            "session_id": "session-a",
            "agent_id": "agent-a",
            "status": status,
        }

    async def get_snapshot(conn, *, tenant_id, workspace_id, user_id, session_id, run_id):
        refs = {
            "read_session_messages": ("recent_messages", [{"message_id": "message-a"}]),
            "read_context_file": ("files", [{"file_id": "file-a"}]),
            "read_run_artifact": ("artifacts", [{"artifact_id": "artifact-a"}]),
            "stage_context_file_to_workspace": ("files", [{"file_id": "file-a"}]),
            "stage_run_artifact_to_workspace": ("artifacts", [{"artifact_id": "artifact-a"}]),
            "search_memory": ("memory_records", [{"memory_record_id": "memory-a"}]),
        }
        manifest = {
            "schema_version": "ai-platform.context-manifest.v1",
            "available_retrieval_tools": tools,
        }
        for tool in tools:
            key, values = refs[tool]
            manifest[key] = values
        return {"payload_json": {"context_manifest": manifest}}

    async def list_current_leases(conn, *, tenant_id, run_id, attempt_id):
        if not active_lease:
            return []
        return [{"lease_payload_json": {"attempt_id": lease_attempt}}]

    async def run_action(retrieval, *, action, arguments, identity):
        calls.append((action, arguments, identity))
        if isinstance(action_result, Exception):
            raise action_result
        return action_result or {
            "artifact_id": "artifact-a",
            "label": "translated.docx",
            "audit": {"action": "context_retrieval.read_run_artifact"},
        }

    async def append_event(conn, **kwargs):
        calls.append(("event", kwargs))
        return "evt-a"

    monkeypatch.setattr(callbacks, "get_settings", lambda: type("S", (), {"sandbox_callback_token": "secret"})())
    monkeypatch.setattr(callbacks, "transaction", lambda: _Transaction())
    monkeypatch.setattr(callbacks.repositories, "get_run_identity", get_run_identity)
    monkeypatch.setattr(
        callbacks.repositories,
        "list_current_sandbox_runtime_leases_for_attempt",
        list_current_leases,
    )
    monkeypatch.setattr(callbacks.repositories, "get_bound_executor_context_snapshot", get_snapshot)
    monkeypatch.setattr(callbacks.repositories, "append_event", append_event)
    monkeypatch.setattr(callbacks, "ObjectStorage", lambda: object())
    monkeypatch.setattr(callbacks, "RepositoryContextRetrievalRepository", lambda conn, storage: object())
    monkeypatch.setattr(callbacks, "ContextRetrieval", lambda repository: object())
    monkeypatch.setattr(callbacks, "_run_context_retrieval_action", run_action)
    return calls


def test_context_retrieval_callback_derives_scope_and_records_allowed_event(monkeypatch):
    calls = _patch_route(monkeypatch)
    response = TestClient(create_app()).post(
        "/api/ai/runtime/callbacks/context-retrieval",
        headers={"X-AI-Platform-Callback-Token": _token("secret")},
        json=_payload(),
    )

    assert response.status_code == 200
    assert response.json()["result"]["label"] == "translated.docx"
    action, arguments, identity = calls[0]
    assert action == "read_run_artifact"
    assert arguments == {"artifact_id": "artifact-a"}
    assert identity == {
        "tenant_id": "tenant-a",
        "workspace_id": "workspace-a",
        "user_id": "user-a",
        "session_id": "session-a",
        "run_id": "run-a",
        "agent_id": "agent-a",
    }
    assert calls[1][0] == "event"
    assert "secret" not in str(response.json())


def test_parallel_same_run_context_attempts_each_use_their_exact_lease_and_token(monkeypatch):
    calls = _patch_route(monkeypatch)
    lease_checks = []
    import app.routes.runtime_callbacks as callbacks

    async def exact_lease(conn, *, tenant_id, run_id, attempt_id):
        lease_checks.append((tenant_id, run_id, attempt_id))
        if attempt_id not in {"attempt-a", "attempt-b"}:
            return []
        return [{"lease_payload_json": {"attempt_id": attempt_id}}]

    monkeypatch.setattr(callbacks.repositories, "list_current_sandbox_runtime_leases_for_attempt", exact_lease)
    client = TestClient(create_app())
    first = client.post(
        "/api/ai/runtime/callbacks/context-retrieval",
        headers={"X-AI-Platform-Callback-Token": _token("secret")},
        json=_payload(),
    )
    second_token_id = "cbt:run-a:attempt-b"
    second = client.post(
        "/api/ai/runtime/callbacks/context-retrieval",
        headers={"X-AI-Platform-Callback-Token": _token("secret", second_token_id)},
        json=_payload(attempt_id="attempt-b", callback_token_id=second_token_id),
    )
    crossed = client.post(
        "/api/ai/runtime/callbacks/context-retrieval",
        headers={"X-AI-Platform-Callback-Token": _token("secret")},
        json=_payload(attempt_id="attempt-b", callback_token_id="cbt:run-a:attempt-a"),
    )

    assert first.status_code == second.status_code == 200
    assert crossed.status_code == 401
    assert lease_checks == [
        ("tenant-a", "run-a", "attempt-a"),
        ("tenant-a", "run-a", "attempt-a"),
        ("tenant-a", "run-a", "attempt-b"),
        ("tenant-a", "run-a", "attempt-b"),
    ]
    assert [call[0] for call in calls] == ["read_run_artifact", "event", "read_run_artifact", "event"]


def test_context_retrieval_callback_rejects_wrong_token_before_storage(monkeypatch):
    calls = _patch_route(monkeypatch)
    response = TestClient(create_app()).post(
        "/api/ai/runtime/callbacks/context-retrieval",
        headers={"X-AI-Platform-Callback-Token": "wrong"},
        json=_payload(),
    )

    assert response.status_code == 401
    assert calls == []


def test_context_retrieval_callback_rejects_missing_attempt_and_caller_tenant(monkeypatch):
    calls = _patch_route(monkeypatch)
    client = TestClient(create_app())
    headers = {"X-AI-Platform-Callback-Token": _token("secret")}
    missing_attempt = _payload()
    missing_attempt.pop("attempt_id")

    missing = client.post(
        "/api/ai/runtime/callbacks/context-retrieval",
        headers=headers,
        json=missing_attempt,
    )
    forged_tenant = client.post(
        "/api/ai/runtime/callbacks/context-retrieval",
        headers=headers,
        json=_payload(tenant_id="tenant-b"),
    )

    assert missing.status_code == 422
    assert forged_tenant.status_code == 422
    assert calls == []


def test_context_retrieval_callback_rejects_unadvertised_and_extra_parameters(monkeypatch):
    calls = _patch_route(monkeypatch, tools=["read_session_messages"])
    client = TestClient(create_app())
    headers = {"X-AI-Platform-Callback-Token": _token("secret")}

    unadvertised = client.post(
        "/api/ai/runtime/callbacks/context-retrieval",
        headers=headers,
        json=_payload(),
    )
    extra = client.post(
        "/api/ai/runtime/callbacks/context-retrieval",
        headers=headers,
        json=_payload(arguments={"artifact_id": "artifact-a", "tenant_id": "tenant-b"}),
    )

    assert unadvertised.status_code == 403
    assert unadvertised.json() == {"detail": "context_retrieval_not_authorized"}
    assert extra.status_code == 422
    assert extra.json() == {"detail": "context_retrieval_parameters_invalid"}
    assert calls == []


def test_context_retrieval_callback_rejects_terminal_run_and_cross_snapshot_id(monkeypatch):
    terminal_calls = _patch_route(monkeypatch, status="succeeded")
    client = TestClient(create_app())
    headers = {"X-AI-Platform-Callback-Token": _token("secret")}
    terminal = client.post(
        "/api/ai/runtime/callbacks/context-retrieval",
        headers=headers,
        json=_payload(),
    )
    assert terminal.status_code == 409
    assert terminal_calls == []

    denied_calls = _patch_route(
        monkeypatch,
        action_result=ContextRetrievalDenied("context_scope_denied"),
    )
    denied = client.post(
        "/api/ai/runtime/callbacks/context-retrieval",
        headers=headers,
        json=_payload(arguments={"artifact_id": "artifact-foreign"}),
    )
    assert denied.status_code == 403
    assert denied.json() == {"detail": "context_scope_denied"}
    assert denied_calls[0][1] == {"artifact_id": "artifact-foreign"}


def test_context_retrieval_callback_fails_closed_when_fixed_snapshot_is_unavailable(monkeypatch):
    calls = _patch_route(monkeypatch)
    import app.routes.runtime_callbacks as callbacks

    async def missing_snapshot(*args, **kwargs):
        return None

    monkeypatch.setattr(callbacks.repositories, "get_bound_executor_context_snapshot", missing_snapshot)
    response = TestClient(create_app()).post(
        "/api/ai/runtime/callbacks/context-retrieval",
        headers={"X-AI-Platform-Callback-Token": _token("secret")},
        json=_payload(),
    )

    assert response.status_code == 409
    assert response.json() == {"detail": "context_snapshot_unavailable"}
    assert calls == []


@pytest.mark.parametrize(
    ("route_kwargs", "detail"),
    [
        ({"lease_attempt": "attempt-b"}, "sandbox_runtime_attempt_mismatch"),
        ({"active_lease": False}, "sandbox_runtime_attempt_inactive"),
    ],
)
def test_context_retrieval_callback_rejects_stale_or_released_attempt_before_action(
    monkeypatch,
    route_kwargs,
    detail,
):
    calls = _patch_route(monkeypatch, **route_kwargs)
    response = TestClient(create_app()).post(
        "/api/ai/runtime/callbacks/context-retrieval",
        headers={"X-AI-Platform-Callback-Token": _token("secret")},
        json=_payload(),
    )

    assert response.status_code == 409
    assert response.json() == {"detail": detail}
    assert calls == []


@pytest.mark.asyncio
async def test_platform_context_client_stages_brokered_bytes_without_returning_token(tmp_path, monkeypatch):
    requests = []
    raw = b"docx-bytes"

    class Response:
        status_code = 200

        def json(self):
            return {
                "result": {
                    "artifact_id": "artifact-a",
                    "name": "translated.docx",
                    "content_base64": base64.b64encode(raw).decode("ascii"),
                    "bytes_read": len(raw),
                }
            }

    class Client:
        def __init__(self, *, timeout):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return None

        async def post(self, url, *, json, headers):
            requests.append((url, json, headers))
            return Response()

    monkeypatch.setattr("app.runtime.sandbox.context_retrieval_client.httpx.AsyncClient", Client)
    scope = ContextRetrievalScope(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        session_id="session-a",
        run_id="run-a",
        agent_id="agent-a",
    )
    retrieval = PlatformContextRetrievalClient(
        callback_url="http://platform.test/api/ai/runtime/callbacks/context-retrieval",
        callback_token_id="cbt:run-a:attempt-a",
        callback_token="secret",
        attempt_id="attempt-a",
        scope=scope,
    )

    result = await retrieval.stage_run_artifact_to_workspace(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        session_id="session-a",
        run_id="run-a",
        artifact_id="artifact-a",
        workspace_root=str(tmp_path),
    )

    assert (tmp_path / "context" / "artifact-a" / "translated.docx").read_bytes() == raw
    assert result["workspace_path"] == "context/artifact-a/translated.docx"
    assert "secret" not in str(result)
    assert requests[0][1]["run_id"] == "run-a"
    assert requests[0][1]["attempt_id"] == "attempt-a"
    assert requests[0][2] == {"X-AI-Platform-Callback-Token": "secret"}


@pytest.mark.asyncio
async def test_platform_context_client_rejects_forged_scope_before_callback(monkeypatch):
    class FailClient:
        def __init__(self, **kwargs):
            raise AssertionError("forged scope must be rejected before HTTP")

    monkeypatch.setattr("app.runtime.sandbox.context_retrieval_client.httpx.AsyncClient", FailClient)
    retrieval = PlatformContextRetrievalClient(
        callback_url="http://platform.test/api/ai/runtime/callbacks/context-retrieval",
        callback_token_id="cbt:run-a:attempt-a",
        callback_token="secret",
        attempt_id="attempt-a",
        scope=ContextRetrievalScope(
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            user_id="user-a",
            session_id="session-a",
            run_id="run-a",
            agent_id="agent-a",
        ),
    )

    with pytest.raises(ContextRetrievalDenied, match="context_scope_denied"):
        await retrieval.read_run_artifact(
            tenant_id="tenant-b",
            workspace_id="workspace-a",
            user_id="user-a",
            session_id="session-a",
            run_id="run-a",
            artifact_id="artifact-a",
        )


@pytest.mark.asyncio
async def test_callback_dispatcher_exports_only_bounded_broker_payload():
    retrieval = ContextRetrieval(
        InMemoryContextRetrievalRepository(
            artifacts=[
                {
                    "tenant_id": "tenant-a",
                    "workspace_id": "workspace-a",
                    "user_id": "user-a",
                    "session_id": "session-a",
                    "run_id": "run-a",
                    "artifact_id": "artifact-a",
                    "label": "translated.docx",
                    "content": "artifact-bytes",
                }
            ]
        )
    )
    identity = {
        "tenant_id": "tenant-a",
        "workspace_id": "workspace-a",
        "user_id": "user-a",
        "session_id": "session-a",
        "run_id": "run-a",
        "agent_id": "agent-a",
    }

    result = await _run_context_retrieval_action(
        retrieval,
        action="stage_run_artifact_to_workspace",
        arguments={"artifact_id": "artifact-a", "max_bytes": 32},
        identity=identity,
    )

    assert base64.b64decode(result["content_base64"]) == b"artifact-bytes"
    assert result["artifact_id"] == "artifact-a"
    assert result["name"] == "translated.docx"
    assert "content_bytes" not in result
