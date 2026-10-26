"""Predispatch failure through runtime, frozen Attempt, terminal CAS, and next admission."""
# ruff: noqa: F401,F811 -- shared PostgreSQL fixture is injected by name.
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from psycopg.types.json import Jsonb

from app.bootstrap.context import configure_context_services
from app.bootstrap.run_attempt_lifecycle import build_run_attempt_lifecycle_service
from app.bootstrap.run_lifecycle import build_run_lifecycle_service
from app.bootstrap.worker_attempt_lifecycle import build_worker_attempt_lifecycle_ports
from app.bootstrap.worker_execution_terminal import build_worker_execution_terminal_service
from app.context.domain.conversation_recovery import make_provider_coverage_authority_receipt
from app.context.domain.provider_sessions import ProviderSessionConflictError, ProviderSessionScope
from app.context.infrastructure import provider_epochs
from app.execution.api import WorkerAttemptLifecycle
from app.runtime.sandbox.container_provider import FakeContainerProvider
from app.runtime.sandbox.contracts import CallbackTargetValidationError, build_trusted_callback_target
from app.runtime.sandbox.runtime import SandboxRuntime
from tests.test_run_attempt_repository import _execution_spec
from tests.test_sandbox_persistence import executor_acceptance_database
from tests.test_sandbox_runtime import request


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", [None, "stale_generation", "terminal_cas"])
async def test_predispatch_failure_closes_only_unused_epoch_and_allows_next_admission(
    executor_acceptance_database, monkeypatch, fault,
):
    conn = executor_acceptance_database
    configure_context_services()
    await conn.execute(Path("app/context/infrastructure/conversation_recovery_schema.sql").read_text())
    # This shared fixture contains an inactive synthetic placeholder, not a provider process.
    await conn.execute("update sandbox_leases set status='released',released_at=now()")
    await conn.execute("update runs set status='queued',session_generation=1 where id='run-a'")
    scope_map = dict(tenant_id="tenant-a", workspace_id="workspace-a", user_id="user-a",
                     session_id="session-a", agent_id="agent-a")
    scope = ProviderSessionScope(**scope_map)
    await provider_epochs.claim_provider_lineage(conn, scope=scope, run_id="run-a")
    coverage = await provider_epochs.read_provider_coverage(conn, scope=scope, run_id="run-a", session_generation=1)
    authority = make_provider_coverage_authority_receipt(
        coverage=coverage, scope=scope_map, through_session_generation=1, current_message_id="user-message-a",
    )
    await conn.execute("insert into messages(id,tenant_id,session_id,run_id,role,content) values('user-message-a','tenant-a','session-a','run-a','user','synthetic input')")
    await conn.execute("insert into run_context_snapshots(id,tenant_id,workspace_id,user_id,session_id,run_id,conversation_authority_json) values('context-a','tenant-a','workspace-a','user-a','session-a','run-a',%s)", (Jsonb(authority),))
    await conn.execute(
        "update runs set context_snapshot_id='context-a',input_json=input_json || %s::jsonb where id='run-a'",
        (Jsonb({"context_snapshot_id": "context-a", "context_snapshot": {"context_snapshot_id": "context-a"}}),),
    )
    frozen = await provider_epochs.prepare_provider_epoch(
        conn, scope=scope, run_id="run-a", conversation_context=dict(
            source_sha256=coverage["source_sha256"], message_count=0,
            authority_schema_version=authority["schema_version"], current_message_id="user-message-a",
            messages=[], selected_message_count=0, selected_turn_count=0,
        ),
    )
    spec = _execution_spec(
        schema_version="ai-platform.execution-spec.v2", model_gateway_revision=1,
        model_max_input_tokens=100000, model_max_output_tokens=8192,
        context_pack={"conversation_context": frozen},
    )
    attempts = build_run_attempt_lifecycle_service()
    started = await attempts.start_worker(
        conn, tenant_id="tenant-a", run_id="run-a", queue_attempt_id="queue-a",
        worker_id="worker-a", execution_spec=spec,
    )
    assert started["owner_generation"] == 4
    runtime = SandboxRuntime(workspace_root="unused", provider=FakeContainerProvider())
    monkeypatch.setattr(runtime, "_trusted_callback_target", lambda _provider: build_trusted_callback_target("http://169.254.169.254"))
    with pytest.raises(CallbackTargetValidationError) as caught:
        await runtime.submit(request(**scope_map, run_id="run-a", attempt_id=started["id"],
                                     owner_generation=started["owner_generation"], provider_epoch_id=frozen["provider_epoch_id"]))
    receipt = caught.value.predispatch_continuation_settlement
    if fault == "stale_generation":
        receipt["sdk_settlement"]["owner_generation"] -= 1

    @asynccontextmanager
    async def transaction():
        async with conn.transaction():
            yield conn

    async def pending(*args, **kwargs):
        pass

    lifecycle = build_run_lifecycle_service()
    ports = build_worker_attempt_lifecycle_ports(attempts, lifecycle)
    async def fail_run(actual, *, capabilities, **kwargs):
        if fault == "terminal_cas":
            from app.runs.api import RunTerminalizationProgress
            return RunTerminalizationProgress(completed=False, status=None)
        return await lifecycle.fail_run(actual, **kwargs)
    # Keep the real Run/Attempt CAS; v4 delivery and diagnostics have separate owning tests.
    ports = replace(ports, fail_run=fail_run, record_result_diagnostics=None)
    attempt = WorkerAttemptLifecycle.from_leased_attempt(
        tenant_id="tenant-a", run_id="run-a", leased_attempt_id="queue-a", worker_id="worker-a",
        is_reconciliation=False, ports=ports,
    )
    service = build_worker_execution_terminal_service(
        transaction, capabilities=SimpleNamespace(pending_admissions=SimpleNamespace(prepare_pending_authority_in_transaction=pending)),
    )
    async def release(*args, **kwargs):
        pass
    async def terminalize():
        return await service.fail_or_cancel(
            payload=SimpleNamespace(tenant_id="tenant-a",run_id="run-a",executor_type="claude-agent-worker"),
            attempt_id=started["id"], attempt=attempt, capabilities=object(), failure_code="executor_failure",
            failure_message="Executor failed", failure_result={}, release_runtime_lease=release,
            continuation_settlement=receipt,
        )
    if fault == "stale_generation":
        with pytest.raises(ProviderSessionConflictError):
            await terminalize()
    else:
        outcome = await terminalize()
        assert outcome.status == ("skipped" if fault else "failed")
    epoch = await (await conn.execute("select * from provider_session_epochs where id=%s", (frozen["provider_epoch_id"],))).fetchone()
    assert all(epoch[key] is None for key in ("writer_run_id", "writer_attempt_id", "writer_owner_generation"))
    assert (await (await conn.execute("select count(*) as n from provider_turn_receipts")).fetchone())["n"] == 0
    assert (await (await conn.execute("select count(*) as n from provider_session_append_receipts")).fetchone())["n"] == 0
    head = await (await conn.execute("select * from provider_session_heads")).fetchone()
    current = await (await conn.execute("select * from run_attempts where id=%s", (started["id"],))).fetchone()
    assert current["execution_spec_sha256"] == spec.spec_sha256
    if fault:
        assert epoch["state"] == "bootstrapping" and epoch["recovery_json"] is None
        assert current["status"] == "running" and current["owner_generation"] == 4
        assert head["active_run_id"] == "run-a"
        return
    assert epoch["state"] == "closed"
    assert epoch["recovery_json"]["stop_evidence"] == "provider_not_created"
    assert epoch["recovery_json"]["owner_generation"] == current["owner_generation"] == 5
    assert current["status"] == "failed" and head["active_run_id"] is None
    assert head["current_epoch_id"] is None and head["recovery_json"] is None
    await conn.execute("insert into runs(id,tenant_id,workspace_id,session_id,user_id,agent_id,status,execution_kind,session_generation) values('next-run','tenant-a','workspace-a','session-a','user-a','agent-a','queued','harness_chat',2)")
    await provider_epochs.claim_provider_lineage(conn, scope=scope, run_id="next-run")
    coverage = await provider_epochs.read_provider_coverage(conn, scope=scope, run_id="next-run", session_generation=2)
    assert coverage["message_count"] == 0
    next_epoch = await provider_epochs.prepare_provider_epoch(
        conn, scope=scope, run_id="next-run", conversation_context=dict(
            source_sha256=coverage["source_sha256"], message_count=0,
            authority_schema_version=coverage.get("authority_schema_version", "ai-platform.conversation-authority.v2"),
            current_message_id="next-user-message", messages=[], selected_message_count=0, selected_turn_count=0,
        ),
    )
    assert next_epoch["execution_mode"] == "empty_start"
