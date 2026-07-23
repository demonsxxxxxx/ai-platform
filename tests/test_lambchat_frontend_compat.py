from contextlib import asynccontextmanager
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.repositories import append_message as real_append_message
from app.repositories import (
    list_authorized_user_messages_for_runs as real_list_authorized_user_messages_for_runs,
)
from app.main import create_app
from app.models import ChatStreamRequest


def auth_settings():
    return type("S", (), {"trusted_principal_secret": "test-secret", "frontend_poc_auth_enabled": False})()


@asynccontextmanager
async def fake_transaction():
    yield object()


def auth_headers():
    return {
        "x-ai-user-id": "user-a",
        "x-ai-user-name": "User A",
        "x-ai-tenant-id": "default",
        "x-ai-roles": "user",
        "x-ai-gateway-secret": "test-secret",
    }


def action_headers(*, user_id="user-a", tenant_id="default", roles="user"):
    return {
        "x-ai-user-id": user_id,
        "x-ai-user-name": user_id,
        "x-ai-tenant-id": tenant_id,
        "x-ai-roles": roles,
        "x-ai-gateway-secret": "test-secret",
    }


@pytest.fixture(autouse=True)
def empty_authorized_history_messages(monkeypatch):
    async def empty_messages(conn, *, tenant_id, user_id, session_id):
        return []

    async def empty_user_messages_for_runs(
        conn, *, tenant_id, user_id, session_id, run_ids
    ):
        return []

    monkeypatch.setattr(
        "app.routes.lambchat_compat.repositories.list_authorized_messages",
        empty_messages,
    )
    monkeypatch.setattr(
        "app.routes.lambchat_compat.repositories.list_authorized_user_messages_for_runs",
        empty_user_messages_for_runs,
        raising=False,
    )


@pytest.mark.asyncio
async def test_session_action_service_enforces_tenant_owner_admin_and_terminal_delete(monkeypatch):
    from app import session_actions
    from app.auth import AuthPrincipal

    records = {
        ("default", "ses-owner"): {
            "id": "ses-owner",
            "tenant_id": "default",
            "workspace_id": "workspace-a",
            "user_id": "user-a",
            "agent_id": "general-agent",
            "title": "Original",
            "status": "active",
        },
        ("default", "ses-other"): {
            "id": "ses-other",
            "tenant_id": "default",
            "workspace_id": "workspace-a",
            "user_id": "user-b",
            "agent_id": "general-agent",
            "title": "Other",
            "status": "active",
        },
        ("default", "ses-deleted"): {
            "id": "ses-deleted",
            "tenant_id": "default",
            "workspace_id": "workspace-a",
            "user_id": "user-a",
            "agent_id": "general-agent",
            "title": "Deleted",
            "status": "deleted",
        },
    }
    writes = []

    async def get_session_for_action(_conn, *, tenant_id, session_id):
        return records.get((tenant_id, session_id))

    async def update_session_title(_conn, *, tenant_id, session_id, title):
        writes.append(("rename", tenant_id, session_id, title))
        record = records[(tenant_id, session_id)]
        record["title"] = title
        return record

    async def mark_session_deleted(_conn, *, tenant_id, session_id):
        writes.append(("delete", tenant_id, session_id))
        record = records[(tenant_id, session_id)]
        record["status"] = "deleted"
        return record

    monkeypatch.setattr(session_actions.repositories, "get_session_for_action", get_session_for_action)
    monkeypatch.setattr(session_actions.repositories, "update_session_title", update_session_title)
    monkeypatch.setattr(session_actions.repositories, "mark_session_deleted", mark_session_deleted)

    owner = AuthPrincipal(user_id="user-a", display_name="A", tenant_id="default", roles=["user"])
    admin = AuthPrincipal(user_id="admin-a", display_name="Admin", tenant_id="default", roles=["admin"])
    other_tenant = AuthPrincipal(user_id="user-a", display_name="A", tenant_id="other", roles=["admin"])

    renamed = await session_actions.rename_session(object(), principal=owner, session_id="ses-owner", title=" Renamed ")
    assert renamed["title"] == "Renamed"
    assert writes == [("rename", "default", "ses-owner", "Renamed")]

    await session_actions.rename_session(object(), principal=admin, session_id="ses-other", title="Admin rename")
    assert writes[-1] == ("rename", "default", "ses-other", "Admin rename")

    with pytest.raises(session_actions.SessionActionValidationError):
        await session_actions.rename_session(object(), principal=owner, session_id="ses-owner", title="   ")
    with pytest.raises(session_actions.SessionActionNotFoundError):
        await session_actions.rename_session(object(), principal=owner, session_id="ses-other", title="Denied")
    with pytest.raises(session_actions.SessionActionNotFoundError):
        await session_actions.rename_session(object(), principal=other_tenant, session_id="ses-owner", title="Denied")
    assert all(entry[2] != "ses-other" or entry[3] != "Denied" for entry in writes if entry[0] == "rename")

    deleted = await session_actions.delete_session(object(), principal=owner, session_id="ses-owner")
    assert deleted["already_deleted"] is False
    repeated = await session_actions.delete_session(object(), principal=owner, session_id="ses-owner")
    assert repeated["already_deleted"] is True
    assert [entry for entry in writes if entry[0] == "delete"] == [("delete", "default", "ses-owner")]

    admin_deleted = await session_actions.delete_session(object(), principal=admin, session_id="ses-other")
    assert admin_deleted["already_deleted"] is False
    assert ("delete", "default", "ses-other") in writes

    with pytest.raises(session_actions.SessionActionNotFoundError):
        await session_actions.delete_session(object(), principal=owner, session_id="missing")
    with pytest.raises(session_actions.SessionActionNotFoundError):
        await session_actions.delete_session(object(), principal=owner, session_id="ses-other")


@pytest.mark.asyncio
async def test_session_action_fork_copies_only_authorized_message_prefix_without_oracles(monkeypatch):
    from app import session_actions
    from app.auth import AuthPrincipal

    source = {
        "id": "ses-source",
        "tenant_id": "default",
        "workspace_id": "workspace-a",
        "user_id": "user-a",
        "agent_id": "general-agent",
        "title": "Source",
        "status": "active",
    }
    copied = []
    created = []
    ensured_users = []

    async def get_session_for_action(_conn, *, tenant_id, session_id):
        if (tenant_id, session_id) == ("default", "ses-source"):
            return source
        return None

    async def list_session_messages_for_fork(_conn, *, tenant_id, session_id):
        assert (tenant_id, session_id) == ("default", "ses-source")
        return [
            {"id": "msg-1", "run_id": "run-source", "role": "user", "content": "one", "metadata_json": {}},
            {"id": "msg-2", "run_id": "run-source", "role": "assistant", "content": "two", "metadata_json": {}},
        ]

    async def create_session(_conn, **kwargs):
        created.append(kwargs)
        return "ses-fork"

    async def ensure_user(_conn, *, tenant_id, user_id, display_name=None):
        ensured_users.append((tenant_id, user_id, display_name))

    async def append_message(_conn, **kwargs):
        copied.append(kwargs)
        return f"msg-copy-{len(copied)}"

    monkeypatch.setattr(session_actions.repositories, "get_session_for_action", get_session_for_action)
    monkeypatch.setattr(session_actions.repositories, "list_session_messages_for_fork", list_session_messages_for_fork)
    monkeypatch.setattr(session_actions.repositories, "ensure_user", ensure_user)
    monkeypatch.setattr(session_actions.repositories, "create_session", create_session)
    monkeypatch.setattr(session_actions.repositories, "append_message", append_message)

    owner = AuthPrincipal(user_id="user-a", display_name="A", tenant_id="default", roles=["user"])
    admin = AuthPrincipal(user_id="admin-a", display_name="Admin", tenant_id="default", roles=["admin"])
    other_user = AuthPrincipal(user_id="user-b", display_name="B", tenant_id="default", roles=["user"])
    other_tenant = AuthPrincipal(user_id="user-a", display_name="A", tenant_id="other", roles=["admin"])

    result = await session_actions.fork_session_message(object(), principal=owner, session_id="ses-source", message_id="msg-1")
    assert result["source_session_id"] == "ses-source"
    assert result["session"]["id"] == "ses-fork"
    assert created == [{"tenant_id": "default", "workspace_id": "workspace-a", "user_id": "user-a", "agent_id": "general-agent", "title": "Source (fork)"}]
    assert copied == [{"tenant_id": "default", "session_id": "ses-fork", "run_id": None, "role": "user", "content": "one", "metadata_json": {}}]
    assert ensured_users == [("default", "user-a", "A")]

    await session_actions.fork_session_message(object(), principal=admin, session_id="ses-source", message_id="msg-2")
    assert created[-1]["user_id"] == "admin-a"
    assert [item["content"] for item in copied[-2:]] == ["one", "two"]
    assert ensured_users[-1] == ("default", "admin-a", "Admin")

    for principal, session_id, message_id in (
        (other_user, "ses-source", "msg-1"),
        (other_tenant, "ses-source", "msg-1"),
        (owner, "ses-source", "msg-missing"),
        (owner, "ses-missing", "msg-1"),
    ):
        with pytest.raises(session_actions.SessionActionNotFoundError):
            await session_actions.fork_session_message(object(), principal=principal, session_id=session_id, message_id=message_id)
    assert len(created) == 2
    assert len(copied) == 3


def test_lambchat_session_action_routes_are_thin_service_adapters(monkeypatch):
    from app import session_actions

    calls = []

    async def rename(_conn, *, principal, session_id, title):
        calls.append(("rename", principal.user_id, session_id, title))
        return {"id": session_id, "workspace_id": "default", "agent_id": "general-agent", "title": title, "status": "active"}

    async def delete(_conn, *, principal, session_id):
        calls.append(("delete", principal.user_id, session_id))
        return {"session": {"id": session_id, "workspace_id": "default", "agent_id": "general-agent", "title": "Deleted", "status": "deleted"}, "already_deleted": False}

    async def fork(_conn, *, principal, session_id, message_id):
        calls.append(("fork", principal.user_id, session_id, message_id))
        return {"source_session_id": session_id, "session": {"id": "ses-fork", "workspace_id": "default", "agent_id": "general-agent", "title": "Fork", "status": "active"}}

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.lambchat_compat.transaction", fake_transaction)
    monkeypatch.setattr(session_actions, "rename_session", rename)
    monkeypatch.setattr(session_actions, "delete_session", delete)
    monkeypatch.setattr(session_actions, "fork_session_message", fork)
    client = TestClient(create_app())

    assert client.patch("/api/sessions/ses-a", headers=action_headers(), json={"name": "Renamed"}).status_code == 200
    assert client.delete("/api/sessions/ses-a", headers=action_headers()).status_code == 200
    assert client.post("/api/sessions/ses-a/messages/msg-a/fork", headers=action_headers()).status_code == 200
    assert calls == [("rename", "user-a", "ses-a", "Renamed"), ("delete", "user-a", "ses-a"), ("fork", "user-a", "ses-a", "msg-a")]


@pytest.fixture(autouse=True)
def default_lambchat_stream_projection(monkeypatch):
    async def empty_run_events(conn, *, tenant_id, run_id):
        return []

    async def empty_run_artifacts(conn, *, tenant_id, run_id):
        return []

    monkeypatch.setattr("app.routes.lambchat_compat.repositories.list_run_events", empty_run_events)
    monkeypatch.setattr("app.routes.lambchat_compat.repositories.list_run_artifacts", empty_run_artifacts)


def test_chat_stream_request_accepts_lambchat_body_shape():
    request = ChatStreamRequest.model_validate(
        {
            "message": "hello",
            "session_id": None,
            "agent_options": {"temperature": 0},
            "attachments": [],
            "disabled_skills": [],
            "enabled_skills": ["general-chat"],
            "disabled_mcp_tools": [],
            "user_timezone": "Asia/Shanghai",
        }
    )

    assert request.message == "hello"
    assert request.agent_options == {"temperature": 0}
    assert request.enabled_skills == ["general-chat"]


def test_lambchat_sessions_project_public_agent_ids(monkeypatch):
    async def fake_list_authorized_sessions(conn, *, tenant_id, user_id):
        assert (tenant_id, user_id) == ("default", "user-a")
        return [
            {
                "id": "ses_review",
                "agent_id": "qa-word-review",
                "workspace_id": "default",
                "title": "审核",
                "status": "active",
                "created_at": None,
                "updated_at": None,
            },
            {
                "id": "ses_translate",
                "agent_id": "baoyu-translate",
                "workspace_id": "default",
                "title": "翻译",
                "status": "active",
                "created_at": None,
                "updated_at": None,
            },
        ]

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.lambchat_compat.transaction", fake_transaction)
    monkeypatch.setattr(
        "app.routes.lambchat_compat.repositories.list_authorized_sessions",
        fake_list_authorized_sessions,
    )
    client = TestClient(create_app())

    response = client.get("/api/sessions", headers=auth_headers())

    assert response.status_code == 200
    sessions = response.json()["sessions"]
    assert sessions[0]["agent_id"] == "document-review"
    assert sessions[0]["metadata"]["agent_id"] == "document-review"
    assert sessions[1]["agent_id"] == "document-translation"
    assert sessions[1]["metadata"]["agent_id"] == "document-translation"
    assert "qa-word-review" not in str(response.json())
    assert "baoyu-translate" not in str(response.json())


def test_lambchat_session_detail_projects_public_agent_id(monkeypatch):
    async def fake_get_authorized_lambchat_session(conn, *, tenant_id, user_id, session_id):
        assert (tenant_id, user_id, session_id) == ("default", "user-a", "ses_review")
        return {
            "id": session_id,
            "agent_id": "qa-word-review",
            "workspace_id": "default",
            "title": "审核",
            "status": "active",
            "created_at": None,
            "updated_at": None,
        }

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.lambchat_compat.transaction", fake_transaction)
    monkeypatch.setattr(
        "app.routes.lambchat_compat.repositories.get_authorized_lambchat_session",
        fake_get_authorized_lambchat_session,
    )
    client = TestClient(create_app())

    response = client.get("/api/sessions/ses_review", headers=auth_headers())

    assert response.status_code == 200
    payload = response.json()
    assert payload["agent_id"] == "document-review"
    assert payload["metadata"]["agent_id"] == "document-review"
    assert "qa-word-review" not in str(payload)


