import pytest

from app.runtime.embedded_poco_kernel import InProcessEmbeddedPocoKernel
from app.runtime.kernel_contracts import RunContext


def build_context(**overrides):
    values = {
        "tenant_id": "tenant-a",
        "workspace_id": "workspace-a",
        "user_id": "user-a",
        "session_id": "session-a",
        "run_id": "run-a",
        "agent_id": "general-agent",
        "skill_ids": ["general-chat"],
        "mcp_tool_ids": [],
        "model": "deepseek-v4-flash",
        "input_message": "hello kernel",
        "file_ids": [],
        "sandbox_mode": "none",
        "browser_enabled": False,
        "permissions": ["chat.respond"],
        "resource_limits": {"max_steps": 4},
        "metadata": {"source": "pytest"},
    }
    values.update(overrides)
    return RunContext.model_validate(values)


@pytest.mark.asyncio
async def test_submit_run_emits_started_delta_completed_for_allowed_chat():
    kernel = InProcessEmbeddedPocoKernel()
    events = []

    async def sink(event):
        events.append(event)

    await kernel.submit_run(build_context(), sink)

    assert [event.type for event in events] == ["run_started", "assistant_delta", "run_completed"]
    assert events[0].message == "Run started"
    assert "hello kernel" in events[1].payload["delta"]
    assert events[2].payload["status"] == "succeeded"
    assert "artifact_storage_prefix" not in events[2].payload
    assert all("tenants/" not in str(event.payload) for event in events)
    assert all("workspaces/" not in str(event.payload) for event in events)


@pytest.mark.asyncio
async def test_submit_run_emits_started_failed_when_chat_permission_is_missing():
    kernel = InProcessEmbeddedPocoKernel()
    events = []

    async def sink(event):
        events.append(event)

    await kernel.submit_run(build_context(permissions=[]), sink)

    assert [event.type for event in events] == ["run_started", "run_failed"]
    assert events[1].payload["error_code"] == "permission_denied"
    assert "chat.respond" in events[1].message
