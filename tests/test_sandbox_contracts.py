import pytest
from pydantic import ValidationError

from app.runtime.sandbox.contracts import (
    ContainerStatus,
    ContainerLease,
    ExecutorCallbackEvent,
    SandboxRuntimeRequest,
    WorkspaceLease,
)
from app.runtime.kernel_contracts import RunContext


def request_payload(**overrides):
    values = {
        "tenant_id": "tenant-a",
        "workspace_id": "workspace-a",
        "user_id": "user-a",
        "session_id": "session-a",
        "run_id": "run-a",
        "attempt_id": "attempt-a",
        "agent_id": "general-agent",
        "skill_ids": ["general-chat"],
        "mcp_tool_ids": [],
        "input_message": "write a file",
        "file_ids": [],
        "sandbox_mode": "ephemeral",
        "browser_enabled": False,
        "model": "deepseek-v4-flash",
        "model_gateway": "new-api",
        "permissions": ["chat.respond", "sandbox.execute"],
        "resource_limits": {"max_seconds": 120, "max_tool_calls": 20},
        "callback_url": "http://ai-platform:8000/api/ai/runtime/callbacks/executor",
        "callback_token_id": "cbt_run_a",
    }
    values.update(overrides)
    return values


def test_sandbox_runtime_request_requires_platform_identity():
    req = SandboxRuntimeRequest.model_validate(request_payload())

    assert req.tenant_id == "tenant-a"
    assert req.workspace_id == "workspace-a"
    assert req.user_id == "user-a"
    assert req.session_id == "session-a"
    assert req.run_id == "run-a"
    assert req.agent_id == "general-agent"
    assert req.sandbox_mode == "ephemeral"
    assert req.model_gateway == "new-api"


def test_sandbox_contracts_accept_email_style_principal_user_id():
    req = SandboxRuntimeRequest.model_validate(request_payload(user_id="alice@example.test"))
    assert req.user_id == "alice@example.test"

    context = RunContext.model_validate(
        {
            "tenant_id": "tenant-a",
            "workspace_id": "workspace-a",
            "user_id": "alice@example.test",
            "session_id": "session-a",
            "run_id": "run-a",
            "agent_id": "general-agent",
            "skill_ids": ["general-chat"],
            "mcp_tool_ids": [],
            "model": "deepseek-v4-flash",
            "model_gateway": "new-api",
            "input_message": "write a file",
            "file_ids": [],
            "sandbox_mode": "ephemeral",
            "browser_enabled": False,
            "permissions": ["chat.respond", "sandbox.execute"],
            "resource_limits": {"max_seconds": 120, "max_tool_calls": 20},
        }
    )
    assert context.user_id == "alice@example.test"

    workspace = WorkspaceLease(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="alice@example.test",
        session_id="session-a",
        run_id="run-a",
        host_root="C:/runtime/tenants/tenant-a/workspaces/workspace-a/users/alice@example.test/sessions/session-a/runs/run-a",
        workspace_host_path="C:/runtime/tenants/tenant-a/workspaces/workspace-a/users/alice@example.test/sessions/session-a/runs/run-a/workspace",
        workspace_container_path="/workspace",
        inputs_host_path="C:/runtime/tenants/tenant-a/workspaces/workspace-a/users/alice@example.test/sessions/session-a/runs/run-a/inputs",
        logs_host_path="C:/runtime/tenants/tenant-a/workspaces/workspace-a/users/alice@example.test/sessions/session-a/runs/run-a/logs",
    )
    assert workspace.user_id == "alice@example.test"

    lease = ContainerLease(
        container_id="exec-run-a",
        container_name="executor-exec-run-a",
        provider="fake",
        executor_url="http://127.0.0.1:18000",
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="alice@example.test",
        session_id="session-a",
        run_id="run-a",
        sandbox_mode="ephemeral",
        browser_enabled=False,
        workspace_host_path="/runtime/workspace",
        workspace_container_path="/workspace",
        labels={"ai-platform.run_id": "run-a"},
    )
    assert lease.platform_labels()["ai-platform.user_id"] == "alice@example.test"

    status = ContainerStatus(
        container_id="exec-run-a",
        container_name="executor-exec-run-a",
        provider="fake",
        status="running",
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="alice@example.test",
        session_id="session-a",
        run_id="run-a",
        sandbox_mode="ephemeral",
    )
    assert status.user_id == "alice@example.test"


