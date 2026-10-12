"""Receipt settlement across real Worker transactions with scoped in-memory IO.

Reuse Worker capability fixtures; do not replace Worker outcomes or owner fences.
"""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from datetime import datetime, timezone
import pytest
from tests import test_worker as w
from tests.test_worker import (
    default_cancel_not_requested as default_cancel_not_requested,
    _stub_terminal_context_ports as _stub_terminal_context_ports,
)
from app import executor_reconciler as r
from app.runs.application.execution_spec import worker_dispatch_fence
from app.runs.application.worker_dispatch_binding import WorkerDispatchBindingService
from app.sandbox.infrastructure import leases_postgres
from app.executors.base import ExecutorResult, RunPayload
from app.executors.registry import AdapterRegistry
from app.execution.api import sandbox_reconciliation_payload


def _settlement_scenario(
    monkeypatch, *, cancel_at="binding", corrupt=None, lose_retry_claim=False
):
    calls = []
    persisted = w.base_payload(
        _leased=False,
        agent_id="general-agent",
        execution_kind="harness_chat",
        skill_id=None,
        file_ids=[],
        input={"message": "synthetic receipt test"},
        executor_type="claude-agent-worker",
        skill_version=None,
        release_decision={},
        skill_manifests=[],
        schema_version=w.RUN_PAYLOAD_SCHEMA_VERSION_V2,
        agent_profile=None,
    )
    run = w.locked_run_from_payload(persisted)
    run.update(status="running", cancel_requested_at=None)
    payload = w.QueueRunPayload.model_validate(persisted)
    run.update(
        executor_type=payload.executor_type,
        context_snapshot_id=payload.context_snapshot_id,
    )
    run_payload = RunPayload(
        **{
            k: getattr(payload, k)
            for k in (
                "tenant_id",
                "workspace_id",
                "user_id",
                "session_id",
                "run_id",
                "agent_id",
                "execution_kind",
                "skill_id",
                "file_ids",
                "input",
                "release_decision",
                "skill_manifests",
                "schema_version",
            )
        },
        attempt_id="attempt-a",
        trace_id="trace-a",
        skill_version="",
        context_snapshot_id=payload.context_snapshot_id or "",
        context_snapshot=payload.context_snapshot,
        model_id=payload.model_id or "",
        model_value=payload.model_value or "",
        agent_profile={},
    )
    row = {
        "id": "lease-a",
        "attempt_id": "attempt-a",
        "status": "active",
        "executor_reconciliation_status": "pending",
        "executor_terminal_json": {
            "run_id": "run-a",
            "status": "succeeded",
            "message": "done",
        },
        "executor_reconciliation_context_json": {
            "adapter_name": "claude-agent-worker",
            "adapter_context": {},
            "run_payload": sandbox_reconciliation_payload(run_payload),
        },
    }
    original_receipt_lock = leases_postgres.lock_scoped_reconciliation_receipt
    spec = w._install_frozen_receipt(monkeypatch, persisted, row, calls=calls)
    # Restore real receipt SQL after helper setup; synthetic DB checks every predicate argument.
    monkeypatch.setattr(
        leases_postgres, "lock_scoped_reconciliation_receipt", original_receipt_lock
    )
    from tests.support.frozen_receipts import frozen_attempt

    attempt = frozen_attempt(spec, id="attempt-a", queue_attempt_id="qat-attempt-a")
    if corrupt == "attempt":
        attempt["id"] = "different-attempt"
    if corrupt == "generation":
        attempt["owner_generation"] = 9

    async def get_attempt(_conn, **kwargs):
        assert kwargs["attempt_id"] == row["attempt_id"]
        return dict(attempt)

    async def scoped_run(_conn, **kwargs):
        assert all(run[k] == v for k, v in kwargs["scope"].items())
        return dict(run)

    monkeypatch.setattr(w._TEST_ATTEMPT_PERSISTENCE, "get_run_attempt", get_attempt)
    monkeypatch.setattr(
        w._TEST_ATTEMPT_PERSISTENCE, "get_scoped_reconciliation_run", scoped_run
    )

    class Cursor:
        def __init__(self, value):
            self.value = value

        async def fetchone(self):
            return self.value

    class Conn:
        async def execute(self, sql, params):
            if "lease_payload_json->>'owner_generation'" in sql:
                assert params == (
                    row["id"],
                    row["tenant_id"],
                    row["workspace_id"],
                    row["user_id"],
                    row["session_id"],
                    row["run_id"],
                    row["attempt_id"],
                    row["executor_reconciliation_claim_token"],
                    "4",
                )
                assert (
                    row["status"] in {"active", "released"}
                    and row["executor_terminal_json"]
                )
                assert row["executor_reconciliation_status"] == "claimed"
                calls.append("full_receipt_scope_checked")
                return Cursor(None if corrupt == "claim" else {"id": row["id"]})
            if "executor_reconciliation_status = 'retry'" in sql:
                assert params[1:] == (row["id"], claimed_tokens[-1])
                calls.append(("retry", params[0]))
                if row["executor_reconciliation_claim_token"] != params[2]:
                    return Cursor(None)
                row.update(
                    executor_reconciliation_status="retry",
                    executor_reconciliation_claim_token=None,
                )
                return Cursor({"id": row["id"]})
            if "status = 'quarantined'" in sql:
                assert params[1:] == (row["id"], claimed_tokens[-1])
                calls.append(("quarantine", params[0]))
                row.update(
                    status="quarantined", executor_reconciliation_status="failed"
                )
                return Cursor({"id": row["id"]})
            if "executor_reconciliation_status = 'finalized'" in sql:
                assert params == (
                    "executor_reconciled",
                    row["tenant_id"],
                    row["user_id"],
                    row["run_id"],
                    row["id"],
                    row["executor_reconciliation_claim_token"],
                )
                assert row["executor_reconciliation_status"] == "claimed"
                calls.append(("finalize", run["status"], attempt["status"]))
                row.update(
                    status="released",
                    executor_reconciliation_status="finalized",
                    executor_reconciliation_claim_token=None,
                )
                return Cursor({"id": row["id"]})
            if "from sandbox_leases" in sql:
                assert params == (row["id"], row["executor_reconciliation_claim_token"])
                assert row["executor_reconciliation_status"] == "claimed"
                return Cursor({"id": row["id"]})
            raise AssertionError(sql)

    @asynccontextmanager
    async def transaction():
        yield Conn()

    async def get_run(_conn, **kwargs):
        assert (kwargs["tenant_id"], kwargs["run_id"]) == (
            row["tenant_id"],
            row["run_id"],
        )
        return dict(run)

    claimed_tokens = []

    async def claim(_conn, **kwargs):
        assert row["executor_reconciliation_status"] in {"pending", "retry"}
        claimed_tokens.append(kwargs["claim_token"])
        row.update(
            executor_reconciliation_status="claimed",
            executor_reconciliation_claim_token=kwargs["claim_token"],
        )
        return [row]

    class Provider:
        async def stop(self, lease, *, reason):
            calls.append(("stop", run["status"], attempt["status"]))
            return SimpleNamespace(status="stopped")

    async def collect(lease_row, **kwargs):
        # Retain context/Run/Attempt identity reconstruction and result validation.
        r._context_and_payload(lease_row)
        result = ExecutorResult(
            status="succeeded",
            adapter_version="probe/1",
            executor_type="claude_agent_sdk",
            executor_version="1",
            capabilities={},
            result={"message": "done"},
            executor_payload={},
        )
        result.validate()
        return result, Provider(), object()

    def request_cancel():
        run["cancel_requested_at"] = datetime.now(timezone.utc)
        attempt.update(status="cancel_requested", owner_generation=5)
        calls.append("owner_cancel_committed")

    original_bind = WorkerDispatchBindingService.bind

    async def binding_cancel(self, **kwargs):
        calls.append("admission_passed")
        # Admission committed; owner cancellation commits before the binding lock.
        if cancel_at == "binding":
            request_cancel()
        return await original_bind(self, **kwargs)

    async def facts(_conn, **kwargs):
        return dict(run)

    async def real_fence(conn, **kwargs):
        value = await worker_dispatch_fence(conn, run_facts_loader=facts, **kwargs)
        calls.append(("fence", value))
        return value

    async def is_cancel(_conn, **kwargs):
        return run["cancel_requested_at"] is not None

    async def no_complete(*args, **kwargs):
        raise AssertionError("cancellation must not complete success")

    # Keep the real Runs cancellation service, including terminal-progress semantics.
    from tests.test_run_lifecycle import MemoryPersistence, service

    class RunPersistence(MemoryPersistence):
        async def finalize_staged_terminalization(self, conn, **kwargs):
            final = await super().finalize_staged_terminalization(conn, **kwargs)
            run["status"] = self.status
            calls.append(("run_terminal", self.status))
            return final

    lifecycle = service(RunPersistence(), [], [])

    async def request_attempt_cancel(_conn, **kwargs):
        assert kwargs["attempt_id"] == attempt["id"] == row["attempt_id"]
        assert attempt["status"] == "cancel_requested"
        return dict(attempt)

    async def terminalize_attempt(_conn, **kwargs):
        assert kwargs["attempt_id"] == attempt["id"] == row["attempt_id"]
        assert run["status"] == kwargs["status"] == "cancelled"
        attempt["status"] = kwargs["status"]
        calls.append(("attempt_terminal", attempt["id"], attempt["status"]))
        return dict(attempt)

    monkeypatch.setattr(
        w._TEST_ATTEMPT_PERSISTENCE,
        "request_run_attempt_cancel",
        request_attempt_cancel,
    )
    monkeypatch.setattr(
        w._TEST_ATTEMPT_PERSISTENCE, "terminalize_run_attempt", terminalize_attempt
    )

    async def capture_outcome(**kwargs):
        if cancel_at == "admission" and run["cancel_requested_at"] is None:
            request_cancel()
        if cancel_at == "terminal_after_collection":
            run["status"] = "cancelled"
            attempt["status"] = "cancelled"
        outcome = await w.worker_module.reconcile_executor_terminal_result(**kwargs)
        calls.append(("outcome", outcome.status, outcome.error_code))
        if lose_retry_claim:
            row["executor_reconciliation_claim_token"] = "replacement-claim"
        return outcome

    monkeypatch.setattr(r, "transaction", transaction)
    monkeypatch.setattr(
        r.sandbox_lease_repository, "claim_sandbox_executor_reconciliations", claim
    )
    monkeypatch.setattr(r.runs_postgres, "get_run", get_run)
    monkeypatch.setattr(r, "_collect_workspace_and_convert_result", collect)
    monkeypatch.setattr(r, "reconcile_executor_terminal_result", capture_outcome)
    monkeypatch.setattr(WorkerDispatchBindingService, "bind", binding_cancel)
    monkeypatch.setattr("app.runs.api.worker_dispatch_fence", real_fence)
    monkeypatch.setattr(w._TEST_RUN_LIFECYCLE, "is_cancel_requested", is_cancel)
    monkeypatch.setattr(w._TEST_RUN_LIFECYCLE, "complete_run", no_complete)
    monkeypatch.setattr(w._TEST_RUN_LIFECYCLE, "cancel_run", lifecycle.cancel_run)

    async def run_once():
        return await r.reconcile_pending_executor_terminals_once(
            registry=AdapterRegistry(
                {"claude-agent-worker": w.SuccessfulExecutorStub()}
            ),
            worker_id="worker-a",
            v4_capabilities=w._FAKE_WORKER_V4_CAPABILITIES,
            attempt_lifecycle=w._TEST_RUN_ATTEMPT_LIFECYCLE,
            lifecycle=w._TEST_RUN_LIFECYCLE,
        )

    return SimpleNamespace(
        run_once=run_once,
        run=run,
        attempt=attempt,
        receipt=row,
        calls=calls,
        claimed_tokens=claimed_tokens,
    )


