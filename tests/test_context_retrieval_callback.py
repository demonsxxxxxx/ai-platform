import app.context.infrastructure.snapshot_postgres as _owner_context_infrastructure_snapshot_postgres
import app.runs.infrastructure.postgres as _owner_runs_infrastructure_postgres
import app.sandbox.infrastructure.leases_postgres as _owner_sandbox_infrastructure_leases_postgres
import app.streaming.infrastructure.run_events_postgres as _owner_streaming_infrastructure_run_events_postgres
import base64
import hashlib
import hmac
import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from app.context.retrieval import (
    ContextRetrievalAuthority,
    ContextRetrievalDenied,
    ContextRetrievalInputError,
)
from app.files.infrastructure.profile_drive import open_profile_drive_file
from tests.support.context_retrieval import InMemoryContextRetrievalRepository
from app.main import create_app
from app.runtime.sandbox.context_retrieval_client import PlatformContextRetrievalClient
from app.runtime.sandbox.contracts import (
    PROFILE_DRIVE_STAGE_MAX_BYTES,
    ContextRetrievalScope,
)


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
    profile_authorized=False,
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
            if tool == "read_session_messages":
                manifest["selection"] = {
                    "selection_version": "conversation-turns-v1",
                    "history_candidate_count": 1,
                    "history_authorized_count": 1,
                    "history_omitted_count": 0,
                }
                continue
            key, values = refs[tool]
            manifest[key] = values
        return {"payload_json": {"context_manifest": manifest}}

    async def list_current_leases(conn, *, tenant_id, run_id, attempt_id):
        if not active_lease:
            return []
        lease_payload = {"attempt_id": lease_attempt}
        if profile_authorized:
            lease_payload["profile_drive_file_staging_authorized"] = True
        return [{"lease_payload_json": lease_payload}]

    async def run_action(action, identity, arguments):
        if "tenant_id" in arguments:
            raise ContextRetrievalInputError("context_retrieval_parameters_invalid")
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
    monkeypatch.setattr(_owner_runs_infrastructure_postgres, 'get_run_identity', get_run_identity)
    monkeypatch.setattr(
        _owner_sandbox_infrastructure_leases_postgres,
        'list_current_sandbox_runtime_leases_for_attempt',
        list_current_leases,
    )
    monkeypatch.setattr(_owner_context_infrastructure_snapshot_postgres, 'get_bound_executor_context_snapshot', get_snapshot)
    monkeypatch.setattr(_owner_streaming_infrastructure_run_events_postgres, 'append_event', append_event)
    monkeypatch.setattr(callbacks, "ObjectStorage", lambda: object())
    authority = type("Authority", (), {"execute": staticmethod(run_action)})()
    monkeypatch.setattr(
        callbacks.ContextRetrievalAuthority,
        "for_broker_connection",
        staticmethod(lambda conn, storage, *, storage_io: authority),
    )
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

    async def exact_lease(conn, *, tenant_id, run_id, attempt_id):
        lease_checks.append((tenant_id, run_id, attempt_id))
        if attempt_id not in {"attempt-a", "attempt-b"}:
            return []
        return [{"lease_payload_json": {"attempt_id": attempt_id}}]

    monkeypatch.setattr(_owner_sandbox_infrastructure_leases_postgres, 'list_current_sandbox_runtime_leases_for_attempt', exact_lease)
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


def test_context_retrieval_callback_rejects_retired_session_message_action(monkeypatch):
    calls = _patch_route(monkeypatch, tools=["read_session_messages"])
    response = TestClient(create_app()).post(
        "/api/ai/runtime/callbacks/context-retrieval",
        headers={"X-AI-Platform-Callback-Token": _token("secret")},
        json=_payload(
            action="read_session_messages",
            arguments={"limit": 5, "offset": 0, "max_tokens": 20},
        ),
    )

    assert response.status_code == 422
    assert calls == []


def test_context_retrieval_callback_rejects_unadvertised_action(monkeypatch):
    calls = _patch_route(monkeypatch, tools=["read_session_messages"])
    client = TestClient(create_app())
    headers = {"X-AI-Platform-Callback-Token": _token("secret")}

    unadvertised = client.post(
        "/api/ai/runtime/callbacks/context-retrieval",
        headers=headers,
        json=_payload(),
    )

    assert unadvertised.status_code == 403
    assert unadvertised.json() == {"detail": "context_retrieval_not_authorized"}
    assert calls == []


