"""Lossless private provider-image storage representation, independent of SDK types."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from copy import deepcopy
from dataclasses import dataclass, field
import hashlib
from typing import Any

from app.context.domain.provider_sessions import ProviderSessionContinuityError


PROVIDER_MEDIA_FORMAT = "image-payloads-v1"
MAX_PROVIDER_IMAGE_BYTES = 8 * 1024 * 1024
MAX_PROVIDER_IMAGE_FIELDS = 128

ImageFields = Callable[
    [dict[str, Any]], Iterable[tuple[tuple[str | int, ...], str, str]]
]


@dataclass
class ProviderMediaTransfer:
    """Private in-process ticket; never serialized to the SDK or public API."""

    epoch_id: str
    next_sequence: int
    owner_generation: int
    objects: list[dict[str, Any]]
    projection: ProviderMediaProjection | None = None
    values: dict[str, str] = field(default_factory=dict)
    completed: bool = False


@dataclass(frozen=True)
class ProviderImagePayload:
    sha256: str
    content: bytes
    media_type: str


@dataclass(frozen=True)
class ProviderMediaProjection:
    entries: list[dict[str, Any]]
    references: list[list[dict[str, Any]]]
    images: dict[str, ProviderImagePayload]


def split_provider_images(
    entries: list[dict[str, Any]], *, image_fields: ImageFields,
) -> ProviderMediaProjection:
    projected = deepcopy(entries)
    references: list[list[dict[str, Any]]] = []
    images: dict[str, ProviderImagePayload] = {}
    count = 0
    for entry, projected_entry in zip(entries, projected, strict=True):
        refs = []
        for path, media_type, value in image_fields(entry):
            count += 1
            if count > MAX_PROVIDER_IMAGE_FIELDS:
                raise ProviderSessionContinuityError("provider_session_image_count_exceeded", debug={
                    "component": "provider_session", "operation": "append", "phase": "image_validation",
                    "failure_class": "size_limit", "image_count": count,
                })
            content = value.encode("utf-8")
            if len(content) > MAX_PROVIDER_IMAGE_BYTES:
                raise ProviderSessionContinuityError("provider_session_image_too_large", debug={
                    "component": "provider_session", "operation": "append", "phase": "image_validation",
                    "failure_class": "size_limit", "observed_bytes": len(content), "limit_bytes": MAX_PROVIDER_IMAGE_BYTES,
                    "image_count": count, "largest_encoded_image_bytes": len(content),
                })
            digest = hashlib.sha256(content).hexdigest()
            images.setdefault(digest, ProviderImagePayload(digest, content, media_type))
            _replace(projected_entry, path, expected=value, replacement="")
            refs.append({"path": list(path), "sha256": digest, "size_bytes": len(content)})
        references.append(refs)
    return ProviderMediaProjection(projected, references, images)


def restore_provider_images(
    entry: dict[str, Any], references: object, *, values: Mapping[str, str],
) -> dict[str, Any]:
    if not isinstance(references, list) or len(references) > MAX_PROVIDER_IMAGE_FIELDS:
        raise ProviderSessionContinuityError("provider_session_integrity_mismatch")
    restored = deepcopy(entry)
    for ref in references:
        if not isinstance(ref, dict) or set(ref) != {"path", "sha256", "size_bytes", "image_id"}:
            raise ProviderSessionContinuityError("provider_session_integrity_mismatch")
        if not isinstance(ref["image_id"], str) or not ref["image_id"]:
            raise ProviderSessionContinuityError("provider_session_integrity_mismatch")
        value = values.get(ref["image_id"])
        if not isinstance(value, str):
            raise ProviderSessionContinuityError("provider_session_image_unavailable")
        content = value.encode("utf-8")
        if (
            type(ref["size_bytes"]) is not int
            or not 0 < len(content) <= MAX_PROVIDER_IMAGE_BYTES
            or len(content) != ref["size_bytes"]
            or hashlib.sha256(content).hexdigest() != ref["sha256"]
        ):
            raise ProviderSessionContinuityError("provider_session_integrity_mismatch")
        _replace(restored, ref["path"], expected="", replacement=value)
    return restored


def _replace(entry: dict, path: object, *, expected: str, replacement: str) -> None:
    if not isinstance(path, (list, tuple)) or not path or len(path) > 64:
        raise ProviderSessionContinuityError("provider_session_integrity_mismatch")
    value: Any = entry
    try:
        for part in path[:-1]:
            if not _valid_part(value, part):
                raise KeyError
            value = value[part]
        part = path[-1]
        if not _valid_part(value, part) or value[part] != expected:
            raise KeyError
        value[part] = replacement
    except (KeyError, IndexError, TypeError):
        raise ProviderSessionContinuityError("provider_session_integrity_mismatch") from None


def _valid_part(value: Any, part: Any) -> bool:
    return (
        isinstance(value, dict) and isinstance(part, str) and part in value
        or isinstance(value, list) and type(part) is int and 0 <= part < len(value)
    )
