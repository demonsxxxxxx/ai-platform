from __future__ import annotations

from pydantic import BaseModel, Field


MAX_UPLOAD_BYTES = 512 * 1024 * 1024


class MultipartUploadCreateRequest(BaseModel):
    workspace_id: str = "default"
    session_id: str | None = None
    name: str = Field(min_length=1, max_length=255)
    content_type: str = "application/octet-stream"
    size_bytes: int = Field(gt=0, le=MAX_UPLOAD_BYTES)


class MultipartUploadPart(BaseModel):
    part_number: int = Field(ge=1, le=10_000)
    etag: str = Field(min_length=1, max_length=512)


class MultipartUploadCompleteRequest(BaseModel):
    parts: list[MultipartUploadPart] = Field(min_length=1, max_length=10_000)
