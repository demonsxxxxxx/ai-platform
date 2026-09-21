from __future__ import annotations

from collections.abc import AsyncIterable
from typing import Any

from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator


class ProfileDriveFileImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, max_length=1024)

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        normalized = value.replace("\\", "/")
        parts = normalized.split("/")
        if (
            "\x00" in value
            or normalized.startswith("/")
            or any(part in {"", ".", ".."} for part in parts)
        ):
            raise ValueError("profile_drive_path_invalid")
        return normalized


def profile_drive_streaming_response(
    content: AsyncIterable[bytes],
    *,
    content_length: int,
) -> Any:
    return StreamingResponse(
        content,
        media_type="application/octet-stream",
        headers={
            "Cache-Control": "private, no-store",
            "Content-Length": str(content_length),
        },
    )
