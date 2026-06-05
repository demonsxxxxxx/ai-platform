from contextlib import asynccontextmanager

import pytest

from fastapi.testclient import TestClient

from app.main import create_app


def auth_settings():
    return type("S", (), {"trusted_principal_secret": "test-secret", "frontend_poc_auth_enabled": False})()


@asynccontextmanager
async def fake_transaction():
    yield object()


async def fake_agent_app_rows(conn, *, tenant_id):
    assert tenant_id == "default"
    return [
        {
            "app_id": "baoyu-translate",
            "name": "文档翻译",
            "agent_type": "file",
            "default_skill_id": "baoyu-translate",
            "input_modes": ["docx"],
            "output_modes": ["translated_docx"],
            "status": "active",
        },
        {
            "app_id": "qa-word-review",
            "name": "文档审核",
            "agent_type": "file",
            "default_skill_id": "qa-file-reviewer",
            "input_modes": ["docx"],
            "output_modes": ["reviewed_docx", "findings_json"],
            "status": "active",
        },
    ]


def test_agent_apps_projection_requires_principal():
    client = TestClient(create_app())

    response = client.get("/api/ai/agent-apps")

    assert response.status_code == 401


def test_agent_apps_projection_requires_admin(monkeypatch):
    async def fail_list_agent_apps(*args, **kwargs):
        raise AssertionError("ordinary user must not list raw agent app skill projections")

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.agent_apps.repositories.list_agent_app_projections", fail_list_agent_apps)
    client = TestClient(create_app())

    response = client.get(
        "/api/ai/agent-apps",
        headers={
            "x-ai-user-id": "user-a",
            "x-ai-user-name": "User A",
            "x-ai-tenant-id": "default",
            "x-ai-roles": "user",
            "x-ai-gateway-secret": "test-secret",
        },
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "not_ai_admin"


def test_agent_apps_projection_returns_translation_and_review_for_admin(monkeypatch):
    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.agent_apps.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.agent_apps.repositories.list_agent_app_projections", fake_agent_app_rows)
    client = TestClient(create_app())

    response = client.get(
        "/api/ai/agent-apps",
        headers={
            "x-ai-user-id": "user-a",
            "x-ai-user-name": "User A",
            "x-ai-tenant-id": "default",
            "x-ai-roles": "developer",
            "x-ai-gateway-secret": "test-secret",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["agent_apps"] == [
        {
            "app_id": "baoyu-translate",
            "name": "文档翻译",
            "mode": "chat_file",
            "default_skill_id": "baoyu-translate",
            "allowed_input_types": ["docx"],
            "output_types": ["translated_docx"],
            "status": "active",
        },
        {
            "app_id": "qa-word-review",
            "name": "文档审核",
            "mode": "chat_file",
            "default_skill_id": "qa-file-reviewer",
            "allowed_input_types": ["docx"],
            "output_types": ["reviewed_docx", "findings_json"],
            "status": "active",
        },
    ]


class FakeCursor:
    def __init__(self, rows):
        self.rows = rows

    async def fetchall(self):
        return self.rows


class RecordingConnection:
    def __init__(self):
        self.executed = []

    async def execute(self, sql, params):
        self.executed.append((" ".join(sql.split()), params))
        return FakeCursor([])


async def test_agent_app_repository_uses_active_platform_registry():
    from app.repositories import list_agent_app_projections

    conn = RecordingConnection()

    rows = await list_agent_app_projections(conn, tenant_id="default")

    assert rows == []
    sql, params = conn.executed[-1]
    assert "from agents join skills on skills.id = agents.default_skill_id" in sql
    assert "agents.id in ('baoyu-translate', 'qa-word-review')" in sql
    assert "agents.status = 'active'" in sql
    assert "skills.status = 'active'" in sql
    assert params == ("default",)


async def test_resolve_agent_skill_uses_tenant_workbench_skill_status():
    from app.repositories import RepositoryConflictError, resolve_agent_skill

    class OneRowCursor:
        async def fetchone(self):
            return {
                "agent_id": "qa-word-review",
                "agent_status": "active",
                "default_skill_id": "qa-file-reviewer",
                "skill_id": "qa-file-reviewer",
                "skill_status": "disabled",
                "skill_version": "0.1.0",
                "executor_type": "claude-agent-worker",
                "input_modes": ["docx"],
            }

    class RecordingConnection:
        def __init__(self):
            self.executed = []

        async def execute(self, sql, params):
            self.executed.append((" ".join(sql.split()), params))
            return OneRowCursor()

    conn = RecordingConnection()

    with pytest.raises(RepositoryConflictError, match="skill_inactive"):
        await resolve_agent_skill(
            conn,
            tenant_id="default",
            agent_id="qa-word-review",
            skill_id="qa-file-reviewer",
        )

    sql, params = conn.executed[-1]
    assert "left join tenant_workbench_skills" in sql
    assert "coalesce(tenant_workbench_skills.status, skills.status) as skill_status" in sql
    assert params == ("qa-file-reviewer", "default", "qa-word-review")


async def test_resolve_agent_skill_rejects_disabled_mcp_backed_skill():
    from app.repositories import RepositoryConflictError, resolve_agent_skill

    class OneRowCursor:
        async def fetchone(self):
            return {
                "agent_id": "sop-assistant",
                "agent_status": "active",
                "default_skill_id": "ragflow-knowledge-search",
                "skill_id": "ragflow-knowledge-search",
                "skill_status": "active",
                "skill_version": "0.1.0",
                "executor_type": "ragflow",
                "input_modes": ["chat"],
                "mcp_tool_status": "disabled",
            }

    class RecordingConnection:
        def __init__(self):
            self.executed = []

        async def execute(self, sql, params):
            self.executed.append((" ".join(sql.split()), params))
            return OneRowCursor()

    conn = RecordingConnection()

    with pytest.raises(RepositoryConflictError, match="mcp_tool_disabled"):
        await resolve_agent_skill(
            conn,
            tenant_id="default",
            agent_id="sop-assistant",
            skill_id="ragflow-knowledge-search",
        )

    sql, params = conn.executed[-1]
    assert "left join mcp_tools on mcp_tools.id = skills.id" in sql
    assert "mcp_tools.status as mcp_tool_status" in sql
    assert params == ("ragflow-knowledge-search", "default", "sop-assistant")


async def test_workbench_capability_status_follows_disabled_mcp_tool():
    from app.repositories import list_workbench_capabilities

    class EmptyCursor:
        async def fetchall(self):
            return []

    class RecordingConnection:
        def __init__(self):
            self.executed = []

        async def execute(self, sql, params):
            self.executed.append((" ".join(sql.split()), params))
            return EmptyCursor()

    conn = RecordingConnection()

    rows = await list_workbench_capabilities(conn, tenant_id="default")

    assert rows == []
    sql, params = conn.executed[-1]
    assert "when skills.executor_type = 'ragflow'" in sql
    assert "coalesce(mcp_tools.status, 'disabled') <> 'active'" in sql
    assert "coalesce(tool_policies.status, 'disabled') <> 'active'" in sql
    assert "coalesce(tool_policies.visible_to_user, false) = false" in sql
    assert "then 'disabled'" in sql
    assert params == ("default",)
