from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

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
            "persona_preset_id": None,
            "user_timezone": "Asia/Shanghai",
        }
    )

    assert request.message == "hello"
    assert request.agent_options == {"temperature": 0}
    assert request.enabled_skills == ["general-chat"]


def test_lambchat_agents_endpoint_projects_platform_agents(monkeypatch):
    async def fake_list_lambchat_agents(conn, *, tenant_id):
        assert tenant_id == "default"
        return [
            {
                "id": "general-agent",
                "name": "通用聊天 Agent",
                "description": "General company chat",
                "skill_version": "hash-internal-release",
            },
            {
                "id": "qa-word-review",
                "name": "文档审核",
                "description": "Document review",
                "skill_version": "hash-internal-review",
            },
            {
                "id": "baoyu-translate",
                "name": "文档翻译",
                "description": "Document translation",
                "skill_version": "hash-internal-translate",
            }
        ]

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.lambchat_compat.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.lambchat_compat.repositories.list_lambchat_agents", fake_list_lambchat_agents)
    client = TestClient(create_app())

    response = client.get("/api/agents", headers=auth_headers())

    assert response.status_code == 200
    payload = response.json()
    assert payload["default_agent"] == "general-agent"
    assert payload["agents"][0]["id"] == "general-agent"
    assert [item["id"] for item in payload["agents"]] == [
        "general-agent",
        "document-review",
        "document-translation",
    ]
    assert payload["agents"][0]["version"] == "platform-managed"
    assert payload["agents"][0]["supports_sandbox"] is False
    assert "qa-word-review" not in str(payload)
    assert "baoyu-translate" not in str(payload)


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
        "agent:admin",
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
    assert '"event_type": "tool_denied"' in response.text
    assert "tool permission required" in response.text
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
    assert response.text.index("event: artifact_card") < response.text.index('"event_type": "run_succeeded"')


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
    assert "assistant_delta" not in response.text


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
    assert '"error": "run_failed"' in response.text


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
    assert calls["run"] == 2
    assert '"error": "stream_timeout"' in response.text
    assert '"status": "timeout"' in response.text


def test_lambchat_status_normalizes_platform_terminal_statuses(monkeypatch):
    async def fake_get_authorized_lambchat_session(conn, *, tenant_id, user_id, session_id):
        assert user_id == "user-a"
        return {"id": session_id}

    async def fake_list_authorized_session_runs(conn, *, tenant_id, user_id, session_id, limit):
        return [
            {
                "id": "run_succeeded",
                "status": "succeeded",
            },
            {
                "id": "run_failed",
                "status": "failed",
            },
            {
                "id": "run_cancelled",
                "status": "canceled",
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
    assert payload["error"] == "run_failed"
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
    events = response.json()["events"]
    assert len(events) == 1
    event = events[0]
    assert event["schema_version"] == "ai-platform.event-envelope.v1"
    assert event["trace_id"] == "trace_run_a"
    assert event["type"] == "capability_selected"
    assert event["event_type"] == "capability_selected"
    assert event["payload"]["capability_id"] == "document_review"
    assert "skill_id" not in str(event)
    assert "skill_ids" not in str(event)
    assert "storage_key" not in str(event)
    assert "/tmp/" not in str(event)


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
    assert event["payload"] == {"content": "hello"}
    assert event["data"] == {"content": "hello"}


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
    assert event["type"] == "error"
    assert event["payload"] == {"error": "run_failed"}
    assert "/home/xinlin.jiang/qa-review-queue-runtime" not in str(event)
    assert "/var/lib/ai-platform" not in str(event)
    assert "runtime211" not in str(event)


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
    assert event["payload"] == {"visible_to_user": True}
    assert event["data"]["error"] == "run_failed"
    assert "runtime211" not in str(event)
    assert "/home/xinlin.jiang/qa-review-queue-runtime" not in str(event)
    assert "/var/lib/ai-platform" not in str(event)
