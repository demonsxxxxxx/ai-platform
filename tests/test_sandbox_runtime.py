import hashlib
import hmac
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from app.runtime.sandbox.container_provider import FakeContainerProvider
from app.runtime.sandbox.contracts import ContainerLease, SandboxRuntimeRequest, StopResult
from app.runtime.sandbox.runtime import SandboxRuntime


def derived_callback_token(secret: str, token_id: str = "cbt_run-a") -> str:
    return hmac.new(secret.encode("utf-8"), token_id.encode("utf-8"), hashlib.sha256).hexdigest()


def noop_lease(*args):
    return None


@asynccontextmanager
async def fake_transaction():
    yield object()


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
    lease_calls = []

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
        record_lease=lambda lease, request, workspace: lease_calls.append(("record", lease, request, workspace)),
        release_lease=lambda lease, reason: lease_calls.append(("release", lease, reason)),
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
    assert lease_calls[0][0] == "record"
    assert lease_calls[0][1].container_id == "exec-run-a"
    assert lease_calls[0][2].run_id == "run-a"
    assert lease_calls[1][0] == "release"
    assert lease_calls[1][2] == "dispatch_completed"


@pytest.mark.asyncio
async def test_runtime_result_splits_sandbox_cold_start_from_executor_latency(tmp_path, monkeypatch):
    class StubSettings:
        sandbox_callback_base_url = "http://platform.test"
        sandbox_callback_token = "settings-token"

    class Clock:
        def __init__(self):
            self.values = iter([1.0, 1.01, 1.07, 1.08, 1.105, 1.110, 1.125, 1.130])

        def monotonic(self):
            return next(self.values)

    class TimedProvider:
        async def create_or_reuse(self, request, workspace):
            return ContainerLease(
                container_id="exec-run-a",
                container_name="executor-exec-run-a",
                provider="fake",
                executor_url="http://executor.test",
                tenant_id=request.tenant_id,
                workspace_id=request.workspace_id,
                user_id=request.user_id,
                session_id=request.session_id,
                run_id=request.run_id,
                sandbox_mode=request.sandbox_mode,
                browser_enabled=request.browser_enabled,
                workspace_host_path=workspace.workspace_host_path,
                timings={
                    "sandbox_container_cold_start_latency_ms": 40,
                    "sandbox_healthcheck_latency_ms": 12,
                },
            )

        async def stop(self, lease, *, reason: str):
            return StopResult(container_id=lease.container_id, status="stopped", message=reason)

    async def execute(executor_url, task_request):
        return {
            "status": "accepted",
            "session_id": task_request.session_id,
            "run_id": task_request.run_id,
            "executor_model_latency_ms": 21,
            "document_processing_latency_ms": 8,
        }

    monkeypatch.setattr("app.runtime.sandbox.runtime.get_settings", lambda: StubSettings())
    monkeypatch.setattr("app.runtime.sandbox.runtime.time", Clock(), raising=False)

    runtime = SandboxRuntime(
        workspace_root=tmp_path,
        provider=TimedProvider(),
        execute_task=execute,
        callback_token_resolver=lambda token_id: "secret-token",
        record_lease=noop_lease,
        release_lease=noop_lease,
    )

    result = await runtime.submit(request(sandbox_mode="ephemeral"))

    assert result.timings == {
        "schema_version": "ai-platform.sandbox-latency-split.v1",
        "sandbox_lease_acquire_latency_ms": 60,
        "sandbox_container_cold_start_latency_ms": 40,
        "sandbox_healthcheck_latency_ms": 12,
        "sandbox_executor_dispatch_latency_ms": 25,
        "executor_model_latency_ms": 21,
        "document_processing_latency_ms": 8,
        "sandbox_cleanup_latency_ms": 15,
        "sandbox_total_latency_ms": 130,
    }
    assert result.timings["sandbox_executor_dispatch_latency_ms"] < result.timings["sandbox_total_latency_ms"]
    assert result.timings["sandbox_container_cold_start_latency_ms"] != result.timings["executor_model_latency_ms"]


