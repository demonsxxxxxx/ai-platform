import asyncio
import hashlib
import hmac
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.file_parser_contracts import build_attachment_preprocessing_contract
from app.runtime.sandbox import container_provider
from app.runtime.sandbox.container_provider import FakeContainerProvider
from app.runtime.sandbox.contracts import ContainerLease, ExecutorTaskRequest, SandboxRuntimeRequest, StopResult
from app.runtime.sandbox.executor_client import SandboxExecutorClient, SandboxExecutorHttpError
from app.runtime.sandbox.readiness_evidence import ExecutorReadinessEvidence
from app.executors.base import RunExecutionOwner
from app.runtime.sandbox.runtime import SandboxRuntime
from app.validation import MAX_SERVER_OWNED_SYSTEM_PROMPT_CHARS


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
        "attempt_id": "qat_test-runtime-attempt",
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


def test_sandbox_system_prompt_uses_the_same_character_limit_as_profile_admission():
    accepted = request(system_prompt="界" * MAX_SERVER_OWNED_SYSTEM_PROMPT_CHARS)

    assert accepted.system_prompt == "界" * MAX_SERVER_OWNED_SYSTEM_PROMPT_CHARS
    with pytest.raises(ValueError):
        request(system_prompt="界" * (MAX_SERVER_OWNED_SYSTEM_PROMPT_CHARS + 1))


@pytest.mark.asyncio
async def test_runtime_submit_prepares_workspace_emits_event_and_dispatches_executor(tmp_path, monkeypatch):
    sent = []
    lease_calls = []

    class StubSettings:
        sandbox_callback_base_url = "http://platform.test"
        sandbox_callback_token = "settings-token"
        sandbox_egress_proof_signing_key = "runtime-test-proof-key-with-enough-entropy-2026"

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
        request(
            materialized_file_names=["z.docx", "a.docx"],
            system_prompt="Private profile instruction",
        ),
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
        / "attempts"
        / "qat_test-runtime-attempt"
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
    assert sent[0][1].callback_token_id == "cbt:run-a:qat_test-runtime-attempt"
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
        "system_prompt": "Private profile instruction",
    }
    assert [event.type for event in events] == ["runtime_container_started"]
    assert lease_calls[0][0] == "record"
    assert lease_calls[0][1].container_id == "exec-run-a"
    assert lease_calls[0][2].run_id == "run-a"
    assert lease_calls[1][0] == "release"
    assert lease_calls[1][2] == "dispatch_completed"


@pytest.mark.asyncio
async def test_runtime_orders_workspace_transfer_between_record_and_dispatch_and_before_stop(tmp_path, monkeypatch):
    steps: list[str] = []

    class StubSettings:
        sandbox_callback_base_url = "http://platform.test"
        sandbox_callback_token = "settings-token"

    class RecordingProvider(FakeContainerProvider):
        async def create_or_reuse(self, runtime_request, workspace):
            steps.append("create")
            return await super().create_or_reuse(runtime_request, workspace)

        async def stage_workspace(self, lease, runtime_request, workspace):
            steps.append("stage")

        async def validate_for_dispatch(self, lease, runtime_request, workspace):
            steps.append("validate")

        async def collect_workspace(self, lease, runtime_request, workspace):
            steps.append("collect")

        async def stop(self, lease, *, reason):
            steps.append("stop")
            return await super().stop(lease, reason=reason)

    async def execute(*_args, **_kwargs):
        steps.append("dispatch")
        return {"status": "completed", "session_id": "session-a", "run_id": "run-a"}

    monkeypatch.setattr("app.runtime.sandbox.runtime.get_settings", lambda: StubSettings())
    runtime = SandboxRuntime(
        workspace_root=tmp_path.parent / "r",
        provider=RecordingProvider(executor_url="http://executor.test"),
        execute_task=execute,
        callback_token_resolver=lambda _token_id: "secret-token",
        record_lease=lambda *_args: steps.append("record") or "lease-a",
        release_lease=lambda *_args: steps.append("release"),
    )

    await runtime.submit(
        request(
            tenant_id="t",
            workspace_id="w",
            user_id="u",
            session_id="s",
            run_id="r",
            attempt_id="a",
        )
    )

    assert steps == ["create", "record", "stage", "validate", "dispatch", "collect", "stop", "release"]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_phase", ["stage", "collect"])
async def test_runtime_workspace_transfer_failure_is_terminal_and_cleans_up(tmp_path, monkeypatch, failure_phase):
    calls: list[str] = []

    class StubSettings:
        sandbox_callback_base_url = "http://platform.test"
        sandbox_callback_token = "settings-token"

    class FailingTransferProvider(FakeContainerProvider):
        async def stage_workspace(self, lease, runtime_request, workspace):
            calls.append("stage")
            if failure_phase == "stage":
                raise RuntimeError("stage failed")

        async def collect_workspace(self, lease, runtime_request, workspace):
            calls.append("collect")
            if failure_phase == "collect":
                raise RuntimeError("collect failed")

        async def stop(self, lease, *, reason):
            calls.append(reason)
            return await super().stop(lease, reason=reason)

    async def execute(*_args, **_kwargs):
        calls.append("dispatch")
        return {"status": "completed", "session_id": "s", "run_id": "r"}

    monkeypatch.setattr("app.runtime.sandbox.runtime.get_settings", lambda: StubSettings())
    runtime = SandboxRuntime(
        workspace_root=tmp_path.parent / f"x-{failure_phase}",
        provider=FailingTransferProvider(executor_url="http://executor.test"),
        execute_task=execute,
        callback_token_resolver=lambda _token_id: "secret-token",
        record_lease=lambda *_args: calls.append("record") or "lease-a",
        release_lease=lambda *_args: calls.append("release"),
    )

    with pytest.raises(RuntimeError, match=failure_phase):
        await runtime.submit(
            request(
                tenant_id="t",
                workspace_id="w",
                user_id="u",
                session_id="s",
                run_id="r",
                attempt_id=f"a-{failure_phase}",
                sandbox_mode="persistent",
            )
        )

    if failure_phase == "stage":
        assert calls == ["record", "stage", "workspace_stage_failed", "release"]
    else:
        assert calls == ["record", "stage", "dispatch", "collect", "workspace_collect_failed", "release"]


