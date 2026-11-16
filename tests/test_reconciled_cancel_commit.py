"""Reconciled cancellation consumes the durable stopped checkpoint without reconnecting."""
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from app import executor_reconciler as owner


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", [None, "scope", "generation", "container", "stop"])
async def test_release_reuses_only_exact_stopped_checkpoint(monkeypatch, fault):
    scope = dict(tenant_id="tenant", workspace_id="workspace", user_id="user", session_id="session",
                 run_id="run", attempt_id="attempt")
    stage = dict(schema_version="ai-platform.terminal-reconciliation.v1", scope=dict(scope),
                 owner_generation=4, container_id="container", continuation={
                     **{key: scope[key] for key in ("tenant_id", "run_id", "attempt_id")},
                     "stop_evidence": "provider_stopped",
                 })
    row = dict(id="lease", runtime_container_id="container", **scope, lease_payload_json=dict(owner_generation=4, terminal_reconciliation=stage))
    if fault == "scope":
        stage["scope"]["attempt_id"] = "foreign"
    elif fault == "generation":
        stage["owner_generation"] = 5
    elif fault == "container":
        stage["container_id"] = "foreign"
    elif fault == "stop":
        stage["continuation"]["stop_evidence"] = "unverified"
    calls = []

    @asynccontextmanager
    async def transaction():
        yield object()

    async def finalize(*args, **kwargs):
        calls.append("release_and_finalize")
        assert kwargs["claim_token"] == "claim"
        return True

    async def release():
        async with transaction() as conn:
            await owner.finalize_stopped_executor_checkpoint(
                conn, lease_row=row, claim_token="claim", finalize=finalize,
            )
    if fault:
        with pytest.raises(RuntimeError, match="stage_scope_invalid"):
            await release()
        assert calls == []
    else:
        await release()
        assert calls == ["release_and_finalize"]


