"""Compose Context application use cases with PostgreSQL adapters."""

from app import repositories
from app.context.api import (
    ProviderSessionContinuityError,
    materialize_worker_context_snapshot,
)
from app.context.application.provider_sessions import (
    ProviderSessionUseCases,
    configure_provider_session_use_cases,
)
from app.context.application.checkpoints import configure_checkpoint_loader
from app.context.infrastructure.checkpoints_postgres import load_ready_checkpoint
from app.context.infrastructure.provider_epochs import PostgresProviderEpochRepository


def configure_context_services() -> None:
    configure_checkpoint_loader(load_ready_checkpoint)
    configure_provider_session_use_cases(
        ProviderSessionUseCases(PostgresProviderEpochRepository())
    )


async def materialize_queued_worker_context_snapshot(
    conn, *, payload, run_identity, context_projector,
):
    identity = {**run_identity, "engine": "claude" if payload.executor_type == "claude-agent-worker" else ""}
    try:
        context = await materialize_worker_context_snapshot(
            conn, identity=identity, context_snapshot_id=str(payload.context_snapshot_id or ""),
            snapshot_loader=repositories.get_context_snapshot_for_worker,
            message_loader=repositories.list_scoped_context_messages,
            history_page_loader=repositories.list_session_context_messages,
            context_projector=context_projector,
        )
    except ProviderSessionContinuityError as exc:
        return None, exc.code
    return context, None
