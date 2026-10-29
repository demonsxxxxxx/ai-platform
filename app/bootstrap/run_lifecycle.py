"""Concrete composition for Run lifecycle application services."""

from __future__ import annotations

import logging

from app.db import transaction
from app.bootstrap.sandbox_cleanup import build_sandbox_runtime_cleanup
from app.queue import remove_queued_run
from app.runtime.sandbox.container_provider import create_container_provider
from app.platform.public_payload import sanitize_public_payload, sanitize_public_text
from app.platform.tracing import standard_trace_id
from app.runs.application.cancellation import (
    RunCancellationDeliveryDeferred,
    RunCancellationPostProcessor,
    RunCancellationUseCase,
)
from app.bootstrap.sandbox_cancel_delivery import build_sandbox_cancel_delivery_service
from app.runs.api import RunAttemptLifecycleService, progress_run_terminalization
from app.runs.application.lifecycle import RunLifecycleService
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
from app.streaming.api import V4PublicationTransportUnavailable, admit_v4_stream, publish_run_event


def _warn_cancellation_publication(message: str, run_id: str, error: Exception) -> None:
    transport_error = isinstance(error, V4PublicationTransportUnavailable)
    safe_delivery_error = isinstance(error, RunCancellationDeliveryDeferred)
    logging.getLogger(__name__).warning(
        message,
        extra={
            "run_id": run_id,
            "error": error.error_code if transport_error or safe_delivery_error else type(error).__name__,
        },
        exc_info=not (transport_error or safe_delivery_error),
    )


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
        return await progress_run_terminalization(
            conn,
            lifecycle=lifecycle,
            tenant_id=tenant_id,
            run_id=run_id,
        )

    sandbox_cleanup = build_sandbox_runtime_cleanup()
    cancel_delivery = build_sandbox_cancel_delivery_service()
    persistence = PostgresRunCancellationPersistence(
        attempt_lifecycle=attempt_lifecycle,
        append_event=append_event,
        append_audit_log=append_audit_log,
        list_active_sandbox_leases=list_active_sandbox_leases_for_run,
    )

    async def handoff_cancelled_attempt(**kwargs: object) -> bool:
        async with transaction() as conn:
            return await persistence.handoff_cancelled_attempt(conn, **kwargs)

    return RunCancellationUseCase(
        transaction_factory=transaction,
        persistence=persistence,
        event_writer=PostgresRunCancellationEventWriter(
            authority_secret=get_settings().ai_session_secret,
            load_terminal_event_fact=load_current_terminal_event_fact,
        ),
        progress_terminalization=progress_terminalization,
        stage_cancel_delivery=cancel_delivery.stage_in_transaction,
        post_processor=RunCancellationPostProcessor(
            queue_cleanup=remove_queued_run,
            sandbox_cleanup=sandbox_cleanup,
            provider_factory=create_container_provider,
            transaction_factory=transaction,
            admit_stream=admit_v4_stream,
            publish_event=publish_run_event,
            warn=_warn_cancellation_publication,
            handoff_cancelled_attempt=handoff_cancelled_attempt,
            deliver_cancel=cancel_delivery.deliver_once,
        ),
    )
