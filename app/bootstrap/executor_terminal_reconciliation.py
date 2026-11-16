"""Durable receipt ingress, independent of queue consumption or submission.

The process must configure its worker runtime services before calling this
entrypoint, just as for queue dispatch; no prior run_once call is required.
"""

from typing import Any

from app.bootstrap.worker_run_preparation import prepare_worker_run
from app.bootstrap.worker_terminal_completion import build_worker_terminal_completion_service
from app.db import transaction
from app.execution.api import (
    WorkerExecutorReconciliation, WorkerRunCancelled, run_elapsed_ms,
    restored_executor_reconciliation_queue_payload,
)
from app.executors.base import ExecutorResult, RunPayload
from app.executors.registry import AdapterRegistry
from app.models import QueueRunPayload
from app.runs.api import RunAttemptLifecycleService, RunLifecycleService, WorkerOutcome
from app.streaming.api import WorkerV4Capabilities, publish_run_event
from app.kernel.identifiers import assert_safe_id
from app.sandbox.api import finalize_stopped_executor_checkpoint
from app.platform.postgres import sandbox_leases


async def reconcile_executor_terminal_result(
    *, lease_row: dict[str, Any], result: ExecutorResult,
    registry: AdapterRegistry | None = None, worker_id: str | None = None,
    claim_token: str, transaction_factory: Any | None = None,
    v4_capabilities: WorkerV4Capabilities,
    run_attempt_lifecycle: RunAttemptLifecycleService,
    run_lifecycle: RunLifecycleService,
    outer_terminal_transaction: bool = False,
) -> WorkerOutcome:
    payload, attempt_id = restored_executor_reconciliation_queue_payload(
        lease_row.get("executor_reconciliation_context_json"), result=result.result,
        run_payload_factory=RunPayload, queue_payload_factory=QueueRunPayload,
    )
    # Preserve the former queue-envelope identity rejection at this ingress.
    if not attempt_id:
        return WorkerOutcome("dead_letter", None, "invalid_queue_attempt", "Queue lease attempt identity is required.")
    try:
        assert_safe_id(attempt_id, "attempt_id")
    except ValueError:
        return WorkerOutcome("dead_letter", None, "invalid_queue_attempt", "Queue lease attempt identity is invalid.")
    transaction_factory = transaction_factory if transaction_factory is not None else transaction
    reconciliation = WorkerExecutorReconciliation(result, lease_row, claim_token)
    prepared = await prepare_worker_run(
        payload, leased_attempt_id=attempt_id, registry=registry, worker_id=worker_id,
        reconciliation=reconciliation, queue_lease=None,
        transaction_factory=transaction_factory, v4_capabilities=v4_capabilities,
        run_attempt_lifecycle=run_attempt_lifecycle, run_lifecycle=run_lifecycle,
        publish_run_event=publish_run_event,
        **({"outer_terminal_transaction": True} if outer_terminal_transaction else {}),
    )
    if isinstance(prepared, WorkerOutcome):
        return prepared

    async def release_runtime_lease(conn, *, reason):
        stage = (lease_row.get("lease_payload_json") or {}).get("terminal_reconciliation")
        if isinstance(stage, dict) and stage.get("continuation") is not None:
            await finalize_stopped_executor_checkpoint(
                conn, lease_row=lease_row, claim_token=claim_token,
                finalize=sandbox_leases.finalize_stopped_executor_checkpoint,
            )
        # Noncheckpoint receipts retain the existing asynchronous cleanup owner.

    completion = build_worker_terminal_completion_service(
        prepared, transaction_factory=transaction_factory, capabilities=v4_capabilities,
        reconciliation=reconciliation, release_runtime_lease=release_runtime_lease,
        publish_run_event=publish_run_event,
        **({"outer_terminal_transaction": True} if outer_terminal_transaction else {}),
    )
    try:
        if prepared.adapter is None:
            raise RuntimeError("executor_adapter_not_resolved")
        latency_ms = run_elapsed_ms(prepared.locked_run.get("started_at"))
    except WorkerRunCancelled:
        return await completion.cancel()
    except Exception as exc:  # noqa: BLE001 - preserve executor-boundary failures.
        return await completion.fail(exc)
    return await completion.complete(result, latency_ms=latency_ms)