def test_context_retrieval_callback_maps_authority_argument_denial(monkeypatch):
    calls = _patch_route(monkeypatch, tools=["read_run_artifact"])
    extra = TestClient(create_app()).post(
        "/api/ai/runtime/callbacks/context-retrieval",
        headers={"X-AI-Platform-Callback-Token": _token("secret")},
        json=_payload(arguments={"artifact_id": "artifact-a", "tenant_id": "tenant-b"}),
    )

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

    async def missing_snapshot(*args, **kwargs):
        return None

    monkeypatch.setattr(_owner_context_infrastructure_snapshot_postgres, 'get_bound_executor_context_snapshot', missing_snapshot)
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


def test_profile_drive_callback_rejects_lease_without_staging_authority(monkeypatch):
    _patch_route(monkeypatch)
    response = TestClient(create_app()).post(
        "/api/ai/runtime/callbacks/context-retrieval",
        headers={"X-AI-Platform-Callback-Token": _token("secret")},
        json=_payload(
            action="stage_profile_drive_file_to_workspace",
            arguments={"path": "S24/source.pdf"},
        ),
    )

    assert response.status_code == 403
    assert response.json() == {"detail": "context_retrieval_not_authorized"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "expected_content_type"),
    [
        (
            "项目基本信息收集表.docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ),
        (
            "数据收集表.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ),
        (
            "汇报材料.pptx",
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        ),
    ],
)
async def test_profile_drive_transfer_has_stable_office_content_types(
    monkeypatch,
    path,
    expected_content_type,
):
    real_async_client = httpx.AsyncClient
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(
            200,
            headers={"Content-Length": "1", "Content-Type": "application/octet-stream"},
            stream=httpx.ByteStream(b"x"),
        )
    )
    monkeypatch.setattr(
        "app.files.infrastructure.profile_drive.httpx.AsyncClient",
        lambda **kwargs: real_async_client(transport=transport, **kwargs),
    )

    client, response, _content_length, content_type = await open_profile_drive_file(
        upstream="https://profile-drive.test",
        ca_cert_file="",
        jwt="company.jwt",
        path=path,
        max_bytes=1024,
        require_nonempty=True,
        not_found_status=404,
        too_large_detail="file_too_large",
    )
    try:
        assert content_type == expected_content_type
    finally:
        await response.aclose()
        await client.aclose()


def test_profile_drive_callback_streams_only_for_attempt_authorized_lease(monkeypatch):
    import app.routes.runtime_callbacks as callbacks

    calls = _patch_route(monkeypatch, profile_authorized=True)
    binary = b"%PDF-\x00\xff-profile-drive"
    captured = {}
    real_async_client = httpx.AsyncClient

    def upstream(request: httpx.Request) -> httpx.Response:
        captured["authorization"] = request.headers.get("authorization")
        captured["payload"] = request.content
        return httpx.Response(
            200,
            headers={"Content-Length": str(len(binary)), "Content-Type": "application/octet-stream"},
            stream=httpx.ByteStream(binary),
        )

    transport = httpx.MockTransport(upstream)

    def client_factory(**kwargs):
        return real_async_client(transport=transport, **kwargs)

    class JwtStore:
        async def get(self, principal):
            assert (principal.tenant_id, principal.user_id) == ("tenant-a", "user-a")
            return "company.jwt"

    monkeypatch.setattr("app.files.infrastructure.profile_drive.httpx.AsyncClient", client_factory)
    monkeypatch.setattr(callbacks, "get_mcp_principal_jwt_store", lambda: JwtStore())
    monkeypatch.setattr(
        callbacks,
        "get_settings",
        lambda: SimpleNamespace(
            sandbox_callback_token="secret",
            profile_drive_transfer_upstream="https://profile-drive.test",
            profile_drive_transfer_ca_cert_file="",
        ),
    )

    response = TestClient(create_app()).post(
        "/api/ai/runtime/callbacks/context-retrieval",
        headers={"X-AI-Platform-Callback-Token": _token("secret")},
        json=_payload(
            action="stage_profile_drive_file_to_workspace",
            arguments={"path": "S24/source.pdf"},
        ),
    )

    assert response.status_code == 200
    assert response.content == binary
    assert response.headers["cache-control"] == "private, no-store"
    assert captured["authorization"] == "Bearer company.jwt"
    assert json.loads(captured["payload"]) == {"path": "S24/source.pdf"}
    assert any(call[0] == "event" for call in calls)


