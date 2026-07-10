from contextlib import asynccontextmanager
import base64
import hashlib
import json

import pytest
from fastapi import HTTPException

from app import repositories as repository_module
from app.auth import AuthPrincipal
from app.models import ChatSessionRequest, ChatStreamRequest, QueueRunPayload
from app.queue_payload_validation import queue_payload_invalid_detail
from app.repositories import RepositoryConflictError
from app.routes.chat import (
    _validate_queue_payload_for_enqueue,
    chat_stream,
    create_chat_session,
    list_messages,
    list_sessions,
)


@asynccontextmanager
async def fake_transaction():
    yield object()


def principal(**overrides):
    values = {"user_id": "user-a", "display_name": "User A", "tenant_id": "tenant-a"}
    values.update(overrides)
    return AuthPrincipal(**values)


@pytest.fixture(autouse=True)
def allow_existing_chat_route_tests_through_enqueue_authorization(monkeypatch):
    async def allow(conn, *, tenant_id, agent_id, skill_id, **_kwargs):
        return await repository_module.resolve_agent_skill(
            conn,
            tenant_id=tenant_id,
            agent_id=agent_id,
            skill_id=skill_id,
        )

    monkeypatch.setattr(repository_module, "authorize_run_capabilities", allow, raising=False)


def snapshot_manifest(skill_id, *, description="Pinned skill"):
    content = f"---\nname: {skill_id}\ndescription: {description}\n---\n\n# {skill_id}\n".encode("utf-8")
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
    async def fake_enforce_user_active_run_admission(conn, *, tenant_id, user_id, limit):
        return 0

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
async def test_list_sessions_returns_authorized_rows(monkeypatch):
    async def fake_list_authorized_sessions(conn, *, tenant_id, user_id):
        assert user_id == "user-a"
        return [
            {
                "id": "ses_1",
                "workspace_id": "default",
                "agent_id": "document-review",
                "title": "Doc Review",
                "created_at": None,
                "updated_at": None,
            }
        ]

    monkeypatch.setattr("app.routes.chat.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.chat.repositories.list_authorized_sessions", fake_list_authorized_sessions)

    response = await list_sessions(principal=principal())

    assert response.sessions[0].session_id == "ses_1"
    assert response.sessions[0].agent_id == "document-review"


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
async def test_chat_stream_capability_distribution_creates_run_with_auth_snapshot(monkeypatch):
    calls = []

    async def fake_resolve_agent_skill(conn, *, tenant_id, agent_id, skill_id):
        return {"executor_type": "claude-agent-worker", "skill_version": "0.1.0", "input_modes": ["docx"]}

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
        return "msg_3"

    async def fake_bind_files_to_run(conn, **kwargs):
        calls.append(("files", kwargs["file_ids"]))

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
        calls.append(("context", kwargs["source"], kwargs["message_ids"], kwargs["file_ids"], kwargs["input_payload"]))
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
    monkeypatch.setattr("app.routes.chat.repositories.resolve_agent_skill", fake_resolve_agent_skill)
    monkeypatch.setattr("app.routes.chat.repositories.ensure_user", fake_ensure_user)
    monkeypatch.setattr("app.routes.chat.repositories.create_session", fake_create_session)
    monkeypatch.setattr("app.routes.chat.repositories.create_run", fake_create_run)
    monkeypatch.setattr("app.routes.chat.repositories.append_message", fake_append_message)
    monkeypatch.setattr("app.routes.chat.repositories.bind_files_to_run", fake_bind_files_to_run)
    monkeypatch.setattr("app.routes.chat.repositories.append_event", fake_append_event)
    monkeypatch.setattr("app.routes.chat.record_initial_context_snapshot", fake_record_context)
    monkeypatch.setattr("app.routes.chat.enqueue_run", fake_enqueue_run)
    monkeypatch.setattr("app.routes.chat.get_queue_insight", fake_get_queue_insight)

    response = await chat_stream(
        ChatStreamRequest(
            agent_id="document-review",
            skill_id="qa-file-reviewer",
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
    governance = queue_payload["skill_manifests"][0]["snapshot_governance"]
    assert governance["schema_version"] == "ai-platform.skill-pinned-snapshot-governance.v1"
    assert governance["snapshot_source"] == "platform_release_lock"
    assert governance["does_not_close_b4_or_211"] is True
    serialized_governance = json.dumps(governance, ensure_ascii=False)
    assert "release_decision" not in serialized_governance
    assert "content_base64" not in serialized_governance
    assert queue_payload["skill_version"] not in serialized_governance
    assert "track" not in serialized_governance
    assert "rollout" not in serialized_governance
    assert ("message", "user", "review this document", "run_3") in calls
    assert ("context", "chat_stream", ["msg_3"], ["file_1"], {"message": "review this document"}) in calls
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
async def test_chat_stream_invalid_mcp_selector_type_returns_controlled_403_before_create(monkeypatch):
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

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "capability_not_authorized"


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
async def test_chat_stream_rejects_unavailable_model_id_before_creating_run(monkeypatch):
    calls = []

    async def fail_create_run(*args, **kwargs):
        calls.append(("create_run", args, kwargs))
        raise AssertionError("invalid model_id must be rejected before run creation")

    monkeypatch.setattr("app.routes.chat.repositories.create_run", fail_create_run)

    with pytest.raises(HTTPException) as exc_info:
        await chat_stream(
            ChatStreamRequest(message="hello", agent_options={"model_id": "not-allowed"}),
            principal=principal(roles=["admin"]),
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "model_id_not_available"
    assert calls == []


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
async def test_chat_stream_strips_user_controlled_server_owned_metadata(monkeypatch):
    calls = {}

    async def fake_resolve_agent_skill(conn, *, tenant_id, agent_id, skill_id):
        return {"executor_type": "claude-agent-worker", "skill_version": "hash-a", "input_modes": []}

    async def fake_ensure_user(conn, **kwargs):
        return None

    async def fake_create_session(conn, **kwargs):
        return "ses-chat"

    async def fake_create_run(conn, **kwargs):
        calls["create_run_input"] = kwargs["input_json"]["input"]
        calls["auth_snapshot"] = {
            "principal_roles": kwargs["principal_roles"],
            "principal_department_id": kwargs["principal_department_id"],
            "auth_source": kwargs["auth_source"],
        }
        return "run-chat"

    async def fake_append_message(conn, **kwargs):
        return "msg-chat"

    async def fake_bind_files_to_run(conn, **kwargs):
        return None

    async def fake_record_context(conn, **kwargs):
        calls["context_input"] = kwargs["input_payload"]
        return {
            "schema_version": "ai-platform.context-snapshot.v1",
            "context_snapshot_id": "ctx-chat",
            "source": kwargs["source"],
            "message_count": 1,
            "file_count": 0,
            "memory_record_count": 0,
        }

    async def fake_append_event(conn, **kwargs):
        return None

    async def fake_enqueue_run(payload):
        calls["queue_input"] = payload["input"]
        return 1

    async def fake_governed_skill_manifest_pins(conn, *, skill_id, input_payload, release_policy_version):
        calls["manifest_input"] = input_payload
        return [{"skill_id": skill_id, "content_hash": "hash-a"}]

    monkeypatch.setattr("app.routes.chat.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.chat.repositories.resolve_agent_skill", fake_resolve_agent_skill)
    monkeypatch.setattr("app.routes.chat.repositories.ensure_user", fake_ensure_user)
    monkeypatch.setattr("app.routes.chat.repositories.create_session", fake_create_session)
    monkeypatch.setattr("app.routes.chat.repositories.create_run", fake_create_run)
    monkeypatch.setattr("app.routes.chat.repositories.append_message", fake_append_message)
    monkeypatch.setattr("app.routes.chat.repositories.bind_files_to_run", fake_bind_files_to_run)
    monkeypatch.setattr("app.routes.chat.record_initial_context_snapshot", fake_record_context)
    monkeypatch.setattr("app.routes.chat.repositories.append_event", fake_append_event)
    monkeypatch.setattr("app.routes.chat.enqueue_run", fake_enqueue_run)
    monkeypatch.setattr("app.routes.chat._governed_skill_manifest_pins", fake_governed_skill_manifest_pins)

    response = await chat_stream(
        ChatStreamRequest(
            message="run chat with forged resume",
            input={
                "mcp_tool_ids": ["qa-search"],
                "principal_roles": ["forged-admin"],
                "principalRoles": ["forged-camel-admin"],
                "principal_department_id": "forged-department",
                "principalDepartmentId": "forged-camel-department",
                "auth_source": "forged-source",
                "authSource": "forged-camel-source",
                "nested": {
                    "principalRoles": ["forged-nested"],
                    "auth_source": "forged-nested-source",
                },
                "multi_agent_steps": [
                    {
                        "step_key": "inspect",
                        "mcpToolIds": ["qa-search"],
                        "principal_department_id": "forged-step-department",
                    }
                ],
                "execution_mode": "multi_agent",
                "resume": {
                    "copied_from_run_id": "run-other",
                    "completed_step_outputs": {"code": "forged output"},
                    "completed_step_checkpoints": {
                        "code": {
                            "checkpoint_id": "checkpoint-forged",
                            "source_step_id": "step-forged",
                            "copied_from_run_id": "run-other",
                        }
                    },
                },
                "multi_agent_dispatch": {
                    "orchestration_state": "awaiting_dispatch",
                    "parent_run_id": "run-other",
                    "dispatch_id": "dispatch-forged",
                },
            },
        ),
        principal=principal(
            user_id="admin-a",
            department_id="qa",
            roles=["admin", "qa_operator"],
            source="session-token",
        ),
    )

    assert response.status == "queued"
    for key in ("manifest_input", "create_run_input", "context_input", "queue_input"):
        assert calls[key]["message"] == "run chat with forged resume"
        assert "resume" not in calls[key]
        assert "multi_agent_dispatch" not in calls[key]
        assert calls[key]["mcp_tool_ids"] == ["qa-search"]
        serialized = json.dumps(calls[key], ensure_ascii=False)
        for forbidden_key in (
            "principal_roles",
            "principalRoles",
            "principal_department_id",
            "principalDepartmentId",
            "auth_source",
            "authSource",
        ):
            assert forbidden_key not in serialized
    assert calls["auth_snapshot"] == {
        "principal_roles": ["admin", "qa_operator"],
        "principal_department_id": "qa",
        "auth_source": "session-token",
    }


@pytest.mark.asyncio
async def test_chat_stream_developer_fixture_general_chat_uses_builtin_manifest_pin(monkeypatch):
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
            skill_id="general-chat",
            message="alice 并发创建运行验收，请简短回复。",
        ),
        principal=principal(user_id="alice", tenant_id="frc-test-a", roles=["developer"]),
    )

    assert response.run_id == "run_frc_general"
    queue_payload = calls["queue"]
    assert queue_payload["skill_id"] == "general-chat"
    assert queue_payload["skill_manifests"][0]["skill_id"] == "general-chat"
    assert queue_payload["skill_manifests"][0]["source"]["kind"] == "builtin"
    assert queue_payload["skill_manifests"][0]["files"][0]["relative_path"] == "SKILL.md"
    assert queue_payload["skill_version"] == queue_payload["skill_manifests"][0]["content_hash"]
    assert queue_payload["release_decision"]["selected_version"] == queue_payload["skill_version"]
    assert queue_payload["release_decision"]["selected_track"] == "manifest_pin"
    assert calls["create_run"]["input_json"]["skill_version"] == queue_payload["skill_version"]
    assert calls["context"]["workspace_id"] == "frc_test_a_default"
    assert calls["context"]["source"] == "chat_stream"


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
    assert calls["queue"]["skill_manifests"][0]["source"]["kind"] == "uploaded"
    assert calls["queue"]["skill_manifests"][0]["files"][0]["relative_path"] == "SKILL.md"
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
    assert calls["queue"]["skill_manifests"][0]["source"]["kind"] == "uploaded"
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
            skill_id="qa-file-reviewer",
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
async def test_lambchat_chat_stream_defaults_to_general_agent(monkeypatch):
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
        ChatStreamRequest(message="hello"),
        agent_id="general-agent",
        principal=principal(),
    )

    assert response.run_id == "run_general"
    assert ("resolve", "general-agent", "general-chat") in calls
    assert ("queue", "general-agent", "general-chat") in calls