@pytest.mark.asyncio
async def test_cancel_result_context_settlement_rolls_back_when_terminal_cas_loses():
    from app.runs.api import WorkerResultCommitCommand, WorkerResultCommitService
    transactions = []

    @asynccontextmanager
    async def transaction():
        writes = []
        try:
            yield writes
        except BaseException:
            transactions.append(("rollback",writes))
            raise
        else:
            transactions.append(("commit",writes))

    async def lock(conn,**kwargs):
        return {"status":"running"}
    async def noop(*args,**kwargs):
        pass
    async def claim(*args,**kwargs):
        return True
    async def settle(conn,**kwargs):
        assert kwargs["settlement"]["sdk_settlement"]["status"] == "cancelled"
        conn.append("context_settled")
    async def release(*args,**kwargs):
        raise AssertionError("lost terminal CAS must not release")
    class Attempt:
        async def is_cancel_requested(self,conn):
            return True
        async def cancel(self,conn,**kwargs):
            conn.append("terminal_cas_lost")
            return False
    service = WorkerResultCommitService(transaction_factory=transaction,lock_run=lock,
        reconciliation_claim_current=claim,materialize_answer=noop,persist_artifacts=noop,
        persist_skill_snapshots=noop,persist_assistant=noop,append_user_event=noop,append_hidden_event=noop,
        persist_failure_event=noop,public_failure_message=lambda _:"safe",prefers_cancelled=lambda _:True,
        settle_continuation=settle,release_continuation_lease=release)
    command = WorkerResultCommitCommand(payload=SimpleNamespace(tenant_id="tenant",run_id="run"),
        result=SimpleNamespace(status="failed",result={},executor_payload={"continuation_settlement":{"sdk_settlement":{}}}),
        result_payload={},artifact_records=[],skill_snapshot={},terminal_event_kwargs={},attempt_id="attempt",trace_id="trace",
        reconciliation_lease_id="lease",reconciliation_claim_token="claim")
    outcome = await service.commit(command,attempt_authority=Attempt(),capabilities=object(),release_runtime_lease=release)
    assert outcome.status == "skipped"
    assert transactions == [("rollback",["context_settled","terminal_cas_lost"])]


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", ["cancelled", "unknown", "failed", "succeeded", "missing_claim", "missing_stop", "unsaved", "unlatched"])
async def test_latched_cancelled_failure_preserves_unknown_facts_without_reclassifying_other_results(scenario):
    from app.runs.api import WorkerResultCommitCommand, WorkerResultCommitService
    captured = []
    cancel_requested = scenario != "failed"
    result_status = "succeeded" if scenario == "succeeded" else "failed"
    runtime_status = "cancelled" if scenario == "cancelled" else result_status
    settlement = dict(tenant_id="tenant",run_id="run",attempt_id="attempt",stop_evidence="provider_stopped",
        workspace_receipt={"state":"saved"},sdk_settlement=dict(error_code="mcp_execution_outcome_unknown",
            terminal_reason="api_error",input_accepted=True,mirror_complete=True,received_result=True,
            ack_unknown=False,external_effect_unknown=True),external_effect_unknown=True)
    if scenario == "missing_stop":
        settlement["stop_evidence"] = "unverified"
    if scenario == "unsaved":
        settlement["workspace_receipt"] = {"state":"unsaved"}
    result_payload = {"message":"Execution delivery failed", "error_code":"stream_delivery_rejected",
                      "runtime_diagnostics":{"error_code":"stream_delivery_rejected","sdk":{"errors":["mcp_execution_outcome_unknown"]}}}

    @asynccontextmanager
    async def transaction():
        yield captured
    async def lock(*args,**kwargs):
        return {"status":"running","terminalization_target":None if scenario == "unlatched" else "cancel_requested"}
    async def noop(*args,**kwargs):
        pass
    async def claim(*args,**kwargs):
        return True
    async def settle(conn,**kwargs):
        captured.append(("settle",kwargs["settlement"]["sdk_settlement"]["status"]))
    class Attempt:
        async def is_cancel_requested(self,conn): return cancel_requested
        async def cancel(self,conn,**kwargs):
            captured.append(("cancel",kwargs["result_json"]))
            return True
        async def fail(self,conn,**kwargs):
            captured.append(("fail",kwargs["result_json"]))
            return True
        async def complete(self,conn,**kwargs):
            captured.append(("complete",kwargs["result_json"]))
            return True
    service = WorkerResultCommitService(transaction_factory=transaction,lock_run=lock,
        reconciliation_claim_current=claim,materialize_answer=noop,persist_artifacts=noop,
        persist_skill_snapshots=noop,persist_assistant=noop,append_user_event=noop,append_hidden_event=noop,
        persist_failure_event=noop,public_failure_message=lambda _:"Execution delivery failed",
        prefers_cancelled=lambda _:runtime_status == "cancelled",settle_continuation=settle,release_continuation_lease=noop)
    command = WorkerResultCommitCommand(payload=SimpleNamespace(tenant_id="tenant",run_id="run"),
        result=SimpleNamespace(status=result_status,result=result_payload,artifacts=[],executor_payload={
            "runtime_terminal_status":runtime_status,"continuation_settlement":settlement}),
        result_payload=result_payload,artifact_records=[],skill_snapshot={},terminal_event_kwargs={},attempt_id="attempt",trace_id="trace",
        reconciliation_lease_id=None if scenario == "missing_claim" else "lease",
        reconciliation_claim_token=None if scenario == "missing_claim" else "claim")
    outcome = await service.commit(command,attempt_authority=Attempt(),capabilities=object(),release_runtime_lease=noop)
    expected = "cancelled" if scenario in {"cancelled","unknown"} else "succeeded" if scenario == "succeeded" else "failed"
    assert outcome.status == expected
    assert captured[0] == ("settle",expected)
    if scenario == "unknown":
        persisted = captured[1][1]
        assert persisted["error_code"] == "stream_delivery_rejected"
        assert persisted["message"] == "Execution delivery failed"
        assert persisted["runtime_diagnostics"] == result_payload["runtime_diagnostics"]
        sdk = persisted["runtime_private_payload"]["sdk_settlement"]
        assert sdk["error_code"] == "mcp_execution_outcome_unknown" and sdk["external_effect_unknown"] is True
    if scenario == "succeeded":
        assert captured[1][0] == "complete"
        assert captured[1][1]["cancel_status"] == "cancel_requested_but_completed"