async def test_lambchat_agent_repository_exposes_only_canonical_agents():
    from app.repositories import list_lambchat_agents

    class FakeCursor:
        async def fetchall(self):
            return []

    class RecordingConnection:
        def __init__(self):
            self.executed = []

        async def execute(self, sql, params):
            self.executed.append((" ".join(sql.split()), params))
            return FakeCursor()

    conn = RecordingConnection()

    rows = await list_lambchat_agents(conn, tenant_id="default")

    assert rows == []
    sql, params = conn.executed[-1]
    assert "agents.id in ('general-agent', 'baoyu-translate', 'qa-word-review')" in sql
    assert "sop-assistant" not in sql
    assert "agents.status = 'active'" in sql
    assert "skills.status = 'active'" in sql
    assert "skill_release_policies.current_version" in sql
    assert "coalesce(skill_versions.status, 'active') as skill_version_status" in sql
    assert "skill_release_policies.previous_version as release_policy_previous_version" in sql
    assert "previous_skill_versions.status as release_policy_previous_version_status" in sql
    assert params == ("default",)


def test_lambchat_bootstrap_endpoints_match_frontend_contract():
    client = TestClient(create_app())

    expectations = {
        "/api/auth/oauth/providers": {"registration_enabled": False},
        "/api/auth/permissions": {"groups": list, "all_permissions": list},
        "/api/agent/models/available": {"default_model_id": "deepseek-v4-flash"},
        "/api/agent/models/": {"enabled_count": 2},
        "/api/roles/?limit=200": {"roles": list, "total": 0, "skip": 0, "limit": 200},
        "/api/settings/": {"settings": {}},
        "/api/version": {"version": "ai-platform-poc"},
        "/api/projects": [],
        "/api/notifications/active": {"notifications": []},
        "/api/upload/config": {"categories": ["document"], "enabled": True, "uploadLimits": dict},
        "/api/tools": {"tools": []},
    }

    for path, expected in expectations.items():
        response = client.get(path)
        assert response.status_code == 200, path
        payload = response.json()
        if isinstance(expected, list):
            assert payload == expected
            continue
        for key, value in expected.items():
            if value is list:
                assert isinstance(payload[key], list), path
            elif value is dict:
                assert isinstance(payload[key], dict), path
            else:
                assert payload[key] == value, path


def test_lambchat_bootstrap_routes_do_not_shadow_authenticated_workbench_projections(monkeypatch):
    from tests.test_workbench_projection_routes import install_workbench_route_fakes
    from tests.test_workbench_projection_routes import user_headers

    install_workbench_route_fakes(monkeypatch)
    client = TestClient(create_app())

    anonymous_settings = client.get("/api/settings/")
    authenticated_settings = client.get("/api/settings/", headers=user_headers())
    anonymous_notifications = client.get("/api/notifications/active")
    authenticated_notifications = client.get("/api/notifications/active", headers=user_headers())

    assert anonymous_settings.status_code == 200
    assert anonymous_settings.json() == {"settings": {}}
    assert authenticated_settings.status_code == 200
    assert set(authenticated_settings.json()["settings"]) == {"personal_preferences", "system_runtime"}
    assert anonymous_notifications.status_code == 200
    assert anonymous_notifications.json() == {"notifications": []}
    assert authenticated_notifications.status_code == 200
    assert authenticated_notifications.json()[0]["id"] == "platform-announcement"


def test_lambchat_model_catalog_comes_from_settings(monkeypatch):
    current_settings = type(
        "S",
        (),
        {
            "openai_model": "deepseek-v4-flash",
            "anthropic_model": "deepseek-v4-flash",
            "claude_agent_model": "deepseek-v4-pro",
            "default_model_id": "deepseek-v4-pro",
            "model_catalog_json": (
                '[{"id":"deepseek-v4-flash","label":"DeepSeek V4 Flash","provider":"new-api","max_input_tokens":128000},'
                '{"id":"deepseek-v4-pro","label":"DeepSeek V4 Pro","provider":"new-api","max_input_tokens":128000}]'
            ),
        },
    )()
    monkeypatch.setattr("app.routes.lambchat_compat.get_settings", lambda: current_settings)
    client = TestClient(create_app())

    response = client.get("/api/agent/models/available")

    assert response.status_code == 200
    payload = response.json()
    assert payload["default_model_id"] == "deepseek-v4-pro"
    assert payload["count"] == 2
    assert payload["enabled_count"] == 2
    assert [model["id"] for model in payload["models"]] == ["deepseek-v4-flash", "deepseek-v4-pro"]
    assert payload["models"][1]["label"] == "DeepSeek V4 Pro"
    assert payload["models"][1]["profile"]["max_input_tokens"] == 128000


