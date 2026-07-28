"""OpenSandbox-specific metadata normalization and exact comparison."""

import base64
import hashlib
import re
from collections.abc import Mapping


OPENSANDBOX_METADATA_MAX_LENGTH = 63
_LABEL = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,61}[A-Za-z0-9])?\Z")
_DIGEST_PREFIX = "osb1-"
_DOMAIN = b"ai-platform.opensandbox.metadata.v1\0"


class OpenSandboxMetadataError(ValueError):
    """Raise a safe error when provider metadata cannot be represented exactly."""


def _digest_value(key: str, value: str) -> str:
    digest = hashlib.sha256(_DOMAIN + key.encode("utf-8") + b"\0" + value.encode("utf-8")).digest()
    encoded = base64.b32encode(digest).decode("ascii").lower().rstrip("=")
    return f"{_DIGEST_PREFIX}{encoded}"


def _valid_label(value: str) -> bool:
    return len(value) <= OPENSANDBOX_METADATA_MAX_LENGTH and _LABEL.fullmatch(value) is not None


def normalize_opensandbox_metadata(metadata: Mapping[str, str]) -> dict[str, str]:
    """Map server-owned metadata into the OpenSandbox provider label contract.

    OpenSandbox applies Kubernetes-style 63-character label limits. Values that
    cannot be represented directly become a domain-separated, full SHA-256
    digest token; keys never change, so equality remains exact and unambiguous.
    """

    if not isinstance(metadata, Mapping):
        raise OpenSandboxMetadataError("OpenSandbox metadata is invalid")
    normalized: dict[str, str] = {}
    normalized_entries: dict[tuple[str, str], str] = {}
    for raw_key, raw_value in metadata.items():
        if not isinstance(raw_key, str) or not isinstance(raw_value, str):
            raise OpenSandboxMetadataError("OpenSandbox metadata is invalid")
        if raw_key.startswith("opensandbox.io/") or not _valid_label(raw_key):
            raise OpenSandboxMetadataError("OpenSandbox metadata is invalid")
        if any(ord(character) < 32 or ord(character) == 127 for character in raw_value):
            raise OpenSandboxMetadataError("OpenSandbox metadata is invalid")
        value = raw_value if not raw_value or _valid_label(raw_value) else _digest_value(raw_key, raw_value)
        existing = normalized.get(raw_key)
        if existing is not None and existing != value:
            raise OpenSandboxMetadataError("OpenSandbox metadata is ambiguous")
        identity = (raw_key, value)
        previous_raw = normalized_entries.get(identity)
        if previous_raw is not None and previous_raw != raw_value:
            raise OpenSandboxMetadataError("OpenSandbox metadata is ambiguous")
        normalized[raw_key] = value
        normalized_entries[identity] = raw_value
    return normalized


def opensandbox_metadata_matches(
    observed: Mapping[object, object],
    expected: Mapping[str, str],
    *,
    ignored_keys: frozenset[str] = frozenset(),
) -> bool:
    """Compare remote labels to a canonical server-owned metadata expectation."""

    try:
        normalized = normalize_opensandbox_metadata(expected)
    except OpenSandboxMetadataError:
        return False
    for key, value in normalized.items():
        if key not in ignored_keys and str(observed.get(key) or "") != value:
            return False
    return True


def opensandbox_status_matches_lease(
    labels: object,
    expected: Mapping[str, str],
    *,
    ignored_keys: frozenset[str] = frozenset({"ai-platform.governed_egress.proof"}),
) -> bool:
    """Compare provider status labels to canonical server-owned lease labels."""

    return isinstance(labels, Mapping) and opensandbox_metadata_matches(
        labels,
        expected,
        ignored_keys=ignored_keys,
    )


def opensandbox_metadata_matches_filters(metadata: Mapping[object, object], filters: Mapping[str, str]) -> bool:
    """Compare provider metadata to raw server-owned inventory filters."""

    expected = {
        f"ai-platform.{key}": value
        for key, value in filters.items()
        if key in {"tenant_id", "workspace_id", "user_id", "session_id", "run_id", "attempt_id", "sandbox_mode"}
    }
    expected["ai-platform.owner"] = "sandbox-runtime"
    return opensandbox_metadata_matches(metadata, expected)