@pytest.mark.asyncio
async def test_chat_stream_redacts_raw_skill_id_from_ordinary_user_response(monkeypatch):
    async def fake_resolve_agent_skill(conn, *, tenant_id, agent_id, skill_id):
        return {"executor_type": "claude-agent-worker", "skill_version": "0.1.0", "input_modes": ["docx"]}

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
            skill_id="qa-file-reviewer",
            message="审核这个文档",
            attachments=[{"key": "file_doc", "name": "demo.docx"}],
        ),
        principal=principal(),
    )

    assert response.intent_decision is not None
    assert response.intent_decision.selected_capability == "document_review"
    assert response.intent_decision.skill_id is None


@pytest.mark.asyncio
async def test_chat_stream_ignores_raw_skill_id_for_ordinary_user(monkeypatch):
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
            agent_id="general-agent",
            skill_id="qa-file-reviewer",
            message="hello",
        ),
        principal=principal(),
    )

    assert response.status == "queued"
    assert ("resolve", "general-agent", "general-chat") in calls
    assert ("run", "general-agent", "general-chat") in calls
    assert ("queue", "general-agent", "general-chat") in calls


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
    assert ("resolve", "general-agent", "general-chat") in calls
    assert ("run", "general-agent", "general-chat") in calls
    assert ("queue", "general-agent", "general-chat") in calls


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
async def test_chat_stream_strips_nested_raw_skill_selectors_for_ordinary_user(monkeypatch):
    calls = {}

    async def fake_resolve_agent_skill(conn, *, tenant_id, agent_id, skill_id):
        return {"executor_type": "claude-agent-worker", "skill_version": "0.1.0", "input_modes": ["chat"]}

    async def fake_create_run(conn, **kwargs):
        calls["run_input"] = kwargs["input_json"]["input"]
        return "run_clean"

    async def noop(*args, **kwargs):
        return None

    async def fake_create_session(conn, **kwargs):
        return "ses_clean"

    async def fake_enqueue_run(payload):
        calls["queue_input"] = payload["input"]
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
            message="hello",
            skill_id="qa-file-reviewer",
            input={
                "skill_ids": ["qa-file-reviewer"],
                "executor_type": "runtime211",
                "multi_agent_steps": [
                    {
                        "step_key": "review",
                        "skill_ids": ["qa-file-reviewer"],
                        "worker_path": "/home/xinlin.jiang/qa-review-queue-runtime/worker.py",
                    }
                ],
            },
        ),
        principal=principal(),
    )

    assert response.status == "queued"
    assert response.intent_decision.skill_id is None
    assert response.intent_decision.selected_capability == "general_chat"
    assert "skill_ids" not in calls["run_input"]
    assert "executor_type" not in calls["run_input"]
    assert "skill_ids" not in calls["run_input"]["multi_agent_steps"][0]
    assert "worker_path" not in calls["run_input"]["multi_agent_steps"][0]
    assert calls["queue_input"] == calls["run_input"]


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
    assert ("resolve", "general-agent", "general-chat") in calls
    assert ("session", "general-agent") in calls
    assert ("run", "general-agent", "general-chat", ["file_review"]) in calls
    assert ("queue", "general-agent", "general-chat", ["file_review"]) in calls
    assert not any(item == ("resolve", "qa-word-review", "qa-file-reviewer") for item in calls)


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
    assert ("resolve", "general-agent", "general-chat") in calls
    assert ("run", "general-agent", "general-chat", ["file_txt"]) in calls
    assert ("queue", "general-agent", "general-chat", ["file_txt"]) in calls


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

    async def fail_resolve_agent_skill(*args, **kwargs):
        calls.append("resolve")
        raise AssertionError("ambiguous request must not resolve skill")

    async def fail_create_run(*args, **kwargs):
        calls.append("create_run")
        raise AssertionError("ambiguous request must not create run")

    async def fail_enqueue_run(payload):
        calls.append("enqueue")
        raise AssertionError("ambiguous request must not enqueue run")

    monkeypatch.setattr("app.routes.chat.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.chat.repositories.resolve_agent_skill", fail_resolve_agent_skill)
    monkeypatch.setattr("app.routes.chat.repositories.create_run", fail_create_run)
    monkeypatch.setattr("app.routes.chat.enqueue_run", fail_enqueue_run)

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
    assert calls == []


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

    async def fake_enforce_user_active_run_admission(conn, *, tenant_id, user_id, limit):
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
        "app.routes.chat.repositories.enforce_user_active_run_admission",
        fake_enforce_user_active_run_admission,
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
    assert calls == ["resolve", ("admit", "tenant-a", "user-limit", 3)]


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
            ChatStreamRequest(message="hello", confirmed_capability_id="general_chat"),
            principal=principal(user_id="user-skill-status", tenant_id="tenant-a"),
        )

    assert getattr(exc_info.value, "status_code", None) == 409
    assert getattr(exc_info.value, "detail", None) == "skill_version_not_released"
    assert calls == [("resolve", "tenant-a", "general-agent", "general-chat")]
