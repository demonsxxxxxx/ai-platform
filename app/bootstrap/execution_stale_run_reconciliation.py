"""Compose Execution's PostgreSQL stale-Run reconciliation application service."""

from __future__ import annotations

from app.db import transaction
from app.execution.application.stale_run_reconciliation import (
    StaleRunReconciliationService,
)
from app.identity.infrastructure.audit_postgres import append_audit_log
from app.runs.api import RunAttemptLifecycleService, RunLifecycleService
from app.streaming.api import WorkerV4Capabilities
from app.streaming.infrastructure.run_events_postgres import append_event


def build_stale_run_reconciliation_service(
    *,
    lifecycle: RunLifecycleService,
    attempt_lifecycle: RunAttemptLifecycleService,
    v4_capabilities: WorkerV4Capabilities,
) -> StaleRunReconciliationService:
    """Inject concrete database and event ports into the Execution owner."""

    return StaleRunReconciliationService(
        lifecycle=lifecycle,
        attempt_lifecycle=attempt_lifecycle,
        v4_capabilities=v4_capabilities,
        transaction_factory=transaction,
        append_event=append_event,
        append_audit_log=append_audit_log,
    )


async def reconcile_worker_stale_runs_with_postgres_claims(
    settings: object,
    *,
    lifecycle: RunLifecycleService,
    attempt_lifecycle: RunAttemptLifecycleService,
    v4_capabilities: WorkerV4Capabilities,
    worker_id: str,
    cancel_requested_after_seconds: int,
) -> list[dict[str, object]]:
    """Run the Execution application service with worker-owned configuration."""

    service = build_stale_run_reconciliation_service(
        lifecycle=lifecycle,
        attempt_lifecycle=attempt_lifecycle,
        v4_capabilities=v4_capabilities,
    )
    return await service.reconcile_postgres_claims(
        worker_id=worker_id,
        limit=int(getattr(settings, "stale_run_reconciliation_limit", 20)),
        stale_after_seconds=int(getattr(settings, "stale_run_reconciliation_seconds", 900)),
        cancel_requested_after_seconds=cancel_requested_after_seconds,
        claim_ttl_seconds=int(
            getattr(settings, "stale_run_reconciliation_claim_ttl_seconds", 300)
        ),
    )
