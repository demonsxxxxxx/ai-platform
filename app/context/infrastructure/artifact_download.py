"""Bounded object download with physical-completion cleanup on abandonment."""

from pathlib import Path

from botocore.exceptions import ClientError

from app.context.domain.artifact_transfer import ArtifactTransferError
from app.platform.storage_io_completion import StorageIOCompletion


def _discard(downloaded):
    if downloaded is not None:
        Path(downloaded.path).unlink(missing_ok=True)


async def download_artifact_object(
    storage, storage_io, storage_key, max_bytes, *, close_storage=True
):
    completion = StorageIOCompletion()
    downloaded = None
    try:
        downloaded = await storage_io(
            storage.download_to_tempfile,
            storage_key=storage_key,
            max_bytes=max_bytes,
            completion=completion,
        )
    except BaseException as error:
        await completion.settle()
        if completion.submitted:
            try:
                downloaded = completion.result()
            except BaseException:
                pass  # The storage operation owns cleanup until successful handoff.
        try:
            _discard(downloaded)
        finally:
            if close_storage:
                try:
                    storage.close()
                except Exception:
                    pass  # Preserve the primary transfer failure or cancellation.
        if isinstance(error, ClientError) and error.response.get("Error", {}).get(
            "Code"
        ) in {"NoSuchKey", "404"}:
            raise ArtifactTransferError("context_artifact_unavailable") from error
        raise
    try:
        if close_storage:
            storage.close()
    except BaseException:
        _discard(downloaded)
        raise
    return downloaded
