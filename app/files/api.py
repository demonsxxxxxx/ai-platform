from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from app.files.application.upload_sessions import (
    FileUploadPersistence,
    configure_file_upload_persistence,
    file_upload_persistence,
)

MAX_UPLOAD_BYTES = 512 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class MultipartUploadCreateRequest:
    workspace_id: str = "default"
    session_id: str | None = None
    name: str = ""
    content_type: str = "application/octet-stream"
    size_bytes: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.workspace_id, str) or not self.workspace_id:
            raise ValueError("workspace_id is invalid")
        if self.session_id is not None and not isinstance(self.session_id, str):
            raise ValueError("session_id is invalid")
        if not isinstance(self.content_type, str) or not self.content_type:
            raise ValueError("content_type is invalid")
        if not isinstance(self.name, str) or not 1 <= len(self.name) <= 255:
            raise ValueError("name must contain 1 to 255 characters")
        if type(self.size_bytes) is not int or not 0 < self.size_bytes <= MAX_UPLOAD_BYTES:
            raise ValueError("size_bytes is invalid")


@dataclass(frozen=True, slots=True)
class MultipartUploadPart:
    part_number: int
    etag: str

    def __post_init__(self) -> None:
        if type(self.part_number) is not int or not 1 <= self.part_number <= 10_000:
            raise ValueError("part_number is invalid")
        if not isinstance(self.etag, str) or not 1 <= len(self.etag) <= 512:
            raise ValueError("etag is invalid")


@dataclass(frozen=True, slots=True)
class MultipartUploadCompleteRequest:
    parts: tuple[MultipartUploadPart, ...]

    def __post_init__(self) -> None:
        parts = tuple(self.parts)
        if not 1 <= len(parts) <= 10_000 or not all(isinstance(part, MultipartUploadPart) for part in parts):
            raise ValueError("parts are invalid")
        object.__setattr__(self, "parts", parts)


def _request_mapping(value: object) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("request body must be an object")
    return value


def parse_multipart_upload_create_request(value: object) -> MultipartUploadCreateRequest:
    if isinstance(value, MultipartUploadCreateRequest):
        return value
    body = _request_mapping(value)
    return MultipartUploadCreateRequest(
        workspace_id=str(body.get("workspace_id", "default")),
        session_id=body.get("session_id"),
        name=body.get("name", ""),
        content_type=body.get("content_type", "application/octet-stream"),
        size_bytes=body.get("size_bytes", 0),
    )


def parse_multipart_upload_complete_request(value: object) -> MultipartUploadCompleteRequest:
    if isinstance(value, MultipartUploadCompleteRequest):
        return value
    body = _request_mapping(value)
    raw_parts = body.get("parts")
    if not isinstance(raw_parts, list) or any(not isinstance(part, Mapping) for part in raw_parts):
        raise ValueError("parts must be a list of objects")
    return MultipartUploadCompleteRequest(
        tuple(MultipartUploadPart(part_number=part.get("part_number"), etag=part.get("etag")) for part in raw_parts)
    )


async def get_file_storage_usage(conn: Any, **kwargs: Any) -> dict[str, int]:
    return await file_upload_persistence().get_file_storage_usage(conn, **kwargs)


async def create_file_upload_session(conn: Any, **kwargs: Any) -> None:
    await file_upload_persistence().create_file_upload_session(conn, **kwargs)


async def get_authorized_file_upload_session(conn: Any, **kwargs: Any) -> dict[str, Any] | None:
    return await file_upload_persistence().get_authorized_file_upload_session(conn, **kwargs)


async def claim_file_upload_session(conn: Any, **kwargs: Any) -> bool:
    return await file_upload_persistence().claim_file_upload_session(conn, **kwargs)


async def complete_file_upload_session(conn: Any, **kwargs: Any) -> None:
    await file_upload_persistence().complete_file_upload_session(conn, **kwargs)


async def expire_file_upload_sessions(conn: Any, **kwargs: Any) -> list[dict[str, Any]]:
    return await file_upload_persistence().expire_file_upload_sessions(conn, **kwargs)


async def retry_expired_file_upload_session(conn: Any, **kwargs: Any) -> None:
    await file_upload_persistence().retry_expired_file_upload_session(conn, **kwargs)


async def delete_expired_file_upload_session(conn: Any, **kwargs: Any) -> None:
    await file_upload_persistence().delete_expired_file_upload_session(conn, **kwargs)


async def abort_file_upload_session(conn: Any, **kwargs: Any) -> None:
    await file_upload_persistence().abort_file_upload_session(conn, **kwargs)


__all__ = [
    "MAX_UPLOAD_BYTES",
    "FileUploadPersistence",
    "MultipartUploadCompleteRequest",
    "MultipartUploadCreateRequest",
    "MultipartUploadPart",
    "abort_file_upload_session",
    "claim_file_upload_session",
    "complete_file_upload_session",
    "configure_file_upload_persistence",
    "create_file_upload_session",
    "delete_expired_file_upload_session",
    "expire_file_upload_sessions",
    "get_authorized_file_upload_session",
    "get_file_storage_usage",
    "parse_multipart_upload_complete_request",
    "parse_multipart_upload_create_request",
    "retry_expired_file_upload_session",
]
