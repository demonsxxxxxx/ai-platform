"""Compose Context application use cases with PostgreSQL adapters."""

from app import repositories
from app.context.api import (
    materialize_worker_context_snapshot, prepare_checkpoint_for_run,
)
from app.context.application.provider_sessions import (
    ProviderSessionUseCases,
    configure_provider_session_use_cases,
)
from app.context.application.checkpoint_build import (
    ConversationCheckpointBuilder,
    configure_checkpoint_builder,
)
from app.context.application.checkpoints import (
    configure_checkpoint_loader, configure_checkpoint_usage_loader,
)
from app.context.infrastructure.checkpoint_build_postgres import PostgresCheckpointBuildRepository
from app.context.infrastructure.checkpoints_postgres import (
    load_ready_checkpoint, load_checkpoint_usage_for_run,
)
from app.context.infrastructure.provider_epochs import PostgresProviderEpochRepository
from app.context.infrastructure.sources_postgres import list_session_context_messages
from app.execution.api import count_checkpoint_input_for_run, summarize_context_for_run


def configure_context_services() -> None:
    configure_checkpoint_loader(load_ready_checkpoint)
    configure_checkpoint_usage_loader(load_checkpoint_usage_for_run)
    configure_checkpoint_builder(ConversationCheckpointBuilder(
        repository=PostgresCheckpointBuildRepository(),
        page_loader=list_session_context_messages,
        checkpoint_loader=load_ready_checkpoint,
        count_tokens=count_checkpoint_input_for_run,
        summarize=summarize_context_for_run,
    ))
    configure_provider_session_use_cases(
        ProviderSessionUseCases(PostgresProviderEpochRepository())
    )




async def prepare_worker_checkpoint(*, transaction_factory, payload, principal,
                                    reconciliation, queue_identity):
    if principal is None:
        return None, False
    try:
        checkpoint_id = await prepare_checkpoint_for_run(
            transaction_factory=transaction_factory, tenant_id=payload.tenant_id,
            run_id=payload.run_id, context_snapshot_id=str(payload.context_snapshot_id or ""),
            executor_type=payload.executor_type, reconciliation=reconciliation,
            queue_identity=queue_identity,
        )
        return checkpoint_id, False
    except Exception:  # noqa: BLE001 - fail closed through Worker terminalization.
        return None, True


async def materialize_queued_worker_context_snapshot(
    conn, *, payload, run_identity, context_projector, prepared_checkpoint_id,
):
    identity = {**run_identity, "engine": "claude" if payload.executor_type == "claude-agent-worker" else ""}
    return await materialize_worker_context_snapshot(
        conn, identity=identity, context_snapshot_id=str(payload.context_snapshot_id or ""),
        snapshot_loader=repositories.get_context_snapshot_for_worker,
        message_loader=repositories.list_scoped_context_messages,
        history_page_loader=repositories.list_session_context_messages,
        context_projector=context_projector,
        prepared_checkpoint_id=prepared_checkpoint_id,
    )
