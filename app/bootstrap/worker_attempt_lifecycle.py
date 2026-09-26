"""Worker composition for the Runs attempt lifecycle."""

from functools import partial

from app import repositories
from app.bootstrap.run_diagnostics import build_run_diagnostics_service
from app.execution.api import WorkerAttemptLifecyclePorts
from app.platform.postgres import sandbox_leases as sandbox_lease_repository
from app.runs.api import (
    RunAttemptLifecycleService,
    cancel_run_with_v4,
    complete_run_with_v4,
    fail_run_with_v4,
)
from app.runs.application.lifecycle import RunLifecycleService


def build_worker_attempt_lifecycle_ports(
    attempt_lifecycle: RunAttemptLifecycleService,
    lifecycle: RunLifecycleService,
) -> WorkerAttemptLifecyclePorts:
    """Bind worker lifecycle ports to one process-owned attempt service."""

    run_diagnostics = build_run_diagnostics_service()
    return WorkerAttemptLifecyclePorts(
        lock_run=repositories.get_run,
        complete_run=partial(complete_run_with_v4, lifecycle=lifecycle),
        fail_run=partial(fail_run_with_v4, lifecycle=lifecycle),
        cancel_run=partial(cancel_run_with_v4, lifecycle=lifecycle),
        is_reconciliation_claim_current=(
            sandbox_lease_repository.is_sandbox_executor_reconciliation_claim_current
        ),
        get_attempt=attempt_lifecycle.get,
        get_attempt_for_queue_attempt=attempt_lifecycle.get_for_queue_attempt,
        start_attempt=attempt_lifecycle.start_worker,
        assert_current_attempt=attempt_lifecycle.assert_worker_current,
        request_attempt_cancel=attempt_lifecycle.request_cancel,
        terminalize_attempt=attempt_lifecycle.terminalize,
        is_cancel_requested=lifecycle.is_cancel_requested,
        classify_success_commit_block=lifecycle.classify_success_commit_block,
        conflict_error=repositories.RepositoryConflictError,
        record_result_diagnostics=run_diagnostics.capture_failure_result,
    )