@pytest.mark.asyncio
async def test_platform_context_client_streams_profile_drive_file_to_workspace(tmp_path, monkeypatch):
    binary = b"PK\x03\x04\x00\xff-arbitrary-file"
    real_async_client = httpx.AsyncClient

    def callback(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert payload["action"] == "stage_profile_drive_file_to_workspace"
        assert payload["arguments"] == {"path": "S24/source.docx"}
        return httpx.Response(200, headers={"Content-Length": str(len(binary))}, content=binary)

    transport = httpx.MockTransport(callback)

    def client_factory(**kwargs):
        return real_async_client(transport=transport, **kwargs)

    monkeypatch.setattr(
        "app.runtime.sandbox.context_retrieval_client.httpx.AsyncClient",
        client_factory,
    )
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
        workspace_root=tmp_path,
    )

    result = await retrieval.execute(
        "stage_profile_drive_file_to_workspace",
        SimpleNamespace(**scope.model_dump()),
        {"path": "S24/source.docx"},
    )

    staged = tmp_path / result["workspace_path"]
    assert staged.read_bytes() == binary
    assert result["workspace_path"].endswith("/source.docx")
    assert list(staged.parent.glob("*.part")) == []
    assert result["redaction"] == {"source_path_removed": True}


@pytest.mark.asyncio
async def test_platform_context_client_rejects_oversized_profile_drive_file_without_writing(
    tmp_path,
    monkeypatch,
):
    real_async_client = httpx.AsyncClient

    def callback(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"Content-Length": str(PROFILE_DRIVE_STAGE_MAX_BYTES + 1)},
            stream=httpx.ByteStream(b""),
        )

    transport = httpx.MockTransport(callback)

    def client_factory(**kwargs):
        return real_async_client(transport=transport, **kwargs)

    monkeypatch.setattr(
        "app.runtime.sandbox.context_retrieval_client.httpx.AsyncClient",
        client_factory,
    )
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
        workspace_root=tmp_path,
    )

    with pytest.raises(ContextRetrievalDenied, match="profile_drive_file_too_large"):
        await retrieval.execute(
            "stage_profile_drive_file_to_workspace",
            SimpleNamespace(**scope.model_dump()),
            {"path": "S24/source.pdf"},
        )

    assert not any(path.is_file() for path in tmp_path.rglob("*"))


@pytest.mark.asyncio
@pytest.mark.parametrize("raw", [b"docx-bytes", b""])
async def test_platform_context_client_stages_brokered_bytes_without_returning_token(tmp_path, monkeypatch, raw):
    requests = []

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
    assert result["bytes_staged"] == len(raw)
    assert result["audit"]["bytes_read"] == len(raw)
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
@pytest.mark.parametrize("content", ["artifact-bytes", ""])
async def test_callback_dispatcher_exports_only_bounded_broker_payload(content):
    retrieval = ContextRetrievalAuthority(
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
                    "content": content,
                    "size_bytes": len(content.encode("utf-8")),
                }
            ]
        ),
        _stage_delivery="broker_export",
    )
    identity = {
        "tenant_id": "tenant-a",
        "workspace_id": "workspace-a",
        "user_id": "user-a",
        "session_id": "session-a",
        "run_id": "run-a",
        "agent_id": "agent-a",
    }

    result = await retrieval.execute(
        "stage_run_artifact_to_workspace",
        identity,
        {"artifact_id": "artifact-a", "max_bytes": 32},
    )

    assert base64.b64decode(result["content_base64"]) == content.encode("utf-8")
    assert result["bytes_read"] == len(content.encode("utf-8"))
    assert result["artifact_id"] == "artifact-a"
    assert result["name"] == "translated.docx"
    assert "content_bytes" not in result


@pytest.mark.parametrize("count", [None, -1, 1, True, False, 0.0, "0", "missing"])
def test_platform_context_client_rejects_invalid_empty_artifact_byte_counts(tmp_path, count):
    scope = ContextRetrievalScope(
        tenant_id="tenant-a", workspace_id="workspace-a", user_id="user-a",
        session_id="session-a", run_id="run-a", agent_id="agent-a",
    )
    client = PlatformContextRetrievalClient(
        callback_url="http://platform.test/context-retrieval",
        callback_token_id="cbt:run-a:attempt-a", callback_token="synthetic",
        attempt_id="attempt-a", scope=scope,
    )
    result = {"artifact_id": "artifact-a", "name": "empty.txt", "content_base64": ""}
    if count != "missing":
        result["bytes_read"] = count
    with pytest.raises(ContextRetrievalDenied, match="context_scope_denied"):
        client._stage_result(
            result, id_key="artifact_id", expected_id="artifact-a",
            workspace_root=str(tmp_path), max_bytes=32,
        )
    assert not list(tmp_path.iterdir())