def historical_image_lease_binding(request):
    from app.runtime.sandbox.opensandbox_policy import internal_test_opensandbox_lease_labels
    from app.runtime.sandbox.opensandbox_policy import opensandbox_container_name
    old = SimpleNamespace(sandbox_container_provider="opensandbox",sandbox_security_profile="internal-test",
        deployment_environment="test",opensandbox_expected_network_mode="bridge",sandbox_egress_policy_enabled=False,
        opensandbox_executor_image="registry.example/executor@sha256:"+"a"*64,
        opensandbox_executor_image_digest="sha256:"+"a"*64,sandbox_runtime_subject="synthetic-runtime")
    current = SimpleNamespace(**{**vars(old),"opensandbox_executor_image":"registry.example/executor@sha256:"+"b"*64,
                                  "opensandbox_executor_image_digest":"sha256:"+"b"*64})
    labels = internal_test_opensandbox_lease_labels(request,old,executor_identity_labels={
        "ai-platform.executor.user":"10001:10001","ai-platform.executor.uid":"10001",
        "ai-platform.executor.gid":"10001","ai-platform.executor.identity_evidence":"authenticated-runtime-endpoint"},skill_mount_labels={})
    name = opensandbox_container_name(request.run_id,request.attempt_id)
    return current, dict(provider="opensandbox",runtime_container_name=name), dict(
        security_profile="internal-test",internal_test_lease_version="active-v1",labels=labels,
        container_id="container-a",container_name=name,executor_url="http://synthetic.invalid",
        workspace_container_path="/workspace",requested_image=old.opensandbox_executor_image,
        requested_image_digest=old.opensandbox_executor_image_digest)


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", [None,"no_checkpoint","scope","generation","container","stop","unsaved","labels"])
async def test_checkpoint_consumer_restores_only_verified_historical_image_identity(tmp_path,monkeypatch,fault):
    from app.execution.api import sandbox_reconciliation_payload
    from app.executors.base import RunPayload
    from app.executors.claude_agent_worker import ClaudeAgentWorkerAdapter
    from app.executors.registry import AdapterRegistry
    payload = RunPayload(tenant_id="tenant-a",workspace_id="workspace-a",user_id="user-a",session_id="session-a",
        run_id="run-a",attempt_id="attempt-a",agent_id="agent-a",skill_id=None,file_ids=[],input={},owner_generation=4,
        execution_kind="harness_chat",schema_version="ai-platform.run-payload.v2")
    request = SimpleNamespace(**{key:getattr(payload,key) for key in (
        "tenant_id","workspace_id","user_id","session_id","run_id","attempt_id")},sandbox_mode="ephemeral",browser_enabled=False)
    current,handle,persisted = historical_image_lease_binding(request)
    row = dict(id="lease-a",**vars(request),**handle,runtime_container_id="container-a",runtime_executor_url="http://synthetic.invalid",
        runtime_workspace_container_path="/workspace",runtime_handle_verified_at="synthetic-verified",
        lease_payload_json={**persisted,"attempt_id":"attempt-a","owner_generation":4},
        executor_terminal_json=dict(run_id="run-a",status="cancelled",error_code="executor_cancelled",message="cancelled",sdk_used=True),
        executor_reconciliation_context_json=dict(adapter_name="claude-agent-worker",run_payload=sandbox_reconciliation_payload(payload),
            adapter_context={"schema_version":"ai-platform.claude-agent-reconciliation-context.v1","workspace":str(tmp_path)}))
    scope = {key:getattr(payload,key) for key in ("tenant_id","workspace_id","user_id","session_id","run_id","attempt_id")}
    saved = dict(schema_version="ai-platform.workspace-version.v1",state="saved",scope=scope,version_id="version-a",
        file_count=1,total_bytes=26,source_stop_evidence="provider_stopped")
    stage = dict(schema_version="ai-platform.terminal-reconciliation.v1",scope=dict(scope),owner_generation=4,container_id="container-a",
        artifact_workspace=None,terminal_result=row["executor_terminal_json"],continuation=dict(
            tenant_id="tenant-a",run_id="run-a",attempt_id="attempt-a",stop_evidence="provider_stopped",workspace_receipt=saved))
    if fault != "no_checkpoint":
        row["lease_payload_json"]["terminal_reconciliation"] = stage
    if fault == "scope":
        stage["scope"]["user_id"] = "foreign"
    if fault == "generation":
        stage["owner_generation"] = 5
    if fault == "container":
        stage["container_id"] = "foreign"
    if fault == "stop":
        stage["continuation"]["stop_evidence"] = "unverified"
    if fault == "unsaved":
        saved["state"] = "unsaved"
    if fault == "labels":
        persisted["labels"]["ai-platform.executor.uid"] = "0"
    monkeypatch.setattr("app.bootstrap.sandbox_cleanup.get_settings",lambda:current)
    assert owner._sandbox_cleanup.container_lease_from_persisted_row(row) is None
    monkeypatch.setattr(owner,"SandboxWorkspaceManager",lambda:SimpleNamespace(prepare=lambda _:SimpleNamespace(workspace_host_path=str(tmp_path))))
    class GoneProvider:
        def __getattr__(self,name):
            raise AssertionError("checkpoint cannot contact provider: "+name)
    monkeypatch.setattr(owner,"_container_provider_for_lease",lambda _:GoneProvider())
    async def consume():
        return await owner._collect_workspace_and_convert_result(row,registry=AdapterRegistry({"claude-agent-worker":ClaudeAgentWorkerAdapter()}),claim_token="claim-a")
    if fault:
        with pytest.raises((RuntimeError,ValueError)):
            await consume()
    else:
        result,_,restored = await consume()
        assert result.executor_payload["continuation_settlement"] == stage["continuation"]
        assert restored.labels["ai-platform.executor.requested_image"].endswith("a"*64)