@pytest.mark.asyncio
async def test_binding_cancel_retries_then_settles_original_attempt(monkeypatch):
    state = _settlement_scenario(monkeypatch)
    assert await state.run_once() == 1
    assert "full_receipt_scope_checked" in state.calls
    assert ("fence", "stale") in state.calls
    assert ("outcome", "skipped", "stale_terminal_state") in state.calls
    assert ("retry", "executor_reconciliation_run_not_terminal") in state.calls
    assert state.run["status"] == "running"
    assert state.attempt["status"] == "cancel_requested"
    assert state.receipt["status"] == "active"
    assert state.receipt["executor_reconciliation_status"] == "retry"
    assert not any(
        call[0] in {"stop", "finalize"}
        for call in state.calls
        if isinstance(call, tuple)
    )

    # A later eligible claim must execute the real admission cancellation flow;
    # merely changing the Run status in a stub would miss the responsibility handoff.
    assert await state.run_once() == 1
    assert len(set(state.claimed_tokens)) == 2
    assert state.attempt["id"] == "attempt-a"
    assert state.run["status"] == state.attempt["status"] == "cancelled"
    assert state.receipt["status"] == "released"
    assert state.receipt["executor_reconciliation_status"] == "finalized"
    expected = [
        ("run_terminal", "cancelled"),
        ("attempt_terminal", "attempt-a", "cancelled"),
        ("outcome", "cancelled", None),
        ("stop", "cancelled", "cancelled"),
        ("finalize", "cancelled", "cancelled"),
    ]
    assert [call for call in state.calls if call in expected] == expected
    assert state.calls.count("full_receipt_scope_checked") >= 3


