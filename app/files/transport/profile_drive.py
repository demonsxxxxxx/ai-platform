from __future__ import annotations

from collections.abc import AsyncIterable
from typing import Any

from fastapi.responses import StreamingResponse


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