@pytest.mark.asyncio
async def test_operator_repair_rejects_sdk_settlement_disagreeing_with_original_receipt(monkeypatch):
    from dataclasses import replace
    from app.executors.base import project_execution_spec_to_run_payload
    from tests.test_run_attempt_repository import _execution_spec
    spec = _execution_spec(schema_version="ai-platform.execution-spec.v2",model_gateway_revision=1,
        model_max_input_tokens=100000,model_max_output_tokens=8192)
    payload = replace(project_execution_spec_to_run_payload(spec,attempt_id="attempt-a"),owner_generation=4)
    scope = {key:getattr(payload,key) for key in ("tenant_id","workspace_id","user_id","session_id","agent_id","run_id","attempt_id")}
    sdk = dict(error_code="mcp_execution_outcome_unknown",input_accepted=True,mirror_complete=True,
               received_result=True,ack_unknown=False,external_effect_unknown=True)
    terminal = dict(status="failed",error_code="stream_delivery_rejected",sdk_settlement={**sdk,"error_code":"different_original_failure"})
    stage = dict(terminal_result=terminal,continuation=dict(sdk_settlement=sdk,external_effect_unknown=True))
    row = dict(updated_at="synthetic",lease_payload_json={"terminal_reconciliation":stage})
    attempt = dict(id="attempt-a",status="cancel_requested",owner_kind="queue_worker",owner_generation=5,
        execution_spec_canonical_json=spec.canonical_json.decode(),execution_spec_sha256=spec.spec_sha256,
        execution_spec_json=spec.to_mapping())
    @asynccontextmanager
    async def transaction(): yield object()
    async def load_run(*args,**kwargs): return {**scope,"status":"running","terminalization_target":"cancel_requested","cancel_requested_at":"synthetic"}
    async def load_lease(*args,**kwargs): return row
    async def load_attempt(*args,**kwargs): return attempt
    async def forbidden_verify(**kwargs): raise AssertionError("inconsistent original SDK proof reached filesystem verification")
    @asynccontextmanager
    async def verification(*args, **kwargs):
        yield
    monkeypatch.setattr("app.context.api.provider_session_verification_scope", verification)
    monkeypatch.setattr(owner.runs_postgres,"get_run",load_run)
    monkeypatch.setattr(owner.sandbox_lease_repository,"get_quarantined_checkpoint_for_repair",load_lease)
    monkeypatch.setattr(owner,"_context_and_payload",lambda _:({"adapter_name":"claude-agent-worker"},terminal,payload))
    monkeypatch.setattr(owner,"_reconciliation_runtime_lease",lambda *args,**kwargs:object())
    monkeypatch.setattr("app.bootstrap.conversation_continuation.verify_checkpoint_workspace",forbidden_verify)
    with pytest.raises(ValueError,match="checkpoint_repair_receipt_invalid"):
        await owner.repair_quarantined_executor_checkpoint(scope=scope,lease_id="lease-a",invocation_id="authorized-one-shot",
            owner_generation=4,v4_capabilities=object(),attempt_lifecycle=SimpleNamespace(get=load_attempt,get_latest=load_attempt),
            lifecycle=object(),transaction_factory=transaction)


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", [None,"ack","workspace","other_writer"])
async def test_operator_context_preflight_verifies_native_proof_without_writes(fault):
    from app.context.infrastructure.provider_continuation import settle_provider_continuation
    from tests.test_conversation_recovery import SettlementConnection,saved_workspace,Cursor
    conn = SettlementConnection()
    conn.row["state"] = "active"
    receipt = saved_workspace()
    if fault == "ack":
        conn.receipts.clear()
    if fault == "workspace":
        receipt["total_bytes"] = -1
    if fault == "other_writer":
        original = conn.execute
        async def other(query,values=()):
            if "id<>%s and writer_run_id" in query:
                return Cursor(row={"id":"other"})
            return await original(query,values)
        conn.execute = other
    async def verify():
        return await settle_provider_continuation(conn,tenant_id="tenant",run_id="run",attempt_id="attempt",
            final_sequence=2,workspace_receipt=receipt,stop_evidence="provider_stopped",
            sdk_settlement=dict(owner_generation=3,writer_owner_generation=2,input_accepted=True,mirror_complete=True,ack_unknown=False),
            external_effect_unknown=True,validate_only=True)
    if fault:
        with pytest.raises(ValueError):
            await verify()
    else:
        assert (await verify())["state"] == "recovery_candidate"
    assert conn.updates == []


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal_status", ["succeeded", "failed", "cancelled"])
@pytest.mark.parametrize("claim_current", [True, False])
async def test_receipt_ingress_releases_checkpoint_inside_terminal_transaction(monkeypatch, terminal_status, claim_current):
    from app.bootstrap import executor_terminal_reconciliation as ingress
    from app.runs.api import WorkerOutcome

    scope = dict(tenant_id="tenant", workspace_id="workspace", user_id="user", session_id="session",
                 run_id="run", attempt_id="attempt")
    stage = dict(schema_version="ai-platform.terminal-reconciliation.v1", scope=scope,
                 owner_generation=4, container_id="container", continuation={
                     **{key: scope[key] for key in ("tenant_id", "run_id", "attempt_id")},
                     "stop_evidence": "provider_stopped",
                 })
    row = dict(id="lease", runtime_container_id="container", **scope,
               lease_payload_json=dict(owner_generation=4, terminal_reconciliation=stage))
    transactions = []
    active = None

    @asynccontextmanager
    async def transaction():
        nonlocal active
        active = []
        try:
            yield active
        except BaseException:
            transactions.append(("rollback", list(active)))
            raise
        else:
            transactions.append(("commit", list(active)))
        finally:
            active = None

    async def prepare(*args, **kwargs):
        return SimpleNamespace(adapter=object(), locked_run={"started_at": None})

    async def finalize(conn, **kwargs):
        assert conn is active and conn == ["terminal"]
        assert kwargs == dict(scope=scope, lease_id="lease", claim_token="claim", expected_stage=stage)
        if claim_current:
            conn.append("lease_finalized")
        return claim_current

    def build_completion(*args, release_runtime_lease, **kwargs):
        class Completion:
            async def complete(self, result, *, latency_ms):
                async with transaction() as conn:
                    conn.append("terminal")
                    await release_runtime_lease(conn, reason="run_" + terminal_status)
                return WorkerOutcome(terminal_status, "run")
        return Completion()

    monkeypatch.setattr(ingress, "prepare_worker_run", prepare)
    monkeypatch.setattr(ingress, "build_worker_terminal_completion_service", build_completion)
    monkeypatch.setattr(ingress, "restored_executor_reconciliation_queue_payload", lambda *args, **kwargs: ({}, "attempt"))
    monkeypatch.setattr(ingress.sandbox_leases, "finalize_stopped_executor_checkpoint", finalize)
    call = ingress.reconcile_executor_terminal_result(
        lease_row=row, result=SimpleNamespace(result={}), claim_token="claim", transaction_factory=transaction,
        v4_capabilities=object(), run_attempt_lifecycle=object(), run_lifecycle=object(),
    )
    if claim_current:
        outcome = await call
        assert outcome.status == terminal_status
        assert transactions == [("commit", ["terminal", "lease_finalized"])]
    else:
        with pytest.raises(RuntimeError, match="executor_reconciliation_claim_lost"):
            await call
        assert transactions == [("rollback", ["terminal"])]


@pytest.mark.asyncio
async def test_failed_completed_checkpoint_is_quarantined_without_provider_io(monkeypatch):
    row = {"id": "lease", "lease_payload_json": {"terminal_reconciliation": {"continuation": {}}}}
    calls = []
    async def quarantine(actual, **kwargs):
        assert actual is row and kwargs["claim_token"] == "claim"
        calls.append("quarantined")
    def forbidden(*args, **kwargs):
        raise AssertionError("completed checkpoint cannot revisit provider cleanup")
    monkeypatch.setattr(owner, "_quarantine_failed_reconciliation", quarantine)
    monkeypatch.setattr(owner._sandbox_cleanup, "container_lease_from_persisted_row", forbidden)
    await owner._finish_terminal_reconciliation_failure(
        row, claim_token="claim", error_code="provider_session_integrity_mismatch", logger=object(),
        v4_capabilities=object(), attempt_lifecycle=object(), lifecycle=object(), run_diagnostics=None,
    )
    assert calls == ["quarantined"]
