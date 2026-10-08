"""Compose Context application use cases with PostgreSQL adapters."""

from pathlib import Path

from app.context.retrieval import (
    ContextRetrievalAuthority,
    ContextRetrievalIdentity as ContextRetrievalIdentity,
)
from app.db import transaction
from app.storage import ObjectStorage, ObjectStorageSizeLimitError, run_storage_io
from app.context.infrastructure import snapshot_postgres as context_snapshot_postgres
from app.context.infrastructure import sources_postgres as context_sources_postgres
from app.context.file_continuity import materialize_run_context_files
from app.context_builder import ensure_public_context_provenance
from app.context_manifest import CONTEXT_MANIFEST_SCHEMA_VERSION, sanitize_context_manifest_payload
from app.control_plane_contracts import CONTEXT_SNAPSHOT_SCHEMA_VERSION

from app.context.api import (
    ProviderSessionContinuityError,
    materialize_worker_context_snapshot,
    project_worker_snapshot_ref,
)
from app.context.application.provider_sessions import (
    ProviderSessionUseCases,
    configure_provider_session_use_cases,
)
from app.context.infrastructure.provider_epochs import PostgresProviderEpochRepository


def configure_context_services() -> None:
    configure_provider_session_use_cases(
        ProviderSessionUseCases(PostgresProviderEpochRepository())
    )


def worker_context_snapshot_ref_from_row(row: dict) -> dict:
    return project_worker_snapshot_ref(
        row,
        public_provenance=ensure_public_context_provenance,
        sanitize_manifest=sanitize_context_manifest_payload,
        snapshot_schema_version=CONTEXT_SNAPSHOT_SCHEMA_VERSION,
        manifest_schema_version=CONTEXT_MANIFEST_SCHEMA_VERSION,
    )


async def materialize_queued_worker_context_snapshot(
    conn, *, payload, run_identity, context_projector,
):
    identity = {**run_identity, "engine": "claude" if payload.executor_type == "claude-agent-worker" else ""}
    try:
        context = await materialize_worker_context_snapshot(
            conn, identity=identity, context_snapshot_id=str(payload.context_snapshot_id or ""),
            snapshot_loader=context_snapshot_postgres.get_context_snapshot_for_worker,
            context_projector=context_projector,
        )
    except ProviderSessionContinuityError as exc:
        return None, exc.code
    return context, None


async def materialize_worker_context_files(*, payload, workspace: Path):
    """Stage scoped files with Context-owned persistence and storage."""
    if workspace.exists() and workspace.is_symlink():
        raise ValueError("run workspace must not be a symlink")
    return await materialize_run_context_files(
        repository=context_sources_postgres,
        transaction_factory=transaction, storage=ObjectStorage(), storage_io=run_storage_io,
        storage_size_limit_error=ObjectStorageSizeLimitError, workspace=workspace,
        tenant_id=payload.tenant_id, workspace_id=payload.workspace_id,
        user_id=payload.user_id, session_id=payload.session_id,
        run_id=payload.run_id, file_ids=payload.file_ids,
    )


def worker_context_retrieval_authority(workspace: Path) -> ContextRetrievalAuthority:
    return ContextRetrievalAuthority.for_workspace_transaction(
        transaction, ObjectStorage(), workspace, storage_io=run_storage_io,
    )