@pytest.mark.asyncio
async def test_runtime_opensandbox_workspace_stage_failure_stops_and_releases_once_without_residue(monkeypatch):
    calls: list[str] = []
    owned: set[str] = set()

    class StubSettings:
        sandbox_container_provider = "opensandbox"
        sandbox_security_profile = "governed"
        sandbox_callback_base_url = "http://platform.test"
        sandbox_callback_token = "settings-token"
        opensandbox_domain = "10.56.1.72:8080"
        opensandbox_protocol = "http"
        opensandbox_api_key = "test-stock-opensandbox-key"
        opensandbox_use_server_proxy = False
        opensandbox_executor_image = "sha256:" + "a" * 64
        opensandbox_executor_image_digest = "sha256:" + "a" * 64
        opensandbox_external_egress_callback_base_url = "https://bridge.internal.example:18443"
        opensandbox_external_egress_openai_base_url = "https://bridge.internal.example:18443/openai/v1"
        opensandbox_external_egress_anthropic_base_url = "https://bridge.internal.example:18443/anthropic"

    class TrustedOpenSandboxProvider(FakeContainerProvider):
        async def create_or_reuse(self, runtime_request, workspace):
            lease = await super().create_or_reuse(runtime_request, workspace)
            lease.provider = "opensandbox"
            lease.labels.update(
                {
                    "ai-platform.provider_backend": "opensandbox",
                    "ai-platform.security_profile": "governed",
                }
            )
            owned.add(lease.container_id)
            return lease

        async def stage_workspace(self, *_args):
            calls.append("stage")
            raise RuntimeError("workspace stage denied")

        async def stop(self, lease, *, reason):
            calls.append(reason)
            owned.discard(lease.container_id)
            return await super().stop(lease, reason=reason)

    monkeypatch.setattr("app.runtime.sandbox.runtime.get_settings", lambda: StubSettings())
    runtime = SandboxRuntime(
        workspace_root=Path(".pytest-tmp") / "r675-runtime-stage",
        provider=TrustedOpenSandboxProvider(executor_url="http://executor.test"),
        execute_task=lambda *_args, **_kwargs: pytest.fail("stage failure must not dispatch"),
        callback_token_resolver=lambda _token_id: "secret-token",
        record_lease=lambda *_args: calls.append("record") or "lease-trusted-a",
        release_lease=lambda *_args: calls.append("release"),
    )

    with pytest.raises(RuntimeError, match="workspace stage denied"):
        await runtime.submit(request(attempt_id="trusted-stage-a", sandbox_mode="persistent"))

    assert calls == ["record", "stage", "workspace_stage_failed", "release"]
    assert owned == set()


@pytest.mark.asyncio
async def test_runtime_and_execution_owner_elect_one_stop_terminator(tmp_path, monkeypatch):
    started = asyncio.Event()
    stop_calls: list[str] = []

    class StubSettings:
        sandbox_callback_base_url = "http://platform.test"
        sandbox_callback_token = "settings-token"

    class SlowStopProvider(FakeContainerProvider):
        async def stop(self, lease, *, reason):
            stop_calls.append(reason)
            await asyncio.sleep(0.01)
            return await super().stop(lease, reason=reason)

    async def execute(*_args, **_kwargs):
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr("app.runtime.sandbox.runtime.get_settings", lambda: StubSettings())
    runtime = SandboxRuntime(
        workspace_root=tmp_path.parent / "owner",
        provider=SlowStopProvider(executor_url="http://executor.test"),
        execute_task=execute,
        callback_token_resolver=lambda _token_id: "secret-token",
        record_lease=lambda *_args: "lease-a",
        release_lease=lambda *_args: None,
    )
    owner = RunExecutionOwner("r")
    owner.start(
        runtime.submit(
            request(
                tenant_id="t",
                workspace_id="w",
                user_id="u",
                session_id="s",
                run_id="r",
                attempt_id="a-owner",
                sandbox_mode="persistent",
            ),
            execution_owner=owner,
        )
    )
    await asyncio.wait_for(started.wait(), timeout=1.0)

    first, second = await asyncio.gather(
        owner.stop(reason="user_cancel", timeout_seconds=1.0),
        owner.stop(reason="duplicate_cancel", timeout_seconds=1.0),
    )

    assert first.quiescent is True
    assert second.quiescent is True
    assert stop_calls == ["user_cancel"]


@pytest.mark.asyncio
async def test_runtime_logs_only_safe_opensandbox_startup_evidence(tmp_path, monkeypatch, caplog):
    from app.runtime.sandbox.providers.opensandbox.startup import OpenSandboxStartupEvidence, OpenSandboxStartupStage

    class StubSettings:
        sandbox_container_provider = "fake"
        sandbox_callback_base_url = "http://platform.test"
        sandbox_callback_token = "settings-token"

    raw_provider_message = "private endpoint https://secret.test path C:\\runtime\\secret token-private"
    startup_error = container_provider.OpenSandboxStartupFailedError(
        OpenSandboxStartupEvidence(
            stage=OpenSandboxStartupStage.CREATE,
            sdk_error_code="POOL_ACQUIRE_FAILED",
            request_id="request-668",
        )
    )
    startup_error.__cause__ = RuntimeError(raw_provider_message)

    class StartupFailedProvider:
        async def create_or_reuse(self, _request, _workspace):
            raise startup_error

    monkeypatch.setattr("app.runtime.sandbox.runtime.get_settings", lambda: StubSettings())
    runtime = SandboxRuntime(
        workspace_root=tmp_path,
        provider=StartupFailedProvider(),
        execute_task=lambda *_args: pytest.fail("executor dispatch must not occur after startup failure"),
        record_lease=lambda *_args: pytest.fail("lease must not be persisted after startup failure"),
    )
    events = []

    with caplog.at_level("ERROR", logger="app.runtime.sandbox.runtime"):
        with pytest.raises(container_provider.OpenSandboxStartupFailedError) as exc_info:
            await runtime.submit(request(), event_sink=events.append)

    assert exc_info.value is startup_error
    assert events == []
    record = caplog.records[-1]
    assert record.getMessage() == "OpenSandbox startup failed"
    assert {
        "run_id": record.run_id,
        "attempt_id": record.attempt_id,
        "provider": record.provider,
        "startup_stage": record.startup_stage,
        "sdk_error_code": record.sdk_error_code,
        "request_id": record.request_id,
    } == {
        "run_id": "run-a",
        "attempt_id": "qat_test-runtime-attempt",
        "provider": "opensandbox",
        "startup_stage": "create",
        "sdk_error_code": "POOL_ACQUIRE_FAILED",
        "request_id": "request-668",
    }
    rendered = caplog.text
    for prohibited in (raw_provider_message, "secret.test", "C:\\runtime\\secret", "token-private"):
        assert prohibited not in rendered


@pytest.mark.asyncio
async def test_runtime_persists_one_private_safe_readiness_event_before_rethrow(tmp_path, monkeypatch):
    from app.public_execution import public_execution_event_from_row

    class StubSettings:
        sandbox_container_provider = "docker"
        sandbox_callback_base_url = "http://platform.test"
        sandbox_callback_token = "settings-token"

    raw_exception = "private health failure at C:\\runtime\\secret with token-private"
    evidence = ExecutorReadinessEvidence(
        readiness_phase="health_probe",
        container_state="exited",
        exit_code=137,
        oom_killed=True,
        published_port_observed=True,
        health_outcome="timeout",
        elapsed_ms=321,
    )

    class ReadinessFailedProvider:
        async def create_or_reuse(self, _request, _workspace):
            raise container_provider.ExecutorHealthTimeoutError(
                raw_exception,
                readiness_evidence=evidence,
            )

    async def execute(*_args, **_kwargs):
        raise AssertionError("executor dispatch must not occur after readiness failure")

    monkeypatch.setattr("app.runtime.sandbox.runtime.get_settings", lambda: StubSettings())
    events = []
    runtime = SandboxRuntime(
        workspace_root=tmp_path,
        provider=ReadinessFailedProvider(),
        execute_task=execute,
        record_lease=lambda *_args: (_ for _ in ()).throw(AssertionError("lease must not be persisted")),
    )

    with pytest.raises(container_provider.ExecutorHealthTimeoutError) as exc_info:
        await runtime.submit(request(), event_sink=events.append)

    assert exc_info.value.readiness_evidence is evidence
    assert len(events) == 1
    event = events[0]
    assert event.type == "sandbox_executor_readiness_failed"
    assert event.admin_only is True
    assert event.message == "Sandbox executor readiness failed"
    assert event.payload == {
        "schema_version": "ai-platform.executor-readiness-evidence.v1",
        "run_id": "run-a",
        "attempt_id": "qat_test-runtime-attempt",
        "readiness_phase": "health_probe",
        "container_state": "exited",
        "exit_code": 137,
        "oom_killed": True,
        "published_port_observed": True,
        "health_outcome": "timeout",
        "elapsed_ms": 321,
    }
    assert set(event.payload) == {
        "schema_version",
        "run_id",
        "attempt_id",
        "readiness_phase",
        "container_state",
        "exit_code",
        "oom_killed",
        "published_port_observed",
        "health_outcome",
        "elapsed_ms",
    }
    serialized = repr(event)
    for prohibited in (
        raw_exception,
        "C:\\runtime\\secret",
        "token-private",
        "hello",
        "stdout",
        "stderr",
        "command",
        "args",
        "image",
        "endpoint",
        "container_id",
        "network_id",
        "private labels",
    ):
        assert prohibited not in serialized
    assert public_execution_event_from_row(
        request().run_id,
        {
            "id": "event-private-readiness",
            "sequence": 1,
            "event_type": event.type,
            "payload_json": event.payload,
            "created_at": None,
        },
    ) is None


