from __future__ import annotations

from collections.abc import AsyncIterable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol


class ProfileDriveTransferError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


@dataclass(frozen=True, slots=True)
class ProfileDriveFileImportRequest:
    path: str
    source_id: str = "profile"

    def __post_init__(self) -> None:
        if not isinstance(self.path, str) or not isinstance(self.source_id, str):
            raise ValueError("profile_drive_path_invalid")
        source_id = self.source_id.strip().lower()
        if source_id not in {"profile", "public"}:
            raise ValueError("profile_drive_source_invalid")
        normalized = self.path.replace("\\", "/")
        parts = normalized.split("/")
        if (
            not 1 <= len(normalized) <= 1024
            or "\x00" in normalized
            or normalized.startswith("/")
            or any(part in {"", ".", ".."} for part in parts)
        ):
            raise ValueError("profile_drive_path_invalid")
        object.__setattr__(self, "path", normalized)
        object.__setattr__(self, "source_id", source_id)


def parse_profile_drive_file_import_request(value: object) -> ProfileDriveFileImportRequest:
    if isinstance(value, ProfileDriveFileImportRequest):
        return value
    if not isinstance(value, Mapping):
        raise ValueError("profile_drive_path_invalid")
    if any(key not in {"path", "source_id"} for key in value):
        raise ValueError("profile_drive_source_invalid")
    return ProfileDriveFileImportRequest(
        value.get("path"),
        value.get("source_id", "profile"),
    )


class ProfileDriveTransferPort(Protocol):
    async def open_profile_drive_file(self, **kwargs: Any) -> tuple[Any, Any, int, str]: ...

    async def download_profile_drive_file(self, **kwargs: Any) -> tuple[str, str, int]: ...


ProfileDriveResponseFactory = Callable[[AsyncIterable[bytes]], Any]
_transfer_port: ProfileDriveTransferPort | None = None
_response_factory: Callable[..., Any] | None = None


def configure_profile_drive_transfer(port: ProfileDriveTransferPort) -> None:
    global _transfer_port
    _transfer_port = port


def configure_profile_drive_streaming_response(factory: Callable[..., Any]) -> None:
    global _response_factory
    _response_factory = factory


def _configured_transfer() -> ProfileDriveTransferPort:
    if _transfer_port is None:
        raise RuntimeError("profile_drive_transfer_not_configured")
    return _transfer_port


async def open_profile_drive_file(**kwargs: Any) -> tuple[Any, Any, int, str]:
    return await _configured_transfer().open_profile_drive_file(**kwargs)


async def download_profile_drive_file(**kwargs: Any) -> tuple[str, str, int]:
    return await _configured_transfer().download_profile_drive_file(**kwargs)


def profile_drive_streaming_response(
    content: AsyncIterable[bytes],
    *,
    content_length: int,
) -> Any:
    if _response_factory is None:
        raise RuntimeError("profile_drive_streaming_response_not_configured")
    return _response_factory(content, content_length=content_length)
