import hashlib
import hmac
from pathlib import Path

import pytest

from app.runtime.sandbox.container_provider import FakeContainerProvider
from app.runtime.sandbox.contracts import SandboxRuntimeRequest
from app.runtime.sandbox.runtime import SandboxRuntime


def derived_callback_token(secret: str, token_id: str = "cbt_run-a") -> str:
    return hmac.new(secret.encode("utf-8"), token_id.encode("utf-8"), hashlib.sha256).hexdigest()


def request(**overrides) -> SandboxRuntimeRequest:
    values = {
        "tenant_id": "tenant-a",
        "workspace_id": "workspace-a",
        "user_id": "user-a",
        "session_id": "session-a",
        "run_id": "run-a",
        "agent_id": "general-agent",
        "skill_ids": ["general-chat"],
        "mcp_tool_ids": ["knowledge.search"],
        "input_message": "hello",
        "file_ids": ["file-a"],
        "sandbox_mode": "ephemeral",
        "browser_enabled": True,
        "model": "deepseek-v4-flash",
        "permissions": ["sandbox.execute"],
        "resource_limits": {"max_seconds": 120, "max_tool_calls": 20},
        "callback_url": "http://callback.test/api/ai/runtime/callbacks/executor",
        "callback_token_id": "cbt_run-a",
    }
    values.update(overrides)
    return SandboxRuntimeRequest(**values)


@pytest.mark.asyncio
async def test_runtime_submit_prepares_workspace_emits_event_and_dispatches_executor(tmp_path, monkeypatch):
    sent = []

    class StubSettings:
        sandbox_callback_base_url = "http://platform.test"
        sandbox_callback_token = "settings-token"

    async def execute(executor_url, task_request):
        sent.append((executor_url, task_request))
        return {"status": "accepted", "session_id": task_request.session_id, "run_id": task_request.run_id}

    monkeypatch.setattr("app.runtime.sandbox.runtime.get_settings", lambda: StubSettings())

    events = []
    runtime = SandboxRuntime(
        workspace_root=tmp_path,
        provider=FakeContainerProvider(executor_url="http://executor.test"),
        execute_task=execute,
        callback_token_resolver=lambda token_id: "secret-token" if token_id == "cbt_run-a" else "",
    )

    result = await runtime.submit(request(), event_sink=events.append)

    run_root = (
        tmp_path
        / "tenants"
        / "tenant-a"
        / "workspaces"
        / "workspace-a"
        / "users"
        / "user-a"
        / "sessions"
        / "session-a"
        / "runs"
        / "run-a"
    )

    assert result.status == "accepted"
    assert result.session_id == "session-a"
    assert result.run_id == "run-a"
    assert result.executor_response["status"] == "accepted"
    assert Path(run_root / "workspace").is_dir()
    assert Path(run_root / "inputs").is_dir()
    assert Path(run_root / "logs").is_dir()
    assert sent[0][0] == "http://executor.test"
    assert sent[0][1].session_id == "session-a"
    assert sent[0][1].run_id == "run-a"
    assert sent[0][1].prompt == "hello"
    assert sent[0][1].callback_url == "http://callback.test/api/ai/runtime/callbacks/executor"
    assert sent[0][1].callback_token_id == "cbt_run-a"
    assert sent[0][1].callback_token == "secret-token"
    assert sent[0][1].callback_base_url == "http://platform.test"
    assert sent[0][1].permission_mode == "default"
    assert sent[0][1].config == {
        "model": "deepseek-v4-flash",
        "browser_enabled": True,
        "resource_limits": {"max_seconds": 120, "max_tool_calls": 20},
        "skill_ids": ["general-chat"],
        "mcp_tool_ids": ["knowledge.search"],
        "input_files": ["file-a"],
    }
    assert [event.type for event in events] == ["runtime_container_started"]


@pytest.mark.asyncio
async def test_runtime_default_callback_token_is_hmac_scoped_to_token_id(tmp_path, monkeypatch):
    sent = []

    class StubSettings:
        sandbox_callback_base_url = "http://platform.test"
        sandbox_callback_token = "settings-token"

    async def execute(executor_url, task_request):
        sent.append(task_request)
        return {"status": "accepted", "session_id": task_request.session_id, "run_id": task_request.run_id}

    monkeypatch.setattr("app.runtime.sandbox.runtime.get_settings", lambda: StubSettings())

    runtime = SandboxRuntime(
        workspace_root=tmp_path,
        provider=FakeContainerProvider(executor_url="http://executor.test"),
        execute_task=execute,
    )

    await runtime.submit(request(callback_token_id="cbt_run-a"))

    assert sent[0].callback_token_id == "cbt_run-a"
    assert sent[0].callback_token == derived_callback_token("settings-token", "cbt_run-a")
    assert sent[0].callback_token != "settings-token"


@pytest.mark.asyncio
async def test_runtime_stops_ephemeral_container_after_dispatch_failure(tmp_path):
    provider = FakeContainerProvider(executor_url="http://executor.test")

    async def fail_execute(executor_url, task_request):
        raise RuntimeError("executor unavailable")

    runtime = SandboxRuntime(
        workspace_root=tmp_path,
        provider=provider,
        execute_task=fail_execute,
        callback_token_resolver=lambda token_id: "secret-token",
    )

    with pytest.raises(RuntimeError, match="executor unavailable"):
        await runtime.submit(request(sandbox_mode="ephemeral"))

    assert await provider.list_runtime_containers({}) == []


@pytest.mark.asyncio
async def test_runtime_keeps_persistent_container_after_dispatch_failure(tmp_path):
    provider = FakeContainerProvider(executor_url="http://executor.test")

    async def fail_execute(executor_url, task_request):
        raise RuntimeError("executor unavailable")

    runtime = SandboxRuntime(
        workspace_root=tmp_path,
        provider=provider,
        execute_task=fail_execute,
        callback_token_resolver=lambda token_id: "secret-token",
    )

    with pytest.raises(RuntimeError, match="executor unavailable"):
        await runtime.submit(request(sandbox_mode="persistent"))

    statuses = await provider.list_runtime_containers({})

    assert len(statuses) == 1
    assert statuses[0].run_id == "run-a"
    assert statuses[0].status == "running"
