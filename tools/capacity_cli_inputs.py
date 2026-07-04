import json
from pathlib import Path

from app.capacity_baseline import CAPACITY_HOST_SANDBOX_OBSERVATION_SCHEMA


def _host_sandbox_observation_error(error_code: str, path_value: str) -> dict[str, object]:
    return {
        "schema_version": CAPACITY_HOST_SANDBOX_OBSERVATION_SCHEMA,
        "status": "not_accepted",
        "input_errors": [f"{error_code}:{Path(path_value).name}"],
        "review_status": "diagnostic_only_not_reviewed_release_evidence",
        "diagnostic_only": True,
        "does_not_mark_b3_recorded_evidence": True,
        "does_not_close_b3": True,
    }


def read_optional_host_sandbox_observation_json(
    path_value: str | None,
) -> tuple[dict[str, object] | None, dict[str, object] | None]:
    """Read optional host sandbox observation JSON without leaking local paths."""
    if not path_value:
        return None, None
    try:
        raw = Path(path_value).read_text(encoding="utf-8")
    except OSError:
        return (
            None,
            _host_sandbox_observation_error(
                "host_sandbox_observation_json_read_failed",
                path_value,
            ),
        )
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return (
            None,
            _host_sandbox_observation_error(
                "host_sandbox_observation_json_decode_failed",
                path_value,
            ),
        )
    if not isinstance(payload, dict):
        return (
            None,
            _host_sandbox_observation_error(
                "host_sandbox_observation_json_object_required",
                path_value,
            ),
        )
    return payload, None