def test_sandbox_contracts_reject_path_like_principal_user_id():
    for unsafe_user_id in ["alice..escape", "../alice@example.test"]:
        with pytest.raises(ValidationError, match="user_id"):
            SandboxRuntimeRequest.model_validate(request_payload(user_id=unsafe_user_id))

        with pytest.raises(ValidationError, match="user_id"):
            RunContext.model_validate(
                {
                    "tenant_id": "tenant-a",
                    "workspace_id": "workspace-a",
                    "user_id": unsafe_user_id,
                    "session_id": "session-a",
                    "run_id": "run-a",
                    "agent_id": "general-agent",
                    "skill_ids": ["general-chat"],
                    "mcp_tool_ids": [],
                    "model": "deepseek-v4-flash",
                    "model_gateway": "new-api",
                    "input_message": "write a file",
                    "file_ids": [],
                    "sandbox_mode": "ephemeral",
                    "browser_enabled": False,
                    "permissions": ["chat.respond", "sandbox.execute"],
                    "resource_limits": {},
                }
            )

        with pytest.raises(ValidationError, match="user_id"):
            WorkspaceLease(
                tenant_id="tenant-a",
                workspace_id="workspace-a",
                user_id=unsafe_user_id,
                session_id="session-a",
                run_id="run-a",
                host_root="/runtime/workspace",
                workspace_host_path="/runtime/workspace",
                workspace_container_path="/workspace",
                inputs_host_path="/runtime/workspace/inputs",
                logs_host_path="/runtime/workspace/logs",
            )

        with pytest.raises(ValidationError, match="user_id"):
            ContainerLease(
                container_id="exec-run-a",
                container_name="executor-exec-run-a",
                provider="fake",
                executor_url="http://127.0.0.1:18000",
                tenant_id="tenant-a",
                workspace_id="workspace-a",
                user_id=unsafe_user_id,
                session_id="session-a",
                run_id="run-a",
                sandbox_mode="ephemeral",
                browser_enabled=False,
                workspace_host_path="/runtime/workspace",
                workspace_container_path="/workspace",
            )

        with pytest.raises(ValidationError, match="user_id"):
            ContainerStatus(
                container_id="exec-run-a",
                container_name="executor-exec-run-a",
                provider="fake",
                status="running",
                tenant_id="tenant-a",
                workspace_id="workspace-a",
                user_id=unsafe_user_id,
                session_id="session-a",
                run_id="run-a",
                sandbox_mode="ephemeral",
            )


def test_sandbox_runtime_request_rejects_none_mode():
    with pytest.raises(ValidationError, match="sandbox_mode"):
        SandboxRuntimeRequest.model_validate(request_payload(sandbox_mode="none"))


def test_workspace_lease_hides_host_path_from_user_payload():
    lease = WorkspaceLease(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        session_id="session-a",
        run_id="run-a",
        host_root="C:/runtime/tenants/tenant-a/workspaces/workspace-a/users/user-a/sessions/session-a/runs/run-a",
        workspace_host_path="C:/runtime/tenants/tenant-a/workspaces/workspace-a/users/user-a/sessions/session-a/runs/run-a/workspace",
        workspace_container_path="/workspace",
        inputs_host_path="C:/runtime/tenants/tenant-a/workspaces/workspace-a/users/user-a/sessions/session-a/runs/run-a/inputs",
        logs_host_path="C:/runtime/tenants/tenant-a/workspaces/workspace-a/users/user-a/sessions/session-a/runs/run-a/logs",
    )

    assert lease.user_visible_payload() == {
        "workspace": "/workspace",
        "inputs": "/workspace/inputs",
    }


def test_container_lease_labels_include_run_scope():
    lease = ContainerLease(
        container_id="exec-run-a",
        container_name="executor-exec-run-a",
        provider="fake",
        executor_url="http://127.0.0.1:18000",
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        session_id="session-a",
        run_id="run-a",
        sandbox_mode="ephemeral",
        browser_enabled=False,
        workspace_host_path="/runtime/workspace",
        workspace_container_path="/workspace",
        labels={"ai-platform.run_id": "run-a"},
    )

    labels = lease.platform_labels()
    assert labels["ai-platform.tenant_id"] == "tenant-a"
    assert labels["ai-platform.workspace_id"] == "workspace-a"
    assert labels["ai-platform.user_id"] == "user-a"
    assert labels["ai-platform.run_id"] == "run-a"


def test_callback_event_rejects_unknown_status():
    with pytest.raises(ValidationError, match="status"):
        ExecutorCallbackEvent.model_validate(
            {
                "session_id": "session-a",
                "run_id": "run-a",
                "attempt_id": "attempt-a",
                "status": "paused",
                "progress": 5,
                "new_message": None,
                "state_patch": {},
                "sdk_session_id": None,
                "error_message": None,
            }
        )


def test_callback_event_accepts_typed_agent_events():
    event = ExecutorCallbackEvent.model_validate(
        {
            "session_id": "session-a",
            "run_id": "run-a",
            "attempt_id": "attempt-a",
            "callback_token_id": "cbt_run-a",
            "status": "running",
            "progress": 40,
            "new_message": None,
            "state_patch": {},
            "events": [
                {
                    "type": "subagent_started",
                    "message": "reviewer started",
                    "payload": {"subagent_id": "reviewer-1", "step_key": "review", "step_index": 2},
                    "admin_only": False,
                }
            ],
        }
    )

    assert len(event.events) == 1
    assert event.events[0].type == "subagent_started"
    assert event.events[0].payload["subagent_id"] == "reviewer-1"


def test_callback_event_rejects_unknown_typed_agent_event_type():
    with pytest.raises(ValidationError, match="Unsupported agent event type"):
        ExecutorCallbackEvent.model_validate(
            {
                "session_id": "session-a",
                "run_id": "run-a",
                "attempt_id": "attempt-a",
                "callback_token_id": "cbt_run-a",
                "status": "running",
                "progress": 40,
                "new_message": None,
                "state_patch": {},
                "events": [
                    {
                        "type": "subagent_secret_dump",
                        "message": "bad event",
                        "payload": {},
                    }
                ],
            }
        )
