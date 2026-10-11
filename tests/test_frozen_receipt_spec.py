from contextlib import asynccontextmanager
from functools import partial
from types import SimpleNamespace

import pytest

from app.executors.base import project_execution_spec_to_run_payload
from app.execution.application.worker_attempt_lifecycle import WorkerAttemptLifecycle, WorkerAttemptLifecyclePorts
from app.runs.api import ReconciliationAuthorityError
from app.runs.application.attempt_lifecycle import RunAttemptLifecycleService
from app.runs.application.worker_dispatch_binding import (
    WorkerDispatchBindingService, WorkerDispatchContextPorts, WorkerDispatchExecutionPorts,
)
from tests.support.frozen_receipts import RESTORE, SCOPE, frozen_attempt, frozen_run, frozen_spec


async def forbidden(*args, **kwargs):
    raise AssertionError("receipt recovery must not dispatch or persist new work")


async def receipt_current(conn, **kwargs):
    assert kwargs["expected_owner_generation"] == 4
    return True


@pytest.mark.asyncio
@pytest.mark.parametrize("fence_value", ["ready", "invalid", "stale"])
async def test_receipt_binding_only_restores_ready_frozen_spec(fence_value):
    calls = []
    spec = frozen_spec()

    @asynccontextmanager
    async def transaction():
        yield object()

    async def fence(*args, **kwargs):
        return fence_value

    async def restore(conn):
        calls.append("restore")
        return spec

    def forbidden_sync(*args, **kwargs):
        raise AssertionError("no context projection, compile, lease or start event")

    service = WorkerDispatchBindingService(
        transaction_factory=transaction, dispatch_fence=fence, compile_spec=forbidden_sync,
        context=WorkerDispatchContextPorts(materialize=forbidden, project=forbidden_sync, execution_pack=forbidden_sync),
        execution=WorkerDispatchExecutionPorts(
            project_spec=project_execution_spec_to_run_payload, attach_mcp=forbidden,
            mcp_runtime_error=ValueError, append_user_event=forbidden,
            runtime_evidence=forbidden_sync, uses_runtime_sandbox=forbidden_sync,
            create_runtime_lease=forbidden, missing_attempt_error=RuntimeError,
        ), fail_pre_dispatch=forbidden,
    )
    kwargs = dict(
        payload=SimpleNamespace(context_snapshot_id="context-a"), run_identity={}, locked_run={},
        trace_id="trace-a", attempt_id="attempt-a", capabilities=None, principal=None,
        worker_id="reconciler", reconciliation=True,
        attempt_authority=SimpleNamespace(restore_reconciliation_execution_spec=restore,
                                          reconciliation_owner_generation=4),
    )
    if fence_value == "invalid":
        with pytest.raises(ReconciliationAuthorityError, match="scope_invalid"):
            await service.bind(**kwargs)
        assert calls == []
    else:
        result = await service.bind(**kwargs)
        assert calls == (["restore"] if fence_value == "ready" else [])
        if fence_value == "ready":
            assert result.run_payload.input["message"] == "original current user"
            assert result.run_payload.owner_generation == 4
            assert result.runtime_sandbox_lease is None
            assert spec == frozen_spec()
        else:
            assert result.outcome.status == "skipped"


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [
    {"owner_generation": 5}, {"owner_generation": 3}, {"owner_kind": "reconciler"},
    {"status": "expired"}, {"id": "other-attempt"}, {"tenant_id": "other-tenant"},
    {"execution_spec_sha256": "0" * 64}, {"execution_spec_canonical_json": "{}"},
    {"execution_spec_json": {}}, {"run_model_gateway_revision": 9},
    {"run_model_value": "other-model"}, {"run_context_snapshot_id": "other-context"},
    {"run_executor_type": "other-engine"},
    {"status": "cancel_requested", "owner_generation": 5},
    {"status": "cancel_requested", "owner_generation": 6, "run_cancel_requested_at": "requested"},
])
async def test_restore_rejects_stale_owner_or_corrupt_binding(change):
    async def read(conn, **kwargs):
        return frozen_attempt(**change)
    async def read_run(conn, **kwargs):
        return frozen_run(await read(conn))
    service = RunAttemptLifecycleService(SimpleNamespace(get_run_attempt=read, get_scoped_reconciliation_run=read_run))
    with pytest.raises(ReconciliationAuthorityError):
        await service.restore_reconciliation(object(), **RESTORE, receipt_authority=receipt_current)


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_restore_preserves_original_spec_and_cancellation_advance(cancel):
    row = frozen_attempt(**({"status": "cancel_requested", "owner_generation": 5,
                             "run_cancel_requested_at": "requested"} if cancel else {}))
    async def read(conn, **kwargs):
        return row
    async def read_run(conn, **kwargs):
        return frozen_run(row)
    service = RunAttemptLifecycleService(SimpleNamespace(get_run_attempt=read, get_scoped_reconciliation_run=read_run))
    restored = await service.restore_reconciliation(object(), **RESTORE, receipt_authority=receipt_current)
    assert restored.execution_spec.canonical_json.decode() == row["execution_spec_canonical_json"]
    assert restored.attempt["owner_generation"] == (5 if cancel else 4)


