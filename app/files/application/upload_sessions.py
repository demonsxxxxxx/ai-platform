from __future__ import annotations

from typing import Any, Protocol


class FileUploadPersistence(Protocol):
    async def get_file_storage_usage(self, conn: Any, **kwargs: Any) -> dict[str, int]: ...

    async def create_file_upload_session(self, conn: Any, **kwargs: Any) -> None: ...

    async def get_authorized_file_upload_session(self, conn: Any, **kwargs: Any) -> dict[str, Any] | None: ...

    async def claim_file_upload_session(self, conn: Any, **kwargs: Any) -> bool: ...

    async def complete_file_upload_session(self, conn: Any, **kwargs: Any) -> None: ...

    async def expire_file_upload_sessions(self, conn: Any, **kwargs: Any) -> list[dict[str, Any]]: ...

    async def retry_expired_file_upload_session(self, conn: Any, **kwargs: Any) -> None: ...

    async def delete_expired_file_upload_session(self, conn: Any, **kwargs: Any) -> None: ...

    async def abort_file_upload_session(self, conn: Any, **kwargs: Any) -> None: ...


_persistence: FileUploadPersistence | None = None


def configure_file_upload_persistence(persistence: FileUploadPersistence) -> None:
    global _persistence
    _persistence = persistence


def file_upload_persistence() -> FileUploadPersistence:
    if _persistence is None:
        raise RuntimeError("file upload persistence is not configured")
    return _persistence