@pytest.mark.asyncio
async def test_runtime_logs_opensandbox_health_stage_without_expanding_readiness_event(tmp_path, monkeypatch, caplog):
    from app.runtime.sandbox.providers.opensandbox.startup import OpenSandboxStartupEvidence, OpenSandboxStartupStage

    class StubSettings:
        sandbox_container_provider = "fake"
        sandbox_callback_base_url = "http://platform.test"
        sandbox_callback_token = "settings-token"

    readiness = ExecutorReadinessEvidence(
        readiness_phase="health_probe",
        container_state="unknown",
        exit_code=None,
        oom_killed=None,
        published_port_observed=True,
        health_outcome="unhealthy",
        elapsed_ms=17,
    )
    startup_error = container_provider.ExecutorHealthTimeoutError(readiness_evidence=readiness)
    startup_error.attach_opensandbox_startup_evidence(
        OpenSandboxStartupEvidence(OpenSandboxStartupStage.HEALTH, None, "request_668-A")
    )

    class HealthFailedProvider:
        async def create_or_reuse(self, _request, _workspace):
            raise startup_error

    monkeypatch.setattr("app.runtime.sandbox.runtime.get_settings", lambda: StubSettings())
    events = []
    runtime = SandboxRuntime(
        workspace_root=tmp_path,
        provider=HealthFailedProvider(),
        execute_task=lambda *_args: pytest.fail("executor dispatch must not occur after readiness failure"),
        record_lease=lambda *_args: pytest.fail("lease must not be persisted after readiness failure"),
    )

    with caplog.at_level("ERROR", logger="app.runtime.sandbox.runtime"):
        with pytest.raises(container_provider.ExecutorHealthTimeoutError) as exc_info:
            await runtime.submit(request(), event_sink=events.append)

    assert exc_info.value is startup_error
    record = caplog.records[-1]
    assert (record.provider, record.startup_stage, record.sdk_error_code, record.request_id) == (
        "opensandbox",
        "health",
        None,
        "request_668-A",
    )
    assert len(events) == 1
    assert set(events[0].payload) == {
        "schema_version",
        "run_id",
        "attempt_id",
        "readiness_phase",
        "container_state",
        "exit_code",
        "oom_killed",
        "published_port_observed",
        "health_outcome",
        "elapsed_ms",
    }
    assert "startup_stage" not in events[0].payload


@pytest.mark.asyncio
async def test_runtime_readiness_sink_failure_does_not_mask_original_error(tmp_path, monkeypatch):
    class StubSettings:
        sandbox_container_provider = "docker"
        sandbox_callback_base_url = "http://platform.test"
        sandbox_callback_token = "settings-token"

    evidence = ExecutorReadinessEvidence(
        readiness_phase="publish_wait",
        container_state="exited",
        exit_code=23,
        oom_killed=False,
        published_port_observed=False,
        health_outcome="not_attempted",
        elapsed_ms=91,
    )
    readiness_cause = TimeoutError("private readiness cause")
    readiness_error = container_provider.ExecutorHealthTimeoutError(
        readiness_evidence=evidence,
    )

    class ReadinessFailedProvider:
        async def create_or_reuse(self, _request, _workspace):
            raise readiness_error from readiness_cause

    attempted_events = []

    def failing_sink(event):
        attempted_events.append(event)
        raise RuntimeError("private persistence unavailable")

    monkeypatch.setattr("app.runtime.sandbox.runtime.get_settings", lambda: StubSettings())
    runtime = SandboxRuntime(
        workspace_root=tmp_path,
        provider=ReadinessFailedProvider(),
        execute_task=lambda *_args: (_ for _ in ()).throw(AssertionError("must not dispatch")),
        record_lease=lambda *_args: (_ for _ in ()).throw(AssertionError("lease must not be persisted")),
    )

    with pytest.raises(container_provider.ExecutorHealthTimeoutError) as exc_info:
        await runtime.submit(request(), event_sink=failing_sink)

    assert exc_info.value is readiness_error
    assert exc_info.value.readiness_evidence is evidence
    assert exc_info.value.__cause__ is readiness_cause
    assert [event.type for event in attempted_events] == ["sandbox_executor_readiness_failed"]


