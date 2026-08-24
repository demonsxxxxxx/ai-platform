import asyncio
import base64
import hashlib
import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException, Response
from fastapi.testclient import TestClient

from app import repositories as repository_module
from app.auth import AuthPrincipal
from app.capability_distribution import CapabilityAuthorizationDenial
from app.main import create_app
from app.models import (
    ChatSessionRequest,
    ChatStreamRequest,
    ChatSubmissionResponse,
    QueueRunPayload,
)
from app.queue import QueueAdmissionMetadata, QueueAdmissionRejected
from app.queue_payload_validation import queue_payload_invalid_detail
from app.repositories import RepositoryConflictError
from app.runs.api import RunTerminalizationProgress
from app.routes.chat import (
    _admit_chat_submission,
    _audit_capability_denial,
    _canonical_pre_persistence_rejection_fingerprint,
    _chat_submission_http_error,
    _file_row_matches_request_scope,
    _preledger_recovery_fingerprint,
    _submission_code,
    _validate_queue_payload_for_enqueue,
    chat_stream,
    create_chat_session,
    get_chat_submission,
    list_messages,
    retry_chat_submission_admission,
)
from app.settings import Settings

_ORIGINAL_AUTHORIZE_RUN_CAPABILITIES = repository_module.authorize_run_capabilities
_ORIGINAL_GET_LATEST_AUTHORIZED_SESSION_RUN_INPUT = (
    repository_module.get_latest_authorized_session_run_input
)


@asynccontextmanager
async def fake_transaction():
    yield object()


@pytest.fixture
def chat_submission_client(monkeypatch):
    """Exercise the mounted aliases and their route-local response wrapper."""

    monkeypatch.setattr(
        "app.auth.get_settings",
        lambda: Settings(frontend_poc_auth_enabled=True),
    )

    async def require_schema_current():
        return {"ready": True}

    monkeypatch.setattr(
        "app.main.require_schema_current",
        require_schema_current,
    )
    with TestClient(create_app(), raise_server_exceptions=False) as client:
        yield client


_CHAT_SUBMISSION_ROUTE_PREFIXES = ("/api", "/api/ai")
_CHAT_SUBMISSION_CLIENT_HEADERS = {
    "x-ai-user-id": "user-a",
    "x-ai-tenant-id": "tenant-a",
}


@pytest.mark.parametrize("prefix", _CHAT_SUBMISSION_ROUTE_PREFIXES)
def test_chat_submission_resolver_success_is_private_no_store(
    monkeypatch, chat_submission_client, prefix
):
    submission_id = "7ea93033-30f5-40ea-8a33-2f3c6e7b21c4"

    async def found_submission(_conn, **_kwargs):
        return {
            "submission_id": submission_id,
            "state": "queued",
            "outcome_json": {
                "session_id": "session-1",
                "run_id": "run-1",
                "trace_id": "trace-1",
                "status": "queued",
                "submission_id": submission_id,
            },
        }

    monkeypatch.setattr("app.routes.chat.transaction", fake_transaction)
    monkeypatch.setattr(repository_module, "get_chat_submission", found_submission)

    response = chat_submission_client.get(
        f"{prefix}/chat/submissions/{submission_id}",
        headers=_CHAT_SUBMISSION_CLIENT_HEADERS,
    )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, no-store"


@pytest.mark.parametrize("prefix", _CHAT_SUBMISSION_ROUTE_PREFIXES)
def test_chat_submission_resolver_missing_is_private_no_store_over_http(
    monkeypatch, chat_submission_client, prefix
):
    async def missing_submission(_conn, **_kwargs):
        return None

    monkeypatch.setattr("app.routes.chat.transaction", fake_transaction)
    monkeypatch.setattr(repository_module, "get_chat_submission", missing_submission)

    response = chat_submission_client.get(
        f"{prefix}/chat/submissions/7ea93033-30f5-40ea-8a33-2f3c6e7b21c4",
        headers=_CHAT_SUBMISSION_CLIENT_HEADERS,
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "chat_submission_not_found"}
    assert response.headers["cache-control"] == "private, no-store"


@pytest.mark.parametrize("prefix", _CHAT_SUBMISSION_ROUTE_PREFIXES)
def test_chat_submission_resolver_validation_error_is_private_no_store_over_http(
    chat_submission_client, prefix
):
    response = chat_submission_client.get(
        f"{prefix}/chat/submissions/not-a-uuid",
        headers=_CHAT_SUBMISSION_CLIENT_HEADERS,
    )

    assert response.status_code == 422
    assert response.json()["detail"]
    assert response.headers["cache-control"] == "private, no-store"


@pytest.mark.parametrize("prefix", _CHAT_SUBMISSION_ROUTE_PREFIXES)
def test_retry_admission_http_error_is_private_no_store_over_http(
    monkeypatch, chat_submission_client, prefix
):
    async def unavailable_recovery(*_args, **_kwargs):
        raise HTTPException(status_code=503, detail="queue_unavailable")

    monkeypatch.setattr(
        "app.routes.chat._recover_preledger_chat_submission",
        unavailable_recovery,
    )

    response = chat_submission_client.post(
        f"{prefix}/chat/submissions/7ea93033-30f5-40ea-8a33-2f3c6e7b21c4/retry-admission",
        headers=_CHAT_SUBMISSION_CLIENT_HEADERS,
    )

    assert response.status_code == 503
    assert response.json() == {"detail": "queue_unavailable"}
    assert response.headers["cache-control"] == "private, no-store"


@pytest.mark.parametrize("prefix", _CHAT_SUBMISSION_ROUTE_PREFIXES)
def test_retry_admission_unhandled_error_is_private_no_store_over_http(
    monkeypatch, chat_submission_client, prefix, caplog
):
    async def broken_recovery(*_args, **_kwargs):
        raise RuntimeError("unexpected persistence failure")

    monkeypatch.setattr(
        "app.routes.chat._recover_preledger_chat_submission",
        broken_recovery,
    )

    with caplog.at_level("ERROR", logger="app.routes.chat"):
        response = chat_submission_client.post(
            f"{prefix}/chat/submissions/7ea93033-30f5-40ea-8a33-2f3c6e7b21c4/retry-admission",
            headers=_CHAT_SUBMISSION_CLIENT_HEADERS,
        )

    assert response.status_code == 500
    detail = response.json()["detail"]
    assert detail["code"] == "chat_submission_internal_error"
    assert detail["diagnostic_id"].startswith("diag_")
    assert len(detail["diagnostic_id"]) == 21
    int(detail["diagnostic_id"][5:], 16)
    assert "submission_disposition" not in detail
    assert response.headers["cache-control"] == "private, no-store"
    assert detail["diagnostic_id"] in caplog.text
    assert "phase=resolver" in caplog.text
    assert "exception_type=RuntimeError" in caplog.text
    assert "unexpected persistence failure" not in caplog.text


def principal(**overrides):
    values = {"user_id": "user-a", "display_name": "User A", "tenant_id": "tenant-a"}
    values.update(overrides)
    return AuthPrincipal(**values)


def test_file_row_scope_uses_effective_continuation_workspace():
    request = ChatStreamRequest(message="review", workspace_id="default", session_id="ses-owned")
    row = {
        "tenant_id": "tenant-a",
        "workspace_id": "workspace-owned",
        "user_id": "user-a",
        "session_id": "ses-owned",
        "run_id": None,
    }
    assert _file_row_matches_request_scope(
        row, request, principal(), workspace_id="workspace-owned"
    )
    assert not _file_row_matches_request_scope(
        row, request, principal(), workspace_id="workspace-other"
    )


def test_chat_mcp_selection_model_preserves_omitted_clear_and_explicit_contract():
    assert ChatStreamRequest(message="hello").selected_mcp_tool_ids is None
    assert ChatStreamRequest(message="hello", selected_mcp_tool_ids=[]).selected_mcp_tool_ids == []
    assert ChatStreamRequest(
        message="hello",
        selected_mcp_tool_ids=[" gateway::tenant-search ", "gateway::tenant-write"],
    ).selected_mcp_tool_ids == ["gateway::tenant-search", "gateway::tenant-write"]
    with pytest.raises(ValueError):
        ChatStreamRequest(
            message="hello",
            selected_mcp_tool_ids=["gateway::tenant-search", "gateway::tenant-search"],
        )
    with pytest.raises(ValueError):
        ChatStreamRequest(message="hello", selected_mcp_tool_ids=["not safe!"])


@pytest.mark.asyncio
async def test_latest_session_run_input_read_is_principal_and_workspace_scoped():
    class Cursor:
        async def fetchone(self):
            return {"input_json": {"input": {"mcp_tool_ids": ["gateway::locked-search"]}}}

    class Connection:
        def __init__(self):
            self.sql = ""
            self.params = ()

        async def execute(self, sql, params):
            self.sql = " ".join(sql.split())
            self.params = params
            return Cursor()

    conn = Connection()

    latest_input = await _ORIGINAL_GET_LATEST_AUTHORIZED_SESSION_RUN_INPUT(
        conn,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        session_id="session-a",
    )

    assert latest_input == {"input": {"mcp_tool_ids": ["gateway::locked-search"]}}
    assert conn.params == ("tenant-a", "workspace-a", "user-a", "session-a")
    assert "sessions.status = 'active'" in conn.sql
    assert "runs.tenant_id = %s" in conn.sql
    assert "runs.workspace_id = %s" in conn.sql
    assert "runs.user_id = %s" in conn.sql
    assert "runs.session_id = %s" in conn.sql


def test_rejection_fingerprint_covers_complete_request_without_retaining_raw_values():
    request = ChatStreamRequest(
        message="reject selected tool",
        agent_id="agent-a",
        skill_id="skill-a",
        selected_skill={"skill_id": "skill-a", "expected_version": "version-a"},
        agent_options={"model_id": "model-a"},
        input={"endpoint": "https://private.example.test/mcp", "credential": "secret-a"},
        file_ids=["file-a", "file-b"],
        attachments=[{"id": "attachment-a", "kind": "document"}],
        submission_id="7ea93033-30f5-40ea-8a33-2f3c6e7b21c4",
    )

    def fingerprint(candidate=request, *, query_agent_id="query-a", code="mcp_tool_not_available"):
        return _canonical_pre_persistence_rejection_fingerprint(
            request=candidate,
            principal=principal(),
            query_agent_id=query_agent_id,
            code=code,
        )

    expected = fingerprint()
    variants = {
        "query_agent_id": (request, "query-b"),
        "agent_id": (request.model_copy(update={"agent_id": "agent-b"}), "query-a"),
        "raw_skill_id": (request.model_copy(update={"skill_id": "skill-b"}), "query-a"),
        "selected_skill": (
            request.model_copy(
                update={
                    "selected_skill": request.selected_skill.model_copy(
                        update={"skill_id": "skill-b", "expected_version": "version-b"}
                    )
                }
            ),
            "query-a",
        ),
        "model": (request.model_copy(update={"agent_options": {"model_id": "model-b"}}), "query-a"),
        "input": (request.model_copy(update={"input": {**request.input, "credential": "secret-b"}}), "query-a"),
        "file_identity": (request.model_copy(update={"file_ids": ["file-c", "file-b"]}), "query-a"),
        "attachment_identity": (
            request.model_copy(update={"attachments": [{"id": "attachment-b", "kind": "document"}]}),
            "query-a",
        ),
    }

    for name, (variant, query_agent_id) in variants.items():
        assert fingerprint(variant, query_agent_id=query_agent_id) != expected, name
    assert fingerprint() == expected
    assert fingerprint(request.model_copy(update={"submission_id": None})) == expected
    assert fingerprint(request.model_copy(update={"input": dict(reversed(request.input.items()))})) == expected
    assert fingerprint(request.model_copy(update={"file_ids": list(reversed(request.file_ids))})) != expected
    assert fingerprint(code="capability_not_authorized") != expected
    assert expected != _canonical_pre_persistence_rejection_fingerprint(
        request=request,
        principal=principal(user_id="user-b"),
        query_agent_id="query-a",
        code="mcp_tool_not_available",
    )
    assert len(expected) == 64
    assert not any(raw in expected for raw in ("agent-a", "skill-a", "secret-a", "file-a"))


@pytest.mark.asyncio
async def test_mcp_denial_audit_redacts_raw_identity_and_correlates_deterministically(monkeypatch):
    audit_params = []

    class AuditConnection:
        async def execute(self, _sql, params):
            audit_params.append(params)

    @asynccontextmanager
    async def audit_transaction():
        yield AuditConnection()

    raw_tool_id = "unauthorized-private-tool"
    denial = CapabilityAuthorizationDenial(
        capability_kind="mcp_tool",
        capability_id=raw_tool_id,
        actor_department_id="finance",
        actor_roles=("user",),
        department_scope_ids=("qa",),
        role_scope_ids=("qa_operator",),
        scope_mode="allowlist",
        decision_reason="capability_not_available",
    )
    error = repository_module.RepositoryAuthorizationError(
        "mcp_tool_not_available",
        denial=denial,
    )

    monkeypatch.setattr("app.routes.chat.transaction", audit_transaction)

    await _audit_capability_denial(principal(), error, source="chat_stream")
    await _audit_capability_denial(principal(), error, source="chat_stream")

    target_ids = [params[5] for params in audit_params]
    payloads = [json.loads(params[8]) for params in audit_params]
    assert target_ids[0] == target_ids[1]
    assert target_ids[0].startswith("mcp_tool_sha256:")
    assert payloads[0]["capability_id"] == target_ids[0]
    assert payloads[1]["capability_id"] == target_ids[1]
    serialized_records = json.dumps(audit_params, sort_keys=True)
    assert raw_tool_id not in serialized_records
    assert "private.example.test" not in serialized_records
    assert "private-credential" not in serialized_records


@pytest.fixture(autouse=True)
def allow_existing_chat_route_tests_through_enqueue_authorization(monkeypatch):
    async def no_submission(*_args, **_kwargs):
        return None

    monkeypatch.setattr(repository_module, "get_chat_submission", no_submission)
    monkeypatch.setattr(
        "app.routes.chat._skill_manifest_pins",
        lambda skill_id, input_payload: [snapshot_manifest(skill_id)],
    )

    async def allow(conn, *, tenant_id, agent_id, skill_id, **_kwargs):
        return await repository_module.resolve_agent_skill(
            conn,
            tenant_id=tenant_id,
            agent_id=agent_id,
            skill_id=skill_id,
        )

    monkeypatch.setattr(repository_module, "authorize_run_capabilities", allow, raising=False)

    async def insert_creation_snapshots(*_args, **_kwargs):
        return None

    monkeypatch.setattr(
        repository_module,
        "insert_run_skill_snapshots_at_creation",
        insert_creation_snapshots,
        raising=False,
    )

    async def authorize_files(*_args, **_kwargs):
        return None

    async def ensure_workspace(*_args, **_kwargs):
        return None

    async def active_harness_chat_agent(_conn, *, tenant_id, agent_id):
        return {
            "id": agent_id,
            "tenant_id": tenant_id,
            "agent_type": "chat",
            "status": "active",
        }

    monkeypatch.setattr(
        repository_module, "authorize_files_for_run", authorize_files, raising=False
    )
    monkeypatch.setattr(
        repository_module,
        "ensure_workspace_belongs_to_tenant",
        ensure_workspace,
        raising=False,
    )
    monkeypatch.setattr(
        repository_module, "get_agent", active_harness_chat_agent, raising=False
    )

    async def no_latest_run_input(*_args, **_kwargs):
        return None

    monkeypatch.setattr(
        repository_module,
        "get_latest_authorized_session_run_input",
        no_latest_run_input,
        raising=False,
    )


@pytest.mark.asyncio
async def test_keyed_chat_replay_returns_the_recorded_outcome_before_routing(monkeypatch):
    request = ChatStreamRequest(
        message="durable replay",
        submission_id="7ea93033-30f5-40ea-8a33-2f3c6e7b21c4",
        selected_mcp_tool_ids=["qa-mcp::qa-search"],
        mcp_context_id="mcpctx-replay",
    )
    fingerprint = repository_module.chat_submission_fingerprint(
        {"request": request.model_dump(mode="json", exclude={"submission_id"}), "query_agent_id": None},
        tenant_id="tenant-a",
        user_id="user-a",
    )
    calls = []
    discarded = []

    async def existing_submission(conn, **kwargs):
        calls.append(kwargs)
        return {
            "submission_id": str(request.submission_id),
            "request_fingerprint_sha256": fingerprint,
            "state": "queued",
            "outcome_json": {
                "session_id": "ses_replayed",
                "run_id": "run_replayed",
                "status": "queued",
                "submission_id": str(request.submission_id),
            },
        }

    def forbidden_route(*_args, **_kwargs):
        raise AssertionError("replay must not route intent")

    async def discard_context(context_id, replay_principal):
        discarded.append((context_id, replay_principal.user_id))

    monkeypatch.setattr("app.routes.chat.transaction", fake_transaction)
    monkeypatch.setattr(repository_module, "get_chat_submission", existing_submission, raising=False)
    monkeypatch.setattr("app.routes.chat.route_intent", forbidden_route)
    monkeypatch.setattr(
        "app.routes.chat.mcp.discard_unbound_mcp_runtime_context",
        discard_context,
    )

    response = await chat_stream(request, principal=principal())

    assert response.session_id == "ses_replayed"
    assert response.run_id == "run_replayed"
    assert calls == [
        {
            "tenant_id": "tenant-a",
            "user_id": "user-a",
            "submission_id": str(request.submission_id),
        }
    ]
    assert discarded == [("mcpctx-replay", "user-a")]


@pytest.mark.parametrize("roles", [[], ["admin"]], ids=["ordinary", "admin"])
@pytest.mark.parametrize(
    ("message", "selected_tools", "context_id", "expected_authorizations"),
    [
        ("不要调用 MCP，只解释它是什么", ["qa-mcp::qa-search"], None, 0),
        ("请调用 MCP 搜索员工手册", ["qa-mcp::qa-search"], "mcpctx-test", 1),
        ("显式不选择 MCP 工具", [], None, 1),
    ],
    ids=["negative-veto", "affirmative", "explicit-empty-selection"],
)
@pytest.mark.asyncio
async def test_chat_stream_current_turn_controls_selected_mcp_before_authorization(
    monkeypatch, roles, message, selected_tools, context_id, expected_authorizations
):
    calls = {"authorization": 0, "bindings": []}
    preflight_calls = []

    async def authorize_run(*_args, **_kwargs):
        return {
            "executor_type": "claude-agent-worker",
            "skill_version": "hash-general-chat",
            "input_modes": ["chat"],
        }

    async def authorize_tools(*_args, **_kwargs):
        calls["authorization"] += 1
        return [{"tool_id": "qa-mcp::qa-search"}]

    async def create_session(*_args, **_kwargs):
        return "ses-polarity"

    async def create_run(*_args, **kwargs):
        calls["run_input"] = kwargs["input_json"]["input"]
        return "run-polarity"

    async def append_message(*_args, **_kwargs):
        return "msg-polarity"

    async def enqueue(payload):
        calls["queue_payload"] = payload
        calls["queue_input"] = payload["input"]
        return 1

    async def manifests(*_args, **_kwargs):
        return [snapshot_manifest("general-chat")]

    async def noop(*_args, **_kwargs):
        return None

    async def bind_context(_conn, **kwargs):
        calls["bindings"].append(kwargs)

    monkeypatch.setattr("app.routes.chat.transaction", fake_transaction)
    monkeypatch.setattr(repository_module, "authorize_run_capabilities", authorize_run)
    monkeypatch.setattr(repository_module, "authorize_selected_chat_mcp_tools", authorize_tools)
    monkeypatch.setattr(repository_module, "ensure_user", noop)
    monkeypatch.setattr(repository_module, "create_session", create_session)
    monkeypatch.setattr(repository_module, "create_run", create_run)
    monkeypatch.setattr(repository_module, "append_message", append_message)
    monkeypatch.setattr(repository_module, "bind_files_to_run", noop)
    monkeypatch.setattr(repository_module, "append_event", noop)
    monkeypatch.setattr("app.routes.chat._governed_skill_manifest_pins", manifests)
    monkeypatch.setattr("app.routes.chat.enqueue_run", enqueue)
    monkeypatch.setattr("app.routes.chat.mcp.bind_run_mcp_context", bind_context)

    async def preflight_stub(**kwargs):
        preflight_calls.append(kwargs)
        return None

    monkeypatch.setattr("app.routes.chat.mcp.preflight_mcp_admission", preflight_stub)

    request_kwargs = {"message": message}
    if selected_tools is not None:
        request_kwargs["selected_mcp_tool_ids"] = selected_tools
    if context_id is not None:
        request_kwargs["mcp_context_id"] = context_id

    response = await chat_stream(
        ChatStreamRequest(**request_kwargs),
        principal=principal(roles=roles),
    )

    assert response.status == "queued"
    assert calls["authorization"] == expected_authorizations
    expected_tools = selected_tools if expected_authorizations else None
    assert calls["run_input"].get("mcp_tool_ids") == expected_tools
    assert calls["queue_input"].get("mcp_tool_ids") == expected_tools
    assert "mcp_context_id" not in calls["queue_payload"]
    assert calls["queue_input"].get("mcp_context_id") == (
        context_id if expected_tools else None
    )
    assert "mcp_tool_names" not in calls["run_input"]
    assert "mcp_tool_names" not in calls["queue_input"]
    assert len(preflight_calls) == 1
    assert preflight_calls[0]["mcp_required"] is bool(expected_tools)
    assert preflight_calls[0]["context_id"] == context_id
    assert calls["bindings"] == (
        [
            {
                "tenant_id": "tenant-a",
                "run_id": "run-polarity",
                "mcp_context_id": context_id,
            }
        ]
        if expected_tools
        else []
    )


@pytest.mark.parametrize(
    "message",
    [
        "请执行 Bash 命令 pwd",
        "只解释 Bash 命令 pwd 是什么，不要执行",
        "不要执行 Bash 命令 pwd",
        "可以执行 Bash 吗？",
    ],
)
@pytest.mark.asyncio
async def test_chat_stream_never_turns_bash_text_into_required_capability(
    monkeypatch, message
):
    manifest = snapshot_manifest("general-chat")
    business = {
        name: AsyncMock(return_value=result)
        for name, result in (
            ("ensure_user", None),
            ("create_session", "ses-required-bash"),
            ("create_run", "run-required-bash"),
            ("insert_run_skill_snapshots_at_creation", None),
            ("append_message", "msg-required-bash"),
            ("bind_files_to_run", None),
            ("append_event", None),
            ("create_tool_permission_request", None),
        )
    }
    for name, mock in business.items():
        monkeypatch.setattr(repository_module, name, mock)
    authorize = AsyncMock(
        return_value={
            "executor_type": "claude-agent-worker",
            "skill_version": manifest["content_hash"],
            "input_modes": ["chat"],
        }
    )
    manifests, enqueue = AsyncMock(return_value=[manifest]), AsyncMock(return_value=1)
    monkeypatch.setattr("app.routes.chat.transaction", fake_transaction)
    monkeypatch.setattr(repository_module, "authorize_run_capabilities", authorize)
    monkeypatch.setattr("app.routes.chat._governed_skill_manifest_pins", manifests)
    monkeypatch.setattr("app.routes.chat.enqueue_run", enqueue)
    request = ChatStreamRequest(message=message)

    assert (await chat_stream(request, principal=principal())).status == "queued"
    business["create_run"].assert_awaited_once()
    business["create_tool_permission_request"].assert_not_awaited()
    assert "_required_capability_declaration" not in enqueue.await_args.args[0]["input"]


def test_submission_code_accepts_only_bounded_required_capability_detail_code():
    assert _submission_code({"detail_code": "required_capability_unavailable"}) == "required_capability_unavailable"
    assert _submission_code({"detail_code": "private_executor_detail"}) == "chat_submission_rejected"
    safe = _chat_submission_http_error(
        status_code=403, code="required_capability_unavailable"
    ).detail
    assert safe == {
        "code": "required_capability_unavailable",
        "detail_code": "required_capability_unavailable",
        "message": "任务所需执行能力当前不可用。请调整请求或联系管理员。",
        "status": "unavailable",
        "submission_disposition": "rejected_before_persist",
    }
    assert _chat_submission_http_error(status_code=403, code="private_error").detail == {
        "code": "private_error",
        "submission_disposition": "rejected_before_persist",
    }


@pytest.mark.asyncio
async def test_keyed_continuation_provisions_principal_and_claims_saved_workspace(monkeypatch):
    request = ChatStreamRequest(
        message="resume durable submission",
        session_id="session-owned",
        submission_id="7ea93033-30f5-40ea-8a33-2f3c6e7b21c4",
    )
    fingerprint = repository_module.chat_submission_fingerprint(
        {"request": request.model_dump(mode="json", exclude={"submission_id"}), "query_agent_id": None},
        tenant_id="tenant-a",
        user_id="user-a",
    )
    calls: list[object] = []

    async def no_existing_submission(*_args, **_kwargs):
        return None

    async def provision_principal(*_args, **_kwargs):
        calls.append("provision")
        return {"id": "user-a", "tenant_id": "tenant-a"}

    async def owned_session(*_args, **kwargs):
        calls.append(("session", kwargs.get("for_update", False)))
        return {
            "id": "session-owned",
            "workspace_id": "workspace-owned",
            "agent_id": "general-agent",
        }

    async def latest_input(*_args, **_kwargs):
        calls.append("latest_input")

    async def claim_submission(*_args, **kwargs):
        calls.append("claim")
        assert calls == [
            "admission_lock",
            "provision",
            ("session", False),
            ("session", True),
            "latest_input",
            "claim",
        ]
        assert kwargs["workspace_id"] == "workspace-owned"
        assert kwargs["request_fingerprint_sha256"] == fingerprint
        return (
            {
                "submission_id": str(request.submission_id),
                "request_fingerprint_sha256": fingerprint,
                "state": "queued",
                "outcome_json": {
                    "session_id": "session-owned",
                    "run_id": "run-owned",
                    "status": "queued",
                    "submission_id": str(request.submission_id),
                },
            },
            False,
        )

    async def admission_lock(*_args, **_kwargs):
        calls.append("admission_lock")

    monkeypatch.setattr("app.routes.chat.transaction", fake_transaction)
    monkeypatch.setattr(repository_module, "get_chat_submission", no_existing_submission, raising=False)
    monkeypatch.setattr(repository_module, "ensure_submission_principal", provision_principal, raising=False)
    monkeypatch.setattr(repository_module, "get_authorized_session", owned_session, raising=False)
    monkeypatch.setattr(
        repository_module,
        "get_latest_authorized_session_run_input",
        latest_input,
        raising=False,
    )
    monkeypatch.setattr(repository_module, "claim_chat_submission", claim_submission, raising=False)
    monkeypatch.setattr(
        repository_module,
        "acquire_user_active_run_admission_lock",
        admission_lock,
        raising=False,
    )

    response = await chat_stream(request, principal=principal())

    assert response.session_id == "session-owned"
    assert calls == [
        "admission_lock",
        "provision",
        ("session", False),
        ("session", True),
        "latest_input",
        "claim",
    ]


