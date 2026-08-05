from app.models import AgentProfileDraftRequest, SelectedSkillRequest


def test_models_normalize_agent_profile_acl_and_use_only_builtin_avatar_references():
    definition = AgentProfileDraftRequest(
        name="Support assistant",
        description="Approved support help.",
        instructions="Private instruction.",
        model_id="model-a",
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
