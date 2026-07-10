from contextlib import asynccontextmanager

from fastapi.testclient import TestClient

from app.control_plane_contracts import (
    EVENT_ENVELOPE_SCHEMA_VERSION,
    EXECUTOR_RESULT_SCHEMA_VERSION,
    RUN_CONTRACT_VERSION,
)
from app.main import create_app


def auth_settings():
    return type("S", (), {"trusted_principal_secret": "test-secret", "frontend_poc_auth_enabled": False})()


@asynccontextmanager
async def fake_transaction():
    yield object()


def headers(roles="user", permissions="chat:read,session:read,artifact:download", department_id="QA"):
    return {
        "x-ai-user-id": "user-a",
        "x-ai-user-name": "User A",
        "x-ai-tenant-id": "tenant-a",
        "x-ai-roles": roles,
        "x-ai-department-id": department_id,
        "x-ai-permissions": permissions,
        "x-ai-gateway-secret": "test-secret",
    }


def test_agent_workspace_agents_use_principal_distribution_projection(monkeypatch):
    calls = []
    unfiltered_rows = [
        {
            "id": "general-agent",
            "name": "General company assistant",
            "description": "General governed assistant",
            "default_skill_id": "general-chat",
            "status": "active",
            "skill_version": "1.0.0",
        },
        {
            "id": "qa-word-review",
            "name": "Document reviewer",
            "description": "Reviews documents",
            "default_skill_id": "qa-file-reviewer",
            "status": "active",
            "skill_version": "1.1.0",
        },
    ]

    async def fake_unfiltered(conn, *, tenant_id):
        return unfiltered_rows

    async def fake_principal_agents(
        conn,
        *,
        tenant_id,
        actor_user_id,
        department_id,
        roles,
        is_admin,
        permissions,
    ):
        calls.append((tenant_id, actor_user_id, department_id, roles, is_admin, permissions))
        return unfiltered_rows[:1]

    async def empty(*args, **kwargs):
        return []

    async def no_policy(*args, **kwargs):
        return {
            "workspace_id": "default",
            "memory_enabled": True,
            "long_term_memory_enabled": False,
            "retention_days": 90,
            "redaction_mode": "standard",
            "source": "default",
            "reason": "",
            "updated_at": None,
        }

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.frontend_projections.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.frontend_projections.repositories.list_lambchat_agents", fake_unfiltered)
    monkeypatch.setattr(
        "app.routes.frontend_projections.repositories.list_principal_lambchat_agents",
        fake_principal_agents,
        raising=False,
    )
    monkeypatch.setattr("app.routes.frontend_projections.repositories.list_agent_workspace_sessions", empty)
    monkeypatch.setattr("app.routes.frontend_projections.repositories.list_agent_workspace_runs", empty)
    monkeypatch.setattr("app.routes.frontend_projections.repositories.list_agent_workspace_tool_permissions", empty)
    monkeypatch.setattr("app.routes.frontend_projections.repositories.get_effective_memory_policy", no_policy)

    response = TestClient(create_app()).get(
        "/api/agent-workspace?workspace_id=default",
        headers=headers(roles="QA-OPERATOR", department_id="QA"),
    )

    assert response.status_code == 200
    assert [agent["agent_id"] for agent in response.json()["agents"]] == ["general-agent"]
    assert calls == [
        (
            "tenant-a",
            "user-a",
            "QA",
            ["QA-OPERATOR"],
            False,
            ["chat:read", "session:read", "artifact:download"],
        )
    ]