@pytest.mark.asyncio
async def test_runtime_releases_ephemeral_lease_as_failed_when_executor_reports_failed(tmp_path, monkeypatch):
    calls = []

    class StubSettings:
        sandbox_callback_base_url = "http://platform.test"
        sandbox_callback_token = "settings-token"

    async def execute(executor_url, task_request):
        return {
            "status": "failed",
            "run_id": task_request.run_id,
            "error_code": "executor_health_timeout",
            "error_message": "Executor health timeout",
        }

    async def record_lease(lease, request, workspace):
        calls.append(("record", lease.run_id))
        return {"id": "lease-created-a"}

    async def release_lease(lease, reason, lease_record_id=None):
        calls.append(("release", reason, lease_record_id))

    monkeypatch.setattr("app.runtime.sandbox.runtime.get_settings", lambda: StubSettings())

    runtime = SandboxRuntime(
        workspace_root=tmp_path,
        provider=FakeContainerProvider(executor_url="http://executor.test"),
        execute_task=execute,
        callback_token_resolver=lambda token_id: "secret-token",
        record_lease=record_lease,
        release_lease=release_lease,
    )

    result = await runtime.submit(request(sandbox_mode="ephemeral"))

    assert result.status == "failed"
    assert calls == [("record", "run-a"), ("release", "run_failed", "lease-created-a")]


@pytest.mark.asyncio
async def test_runtime_default_db_release_targets_created_lease_id(tmp_path, monkeypatch):
    calls = []

    class StubSettings:
        sandbox_callback_base_url = "http://platform.test"
        sandbox_callback_token = "settings-token"

    async def execute(executor_url, task_request):
        return {"status": "accepted", "session_id": task_request.session_id, "run_id": task_request.run_id}

    async def create_sandbox_lease(conn, **kwargs):
        calls.append(("create", kwargs["run_id"]))
        return {"id": "lease-created-a"}

    async def release_sandbox_lease(conn, **kwargs):
        calls.append(("release_one", kwargs["lease_id"], kwargs["reason"]))
        return {"id": kwargs["lease_id"], "status": "released"}

    async def release_active_sandbox_leases_for_run(*args, **kwargs):
        raise AssertionError("runtime must not release every active lease for the run")

    monkeypatch.setattr("app.runtime.sandbox.runtime.get_settings", lambda: StubSettings())
    monkeypatch.setattr("app.runtime.sandbox.runtime.transaction", fake_transaction)
    monkeypatch.setattr("app.runtime.sandbox.runtime.repositories.create_sandbox_lease", create_sandbox_lease)
    monkeypatch.setattr("app.runtime.sandbox.runtime.repositories.release_sandbox_lease", release_sandbox_lease)
    monkeypatch.setattr(
        "app.runtime.sandbox.runtime.repositories.release_active_sandbox_leases_for_run",
        release_active_sandbox_leases_for_run,
    )

    runtime = SandboxRuntime(
        workspace_root=tmp_path,
        provider=FakeContainerProvider(executor_url="http://executor.test"),
        execute_task=execute,
        callback_token_resolver=lambda token_id: "secret-token",
    )

    await runtime.submit(request(sandbox_mode="ephemeral"))

    assert calls == [("create", "run-a"), ("release_one", "lease-created-a", "dispatch_completed")]


@pytest.mark.asyncio
async def test_runtime_does_not_release_db_lease_when_completion_stop_fails(tmp_path):
    calls = []

    class StopFailedProvider(FakeContainerProvider):
        async def stop(self, lease, *, reason: str):
            calls.append(("stop", reason))
            return StopResult(container_id=lease.container_id, status="failed", message="stop failed")

    async def execute(executor_url, task_request):
        return {"status": "accepted", "session_id": task_request.session_id, "run_id": task_request.run_id}

    async def record_lease(lease, request, workspace):
        calls.append(("record", lease.run_id))
        return {"id": "lease-created-a"}

    async def release_lease(lease, reason, lease_record_id=None):
        calls.append(("release", reason, lease_record_id))

    runtime = SandboxRuntime(
        workspace_root=tmp_path,
        provider=StopFailedProvider(executor_url="http://executor.test"),
        execute_task=execute,
        callback_token_resolver=lambda token_id: "secret-token",
        record_lease=record_lease,
        release_lease=release_lease,
    )

    with pytest.raises(RuntimeError, match="sandbox_runtime_cleanup_failed"):
        await runtime.submit(request(sandbox_mode="ephemeral"))

    assert calls == [("record", "run-a"), ("stop", "dispatch_completed")]


