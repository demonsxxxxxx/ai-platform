"""Strict cleanup-only compatibility for pre-governance OpenSandbox leases.

Nothing in this module can create, reuse, persist, or dispatch a sandbox.  It
exists only to identify and terminalize exact historical ``trusted_internal``
leases after that execution profile was retired.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.runtime.sandbox.opensandbox_policy import (
    SANDBOX_SECURITY_PROFILE_LABEL,
    OpenSandboxProfileConfigurationError,
    validate_opensandbox_image_reference,
)
from app.runtime.sandbox.providers.opensandbox.metadata import (
    OpenSandboxMetadataError,
    normalize_opensandbox_metadata,
    opensandbox_metadata_matches,
)

SANDBOX_SECURITY_PROFILE_TRUSTED_INTERNAL = "trusted_internal"

_GOVERNED_EGRESS_PROOF_LABEL = "ai-platform.governed_egress.proof"
_LEGACY_ORPHAN_CLEANUP_FILTER_LABELS = {
    "tenant_id": "ai-platform.tenant_id",
    "workspace_id": "ai-platform.workspace_id",
    "user_id": "ai-platform.user_id",
    "session_id": "ai-platform.session_id",
    "run_id": "ai-platform.run_id",
    "attempt_id": "ai-platform.attempt_id",
    "sandbox_mode": "ai-platform.sandbox_mode",
    "security_profile": SANDBOX_SECURITY_PROFILE_LABEL,
}


def _has_governed_projection(
    labels: Mapping[object, object],
    *,
    allow_passive_image_runtime_subject: bool = False,
) -> bool:
    return _GOVERNED_EGRESS_PROOF_LABEL in labels or any(
        str(key).startswith(("ai-platform.external_egress.", "ai-platform.governed_egress."))
        or (str(key) == "ai-platform.runtime_subject" and not allow_passive_image_runtime_subject)
        for key in labels
    )


def trusted_internal_orphan_cleanup_metadata_filter(
    filters: Mapping[str, object],
) -> dict[str, str] | None:
    """Return an exact remote filter only for a complete legacy lease scope."""

    if set(filters) != set(_LEGACY_ORPHAN_CLEANUP_FILTER_LABELS):
        return None
    values: dict[str, str] = {}
    for field, label in _LEGACY_ORPHAN_CLEANUP_FILTER_LABELS.items():
        value = filters.get(field)
        if (
            not isinstance(value, str)
            or not value
            or value != value.strip()
            or len(value) > 512
            or any(ord(character) < 32 or ord(character) == 127 for character in value)
        ):
            return None
        values[label] = value
    if (
        values["ai-platform.sandbox_mode"] not in {"ephemeral", "persistent"}
        or values[SANDBOX_SECURITY_PROFILE_LABEL] != SANDBOX_SECURITY_PROFILE_TRUSTED_INTERNAL
    ):
        return None
    raw_metadata = {
        "ai-platform.owner": "sandbox-runtime",
        "ai-platform.provider_backend": "opensandbox",
        **values,
    }
    try:
        return normalize_opensandbox_metadata(raw_metadata)
    except OpenSandboxMetadataError:
        return None


def trusted_internal_orphan_cleanup_identity_is_authorized(
    status_labels: object,
    filters: Mapping[str, object],
) -> bool:
    """Match readback to one complete historical cleanup scope."""

    expected = trusted_internal_orphan_cleanup_metadata_filter(filters)
    return (
        expected is not None
        and isinstance(status_labels, dict)
        and not _has_governed_projection(status_labels)
        and all(status_labels.get(key) == value for key, value in expected.items())
    )


def trusted_internal_cleanup_identity_is_authorized(
    status_labels: object,
    lease_labels: Mapping[str, str],
) -> bool:
    """Verify exact historical identity before destructive cleanup."""

    if not isinstance(status_labels, dict):
        return False
    image = str(lease_labels.get("ai-platform.executor.requested_image") or "")
    digest = str(lease_labels.get("ai-platform.executor.requested_image_digest") or "")
    if bool(image) != bool(digest):
        return False
    try:
        image_valid = bool(image) and validate_opensandbox_image_reference(
            image,
            digest,
            allow_local_image_id=True,
        ) == (image, digest)
    except OpenSandboxProfileConfigurationError:
        image_valid = False
    persisted_scope = {
        "ai-platform.owner",
        "ai-platform.tenant_id",
        "ai-platform.workspace_id",
        "ai-platform.user_id",
        "ai-platform.session_id",
        "ai-platform.run_id",
        "ai-platform.attempt_id",
        "ai-platform.sandbox_mode",
        "ai-platform.browser_enabled",
        "ai-platform.provider_backend",
        SANDBOX_SECURITY_PROFILE_LABEL,
    }
    scope_valid = (
        set(lease_labels) == persisted_scope
        and all(isinstance(value, str) and value for value in lease_labels.values())
        and lease_labels.get("ai-platform.owner") == "sandbox-runtime"
        and lease_labels.get("ai-platform.provider_backend") == "opensandbox"
        and lease_labels.get(SANDBOX_SECURITY_PROFILE_LABEL) == SANDBOX_SECURITY_PROFILE_TRUSTED_INTERNAL
        and lease_labels.get("ai-platform.sandbox_mode") in {"ephemeral", "persistent"}
        and lease_labels.get("ai-platform.browser_enabled") in {"true", "false"}
    )
    return (
        (image_valid or scope_valid)
        and status_labels.get(SANDBOX_SECURITY_PROFILE_LABEL) == SANDBOX_SECURITY_PROFILE_TRUSTED_INTERNAL
        and status_labels.get("ai-platform.provider_backend") == "opensandbox"
        and opensandbox_metadata_matches(status_labels, lease_labels)
        and not _has_governed_projection(lease_labels)
        and not _has_governed_projection(status_labels, allow_passive_image_runtime_subject=True)
    )


def trusted_internal_runtime_lease_payload_matches_row(
    row: Mapping[str, Any],
    payload: Mapping[str, Any],
) -> bool:
    """Verify exact stored row, payload, scope, attempt and runtime bindings."""

    labels = payload.get("labels")
    if not isinstance(labels, dict):
        return False
    required_row_strings = {
        "tenant_id": row.get("tenant_id"),
        "workspace_id": row.get("workspace_id"),
        "user_id": row.get("user_id"),
        "session_id": row.get("session_id"),
        "run_id": row.get("run_id"),
        "sandbox_mode": row.get("sandbox_mode"),
        "runtime_container_id": row.get("runtime_container_id"),
        "runtime_container_name": row.get("runtime_container_name"),
        "runtime_executor_url": row.get("runtime_executor_url"),
        "runtime_workspace_container_path": row.get("runtime_workspace_container_path"),
    }
    if any(not isinstance(value, str) or not value for value in required_row_strings.values()):
        return False
    if required_row_strings["sandbox_mode"] not in {"ephemeral", "persistent"}:
        return False
    browser_enabled = row.get("browser_enabled")
    attempt_id = payload.get("attempt_id")
    if not isinstance(browser_enabled, bool) or not isinstance(attempt_id, str) or not attempt_id:
        return False
    if any(
        payload.get(payload_key) != required_row_strings[row_key]
        for payload_key, row_key in (
            ("container_id", "runtime_container_id"),
            ("container_name", "runtime_container_name"),
            ("executor_url", "runtime_executor_url"),
            ("workspace_container_path", "runtime_workspace_container_path"),
        )
    ):
        return False
    expected_labels = {
        "ai-platform.owner": "sandbox-runtime",
        "ai-platform.tenant_id": required_row_strings["tenant_id"],
        "ai-platform.workspace_id": required_row_strings["workspace_id"],
        "ai-platform.user_id": required_row_strings["user_id"],
        "ai-platform.session_id": required_row_strings["session_id"],
        "ai-platform.run_id": required_row_strings["run_id"],
        "ai-platform.attempt_id": attempt_id,
        "ai-platform.sandbox_mode": required_row_strings["sandbox_mode"],
        "ai-platform.browser_enabled": "true" if browser_enabled else "false",
        "ai-platform.provider_backend": "opensandbox",
        SANDBOX_SECURITY_PROFILE_LABEL: SANDBOX_SECURITY_PROFILE_TRUSTED_INTERNAL,
    }
    if set(labels) != set(expected_labels) or any(
        str(labels.get(key) or "") != expected for key, expected in expected_labels.items()
    ):
        return False
    if _has_governed_projection(labels):
        return False
    return not any(
        key == "governed_egress_proof" or str(key).startswith("governed_egress_")
        for key in payload
    )


def trusted_internal_cleanup_labels_from_persisted_row(
    row: Mapping[str, Any],
    payload: Mapping[str, Any],
) -> dict[str, str] | None:
    """Rebuild only a complete persisted historical cleanup scope."""

    if (
        payload.get("security_profile") != SANDBOX_SECURITY_PROFILE_TRUSTED_INTERNAL
        or not trusted_internal_runtime_lease_payload_matches_row(row, payload)
    ):
        return None
    labels = payload.get("labels")
    return {str(key): str(value) for key, value in labels.items()} if isinstance(labels, dict) else None