@pytest.mark.asyncio
async def test_readiness_emit_propagates_cancel(tmp_path, monkeypatch):
    from app.worker import WorkerRunCancelled

    class StubSettings:
        sandbox_container_provider = "docker"
        sandbox_callback_base_url = "http://platform.test"
        sandbox_callback_token = "settings-token"

    evidence = ExecutorReadinessEvidence(
        readiness_phase="health_probe",
        container_state="running",
        exit_code=None,
        oom_killed=None,
        published_port_observed=True,
        health_outcome="timeout",
        elapsed_ms=50,
    )
    monkeypatch.setattr("app.runtime.sandbox.runtime.get_settings", lambda: StubSettings())

    for index, cancellation in enumerate(
        (asyncio.CancelledError("task cancelled"), WorkerRunCancelled("worker cancelled"))
    ):
        class ReadinessFailedProvider:
            async def create_or_reuse(self, _request, _workspace):
                raise container_provider.ExecutorHealthTimeoutError(readiness_evidence=evidence)

        attempted_events = []

        async def cancelling_sink(event):
            attempted_events.append(event)
            raise cancellation

        runtime = SandboxRuntime(
            workspace_root=tmp_path.parent / f"c{index}",
            provider=ReadinessFailedProvider(),
            execute_task=lambda *_args: (_ for _ in ()).throw(AssertionError("must not dispatch")),
            record_lease=lambda *_args: (_ for _ in ()).throw(AssertionError("lease must not be persisted")),
        )

        with pytest.raises(type(cancellation)) as exc_info:
            await runtime.submit(
                request(run_id=f"run-cancel-{index}"),
                event_sink=cancelling_sink,
            )

        assert exc_info.value is cancellation
        assert [event.type for event in attempted_events] == ["sandbox_executor_readiness_failed"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("phase", "state", "exit_code", "oom_killed", "published", "outcome"),
    [
        ("publish_wait", "exited", 23, False, False, "not_attempted"),
        ("health_probe", "running", None, None, True, "timeout"),
    ],
)
@pytest.mark.parametrize("sink_fails", [False, True])
async def test_readiness_cleanup_failure_keeps_precedence(
    tmp_path,
    monkeypatch,
    phase,
    state,
    exit_code,
    oom_killed,
    published,
    outcome,
    sink_fails,
):
    class StubSettings:
        sandbox_container_provider = "docker"
        sandbox_callback_base_url = "http://platform.test"
        sandbox_callback_token = "settings-token"

    evidence = ExecutorReadinessEvidence(
        readiness_phase=phase,
        container_state=state,
        exit_code=exit_code,
        oom_killed=oom_killed,
        published_port_observed=published,
        health_outcome=outcome,
        elapsed_ms=73,
    )
    readiness_error = container_provider.ExecutorHealthTimeoutError(readiness_evidence=evidence)
    cleanup_error = container_provider.ContainerCleanupFailedError(readiness_evidence=evidence)

    class CleanupFailedProvider:
        async def create_or_reuse(self, _request, _workspace):
            raise cleanup_error from readiness_error

    attempted_events = []

    def event_sink(event):
        attempted_events.append(event)
        if sink_fails:
            raise RuntimeError("private persistence unavailable")

    monkeypatch.setattr("app.runtime.sandbox.runtime.get_settings", lambda: StubSettings())
    runtime = SandboxRuntime(
        workspace_root=tmp_path.parent / f"f{int(sink_fails)}{phase[0]}",
        provider=CleanupFailedProvider(),
        execute_task=lambda *_args: (_ for _ in ()).throw(AssertionError("must not dispatch")),
        record_lease=lambda *_args: (_ for _ in ()).throw(AssertionError("lease must not be persisted")),
    )

    with pytest.raises(container_provider.ContainerCleanupFailedError) as exc_info:
        await runtime.submit(request(), event_sink=event_sink)

    assert exc_info.value is cleanup_error
    assert exc_info.value.__cause__ is readiness_error
    assert exc_info.value.readiness_evidence is evidence
    assert len(attempted_events) == 1
    assert attempted_events[0].type == "sandbox_executor_readiness_failed"
    assert attempted_events[0].payload == {
        "schema_version": "ai-platform.executor-readiness-evidence.v1",
        "run_id": "run-a",
        "attempt_id": "qat_test-runtime-attempt",
        "readiness_phase": phase,
        "container_state": state,
        "exit_code": exit_code,
        "oom_killed": oom_killed,
        "published_port_observed": published,
        "health_outcome": outcome,
        "elapsed_ms": 73,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("cleanup_fails", [False, True])
async def test_no_readiness_event_for_proof_or_untyped_cleanup(
    tmp_path,
    monkeypatch,
    cleanup_fails,
):
    class StubSettings:
        sandbox_container_provider = "docker"
        sandbox_callback_base_url = "http://platform.test"
        sandbox_callback_token = "settings-token"

    proof_error = container_provider.GovernedEgressAdmissionError()
    provider_error = (
        container_provider.ContainerCleanupFailedError() if cleanup_fails else proof_error
    )

    class ProofRejectedProvider:
        async def create_or_reuse(self, _request, _workspace):
            if cleanup_fails:
                raise provider_error from proof_error
            raise provider_error

    monkeypatch.setattr("app.runtime.sandbox.runtime.get_settings", lambda: StubSettings())
    events = []
    runtime = SandboxRuntime(
        workspace_root=tmp_path,
        provider=ProofRejectedProvider(),
        execute_task=lambda *_args: (_ for _ in ()).throw(AssertionError("must not dispatch")),
        record_lease=lambda *_args: (_ for _ in ()).throw(AssertionError("lease must not be persisted")),
    )

    with pytest.raises(type(provider_error)) as exc_info:
        await runtime.submit(request(), event_sink=events.append)

    assert exc_info.value is provider_error
    if cleanup_fails:
        assert exc_info.value.__cause__ is proof_error
        assert exc_info.value.readiness_evidence is None
    assert events == []


@pytest.mark.asyncio
async def test_runtime_uses_opensandbox_external_bridge_callback_without_changing_docker_base(
    tmp_path, monkeypatch
):
    sent = []

    class StubSettings:
        sandbox_container_provider = "opensandbox"
        sandbox_callback_base_url = "http://api.sandbox.internal:8020"
        sandbox_callback_token = "settings-token"
        opensandbox_external_egress_callback_base_url = "https://bridge.internal.example:18443"
        opensandbox_external_egress_openai_base_url = "https://bridge.internal.example:18443/openai/v1"
        opensandbox_external_egress_anthropic_base_url = "https://bridge.internal.example:18443/anthropic"

    class OpenSandboxProvider(FakeContainerProvider):
        async def create_or_reuse(self, runtime_request, workspace):
            lease = await super().create_or_reuse(runtime_request, workspace)
            return ContainerLease(**{**lease.model_dump(), "provider": "opensandbox"})

    async def execute(_executor_url, task_request):
        sent.append(task_request)
        return {"status": "accepted", "session_id": task_request.session_id, "run_id": task_request.run_id}

    monkeypatch.setattr("app.runtime.sandbox.runtime.get_settings", lambda: StubSettings())
    runtime = SandboxRuntime(
        workspace_root=tmp_path,
        provider=OpenSandboxProvider(executor_url="http://executor.test"),
        execute_task=execute,
        callback_token_resolver=lambda _token_id: "secret-token",
        record_lease=noop_lease,
        release_lease=noop_lease,
    )

    await runtime.submit(request())

    assert sent[0].callback_base_url == "https://bridge.internal.example:18443"
    assert sent[0].callback_url == (
        "https://bridge.internal.example:18443/api/ai/runtime/callbacks/executor"
    )
    assert StubSettings.sandbox_callback_base_url == "http://api.sandbox.internal:8020"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("sandbox_mode", "cancelled", "reason"),
    (
        ("ephemeral", False, "dispatch_validation_failed"),
        ("persistent", False, "dispatch_validation_failed"),
        ("ephemeral", True, "dispatch_validation_cancelled"),
        ("persistent", True, "dispatch_validation_cancelled"),
    ),
)
async def test_runtime_revalidates_provider_evidence_immediately_before_dispatch(
    tmp_path,
    monkeypatch,
    sandbox_mode,
    cancelled,
    reason,
):
    """A proof/topology change after lease persistence must block executor I/O."""
    calls: list[tuple[str, str]] = []

    class StubSettings:
        sandbox_callback_base_url = "http://platform.test"
        sandbox_callback_token = "settings-token"

    class DriftedProvider(FakeContainerProvider):
        async def validate_for_dispatch(self, lease, runtime_request, leased_workspace):
            calls.append(("validate", lease.container_id))
            if cancelled:
                raise asyncio.CancelledError()
            raise RuntimeError("governed egress changed after acquisition")

        async def stop(self, lease, *, reason):
            calls.append(("stop", reason))
            return await super().stop(lease, reason=reason)

    async def execute(*_args, **_kwargs):
        raise AssertionError("executor dispatch must not occur after evidence drift")

    monkeypatch.setattr("app.runtime.sandbox.runtime.get_settings", lambda: StubSettings())
    runtime = SandboxRuntime(
        workspace_root=tmp_path,
        provider=DriftedProvider(executor_url="http://executor.test"),
        execute_task=execute,
        callback_token_resolver=lambda _token_id: "secret-token",
        record_lease=lambda *_args: calls.append(("record", "lease")) or "lease-a",
        release_lease=lambda _lease, reason, *_args: calls.append(("release", reason)),
    )

    if cancelled:
        with pytest.raises(asyncio.CancelledError):
            await runtime.submit(request(sandbox_mode=sandbox_mode))
    else:
        with pytest.raises(RuntimeError, match="egress changed"):
            await runtime.submit(request(sandbox_mode=sandbox_mode))

    assert calls == [
        ("record", "lease"),
        ("validate", "exec-run-a"),
        ("stop", reason),
        ("release", reason),
    ]


