import pytest
from fastapi import HTTPException

from app.auth import AuthPrincipal


def _principal(*, roles: list[str] | None = None, department_id: str = "") -> AuthPrincipal:
    return AuthPrincipal(
        user_id="user-a",
        display_name="User A",
        tenant_id="tenant-a",
        department_id=department_id,
        roles=roles or ["user"],
    )


def test_profile_acl_and_safe_projection_are_owned_by_the_agent_apps_module():
    from app.agent_apps import profile_acl_allows, profile_public_projection

    row = {
        "agent_id": "agt_support",
        "revision": 7,
        "name": "Support assistant",
        "description": "Approved support help.",
        "avatar_ref": "builtin:assistant",
        "category": "support",
        "visibility": "restricted",
        "allowed_department_ids": ["support"],
        "allowed_roles": ["user"],
        "allowed_user_ids": [],
        "instructions": "private instruction",
        "model_id": "private-model",
        "skill_id": "private-skill",
        "skill_version": "private-version",
        "mcp_tool_ids": ["private-tool"],
        "content_hash": "a" * 64,
    }

    assert profile_acl_allows(row, principal=_principal(department_id="support")) is True
    assert profile_acl_allows(row, principal=_principal(department_id="finance")) is False
    assert profile_public_projection(row) == {
        "agent_id": "agt_support",
        "expected_revision": 7,
        "name": "Support assistant",
        "description": "Approved support help.",
        "avatar_ref": "builtin:assistant",
        "category": "support",
    }


@pytest.mark.asyncio
async def test_unpublished_profile_is_not_admitted_to_an_existing_agent_conversation(monkeypatch):
    from app.agent_apps import AgentProfileAuthority
    from app.models import SelectedAgentProfileRequest

    async def no_current_publication(*_args, **_kwargs):
        return None

    monkeypatch.setattr(
        "app.agent_apps.authority.repositories.get_current_published_agent_profile",
        no_current_publication,
    )
    authority = AgentProfileAuthority()

    with pytest.raises(Exception) as caught:
        await authority.resolve_for_admission(
            object(),
            principal=_principal(),
            selection=SelectedAgentProfileRequest(agent_id="agt_support", expected_revision=7),
        )

    assert getattr(caught.value, "detail", None) == "agent_profile_not_available"


def _profile_row(*, status: str = "published", revision: int = 7) -> dict[str, object]:
    return {
        "agent_id": "agt_support",
        "revision": revision,
        "status": status,
        "name": "Support assistant",
        "description": "Approved support help.",
        "avatar_ref": "builtin:assistant",
        "category": "support",
        "visibility": "tenant",
        "allowed_department_ids": [],
        "allowed_roles": [],
        "allowed_user_ids": [],
        "instructions": "private instruction",
        "model_id": "model-a",
        "skill_id": "general-chat",
        "skill_version": "version-a",
        "mcp_tool_ids": [],
        "content_hash": "a" * 64,
    }


@pytest.mark.asyncio
async def test_public_detail_uses_the_same_acl_as_catalog(monkeypatch):
    from app.agent_apps import AgentProfileAuthority

    restricted = _profile_row()
    restricted.update({"visibility": "restricted", "allowed_department_ids": ["support"]})

    async def get_current(*_args, **_kwargs):
        return restricted

    async def list_current(*_args, **_kwargs):
        return [restricted]

    monkeypatch.setattr("app.agent_apps.authority.repositories.get_current_published_agent_profile", get_current)
    monkeypatch.setattr("app.agent_apps.authority.repositories.list_current_published_agent_profiles", list_current)
    authority = AgentProfileAuthority()

    assert await authority.list_public(object(), principal=_principal(department_id="finance")) == []
    with pytest.raises(HTTPException) as caught:
        await authority.get_public(object(), principal=_principal(department_id="finance"), agent_id="agt_support")
    assert (caught.value.status_code, caught.value.detail) == (404, "agent_profile_not_found")


