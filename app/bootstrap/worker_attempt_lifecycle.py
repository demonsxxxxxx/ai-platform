"""Worker composition for the Runs attempt lifecycle."""

from functools import partial

from app.platform.postgres import errors as platform_errors
from app.runs.infrastructure import postgres as runs_postgres

from app.bootstrap.run_diagnostics import build_run_diagnostics_service
from app.bootstrap.attempt_settlement_projection import build_attempt_settlement_projection_service
from app.execution.api import WorkerAttemptLifecyclePorts
from app.runs.api import (
    RunAttemptLifecycleService,
    cancel_run_terminalization,
    complete_run_terminalization,
    fail_run_terminalization,
)
from app.runs.application.lifecycle import RunLifecycleService
from app.sandbox.api import SandboxReconciliationReceiptAuthority
from app.sandbox.infrastructure import leases_postgres


def build_worker_attempt_lifecycle_ports(
    attempt_lifecycle: RunAttemptLifecycleService,
    lifecycle: RunLifecycleService,
) -> WorkerAttemptLifecyclePorts:
    """Bind worker lifecycle ports to one process-owned attempt service."""

    run_diagnostics = build_run_diagnostics_service()
    receipt_authority = SandboxReconciliationReceiptAuthority(
        lock_receipt=leases_postgres.lock_scoped_reconciliation_receipt,
        lock_resource=leases_postgres.lock_scoped_reconciliation_resource,
        settlement_reader=build_attempt_settlement_projection_service(),
    )
    return WorkerAttemptLifecyclePorts(
        lock_run=runs_postgres.get_run,
        restore_reconciliation_attempt=partial(
            attempt_lifecycle.restore_reconciliation, receipt_authority=receipt_authority.is_current,
        ),
        complete_run=partial(complete_run_terminalization, lifecycle=lifecycle),
        fail_run=partial(fail_run_terminalization, lifecycle=lifecycle),
        cancel_run=partial(cancel_run_terminalization, lifecycle=lifecycle),
        start_attempt=attempt_lifecycle.start_worker,
        assert_current_attempt=attempt_lifecycle.assert_worker_current,
        request_attempt_cancel=attempt_lifecycle.request_cancel,
        terminalize_attempt=attempt_lifecycle.terminalize,
        is_cancel_requested=lifecycle.is_cancel_requested,
        classify_success_commit_block=lifecycle.classify_success_commit_block,
        conflict_error=platform_errors.RepositoryConflictError,
        record_result_diagnostics=run_diagnostics.capture_failure_result,
    )