@pytest.mark.asyncio
async def test_runtime_validation_cleanup_failure_keeps_persistent_lease_fail_closed(tmp_path, monkeypatch):
    calls: list[tuple[str, str]] = []

    class StubSettings:
        sandbox_callback_base_url = "http://platform.test"
        sandbox_callback_token = "settings-token"

    class StopFailedProvider(FakeContainerProvider):
        async def validate_for_dispatch(self, lease, runtime_request, leased_workspace):
            raise RuntimeError("governed egress changed after acquisition")

        async def stop(self, lease, *, reason):
            calls.append(("stop", reason))
            return StopResult(container_id=lease.container_id, status="failed", message="still tracked")

    async def execute(*_args, **_kwargs):
        raise AssertionError("executor dispatch must not occur after evidence drift")

    monkeypatch.setattr("app.runtime.sandbox.runtime.get_settings", lambda: StubSettings())
    runtime = SandboxRuntime(
        workspace_root=tmp_path,
        provider=StopFailedProvider(executor_url="http://executor.test"),
        execute_task=execute,
        callback_token_resolver=lambda _token_id: "secret-token",
        record_lease=lambda *_args: "lease-a",
        release_lease=lambda _lease, reason, *_args: calls.append(("release", reason)),
    )

    with pytest.raises(RuntimeError, match="sandbox_runtime_cleanup_failed"):
        await runtime.submit(request(sandbox_mode="persistent"))

    assert calls == [("stop", "dispatch_validation_failed")]


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
    assert sent[0].callback_token_id == "cbt:run-a:qat_test-runtime-attempt"


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

        async def validate_for_dispatch(self, lease, runtime_request, leased_workspace):
            return None

        async def stage_workspace(self, lease, runtime_request, leased_workspace):
            return None

        async def collect_workspace(self, lease, runtime_request, leased_workspace):
            return None

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
@pytest.mark.parametrize(
    ("reported_code", "reported_message", "expected_code", "expected_message"),
    [
        (
            "executor_health_timeout",
            "Executor health timeout",
            "executor_health_timeout",
            "Executor health timeout",
        ),
        (
            "https://executor.test/run?token=private-token<html>private-prompt</html>",
            "private-prompt",
            "executor_reported_failure",
            "Executor reported failure",
        ),
    ],
)
async def test_runtime_releases_ephemeral_lease_as_failed_when_executor_reports_failed(
    tmp_path,
    monkeypatch,
    reported_code,
    reported_message,
    expected_code,
    expected_message,
):
    calls = []

    class StubSettings:
        sandbox_callback_base_url = "http://platform.test"
        sandbox_callback_token = "settings-token"

    async def execute(executor_url, task_request):
        return {
            "status": "failed",
            "run_id": task_request.run_id,
            "error_code": reported_code,
            "error_message": reported_message,
            "url": "https://executor.test/run?token=private-token",
            "path": "/private/workspace",
            "nested": {"prompt": "private-prompt"},
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
    assert result.executor_response["error_code"] == expected_code
    assert result.executor_response["error_message"] == expected_message
    assert "private-token" not in str(result.executor_response)
    assert "private-prompt" not in str(result.executor_response)
    assert set(result.executor_response) == {"status", "run_id", "error_code", "error_message"}
    assert calls == [("record", "run-a"), ("release", "run_failed", "lease-created-a")]


@pytest.mark.asyncio
async def test_runtime_preserves_typed_executor_http_error_and_cleanup_order(tmp_path, monkeypatch):
    calls = []
    expected_error = SandboxExecutorHttpError(
        status_code=401,
        error_code="invalid_executor_credential",
        detail="invalid_executor_credential",
    )

    class StubSettings:
        sandbox_callback_base_url = "http://platform.test"
        sandbox_callback_token = "settings-token"

    class OrderedProvider(FakeContainerProvider):
        async def stop(self, lease, reason):
            calls.append(("stop", reason))
            return await super().stop(lease, reason=reason)

    async def execute(executor_url, task_request):
        raise expected_error

    async def record_lease(lease, runtime_request, workspace):
        return {"id": "lease-http-error"}

    async def release_lease(lease, reason, lease_record_id=None):
        calls.append(("release", reason, lease_record_id))

    monkeypatch.setattr("app.runtime.sandbox.runtime.get_settings", lambda: StubSettings())
    runtime = SandboxRuntime(
        workspace_root=tmp_path,
        provider=OrderedProvider(executor_url="http://executor.test"),
        execute_task=execute,
        callback_token_resolver=lambda token_id: "secret-token",
        record_lease=record_lease,
        release_lease=release_lease,
    )

    with pytest.raises(SandboxExecutorHttpError) as raised:
        await runtime.submit(request(sandbox_mode="ephemeral"))

    assert raised.value is expected_error
    assert calls == [
        ("stop", "dispatch_failed"),
        ("release", "dispatch_failed", "lease-http-error"),
    ]


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
                        "security_profile": "governed",
                        "attempt_id": "qat_test-runtime-attempt",
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
                        / "attempts"
                        / "qat_test-runtime-attempt"
                        / "workspace"
                    ),
                    "workspace_container_path": "/workspace",
                    "labels": {
                        "ai-platform.run_id": "run-a",
                        "ai-platform.attempt_id": "qat_test-runtime-attempt",
                    },
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
    from app.execution_boundary import (
        build_governed_egress_proof,
        governed_egress_authorized_native_tool_scope,
        governed_egress_authorized_skill_scope,
        governed_egress_proof_label,
    )

    signing_key = "runtime-test-proof-key-with-enough-entropy-2026"
    runtime_request = request(sandbox_mode="ephemeral", trace_id="trace-run-a")

    governed_egress_proof = build_governed_egress_proof(
        signing_key=signing_key,
        provider="opensandbox",
        runtime_subject="runtime-subject-a",
        policy_subject="gateway-policy-subject-a",
        callback_subject="callback-boundary-subject-a",
        denial_subject="gateway-deny-subject-a",
        network_id="profile-a",
        network_name="opensandbox.local:8080",
        network_internal=False,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        session_id="session-a",
        run_id="run-a",
        attempt_id=runtime_request.attempt_id,
        image_subject="registry.example/ai-platform@sha256:" + "a" * 64,
        image_digest="sha256:" + "a" * 64,
        authorized_skill_scope=governed_egress_authorized_skill_scope(
            skill_ids=runtime_request.skill_ids,
            mcp_tool_ids=runtime_request.mcp_tool_ids,
        ),
        authorized_native_tool_scope=governed_egress_authorized_native_tool_scope(
            runtime_request.tool_policy_subjects
        ),
        lease_identity="opensandbox:opensandbox-run-a:osb-run-a",
    )

    class StubSettings:
        sandbox_callback_base_url = "http://platform.test"
        sandbox_callback_token = "settings-token"
        sandbox_egress_proof_signing_key = signing_key
        opensandbox_external_egress_callback_base_url = "https://bridge.internal.example:18443"
        opensandbox_external_egress_openai_base_url = "https://bridge.internal.example:18443/openai/v1"
        opensandbox_external_egress_anthropic_base_url = "https://bridge.internal.example:18443/anthropic"

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
                        "ai-platform.executor.requested_image": "registry.example/ai-platform@sha256:" + "a" * 64,
                        "ai-platform.executor.requested_image_digest": "sha256:" + "a" * 64,
                        "ai-platform.external_egress.endpoint": "http://127.0.0.1:18081/private-capability",
                        "ai-platform.governed_egress.proof": governed_egress_proof_label(governed_egress_proof),
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

    await runtime.submit(runtime_request)

    create_kwargs = calls[0][1]
    assert create_kwargs["provider"] == "opensandbox"
    assert create_kwargs["attempt_id"] == runtime_request.attempt_id
    assert create_kwargs["runtime_container_id"] == "osb-run-a"
    assert create_kwargs["runtime_container_name"] == "opensandbox-run-a"
    assert create_kwargs["runtime_executor_url"] == "http://opensandbox-executor.test"
    assert create_kwargs["runtime_workspace_container_path"] == "/sandbox-workspace"
    assert create_kwargs["lease_payload_json"]["container_id"] == "osb-run-a"
    assert create_kwargs["lease_payload_json"]["attempt_id"] == runtime_request.attempt_id
    assert "executor_headers" not in create_kwargs["lease_payload_json"]
    assert create_kwargs["lease_payload_json"]["labels"] == {
        "ai-platform.run_id": "run-a",
        "ai-platform.attempt_id": runtime_request.attempt_id,
    }
    assert create_kwargs["lease_payload_json"]["governed_egress_proof"] == governed_egress_proof
    assert "private-capability" not in repr(create_kwargs["lease_payload_json"])
    assert "registry.example" not in repr(create_kwargs["lease_payload_json"])
    assert calls[1] == ("release", "lease-created-a", "dispatch_completed")