@pytest.mark.asyncio
async def test_agent_conversation_admission_locks_and_pins_only_safe_identity(monkeypatch):
    from app.agent_apps import AgentProfileAuthority
    from app.models import SelectedAgentProfileRequest

    observed: dict[str, object] = {}

    async def get_current(*_args, **kwargs):
        observed["for_update"] = kwargs.get("for_update")
        return _profile_row()

    async def validate(*_args, **_kwargs):
        return ({"skill_id": "general-chat", "skill_version": "version-a"}, {"id": "model-a", "value": "model-a"})

    async def remember_workspace(*_args, **_kwargs):
        observed["workspace"] = True

    async def remember_user(*_args, **_kwargs):
        observed["user"] = True

    async def create_session(*_args, **kwargs):
        observed["session"] = kwargs
        return "ses_profile"

    async def audit(*_args, **kwargs):
        observed["audit"] = kwargs
        return "aud_conversation"

    monkeypatch.setattr("app.agent_apps.authority.repositories.get_current_published_agent_profile", get_current)
    monkeypatch.setattr("app.agent_apps.authority.repositories.ensure_workspace", remember_workspace)
    monkeypatch.setattr("app.agent_apps.authority.repositories.ensure_user", remember_user)
    monkeypatch.setattr("app.agent_apps.authority.repositories.create_session", create_session)
    monkeypatch.setattr("app.agent_apps.authority.repositories.append_audit_log", audit)
    authority = AgentProfileAuthority()
    monkeypatch.setattr(authority, "_validate_definition", validate)

    response = await authority.create_conversation(
        object(),
        principal=_principal(),
        workspace_id="default",
        selection=SelectedAgentProfileRequest(agent_id="agt_support", expected_revision=7),
        title="",
    )

    assert observed["for_update"] is True
    assert observed["workspace"] is True and observed["user"] is True
    assert observed["session"] == {
        "tenant_id": "tenant-a",
        "workspace_id": "default",
        "user_id": "user-a",
        "agent_id": "agt_support",
        "title": "Support assistant",
        "admitted_agent_profile_revision": 7,
        "admitted_agent_profile_hash": "a" * 64,
    }
    assert observed["audit"]["payload_json"] == {"revision": 7, "session_id": "ses_profile"}
    assert response.model_dump() == {
        "session_id": "ses_profile",
        "workspace_id": "default",
        "agent_id": "agt_support",
        "title": "Support assistant",
        "agent_conversation": {
            "agent_id": "agt_support",
            "revision": 7,
            "name": "Support assistant",
            "description": "Approved support help.",
            "avatar_ref": "builtin:assistant",
            "category": "support",
        },
        "created_at": None,
        "updated_at": None,
    }
    assert "private instruction" not in str(response.model_dump())


@pytest.mark.asyncio
async def test_unpublish_records_an_immutable_withdrawn_revision_and_clears_admission(monkeypatch):
    from app.agent_apps import AgentProfileAuthority

    observed: dict[str, object] = {}

    async def aggregate(*_args, **kwargs):
        observed["aggregate_lock"] = kwargs.get("for_update")
        return {"lifecycle_status": "published", "published_revision": 7, "latest_revision": 8}

    async def get_revision(*_args, **kwargs):
        observed["published_lookup"] = kwargs
        return _profile_row()

    async def append_revision(*_args, **kwargs):
        observed["append"] = kwargs
        return _profile_row(status="withdrawn", revision=9)

    async def record_withdrawal(*_args, **kwargs):
        observed["withdrawal"] = kwargs

    async def audit(*_args, **kwargs):
        observed["audit"] = kwargs
        return "aud_profile_withdrawn"

    monkeypatch.setattr("app.agent_apps.authority.repositories.get_agent_profile_aggregate", aggregate)
    monkeypatch.setattr("app.agent_apps.authority.repositories.get_agent_profile_revision", get_revision)
    monkeypatch.setattr("app.agent_apps.authority.repositories.create_agent_profile_revision", append_revision)
    monkeypatch.setattr("app.agent_apps.authority.repositories.record_agent_profile_withdrawal", record_withdrawal)
    monkeypatch.setattr("app.agent_apps.authority.repositories.append_audit_log", audit)

    profile, audit_id = await AgentProfileAuthority().unpublish(
        object(),
        principal=_principal(roles=["admin"]),
        agent_id="agt_support",
        expected_revision=7,
    )

    assert observed["aggregate_lock"] is True
    assert observed["append"]["status"] == "withdrawn"
    assert observed["append"]["expected_previous_revision"] == 8
    assert observed["append"]["withdrawn_from_revision"] == 7
    assert observed["withdrawal"] == {"tenant_id": "tenant-a", "agent_id": "agt_support", "revision": 9}
    assert profile.status == "withdrawn"
    assert audit_id == "aud_profile_withdrawn"


def test_profile_bound_continuation_rejects_client_execution_overrides():
    from app.agent_apps.authority import reject_profile_selector_conflicts
    from app.models import ChatStreamRequest, SelectedSkillRequest

    request = ChatStreamRequest(
        message="continue",
        selected_skill=SelectedSkillRequest(skill_id="general-chat", expected_version="version-a"),
    )

    with pytest.raises(HTTPException) as caught:
        reject_profile_selector_conflicts(request, active=True)
    assert (caught.value.status_code, caught.value.detail) == (400, "agent_profile_selector_conflict")


def test_session_recovery_projects_only_safe_agent_conversation_identity():
    from app.routes.chat import _session_response

    response = _session_response(
        {
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
            "instructions": "must never be projected",
            "model_id": "private-model",
            "skill_id": "private-skill",
            "mcp_tool_ids": ["private-tool"],
        }
    ).model_dump()

    assert response["agent_conversation"] == {
        "agent_id": "agt_support",
        "revision": 7,
        "name": "Support assistant",
        "description": "Approved support help.",
        "avatar_ref": "builtin:assistant",
        "category": "support",
    }
    serialized = str(response)
    for forbidden in ("must never be projected", "private-model", "private-skill", "private-tool", "a" * 64):
        assert forbidden not in serialized
