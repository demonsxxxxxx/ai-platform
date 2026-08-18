from contextlib import asynccontextmanager

import pytest

from fastapi.testclient import TestClient

from app.main import create_app
from app.models import AgentProfilePublicProjection, ChatStreamResponse


def auth_settings():
    return type("S", (), {"trusted_principal_secret": "test-secret", "frontend_poc_auth_enabled": False})()


def auth_headers(*, roles: str = "user") -> dict[str, str]:
    return {
        "x-ai-user-id": "user-a",
        "x-ai-user-name": "User A",
        "x-ai-tenant-id": "default",
        "x-ai-roles": roles,
        "x-ai-gateway-secret": "test-secret",
    }


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
        "welcome_message": "",
        "starter_prompts": [],
        "capability_summary": "",
        "recommended_tasks": [],
        "supported_input_types": ["text", "file"],
        "expected_outputs": [],
        "permissions_and_data_access_notice": "",
        "published_at": None,
        "avatar_ref": "builtin:assistant",
        "avatar_seed": "",
        "category": "support",
    }


def test_dedicated_agent_run_restores_session_and_delegates_without_client_selectors(monkeypatch):
    observed: dict[str, object] = {}

    async def get_session(_conn, *, tenant_id, user_id, session_id):
        observed["session_read"] = (tenant_id, user_id, session_id)
        return {"workspace_id": "finance", "agent_id": "agt_support"}

    async def chat_stream(request, *, agent_id, principal):
        observed["chat_request"] = request.model_dump(mode="python")
        observed["chat_agent"] = agent_id
        observed["chat_user"] = principal.user_id
        return ChatStreamResponse(
            session_id=request.session_id,
            run_id="run-agent",
            status="queued",
            submission_id=str(request.submission_id),
        )

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.agent_profiles.transaction", fake_transaction)
    monkeypatch.setattr(
        "app.routes.agent_profiles.repositories.get_authorized_session_projection",
        get_session,
    )
    monkeypatch.setattr("app.routes.chat.chat_stream", chat_stream)
    client = TestClient(create_app())

    response = client.post(
        "/api/ai/agent-apps/agt_support/conversations/ses_support/runs",
        headers=auth_headers(),
        json={
            "message": "Review this request",
            "submission_id": "11111111-1111-4111-8111-111111111111",
            "file_ids": ["file-a"],
            "user_timezone": "Asia/Shanghai",
        },
    )

    assert response.status_code == 200
    assert response.json()["run_id"] == "run-agent"
    assert observed["session_read"] == ("default", "user-a", "ses_support")
    assert observed["chat_agent"] == "agt_support"
    assert observed["chat_user"] == "user-a"
    chat_request = observed["chat_request"]
    assert chat_request["workspace_id"] == "finance"
    assert chat_request["session_id"] == "ses_support"
    assert chat_request["selected_agent_profile"] is None
    assert chat_request["selected_skill"] is None
    assert chat_request["selected_mcp_tool_ids"] is None
    assert "model_id" not in chat_request


@pytest.mark.parametrize(
    ("path_suffix", "extra_headers", "body_extra", "expected_status"),
    [
        ("?model_id=private-model", {}, {}, 400),
        ("", {"x-skill-id": "private-skill"}, {}, 400),
        ("", {}, {"selected_mcp_tool_ids": ["private-tool"]}, 422),
        ("", {}, {"agent_id": "agt_other"}, 422),
    ],
)
def test_dedicated_agent_run_rejects_every_override_before_storage_or_dispatch(
    monkeypatch,
    path_suffix,
    extra_headers,
    body_extra,
    expected_status,
):
    calls: list[str] = []

    async def forbidden_session_read(*_args, **_kwargs):
        calls.append("session")
        return {"workspace_id": "default", "agent_id": "agt_support"}

    async def forbidden_chat(*_args, **_kwargs):
        calls.append("chat")
        raise AssertionError("override must be rejected before dispatch")

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.agent_profiles.transaction", fake_transaction)
    monkeypatch.setattr(
        "app.routes.agent_profiles.repositories.get_authorized_session_projection",
        forbidden_session_read,
    )
    monkeypatch.setattr("app.routes.chat.chat_stream", forbidden_chat)
    client = TestClient(create_app())
    body = {
        "message": "Review this request",
        "submission_id": "11111111-1111-4111-8111-111111111111",
        **body_extra,
    }

    response = client.post(
        f"/api/ai/agent-apps/agt_support/conversations/ses_support/runs{path_suffix}",
        headers={**auth_headers(), **extra_headers},
        json=body,
    )

    assert response.status_code == expected_status
    assert calls == []


