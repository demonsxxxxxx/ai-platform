"""Full-schema cancellation receipt through the production worker reconciliation entry."""
# ruff: noqa: F401,F811 -- imported shared pytest fixture.
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from psycopg.types.json import Jsonb

from app import executor_reconciler as reconciler
from app import worker
from app.bootstrap import worker_run_preparation as preparation
from app.bootstrap import executor_terminal_reconciliation as reconciliation_entry
from app.required_tool_contract import TOOL_INVOCATION_EVIDENCE_KEY
from app.worker_principal_authority import _payload_identity
from app.bootstrap.context import configure_context_services
from app.bootstrap import conversation_continuation as continuation_composition
from app.bootstrap.conversation_continuation import build_continuation_settlement_ports
from app.bootstrap.run_attempt_lifecycle import build_run_attempt_lifecycle_service
from app.bootstrap.run_lifecycle import build_run_lifecycle_service
from app.context.domain.conversation_recovery import make_provider_coverage_authority_receipt
from app.context.domain.provider_sessions import ProviderSessionScope
from app.context.infrastructure import provider_epochs
from app.context.infrastructure.provider_continuation import get_session_continuation
from app.execution.application.executor_reconciliation import sandbox_reconciliation_payload
from app.execution.api import sandbox_failure_prefers_cancelled
from app.executors.base import project_execution_spec_to_run_payload
from app.executors.claude_agent_worker import ClaudeAgentWorkerAdapter
from app.executors.registry import AdapterRegistry
from app.platform.postgres import sandbox_leases
from app.runs.application.worker_dispatch_admission import WorkerAuthorizedDispatchCandidate
from app.runs.application.worker_capability_admission import WorkerCapabilityAuthorization
from app.runs.api import RunTerminalizationProgress
from app.runs.infrastructure import postgres as runs_postgres
from app.runtime.sandbox.workspace_manager import SandboxWorkspaceManager
from app.streaming.infrastructure.run_events_postgres import append_event
from app.identity.infrastructure.audit_postgres import append_audit_log
from tests.test_run_attempt_repository import _execution_spec
from tests.test_sandbox_persistence import executor_acceptance_database
from tests.test_reconciled_cancel_commit import historical_image_lease_binding