@pytest.mark.asyncio
async def test_admission_cancel_settles_before_receipt_release(monkeypatch):
    state = _settlement_scenario(monkeypatch, cancel_at="admission")
    assert await state.run_once() == 1
    assert ("outcome", "cancelled", None) in state.calls
    assert ("stop", "cancelled", "cancelled") in state.calls
    assert ("finalize", "cancelled", "cancelled") in state.calls
    assert state.receipt["executor_reconciliation_status"] == "finalized"


@pytest.mark.asyncio
@pytest.mark.parametrize("corrupt", ["claim", "attempt", "generation"])
async def test_receipt_scope_or_owner_mismatch_never_releases(monkeypatch, corrupt):
    state = _settlement_scenario(monkeypatch, corrupt=corrupt)
    assert await state.run_once() == 1
    assert state.run["status"] == "running"
    assert state.receipt["status"] == "quarantined"
    assert not any(
        call[0] in {"stop", "finalize"}
        for call in state.calls
        if isinstance(call, tuple)
    )
    assert not any(
        call[0] == "outcome" for call in state.calls if isinstance(call, tuple)
    )


@pytest.mark.asyncio
async def test_nonterminal_outcome_cannot_release_or_retry_replacement_claim(
    monkeypatch,
):
    state = _settlement_scenario(monkeypatch, lose_retry_claim=True)
    assert await state.run_once() == 1
    assert state.receipt["executor_reconciliation_claim_token"] == "replacement-claim"
    assert state.receipt["executor_reconciliation_status"] == "claimed"
    assert state.receipt["status"] == "active"
    assert not any(
        call[0] in {"stop", "finalize"}
        for call in state.calls
        if isinstance(call, tuple)
    )


