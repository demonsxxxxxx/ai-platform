from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.execution.api import ContinuationSettlementPorts
from app.executors.claude.capability_policy import RecoveryToolGate
from app.runs.application.attempt_lifecycle import RunAttemptLifecycleService
from app.runs.application.worker_result_commit import WorkerResultCommitCommand, WorkerResultCommitService


@pytest.mark.parametrize("tool", ["Bash", "Agent", "Skill", "WebFetch", "WebSearch", "Write", "Edit", "NotebookEdit", "mcp__server__write", "mcp__server__code"])
def test_recovery_inspection_denies_execution_and_unconfirmed_effects(tool):
    gate = RecoveryToolGate.from_mapping({
        "state": "inspection", "allowed_tool_identities": [tool], "released_tool_identities": [],
    })
    assert gate.denial(tool, {"write_capable": True}) == "recovery_effect_unconfirmed"


def test_recovery_inspection_requires_operation_evidence_even_for_released_write_identities():
    gate = RecoveryToolGate.from_mapping({
        "state": "inspection", "allowed_tool_identities": ["Read", "mcp__server__query", "mcp__server__write", "Bash"],
        "released_tool_identities": ["mcp__server__write"],
    })
    assert gate.denial("Read", None) is None
    assert gate.denial("mcp__server__query", {"write_capable": False}) is None
    assert gate.denial("mcp__server__write", {"write_capable": True}) == "recovery_effect_unconfirmed"
    assert gate.denial("Bash", {"write_capable": False}) == "recovery_effect_unconfirmed"
    assert gate.denial("Glob", None) == "recovery_tool_not_allowed"
    with pytest.raises(ValueError, match="recovery_tool_gate_invalid"):
        RecoveryToolGate.from_mapping({"state": "inspection", "allowed_tool_identities": [], "released_tool_identities": ["Bash"]})


@pytest.mark.asyncio
@pytest.mark.parametrize("operation,started,allowed", [
    ("provider_append", False, True), ("executor_terminal", False, True),
    ("tool_completion", False, False), ("tool_completion", True, True),
    ("provider_load", False, False), ("tool_start", True, False), ("run_input", False, False),
])
@pytest.mark.parametrize("defect", [None, "expired", "released", "generation", "attempt", "takeover", "terminal", "scope"])
async def test_original_cancel_tail_requires_live_exact_previous_generation(operation, started, allowed, defect):
    now = datetime.now(timezone.utc)
    run = dict(id="run-a", tenant_id="tenant-a", workspace_id="workspace-a", user_id="user-a", session_id="session-a", status="running", cancel_requested_at=now)
    attempt = dict(id="attempt-a", tenant_id="tenant-a", run_id="run-a", status="cancel_requested", owner_kind="queue_worker", owner_id="worker-a", owner_generation=2)
    lease = {**run, "id": "lease-a", "run_id": "run-a", "attempt_id": "attempt-a", "status": "active", "expires_at": now + timedelta(seconds=60), "lease_payload_json": {"owner_generation": 1}}
    if defect == "expired":
        lease["expires_at"] = now
    if defect == "released":
        lease["released_at"] = now
    if defect == "generation":
        attempt["owner_generation"] = 3
    if defect == "attempt":
        lease["attempt_id"] = "another-attempt"
    if defect == "takeover":
        attempt["owner_kind"] = "reconciler"
    if defect == "terminal":
        run["status"] = "cancelled"
    if defect == "scope":
        lease["session_id"] = "another-session"

    async def load_run(*args, **kwargs): return run
    async def load_attempt(*args, **kwargs): return attempt
    service = RunAttemptLifecycleService(SimpleNamespace(get_scoped_reconciliation_run=load_run, get_run_attempt=load_attempt))
    assert await service.authorize_cancel_tail_receipt(
        object(), tenant_id="tenant-a", run_id="run-a", attempt_id="attempt-a",
        scope={"workspace_id": "workspace-a", "user_id": "user-a", "session_id": "session-a", "agent_id": "agent-a"},
        lease=lease, owner_generation=1, operation=operation, now=now, operation_started=started,
    ) is (allowed and defect is None)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["succeeded", "failed", "cancelled"])