@pytest.mark.asyncio
async def test_receipt_generation_stays_captured_through_final_attempt_fence():
    row = frozen_attempt()
    async def read(conn, **kwargs):
        return row
    async def read_run(conn, **kwargs):
        return frozen_run(await read(conn))
    service = RunAttemptLifecycleService(SimpleNamespace(get_run_attempt=read, get_scoped_reconciliation_run=read_run))
    values = {name: forbidden for name in (
        "lock_run", "complete_run", "fail_run", "cancel_run",
        "start_attempt", "assert_current_attempt",
        "request_attempt_cancel", "terminalize_attempt", "is_cancel_requested", "classify_success_commit_block",
    )}
    authority = WorkerAttemptLifecycle.from_leased_attempt(
        tenant_id="tenant-a", run_id="run-a", leased_attempt_id="attempt-a", worker_id="reconciler",
        is_reconciliation=True, reconciliation_lease_id="lease-a", reconciliation_claim_token="claim-a",
        reconciliation_owner_generation=4, reconciliation_scope=tuple(SCOPE.items()),
        ports=WorkerAttemptLifecyclePorts(**values, conflict_error=ValueError,
                                         restore_reconciliation_attempt=partial(service.restore_reconciliation, receipt_authority=receipt_current)),
    )
    authority = await authority.restore_reconciliation_authority(object())
    assert authority.worker_owner_id == "worker-original"
    assert await authority.restore_reconciliation_execution_spec(object()) == frozen_spec()
    row["owner_generation"] = 5
    with pytest.raises(ReconciliationAuthorityError, match="owner_stale"):
        await authority._current_attempt(object())
    row.update(status="cancel_requested", run_cancel_requested_at="requested")
    assert (await authority._current_attempt(object()))["owner_generation"] == 5
    row["owner_id"] = "replacement-worker"
    with pytest.raises(ReconciliationAuthorityError, match="owner_stale"):
        await authority._current_attempt(object())
    with pytest.raises(ValueError, match="must_restore_spec"):
        await authority.bind_execution_spec(object(), frozen_spec())


@pytest.mark.asyncio
async def test_runs_restore_orders_owners_and_never_reads_attempt_after_untrusted_scope_or_claim():
    calls = []
    run_available = [True]
    claim_available = [True]
    async def run(conn, **kwargs):
        calls.append("run")
        return frozen_run() if run_available[0] else None
    async def claim(conn, **kwargs):
        calls.append("sandbox")
        return claim_available[0]
    async def attempt(conn, **kwargs):
        calls.append("attempt")
        return frozen_attempt()
    service = RunAttemptLifecycleService(SimpleNamespace(get_scoped_reconciliation_run=run, get_run_attempt=attempt))
    await service.restore_reconciliation(object(), **RESTORE, receipt_authority=claim)
    assert calls == ["run", "sandbox", "attempt"]
    calls.clear()
    run_available[0] = False
    with pytest.raises(ReconciliationAuthorityError, match="scope_invalid"):
        await service.restore_reconciliation(object(), **RESTORE, receipt_authority=claim)
    assert calls == ["run"]
    calls.clear()
    run_available[0], claim_available[0] = True, False
    with pytest.raises(ReconciliationAuthorityError, match="claim_stale"):
        await service.restore_reconciliation(object(), **RESTORE, receipt_authority=claim)
    assert calls == ["run", "sandbox"]


@pytest.mark.asyncio
@pytest.mark.parametrize("denied_at", ["profile", "capability"])
async def test_frozen_receipt_still_obeys_current_capability_denial(denied_at):
    from app.runs.application.worker_locked_authorization import WorkerLockedAuthorizationService
    from app.runs.application.worker_capability_admission import WorkerCapabilityAuthorization
    spec = frozen_spec()
    calls = []

    class Payload(SimpleNamespace):
        def model_copy(self, *, update):
            return Payload(**{**vars(self), **update})
    payload = Payload(skill_manifests=[], agent_profile={})
    async def snapshot(conn, **kwargs):
        assert kwargs["reconciliation"] is True
        return SimpleNamespace(run_identity={"tenant_id": "tenant-a", "run_id": "run-a", "agent_id": "agent-a"},
                               locked_run={}, trace_id="trace-a", mismatch_fields=[], valid=True, payload=payload)
    async def skills(conn, **kwargs):
        calls.append("skills")
        return []
    async def profile(conn, **kwargs):
        calls.append("profile")
        return kwargs["payload"], "revoked" if denied_at == "profile" else None
    async def capability(conn, **kwargs):
        calls.append("capability")
        return WorkerCapabilityAuthorization(payload, None, (), "revoked")
    async def audit(conn, **kwargs):
        calls.append("audit")
    async def deny(conn, **kwargs):
        calls.append("denied")
        return SimpleNamespace(payload=kwargs["payload"], outcome=SimpleNamespace(
            status="failed", error_code="capability_not_authorized", error_message="revoked"))
    service = WorkerLockedAuthorizationService(
        snapshot=SimpleNamespace(resolve=snapshot), skill_materialize=skills, profile_authorize=profile,
        capability_authority=SimpleNamespace(authorize=capability),
        audit_authority=SimpleNamespace(record_admission=audit),
        distribution_authority=SimpleNamespace(denied=lambda *args: "revoked"),
        failures=SimpleNamespace(capability_denial=deny, invalid_snapshot=forbidden, pre_dispatch_error=forbidden),
    )
    result = await service.authorize(object(), payload=payload, locked_run={}, current_principal=None,
        attempt_authority=None, capabilities=None, attempt_id="attempt-a", trace_id="trace-a", reconciliation=True)
    assert result.outcome.error_code == "capability_not_authorized"
    assert calls == (["skills", "profile", "denied"] if denied_at == "profile"
                     else ["skills", "profile", "capability", "audit", "denied"])
    assert spec == frozen_spec()
