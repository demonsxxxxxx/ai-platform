import pytest
from pydantic import ValidationError

from app.models import AgentProfileDraftRequest, SelectedSkillRequest


def test_models_normalize_agent_profile_acl_and_use_only_builtin_avatar_references():
    definition = AgentProfileDraftRequest(
        name="Support assistant",
        description="Approved support help.",
        instructions="Private instruction.",
        selected_skill=SelectedSkillRequest(skill_id="general-chat", expected_version="version-a"),
        mcp_tool_ids=[],
        avatar_ref="builtin:assistant",
        category="support",
        visibility="restricted",
        allowed_department_ids=["support", "support"],
        allowed_roles=["User", "user"],
        allowed_user_ids=["user-a", "user-a"],
        expected_draft_revision=0,
    )

    assert definition.allowed_department_ids == ["support"]
    assert definition.allowed_roles == ["user"]
    assert definition.allowed_user_ids == ["user-a"]


def test_models_discard_only_the_retired_agent_profile_model_field():
    payload = {
        "name": "Support assistant",
        "instructions": "Private instruction.",
        "model_id": "legacy-model",
        "selected_skill": {
            "skill_id": "general-chat",
            "expected_version": "version-a",
        },
        "expected_draft_revision": 0,
    }

    definition = AgentProfileDraftRequest.model_validate(payload)

    assert not hasattr(definition, "model_id")
    assert "model_id" not in definition.model_dump()
    with pytest.raises(ValidationError):
        AgentProfileDraftRequest.model_validate({**payload, "unknown_field": "forbidden"})
