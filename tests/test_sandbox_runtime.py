import hashlib
import hmac
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from app.file_parser_contracts import build_attachment_preprocessing_contract
from app.runtime.sandbox.container_provider import FakeContainerProvider
from app.runtime.sandbox.contracts import ContainerLease, ExecutorTaskRequest, SandboxRuntimeRequest, StopResult
from app.runtime.sandbox.executor_client import SandboxExecutorClient
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
        callback_token_resolver=lambda token_id: "secret-token",
        record_lease=lambda lease, request, workspace: lease_calls.append(("record", lease, request, workspace)),
        release_lease=lambda lease, reason: lease_calls.append(("release", lease, reason)),
    )

    result = await runtime.submit(
        request(materialized_file_names=["z.docx", "a.docx"]),
        event_sink=events.append,
    )

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
    assert Path(run_root / "workspace" / "inputs").is_dir()
    assert Path(run_root / "logs").is_dir()
    assert sent[0][0] == "http://executor.test"
    assert sent[0][1].session_id == "session-a"
    assert sent[0][1].run_id == "run-a"
    assert sent[0][1].prompt == "hello"
    assert sent[0][1].callback_url == "http://platform.test/api/ai/runtime/callbacks/executor"
    assert sent[0][1].callback_token_id == "cbt_run-a_exec-run-a"
    assert sent[0][1].callback_token == "secret-token"
    assert sent[0][1].callback_base_url == "http://platform.test"
    assert sent[0][1].permission_mode == "default"
    assert sent[0][1].config == {
        "model": "deepseek-v4-flash",
        "browser_enabled": True,
        "resource_limits": {"max_seconds": 120, "max_tool_calls": 20},
        "skill_ids": ["general-chat"],
        "mcp_tool_ids": ["knowledge.search"],
        "tool_policy_subjects": [],
        "input_files": ["file-a"],
        "materialized_file_names": ["z.docx", "a.docx"],
    }
    assert [event.type for event in events] == ["runtime_container_started"]
    assert lease_calls[0][0] == "record"
    assert lease_calls[0][1].container_id == "exec-run-a"
    assert lease_calls[0][2].run_id == "run-a"
    assert lease_calls[1][0] == "release"
    assert lease_calls[1][2] == "dispatch_completed"


