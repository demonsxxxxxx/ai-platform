"""Compose Context application use cases with PostgreSQL adapters."""

from app.context.infrastructure import snapshot_postgres as context_snapshot_postgres
from app.context.infrastructure import sources_postgres as context_sources_postgres

from app.context.file_continuity import materialize_run_context_files

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
            snapshot_loader=context_snapshot_postgres.get_context_snapshot_for_worker,
            message_loader=context_sources_postgres.list_scoped_context_messages,
            history_page_loader=context_sources_postgres.list_session_context_messages,
            context_projector=context_projector,
        )
    except ProviderSessionContinuityError as exc:
        return None, exc.code
    return context, None


async def materialize_worker_context_files(
    *, transaction_factory, storage, storage_io, storage_size_limit_error,
    workspace, tenant_id, workspace_id, user_id, session_id, run_id, file_ids,
):
    """Bind the scoped file reader at the worker composition boundary."""
    return await materialize_run_context_files(
        repository=context_sources_postgres,
        transaction_factory=transaction_factory, storage=storage, storage_io=storage_io,
        storage_size_limit_error=storage_size_limit_error, workspace=workspace,
        tenant_id=tenant_id, workspace_id=workspace_id, user_id=user_id,
        session_id=session_id, run_id=run_id, file_ids=file_ids,
    )