@pytest.mark.asyncio
async def test_keyed_continuation_inherits_and_reauthorizes_latest_mcp_selection_before_claim(monkeypatch):
    request = ChatStreamRequest(
        message="continue with selected tool",
        session_id="session-owned",
        submission_id="7ea93033-30f5-40ea-8a33-2f3c6e7b21c4",
        mcp_context_id="mcpctx-concurrent",
    )
    calls: list[object] = []
    discarded = []

    async def no_existing_submission(*_args, **_kwargs):
        return None

    async def provision_principal(*_args, **_kwargs):
        calls.append("provision")

    async def owned_session(*_args, **kwargs):
        for_update = kwargs.get("for_update", False)
        calls.append(("session", for_update))
        return {
            "id": "session-owned",
            "workspace_id": "workspace-owned",
            "agent_id": "general-agent",
            "latest_run_input_json": {
                "input": {
                    "mcp_tool_ids": [
                        "gateway::locked-search" if for_update else "gateway::stale-unlocked-search"
                    ]
                }
            },
        }

    async def admission_lock(*_args, **_kwargs):
        calls.append("admission_lock")

    async def latest_input(*_args, **_kwargs):
        calls.append("latest_input")
        return {"input": {"mcp_tool_ids": ["gateway::locked-search"]}}

    async def authorize_tools(*_args, **kwargs):
        calls.append(("authorize", kwargs["tool_ids"]))
        assert kwargs["tool_ids"] == ["gateway::locked-search"]
        return [{"tool_id": "gateway::locked-search"}]

    async def claim_submission(*_args, **kwargs):
        calls.append(("claim", kwargs["request_fingerprint_sha256"]))
        fingerprint_request = request.model_dump(mode="json", exclude={"submission_id"})
        fingerprint_request["selected_mcp_tool_ids"] = ["gateway::locked-search"]
        assert kwargs["request_fingerprint_sha256"] == repository_module.chat_submission_fingerprint(
            {"request": fingerprint_request, "query_agent_id": None},
            tenant_id="tenant-a",
            user_id="user-a",
        )
        return (
            {
                "request_fingerprint_sha256": kwargs["request_fingerprint_sha256"],
                "state": "queued",
                "outcome_json": {
                    "session_id": "session-owned",
                    "run_id": "run-owned",
                    "status": "queued",
                    "submission_id": str(request.submission_id),
                },
            },
            False,
        )

    async def discard_context(context_id, replay_principal):
        discarded.append((context_id, replay_principal.user_id))

    monkeypatch.setattr("app.routes.chat.transaction", fake_transaction)
    monkeypatch.setattr(repository_module, "get_chat_submission", no_existing_submission)
    monkeypatch.setattr(repository_module, "ensure_submission_principal", provision_principal)
    monkeypatch.setattr(repository_module, "get_authorized_session", owned_session)
    monkeypatch.setattr(
        repository_module,
        "get_latest_authorized_session_run_input",
        latest_input,
    )
    monkeypatch.setattr(
        repository_module,
        "acquire_user_active_run_admission_lock",
        admission_lock,
        raising=False,
    )
    monkeypatch.setattr(
        repository_module,
        "authorize_selected_chat_mcp_tools",
        authorize_tools,
    )
    monkeypatch.setattr(repository_module, "claim_chat_submission", claim_submission)
    monkeypatch.setattr(
        "app.routes.chat.mcp.discard_unbound_mcp_runtime_context",
        discard_context,
    )

    response = await chat_stream(request, principal=principal())

    assert response.run_id == "run-owned"
    assert calls[:6] == [
        "admission_lock",
        "provision",
        ("session", False),
        ("session", True),
        "latest_input",
        ("authorize", ["gateway::locked-search"]),
    ]
    assert calls[6][0] == "claim"
    assert discarded == [("mcpctx-concurrent", "user-a")]


@pytest.mark.asyncio
async def test_keyed_rejection_provisions_principal_before_saved_workspace_ledger(monkeypatch):
    request = ChatStreamRequest(
        message="reject before mutation",
        session_id="session-owned",
        skill_id="raw-skill-forbidden-to-user",
        submission_id="7ea93033-30f5-40ea-8a33-2f3c6e7b21c4",
    )
    calls: list[str] = []

    async def provision_principal(*_args, **_kwargs):
        calls.append("provision")
        return {"id": "user-a", "tenant_id": "tenant-a"}

    async def owned_session(*_args, **_kwargs):
        calls.append("session")
        return {
            "id": "session-owned",
            "workspace_id": "workspace-owned",
            "agent_id": "general-agent",
        }

    fingerprints = []

    async def claim_submission(*_args, **kwargs):
        calls.append("claim")
        assert calls == ["provision", "session", "claim"]
        assert kwargs["workspace_id"] == "workspace-owned"
        fingerprints.append(kwargs["request_fingerprint_sha256"])
        return ({"state": "resolving"}, True)

    async def finalize_submission(*_args, **kwargs):
        calls.append("finalize")
        assert kwargs["workspace_id"] == "workspace-owned"
        assert kwargs["state"] == "rejected_before_persist"

    monkeypatch.setattr("app.routes.chat.transaction", fake_transaction)
    monkeypatch.setattr(repository_module, "ensure_submission_principal", provision_principal, raising=False)
    monkeypatch.setattr(repository_module, "get_authorized_session", owned_session, raising=False)
    monkeypatch.setattr(repository_module, "claim_chat_submission", claim_submission, raising=False)
    monkeypatch.setattr(repository_module, "finalize_chat_submission", finalize_submission, raising=False)

    with pytest.raises(HTTPException) as exc_info:
        await chat_stream(request, principal=principal())

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail["code"] == "raw_skill_selector_forbidden"
    assert calls == ["provision", "session", "claim", "finalize"]
    assert len(fingerprints[0]) == 64
    assert "raw-skill-forbidden-to-user" not in fingerprints[0]


@pytest.mark.asyncio
async def test_keyed_legacy_mcp_rejection_is_durable_replay_safe_and_payload_mismatch_safe(monkeypatch):
    submission_id = "7ea93033-30f5-40ea-8a33-2f3c6e7b21c4"
    ledger: dict[str, object] = {}
    claims: list[str] = []
    finalizations = []

    async def provision_principal(*_args, **_kwargs):
        return {"id": "user-a", "tenant_id": "tenant-a"}

    async def claim_submission(*_args, **kwargs):
        fingerprint = kwargs["request_fingerprint_sha256"]
        claims.append(fingerprint)
        if ledger:
            return dict(ledger), False
        ledger.update(
            state="resolving",
            request_fingerprint_sha256=fingerprint,
            workspace_id=kwargs["workspace_id"],
        )
        return dict(ledger), True

    async def finalize_submission(*_args, **kwargs):
        finalizations.append(dict(kwargs))
        ledger.update(
            state=kwargs["state"],
            submission_disposition=kwargs["submission_disposition"],
            rejection_code=kwargs["rejection_code"],
        )

    monkeypatch.setattr("app.routes.chat.transaction", fake_transaction)
    monkeypatch.setattr(repository_module, "ensure_submission_principal", provision_principal)
    monkeypatch.setattr(repository_module, "claim_chat_submission", claim_submission)
    monkeypatch.setattr(repository_module, "finalize_chat_submission", finalize_submission)

    def rejected_request(message: str) -> ChatStreamRequest:
        return ChatStreamRequest(
            message=message,
            submission_id=submission_id,
            input={
                "mcp_tool_ids": ["unauthorized-private-tool"],
                "endpoint": "https://private.example.test/mcp",
                "credential": "private-credential",
            },
        )

    with pytest.raises(HTTPException) as first_info:
        await chat_stream(rejected_request("same rejected payload"), principal=principal())
    assert first_info.value.status_code == 400
    assert first_info.value.detail["code"] == "selected_mcp_tool_ids_required"

    with pytest.raises(HTTPException) as replay_info:
        await chat_stream(rejected_request("same rejected payload"), principal=principal())
    assert replay_info.value.status_code == 409
    assert replay_info.value.detail["code"] == "selected_mcp_tool_ids_required"

    with pytest.raises(HTTPException) as mismatch_info:
        await chat_stream(rejected_request("different rejected payload"), principal=principal())

    assert mismatch_info.value.status_code == 409
    assert mismatch_info.value.detail == "submission_payload_mismatch"
    assert len(set(claims[:2])) == 1
    assert claims[2] != claims[0]
    assert len(finalizations) == 1
    serialized_ledger = json.dumps(ledger, sort_keys=True)
    assert "unauthorized-private-tool" not in serialized_ledger
    assert "private.example.test" not in serialized_ledger
    assert "private-credential" not in serialized_ledger


@pytest.mark.asyncio
async def test_chat_submission_resolver_missing_is_read_only_and_fail_closed(monkeypatch):
    calls = []

    async def missing_submission(conn, **kwargs):
        calls.append(kwargs)

    monkeypatch.setattr("app.routes.chat.transaction", fake_transaction)
    monkeypatch.setattr(repository_module, "get_chat_submission", missing_submission, raising=False)
    monkeypatch.setattr(
        repository_module,
        "claim_chat_submission",
        lambda *_args, **_kwargs: pytest.fail("GET must not claim a tombstone"),
        raising=False,
    )
    response = Response()

    with pytest.raises(HTTPException) as exc_info:
        await get_chat_submission(
            "7ea93033-30f5-40ea-8a33-2f3c6e7b21c4",
            response=response,
            principal=principal(tenant_id="tenant-b", user_id="user-b"),
        )

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "chat_submission_not_found"
    assert exc_info.value.headers == {"Cache-Control": "private, no-store"}
    assert response.headers["Cache-Control"] == "private, no-store"
    assert calls == [
        {
            "tenant_id": "tenant-b",
            "user_id": "user-b",
            "submission_id": "7ea93033-30f5-40ea-8a33-2f3c6e7b21c4",
        }
    ]


@pytest.mark.asyncio
async def test_retry_admission_returns_versioned_absence_before_attempting_admission(monkeypatch):
    calls: list[tuple[str, dict[str, object]]] = []

    async def ensure_principal(_conn, **kwargs):
        calls.append(("ensure", kwargs))

    async def claim_tombstone(_conn, **kwargs):
        calls.append(("claim", kwargs))
        return {"submission_id": kwargs["submission_id"], "state": "resolving"}, True

    async def finalize_tombstone(_conn, **kwargs):
        calls.append(("finalize", kwargs))

    async def forbidden_admission(*_args, **_kwargs):
        raise AssertionError("a new tombstone must not attempt queue admission")

    monkeypatch.setattr("app.routes.chat.transaction", fake_transaction)
    monkeypatch.setattr(repository_module, "ensure_submission_principal", ensure_principal, raising=False)
    monkeypatch.setattr(repository_module, "claim_chat_submission", claim_tombstone, raising=False)
    monkeypatch.setattr(repository_module, "finalize_chat_submission", finalize_tombstone, raising=False)
    monkeypatch.setattr("app.routes.chat._admit_chat_submission", forbidden_admission)
    response_headers = Response()

    response = await retry_chat_submission_admission(
        "7ea93033-30f5-40ea-8a33-2f3c6e7b21c4",
        response=response_headers,
        principal=principal(tenant_id="tenant-b", user_id="user-b"),
    )

    assert response.protocol_version == "chat_submission_resolution.v2"
    assert response.state == "absent_before_ledger"
    assert response_headers.headers["Cache-Control"] == "private, no-store"
    assert [name for name, _kwargs in calls] == ["ensure", "claim", "finalize"]
    assert calls[1][1]["workspace_id"] is None
    assert calls[2][1]["state"] == "rejected_before_persist"
    assert calls[2][1]["submission_disposition"] == "rejected_before_persist"
    assert calls[2][1]["rejection_code"] == "chat_submission_retired_before_ledger"


@pytest.mark.asyncio
async def test_chat_submission_get_resolves_a_durable_recovery_tombstone(monkeypatch):
    request_principal = principal(tenant_id="tenant-b", user_id="user-b")
    submission_id = "7ea93033-30f5-40ea-8a33-2f3c6e7b21c4"

    async def durable_tombstone(_conn, **_kwargs):
        return {
            "submission_id": submission_id,
            "state": "rejected_before_persist",
            "submission_disposition": "rejected_before_persist",
            "rejection_code": "chat_submission_retired_before_ledger",
            "request_fingerprint_sha256": _preledger_recovery_fingerprint(request_principal),
            "outcome_json": {},
        }

    monkeypatch.setattr("app.routes.chat.transaction", fake_transaction)
    monkeypatch.setattr(repository_module, "get_chat_submission", durable_tombstone, raising=False)
    response_headers = Response()

    response = await get_chat_submission(
        submission_id,
        response=response_headers,
        principal=request_principal,
    )

    assert response.state == "rejected_before_persist"
    assert response.submission_disposition == "rejected_before_persist"
    assert response.rejection_code == "chat_submission_retired_before_ledger"
    assert response_headers.headers["Cache-Control"] == "private, no-store"


@pytest.mark.asyncio
async def test_retry_admission_preserves_existing_submission_admission(monkeypatch):
    submission_id = "7ea93033-30f5-40ea-8a33-2f3c6e7b21c4"
    admitted: list[str] = []

    async def ensure_principal(*_args, **_kwargs):
        return None

    async def existing_submission(_conn, **_kwargs):
        return {
            "submission_id": submission_id,
            "state": "accepted_pending_enqueue",
            "request_fingerprint_sha256": "a" * 64,
            "outcome_json": _pending_submission_row()["outcome_json"],
        }, False

    async def admit(*, principal: AuthPrincipal, submission_id: str):
        assert principal.user_id == "user-a"
        admitted.append(submission_id)
        return ChatSubmissionResponse(
            submission_id=submission_id,
            state="accepted_pending_enqueue",
            outcome=_pending_submission_row()["outcome_json"],
        )

    monkeypatch.setattr("app.routes.chat.transaction", fake_transaction)
    monkeypatch.setattr(repository_module, "ensure_submission_principal", ensure_principal, raising=False)
    monkeypatch.setattr(repository_module, "claim_chat_submission", existing_submission, raising=False)
    monkeypatch.setattr("app.routes.chat._admit_chat_submission", admit)

    response = await retry_chat_submission_admission(
        submission_id,
        response=Response(),
        principal=principal(),
    )

    assert response.state == "accepted_pending_enqueue"
    assert admitted == [submission_id]


@pytest.mark.asyncio
async def test_retry_admission_error_keeps_resolution_response_private_and_uncached(monkeypatch):
    submission_id = "7ea93033-30f5-40ea-8a33-2f3c6e7b21c4"

    async def ensure_principal(*_args, **_kwargs):
        return None

    async def existing_submission(_conn, **_kwargs):
        return {
            "submission_id": submission_id,
            "state": "accepted_pending_enqueue",
            "request_fingerprint_sha256": "a" * 64,
            "outcome_json": _pending_submission_row()["outcome_json"],
        }, False

    async def missing_admission(*_args, **_kwargs):
        raise HTTPException(status_code=404, detail="chat_submission_not_found")

    monkeypatch.setattr("app.routes.chat.transaction", fake_transaction)
    monkeypatch.setattr(repository_module, "ensure_submission_principal", ensure_principal, raising=False)
    monkeypatch.setattr(repository_module, "claim_chat_submission", existing_submission, raising=False)
    monkeypatch.setattr("app.routes.chat._admit_chat_submission", missing_admission)

    with pytest.raises(HTTPException) as exc_info:
        await retry_chat_submission_admission(
            submission_id,
            response=Response(),
            principal=principal(),
        )

    assert exc_info.value.status_code == 404
    assert exc_info.value.headers == {"Cache-Control": "private, no-store"}


@pytest.mark.asyncio
async def test_late_chat_post_is_rejected_after_recovery_tombstone_wins(monkeypatch):
    request = ChatStreamRequest(
        message="late original request",
        submission_id="7ea93033-30f5-40ea-8a33-2f3c6e7b21c4",
    )
    request_principal = principal()
    tombstone = {
        "submission_id": str(request.submission_id),
        "state": "rejected_before_persist",
        "submission_disposition": "rejected_before_persist",
        "rejection_code": "chat_submission_retired_before_ledger",
        "request_fingerprint_sha256": _preledger_recovery_fingerprint(request_principal),
        "outcome_json": {},
    }
    claim_calls = 0

    async def initially_missing(*_args, **_kwargs):
        return None

    async def ensure_principal(*_args, **_kwargs):
        return None

    async def tombstone_wins(_conn, **_kwargs):
        nonlocal claim_calls
        claim_calls += 1
        return tombstone, False

    def forbidden_route(*_args, **_kwargs):
        raise AssertionError("a tombstone must prevent intent routing and run creation")

    monkeypatch.setattr("app.routes.chat.transaction", fake_transaction)
    monkeypatch.setattr(repository_module, "get_chat_submission", initially_missing, raising=False)
    monkeypatch.setattr(repository_module, "ensure_submission_principal", ensure_principal, raising=False)
    monkeypatch.setattr(repository_module, "claim_chat_submission", tombstone_wins, raising=False)
    monkeypatch.setattr("app.routes.chat.route_intent", forbidden_route)

    with pytest.raises(HTTPException) as exc_info:
        await chat_stream(request, principal=request_principal)

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == {
        "code": "chat_submission_retired_before_ledger",
        "submission_disposition": "rejected_before_persist",
    }
    # Validation persistence rechecks the already-retired UUID after the
    # deterministic rejection; neither claim may route or create a run.
    assert claim_calls == 2


def _pending_submission_row(*, state: str = "accepted_pending_enqueue") -> dict[str, object]:
    return {
        "submission_id": "7ea93033-30f5-40ea-8a33-2f3c6e7b21c4",
        "state": state,
        "run_id": "run-durable",
        "outcome_json": {
            "session_id": "ses-durable",
            "run_id": "run-durable",
            "status": "accepted_pending_enqueue",
            "submission_id": "7ea93033-30f5-40ea-8a33-2f3c6e7b21c4",
        },
    }


def _durable_run_row(*, status: str = "queued") -> dict[str, object]:
    return {
        "id": "run-durable",
        "status": status,
        "workspace_id": "default",
        "session_id": "ses-durable",
        "agent_id": "general-agent",
        "skill_id": "general-chat",
        "input_json": {},
    }


@pytest.mark.asyncio
async def test_profile_retry_admission_uses_fresh_authority_transaction_for_committed_run(monkeypatch):
    submission = _pending_submission_row()
    run = _durable_run_row()
    run.update(
        {
            "agent_id": "agt_support",
            "skill_id": "profile-specialist",
            "admitted_agent_profile_revision": 7,
            "admitted_agent_profile_hash": "a" * 64,
            "input_json": {
                "input": {"message": "retry profile run", "mcp_tool_ids": []},
                "executor_type": "claude-agent-worker",
                "skill_version": "version-profile",
                "skill_manifests": [],
                "model_id": "model-a",
                "model_value": "provider-model-a",
                "agent_profile": {
                    "agent_id": "agt_support",
                    "revision": 7,
                    "content_hash": "a" * 64,
                    "instructions": "private",
                },
            },
        }
    )
    transaction_depth = 0
    calls: list[str] = []

    @asynccontextmanager
    async def tracked_transaction():
        nonlocal transaction_depth
        transaction_depth += 1
        try:
            yield object()
        finally:
            transaction_depth -= 1

    async def get_submission(*_args, **_kwargs):
        return submission

    async def get_run(*_args, **_kwargs):
        return run

    async def reauthorize(*_args, **_kwargs):
        assert transaction_depth == 1
        calls.append("profile_authority")

    async def no_existing(_payload):
        calls.append("read")
        return None

    async def enqueue(_payload):
        assert transaction_depth == 1
        calls.append("enqueue")
        return QueueAdmissionMetadata(1, 3, "profile-message")

    async def append_event(*_args, **_kwargs):
        calls.append("event")

    async def finalize(*_args, **kwargs):
        submission["state"] = kwargs["state"]
        submission["outcome_json"] = kwargs["outcome_json"]
        calls.append("finalize")

    monkeypatch.setattr("app.routes.chat.transaction", tracked_transaction)
    monkeypatch.setattr(repository_module, "get_chat_submission", get_submission)
    monkeypatch.setattr(repository_module, "get_authorized_run", get_run)
    monkeypatch.setattr("app.routes.chat._validate_queue_payload_for_enqueue", lambda payload: payload)
    monkeypatch.setattr("app.routes.chat.reauthorize_pinned_run_for_replay", reauthorize)
    monkeypatch.setattr("app.routes.chat.read_queue_admission", no_existing)
    monkeypatch.setattr("app.routes.chat._enqueue_chat_run", enqueue)
    monkeypatch.setattr(repository_module, "append_event", append_event)
    monkeypatch.setattr(repository_module, "finalize_chat_submission", finalize)

    response = await _admit_chat_submission(
        principal=principal(),
        submission_id="7ea93033-30f5-40ea-8a33-2f3c6e7b21c4",
    )

    assert response.state == "queued"
    assert calls == ["profile_authority", "read", "enqueue", "event", "finalize"]


@pytest.mark.asyncio
async def test_profile_postcommit_lost_ack_is_recoverable_and_duplicate_retry_does_not_reenqueue(
    monkeypatch,
):
    submission = _pending_submission_row()
    run = _durable_run_row()
    run.update(
        {
            "agent_id": "agt_support",
            "skill_id": "profile-specialist",
            "admitted_agent_profile_revision": 7,
            "admitted_agent_profile_hash": "a" * 64,
            "input_json": {
                "input": {"message": "retry profile run", "mcp_tool_ids": []},
                "executor_type": "claude-agent-worker",
                "skill_version": "version-profile",
                "skill_manifests": [],
                "model_id": "model-a",
                "model_value": "provider-model-a",
                "agent_profile": {
                    "agent_id": "agt_support",
                    "revision": 7,
                    "content_hash": "a" * 64,
                    "instructions": "private",
                },
            },
        }
    )
    transaction_depth = 0
    committed_transactions = 0
    enqueue_calls = 0
    queue_admission: QueueAdmissionMetadata | None = None
    readback_available = False
    enqueued_payloads: list[dict[str, object]] = []
    read_payloads: list[dict[str, object]] = []
    queued_events = 0

    @asynccontextmanager
    async def tracked_transaction():
        nonlocal committed_transactions, transaction_depth
        transaction_depth += 1
        try:
            yield object()
        except BaseException:
            raise
        else:
            committed_transactions += 1
        finally:
            transaction_depth -= 1

    async def get_submission(*_args, **_kwargs):
        return submission

    async def get_run(*_args, **_kwargs):
        return run

    async def reauthorize(*_args, **_kwargs):
        assert transaction_depth == 1

    async def read_admission(payload):
        read_payloads.append(dict(payload))
        return queue_admission if readback_available else None

    async def enqueue(payload):
        nonlocal enqueue_calls, queue_admission
        assert transaction_depth == 1
        enqueue_calls += 1
        enqueued_payloads.append(dict(payload))
        queue_admission = QueueAdmissionMetadata(1, 11, "stable-profile-message")
        raise RuntimeError("redis acknowledgement unavailable")

    async def append_event(*_args, **_kwargs):
        nonlocal queued_events
        queued_events += 1

    async def finalize(*_args, **kwargs):
        submission["state"] = kwargs["state"]
        if kwargs.get("outcome_json") is not None:
            submission["outcome_json"] = kwargs["outcome_json"]

    async def forbidden_failure_transition(*_args, **_kwargs):
        raise AssertionError("an ambiguous post-commit publish must remain recoverable")

    monkeypatch.setattr("app.routes.chat.transaction", tracked_transaction)
    monkeypatch.setattr(repository_module, "get_chat_submission", get_submission)
    monkeypatch.setattr(repository_module, "get_authorized_run", get_run)
    monkeypatch.setattr("app.routes.chat._validate_queue_payload_for_enqueue", lambda payload: payload)
    monkeypatch.setattr("app.routes.chat.reauthorize_pinned_run_for_replay", reauthorize)
    monkeypatch.setattr("app.routes.chat.read_queue_admission", read_admission)
    monkeypatch.setattr("app.routes.chat._enqueue_chat_run", enqueue)
    monkeypatch.setattr(repository_module, "append_event", append_event)
    monkeypatch.setattr(repository_module, "finalize_chat_submission", finalize)
    monkeypatch.setattr(repository_module, "mark_run_enqueue_failed", forbidden_failure_transition)

    first = await _admit_chat_submission(
        principal=principal(),
        submission_id="7ea93033-30f5-40ea-8a33-2f3c6e7b21c4",
    )
    assert first.state == "accepted_pending_enqueue"
    assert enqueue_calls == 1
    assert committed_transactions == 1

    readback_available = True
    recovered = await _admit_chat_submission(
        principal=principal(),
        submission_id="7ea93033-30f5-40ea-8a33-2f3c6e7b21c4",
    )
    duplicate = await _admit_chat_submission(
        principal=principal(),
        submission_id="7ea93033-30f5-40ea-8a33-2f3c6e7b21c4",
    )

    assert recovered.state == "queued"
    assert duplicate.state == "queued"
    assert enqueue_calls == 1
    assert queued_events == 1
    assert enqueued_payloads
    assert read_payloads
    assert all(payload == enqueued_payloads[0] for payload in read_payloads)


@pytest.mark.asyncio
async def test_retry_admission_marks_committed_submission_enqueue_failed_only_for_definitive_rejection(monkeypatch):
    submission = _pending_submission_row()
    finalized: list[dict[str, object]] = []

    async def get_submission(*_args, **_kwargs):
        return submission

    async def get_run(*_args, **_kwargs):
        return _durable_run_row()

    async def fail_enqueue(_payload):
        raise QueueAdmissionRejected("queue_payload_invalid")

    async def no_existing_admission(_payload):
        return None

    async def finalize(*_args, **kwargs):
        finalized.append(kwargs)

    async def mark_enqueue_failed(*_args, **kwargs):
        assert kwargs["run_id"] == "run-durable"
        return RunTerminalizationProgress(
            completed=True,
            status="failed",
            did_transition=True,
        )

    monkeypatch.setattr("app.routes.chat.transaction", fake_transaction)
    monkeypatch.setattr(repository_module, "get_chat_submission", get_submission, raising=False)
    monkeypatch.setattr(repository_module, "get_authorized_run", get_run, raising=False)
    monkeypatch.setattr("app.routes.chat._validate_queue_payload_for_enqueue", lambda payload: payload)
    monkeypatch.setattr("app.routes.chat._enqueue_chat_run", fail_enqueue)
    monkeypatch.setattr("app.routes.chat.read_queue_admission", no_existing_admission)
    monkeypatch.setattr(repository_module, "finalize_chat_submission", finalize, raising=False)
    monkeypatch.setattr(repository_module, "mark_run_enqueue_failed", mark_enqueue_failed, raising=False)

    with pytest.raises(HTTPException) as exc_info:
        await _admit_chat_submission(
            principal=principal(),
            submission_id="7ea93033-30f5-40ea-8a33-2f3c6e7b21c4",
        )

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == "queue_enqueue_failed"
    assert finalized == [{
        "tenant_id": "tenant-a",
        "user_id": "user-a",
        "submission_id": "7ea93033-30f5-40ea-8a33-2f3c6e7b21c4",
        "state": "enqueue_failed",
        "rejection_code": "queue_enqueue_failed",
    }]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "enqueue_error",
    [
        RuntimeError("redis connection dropped after write"),
        QueueAdmissionRejected("run_reconciliation_in_progress"),
    ],
    ids=["ambiguous-redis-write", "transient-reconciliation-fence"],
)
async def test_retry_admission_keeps_unknown_enqueue_outcome_recoverable_without_failure_transition(
    monkeypatch,
    enqueue_error,
):
    submission = _pending_submission_row()
    enqueue_calls: list[dict[str, object]] = []

    async def get_submission(*_args, **_kwargs):
        return submission

    async def get_run(*_args, **_kwargs):
        return _durable_run_row()

    async def enqueue(payload):
        enqueue_calls.append(payload)
        raise enqueue_error

    async def no_exact_admission(_payload):
        return None

    async def forbidden_failure_transition(*_args, **_kwargs):
        raise AssertionError("an unknown enqueue result must not terminalize the run")

    monkeypatch.setattr("app.routes.chat.transaction", fake_transaction)
    monkeypatch.setattr(repository_module, "get_chat_submission", get_submission, raising=False)
    monkeypatch.setattr(repository_module, "get_authorized_run", get_run, raising=False)
    monkeypatch.setattr("app.routes.chat._validate_queue_payload_for_enqueue", lambda payload: payload)
    monkeypatch.setattr("app.routes.chat._enqueue_chat_run", enqueue)
    monkeypatch.setattr("app.routes.chat.read_queue_admission", no_exact_admission)
    monkeypatch.setattr(repository_module, "mark_run_enqueue_failed", forbidden_failure_transition, raising=False)
    monkeypatch.setattr(repository_module, "finalize_chat_submission", forbidden_failure_transition, raising=False)

    response = await _admit_chat_submission(
        principal=principal(),
        submission_id="7ea93033-30f5-40ea-8a33-2f3c6e7b21c4",
    )

    assert response.state == "accepted_pending_enqueue"
    assert len(enqueue_calls) == 1
    assert submission["state"] == "accepted_pending_enqueue"


