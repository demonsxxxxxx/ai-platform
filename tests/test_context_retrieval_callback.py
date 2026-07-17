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


def _token(secret: str, token_id: str = "cbt_run-a") -> str:
    return hmac.new(secret.encode(), token_id.encode(), hashlib.sha256).hexdigest()


def _payload(action: str = "read_run_artifact", arguments=None, **overrides):
    payload = {
        "session_id": "session-a",
        "run_id": "run-a",
        "callback_token_id": "cbt_run-a",
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


def _patch_route(monkeypatch, *, status="running", tools=None, action_result=None):
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

    async def get_snapshot(conn, *, tenant_id, user_id, run_id):
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
    monkeypatch.setattr(callbacks.repositories, "get_latest_authorized_executor_context_snapshot", get_snapshot)
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


def test_context_retrieval_callback_rejects_wrong_token_before_storage(monkeypatch):
    calls = _patch_route(monkeypatch)
    response = TestClient(create_app()).post(
        "/api/ai/runtime/callbacks/context-retrieval",
        headers={"X-AI-Platform-Callback-Token": "wrong"},
        json=_payload(),
    )

    assert response.status_code == 401
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
        callback_token_id="cbt_run-a",
        callback_token="secret",
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
    assert requests[0][2] == {"X-AI-Platform-Callback-Token": "secret"}


@pytest.mark.asyncio
async def test_platform_context_client_rejects_forged_scope_before_callback(monkeypatch):
    class FailClient:
        def __init__(self, **kwargs):
            raise AssertionError("forged scope must be rejected before HTTP")

    monkeypatch.setattr("app.runtime.sandbox.context_retrieval_client.httpx.AsyncClient", FailClient)
    retrieval = PlatformContextRetrievalClient(
        callback_url="http://platform.test/api/ai/runtime/callbacks/context-retrieval",
        callback_token_id="cbt_run-a",
        callback_token="secret",
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
