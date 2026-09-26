"""Concrete composition for Run lifecycle application services."""

from __future__ import annotations

from app.db import transaction
from app.platform.public_payload import sanitize_public_payload, sanitize_public_text
from app.platform.tracing import standard_trace_id
from app.runs.application.cancellation import RunCancellationUseCase
from app.runs.api import RunAttemptLifecycleService
from app.runs.application.lifecycle import RunLifecycleService
from app.runs.application.provider_terminalization import progress_run_terminalization_with_context
from app.runs.infrastructure.postgres import (
    PostgresRunCancellationPersistence,
    load_current_terminal_event_fact,
)
from app.runs.infrastructure.lifecycle_postgres import (
    PostgresRunLifecyclePersistence,
    require_run_result_size,
)
from app.settings import get_settings
from app.streaming.infrastructure.run_events_postgres import append_event
from app.identity.infrastructure.audit_postgres import append_audit_log
from app.sandbox.infrastructure.leases_postgres import list_active_sandbox_leases_for_run
from app.streaming.infrastructure.run_v4_events import PostgresRunCancellationEventWriter


def build_run_lifecycle_service() -> RunLifecycleService:
    """Compose the Runs lifecycle application service and its concrete ports."""

    return RunLifecycleService(
        persistence=PostgresRunLifecyclePersistence(),
        append_event=append_event,
        append_audit_log=append_audit_log,
        validate_result_size=require_run_result_size,
        sanitize_payload=sanitize_public_payload,
        sanitize_text=sanitize_public_text,
        make_trace_id=standard_trace_id,
    )


def build_run_cancellation_use_case(
    *,
    attempt_lifecycle: RunAttemptLifecycleService,
    lifecycle: RunLifecycleService,
) -> RunCancellationUseCase:
    async def progress_terminalization(conn: object, *, tenant_id: str, run_id: str):
        return await progress_run_terminalization_with_context(
            conn,
            progress_terminalization=lifecycle.progress_run_terminalization,
            tenant_id=tenant_id,
            run_id=run_id,
        )

    return RunCancellationUseCase(
        transaction_factory=transaction,
        persistence=PostgresRunCancellationPersistence(
            attempt_lifecycle=attempt_lifecycle,
            append_event=append_event,
            append_audit_log=append_audit_log,
            list_active_sandbox_leases=list_active_sandbox_leases_for_run,
        ),
        event_writer=PostgresRunCancellationEventWriter(
            authority_secret=get_settings().ai_session_secret,
            load_terminal_event_fact=load_current_terminal_event_fact,
        ),
        progress_terminalization=progress_terminalization,
    )