@pytest.mark.parametrize("save_failed", [False, True])
@pytest.mark.parametrize("context_failed", [False, True])
@pytest.mark.parametrize("inline", [False, True])
async def test_stop_save_context_and_real_terminal_share_order_and_transaction(status, save_failed, context_failed, inline):
    calls = []
    active = False
    connection = object()

    @asynccontextmanager
    async def transaction():
        nonlocal active
        active = True
        try:
            yield connection
        finally:
            active = False
            calls.append("transaction.end")

    async def save(**kwargs):
        assert not active and kwargs["stop_evidence"] == "provider_stopped"
        calls.append("workspace.save")
        if save_failed:
            raise OSError("synthetic save failure")
        return {"state": "saved", "version_id": "private-version"}

    async def settle(conn, **kwargs):
        assert active and conn is connection
        assert kwargs["sdk_settlement"]["status"] == status
        assert kwargs["workspace_receipt"]["state"] == ("unsaved" if save_failed else "saved")
        calls.append("context.settle")
        if context_failed:
            raise RuntimeError("synthetic_context_failure")
        return {}

    async def release_saved_runtime(conn, **kwargs):
        assert active and conn is connection and "run.terminal" in calls
        assert kwargs["lease_id"] == "runtime-lease-a"
        calls.append("runtime.release")
    ports = ContinuationSettlementPorts(save, settle, release_saved_runtime)
    request = SimpleNamespace(tenant_id="tenant-a", run_id="run-a", attempt_id="attempt-a")
    envelope = await ports.prepare(
        request=request, workspace_lease=object(), stop_result=SimpleNamespace(status="stopped"),
        terminal_result={"status": status, "provider_session_final_sequence": 3, "sdk_settlement": {"mirror_complete": True, "received_result": True}},
    )
    if inline:
        envelope["runtime_lease"] = dict(tenant_id="tenant-a", run_id="run-a", attempt_id="attempt-a", user_id="user-a", lease_id="runtime-lease-a")
    async def lock(*args, **kwargs): return {"status": "running"}
    async def noop(*args, **kwargs): pass
    async def terminal(conn, **kwargs):
        assert active and conn is connection and calls[-1] == "context.settle"
        calls.append("run.terminal")
        return True
    async def cancelled(conn): return status == "cancelled"
    async def release(conn, **kwargs):
        assert active and "run.terminal" in calls
        calls.append("lease.release")
    authority = SimpleNamespace(is_cancel_requested=cancelled, complete=terminal, cancel=terminal, fail=terminal)
    service = WorkerResultCommitService(
        transaction_factory=transaction, lock_run=lock, reconciliation_claim_current=noop,
        materialize_answer=noop, persist_artifacts=noop, persist_skill_snapshots=noop,
        persist_assistant=noop, append_user_event=noop, append_hidden_event=noop,
        persist_failure_event=noop, public_failure_message=lambda _: "failed",
        prefers_cancelled=lambda _: status == "cancelled", settle_continuation=ports.commit,
        release_continuation_lease=ports.release,
    )
    result = SimpleNamespace(
        status="succeeded" if status == "succeeded" else "failed", result={}, artifacts=[],
        executor_payload={"continuation_settlement": envelope},
    )
    command = WorkerResultCommitCommand(
        payload=SimpleNamespace(tenant_id="tenant-a", run_id="run-a"), result=result,
        result_payload={}, artifact_records=[], skill_snapshot={}, terminal_event_kwargs={},
        attempt_id="attempt-a", trace_id="trace-a",
    )
    if context_failed:
        with pytest.raises(RuntimeError, match="synthetic_context_failure"):
            await service.commit(command, attempt_authority=authority, capabilities=None, release_runtime_lease=release)
        assert calls == ["workspace.save", "context.settle", "transaction.end"]
    else:
        outcome = await service.commit(command, attempt_authority=authority, capabilities=None, release_runtime_lease=release)
        assert outcome.status == status
        assert calls == ["workspace.save", "context.settle", "run.terminal", *(["runtime.release"] if inline else []), "lease.release", "transaction.end"]


