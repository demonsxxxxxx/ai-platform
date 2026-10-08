"""Bind the Runs Worker dispatch admission transaction to its owners."""

from typing import Any, Callable

from app.runs import api as runs_api
from app.runs.infrastructure import postgres as runs_postgres
from app.principal_authority import resolve_current_principal
from app.worker_principal_authority import _resolve_current_principal_before_dispatch
from app.streaming.infrastructure import run_events_postgres


async def resolve_worker_dispatch_principal(payload: Any, *, transaction_factory: Any) -> Any:
    return await _resolve_current_principal_before_dispatch(
        payload, transaction_factory=transaction_factory,
        run_loader=runs_postgres.get_run,
        principal_resolver=resolve_current_principal,
    )


def build_worker_dispatch_admission_service(
    transaction_factory: Any,
    *,
    run_attempt_lifecycle: Any,
    capabilities: Any,
    authorize_locked_candidate: Callable[..., Any],
    executor_resolution_error: Callable[[str], KeyError | None],
    predispatch_failure_result: Callable[..., dict[str, Any]],
) -> runs_api.WorkerDispatchAdmissionService:
    return runs_api.WorkerDispatchAdmissionService(
        transaction_factory=transaction_factory,
        lock_queued_run=run_attempt_lifecycle.lock_queued_run,
        get_run=runs_postgres.get_run,
        prepare_pending_authority=capabilities.pending_admissions.prepare_pending_authority_in_transaction,
        authorize_locked_candidate=authorize_locked_candidate,
        append_hidden_event=run_events_postgres.append_event,
        executor_resolution_error=executor_resolution_error,
        predispatch_failure_result=predispatch_failure_result,
    )