@pytest.mark.asyncio
async def test_runtime_persists_explicit_internal_test_opensandbox_evidence_without_governed_proof(
    tmp_path, monkeypatch
):
    from app.runtime.sandbox.opensandbox_policy import internal_test_opensandbox_lease_labels

    runtime_request = request(sandbox_mode="ephemeral", browser_enabled=False)
    captured: list[dict[str, Any]] = []
    image = "registry.example/ai-platform@sha256:" + "a" * 64
    digest = "sha256:" + "a" * 64

    class StubSettings:
        sandbox_container_provider = "opensandbox"
        sandbox_security_profile = "internal-test"
        deployment_environment = "test"
        sandbox_egress_proof_signing_key = ""
        opensandbox_expected_network_mode = "bridge"
        opensandbox_executor_image = image
        opensandbox_executor_image_digest = digest
        sandbox_executor_image = image
        sandbox_runtime_subject = "runtime-subject-fixed-sha"

    async def create_sandbox_lease(_conn, **kwargs):
        captured.append(kwargs)
        return {"id": "lease-internal-test"}

    labels = internal_test_opensandbox_lease_labels(
        runtime_request,
        StubSettings(),
        executor_identity_labels={},
        skill_mount_labels={},
    )
    lease = ContainerLease(
        container_id="osb-run-a",
        container_name="opensandbox-run-a",
        provider="opensandbox",
        executor_url="http://opensandbox-executor.test",
        tenant_id=runtime_request.tenant_id,
        workspace_id=runtime_request.workspace_id,
        user_id=runtime_request.user_id,
        session_id=runtime_request.session_id,
        run_id=runtime_request.run_id,
        sandbox_mode=runtime_request.sandbox_mode,
        browser_enabled=False,
        workspace_host_path=str(tmp_path),
        workspace_container_path="/workspace",
        labels=labels,
    )
    monkeypatch.setattr("app.runtime.sandbox.runtime.get_settings", lambda: StubSettings())
    monkeypatch.setattr("app.runtime.sandbox.runtime.transaction", fake_transaction)
    monkeypatch.setattr("app.runtime.sandbox.runtime.repositories.create_sandbox_lease", create_sandbox_lease)
    runtime = SandboxRuntime(workspace_root=tmp_path, provider=FakeContainerProvider())
    workspace = runtime.workspace_manager.prepare(runtime_request)

    lease_id = await runtime._record_runtime_lease(lease, runtime_request, workspace)

    assert lease_id == "lease-internal-test"
    payload = captured[0]["lease_payload_json"]
    assert payload["security_profile"] == "internal-test"
    assert payload["labels"]["ai-platform.internal_test.profile"] == "official-opensandbox-direct-v1"
    assert payload["requested_image"] == image
    assert payload["requested_image_digest"] == digest
    assert "governed_egress_proof" not in payload