@pytest.mark.asyncio
async def test_failed_stop_cannot_save_or_settle_and_wrong_scope_cannot_commit():
    async def unexpected(*args, **kwargs): raise AssertionError("must not execute")
    ports = ContinuationSettlementPorts(unexpected, unexpected)
    with pytest.raises(RuntimeError, match="continuation_writer_unverified"):
        await ports.prepare(request=object(), workspace_lease=object(), stop_result=SimpleNamespace(status="failed"), terminal_result={})
    with pytest.raises(ValueError, match="continuation_settlement_scope_invalid"):
        await ports.commit(object(), tenant_id="tenant-a", run_id="run-a", attempt_id="attempt-a", settlement={"tenant_id": "tenant-b"})


@pytest.mark.asyncio
@pytest.mark.parametrize("mirror_error", [False, True])
async def test_sdk_cancellation_retains_result_final_sequence_and_mirror_error(monkeypatch, tmp_path, mirror_error):
    import sys
    from tests.test_claude_agent_sdk_runner import _fake_sdk, _settings
    from app.executors.claude_agent_sdk_runner import run_claude_agent_sdk

    captured = {}
    sdk = _fake_sdk(captured, hook_invocations=[], emit_answer=False)
    started = asyncio.Event()
    interrupted = asyncio.Event()
    base = sdk.ClaudeSDKClient

    class Client(base):
        async def interrupt(self): interrupted.set()
        async def receive_messages(self):
            started.set()
            await interrupted.wait()
            async for message in super().receive_messages():
                yield message
            if mirror_error:
                yield sdk.MirrorErrorMessage(error="synthetic final mirror failure")

    class Store:
        accepted_final_sequence = 7
        async def load(self, key): return None
        async def append(self, key, entries): pass

    monkeypatch.setitem(sys.modules, "claude_agent_sdk", sdk)
    monkeypatch.setattr("app.executors.claude_agent_sdk_runner.get_settings", _settings)
    task = asyncio.create_task(run_claude_agent_sdk(
        prompt="synthetic", cwd=tmp_path, skill_id=None, session_id="provider-a",
        session_store=Store(), provider_session_resume_required=False, client_fn=Client,
    ))
    await asyncio.wait_for(started.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError) as caught:
        await asyncio.wait_for(task, 2)
    receipt = caught.value.sdk_settlement
    assert receipt.provider_final_sequence == 7
    assert receipt.sdk_settlement["received_result"] is True
    assert receipt.sdk_settlement["mirror_complete"] is (not mirror_error)
    if mirror_error:
        assert receipt.error == "claude_agent_sdk_provider_session_failed"
    else:
        assert receipt.terminal_reason == "end_turn"


@pytest.mark.asyncio
async def test_executor_deadline_wrapper_preserves_cancel_settlement():
    from app.runtime.sandbox.executor_app import _await_with_deadline
    started = asyncio.Event()
    async def runner():
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError as exc:
            exc.executor_terminal_result = {"provider_session_final_sequence": 4, "sdk_settlement": {"mirror_complete": False}}
            raise
    task = asyncio.create_task(_await_with_deadline(runner(), timeout_seconds=10))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError) as caught:
        await task
    assert caught.value.executor_terminal_result["provider_session_final_sequence"] == 4


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", ["Bash", "Agent", "WebFetch", "Write", "mcp__tenant-server__write"])
async def test_recovery_gate_denies_at_sdk_pretool_and_permission_boundaries(monkeypatch, tmp_path, tool):
    import sys
    from tests.test_claude_agent_sdk_runner import _fake_sdk, _settings, _subject
    from tests.support.claude_mcp import install_mcp_sessions
    from app.executors.claude_agent_sdk_runner import run_claude_agent_sdk
    install_mcp_sessions(monkeypatch)
    captured = {"permission_probes": [(tool, {}, None)]}
    sdk = _fake_sdk(captured, hook_invocations=[("PreToolUse", {"tool_name": tool, "tool_input": {}}, "call-a")], emit_answer=False)
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", sdk)
    monkeypatch.setattr("app.executors.claude_agent_sdk_runner.get_settings", _settings)
    subject = _subject(tool_name="write")
    subject["write_capable"] = True
    await run_claude_agent_sdk(
        prompt="synthetic", cwd=tmp_path, skill_id=None, tool_policy_subjects=[subject],
        execution_policy="sandbox_brokered", recovery_tool_gate={
            "state": "inspection", "allowed_tool_identities": [tool], "released_tool_identities": [],
        },
    )
    assert captured["permission_results"][0].behavior == "deny"
    output = captured["hook_results"][0][1]["hookSpecificOutput"]
    assert output["permissionDecision"] == "deny"
    assert output["permissionDecisionReason"] == "recovery_effect_unconfirmed"