def test_agent_workspace_projection_sanitizes_public_contract(monkeypatch):
    calls = []

    async def fake_list_lambchat_agents(conn, *, tenant_id, **kwargs):
        calls.append(("agents", tenant_id))
        return [
            {
                "id": "general-agent",
                "name": "General company assistant",
                "description": "General governed assistant",
                "default_skill_id": "general-chat",
                "status": "active",
                "skill_version": "1.0.0",
            },
            {
                "id": "qa-word-review",
                "name": "Document reviewer",
                "description": "Reviews documents",
                "default_skill_id": "qa-file-reviewer",
                "status": "active",
                "skill_version": "1.1.0",
            },
        ]

    async def fake_list_workspace_sessions(conn, *, tenant_id, workspace_id, user_id, agent_id=None, limit=20):
        calls.append(("sessions", tenant_id, workspace_id, user_id, agent_id, limit))
        return [
            {
                "id": "ses-a",
                "workspace_id": workspace_id,
                "agent_id": "qa-word-review",
                "title": "Document task",
                "created_at": None,
                "updated_at": None,
            }
        ]

    async def fake_list_workspace_runs(
        conn,
        *,
        tenant_id,
        workspace_id,
        user_id,
        agent_id=None,
        session_id=None,
        limit=10,
    ):
        calls.append(("runs", tenant_id, workspace_id, user_id, agent_id, session_id, limit))
        return [
            {
                "id": "run-a",
                "session_id": "ses-a",
                "workspace_id": workspace_id,
                "trace_id": "trace-run-a",
                "schema_version": RUN_CONTRACT_VERSION,
                "executor_schema_version": EXECUTOR_RESULT_SCHEMA_VERSION,
                "agent_id": "qa-word-review",
                "skill_id": "qa-file-reviewer",
                "status": "running",
                "result_json": {
                    "final_report": "Saved to C:\\private\\review.docx",
                    "source_json": {"storage_key": "tenants/tenant-a/private.json"},
                    "safe_summary": "Review is in progress",
                },
                "error_code": "executor_failure token=secret-token",
                "error_message": "failed under /home/private-user/agent-workspaces/run-a",
                "created_at": None,
                "queued_at": None,
                "started_at": None,
                "finished_at": None,
                "cancel_requested_at": None,
                "cancel_requested_by": None,
            }
        ]

    async def fake_list_run_events(conn, *, tenant_id, run_id, after_sequence=None, limit=None):
        calls.append(("events", tenant_id, run_id, after_sequence, limit))
        return [
            {
                "id": "evt-a",
                "trace_id": "trace-run-a",
                "schema_version": EVENT_ENVELOPE_SCHEMA_VERSION,
                "sequence": 1,
                "event_type": "tool_permission_requested",
                "stage": "tool_policy",
                "message": "Need approval",
                "severity": "info",
                "visible_to_user": True,
                "error_code": None,
                "latency_ms": None,
                "input_token_count": 0,
                "output_token_count": 0,
                "total_token_count": 0,
                "estimated_cost_minor": 0,
                "payload_json": {
                    "visible_to_user": True,
                    "permission_request_id": "tpr-a",
                    "tool_id": "shell",
                    "tool_call_id": "call-a",
                    "action": "execute",
                    "risk_level": "high",
                    "write_capable": True,
                    "reason": "Needs shell",
                    "skill_id": "qa-file-reviewer",
                    "storage_key": "tenants/tenant-a/runs/run-a/raw.json",
                },
                "created_at": None,
            }
        ]

    async def fake_list_run_steps(conn, *, tenant_id, run_id):
        calls.append(("steps", tenant_id, run_id))
        return [
            {
                "id": "step-a",
                "run_id": run_id,
                "step_key": "review",
                "step_kind": "agent",
                "status": "running",
                "title": "Review document",
                "role": "reviewer",
                "sequence": 1,
                "payload_json": {
                    "summary": "Reading public artifact",
                    "sandbox_workdir": "/var/lib/ai-platform/run-a",
                    "source_json": {"local_path": "C:\\private\\workdir"},
                    "skill_ids": ["qa-file-reviewer"],
                },
                "started_at": None,
                "finished_at": None,
                "created_at": None,
                "updated_at": None,
            }
        ]

    async def fake_list_run_artifacts(conn, *, tenant_id, run_id):
        calls.append(("artifacts", tenant_id, run_id))
        return [
            {
                "id": "art-a",
                "trace_id": "trace-run-a",
                "artifact_type": "reviewed_docx",
                "label": "Reviewed document",
                "content_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                "storage_key": "tenants/tenant-a/workspaces/default/runs/run-a/review.docx",
                "size_bytes": 1200,
                "manifest_version": "ai-platform.artifact-manifest.v1",
                "manifest_json": {
                    "source_json": {"local_path": "C:\\private\\review.docx"},
                    "summary": "Reviewed document",
                },
                "created_at": None,
            }
        ]

    async def fake_list_agent_workspace_tool_permissions(
        conn,
        *,
        tenant_id,
        user_id,
        workspace_id,
        agent_id=None,
        session_id=None,
        status="pending",
        limit=50,
    ):
        calls.append(("permissions", tenant_id, user_id, workspace_id, agent_id, session_id, status, limit))
        return [
            {
                "id": "tpr-older",
                "tenant_id": tenant_id,
                "workspace_id": "default",
                "user_id": user_id,
                "session_id": "ses-a",
                "run_id": "run-older",
                "trace_id": "trace-run-older",
                "tool_id": "shell",
                "tool_call_id": "call-older",
                "action": "execute",
                "risk_level": "high",
                "write_capable": True,
                "status": "pending",
                "decision": None,
                "reason": "Needs shell",
                "created_at": None,
                "decided_at": None,
                "expires_at": None,
            }
        ]

    async def fake_get_effective_memory_policy(conn, *, tenant_id, workspace_id, user_id, agent_id):
        calls.append(("memory", tenant_id, workspace_id, user_id, agent_id))
        return {
            "tenant_id": tenant_id,
            "workspace_id": workspace_id,
            "user_id": user_id,
            "agent_id": agent_id,
            "memory_enabled": True,
            "long_term_memory_enabled": False,
            "retention_days": 30,
            "redaction_mode": "standard",
            "source": "user",
            "reason": "Configured for /home/private-user/private-policy",
            "updated_by": user_id,
            "updated_at": None,
        }

    async def fake_get_latest_context(conn, *, tenant_id, user_id, run_id):
        calls.append(("context", tenant_id, user_id, run_id))
        return {
            "id": "ctx-a",
            "tenant_id": tenant_id,
            "workspace_id": "default",
            "user_id": user_id,
            "session_id": "ses-a",
            "run_id": run_id,
            "trace_id": "trace-run-a",
            "schema_version": "ai-platform.context-snapshot.v1",
            "context_kind": "executor",
            "included_message_ids": ["msg-a"],
            "included_file_ids": ["file-a"],
            "included_artifact_ids": ["art-a"],
            "included_memory_record_ids": ["mem-a"],
            "redaction_summary_json": {"mode": "standard"},
            "payload_json": {
                "used_context_summary": {
                    "source": "chat_stream",
                    "input_keys": [
                        "message",
                        "raw_storage_key",
                        "storage_key",
                        "sandbox_workdir",
                        "source_json",
                        "executor_payload",
                        "local_path",
                    ],
                    "memory_policy_source": "user",
                    "long_term_memory_read": False,
                },
                "referenced_materials": {
                    "message_count": 1,
                    "file_count": 1,
                    "artifact_count": 1,
                    "memory_record_count": 1,
                },
                "sandbox_workdir": "/home/private-user/agent-workspaces/run-a",
            },
            "created_at": None,
        }

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.frontend_projections.transaction", fake_transaction)
    monkeypatch.setattr(
        "app.routes.frontend_projections.repositories.list_principal_lambchat_agents",
        fake_list_lambchat_agents,
    )
    monkeypatch.setattr(
        "app.routes.frontend_projections.repositories.list_agent_workspace_sessions",
        fake_list_workspace_sessions,
        raising=False,
    )
    monkeypatch.setattr(
        "app.routes.frontend_projections.repositories.list_agent_workspace_runs",
        fake_list_workspace_runs,
        raising=False,
    )
    monkeypatch.setattr("app.routes.frontend_projections.repositories.list_run_events", fake_list_run_events)
    monkeypatch.setattr("app.routes.frontend_projections.repositories.list_run_steps", fake_list_run_steps)
    monkeypatch.setattr("app.routes.frontend_projections.repositories.list_run_artifacts", fake_list_run_artifacts)
    monkeypatch.setattr(
        "app.routes.frontend_projections.repositories.list_agent_workspace_tool_permissions",
        fake_list_agent_workspace_tool_permissions,
        raising=False,
    )
    monkeypatch.setattr(
        "app.routes.frontend_projections.repositories.get_effective_memory_policy",
        fake_get_effective_memory_policy,
    )
    monkeypatch.setattr(
        "app.routes.frontend_projections.repositories.get_latest_authorized_executor_context_snapshot",
        fake_get_latest_context,
    )

    client = TestClient(create_app())
    response = client.get(
        "/api/agent-workspace?workspace_id=default&agent_id=document-review",
        headers=headers(),
    )

    assert response.status_code == 200
    data = response.json()
    assert data["workspace_id"] == "default"
    assert data["selected_agent"]["agent_id"] == "document-review"
    assert data["selected_agent"]["capability_id"] == "document_review"
    assert data["sessions"][0]["session_id"] == "ses-a"
    assert data["latest_runs"][0]["run_id"] == "run-a"
    assert data["latest_runs"][0]["agent_id"] == "document-review"
    assert data["run_console"]["events"][0]["payload"]["tool_permission_card"]["permission_request_id"] == "tpr-a"
    assert data["artifacts"][0]["artifact_id"] == "art-a"
    assert data["pending_tool_permissions"][0]["permission_request_id"] == "tpr-older"
    assert data["pending_tool_permissions"][0]["run_id"] == "run-older"
    assert data["memory_context_policy"]["memory_enabled"] is True
    assert data["memory_context_policy"]["latest_context"]["referenced_materials"]["artifact_count"] == 1
    assert data["memory_context_policy"]["latest_context"]["used_context_summary"]["input_keys"] == ["message"]

    serialized = response.text
    forbidden_terms = [
        "storage_key",
        "local_path",
        "sandbox_workdir",
        "source_json",
        "executor_payload",
        "qa-file-reviewer",
        "qa-word-review",
        "C:\\private",
        "/home/private-user",
        "/var/lib/ai-platform",
        "secret-token",
    ]
    for term in forbidden_terms:
        assert term not in serialized

    assert ("sessions", "tenant-a", "default", "user-a", "qa-word-review", 20) in calls
    assert ("runs", "tenant-a", "default", "user-a", "qa-word-review", None, 10) in calls
    assert ("permissions", "tenant-a", "user-a", "default", "qa-word-review", None, "pending", 50) in calls


