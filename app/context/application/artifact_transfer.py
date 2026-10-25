"""Authorize immutable object identity before any artifact transfer I/O."""

from app.artifacts.api import ARTIFACT_MAX_FILE_BYTES as ARTIFACT_STAGE_MAX_BYTES

from app.context.domain.artifact_transfer import (
    ArtifactTransferError,
    ArtifactTransferSource,
)


async def prepare_artifact_transfer(retrieval, identity, arguments):
    action, values = retrieval._validated_arguments(
        "stage_run_artifact_to_workspace", arguments
    )
    scope = retrieval._scoped_identity(identity, require_agent=False)
    artifact_id = retrieval._required_identifier(values, "artifact_id")
    row = await retrieval._get_artifact_row(
        **retrieval._run_identity(scope), artifact_id=artifact_id
    )
    try:
        cap = int(values.get("max_bytes", ARTIFACT_STAGE_MAX_BYTES))
    except (TypeError, ValueError):
        cap = ARTIFACT_STAGE_MAX_BYTES
    return ArtifactTransferSource.from_row(
        row,
        artifact_id=artifact_id,
        max_bytes=min(ARTIFACT_STAGE_MAX_BYTES, max(1, cap)),
    )


async def download_artifact_transfer(source, download):
    downloaded = await download(source.storage_key, source.size_bytes)
    try:
        if (
            downloaded.size_bytes != source.size_bytes
            or source.expected_sha256 is not None
            and downloaded.sha256 != source.expected_sha256
        ):
            raise ArtifactTransferError("context_artifact_identity_mismatch")
        return downloaded
    except BaseException:
        from pathlib import Path

        Path(downloaded.path).unlink(missing_ok=True)
        raise