@pytest.mark.asyncio
async def test_runtime_does_not_release_db_lease_when_dispatch_failure_stop_fails(tmp_path):
    calls = []

    class StopFailedProvider(FakeContainerProvider):
        async def stop(self, lease, *, reason: str):
            calls.append(("stop", reason))
            return StopResult(container_id=lease.container_id, status="failed", message="stop failed")

    async def fail_execute(executor_url, task_request):
        raise RuntimeError("executor unavailable")

    async def record_lease(lease, request, workspace):
        calls.append(("record", lease.run_id))
        return {"id": "lease-created-a"}

    async def release_lease(lease, reason, lease_record_id=None):
        calls.append(("release", reason, lease_record_id))

    runtime = SandboxRuntime(
        workspace_root=tmp_path,
        provider=StopFailedProvider(executor_url="http://executor.test"),
        execute_task=fail_execute,
        callback_token_resolver=lambda token_id: "secret-token",
        record_lease=record_lease,
        release_lease=release_lease,
    )

    with pytest.raises(RuntimeError, match="sandbox_runtime_cleanup_failed"):
        await runtime.submit(request(sandbox_mode="ephemeral"))

    assert calls == [("record", "run-a"), ("stop", "dispatch_failed")]


@pytest.mark.asyncio
async def test_runtime_stops_live_container_when_lease_recording_fails(tmp_path):
    provider = FakeContainerProvider(executor_url="http://executor.test")

    async def execute(executor_url, task_request):
        raise AssertionError("executor must not run when lease recording fails")

    async def fail_record_lease(lease, request, workspace):
        raise RuntimeError("db unavailable")

    runtime = SandboxRuntime(
        workspace_root=tmp_path,
        provider=provider,
        execute_task=execute,
        callback_token_resolver=lambda token_id: "secret-token",
        record_lease=fail_record_lease,
    )

    with pytest.raises(RuntimeError, match="db unavailable"):
        await runtime.submit(request(sandbox_mode="persistent"))

    assert await provider.list_runtime_containers({}) == []


@pytest.mark.asyncio
async def test_runtime_surfaces_cleanup_failure_when_lease_recording_stop_fails(tmp_path):
    calls = []

    class StopFailedProvider(FakeContainerProvider):
        async def stop(self, lease, *, reason: str):
            calls.append(("stop", reason))
            return StopResult(container_id=lease.container_id, status="failed", message="stop failed")

    async def execute(executor_url, task_request):
        raise AssertionError("executor must not run when lease recording fails")

    async def fail_record_lease(lease, request, workspace):
        raise RuntimeError("db unavailable")

    runtime = SandboxRuntime(
        workspace_root=tmp_path,
        provider=StopFailedProvider(executor_url="http://executor.test"),
        execute_task=execute,
        callback_token_resolver=lambda token_id: "secret-token",
        record_lease=fail_record_lease,
    )

    with pytest.raises(RuntimeError, match="sandbox_runtime_cleanup_failed"):
        await runtime.submit(request(sandbox_mode="persistent"))

    assert calls == [("stop", "lease_record_failed")]


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
        record_lease=noop_lease,
        release_lease=noop_lease,
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
        record_lease=noop_lease,
        release_lease=noop_lease,
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
        record_lease=noop_lease,
        release_lease=noop_lease,
    )

    with pytest.raises(RuntimeError, match="executor unavailable"):
        await runtime.submit(request(sandbox_mode="persistent"))

    statuses = await provider.list_runtime_containers({})

    assert len(statuses) == 1
    assert statuses[0].run_id == "run-a"
    assert statuses[0].status == "running"