@pytest.mark.parametrize(
    ("session", "expected_status", "expected_detail"),
    [
        (None, 404, "agent_conversation_not_found"),
        ({"workspace_id": "default", "agent_id": "agt_other"}, 409, "agent_profile_session_mismatch"),
    ],
)
def test_dedicated_agent_run_fails_closed_on_ownership_or_agent_mismatch(
    monkeypatch,
    session,
    expected_status,
    expected_detail,
):
    dispatched = False

    async def get_session(*_args, **_kwargs):
        return session

    async def forbidden_chat(*_args, **_kwargs):
        nonlocal dispatched
        dispatched = True
        raise AssertionError("invalid session must not dispatch")

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.agent_profiles.transaction", fake_transaction)
    monkeypatch.setattr(
        "app.routes.agent_profiles.repositories.get_authorized_session_projection",
        get_session,
    )
    monkeypatch.setattr("app.routes.chat.chat_stream", forbidden_chat)
    client = TestClient(create_app())

    response = client.post(
        "/api/ai/agent-apps/agt_support/conversations/ses_support/runs",
        headers=auth_headers(),
        json={
            "message": "Review this request",
            "submission_id": "11111111-1111-4111-8111-111111111111",
        },
    )

    assert response.status_code == expected_status
    assert response.json()["detail"] == expected_detail
    assert dispatched is False


def test_builder_trial_run_is_idempotently_bound_to_one_test_session_and_canonical_run(monkeypatch):
    observed: dict[str, list[object]] = {"conversations": [], "runs": []}

    async def create_conversation(_conn, **kwargs):
        observed["conversations"].append(kwargs)

    async def submit_run(**kwargs):
        observed["runs"].append(kwargs)
        return ChatStreamResponse(
            session_id=kwargs["session_id"],
            run_id="run-test",
            status="queued",
            submission_id=str(kwargs["request"].submission_id),
        )

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.agent_profiles.transaction", fake_transaction)
    monkeypatch.setattr(
        "app.routes.agent_profiles._authority.create_conversation",
        create_conversation,
    )
    monkeypatch.setattr("app.routes.agent_profiles._submit_dedicated_agent_run", submit_run)
    client = TestClient(create_app())
    body = {
        "expected_revision": 7,
        "message": "Run the enterprise test",
        "submission_id": "22222222-2222-4222-8222-222222222222",
    }

    first = client.post(
        "/api/ai/admin/agent-profiles/agt_support/test-runs",
        headers=auth_headers(roles="admin"),
        json=body,
    )
    second = client.post(
        "/api/ai/admin/agent-profiles/agt_support/test-runs",
        headers=auth_headers(roles="admin"),
        json=body,
    )

    assert first.status_code == second.status_code == 200
    assert first.json()["purpose"] == "builder_test"
    assert first.json()["session_id"] == second.json()["session_id"]
    assert first.json()["run_id"] == second.json()["run_id"] == "run-test"
    expected_session_id = "ses_test_22222222222242228222222222222222"
    assert [call["session_id"] for call in observed["conversations"]] == [
        expected_session_id,
        expected_session_id,
    ]
    for call in observed["conversations"]:
        assert call["purpose"] == "builder_test"
        assert call["selection"].agent_id == "agt_support"
        assert call["selection"].expected_revision == 7
    assert [call["session_id"] for call in observed["runs"]] == [
        expected_session_id,
        expected_session_id,
    ]


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
