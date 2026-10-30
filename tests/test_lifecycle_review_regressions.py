"""Regression windows from the independent lifecycle control review."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.execution.api import ContinuationSettlementPorts, RecoveryOperationError


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["conversion", "terminal_database"])
async def test_reconciler_retry_after_stop_preserves_success_and_nonempty_attachments(monkeypatch, tmp_path, failure):
    from app import executor_reconciler as r
    from app.executors.base import ExecutorResult
    request = SimpleNamespace(tenant_id="t", workspace_id="w", user_id="u", session_id="s",
                              run_id="r", attempt_id="a", owner_generation=2)
    workspace = SimpleNamespace(workspace_host_path=str(tmp_path / "workspace"))
    snapshot = tmp_path / ".ai-platform-artifact-snapshot-v1"
    snapshot.mkdir()
    (snapshot / "report.txt").write_text("complete delivery")
    lease = SimpleNamespace(container_id="container", provider="opensandbox")
    lease.model_copy = lambda **kwargs: lease
    terminal = {"status": "completed", "response_files": ["report.txt"], "sdk_settlement": {"mirror_complete": True}}
    context = {"adapter_name": "claude-agent-worker", "adapter_context": {}}
    row = {"id": "lease", "tenant_id": "t", "workspace_id": "w", "user_id": "u",
           "session_id": "s", "run_id": "r", "attempt_id": "a", "provider": "opensandbox",
           "runtime_container_id": "container", "lease_payload_json": {"owner_generation": 2}}
    calls = []
    class Provider:
        async def collect_workspace(self, *args):
            assert "stop" not in calls, "retry must not connect the killed sandbox"
            calls.append("collect")
            return snapshot
        async def stop(self, *args, **kwargs):
            calls.append("stop")
            return SimpleNamespace(status="stopped", container_id="container")
    async def save(**kwargs):
        calls.append("save")
        return {"state": "saved", "version_id": "private-version"}
    async def unused(*args, **kwargs): pass
    async def checkpoint(actual_row, *, stage, **kwargs):
        actual_row["lease_payload_json"]["terminal_reconciliation"] = deepcopy(stage)
    def convert(**kwargs):
        calls.append("convert")
        assert kwargs["terminal_result"]["status"] == "completed"
        assert (Path(kwargs["adapter_context"]["_artifact_workspace"]) / "report.txt").read_text() == "complete delivery"
        if failure == "conversion" and calls.count("convert") == 1:
            raise OSError("transient converter IO")
        return ExecutorResult(status="succeeded", adapter_version="1", executor_type="claude_agent_sdk",
                              executor_version="1", capabilities={}, result={"message": "complete"})
    @asynccontextmanager
    async def transaction(): yield object()
    async def claim(*args, **kwargs): return True
    async def io(operation, **kwargs):
        kwargs.pop("on_abandoned", None)
        return operation(**kwargs)
    lease_port = SimpleNamespace(has_sandbox_executor_reconciliation_claim=claim)
    monkeypatch.setattr(r, "_context_and_payload", lambda _: (context, terminal, SimpleNamespace(attempt_id="a")))
    monkeypatch.setattr(r, "_reconciliation_request", lambda *args: request)
    monkeypatch.setattr(r, "SandboxWorkspaceManager", lambda: SimpleNamespace(prepare=lambda _: workspace))
    monkeypatch.setattr(r._sandbox_cleanup, "container_lease_from_persisted_row", lambda _: lease)
    monkeypatch.setattr(r, "_container_provider_for_lease", lambda _: Provider())
    monkeypatch.setattr(r, "transaction", transaction)
    monkeypatch.setattr(r, "_record_reconciliation_stage", checkpoint)
    monkeypatch.setattr(r, "run_storage_io", io)
    async def reconcile():
        return await r._collect_workspace_and_convert_result(
            row, registry=SimpleNamespace(get=lambda _: SimpleNamespace(reconcile_sandbox_terminal=convert)),
            claim_token="claim", lease_port=lease_port,
            continuation_ports=ContinuationSettlementPorts(save, unused),
        )
    if failure == "conversion":
        with pytest.raises(OSError):
            await reconcile()
    else:
        first, _, _ = await reconcile()
        assert first.status == "succeeded"
        # The terminal transaction rolls back; only the separately committed host checkpoint survives.
    result, _, _ = await reconcile()
    assert result.status == "succeeded"
    assert calls == ["collect", "stop", "save", "convert", "convert"]
    request.owner_generation += 1
    with pytest.raises(RuntimeError, match="stage_scope_invalid"):
        await reconcile()


@pytest.mark.asyncio
async def test_cancel_during_pre_runner_callback_retains_explicit_undispatched_proof(tmp_path):
    from app.runtime.sandbox.executor_app import create_executor_app
    from app.runtime.sandbox.contracts import ExecutorTaskRequest
    from tests.test_sandbox_executor_app import task_payload, callback_ack, EXECUTOR_AUTH_TOKEN, TRUSTED_CALLBACK_BASE_URL
    callback_entered, terminal, release_callback = asyncio.Event(), asyncio.Event(), asyncio.Event()
    receipts = []
    async def runner(*args): raise AssertionError("SDK must not be entered")
    async def send(url, payload, token):
        if payload.get("terminal_result"):
            receipts.append(payload["terminal_result"])
            terminal.set()
            return callback_ack(payload)
        callback_entered.set()
        await release_callback.wait()
        return callback_ack(payload)
    app = create_executor_app(workspace_root=tmp_path, executor_runner=runner, callback_sender=send,
        executor_auth_token=EXECUTOR_AUTH_TOKEN, expected_session_id="session-a", expected_run_id="run-a",
        expected_attempt_id="qat-attempt-a", trusted_callback_base_url=TRUSTED_CALLBACK_BASE_URL)
    async with app.router.lifespan_context(app):
        dispatch = next(route.endpoint for route in app.routes if route.path == "/v2/tasks")
        cancel = next(route.endpoint for route in app.routes if route.path.endswith("/cancel"))
        await dispatch(ExecutorTaskRequest.model_validate(task_payload()), executor_credential=EXECUTOR_AUTH_TOKEN)
        await asyncio.wait_for(callback_entered.wait(), 2)
        await cancel("run-a", "qat-attempt-a", executor_credential=EXECUTOR_AUTH_TOKEN)
        release_callback.set()
        await asyncio.wait_for(terminal.wait(), 2)
    assert len(receipts) == 1
    assert receipts[0]["sdk_settlement"]["input_accepted"] is False
    assert receipts[0]["sdk_settlement"]["ack_unknown"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("race", ["live", "endpoint_404", "get_404", "write_conflict", "unowned_404", "foreign_terminal"])
@pytest.mark.parametrize("dispatch_wait", ["post", "probe", "existing_probe"])
async def test_dispatch_inflight_cancel_observes_same_attempt_and_hands_terminal_to_reconciler(monkeypatch, tmp_path, race, dispatch_wait):
    from app.runtime.sandbox import runtime as module
    from app.runtime.sandbox.container_provider import FakeContainerProvider
    from app.executors.base import RunExecutionOwner, ExecutorDispatchAccepted
    from app.execution.application.adapter_run import submit_run_until_cancelled
    from app.sandbox.domain.lease_errors import SandboxLeaseReleaseScopeMismatchError
    from tests.test_sandbox_runtime import request
    sent = asyncio.Event()
    calls = []
    rejected = False
    class Owner(RunExecutionOwner):
        async def stop(self, **kwargs):
            nonlocal rejected
            result = await super().stop(**kwargs)
            if race in {"unowned_404", "foreign_terminal"} and not rejected:
                assert result.quiescent is False
                rejected = True
            return result
    class Provider(FakeContainerProvider):
        async def executor_control_endpoint(self, *args):
            if race in {"endpoint_404", "unowned_404", "foreign_terminal"}:
                raise OSError("executor stopped after durable terminal ACK")
            return await super().executor_control_endpoint(*args)
        async def stage_workspace(self, *args): pass
        async def validate_for_dispatch(self, *args): pass
        async def stop(self, *args, **kwargs): raise AssertionError("reconciler owns stop/save after receipt")
    async def execute(*args, **kwargs):
        assert calls == ["durable-observation"]
        assert dispatch_wait != "existing_probe", "a persisted intent must never be posted again"
        if dispatch_wait == "probe":
            raise OSError("dispatch response lost before status probe")
        sent.set()
        await asyncio.Event().wait()  # Remote accepted, network response never arrives.
    async def pending_probe(*args):
        sent.set()
        await asyncio.Event().wait()
    async def unexpected(*args, **kwargs): raise AssertionError("no early release/save")
    async def cancel(*args, **kwargs):
        assert kwargs["run_id"] == "r"
        assert kwargs["attempt_id"] == "a"
        calls.append("cancel-sdk")
    async def status(*args, **kwargs):
        assert kwargs["run_id"] == "r"
        assert kwargs["attempt_id"] == "a"
        calls.append("observe-terminal")
        if race == "get_404":
            raise OSError("GET 404 after terminal ACK")
        return {"status": "cancelled", "run_id": "r", "attempt_id": "a",
                "terminal_result": {"status": "cancelled", "run_id": "r", "message": "cancelled",
                                    "error_code": "executor_cancelled", "error_message": "Task cancelled",
                                    "sdk_settlement": {"input_accepted": True, "mirror_complete": False}}}
    @asynccontextmanager
    async def transaction(): yield object()
    async def accepted(*args, **kwargs): calls.append("durable-observation")
    async def persist_intent(*args, **kwargs):
        calls.append("durable-observation")
        return {"status": "intent"}, dispatch_wait != "existing_probe"
    async def record_outcome(*args, **kwargs):
        return {"status": kwargs["outcome"]}
    async def receipt(*args, **kwargs):
        calls.append("durable-terminal")
        assert kwargs["tenant_id"] == "t"
        assert kwargs["run_id"] == "r"
        assert kwargs["attempt_id"] == "a"
        assert kwargs["lease_id"] == "lease"
        assert kwargs["executor_status"] == "cancelled"
        assert kwargs["terminal_result"]["run_id"] == "r"
        assert kwargs["terminal_result"]["status"] == "cancelled"
        assert kwargs["terminal_result"]["sdk_settlement"]["input_accepted"] is True
        if race == "write_conflict":
            raise SandboxLeaseReleaseScopeMismatchError("concurrent release")
    async def persisted(*args, **kwargs):
        calls.append("persisted-handoff")
        assert kwargs == dict(tenant_id="t", workspace_id="w", user_id="u", session_id="s",
                              run_id="r", attempt_id="a", lease_id="lease", owner_generation=1)
        if race == "unowned_404" and not rejected:
            return None
        return {"executor_status": "cancelled", "executor_terminal_json": {"status": "cancelled", "run_id": "foreign" if race == "foreign_terminal" and not rejected else "r",
                "message": "cancelled", "error_code": "executor_cancelled", "error_message": "Task cancelled"}}
    monkeypatch.setattr(module, "get_settings", lambda: SimpleNamespace(sandbox_callback_base_url="http://platform.test", sandbox_callback_token="synthetic", sandbox_lease_ttl_seconds=1800))
    monkeypatch.setattr(module, "transaction", transaction)
    runtime = module.SandboxRuntime(workspace_root=tmp_path.parent / race, provider=Provider(executor_url="http://executor.test"),
        execute_task=execute, callback_token_resolver=lambda _: "synthetic",
        lease_port=SimpleNamespace(
            new_lease_id=lambda: "lease",
            record_sandbox_executor_accepted=accepted,
            get_sandbox_executor_terminal_handoff=persisted,
        ),
        callback_lease_port=SimpleNamespace(record_sandbox_executor_terminal=receipt),
        dispatch_intents=SimpleNamespace(persist_intent=persist_intent, record_outcome=record_outcome),
        record_lease=lambda *args: "lease",
        release_lease=unexpected, continuation_ports=ContinuationSettlementPorts(unexpected, unexpected))
    runtime.executor_client = SimpleNamespace(cancel=cancel, get_status=status)
    if dispatch_wait != "post":
        runtime._probe_dispatch_status = pending_probe
    runtime._uses_default_lease_recorder = True
    runtime._record_runtime_lease = lambda *args, **kwargs: "lease"
    req = request(tenant_id="t", workspace_id="w", user_id="u", session_id="s", run_id="r", attempt_id="a",
                  reconciliation_context={"adapter_name": "claude-agent-worker"})
    class Adapter:
        async def submit_run(self, payload, *, execution_owner, **kwargs):
            result = await runtime.submit(req, execution_owner=execution_owner)
            assert result.status == "accepted"
            return ExecutorDispatchAccepted("r", "a", result.lease_id, result.provider)
    async def cancelled(): return sent.is_set()
    async def sink(**kwargs): pass
    result = await asyncio.wait_for(submit_run_until_cancelled(
        Adapter(), SimpleNamespace(run_id="r"), owner_factory=Owner, event_sink=sink,
        cancel_requested=cancelled, poll_interval_seconds=.001,
    ), 3)
    assert isinstance(result, ExecutorDispatchAccepted)
    assert rejected is (race in {"unowned_404", "foreign_terminal"})
    assert calls == {
        "live": ["durable-observation", "cancel-sdk", "observe-terminal", "durable-terminal"],
        "endpoint_404": ["durable-observation", "persisted-handoff"],
        "get_404": ["durable-observation", "cancel-sdk", "observe-terminal", "persisted-handoff"],
        "unowned_404": ["durable-observation", "persisted-handoff", "persisted-handoff"],
        "foreign_terminal": ["durable-observation", "persisted-handoff", "persisted-handoff"],
        "write_conflict": ["durable-observation", "cancel-sdk", "observe-terminal", "durable-terminal", "persisted-handoff"],
    }[race]


@pytest.mark.asyncio
@pytest.mark.parametrize("defect", [None, "generation", "expired", "released", "scope", "takeover", "terminal", "missing_started", "wrong_invocation"])
async def test_durable_started_completion_tail_is_exact_and_idempotent(defect):
    from app.bootstrap.recovery_operations import _completion_tail_authority
    from tests.test_recovery_operations import scenario
    s = await scenario()
    s.active.remove(s.source)
    now = datetime.now(timezone.utc)
    run = {**s.source.scope.mapping(), "id": s.source.run_id, "status": "running", "cancel_requested_at": now.isoformat()}
    attempt = dict(id=s.source.attempt_id, tenant_id=s.source.scope.tenant_id, run_id=s.source.run_id,
                   status="cancel_requested", owner_kind="queue_worker", owner_id="worker", owner_generation=2)
    lease = {**run, "run_id": s.source.run_id, "attempt_id": s.source.attempt_id, "status": "active",
             "released_at": None, "expires_at": (now + timedelta(seconds=60)).isoformat(),
             "lease_payload_json": {"owner_generation": 1}}
    if defect == "generation":
        attempt["owner_generation"] = 3
    if defect == "expired":
        lease["expires_at"] = (now - timedelta(seconds=1)).isoformat()
    if defect == "released":
        lease["released_at"] = now.isoformat()
    if defect == "scope":
        lease["session_id"] = "other"
    if defect == "takeover":
        attempt["owner_kind"] = "reconciler"
    if defect == "terminal":
        run["status"] = "cancelled"
    if defect == "missing_started":
        s.store.operations.clear()
    invocation = replace(s.source, invocation_id="different") if defect == "wrong_invocation" else s.source
    class Conn:
        async def execute(self, query, params):
            assert query.count("%s") == len(params)
            return SimpleNamespace(fetchall=self.rows)
        async def rows(self): return [{"run": run, "attempt": attempt, "lease": lease}]
    service = replace(s.service, authorize_completion_tail=_completion_tail_authority)
    if defect is None:
        for _ in range(2):
            await service.record_reported(Conn(), invocation=invocation, operation_key=s.operation_key, status="completed")
        assert s.store.operations[s.operation_key]["reported_status"] == "completed"
        assert s.store.operations[s.operation_key]["verified_outcome"] == "unknown"
        with pytest.raises(RecoveryOperationError, match="report_conflict"):
            await service.record_reported(Conn(), invocation=invocation, operation_key=s.operation_key, status="failed")
        with pytest.raises(RecoveryOperationError, match="authority_stale"):
            await service.query(Conn(), invocation=invocation)
        with pytest.raises(RecoveryOperationError, match="authority_stale"):
            await service.record_started(Conn(), invocation=invocation, tool_facts=s.facts, public_summary="new")
    else:
        with pytest.raises(RecoveryOperationError):
            await service.record_reported(Conn(), invocation=invocation, operation_key=s.operation_key, status="completed")


@pytest.mark.asyncio
async def test_production_summary_gap_keeps_two_started_facts_but_publishes_no_ambiguous_question():
    from app.bootstrap.recovery_operations import build_recovery_operations
    from app.execution.api import RecoveryInvocation, RecoveryOperationScope
    from tests.test_recovery_operations import MemoryOperations
    service, _ = build_recovery_operations()
    store = MemoryOperations()
    published = []
    async def publish(*args, **kwargs): published.append(kwargs)
    service = replace(service, persistence=store, publish_question=publish)
    class Conn:
        recovering = False
        async def execute(self, query, params):
            assert query.count("%s") == len(params)
            async def row():
                return {"frozen": {"recovery": {"state": "recovery_candidate"}} if self.recovering else {},
                        "subjects": [{"identity": "mcp__private-server__private-write", "public_tool_label": "创建条目", "write_capable": True}]}
            return SimpleNamespace(fetchone=row)
    conn = Conn()
    scope = RecoveryOperationScope("tenant", "workspace", "user", "session")
    sources = [RecoveryInvocation(scope, "source", "source-attempt", 1, name, "mcp__private-server__private-write")
               for name in ("private-native-a", "private-native-b")]
    for invocation in sources:
        receipt = await service.record_started(conn, invocation=invocation, tool_facts={"write_capable": True},
                                               public_summary="本轮已授权外部工具操作")
        await service.record_reported(conn, invocation=invocation, operation_key=receipt["operation_key"], status="completed")
    assert len(store.operations) == 2
    assert all(row["reported_status"] == "completed" for row in store.operations.values())
    conn.recovering = True
    for source in sources:
        current = replace(source, run_id="recovery", attempt_id="recovery-attempt", owner_generation=2)
        denied = await service.query(conn, invocation=current)
        assert denied["decision"] == "deny"
        assert denied["reason_code"] == "recovery_operation_public_object_metadata_missing"
        assert "question_id" not in denied
        with pytest.raises(RecoveryOperationError, match="public_object_metadata_missing"):
            await service.record_started(conn, invocation=current, tool_facts={}, public_summary="unsafe")
    assert published == []


@pytest.mark.asyncio
@pytest.mark.parametrize("option", ["o0", "o1", "o2", "o3"])
async def test_production_summary_gap_rejects_preexisting_question_answers(option):
    from app.bootstrap.recovery_operations import _current_authority
    from tests.test_recovery_operations import scenario
    s = await scenario()
    question = await s.service.query(None, invocation=s.current)
    class Conn:
        async def execute(self, query, params):
            async def row():
                return {"frozen": {"recovery": {"state": "recovery_candidate"}}, "subjects": []}
            return SimpleNamespace(fetchone=row)
    service = replace(s.service, authorize_current=_current_authority)
    with pytest.raises(RecoveryOperationError, match="public_object_metadata_missing"):
        await service.on_manual_answer(Conn(), tenant_id="tenant-a", user_id="user-a", run_id="recovery-run",
            attempt_id="recovery-attempt", input_id="synthetic-answer", question_id=question["question_id"],
            answers={"q0": option}, source="manual")
    assert s.store.operations[s.operation_key]["verified_outcome"] == "unknown"
    assert next(iter(s.store.bindings.values()))["authorization"] == "pending"


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal", ["succeeded", "failed", "cancelled"])
async def test_terminal_cas_passes_exact_transition_to_context_before_return(terminal):
    from app.runs.application.attempt_lifecycle import RunAttemptLifecycleService
    previous = dict(tenant_id="t", run_id="r", id="a", owner_generation=7, status="running", execution_spec_sha256="frozen")
    calls = []
    async def get(*args, **kwargs): return dict(previous)
    async def transition(*args, **kwargs):
        calls.append("terminal-cas")
        return {**previous, "owner_generation": 8, "status": terminal}
    async def finalize(conn, **kwargs):
        calls.append("context-finalize")
        assert kwargs["previous"] == previous
        assert kwargs["terminal"]["owner_generation"] == 8
    service = RunAttemptLifecycleService(
        SimpleNamespace(get_run_attempt=get, terminalize_run_attempt=transition),
        finalize_terminal_transition=finalize,
    )
    result = await service.terminalize(object(), tenant_id="t", run_id="r", attempt_id="a", status=terminal)
    assert result["owner_generation"] == 8
    assert calls == ["terminal-cas", "context-finalize"]


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["skill_staging", "model_wait"])
async def test_default_runner_cancel_before_sdk_reaches_unclaimed_context(monkeypatch, tmp_path, phase):
    from app.runtime.sandbox import executor_app
    from app.runtime.sandbox.contracts import ExecutorTaskRequest
    from tests.test_sandbox_executor_app import task_payload, callback_ack, EXECUTOR_AUTH_TOKEN, TRUSTED_CALLBACK_BASE_URL
    from tests.test_provider_unclaimed_continuation import UnclaimedConnection
    from app.context.infrastructure.provider_continuation import settle_provider_continuation
    entered, terminal = asyncio.Event(), asyncio.Event()
    receipts = []
    async def sdk(**kwargs):
        raise AssertionError("SDK input must remain undispatched")
    async def send(url, payload, token):
        if payload.get("terminal_result"):
            receipts.append(payload["terminal_result"])
            terminal.set()
        return callback_ack(payload)
    original = executor_app._SealableExecutorEventEmitter.__call__
    async def emit(self, event):
        if isinstance(event, executor_app._PlatformExecutionPhaseFact) and event.phase == phase:
            entered.set()
            await asyncio.Event().wait()
        return await original(self, event)
    monkeypatch.setattr(executor_app._SealableExecutorEventEmitter, "__call__", emit)
    monkeypatch.setattr(executor_app, "run_claude_agent_sdk", sdk)
    monkeypatch.setattr(executor_app, "get_settings", lambda: SimpleNamespace(claude_agent_sdk_enabled=True))
    app = executor_app.create_executor_app(workspace_root=tmp_path, callback_sender=send,
        executor_auth_token=EXECUTOR_AUTH_TOKEN, expected_session_id="session-a", expected_run_id="run-a",
        expected_attempt_id="qat-attempt-a", trusted_callback_base_url=TRUSTED_CALLBACK_BASE_URL)
    payload = task_payload()
    if phase == "skill_staging":
        payload["config"]["skill_ids"] = ["general-chat"]
    async with app.router.lifespan_context(app):
        dispatch = next(route.endpoint for route in app.routes if route.path == "/v2/tasks")
        cancel = next(route.endpoint for route in app.routes if route.path.endswith("/cancel"))
        await dispatch(ExecutorTaskRequest.model_validate(payload), executor_credential=EXECUTOR_AUTH_TOKEN)
        await asyncio.wait_for(entered.wait(), 2)
        await cancel("run-a", "qat-attempt-a", executor_credential=EXECUTOR_AUTH_TOKEN)
        await asyncio.wait_for(terminal.wait(), 2)
    sdk_receipt = receipts[0]["sdk_settlement"]
    assert sdk_receipt["input_accepted"] is False and sdk_receipt["ack_unknown"] is False
    conn = UnclaimedConnection()
    result = await settle_provider_continuation(conn, tenant_id="tenant", run_id="run", attempt_id="attempt",
        final_sequence=None, workspace_receipt=None, stop_evidence="provider_stopped",
        sdk_settlement={**sdk_receipt, "owner_generation": 3})
    assert result["settlement_kind"] == "unclaimed_unaccepted"


@pytest.mark.asyncio
@pytest.mark.parametrize("defect", [None, "schema_version", "run_id", "attempt_id", "tool_identity", "invocation_id", "allow"])
async def test_recovery_metadata_denial_validates_identity_at_both_sdk_hooks(monkeypatch, tmp_path, defect):
    import sys
    from tests.test_claude_agent_sdk_runner import _fake_sdk, _settings, _subject
    from tests.support.claude_mcp import install_mcp_sessions
    from app.executors.claude_agent_sdk_runner import run_claude_agent_sdk
    install_mcp_sessions(monkeypatch)
    identity = "mcp__tenant-server__write"
    captured = {"permission_probes": [(identity, {}, {"tool_use_id": "call-a"})]}
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", _fake_sdk(captured,
        hook_invocations=[("PreToolUse", {"tool_name": identity, "tool_input": {}}, "call-a")], emit_answer=False))
    monkeypatch.setattr("app.executors.claude_agent_sdk_runner.get_settings", _settings)
    subject = _subject(tool_name="write")
    subject["write_capable"] = True
    async def query(**kwargs):
        evidence = dict(schema_version="ai-platform.recovery-operation-evidence.v1",
            decision="deny", reason_code="recovery_operation_public_object_metadata_missing",
            **{key: kwargs[key] for key in ("run_id", "attempt_id", "tool_identity", "invocation_id")})
        if defect == "allow":
            evidence.update(decision="allow", evidence_kind="platform_receipt",
                            permission_basis="retry_not_effective", verified_outcome="not_effective")
        elif defect:
            evidence[defect] = "forged"
        return evidence
    await run_claude_agent_sdk(prompt="synthetic", cwd=tmp_path, skill_id=None, tool_policy_subjects=[subject],
        run_id="run-a", attempt_id="attempt-a", execution_policy="sandbox_brokered",
        recovery_tool_gate={"state": "inspection", "allowed_tool_identities": [identity], "released_tool_identities": []},
        recovery_tool_gate_query=query)
    permission = captured["permission_results"][0]
    hook = captured["hook_results"][0][1]["hookSpecificOutput"]
    assert permission.behavior == hook["permissionDecision"] == "deny"
    if defect is None:
        assert "缺少可安全展示的操作对象摘要" in permission.message
        assert "缺少可安全展示的操作对象摘要" in hook["permissionDecisionReason"]
    else:
        assert permission.message == hook["permissionDecisionReason"] == "recovery_effect_unconfirmed"