@pytest.mark.asyncio
async def test_retry_admission_reconciles_concurrent_redis_success_without_terminalizing(monkeypatch):
    submission = _pending_submission_row()
    calls: list[str] = []

    async def get_submission(*_args, **_kwargs):
        return submission

    async def get_run(*_args, **_kwargs):
        return _durable_run_row()

    async def enqueue(_payload):
        calls.append("enqueue")
        raise RuntimeError("caller lost enqueue acknowledgement")

    async def read_admission(_payload):
        calls.append("read")
        if calls.count("read") == 1:
            return None
        submission["state"] = "queued"
        submission["outcome_json"] = {**submission["outcome_json"], "status": "queued"}
        return QueueAdmissionMetadata(1, 9, "stable-message-id", "redis_readback_queued")

    async def forbidden_failure_transition(*_args, **_kwargs):
        raise AssertionError("concurrent success must not be replaced by enqueue_failed")

    monkeypatch.setattr("app.routes.chat.transaction", fake_transaction)
    monkeypatch.setattr(repository_module, "get_chat_submission", get_submission, raising=False)
    monkeypatch.setattr(repository_module, "get_authorized_run", get_run, raising=False)
    monkeypatch.setattr("app.routes.chat._validate_queue_payload_for_enqueue", lambda payload: payload)
    monkeypatch.setattr("app.routes.chat._enqueue_chat_run", enqueue)
    monkeypatch.setattr("app.routes.chat.read_queue_admission", read_admission)
    monkeypatch.setattr(repository_module, "mark_run_enqueue_failed", forbidden_failure_transition, raising=False)
    monkeypatch.setattr(repository_module, "finalize_chat_submission", forbidden_failure_transition, raising=False)

    response = await _admit_chat_submission(
        principal=principal(),
        submission_id="7ea93033-30f5-40ea-8a33-2f3c6e7b21c4",
    )

    assert response.state == "queued"
    assert calls == ["read", "enqueue", "read"]


@pytest.mark.asyncio
async def test_retry_admission_commits_enqueue_compensation_before_503_escapes(monkeypatch):
    """The queue failure must not roll back its terminal submission transition."""

    submission = _pending_submission_row()
    committed: list[tuple[str, object]] = []
    transaction_outcomes: list[tuple[str, list[tuple[str, object]]]] = []
    invalidated: list[str] = []

    class TransactionState:
        def __init__(self) -> None:
            self.pending: list[tuple[str, object]] = []

    @asynccontextmanager
    async def transaction_with_rollback_tracking():
        state = TransactionState()
        try:
            yield state
        except BaseException:
            transaction_outcomes.append(("rollback", list(state.pending)))
            raise
        else:
            committed.extend(state.pending)
            transaction_outcomes.append(("commit", list(state.pending)))

    async def get_submission(*_args, **_kwargs):
        return submission

    async def get_run(*_args, **_kwargs):
        return {
            **_durable_run_row(),
            "mcp_context_id": "mcpctx-durable",
        }

    async def fail_enqueue(_payload):
        raise QueueAdmissionRejected("queue_payload_invalid")

    async def no_existing_admission(_payload):
        return None

    async def mark_enqueue_failed(conn, **kwargs):
        conn.pending.append(("run", kwargs["run_id"]))
        return RunTerminalizationProgress(
            completed=True,
            status="failed",
            did_transition=True,
        )

    async def finalize(conn, **kwargs):
        conn.pending.append(("submission", kwargs["state"]))

    async def invalidate(context_id):
        assert committed == [
            ("run", "run-durable"),
            ("submission", "enqueue_failed"),
        ]
        invalidated.append(context_id)

    monkeypatch.setattr("app.routes.chat.transaction", transaction_with_rollback_tracking)
    monkeypatch.setattr(repository_module, "get_chat_submission", get_submission, raising=False)
    monkeypatch.setattr(repository_module, "get_authorized_run", get_run, raising=False)
    monkeypatch.setattr("app.routes.chat._validate_queue_payload_for_enqueue", lambda payload: payload)
    monkeypatch.setattr("app.routes.chat._enqueue_chat_run", fail_enqueue)
    monkeypatch.setattr("app.routes.chat.read_queue_admission", no_existing_admission)
    monkeypatch.setattr(repository_module, "mark_run_enqueue_failed", mark_enqueue_failed, raising=False)
    monkeypatch.setattr(repository_module, "finalize_chat_submission", finalize, raising=False)
    monkeypatch.setattr("app.routes.chat.mcp.invalidate_mcp_runtime_context", invalidate)

    with pytest.raises(HTTPException) as exc_info:
        await _admit_chat_submission(
            principal=principal(),
            submission_id="7ea93033-30f5-40ea-8a33-2f3c6e7b21c4",
        )

    assert exc_info.value.status_code == 503
    assert committed == [("run", "run-durable"), ("submission", "enqueue_failed")]
    assert transaction_outcomes == [
        ("commit", []),
        ("commit", [("run", "run-durable"), ("submission", "enqueue_failed")]),
    ]
    assert invalidated == ["mcpctx-durable"]


@pytest.mark.asyncio
async def test_retry_admission_does_not_requeue_a_processing_run(monkeypatch):
    submission = _pending_submission_row()
    finalized: list[dict[str, object]] = []

    async def get_submission(*_args, **_kwargs):
        return submission

    async def get_run(*_args, **_kwargs):
        return _durable_run_row(status="processing")

    async def forbidden_enqueue(_payload):
        raise AssertionError("a processing run must never be re-enqueued")

    async def finalize(*_args, **kwargs):
        finalized.append(kwargs)

    monkeypatch.setattr("app.routes.chat.transaction", fake_transaction)
    monkeypatch.setattr(repository_module, "get_chat_submission", get_submission, raising=False)
    monkeypatch.setattr(repository_module, "get_authorized_run", get_run, raising=False)
    monkeypatch.setattr("app.routes.chat._enqueue_chat_run", forbidden_enqueue)
    monkeypatch.setattr(repository_module, "finalize_chat_submission", finalize, raising=False)

    response = await _admit_chat_submission(
        principal=principal(),
        submission_id="7ea93033-30f5-40ea-8a33-2f3c6e7b21c4",
    )

    assert response.state == "queued"
    assert finalized[0]["state"] == "queued"


@pytest.mark.asyncio
async def test_retry_admission_reuses_queue_identity_after_a_ledger_update_loss(monkeypatch):
    submission = _pending_submission_row()
    enqueued_payloads: list[dict[str, object]] = []
    finalize_attempts = 0

    async def get_submission(*_args, **_kwargs):
        return submission

    async def get_run(*_args, **_kwargs):
        return _durable_run_row()

    async def enqueue(payload):
        enqueued_payloads.append(payload)
        return QueueAdmissionMetadata(1, 7, "stable-message-id")

    async def read_admission(_payload):
        return QueueAdmissionMetadata(1, 7, "stable-message-id") if enqueued_payloads else None

    async def append_event(*_args, **_kwargs):
        return None

    async def finalize(*_args, **kwargs):
        nonlocal finalize_attempts
        finalize_attempts += 1
        if finalize_attempts == 1:
            raise RuntimeError("response/ledger update lost after Redis success")
        submission["state"] = "queued"
        submission["outcome_json"] = kwargs["outcome_json"]

    monkeypatch.setattr("app.routes.chat.transaction", fake_transaction)
    monkeypatch.setattr(repository_module, "get_chat_submission", get_submission, raising=False)
    monkeypatch.setattr(repository_module, "get_authorized_run", get_run, raising=False)
    monkeypatch.setattr("app.routes.chat._validate_queue_payload_for_enqueue", lambda payload: payload)
    monkeypatch.setattr("app.routes.chat._enqueue_chat_run", enqueue)
    monkeypatch.setattr("app.routes.chat.read_queue_admission", read_admission)
    monkeypatch.setattr(repository_module, "append_event", append_event, raising=False)
    monkeypatch.setattr(repository_module, "finalize_chat_submission", finalize, raising=False)

    with pytest.raises(RuntimeError, match="ledger update lost"):
        await _admit_chat_submission(
            principal=principal(),
            submission_id="7ea93033-30f5-40ea-8a33-2f3c6e7b21c4",
        )
    response = await _admit_chat_submission(
        principal=principal(),
        submission_id="7ea93033-30f5-40ea-8a33-2f3c6e7b21c4",
    )

    assert response.state == "queued"
    assert len(enqueued_payloads) == 1


