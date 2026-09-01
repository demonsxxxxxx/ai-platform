import pytest
from pydantic import ValidationError

from app.agent_apps.api import agent_profile_contracts
from app.models import SelectedSkillRequest

AgentProfileDraftRequest = agent_profile_contracts().AgentProfileDraftRequest


def test_models_normalize_agent_profile_acl_and_use_only_builtin_avatar_references():
    definition = AgentProfileDraftRequest(
        name="Support assistant",
        description="Approved support help.",
        instructions="Private instruction.",
        selected_skill=SelectedSkillRequest(skill_id="general-chat", expected_version="version-a"),
        mcp_tool_ids=[],
        avatar_ref="builtin:assistant",
        category="support",
        market_tag=" 客户服务 ",
        visibility="restricted",
        allowed_department_ids=["药品注册", "药品注册"],
        allowed_roles=["User", "user"],
        allowed_user_ids=["user-a", "user-a"],
        expected_draft_revision=0,
    )

    assert definition.allowed_department_ids == ["药品注册"]
    assert definition.allowed_roles == ["user"]
    assert definition.allowed_user_ids == ["user-a"]
    assert definition.market_tag == "客户服务"

    for unsafe_department_id in (
        " 研发一部",
        "研发一部 ",
        "研发一部\n",
        "\t研发一部",
        "研发\u200b一部",
    ):
        invalid = definition.model_dump()
        invalid["allowed_department_ids"] = [unsafe_department_id]
        with pytest.raises(ValidationError):
            AgentProfileDraftRequest.model_validate(invalid)


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
