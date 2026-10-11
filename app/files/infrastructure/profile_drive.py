from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator
from contextlib import AsyncExitStack, contextmanager
from dataclasses import dataclass, field
import hashlib
import logging
import math
import mimetypes
import os
from pathlib import Path
import re
import tempfile
from typing import Any

import anyio
import httpx

from app.files.application.profile_drive import ProfileDriveFile, ProfileDriveTransferError


_SAFE_CONTENT_TYPE_PATTERN = re.compile(
    r"^[a-z0-9][a-z0-9!#$&^_.+-]*/[a-z0-9][a-z0-9!#$&^_.+-]*$"
)
_OFFICE_CONTENT_TYPES = {
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".ppt": "application/vnd.ms-powerpoint",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".xls": "application/vnd.ms-excel",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


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


_LOGGER = logging.getLogger(__name__)
_CLEANUP_TIMEOUT_SECONDS = 5.0


def _log_transfer_warning(code: str, **fields: str) -> None:
    try:
        _LOGGER.warning(code, extra=fields)
    except Exception:
        # A broken handler must not become the operation or cleanup failure.
        pass


@contextmanager
def _transfer_budget(deadline: float) -> Iterator[None]:
    scope = None
    try:
        with anyio.fail_after(max(0.0, deadline - anyio.current_time())) as scope:
            yield
    except TimeoutError as exc:
        if scope is not None and scope.cancel_called:
            raise ProfileDriveTransferError(503, "profile_drive_transfer_timeout") from exc
        raise


async def _close_resource(resource: Any, kind: str) -> None:
    # Each stack callback gets its own shield/budget; one failed close must not
    # prevent the other transport resource from being attempted.
    try:
        with anyio.move_on_after(_CLEANUP_TIMEOUT_SECONDS, shield=True) as scope:
            await resource.aclose()
        if scope.cancel_called:
            _log_transfer_warning("profile_drive_cleanup_timeout", resource_kind=kind)
    except Exception as exc:
        _log_transfer_warning(
            "profile_drive_cleanup_failed", resource_kind=kind, error_type=type(exc).__name__,
        )


async def _close_after_error(resource: Any) -> None:
    try:
        await resource.aclose()
    except (Exception, asyncio.CancelledError) as exc:
        # Preserve the operation's existing error/cancellation, not a secondary
        # cleanup error. No paths, credentials or upstream exception text.
        _log_transfer_warning("profile_drive_cleanup_interrupted", error_type=type(exc).__name__)


@dataclass
class _ProfileDriveFile:
    content_length: int
    content_type: str
    response: Any
    resources: AsyncExitStack
    deadline: float
    read_timeout_seconds: float
    max_bytes: int
    too_large_detail: str
    _closed: bool = field(default=False, init=False)

    def budget(self):
        return _transfer_budget(self.deadline)

    async def __aenter__(self) -> _ProfileDriveFile:
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        if exc is not None:
            await _close_after_error(self)
        else:
            await self.aclose()

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self.resources.aclose()

    async def iter_bytes(self) -> AsyncIterator[bytes]:
        transferred = 0
        try:
            if self._closed:
                raise ProfileDriveTransferError(503, "profile_drive_transfer_closed")
            iterator = self.response.aiter_raw().__aiter__()
            while True:
                remaining = self.deadline - anyio.current_time()
                if remaining <= 0:
                    raise ProfileDriveTransferError(503, "profile_drive_transfer_timeout")
                try:
                    # Do not hold a cancel scope across yield/ASGI send. The
                    # absolute deadline still includes downstream backpressure.
                    with _transfer_budget(min(self.deadline, anyio.current_time() + self.read_timeout_seconds)):
                        chunk = await iterator.__anext__()
                except StopAsyncIteration:
                    break
                transferred += len(chunk)
                if transferred > self.content_length or transferred > self.max_bytes:
                    raise ProfileDriveTransferError(413, self.too_large_detail)
                yield chunk
            if transferred != self.content_length:
                raise ProfileDriveTransferError(503, "profile_drive_transfer_invalid")
        except httpx.TimeoutException as exc:
            await _close_after_error(self)
            raise ProfileDriveTransferError(503, "profile_drive_transfer_timeout") from exc
        except ProfileDriveTransferError:
            await _close_after_error(self)
            raise
        except Exception as exc:
            await _close_after_error(self)
            raise ProfileDriveTransferError(503, "profile_drive_transfer_failed") from exc
        except BaseException:
            await _close_after_error(self)
            raise
        else:
            await self.aclose()


async def open_profile_drive_file(
    *,
    upstream: str,
    ca_cert_file: str,
    jwt: str,
    path: str,
    source_id: str = "profile",
    max_bytes: int,
    require_nonempty: bool,
    not_found_status: int,
    too_large_detail: str,
    empty_detail: str = "empty_file_not_supported",
    read_timeout_seconds: float = 60.0,
    total_timeout_seconds: float = 1800.0,
) -> ProfileDriveFile:
    if not upstream:
        raise ProfileDriveTransferError(503, "profile_drive_transfer_unavailable")
    if any(not math.isfinite(value) or value <= 0 for value in (read_timeout_seconds, total_timeout_seconds)):
        raise ValueError("profile_drive_transfer_timeout_invalid")
    deadline = anyio.current_time() + total_timeout_seconds
    resources = AsyncExitStack()
    verify: bool | str = ca_cert_file.strip() or True
    client = httpx.AsyncClient(
        timeout=httpx.Timeout(read_timeout_seconds, connect=30.0),
        follow_redirects=False,
        trust_env=False,
        verify=verify,
    )
    resources.push_async_callback(_close_resource, client, "client")
    payload = {"path": path}
    if source_id != "profile":
        payload["source"] = source_id
    try:
        with _transfer_budget(deadline):
            response = await client.send(
                client.build_request(
                    "POST", f"{upstream.rstrip('/')}/api/profile-drive/files/content",
                    json=payload,
                    headers={
                        "Authorization": f"Bearer {jwt}",
                        "Accept": "application/octet-stream",
                        "Accept-Encoding": "identity",
                    },
                ),
                stream=True,
            )
        resources.push_async_callback(_close_resource, response, "response")
        if response.status_code != 200:
            raise ProfileDriveTransferError(
                _upstream_status_code(response.status_code, not_found_status=not_found_status),
                "profile_drive_transfer_denied",
            )
        if response.headers.get("content-encoding", "identity").strip().lower() != "identity":
            raise ProfileDriveTransferError(503, "profile_drive_transfer_invalid")
        try:
            content_length = int(response.headers["content-length"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ProfileDriveTransferError(503, "profile_drive_transfer_invalid") from exc
        if content_length < 0 or content_length > max_bytes:
            raise ProfileDriveTransferError(413, too_large_detail)
        if require_nonempty and content_length == 0:
            raise ProfileDriveTransferError(400, empty_detail)
        filename = path.rsplit("/", 1)[-1]
        declared_content_type = str(response.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
        if not _SAFE_CONTENT_TYPE_PATTERN.fullmatch(declared_content_type):
            declared_content_type = "application/octet-stream"
        guessed_content_type = _OFFICE_CONTENT_TYPES.get(
            Path(filename).suffix.lower()
        ) or mimetypes.guess_type(filename)[0]
        content_type = guessed_content_type or declared_content_type or "application/octet-stream"
        return _ProfileDriveFile(
            content_length, content_type, response, resources,
            deadline, read_timeout_seconds, max_bytes, too_large_detail,
        )
    except httpx.TimeoutException as exc:
        await _close_after_error(resources)
        raise ProfileDriveTransferError(503, "profile_drive_transfer_timeout") from exc
    except ProfileDriveTransferError:
        await _close_after_error(resources)
        raise
    except Exception as exc:
        await _close_after_error(resources)
        raise ProfileDriveTransferError(503, "profile_drive_transfer_failed") from exc
    except BaseException:
        await _close_after_error(resources)
        raise


async def download_profile_drive_file(*, transfer: ProfileDriveFile) -> tuple[str, str, int]:
    temporary_path: str | None = None
    try:
        file_descriptor, temporary_path = tempfile.mkstemp(prefix="ai-platform-profile-drive-")
        os.close(file_descriptor)
        digest = hashlib.sha256()
        transferred = 0
        async with await anyio.open_file(temporary_path, "wb") as destination:
            async for chunk in transfer.iter_bytes():
                transferred += len(chunk)
                digest.update(chunk)
                await destination.write(chunk)
        await transfer.aclose()
        return temporary_path, digest.hexdigest(), transferred
    except BaseException:
        if temporary_path is not None:
            try:
                Path(temporary_path).unlink(missing_ok=True)
            except OSError as exc:
                _log_transfer_warning("profile_drive_temp_cleanup_failed", error_type=type(exc).__name__)
        await _close_after_error(transfer)
        raise


class ProfileDriveTransferAdapter:
    async def open_profile_drive_file(self, **kwargs: Any) -> ProfileDriveFile:
        return await open_profile_drive_file(**kwargs)

    async def download_profile_drive_file(self, **kwargs: Any) -> tuple[str, str, int]:
        return await download_profile_drive_file(**kwargs)
