"""Compose Runs executor terminalization with Streaming publication records."""

from typing import Any
from functools import partial
from app.context.api import provider_session_verification_scope

from app.runs import api as runs_api
from app.streaming.infrastructure import run_events_postgres

from app.bootstrap.worker_early_failure import build_worker_early_failure_service
from app.bootstrap.conversation_continuation import (
    release_predispatch_continuation, settle_predispatch_continuation,
)


def build_worker_execution_terminal_service(
    transaction_factory: Any, *, capabilities: Any,
) -> runs_api.WorkerExecutionTerminalService:
    return runs_api.WorkerExecutionTerminalService(
        transaction_factory=transaction_factory,
        prepare_pending_authority=capabilities.pending_admissions.prepare_pending_authority_in_transaction,
        append_event=run_events_postgres.append_event,
        fail_run=build_worker_early_failure_service().fail_run,
        settle_continuation=settle_predispatch_continuation,
        release_continuation_lease=release_predispatch_continuation,
        verification_scope=partial(provider_session_verification_scope, transaction_factory),
    )