@pytest.mark.asyncio
async def test_sdk_cancel_receipt_reaches_actual_executor_terminal_callback(monkeypatch, tmp_path):
    from app.runtime.sandbox import executor_app
    from app.runtime.sandbox.contracts import ExecutorTaskRequest
    from app.executors.claude_agent_sdk_runner import ClaudeAgentSdkRunResult
    from tests.test_sandbox_executor_app import task_payload, callback_ack, EXECUTOR_AUTH_TOKEN, TRUSTED_CALLBACK_BASE_URL

    started, terminal = asyncio.Event(), asyncio.Event()
    draining, release_drain = asyncio.Event(), asyncio.Event()
    receipts = []
    async def sdk(**kwargs):
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError as exc:
            draining.set()
            await release_drain.wait()
            exc.sdk_settlement = ClaudeAgentSdkRunResult(
                used_sdk=True, provider_final_sequence=9, terminal_reason="aborted_tools",
                error="claude_agent_sdk_provider_session_failed",
                sdk_settlement={"mirror_complete": False, "received_result": True, "error_code": "claude_agent_sdk_provider_session_failed"},
            )
            raise
    async def send(url, payload, token):
        if payload.get("terminal_result"):
            receipts.append(payload["terminal_result"])
            terminal.set()
        return callback_ack(payload)
    monkeypatch.setattr(executor_app, "run_claude_agent_sdk", sdk)
    monkeypatch.setattr(executor_app, "get_settings", lambda: SimpleNamespace(claude_agent_sdk_enabled=True))
    app = executor_app.create_executor_app(
        workspace_root=tmp_path, callback_sender=send, executor_auth_token=EXECUTOR_AUTH_TOKEN,
        expected_session_id="session-a", expected_run_id="run-a", expected_attempt_id="qat-attempt-a",
        trusted_callback_base_url=TRUSTED_CALLBACK_BASE_URL, heartbeat_interval_seconds=60,
    )
    async with app.router.lifespan_context(app):
        dispatch = next(route.endpoint for route in app.routes if route.path == "/v2/tasks")
        cancel = next(route.endpoint for route in app.routes if route.path.endswith("/cancel"))
        await dispatch(ExecutorTaskRequest.model_validate(task_payload()), executor_credential=EXECUTOR_AUTH_TOKEN)
        await asyncio.wait_for(started.wait(), 2)
        await cancel("run-a", "qat-attempt-a", executor_credential=EXECUTOR_AUTH_TOKEN)
        await asyncio.wait_for(draining.wait(), 2)
        await cancel("run-a", "qat-attempt-a", executor_credential=EXECUTOR_AUTH_TOKEN)
        release_drain.set()
        await asyncio.wait_for(terminal.wait(), 2)
    assert len(receipts) == 1
    assert receipts[0]["status"] == "cancelled"
    assert receipts[0]["provider_session_final_sequence"] == 9
    assert receipts[0]["sdk_terminal_reason"] == "aborted_tools"
    assert receipts[0]["sdk_settlement"]["mirror_complete"] is False
    assert receipts[0]["sdk_settlement"]["error_code"] == "claude_agent_sdk_provider_session_failed"


