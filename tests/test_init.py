from app.agent_apps import AgentProfileAuthority


def test_init_exports_agent_apps_authority():
    assert AgentProfileAuthority.__name__ == "AgentProfileAuthority"
