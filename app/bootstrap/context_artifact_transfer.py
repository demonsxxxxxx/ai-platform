"""Compose the Context artifact broker with existing callback authorities."""

from app.context.application.artifact_broker import broker_artifact_transfer
from app.context.domain.artifact_transfer import ArtifactTransferError
from app.storage import ObjectStorageSizeLimitError
from app.context.infrastructure.artifact_download import download_artifact_object
from app.context.retrieval import ContextRetrievalAuthority
from app.context.transport.artifact_transfer import ArtifactTransferResponse


async def build_artifact_transfer_response(
    request,
    *,
    transaction,
    lock_authority,
    load_snapshot,
    storage_factory,
    storage_io,
    tools,
    record_event,
):
    async def download(storage_key, max_bytes):
        try:
            return await download_artifact_object(
                storage_factory(), storage_io, storage_key, max_bytes
            )
        except ObjectStorageSizeLimitError as exc:
            raise ArtifactTransferError("context_artifact_identity_mismatch") from exc

    source, downloaded = await broker_artifact_transfer(
        request,
        transaction=transaction,
        lock_authority=lock_authority,
        load_snapshot=load_snapshot,
        retrieval_factory=lambda conn: ContextRetrievalAuthority.for_connection(
            conn, None
        ),
        download=download,
        tools=tools,
        record_event=record_event,
    )
    try:
        return ArtifactTransferResponse(source, downloaded)
    except BaseException:
        from pathlib import Path

        Path(downloaded.path).unlink(missing_ok=True)
        raise