@pytest.mark.asyncio
@pytest.mark.parametrize("stop_status", ["stopped", "not_found", "failed", "wrong_identity"])
async def test_reconciler_stops_exact_provider_then_saves_before_terminal_conversion(monkeypatch, stop_status):
    from app import executor_reconciler as r
    from app.executors.base import ExecutorResult
    calls = []
    request = SimpleNamespace(tenant_id="tenant-a", workspace_id="workspace-a", user_id="user-a",
                              session_id="session-a", run_id="run-a", attempt_id="attempt-a", owner_generation=1)
    workspace = SimpleNamespace(workspace_host_path="synthetic-host-bind")
    lease = SimpleNamespace(container_id="container-a", provider="opensandbox")
    lease.model_copy = lambda **kwargs: lease
    terminal = {"status": "failed", "error_code": "synthetic_fault", "provider_session_final_sequence": 3, "sdk_settlement": {"mirror_complete": False}}
    context = {"adapter_name": "claude-agent-worker", "adapter_context": {}}
    payload = SimpleNamespace(attempt_id="attempt-a")
    @asynccontextmanager
    async def transaction(): yield object()
    async def claim(*args, **kwargs):
        calls.append("claim")
        return True
    class Provider:
        async def collect_workspace(self, *args):
            calls.append("deliverables.collect")
            return None
        async def stop(self, actual_lease, **kwargs):
            assert actual_lease is lease
            calls.append("provider.stop")
            return SimpleNamespace(status="stopped" if stop_status == "wrong_identity" else stop_status, container_id="other" if stop_status == "wrong_identity" else "container-a")
    async def save(**kwargs):
        assert calls[-1] == "provider.stop"
        assert kwargs["workspace_lease"] is workspace
        calls.append("workspace.save")
        return {"state": "saved", "version_id": "private-version"}
    def convert(**kwargs):
        assert calls[-1] == "workspace.save"
        calls.append("terminal.convert")
        return ExecutorResult(status="failed", adapter_version="1", executor_type="claude_agent_sdk", executor_version="1", capabilities={})
    async def storage_io(operation, **kwargs):
        kwargs.pop("on_abandoned", None)
        return operation(**kwargs)
    async def unexpected_settle(*args, **kwargs): raise AssertionError("not in terminal transaction yet")
    reconciliation_port = SimpleNamespace(has_sandbox_executor_reconciliation_claim=claim)
    monkeypatch.setattr(r, "_context_and_payload", lambda row: (context, terminal, payload))
    monkeypatch.setattr(r, "_reconciliation_request", lambda *args: request)
    monkeypatch.setattr(r, "SandboxWorkspaceManager", lambda: SimpleNamespace(prepare=lambda _: workspace))
    monkeypatch.setattr(r._sandbox_cleanup, "container_lease_from_persisted_row", lambda _: lease)
    monkeypatch.setattr(r, "_container_provider_for_lease", lambda _: Provider())
    monkeypatch.setattr(r, "transaction", transaction)
    async def checkpoint(*args, **kwargs): pass
    monkeypatch.setattr(r, "_record_reconciliation_stage", checkpoint)
    monkeypatch.setattr(r, "run_storage_io", storage_io)
    row = {"id": "lease-a", "run_id": "run-a", "provider": "opensandbox"}
    action = r._collect_workspace_and_convert_result(
        row, registry=SimpleNamespace(get=lambda _: SimpleNamespace(reconcile_sandbox_terminal=convert)),
        claim_token="claim-a", lease_port=reconciliation_port,
        continuation_ports=ContinuationSettlementPorts(save, unexpected_settle),
    )
    if stop_status in {"failed", "wrong_identity"}:
        with pytest.raises(r.SandboxReconciliationStopError):
            await action
        assert calls == ["deliverables.collect", "claim", "provider.stop"]
    else:
        result, _, _ = await action
        assert result.executor_payload["continuation_settlement"]["workspace_receipt"]["state"] == "saved"
        assert "continuation_settlement" not in result.result
        assert calls == ["deliverables.collect", "claim", "provider.stop", "workspace.save", "terminal.convert"]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["ephemeral", "persistent"])