class _ArtifactPrefixBody:
    def __init__(self, payload, *, chunk_bytes=2):
        self.payload = payload
        self.chunk_bytes = chunk_bytes
        self.position = 0
        self.read_sizes = []
        self.closed = False

    def read(self, size):
        assert size > 0, "prefix reads must always have a positive byte bound"
        self.read_sizes.append(size)
        start = self.position
        self.position = min(len(self.payload), start + min(size, self.chunk_bytes))
        return self.payload[start:self.position]

    def close(self):
        self.closed = True


def _artifact_prefix_authority(monkeypatch, body, *, transactional=False, allowed=True):
    from app.context import retrieval as retrieval_module
    from app.storage import ObjectStorage, run_storage_io

    scope = {
        "tenant_id": "tenant-a",
        "workspace_id": "workspace-a",
        "user_id": "user-a",
        "session_id": "session-a",
        "run_id": "run-a",
    }
    storage_calls = []

    async def get_artifact(conn, **kwargs):
        assert kwargs == {**scope, "artifact_id": "artifact-a"}
        if not allowed:
            return None
        return {
            "id": "artifact-a",
            "label": "report.txt",
            "artifact_type": "report_txt",
            "storage_key": "private/artifact-a",
            # Preview truncation must come from bytes, not stale declared size.
            "size_bytes": 1,
        }

    class Client:
        def get_object(self, **kwargs):
            storage_calls.append(kwargs)
            assert kwargs == {"Bucket": "bucket", "Key": "private/artifact-a"}
            return {"Body": body}

    def forbidden_full_read(*args, **kwargs):
        raise AssertionError("a text preview must not use whole-object reads")

    monkeypatch.setattr(
        retrieval_module.context_sources_postgres, "get_scoped_context_artifact", get_artifact,
    )
    storage = ObjectStorage.__new__(ObjectStorage)
    storage.bucket = "bucket"
    storage.client = Client()
    storage.get_bytes = forbidden_full_read
    storage.get_bytes_bounded = forbidden_full_read
    if transactional:
        authority = ContextRetrievalAuthority.for_transaction(_Transaction, storage)
    else:
        authority = ContextRetrievalAuthority.for_broker_connection(
            object(), storage, storage_io=run_storage_io,
        )
    return authority, scope, storage_calls


@pytest.mark.asyncio
@pytest.mark.parametrize("transactional", [False, True])
@pytest.mark.parametrize(
    ("payload", "cap", "content", "truncated"),
    [
        (b"", 1, "", False),
        (b"a", 1, "a", False),
        (b"ab", 1, "a", True),
        (b"x" * 1048576, 1, "x", True),
        ("你好".encode(), 3, "你", True),
        ("你好".encode(), 4, "你", True),
        ("你好".encode(), 6, "你好", False),
        ("A😀Z".encode(), 3, "A", True),
        (b"a\xffb", 3, "ab", False),
    ],
    ids=("empty", "exact", "sentinel", "large", "utf8-exact", "utf8-cut", "utf8-full", "utf8-four-byte", "invalid-utf8"),
)
async def test_context_artifact_preview_reads_only_prefix_and_closes_body(
    monkeypatch, transactional, payload, cap, content, truncated,
):
    body = _ArtifactPrefixBody(payload)
    authority, scope, storage_calls = _artifact_prefix_authority(
        monkeypatch, body, transactional=transactional,
    )

    result = await authority.execute(
        "read_run_artifact", scope, {"artifact_id": "artifact-a", "max_bytes": cap},
    )

    assert result["content"] == content
    assert result["truncated"] is truncated
    assert result["artifact_id"] == "artifact-a"
    assert result["label"] == "report.txt"
    assert "private/artifact-a" not in json.dumps(result)
    assert body.position == min(len(payload), cap + 1)
    assert max(body.read_sizes) <= cap + 1
    assert body.closed
    assert len(storage_calls) == 1


@pytest.mark.asyncio
async def test_context_artifact_preview_caps_large_chunks_and_preserves_redaction(monkeypatch):
    body = _ArtifactPrefixBody(b"x" * 1048576, chunk_bytes=1048576)
    authority, scope, _ = _artifact_prefix_authority(monkeypatch, body)
    result = await authority.execute(
        "read_run_artifact", scope, {"artifact_id": "artifact-a", "max_bytes": 262144},
    )
    assert result["content"] == "x" * 262144
    assert result["truncated"] is True
    assert body.position == 262145
    assert max(body.read_sizes) == 65536
    assert body.closed

    body = _ArtifactPrefixBody(b"/tmp/private/runtime.txt")
    authority, scope, _ = _artifact_prefix_authority(monkeypatch, body)
    result = await authority.execute("read_run_artifact", scope, {"artifact_id": "artifact-a"})
    assert result["content"] == ""
    assert result["truncated"] is False
    assert body.closed