@pytest.mark.asyncio
async def test_runtime_submit_threads_context_manifest_and_scope_to_executor(tmp_path, monkeypatch):
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
        callback_token_resolver=lambda token_id: "secret-token",
        record_lease=noop_lease,
        release_lease=noop_lease,
    )

    await runtime.submit(
        request(
            context_manifest={
                "schema_version": "ai-platform.context-manifest.v1",
                "available_retrieval_tools": ["read_context_file"],
                "attachment_preprocessing": build_attachment_preprocessing_contract(
                    file_ids=["file-a"],
                    file_names=["book.xlsx"],
                ),
            },
            context_retrieval_scope={
                "tenant_id": "tenant-a",
                "workspace_id": "workspace-a",
                "user_id": "user-a",
                "session_id": "session-a",
                "run_id": "run-a",
                "agent_id": "general-agent",
            },
        )
    )

    assert sent[0].config["context_manifest"]["available_retrieval_tools"] == ["read_context_file"]
    requirement = sent[0].config["context_manifest"]["attachment_preprocessing"]["requirements"][0]
    assert requirement["file_id"] == "file-a"
    assert requirement["parser_id"] == "ai-platform.xlsx.openpyxl"
    assert sent[0].config["context_retrieval_scope"]["user_id"] == "user-a"
    assert sent[0].callback_url == "http://platform.test/api/ai/runtime/callbacks/executor"
    assert sent[0].callback_token_id == "cbt_run-a_exec-run-a"


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

    result = await runtime.submit(request(sandbox_mode="ephemeral", queue_wait_ms=33))

    assert result.timings == {
        "schema_version": "ai-platform.sandbox-latency-split.v1",
        "sandbox_queue_wait_latency_ms": 33,
        "sandbox_lease_acquire_latency_ms": 60,
        "sandbox_container_start_latency_ms": 40,
        "sandbox_container_cold_start_latency_ms": 40,
        "sandbox_healthcheck_latency_ms": 12,
        "sandbox_executor_dispatch_latency_ms": 25,
        "executor_first_token_latency_ms": 0,
        "executor_tool_call_latency_ms": 0,
        "executor_model_latency_ms": 21,
        "document_processing_latency_ms": 8,
        "artifact_upload_latency_ms": 0,
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
        calls.append(
            (
                "create",
                kwargs["run_id"],
                kwargs["trace_id"],
                kwargs["lease_payload_json"],
                {
                    "runtime_container_id": kwargs.get("runtime_container_id"),
                    "runtime_container_name": kwargs.get("runtime_container_name"),
                    "runtime_executor_url": kwargs.get("runtime_executor_url"),
                    "runtime_workspace_container_path": kwargs.get("runtime_workspace_container_path"),
                },
            )
        )
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

    await runtime.submit(request(sandbox_mode="ephemeral", trace_id="trace-run-a"))

    assert calls == [
        (
                "create",
                "run-a",
                "trace-run-a",
                    {
                        "source": "sandbox_runtime",
                        "evidence_class": "runtime_lease_projection",
                        "container_id": "exec-run-a",
                        "container_name": "executor-exec-run-a",
                        "executor_url": "http://executor.test",
                        "workspace_host_path": str(
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
                        / "workspace"
                    ),
                    "workspace_container_path": "/workspace",
                    "labels": {"ai-platform.run_id": "run-a"},
                },
                {
                    "runtime_container_id": "exec-run-a",
                    "runtime_container_name": "executor-exec-run-a",
                    "runtime_executor_url": "http://executor.test",
                    "runtime_workspace_container_path": "/workspace",
                },
        ),
        ("release_one", "lease-created-a", "dispatch_completed"),
    ]


@pytest.mark.asyncio
async def test_runtime_default_db_record_persists_trusted_opensandbox_runtime_handle(tmp_path, monkeypatch):
    calls = []

    class StubSettings:
        sandbox_callback_base_url = "http://platform.test"
        sandbox_callback_token = "settings-token"

    class OpenSandboxProvider(FakeContainerProvider):
        async def create_or_reuse(self, request, workspace):
            lease = await super().create_or_reuse(request, workspace)
            return ContainerLease(
                **{
                    **lease.model_dump(),
                    "container_id": "osb-run-a",
                    "container_name": "opensandbox-run-a",
                    "provider": "opensandbox",
                    "executor_url": "http://opensandbox-executor.test",
                    "workspace_container_path": "/sandbox-workspace",
                    "labels": {
                        **lease.labels,
                        "ai-platform.executor.user": "10001:10001",
                        "ai-platform.executor.uid": "10001",
                        "ai-platform.executor.gid": "10001",
                        "ai-platform.executor.identity_evidence": "authenticated-runtime-endpoint",
                    },
                }
            )

    async def execute(executor_url, task_request):
        return {"status": "accepted", "session_id": task_request.session_id, "run_id": task_request.run_id}

    async def create_sandbox_lease(conn, **kwargs):
        calls.append(("create", kwargs))
        return {"id": "lease-created-a"}

    async def release_sandbox_lease(conn, **kwargs):
        calls.append(("release", kwargs["lease_id"], kwargs["reason"]))
        return {"id": kwargs["lease_id"], "status": "released"}

    monkeypatch.setattr("app.runtime.sandbox.runtime.get_settings", lambda: StubSettings())
    monkeypatch.setattr("app.runtime.sandbox.runtime.transaction", fake_transaction)
    monkeypatch.setattr("app.runtime.sandbox.runtime.repositories.create_sandbox_lease", create_sandbox_lease)
    monkeypatch.setattr("app.runtime.sandbox.runtime.repositories.release_sandbox_lease", release_sandbox_lease)

    runtime = SandboxRuntime(
        workspace_root=tmp_path,
        provider=OpenSandboxProvider(executor_url="http://unused.test"),
        execute_task=execute,
        callback_token_resolver=lambda token_id: "secret-token",
    )

    await runtime.submit(request(sandbox_mode="ephemeral", trace_id="trace-run-a"))

    create_kwargs = calls[0][1]
    assert create_kwargs["provider"] == "opensandbox"
    assert create_kwargs["runtime_container_id"] == "osb-run-a"
    assert create_kwargs["runtime_container_name"] == "opensandbox-run-a"
    assert create_kwargs["runtime_executor_url"] == "http://opensandbox-executor.test"
    assert create_kwargs["runtime_workspace_container_path"] == "/sandbox-workspace"
    assert create_kwargs["lease_payload_json"]["container_id"] == "osb-run-a"
    assert "executor_headers" not in create_kwargs["lease_payload_json"]
    assert create_kwargs["lease_payload_json"]["labels"] == {"ai-platform.run_id": "run-a"}
    assert calls[1] == ("release", "lease-created-a", "dispatch_completed")


@pytest.mark.asyncio
async def test_runtime_default_db_record_rejects_incomplete_trusted_runtime_handle(tmp_path, monkeypatch):
    calls = []

    class StubSettings:
        sandbox_callback_base_url = "http://platform.test"
        sandbox_callback_token = "settings-token"

    class IncompleteProvider(FakeContainerProvider):
        async def create_or_reuse(self, request, workspace):
            lease = await super().create_or_reuse(request, workspace)
            return ContainerLease(
                **{
                    **lease.model_dump(),
                    "container_id": "osb-run-a",
                    "container_name": "",
                    "provider": "opensandbox",
                    "executor_url": "http://opensandbox-executor.test",
                }
            )

    async def execute(executor_url, task_request):
        raise AssertionError("incomplete runtime handle must fail before executor dispatch")

    async def create_sandbox_lease(conn, **kwargs):
        raise AssertionError("incomplete runtime handle must not be persisted")

    async def release_sandbox_lease(conn, **kwargs):
        calls.append(("release", kwargs["lease_id"], kwargs["reason"]))
        return {"id": kwargs["lease_id"], "status": "released"}

    monkeypatch.setattr("app.runtime.sandbox.runtime.get_settings", lambda: StubSettings())
    monkeypatch.setattr("app.runtime.sandbox.runtime.transaction", fake_transaction)
    monkeypatch.setattr("app.runtime.sandbox.runtime.repositories.create_sandbox_lease", create_sandbox_lease)
    monkeypatch.setattr("app.runtime.sandbox.runtime.repositories.release_sandbox_lease", release_sandbox_lease)

    runtime = SandboxRuntime(
        workspace_root=tmp_path,
        provider=IncompleteProvider(executor_url="http://unused.test"),
        execute_task=execute,
        callback_token_resolver=lambda token_id: "secret-token",
    )

    with pytest.raises(ValueError, match="incomplete_runtime_handle"):
        await runtime.submit(request(sandbox_mode="ephemeral"))

    assert calls == []


@pytest.mark.asyncio
async def test_runtime_records_opensandbox_provider_as_platform_db_lease(tmp_path):
    calls = []

    class OpenSandboxProvider(FakeContainerProvider):
        async def create_or_reuse(self, request, workspace):
            lease = await super().create_or_reuse(request, workspace)
            return ContainerLease(
                **{
                    **lease.model_dump(),
                    "container_id": "osb-run-a",
                    "container_name": "opensandbox-run-a",
                    "provider": "opensandbox",
                    "executor_url": "http://opensandbox-executor.test",
                }
            )

    async def execute(executor_url, task_request):
        calls.append(("execute", executor_url, task_request.run_id))
        return {"status": "accepted", "session_id": task_request.session_id, "run_id": task_request.run_id}

    async def record_lease(lease, request, workspace):
        calls.append(
            (
                "record",
                lease.provider,
                lease.container_id,
                request.run_id,
                workspace.user_visible_payload(),
            )
        )
        return {"id": "lease-opensandbox-a"}

    async def release_lease(lease, reason, lease_record_id=None):
        calls.append(("release", lease.provider, reason, lease_record_id))

    runtime = SandboxRuntime(
        workspace_root=tmp_path,
        provider=OpenSandboxProvider(executor_url="http://unused.test"),
        execute_task=execute,
        callback_token_resolver=lambda token_id: "secret-token",
        record_lease=record_lease,
        release_lease=release_lease,
    )

    await runtime.submit(request(sandbox_mode="ephemeral"))

    assert calls == [
        (
            "record",
            "opensandbox",
            "osb-run-a",
            "run-a",
            {"workspace": "/workspace", "inputs": "/workspace/inputs"},
        ),
        ("execute", "http://opensandbox-executor.test", "run-a"),
        ("release", "opensandbox", "dispatch_completed", "lease-opensandbox-a"),
    ]


@pytest.mark.asyncio
async def test_runtime_passes_private_executor_headers_to_dispatch_without_db_leak(tmp_path, monkeypatch):
    calls = []

    class StubSettings:
        sandbox_callback_base_url = "http://platform.test"
        sandbox_callback_token = "settings-token"

    class HeaderProvider(FakeContainerProvider):
        async def create_or_reuse(self, request, workspace):
            lease = await super().create_or_reuse(request, workspace)
            return ContainerLease(
                **{
                    **lease.model_dump(),
                    "container_id": "osb-run-a",
                    "container_name": "opensandbox-run-a",
                    "provider": "opensandbox",
                    "executor_url": "http://opensandbox-executor.test",
                    "executor_headers": {"OPENSANDBOX-EGRESS-AUTH": "opensandbox-secret"},
                }
            )

    async def execute(executor_url, task_request, *, executor_headers=None):
        calls.append(("execute", executor_url, dict(executor_headers or {})))
        return {"status": "accepted", "session_id": task_request.session_id, "run_id": task_request.run_id}

    async def create_sandbox_lease(conn, **kwargs):
        calls.append(("create", kwargs["lease_payload_json"]))
        return {"id": "lease-created-a"}

    async def release_sandbox_lease(conn, **kwargs):
        calls.append(("release", kwargs["lease_id"], kwargs["reason"]))
        return {"id": kwargs["lease_id"], "status": "released"}

    monkeypatch.setattr("app.runtime.sandbox.runtime.get_settings", lambda: StubSettings())
    monkeypatch.setattr("app.runtime.sandbox.runtime.transaction", fake_transaction)
    monkeypatch.setattr("app.runtime.sandbox.runtime.repositories.create_sandbox_lease", create_sandbox_lease)
    monkeypatch.setattr("app.runtime.sandbox.runtime.repositories.release_sandbox_lease", release_sandbox_lease)

    runtime = SandboxRuntime(
        workspace_root=tmp_path,
        provider=HeaderProvider(executor_url="http://unused.test"),
        execute_task=execute,
        callback_token_resolver=lambda token_id: "secret-token",
    )

    await runtime.submit(request(sandbox_mode="ephemeral"))

    create_payload = calls[0][1]
    assert calls[1] == (
        "execute",
        "http://opensandbox-executor.test",
        {"OPENSANDBOX-EGRESS-AUTH": "opensandbox-secret"},
    )
    assert calls[2] == ("release", "lease-created-a", "dispatch_completed")
    assert "executor_headers" not in create_payload
    assert "opensandbox-secret" not in str(create_payload)


@pytest.mark.asyncio
async def test_executor_client_posts_private_executor_headers():
    calls = []

    async def post_json(url, payload, timeout, headers=None):
        calls.append((url, payload, timeout, dict(headers or {})))
        return {"status": "accepted"}

    client = SandboxExecutorClient(post_json=post_json, timeout_seconds=3.0)

    response = await client.execute(
        "http://executor.test/",
        ExecutorTaskRequest(
            session_id="session-a",
            run_id="run-a",
            prompt="hello",
            callback_url="http://callback.test",
            callback_token_id="cbt_run-a",
            callback_token="callback-secret",
            callback_base_url="http://platform.test",
        ),
        executor_headers={"OPENSANDBOX-EGRESS-AUTH": "opensandbox-secret"},
    )

    assert response == {"status": "accepted"}
    assert calls == [
        (
            "http://executor.test/v1/tasks/execute",
            {
                "session_id": "session-a",
                "run_id": "run-a",
                "prompt": "hello",
                "callback_url": "http://callback.test",
                "callback_token_id": "cbt_run-a",
                "callback_token": "callback-secret",
                "callback_base_url": "http://platform.test",
                "sdk_session_id": None,
                "permission_mode": "default",
                "governed_permission_wait": False,
                "config": {},
            },
            3.0,
            {"OPENSANDBOX-EGRESS-AUTH": "opensandbox-secret"},
        )
    ]


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

    assert sent[0].callback_token_id == "cbt_run-a_exec-run-a"
    assert sent[0].callback_token == derived_callback_token("settings-token", "cbt_run-a_exec-run-a")
    assert sent[0].callback_token != "settings-token"


@pytest.mark.asyncio
async def test_runtime_ignores_untrusted_callback_input_and_uses_trusted_platform_target(tmp_path, monkeypatch):
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
        callback_token_resolver=lambda token_id: f"derived-for-{token_id}",
        record_lease=noop_lease,
        release_lease=noop_lease,
    )

    await runtime.submit(
        request(
            callback_url="http://169.254.169.254/latest/meta-data",
            callback_token_id="cbt_run-a",
        )
    )

    assert sent[0].callback_url == "http://platform.test/api/ai/runtime/callbacks/executor"
    assert sent[0].callback_base_url == "http://platform.test"
    assert sent[0].callback_token_id == "cbt_run-a_exec-run-a"
    assert sent[0].callback_token == "derived-for-cbt_run-a_exec-run-a"


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