async def test_inline_runtime_stops_and_saves_but_defers_lease_release_to_terminal_transaction(monkeypatch, tmp_path, mode):
    from app.runtime.sandbox import runtime as owner
    from app.runtime.sandbox.container_provider import FakeContainerProvider
    from tests.test_sandbox_runtime import (
        SandboxRuntime as TestSandboxRuntime,
        request,
    )
    calls = []
    class Provider(FakeContainerProvider):
        async def stage_workspace(self, *args): pass
        async def validate_for_dispatch(self, *args): pass
        async def collect_workspace(self, *args):
            calls.append("deliverables.collect")
            return None
        async def stop(self, *args, **kwargs):
            calls.append("provider.stop")
            return await super().stop(*args, **kwargs)
    async def execute(*args, **kwargs):
        return {"status": "completed", "run_id": "r", "provider_session_final_sequence": 3, "sdk_settlement": {"mirror_complete": True}}
    async def save(**kwargs):
        assert calls[-1] == "provider.stop"
        calls.append("workspace.save")
        return {"state": "saved", "version_id": "private-version"}
    async def unexpected(*args, **kwargs): raise AssertionError("terminal transaction has not started")
    monkeypatch.setattr(owner, "get_settings", lambda: SimpleNamespace(sandbox_callback_base_url="http://platform.test", sandbox_callback_token="synthetic", sandbox_lease_ttl_seconds=1800))
    runtime = TestSandboxRuntime(
        workspace_root=tmp_path.parent / ("r" + mode[0]), provider=Provider(executor_url="http://executor.test"),
        execute_task=execute, callback_token_resolver=lambda _: "synthetic",
        record_lease=lambda *args: "runtime-lease-a", release_lease=unexpected,
        continuation_ports=ContinuationSettlementPorts(save, unexpected),
    )
    result = await runtime.submit(request(tenant_id="t", workspace_id="w", user_id="u", session_id="s", run_id="r", attempt_id="a", sandbox_mode=mode))
    envelope = result.executor_response["continuation_settlement"]
    assert envelope["runtime_lease"] == dict(lease_id="runtime-lease-a", tenant_id="t", user_id="u", run_id="r", attempt_id="a")
    assert calls == ["deliverables.collect", "provider.stop", "workspace.save"]


@pytest.mark.asyncio
@pytest.mark.parametrize("evidence_state", ["confirmed", "missing", "query_failed", "sandbox_claim", "wrong_attempt", "wrong_invocation", "wrong_tool", "unrelated", "unauthorized"])
async def test_recovery_operation_queries_trusted_evidence_at_actual_sdk_boundaries(monkeypatch, tmp_path, evidence_state):
    import sys
    from tests.test_claude_agent_sdk_runner import _fake_sdk, _settings, _subject
    from tests.support.claude_mcp import install_mcp_sessions
    from app.executors.claude_agent_sdk_runner import run_claude_agent_sdk
    install_mcp_sessions(monkeypatch)
    identity = "mcp__tenant-server__write"
    captured = {"permission_probes": [(identity, {}, {"tool_use_id": "call-a"})]}
    sdk = _fake_sdk(captured, hook_invocations=[("PreToolUse", {"tool_name": identity, "tool_input": {}}, "call-a")], emit_answer=False)
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", sdk)
    monkeypatch.setattr("app.executors.claude_agent_sdk_runner.get_settings", _settings)
    subject = _subject(tool_name="write")
    subject["write_capable"] = True
    if evidence_state == "unauthorized":
        subject["identity_authorized"] = False
    calls = []
    async def query(**kwargs):
        calls.append(kwargs)
        if evidence_state == "query_failed":
            raise OSError("synthetic host query failure")
        evidence = dict(
            schema_version="ai-platform.recovery-operation-evidence.v1", decision="allow",
            evidence_kind="platform_receipt", operation_key="rop_" + "a" * 32,
            permission_basis="retry_not_effective", verified_outcome="not_effective",
            **{key: kwargs[key] for key in ("run_id", "attempt_id", "tool_identity", "invocation_id")},
        )
        if evidence_state == "missing":
            return {}
        if evidence_state == "sandbox_claim":
            evidence["evidence_kind"] = "model_text"
        if evidence_state == "wrong_attempt":
            evidence["attempt_id"] = "another-attempt"
        if evidence_state == "wrong_invocation":
            evidence["invocation_id"] = "another-call"
        if evidence_state == "wrong_tool":
            evidence["tool_identity"] = "mcp__tenant-server__other"
        if evidence_state == "unrelated":
            evidence["operation_key"] = ""
        return evidence
    async def acknowledged(*args): return True
    await run_claude_agent_sdk(
        prompt="synthetic", cwd=tmp_path, skill_id=None, tool_policy_subjects=[subject],
        run_id="run-a", attempt_id="attempt-a", on_capability_evidence=acknowledged,
        on_agent_event=acknowledged, execution_policy="sandbox_brokered", recovery_tool_gate={
            "state": "inspection", "allowed_tool_identities": [identity], "released_tool_identities": [],
        }, recovery_tool_gate_query=query,
    )
    expected = "allow" if evidence_state == "confirmed" else "deny"
    assert captured["permission_results"][0].behavior == expected
    assert captured["hook_results"][0][1]["hookSpecificOutput"]["permissionDecision"] == expected
    assert len(calls) == 2
    assert all(call["run_id"] == "run-a" and call["attempt_id"] == "attempt-a" and call["invocation_id"] == "call-a" for call in calls)