@pytest.mark.asyncio
async def test_context_artifact_preview_denial_precedes_storage_access(monkeypatch):
    body = _ArtifactPrefixBody(b"private contents")
    authority, scope, storage_calls = _artifact_prefix_authority(monkeypatch, body, allowed=False)
    with pytest.raises(ContextRetrievalDenied, match="^context_scope_denied$"):
        await authority.execute("read_run_artifact", scope, {"artifact_id": "artifact-a"})
    assert storage_calls == []
    assert body.read_sizes == []


@pytest.mark.asyncio
async def test_context_artifact_preview_closes_body_on_storage_error(monkeypatch):
    class FailedBody(_ArtifactPrefixBody):
        def read(self, size):
            super().read(size)
            raise OSError("synthetic interrupted object read")

    body = FailedBody(b"partial")
    authority, scope, _ = _artifact_prefix_authority(monkeypatch, body)
    with pytest.raises(OSError, match="synthetic interrupted object read"):
        await authority.execute("read_run_artifact", scope, {"artifact_id": "artifact-a"})
    assert body.closed


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True], ids=("timeout", "cancel"))
async def test_context_artifact_prefix_abandonment_holds_capacity_until_body_closes(
    monkeypatch, cancel,
):
    import asyncio
    import threading

    from app import storage as storage_module

    entered = threading.Event()
    release = threading.Event()
    closed = threading.Event()
    admissions = asyncio.BoundedSemaphore(1)
    slots = asyncio.Semaphore(1)
    monkeypatch.setattr(storage_module, "_STORAGE_IO_ADMISSIONS", admissions)
    monkeypatch.setattr(storage_module, "_STORAGE_IO_SLOTS", slots)

    class PausedBody(_ArtifactPrefixBody):
        def read(self, size):
            entered.set()
            if not release.wait(timeout=5):
                raise TimeoutError("test did not release the synthetic read")
            return super().read(size)

        def close(self):
            super().close()
            closed.set()

    body = PausedBody(b"longer than preview")
    authority, scope, _ = _artifact_prefix_authority(monkeypatch, body)

    async def bounded_io(operation, *args, **kwargs):
        return await storage_module.run_storage_io(
            operation, *args, timeout_seconds=5 if cancel else 0.05, **kwargs,
        )

    authority._storage_io = bounded_io
    task = asyncio.create_task(authority.execute(
        "read_run_artifact", scope, {"artifact_id": "artifact-a", "max_bytes": 1},
    ))
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        if cancel:
            task.cancel()
        with pytest.raises(asyncio.CancelledError if cancel else storage_module.StorageIOTimeoutError):
            await task
        assert not body.closed
        with pytest.raises(storage_module.StorageIOBusyError):
            await storage_module.run_storage_io(lambda: "must not start")
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        assert await asyncio.to_thread(closed.wait, 2)
        # Wait for the worker's capacity callback before restoring global slots.
        await asyncio.wait_for(admissions.acquire(), timeout=2)
        admissions.release()
    assert body.closed
    assert body.position == 2
    assert await storage_module.run_storage_io(lambda: "available") == "available"


def test_context_retrieval_callback_returns_large_artifact_as_truncated_preview(monkeypatch):
    body = _ArtifactPrefixBody(b"x" * 1048576)
    authority, _, storage_calls = _artifact_prefix_authority(monkeypatch, body)
    calls = _patch_route(monkeypatch)
    monkeypatch.setattr(
        ContextRetrievalAuthority,
        "for_broker_connection",
        staticmethod(lambda conn, storage, *, storage_io: authority),
    )

    with TestClient(create_app()) as client:
        response = client.post(
            "/api/ai/runtime/callbacks/context-retrieval",
            headers={"X-AI-Platform-Callback-Token": _token("secret")},
            json=_payload(arguments={"artifact_id": "artifact-a", "max_bytes": 1}),
        )

    assert response.status_code == 200
    assert response.json()["result"]["content"] == "x"
    assert response.json()["result"]["truncated"] is True
    assert body.position == 2
    assert body.closed
    assert len(storage_calls) == 1
    assert calls[0][0] == "event"