def test_agent_workspace_projection_requires_chat_and_session_read(monkeypatch):
    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    client = TestClient(create_app())

    missing_chat = client.get(
        "/api/agent-workspace?workspace_id=default",
        headers=headers(permissions="session:read,artifact:download"),
    )
    missing_session = client.get(
        "/api/agent-workspace?workspace_id=default",
        headers=headers(permissions="chat:read,artifact:download"),
    )

    assert missing_chat.status_code == 403
    assert missing_chat.json()["detail"] == "missing_permission:chat:read"
    assert missing_session.status_code == 403
    assert missing_session.json()["detail"] == "missing_permission:session:read"


def test_agent_workspace_projection_omits_artifacts_without_download_permission(monkeypatch):
    calls = []

    async def fake_list_lambchat_agents(conn, *, tenant_id, **kwargs):
        calls.append(("agents", tenant_id))
        return [
            {
                "id": "general-agent",
                "name": "General assistant",
                "description": "Safe",
                "default_skill_id": "general-chat",
                "status": "active",
                "skill_version": "1.0.0",
            }
        ]

    async def fake_list_workspace_sessions(conn, *, tenant_id, workspace_id, user_id, agent_id=None, limit=20):
        calls.append(("sessions", tenant_id, workspace_id, user_id, agent_id, limit))
        return []

    async def fake_list_workspace_runs(
        conn,
        *,
        tenant_id,
        workspace_id,
        user_id,
        agent_id=None,
        session_id=None,
        limit=10,
    ):
        calls.append(("runs", tenant_id, workspace_id, user_id, agent_id, session_id, limit))
        return [
            {
                "id": "run-a",
                "session_id": "ses-a",
                "workspace_id": workspace_id,
                "trace_id": "trace-run-a",
                "schema_version": RUN_CONTRACT_VERSION,
                "executor_schema_version": EXECUTOR_RESULT_SCHEMA_VERSION,
                "agent_id": "general-agent",
                "skill_id": "general-chat",
                "status": "completed",
                "result_json": {"safe_summary": "Done"},
                "error_code": None,
                "error_message": None,
                "created_at": None,
                "queued_at": None,
                "started_at": None,
                "finished_at": None,
                "cancel_requested_at": None,
                "cancel_requested_by": None,
            }
        ]

    async def fake_list_run_events(conn, *, tenant_id, run_id, after_sequence=None, limit=None):
        calls.append(("events", tenant_id, run_id, after_sequence, limit))
        return []

    async def fake_list_run_steps(conn, *, tenant_id, run_id):
        calls.append(("steps", tenant_id, run_id))
        return []

    async def fake_list_run_artifacts(conn, *, tenant_id, run_id):
        raise AssertionError("artifacts must not be queried without artifact:download")

    async def fake_get_latest_context(conn, *, tenant_id, user_id, run_id):
        calls.append(("context", tenant_id, user_id, run_id))
        return None

    async def fake_list_agent_workspace_tool_permissions(
        conn,
        *,
        tenant_id,
        user_id,
        workspace_id,
        agent_id=None,
        session_id=None,
        status="pending",
        limit=50,
    ):
        calls.append(("permissions", tenant_id, user_id, workspace_id, agent_id, session_id, status, limit))
        return []

    async def fake_get_effective_memory_policy(conn, *, tenant_id, workspace_id, user_id, agent_id):
        calls.append(("memory", tenant_id, workspace_id, user_id, agent_id))
        return {
            "tenant_id": tenant_id,
            "workspace_id": workspace_id,
            "user_id": user_id,
            "agent_id": agent_id,
            "memory_enabled": True,
            "long_term_memory_enabled": False,
            "retention_days": 30,
            "redaction_mode": "standard",
            "source": "default",
            "reason": "",
            "updated_at": None,
        }

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.frontend_projections.transaction", fake_transaction)
    monkeypatch.setattr(
        "app.routes.frontend_projections.repositories.list_principal_lambchat_agents",
        fake_list_lambchat_agents,
    )
    monkeypatch.setattr(
        "app.routes.frontend_projections.repositories.list_agent_workspace_sessions",
        fake_list_workspace_sessions,
        raising=False,
    )
    monkeypatch.setattr(
        "app.routes.frontend_projections.repositories.list_agent_workspace_runs",
        fake_list_workspace_runs,
        raising=False,
    )
    monkeypatch.setattr("app.routes.frontend_projections.repositories.list_run_events", fake_list_run_events)
    monkeypatch.setattr("app.routes.frontend_projections.repositories.list_run_steps", fake_list_run_steps)
    monkeypatch.setattr("app.routes.frontend_projections.repositories.list_run_artifacts", fake_list_run_artifacts)
    monkeypatch.setattr(
        "app.routes.frontend_projections.repositories.get_latest_authorized_executor_context_snapshot",
        fake_get_latest_context,
    )
    monkeypatch.setattr(
        "app.routes.frontend_projections.repositories.list_agent_workspace_tool_permissions",
        fake_list_agent_workspace_tool_permissions,
        raising=False,
    )
    monkeypatch.setattr(
        "app.routes.frontend_projections.repositories.get_effective_memory_policy",
        fake_get_effective_memory_policy,
    )

    client = TestClient(create_app())
    response = client.get(
        "/api/agent-workspace?workspace_id=default",
        headers=headers(permissions="chat:read,session:read"),
    )

    assert response.status_code == 200
    assert response.json()["artifacts"] == []
    assert not any(call[0] == "artifacts" for call in calls)