@pytest.mark.asyncio
async def test_executor_cancel_before_supervisor_start_publishes_one_receipt_without_execution(tmp_path):
    from app.runtime.sandbox.executor_app import create_executor_app
    from app.runtime.sandbox.contracts import ExecutorTaskRequest
    from tests.test_sandbox_executor_app import task_payload, callback_ack, EXECUTOR_AUTH_TOKEN, TRUSTED_CALLBACK_BASE_URL
    terminal = asyncio.Event()
    receipts = []
    async def unexpected(*args): raise AssertionError("queued cancellation must not enter the runner")
    async def send(url, payload, token):
        if payload.get("terminal_result"):
            receipts.append(payload["terminal_result"])
            terminal.set()
        return callback_ack(payload)
    app = create_executor_app(
        workspace_root=tmp_path, executor_runner=unexpected, callback_sender=send,
        executor_auth_token=EXECUTOR_AUTH_TOKEN, expected_session_id="session-a",
        expected_run_id="run-a", expected_attempt_id="qat-attempt-a",
        trusted_callback_base_url=TRUSTED_CALLBACK_BASE_URL,
    )
    async with app.router.lifespan_context(app):
        dispatch = next(route.endpoint for route in app.routes if route.path == "/v2/tasks")
        cancel = next(route.endpoint for route in app.routes if route.path.endswith("/cancel"))
        await dispatch(ExecutorTaskRequest.model_validate(task_payload()), executor_credential=EXECUTOR_AUTH_TOKEN)
        await cancel("run-a", "qat-attempt-a", executor_credential=EXECUTOR_AUTH_TOKEN)
        await asyncio.wait_for(terminal.wait(), 2)
    assert len(receipts) == 1 and receipts[0]["status"] == "cancelled"


@pytest.mark.asyncio
async def test_recovery_evidence_can_unlock_the_same_attempt_without_changing_authorization(monkeypatch, tmp_path):
    import sys
    from tests.test_claude_agent_sdk_runner import _fake_sdk, _settings, _subject
    from tests.support.claude_mcp import install_mcp_sessions
    from app.executors.claude_agent_sdk_runner import run_claude_agent_sdk
    install_mcp_sessions(monkeypatch)
    identity = "mcp__tenant-server__write"
    captured = {}
    hook = ("PreToolUse", {"tool_name": identity, "tool_input": {}}, "call-a")
    sdk = _fake_sdk(captured, hook_invocations=[hook, hook], emit_answer=False)
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", sdk)
    monkeypatch.setattr("app.executors.claude_agent_sdk_runner.get_settings", _settings)
    subject = _subject(tool_name="write")
    subject["write_capable"] = True
    gate = {"state": "inspection", "allowed_tool_identities": [identity], "released_tool_identities": []}
    checks = 0
    async def query(**kwargs):
        nonlocal checks
        checks += 1
        if checks == 1:
            return {}
        return dict(schema_version="ai-platform.recovery-operation-evidence.v1", decision="allow",
                    evidence_kind="reconciliation", operation_key="rop_" + "b" * 32,
                    permission_basis="retry_not_effective", verified_outcome="not_effective",
                    **{key: kwargs[key] for key in ("run_id", "attempt_id", "tool_identity", "invocation_id")})
    async def acknowledged(*args): return True
    await run_claude_agent_sdk(
        prompt="synthetic", cwd=tmp_path, skill_id=None, tool_policy_subjects=[subject],
        run_id="run-a", attempt_id="attempt-a", execution_policy="sandbox_brokered",
        recovery_tool_gate=gate, recovery_tool_gate_query=query,
        on_capability_evidence=acknowledged, on_agent_event=acknowledged,
    )
    assert [item[1]["hookSpecificOutput"]["permissionDecision"] for item in captured["hook_results"]] == ["deny", "allow"]
    assert checks == 2 and gate["released_tool_identities"] == []
