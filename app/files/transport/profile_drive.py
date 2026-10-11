from __future__ import annotations

import logging
from typing import Any

from fastapi import HTTPException
from fastapi.responses import StreamingResponse

from app.files.application.profile_drive import ProfileDriveFile, ProfileDriveTransferError


class _ProfileDriveStreamingResponse(StreamingResponse):
    def __init__(self, transfer: ProfileDriveFile) -> None:
        self.transfer = transfer
        super().__init__(
            transfer.iter_bytes(),
            media_type="application/octet-stream",
            headers={
                "Cache-Control": "private, no-store",
                "Content-Length": str(transfer.content_length),
            },
        )

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        # A disconnect/send failure can happen before the iterator ever starts.
        # The response owns the transfer across the entire ASGI lifecycle.
        started = False

        async def tracked_send(message: Any) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                # send may consume the start message and then block/fail. Once
                # attempted, conservatively forbid any replacement status.
                started = True
            await send(message)

        try:
            async with self.transfer:
                with self.transfer.budget():
                    await super().__call__(scope, receive, tracked_send)
        except ProfileDriveTransferError as exc:
            try:
                logging.getLogger(__name__).warning(
                    "profile_drive_stream_failed", extra={"error_code": exc.detail}
                )
            except Exception:
                pass
            if not started:
                raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
            # Status has already been sent. Preserve an incomplete stream; never
            # emit a second HTTP response or a false successful completion.
            raise


def profile_drive_streaming_response(transfer: ProfileDriveFile) -> Any:
    return _ProfileDriveStreamingResponse(transfer)
