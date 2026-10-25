"""Private octet-stream response; temporary file ownership spans ASGI send."""

import json
from pathlib import Path

from starlette.responses import StreamingResponse

from app.context.domain.artifact_transfer import (
    ARTIFACT_TRANSFER_CHUNK_BYTES,
    ARTIFACT_TRANSFER_HEADER,
)


class ArtifactTransferResponse(StreamingResponse):
    def __init__(self, source, downloaded):
        self.path = Path(downloaded.path)

        async def chunks():
            with self.path.open("rb") as stream:
                while chunk := stream.read(ARTIFACT_TRANSFER_CHUNK_BYTES):
                    yield chunk

        super().__init__(
            chunks(),
            media_type="application/octet-stream",
            headers={
                "Content-Length": str(downloaded.size_bytes),
                "Cache-Control": "no-store",
                "Content-Encoding": "identity",
                ARTIFACT_TRANSFER_HEADER: json.dumps(
                    source.metadata(sha256=downloaded.sha256),
                    ensure_ascii=True,
                    separators=(",", ":"),
                ),
            },
        )

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            try:
                await self.body_iterator.aclose()
            finally:
                self.path.unlink(missing_ok=True)
