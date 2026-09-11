"""Worker composition for the Runs attempt lifecycle."""

from functools import partial
from typing import Any, Callable

from app import repositories
from app.execution.api import WorkerAttemptLifecyclePorts, finalize_worker_child_parent
from app.platform.postgres import sandbox_leases as sandbox_lease_repository
from app.runs.api import RunAttemptLifecycleService
from app.tool_permission_lifecycle import (
    cancel_run_with_v4,
    complete_run_with_v4,
    drain_run_tool_permission_terminalization,
    fail_run_with_v4,
)


def build_worker_attempt_lifecycle_ports(
    attempt_lifecycle: RunAttemptLifecycleService,
) -> WorkerAttemptLifecyclePorts:
    """Bind worker lifecycle ports to one process-owned attempt service."""

    return WorkerAttemptLifecyclePorts(
        lock_run=repositories.get_run,
        complete_run=complete_run_with_v4,
        fail_run=fail_run_with_v4,
        cancel_run=cancel_run_with_v4,
        drain_terminalization=partial(
            drain_run_tool_permission_terminalization,
            attempt_lifecycle=attempt_lifecycle,
        ),
        is_reconciliation_claim_current=(
            sandbox_lease_repository.is_sandbox_executor_reconciliation_claim_current
        ),
        get_attempt=attempt_lifecycle.get,
        get_attempt_for_queue_attempt=attempt_lifecycle.get_for_queue_attempt,
        start_attempt=attempt_lifecycle.start_worker,
        assert_current_attempt=attempt_lifecycle.assert_worker_current,
        request_attempt_cancel=attempt_lifecycle.request_cancel,
        terminalize_attempt=attempt_lifecycle.terminalize,
        conflict_error=repositories.RepositoryConflictError,
    )


def build_worker_parent_finalizer(
    attempt_lifecycle: RunAttemptLifecycleService,
    *,
    reconcile_terminalized_run: Callable[..., Any],
) -> Callable[..., Any]:
    """Bind child-to-parent reconciliation to the same attempt service."""

    return partial(
        finalize_worker_child_parent,
        reconcile_terminalized_run=partial(
            reconcile_terminalized_run,
            attempt_lifecycle=attempt_lifecycle,
        ),
    )