def test_agent_workspace_projection_selected_empty_session_does_not_widen_approvals(monkeypatch):
    calls = []

    async def fake_list_lambchat_agents(conn, *, tenant_id, **kwargs):
        calls.append(("agents", tenant_id))
        return []

    async def fake_list_workspace_sessions(conn, *, tenant_id, workspace_id, user_id, agent_id=None, limit=20):
        calls.append(("sessions", tenant_id, workspace_id, user_id, agent_id, limit))
        return []

    async def fake_list_workspace_runs(
        conn,
        *,
        tenant_id,
        workspace_id,
        user_id,
        agent_id=None,
        session_id=None,
        limit=10,
    ):
        calls.append(("runs", tenant_id, workspace_id, user_id, agent_id, session_id, limit))
        assert session_id == "ses-empty"
        return []

    async def fake_list_agent_workspace_tool_permissions(
        conn,
        *,
        tenant_id,
        user_id,
        workspace_id,
        agent_id=None,
        session_id=None,
        status="pending",
        limit=50,
    ):
        calls.append(("permissions", tenant_id, user_id, workspace_id, agent_id, session_id, status, limit))
        if session_id != "ses-empty":
            return [
                {
                    "id": "tpr-other",
                    "workspace_id": workspace_id,
                    "session_id": "ses-other",
                    "run_id": "run-other",
                    "trace_id": "trace-other",
                    "tool_id": "shell",
                    "tool_call_id": "call-other",
                    "action": "execute",
                    "risk_level": "high",
                    "write_capable": True,
                    "status": "pending",
                    "reason": "Should not be projected",
                    "created_at": None,
                }
            ]
        return []

    async def fake_get_effective_memory_policy(conn, *, tenant_id, workspace_id, user_id, agent_id):
        calls.append(("memory", tenant_id, workspace_id, user_id, agent_id))
        return {
            "tenant_id": tenant_id,
            "workspace_id": workspace_id,
            "user_id": user_id,
            "agent_id": agent_id,
            "memory_enabled": True,
            "long_term_memory_enabled": False,
            "retention_days": 30,
            "redaction_mode": "standard",
            "source": "default",
            "reason": "",
            "updated_at": None,
        }

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.frontend_projections.transaction", fake_transaction)
    monkeypatch.setattr(
        "app.routes.frontend_projections.repositories.list_principal_lambchat_agents",
        fake_list_lambchat_agents,
    )
    monkeypatch.setattr(
        "app.routes.frontend_projections.repositories.list_agent_workspace_sessions",
        fake_list_workspace_sessions,
        raising=False,
    )
    monkeypatch.setattr(
        "app.routes.frontend_projections.repositories.list_agent_workspace_runs",
        fake_list_workspace_runs,
        raising=False,
    )
    monkeypatch.setattr(
        "app.routes.frontend_projections.repositories.list_agent_workspace_tool_permissions",
        fake_list_agent_workspace_tool_permissions,
        raising=False,
    )
    monkeypatch.setattr(
        "app.routes.frontend_projections.repositories.get_effective_memory_policy",
        fake_get_effective_memory_policy,
    )

    client = TestClient(create_app())
    response = client.get(
        "/api/agent-workspace?workspace_id=default&session_id=ses-empty",
        headers=headers(),
    )

    assert response.status_code == 200
    data = response.json()
    assert data["latest_runs"] == []
    assert data["run_console"]["status"] == "idle"
    assert data["pending_tool_permissions"] == []
    assert ("runs", "tenant-a", "default", "user-a", None, "ses-empty", 10) in calls
    assert ("permissions", "tenant-a", "user-a", "default", None, "ses-empty", "pending", 50) in calls


def test_agent_workspace_projection_rejects_unsafe_workspace_id(monkeypatch):
    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    client = TestClient(create_app())

    response = client.get(
        "/api/agent-workspace?workspace_id=../private",
        headers=headers(),
    )

    assert response.status_code == 400
    assert "workspace_id" in response.json()["detail"]