@pytest.mark.asyncio
async def test_chat_stream_selected_skill_maps_stale_lock_to_stable_409_before_writes(monkeypatch):
    calls = []

    async def stale(*args, **kwargs):
        calls.append((kwargs["skill_id"], kwargs["expected_version"]))
        raise RepositoryConflictError("skill_selection_stale")

    async def forbidden_write(*args, **kwargs):
        raise AssertionError("stale selected Skill must not write chat state")

    monkeypatch.setattr("app.routes.chat.transaction", fake_transaction)
    monkeypatch.setattr(repository_module, "authorize_selected_run_capabilities", stale, raising=False)
    monkeypatch.setattr(repository_module, "create_run", forbidden_write)
    monkeypatch.setattr(repository_module, "append_event", forbidden_write)

    with pytest.raises(HTTPException) as exc_info:
        await chat_stream(
            ChatStreamRequest(
                message="review this",
                agent_id="general-agent",
                selected_skill={"skill_id": "department-review", "expected_version": "hash-v1"},
            ),
            principal=principal(department_id="qa", roles=["reviewer"]),
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "skill_selection_stale"
    assert calls == [("department-review", "hash-v1")]


def snapshot_manifest(skill_id, *, description="Pinned skill"):
    content = f"---\nname: {skill_id}\ndescription: {description}\n---\n\n# {skill_id}\n".encode()
    files = [
        {
            "relative_path": "SKILL.md",
            "content_base64": base64.b64encode(content).decode("ascii"),
            "size_bytes": len(content),
        }
    ]
    digest = hashlib.sha256()
    path = b"SKILL.md"
    digest.update(len(path).to_bytes(8, "big"))
    digest.update(path)
    digest.update(len(content).to_bytes(8, "big"))
    digest.update(content)
    version = digest.hexdigest()
    return {
        "skill_id": skill_id,
        "description": description,
        "version": version,
        "content_hash": version,
        "source": {"kind": "builtin", "asset_dir": skill_id, "version": version},
        "files": files,
        "dependency_ids": [],
        "allowed": True,
        "staged": False,
        "used": False,
    }


def uploaded_skill_version_row(skill_id="qa-file-reviewer", version="hash-uploaded", dependency_ids=None, dependency_manifests=None):
    if skill_id == "qa-file-reviewer" and dependency_ids is None:
        dependency_ids = ["minimax-docx"]
    if skill_id == "qa-file-reviewer" and dependency_manifests is None:
        dependency_manifests = [snapshot_manifest("minimax-docx", description="Pinned DOCX helper")]
    source = {
        "kind": "uploaded",
        "storage_key": f"tenants/tenant-a/skills/{skill_id}/versions/{version}/package.zip",
        "files": [{"relative_path": "SKILL.md", "content_base64": "c2tpbGw=", "size_bytes": 5}],
    }
    if dependency_manifests is not None:
        source["dependency_manifests"] = dependency_manifests
    return {
        "skill_id": skill_id,
        "version": version,
        "content_hash": version,
        "description": "Uploaded skill",
        "source": source,
        "dependency_ids": dependency_ids if dependency_ids is not None else [],
        "status": "active",
        "created_by": "admin-a",
        "created_at": None,
    }


def test_queue_payload_invalid_detail_is_field_level_and_redacted():
    payload = {
        "tenant_id": "frc-test-a",
        "workspace_id": "frc_test_a_default",
        "user_id": "alice",
        "session_id": "ses_123abc",
        "run_id": "run_123abc",
        "agent_id": "frc_agent_83ebaed7aa4c5f49",
        "skill_id": "general-chat",
        "file_ids": [],
        "input": {"message": "alice 并发创建运行验收，请简短回复。"},
        "executor_type": "claude-agent-worker",
        "skill_version": "0.1.0",
        "release_decision": {
            "schema_version": "ai-platform.skill-release-decision.v1",
            "policy_active": False,
            "selected_version": "0.1.0",
            "selected_track": "catalog",
        },
        "skill_manifests": [],
        "context_snapshot_id": "ctx_123abc",
        "context_snapshot": {"context_snapshot_id": "ctx_123abc"},
    }

    with pytest.raises(HTTPException) as exc_info:
        _validate_queue_payload_for_enqueue(payload)

    assert exc_info.value.status_code == 500
    detail = exc_info.value.detail
    assert detail["code"] == "queue_payload_invalid"
    assert detail["submission_disposition"] == "rejected_before_persist"
    assert detail["errors"] == [
        {
            "loc": [],
            "type": "value_error",
            "message": "Value error, release_decision_primary_manifest_missing",
        }
    ]
    serialized = str(detail)
    assert "alice" not in serialized
    assert "frc-test-a" not in serialized
    assert "frc_agent_83ebaed7aa4c5f49" not in serialized


def test_queue_payload_invalid_detail_sanitizes_validation_messages():
    class PydanticStyleError(ValueError):
        def errors(self):
            return [
                {
                    "loc": ("input", "token=loc-secret"),
                    "type": "value_error /var/lib/ai-platform/private/type.log",
                    "msg": "bad token=queue-secret-token at /var/lib/ai-platform/private/run.log",
                }
            ]

    detail = queue_payload_invalid_detail(PydanticStyleError("invalid"))

    assert detail == {
        "code": "queue_payload_invalid",
        "submission_disposition": "rejected_before_persist",
        "errors": [
            {
                "loc": ["input", "field"],
                "type": "validation_error",
                "message": "validation_error",
            }
        ],
    }
    serialized = str(detail)
    assert "queue-secret-token" not in serialized
    assert "loc-secret" not in serialized
    assert "/var/lib/ai-platform/private/run.log" not in serialized
    assert "/var/lib/ai-platform/private/type.log" not in serialized


class EmptyBuiltinRegistry:
    def __init__(self, root):
        self.root = root

    def list_builtin_skills(self):
        return []


class PolicyBuiltinRegistry:
    def __init__(self, root):
        self.root = root

    def list_builtin_skills(self):
        return [
            type("SkillRef", (), {"name": "qa-file-reviewer"})(),
            type("SkillRef", (), {"name": "minimax-docx"})(),
        ]


@pytest.fixture(autouse=True)
def default_active_run_count(monkeypatch):
    async def fake_acquire_user_active_run_admission_lock(conn, *, tenant_id, user_id):
        return None

    async def fake_enforce_user_active_run_admission_under_lock(conn, *, tenant_id, user_id, limit):
        return 0

    async def fake_enforce_user_active_run_admission(conn, *, tenant_id, user_id, limit):
        return 0

    async def fake_ensure_submission_principal(conn, *, tenant_id, user_id, display_name):
        return None

    async def fake_get_queue_insight(tenant_id, **_kwargs):
        return {
            "tenant_id": tenant_id,
            "reason": "queued_behind_existing_work",
            "depths": {"tenant_queued": 0, "tenant_processing": 0},
            "workers": {"active": 0},
            "capacity": {"available_worker_slots": None},
        }

    monkeypatch.setattr(
        "app.routes.chat.repositories.enforce_user_active_run_admission",
        fake_enforce_user_active_run_admission,
        raising=False,
    )
    monkeypatch.setattr(
        "app.routes.chat.repositories.acquire_user_active_run_admission_lock",
        fake_acquire_user_active_run_admission_lock,
        raising=False,
    )
    monkeypatch.setattr(
        "app.routes.chat.repositories.enforce_user_active_run_admission_under_lock",
        fake_enforce_user_active_run_admission_under_lock,
        raising=False,
    )
    monkeypatch.setattr(
        "app.routes.chat.repositories.ensure_submission_principal",
        fake_ensure_submission_principal,
        raising=False,
    )
    monkeypatch.setattr("app.routes.chat.get_queue_insight", fake_get_queue_insight, raising=False)


@pytest.fixture(autouse=True)
def default_context_snapshot(monkeypatch):
    async def fake_record_initial_context_snapshot(conn, **kwargs):
        return {
            "schema_version": "ai-platform.context-snapshot.v1",
            "context_snapshot_id": "ctx_chat_test",
            "source": kwargs["source"],
            "message_count": len(kwargs.get("message_ids") or []),
            "file_count": len(kwargs.get("file_ids") or []),
            "memory_record_count": 0,
        }

    monkeypatch.setattr(
        "app.routes.chat.record_initial_context_snapshot",
        fake_record_initial_context_snapshot,
        raising=False,
    )




@pytest.mark.asyncio
async def test_get_session_recovers_safe_agent_conversation_identity(monkeypatch):
    from app.routes.chat import get_session

    async def projection(_conn, *, tenant_id, user_id, session_id):
        assert (tenant_id, user_id, session_id) == ("tenant-a", "user-a", "ses_profile")
        return {
            "id": "ses_profile",
            "workspace_id": "default",
            "agent_id": "agt_support",
            "title": "Support thread",
            "admitted_agent_profile_revision": 7,
            "admitted_agent_profile_hash": "a" * 64,
            "agent_profile_name": "Support assistant",
            "agent_profile_description": "Approved support help.",
            "agent_profile_avatar_ref": "builtin:assistant",
            "agent_profile_category": "support",
        }

    monkeypatch.setattr("app.routes.chat.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.chat.repositories.get_authorized_session_projection", projection)

    response = await get_session("ses_profile", principal=principal())

    assert response.agent_conversation is not None
    assert response.agent_conversation.model_dump() == {
        "agent_id": "agt_support",
        "revision": 7,
        "name": "Support assistant",
        "description": "Approved support help.",
        "welcome_message": "",
        "starter_prompts": [],
        "capability_summary": "",
        "recommended_tasks": [],
        "supported_input_types": ["text", "file"],
        "expected_outputs": [],
        "permissions_and_data_access_notice": "",
        "published_at": None,
        "avatar_ref": "builtin:assistant",
        "avatar_seed": "agt_support",
        "category": "support",
    }


@pytest.mark.asyncio
async def test_create_chat_session_uses_platform_principal(monkeypatch):
    calls = []

    async def fake_ensure_workspace(conn, *, tenant_id, workspace_id):
        calls.append(("workspace", tenant_id, workspace_id))

    async def fake_ensure_user(conn, *, tenant_id, user_id, display_name=None):
        calls.append(("user", user_id, display_name))

    async def fake_create_session(conn, **kwargs):
        calls.append(("session", kwargs["user_id"], kwargs["agent_id"]))
        return "ses_2"

    async def fake_list_authorized_sessions(conn, *, tenant_id, user_id):
        return [
            {
                "id": "ses_2",
                "workspace_id": "default",
                "agent_id": "translate",
                "title": "Translate",
                "created_at": None,
                "updated_at": None,
            }
        ]

    monkeypatch.setattr("app.routes.chat.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.chat.repositories.ensure_workspace", fake_ensure_workspace)
    monkeypatch.setattr("app.routes.chat.repositories.ensure_user", fake_ensure_user)
    monkeypatch.setattr("app.routes.chat.repositories.create_session", fake_create_session)
    monkeypatch.setattr("app.routes.chat.repositories.list_authorized_sessions", fake_list_authorized_sessions)

    response = await create_chat_session(
        ChatSessionRequest(agent_id="translate", title="Translate"),
        principal=principal(),
    )

    assert response.session_id == "ses_2"
    assert ("user", "user-a", "User A") in calls
    assert ("session", "user-a", "translate") in calls


@pytest.mark.asyncio
async def test_create_chat_session_maps_public_agent_id_before_persisting(monkeypatch):
    calls = []

    async def fake_ensure_workspace(conn, *, tenant_id, workspace_id):
        return None

    async def fake_ensure_user(conn, *, tenant_id, user_id, display_name=None):
        return None

    async def fake_create_session(conn, **kwargs):
        calls.append(("session", kwargs["agent_id"]))
        return "ses_public_review"

    async def fake_list_authorized_sessions(conn, *, tenant_id, user_id):
        return [
            {
                "id": "ses_public_review",
                "workspace_id": "default",
                "agent_id": "qa-word-review",
                "title": "Review",
                "created_at": None,
                "updated_at": None,
            }
        ]

    monkeypatch.setattr("app.routes.chat.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.chat.repositories.ensure_workspace", fake_ensure_workspace)
    monkeypatch.setattr("app.routes.chat.repositories.ensure_user", fake_ensure_user)
    monkeypatch.setattr("app.routes.chat.repositories.create_session", fake_create_session)
    monkeypatch.setattr("app.routes.chat.repositories.list_authorized_sessions", fake_list_authorized_sessions)

    response = await create_chat_session(
        ChatSessionRequest(agent_id="document-review", title="Review"),
        principal=principal(),
    )

    assert calls == [("session", "qa-word-review")]
    assert response.agent_id == "document-review"


@pytest.mark.asyncio
async def test_list_messages_rejects_cross_user_session(monkeypatch):
    async def fake_get_authorized_session(conn, *, tenant_id, user_id, session_id):
        return None

    async def fake_list_authorized_messages(conn, **kwargs):
        raise AssertionError("messages must not be listed for unauthorized sessions")

    monkeypatch.setattr("app.routes.chat.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.chat.repositories.get_authorized_session", fake_get_authorized_session)
    monkeypatch.setattr("app.routes.chat.repositories.list_authorized_messages", fake_list_authorized_messages)

    with pytest.raises(Exception) as exc_info:
        await list_messages("ses_b", principal=principal())

    assert getattr(exc_info.value, "status_code", None) == 404
    assert getattr(exc_info.value, "detail", None) == "session_not_found"


@pytest.mark.asyncio
async def test_list_messages_redacts_raw_skill_metadata_for_ordinary_user(monkeypatch):
    async def fake_get_authorized_session(conn, *, tenant_id, user_id, session_id):
        return {"id": session_id}

    async def fake_list_authorized_messages(conn, *, tenant_id, user_id, session_id):
        return [
            {
                "id": "msg_1",
                "session_id": session_id,
                "run_id": "run_1",
                "role": "user",
                "content": "审核这个文件",
                "metadata_json": {
                    "skill_id": "qa-file-reviewer",
                    "skill_ids": ["qa-file-reviewer"],
                    "skillIds": ["qa-file-reviewer"],
                    "used_skills_source": "executor_hook",
                    "workerPath": "/home/xinlin.jiang/qa-review-queue-runtime/worker.py",
                    "runtimePrivatePayload": {"cwd": "/var/lib/ai-platform/run-a"},
                    "attachments": [
                        {
                            "file_id": "file-a",
                            "resume": {"copied_from_run_id": "run-forged"},
                            "multi_agent_dispatch": {"parent_run_id": "run-forged"},
                            "dispatch_id": "dispatch-forged",
                        }
                    ],
                    "intent": {"skill_id": "qa-file-reviewer", "selected_capability": "document_review"},
                },
                "created_at": None,
            },
            {
                "id": "msg_2",
                "session_id": session_id,
                "run_id": "run_1",
                "role": "assistant",
                "content": (
                    "Command executed: python "
                    ".claude/skills/baoyu-translate/scripts/run_translation.py "
                    "input.docx output"
                ),
                "metadata_json": {},
                "created_at": None,
            }
        ]

    monkeypatch.setattr("app.routes.chat.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.chat.repositories.get_authorized_session", fake_get_authorized_session)
    monkeypatch.setattr("app.routes.chat.repositories.list_authorized_messages", fake_list_authorized_messages)

    response = await list_messages("ses_a", principal=principal())

    metadata = response.messages[0].metadata
    assert "skill_id" not in metadata
    assert "skill_ids" not in metadata
    assert "skillIds" not in metadata
    assert "used_skills_source" not in metadata
    assert "executor_hook" not in str(metadata)
    assert "workerPath" not in str(metadata)
    assert "runtimePrivatePayload" not in str(metadata)
    assert "resume" not in str(metadata)
    assert "multi_agent_dispatch" not in str(metadata)
    assert "dispatch-forged" not in str(metadata)
    assert "/home/xinlin.jiang/qa-review-queue-runtime" not in str(metadata)
    assert "/var/lib/ai-platform" not in str(metadata)
    assert metadata["intent"]["selected_capability"] == "document_review"
    assert metadata["intent"]["capability_id"] == "document_review"
    assert response.messages[1].content == ""


@pytest.mark.asyncio
async def test_list_messages_returns_stable_bounded_cursor(monkeypatch):
    created_at = datetime(2026, 8, 9, tzinfo=timezone.utc)

    async def fake_get_authorized_session(conn, *, tenant_id, user_id, session_id):
        return {"id": session_id}

    async def fake_list_authorized_messages(conn, **kwargs):
        assert kwargs["cursor"] is None
        assert kwargs["limit"] == 3
        return [
            {
                "id": f"msg_{index}",
                "session_id": "ses_a",
                "run_id": None,
                "role": "user",
                "content": str(index),
                "metadata_json": {},
                "created_at": created_at,
            }
            for index in (1, 2, 3)
        ]

    monkeypatch.setattr("app.routes.chat.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.chat.repositories.get_authorized_session", fake_get_authorized_session)
    monkeypatch.setattr("app.routes.chat.repositories.list_authorized_messages", fake_list_authorized_messages)

    response = await list_messages("ses_a", cursor=None, limit=2, principal=principal())

    assert [message.message_id for message in response.messages] == ["msg_1", "msg_2"]
    assert response.next_cursor is not None
    padding = "=" * (-len(response.next_cursor) % 4)
    payload = json.loads(base64.urlsafe_b64decode(f"{response.next_cursor}{padding}"))
    assert payload == {
        "created_at": created_at.isoformat(),
        "message_id": "msg_2",
        "session_id": "ses_a",
        "v": 1,
    }
@pytest.mark.asyncio
async def test_chat_stream_capability_distribution_creates_run_with_auth_snapshot(monkeypatch):
    calls = []

    def fake_resolve_model_selection(model_id, _settings, **kwargs):
        assert model_id == "deepseek-v4-pro"
        return {"id": model_id, "value": model_id}

    async def fake_resolve_agent_skill(conn, *, tenant_id, agent_id, skill_id):
        return {
            "executor_type": "claude-agent-worker",
            "skill_version": "0.1.0",
            "skill_display_label": "internal-comms",
            "input_modes": ["docx"],
        }

    async def fake_authorize_selected(conn, **kwargs):
        assert kwargs["skill_id"] == "qa-file-reviewer"
        assert kwargs["expected_version"] == "0.1.0"
        return await fake_resolve_agent_skill(
            conn,
            tenant_id=kwargs["tenant_id"],
            agent_id=kwargs["agent_id"],
            skill_id=kwargs["skill_id"],
        )

    async def fake_ensure_user(conn, **kwargs):
        calls.append(("user", kwargs["user_id"]))

    async def fake_create_session(conn, **kwargs):
        calls.append(("session", kwargs["user_id"], kwargs["agent_id"]))
        return "ses_3"

    async def fake_create_run(conn, **kwargs):
        calls.append(("run", kwargs["user_id"], kwargs["skill_id"], kwargs["input_json"]["file_ids"]))
        calls.append(
            (
                "auth_snapshot",
                kwargs["principal_roles"],
                kwargs["principal_department_id"],
                kwargs["auth_source"],
            )
        )
        return "run_3"

    async def fake_append_message(conn, **kwargs):
        calls.append(("message", kwargs["role"], kwargs["content"], kwargs["run_id"]))
        calls.append(("message_metadata", kwargs["metadata_json"]))
        return "msg_3"

    async def fake_bind_files_to_run(conn, **kwargs):
        calls.append(("files", kwargs["file_ids"]))

    async def fake_insert_creation_snapshots(conn, **kwargs):
        calls.append(("creation_snapshots", kwargs["run_id"], kwargs["skill_manifests"][0]["skill_id"]))

    async def fake_append_event(conn, **kwargs):
        calls.append(("event", kwargs["event_type"], kwargs["stage"], kwargs.get("payload", {})))
        return "evt_3"

    async def fake_enqueue_run(payload):
        calls.append(("queue_payload", payload))
        calls.append(
            (
                "queue",
                payload["executor_type"],
                payload["run_id"],
                payload["file_ids"],
                payload["user_id"],
                payload["skill_version"],
                payload["skill_manifests"],
            )
        )
        return 3

    async def fake_record_context(conn, **kwargs):
        calls.append(("context", kwargs["source"], kwargs["message_ids"], kwargs["file_ids"], kwargs["input_payload"], kwargs.get("include_session_history")))
        return {
            "schema_version": "ai-platform.context-snapshot.v1",
            "context_snapshot_id": "ctx_chat_3",
            "source": kwargs["source"],
            "message_count": len(kwargs.get("message_ids") or []),
            "file_count": len(kwargs.get("file_ids") or []),
            "memory_record_count": 0,
        }

    async def fake_get_queue_insight(tenant_id, **kwargs):
        assert tenant_id == "tenant-a"
        assert kwargs == {"user_id": "user-a"}
        return {
            "tenant_id": tenant_id,
            "reason": "workers_busy",
            "depths": {"tenant_queued": 5, "tenant_processing": 1},
            "workers": {"active": 1},
            "capacity": {"available_worker_slots": 0},
        }

    monkeypatch.setattr("app.routes.chat.transaction", fake_transaction)
    monkeypatch.setattr(
        "app.routes.chat.resolve_model_selection",
        fake_resolve_model_selection,
    )
    monkeypatch.setattr("app.routes.chat.repositories.resolve_agent_skill", fake_resolve_agent_skill)
    monkeypatch.setattr(
        "app.routes.chat.repositories.authorize_selected_run_capabilities",
        fake_authorize_selected,
    )
    monkeypatch.setattr("app.routes.chat.repositories.ensure_user", fake_ensure_user)
    monkeypatch.setattr("app.routes.chat.repositories.create_session", fake_create_session)
    monkeypatch.setattr("app.routes.chat.repositories.create_run", fake_create_run)
    monkeypatch.setattr(
        "app.routes.chat.repositories.insert_run_skill_snapshots_at_creation",
        fake_insert_creation_snapshots,
    )
    monkeypatch.setattr("app.routes.chat.repositories.append_message", fake_append_message)
    monkeypatch.setattr("app.routes.chat.repositories.bind_files_to_run", fake_bind_files_to_run)
    monkeypatch.setattr("app.routes.chat.repositories.append_event", fake_append_event)
    monkeypatch.setattr("app.routes.chat.record_initial_context_snapshot", fake_record_context)
    monkeypatch.setattr("app.routes.chat.enqueue_run", fake_enqueue_run)
    monkeypatch.setattr("app.routes.chat.get_queue_insight", fake_get_queue_insight)

    response = await chat_stream(
        ChatStreamRequest(
            agent_id="document-review",
            selected_skill={"skill_id": "qa-file-reviewer", "expected_version": "0.1.0"},
            message="review this document",
            agent_options={"model_id": "deepseek-v4-pro"},
            attachments=[{"key": "file_1", "name": "review.docx"}],
        ),
        principal=principal(department_id="qa", roles=["qa_operator"], source="session-token"),
    )

    assert response.run_id == "run_3"
    assert response.session_id == "ses_3"
    assert response.queue_position == 3
    assert response.queue_insight == {
        "tenant_id": "tenant-a",
        "reason": "workers_busy",
        "depths": {"tenant_queued": 5, "tenant_processing": 1},
        "workers": {"active": 1},
        "capacity": {"available_worker_slots": 0},
    }
    assert ("run", "user-a", "qa-file-reviewer", ["file_1"]) in calls
    assert ("auth_snapshot", ["qa_operator"], "qa", "session-token") in calls
    assert ("files", ["file_1"]) in calls
    snapshot_index = next(index for index, item in enumerate(calls) if item[0] == "creation_snapshots")
    message_index = next(index for index, item in enumerate(calls) if item[0] == "message")
    event_index = next(index for index, item in enumerate(calls) if item[0] == "event")
    queue_index = next(index for index, item in enumerate(calls) if item[0] == "queue_payload")
    assert snapshot_index < message_index < event_index < queue_index
    queue_payload = next(item[1] for item in calls if item[0] == "queue_payload")
    assert queue_payload["executor_type"] == "claude-agent-worker"
    assert queue_payload["run_id"] == "run_3"
    assert queue_payload["file_ids"] == ["file_1"]
    assert queue_payload["user_id"] == "user-a"
    assert queue_payload["model_id"] == "deepseek-v4-pro"
    assert queue_payload["model_value"] == "deepseek-v4-pro"
    assert queue_payload["skill_manifests"][0]["skill_id"] == "qa-file-reviewer"
    assert queue_payload["skill_version"] == queue_payload["skill_manifests"][0]["content_hash"]
    assert queue_payload["release_decision"]["selected_version"] == queue_payload["skill_version"]
    assert queue_payload["release_decision"]["selected_track"] == "manifest_pin"
    ref = queue_payload["skill_manifests"][0]
    assert ref["schema_version"] == "ai-platform.skill-materialization-ref.v1"
    assert len(ref["materialization_sha256"]) == 64
    assert "files" not in ref
    assert "content_base64" not in json.dumps(ref, ensure_ascii=False)
    assert ("message", "user", "review this document", "run_3") in calls
    message_metadata = next(item[1] for item in calls if item[0] == "message_metadata")
    assert message_metadata["locked_skill"] == {"label": "internal-comms"}
    assert "qa-file-reviewer" not in json.dumps(message_metadata, ensure_ascii=False)
    assert "0.1.0" not in json.dumps(message_metadata, ensure_ascii=False)
    assert "/skill" not in json.dumps(message_metadata, ensure_ascii=False)
    assert ("context", "chat_stream", ["msg_3"], ["file_1"], {"message": "review this document"}, True) in calls
    assert queue_payload["context_snapshot_id"] == "ctx_chat_3"
    assert queue_payload["context_snapshot"]["source"] == "chat_stream"
    assert queue_payload["context_snapshot"]["message_count"] == 1
    assert queue_payload["context_snapshot"]["file_count"] == 1
    assert (
        "event",
        "queued",
        "queue",
        {
            "visible_to_user": False,
            "source": "admin_runtime_queue",
            "queue_position": 3,
            "queue_admission_ordinal": 3,
            "queue_probe_source": "redis_metadata",
        },
    ) in calls


@pytest.mark.asyncio
async def test_chat_stream_capability_distribution_denial_precedes_create_run(monkeypatch):
    calls = []

    async def deny(*args, **kwargs):
        calls.append(("authorize", kwargs["skill_id"]))
        raise repository_module.RepositoryAuthorizationError("capability_not_authorized")

    async def fail_create_run(*args, **kwargs):
        calls.append(("create_run", kwargs))
        raise AssertionError("authorization denial must precede create_run")

    monkeypatch.setattr("app.routes.chat.transaction", fake_transaction)
    monkeypatch.setattr(repository_module, "authorize_run_capabilities", deny)
    monkeypatch.setattr("app.routes.chat.repositories.create_run", fail_create_run)

    with pytest.raises(HTTPException) as exc_info:
        await chat_stream(
            ChatStreamRequest(
                agent_id="document-review",
                message="review this document",
                file_ids=["file_1"],
            ),
            principal=principal(department_id="finance", roles=["user"]),
        )

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "capability_not_authorized"
    assert calls == [("authorize", "qa-file-reviewer")]


@pytest.mark.asyncio
async def test_chat_stream_audits_capability_denial_after_source_transaction_rollback(monkeypatch):
    events = []
    transaction_count = 0

    @asynccontextmanager
    async def ordered_transaction():
        nonlocal transaction_count
        transaction_count += 1
        transaction_id = transaction_count
        events.append(("enter", transaction_id))
        try:
            yield f"conn-{transaction_id}"
        finally:
            events.append(("exit", transaction_id))

    denial = CapabilityAuthorizationDenial(
        capability_kind="skill",
        capability_id="qa-file-reviewer",
        actor_department_id="finance",
        actor_roles=("user",),
        department_scope_ids=("qa",),
        role_scope_ids=("qa_operator",),
        scope_mode="allowlist",
        decision_reason="department_not_allowed",
    )

    async def deny(*args, **kwargs):
        events.append(("authorize", kwargs["skill_id"]))
        raise repository_module.RepositoryAuthorizationError(
            "capability_not_authorized",
            denial=denial,
        )

    async def record_audit(conn, **kwargs):
        events.append(("audit", conn, kwargs["source"], kwargs["error"].denial.capability_id))
        return "aud-denied"

    monkeypatch.setattr("app.routes.chat.transaction", ordered_transaction)
    monkeypatch.setattr(repository_module, "authorize_run_capabilities", deny)
    monkeypatch.setattr(repository_module, "append_capability_authorization_denial_audit", record_audit)

    with pytest.raises(HTTPException) as exc_info:
        await chat_stream(
            ChatStreamRequest(
                agent_id="document-review",
                message="review this document",
                file_ids=["file_1"],
            ),
            principal=principal(department_id="finance", roles=["user"]),
        )

    assert exc_info.value.status_code == 403
    assert events == [
        ("enter", 1),
        ("authorize", "qa-file-reviewer"),
        ("exit", 1),
        ("enter", 2),
        ("audit", "conn-2", "chat_stream", "qa-file-reviewer"),
        ("exit", 2),
    ]


@pytest.mark.asyncio
async def test_chat_stream_direct_ragflow_without_explicit_selector_uses_unified_authorizer(monkeypatch):
    calls = []

    async def deny(*args, **kwargs):
        calls.append(kwargs)
        raise repository_module.RepositoryAuthorizationError("capability_not_authorized")

    async def fail_create_run(*args, **kwargs):
        raise AssertionError("direct ragflow denial must precede create_run")

    monkeypatch.setattr("app.routes.chat.transaction", fake_transaction)
    monkeypatch.setattr(repository_module, "authorize_run_capabilities", deny)
    monkeypatch.setattr("app.routes.chat.repositories.create_run", fail_create_run)

    with pytest.raises(HTTPException) as exc_info:
        await chat_stream(
            ChatStreamRequest(
                agent_id="sop-assistant",
                message="search the knowledge base",
            ),
            principal=principal(department_id="qa", roles=["user"]),
        )

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "capability_not_authorized"
    assert len(calls) == 1
    assert calls[0]["agent_id"] == "sop-assistant"
    assert calls[0]["skill_id"] == "ragflow-knowledge-search"
    assert "mcp_tool_ids" not in calls[0]["normalized_input"]


@pytest.mark.asyncio
async def test_chat_stream_legacy_nested_mcp_selector_returns_controlled_400_before_create(monkeypatch):
    async def fail_create_run(*args, **kwargs):
        raise AssertionError("invalid MCP selector must fail before create_run")

    monkeypatch.setattr(repository_module, "create_run", fail_create_run)

    with pytest.raises(HTTPException) as exc_info:
        await chat_stream(
            ChatStreamRequest(
                message="run",
                input={"mcp_tool_ids": "not-a-list"},
            ),
            principal=principal(roles=["admin"]),
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "selected_mcp_tool_ids_required"


@pytest.mark.asyncio
async def test_chat_stream_unauthorized_structured_mcp_selection_fails_before_create(monkeypatch):
    calls = []

    async def deny_selection(*_args, **kwargs):
        calls.append(("authorize", kwargs["tool_ids"]))
        raise repository_module.RepositoryAuthorizationError("mcp_tool_not_available")

    async def fail_create_run(*_args, **_kwargs):
        raise AssertionError("unauthorized MCP selection must fail before create_run")

    monkeypatch.setattr("app.routes.chat.transaction", fake_transaction)
    monkeypatch.setattr(
        repository_module,
        "authorize_selected_chat_mcp_tools",
        deny_selection,
    )
    monkeypatch.setattr(repository_module, "create_run", fail_create_run)

    with pytest.raises(HTTPException) as exc_info:
        await chat_stream(
            ChatStreamRequest(
                message="run",
                selected_mcp_tool_ids=["gateway::tenant-search"],
            ),
            principal=principal(),
        )

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "mcp_tool_not_available"
    assert calls == [("authorize", ["gateway::tenant-search"])]


@pytest.mark.asyncio
async def test_keyed_unauthorized_structured_mcp_rejection_persists_only_safe_ledger_data(monkeypatch):
    ledger = {}
    claims = []
    finalizations = []

    async def deny_selection(*_args, **_kwargs):
        raise repository_module.RepositoryAuthorizationError("mcp_tool_not_available")

    async def provision_principal(*_args, **_kwargs):
        return {"id": "user-a", "tenant_id": "tenant-a"}

    async def claim_submission(*_args, **kwargs):
        claims.append(kwargs["request_fingerprint_sha256"])
        if ledger:
            return dict(ledger), False
        ledger.update(
            request_fingerprint_sha256=kwargs["request_fingerprint_sha256"],
            workspace_id=kwargs["workspace_id"],
            state="resolving",
        )
        return dict(ledger), True

    async def finalize_submission(*_args, **kwargs):
        finalizations.append(kwargs["rejection_code"])
        ledger.update(
            state=kwargs["state"],
            submission_disposition=kwargs["submission_disposition"],
            rejection_code=kwargs["rejection_code"],
        )

    monkeypatch.setattr("app.routes.chat.transaction", fake_transaction)
    monkeypatch.setattr(repository_module, "authorize_selected_chat_mcp_tools", deny_selection)
    monkeypatch.setattr(repository_module, "ensure_submission_principal", provision_principal)
    monkeypatch.setattr(repository_module, "claim_chat_submission", claim_submission)
    monkeypatch.setattr(repository_module, "finalize_chat_submission", finalize_submission)

    def rejected_request(tool_ids):
        return ChatStreamRequest(
            message="run unavailable tool",
            submission_id="7ea93033-30f5-40ea-8a33-2f3c6e7b21c4",
            selected_mcp_tool_ids=tool_ids,
            input={
                "endpoint": "https://private.example.test/mcp",
                "credential": "private-credential",
            },
        )

    with pytest.raises(HTTPException) as first_info:
        await chat_stream(
            rejected_request([" gateway::unauthorized-private-tool ", "gateway::second-private-tool"]),
            principal=principal(),
        )

    with pytest.raises(HTTPException) as replay_info:
        await chat_stream(
            rejected_request(["gateway::unauthorized-private-tool", "gateway::second-private-tool"]),
            principal=principal(),
        )

    with pytest.raises(HTTPException) as mismatch_info:
        await chat_stream(
            rejected_request(["gateway::unauthorized-private-tool", "gateway::different-private-tool"]),
            principal=principal(),
        )

    assert first_info.value.status_code == 403
    assert first_info.value.detail == {
        "code": "mcp_tool_not_available",
        "submission_disposition": "rejected_before_persist",
    }
    assert replay_info.value.status_code == 409
    assert replay_info.value.detail["code"] == "mcp_tool_not_available"
    assert mismatch_info.value.status_code == 409
    assert mismatch_info.value.detail == "submission_payload_mismatch"
    assert claims[0] == claims[1]
    assert claims[2] != claims[0]
    assert finalizations == ["mcp_tool_not_available"]
    assert ledger["state"] == "rejected_before_persist"
    assert ledger["rejection_code"] == "mcp_tool_not_available"
    assert len(str(ledger["request_fingerprint_sha256"])) == 64
    serialized_ledger = json.dumps(ledger, sort_keys=True)
    assert "unauthorized-private-tool" not in serialized_ledger
    assert "private.example.test" not in serialized_ledger
    assert "private-credential" not in serialized_ledger


@pytest.mark.asyncio
async def test_chat_stream_prevalidates_queue_payload_before_persisting(monkeypatch):
    calls = []

    async def fake_resolve_agent_skill(conn, *, tenant_id, agent_id, skill_id):
        return {"executor_type": "claude-agent-worker", "skill_version": "0.1.0", "input_modes": []}

    async def fail_persist(*args, **kwargs):
        calls.append(("persist", args, kwargs))
        raise AssertionError("invalid queue payload must be rejected before persistence")

    async def fail_enqueue_run(payload):
        calls.append(("enqueue", payload))
        raise AssertionError("invalid queue payload must be rejected before enqueue")

    async def fake_governed_skill_manifest_pins(conn, *, skill_id, input_payload, release_policy_version):
        return [snapshot_manifest(skill_id)]

    monkeypatch.setattr("app.routes.chat.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.chat._governed_skill_manifest_pins", fake_governed_skill_manifest_pins)
    monkeypatch.setattr("app.routes.chat.repositories.resolve_agent_skill", fake_resolve_agent_skill)
    monkeypatch.setattr("app.routes.chat.repositories.ensure_user", fail_persist)
    monkeypatch.setattr("app.routes.chat.repositories.create_session", fail_persist)
    monkeypatch.setattr("app.routes.chat.repositories.create_run", fail_persist)
    monkeypatch.setattr("app.routes.chat.repositories.append_message", fail_persist)
    monkeypatch.setattr("app.routes.chat.repositories.bind_files_to_run", fail_persist)
    monkeypatch.setattr("app.routes.chat.repositories.append_event", fail_persist)
    monkeypatch.setattr("app.routes.chat.record_initial_context_snapshot", fail_persist)
    monkeypatch.setattr("app.routes.chat.enqueue_run", fail_enqueue_run)

    with pytest.raises(HTTPException) as exc_info:
        await chat_stream(
            ChatStreamRequest(message="hello"),
            principal=principal(user_id="../runtime/private"),
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "invalid_principal_user_id"
    assert calls == []


@pytest.mark.asyncio
async def test_chat_stream_rejects_unavailable_model_id_before_side_effects(monkeypatch):
    calls = []

    async def fail_side_effect(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("invalid model_id must be rejected before side effects")

    monkeypatch.setattr("app.routes.chat.repositories.create_session", fail_side_effect)
    monkeypatch.setattr("app.routes.chat.repositories.create_run", fail_side_effect)
    monkeypatch.setattr("app.routes.chat.repositories.append_message", fail_side_effect)
    monkeypatch.setattr("app.routes.chat.repositories.append_event", fail_side_effect)
    monkeypatch.setattr("app.routes.chat.enqueue_run", fail_side_effect)

    with pytest.raises(HTTPException) as exc_info:
        await chat_stream(
            ChatStreamRequest(message="hello", agent_options={"model_id": "not-allowed"}),
            principal=principal(roles=["admin"]),
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "model_id_not_available"
    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("raw_model_id", [True, 123, 1.5])
async def test_chat_stream_rejects_non_string_model_id_without_coercion(
    monkeypatch,
    raw_model_id,
):
    calls = []
    current_settings = type(
        "S",
        (),
        {
            "model_catalog_json": '[{"id":"True"},{"id":"123"},{"id":"1.5"}]',
            "default_model_id": "True",
            "claude_agent_model": "",
            "anthropic_model": "",
            "openai_model": "",
            "max_active_runs_per_user": 3,
            "platform_skills_root": "",
        },
    )()

    async def fail_side_effect(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("non-string model_id must be rejected before side effects")

    monkeypatch.setattr("app.routes.chat.get_settings", lambda: current_settings)
    monkeypatch.setattr("app.routes.chat.repositories.create_session", fail_side_effect)
    monkeypatch.setattr("app.routes.chat.repositories.create_run", fail_side_effect)
    monkeypatch.setattr("app.routes.chat.repositories.append_message", fail_side_effect)
    monkeypatch.setattr("app.routes.chat.repositories.append_event", fail_side_effect)
    monkeypatch.setattr("app.routes.chat.enqueue_run", fail_side_effect)

    with pytest.raises(HTTPException) as exc_info:
        await chat_stream(
            ChatStreamRequest(message="hello", agent_options={"model_id": raw_model_id}),
            principal=principal(roles=["admin"]),
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "model_id_not_available"
    assert calls == []


@pytest.mark.parametrize("raw_model_id", [[], {}])
def test_chat_stream_request_rejects_structured_model_id(raw_model_id):
    with pytest.raises(ValueError):
        ChatStreamRequest(
            message="hello",
            agent_options={"model_id": raw_model_id},
        )


@pytest.mark.asyncio
async def test_chat_stream_maps_catalog_model_id_to_runtime_model_value(monkeypatch):
    calls = []
    current_settings = type(
        "S",
        (),
        {
            "model_catalog_json": '[{"id":"pro-tier","value":"deepseek-v4-pro","label":"Pro tier"}]',
            "default_model_id": "pro-tier",
            "claude_agent_model": "",
            "anthropic_model": "",
            "openai_model": "",
            "max_active_runs_per_user": 3,
            "platform_skills_root": "",
        },
    )()

    async def fake_resolve_agent_skill(conn, *, tenant_id, agent_id, skill_id):
        return {"executor_type": "claude-agent-worker", "skill_version": "0.1.0", "input_modes": []}

    async def fake_ensure_user(conn, **kwargs):
        return None

    async def fake_create_session(conn, **kwargs):
        return "ses_model"

    async def fake_create_run(conn, **kwargs):
        calls.append(("create_run_input", kwargs["input_json"]))
        return "run_model"

    async def fake_append_message(conn, **kwargs):
        return "msg_model"

    async def fake_bind_files_to_run(conn, **kwargs):
        return None

    async def fake_append_event(conn, **kwargs):
        return "evt_model"

    async def fake_enqueue_run(payload):
        calls.append(("queue_payload", payload))
        return 1

    async def fake_governed_skill_manifest_pins(conn, *, skill_id, input_payload, release_policy_version):
        return [snapshot_manifest(skill_id)]

    monkeypatch.setattr("app.routes.chat.get_settings", lambda: current_settings)
    monkeypatch.setattr("app.routes.chat.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.chat._governed_skill_manifest_pins", fake_governed_skill_manifest_pins)
    monkeypatch.setattr("app.routes.chat.repositories.resolve_agent_skill", fake_resolve_agent_skill)
    monkeypatch.setattr("app.routes.chat.repositories.ensure_user", fake_ensure_user)
    monkeypatch.setattr("app.routes.chat.repositories.create_session", fake_create_session)
    monkeypatch.setattr("app.routes.chat.repositories.create_run", fake_create_run)
    monkeypatch.setattr("app.routes.chat.repositories.append_message", fake_append_message)
    monkeypatch.setattr("app.routes.chat.repositories.bind_files_to_run", fake_bind_files_to_run)
    monkeypatch.setattr("app.routes.chat.repositories.append_event", fake_append_event)
    monkeypatch.setattr("app.routes.chat.enqueue_run", fake_enqueue_run)

    response = await chat_stream(
        ChatStreamRequest(message="hello", agent_options={"model_id": "pro-tier"}),
        principal=principal(),
    )

    create_run_input = next(item[1] for item in calls if item[0] == "create_run_input")
    queue_payload = next(item[1] for item in calls if item[0] == "queue_payload")
    assert response.run_id == "run_model"
    assert create_run_input["model_id"] == "pro-tier"
    assert create_run_input["model_value"] == "deepseek-v4-pro"
    assert queue_payload["model_id"] == "pro-tier"
    assert queue_payload["model_value"] == "deepseek-v4-pro"



@pytest.mark.asyncio
async def test_chat_stream_developer_fixture_uses_skillless_harness_chat(monkeypatch):
    calls = {}

    async def fake_resolve_agent_skill(conn, *, tenant_id, agent_id, skill_id):
        assert tenant_id == "frc-test-a"
        assert agent_id == "frc_agent_83ebaed7aa4c5f49"
        assert skill_id == "general-chat"
        return {"executor_type": "claude-agent-worker", "skill_version": "0.1.0", "input_modes": ["chat"]}

    async def noop(*args, **kwargs):
        return None

    async def fake_create_session(conn, **kwargs):
        return "ses_frc_general"

    async def fake_create_run(conn, **kwargs):
        calls["create_run"] = kwargs
        return "run_frc_general"

    async def fake_append_message(conn, **kwargs):
        return "msg_frc_general"

    async def fake_record_context(conn, **kwargs):
        calls["context"] = kwargs
        return {
            "schema_version": "ai-platform.context-snapshot.v1",
            "context_snapshot_id": "ctx_frc_general",
            "source": kwargs["source"],
            "message_count": len(kwargs.get("message_ids") or []),
            "file_count": len(kwargs.get("file_ids") or []),
            "memory_record_count": 0,
        }

    async def fake_append_event(conn, **kwargs):
        calls.setdefault("events", []).append(kwargs)

    async def fake_enqueue_run(payload):
        calls["queue"] = payload
        return 1

    monkeypatch.setattr("app.routes.chat.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.chat.repositories.resolve_agent_skill", fake_resolve_agent_skill)
    monkeypatch.setattr("app.routes.chat.repositories.ensure_user", noop)
    monkeypatch.setattr("app.routes.chat.repositories.create_session", fake_create_session)
    monkeypatch.setattr("app.routes.chat.repositories.create_run", fake_create_run)
    monkeypatch.setattr("app.routes.chat.repositories.append_message", fake_append_message)
    monkeypatch.setattr("app.routes.chat.repositories.bind_files_to_run", noop)
    monkeypatch.setattr("app.routes.chat.record_initial_context_snapshot", fake_record_context)
    monkeypatch.setattr("app.routes.chat.repositories.append_event", fake_append_event)
    monkeypatch.setattr("app.routes.chat.enqueue_run", fake_enqueue_run)

    response = await chat_stream(
        ChatStreamRequest(
            workspace_id="frc_test_a_default",
            agent_id="frc_agent_83ebaed7aa4c5f49",
            message="alice 并发创建运行验收，请简短回复。",
        ),
        principal=principal(user_id="alice", tenant_id="frc-test-a", roles=["developer"]),
    )

    assert response.run_id == "run_frc_general"
    queue_payload = calls["queue"]
    assert queue_payload["execution_kind"] == "harness_chat"
    assert queue_payload["schema_version"] == "ai-platform.run-payload.v2"
    assert queue_payload["skill_id"] is None
    assert queue_payload["skill_version"] is None
    assert queue_payload["skill_manifests"] == []
    assert queue_payload["release_decision"] == {}
    assert calls["create_run"]["execution_kind"] == "harness_chat"
    assert calls["create_run"]["skill_id"] is None
    assert calls["context"]["workspace_id"] == "frc_test_a_default"
    assert calls["context"]["skill_id"] is None
    assert calls["context"]["source"] == "chat_stream"
    assert not any(event["event_type"] == "skill_selected" for event in calls["events"])
    assert not any(
        event["event_type"] == "skill_release_decision" for event in calls["events"]
    )


@pytest.mark.asyncio
async def test_chat_stream_rejects_unsafe_principal_user_id_before_persistence(monkeypatch):
    calls = []

    async def fake_resolve_agent_skill(conn, *, tenant_id, agent_id, skill_id):
        calls.append(("resolve_agent_skill", tenant_id, agent_id, skill_id))
        return {"executor_type": "claude-agent-worker", "skill_version": "0.1.0", "input_modes": ["chat"]}

    async def fail_persistence(*args, **kwargs):
        calls.append(("persisted", kwargs))
        raise AssertionError("unsafe principal user_id should fail before persistence")

    monkeypatch.setattr("app.routes.chat.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.chat.repositories.resolve_agent_skill", fake_resolve_agent_skill)
    monkeypatch.setattr("app.routes.chat.repositories.ensure_user", fail_persistence)
    monkeypatch.setattr("app.routes.chat.repositories.create_run", fail_persistence)

    with pytest.raises(HTTPException) as exc_info:
        await chat_stream(
            ChatStreamRequest(
                workspace_id="default",
                agent_id="agent-a",
                skill_id="general-chat",
                message="hello",
            ),
            principal=principal(user_id="../alice@example.test", tenant_id="tenant-a"),
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "invalid_principal_user_id"
    assert calls == []


@pytest.mark.asyncio
async def test_chat_stream_rejects_release_policy_version_that_differs_from_primary_pin(monkeypatch):
    async def fake_resolve_agent_skill(conn, *, tenant_id, agent_id, skill_id):
        return {
            "executor_type": "claude-agent-worker",
            "skill_version": "old-release-version",
            "release_policy_version": "old-release-version",
            "input_modes": ["docx"],
        }

    async def fail_create_run(*args, **kwargs):
        raise AssertionError("run must not be created when policy version cannot be materialized")

    def fake_skill_manifest_pins(skill_id, input_payload):
        return [
            {
                "skill_id": "qa-file-reviewer",
                "version": "current-hash",
                "content_hash": "current-hash",
                "source": {"kind": "builtin", "asset_dir": "qa-file-reviewer"},
                "files": [],
                "dependency_ids": [],
                "allowed": True,
                "staged": False,
                "used": False,
            }
        ]

    async def fake_get_effective_skill_version_for_policy(conn, *, skill_id, version):
        return None

    monkeypatch.setattr("app.routes.chat.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.chat.repositories.resolve_agent_skill", fake_resolve_agent_skill)
    monkeypatch.setattr("app.routes.chat.repositories.create_run", fail_create_run)
    monkeypatch.setattr(
        "app.routes.chat.repositories.get_effective_skill_version_for_policy",
        fake_get_effective_skill_version_for_policy,
    )
    monkeypatch.setattr("app.routes.chat._skill_manifest_pins", fake_skill_manifest_pins)

    with pytest.raises(Exception) as exc_info:
        await chat_stream(
            ChatStreamRequest(
                agent_id="document-review",
                skill_id="qa-file-reviewer",
                message="review this document",
                attachments=[{"key": "file_1", "name": "review.docx"}],
            ),
            principal=principal(roles=["admin"]),
        )

    assert getattr(exc_info.value, "status_code", None) == 409
    assert getattr(exc_info.value, "detail", None) == "skill_version_not_materializable"


@pytest.mark.asyncio
async def test_chat_stream_rejects_invalid_snapshot_governance_manifest_as_materialization_conflict(monkeypatch):
    async def fake_resolve_agent_skill(conn, *, tenant_id, agent_id, skill_id):
        return {"executor_type": "claude-agent-worker", "skill_version": "hash-pin", "input_modes": ["docx"]}

    async def fail_create_run(*args, **kwargs):
        raise AssertionError("run must not be created when snapshot governance cannot be materialized")

    def fake_skill_manifest_pins(skill_id, input_payload):
        return [
            {
                "skill_id": skill_id,
                "version": "hash-pin",
                "content_hash": "hash-pin",
                "source": {"kind": "builtin", "asset_dir": skill_id},
                "files": [{"relative_path": "references/..", "content_base64": "c2tpbGw=", "size_bytes": 5}],
                "dependency_ids": [],
                "allowed": True,
                "staged": False,
                "used": False,
            }
        ]

    monkeypatch.setattr("app.routes.chat.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.chat.repositories.resolve_agent_skill", fake_resolve_agent_skill)
    monkeypatch.setattr("app.routes.chat.repositories.create_run", fail_create_run)
    monkeypatch.setattr("app.routes.chat._skill_manifest_pins", fake_skill_manifest_pins)

    with pytest.raises(Exception) as exc_info:
        await chat_stream(
            ChatStreamRequest(
                agent_id="document-review",
                skill_id="qa-file-reviewer",
                message="review this document",
                attachments=[{"key": "file_1", "name": "review.docx"}],
            ),
            principal=principal(roles=["admin"]),
        )

    assert getattr(exc_info.value, "status_code", None) == 409
    assert getattr(exc_info.value, "detail", None) == "skill_version_not_materializable"


@pytest.mark.asyncio
async def test_chat_stream_producer_contract_persists_uploaded_release_policy_manifest(monkeypatch):
    calls = {}
    dependency_manifest = snapshot_manifest("minimax-docx", description="Pinned DOCX helper")

    async def fake_resolve_agent_skill(conn, *, tenant_id, agent_id, skill_id):
        return {
            "executor_type": "claude-agent-worker",
            "skill_version": "hash-uploaded",
            "release_policy_version": "hash-uploaded",
            "input_modes": ["chat"],
        }

    async def fake_get_effective_skill_version_for_policy(conn, *, skill_id, version):
        assert skill_id == "qa-file-reviewer"
        assert version == "hash-uploaded"
        return uploaded_skill_version_row(
            skill_id=skill_id,
            version=version,
            dependency_ids=["minimax-docx"],
            dependency_manifests=[dependency_manifest],
        )

    async def noop(*args, **kwargs):
        return None

    async def fake_create_session(conn, **kwargs):
        return "ses_uploaded"

    async def fake_create_run(conn, **kwargs):
        calls["create_run"] = kwargs
        return "run_uploaded"

    async def fake_append_message(conn, **kwargs):
        calls["message"] = kwargs
        return "msg_uploaded"

    async def fake_append_event(conn, **kwargs):
        calls.setdefault("events", []).append(kwargs)

    async def fake_enqueue_run(payload):
        calls["queue"] = payload
        return 4

    monkeypatch.setattr("app.routes.chat.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.chat.BuiltinSkillRegistry", PolicyBuiltinRegistry)
    monkeypatch.setattr("app.routes.chat.repositories.resolve_agent_skill", fake_resolve_agent_skill)
    monkeypatch.setattr(
        "app.routes.chat.repositories.get_effective_skill_version_for_policy",
        fake_get_effective_skill_version_for_policy,
    )
    monkeypatch.setattr("app.routes.chat.repositories.ensure_user", noop)
    monkeypatch.setattr("app.routes.chat.repositories.create_session", fake_create_session)
    monkeypatch.setattr("app.routes.chat.repositories.create_run", fake_create_run)
    monkeypatch.setattr("app.routes.chat.repositories.append_message", fake_append_message)
    monkeypatch.setattr("app.routes.chat.repositories.bind_files_to_run", noop)
    monkeypatch.setattr("app.routes.chat.repositories.append_event", fake_append_event)
    monkeypatch.setattr("app.routes.chat.enqueue_run", fake_enqueue_run)

    response = await chat_stream(
        ChatStreamRequest(
            agent_id="document-review",
            message="review this document",
            input={"note": "uploaded policy"},
        ),
        principal=principal(),
    )

    assert response.run_id == "run_uploaded"
    assert calls["create_run"]["input_json"]["skill_version"] == "hash-uploaded"
    assert calls["queue"]["skill_version"] == "hash-uploaded"
    assert calls["create_run"]["input_json"]["skill_manifests"] == calls["queue"]["skill_manifests"]
    assert [item["skill_id"] for item in calls["queue"]["skill_manifests"]] == ["qa-file-reviewer", "minimax-docx"]
    assert calls["queue"]["skill_manifests"][0]["schema_version"] == (
        "ai-platform.skill-materialization-ref.v1"
    )
    assert "files" not in calls["queue"]["skill_manifests"][0]
    assert calls["queue"]["skill_manifests"][1]["content_hash"] == dependency_manifest["content_hash"]
    assert any(event["payload"].get("skill_version") == "hash-uploaded" for event in calls["events"])
    persisted_non_identity_snapshot = {
        **calls["create_run"]["input_json"],
        "context_snapshot_id": calls["queue"]["context_snapshot_id"],
        "context_snapshot": calls["queue"]["context_snapshot"],
    }
    locked_payload = QueueRunPayload.model_validate(
        {
            "tenant_id": calls["create_run"]["tenant_id"],
            "workspace_id": calls["create_run"]["workspace_id"],
            "user_id": calls["create_run"]["user_id"],
            "session_id": calls["create_run"]["session_id"],
            "run_id": response.run_id,
            "agent_id": calls["create_run"]["agent_id"],
            "skill_id": calls["create_run"]["skill_id"],
            **{
                field: persisted_non_identity_snapshot[field]
                for field in QueueRunPayload.model_fields
                if field in persisted_non_identity_snapshot
            },
        }
    )
    assert locked_payload.model_dump(mode="json") == calls["queue"]


@pytest.mark.asyncio
async def test_chat_stream_uses_rollout_selected_previous_version(monkeypatch):
    calls = {}

    async def fake_resolve_agent_skill(conn, *, tenant_id, agent_id, skill_id):
        return {
            "executor_type": "claude-agent-worker",
            "skill_version": "hash-new",
            "release_policy_version": "hash-new",
            "release_policy_previous_version": "hash-old",
            "release_policy_rollout_percent": 0,
            "input_modes": ["chat"],
        }

    async def fake_get_effective_skill_version_for_policy(conn, *, skill_id, version):
        assert skill_id == "qa-file-reviewer"
        assert version == "hash-old"
        return uploaded_skill_version_row(skill_id=skill_id, version=version)

    async def noop(*args, **kwargs):
        return None

    async def fake_create_session(conn, **kwargs):
        return "ses_rollout"

    async def fake_create_run(conn, **kwargs):
        calls["create_run"] = kwargs
        return "run_rollout"

    async def fake_append_message(conn, **kwargs):
        calls["message"] = kwargs
        return "msg_rollout"

    async def fake_append_event(conn, **kwargs):
        calls.setdefault("events", []).append(kwargs)

    async def fake_enqueue_run(payload):
        calls["queue"] = payload
        return 4

    monkeypatch.setattr("app.routes.chat.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.chat.BuiltinSkillRegistry", PolicyBuiltinRegistry)
    monkeypatch.setattr("app.routes.chat.repositories.resolve_agent_skill", fake_resolve_agent_skill)
    monkeypatch.setattr(
        "app.routes.chat.repositories.get_effective_skill_version_for_policy",
        fake_get_effective_skill_version_for_policy,
    )
    monkeypatch.setattr("app.routes.chat.repositories.ensure_user", noop)
    monkeypatch.setattr("app.routes.chat.repositories.create_session", fake_create_session)
    monkeypatch.setattr("app.routes.chat.repositories.create_run", fake_create_run)
    monkeypatch.setattr("app.routes.chat.repositories.append_message", fake_append_message)
    monkeypatch.setattr("app.routes.chat.repositories.bind_files_to_run", noop)
    monkeypatch.setattr("app.routes.chat.repositories.append_event", fake_append_event)
    monkeypatch.setattr("app.routes.chat.enqueue_run", fake_enqueue_run)

    response = await chat_stream(
        ChatStreamRequest(
            agent_id="document-review",
            message="review this document",
            input={"note": "rollout policy"},
        ),
        principal=principal(),
    )

    assert response.run_id == "run_rollout"
    assert calls["create_run"]["input_json"]["skill_version"] == "hash-old"
    assert calls["create_run"]["input_json"]["release_decision"]["selected_version"] == "hash-old"
    assert calls["create_run"]["input_json"]["release_decision"]["selected_track"] == "previous"
    assert calls["queue"]["skill_version"] == "hash-old"
    assert calls["queue"]["release_decision"]["selected_track"] == "previous"
    assert calls["queue"]["skill_manifests"][0]["schema_version"] == (
        "ai-platform.skill-materialization-ref.v1"
    )
    assert "files" not in calls["queue"]["skill_manifests"][0]
    assert any(event["payload"].get("skill_version") == "hash-old" for event in calls["events"])
    assert any(
        event["event_type"] == "skill_release_decision"
        and event["payload"]["selected_version"] == "hash-old"
        and event["payload"]["visible_to_user"] is False
        for event in calls["events"]
    )


@pytest.mark.asyncio
async def test_chat_stream_rejects_reviewed_rollout_previous_version(monkeypatch):
    async def fake_resolve_agent_skill(conn, *, tenant_id, agent_id, skill_id):
        return {
            "executor_type": "claude-agent-worker",
            "skill_version": "hash-new",
            "release_policy_version": "hash-new",
            "release_policy_previous_version": "hash-old",
            "release_policy_rollout_percent": 0,
            "input_modes": ["chat"],
        }

    async def fake_get_effective_skill_version_for_policy(conn, *, skill_id, version):
        row = uploaded_skill_version_row(skill_id=skill_id, version=version)
        row["status"] = "reviewed"
        return row

    async def noop(*args, **kwargs):
        return None

    async def fail_create_run(*args, **kwargs):
        raise AssertionError("run must not be created for reviewed rollout previous version")

    async def fail_enqueue_run(*args, **kwargs):
        raise AssertionError("queue must not receive reviewed rollout previous version")

    monkeypatch.setattr("app.routes.chat.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.chat.BuiltinSkillRegistry", PolicyBuiltinRegistry)
    monkeypatch.setattr("app.routes.chat.repositories.resolve_agent_skill", fake_resolve_agent_skill)
    monkeypatch.setattr(
        "app.routes.chat.repositories.get_effective_skill_version_for_policy",
        fake_get_effective_skill_version_for_policy,
    )
    monkeypatch.setattr("app.routes.chat.repositories.ensure_user", noop)
    monkeypatch.setattr("app.routes.chat.repositories.create_session", noop)
    monkeypatch.setattr("app.routes.chat.repositories.create_run", fail_create_run)
    monkeypatch.setattr("app.routes.chat.repositories.append_message", noop)
    monkeypatch.setattr("app.routes.chat.repositories.bind_files_to_run", noop)
    monkeypatch.setattr("app.routes.chat.repositories.append_event", noop)
    monkeypatch.setattr("app.routes.chat.enqueue_run", fail_enqueue_run)

    with pytest.raises(HTTPException) as exc_info:
        await chat_stream(
            ChatStreamRequest(
                agent_id="document-review",
                message="review this document",
                input={"note": "rollout policy"},
            ),
            principal=principal(),
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "skill_version_not_materializable"


@pytest.mark.asyncio
async def test_chat_stream_appends_canonical_product_events(monkeypatch):
    events = []

    async def fake_resolve_agent_skill(conn, *, tenant_id, agent_id, skill_id):
        return {"executor_type": "claude-agent-worker", "skill_version": "0.1.0", "input_modes": ["docx"]}

    async def fake_authorize_selected(conn, **kwargs):
        assert kwargs["skill_id"] == "qa-file-reviewer"
        assert kwargs["expected_version"] == "0.1.0"
        return await fake_resolve_agent_skill(
            conn,
            tenant_id=kwargs["tenant_id"],
            agent_id=kwargs["agent_id"],
            skill_id=kwargs["skill_id"],
        )

    async def fake_create_session(conn, **kwargs):
        return "ses_events"

    async def fake_create_run(conn, **kwargs):
        return "run_events"

    async def noop(*args, **kwargs):
        return None

    async def fake_append_event(conn, **kwargs):
        events.append(
            {
                "event_type": kwargs["event_type"],
                "stage": kwargs["stage"],
                "message": kwargs["message"],
                "payload": kwargs["payload"],
            }
        )
        return f"evt_{len(events)}"

    async def fake_enqueue_run(payload):
        return 1

    monkeypatch.setattr("app.routes.chat.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.chat.repositories.resolve_agent_skill", fake_resolve_agent_skill)
    monkeypatch.setattr(
        "app.routes.chat.repositories.authorize_selected_run_capabilities",
        fake_authorize_selected,
    )
    monkeypatch.setattr("app.routes.chat.repositories.ensure_user", noop)
    monkeypatch.setattr("app.routes.chat.repositories.create_session", fake_create_session)
    monkeypatch.setattr("app.routes.chat.repositories.create_run", fake_create_run)
    monkeypatch.setattr("app.routes.chat.repositories.append_message", noop)
    monkeypatch.setattr("app.routes.chat.repositories.bind_files_to_run", noop)
    monkeypatch.setattr("app.routes.chat.repositories.append_event", fake_append_event)
    monkeypatch.setattr("app.routes.chat.enqueue_run", fake_enqueue_run)

    await chat_stream(
        ChatStreamRequest(
            agent_id="qa-word-review",
            selected_skill={"skill_id": "qa-file-reviewer", "expected_version": "0.1.0"},
            message="审核这个文档",
            attachments=[{"key": "file_doc", "name": "demo.docx"}],
        ),
        principal=principal(),
    )

    product_events = [
        event
        for event in events
        if not (event["event_type"] == "queued" and event["payload"].get("source") == "admin_runtime_queue")
    ]
    assert [event["event_type"] for event in product_events] == [
        "intent_detected",
        "intent_confirmed",
        "queued",
        "skill_selected",
        "file_bound",
        "skill_release_decision",
    ]
    assert product_events[0]["payload"]["visible_to_user"] is True
    assert product_events[1]["payload"]["selected_capability"] == "document_review"
    assert product_events[3]["payload"]["skill_id"] == "qa-file-reviewer"
    assert product_events[4]["payload"]["file_ids"] == ["file_doc"]
    assert any(
        event["event_type"] == "queued"
        and event["stage"] == "queue"
        and event["payload"] == {
            "visible_to_user": False,
            "source": "admin_runtime_queue",
            "queue_position": 1,
            "queue_admission_ordinal": 1,
            "queue_probe_source": "redis_metadata",
        }
        for event in events
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("requested_agent_id", ["general-agent", "team-chat"])
async def test_lambchat_chat_stream_uses_skillless_harness_for_chat_agents(
    monkeypatch,
    requested_agent_id,
):
    calls = []

    async def fake_resolve_agent_skill(conn, *, tenant_id, agent_id, skill_id):
        calls.append(("resolve", agent_id, skill_id))
        return {"executor_type": "claude-agent-worker", "skill_version": "0.1.0", "input_modes": ["chat"]}

    async def fake_create_run(conn, **kwargs):
        calls.append(
            ("run", kwargs["agent_id"], kwargs["execution_kind"], kwargs["skill_id"])
        )
        return "run_general"

    async def noop(*args, **kwargs):
        return None

    async def fake_create_session(conn, **kwargs):
        return "ses_general"

    async def fake_enqueue_run(payload):
        calls.append(
            (
                "queue",
                payload["agent_id"],
                payload["execution_kind"],
                payload["skill_id"],
            )
        )
        return 1

    monkeypatch.setattr("app.routes.chat.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.chat.repositories.resolve_agent_skill", fake_resolve_agent_skill)
    monkeypatch.setattr("app.routes.chat.repositories.ensure_user", noop)
    monkeypatch.setattr("app.routes.chat.repositories.create_session", fake_create_session)
    monkeypatch.setattr("app.routes.chat.repositories.create_run", fake_create_run)
    monkeypatch.setattr("app.routes.chat.repositories.append_message", noop)
    monkeypatch.setattr("app.routes.chat.repositories.bind_files_to_run", noop)
    monkeypatch.setattr("app.routes.chat.repositories.append_event", noop)
    monkeypatch.setattr("app.routes.chat.enqueue_run", fake_enqueue_run)

    response = await chat_stream(
        ChatStreamRequest(message="hello"),
        agent_id=requested_agent_id,
        principal=principal(),
    )

    assert response.run_id == "run_general"
    assert not any(call[0] == "resolve" for call in calls)
    assert ("run", requested_agent_id, "harness_chat", None) in calls
    assert ("queue", requested_agent_id, "harness_chat", None) in calls


@pytest.mark.asyncio
async def test_chat_stream_rejects_legacy_general_chat_as_selected_skill():
    with pytest.raises(HTTPException) as exc_info:
        await chat_stream(
            ChatStreamRequest(
                message="hello",
                selected_skill={
                    "skill_id": "general-chat",
                    "expected_version": "0.1.0",
                },
            ),
            principal=principal(),
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "general_chat_is_not_a_skill"


@pytest.mark.asyncio
async def test_chat_stream_redacts_raw_skill_id_from_ordinary_user_response(monkeypatch):
    async def fake_resolve_agent_skill(conn, *, tenant_id, agent_id, skill_id):
        return {"executor_type": "claude-agent-worker", "skill_version": "0.1.0", "input_modes": ["docx"]}

    async def fake_authorize_selected(conn, **kwargs):
        assert kwargs["skill_id"] == "qa-file-reviewer"
        assert kwargs["expected_version"] == "0.1.0"
        return await fake_resolve_agent_skill(
            conn,
            tenant_id=kwargs["tenant_id"],
            agent_id=kwargs["agent_id"],
            skill_id=kwargs["skill_id"],
        )

    async def fake_create_run(conn, **kwargs):
        return "run_review"

    async def noop(*args, **kwargs):
        return None

    async def fake_create_session(conn, **kwargs):
        return "ses_review"

    async def fake_enqueue_run(payload):
        return 1

    monkeypatch.setattr("app.routes.chat.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.chat.repositories.resolve_agent_skill", fake_resolve_agent_skill)
    monkeypatch.setattr(
        "app.routes.chat.repositories.authorize_selected_run_capabilities",
        fake_authorize_selected,
    )
    monkeypatch.setattr("app.routes.chat.repositories.ensure_user", noop)
    monkeypatch.setattr("app.routes.chat.repositories.create_session", fake_create_session)
    monkeypatch.setattr("app.routes.chat.repositories.create_run", fake_create_run)
    monkeypatch.setattr("app.routes.chat.repositories.append_message", noop)
    monkeypatch.setattr("app.routes.chat.repositories.bind_files_to_run", noop)
    monkeypatch.setattr("app.routes.chat.repositories.append_event", noop)
    monkeypatch.setattr("app.routes.chat.enqueue_run", fake_enqueue_run)

    response = await chat_stream(
        ChatStreamRequest(
            agent_id="qa-word-review",
            selected_skill={"skill_id": "qa-file-reviewer", "expected_version": "0.1.0"},
            message="审核这个文档",
            attachments=[{"key": "file_doc", "name": "demo.docx"}],
        ),
        principal=principal(),
    )

    assert response.intent_decision is not None
    assert response.intent_decision.selected_capability == "document_review"
    assert response.intent_decision.skill_id is None


@pytest.mark.asyncio
async def test_chat_stream_rejects_raw_skill_id_for_ordinary_user(monkeypatch):
    calls = []

    async def fake_resolve_agent_skill(conn, *, tenant_id, agent_id, skill_id):
        calls.append(("resolve", agent_id, skill_id))
        return {"executor_type": "claude-agent-worker", "skill_version": "0.1.0", "input_modes": ["chat"]}

    async def fake_create_run(conn, **kwargs):
        calls.append(("run", kwargs["agent_id"], kwargs["skill_id"]))
        return "run_general"

    async def noop(*args, **kwargs):
        return None

    async def fake_create_session(conn, **kwargs):
        return "ses_general"

    async def fake_enqueue_run(payload):
        calls.append(("queue", payload["agent_id"], payload["skill_id"]))
        return 1

    monkeypatch.setattr("app.routes.chat.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.chat.repositories.resolve_agent_skill", fake_resolve_agent_skill)
    monkeypatch.setattr("app.routes.chat.repositories.ensure_user", noop)
    monkeypatch.setattr("app.routes.chat.repositories.create_session", fake_create_session)
    monkeypatch.setattr("app.routes.chat.repositories.create_run", fake_create_run)
    monkeypatch.setattr("app.routes.chat.repositories.append_message", noop)
    monkeypatch.setattr("app.routes.chat.repositories.bind_files_to_run", noop)
    monkeypatch.setattr("app.routes.chat.repositories.append_event", noop)
    monkeypatch.setattr("app.routes.chat.enqueue_run", fake_enqueue_run)

    with pytest.raises(HTTPException) as exc_info:
        await chat_stream(
            ChatStreamRequest(
                agent_id="general-agent",
                skill_id="qa-file-reviewer",
                message="hello",
            ),
            principal=principal(),
        )

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "raw_skill_selector_forbidden"
    assert calls == []


@pytest.mark.asyncio
async def test_chat_stream_ignores_raw_skill_like_agent_id_for_ordinary_user(monkeypatch):
    calls = []

    async def fake_resolve_agent_skill(conn, *, tenant_id, agent_id, skill_id):
        calls.append(("resolve", agent_id, skill_id))
        return {"executor_type": "claude-agent-worker", "skill_version": "0.1.0", "input_modes": ["chat"]}

    async def fake_create_run(conn, **kwargs):
        calls.append(("run", kwargs["agent_id"], kwargs["skill_id"]))
        return "run_general"

    async def noop(*args, **kwargs):
        return None

    async def fake_create_session(conn, **kwargs):
        return "ses_general"

    async def fake_enqueue_run(payload):
        calls.append(("queue", payload["agent_id"], payload["skill_id"]))
        return 1

    monkeypatch.setattr("app.routes.chat.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.chat.repositories.resolve_agent_skill", fake_resolve_agent_skill)
    monkeypatch.setattr("app.routes.chat.repositories.ensure_user", noop)
    monkeypatch.setattr("app.routes.chat.repositories.create_session", fake_create_session)
    monkeypatch.setattr("app.routes.chat.repositories.create_run", fake_create_run)
    monkeypatch.setattr("app.routes.chat.repositories.append_message", noop)
    monkeypatch.setattr("app.routes.chat.repositories.bind_files_to_run", noop)
    monkeypatch.setattr("app.routes.chat.repositories.append_event", noop)
    monkeypatch.setattr("app.routes.chat.enqueue_run", fake_enqueue_run)

    response = await chat_stream(
        ChatStreamRequest(
            agent_id="baoyu-translate",
            message="hello",
        ),
        principal=principal(),
    )

    assert response.status == "queued"
    assert not any(call[0] == "resolve" for call in calls)
    assert ("run", "general-agent", None) in calls
    assert ("queue", "general-agent", None) in calls


@pytest.mark.asyncio
async def test_general_chat_queues_claude_agent_worker_executor(monkeypatch):
    calls = []

    async def fake_resolve_agent_skill(conn, *, tenant_id, agent_id, skill_id):
        assert agent_id == "general-agent"
        assert skill_id == "general-chat"
        return {"executor_type": "claude-agent-worker", "skill_version": "0.1.0", "input_modes": ["chat"]}

    async def fake_create_run(conn, **kwargs):
        calls.append(("run_input", kwargs["input_json"]["executor_type"]))
        return "run_embedded"

    async def noop(*args, **kwargs):
        return None

    async def fake_create_session(conn, **kwargs):
        return "ses_embedded"

    async def fake_enqueue_run(payload):
        calls.append(("queue", payload["executor_type"], payload["user_id"], payload["input"]["message"]))
        return 1

    monkeypatch.setattr("app.routes.chat.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.chat.repositories.resolve_agent_skill", fake_resolve_agent_skill)
    monkeypatch.setattr("app.routes.chat.repositories.ensure_user", noop)
    monkeypatch.setattr("app.routes.chat.repositories.create_session", fake_create_session)
    monkeypatch.setattr("app.routes.chat.repositories.create_run", fake_create_run)
    monkeypatch.setattr("app.routes.chat.repositories.append_message", noop)
    monkeypatch.setattr("app.routes.chat.repositories.bind_files_to_run", noop)
    monkeypatch.setattr("app.routes.chat.repositories.append_event", noop)
    monkeypatch.setattr("app.routes.chat.enqueue_run", fake_enqueue_run)

    response = await chat_stream(ChatStreamRequest(message="hello"), principal=principal())

    assert response.status == "queued"
    assert ("run_input", "claude-agent-worker") in calls
    assert ("queue", "claude-agent-worker", "user-a", "hello") in calls



@pytest.mark.asyncio
async def test_lambchat_word_review_attachment_routes_to_qa_agent(monkeypatch):
    calls = []

    async def fake_resolve_agent_skill(conn, *, tenant_id, agent_id, skill_id):
        calls.append(("resolve", agent_id, skill_id))
        return {"executor_type": "claude-agent-worker", "skill_version": "0.1.0", "input_modes": ["docx"]}

    async def fake_create_run(conn, **kwargs):
        calls.append(("run", kwargs["agent_id"], kwargs["skill_id"], kwargs["input_json"]["file_ids"]))
        return "run_review"

    async def noop(*args, **kwargs):
        return None

    async def fake_create_session(conn, **kwargs):
        calls.append(("session", kwargs["agent_id"]))
        return "ses_review"

    async def fake_bind_files_to_run(conn, **kwargs):
        calls.append(("files", kwargs["file_ids"]))

    async def fake_enqueue_run(payload):
        calls.append(("queue", payload["agent_id"], payload["skill_id"], payload["file_ids"]))
        return 1

    monkeypatch.setattr("app.routes.chat.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.chat.repositories.resolve_agent_skill", fake_resolve_agent_skill)
    monkeypatch.setattr("app.routes.chat.repositories.ensure_user", noop)
    monkeypatch.setattr("app.routes.chat.repositories.create_session", fake_create_session)
    monkeypatch.setattr("app.routes.chat.repositories.create_run", fake_create_run)
    monkeypatch.setattr("app.routes.chat.repositories.append_message", noop)
    monkeypatch.setattr("app.routes.chat.repositories.bind_files_to_run", fake_bind_files_to_run)
    monkeypatch.setattr("app.routes.chat.repositories.append_event", noop)
    monkeypatch.setattr("app.routes.chat.enqueue_run", fake_enqueue_run)

    response = await chat_stream(
        ChatStreamRequest(
            message="审核一下这个文档",
            attachments=[
                {
                    "key": "file_review",
                    "name": "TR(G)-AD-IP321-1-031-1.0 IP321.docx",
                    "mimeType": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                }
            ],
        ),
        agent_id="general-agent",
        principal=principal(),
    )

    assert response.run_id == "run_review"
    assert ("resolve", "qa-word-review", "qa-file-reviewer") in calls
    assert ("session", "qa-word-review") in calls
    assert ("run", "qa-word-review", "qa-file-reviewer", ["file_review"]) in calls
    assert ("files", ["file_review"]) in calls
    assert ("queue", "qa-word-review", "qa-file-reviewer", ["file_review"]) in calls


@pytest.mark.asyncio
async def test_chat_stream_word_review_file_id_routes_to_qa_agent(monkeypatch):
    calls = []

    async def fake_get_file(conn, *, tenant_id, file_id):
        calls.append(("get_file", tenant_id, file_id))
        return {
            "id": file_id,
            "tenant_id": "tenant-a",
            "workspace_id": "default",
            "user_id": "user-a",
            "session_id": None,
            "run_id": None,
            "original_name": "TR(G)-AD-IP321-1-031-1.0 IP321.docx",
            "content_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        }

    async def fake_resolve_agent_skill(conn, *, tenant_id, agent_id, skill_id):
        calls.append(("resolve", agent_id, skill_id))
        return {"executor_type": "claude-agent-worker", "skill_version": "0.1.0", "input_modes": ["docx"]}

    async def fake_create_run(conn, **kwargs):
        calls.append(("run", kwargs["agent_id"], kwargs["skill_id"], kwargs["input_json"]["file_ids"]))
        return "run_review_file_id"

    async def noop(*args, **kwargs):
        return None

    async def fake_create_session(conn, **kwargs):
        calls.append(("session", kwargs["agent_id"]))
        return "ses_review_file_id"

    async def fake_bind_files_to_run(conn, **kwargs):
        calls.append(("files", kwargs["file_ids"]))

    async def fake_enqueue_run(payload):
        calls.append(("queue", payload["agent_id"], payload["skill_id"], payload["file_ids"]))
        return 1

    monkeypatch.setattr("app.routes.chat.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.chat.repositories.get_file", fake_get_file)
    monkeypatch.setattr("app.routes.chat.repositories.resolve_agent_skill", fake_resolve_agent_skill)
    monkeypatch.setattr("app.routes.chat.repositories.ensure_user", noop)
    monkeypatch.setattr("app.routes.chat.repositories.create_session", fake_create_session)
    monkeypatch.setattr("app.routes.chat.repositories.create_run", fake_create_run)
    monkeypatch.setattr("app.routes.chat.repositories.append_message", noop)
    monkeypatch.setattr("app.routes.chat.repositories.bind_files_to_run", fake_bind_files_to_run)
    monkeypatch.setattr("app.routes.chat.repositories.append_event", noop)
    monkeypatch.setattr("app.routes.chat.enqueue_run", fake_enqueue_run)

    response = await chat_stream(
        ChatStreamRequest(message="审核一下这个文档", file_ids=["file_review"]),
        agent_id="general-agent",
        principal=principal(),
    )

    assert response.run_id == "run_review_file_id"
    assert ("get_file", "tenant-a", "file_review") in calls
    assert ("resolve", "qa-word-review", "qa-file-reviewer") in calls
    assert ("session", "qa-word-review") in calls
    assert ("run", "qa-word-review", "qa-file-reviewer", ["file_review"]) in calls
    assert ("files", ["file_review"]) in calls
    assert ("queue", "qa-word-review", "qa-file-reviewer", ["file_review"]) in calls


@pytest.mark.parametrize(
    "row_overrides",
    [
        pytest.param({}, id="missing-scope"),
        pytest.param({"tenant_id": "tenant-b", "workspace_id": "default", "user_id": "user-a", "session_id": None, "run_id": None}, id="tenant-mismatch"),
        pytest.param({"tenant_id": "tenant-a", "workspace_id": "other", "user_id": "user-a", "session_id": None, "run_id": None}, id="workspace-mismatch"),
        pytest.param({"tenant_id": "tenant-a", "workspace_id": "default", "user_id": "user-b", "session_id": None, "run_id": None}, id="user-mismatch"),
        pytest.param({"tenant_id": "tenant-a", "workspace_id": "default", "user_id": "user-a", "session_id": "ses_other", "run_id": None}, id="session-mismatch"),
        pytest.param({"tenant_id": "tenant-a", "workspace_id": "default", "user_id": "user-a", "session_id": None, "run_id": "run_bound"}, id="already-bound"),
    ],
)
@pytest.mark.asyncio
async def test_chat_stream_ignores_file_id_metadata_outside_request_scope(monkeypatch, row_overrides):
    calls = []

    async def fake_get_file(conn, *, tenant_id, file_id):
        calls.append(("get_file", tenant_id, file_id))
        row = {
            "id": file_id,
            "original_name": "review.docx",
            "content_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        }
        row.update(row_overrides)
        return row

    async def fake_resolve_agent_skill(conn, *, tenant_id, agent_id, skill_id):
        calls.append(("resolve", agent_id, skill_id))
        return {"executor_type": "claude-agent-worker", "skill_version": "0.1.0", "input_modes": ["chat"]}

    async def fake_create_run(conn, **kwargs):
        calls.append(("run", kwargs["agent_id"], kwargs["skill_id"], kwargs["input_json"]["file_ids"]))
        return "run_scope_guard"

    async def noop(*args, **kwargs):
        return None

    async def fake_create_session(conn, **kwargs):
        calls.append(("session", kwargs["agent_id"]))
        return "ses_scope_guard"

    async def fake_enqueue_run(payload):
        calls.append(("queue", payload["agent_id"], payload["skill_id"], payload["file_ids"]))
        return 1

    monkeypatch.setattr("app.routes.chat.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.chat.repositories.get_file", fake_get_file)
    monkeypatch.setattr("app.routes.chat.repositories.resolve_agent_skill", fake_resolve_agent_skill)
    monkeypatch.setattr("app.routes.chat.repositories.ensure_user", noop)
    monkeypatch.setattr("app.routes.chat.repositories.create_session", fake_create_session)
    monkeypatch.setattr("app.routes.chat.repositories.create_run", fake_create_run)
    monkeypatch.setattr("app.routes.chat.repositories.append_message", noop)
    monkeypatch.setattr("app.routes.chat.repositories.bind_files_to_run", noop)
    monkeypatch.setattr("app.routes.chat.repositories.append_event", noop)
    monkeypatch.setattr("app.routes.chat.enqueue_run", fake_enqueue_run)

    response = await chat_stream(
        ChatStreamRequest(message="审核一下这个文档", file_ids=["file_review"]),
        agent_id="general-agent",
        principal=principal(),
    )

    assert response.run_id == "run_scope_guard"
    assert ("get_file", "tenant-a", "file_review") in calls
    assert not any(
        item == ("resolve", "general-agent", "general-chat") for item in calls
    )
    assert ("session", "general-agent") in calls
    assert ("run", "general-agent", None, ["file_review"]) in calls
    assert ("queue", "general-agent", None, ["file_review"]) in calls
    assert not any(
        item == ("resolve", "qa-word-review", "qa-file-reviewer") for item in calls
    )


@pytest.mark.asyncio
async def test_lambchat_translate_agent_defaults_to_translate_skill(monkeypatch):
    calls = []

    async def fake_resolve_agent_skill(conn, *, tenant_id, agent_id, skill_id):
        calls.append(("resolve", agent_id, skill_id))
        return {"executor_type": "claude-agent-worker", "skill_version": "0.1.0", "input_modes": ["docx"]}

    async def fake_create_run(conn, **kwargs):
        calls.append(("run", kwargs["agent_id"], kwargs["skill_id"], kwargs["input_json"]["file_ids"]))
        return "run_translate"

    async def noop(*args, **kwargs):
        return None

    async def fake_create_session(conn, **kwargs):
        calls.append(("session", kwargs["agent_id"]))
        return "ses_translate"

    async def fake_enqueue_run(payload):
        calls.append(("queue", payload["agent_id"], payload["skill_id"], payload["file_ids"]))
        return 1

    monkeypatch.setattr("app.routes.chat.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.chat.repositories.resolve_agent_skill", fake_resolve_agent_skill)
    monkeypatch.setattr("app.routes.chat.repositories.ensure_user", noop)
    monkeypatch.setattr("app.routes.chat.repositories.create_session", fake_create_session)
    monkeypatch.setattr("app.routes.chat.repositories.create_run", fake_create_run)
    monkeypatch.setattr("app.routes.chat.repositories.append_message", noop)
    monkeypatch.setattr("app.routes.chat.repositories.bind_files_to_run", noop)
    monkeypatch.setattr("app.routes.chat.repositories.append_event", noop)
    monkeypatch.setattr("app.routes.chat.enqueue_run", fake_enqueue_run)

    response = await chat_stream(
        ChatStreamRequest(
            message="翻译一下这个文档",
            attachments=[
                {
                    "key": "file_translate",
                    "name": "demo.docx",
                    "mimeType": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                }
            ],
        ),
        agent_id="document-translation",
        principal=principal(),
    )

    assert response.run_id == "run_translate"
    assert ("resolve", "baoyu-translate", "baoyu-translate") in calls
    assert ("queue", "baoyu-translate", "baoyu-translate", ["file_translate"]) in calls
    assert response.intent_decision is not None
    assert response.intent_decision.selected_capability == "document_translation"
    assert "baoyu-translate" not in response.intent_decision.model_dump_json()


@pytest.mark.asyncio
async def test_chat_stream_reuses_authorized_prior_turn_file_for_routed_skill(monkeypatch):
    """A routed file-capable Skill reuses its snapshot-authorized session input."""

    calls = []
    run_ids = iter(["run_routed_first", "run_routed_second"])

    async def fake_resolve_agent_skill(conn, *, tenant_id, agent_id, skill_id):
        calls.append(("resolve", agent_id, skill_id))
        return {
            "executor_type": "claude-agent-worker",
            "skill_version": "0.1.0",
            "input_modes": ["docx"],
        }

    async def fake_create_session(conn, **kwargs):
        calls.append(
            ("session", kwargs["session_id"], kwargs["agent_id"], kwargs["workspace_id"])
        )
        return kwargs["session_id"]

    async def fake_get_authorized_session(
        conn,
        *,
        tenant_id,
        user_id,
        session_id,
        workspace_id=None,
        for_update=False,
    ):
        assert (tenant_id, user_id, session_id) == ("tenant-a", "user-a", "ses_routed")
        calls.append(("session_lookup", workspace_id, for_update))
        return {"id": session_id, "agent_id": "general-agent", "workspace_id": "workspace-routed"}

    async def fake_list_authorized_session_runs(
        conn,
        *,
        tenant_id,
        user_id,
        session_id,
        workspace_id=None,
        limit=20,
    ):
        calls.append(("continuation_runs", tenant_id, user_id, session_id, workspace_id, limit))
        return [{"agent_id": "general-agent", "skill_id": "baoyu-translate"}]

    async def fake_create_run(conn, **kwargs):
        run_id = next(run_ids)
        calls.append(
            (
                "run",
                kwargs["session_id"],
                kwargs["agent_id"],
                kwargs["skill_id"],
                kwargs["workspace_id"],
                run_id,
            )
        )
        return run_id

    async def noop(*args, **kwargs):
        return None

    async def fake_workspace(conn, *, tenant_id, workspace_id):
        calls.append(("workspace", workspace_id))

    async def fake_authorize_files(conn, **kwargs):
        calls.append(
            (
                "files",
                kwargs["workspace_id"],
                kwargs["file_ids"],
                kwargs["reusable_file_ids"],
            )
        )

    async def fake_list_authorized_session_input_files(conn, **kwargs):
        calls.append(
            (
                "session_files",
                kwargs["tenant_id"],
                kwargs["workspace_id"],
                kwargs["user_id"],
                kwargs["session_id"],
            )
        )
        return [
            {
                "id": "file_routed",
                "original_name": "demo.docx",
                "content_type": (
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                ),
                "created_at": "2026-08-11T00:00:00Z",
            }
        ]

    async def fake_bind_files(conn, **kwargs):
        calls.append(("bind", kwargs["file_ids"]))

    async def fake_enqueue_run(payload):
        calls.append(
            (
                "queue",
                payload["session_id"],
                payload["agent_id"],
                payload["skill_id"],
                payload["workspace_id"],
                payload["file_ids"],
            )
        )
        return 1

    monkeypatch.setattr("app.routes.chat.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.chat.repositories.resolve_agent_skill", fake_resolve_agent_skill)
    monkeypatch.setattr("app.routes.chat.repositories.ensure_user", noop)
    monkeypatch.setattr("app.routes.chat.repositories.get_authorized_session", fake_get_authorized_session)
    monkeypatch.setattr(
        "app.routes.chat.repositories.list_authorized_session_runs",
        fake_list_authorized_session_runs,
    )
    monkeypatch.setattr("app.routes.chat.repositories.ensure_workspace_belongs_to_tenant", fake_workspace)
    monkeypatch.setattr("app.routes.chat.repositories.create_session", fake_create_session)
    monkeypatch.setattr("app.routes.chat.repositories.create_run", fake_create_run)
    monkeypatch.setattr("app.routes.chat.repositories.append_message", noop)
    monkeypatch.setattr("app.routes.chat.repositories.authorize_files_for_run", fake_authorize_files)
    monkeypatch.setattr(
        "app.routes.chat.repositories.list_authorized_session_input_files",
        fake_list_authorized_session_input_files,
    )
    monkeypatch.setattr("app.routes.chat.repositories.bind_files_to_run", fake_bind_files)
    monkeypatch.setattr("app.routes.chat.repositories.append_event", noop)
    monkeypatch.setattr("app.routes.chat.enqueue_run", fake_enqueue_run)
    monkeypatch.setattr(
        "app.routes.chat.repositories.new_id",
        lambda kind: "ses_routed" if kind == "ses" else f"{kind}_unexpected",
    )

    first = await chat_stream(
        ChatStreamRequest(
            message="翻译这个 Word 文档",
            attachments=[
                {
                    "key": "file_routed",
                    "name": "demo.docx",
                    "mimeType": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                }
            ],
        ),
        agent_id="document-translation",
        principal=principal(),
    )

    assert first.session_id == "ses_routed"
    assert first.intent_decision is not None
    assert first.intent_decision.agent_id == "document-translation"

    second = await chat_stream(
        ChatStreamRequest(
            message="继续处理同一份文档",
            session_id=first.session_id,
        ),
        agent_id="general-agent",
        principal=principal(),
    )

    assert second.session_id == first.session_id
    assert second.intent_decision is not None
    assert second.intent_decision.agent_id == "document-translation"
    assert calls == [
        ("resolve", "baoyu-translate", "baoyu-translate"),
        ("workspace", "default"),
        ("files", "default", ["file_routed"], []),
        ("session", "ses_routed", "baoyu-translate", "default"),
        ("run", "ses_routed", "baoyu-translate", "baoyu-translate", "default", "run_routed_first"),
        ("bind", ["file_routed"]),
        ("queue", "ses_routed", "baoyu-translate", "baoyu-translate", "default", ["file_routed"]),
        ("session_lookup", None, False),
        ("session_lookup", "workspace-routed", True),
        ("continuation_runs", "tenant-a", "user-a", "ses_routed", "workspace-routed", 1),
        ("resolve", "general-agent", "baoyu-translate"),
        ("session_files", "tenant-a", "workspace-routed", "user-a", "ses_routed"),
        ("workspace", "workspace-routed"),
        ("files", "workspace-routed", ["file_routed"], ["file_routed"]),
        ("run", "ses_routed", "general-agent", "baoyu-translate", "workspace-routed", "run_routed_second"),
        ("bind", []),
        (
            "queue",
            "ses_routed",
            "general-agent",
            "baoyu-translate",
            "workspace-routed",
            ["file_routed"],
        ),
    ]


@pytest.mark.asyncio
async def test_chat_stream_revalidates_preserved_continuation_skill_for_current_principal(monkeypatch):
    calls = []
    session_locks = []

    async def owned_session(conn, *, tenant_id, user_id, session_id, workspace_id=None, for_update=False):
        assert (tenant_id, user_id, session_id) == ("tenant-a", "user-a", "ses_locked")
        session_locks.append((workspace_id, for_update))
        if for_update:
            calls.append(("session_lock", tenant_id, workspace_id, user_id, session_id))
        return {"id": session_id, "agent_id": "general-agent", "workspace_id": "workspace-owned"}

    async def admission_lock(conn, *, tenant_id, user_id):
        calls.append(("admission_lock", tenant_id, user_id))

    async def forbidden_limit_check(*_args, **_kwargs):
        raise AssertionError("capability denial must precede active-run limit rejection")

    async def prior_runs(conn, *, tenant_id, user_id, session_id, workspace_id=None, limit=20):
        calls.append(("prior_runs", tenant_id, user_id, session_id, workspace_id, limit))
        return [{"agent_id": "general-agent", "skill_id": "audit-finding-rca"}]

    async def deny_preserved_skill(conn, **kwargs):
        calls.append(("authorize", kwargs["tenant_id"], kwargs["agent_id"], kwargs["skill_id"]))
        raise repository_module.RepositoryAuthorizationError("capability_not_authorized")

    async def noop(*_args, **_kwargs):
        return None

    monkeypatch.setattr("app.routes.chat.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.chat.repositories.get_authorized_session", owned_session)
    monkeypatch.setattr(
        "app.routes.chat.repositories.acquire_user_active_run_admission_lock",
        admission_lock,
        raising=False,
    )
    monkeypatch.setattr(
        "app.routes.chat.repositories.enforce_user_active_run_admission_under_lock",
        forbidden_limit_check,
        raising=False,
    )
    monkeypatch.setattr("app.routes.chat.repositories.list_authorized_session_runs", prior_runs)
    monkeypatch.setattr("app.routes.chat.repositories.authorize_run_capabilities", deny_preserved_skill)
    monkeypatch.setattr("app.routes.chat.repositories.append_capability_authorization_denial_audit", noop)

    with pytest.raises(HTTPException) as exc_info:
        await chat_stream(
            ChatStreamRequest(message="继续查看这批审计发现", session_id="ses_locked"),
            agent_id="general-agent",
            principal=principal(),
        )

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "capability_not_authorized"
    assert session_locks == [(None, False), ("workspace-owned", True)]
    assert calls == [
        ("admission_lock", "tenant-a", "user-a"),
        ("session_lock", "tenant-a", "workspace-owned", "user-a", "ses_locked"),
        ("prior_runs", "tenant-a", "user-a", "ses_locked", "workspace-owned", 1),
        ("authorize", "tenant-a", "general-agent", "audit-finding-rca"),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    (
        "submission_id",
        "force_creation_rollback",
        "restored_continuation",
        "enqueue_failure_mode",
    ),
    [
        (None, False, False, None),
        ("7ea93033-30f5-40ea-8a33-2f3c6e7b21c4", False, False, None),
        (None, True, False, None),
        ("7ea93033-30f5-40ea-8a33-2f3c6e7b21c4", True, False, None),
        (None, False, False, "before_publish"),
        (None, False, False, "definitive_rejection"),
        ("8f2cf18b-e414-4ddd-b99e-c21c32d4f086", False, True, None),
        (
            "9c356f6d-360b-41d0-a97e-3ab16d70a874",
            False,
            True,
            "after_publish_unknown",
        ),
    ],
    ids=[
        "unkeyed",
        "keyed",
        "unkeyed-rollback",
        "keyed-rollback",
        "unkeyed-publish-failure",
        "unkeyed-definitive-rejection",
        "restored-continuation",
        "restored-lost-ack",
    ],
)
async def test_new_profile_submit_commits_after_user_and_profile_admission_before_enqueue(
    monkeypatch,
    submission_id,
    force_creation_rollback,
    restored_continuation,
    enqueue_failure_mode,
):
    """Route/persistence mirror only; PostgreSQL commit visibility is opt-in coverage."""

    from app.agent_apps import AgentProfileAdmission
    from app.models import AgentConversationIdentity

    calls: list[str] = []
    persisted: dict[str, dict] = {}
    transaction_depth = 0
    committed_transactions = 0
    committed_submission: dict[str, object] | None = None
    committed_run: dict[str, object] | None = None
    queue_admission: QueueAdmissionMetadata | None = None
    queue_readback_available = enqueue_failure_mode != "after_publish_unknown"
    enqueue_payloads: list[dict[str, object]] = []
    published_payloads: list[dict[str, object]] = []
    invalidated_contexts: list[str] = []
    profile_manifest = snapshot_manifest("profile-specialist")
    secondary_profile_manifest = snapshot_manifest("profile-reference-search")

    class TransactionState:
        def __init__(self) -> None:
            self.submission = dict(committed_submission) if committed_submission is not None else None
            self.run = dict(committed_run) if committed_run is not None else None

    @asynccontextmanager
    async def tracked_transaction():
        nonlocal committed_run, committed_submission, committed_transactions, transaction_depth
        state = TransactionState()
        transaction_depth += 1
        try:
            yield state
        except BaseException:
            raise
        else:
            if force_creation_rollback and state.run is not None and committed_run is None:
                raise RuntimeError("forced Agent run commit failure")
            committed_submission = state.submission
            committed_run = state.run
            committed_transactions += 1
        finally:
            transaction_depth -= 1

    async def admission_lock(*_args, **_kwargs):
        calls.append("user_lock")

    async def ensure_principal(*_args, **_kwargs):
        calls.append("principal")

    async def late_ensure_user(*_args, **_kwargs):
        calls.append("late_user")

    async def profile_admission(*_args, **kwargs):
        calls.append("profile_lock")
        submitted_request = kwargs["submitted_request"]
        assert kwargs["query_agent_id"] == (
            "agt_support" if restored_continuation else "general-agent"
        )
        assert submitted_request.session_id == (
            "ses-profile-lock-order" if restored_continuation else None
        )
        assert submitted_request.agent_options == {"enable_thinking": "off"}
        assert submitted_request.disabled_skills == []
        assert submitted_request.selected_mcp_tool_ids == []
        return AgentProfileAdmission(
            agent_id="agt_support",
            revision=7,
            content_hash="a" * 64,
            skill={
                "skill_id": "profile-specialist",
                "skill_version": profile_manifest["content_hash"],
                "executor_type": "claude-agent-worker",
                "input_modes": ["docx"],
            },
            skills=(
                {
                    "skill_id": "profile-specialist",
                    "skill_version": profile_manifest["content_hash"],
                    "executor_type": "claude-agent-worker",
                    "input_modes": ["docx"],
                },
                {
                    "skill_id": "profile-reference-search",
                    "skill_version": secondary_profile_manifest["content_hash"],
                    "executor_type": "claude-agent-worker",
                    "input_modes": [],
                },
            ),
            model={"id": "model-a", "value": "provider-model-a"},
            mcp_tool_ids=("qa-mcp::qa-search",) if force_creation_rollback else (),
            private_execution_input={
                "agent_id": "agt_support",
                "revision": 7,
                "content_hash": "a" * 64,
                "instructions": "private profile instructions",
                "skill_set": [
                    {
                        "skill_id": "profile-specialist",
                        "expected_version": profile_manifest["content_hash"],
                    },
                    {
                        "skill_id": "profile-reference-search",
                        "expected_version": secondary_profile_manifest["content_hash"],
                    },
                ],
            },
            public_identity=AgentConversationIdentity(
                agent_id="agt_support",
                revision=7,
                name="Support assistant",
                supported_input_types=["text", "file"],
            ),
        )

    async def owned_session(*_args, **_kwargs):
        return {
            "id": "ses-profile-lock-order",
            "workspace_id": "default",
            "agent_id": "agt_support",
            "admitted_agent_profile_revision": 7,
            "admitted_agent_profile_hash": "a" * 64,
        }

    async def authorize_profile_skill(*_args, **_kwargs):
        calls.append("skill_auth")
        return {
            "skill_id": "profile-specialist",
            "skill_version": profile_manifest["content_hash"],
            "executor_type": "claude-agent-worker",
            "input_modes": ["docx"],
        }

    async def governed_manifest(*_args, **kwargs):
        skill_id = kwargs["skill_id"]
        manifest = (
            profile_manifest
            if skill_id == "profile-specialist"
            else secondary_profile_manifest
        )
        return [dict(manifest)]

    async def authorize_workspace(*_args, **_kwargs):
        calls.append("workspace_auth")

    async def authorize_files(*_args, **_kwargs):
        calls.append("file_auth")
        assert _kwargs["file_ids"] == (
            ["file_profile_history"]
            if restored_continuation
            else ["file_profile_context"]
        )
        assert _kwargs["reusable_file_ids"] == (
            ["file_profile_history"] if restored_continuation else []
        )
        assert _kwargs["input_modes"] is None

    async def list_reusable_files(*_args, **_kwargs):
        calls.append("list_reusable_files")
        return [
            {
                "id": "file_profile_history",
                "original_name": "history.pdf",
                "content_type": "application/pdf",
                "created_at": "2026-08-11T00:00:00Z",
            }
        ]

    async def create_session(*_args, **kwargs):
        calls.append("create_session")
        persisted["session"] = kwargs
        return kwargs["session_id"]

    async def create_run(conn, **kwargs):
        calls.append("create_run")
        persisted["run"] = kwargs
        conn.run = {
            "id": "run-profile-lock-order",
            "status": "queued",
            "workspace_id": kwargs["workspace_id"],
            "session_id": kwargs["session_id"],
            "agent_id": kwargs["agent_id"],
            "skill_id": kwargs["skill_id"],
            "trace_id": "trace-profile-lock-order",
            "admitted_agent_profile_revision": kwargs["admitted_agent_profile_revision"],
            "admitted_agent_profile_hash": kwargs["admitted_agent_profile_hash"],
            "input_json": kwargs["input_json"],
        }
        return "run-profile-lock-order"

    async def append_message(*_args, **_kwargs):
        return "msg-profile-lock-order"

    async def claim_submission(conn, **kwargs):
        calls.append("claim")
        conn.submission = {
            "submission_id": kwargs["submission_id"],
            "request_fingerprint_sha256": kwargs["request_fingerprint_sha256"],
            "state": "resolving",
            "outcome_json": {},
        }
        return conn.submission, True

    async def get_submission(conn, **_kwargs):
        return conn.submission

    async def get_run(conn, **_kwargs):
        return conn.run

    async def finalize_submission(conn, **kwargs):
        assert conn.submission is not None
        conn.submission.update(
            {
                key: value
                for key, value in kwargs.items()
                if key
                in {
                    "state",
                    "workspace_id",
                    "session_id",
                    "run_id",
                    "outcome_json",
                    "rejection_code",
                    "queue_position",
                    "queue_admission_ordinal",
                    "queue_message_id",
                }
                and value is not None
            }
        )
        if kwargs.get("state") == "enqueue_failed":
            calls.append("terminalize_submission")

    async def mark_enqueue_failed(conn, **kwargs):
        assert conn.run is not None
        assert kwargs["run_id"] == "run-profile-lock-order"
        conn.run["status"] = "failed"
        conn.run["error_code"] = "queue_enqueue_failed"
        calls.append("terminalize_run")
        return RunTerminalizationProgress(
            completed=True,
            status="failed",
            did_transition=True,
        )

    async def reauthorize(*_args, **kwargs):
        assert transaction_depth == 1
        assert committed_run is not None
        assert committed_submission is not None
        assert kwargs["run_id"] == "run-profile-lock-order"
        assert committed_run["admitted_agent_profile_revision"] == 7
        assert committed_run["admitted_agent_profile_hash"] == "a" * 64
        calls.append("profile_reauth")

    async def enqueue(payload):
        nonlocal queue_admission
        assert transaction_depth == 1, "Redis dispatch must use a fresh transaction over committed Agent state"
        assert committed_transactions >= 1
        assert committed_run is not None
        assert committed_submission is not None
        assert committed_submission["state"] == "accepted_pending_enqueue"
        calls.append("enqueue")
        enqueue_payloads.append(dict(payload))
        if enqueue_failure_mode == "definitive_rejection":
            raise QueueAdmissionRejected("queue_payload_invalid")
        if len(enqueue_payloads) == 1 and enqueue_failure_mode == "before_publish":
            raise RuntimeError("queue publication failed")
        published_payloads.append(dict(payload))
        queue_admission = QueueAdmissionMetadata(1, 7, "profile-message")
        if len(enqueue_payloads) == 1 and enqueue_failure_mode == "after_publish_unknown":
            raise RuntimeError("queue acknowledgement unavailable")
        return 1

    async def existing_queue_admission(payload):
        assert not enqueue_payloads or dict(payload) == enqueue_payloads[0]
        return queue_admission if queue_readback_available else None

    async def noop(*_args, **_kwargs):
        return None

    async def preflight(**kwargs):
        if kwargs["mcp_required"]:
            calls.append("mcp_preflight")

    async def bind_mcp_context(*_args, **_kwargs):
        calls.append("bind_mcp_context")

    async def invalidate_mcp_context(context_id):
        invalidated_contexts.append(context_id)

    monkeypatch.setattr("app.routes.chat.transaction", tracked_transaction)
    monkeypatch.setattr(
        "app.routes.chat.repositories.acquire_user_active_run_admission_lock",
        admission_lock,
        raising=False,
    )
    monkeypatch.setattr(
        "app.routes.chat.repositories.ensure_submission_principal",
        ensure_principal,
        raising=False,
    )
    monkeypatch.setattr("app.routes.chat.repositories.ensure_user", late_ensure_user)
    monkeypatch.setattr("app.routes.chat.resolve_profile_for_admission", profile_admission)
    monkeypatch.setattr("app.routes.chat.resolve_bound_profile_for_submission", profile_admission)
    monkeypatch.setattr("app.routes.chat.repositories.get_authorized_session", owned_session)
    monkeypatch.setattr(
        "app.routes.chat.repositories.authorize_selected_run_capabilities",
        authorize_profile_skill,
    )
    monkeypatch.setattr("app.routes.chat._governed_skill_manifest_pins", governed_manifest)
    monkeypatch.setattr(
        "app.routes.chat.repositories.ensure_workspace_belongs_to_tenant",
        authorize_workspace,
    )
    monkeypatch.setattr("app.routes.chat.repositories.authorize_files_for_run", authorize_files)
    monkeypatch.setattr(
        "app.routes.chat.repositories.list_authorized_session_input_files",
        list_reusable_files,
    )
    monkeypatch.setattr("app.routes.chat.repositories.create_session", create_session)
    monkeypatch.setattr("app.routes.chat.repositories.create_run", create_run)
    monkeypatch.setattr("app.routes.chat.repositories.insert_run_skill_snapshots_at_creation", noop)
    monkeypatch.setattr("app.routes.chat.repositories.append_message", append_message)
    monkeypatch.setattr("app.routes.chat.repositories.claim_chat_submission", claim_submission)
    monkeypatch.setattr("app.routes.chat.repositories.get_chat_submission", get_submission)
    monkeypatch.setattr("app.routes.chat.repositories.get_authorized_run", get_run)
    monkeypatch.setattr("app.routes.chat.repositories.finalize_chat_submission", finalize_submission)
    monkeypatch.setattr("app.routes.chat.repositories.mark_run_enqueue_failed", mark_enqueue_failed)
    monkeypatch.setattr("app.routes.chat.repositories.bind_files_to_run", noop)
    monkeypatch.setattr("app.routes.chat.repositories.append_event", noop)
    monkeypatch.setattr("app.routes.chat.mcp.preflight_mcp_admission", preflight)
    monkeypatch.setattr("app.routes.chat.mcp.bind_run_mcp_context", bind_mcp_context)
    monkeypatch.setattr(
        "app.routes.chat.mcp.invalidate_mcp_runtime_context",
        invalidate_mcp_context,
    )
    monkeypatch.setattr("app.routes.chat.reauthorize_pinned_run_for_replay", reauthorize)
    monkeypatch.setattr("app.routes.chat.read_queue_admission", existing_queue_admission)
    monkeypatch.setattr("app.routes.chat.enqueue_run", enqueue)
    monkeypatch.setattr(
        "app.routes.chat.repositories.new_id",
        lambda kind: "ses-profile-lock-order" if kind == "ses" else "run-profile-lock-order",
    )

    request_payload = {
        "message": "run the selected Agent",
        "agent_options": {"enable_thinking": "off"},
        "disabled_skills": [],
        "selected_mcp_tool_ids": [],
        "submission_id": submission_id,
        "user_timezone": "Asia/Shanghai",
    }
    if restored_continuation:
        request_payload["session_id"] = "ses-profile-lock-order"
    else:
        request_payload["file_ids"] = ["file_profile_context"]
        request_payload["selected_agent_profile"] = {
            "agent_id": "agt_support",
            "expected_revision": 7,
        }
    if force_creation_rollback:
        request_payload["mcp_context_id"] = "mcpctx-profile-rollback"
    chat_request = ChatStreamRequest.model_validate(request_payload)
    query_agent_id = "agt_support" if restored_continuation else "general-agent"
    if force_creation_rollback:
        with pytest.raises(HTTPException) as exc_info:
            await chat_stream(
                chat_request,
                agent_id=query_agent_id,
                principal=principal(),
            )
        assert exc_info.value.status_code == 500
        assert exc_info.value.detail["code"] == "chat_submission_internal_error"
        assert exc_info.value.detail["submission_disposition"] == "rejected_before_persist"
        diagnostic_id = exc_info.value.detail["diagnostic_id"]
        assert diagnostic_id.startswith("diag_")
        assert len(diagnostic_id) == 21
        int(diagnostic_id[5:], 16)
        assert committed_run is None
        assert committed_submission is not None
        assert committed_submission["state"] == "rejected_before_persist"
        assert committed_submission["rejection_code"] == "chat_submission_internal_error"
        assert "enqueue" not in calls
        assert "mcp_preflight" in calls
        assert "bind_mcp_context" in calls
        assert invalidated_contexts == ["mcpctx-profile-rollback"]
        return

    if enqueue_failure_mode == "definitive_rejection":
        with pytest.raises(HTTPException) as exc_info:
            await chat_stream(
                chat_request,
                agent_id=query_agent_id,
                principal=principal(),
            )
        assert exc_info.value.status_code == 503
        assert exc_info.value.detail == "queue_enqueue_failed"
        assert committed_transactions >= 2
        assert committed_run is not None
        assert committed_run["status"] == "failed"
        assert committed_run["error_code"] == "queue_enqueue_failed"
        assert committed_submission is not None
        assert committed_submission["submission_id"]
        assert committed_submission["run_id"] == "run-profile-lock-order"
        assert committed_submission["state"] == "enqueue_failed"
        assert committed_submission["rejection_code"] == "queue_enqueue_failed"
        assert calls == [
            "user_lock",
            "principal",
            "profile_lock",
            "skill_auth",
            "workspace_auth",
            "file_auth",
            "claim",
            "create_session",
            "create_run",
            "profile_reauth",
            "enqueue",
            "terminalize_run",
            "terminalize_submission",
        ]
        assert len(enqueue_payloads) == 1
        assert published_payloads == []
        return

    response = await chat_stream(
        chat_request,
        agent_id=query_agent_id,
        principal=principal(),
    )

    assert response.session_id == "ses-profile-lock-order"
    assert response.run_id == "run-profile-lock-order"
    assert response.submission_id is not None
    if submission_id is not None:
        assert response.submission_id == submission_id
    initial_calls = list(calls)
    expected_calls = [
        "user_lock",
        "principal",
        "profile_lock",
    ]
    expected_calls.extend(["skill_auth", "workspace_auth", "file_auth", "claim"])
    if restored_continuation:
        expected_calls.insert(expected_calls.index("workspace_auth"), "list_reusable_files")
    if not restored_continuation:
        expected_calls.append("create_session")
    expected_calls.extend(["create_run", "profile_reauth", "enqueue"])
    assert initial_calls == expected_calls
    if restored_continuation:
        assert "session" not in persisted
    else:
        assert persisted["session"]["admitted_agent_profile_revision"] == 7
        assert persisted["session"]["admitted_agent_profile_hash"] == "a" * 64
    assert persisted["run"]["admitted_agent_profile_revision"] == 7
    assert persisted["run"]["admitted_agent_profile_hash"] == "a" * 64
    assert persisted["run"]["input_json"]["file_ids"] == (
        ["file_profile_history"]
        if restored_continuation
        else ["file_profile_context"]
    )
    assert persisted["run"]["input_json"]["agent_profile"] == {
        "agent_id": "agt_support",
        "revision": 7,
        "content_hash": "a" * 64,
        "instructions": "private profile instructions",
        "skill_set": [
            {
                "skill_id": "profile-specialist",
                "expected_version": profile_manifest["content_hash"],
            },
            {
                "skill_id": "profile-reference-search",
                "expected_version": secondary_profile_manifest["content_hash"],
            },
        ],
    }
    assert {
        manifest["skill_id"]
        for manifest in persisted["run"]["input_json"]["skill_manifests"]
    } == {"profile-specialist", "profile-reference-search"}
    if enqueue_failure_mode is None:
        assert response.status == "queued"
        assert len(enqueue_payloads) == 1
        assert published_payloads == enqueue_payloads
        return

    assert response.status == "accepted_pending_enqueue"
    assert committed_submission is not None
    assert committed_submission["state"] == "accepted_pending_enqueue"
    generated_or_client_submission_id = response.submission_id
    queue_readback_available = True
    recovered = await _admit_chat_submission(
        principal=principal(),
        submission_id=generated_or_client_submission_id,
    )
    duplicate = await _admit_chat_submission(
        principal=principal(),
        submission_id=generated_or_client_submission_id,
    )

    assert recovered.state == "queued"
    assert duplicate.state == "queued"
    if enqueue_failure_mode == "before_publish":
        assert len(enqueue_payloads) == 2
        assert enqueue_payloads[0] == enqueue_payloads[1]
    else:
        assert len(enqueue_payloads) == 1


@pytest.mark.asyncio
async def test_concurrent_profile_submits_serialize_on_user_lock_before_profile_admission(monkeypatch):
    """Mock transaction ordering only; PostgreSQL lock contention remains opt-in coverage."""

    from app.models import SelectedAgentProfileRequest

    user_lock = asyncio.Lock()
    holders: set[int] = set()
    first_profile_entered = asyncio.Event()
    release_first_profile = asyncio.Event()
    second_lock_attempted = asyncio.Event()
    profile_entries = 0
    active_profile_admissions = 0
    max_active_profile_admissions = 0

    @asynccontextmanager
    async def serialized_transaction():
        conn = object()
        try:
            yield conn
        finally:
            if id(conn) in holders:
                holders.remove(id(conn))
                user_lock.release()

    async def admission_lock(conn, *_args, **_kwargs):
        if user_lock.locked():
            second_lock_attempted.set()
        await user_lock.acquire()
        holders.add(id(conn))

    async def profile_admission(*_args, **_kwargs):
        nonlocal profile_entries, active_profile_admissions, max_active_profile_admissions
        profile_entries += 1
        active_profile_admissions += 1
        max_active_profile_admissions = max(max_active_profile_admissions, active_profile_admissions)
        try:
            if profile_entries == 1:
                first_profile_entered.set()
                await release_first_profile.wait()
            raise HTTPException(status_code=409, detail="agent_profile_not_available")
        finally:
            active_profile_admissions -= 1

    monkeypatch.setattr("app.routes.chat.transaction", serialized_transaction)
    monkeypatch.setattr(
        "app.routes.chat.repositories.acquire_user_active_run_admission_lock",
        admission_lock,
        raising=False,
    )
    monkeypatch.setattr("app.routes.chat.resolve_profile_for_admission", profile_admission)
    request = ChatStreamRequest(
        message="run the selected Agent",
        selected_agent_profile=SelectedAgentProfileRequest(
            agent_id="agt_support",
            expected_revision=7,
        ),
    )

    first = asyncio.create_task(chat_stream(request, principal=principal()))
    await asyncio.wait_for(first_profile_entered.wait(), timeout=1)
    second = asyncio.create_task(chat_stream(request, principal=principal()))
    await asyncio.wait_for(second_lock_attempted.wait(), timeout=1)
    await asyncio.sleep(0)
    assert profile_entries == 1
    release_first_profile.set()
    outcomes = await asyncio.gather(first, second, return_exceptions=True)

    assert all(isinstance(outcome, HTTPException) for outcome in outcomes)
    assert profile_entries == 2
    assert max_active_profile_admissions == 1


@pytest.mark.asyncio
async def test_profile_secondary_skill_denial_is_audited_after_transaction_rollback(monkeypatch):
    from app.models import SelectedAgentProfileRequest

    denial = CapabilityAuthorizationDenial(
        capability_kind="skill",
        capability_id="secondary-skill",
        actor_department_id="quality",
        actor_roles=("user",),
        department_scope_ids=("finance",),
        role_scope_ids=(),
        scope_mode="allowlist",
        decision_reason="department_scope_denied",
        admin_bypass=False,
    )
    audited: list[tuple[str, str]] = []

    async def admission_lock(*_args, **_kwargs):
        return None

    async def deny_profile(*_args, **_kwargs):
        try:
            raise repository_module.RepositoryAuthorizationError(
                "capability_not_authorized",
                denial=denial,
            )
        except repository_module.RepositoryAuthorizationError as exc:
            raise HTTPException(
                status_code=403,
                detail="agent_profile_capability_not_available",
            ) from exc

    async def record_audit(_principal, error, *, source):
        audited.append((source, error.denial.capability_id))

    monkeypatch.setattr("app.routes.chat.transaction", fake_transaction)
    monkeypatch.setattr(
        "app.routes.chat.repositories.acquire_user_active_run_admission_lock",
        admission_lock,
        raising=False,
    )
    monkeypatch.setattr("app.routes.chat.resolve_profile_for_admission", deny_profile)
    monkeypatch.setattr("app.routes.chat._audit_capability_denial", record_audit)

    with pytest.raises(HTTPException) as caught:
        await chat_stream(
            ChatStreamRequest(
                message="run the selected Agent",
                selected_agent_profile=SelectedAgentProfileRequest(
                    agent_id="agt_support",
                    expected_revision=7,
                ),
            ),
            principal=principal(department_id="quality", roles=["user"]),
        )

    assert caught.value.status_code == 403
    assert caught.value.detail == "agent_profile_capability_not_available"
    assert audited == [("chat_stream", "secondary-skill")]


@pytest.mark.asyncio
async def test_first_selector_free_profile_submit_keeps_the_persisted_non_general_skill(monkeypatch):
    from app.agent_apps import AgentProfileAdmission
    from app.models import AgentConversationIdentity

    calls: list[object] = []
    pinned_session = {
        "id": "ses_profile_first",
        "workspace_id": "workspace-owned",
        "agent_id": "agt_support",
        "admitted_agent_profile_revision": 7,
        "admitted_agent_profile_hash": "a" * 64,
    }

    async def owned_session(*_args, **_kwargs):
        return pinned_session

    async def admission_lock(*_args, **_kwargs):
        calls.append("user_lock")

    async def ensure_principal(*_args, **_kwargs):
        calls.append("principal")

    async def bound_profile(*_args, **kwargs):
        calls.append("bound_profile")
        submitted_request = kwargs["submitted_request"]
        assert kwargs["query_agent_id"] == "agt_support"
        assert submitted_request.session_id == "ses_profile_first"
        assert submitted_request.agent_options == {"enable_thinking": "off"}
        assert submitted_request.disabled_skills == []
        assert submitted_request.selected_mcp_tool_ids == []
        return AgentProfileAdmission(
            agent_id="agt_support",
            revision=7,
            content_hash="a" * 64,
            skill={
                "skill_id": "profile-specialist",
                "skill_version": "version-profile",
                "executor_type": "claude-agent-worker",
            },
            model={"id": "model-a", "value": "provider-model-a"},
            mcp_tool_ids=(),
            private_execution_input={
                "agent_id": "agt_support",
                "revision": 7,
                "content_hash": "a" * 64,
                "instructions": "private",
            },
            public_identity=AgentConversationIdentity(
                agent_id="agt_support",
                revision=7,
                name="Support assistant",
            ),
        )

    async def forbidden_prior_run(*_args, **_kwargs):
        raise AssertionError("a persisted Agent profile, not a prior run, owns the first Skill")

    async def authorize_selected(*_args, **kwargs):
        calls.append(
            (
                "authorize",
                kwargs["agent_id"],
                kwargs["skill_id"],
                kwargs["expected_version"],
            )
        )
        raise HTTPException(status_code=418, detail="captured_profile_skill")

    async def forbidden_submission_claim(*_args, **_kwargs):
        raise AssertionError("a denied unkeyed Agent request must not mint a recovery key")

    async def authorize_transport_mcp_defaults(*_args, **_kwargs):
        return None

    monkeypatch.setattr("app.routes.chat.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.chat.repositories.get_authorized_session", owned_session)
    monkeypatch.setattr(
        "app.routes.chat.repositories.acquire_user_active_run_admission_lock",
        admission_lock,
        raising=False,
    )
    monkeypatch.setattr(
        "app.routes.chat.repositories.ensure_submission_principal",
        ensure_principal,
        raising=False,
    )
    monkeypatch.setattr("app.routes.chat.resolve_bound_profile_for_submission", bound_profile)
    monkeypatch.setattr(
        "app.routes.chat.repositories.authorize_selected_chat_mcp_tools",
        authorize_transport_mcp_defaults,
    )
    monkeypatch.setattr(
        "app.routes.chat.repositories.list_authorized_session_runs",
        forbidden_prior_run,
    )
    monkeypatch.setattr(
        "app.routes.chat.repositories.authorize_selected_run_capabilities",
        authorize_selected,
    )
    monkeypatch.setattr(
        "app.routes.chat.repositories.claim_chat_submission",
        forbidden_submission_claim,
    )

    with pytest.raises(HTTPException) as caught:
        await chat_stream(
            ChatStreamRequest.model_validate(
                {
                    "message": "run the pinned specialist",
                    "session_id": "ses_profile_first",
                    "agent_options": {"enable_thinking": "off"},
                    "disabled_skills": [],
                    "selected_mcp_tool_ids": [],
                    "user_timezone": "Asia/Shanghai",
                }
            ),
            agent_id="agt_support",
            principal=principal(),
        )

    assert caught.value.detail == "captured_profile_skill"
    assert calls == [
        "user_lock",
        "principal",
        "bound_profile",
        ("authorize", "agt_support", "profile-specialist", "version-profile"),
    ]


@pytest.mark.asyncio
async def test_chat_stream_rejects_a_rotated_principal_stale_session_before_capability_or_persistence(monkeypatch):
    """A post-login principal must never reuse another principal's supplied session id."""

    checked = []

    async def no_owned_session(conn, *, tenant_id, user_id, session_id, workspace_id=None, for_update=False):
        checked.append((tenant_id, user_id, session_id, workspace_id, for_update))

    async def forbidden_after_ownership_check(*_args, **_kwargs):
        raise AssertionError("foreign session must fail before capability or persistence work")

    monkeypatch.setattr("app.routes.chat.transaction", fake_transaction)
    monkeypatch.setattr(
        "app.routes.chat.repositories.get_authorized_session",
        no_owned_session,
    )
    monkeypatch.setattr(
        "app.routes.chat.repositories.authorize_run_capabilities",
        forbidden_after_ownership_check,
    )
    monkeypatch.setattr(
        "app.routes.chat.repositories.create_session",
        forbidden_after_ownership_check,
    )

    with pytest.raises(HTTPException) as exc_info:
        await chat_stream(
            ChatStreamRequest(
                message="普通后续请求",
                session_id="ses_previous_principal",
            ),
            agent_id="general-agent",
            principal=principal(user_id="ordinary-user-b", tenant_id="tenant-b"),
        )

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "session_not_found"
    assert checked == [("tenant-b", "ordinary-user-b", "ses_previous_principal", None, False)]


@pytest.mark.asyncio
async def test_chat_stream_rejects_a_continuation_workspace_mismatch_before_routing(monkeypatch):
    """A client may not move an owned session into another workspace after login."""

    calls = []

    async def owned_session(conn, *, tenant_id, user_id, session_id, workspace_id=None, for_update=False):
        calls.append(("session", tenant_id, user_id, session_id, workspace_id, for_update))
        return {
            "id": session_id,
            "agent_id": "general-agent",
            "workspace_id": "workspace-owned",
        }

    async def forbidden_after_workspace_check(*_args, **_kwargs):
        raise AssertionError("workspace mismatch must fail before routing or persistence")

    async def forbidden_prior_skill_lookup(*_args, **_kwargs):
        raise AssertionError("workspace mismatch must not inspect another workspace's prior Skill")

    monkeypatch.setattr("app.routes.chat.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.chat.repositories.get_authorized_session", owned_session)
    monkeypatch.setattr(
        "app.routes.chat.repositories.list_authorized_session_runs",
        forbidden_prior_skill_lookup,
    )
    monkeypatch.setattr("app.routes.chat.repositories.authorize_run_capabilities", forbidden_after_workspace_check)
    monkeypatch.setattr("app.routes.chat.repositories.create_session", forbidden_after_workspace_check)

    with pytest.raises(HTTPException) as exc_info:
        await chat_stream(
            ChatStreamRequest(
                message="普通后续请求",
                session_id="ses_owned",
                workspace_id="workspace-other",
            ),
            agent_id="general-agent",
            principal=principal(),
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "session_workspace_mismatch"
    assert calls == [("session", "tenant-a", "user-a", "ses_owned", None, False)]


@pytest.mark.asyncio
async def test_chat_stream_word_translate_file_id_routes_from_general_agent(monkeypatch):
    calls = []

    async def fake_get_file(conn, *, tenant_id, file_id):
        calls.append(("get_file", tenant_id, file_id))
        return {
            "id": file_id,
            "tenant_id": "tenant-a",
            "workspace_id": "default",
            "user_id": "user-a",
            "session_id": None,
            "run_id": None,
            "original_name": "demo.docx",
            "content_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        }

    async def fake_resolve_agent_skill(conn, *, tenant_id, agent_id, skill_id):
        calls.append(("resolve", agent_id, skill_id))
        return {"executor_type": "claude-agent-worker", "skill_version": "0.1.0", "input_modes": ["docx"]}

    async def fake_create_run(conn, **kwargs):
        calls.append(("run", kwargs["agent_id"], kwargs["skill_id"], kwargs["input_json"]["file_ids"]))
        return "run_translate_file_id"

    async def noop(*args, **kwargs):
        return None

    async def fake_create_session(conn, **kwargs):
        calls.append(("session", kwargs["agent_id"]))
        return "ses_translate_file_id"

    async def fake_enqueue_run(payload):
        calls.append(("queue", payload["agent_id"], payload["skill_id"], payload["file_ids"]))
        return 1

    monkeypatch.setattr("app.routes.chat.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.chat.repositories.get_file", fake_get_file)
    monkeypatch.setattr("app.routes.chat.repositories.resolve_agent_skill", fake_resolve_agent_skill)
    monkeypatch.setattr("app.routes.chat.repositories.ensure_user", noop)
    monkeypatch.setattr("app.routes.chat.repositories.create_session", fake_create_session)
    monkeypatch.setattr("app.routes.chat.repositories.create_run", fake_create_run)
    monkeypatch.setattr("app.routes.chat.repositories.append_message", noop)
    monkeypatch.setattr("app.routes.chat.repositories.bind_files_to_run", noop)
    monkeypatch.setattr("app.routes.chat.repositories.append_event", noop)
    monkeypatch.setattr("app.routes.chat.enqueue_run", fake_enqueue_run)

    response = await chat_stream(
        ChatStreamRequest(message="translate this Word file", file_ids=["file_word_translate"]),
        agent_id="general-agent",
        principal=principal(),
    )

    assert response.run_id == "run_translate_file_id"
    assert ("get_file", "tenant-a", "file_word_translate") in calls
    assert ("resolve", "baoyu-translate", "baoyu-translate") in calls
    assert ("run", "baoyu-translate", "baoyu-translate", ["file_word_translate"]) in calls
    assert ("queue", "baoyu-translate", "baoyu-translate", ["file_word_translate"]) in calls


@pytest.mark.asyncio
async def test_lambchat_txt_attachment_stays_on_general_chat(monkeypatch):
    calls = []

    async def fake_resolve_agent_skill(conn, *, tenant_id, agent_id, skill_id):
        calls.append(("resolve", agent_id, skill_id))
        return {"executor_type": "claude-agent-worker", "skill_version": "0.1.0", "input_modes": ["chat"]}

    async def fake_create_run(conn, **kwargs):
        calls.append(("run", kwargs["agent_id"], kwargs["skill_id"], kwargs["input_json"]["file_ids"]))
        return "run_txt"

    async def noop(*args, **kwargs):
        return None

    async def fake_create_session(conn, **kwargs):
        calls.append(("session", kwargs["agent_id"]))
        return "ses_txt"

    async def fake_bind_files_to_run(conn, **kwargs):
        calls.append(("files", kwargs["file_ids"]))

    async def fake_enqueue_run(payload):
        calls.append(("queue", payload["agent_id"], payload["skill_id"], payload["file_ids"]))
        return 1

    monkeypatch.setattr("app.routes.chat.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.chat.repositories.resolve_agent_skill", fake_resolve_agent_skill)
    monkeypatch.setattr("app.routes.chat.repositories.ensure_user", noop)
    monkeypatch.setattr("app.routes.chat.repositories.create_session", fake_create_session)
    monkeypatch.setattr("app.routes.chat.repositories.create_run", fake_create_run)
    monkeypatch.setattr("app.routes.chat.repositories.append_message", noop)
    monkeypatch.setattr("app.routes.chat.repositories.bind_files_to_run", fake_bind_files_to_run)
    monkeypatch.setattr("app.routes.chat.repositories.append_event", noop)
    monkeypatch.setattr("app.routes.chat.enqueue_run", fake_enqueue_run)

    response = await chat_stream(
        ChatStreamRequest(
            message="请总结这个文本附件",
            attachments=[{"key": "file_txt", "name": "notes.txt", "mimeType": "text/plain"}],
        ),
        agent_id="general-agent",
        principal=principal(),
    )

    assert response.run_id == "run_txt"
    assert not any(
        item == ("resolve", "general-agent", "general-chat") for item in calls
    )
    assert ("run", "general-agent", None, ["file_txt"]) in calls
    assert ("queue", "general-agent", None, ["file_txt"]) in calls


@pytest.mark.asyncio
async def test_lambchat_word_translate_attachment_routes_from_general_agent(monkeypatch):
    calls = []

    async def fake_resolve_agent_skill(conn, *, tenant_id, agent_id, skill_id):
        calls.append(("resolve", agent_id, skill_id))
        return {"executor_type": "claude-agent-worker", "skill_version": "0.1.0", "input_modes": ["docx"]}

    async def fake_create_run(conn, **kwargs):
        calls.append(("run", kwargs["agent_id"], kwargs["skill_id"], kwargs["input_json"]["file_ids"]))
        return "run_translate_inferred"

    async def noop(*args, **kwargs):
        return None

    async def fake_create_session(conn, **kwargs):
        calls.append(("session", kwargs["agent_id"]))
        return "ses_translate_inferred"

    async def fake_enqueue_run(payload):
        calls.append(("queue", payload["agent_id"], payload["skill_id"], payload["file_ids"]))
        return 1

    monkeypatch.setattr("app.routes.chat.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.chat.repositories.resolve_agent_skill", fake_resolve_agent_skill)
    monkeypatch.setattr("app.routes.chat.repositories.ensure_user", noop)
    monkeypatch.setattr("app.routes.chat.repositories.create_session", fake_create_session)
    monkeypatch.setattr("app.routes.chat.repositories.create_run", fake_create_run)
    monkeypatch.setattr("app.routes.chat.repositories.append_message", noop)
    monkeypatch.setattr("app.routes.chat.repositories.bind_files_to_run", noop)
    monkeypatch.setattr("app.routes.chat.repositories.append_event", noop)
    monkeypatch.setattr("app.routes.chat.enqueue_run", fake_enqueue_run)

    response = await chat_stream(
        ChatStreamRequest(
            message="translate this Word file",
            attachments=[
                {
                    "key": "file_word_translate",
                    "name": "demo.docx",
                    "mimeType": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                }
            ],
        ),
        agent_id="general-agent",
        principal=principal(),
    )

    assert response.run_id == "run_translate_inferred"
    assert ("resolve", "baoyu-translate", "baoyu-translate") in calls
    assert ("queue", "baoyu-translate", "baoyu-translate", ["file_word_translate"]) in calls


@pytest.mark.asyncio
async def test_chat_stream_returns_suggestions_for_ambiguous_docx_without_creating_run(monkeypatch):
    calls = []

    async def admission_lock(conn, *, tenant_id, user_id):
        calls.append(("admission_lock", tenant_id, user_id))

    async def forbidden_limit_check(*_args, **_kwargs):
        raise AssertionError("needs_confirmation must precede active-run limit rejection")

    async def fail_resolve_agent_skill(*args, **kwargs):
        calls.append("resolve")
        raise AssertionError("ambiguous request must not resolve skill")

    async def fail_create_run(*args, **kwargs):
        calls.append("create_run")
        raise AssertionError("ambiguous request must not create run")

    async def fail_enqueue_run(payload):
        calls.append("enqueue")
        raise AssertionError("ambiguous request must not enqueue run")

    async def all_principal_agents(conn, **kwargs):
        return [
            {"id": "qa-word-review", "default_skill_id": "qa-file-reviewer"},
            {"id": "baoyu-translate", "default_skill_id": "baoyu-translate"},
            {"id": "general-agent", "default_skill_id": "general-chat"},
        ]

    monkeypatch.setattr("app.routes.chat.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.chat.repositories.resolve_agent_skill", fail_resolve_agent_skill)
    monkeypatch.setattr("app.routes.chat.repositories.create_run", fail_create_run)
    monkeypatch.setattr("app.routes.chat.enqueue_run", fail_enqueue_run)
    monkeypatch.setattr(
        "app.routes.chat.repositories.acquire_user_active_run_admission_lock",
        admission_lock,
        raising=False,
    )
    monkeypatch.setattr(
        "app.routes.chat.repositories.enforce_user_active_run_admission_under_lock",
        forbidden_limit_check,
        raising=False,
    )
    monkeypatch.setattr(
        "app.routes.chat.repositories.list_principal_lambchat_agents",
        all_principal_agents,
        raising=False,
    )

    response = await chat_stream(
        ChatStreamRequest(
            message="处理一下这个文件",
            attachments=[{"key": "file_docx", "name": "demo.docx"}],
        ),
        principal=principal(),
    )

    assert response.status == "needs_confirmation"
    assert response.run_id is None
    assert [item.capability_id for item in response.suggestions] == [
        "document_review",
        "document_translation",
        "general_chat",
    ]
    assert calls == [("admission_lock", "tenant-a", "user-a")]


@pytest.mark.asyncio
async def test_chat_stream_filters_confirmation_suggestions_through_principal_projection(monkeypatch):
    calls = []

    async def principal_agents(conn, **kwargs):
        calls.append(kwargs)
        return [
            {"id": "baoyu-translate", "default_skill_id": "baoyu-translate"},
            {"id": "general-agent", "default_skill_id": "general-chat"},
        ]

    monkeypatch.setattr("app.routes.chat.transaction", fake_transaction)
    monkeypatch.setattr(
        "app.routes.chat.repositories.list_principal_lambchat_agents",
        principal_agents,
        raising=False,
    )

    response = await chat_stream(
        ChatStreamRequest(
            message="处理一下这个文件",
            attachments=[{"key": "file_docx", "name": "demo.docx"}],
        ),
        principal=principal(department_id="QA", roles=["QA-OPERATOR"]),
    )

    assert response.status == "needs_confirmation"
    assert [item.capability_id for item in response.suggestions] == [
        "document_translation",
        "general_chat",
    ]
    assert [item.capability_id for item in response.intent_decision.suggestions] == [
        "document_translation",
        "general_chat",
    ]
    assert calls == [
        {
            "tenant_id": "tenant-a",
            "actor_user_id": "user-a",
            "department_id": "QA",
            "roles": ["QA-OPERATOR"],
            "is_admin": False,
            "permissions": [],
        }
    ]


@pytest.mark.asyncio
async def test_chat_stream_falls_back_to_general_chat_when_implicit_knowledge_admission_fails(monkeypatch):
    """An unavailable implicit route must not expose or execute its capability."""

    calls = []

    async def fake_resolve_agent_skill(conn, *, tenant_id, agent_id, skill_id):
        calls.append(("resolve", agent_id, skill_id))
        if (agent_id, skill_id) == ("sop-assistant", "ragflow-knowledge-search"):
            raise repository_module.RepositoryAuthorizationError("capability_not_authorized")
        assert (agent_id, skill_id) == ("general-agent", "general-chat")
        return {"executor_type": "claude-agent-worker", "skill_version": "0.1.0", "input_modes": ["chat"]}

    async def fake_create_session(conn, **kwargs):
        return "ses_implicit_fallback"

    async def fake_create_run(conn, **kwargs):
        calls.append(("run", kwargs["agent_id"], kwargs["skill_id"]))
        return "run_implicit_fallback"

    async def noop(*args, **kwargs):
        return None

    async def fake_enqueue_run(payload):
        calls.append(("queue", payload["agent_id"], payload["skill_id"]))
        return 1

    monkeypatch.setattr("app.routes.chat.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.chat.repositories.resolve_agent_skill", fake_resolve_agent_skill)
    monkeypatch.setattr("app.routes.chat.repositories.ensure_user", noop)
    monkeypatch.setattr("app.routes.chat.repositories.create_session", fake_create_session)
    monkeypatch.setattr("app.routes.chat.repositories.create_run", fake_create_run)
    monkeypatch.setattr("app.routes.chat.repositories.append_message", noop)
    monkeypatch.setattr("app.routes.chat.repositories.bind_files_to_run", noop)
    monkeypatch.setattr("app.routes.chat.repositories.append_event", noop)
    monkeypatch.setattr("app.routes.chat.enqueue_run", fake_enqueue_run)

    response = await chat_stream(
        ChatStreamRequest(message="这个权限申请要怎么做？"),
        principal=principal(),
    )

    assert response.status == "queued"
    assert response.intent_decision is not None
    assert response.intent_decision.selected_capability == "general_chat"
    assert response.intent_decision.reason == "已使用通用对话处理"
    assert "ragflow-knowledge-search" not in response.intent_decision.model_dump_json()
    assert ("resolve", "sop-assistant", "ragflow-knowledge-search") in calls
    assert ("resolve", "general-agent", "general-chat") not in calls
    assert ("run", "general-agent", None) in calls
    assert ("queue", "general-agent", None) in calls


@pytest.mark.asyncio
async def test_chat_stream_keeps_implicit_knowledge_intent_when_rag_admission_succeeds(monkeypatch):
    """Authorized RAG remains the selected implicit knowledge capability."""

    calls = []

    async def fake_resolve_agent_skill(conn, *, tenant_id, agent_id, skill_id):
        calls.append(("resolve", agent_id, skill_id))
        assert (agent_id, skill_id) == ("sop-assistant", "ragflow-knowledge-search")
        return {"executor_type": "claude-agent-worker", "skill_version": "0.1.0", "input_modes": ["chat"]}

    async def fake_create_session(conn, **kwargs):
        return "ses_implicit_rag"

    async def fake_create_run(conn, **kwargs):
        return "run_implicit_rag"

    async def noop(*args, **kwargs):
        return None

    async def fake_enqueue_run(payload):
        return 1

    monkeypatch.setattr("app.routes.chat.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.chat.repositories.resolve_agent_skill", fake_resolve_agent_skill)
    monkeypatch.setattr("app.routes.chat.repositories.ensure_user", noop)
    monkeypatch.setattr("app.routes.chat.repositories.create_session", fake_create_session)
    monkeypatch.setattr("app.routes.chat.repositories.create_run", fake_create_run)
    monkeypatch.setattr("app.routes.chat.repositories.append_message", noop)
    monkeypatch.setattr("app.routes.chat.repositories.bind_files_to_run", noop)
    monkeypatch.setattr("app.routes.chat.repositories.append_event", noop)
    monkeypatch.setattr("app.routes.chat.enqueue_run", fake_enqueue_run)

    response = await chat_stream(
        ChatStreamRequest(message="这个权限申请要怎么做？"),
        principal=principal(),
    )

    assert response.status == "queued"
    assert response.intent_decision is not None
    assert response.intent_decision.selected_capability == "knowledge_answer"
    assert ("resolve", "sop-assistant", "ragflow-knowledge-search") in calls


@pytest.mark.asyncio
async def test_chat_stream_fails_closed_when_implicit_knowledge_intent_has_no_safe_fallback(
    monkeypatch,
):
    """A missing Harness chat agent must not silently reroute a denied intent."""

    calls = []

    async def deny(conn, **kwargs):
        calls.append(("authorize", kwargs["agent_id"], kwargs["skill_id"]))
        raise repository_module.RepositoryAuthorizationError("capability_not_authorized")

    async def fail_create_run(*args, **kwargs):
        raise AssertionError("denied implicit routing must not create a run")

    async def missing_harness_agent(*_args, **_kwargs):
        return None

    monkeypatch.setattr("app.routes.chat.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.chat.repositories.authorize_run_capabilities", deny)
    monkeypatch.setattr("app.routes.chat.repositories.get_agent", missing_harness_agent)
    monkeypatch.setattr("app.routes.chat.repositories.create_run", fail_create_run)

    with pytest.raises(HTTPException) as exc_info:
        await chat_stream(
            ChatStreamRequest(message="这个权限申请要怎么做？"),
            principal=principal(),
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "harness_chat_agent_unavailable"
    assert calls == [
        ("authorize", "sop-assistant", "ragflow-knowledge-search"),
    ]


@pytest.mark.asyncio
async def test_chat_stream_admin_implicit_disabled_knowledge_falls_back_with_strict_admission(monkeypatch):
    """Implicit routing must not use the administrator distribution bypass."""

    calls = []

    async def authorize(conn, **kwargs):
        calls.append((kwargs["agent_id"], kwargs["skill_id"], kwargs["is_admin"]))
        assert kwargs["is_admin"] is False
        if kwargs["skill_id"] == "ragflow-knowledge-search":
            raise repository_module.RepositoryAuthorizationError("capability_not_authorized")
        return {"executor_type": "claude-agent-worker", "skill_version": "0.1.0", "input_modes": ["chat"]}

    async def noop(*args, **kwargs):
        return None

    async def fake_create_session(conn, **kwargs):
        return "ses_admin_implicit_fallback"

    async def fake_create_run(conn, **kwargs):
        return "run_admin_implicit_fallback"

    async def fake_enqueue_run(payload):
        return 1

    monkeypatch.setattr("app.routes.chat.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.chat.repositories.authorize_run_capabilities", authorize)
    monkeypatch.setattr("app.routes.chat.repositories.ensure_user", noop)
    monkeypatch.setattr("app.routes.chat.repositories.create_session", fake_create_session)
    monkeypatch.setattr("app.routes.chat.repositories.create_run", fake_create_run)
    monkeypatch.setattr("app.routes.chat.repositories.append_message", noop)
    monkeypatch.setattr("app.routes.chat.repositories.bind_files_to_run", noop)
    monkeypatch.setattr("app.routes.chat.repositories.append_event", noop)
    monkeypatch.setattr("app.routes.chat.enqueue_run", fake_enqueue_run)

    response = await chat_stream(
        ChatStreamRequest(message="这个权限申请要怎么做？"),
        principal=principal(roles=["admin"]),
    )

    assert response.status == "queued"
    assert response.intent_decision is not None
    assert response.intent_decision.selected_capability == "general_chat"
    assert "ragflow-knowledge-search" not in response.intent_decision.model_dump_json()
    assert calls == [
        ("sop-assistant", "ragflow-knowledge-search", False),
    ]


@pytest.mark.asyncio
async def test_chat_stream_implicit_rag_backing_mcp_failure_falls_back_to_general_chat(monkeypatch):
    """The candidate admission includes backing MCP/server health before selection."""

    calls = []

    async def authorize(conn, **kwargs):
        calls.append((kwargs["agent_id"], kwargs["skill_id"], kwargs["is_admin"]))
        if kwargs["skill_id"] == "ragflow-knowledge-search":
            return await _ORIGINAL_AUTHORIZE_RUN_CAPABILITIES(conn, **kwargs)
        return {"executor_type": "claude-agent-worker", "skill_version": "0.1.0", "input_modes": ["chat"]}

    async def resolve_agent_skill(conn, *, tenant_id, agent_id, skill_id):
        assert (tenant_id, agent_id, skill_id) == (
            "tenant-a",
            "sop-assistant",
            "ragflow-knowledge-search",
        )
        return {
            "skill_id": skill_id,
            "skill_status": "active",
            "executor_type": "ragflow",
            "backing_mcp_tool_id": "ragflow::ragflow_search",
        }

    async def get_distribution(conn, *, tenant_id, capability_kind, capability_id):
        return {
            "status": "active",
            "visible_to_user": True,
            "scope_mode": "allowlist",
            "department_ids": [],
            "allowed_roles": [],
        }

    async def get_tool(conn, *, tenant_id, tool_id):
        assert (tenant_id, tool_id) == ("tenant-a", "ragflow::ragflow_search")
        return {
            "tool_id": tool_id,
            "server_id": "ragflow",
            "effective_status": "active",
            "server_status": "disabled",
            "visible_to_user": True,
        }

    async def noop(*args, **kwargs):
        return None

    async def fake_create_session(conn, **kwargs):
        return "ses_mcp_fallback"

    async def fake_create_run(conn, **kwargs):
        return "run_mcp_fallback"

    async def fake_enqueue_run(payload):
        return 1

    monkeypatch.setattr("app.routes.chat.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.chat.repositories.authorize_run_capabilities", authorize)
    monkeypatch.setattr("app.routes.chat.repositories.resolve_agent_skill", resolve_agent_skill)
    monkeypatch.setattr("app.routes.chat.repositories.get_capability_distribution_row", get_distribution)
    monkeypatch.setattr("app.routes.chat.repositories.get_mcp_tool_registry_entry", get_tool)
    monkeypatch.setattr("app.routes.chat.repositories.ensure_user", noop)
    monkeypatch.setattr("app.routes.chat.repositories.create_session", fake_create_session)
    monkeypatch.setattr("app.routes.chat.repositories.create_run", fake_create_run)
    monkeypatch.setattr("app.routes.chat.repositories.append_message", noop)
    monkeypatch.setattr("app.routes.chat.repositories.bind_files_to_run", noop)
    monkeypatch.setattr("app.routes.chat.repositories.append_event", noop)
    monkeypatch.setattr("app.routes.chat.enqueue_run", fake_enqueue_run)

    response = await chat_stream(
        ChatStreamRequest(message="这个权限申请要怎么做？"),
        principal=principal(department_id="qa", roles=["qa_operator"]),
    )

    assert response.status == "queued"
    assert response.intent_decision is not None
    assert response.intent_decision.selected_capability == "general_chat"
    assert calls == [
        ("sop-assistant", "ragflow-knowledge-search", False),
    ]


@pytest.mark.asyncio
async def test_chat_stream_never_suggests_archived_default_skill_from_principal_projection(monkeypatch):
    async def fake_list_agents(conn, *, tenant_id):
        assert tenant_id == "tenant-a"
        return [
            {"id": "baoyu-translate", "default_skill_id": "baoyu-translate", "status": "active"},
            {"id": "general-agent", "default_skill_id": "general-chat", "status": "active"},
            {"id": "qa-word-review", "default_skill_id": "qa-file-reviewer", "status": "active"},
        ]

    async def fake_list_distributions(conn, **kwargs):
        return [
            {
                "capability_kind": "skill",
                "capability_id": "baoyu-translate",
                "status": "active",
                "visible_to_user": True,
                "scope_mode": "allowlist",
                "department_ids": [],
                "allowed_roles": [],
                "metadata_json": {},
            },
            {
                "capability_kind": "skill",
                "capability_id": "general-chat",
                "status": "active",
                "visible_to_user": True,
                "scope_mode": "allowlist",
                "department_ids": [],
                "allowed_roles": [],
                "metadata_json": {},
            },
            {
                "capability_kind": "skill",
                "capability_id": "qa-file-reviewer",
                "status": "disabled",
                "visible_to_user": False,
                "scope_mode": "allowlist",
                "department_ids": [],
                "allowed_roles": [],
                "metadata_json": {"archived_at": "2026-07-15T00:00:00.000Z"},
            },
        ]

    async def fake_append_audit(conn, **kwargs):
        return "audit"

    monkeypatch.setattr("app.routes.chat.transaction", fake_transaction)
    monkeypatch.setattr(repository_module, "list_lambchat_agents", fake_list_agents)
    monkeypatch.setattr(repository_module, "list_capability_distribution_rows", fake_list_distributions)
    monkeypatch.setattr(repository_module, "append_audit_log", fake_append_audit)

    response = await chat_stream(
        ChatStreamRequest(
            message="处理一下这个文件",
            attachments=[{"key": "file_docx", "name": "demo.docx"}],
        ),
        principal=principal(department_id="platform", roles=["admin"]),
    )

    assert response.status == "needs_confirmation"
    assert [item.capability_id for item in response.suggestions] == [
        "document_translation",
        "general_chat",
    ]


@pytest.mark.asyncio
async def test_chat_stream_records_intent_decision_and_confirmed_event(monkeypatch):
    events = []
    run_inputs = []

    async def fake_resolve_agent_skill(conn, *, tenant_id, agent_id, skill_id):
        assert agent_id == "qa-word-review"
        assert skill_id == "qa-file-reviewer"
        return {"executor_type": "claude-agent-worker", "skill_version": "0.1.0", "input_modes": ["docx"]}

    async def fake_create_session(conn, **kwargs):
        return "ses_confirmed"

    async def fake_create_run(conn, **kwargs):
        run_inputs.append(kwargs["input_json"])
        return "run_confirmed"

    async def noop(*args, **kwargs):
        return None

    async def fake_append_event(conn, **kwargs):
        events.append(kwargs["event_type"])
        return f"evt_{len(events)}"

    async def fake_enqueue_run(payload):
        return 1

    monkeypatch.setattr("app.routes.chat.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.chat.repositories.resolve_agent_skill", fake_resolve_agent_skill)
    monkeypatch.setattr("app.routes.chat.repositories.ensure_user", noop)
    monkeypatch.setattr("app.routes.chat.repositories.create_session", fake_create_session)
    monkeypatch.setattr("app.routes.chat.repositories.create_run", fake_create_run)
    monkeypatch.setattr("app.routes.chat.repositories.append_message", noop)
    monkeypatch.setattr("app.routes.chat.repositories.bind_files_to_run", noop)
    monkeypatch.setattr("app.routes.chat.repositories.append_event", fake_append_event)
    monkeypatch.setattr("app.routes.chat.enqueue_run", fake_enqueue_run)

    response = await chat_stream(
        ChatStreamRequest(
            message="处理一下这个文件",
            confirmed_capability_id="document_review",
            attachments=[{"key": "file_review", "name": "demo.docx"}],
        ),
        principal=principal(),
    )

    assert response.status == "queued"
    assert response.intent_decision.selected_capability == "document_review"
    assert response.intent_decision.confirmed_by_user is True
    assert run_inputs[0]["intent"]["selected_capability"] == "document_review"
    assert "intent_detected" in events
    assert "intent_confirmed" in events


@pytest.mark.asyncio
async def test_chat_stream_rejects_when_user_active_run_limit_is_reached(monkeypatch):
    calls = []

    async def fake_resolve_agent_skill(conn, *, tenant_id, agent_id, skill_id):
        calls.append("resolve")
        return {"executor_type": "claude-agent-worker", "skill_version": "0.1.0", "input_modes": ["chat"]}

    async def fake_acquire_user_active_run_admission_lock(conn, *, tenant_id, user_id):
        calls.append(("lock", tenant_id, user_id))

    async def fake_enforce_user_active_run_admission_under_lock(conn, *, tenant_id, user_id, limit):
        calls.append(("admit", tenant_id, user_id, limit))
        raise RepositoryConflictError("user_active_run_limit_exceeded")

    async def fail_create_session(*args, **kwargs):
        calls.append("create_session")
        raise AssertionError("session must not be created after admission rejection")

    class LimitSettings:
        max_active_runs_per_user = 3

    monkeypatch.setattr("app.routes.chat.get_settings", lambda: LimitSettings())
    monkeypatch.setattr("app.routes.chat.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.chat.repositories.resolve_agent_skill", fake_resolve_agent_skill)
    monkeypatch.setattr(
        "app.routes.chat.repositories.acquire_user_active_run_admission_lock",
        fake_acquire_user_active_run_admission_lock,
        raising=False,
    )
    monkeypatch.setattr(
        "app.routes.chat.repositories.enforce_user_active_run_admission_under_lock",
        fake_enforce_user_active_run_admission_under_lock,
        raising=False,
    )
    monkeypatch.setattr("app.routes.chat.repositories.create_session", fail_create_session)

    with pytest.raises(Exception) as exc_info:
        await chat_stream(
            ChatStreamRequest(message="hello"),
            principal=principal(user_id="user-limit", tenant_id="tenant-a"),
        )

    assert getattr(exc_info.value, "status_code", None) == 409
    assert getattr(exc_info.value, "detail", None) == "user_active_run_limit_exceeded"
    assert calls == [
        ("lock", "tenant-a", "user-limit"),
        ("admit", "tenant-a", "user-limit", 3),
    ]


@pytest.mark.asyncio
async def test_chat_stream_maps_unreleased_skill_version_conflict_to_409(monkeypatch):
    calls = []

    async def fake_resolve_agent_skill(conn, *, tenant_id, agent_id, skill_id):
        calls.append(("resolve", tenant_id, agent_id, skill_id))
        raise RepositoryConflictError("skill_version_not_released")

    async def fail_create_session(*args, **kwargs):
        calls.append("create_session")
        raise AssertionError("chat stream must not create a session for unreleased skill version")

    monkeypatch.setattr("app.routes.chat.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.chat.repositories.resolve_agent_skill", fake_resolve_agent_skill)
    monkeypatch.setattr("app.routes.chat.repositories.create_session", fail_create_session)

    with pytest.raises(Exception) as exc_info:
        await chat_stream(
            ChatStreamRequest(
                message="审核这个文档", confirmed_capability_id="document_review"
            ),
            principal=principal(user_id="user-skill-status", tenant_id="tenant-a"),
        )

    assert getattr(exc_info.value, "status_code", None) == 409
    assert getattr(exc_info.value, "detail", None) == "skill_version_not_released"
    assert calls == [("resolve", "tenant-a", "qa-word-review", "qa-file-reviewer")]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("row_field", "row_value"),
    [
        pytest.param("agent_status", "disabled", id="agent-inactive"),
        pytest.param("skill_status", "disabled", id="skill-inactive"),
        pytest.param("skill_version_status", "draft", id="skill-version-not-released"),
        pytest.param("executor_type", "unsupported", id="executor-type-not-allowed"),
        pytest.param("default_skill_id", "other-skill", id="agent-skill-mismatch"),
    ],
)
async def test_chat_stream_real_authorizer_maps_agent_skill_state_to_generic_403(
    monkeypatch,
    row_field,
    row_value,
):
    row = {
        "agent_status": "active",
        "skill_status": "active",
        "skill_version_status": "active",
        "executor_type": "claude-agent-worker",
        "default_skill_id": "qa-file-reviewer",
    }
    row[row_field] = row_value
    execute_params = []
    audits = []

    class Cursor:
        async def fetchone(self):
            return row

    class Connection:
        async def execute(self, query, params):
            assert "from agents" in query
            execute_params.append(params)
            return Cursor()

    @asynccontextmanager
    async def lifecycle_transaction():
        yield Connection()

    async def record_audit(conn, **kwargs):
        audits.append(
            (
                kwargs["source"],
                kwargs["error"].denial.capability_id,
                kwargs["error"].denial.decision_reason,
            )
        )
        return "aud-denied"

    async def fail_create_session(*args, **kwargs):
        raise AssertionError("authorization denial must precede persistence")

    monkeypatch.setattr("app.routes.chat.transaction", lifecycle_transaction)
    monkeypatch.setattr(
        repository_module,
        "authorize_run_capabilities",
        _ORIGINAL_AUTHORIZE_RUN_CAPABILITIES,
    )
    monkeypatch.setattr(repository_module, "append_capability_authorization_denial_audit", record_audit)
    monkeypatch.setattr(repository_module, "create_session", fail_create_session)

    with pytest.raises(HTTPException) as exc_info:
        await chat_stream(
            ChatStreamRequest(
                message="审核这个文档", confirmed_capability_id="document_review"
            ),
            principal=principal(
                user_id="user-skill-status",
                tenant_id="tenant-a",
                department_id="qa",
                roles=["user"],
            ),
        )

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "capability_not_authorized"
    assert execute_params == [("qa-file-reviewer", "tenant-a", "qa-word-review")]
    assert audits == [("chat_stream", "qa-file-reviewer", "capability_not_authorized")]
