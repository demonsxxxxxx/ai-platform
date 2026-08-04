from contextlib import asynccontextmanager

import pytest

from fastapi.testclient import TestClient

from app.main import create_app
from app.models import AgentProfilePublicProjection


def auth_settings():
    return type("S", (), {"trusted_principal_secret": "test-secret", "frontend_poc_auth_enabled": False})()


@asynccontextmanager
async def fake_transaction():
    yield object()


def test_retired_agent_apps_route_requires_principal():
    client = TestClient(create_app())

    response = client.get("/api/ai/agent-apps")

    assert response.status_code == 401


def test_retired_agent_apps_route_points_authenticated_clients_to_profiles(monkeypatch):
    monkeypatch.setattr("app.auth.get_settings", auth_settings)
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

    assert response.status_code == 410
    assert response.json()["detail"] == "agent_apps_retired_use_agent_profiles"


def test_agent_apps_public_profile_detail_uses_safe_authority_projection(monkeypatch):
    async def public_profile(_conn, *, principal, agent_id):
        assert (principal.tenant_id, agent_id) == ("default", "agt_support")
        return AgentProfilePublicProjection(
            agent_id="agt_support",
            expected_revision=7,
            name="Support assistant",
            description="Approved support help.",
            avatar_ref="builtin:assistant",
            category="support",
        )

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.agent_profiles.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.agent_profiles._authority.get_public", public_profile)
    client = TestClient(create_app())

    response = client.get(
        "/api/ai/agent-profiles/agt_support",
        headers={
            "x-ai-user-id": "user-a",
            "x-ai-user-name": "User A",
            "x-ai-tenant-id": "default",
            "x-ai-roles": "user",
            "x-ai-gateway-secret": "test-secret",
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "agent_id": "agt_support",
        "expected_revision": 7,
        "name": "Support assistant",
        "description": "Approved support help.",
        "avatar_ref": "builtin:assistant",
        "category": "support",
    }


async def test_resolve_agent_skill_uses_global_skill_lifecycle_status():
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
    assert "skills.status as skill_status" in sql
    assert "tenant_workbench_skills" not in sql
    assert params == ("qa-file-reviewer", "default", "qa-word-review")


async def test_resolve_agent_skill_projects_canonical_mcp_backing_for_authorizer():
    from app.repositories import resolve_agent_skill

    class OneRowCursor:
        async def fetchone(self):
            return {
                "agent_id": "sop-assistant",
                "agent_status": "active",
                "default_skill_id": "ragflow-knowledge-search",
                "skill_id": "ragflow-knowledge-search",
                "skill_status": "active",
                "skill_version": "0.1.0",
                "executor_type": "claude-agent-worker",
                "input_modes": ["chat"],
                "backing_mcp_tool_id": "ragflow-knowledge-search",
                "mcp_tool_status": "disabled",
            }

    class RecordingConnection:
        def __init__(self):
            self.executed = []

        async def execute(self, sql, params):
            self.executed.append((" ".join(sql.split()), params))
            return OneRowCursor()

    conn = RecordingConnection()

    row = await resolve_agent_skill(
        conn,
        tenant_id="default",
        agent_id="sop-assistant",
        skill_id="ragflow-knowledge-search",
    )

    sql, params = conn.executed[-1]
    assert row["backing_mcp_tool_id"] == "ragflow-knowledge-search"
    assert "left join mcp_tools on mcp_tools.id = skills.id" in sql
    assert "mcp_tools.id as backing_mcp_tool_id" in sql
    assert params == ("ragflow-knowledge-search", "default", "sop-assistant")


async def test_authorize_run_capabilities_rejects_disabled_mcp_backed_skill(monkeypatch):
    from app import repositories

    calls = []

    async def resolve_skill(conn, *, tenant_id, agent_id, skill_id):
        calls.append(("skill", skill_id))
        return {
            "skill_id": skill_id,
            "skill_status": "active",
            "executor_type": "claude-agent-worker",
            "backing_mcp_tool_id": "ragflow-knowledge-search",
        }

    async def get_distribution(conn, *, tenant_id, capability_kind, capability_id):
        calls.append(("distribution", capability_kind, capability_id))
        return {
            "status": "active",
            "visible_to_user": True,
            "scope_mode": "allowlist",
            "department_ids": [],
            "allowed_roles": [],
        }

    async def get_tool(conn, *, tenant_id, tool_id):
        calls.append(("tool", tool_id))
        return {
            "id": tool_id,
            "server_id": "ragflow-server",
            "effective_status": "disabled",
            "server_status": "active",
            "visible_to_user": True,
        }

    monkeypatch.setattr(repositories, "resolve_agent_skill", resolve_skill)
    monkeypatch.setattr(repositories, "get_capability_distribution_row", get_distribution)
    monkeypatch.setattr(repositories, "get_mcp_tool_registry_entry", get_tool)

    with pytest.raises(repositories.RepositoryAuthorizationError) as exc_info:
        await repositories.authorize_run_capabilities(
            object(),
            tenant_id="default",
            agent_id="sop-assistant",
            skill_id="ragflow-knowledge-search",
            normalized_input={},
            principal_department_id="qa",
            principal_roles=["user"],
            is_admin=False,
            permissions=[],
        )

    assert str(exc_info.value) == "capability_not_authorized"
    assert exc_info.value.denial.capability_kind == "mcp_tool"
    assert exc_info.value.denial.capability_id == "ragflow-knowledge-search"
    assert calls == [
        ("skill", "ragflow-knowledge-search"),
        ("distribution", "skill", "ragflow-knowledge-search"),
        ("tool", "ragflow-knowledge-search"),
        ("distribution", "mcp_server", "ragflow-server"),
    ]


async def test_workbench_capability_status_follows_disabled_mcp_tool(monkeypatch):
    from app.repositories import list_workbench_capabilities

    async def no_backfill(conn, *, tenant_id):
        assert tenant_id == "default"

    monkeypatch.setattr("app.repositories.ensure_tenant_capability_distribution_backfill", no_backfill)

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
    assert "when skills.id = 'ragflow-knowledge-search'" in sql
    assert "coalesce(mcp_tools.status, 'disabled') <> 'active'" in sql
    assert "coalesce(tool_policies.status, 'disabled') <> 'active'" in sql
    assert "coalesce(tool_policies.visible_to_user, false) = false" in sql
    assert "tenant_workbench_skills" not in sql
    assert "join tenant_capability_distributions" in sql
    assert "then 'disabled'" in sql
    assert params == ("default", "default")
