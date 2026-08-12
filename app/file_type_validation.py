import re
from collections.abc import Iterable, Mapping
from typing import Any


MAX_PROFILE_FILE_TYPE_CHARS = 127
_TOKEN = r"[a-z0-9!#$%&'+.^_`|~-]+"
_MEDIA_TYPE_PATTERN = re.compile(rf"^(?P<type>{_TOKEN})/(?P<subtype>{_TOKEN}|\*)$")
_EXTENSION_PATTERN = re.compile(r"^\.[a-z0-9][a-z0-9.+-]{0,31}$")


def normalize_profile_file_type(value: str) -> str | None:
    """Normalize one configured extension or bounded media type, fail closed."""

    candidate = value.strip().casefold()
    if not candidate or len(candidate) > MAX_PROFILE_FILE_TYPE_CHARS:
        return None
    if _EXTENSION_PATTERN.fullmatch(candidate):
        return candidate
    if ";" in candidate or any(char.isspace() for char in candidate):
        return None
    match = _MEDIA_TYPE_PATTERN.fullmatch(candidate)
    if match is None:
        return None
    if match.group("type") == "*" or match.group("subtype") == "*" and candidate == "*/*":
        return None
    return candidate


def canonical_observed_media_type(value: object) -> str | None:
    """Canonicalize server-owned Content-Type metadata without trusting parameters."""

    candidate = str(value or "").split(";", 1)[0].strip().casefold()
    if not candidate or len(candidate) > MAX_PROFILE_FILE_TYPE_CHARS:
        return None
    if any(char.isspace() for char in candidate):
        return None
    match = _MEDIA_TYPE_PATTERN.fullmatch(candidate)
    if match is None or match.group("subtype") == "*":
        return None
    return candidate


def profile_file_type_allowed(
    row: Mapping[str, Any],
    *,
    allowed_file_types: Iterable[str],
) -> bool:
    """Match configured profile policy against server-owned file metadata."""

    original_name = str(row.get("original_name") or "").strip().casefold()
    content_type = canonical_observed_media_type(row.get("content_type"))
    for entry in allowed_file_types:
        candidate = normalize_profile_file_type(str(entry))
        if candidate is None:
            continue
        if candidate.startswith("."):
            if original_name.endswith(candidate):
                return True
            continue
        if content_type is None:
            continue
        if candidate.endswith("/*"):
            if content_type.startswith(f"{candidate[:-2]}/"):
                return True
            continue
        if content_type == candidate:
            return True
    return False