@pytest.mark.asyncio
async def test_skipped_terminal_race_retries_then_uses_existing_terminal_branch(
    monkeypatch,
):
    state = _settlement_scenario(monkeypatch, cancel_at="terminal_after_collection")
    assert await state.run_once() == 1
    assert ("outcome", "skipped", "stale_terminal_state") in state.calls
    assert state.run["status"] == state.attempt["status"] == "cancelled"
    assert state.receipt["executor_reconciliation_status"] == "retry"
    assert not any(call[0] == "stop" for call in state.calls if isinstance(call, tuple))

    class Lease:
        def model_copy(self, *, update):
            assert update == {"workspace_host_path": "/synthetic/workspace"}
            return self

    class Provider:
        async def stop(self, lease, *, reason):
            assert isinstance(lease, Lease)
            state.calls.append(("stop", state.run["status"], state.attempt["status"]))
            return SimpleNamespace(status="stopped")

    monkeypatch.setattr(
        r,
        "SandboxWorkspaceManager",
        lambda: SimpleNamespace(
            prepare=lambda request: SimpleNamespace(
                workspace_host_path="/synthetic/workspace"
            )
        ),
    )
    monkeypatch.setattr(
        r._sandbox_cleanup, "container_lease_from_persisted_row", lambda row: Lease()
    )
    monkeypatch.setattr(r, "_container_provider_for_lease", lambda lease: Provider())
    assert await state.run_once() == 1
    assert state.receipt["executor_reconciliation_status"] == "finalized"
    assert ("stop", "cancelled", "cancelled") in state.calls
    assert ("finalize", "cancelled", "cancelled") in state.calls
    assert (
        sum(call[0] == "outcome" for call in state.calls if isinstance(call, tuple))
        == 1
    )