@pytest.mark.asyncio
async def test_runtime_rejects_retired_opensandbox_profile_before_persistence(tmp_path, monkeypatch):
    runtime_request = request(sandbox_mode="ephemeral", browser_enabled=False)

    class StubSettings:
        sandbox_container_provider = "opensandbox"
        sandbox_security_profile = "governed"
        sandbox_callback_base_url = "http://platform.test"
        sandbox_callback_token = "settings-token"

    labels = {
        "ai-platform.owner": "sandbox-runtime",
        "ai-platform.tenant_id": runtime_request.tenant_id,
        "ai-platform.workspace_id": runtime_request.workspace_id,
        "ai-platform.user_id": runtime_request.user_id,
        "ai-platform.session_id": runtime_request.session_id,
        "ai-platform.run_id": runtime_request.run_id,
        "ai-platform.attempt_id": runtime_request.attempt_id,
        "ai-platform.sandbox_mode": runtime_request.sandbox_mode,
        "ai-platform.browser_enabled": "false",
        "ai-platform.provider_backend": "opensandbox",
        "ai-platform.security_profile": "trusted_internal",
        "ai-platform.executor.requested_image": "registry.example/ai-platform@sha256:" + "a" * 64,
        "ai-platform.executor.requested_image_digest": "sha256:" + "a" * 64,
    }

    monkeypatch.setattr("app.runtime.sandbox.runtime.get_settings", lambda: StubSettings())
    runtime = SandboxRuntime(workspace_root=tmp_path, provider=FakeContainerProvider())
    workspace = runtime.workspace_manager.prepare(runtime_request)
    lease = ContainerLease(
        container_id="osb-run-a",
        container_name="opensandbox-run-a-qat_test-runtime-attempt",
        provider="opensandbox",
        executor_url="http://osb-run-a.opensandbox.test:18000",
        executor_headers={"X-AI-Platform-Executor-Credential": "test-executor-key"},
        tenant_id=runtime_request.tenant_id,
        workspace_id=runtime_request.workspace_id,
        user_id=runtime_request.user_id,
        session_id=runtime_request.session_id,
        run_id=runtime_request.run_id,
        sandbox_mode=runtime_request.sandbox_mode,
        browser_enabled=runtime_request.browser_enabled,
        workspace_host_path=workspace.workspace_host_path,
        workspace_container_path=workspace.workspace_container_path,
        labels=labels,
    )

    with pytest.raises(ValueError, match="sandbox_security_profile_invalid"):
        await runtime._record_runtime_lease(lease, runtime_request, workspace)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "replayed_binding",
        ("tenant", "workspace", "user", "session", "run", "attempt", "image", "skill", "tool"),
)
async def test_runtime_rejects_signed_proof_replayed_across_request_bindings(
    tmp_path,
    monkeypatch,
    replayed_binding,
):
    from app.execution_boundary import (
        build_governed_egress_proof,
        governed_egress_authorized_native_tool_scope,
        governed_egress_authorized_skill_scope,
        governed_egress_proof_label,
    )

    signing_key = "runtime-test-proof-key-with-enough-entropy-2026"

    class StubSettings:
        sandbox_callback_base_url = "http://platform.test"
        sandbox_callback_token = "settings-token"
        sandbox_egress_proof_signing_key = signing_key

    runtime_request = request(sandbox_mode="ephemeral")
    image = "registry.example/ai-platform@sha256:" + "a" * 64
    proof_scope = {
        "tenant_id": runtime_request.tenant_id,
        "workspace_id": runtime_request.workspace_id,
        "user_id": runtime_request.user_id,
        "session_id": runtime_request.session_id,
            "run_id": runtime_request.run_id,
            "attempt_id": runtime_request.attempt_id,
        "image_subject": image,
        "image_digest": "sha256:" + "a" * 64,
        "authorized_skill_scope": governed_egress_authorized_skill_scope(
            skill_ids=runtime_request.skill_ids,
            mcp_tool_ids=runtime_request.mcp_tool_ids,
        ),
        "authorized_native_tool_scope": governed_egress_authorized_native_tool_scope(
            runtime_request.tool_policy_subjects
        ),
    }
    replacements = {
        "tenant": ("tenant_id", "tenant-b"),
        "workspace": ("workspace_id", "workspace-b"),
        "user": ("user_id", "user-b"),
        "session": ("session_id", "session-b"),
            "run": ("run_id", "run-b"),
            "attempt": ("attempt_id", "qat_other-attempt"),
        "image": ("image_subject", "registry.example/ai-platform@sha256:" + "b" * 64),
        "skill": (
            "authorized_skill_scope",
            governed_egress_authorized_skill_scope(skill_ids=["other-skill"], mcp_tool_ids=[]),
        ),
        "tool": (
            "authorized_native_tool_scope",
            governed_egress_authorized_native_tool_scope([{"identity": "Bash"}]),
        ),
    }
    changed_field, changed_value = replacements[replayed_binding]
    proof_scope[changed_field] = changed_value
    proof = build_governed_egress_proof(
        signing_key=signing_key,
        provider="docker",
        runtime_subject="docker-internal-bridge",
        policy_subject="network-a:internal",
        callback_subject="http://api.sandbox.internal:8020",
        denial_subject="network-a:default-deny",
        network_id="network-a",
        network_name="ai-platform-sandbox-egress-internal-v1",
        network_internal=True,
        lease_identity="docker:executor-exec-run-a:exec-run-a",
        **proof_scope,
    )
    lease = ContainerLease(
        container_id="exec-run-a",
        container_name="executor-exec-run-a",
        provider="docker",
        executor_url="http://executor.test",
        tenant_id=runtime_request.tenant_id,
        workspace_id=runtime_request.workspace_id,
        user_id=runtime_request.user_id,
        session_id=runtime_request.session_id,
        run_id=runtime_request.run_id,
        sandbox_mode=runtime_request.sandbox_mode,
        browser_enabled=runtime_request.browser_enabled,
        workspace_host_path=str(tmp_path / "workspace"),
        labels={
            "ai-platform.executor.requested_image": image,
            "ai-platform.executor.requested_image_digest": "sha256:" + "a" * 64,
            "ai-platform.governed_egress.proof": governed_egress_proof_label(proof),
        },
    )
    monkeypatch.setattr("app.runtime.sandbox.runtime.get_settings", lambda: StubSettings())
    runtime = SandboxRuntime(workspace_root=tmp_path, provider=FakeContainerProvider(executor_url="http://executor.test"))
    workspace = runtime.workspace_manager.prepare(runtime_request)

    with pytest.raises(ValueError, match="governed_egress_proof_invalid"):
        await runtime._record_runtime_lease(lease, runtime_request, workspace)


