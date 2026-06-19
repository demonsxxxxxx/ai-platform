from __future__ import annotations

from typing import Any


BOUNDED_ERROR_PROJECTION_ALLOWED_KEYS = {
    "source",
    "run_id",
    "status",
    "error_code",
    "host_paths_redacted",
    "raw_docker_payload_absent",
    "callback_token_absent",
}
BOUNDED_ERROR_PROJECTION_ALLOWED_SOURCES = {"admin_runtime_projection"}
BOUNDED_ERROR_PROJECTION_ALLOWED_STATUSES = {"failed"}
BOUNDED_ERROR_PROJECTION_ALLOWED_ERROR_CODES = {
    "container_start_failed",
    "executor_health_timeout",
    "sandbox_runtime_cleanup_failed",
}
BOUNDED_ERROR_PROJECTION_REDACTION_FLAGS = (
    "host_paths_redacted",
    "raw_docker_payload_absent",
    "callback_token_absent",
)


def bounded_error_projection_error(projection: object, *, run_id: str) -> str | None:
    """Return the first field-level rejection reason for bounded error projection evidence."""
    if not isinstance(projection, dict):
        return "resource_limits.bounded_error_projection"
    unknown_keys = sorted(str(key) for key in projection if key not in BOUNDED_ERROR_PROJECTION_ALLOWED_KEYS)
    if unknown_keys:
        return "resource_limits.bounded_error_projection.unknown_fields"
    if projection.get("source") not in BOUNDED_ERROR_PROJECTION_ALLOWED_SOURCES:
        return "resource_limits.bounded_error_projection.source"
    if not run_id or projection.get("run_id") != run_id:
        return "resource_limits.bounded_error_projection.run_id"
    if projection.get("status") not in BOUNDED_ERROR_PROJECTION_ALLOWED_STATUSES:
        return "resource_limits.bounded_error_projection.status"
    if projection.get("error_code") not in BOUNDED_ERROR_PROJECTION_ALLOWED_ERROR_CODES:
        return "resource_limits.bounded_error_projection.error_code"
    for field in BOUNDED_ERROR_PROJECTION_REDACTION_FLAGS:
        if projection.get(field) is not True:
            return f"resource_limits.bounded_error_projection.{field}"
    return None


def bounded_error_projection_is_safe(projection: object, *, run_id: str) -> bool:
    """Check whether a bounded error projection is safe to treat as runtime evidence."""
    return bounded_error_projection_error(projection, run_id=run_id) is None


def safe_bounded_error_projection(projection: object, *, run_id: str) -> dict[str, Any] | None:
    """Return a normalized projection object only when it satisfies the safe evidence contract."""
    if not bounded_error_projection_is_safe(projection, run_id=run_id):
        return None
    assert isinstance(projection, dict)
    return {
        "source": projection["source"],
        "run_id": projection["run_id"],
        "status": projection["status"],
        "error_code": projection["error_code"],
        "host_paths_redacted": projection["host_paths_redacted"],
        "raw_docker_payload_absent": projection["raw_docker_payload_absent"],
        "callback_token_absent": projection["callback_token_absent"],
    }