@pytest.mark.asyncio
@pytest.mark.parametrize("lose_terminal_cas", [False, True])
@pytest.mark.parametrize("terminal_kind", ["cancelled", "mcp_unknown", "succeeded", "failed"])
async def test_cancelled_checkpoint_settles_context_in_real_worker_and_next_native_admission(
    executor_acceptance_database, monkeypatch, tmp_path, lose_terminal_cas, terminal_kind, operator_fault=None, with_image=False, lose_checkpoint_claim=False,
):
    conn = executor_acceptance_database
    configure_context_services()
    await conn.execute(Path("app/context/infrastructure/conversation_recovery_schema.sql").read_text())
    await conn.execute("update sandbox_leases set status='released',released_at=now()")
    await conn.execute("""insert into model_gateway_revisions
        (revision,base_url,api_key_ciphertext,key_fingerprint,active,created_by)
        values(1,'https://synthetic.invalid',decode('00','hex'),'0000000000000000',true,'user-a')""")
    await conn.execute("""update runs set status='queued',session_generation=1,
        model_id='model-a',model_value='model-a',model_gateway_revision=1,auto_compact_window=100000,max_output_tokens=8192,
        input_json='{"executor_type":"claude-agent-worker"}' where id='run-a'""")
    scope_map = dict(tenant_id="tenant-a", workspace_id="workspace-a", user_id="user-a", session_id="session-a", agent_id="agent-a")
    scope = ProviderSessionScope(**scope_map)
    await provider_epochs.claim_provider_lineage(conn, scope=scope, run_id="run-a")
    coverage = await provider_epochs.read_provider_coverage(conn, scope=scope, run_id="run-a", session_generation=1)
    authority = make_provider_coverage_authority_receipt(coverage=coverage, scope=scope_map,
        through_session_generation=1, current_message_id="user-message")
    await conn.execute("insert into messages(id,tenant_id,session_id,run_id,role,content) values('user-message','tenant-a','session-a','run-a','user','synthetic input')")
    await conn.execute("insert into run_context_snapshots(id,tenant_id,workspace_id,user_id,session_id,run_id,conversation_authority_json) values('context-a','tenant-a','workspace-a','user-a','session-a','run-a',%s)", (Jsonb(authority),))
    await conn.execute(
        "update runs set context_snapshot_id='context-a',input_json=input_json || %s::jsonb where id='run-a'",
        (Jsonb({"context_snapshot_id": "context-a", "context_snapshot": {"context_snapshot_id": "context-a"}}),),
    )
    frozen = await provider_epochs.prepare_provider_epoch(conn, scope=scope, run_id="run-a", conversation_context=dict(
        source_sha256=coverage["source_sha256"],message_count=0,authority_schema_version=authority["schema_version"],
        current_message_id="user-message",messages=[],selected_message_count=0,selected_turn_count=0))
    spec = _execution_spec(schema_version="ai-platform.execution-spec.v2", model_gateway_revision=1,
        model_max_input_tokens=100000,model_max_output_tokens=8192,context_pack={"conversation_context":frozen})
    attempts = build_run_attempt_lifecycle_service()
    now = datetime.now(timezone.utc)
    started = await attempts.start_worker(conn, tenant_id="tenant-a",run_id="run-a",queue_attempt_id="queue-a",
        worker_id="worker-a",execution_spec=spec,queue_message_id="a"*64,
        last_heartbeat_at=now,lease_expires_at=now+timedelta(minutes=10))
    assert started["owner_generation"] == 4
    payload = replace(project_execution_spec_to_run_payload(spec, attempt_id=started["id"]), owner_generation=4)
    historical_request = SimpleNamespace(**{key:getattr(payload,key) for key in (
        "tenant_id","workspace_id","user_id","session_id","run_id","attempt_id")},sandbox_mode="ephemeral",browser_enabled=False)
    current_settings, historical_handle, historical_payload = historical_image_lease_binding(historical_request)
    monkeypatch.setattr("app.bootstrap.sandbox_cleanup.get_settings",lambda:current_settings)
    lease = await sandbox_leases.create_sandbox_lease(conn, **{key: getattr(payload,key) for key in (
        "tenant_id","workspace_id","user_id","session_id","run_id","attempt_id")}, trace_id="trace-a",
        sandbox_mode="ephemeral",provider="opensandbox",browser_enabled=False,ttl_seconds=1800,
        resource_limits_json={},user_visible_payload_json={},lease_payload_json={**historical_payload,"attempt_id":started["id"],
            "owner_generation":4,"callback_token_id":"cbt:run-a:"+started["id"]+":g4"},
        runtime_container_id="container-a",runtime_container_name=historical_handle["runtime_container_name"],runtime_executor_url="http://synthetic.invalid",
        runtime_workspace_container_path="/workspace",lease_id="runtime-lease")
    assert reconciler._sandbox_cleanup.container_lease_from_persisted_row(lease) is None
    callback_scope = dict(**scope_map,run_id="run-a",attempt_id=started["id"],provider_session_id=frozen["provider_session_id"],subpath=None)
    await provider_epochs.callback_provider_epoch(conn, **callback_scope,action="load",entries=[],expected_sequence=None)
    entries = [{"type": "user", "uuid": "synthetic-user", "message": {"role": "user", "content": "synthetic input"}},
               {"type": "assistant", "uuid": "synthetic-answer", "message": {"role": "assistant", "content": [{"type": "text", "text": "Synthetic answer"}]}}]
    if with_image:
        from app.context.application.provider_sessions import ProviderSessionUseCases
        from app.context.application import provider_sessions
        from app.execution.infrastructure.harness.claude.session_images import provider_session_image_fields
        from tests.test_provider_session_images_postgres import ImageStorage, image_entry
        from psycopg.pq import TransactionStatus
        image_storage = ImageStorage()
        async def unlocked_storage(operation, **kwargs):
            assert conn.info.transaction_status == TransactionStatus.IDLE
            return operation(**kwargs)
        image_service = ProviderSessionUseCases(provider_epochs.PostgresProviderEpochRepository(),
            image_fields=provider_session_image_fields, storage_factory=lambda: image_storage, storage_io=unlocked_storage)
        monkeypatch.setattr(provider_sessions, "_use_cases", image_service)
        entries[0] = {**image_entry(), "uuid": "synthetic-user"}
        request = dict(callback_scope, action="append", entries=entries, expected_sequence=1)
        async with conn.transaction():
            media = await image_service.prepare_callback_media(conn, **request)
        await image_service.transfer_callback_media(media)
        async with conn.transaction():
            await image_service.execute_callback(conn, **request, media=media)
    else:
        await provider_epochs.callback_provider_epoch(conn, **callback_scope,action="append",entries=entries,expected_sequence=1)
    requested_cancel = terminal_kind in {"cancelled", "mcp_unknown"}
    expected_status = "cancelled" if requested_cancel else terminal_kind
    pending_generation = 5 if requested_cancel else 4
    if requested_cancel:
        cancel = runs_postgres.PostgresRunCancellationPersistence(attempt_lifecycle=attempts,append_event=append_event,
            append_audit_log=append_audit_log,list_active_sandbox_leases=lambda *args,**kwargs: None)
        intent = await cancel.begin_owner_request(conn,tenant_id="tenant-a",run_id="run-a",owner_user_id="user-a")
        assert intent is not None
        assert (await attempts.get(conn,tenant_id="tenant-a",run_id="run-a",attempt_id=started["id"]))["owner_generation"] == 5
    unknown_effect = terminal_kind == "mcp_unknown"
    expected_recovery = "recovery_candidate" if unknown_effect else "resumable"
    terminal = dict(run_id="run-a",status="cancelled",message="cancelled",error_code="executor_cancelled",error_message="Task cancelled",sdk_used=True,
        provider_session_final_sequence=2,response_files=[],sdk_settlement=dict(owner_generation=4,mirror_complete=True,
            input_accepted=True,received_result=True,ack_unknown=False,external_effect_unknown=False))
    if not requested_cancel:
        terminal.update(status="completed" if terminal_kind == "succeeded" else terminal_kind,
                        message="Synthetic answer" if terminal_kind == "succeeded" else "Failed",
                        error_code="synthetic_failure" if terminal_kind == "failed" else "")
    if terminal_kind == "succeeded":
        terminal[TOOL_INVOCATION_EVIDENCE_KEY] = []
    if unknown_effect:
        terminal.update(status="failed",error_code="stream_delivery_rejected",error_message="Execution delivery failed",
            runtime_diagnostics=dict(schema_version="ai-platform.sdk-runtime-diagnostics.v1",
                error_code="stream_delivery_rejected",failure_source="sandbox_runtime",failure_stage="executor",
                sdk={"terminal_reason":"api_error","errors":["mcp_execution_outcome_unknown"]}))
        terminal["sdk_settlement"].update(error_code="mcp_execution_outcome_unknown",terminal_reason="api_error",
                                          external_effect_unknown=True)
    manager = SandboxWorkspaceManager(root=tmp_path / "w")
    # This is a durable host checkpoint; neither conversion nor release may reconnect its dead producer.
    context = dict(adapter_name="claude-agent-worker",run_payload=sandbox_reconciliation_payload(payload),
        adapter_context={"schema_version":"ai-platform.claude-agent-reconciliation-context.v1","workspace":str(tmp_path / "w")})
    binding = dict(tenant_id="tenant-a",run_id="run-a",attempt_id=started["id"],lease_id=lease["id"])
    await sandbox_leases.record_sandbox_executor_accepted(conn,**binding,reconciliation_context=context)
    await sandbox_leases.record_sandbox_executor_terminal(conn,**binding,executor_status=terminal["status"],terminal_result=terminal)
    claimed = await sandbox_leases.claim_sandbox_executor_reconciliations(conn,claim_token="claim-a",limit=10,stale_after_seconds=60)
    assert len(claimed) == 1
    row = claimed[0]
    request = reconciler._reconciliation_request(row,payload)
    saved = dict(schema_version="ai-platform.workspace-version.v1",state="saved",version_id="synthetic-version",
        scope={key:getattr(payload,key) for key in ("tenant_id","workspace_id","user_id","session_id","run_id","attempt_id")},
        file_count=1,total_bytes=26,source_stop_evidence="provider_stopped")
    if operator_fault is not None:
        monkeypatch.setattr(continuation_composition,"get_settings",lambda:SimpleNamespace(sandbox_workspace_root=tmp_path / "w"))
        saved = await continuation_composition._save_stopped_workspace(request=request,
            workspace_lease=manager.prepare(request),stop_evidence="provider_stopped")
    continuation = dict(**binding,final_sequence=2,workspace_receipt=saved,stop_evidence="provider_stopped",
        sdk_settlement=terminal["sdk_settlement"],external_effect_unknown=unknown_effect)
    continuation.pop("lease_id")
    stage = dict(schema_version="ai-platform.terminal-reconciliation.v1",scope=saved["scope"],owner_generation=4,
        container_id="container-a",artifact_workspace=None,terminal_result=terminal,continuation=continuation)

    @asynccontextmanager
    async def transaction():
        async with conn.transaction():
            yield conn

    async def noop(*args,**kwargs):
        pass

    class GoneProvider:
        async def stop(self,*args,**kwargs):
            raise AssertionError("stopped checkpoint cannot reconnect provider")
        async def collect_workspace(self,*args,**kwargs):
            raise AssertionError("stopped checkpoint cannot collect provider workspace")
        async def get_info(self,*args,**kwargs):
            raise AssertionError("stopped checkpoint cannot probe provider")

    monkeypatch.setattr(reconciler,"transaction",transaction)
    monkeypatch.setattr(reconciler,"SandboxWorkspaceManager",lambda:manager)
    monkeypatch.setattr(reconciler,"_container_provider_for_lease",lambda _:GoneProvider())
    await reconciler._record_reconciliation_stage(row,claim_token="claim-a",stage=stage)
    registry = AdapterRegistry({"claude-agent-worker":ClaudeAgentWorkerAdapter()})
    if operator_fault is None:
        result, provider, handle = await reconciler._collect_workspace_and_convert_result(row,registry=registry,
            claim_token="claim-a",continuation_ports=build_continuation_settlement_ports())
        assert result.executor_payload["continuation_settlement"] == continuation
        assert result.status == ("succeeded" if terminal_kind == "succeeded" else "failed"), {
            "status": result.status, "error_code": result.result.get("error_code"),
        }
        assert sandbox_failure_prefers_cancelled(result) is (terminal_kind == "cancelled"), {
            "status": result.status, "error_code": result.result.get("error_code"),
            "provider": result.executor_payload.get("sandbox_provider"),
            "runtime_terminal_status": result.executor_payload.get("runtime_terminal_status"),
        }
    settlement_visits = []
    validation_visits = []
    publication_visits = []
    cancel_cas_visits = []
    original_settle = continuation_composition._settle_stopped_continuation

    async def observe_settlement(actual, **kwargs):
        # Observe the real Context write; do not replace its claim, prefix or generation checks.
        assert kwargs["sdk_settlement"]["status"] == expected_status
        recovery = await original_settle(actual, **kwargs)
        assert recovery["state"] == expected_recovery and recovery["owner_generation"] == pending_generation
        if kwargs.get("validate_only"):
            original_epoch = await (await actual.execute("select state,recovery_json from provider_session_epochs where id=%s",(frozen["provider_epoch_id"],))).fetchone()
            assert original_epoch == {"state":"active","recovery_json":None}
            validation_visits.append(recovery)
        else:
            settlement_visits.append(recovery)
        return recovery

    monkeypatch.setattr(continuation_composition, "_settle_stopped_continuation", observe_settlement)

    async def authorize(actual,**kwargs):
        return WorkerAuthorizedDispatchCandidate(kwargs["payload"],kwargs["locked_run"],
            _payload_identity(kwargs["payload"]),kwargs["trace_id"],
            authorization=WorkerCapabilityAuthorization(kwargs["payload"],object(),()))
    monkeypatch.setattr("app.bootstrap.worker_dispatch_admission.build_worker_locked_authorization",
        lambda **kwargs:SimpleNamespace(authorize=authorize))
    monkeypatch.setattr(preparation,"resolve_worker_dispatch_principal",noop)
    monkeypatch.setattr(preparation,"admit_v4_stream",noop)
    monkeypatch.setattr(reconciliation_entry,"publish_run_event",noop)
    monkeypatch.setattr("app.runs.application.execution_spec._dispatch_run_facts_loader",runs_postgres.load_worker_dispatch_run_facts)
    monkeypatch.setattr(worker.worker_execution_bootstrap,"enforce_worker_required_tool_completion",lambda result,**kwargs:result)
    original_ports = preparation.build_worker_attempt_lifecycle_ports
    lifecycle = build_run_lifecycle_service()
    async def cancel_run(actual,*,capabilities,**kwargs):
        assert len(settlement_visits) == 1, "cancel CAS reached without real Context settlement"
        pending_epoch = await (await actual.execute(
            "select state,recovery_json from provider_session_epochs where id=%s",
            (frozen["provider_epoch_id"],),
        )).fetchone()
        assert pending_epoch["state"] == "ready" and pending_epoch["recovery_json"]["state"] == expected_recovery
        pending_attempt = await attempts.get(actual, tenant_id="tenant-a", run_id="run-a", attempt_id=started["id"])
        assert pending_attempt["status"] == "cancel_requested" and pending_attempt["owner_generation"] == 5
        if unknown_effect:
            diagnostic = await (await actual.execute("select payload_json from run_diagnostics where run_id='run-a'")).fetchone()
            assert diagnostic is not None
            assert "stream_delivery_rejected" in str(diagnostic["payload_json"])
            assert "mcp_execution_outcome_unknown" in str(diagnostic["payload_json"])
        cancel_cas_visits.append(pending_epoch)
        if lose_terminal_cas:
            return RunTerminalizationProgress(completed=False,status=None)
        return await lifecycle.cancel_run(actual,**kwargs)
    def terminal_ports(*args):
        ports = original_ports(*args)
        async def complete_run(actual, **kwargs):
            if lose_terminal_cas:
                return RunTerminalizationProgress(completed=False, status=None)
            return await ports.complete_run(actual, **kwargs)
        async def fail_run(actual, **kwargs):
            if lose_terminal_cas:
                return RunTerminalizationProgress(completed=False, status=None)
            return await ports.fail_run(actual, **kwargs)
        return replace(ports, cancel_run=cancel_run, complete_run=complete_run, fail_run=fail_run)
    monkeypatch.setattr(preparation, "build_worker_attempt_lifecycle_ports", terminal_ports)
    from app.bootstrap.streaming import build_worker_v4_capabilities
    monkeypatch.setattr("app.bootstrap.streaming.get_settings", lambda: SimpleNamespace(
        ai_session_secret="synthetic-checkpoint-event-authority"))
    capabilities = replace(build_worker_v4_capabilities(None, transaction, lifecycle),
                           pending_admissions=SimpleNamespace(prepare_pending_authority_in_transaction=noop))
    if operator_fault is not None:
        assert await sandbox_leases.quarantine_sandbox_executor_reconciliation(conn,
            lease_id=row["id"],claim_token="claim-a",error="executor_reconciliation_runtime_handle_invalid")
        quarantined = await (await conn.execute("select * from sandbox_leases where id=%s",(row["id"],))).fetchone()
        if operator_fault == "ack":
            await conn.execute("update provider_session_append_receipts set batch_sha256=%s where epoch_id=%s",("f"*64,frozen["provider_epoch_id"]))
        if operator_fault == "workspace":
            monkeypatch.setattr(continuation_composition,"get_settings",lambda:SimpleNamespace(sandbox_workspace_root=tmp_path / "missing"))
        operator_scope = {**scope_map,"run_id":"run-a","attempt_id":started["id"]}
        if operator_fault == "scope":
            operator_scope["workspace_id"] = "foreign"
        async def published(*args,**kwargs):
            from psycopg.pq import TransactionStatus
            assert conn.info.transaction_status == TransactionStatus.IDLE
            assert (await (await conn.execute("select status from runs where id='run-a'")).fetchone())["status"] == "cancelled"
            publication_visits.append("after_outer_commit")
        monkeypatch.setattr(reconciler,"publish_run_event",published)
        monkeypatch.setattr(reconciliation_entry,"publish_run_event",published)
        async def repair():
            return await reconciler.repair_quarantined_executor_checkpoint(scope=operator_scope,
                lease_id=row["id"],invocation_id="operator-authorized-repair",owner_generation=4,
                v4_capabilities=capabilities,attempt_lifecycle=attempts,lifecycle=lifecycle,
                registry=registry,transaction_factory=transaction)
        if operator_fault != "success":
            with pytest.raises((ValueError,RuntimeError)):
                await repair()
            after = await (await conn.execute("select * from sandbox_leases where id=%s",(row["id"],))).fetchone()
            for key in ("status","executor_reconciliation_status","executor_reconciliation_error","updated_at","lease_payload_json","executor_terminal_json"):
                assert after[key] == quarantined[key], key
            assert publication_visits == []
            expected_visits = 1 if operator_fault == "cas" else 0
            assert len(settlement_visits) == len(cancel_cas_visits) == expected_visits
            if operator_fault == "cas":
                assert len(validation_visits) == 1
            current_epoch = await (await conn.execute("select state,recovery_json from provider_session_epochs where id=%s",(frozen["provider_epoch_id"],))).fetchone()
            assert current_epoch == {"state":"active","recovery_json":None}
            assert (await attempts.get(conn,tenant_id="tenant-a",run_id="run-a",attempt_id=started["id"]))["owner_generation"] == 5
            assert await (await conn.execute("select 1 from run_diagnostics where run_id='run-a'")).fetchone() is None
            return
        outcome = await repair()
        assert len(validation_visits) == 1 and publication_visits == ["after_outer_commit"]
        after = await (await conn.execute("select * from sandbox_leases where id=%s",(row["id"],))).fetchone()
        assert after["executor_terminal_json"] == terminal
        assert after["lease_payload_json"]["terminal_reconciliation"] == stage
        mark = after["lease_payload_json"]["checkpoint_repair"]
        assert mark["invocation_id"] == "operator-authorized-repair"
        assert mark["source_error"] == quarantined["executor_reconciliation_error"]
        assert datetime.fromisoformat(mark["source_quarantined_at"]) == quarantined["updated_at"]
        assert mark["writer_generation"] == 4 and mark["cancellation_generation"] == 5
        with pytest.raises(ValueError):
            await repair()
        assert (await (await conn.execute("select lease_payload_json->'checkpoint_repair' as mark from sandbox_leases where id=%s",(row["id"],))).fetchone())["mark"] == mark
    else:
        original_reconcile = reconciliation_entry.reconcile_executor_terminal_result
        outcomes = []
        async def observe_ingress(**kwargs):
            outcome = await original_reconcile(**kwargs)
            outcomes.append(outcome)
            return outcome
        monkeypatch.setattr(reconciler, "reconcile_executor_terminal_result", observe_ingress)
        original_finalize = sandbox_leases.finalize_stopped_executor_checkpoint
        async def observe_finalize(actual, **kwargs):
            assert len(settlement_visits) == 1
            pending = await (await actual.execute("select status from runs where id='run-a'")).fetchone()
            assert pending["status"] == expected_status
            if lose_checkpoint_claim:
                kwargs = {**kwargs, "claim_token": "foreign-claim"}
            return await original_finalize(actual, **kwargs)
        monkeypatch.setattr(sandbox_leases, "finalize_stopped_executor_checkpoint", observe_finalize)
        # Hand the existing checkpoint to the real scanner's fresh claim, not a helper-only commit.
        await conn.execute("update sandbox_leases set executor_reconciliation_status='pending',executor_reconciliation_claim_token=null,executor_reconciliation_claimed_at=null where id='runtime-lease'")
        assert await reconciler.reconcile_pending_executor_terminals_once(
            registry=registry, worker_id="reconciler-a", v4_capabilities=capabilities,
            attempt_lifecycle=attempts, lifecycle=lifecycle,
            continuation_ports=build_continuation_settlement_ports(),
        ) == 1
        if lose_checkpoint_claim:
            assert outcomes == []
            assert (await (await conn.execute("select status from runs where id='run-a'")).fetchone())["status"] == "running"
            current = await (await conn.execute("select state,recovery_json from provider_session_epochs where id=%s", (frozen["provider_epoch_id"],))).fetchone()
            assert current == {"state": "active", "recovery_json": None}
            lease_state = await (await conn.execute("select status,executor_reconciliation_status from sandbox_leases where id='runtime-lease'")).fetchone()
            assert lease_state["status"] == "active" and lease_state["executor_reconciliation_status"] != "finalized"
            attempt_state = await attempts.get(conn, tenant_id="tenant-a", run_id="run-a", attempt_id=started["id"])
            assert attempt_state["status"] == "cancel_requested" and attempt_state["owner_generation"] == pending_generation
            assert (await (await conn.execute("select count(*) as n from run_events where run_id='run-a' and event_type in ('run_succeeded','run_failed','run_cancelled')")).fetchone())["n"] == 0
            assert (await (await conn.execute("select count(*) as n from messages where run_id='run-a' and role='assistant'")).fetchone())["n"] == 0
            return
        assert len(outcomes) == 1
        outcome = outcomes[0]
    diagnostic = f"outcome={outcome!r}; Context settlements={len(settlement_visits)}; cancel CAS calls={len(cancel_cas_visits)}"
    assert len(settlement_visits) == 1 and len(cancel_cas_visits) == int(requested_cancel), diagnostic
    epoch = await (await conn.execute("select * from provider_session_epochs where id=%s",(frozen["provider_epoch_id"],))).fetchone()
    attempt = await attempts.get(conn,tenant_id="tenant-a",run_id="run-a",attempt_id=started["id"])
    if lose_terminal_cas:
        assert outcome.status == "skipped" and outcome.error_code == "stale_terminal_state", diagnostic
        assert (await (await conn.execute("select status from runs where id='run-a'")).fetchone())["status"] == "running"
        assert (await (await conn.execute("select count(*) as n from run_events where run_id='run-a' and event_type in ('run_succeeded','run_failed','run_cancelled')")).fetchone())["n"] == 0
        assert epoch["state"] == "active" and epoch["recovery_json"] is None
        assert attempt["status"] == ("cancel_requested" if requested_cancel else "running") and attempt["owner_generation"] == pending_generation
        assert await (await conn.execute("select 1 from run_diagnostics where run_id='run-a'")).fetchone() is None
        lease_state = await (await conn.execute("select status,executor_reconciliation_status from sandbox_leases where id='runtime-lease'")).fetchone()
        assert lease_state["status"] == "active" and lease_state["executor_reconciliation_status"] != "finalized"
        assert (await (await conn.execute("select count(*) as n from messages where run_id='run-a' and role='assistant'")).fetchone())["n"] == 0
        return
    assert outcome.status == expected_status, diagnostic
    assert attempt["status"] == expected_status and attempt["owner_generation"] == pending_generation + 1
    assert epoch["state"] == "ready" and epoch["recovery_json"]["state"] == expected_recovery
    assert epoch["recovery_json"]["owner_generation"] == pending_generation
    assert epoch["recovery_json"]["input_accepted"] is True
    assert epoch["recovery_json"]["final_sequence"] == 2 and epoch["recovery_json"]["workspace_saved"] is True
    assert all(epoch[key] is None for key in ("writer_run_id", "writer_attempt_id", "writer_owner_generation"))
    assert (await (await conn.execute("select count(*) as n from provider_session_append_receipts where epoch_id=%s", (epoch["id"],))).fetchone())["n"] == 1
    persisted = await (await conn.execute("select status,executor_reconciliation_status from sandbox_leases where id='runtime-lease'")).fetchone()
    assert persisted == dict(status="released",executor_reconciliation_status="finalized")
    if operator_fault is None:
        # A completed checkpoint is no longer claimable and cannot revisit its provider.
        assert await reconciler.reconcile_pending_executor_terminals_once(
            registry=registry, worker_id="reconciler-a", v4_capabilities=capabilities,
            attempt_lifecycle=attempts, lifecycle=lifecycle,
        ) == 0
        # Recover an old partial terminal commit using only its durable checkpoint.
        await conn.execute("update sandbox_leases set status='active',released_at=null,executor_reconciliation_status='pending',executor_reconciliation_claim_token=null,executor_reconciliation_claimed_at=null where id='runtime-lease'")
        def forbidden_provider(*args, **kwargs):
            raise AssertionError("terminal checkpoint recovery must not create a provider client")
        monkeypatch.setattr(reconciler, "_container_provider_for_lease", forbidden_provider)
        assert await reconciler.reconcile_pending_executor_terminals_once(
            registry=registry, worker_id="reconciler-a", v4_capabilities=capabilities,
            attempt_lifecycle=attempts, lifecycle=lifecycle,
        ) == 1
        assert (await (await conn.execute("select status,executor_reconciliation_status from sandbox_leases where id='runtime-lease'")).fetchone()) == dict(status="released", executor_reconciliation_status="finalized")
    projected = await get_session_continuation(conn,tenant_id="tenant-a",user_id="user-a",session_id="session-a")
    assert projected["state"] == expected_recovery and projected["action"] == "continue"
    if unknown_effect:
        assert projected["reason_code"] == "external_effect_unknown"
        persisted_result = (await (await conn.execute("select result_json from runs where id='run-a'")).fetchone())["result_json"]
        assert persisted_result["error_code"] == "stream_delivery_rejected"
        sdk = persisted_result["runtime_private_payload"]["sdk_settlement"]
        assert sdk["error_code"] == "mcp_execution_outcome_unknown" and sdk["external_effect_unknown"] is True
        assert sdk["terminal_reason"] == "api_error"
        from app.platform.public_payload import sanitize_public_payload
        assert "runtime_private_payload" not in sanitize_public_payload(persisted_result)
    await conn.execute("insert into runs(id,tenant_id,workspace_id,session_id,user_id,agent_id,status,execution_kind,session_generation) values('next-run','tenant-a','workspace-a','session-a','user-a','agent-a','queued','harness_chat',2)")
    await provider_epochs.claim_provider_lineage(conn,scope=scope,run_id="next-run")
    coverage = await provider_epochs.read_provider_coverage(conn,scope=scope,run_id="next-run",session_generation=2)
    from app.context.api import provider_session_verification_scope
    async with provider_session_verification_scope(transaction, tenant_id="tenant-a", run_id="next-run"):
        async with transaction():
            next_epoch = await provider_epochs.prepare_provider_epoch(conn,scope=scope,run_id="next-run",conversation_context=dict(
                source_sha256=coverage["source_sha256"],message_count=coverage["message_count"],
                authority_schema_version=coverage["authority_schema_version"],current_message_id="next-message",
                messages=[],selected_message_count=0,selected_turn_count=0))
    assert next_epoch["execution_mode"] == "native_resume"
    assert next_epoch["provider_session_id"] == frozen["provider_session_id"]
    assert next_epoch["source_epoch_id"] == frozen["provider_epoch_id"]

    if unknown_effect:
        assert next_epoch["recovery"]["state"] == "recovery_candidate"
        fields = continuation_composition.runtime_continuation_fields({"conversation_context":next_epoch}, [])
        assert fields["recovery_tool_gate"]["state"] == "inspection"
        assert "Bash" not in fields["recovery_tool_gate"]["allowed_tool_identities"]
        assert "Write" not in fields["recovery_tool_gate"]["allowed_tool_identities"]


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["success","cas","ack","scope","workspace"])
async def test_operator_quarantined_checkpoint_repair_is_atomic_one_shot(
    executor_acceptance_database,monkeypatch,tmp_path,fault,
):
    await test_cancelled_checkpoint_settles_context_in_real_worker_and_next_native_admission(
        executor_acceptance_database,monkeypatch,tmp_path,lose_terminal_cas=fault == "cas",
        terminal_kind="mcp_unknown",operator_fault=fault,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["success", "cas", "ack"])
async def test_operator_image_checkpoint_uses_unlocked_verified_media_and_atomic_repair(
    executor_acceptance_database, monkeypatch, tmp_path, fault,
):
    await test_cancelled_checkpoint_settles_context_in_real_worker_and_next_native_admission(
        executor_acceptance_database, monkeypatch, tmp_path, lose_terminal_cas=fault == "cas",
        terminal_kind="mcp_unknown", operator_fault=fault, with_image=True,
    )


@pytest.mark.asyncio
async def test_normal_scanner_checkpoint_rejects_lost_exact_claim_and_rolls_back_terminal(
    executor_acceptance_database, monkeypatch, tmp_path,
):
    await test_cancelled_checkpoint_settles_context_in_real_worker_and_next_native_admission(
        executor_acceptance_database, monkeypatch, tmp_path, lose_terminal_cas=False,
        terminal_kind="cancelled", lose_checkpoint_claim=True,
    )
