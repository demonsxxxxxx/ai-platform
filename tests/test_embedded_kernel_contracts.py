import pytest

from app.runtime.kernel_contracts import AgentEvent, RunContext, artifact_storage_prefix


def build_context(**overrides):
    values = {
        "tenant_id": "tenant-a",
        "workspace_id": "workspace-a",
        "user_id": "user-a",
        "session_id": "session-a",
        "run_id": "run-a",
        "agent_id": "general-agent",
        "skill_ids": ["general-chat"],
        "mcp_tool_ids": ["ragflow-search"],
        "model": "deepseek-v4-flash",
        "input_message": "hello kernel",
        "file_ids": ["file-a"],
        "sandbox_mode": "ephemeral",
        "browser_enabled": False,
        "permissions": ["chat.respond"],
        "resource_limits": {"max_steps": 8},
        "metadata": {"source": "pytest"},
    }
    values.update(overrides)
    return RunContext.model_validate(values)


def test_run_context_keeps_phase1_runtime_contract_defaults():
    context = build_context()

    assert context.tenant_id == "tenant-a"
    assert context.workspace_id == "workspace-a"
    assert context.user_id == "user-a"
    assert context.session_id == "session-a"
    assert context.run_id == "run-a"
    assert context.agent_id == "general-agent"
    assert context.skill_ids == ["general-chat"]
    assert context.mcp_tool_ids == ["ragflow-search"]
    assert context.model == "deepseek-v4-flash"
    assert context.model_gateway == "new-api"
    assert context.input_message == "hello kernel"
    assert context.file_ids == ["file-a"]
    assert context.sandbox_mode == "ephemeral"
    assert context.browser_enabled is False
    assert context.permissions == ["chat.respond"]
    assert context.resource_limits == {"max_steps": 8}
    assert context.metadata == {"source": "pytest"}


def test_run_context_requires_user_id():
    values = build_context().model_dump()
    values.pop("user_id")

    with pytest.raises(ValueError, match="user_id"):
        RunContext.model_validate(values)


def test_agent_event_rejects_unsupported_event_types():
    with pytest.raises(ValueError, match="Unsupported agent event type"):
        AgentEvent(type="unsupported", message="nope")


def test_artifact_storage_prefix_follows_tenant_workspace_session_run_contract():
    context = build_context()

    assert artifact_storage_prefix(context) == (
        "tenants/tenant-a/workspaces/workspace-a/sessions/session-a/runs/run-a"
    )
