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
        "resolve_bound_for_submission",
        "resolve_bound_for_worker_dispatch",
        "reauthorize_pinned_run_for_replay",
        "create_conversation",
    ):
        assert callable(getattr(authority, method))
