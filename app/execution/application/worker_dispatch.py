"""Classify execution handoff outcomes without granting terminal authority on uncertainty."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class WorkerDispatchResult:
    result: Any | None = None
    latency_ms: int | None = None
    detached: bool = False
    outcome: Any | None = None


async def execute_worker_run(
    adapter: Any,
    run_payload: Any,
    *,
    event_sink: Any,
    cancel_requested: Any,
    execution_owner: Any,
    monotonic: Callable[[], float],
    submit: Callable[..., Any],
    accepted_type: type,
    uncertain_dispatch_type: type[Exception],
    running_outcome: Callable[..., Any],
) -> WorkerDispatchResult:
    """Classify one execution handoff and preserve uncertain Sandbox dispatches."""

    started_at = monotonic()
    try:
        result = await submit(
            adapter,
            run_payload,
            event_sink=event_sink,
            cancel_requested=cancel_requested,
            execution_owner=execution_owner,
        )
    except uncertain_dispatch_type:
        return WorkerDispatchResult(
            detached=True,
            outcome=running_outcome(status="running", run_id=run_payload.run_id),
        )
    if isinstance(result, accepted_type):
        if not result.lease_id:
            raise ValueError("executor_dispatch_acceptance_lease_missing")
        return WorkerDispatchResult(
            detached=True,
            outcome=running_outcome(status="running", run_id=run_payload.run_id),
        )
    return WorkerDispatchResult(
        result=result,
        latency_ms=max(int((monotonic() - started_at) * 1000), 0),
    )
