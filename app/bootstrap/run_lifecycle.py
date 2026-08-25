"""Concrete composition for Run lifecycle application services."""

from __future__ import annotations

from typing import cast

from app import repositories
from app.db import transaction
from app.runs.application.cancellation import (
    RunCancellationUseCase,
    RunTerminalizationProgressor,
)
from app.runs.infrastructure.postgres import (
    PostgresRunCancellationPersistence,
    load_current_terminal_event_fact,
)
from app.settings import get_settings
from app.streaming.infrastructure.run_v4_events import PostgresRunCancellationEventWriter


def build_run_cancellation_use_case() -> RunCancellationUseCase:
    return RunCancellationUseCase(
        transaction_factory=transaction,
        persistence=PostgresRunCancellationPersistence(
            append_event=repositories.append_event,
            append_audit_log=repositories.append_audit_log,
            list_active_sandbox_leases=repositories.list_active_sandbox_leases_for_run,
        ),
        event_writer=PostgresRunCancellationEventWriter(
            authority_secret=get_settings().ai_session_secret,
            load_terminal_event_fact=load_current_terminal_event_fact,
        ),
        progress_terminalization=cast(
            RunTerminalizationProgressor,
            repositories.progress_run_tool_permission_terminalization,
        ),
    )
