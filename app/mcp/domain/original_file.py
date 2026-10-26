"""Opaque ProfileDrive original-file result contracts for the MCP adapter."""

from __future__ import annotations

import base64
import binascii
import json
from dataclasses import dataclass
from typing import Any


class _DuplicateKeyError(ValueError):
    pass


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise _DuplicateKeyError(key)
        value[key] = item
    return value

ORIGINAL_FILE_RESULT_FORMAT = "profile-drive.original-file.v1"
ORIGINAL_FILE_RESULT_FORMAT_MARKER = "x-ai-platform-result-format"
ORIGINAL_FILE_RESOURCE_URI = "profile-drive://original-file"
MAX_ORIGINAL_FILE_BYTES = 8 * 1024 * 1024
# Raw bytes are Base64 encoded in the native MCP resource plus JSON framing.
MAX_ORIGINAL_FILE_RESPONSE_BYTES = 16 * 1024 * 1024


class OriginalFileResultError(ValueError):
    """Raised when a selected original-file tool returns an invalid result."""


@dataclass(frozen=True, slots=True)
class OriginalFileMetadata:
    format: str
    source: str
    path: str
    name: str
    size_bytes: int
    content_type: str


def _metadata_from_text(content: object) -> OriginalFileMetadata:
    if not isinstance(content, str):
        raise OriginalFileResultError("original_file_metadata_invalid")
    try:
        value = json.loads(content, object_pairs_hook=_reject_duplicate_keys)
    except (TypeError, ValueError) as exc:
        raise OriginalFileResultError("original_file_metadata_invalid") from exc
    if not isinstance(value, dict) or set(value) != {
        "format", "source", "path", "name", "sizeBytes", "contentType",
    }:
        raise OriginalFileResultError("original_file_metadata_invalid")
    format_name = value.get("format")
    source = value.get("source")
    path = value.get("path")
    name = value.get("name")
    size_bytes = value.get("sizeBytes")
    content_type = value.get("contentType")
    if (
        format_name != ORIGINAL_FILE_RESULT_FORMAT
        or not isinstance(source, str)
        or source not in {"profile", "public"}
        or not isinstance(path, str)
        or not isinstance(name, str)
        or not isinstance(content_type, str)
        or type(size_bytes) is not int
        or not 0 <= size_bytes <= MAX_ORIGINAL_FILE_BYTES
        or not path
        or not name
        or not content_type
        or "\x00" in path
        or "\x00" in name
        or "\\" in path
        or "\\" in name
        or ":" in path
        or ":" in name
        or path.startswith("/")
        or any(part in {"", ".", ".."} for part in path.split("/"))
        or "/" in name
        or len(path) > 1024
        or len(name) > 255
        or len(content_type) > 255
        or not all(character.isprintable() for character in (path + name + content_type))
    ):
        raise OriginalFileResultError("original_file_metadata_invalid")
    return OriginalFileMetadata(
        format=format_name,
        source=source,
        path=path,
        name=name,
        size_bytes=size_bytes,
        content_type=content_type,
    )


def original_file_result_metadata(tool_input_schema: object) -> bool:
    """Return whether the exact selected tool declares opaque original delivery."""

    return isinstance(tool_input_schema, dict) and tool_input_schema.get(
        ORIGINAL_FILE_RESULT_FORMAT_MARKER
    ) == ORIGINAL_FILE_RESULT_FORMAT


def extract_original_file_result(
    result: object,
    *,
    max_bytes: int | None = None,
) -> tuple[dict[str, Any], bytes]:
    """Validate native MCP content and decode the opaque resource without parsing it."""

    if result is None or getattr(result, "is_error", None) is not False:
        raise OriginalFileResultError("original_file_result_not_success")
    content = getattr(result, "content", None)
    if not isinstance(content, list) or len(content) != 2:
        raise OriginalFileResultError("original_file_result_invalid")
    metadata_content, resource_content = content
    if getattr(metadata_content, "type", None) != "text":
        raise OriginalFileResultError("original_file_metadata_invalid")
    metadata = _metadata_from_text(getattr(metadata_content, "text", None))
    if getattr(resource_content, "type", None) != "resource":
        raise OriginalFileResultError("original_file_resource_invalid")
    resource = getattr(resource_content, "resource", None)
    if (
        resource is None
        or getattr(resource, "uri", None) != ORIGINAL_FILE_RESOURCE_URI
        or getattr(resource, "mime_type", None) != metadata.content_type
        or not isinstance(getattr(resource, "blob", None), str)
    ):
        raise OriginalFileResultError("original_file_resource_invalid")
    blob = resource.blob
    if len(blob) > ((MAX_ORIGINAL_FILE_BYTES + 2) // 3) * 4:
        raise OriginalFileResultError("original_file_blob_invalid")
    try:
        raw = base64.b64decode(blob, validate=True)
    except (binascii.Error, ValueError, TypeError) as exc:
        raise OriginalFileResultError("original_file_blob_invalid") from exc
    if base64.b64encode(raw).decode("ascii") != blob:
        raise OriginalFileResultError("original_file_blob_invalid")
    if max_bytes is not None and (
        type(max_bytes) is not int or not 0 < max_bytes <= MAX_ORIGINAL_FILE_BYTES
    ):
        raise OriginalFileResultError("original_file_max_bytes_invalid")
    if max_bytes is not None and metadata.size_bytes > max_bytes:
        raise OriginalFileResultError("original_file_max_bytes_exceeded")
    if len(raw) != metadata.size_bytes:
        raise OriginalFileResultError("original_file_size_mismatch")
    return (
        {
            "format": metadata.format,
            "source": metadata.source,
            "path": metadata.path,
            "name": metadata.name,
            "size_bytes": metadata.size_bytes,
            "content_type": metadata.content_type,
        },
        raw,
    )
