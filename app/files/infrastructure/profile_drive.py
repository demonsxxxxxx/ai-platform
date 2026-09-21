from __future__ import annotations

from dataclasses import dataclass
import hashlib
import mimetypes
import os
from pathlib import Path
import re
import tempfile
from typing import Any

import anyio
import httpx


@dataclass(frozen=True, slots=True)
class ProfileDriveTransferError(Exception):
    status_code: int
    detail: str

    def __str__(self) -> str:
        return self.detail


_SAFE_CONTENT_TYPE_PATTERN = re.compile(
    r"^[a-z0-9][a-z0-9!#$&^_.+-]*/[a-z0-9][a-z0-9!#$&^_.+-]*$"
)
def _upstream_status_code(status_code: int, *, not_found_status: int) -> int:
    if status_code == 413:
        return 413
    if status_code in {401, 409}:
        return 409
    if status_code == 404:
        return not_found_status
    if status_code in {400, 403, 422}:
        return 403
    return 503


async def open_profile_drive_file(
    *,
    upstream: str,
    ca_cert_file: str,
    jwt: str,
    path: str,
    max_bytes: int,
    require_nonempty: bool,
    not_found_status: int,
    too_large_detail: str,
    empty_detail: str = "empty_file_not_supported",
) -> tuple[Any, Any, int, str]:
    if not upstream:
        raise ProfileDriveTransferError(503, "profile_drive_transfer_unavailable")

    verify: bool | str = ca_cert_file.strip() or True
    client = httpx.AsyncClient(
        timeout=httpx.Timeout(None, connect=30.0),
        follow_redirects=False,
        trust_env=False,
        verify=verify,
    )
    try:
        response = await client.send(
            client.build_request(
                "POST",
                f"{upstream.rstrip('/')}/api/profile-drive/files/content",
                json={"path": path},
                headers={
                    "Authorization": f"Bearer {jwt}",
                    "Accept": "application/octet-stream",
                    "Accept-Encoding": "identity",
                },
            ),
            stream=True,
        )
    except Exception as exc:
        await client.aclose()
        raise ProfileDriveTransferError(503, "profile_drive_transfer_failed") from exc

    if response.status_code != 200:
        status_code = _upstream_status_code(
            response.status_code,
            not_found_status=not_found_status,
        )
        await response.aclose()
        await client.aclose()
        raise ProfileDriveTransferError(status_code, "profile_drive_transfer_denied")
    if response.headers.get("content-encoding", "identity").strip().lower() != "identity":
        await response.aclose()
        await client.aclose()
        raise ProfileDriveTransferError(503, "profile_drive_transfer_invalid")
    try:
        content_length = int(response.headers["content-length"])
    except (KeyError, TypeError, ValueError) as exc:
        await response.aclose()
        await client.aclose()
        raise ProfileDriveTransferError(503, "profile_drive_transfer_invalid") from exc
    if content_length < 0 or content_length > max_bytes:
        await response.aclose()
        await client.aclose()
        raise ProfileDriveTransferError(413, too_large_detail)
    if require_nonempty and content_length == 0:
        await response.aclose()
        await client.aclose()
        raise ProfileDriveTransferError(400, empty_detail)

    filename = path.rsplit("/", 1)[-1]
    declared_content_type = str(response.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
    if not _SAFE_CONTENT_TYPE_PATTERN.fullmatch(declared_content_type):
        declared_content_type = "application/octet-stream"
    guessed_content_type = mimetypes.guess_type(filename)[0]
    content_type = guessed_content_type or declared_content_type or "application/octet-stream"
    return client, response, content_length, content_type


async def download_profile_drive_file(
    *,
    client: Any,
    response: Any,
    content_length: int,
    max_bytes: int,
    too_large_detail: str,
) -> tuple[str, str, int]:
    file_descriptor, temporary_path = tempfile.mkstemp(prefix="ai-platform-profile-drive-")
    os.close(file_descriptor)
    digest = hashlib.sha256()
    transferred = 0
    try:
        async with await anyio.open_file(temporary_path, "wb") as destination:
            async for chunk in response.aiter_raw():
                transferred += len(chunk)
                if transferred > content_length or transferred > max_bytes:
                    raise ProfileDriveTransferError(413, too_large_detail)
                digest.update(chunk)
                await destination.write(chunk)
        if transferred != content_length:
            raise ProfileDriveTransferError(503, "profile_drive_transfer_invalid")
        return temporary_path, digest.hexdigest(), transferred
    except BaseException:
        Path(temporary_path).unlink(missing_ok=True)
        raise
    finally:
        await response.aclose()
        await client.aclose()
