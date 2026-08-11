import pytest

from app.runtime.embedded_poco_kernel import (
    AgentStepExecutionContext,
    ClaudeAgentRoleRunner,
    InProcessEmbeddedPocoKernel,
)
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


@pytest.mark.asyncio
async def test_claude_role_runner_does_not_invent_general_chat_skill(tmp_path):
    captured = {}

    async def fake_sdk_runner(**kwargs):
        captured.update(kwargs)
        return type(
            "SdkResult",
            (),
            {
                "used_sdk": True,
                "message": "done",
                "session_id": "sdk-a",
                "usage": {},
                "error": None,
            },
        )()

    runner = ClaudeAgentRoleRunner(
        workspace_root=tmp_path,
        sdk_runner=fake_sdk_runner,
    )
    step = AgentStepExecutionContext(
        step_key="draft",
        role="writer",
        step_index=0,
        depends_on=[],
        skill_ids=[],
        mcp_tool_ids=[],
        resource_limits={},
        sandbox_mode="none",
        browser_enabled=False,
    )

    await runner.run_role(
        role="writer",
        context=build_context(skill_ids=[], metadata={}),
        previous_outputs=[],
        step=step,
    )

    assert captured["skill_id"] is None