def test_lambchat_upload_file_endpoint_matches_frontend_contract(monkeypatch, tmp_path):
    async def fake_upload_platform_file(file, workspace_id, session_id, principal):
        assert workspace_id == "default"
        assert session_id is None
        assert principal.user_id == "user-a"
        return SimpleNamespace(
            file_id="file_uploaded",
            storage_key="tenants/default/files/file_uploaded/sample.docx",
            sha256="abc123",
            size_bytes=12,
        )

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.lambchat_compat.upload_platform_file", fake_upload_platform_file)
    client = TestClient(create_app())
    sample = tmp_path / "sample.docx"
    sample.write_bytes(b"fake-docx")

    with sample.open("rb") as handle:
        response = client.post(
            "/api/upload/file?folder=uploads",
            headers=auth_headers(),
            files={"file": ("sample.docx", handle, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["key"] == "file_uploaded"
    assert payload["file_id"] == "file_uploaded"
    assert payload["name"] == "sample.docx"
    assert payload["type"] == "uploads"
    assert payload["mimeType"] == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    assert payload["size"] == 12


def test_lambchat_upload_check_returns_not_existing():
    client = TestClient(create_app())

    response = client.post("/api/upload/check", json={"hash": "abc", "size": 12, "name": "sample.docx"})

    assert response.status_code == 200
    assert response.json() == {"exists": False}


def test_lambchat_permissions_include_user_and_admin_capabilities():
    client = TestClient(create_app())

    response = client.get("/api/auth/permissions")

    assert response.status_code == 200
    values = {item["value"] for item in response.json()["all_permissions"]}
    assert {
        "agent:use",
        "artifact:download",
        "model:admin",
        "settings:manage",
        "admin:status",
    }.issubset(values)


def test_lambchat_profile_endpoint_returns_principal_and_metadata(monkeypatch):
    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    client = TestClient(create_app())

    response = client.put(
        "/api/auth/profile/metadata",
        headers=auth_headers(),
        json={"metadata": {"pinned_model_ids": ["deepseek-v4-flash"]}},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["id"] == "user-a"
    assert payload["metadata"]["display_name"] == "User A"
    assert payload["metadata"]["source"] == "trusted-header"
    assert payload["metadata"]["pinned_model_ids"] == ["deepseek-v4-flash"]


def test_lambchat_profile_keeps_empty_principal_permissions(monkeypatch):
    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    client = TestClient(create_app())

    me_response = client.get("/api/auth/me", headers=auth_headers())
    profile_response = client.get("/api/auth/profile", headers=auth_headers())

    assert me_response.status_code == 200
    assert profile_response.status_code == 200
    assert me_response.json()["permissions"] == []
    assert profile_response.json()["permissions"] == []


def test_lambchat_sse_stream_emits_finished_run_answer(monkeypatch):
    async def fake_get_authorized_run(conn, *, tenant_id, user_id, run_id):
        assert user_id == "user-a"
        return {
            "id": run_id,
            "session_id": "ses_a",
            "status": "succeeded",
            "result_json": {"message": "ai-platform response"},
            "error_code": None,
            "error_message": None,
        }

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.lambchat_compat.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.lambchat_compat.repositories.get_authorized_run", fake_get_authorized_run)
    client = TestClient(create_app())

    response = client.get("/api/chat/sessions/ses_a/stream?run_id=run_a", headers=auth_headers())

    assert response.status_code == 200
    assert "event: message:chunk" in response.text
    assert "ai-platform response" in response.text
    assert "event: done" in response.text


def test_lambchat_sse_stream_replays_run_events_and_artifact_cards(monkeypatch):
    async def fake_get_authorized_run(conn, *, tenant_id, user_id, run_id):
        assert user_id == "user-a"
        return {
            "id": run_id,
            "session_id": "ses_a",
            "status": "succeeded",
            "result_json": {"message": "review complete"},
            "error_code": None,
            "error_message": None,
        }

    async def fake_list_run_events(conn, *, tenant_id, run_id):
        return [
            {
                "id": "evt-tool",
                "trace_id": "trace-run-a",
                "schema_version": "ai-platform.event-envelope.v1",
                "sequence": 4,
                "event_type": "mcp_tool_denied",
                "stage": "tool_policy",
                "message": "tool permission required",
                "severity": "warning",
                "visible_to_user": True,
                "error_code": None,
                "latency_ms": None,
                "input_token_count": 0,
                "output_token_count": 0,
                "total_token_count": 0,
                "estimated_cost_minor": 0,
                "payload_json": {
                    "tool_id": "customer-write",
                    "reason": "requires confirmation",
                    "storage_key": "tenants/default/private/tool.json",
                },
                "created_at": None,
            }
        ]

    async def fake_list_run_artifacts(conn, *, tenant_id, run_id):
        return [
            {
                "id": "art-reviewed",
                "trace_id": "trace-run-a",
                "artifact_type": "reviewed_docx",
                "label": "审核 Word",
                "content_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                "storage_key": "tenants/default/runs/run-a/artifacts/reviewed.docx",
                "size_bytes": 123,
                "manifest_version": "ai-platform.artifact-manifest.v1",
                "manifest_json": {
                    "local_path": "/tmp/private/reviewed.docx",
                    "schema_version": "ai-platform.artifact-manifest.v1",
                },
                "created_at": None,
            }
        ]

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.lambchat_compat.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.lambchat_compat.repositories.get_authorized_run", fake_get_authorized_run)
    monkeypatch.setattr("app.routes.lambchat_compat.repositories.list_run_events", fake_list_run_events)
    monkeypatch.setattr("app.routes.lambchat_compat.repositories.list_run_artifacts", fake_list_run_artifacts)
    client = TestClient(create_app())

    response = client.get("/api/chat/sessions/ses_a/stream?run_id=run_a", headers=auth_headers())

    assert response.status_code == 200
    assert "event: run_event" in response.text
    assert '"event_type": "agent_step_blocked"' in response.text
    assert "当前处理步骤未获授权，正在等待权限调整" in response.text
    assert "tool permission required" not in response.text
    assert "event: artifact_card" in response.text
    assert '"artifact_id": "art-reviewed"' in response.text
    assert '"/api/ai/artifacts/art-reviewed/download"' in response.text
    assert "event: message:chunk" in response.text
    assert "review complete" in response.text
    assert "storage_key" not in response.text
    assert "tenants/default" not in response.text
    assert "/tmp/private" not in response.text


def test_lambchat_sse_stream_reports_bad_event_projection_as_sse_error(monkeypatch):
    async def fake_get_authorized_run(conn, *, tenant_id, user_id, run_id):
        assert user_id == "user-a"
        return {
            "id": run_id,
            "session_id": "ses_a",
            "status": "running",
            "result_json": {},
            "error_code": None,
            "error_message": None,
        }

    async def fake_list_run_events(conn, *, tenant_id, run_id):
        return [
            {
                "id": "evt-bad",
                "trace_id": "trace-run-a",
                "schema_version": "bad-schema",
                "sequence": 1,
                "event_type": "worker_started",
                "stage": "worker",
                "message": "Run started",
                "severity": "info",
                "visible_to_user": True,
                "error_code": None,
                "payload_json": {"visible_to_user": True},
                "created_at": None,
            }
        ]

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.lambchat_compat.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.lambchat_compat.repositories.get_authorized_run", fake_get_authorized_run)
    monkeypatch.setattr("app.routes.lambchat_compat.repositories.list_run_events", fake_list_run_events)
    client = TestClient(create_app())

    response = client.get("/api/chat/sessions/ses_a/stream?run_id=run_a", headers=auth_headers())

    assert response.status_code == 200
    assert "event: error" in response.text
    assert "invalid_event_schema_version" in response.text
    assert "event: done" in response.text
    assert '"status": "error"' in response.text


def test_lambchat_sse_stream_places_artifact_card_before_terminal_run_event(monkeypatch):
    async def fake_get_authorized_run(conn, *, tenant_id, user_id, run_id):
        assert user_id == "user-a"
        return {
            "id": run_id,
            "session_id": "ses_a",
            "status": "succeeded",
            "result_json": {"message": "review complete"},
            "error_code": None,
            "error_message": None,
        }

    async def fake_list_run_events(conn, *, tenant_id, run_id):
        base = {
            "trace_id": "trace-run-a",
            "schema_version": "ai-platform.event-envelope.v1",
            "severity": "info",
            "visible_to_user": True,
            "error_code": None,
            "latency_ms": None,
            "input_token_count": 0,
            "output_token_count": 0,
            "total_token_count": 0,
            "estimated_cost_minor": 0,
            "created_at": None,
        }
        return [
            {
                **base,
                "id": "evt-artifact",
                "sequence": 3,
                "event_type": "artifact_created",
                "stage": "artifact",
                "message": "Artifact created",
                "payload_json": {"artifact_id": "art-reviewed", "visible_to_user": True},
            },
            {
                **base,
                "id": "evt-message",
                "sequence": 4,
                "event_type": "assistant_message_created",
                "stage": "message",
                "message": "Assistant response is ready",
                "payload_json": {"artifact_count": 1, "visible_to_user": True},
            },
            {
                **base,
                "id": "evt-succeeded",
                "sequence": 5,
                "event_type": "run_succeeded",
                "stage": "worker",
                "message": "Run succeeded",
                "payload_json": {"artifact_count": 1, "visible_to_user": True},
            },
        ]

    async def fake_list_run_artifacts(conn, *, tenant_id, run_id):
        return [
            {
                "id": "art-reviewed",
                "trace_id": "trace-run-a",
                "artifact_type": "reviewed_docx",
                "label": "审核 Word",
                "content_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                "storage_key": "tenants/default/runs/run-a/artifacts/reviewed.docx",
                "size_bytes": 123,
                "manifest_version": "ai-platform.artifact-manifest.v1",
                "manifest_json": {},
                "created_at": None,
            }
        ]

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.lambchat_compat.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.lambchat_compat.repositories.get_authorized_run", fake_get_authorized_run)
    monkeypatch.setattr("app.routes.lambchat_compat.repositories.list_run_events", fake_list_run_events)
    monkeypatch.setattr("app.routes.lambchat_compat.repositories.list_run_artifacts", fake_list_run_artifacts)
    client = TestClient(create_app())

    response = client.get("/api/chat/sessions/ses_a/stream?run_id=run_a", headers=auth_headers())

    assert response.status_code == 200
    artifact_index = response.text.index("event: artifact_card")
    final_index = response.text.index("event: message:chunk")
    terminal_index = response.text.index("event: done")
    assert artifact_index < final_index < terminal_index
    assert '"event_type": "run_succeeded"' not in response.text
    assert '"run_id": "run_a", "status": "succeeded"' in response.text


def test_lambchat_sse_stream_defers_persisted_terminal_until_status_and_final_payload(monkeypatch):
    calls = 0

    async def fake_get_authorized_run(conn, *, tenant_id, user_id, run_id):
        nonlocal calls
        calls += 1
        return {
            "id": run_id,
            "session_id": "ses_a",
            "status": "running" if calls == 1 else "succeeded",
            "result_json": {"message": "final answer"},
            "error_code": None,
            "error_message": None,
        }

    async def fake_list_run_events(conn, *, tenant_id, run_id):
        return [
            {
                "id": "evt-succeeded",
                "trace_id": "trace-run-a",
                "schema_version": "ai-platform.event-envelope.v1",
                "sequence": 9,
                "event_type": "run_succeeded",
                "stage": "worker",
                "message": "Run succeeded",
                "severity": "info",
                "visible_to_user": True,
                "error_code": None,
                "latency_ms": None,
                "input_token_count": 0,
                "output_token_count": 0,
                "total_token_count": 0,
                "estimated_cost_minor": 0,
                "payload_json": {"visible_to_user": True},
                "created_at": None,
            }
        ]

    async def no_sleep(_seconds):
        return None

    async def fake_list_run_artifacts(conn, *, tenant_id, run_id):
        return []

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.lambchat_compat.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.lambchat_compat.get_settings", lambda: SimpleNamespace(run_event_stream_max_heartbeats=2))
    monkeypatch.setattr("app.routes.lambchat_compat.asyncio.sleep", no_sleep)
    monkeypatch.setattr("app.routes.lambchat_compat.repositories.get_authorized_run", fake_get_authorized_run)
    monkeypatch.setattr("app.routes.lambchat_compat.repositories.list_run_events", fake_list_run_events)
    monkeypatch.setattr("app.routes.lambchat_compat.repositories.list_run_artifacts", fake_list_run_artifacts)
    client = TestClient(create_app())

    response = client.get("/api/chat/sessions/ses_a/stream?run_id=run_a", headers=auth_headers())

    assert response.status_code == 200
    assert calls == 2
    assert '"event_type": "run_succeeded"' not in response.text
    assert response.text.index("final answer") < response.text.index("event: done")


def test_lambchat_sse_stream_does_not_duplicate_answer_when_assistant_delta_was_persisted(monkeypatch):
    async def fake_get_authorized_run(conn, *, tenant_id, user_id, run_id):
        assert user_id == "user-a"
        return {
            "id": run_id,
            "session_id": "ses_a",
            "status": "succeeded",
            "result_json": {"message": "hello from worker"},
            "error_code": None,
            "error_message": None,
        }

    async def fake_list_run_events(conn, *, tenant_id, run_id):
        return [
            {
                "id": "evt-delta",
                "trace_id": "trace-run-a",
                "schema_version": "ai-platform.event-envelope.v1",
                "sequence": 2,
                "event_type": "assistant_delta",
                "stage": "message",
                "message": "hello from worker",
                "severity": "info",
                "visible_to_user": True,
                "error_code": None,
                "latency_ms": None,
                "input_token_count": 0,
                "output_token_count": 0,
                "total_token_count": 0,
                "estimated_cost_minor": 0,
                "payload_json": {"content": "hello from worker", "visible_to_user": True},
                "created_at": None,
            }
        ]

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.lambchat_compat.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.lambchat_compat.repositories.get_authorized_run", fake_get_authorized_run)
    monkeypatch.setattr("app.routes.lambchat_compat.repositories.list_run_events", fake_list_run_events)
    client = TestClient(create_app())

    response = client.get("/api/chat/sessions/ses_a/stream?run_id=run_a", headers=auth_headers())

    assert response.status_code == 200
    assert response.text.count("hello from worker") == 1
    assert '"projection_kind": "assistant_final"' in response.text
    assert "evt-delta" not in response.text


@pytest.mark.parametrize(
    (
        "agent_id",
        "skill_id",
        "private_identifier",
        "public_identifier",
        "message",
        "expected_answer",
    ),
    [
        (
            "general-agent",
            "general-chat",
            "general-chat",
            "general-agent",
            "当前 general-chat 没有 Bash 工具，无法执行该命令。",
            "当前 general-agent 没有 Bash 工具，无法执行该命令。",
        ),
        (
            "qa-word-review",
            "qa-file-reviewer",
            "qa-word-review",
            "document-review",
            "当前 qa-word-review 没有 Bash 工具，无法执行该命令。",
            "当前 document-review 没有 Bash 工具，无法执行该命令。",
        ),
    ],
)
def test_lambchat_terminal_answer_replaces_private_identifier_for_sse_and_history(
    monkeypatch,
    agent_id,
    skill_id,
    private_identifier,
    public_identifier,
    message,
    expected_answer,
):
    run = {
        "id": "run_a",
        "session_id": "ses_a",
        "trace_id": "trace_run_a",
        "agent_id": agent_id,
        "skill_id": skill_id,
        "status": "succeeded",
        "result_json": {"message": message},
        "error_code": None,
        "error_message": None,
        "finished_at": "2026-07-19T00:00:00Z",
    }

    async def fake_get_authorized_lambchat_session(conn, *, tenant_id, user_id, session_id):
        return {"id": session_id}

    async def fake_get_authorized_run(conn, *, tenant_id, user_id, run_id):
        assert (tenant_id, user_id, run_id) == ("default", "user-a", "run_a")
        return run

    async def fake_list_run_events(conn, *, tenant_id, run_id):
        return [
            {
                "id": "evt-delta",
                "trace_id": "trace_run_a",
                "schema_version": "ai-platform.event-envelope.v1",
                "sequence": 1,
                "event_type": "assistant_delta",
                "stage": "answer",
                "message": "",
                "severity": "info",
                "visible_to_user": True,
                "error_code": None,
                "payload_json": {
                    "delta": "旧的部分输出",
                    "source": "worker_answer_delta_v1",
                    "visible_to_user": True,
                    "severity": "info",
                },
                "created_at": "2026-07-19T00:00:00Z",
            }
        ]

    async def fake_list_run_artifacts(conn, *, tenant_id, run_id):
        return []

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.lambchat_compat.transaction", fake_transaction)
    monkeypatch.setattr(
        "app.routes.lambchat_compat.get_settings",
        lambda: SimpleNamespace(run_event_stream_max_heartbeats=1),
    )
    monkeypatch.setattr(
        "app.routes.lambchat_compat.repositories.get_authorized_lambchat_session",
        fake_get_authorized_lambchat_session,
    )
    monkeypatch.setattr(
        "app.routes.lambchat_compat.repositories.get_authorized_run",
        fake_get_authorized_run,
    )
    monkeypatch.setattr(
        "app.routes.lambchat_compat.repositories.list_run_events",
        fake_list_run_events,
    )
    monkeypatch.setattr(
        "app.routes.lambchat_compat.repositories.list_run_artifacts",
        fake_list_run_artifacts,
    )
    client = TestClient(create_app())

    stream_response = client.get(
        "/api/chat/sessions/ses_a/stream?run_id=run_a",
        headers=auth_headers(),
    )
    history_response = client.get(
        "/api/sessions/ses_a/events?run_id=run_a",
        headers=auth_headers(),
    )

    assert stream_response.status_code == 200
    assert history_response.status_code == 200
    stream_payloads = [
        json.loads(line.removeprefix("data: "))
        for line in stream_response.text.splitlines()
        if line.startswith("data: ")
    ]
    stream_final = next(
        payload
        for payload in stream_payloads
        if payload.get("projection_kind") == "assistant_final"
    )
    history_final = next(
        event["data"]
        for event in history_response.json()["events"]
        if event["data"].get("projection_kind") == "assistant_final"
    )
    assert stream_final == history_final
    assert stream_final["content"] == expected_answer
    assert "没有 Bash 工具，无法执行" in stream_final["content"]
    assert public_identifier in stream_final["content"]
    assert private_identifier not in stream_response.text
    assert private_identifier not in history_response.text
    assert stream_response.text.count(expected_answer) == 1
    assert "evt-delta" not in stream_response.text
    assert "evt-delta" not in history_response.text
    assert "旧的部分输出" not in stream_response.text
    assert "旧的部分输出" not in history_response.text


def test_lambchat_failed_terminal_uses_same_allowlist_for_sse_and_exact_run_reload(monkeypatch):
    raw_terms = (
        "command=render-report --private-param=amber",
        "provider-model=solstice-3 sdk diagnostic",
        "reasoning-draft request-id=orchid digest=0123456789abcdef",
        "url=https://executor.internal.example.invalid/v1",
    )
    run = {
        "id": "run_a",
        "session_id": "ses_a",
        "trace_id": "trace_run_a",
        "agent_id": "general-agent",
        "skill_id": "general-chat",
        "status": "failed",
        "result_json": {
            "message": raw_terms[0],
            "sdk_error": raw_terms[1],
            "error": {"message": raw_terms[2]},
        },
        "error_code": "claude_agent_sdk_runtime_error",
        "error_message": raw_terms[3],
        "finished_at": "2026-07-23T00:00:00Z",
    }

    async def fake_get_authorized_lambchat_session(conn, *, tenant_id, user_id, session_id):
        return {"id": session_id}

    async def fake_get_authorized_run(conn, *, tenant_id, user_id, run_id):
        assert (tenant_id, user_id, run_id) == ("default", "user-a", "run_a")
        return run

    async def fake_list_run_events(conn, *, tenant_id, run_id):
        return [
            {
                "id": "evt-terminal",
                "trace_id": "trace_run_a",
                "schema_version": "ai-platform.event-envelope.v1",
                "sequence": 1,
                "event_type": "error",
                "stage": "executor",
                "message": raw_terms[0],
                "severity": "error",
                "visible_to_user": True,
                "error_code": "claude_agent_sdk_runtime_error",
                "payload_json": {
                    "result": {
                        "message": raw_terms[0],
                        "sdk_error": raw_terms[1],
                        "error": {"message": raw_terms[2]},
                    },
                    "error_message": raw_terms[3],
                    "visible_to_user": True,
                },
                "created_at": "2026-07-23T00:00:00Z",
            }
        ]

    async def fake_list_run_artifacts(conn, *, tenant_id, run_id):
        return []

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.lambchat_compat.transaction", fake_transaction)
    monkeypatch.setattr(
        "app.routes.lambchat_compat.get_settings",
        lambda: SimpleNamespace(run_event_stream_max_heartbeats=1),
    )
    monkeypatch.setattr(
        "app.routes.lambchat_compat.repositories.get_authorized_lambchat_session",
        fake_get_authorized_lambchat_session,
    )
    monkeypatch.setattr(
        "app.routes.lambchat_compat.repositories.get_authorized_run",
        fake_get_authorized_run,
    )
    monkeypatch.setattr(
        "app.routes.lambchat_compat.repositories.list_run_events",
        fake_list_run_events,
    )
    monkeypatch.setattr(
        "app.routes.lambchat_compat.repositories.list_run_artifacts",
        fake_list_run_artifacts,
    )
    client = TestClient(create_app())

    stream_response = client.get(
        "/api/chat/sessions/ses_a/stream?run_id=run_a",
        headers=auth_headers(),
    )
    history_response = client.get(
        "/api/sessions/ses_a/events?run_id=run_a",
        headers=auth_headers(),
    )

    assert stream_response.status_code == 200
    assert history_response.status_code == 200
    stream_payloads = [
        json.loads(line.removeprefix("data: "))
        for line in stream_response.text.splitlines()
        if line.startswith("data: ")
    ]
    stream_detail = next(payload for payload in stream_payloads if payload.get("detail_kind") == "failed")
    history_detail = next(
        event["data"]
        for event in history_response.json()["events"]
        if event["data"].get("detail_kind") == "failed"
    )
    assert stream_detail == history_detail
    assert stream_detail["detail_code"] == "model_service_unavailable"
    assert stream_detail["message"] == "模型服务暂时不可用。请稍后重试；如问题持续，请联系管理员。"
    for rendered in (stream_response.text, history_response.text):
        assert all(term not in rendered for term in raw_terms)


@pytest.mark.parametrize(
    ("agent_id", "skill_id", "message", "expected_content"),
    [
        (
            "general-agent",
            "x",
            "execute exactly once",
            "execute exactly once",
        ),
        (
            "general-agent",
            "x",
            "x 没有 Bash 工具，无法执行。",
            "任务完成",
        ),
        (
            "general-agent",
            "general-chat",
            "non-general-chat-support 没有 Bash 工具，无法执行。",
            "non-general-chat-support 没有 Bash 工具，无法执行。",
        ),
        (
            "general-agent",
            "general-chat",
            "当前（general-chat），没有 Bash 工具，无法执行。",
            "当前（general-agent），没有 Bash 工具，无法执行。",
        ),
        (
            "general-agent",
            "general-chat",
            "请查看 https://general-chat.example.com/help",
            "请查看 https://general-chat.example.com/help",
        ),
        (
            "general-agent",
            "general-chat",
            "team.general-chat.policy 不可用",
            "team.general-chat.policy 不可用",
        ),
        (
            "general-agent",
            "general-chat",
            "team_general-chat_policy 不可用",
            "team_general-chat_policy 不可用",
        ),
        (
            "general-agent",
            "general-chat",
            "team:general-chat:policy 不可用",
            "team:general-chat:policy 不可用",
        ),
        (
            "general-agent",
            "general-chat",
            "团队general-chat策略不可用",
            "团队general-chat策略不可用",
        ),
        (
            "unknown-agent",
            "unknown-skill",
            "unknown-skill 没有 Bash 工具，无法执行。",
            "任务完成",
        ),
    ],
    ids=[
        "one-character-substring",
        "one-character-exact-unknown",
        "larger-token",
        "punctuated-exact-token",
        "url-domain-token",
        "dot-qualified-token",
        "underscore-qualified-token",
        "colon-qualified-token",
        "unicode-adjacent-token",
        "unknown-exact-identifier",
    ],
)
def test_lambchat_terminal_answer_uses_trusted_identifier_token_boundaries(
    agent_id,
    skill_id,
    message,
    expected_content,
):
    from app.routes.lambchat_compat import _terminal_final_payload

    final_payload = _terminal_final_payload(
        {
            "id": "run_a",
            "agent_id": agent_id,
            "skill_id": skill_id,
            "status": "succeeded",
            "result_json": {"message": message},
        }
    )

    assert final_payload is not None
    _, payload, _ = final_payload
    assert payload["content"] == expected_content


@pytest.mark.parametrize(
    ("agent_id", "skill_id", "message", "expected_content"),
    [
        (
            "qa-word-review",
            "general-chat",
            "general-chat 拒绝执行",
            "任务完成",
        ),
        (
            "qa-word-review",
            "general-chat",
            "qa-word-review 拒绝执行",
            "任务完成",
        ),
        (
            "unknown-agent",
            "general-chat",
            "general-chat 拒绝执行",
            "任务完成",
        ),
        (
            "unknown-agent",
            "general-chat",
            "unknown-agent 拒绝执行",
            "任务完成",
        ),
        (
            "qa-word-review",
            "unknown-skill",
            "unknown-skill 拒绝执行",
            "任务完成",
        ),
        (
            "qa-word-review",
            "unknown-skill",
            "qa-word-review 拒绝执行",
            "任务完成",
        ),
        (
            "qa-word-review",
            "",
            "qa-word-review 拒绝执行",
            "document-review 拒绝执行",
        ),
        (
            "",
            "general-chat",
            "general-chat 拒绝执行",
            "任务完成",
        ),
    ],
    ids=[
        "mapped-mismatch-skill-side",
        "mapped-mismatch-agent-side",
        "mapped-skill-unmapped-agent-skill-side",
        "mapped-skill-unmapped-agent-agent-side",
        "unmapped-skill-mapped-agent-skill-side",
        "unmapped-skill-mapped-agent-agent-side",
        "missing-skill-mapped-agent",
        "mapped-skill-missing-agent",
    ],
)
def test_lambchat_terminal_answer_requires_consistent_identifier_capabilities(
    agent_id,
    skill_id,
    message,
    expected_content,
):
    from app.routes.lambchat_compat import _terminal_final_payload

    final_payload = _terminal_final_payload(
        {
            "id": "run_a",
            "agent_id": agent_id,
            "skill_id": skill_id,
            "status": "succeeded",
            "result_json": {"message": message},
        }
    )

    assert final_payload is not None
    _, payload, _ = final_payload
    assert payload["content"] == expected_content


@pytest.mark.parametrize(
    ("agent_id", "skill_id", "message", "private_marker"),
    [
        (
            "general-agent",
            "general-chat",
            "general-chat 拒绝读取 /var/lib/private/answer.txt",
            "/var/",
        ),
        (
            "executor_native",
            "custom-skill",
            "custom-skill 拒绝暴露运行时详情",
            "executor_native",
        ),
    ],
)
def test_lambchat_terminal_answer_identifier_replacement_keeps_private_text_gate(
    agent_id,
    skill_id,
    message,
    private_marker,
):
    from app.routes.lambchat_compat import _terminal_final_payload

    final_payload = _terminal_final_payload(
        {
            "id": "run_a",
            "agent_id": agent_id,
            "skill_id": skill_id,
            "status": "succeeded",
            "result_json": {"message": message},
        }
    )

    assert final_payload is not None
    _, payload, _ = final_payload
    assert payload["content"] == "任务完成"
    assert private_marker not in str(payload)
    assert skill_id not in str(payload)


def test_lambchat_sse_stream_projects_only_safe_versioned_chat_progress(monkeypatch):
    async def fake_get_authorized_run(conn, *, tenant_id, user_id, run_id):
        assert (tenant_id, user_id) == ("default", "user-a")
        return {
            "id": run_id,
            "session_id": "ses_a",
            "agent_id": "qa-word-review",
            "skill_id": "qa-file-reviewer",
            "status": "running",
            "result_json": {},
            "error_code": None,
            "error_message": None,
        }

    async def fake_list_run_events(conn, *, tenant_id, run_id):
        base = {
            "trace_id": "trace-run-a",
            "schema_version": "ai-platform.event-envelope.v1",
            "severity": "info",
            "visible_to_user": True,
            "error_code": None,
            "created_at": None,
        }
        return [
            {
                **base,
                "id": "evt-queued",
                "sequence": 1,
                "event_type": "queued",
                "stage": "queue",
                "message": "internal queue source",
                "visible_to_user": False,
                "payload_json": {"queue_position": 3, "visible_to_user": False},
            },
            {
                **base,
                "id": "evt-worker",
                "sequence": 2,
                "event_type": "worker_started",
                "stage": "worker",
                "message": "worker alpha at /var/lib/private",
                "payload_json": {"worker_id": "worker-alpha", "visible_to_user": True},
            },
            {
                **base,
                "id": "evt-delta",
                "sequence": 3,
                "event_type": "assistant_delta",
                "stage": "answer",
                "message": "",
                "payload_json": {
                    "delta": "安全回答",
                    "source": "worker_answer_delta_v1",
                    "visible_to_user": True,
                    "severity": "info",
                },
            },
            {
                **base,
                "id": "evt-tool",
                "sequence": 4,
                "event_type": "tool_call_started",
                "stage": "tool",
                "message": "Bash /var/lib/private",
                "payload_json": {
                    "tool_id": "Bash",
                    "args": {"command": "read /var/lib/private"},
                    "visible_to_user": True,
                },
            },
            {
                **base,
                "id": "evt-skill-delta",
                "sequence": 5,
                "event_type": "assistant_delta",
                "stage": "message",
                "message": "ignored fallback",
                "payload_json": {
                    "delta": "internal qa-file-reviewer detail",
                    "source": "untrusted_sdk_event",
                    "visible_to_user": True,
                    "severity": "info",
                },
            },
            {
                **base,
                "id": "evt-thinking",
                "sequence": 6,
                "event_type": "thinking",
                "stage": "sdk",
                "message": "private chain of thought",
                "payload_json": {"visible_to_user": True},
            },
            {
                **base,
                "id": "evt-content-fallback",
                "sequence": 7,
                "event_type": "assistant_delta",
                "stage": "answer",
                "message": "",
                "payload_json": {
                    "content": "content fallback must stay hidden",
                    "source": "worker_answer_delta_v1",
                    "visible_to_user": True,
                    "severity": "info",
                },
            },
            {
                **base,
                "id": "evt-message-fallback",
                "sequence": 8,
                "event_type": "assistant_delta",
                "stage": "answer",
                "message": "message fallback must stay hidden",
                "payload_json": {
                    "source": "worker_answer_delta_v1",
                    "visible_to_user": True,
                    "severity": "info",
                },
            },
            {
                **base,
                "id": "evt-extra-fields",
                "sequence": 9,
                "event_type": "assistant_delta",
                "stage": "answer",
                "message": "",
                "payload_json": {
                    "delta": "delta with untrusted extras",
                    "source": "worker_answer_delta_v1",
                    "visible_to_user": True,
                    "severity": "info",
                    "tool_args": {"path": "/var/lib/private"},
                    "raw_sdk_event": {"type": "content_block_delta"},
                },
            },
            {
                **base,
                "id": "evt-heartbeat",
                "sequence": 10,
                "event_type": "run_started",
                "stage": "status",
                "message": "raw worker heartbeat /var/lib/private",
                "payload_json": {
                    "heartbeat": True,
                    "progress_kind": "active",
                    "worker_id": "worker-private",
                    "visible_to_user": True,
                },
            },
            {
                **base,
                "id": "evt-tool-delta",
                "sequence": 11,
                "event_type": "tool_call_delta",
                "stage": "tool",
                "message": "raw command activity",
                "payload_json": {
                    "current_step": "read /var/lib/private",
                    "visible_to_user": True,
                },
            },
            {
                **base,
                "id": "evt-checkpoint",
                "sequence": 12,
                "event_type": "checkpoint_created",
                "stage": "checkpoint",
                "message": "stored tenants/default/private/checkpoint",
                "payload_json": {
                    "storage_key": "tenants/default/private/checkpoint",
                    "visible_to_user": True,
                },
            },
        ]

    async def empty_artifacts(conn, *, tenant_id, run_id):
        return []

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.lambchat_compat.transaction", fake_transaction)
    monkeypatch.setattr(
        "app.routes.lambchat_compat.get_settings",
        lambda: SimpleNamespace(run_event_stream_max_heartbeats=1),
    )
    monkeypatch.setattr("app.routes.lambchat_compat.asyncio.sleep", no_sleep)
    monkeypatch.setattr(
        "app.routes.lambchat_compat.repositories.get_authorized_run",
        fake_get_authorized_run,
    )
    monkeypatch.setattr(
        "app.routes.lambchat_compat.repositories.list_run_events",
        fake_list_run_events,
    )
    monkeypatch.setattr(
        "app.routes.lambchat_compat.repositories.list_run_artifacts",
        empty_artifacts,
    )
    client = TestClient(create_app())

    response = client.get(
        "/api/chat/sessions/ses_a/stream?run_id=run_a",
        headers=auth_headers(),
    )

    assert response.status_code == 200
    assert '"projection_version": "ai-platform.chat-public-projection.v1"' in response.text
    assert '"projection_kind": "assistant_delta"' in response.text
    assert '"event_id": "evt-delta"' in response.text
    assert '"sequence": 3' in response.text
    assert '"content": "安全回答"' in response.text
    assert "evt-queued" not in response.text
    assert '"queue_position": 3' not in response.text
    assert "internal queue source" not in response.text
    assert '"event_type": "run_started"' in response.text
    assert '"event_type": "agent_step_started"' in response.text
    assert "已完成请求准备，正在进入受控执行阶段" in response.text
    assert "正在处理数据并准备结果" in response.text
    assert "worker-alpha" not in response.text
    assert "private chain of thought" not in response.text
    assert "/var/lib/private" not in response.text
    assert '"tool_id": "Bash"' not in response.text
    assert '"args"' not in response.text
    assert "qa-file-reviewer" not in response.text
    assert "evt-skill-delta" not in response.text
    assert "evt-thinking" not in response.text
    assert "evt-content-fallback" not in response.text
    assert "content fallback must stay hidden" not in response.text
    assert "evt-message-fallback" not in response.text
    assert "message fallback must stay hidden" not in response.text
    assert "evt-extra-fields" not in response.text
    assert "delta with untrusted extras" not in response.text
    assert '"kind": "run"' in response.text
    assert '"category": "execution"' in response.text
    assert '"kind": "activity"' in response.text
    assert '"category": "processing"' in response.text
    assert '"status": "running"' in response.text
    assert "任务仍在处理中" in response.text
    assert "正在处理数据并准备结果" in response.text
    assert '"name": "Bash"' not in response.text
    assert "Bash" not in response.text
    assert "已保存阶段性进度" in response.text
    assert "worker-private" not in response.text
    assert "raw command activity" not in response.text
    assert "current_step" not in response.text
    assert "stored tenants/default" not in response.text


@pytest.mark.parametrize(
    ("user_id", "roles"),
    [("user-a", "user"), ("admin-a", "admin")],
)
def test_lambchat_sse_rebuilds_permission_cards_for_every_principal(
    monkeypatch,
    user_id,
    roles,
):
    async def fake_get_authorized_run(conn, *, tenant_id, user_id, run_id):
        assert tenant_id == "default"
        return {
            "id": run_id,
            "session_id": "ses_a",
            "agent_id": "general-agent",
            "skill_id": "general-chat",
            "status": "running",
            "result_json": {},
            "error_code": None,
            "error_message": None,
        }

    async def fake_list_run_events(conn, *, tenant_id, run_id):
        return [
            {
                "id": "evt-permission",
                "trace_id": "trace-run-a",
                "schema_version": "ai-platform.event-envelope.v1",
                "sequence": 1,
                "event_type": "tool_permission_requested",
                "stage": "policy",
                "message": "raw permission message /var/lib/private",
                "severity": "info",
                "visible_to_user": True,
                "error_code": None,
                "payload_json": {
                    "visible_to_user": True,
                    "tool_permission_card": {
                        "schema_version": "forged-schema",
                        "permission_request_id": "req-a",
                        "run_id": "run-foreign",
                        "tool_id": "file-write",
                        "tool_call_id": "call-a",
                        "action": "execute",
                        "risk_level": "high",
                        "write_capable": True,
                        "reason": "需要写入文件",
                        "status": "pending",
                        "decision": None,
                        "request_payload": {"path": "/var/lib/private"},
                        "args": {"path": "/var/lib/private"},
                        "raw_command": "write /var/lib/private",
                        "injected_extra": "must-not-pass",
                    },
                },
                "created_at": None,
            }
        ]

    async def empty_artifacts(conn, *, tenant_id, run_id):
        return []

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.lambchat_compat.transaction", fake_transaction)
    monkeypatch.setattr(
        "app.routes.lambchat_compat.get_settings",
        lambda: SimpleNamespace(run_event_stream_max_heartbeats=1),
    )
    monkeypatch.setattr("app.routes.lambchat_compat.asyncio.sleep", no_sleep)
    monkeypatch.setattr(
        "app.routes.lambchat_compat.repositories.get_authorized_run",
        fake_get_authorized_run,
    )
    monkeypatch.setattr(
        "app.routes.lambchat_compat.repositories.list_run_events",
        fake_list_run_events,
    )
    monkeypatch.setattr(
        "app.routes.lambchat_compat.repositories.list_run_artifacts",
        empty_artifacts,
    )
    client = TestClient(create_app())

    response = client.get(
        "/api/chat/sessions/ses_a/stream?run_id=run_a",
        headers=action_headers(user_id=user_id, roles=roles),
    )

    assert response.status_code == 200
    assert '"schema_version": "ai-platform.tool-permission-card.v1"' in response.text
    assert '"permission_request_id": "req-a"' in response.text
    assert '"run_id": "run_a"' in response.text
    assert '"tool_id": "file-write"' in response.text
    assert '"risk_level": "high"' in response.text
    assert '"write_capable": true' in response.text
    assert "forged-schema" not in response.text
    assert "run-foreign" not in response.text
    assert "request_payload" not in response.text
    assert '"args"' not in response.text
    assert "raw_command" not in response.text
    assert "injected_extra" not in response.text
    assert "must-not-pass" not in response.text
    assert "/var/lib/private" not in response.text


def test_lambchat_active_history_replays_versioned_delta_once_with_sequence(monkeypatch):
    async def fake_get_authorized_lambchat_session(conn, *, tenant_id, user_id, session_id):
        return {"id": session_id}

    async def fake_list_authorized_session_runs(conn, *, tenant_id, user_id, session_id, limit):
        return [
            {
                "id": "run_a",
                "trace_id": "trace_run_a",
                "status": "running",
                "result_json": {},
                "error_message": None,
            }
        ]

    async def fake_list_run_events(conn, *, tenant_id, run_id):
        return [
            {
                "id": "evt-delta",
                "trace_id": "trace_run_a",
                "schema_version": "ai-platform.event-envelope.v1",
                "sequence": 7,
                "event_type": "assistant_delta",
                "stage": "answer",
                "message": "",
                "severity": "info",
                "visible_to_user": True,
                "payload_json": {
                    "delta": "partial",
                    "source": "worker_answer_delta_v1",
                    "visible_to_user": True,
                    "severity": "info",
                },
                "created_at": None,
            }
        ]

    async def empty_artifacts(conn, *, tenant_id, run_id):
        return []

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.lambchat_compat.transaction", fake_transaction)
    monkeypatch.setattr(
        "app.routes.lambchat_compat.repositories.get_authorized_lambchat_session",
        fake_get_authorized_lambchat_session,
    )
    monkeypatch.setattr(
        "app.routes.lambchat_compat.repositories.list_authorized_session_runs",
        fake_list_authorized_session_runs,
    )
    monkeypatch.setattr(
        "app.routes.lambchat_compat.repositories.list_run_events",
        fake_list_run_events,
    )
    monkeypatch.setattr(
        "app.routes.lambchat_compat.repositories.list_run_artifacts",
        empty_artifacts,
    )
    client = TestClient(create_app())

    response = client.get("/api/sessions/ses_a/events", headers=auth_headers())

    assert response.status_code == 200
    events = response.json()["events"]
    assert len(events) == 1
    assert events[0]["event_type"] == "message:chunk"
    assert events[0]["sequence"] == 7
    assert events[0]["payload"] == {
        "projection_version": "ai-platform.chat-public-projection.v1",
        "projection_kind": "assistant_delta",
        "event_id": "evt-delta",
        "sequence": 7,
        "run_id": "run_a",
        "content": "partial",
    }


@pytest.mark.parametrize(
    ("status", "error_code", "detail_kind", "detail_code"),
    [
        ("failed", "claude_agent_sdk_runtime_error", "failed", "model_service_unavailable"),
        ("canceled", None, "cancelled", "run_cancelled"),
    ],
)
def test_lambchat_terminal_history_replays_safe_partial_activity_and_detail(
    status,
    error_code,
    detail_kind,
    detail_code,
):
    from app.auth import AuthPrincipal
    from app.routes.lambchat_compat import _compatibility_events_for_run

    principal = AuthPrincipal(
        user_id="user-a",
        display_name="User A",
        tenant_id="default",
        roles=["user"],
    )
    run = {
        "id": "run-terminal-partial",
        "trace_id": "trace-terminal-partial",
        "agent_id": "general-agent",
        "skill_id": "general-chat",
        "status": status,
        "result_json": {},
        "error_code": error_code,
        "error_message": "private token at /home/private/runtime.log",
        "finished_at": "2026-07-22T01:02:03Z",
    }
    base = {
        "trace_id": "trace-terminal-partial",
        "schema_version": "ai-platform.event-envelope.v1",
        "severity": "info",
        "visible_to_user": True,
        "error_code": None,
        "created_at": "2026-07-22T01:02:00Z",
    }
    run_events = [
        {
            **base,
            "id": "evt-started",
            "sequence": 1,
            "event_type": "worker_started",
            "stage": "worker",
            "message": "worker at /home/private/runtime",
            "payload_json": {"worker_id": "worker-private", "visible_to_user": True},
        },
        {
            **base,
            "id": "evt-tool-progress",
            "sequence": 2,
            "event_type": "tool_call_delta",
            "stage": "tool",
            "message": "raw command activity",
            "payload_json": {
                "current_step": "read /home/private/input.txt",
                "visible_to_user": True,
            },
        },
        {
            **base,
            "id": "evt-safe-delta",
            "sequence": 3,
            "event_type": "assistant_delta",
            "stage": "answer",
            "message": "",
            "payload_json": {
                "delta": "已完成公开部分；",
                "source": "worker_answer_delta_v1",
                "visible_to_user": True,
                "severity": "info",
            },
        },
        {
            **base,
            "id": "evt-private-delta",
            "sequence": 4,
            "event_type": "assistant_delta",
            "stage": "answer",
            "message": "",
            "payload_json": {
                "delta": "secret token at /home/private/result.txt",
                "source": "worker_answer_delta_v1",
                "visible_to_user": True,
                "severity": "info",
            },
        },
        {
            **base,
            "id": "evt-thinking",
            "sequence": 5,
            "event_type": "thinking",
            "stage": "sdk",
            "message": "private chain of thought",
            "payload_json": {"visible_to_user": True},
        },
    ]

    records = _compatibility_events_for_run(run, run_events, [], principal)
    history = [record.history_event for record in records]

    assert [event["event_type"] for event in history] == [
        "run_started",
        "agent_step_started",
        "message:chunk",
        "final_detail",
        "done",
    ]
    assert history[2]["data"]["content"] == "已完成公开部分；"
    assert history[3]["data"]["detail_kind"] == detail_kind
    assert history[3]["data"]["detail_code"] == detail_code
    assert history[-1]["data"]["status"] == (
        "cancelled" if status == "canceled" else status
    )
    serialized = str(history)
    assert "正在处理数据并准备结果" in serialized
    assert "private chain of thought" not in serialized
    assert "secret token" not in serialized
    assert "/home/private" not in serialized
    assert "worker-private" not in serialized
    assert "current_step" not in serialized


def test_lambchat_sse_stream_cannot_read_cross_tenant_run_events(monkeypatch):
    async def missing_authorized_run(conn, *, tenant_id, user_id, run_id):
        assert (tenant_id, user_id, run_id) == ("other", "user-a", "run_a")
        return None

    async def forbidden_event_read(*args, **kwargs):
        raise AssertionError("unauthorized run must not reach event storage")

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.lambchat_compat.transaction", fake_transaction)
    monkeypatch.setattr(
        "app.routes.lambchat_compat.repositories.get_authorized_run",
        missing_authorized_run,
    )
    monkeypatch.setattr(
        "app.routes.lambchat_compat.repositories.list_run_events",
        forbidden_event_read,
    )
    client = TestClient(create_app())

    response = client.get(
        "/api/chat/sessions/ses_a/stream?run_id=run_a",
        headers=action_headers(tenant_id="other"),
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "run_not_found"
    assert "event: metadata" not in response.text
    assert "event:" not in response.text


@pytest.mark.parametrize("case", ["active", "deleted", "foreign"])
def test_lambchat_sse_stream_authorizes_exact_run_before_response_or_metadata(monkeypatch, case):
    calls = 0
    child_reads = []

    async def fake_get_authorized_run(conn, *, tenant_id, user_id, run_id):
        nonlocal calls
        calls += 1
        assert (tenant_id, user_id, run_id) == ("default", "user-a", "run_a")
        if case == "deleted":
            return None
        return {
            "id": run_id,
            "session_id": "ses_a" if case == "active" else "ses_foreign",
            "status": "succeeded",
            "result_json": {"message": "authorized answer"},
            "error_code": None,
            "error_message": None,
        }

    async def record_event_read(conn, *, tenant_id, run_id):
        child_reads.append("events")
        return []

    async def record_artifact_read(conn, *, tenant_id, run_id):
        child_reads.append("artifacts")
        return []

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.lambchat_compat.transaction", fake_transaction)
    monkeypatch.setattr(
        "app.routes.lambchat_compat.repositories.get_authorized_run",
        fake_get_authorized_run,
    )
    monkeypatch.setattr(
        "app.routes.lambchat_compat.repositories.list_run_events",
        record_event_read,
    )
    monkeypatch.setattr(
        "app.routes.lambchat_compat.repositories.list_run_artifacts",
        record_artifact_read,
    )
    client = TestClient(create_app())

    response = client.get(
        "/api/chat/sessions/ses_a/stream?run_id=run_a",
        headers=auth_headers(),
    )

    if case == "active":
        assert response.status_code == 200
        assert response.text.index("event: metadata") < response.text.index("event: message:chunk")
        assert calls == 2
        assert child_reads == ["events", "artifacts"]
    else:
        assert response.status_code == 404
        assert response.json()["detail"] == "run_not_found"
        assert "event: metadata" not in response.text
        assert "event:" not in response.text
        assert calls == 1
        assert child_reads == []


def test_lambchat_sse_stream_rechecks_authorization_before_later_child_reads(monkeypatch):
    calls = 0
    child_reads = []

    async def fake_get_authorized_run(conn, *, tenant_id, user_id, run_id):
        nonlocal calls
        calls += 1
        if calls == 3:
            return None
        return {
            "id": run_id,
            "session_id": "ses_a",
            "status": "running",
            "result_json": {},
            "error_code": None,
            "error_message": None,
        }

    async def record_event_read(conn, *, tenant_id, run_id):
        child_reads.append("events")
        return []

    async def record_artifact_read(conn, *, tenant_id, run_id):
        child_reads.append("artifacts")
        return []

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.lambchat_compat.transaction", fake_transaction)
    monkeypatch.setattr(
        "app.routes.lambchat_compat.get_settings",
        lambda: SimpleNamespace(run_event_stream_max_heartbeats=2),
    )
    monkeypatch.setattr("app.routes.lambchat_compat.asyncio.sleep", no_sleep)
    monkeypatch.setattr(
        "app.routes.lambchat_compat.repositories.get_authorized_run",
        fake_get_authorized_run,
    )
    monkeypatch.setattr(
        "app.routes.lambchat_compat.repositories.list_run_events",
        record_event_read,
    )
    monkeypatch.setattr(
        "app.routes.lambchat_compat.repositories.list_run_artifacts",
        record_artifact_read,
    )
    client = TestClient(create_app())

    response = client.get(
        "/api/chat/sessions/ses_a/stream?run_id=run_a",
        headers=auth_headers(),
    )

    assert response.status_code == 200
    assert calls == 3
    assert child_reads == ["events", "artifacts"]
    assert response.text.count("event: metadata") == 1
    assert '"error": "run_not_found"' in response.text
    assert "event: done" in response.text


def test_lambchat_sse_stream_redacts_runtime_private_answer(monkeypatch):
    async def fake_get_authorized_run(conn, *, tenant_id, user_id, run_id):
        assert user_id == "user-a"
        return {
            "id": run_id,
            "session_id": "ses_a",
            "status": "succeeded",
            "result_json": {"message": "written to /home/xinlin.jiang/qa-review-queue-runtime/out.docx"},
            "error_code": None,
            "error_message": None,
        }

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.lambchat_compat.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.lambchat_compat.repositories.get_authorized_run", fake_get_authorized_run)
    client = TestClient(create_app())

    response = client.get("/api/chat/sessions/ses_a/stream?run_id=run_a", headers=auth_headers())

    assert response.status_code == 200
    assert "/home/xinlin.jiang/qa-review-queue-runtime" not in response.text
    assert "written to" not in response.text
    assert "event: message:chunk" in response.text
    assert "任务完成" in response.text

    admin_response = client.get(
        "/api/chat/sessions/ses_a/stream?run_id=run_a",
        headers=action_headers(roles="admin"),
    )
    assert admin_response.status_code == 200
    assert "/home/" not in admin_response.text
    assert "written to" not in admin_response.text
    assert "任务完成" in admin_response.text


def test_lambchat_sse_stream_redacts_runtime_private_error(monkeypatch):
    async def fake_get_authorized_run(conn, *, tenant_id, user_id, run_id):
        assert user_id == "user-a"
        return {
            "id": run_id,
            "session_id": "ses_a",
            "status": "failed",
            "result_json": {},
            "error_code": "runtime211_stream_error",
            "error_message": "failed in /var/lib/ai-platform/private.log",
        }

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.lambchat_compat.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.lambchat_compat.repositories.get_authorized_run", fake_get_authorized_run)
    client = TestClient(create_app())

    response = client.get("/api/chat/sessions/ses_a/stream?run_id=run_a", headers=auth_headers())

    assert response.status_code == 200
    assert "runtime211" not in response.text
    assert "/var/lib/ai-platform" not in response.text
    assert "event: final_detail" in response.text
    assert '"detail_kind": "failed"' in response.text
    assert '"detail_code": "run_failed"' in response.text
    assert "任务未能完成。请稍后重试；如问题持续，请联系管理员。" in response.text
    assert "event: error" not in response.text


def test_lambchat_sse_stream_terminates_cancelled_run(monkeypatch):
    async def fake_get_authorized_run(conn, *, tenant_id, user_id, run_id):
        assert user_id == "user-a"
        return {
            "id": run_id,
            "session_id": "ses_a",
            "status": "canceled",
            "result_json": {},
            "error_code": None,
            "error_message": None,
        }

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.lambchat_compat.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.lambchat_compat.repositories.get_authorized_run", fake_get_authorized_run)
    client = TestClient(create_app())

    response = client.get("/api/chat/sessions/ses_a/stream?run_id=run_a", headers=auth_headers())

    assert response.status_code == 200
    assert 'event: done' in response.text
    assert 'event: final_detail' in response.text
    assert '"detail_kind": "cancelled"' in response.text
    assert '"detail_code": "run_cancelled"' in response.text
    assert "取消前已产生的公开内容仍会保留" in response.text
    assert '"status": "cancelled"' in response.text
    assert '"status": "canceled"' not in response.text


def test_lambchat_sse_stream_uses_configured_long_task_heartbeat_window(monkeypatch):
    calls = {"run": 0}

    def stream_settings():
        return type("S", (), {"run_event_stream_max_heartbeats": 2})()

    async def fake_get_authorized_run(conn, *, tenant_id, user_id, run_id):
        calls["run"] += 1
        assert user_id == "user-a"
        return {
            "id": run_id,
            "session_id": "ses_a",
            "status": "running",
            "result_json": {},
            "error_code": None,
            "error_message": None,
        }

    async def no_sleep(seconds):
        return None

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.lambchat_compat.get_settings", stream_settings)
    monkeypatch.setattr("app.routes.lambchat_compat.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.lambchat_compat.repositories.get_authorized_run", fake_get_authorized_run)
    monkeypatch.setattr("app.routes.lambchat_compat.asyncio.sleep", no_sleep)
    client = TestClient(create_app())

    response = client.get("/api/chat/sessions/ses_a/stream?run_id=run_a", headers=auth_headers())

    assert response.status_code == 200
    assert calls["run"] == 3
    assert '"error": "stream_timeout"' in response.text
    assert '"status": "timeout"' in response.text


def test_lambchat_status_normalizes_platform_terminal_statuses(monkeypatch):
    async def fake_get_authorized_lambchat_session(conn, *, tenant_id, user_id, session_id):
        assert user_id == "user-a"
        return {"id": session_id}

    statuses = {
        "run_succeeded": "succeeded",
        "run_failed": "failed",
        "run_cancelled": "canceled",
    }

    async def fake_get_authorized_run(conn, *, tenant_id, user_id, run_id):
        assert (tenant_id, user_id) == ("default", "user-a")
        return {"id": run_id, "session_id": "ses_a", "status": statuses[run_id]}

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.lambchat_compat.transaction", fake_transaction)
    monkeypatch.setattr(
        "app.routes.lambchat_compat.repositories.get_authorized_lambchat_session",
        fake_get_authorized_lambchat_session,
    )
    monkeypatch.setattr(
        "app.routes.lambchat_compat.repositories.get_authorized_run",
        fake_get_authorized_run,
    )
    client = TestClient(create_app())

    succeeded = client.get("/api/chat/sessions/ses_a/status?run_id=run_succeeded", headers=auth_headers())
    failed = client.get("/api/chat/sessions/ses_a/status?run_id=run_failed", headers=auth_headers())
    cancelled = client.get("/api/chat/sessions/ses_a/status?run_id=run_cancelled", headers=auth_headers())

    assert succeeded.status_code == 200
    assert succeeded.json()["status"] == "completed"
    assert succeeded.json()["raw_status"] == "succeeded"
    assert failed.status_code == 200
    assert failed.json()["status"] == "error"
    assert failed.json()["raw_status"] == "failed"
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    assert cancelled.json()["raw_status"] == "cancelled"


def test_lambchat_status_rejects_an_absent_explicit_run_without_falling_back(monkeypatch):
    async def fake_get_authorized_lambchat_session(conn, *, tenant_id, user_id, session_id):
        assert (tenant_id, user_id, session_id) == ("default", "user-a", "ses_a")
        return {"id": session_id}

    async def fake_get_authorized_run(conn, *, tenant_id, user_id, run_id):
        assert (tenant_id, user_id, run_id) == ("default", "user-a", "run-requested")
        return None

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.lambchat_compat.transaction", fake_transaction)
    monkeypatch.setattr(
        "app.routes.lambchat_compat.repositories.get_authorized_lambchat_session",
        fake_get_authorized_lambchat_session,
    )
    monkeypatch.setattr(
        "app.routes.lambchat_compat.repositories.get_authorized_run",
        fake_get_authorized_run,
    )
    client = TestClient(create_app())

    response = client.get(
        "/api/chat/sessions/ses_a/status?run_id=run-requested",
        headers=auth_headers(),
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "run_not_found"


def test_lambchat_status_uses_exact_authorized_run_beyond_latest_list_and_rejects_scope_mismatch(monkeypatch):
    calls = []

    async def fake_get_authorized_lambchat_session(conn, *, tenant_id, user_id, session_id):
        calls.append(("session", tenant_id, user_id, session_id))
        return {"id": session_id} if (tenant_id, user_id) == ("default", "user-a") else None

    async def fake_get_authorized_run(conn, *, tenant_id, user_id, run_id):
        calls.append(("run", tenant_id, user_id, run_id))
        if run_id == "run-old":
            return {"id": run_id, "session_id": "ses_a", "status": "succeeded"}
        if run_id == "run-other-session":
            return {"id": run_id, "session_id": "ses_other", "status": "running"}
        return None

    async def unexpected_recent_list(*args, **kwargs):
        raise AssertionError("explicit run lookup must not use the latest-ten list")

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.lambchat_compat.transaction", fake_transaction)
    monkeypatch.setattr(
        "app.routes.lambchat_compat.repositories.get_authorized_lambchat_session",
        fake_get_authorized_lambchat_session,
    )
    monkeypatch.setattr(
        "app.routes.lambchat_compat.repositories.get_authorized_run",
        fake_get_authorized_run,
    )
    monkeypatch.setattr(
        "app.routes.lambchat_compat.repositories.list_authorized_session_runs",
        unexpected_recent_list,
    )
    client = TestClient(create_app())

    old = client.get("/api/chat/sessions/ses_a/status?run_id=run-old", headers=auth_headers())
    wrong_session = client.get(
        "/api/chat/sessions/ses_a/status?run_id=run-other-session",
        headers=auth_headers(),
    )
    wrong_user = client.get(
        "/api/chat/sessions/ses_a/status?run_id=run-old",
        headers=action_headers(user_id="user-b"),
    )
    wrong_tenant = client.get(
        "/api/chat/sessions/ses_a/status?run_id=run-old",
        headers=action_headers(tenant_id="other-tenant"),
    )

    assert old.status_code == 200
    assert old.json() == {
        "session_id": "ses_a",
        "run_id": "run-old",
        "status": "completed",
        "raw_status": "succeeded",
    }
    assert wrong_session.status_code == 404
    assert wrong_user.status_code == 404
    assert wrong_tenant.status_code == 404
    assert calls == [
        ("session", "default", "user-a", "ses_a"),
        ("run", "default", "user-a", "run-old"),
        ("session", "default", "user-a", "ses_a"),
        ("run", "default", "user-a", "run-other-session"),
        ("session", "default", "user-b", "ses_a"),
        ("session", "other-tenant", "user-a", "ses_a"),
    ]


def test_lambchat_status_keeps_latest_selection_scoped_to_tenant_and_user(monkeypatch):
    calls = []

    async def fake_get_authorized_lambchat_session(conn, *, tenant_id, user_id, session_id):
        calls.append(("session", tenant_id, user_id, session_id))
        return {"id": session_id} if (tenant_id, user_id) == ("default", "user-a") else None

    async def fake_list_authorized_session_runs(conn, *, tenant_id, user_id, session_id, limit):
        calls.append(("runs", tenant_id, user_id, session_id, limit))
        return [{"id": "run-latest", "status": "running", "session_generation": 1}]

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.lambchat_compat.transaction", fake_transaction)
    monkeypatch.setattr(
        "app.routes.lambchat_compat.repositories.get_authorized_lambchat_session",
        fake_get_authorized_lambchat_session,
    )
    monkeypatch.setattr(
        "app.routes.lambchat_compat.repositories.list_authorized_session_runs",
        fake_list_authorized_session_runs,
    )
    client = TestClient(create_app())

    owner = client.get("/api/chat/sessions/ses_a/status", headers=auth_headers())
    other_user = client.get(
        "/api/chat/sessions/ses_a/status",
        headers=action_headers(user_id="user-b"),
    )
    other_tenant = client.get(
        "/api/chat/sessions/ses_a/status",
        headers=action_headers(tenant_id="other-tenant"),
    )

    assert owner.status_code == 200
    assert owner.json()["status"] == "running"
    assert owner.json()["run_id"] is None
    assert other_user.status_code == 404
    assert other_tenant.status_code == 404
    assert calls == [
        ("session", "default", "user-a", "ses_a"),
        ("runs", "default", "user-a", "ses_a", 10),
        ("session", "default", "user-b", "ses_a"),
        ("session", "other-tenant", "user-a", "ses_a"),
    ]


def test_lambchat_session_runs_normalizes_legacy_canceled_status(monkeypatch):
    async def fake_get_authorized_lambchat_session(conn, *, tenant_id, user_id, session_id):
        assert user_id == "user-a"
        return {"id": session_id}

    async def fake_list_authorized_session_runs(conn, *, tenant_id, user_id, session_id, limit):
        return [
            {
                "id": "run_cancelled",
                "agent_id": "general-agent",
                "skill_id": "general-chat",
                "status": "canceled",
                "error_message": None,
                "created_at": None,
                "finished_at": None,
            }
        ]

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.lambchat_compat.transaction", fake_transaction)
    monkeypatch.setattr(
        "app.routes.lambchat_compat.repositories.get_authorized_lambchat_session",
        fake_get_authorized_lambchat_session,
    )
    monkeypatch.setattr(
        "app.routes.lambchat_compat.repositories.list_authorized_session_runs",
        fake_list_authorized_session_runs,
    )
    client = TestClient(create_app())

    response = client.get("/api/sessions/ses_a/runs", headers=auth_headers())

    assert response.status_code == 200
    assert response.json()["runs"][0]["status"] == "cancelled"
    assert response.json()["runs"][0]["capability_id"] == "general_chat"
    assert "skill_id" not in response.json()["runs"][0]


def test_lambchat_session_runs_redacts_raw_skill_agent_id_for_ordinary_user(monkeypatch):
    async def fake_get_authorized_lambchat_session(conn, *, tenant_id, user_id, session_id):
        assert user_id == "user-a"
        return {"id": session_id}

    async def fake_list_authorized_session_runs(conn, *, tenant_id, user_id, session_id, limit):
        return [
            {
                "id": "run_translate",
                "agent_id": "baoyu-translate",
                "skill_id": "baoyu-translate",
                "status": "running",
                "session_generation": 1,
                "error_message": None,
                "created_at": None,
                "finished_at": None,
            }
        ]

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.lambchat_compat.transaction", fake_transaction)
    monkeypatch.setattr(
        "app.routes.lambchat_compat.repositories.get_authorized_lambchat_session",
        fake_get_authorized_lambchat_session,
    )
    monkeypatch.setattr(
        "app.routes.lambchat_compat.repositories.list_authorized_session_runs",
        fake_list_authorized_session_runs,
    )
    client = TestClient(create_app())

    response = client.get("/api/sessions/ses_a/runs", headers=auth_headers())

    assert response.status_code == 200
    run = response.json()["runs"][0]
    assert run["capability_id"] == "document_translation"
    assert "skill_id" not in run
    assert "baoyu-translate" not in str(run)


def test_lambchat_session_runs_include_latest_frontend_run_aliases(monkeypatch):
    async def fake_get_authorized_lambchat_session(conn, *, tenant_id, user_id, session_id):
        assert user_id == "user-a"
        return {"id": session_id}

    async def fake_list_authorized_session_runs(conn, *, tenant_id, user_id, session_id, limit):
        return [
            {
                "id": "run_a",
                "trace_id": "trace_run_a",
                "agent_id": "general-agent",
                "skill_id": "general-chat",
                "status": "succeeded",
                "error_message": None,
                "created_at": "2026-06-01T10:00:00Z",
                "started_at": "2026-06-01T10:00:05Z",
                "finished_at": "2026-06-01T10:00:20Z",
            }
        ]

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.lambchat_compat.transaction", fake_transaction)
    monkeypatch.setattr(
        "app.routes.lambchat_compat.repositories.get_authorized_lambchat_session",
        fake_get_authorized_lambchat_session,
    )
    monkeypatch.setattr(
        "app.routes.lambchat_compat.repositories.list_authorized_session_runs",
        fake_list_authorized_session_runs,
    )
    client = TestClient(create_app())

    response = client.get("/api/sessions/ses_a/runs?trace_id=trace_run_a", headers=auth_headers())

    assert response.status_code == 200
    payload = response.json()["runs"][0]
    assert payload["trace_id"] == "trace_run_a"
    assert payload["started_at"] == "2026-06-01T10:00:05Z"
    assert payload["completed_at"] == "2026-06-01T10:00:20Z"


def test_lambchat_session_runs_redacts_runtime_private_error(monkeypatch):
    async def fake_get_authorized_lambchat_session(conn, *, tenant_id, user_id, session_id):
        assert user_id == "user-a"
        return {"id": session_id}

    async def fake_list_authorized_session_runs(conn, *, tenant_id, user_id, session_id, limit):
        return [
            {
                "id": "run_failed",
                "agent_id": "general-agent",
                "skill_id": "general-chat",
                "status": "failed",
                "error_code": "runtime211_stream_error",
                "error_message": "failed in /var/lib/ai-platform/private.log",
                "created_at": None,
                "finished_at": None,
            }
        ]

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.lambchat_compat.transaction", fake_transaction)
    monkeypatch.setattr(
        "app.routes.lambchat_compat.repositories.get_authorized_lambchat_session",
        fake_get_authorized_lambchat_session,
    )
    monkeypatch.setattr(
        "app.routes.lambchat_compat.repositories.list_authorized_session_runs",
        fake_list_authorized_session_runs,
    )
    client = TestClient(create_app())

    response = client.get("/api/sessions/ses_a/runs", headers=auth_headers())

    assert response.status_code == 200
    payload = response.json()["runs"][0]
    assert payload["error"] == "任务未能完成。请稍后重试；如问题持续，请联系管理员。"
    assert payload["error_code"] == "run_failed"
    assert "runtime211" not in str(payload)
    assert "/var/lib/ai-platform" not in str(payload)


def test_lambchat_session_events_project_g2_envelope_and_redact_skills(monkeypatch):
    async def fake_get_authorized_lambchat_session(conn, *, tenant_id, user_id, session_id):
        return {"id": session_id}

    async def fake_list_authorized_session_runs(conn, *, tenant_id, user_id, session_id, limit):
        return [
            {
                "id": "run_a",
                "agent_id": "qa-word-review",
                "skill_id": "qa-file-reviewer",
                "status": "running",
                "session_generation": 1,
                "error_message": None,
                "created_at": None,
                "finished_at": None,
            }
        ]

    async def fake_list_run_events(conn, *, tenant_id, run_id):
        return [
            {
                "id": "evt_a",
                "sequence": 37,
                "trace_id": "trace_run_a",
                "schema_version": "ai-platform.event-envelope.v1",
                "event_type": "skill_selected",
                "stage": "planning",
                "message": "已选择后台能力",
                "severity": "info",
                "visible_to_user": True,
                "error_code": None,
                "latency_ms": None,
                "input_token_count": 0,
                "output_token_count": 0,
                "total_token_count": 0,
                "estimated_cost_minor": 0,
                "payload_json": {
                    "agent_id": "qa-word-review",
                    "skill_id": "qa-file-reviewer",
                    "skill_ids": ["qa-file-reviewer"],
                    "storage_key": "tenants/default/private.docx",
                    "local_path": "/tmp/private.docx",
                    "visible_to_user": True,
                },
                "created_at": None,
            },
            {
                "id": "evt_hidden",
                "trace_id": "trace_run_a",
                "schema_version": "ai-platform.event-envelope.v1",
                "event_type": "worker_started",
                "stage": "worker",
                "message": "internal runtime evidence",
                "severity": "info",
                "visible_to_user": False,
                "error_code": None,
                "latency_ms": None,
                "input_token_count": 0,
                "output_token_count": 0,
                "total_token_count": 0,
                "estimated_cost_minor": 0,
                "payload_json": {
                    "visible_to_user": False,
                    "storage_key": "tenants/default/hidden.docx",
                },
                "created_at": None,
            }
        ]

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.lambchat_compat.transaction", fake_transaction)
    monkeypatch.setattr(
        "app.routes.lambchat_compat.repositories.get_authorized_lambchat_session",
        fake_get_authorized_lambchat_session,
    )
    monkeypatch.setattr(
        "app.routes.lambchat_compat.repositories.list_authorized_session_runs",
        fake_list_authorized_session_runs,
    )
    monkeypatch.setattr(
        "app.routes.lambchat_compat.repositories.list_run_events",
        fake_list_run_events,
    )
    client = TestClient(create_app())

    response = client.get("/api/sessions/ses_a/events", headers=auth_headers())

    assert response.status_code == 200
    assert response.json()["current_run_id"] == "run_a"
    events = response.json()["events"]
    assert len(events) == 1
    event = events[0]
    assert event["schema_version"] == "ai-platform.event-envelope.v1"
    assert event["trace_id"] == "trace_run_a"
    assert event["type"] == "capability_selected"
    assert event["event_type"] == "capability_selected"
    assert event["sequence"] == 37
    assert "sequence" not in event["data"]
    assert event["payload"]["capability_id"] == "document_review"
    assert event["data"] == {
        "projection_version": "ai-platform.chat-public-projection.v1",
        "event_id": "evt_a",
        "run_id": "run_a",
        "event_type": "capability_selected",
        "stage": "planning",
        "message": "已加载授权处理能力，下一步将按所选流程分析请求",
        "severity": "info",
        "progress_kind": "completed",
        "wait_reason": None,
        "payload": {"capability_id": "document_review"},
        "created_at": None,
        "content": "已加载授权处理能力，下一步将按所选流程分析请求",
        "status": "planning",
    }
    assert "skill_id" not in str(event)
    assert "skill_ids" not in str(event)
    assert "storage_key" not in str(event)
    assert "/tmp/" not in str(event)


def test_lambchat_session_events_restore_two_real_user_turns_before_each_run(monkeypatch):
    message_calls = []

    async def fake_get_authorized_lambchat_session(conn, *, tenant_id, user_id, session_id):
        return {"id": session_id}

    async def fake_list_authorized_session_runs(conn, *, tenant_id, user_id, session_id, limit):
        return [
            {
                "id": "run-new",
                "trace_id": "trace-new",
                "agent_id": "general-agent",
                "skill_id": "general-chat",
                "status": "succeeded",
                "result_json": {"message": "第二轮回答"},
                "created_at": "2026-07-15T02:00:00Z",
                "finished_at": "2026-07-15T02:01:00Z",
            },
            {
                "id": "run-old",
                "trace_id": "trace-old",
                "agent_id": "general-agent",
                "skill_id": "general-chat",
                "status": "succeeded",
                "result_json": {"message": "第一轮回答"},
                "created_at": "2026-07-15T01:00:00Z",
                "finished_at": "2026-07-15T01:01:00Z",
            },
        ]

    async def fake_list_authorized_user_messages_for_runs(
        conn, *, tenant_id, user_id, session_id, run_ids
    ):
        message_calls.append((tenant_id, user_id, session_id, run_ids))
        return [
            {
                "id": "msg-old-user",
                "run_id": "run-old",
                "content": "第一轮问题",
                "metadata_json": {
                    "locked_skill": {"label": "internal-comms"},
                    "skill_id": "internal-comms",
                    "expected_version": "a" * 64,
                    "storage_key": "tenants/default/private/skill.zip",
                },
                "created_at": "2026-07-15T01:00:00Z",
            },
            {
                "id": "msg-new-user",
                "run_id": "run-new",
                "content": "第二轮问题",
                "created_at": "2026-07-15T02:00:00Z",
            },
        ]

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.lambchat_compat.transaction", fake_transaction)
    monkeypatch.setattr(
        "app.routes.lambchat_compat.repositories.get_authorized_lambchat_session",
        fake_get_authorized_lambchat_session,
    )
    monkeypatch.setattr(
        "app.routes.lambchat_compat.repositories.list_authorized_session_runs",
        fake_list_authorized_session_runs,
    )
    monkeypatch.setattr(
        "app.routes.lambchat_compat.repositories.list_authorized_user_messages_for_runs",
        fake_list_authorized_user_messages_for_runs,
    )
    client = TestClient(create_app())

    response = client.get("/api/sessions/ses_a/events", headers=auth_headers())

    assert response.status_code == 200
    assert message_calls == [
        ("default", "user-a", "ses_a", ["run-new", "run-old"])
    ]
    events = response.json()["events"]
    assert [event["event_type"] for event in events] == [
        "user:message",
        "message:chunk",
        "done",
        "user:message",
        "message:chunk",
        "done",
    ]
    user_events = [event for event in events if event["event_type"] == "user:message"]
    assert [event["data"]["content"] for event in user_events] == ["第一轮问题", "第二轮问题"]
    assert [event["data"]["message_id"] for event in user_events] == [
        "msg-old-user",
        "msg-new-user",
    ]
    assert set(user_events[0]) == {"id", "type", "event_type", "timestamp", "run_id", "data"}
    assert set(user_events[0]["data"]) == {
        "message_id",
        "run_id",
        "content",
        "locked_skill_label",
    }
    assert user_events[0]["data"]["locked_skill_label"] == "internal-comms"
    assert "locked_skill_label" not in user_events[1]["data"]
    serialized = str(events)
    assert "metadata_json" not in serialized
    assert "tenants/default" not in serialized
    assert "a" * 64 not in serialized


def test_lambchat_failed_run_projects_only_safe_native_skill_sandbox_stage(monkeypatch):
    async def fake_get_authorized_lambchat_session(conn, *, tenant_id, user_id, session_id):
        return {"id": session_id}

    async def fake_list_authorized_session_runs(conn, *, tenant_id, user_id, session_id, limit):
        return [
            {
                "id": "run-native-failed",
                "trace_id": "trace-native-failed",
                "agent_id": "general-agent",
                "skill_id": "internal-comms",
                "status": "failed",
                "result_json": {},
                "error_code": "native_tool_admission_failed",
                "error_message": "private token at /home/private/workspace",
                "created_at": None,
                "finished_at": None,
            }
        ]

    async def fake_list_run_events(conn, *, tenant_id, run_id):
        return []

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.lambchat_compat.transaction", fake_transaction)
    monkeypatch.setattr(
        "app.routes.lambchat_compat.repositories.get_authorized_lambchat_session",
        fake_get_authorized_lambchat_session,
    )
    monkeypatch.setattr(
        "app.routes.lambchat_compat.repositories.list_authorized_session_runs",
        fake_list_authorized_session_runs,
    )
    monkeypatch.setattr(
        "app.routes.lambchat_compat.repositories.list_run_events",
        fake_list_run_events,
    )

    response = TestClient(create_app()).get(
        "/api/sessions/ses_a/events",
        headers=auth_headers(),
    )

    assert response.status_code == 200
    final_detail = next(
        event for event in response.json()["events"] if event["event_type"] == "final_detail"
    )
    assert final_detail["data"] == {
        "run_id": "run-native-failed",
        "projection_version": "ai-platform.chat-public-projection.v1",
        "detail_kind": "failed",
        "detail_code": "skill_sandbox_admission_failed",
        "message": "所选 Skill 未能通过隔离沙箱准入。请调整 Skill 或联系管理员。",
    }
    assert "native_tool_admission_failed" not in response.text
    assert "/home/private/workspace" not in response.text
    assert "private token" not in response.text


def test_lambchat_default_history_queries_user_messages_for_only_latest_fifty_runs(monkeypatch):
    target_runs = [
        {
            "id": f"run-{index:02d}",
            "trace_id": f"trace-{index:02d}",
            "status": "succeeded",
            "result_json": {"message": f"answer-{index:02d}"},
            "created_at": f"2026-07-15T{index % 24:02d}:00:00Z",
        }
        for index in range(50)
    ]
    message_queries = []

    async def fake_get_authorized_lambchat_session(conn, *, tenant_id, user_id, session_id):
        return {"id": session_id}

    async def fake_list_authorized_session_runs(conn, *, tenant_id, user_id, session_id, limit):
        assert limit == 50
        return target_runs

    async def fake_list_authorized_user_messages_for_runs(
        conn, *, tenant_id, user_id, session_id, run_ids
    ):
        message_queries.append(run_ids)
        return []

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.lambchat_compat.transaction", fake_transaction)
    monkeypatch.setattr(
        "app.routes.lambchat_compat.repositories.get_authorized_lambchat_session",
        fake_get_authorized_lambchat_session,
    )
    monkeypatch.setattr(
        "app.routes.lambchat_compat.repositories.list_authorized_session_runs",
        fake_list_authorized_session_runs,
    )
    monkeypatch.setattr(
        "app.routes.lambchat_compat.repositories.list_authorized_user_messages_for_runs",
        fake_list_authorized_user_messages_for_runs,
    )

    response = TestClient(create_app()).get(
        "/api/sessions/ses_a/events",
        headers=auth_headers(),
    )

    assert response.status_code == 200
    assert message_queries == [[f"run-{index:02d}" for index in range(50)]]
    assert "run-50" not in str(message_queries)


@pytest.mark.asyncio
async def test_lambchat_session_events_use_persisted_message_repository_contract(monkeypatch):
    class MessageCursor:
        def __init__(self, rows=None):
            self.rows = rows or []

        async def fetchall(self):
            return self.rows

    class MessageConnection:
        def __init__(self):
            self.messages = []

        async def execute(self, sql, params):
            normalized = " ".join(sql.split())
            if normalized.startswith("insert into messages"):
                message_id, tenant_id, session_id, run_id, role, content, metadata_json = params
                self.messages.append(
                    {
                        "id": message_id,
                        "tenant_id": tenant_id,
                        "session_id": session_id,
                        "run_id": run_id,
                        "role": role,
                        "content": content,
                        "metadata_json": metadata_json,
                        "created_at": f"2026-07-15T0{len(self.messages) + 1}:00:00Z",
                    }
                )
                return MessageCursor()
            if normalized.startswith("update sessions set updated_at"):
                return MessageCursor()
            if normalized.startswith("select messages.id"):
                tenant_id, session_id, user_id, run_ids = params
                assert user_id == "user-a"
                rows = [
                    {
                        key: row[key]
                        for key in (
                            "id",
                            "run_id",
                            "content",
                            "created_at",
                        )
                    }
                    for row in self.messages
                    if row["tenant_id"] == tenant_id
                    and row["session_id"] == session_id
                    and row["role"] == "user"
                    and row["run_id"] in run_ids
                ]
                rows.sort(key=lambda row: (row["created_at"], row["id"]))
                return MessageCursor(rows)
            raise AssertionError(f"unexpected message repository SQL: {normalized}")

    conn = MessageConnection()
    old_message_id = await real_append_message(
        conn,
        tenant_id="default",
        session_id="ses_a",
        run_id="run-old",
        role="user",
        content="持久化第一轮问题",
        metadata_json={"file_ids": ["private-file-id"]},
    )
    await real_append_message(
        conn,
        tenant_id="default",
        session_id="ses_a",
        run_id="run-old",
        role="assistant",
        content="不得从持久化 assistant message 重建",
    )
    new_message_id = await real_append_message(
        conn,
        tenant_id="default",
        session_id="ses_a",
        run_id="run-new",
        role="user",
        content="持久化第二轮问题",
        metadata_json={"skill_id": "private-skill"},
    )
    await real_append_message(
        conn,
        tenant_id="tenant-b",
        session_id="ses_a",
        run_id="run-new",
        role="user",
        content="不得跨 tenant 投影",
    )

    @asynccontextmanager
    async def message_transaction():
        yield conn

    async def fake_get_authorized_lambchat_session(conn, *, tenant_id, user_id, session_id):
        return {"id": session_id}

    async def fake_list_authorized_session_runs(conn, *, tenant_id, user_id, session_id, limit):
        return [
            {
                "id": "run-new",
                "trace_id": "trace-new",
                "status": "succeeded",
                "result_json": {"message": "第二轮回答"},
                "created_at": "2026-07-15T04:00:00Z",
            },
            {
                "id": "run-old",
                "trace_id": "trace-old",
                "status": "succeeded",
                "result_json": {"message": "第一轮回答"},
                "created_at": "2026-07-15T01:00:00Z",
            },
        ]

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.lambchat_compat.transaction", message_transaction)
    monkeypatch.setattr(
        "app.routes.lambchat_compat.repositories.get_authorized_lambchat_session",
        fake_get_authorized_lambchat_session,
    )
    monkeypatch.setattr(
        "app.routes.lambchat_compat.repositories.list_authorized_session_runs",
        fake_list_authorized_session_runs,
    )
    monkeypatch.setattr(
        "app.routes.lambchat_compat.repositories.list_authorized_user_messages_for_runs",
        real_list_authorized_user_messages_for_runs,
    )

    response = TestClient(create_app()).get(
        "/api/sessions/ses_a/events",
        headers=auth_headers(),
    )

    assert response.status_code == 200
    user_events = [
        event for event in response.json()["events"] if event["event_type"] == "user:message"
    ]
    assert [event["id"] for event in user_events] == [old_message_id, new_message_id]
    assert [event["data"]["content"] for event in user_events] == [
        "持久化第一轮问题",
        "持久化第二轮问题",
    ]
    assert "private-file-id" not in response.text
    assert "private-skill" not in response.text
    assert "不得从持久化 assistant message 重建" not in response.text
    assert "不得跨 tenant 投影" not in response.text


def test_lambchat_routes_keep_running_latest_run_stable_with_legacy_queued_at_ties(monkeypatch):
    async def fake_get_authorized_lambchat_session(conn, *, tenant_id, user_id, session_id):
        return {"id": session_id}

    async def fake_list_authorized_session_runs(conn, *, tenant_id, user_id, session_id, limit):
        assert limit in (10, 50)
        return [
            {
                "id": "run-created-newer",
                "trace_id": "trace-newer",
                "agent_id": "general-agent",
                "skill_id": "general-chat",
                "status": "running",
                "result_json": {},
                "created_at": "2026-07-15T02:00:00Z",
                "queue_admission_ordinal": None,
                "queued_at": "2026-07-15T02:00:02Z",
                "finished_at": None,
            },
            {
                "id": "run-created-older",
                "trace_id": "trace-older",
                "agent_id": "general-agent",
                "skill_id": "general-chat",
                "status": "failed",
                "result_json": {},
                "error_code": "run_failed",
                "error_message": "older finished later",
                "created_at": "2026-07-15T02:00:00Z",
                "queue_admission_ordinal": None,
                "queued_at": "2026-07-15T02:00:01Z",
                "finished_at": "2026-07-15T03:00:00Z",
            },
        ]

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.lambchat_compat.transaction", fake_transaction)
    monkeypatch.setattr(
        "app.routes.lambchat_compat.repositories.get_authorized_lambchat_session",
        fake_get_authorized_lambchat_session,
    )
    monkeypatch.setattr(
        "app.routes.lambchat_compat.repositories.list_authorized_session_runs",
        fake_list_authorized_session_runs,
    )
    client = TestClient(create_app())

    first_events = client.get("/api/sessions/ses_a/events", headers=auth_headers())
    second_events = client.get("/api/sessions/ses_a/events", headers=auth_headers())
    first_status = client.get("/api/chat/sessions/ses_a/status", headers=auth_headers())
    second_status = client.get("/api/chat/sessions/ses_a/status", headers=auth_headers())

    assert [response.status_code for response in (first_events, second_events, first_status, second_status)] == [
        200,
        200,
        200,
        200,
    ]
    assert [first_events.json()["current_run_id"], second_events.json()["current_run_id"]] == [None, None]
    assert [first_status.json()["raw_status"], second_status.json()["raw_status"]] == [
        "idle",
        "idle",
    ]


def test_lambchat_exact_session_events_restore_an_authorized_run_beyond_the_latest_fifty(monkeypatch):
    async def fake_get_authorized_lambchat_session(conn, *, tenant_id, user_id, session_id):
        return {"id": session_id}

    async def fake_get_authorized_run(conn, *, tenant_id, user_id, run_id):
        assert (tenant_id, user_id, run_id) == ("default", "user-a", "run-51")
        return {
            "id": run_id,
            "session_id": "ses_a",
            "trace_id": "trace-run-51",
            "agent_id": "general-agent",
            "skill_id": "general-chat",
            "status": "succeeded",
            "result_json": {"message": "restored exact old answer"},
            "created_at": "2026-01-01T00:00:00Z",
            "finished_at": "2026-01-01T00:01:00Z",
        }

    async def fail_latest_run_list(*args, **kwargs):
        raise AssertionError("an explicit run id must not use the latest-50 list")

    async def fake_list_authorized_user_messages_for_runs(
        conn, *, tenant_id, user_id, session_id, run_ids
    ):
        assert (tenant_id, user_id, session_id) == ("default", "user-a", "ses_a")
        assert run_ids == ["run-51"]
        return [
            {
                "id": "msg-run-51",
                "run_id": "run-51",
                "content": "恢复旧问题",
                "created_at": "2026-01-01T00:00:00Z",
            },
        ]

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.lambchat_compat.transaction", fake_transaction)
    monkeypatch.setattr(
        "app.routes.lambchat_compat.repositories.get_authorized_lambchat_session",
        fake_get_authorized_lambchat_session,
    )
    monkeypatch.setattr(
        "app.routes.lambchat_compat.repositories.get_authorized_run",
        fake_get_authorized_run,
    )
    monkeypatch.setattr(
        "app.routes.lambchat_compat.repositories.list_authorized_session_runs",
        fail_latest_run_list,
    )
    monkeypatch.setattr(
        "app.routes.lambchat_compat.repositories.list_authorized_user_messages_for_runs",
        fake_list_authorized_user_messages_for_runs,
    )
    client = TestClient(create_app())

    response = client.get(
        "/api/sessions/ses_a/events?run_id=run-51",
        headers=auth_headers(),
    )

    assert response.status_code == 200
    assert response.json()["current_run_id"] == "run-51"
    assert [event["event_type"] for event in response.json()["events"]] == [
        "user:message",
        "message:chunk",
        "done",
    ]
    assert response.json()["events"][0]["data"]["content"] == "恢复旧问题"
    assert response.json()["events"][1]["data"]["content"] == "restored exact old answer"
    assert "metadata_json" not in response.text


def test_lambchat_session_events_reject_cross_tenant_before_listing_messages(monkeypatch):
    async def fake_get_authorized_lambchat_session(conn, *, tenant_id, user_id, session_id):
        assert (tenant_id, user_id, session_id) == ("tenant-b", "user-b", "ses_a")
        return None

    async def fail_list_authorized_user_messages_for_runs(*args, **kwargs):
        raise AssertionError("unauthorized session must not list messages")

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.lambchat_compat.transaction", fake_transaction)
    monkeypatch.setattr(
        "app.routes.lambchat_compat.repositories.get_authorized_lambchat_session",
        fake_get_authorized_lambchat_session,
    )
    monkeypatch.setattr(
        "app.routes.lambchat_compat.repositories.list_authorized_user_messages_for_runs",
        fail_list_authorized_user_messages_for_runs,
    )
    client = TestClient(create_app())

    response = client.get(
        "/api/sessions/ses_a/events",
        headers=action_headers(user_id="user-b", tenant_id="tenant-b"),
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "session_not_found"


@pytest.mark.parametrize("target", [None, {"id": "run-51", "session_id": "ses_other"}])
def test_lambchat_exact_session_events_hide_missing_or_wrong_session_runs(monkeypatch, target):
    async def fake_get_authorized_lambchat_session(conn, *, tenant_id, user_id, session_id):
        return {"id": session_id}

    async def fake_get_authorized_run(conn, *, tenant_id, user_id, run_id):
        assert (tenant_id, user_id, run_id) == ("default", "user-a", "run-51")
        return target

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.lambchat_compat.transaction", fake_transaction)
    monkeypatch.setattr(
        "app.routes.lambchat_compat.repositories.get_authorized_lambchat_session",
        fake_get_authorized_lambchat_session,
    )
    monkeypatch.setattr(
        "app.routes.lambchat_compat.repositories.get_authorized_run",
        fake_get_authorized_run,
    )
    client = TestClient(create_app())

    response = client.get(
        "/api/sessions/ses_a/events?run_id=run-51",
        headers=auth_headers(),
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "run_not_found"


def test_lambchat_session_answer_event_uses_g2_envelope(monkeypatch):
    async def fake_get_authorized_lambchat_session(conn, *, tenant_id, user_id, session_id):
        return {"id": session_id}

    async def fake_list_authorized_session_runs(conn, *, tenant_id, user_id, session_id, limit):
        return [
            {
                "id": "run_a",
                "trace_id": "trace_run_a",
                "agent_id": "general-agent",
                "skill_id": "general-chat",
                "status": "succeeded",
                "result_json": {"message": "hello"},
                "error_message": None,
                "created_at": None,
                "finished_at": None,
            }
        ]

    async def fake_list_run_events(conn, *, tenant_id, run_id):
        return []

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.lambchat_compat.transaction", fake_transaction)
    monkeypatch.setattr(
        "app.routes.lambchat_compat.repositories.get_authorized_lambchat_session",
        fake_get_authorized_lambchat_session,
    )
    monkeypatch.setattr(
        "app.routes.lambchat_compat.repositories.list_authorized_session_runs",
        fake_list_authorized_session_runs,
    )
    monkeypatch.setattr(
        "app.routes.lambchat_compat.repositories.list_run_events",
        fake_list_run_events,
    )
    client = TestClient(create_app())

    response = client.get("/api/sessions/ses_a/events", headers=auth_headers())

    assert response.status_code == 200
    event = response.json()["events"][0]
    assert event["schema_version"] == "ai-platform.event-envelope.v1"
    assert event["trace_id"] == "trace_run_a"
    assert event["type"] == "message:chunk"
    assert event["stage"] == "answer"
    assert event["payload"] == {
        "run_id": "run_a",
        "projection_version": "ai-platform.chat-public-projection.v1",
        "projection_kind": "assistant_final",
        "content": "hello",
    }
    assert event["data"] == event["payload"]
    assert "sequence" not in event


def test_lambchat_session_answer_event_redacts_runtime_private_text(monkeypatch):
    async def fake_get_authorized_lambchat_session(conn, *, tenant_id, user_id, session_id):
        return {"id": session_id}

    async def fake_list_authorized_session_runs(conn, *, tenant_id, user_id, session_id, limit):
        return [
            {
                "id": "run_a",
                "trace_id": "trace_run_a",
                "agent_id": "general-agent",
                "skill_id": "general-chat",
                "status": "failed",
                "result_json": {"message": "failed in /home/xinlin.jiang/qa-review-queue-runtime/out.log"},
                "error_code": "runtime211_stream_error",
                "error_message": "failed in /var/lib/ai-platform/private.log",
                "created_at": None,
                "finished_at": None,
            }
        ]

    async def fake_list_run_events(conn, *, tenant_id, run_id):
        return []

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.lambchat_compat.transaction", fake_transaction)
    monkeypatch.setattr(
        "app.routes.lambchat_compat.repositories.get_authorized_lambchat_session",
        fake_get_authorized_lambchat_session,
    )
    monkeypatch.setattr(
        "app.routes.lambchat_compat.repositories.list_authorized_session_runs",
        fake_list_authorized_session_runs,
    )
    monkeypatch.setattr(
        "app.routes.lambchat_compat.repositories.list_run_events",
        fake_list_run_events,
    )
    client = TestClient(create_app())

    response = client.get("/api/sessions/ses_a/events", headers=auth_headers())

    assert response.status_code == 200
    event = response.json()["events"][0]
    assert event["type"] == "final_detail"
    assert event["payload"] == {
        "run_id": "run_a",
        "projection_version": "ai-platform.chat-public-projection.v1",
        "detail_kind": "failed",
        "detail_code": "run_failed",
        "message": "任务未能完成。请稍后重试；如问题持续，请联系管理员。",
    }
    assert "/home/xinlin.jiang/qa-review-queue-runtime" not in str(event)
    assert "/var/lib/ai-platform" not in str(event)
    assert "runtime211" not in str(event)


def test_lambchat_history_places_artifact_and_safe_failure_detail_before_terminal(monkeypatch):
    async def fake_get_authorized_lambchat_session(conn, *, tenant_id, user_id, session_id):
        return {"id": session_id}

    async def fake_list_authorized_session_runs(conn, *, tenant_id, user_id, session_id, limit):
        return [
            {
                "id": "run_a",
                "trace_id": "trace_run_a",
                "agent_id": "general-agent",
                "skill_id": "general-chat",
                "status": "failed",
                "result_json": {"message": "Executor failed at /private/runtime.log"},
                "error_code": "executor_failed",
                "error_message": "Executor failed at /private/runtime.log",
                "created_at": None,
                "finished_at": None,
            }
        ]

    async def fake_list_run_events(conn, *, tenant_id, run_id):
        base = {
            "trace_id": "trace_run_a",
            "schema_version": "ai-platform.event-envelope.v1",
            "severity": "info",
            "visible_to_user": True,
            "error_code": None,
            "latency_ms": None,
            "input_token_count": 0,
            "output_token_count": 0,
            "total_token_count": 0,
            "estimated_cost_minor": 0,
            "created_at": None,
        }
        # The terminal row is deliberately listed before the artifact row to
        # exercise the compatibility projection ordering contract.
        return [
            {
                **base,
                "id": "evt-failed",
                "sequence": 12,
                "event_type": "run_failed",
                "stage": "worker",
                "message": "Run failed",
                "payload_json": {"visible_to_user": True},
            },
            {
                **base,
                "id": "evt-artifact",
                "sequence": 13,
                "event_type": "artifact_created",
                "stage": "artifact",
                "message": "Artifact created",
                "payload_json": {"artifact_id": "artifact-a", "visible_to_user": True},
            },
        ]

    async def fake_list_run_artifacts(conn, *, tenant_id, run_id):
        return [
            {
                "id": "artifact-a",
                "trace_id": "trace_run_a",
                "artifact_type": "report",
                "label": "失败报告",
                "content_type": "text/plain",
                "storage_key": "tenants/tenant-a/runs/run_a/private.txt",
                "size_bytes": 42,
                "manifest_version": "ai-platform.artifact-manifest.v1",
                "manifest_json": {"local_path": "/var/lib/private.txt"},
                "created_at": None,
            }
        ]

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.lambchat_compat.transaction", fake_transaction)
    monkeypatch.setattr(
        "app.routes.lambchat_compat.repositories.get_authorized_lambchat_session",
        fake_get_authorized_lambchat_session,
    )
    monkeypatch.setattr(
        "app.routes.lambchat_compat.repositories.list_authorized_session_runs",
        fake_list_authorized_session_runs,
    )
    monkeypatch.setattr(
        "app.routes.lambchat_compat.repositories.list_run_events",
        fake_list_run_events,
    )
    monkeypatch.setattr(
        "app.routes.lambchat_compat.repositories.list_run_artifacts",
        fake_list_run_artifacts,
    )
    client = TestClient(create_app())

    response = client.get("/api/sessions/ses_a/events", headers=auth_headers())

    assert response.status_code == 200
    events = response.json()["events"]
    event_types = [event["event_type"] for event in events]
    assert event_types.index("artifact_created") < event_types.index("artifact_card") < event_types.index("final_detail") < event_types.index("done")
    final = events[event_types.index("final_detail")]
    assert final["payload"] == {
        "run_id": "run_a",
        "projection_version": "ai-platform.chat-public-projection.v1",
        "detail_kind": "failed",
        "detail_code": "run_failed",
        "message": "任务未能完成。请稍后重试；如问题持续，请联系管理员。",
    }
    assert final["data"]["run_id"] == "run_a"
    assert "Executor failed" not in str(final)
    artifact = events[event_types.index("artifact_card")]
    assert artifact["data"]["artifact_id"] == "artifact-a"
    assert artifact["data"]["download_url"] == "/api/ai/artifacts/artifact-a/download"
    assert "storage_key" not in str(artifact)
    terminal = events[event_types.index("done")]
    assert terminal["data"] == {"run_id": "run_a", "status": "failed"}
    assert "sequence" not in terminal
    assert all(event["event_type"] != "run_failed" for event in events)


def test_lambchat_session_event_data_redacts_runtime_private_message(monkeypatch):
    async def fake_get_authorized_lambchat_session(conn, *, tenant_id, user_id, session_id):
        return {"id": session_id}

    async def fake_list_authorized_session_runs(conn, *, tenant_id, user_id, session_id, limit):
        return [
            {
                "id": "run_a",
                "trace_id": "trace_run_a",
                "agent_id": "general-agent",
                "skill_id": "general-chat",
                "status": "running",
                "result_json": {},
                "error_message": None,
                "created_at": None,
                "finished_at": None,
            }
        ]

    async def fake_list_run_events(conn, *, tenant_id, run_id):
        return [
            {
                "id": "evt_a",
                "trace_id": "trace_run_a",
                "schema_version": "ai-platform.event-envelope.v1",
                "event_type": "error",
                "stage": "worker",
                "message": "failed in /home/xinlin.jiang/qa-review-queue-runtime/out.log",
                "severity": "error",
                "visible_to_user": True,
                "error_code": "runtime211_stream_error",
                "payload_json": {"visible_to_user": True, "workerPath": "/var/lib/ai-platform/run-a"},
                "created_at": None,
            }
        ]

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.lambchat_compat.transaction", fake_transaction)
    monkeypatch.setattr(
        "app.routes.lambchat_compat.repositories.get_authorized_lambchat_session",
        fake_get_authorized_lambchat_session,
    )
    monkeypatch.setattr(
        "app.routes.lambchat_compat.repositories.list_authorized_session_runs",
        fake_list_authorized_session_runs,
    )
    monkeypatch.setattr(
        "app.routes.lambchat_compat.repositories.list_run_events",
        fake_list_run_events,
    )
    client = TestClient(create_app())

    response = client.get("/api/sessions/ses_a/events", headers=auth_headers())

    assert response.status_code == 200
    event = response.json()["events"][0]
    assert event["payload"] == {"detail_code": "run_failed"}
    assert event["data"]["error"] == "任务未能完成。请稍后重试；如问题持续，请联系管理员。"
    assert "runtime211" not in str(event)
    assert "/home/xinlin.jiang/qa-review-queue-runtime" not in str(event)
    assert "/var/lib/ai-platform" not in str(event)
