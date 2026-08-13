"""Durable terminalization for runs, independent of execution capabilities."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from app.repositories import RunTerminalizationProgress


async def drain_run_terminalization(
    *,
    tenant_id: str,
    run_id: str,
    transaction_factory: Callable[[], Any],
    max_batches: int = 4,
) -> RunTerminalizationProgress | None:
    """Commit a bounded number of durable terminalization batches for one run."""

    from app import repositories

    result: RunTerminalizationProgress | None = None
    for _ in range(max(1, int(max_batches))):
        async with transaction_factory() as conn:
            result = await repositories.progress_run_terminalization(
                conn,
                tenant_id=tenant_id,
                run_id=run_id,
            )
        if result is None or result.completed or result.status is None:
            return result
    return result


async def reconcile_terminalized_run(
    *,
    tenant_id: str,
    run_id: str,
    transaction_factory: Callable[[], Any],
    progress: Any | None = None,
) -> dict[str, Any] | None:
    """Reconcile one final child transition after the run commit."""

    if progress is not None and (
        not progress.did_transition or not progress.needs_reconcile
    ):
        return None
    from app import repositories

    async with transaction_factory() as conn:
        run = await repositories.get_run(
            conn,
            tenant_id=tenant_id,
            run_id=run_id,
            for_update=True,
        )
        if run is None:
            return None
        status = str(run.get("status") or "")
        reconciled = await repositories.reconcile_multi_agent_child_run_terminal_state(
            conn,
            tenant_id=tenant_id,
            child_run_id=run_id,
            child_status=status,
            result_json=run.get("result_json") if isinstance(run.get("result_json"), dict) else {},
            error_code=str(run.get("error_code") or "") or None,
            error_message=str(run.get("error_message") or "") or None,
        )
    return reconciled