@pytest.mark.asyncio
async def test_runtime_default_db_record_rejects_incomplete_trusted_runtime_handle(tmp_path, monkeypatch):
    calls = []

    class StubSettings:
        sandbox_callback_base_url = "http://platform.test"
        sandbox_callback_token = "settings-token"
        opensandbox_external_egress_callback_base_url = "https://bridge.internal.example:18443"
        opensandbox_external_egress_openai_base_url = "https://bridge.internal.example:18443/openai/v1"
        opensandbox_external_egress_anthropic_base_url = "https://bridge.internal.example:18443/anthropic"

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
async def test_runtime_records_opensandbox_provider_as_platform_db_lease(tmp_path, monkeypatch):
    calls = []

    settings = SimpleNamespace(
        sandbox_callback_base_url="http://platform.test",
        sandbox_callback_token="settings-token",
        opensandbox_external_egress_callback_base_url="https://bridge.internal.example:18443",
        opensandbox_external_egress_openai_base_url="https://bridge.internal.example:18443/openai/v1",
        opensandbox_external_egress_anthropic_base_url="https://bridge.internal.example:18443/anthropic",
    )
    monkeypatch.setattr("app.runtime.sandbox.runtime.get_settings", lambda: settings)

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
        sandbox_egress_proof_signing_key = "runtime-test-proof-key-with-enough-entropy-2026"
        opensandbox_external_egress_callback_base_url = "https://bridge.internal.example:18443"
        opensandbox_external_egress_openai_base_url = "https://bridge.internal.example:18443/openai/v1"
        opensandbox_external_egress_anthropic_base_url = "https://bridge.internal.example:18443/anthropic"

    class HeaderProvider(FakeContainerProvider):
        async def create_or_reuse(self, request, workspace):
            from app.execution_boundary import (
                build_governed_egress_proof,
                governed_egress_authorized_native_tool_scope,
                governed_egress_authorized_skill_scope,
                governed_egress_proof_label,
            )

            lease = await super().create_or_reuse(request, workspace)
            image = "registry.example/ai-platform@sha256:" + "a" * 64
            proof = build_governed_egress_proof(
                signing_key=StubSettings.sandbox_egress_proof_signing_key,
                provider="opensandbox",
                runtime_subject="runsc",
                policy_subject="gateway-a",
                callback_subject="callback-a",
                denial_subject="deny-a",
                network_id="profile-a",
                network_name="opensandbox-a",
                network_internal=False,
                tenant_id=request.tenant_id,
                workspace_id=request.workspace_id,
                user_id=request.user_id,
                    session_id=request.session_id,
                    run_id=request.run_id,
                    attempt_id=request.attempt_id,
                image_subject=image,
                image_digest="sha256:" + "a" * 64,
                authorized_skill_scope=governed_egress_authorized_skill_scope(
                    skill_ids=request.skill_ids, mcp_tool_ids=request.mcp_tool_ids
                ),
                authorized_native_tool_scope=governed_egress_authorized_native_tool_scope(
                    request.tool_policy_subjects
                ),
                lease_identity="opensandbox:opensandbox-run-a:osb-run-a",
            )
            return ContainerLease(
                **{
                    **lease.model_dump(),
                    "container_id": "osb-run-a",
                    "container_name": "opensandbox-run-a",
                    "provider": "opensandbox",
                    "executor_url": "http://opensandbox-executor.test",
                    "executor_headers": {"OPENSANDBOX-EGRESS-AUTH": "opensandbox-secret"},
                    "labels": {
                        **lease.labels,
                        "ai-platform.executor.requested_image": image,
                        "ai-platform.executor.requested_image_digest": "sha256:" + "a" * 64,
                        "ai-platform.governed_egress.proof": governed_egress_proof_label(proof),
                    },
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
            attempt_id="qat_test-executor-attempt",
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
                    "attempt_id": "qat_test-executor-attempt",
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
    created_requests = []

    class RecordingProvider(FakeContainerProvider):
        async def create_or_reuse(self, runtime_request, workspace):
            created_requests.append(list(runtime_request.materialized_file_names))
            return await super().create_or_reuse(runtime_request, workspace)

    provider = RecordingProvider(executor_url="http://executor.test")

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
        await runtime.submit(
            request(
                sandbox_mode="persistent",
                materialized_file_names=["controlled-workbook.xlsx", "controlled-report.pdf"],
                tool_policy_subjects=[
                    {
                        "identity": "Skill",
                        "registered": True,
                        "declared": True,
                        "allowed_skill_names": [f"controlled-file-skill-{index:03d}" for index in range(256)],
                    }
                ],
            )
        )

    assert created_requests == [["controlled-workbook.xlsx", "controlled-report.pdf"]]
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

    assert sent[0].callback_token_id == "cbt:run-a:qat_test-runtime-attempt"
    assert sent[0].callback_token == derived_callback_token("settings-token", "cbt:run-a:qat_test-runtime-attempt")
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
    assert sent[0].callback_token_id == "cbt:run-a:qat_test-runtime-attempt"
    assert sent[0].callback_token == "derived-for-cbt:run-a:qat_test-runtime-attempt"


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


@pytest.mark.asyncio
async def test_runtime_execution_owner_stops_persistent_provider_before_confirming_quiescence(tmp_path):
    calls = []
    executing = asyncio.Event()

    class RecordingProvider(FakeContainerProvider):
        async def stop(self, lease, *, reason: str):
            calls.append(("stop", reason))
            return await super().stop(lease, reason=reason)

    async def execute(executor_url, task_request):
        executing.set()
        await asyncio.Event().wait()

    async def record_lease(lease, request, workspace):
        calls.append(("record", request.run_id))
        return {"id": "lease-a"}

    async def release_lease(lease, reason, lease_record_id=None):
        calls.append(("release", reason, lease_record_id))

    runtime = SandboxRuntime(
        workspace_root=tmp_path,
        provider=RecordingProvider(executor_url="http://executor.test"),
        execute_task=execute,
        callback_token_resolver=lambda token_id: "secret-token",
        record_lease=record_lease,
        release_lease=release_lease,
    )
    owner = RunExecutionOwner("run-a")
    owner.start(runtime.submit(request(sandbox_mode="persistent"), execution_owner=owner))
    await asyncio.wait_for(executing.wait(), timeout=0.5)

    stopped = await owner.stop(reason="cancel_requested", timeout_seconds=0.2)
    stopped_again = await owner.stop(reason="cancel_requested", timeout_seconds=0.2)

    assert stopped.quiescent is True
    assert stopped_again.quiescent is True
    assert calls == [
        ("record", "run-a"),
        ("stop", "cancel_requested"),
        ("release", "cancel_requested", "lease-a"),
    ]


@pytest.mark.asyncio
async def test_runtime_execution_owner_does_not_release_lease_when_provider_stop_fails(tmp_path):
    calls = []
    executing = asyncio.Event()

    class StopFailedProvider(FakeContainerProvider):
        async def stop(self, lease, *, reason: str):
            calls.append(("stop", reason))
            if len(calls) == 1:
                return StopResult(container_id=lease.container_id, status="failed", message="stop failed")
            return await super().stop(lease, reason=reason)

    async def execute(executor_url, task_request):
        executing.set()
        await asyncio.Event().wait()

    async def record_lease(lease, request, workspace):
        return {"id": "lease-a"}

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
    owner = RunExecutionOwner("run-a")
    owner.start(runtime.submit(request(sandbox_mode="persistent"), execution_owner=owner))
    await asyncio.wait_for(executing.wait(), timeout=0.5)

    stopped = await owner.stop(reason="cancel_requested", timeout_seconds=0.2)

    assert stopped.status == "failed"
    assert stopped.quiescent is False
    assert calls == [("stop", "cancel_requested")]
    assert (await owner.stop(reason="test_cleanup", timeout_seconds=0.2)).quiescent is True
    assert calls == [
        ("stop", "cancel_requested"),
        ("stop", "test_cleanup"),
        ("release", "test_cleanup", "lease-a"),
    ]


@pytest.mark.asyncio
async def test_runtime_execution_owner_retries_after_provider_stop_raises(tmp_path):
    calls = []
    executing = asyncio.Event()

    class StopRaisesProvider(FakeContainerProvider):
        async def stop(self, lease, *, reason: str):
            calls.append(("stop", reason))
            if len(calls) == 1:
                raise RuntimeError("stop unavailable")
            return await super().stop(lease, reason=reason)

    async def execute(executor_url, task_request):
        executing.set()
        await asyncio.Event().wait()

    async def record_lease(lease, request, workspace):
        return {"id": "lease-a"}

    async def release_lease(lease, reason, lease_record_id=None):
        calls.append(("release", reason, lease_record_id))

    runtime = SandboxRuntime(
        workspace_root=tmp_path,
        provider=StopRaisesProvider(executor_url="http://executor.test"),
        execute_task=execute,
        callback_token_resolver=lambda token_id: "secret-token",
        record_lease=record_lease,
        release_lease=release_lease,
    )
    owner = RunExecutionOwner("run-a")
    owner.start(runtime.submit(request(sandbox_mode="persistent"), execution_owner=owner))
    await asyncio.wait_for(executing.wait(), timeout=0.5)

    failed = await owner.stop(reason="cancel_requested", timeout_seconds=0.2)

    assert failed.status == "failed"
    assert failed.quiescent is False
    assert calls == [("stop", "cancel_requested")]
    assert (await owner.stop(reason="test_cleanup", timeout_seconds=0.2)).quiescent is True
    assert calls == [
        ("stop", "cancel_requested"),
        ("stop", "test_cleanup"),
        ("release", "test_cleanup", "lease-a"),
    ]


@pytest.mark.asyncio
async def test_runtime_execution_owner_cancel_before_lease_established_releases_nothing(tmp_path):
    create_started = asyncio.Event()
    calls = []

    class SlowCreateProvider(FakeContainerProvider):
        async def create_or_reuse(self, request, workspace):
            create_started.set()
            await asyncio.Event().wait()

        async def stop(self, lease, *, reason: str):
            calls.append(("stop", reason))
            return await super().stop(lease, reason=reason)

    async def release_lease(lease, reason, lease_record_id=None):
        calls.append(("release", reason, lease_record_id))

    runtime = SandboxRuntime(
        workspace_root=tmp_path,
        provider=SlowCreateProvider(executor_url="http://executor.test"),
        execute_task=lambda *_args, **_kwargs: None,
        callback_token_resolver=lambda token_id: "secret-token",
        record_lease=noop_lease,
        release_lease=release_lease,
    )
    owner = RunExecutionOwner("run-a")
    owner.start(runtime.submit(request(sandbox_mode="persistent"), execution_owner=owner))
    await asyncio.wait_for(create_started.wait(), timeout=0.5)

    stopped = await owner.stop(reason="cancel_requested", timeout_seconds=0.2)

    assert stopped.quiescent is True
    assert calls == []
