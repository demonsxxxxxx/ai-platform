import pytest
from pydantic import ValidationError

from app.models import AgentProfileDraftRequest


def profile_payload() -> dict[str, object]:
    return {
        "name": "Support assistant",
        "description": "Approved support help.",
        "starter_prompts": ["Help with support"],
        "instructions": "Private instruction.",
        "skill_set": [{"skill_id": "general-chat"}],
        "mcp_tool_ids": [],
        "avatar_ref": "builtin:assistant",
        "avatar_seed": "support-assistant",
        "market_tags": ["support"],
        "visibility": "restricted",
        "allowed_department_ids": ["药品注册", "药品注册"],
        "allowed_roles": ["User", "user"],
        "allowed_user_ids": ["user-a", "user-a"],
        "expected_draft_revision": 0,
    }


def test_models_normalize_current_profile_lists_and_acl():
    definition = AgentProfileDraftRequest.model_validate(profile_payload())

    assert definition.market_tags == ["support"]
    assert definition.allowed_department_ids == ["药品注册"]
    assert definition.allowed_roles == ["user"]
    assert definition.allowed_user_ids == ["user-a"]

    for unsafe_department_id in (
        " 研发一部",
        "研发一部 ",
        "研发一部\n",
        "\t研发一部",
        "研发\u200b一部",
    ):
        with pytest.raises(ValidationError):
            AgentProfileDraftRequest.model_validate(
                {**profile_payload(), "allowed_department_ids": [unsafe_department_id]}
            )


def test_models_normalize_market_tags_and_reject_duplicates():
    definition = AgentProfileDraftRequest.model_validate(
        {**profile_payload(), "market_tags": [" 客户服务 ", "写作"]}
    )

    assert definition.market_tags == ["客户服务", "写作"]
    with pytest.raises(ValidationError):
        AgentProfileDraftRequest.model_validate(
            {**profile_payload(), "market_tags": ["客户服务", "客户服务"]}
        )


def test_models_reject_every_retired_agent_profile_field():
    retired_fields = {
        "welcome_message": "Welcome",
        "capability_summary": "Summary",
        "recommended_tasks": ["Review"],
        "supported_input_types": ["text", "file"],
        "supported_file_types": ["application/pdf"],
        "expected_outputs": ["Memo"],
        "permissions_and_data_access_notice": "Notice",
        "model_id": "legacy-model",
        "selected_skill": {"skill_id": "general-chat"},
        "avatar_style_ref": "builtin:assistant",
        "avatar_asset_id": "file-avatar",
        "category": "support",
        "market_tag": "support",
    }

    for field, value in retired_fields.items():
        with pytest.raises(ValidationError):
            AgentProfileDraftRequest.model_validate({**profile_payload(), field: value})
