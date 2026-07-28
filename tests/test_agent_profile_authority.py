from app.agent_apps import AgentProfileAuthority


def test_agent_profile_authority_exposes_one_deep_lifecycle_interface():
    authority = AgentProfileAuthority()

    for method in (
        "save_draft",
        "publish_draft",
        "unpublish",
        "validate_draft",
        "list_public",
        "get_public",
        "resolve_for_admission",
        "create_conversation",
    ):
        assert callable(getattr(authority, method))
