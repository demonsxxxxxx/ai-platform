"""Versioned, bounded private diagnostics owned by Runs."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import hashlib
import json
import math
import re
from typing import Any


RUN_DIAGNOSTICS_SCHEMA_VERSION = "ai-platform.run-diagnostics.v1"
RUN_DIAGNOSTICS_BUDGET_POLICY_VERSION = "run-diagnostics-budget.v1"
RUN_DIAGNOSTICS_REDACTION_POLICY_VERSION = "run-diagnostics-redaction.v1"
RUN_DIAGNOSTICS_MAX_BYTES = 128 * 1024
RUN_DIAGNOSTICS_OBSERVATION_LIMIT = 64

_IDENTITY_PATTERN = re.compile(r"[A-Za-z0-9_.:-]{1,160}")
_LOSS_FIELD_PATTERN = re.compile(r"[a-z][a-z0-9_.\[\]]{0,127}")
_PRIVATE_KEY_PATTERN = re.compile(
    r"(?i)(?:api[_-]?key|authorization|bearer|cookie|credential|password|secret|token|"
    r"tool[_-]?input|prompt|command|path|raw|request|response|header|environment|env|"
    r"content|body|input|output)"
)
_SECRET_ASSIGNMENT_PATTERN = re.compile(
    r"(?i)\b(api[_-]?key|access[_-]?token|authorization|bearer|credential|password|"
    r"private[_-]?key|refresh[_-]?token|secret|token)\b\s*[:=]\s*[^\s,;]+"
)
_BEARER_PATTERN = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+")
_JWT_PATTERN = re.compile(
    r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"
)
_PROVIDER_TOKEN_PATTERN = re.compile(r"\b(?:sk|ghp|glpat)-?[A-Za-z0-9_-]{16,}\b")
_URL_PATTERN = re.compile(r"(?i)\bhttps?://[^\s'\"<>]+")
_POSIX_PATH_PATTERN = re.compile(r"(?<![A-Za-z0-9])/(?:[^/\s:'\"<>]+/)+([^/\s:'\"<>]+)")
_WINDOWS_PATH_PATTERN = re.compile(
    r"(?i)\b[A-Z]:\\(?:[^\\\s:'\"<>]+\\)+([^\\\s:'\"<>]+)"
)
_EMPTY_COUNTS = {"retained_observations": 0, "omitted_observations": 0}
_EXECUTOR_PROTOCOL_FIELDS = (
    "status",
    "run_id",
    "message",
    "answer_receipt",
    "error_code",
    "error_message",
)
_EXECUTOR_PROTOCOL_LOCATION_FIELDS = frozenset(
    {
        *_EXECUTOR_PROTOCOL_FIELDS,
        "schema_version",
        "message_id",
        "delta_count",
        "text_length",
        "last_delta_event_id",
    }
)
_EXECUTOR_PROTOCOL_VALIDATION_LIMIT = 16
_EXECUTOR_TASK_STATUSES = frozenset(
    {
        "idle",
        "running",
        "completed",
        "succeeded",
        "failed",
        "cancelled",
        "canceled",
        "callback_failed",
    }
)
_EXECUTOR_TERMINAL_STATUSES = frozenset(
    {"completed", "succeeded", "failed", "cancelled", "canceled"}
)
_EXECUTOR_PROTOCOL_VALUE_TYPES = frozenset(
    {
        "missing",
        "null",
        "boolean",
        "integer",
        "number",
        "string",
        "object",
        "array",
        "other",
    }
)
_EXECUTOR_PROTOCOL_VALIDATION_MESSAGES = {
    "literal_error": "Terminal status is not supported",
    "missing": "Required terminal field is missing",
    "model_type": "Terminal result must be an object",
    "run_id_mismatch": "Terminal result Run does not match the claimed Run",
    "status_mismatch": "Executor task status does not match terminal result status",
    "string_too_long": "Terminal text field exceeds its limit",
    "string_type": "Terminal field must be text",
    "validation_error": "Terminal result validation failed",
    "value_error": "Terminal result violates a protocol rule",
}
_EXECUTOR_PROTOCOL_CANONICAL = {
    "status": "failed",
    "error_code": "executor_protocol_invalid",
    "message_non_empty": False,
    "answer_receipt_present": False,
    "structured_error_present": True,
}


class RunDiagnosticsContractError(ValueError):
    """The stored record cannot be safely interpreted by this version."""


def split_runtime_diagnostics(
    result_json: dict[str, Any] | None,
) -> tuple[object | None, dict[str, Any] | None]:
    """Remove the private carrier from the Run result before terminal storage."""

    if not isinstance(result_json, dict):
        return None, result_json
    private_diagnostics = result_json.get("runtime_diagnostics")
    public_result = _without_runtime_diagnostics(result_json)
    return private_diagnostics, public_result or None


def _without_runtime_diagnostics(value: object) -> object:
    if isinstance(value, dict):
        return {
            key: _without_runtime_diagnostics(item)
            for key, item in value.items()
            if str(key) != "runtime_diagnostics"
        }
    if isinstance(value, list):
        return [_without_runtime_diagnostics(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_without_runtime_diagnostics(item) for item in value)
    return value


def build_failure_observation(
    *,
    attempt_id: str | None,
    source: str,
    stage: str,
    error_code: str,
    runtime_diagnostics: dict[str, Any],
    received_at: datetime,
    lease_id: str | None = None,
    request_id: str | None = None,
    callback_id: str | None = None,
) -> dict[str, Any]:
    """Build a stable observation from an already-normalized private carrier."""

    runtime_diagnostics = sanitize_runtime_diagnostics(runtime_diagnostics)
    identity = {
        "attempt_id": _identity(attempt_id),
        "source": _identity(source, fallback="worker"),
        "stage": _identity(stage, fallback="terminalization"),
        "error_code": _identity(error_code, fallback="executor_failure"),
        "lease_id": _identity(lease_id),
        "request_id": _identity(request_id),
        "callback_id": _identity(callback_id),
        "runtime_diagnostics": runtime_diagnostics,
    }
    digest = hashlib.sha256(
        _json_bytes(
            {
                "attempt_id": identity["attempt_id"],
                "source": identity["source"],
                "stage": identity["stage"],
                "error_code": identity["error_code"],
                "lease_id": identity["lease_id"],
                "request_id": identity["request_id"],
                "callback_id": identity["callback_id"],
                "runtime_diagnostics": runtime_diagnostics,
            }
        )
    ).hexdigest()
    return {
        "observation_id": f"obs_{digest[:32]}",
        "attempt_id": identity["attempt_id"] or None,
        "kind": "failure",
        "source": identity["source"],
        "stage": identity["stage"],
        "error_code": identity["error_code"],
        "lease_id": identity["lease_id"] or None,
        "request_id": identity["request_id"] or None,
        "callback_id": identity["callback_id"] or None,
        "received_at": received_at.isoformat(),
        "runtime_diagnostics": deepcopy(runtime_diagnostics),
    }


def sanitize_runtime_diagnostics(value: object) -> dict[str, Any]:
    """Project one normalized SDK payload into the private Runs storage contract."""

    if not isinstance(value, dict):
        return {}
    losses = sanitize_run_diagnostic_losses(value.get("normalization_losses"))
    sdk = value.get("sdk") if isinstance(value.get("sdk"), dict) else {}
    sdk_projection: dict[str, Any] = {}
    for key in ("result_subtype", "stop_reason", "terminal_reason"):
        if projected := _identity(sdk.get(key)):
            sdk_projection[key] = projected
    for key in ("exception_type", "exception_message", "exception_traceback"):
        if isinstance(sdk.get(key), str):
            sdk_projection[key] = _redact_diagnostic_text(sdk[key])
    if "errors" in sdk:
        sdk_projection["errors"] = _sanitize_diagnostic_value(sdk["errors"])

    failure_observations = [
        projected
        for raw in _list_value(value.get("failure_observations"))
        if (projected := _sanitize_failure_observation(raw)) is not None
    ][:8]
    tool_lifecycles = [
        projected
        for raw in _list_value(value.get("tool_lifecycles"))
        if (projected := _sanitize_tool_observation(raw, kind="lifecycle")) is not None
    ][:128]
    tool_calls = []
    for raw in _list_value(value.get("tool_calls")):
        if isinstance(raw, dict) and any(
            key in raw for key in ("tool_input", "failure")
        ):
            _append_redaction_loss(losses, field="tool_calls")
        if projected := _sanitize_tool_observation(raw, kind="call"):
            tool_calls.append(projected)
    tool_policy_denials = []
    for raw in _list_value(value.get("tool_policy_denials")):
        if isinstance(raw, dict) and "tool_input" in raw:
            _append_redaction_loss(losses, field="tool_policy_denials")
        if projected := _sanitize_tool_observation(raw, kind="denial"):
            tool_policy_denials.append(projected)
    executor_protocol = _sanitize_executor_protocol(value.get("executor_protocol"))
    projected = {
        "schema_version": _identity(value.get("schema_version")),
        "error_code": _identity(value.get("error_code")),
        "failure_source": _identity(value.get("failure_source")),
        "failure_stage": _identity(value.get("failure_stage")),
        "sdk": sdk_projection,
        "failure_observations": failure_observations,
        "tool_lifecycles": tool_lifecycles,
        "tool_calls": tool_calls[:8],
        "tool_policy_denials": tool_policy_denials[:8],
        "normalization_losses": losses,
    }
    if isinstance(executor_protocol, dict) and executor_protocol:
        projected["executor_protocol"] = executor_protocol
    return projected


def sanitize_run_diagnostic_losses(value: object) -> list[dict[str, Any]]:
    losses: list[dict[str, Any]] = []
    if not isinstance(value, list):
        return losses
    for raw in value[:64]:
        if not isinstance(raw, dict):
            continue
        field = raw.get("field")
        reason = _identity(raw.get("reason"))
        if (
            not isinstance(field, str)
            or not _LOSS_FIELD_PATTERN.fullmatch(field)
            or not reason
        ):
            continue
        loss: dict[str, Any] = {"field": field, "reason": reason}
        for key in (
            "original_bytes",
            "retained_bytes",
            "original",
            "retained",
            "count",
        ):
            measurement = raw.get(key)
            if type(measurement) is int and 0 <= measurement <= 1_000_000_000:
                loss[key] = measurement
        losses.append(loss)
    return losses


def sanitize_run_diagnostic_text(
    value: object,
    *,
    max_bytes: int = 4_096,
) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    return _bounded_utf8(_redact_diagnostic_text(value), max_bytes=max_bytes)


def build_executor_protocol_diagnostics(
    *,
    runtime_schema_version: str,
    task_status: object,
    terminal_result: object,
    expected_run_id: str,
    validation_errors: object,
) -> dict[str, Any]:
    """Build a value-free structural summary of one invalid terminal result."""

    raw = terminal_result if isinstance(terminal_result, dict) else {}
    reported_run_id = raw.get("run_id")
    issues = _executor_protocol_validation_issues(validation_errors)
    observation = {
        "error_code": "executor_protocol_invalid",
        "failure_source": "executor_probe",
        "failure_stage": "terminal_result_validation",
    }
    return {
        "schema_version": runtime_schema_version,
        **observation,
        "sdk": {},
        "failure_observations": [],
        "tool_lifecycles": [],
        "tool_calls": [],
        "tool_policy_denials": [],
        "normalization_losses": [],
        "executor_protocol": {
            "reported": {
                "task_status": _executor_protocol_status(
                    task_status, allowed=_EXECUTOR_TASK_STATUSES
                ),
                "terminal_status": _executor_protocol_status(
                    raw.get("status"), allowed=_EXECUTOR_TERMINAL_STATUSES
                ),
                "run_id_matches": (
                    reported_run_id == expected_run_id
                    if isinstance(reported_run_id, str)
                    else None
                ),
                "fields": {
                    key: _executor_protocol_field_summary(raw, key)
                    for key in _EXECUTOR_PROTOCOL_FIELDS
                },
                "additional_field_count": min(
                    sum(
                        1
                        for key in raw
                        if not isinstance(key, str)
                        or key not in _EXECUTOR_PROTOCOL_FIELDS
                    ),
                    1_000_000_000,
                ),
            },
            "validation": issues,
            "validation_omitted_count": min(
                max(
                    len(validation_errors) - len(issues)
                    if isinstance(validation_errors, list)
                    else 0,
                    0,
                ),
                1_000_000_000,
            ),
            "canonical": dict(_EXECUTOR_PROTOCOL_CANONICAL),
        },
    }


def _sanitize_executor_protocol(value: object) -> dict[str, Any] | None:
    expected_keys = {"reported", "validation", "validation_omitted_count", "canonical"}
    if not isinstance(value, dict) or set(value) != expected_keys:
        return None
    reported = value.get("reported")
    if not isinstance(reported, dict) or set(reported) != {
        "task_status",
        "terminal_status",
        "run_id_matches",
        "fields",
        "additional_field_count",
    }:
        return None
    raw_task_status = reported.get("task_status")
    raw_terminal_status = reported.get("terminal_status")
    task_status = _executor_protocol_status(
        raw_task_status, allowed=_EXECUTOR_TASK_STATUSES
    )
    terminal_status = _executor_protocol_status(
        raw_terminal_status, allowed=_EXECUTOR_TERMINAL_STATUSES
    )
    if (raw_task_status is not None and task_status != raw_task_status) or (
        raw_terminal_status is not None and terminal_status != raw_terminal_status
    ):
        return None
    run_id_matches = reported.get("run_id_matches")
    if run_id_matches is not None and type(run_id_matches) is not bool:
        return None
    additional_field_count = reported.get("additional_field_count")
    if not _executor_protocol_measurement(additional_field_count):
        return None
    raw_fields = reported.get("fields")
    if not isinstance(raw_fields, dict) or set(raw_fields) != set(
        _EXECUTOR_PROTOCOL_FIELDS
    ):
        return None
    fields: dict[str, dict[str, Any]] = {}
    for key in _EXECUTOR_PROTOCOL_FIELDS:
        field = _sanitize_executor_protocol_field(raw_fields.get(key))
        if field is None:
            return None
        fields[key] = field
    validation = _sanitize_executor_protocol_validation(value.get("validation"))
    omitted = value.get("validation_omitted_count")
    if validation is None or not _executor_protocol_measurement(omitted):
        return None
    canonical = value.get("canonical")
    if not isinstance(canonical, dict) or set(canonical) != set(
        _EXECUTOR_PROTOCOL_CANONICAL
    ):
        return None
    for key, expected in _EXECUTOR_PROTOCOL_CANONICAL.items():
        if type(canonical[key]) is not type(expected) or canonical[key] != expected:
            return None
    return {
        "reported": {
            "task_status": task_status,
            "terminal_status": terminal_status,
            "run_id_matches": run_id_matches,
            "fields": fields,
            "additional_field_count": additional_field_count,
        },
        "validation": validation,
        "validation_omitted_count": omitted,
        "canonical": dict(_EXECUTOR_PROTOCOL_CANONICAL),
    }


def _sanitize_executor_protocol_field(value: object) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    present = value.get("present")
    value_type = value.get("type")
    if (
        type(present) is not bool
        or not isinstance(value_type, str)
        or value_type not in _EXECUTOR_PROTOCOL_VALUE_TYPES
    ):
        return None
    if present == (value_type == "missing"):
        return None
    expected_keys = {"present", "type"}
    projected: dict[str, Any] = {"present": present, "type": value_type}
    if value_type == "string":
        expected_keys.update({"bytes", "non_empty"})
        if not _executor_protocol_measurement(value.get("bytes")):
            return None
        if type(value.get("non_empty")) is not bool:
            return None
        projected.update(bytes=value["bytes"], non_empty=value["non_empty"])
    elif value_type in {"object", "array"}:
        expected_keys.add("items")
        if not _executor_protocol_measurement(value.get("items")):
            return None
        projected["items"] = value["items"]
    return projected if set(value) == expected_keys else None


def _sanitize_executor_protocol_validation(
    value: object,
) -> list[dict[str, str]] | None:
    if not isinstance(value, list) or len(value) > _EXECUTOR_PROTOCOL_VALIDATION_LIMIT:
        return None
    projected: list[dict[str, str]] = []
    for raw in value:
        if not isinstance(raw, dict) or set(raw) != {"location", "type", "message"}:
            return None
        issue_type = raw.get("type")
        message = (
            _EXECUTOR_PROTOCOL_VALIDATION_MESSAGES.get(issue_type)
            if isinstance(issue_type, str)
            else None
        )
        location = _executor_protocol_stored_location(raw.get("location"))
        if message is None or raw.get("message") != message or location is None:
            return None
        projected.append({"location": location, "type": issue_type, "message": message})
    return projected


def _executor_protocol_stored_location(value: object) -> str | None:
    if value == "$":
        return "$"
    if not isinstance(value, str) or not value:
        return None
    for part in value.split("."):
        if part in _EXECUTOR_PROTOCOL_LOCATION_FIELDS or part == "[field]":
            continue
        if (
            part.isascii()
            and part.isdigit()
            and len(part) <= 7
            and int(part) <= 1_000_000
        ):
            continue
        return None
    return value


def _executor_protocol_measurement(value: object) -> bool:
    return type(value) is int and 0 <= value <= 1_000_000_000


def _executor_protocol_field_summary(
    value: dict[object, object],
    key: str,
) -> dict[str, Any]:
    if key not in value:
        return {"present": False, "type": "missing"}
    raw = value[key]
    summary: dict[str, Any] = {
        "present": True,
        "type": _executor_protocol_value_type(raw),
    }
    if isinstance(raw, str):
        summary["bytes"] = min(
            len(raw.encode("utf-8", errors="replace")), 1_000_000_000
        )
        summary["non_empty"] = bool(raw.strip())
    elif isinstance(raw, (dict, list, tuple)):
        summary["items"] = min(len(raw), 1_000_000_000)
    return summary


def _executor_protocol_status(
    value: object,
    *,
    allowed: frozenset[str],
) -> str | None:
    normalized = value.strip().lower() if isinstance(value, str) else ""
    return normalized if normalized in allowed else None


def _executor_protocol_value_type(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, (list, tuple)):
        return "array"
    return "other"


def _executor_protocol_validation_issues(value: object) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    issues: list[dict[str, str]] = []
    for raw in value[:_EXECUTOR_PROTOCOL_VALIDATION_LIMIT]:
        if not isinstance(raw, dict):
            continue
        location = raw.get("loc")
        parts = []
        if isinstance(location, (list, tuple)):
            for part in location:
                if isinstance(part, int) and 0 <= part <= 1_000_000:
                    parts.append(str(part))
                elif isinstance(part, str):
                    parts.append(
                        part
                        if part in _EXECUTOR_PROTOCOL_LOCATION_FIELDS
                        else "[field]"
                    )
        issue_type = _identity(raw.get("type"), fallback="validation_error")
        if issue_type not in _EXECUTOR_PROTOCOL_VALIDATION_MESSAGES:
            issue_type = "validation_error"
        issues.append(
            {
                "location": ".".join(parts) or "$",
                "type": issue_type,
                "message": _EXECUTOR_PROTOCOL_VALIDATION_MESSAGES[issue_type],
            }
        )
    return issues


def _list_value(value: object) -> list[object]:
    return value if isinstance(value, list) else []


def _sanitize_failure_observation(value: object) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    error_code = _identity(value.get("error_code"))
    if not error_code:
        return None
    projected: dict[str, Any] = {
        "error_code": error_code,
        "failure_source": _identity(value.get("failure_source")),
        "failure_stage": _identity(value.get("failure_stage")),
    }
    exception = value.get("exception")
    if isinstance(exception, dict):
        exception_projection = {
            target: _redact_diagnostic_text(raw)
            for target, raw in (
                ("type", exception.get("type")),
                ("message", exception.get("message")),
                ("traceback", exception.get("traceback")),
            )
            if isinstance(raw, str) and raw
        }
        if exception_projection:
            projected["exception"] = exception_projection
    return projected


def _sanitize_tool_observation(
    value: object,
    *,
    kind: str,
) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    tool_name = _identity(value.get("tool_name"))
    invocation_id = _identity(value.get("invocation_id"))
    if not tool_name or (kind != "denial" and not invocation_id):
        return None
    projected: dict[str, Any] = {"tool_name": tool_name}
    if invocation_id:
        projected["invocation_id"] = invocation_id
    for key in ("state", "last_stage", "capability_kind"):
        if identity := _identity(value.get(key)):
            projected[key] = identity
    if kind == "denial" and isinstance(value.get("reason"), str):
        projected["reason"] = _bounded_utf8(
            _redact_diagnostic_text(value["reason"]),
            max_bytes=1_024,
        )
    return projected


def _append_redaction_loss(losses: list[dict[str, Any]], *, field: str) -> None:
    for loss in losses:
        if loss.get("field") == field and loss.get("reason") == "redacted":
            loss["count"] = int(loss.get("count") or 0) + 1
            return
    losses.append({"field": field, "reason": "redacted", "count": 1})


def _sanitize_diagnostic_value(value: object, *, depth: int = 0) -> object:
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else "[invalid-number]"
    if isinstance(value, str):
        return _bounded_utf8(_redact_diagnostic_text(value), max_bytes=4_096)
    if depth >= 4:
        return "[truncated]"
    if isinstance(value, list):
        return [
            _sanitize_diagnostic_value(item, depth=depth + 1) for item in value[:16]
        ]
    if isinstance(value, dict):
        projected: dict[str, Any] = {}
        redacted_fields = 0
        for raw_key, raw_value in list(value.items())[:32]:
            key = _identity(raw_key)
            if not key or _PRIVATE_KEY_PATTERN.search(key):
                redacted_fields += 1
                continue
            projected[key] = _sanitize_diagnostic_value(raw_value, depth=depth + 1)
        if redacted_fields:
            projected["redacted_fields"] = redacted_fields
        return projected
    return _bounded_utf8(_redact_diagnostic_text(str(value)), max_bytes=4_096)


def _redact_diagnostic_text(value: str) -> str:
    text = _SECRET_ASSIGNMENT_PATTERN.sub(r"\1=[redacted-secret]", value)
    text = _BEARER_PATTERN.sub("Bearer [redacted-secret]", text)
    text = _JWT_PATTERN.sub("[redacted-secret]", text)
    text = _PROVIDER_TOKEN_PATTERN.sub("[redacted-secret]", text)
    text = _URL_PATTERN.sub(
        lambda match: match.group(0).split(":", 1)[0] + "://[redacted-host]", text
    )
    text = _WINDOWS_PATH_PATTERN.sub(r"<path>\\\1", text)
    return _POSIX_PATH_PATTERN.sub(r"<path>/\1", text)


def merge_run_diagnostics(
    current: object,
    observation: dict[str, Any],
) -> tuple[dict[str, Any], bool]:
    """Append one immutable observation and keep the whole Run within budget."""

    payload = _validated_payload(current)
    observations = payload["observations"]
    observation_id = observation.get("observation_id")
    if any(item.get("observation_id") == observation_id for item in observations):
        return payload, False
    observations.append(deepcopy(observation))
    omitted = int(payload["counts"].get("omitted_observations") or 0)
    if len(observations) > RUN_DIAGNOSTICS_OBSERVATION_LIMIT:
        del observations[1]
        omitted += 1
        _upsert_budget_loss(
            payload,
            reason="observation_limit_truncated",
            count=omitted,
        )
    payload["counts"] = {
        "retained_observations": len(observations),
        "omitted_observations": omitted,
    }
    if payload["losses"] or _evidence_has_losses(observations):
        payload["coverage"] = "partial"
    _fit_payload(payload)
    return payload, True


def empty_run_diagnostics_payload() -> dict[str, Any]:
    return {
        "schema_version": RUN_DIAGNOSTICS_SCHEMA_VERSION,
        "coverage": "full",
        "redaction_policy_version": RUN_DIAGNOSTICS_REDACTION_POLICY_VERSION,
        "budget_policy_version": RUN_DIAGNOSTICS_BUDGET_POLICY_VERSION,
        "observations": [],
        "losses": [],
        "counts": dict(_EMPTY_COUNTS),
    }


def serialize_run_diagnostics_payload(value: object) -> str:
    """Encode one stored payload with the Runs-owned strict size contract."""

    encoded = _json_bytes(value)
    if len(encoded) > RUN_DIAGNOSTICS_MAX_BYTES:
        raise RunDiagnosticsContractError("run_diagnostics_too_large")
    return encoded.decode("utf-8")


def _validated_payload(value: object) -> dict[str, Any]:
    if value in (None, {}):
        return empty_run_diagnostics_payload()
    if not isinstance(value, dict):
        raise RunDiagnosticsContractError("run_diagnostics_payload_invalid")
    if value.get("schema_version") != RUN_DIAGNOSTICS_SCHEMA_VERSION:
        raise RunDiagnosticsContractError("run_diagnostics_schema_unsupported")
    observations = value.get("observations")
    losses = value.get("losses")
    counts = value.get("counts")
    if not isinstance(observations, list) or not isinstance(losses, list):
        raise RunDiagnosticsContractError("run_diagnostics_payload_invalid")
    if not isinstance(counts, dict):
        raise RunDiagnosticsContractError("run_diagnostics_payload_invalid")
    return deepcopy(value)


def _fit_payload(payload: dict[str, Any]) -> None:
    if len(_json_bytes(payload)) <= RUN_DIAGNOSTICS_MAX_BYTES:
        return
    payload["coverage"] = "partial"
    _upsert_budget_loss(
        payload,
        reason="run_budget_truncated",
        count=max(int(payload["counts"].get("omitted_observations") or 0), 1),
    )
    observations = payload["observations"]
    for item in observations[1:-1]:
        _minimize_observation(item)
        if len(_json_bytes(payload)) <= RUN_DIAGNOSTICS_MAX_BYTES:
            break
    if len(_json_bytes(payload)) > RUN_DIAGNOSTICS_MAX_BYTES:
        for item in observations:
            _minimize_observation(item)
    while (
        len(_json_bytes(payload)) > RUN_DIAGNOSTICS_MAX_BYTES and len(observations) > 2
    ):
        del observations[1]
        payload["counts"]["omitted_observations"] += 1
        _upsert_budget_loss(
            payload,
            reason="run_budget_truncated",
            count=payload["counts"]["omitted_observations"],
        )
    if len(_json_bytes(payload)) > RUN_DIAGNOSTICS_MAX_BYTES:
        for item in observations:
            _minimize_observation(item, severe=True)
    payload["counts"]["retained_observations"] = len(observations)
    _upsert_budget_loss(
        payload,
        reason="run_budget_truncated",
        count=max(int(payload["counts"].get("omitted_observations") or 0), 1),
    )
    if len(_json_bytes(payload)) > RUN_DIAGNOSTICS_MAX_BYTES:
        raise RunDiagnosticsContractError("run_diagnostics_minimum_too_large")


def _upsert_budget_loss(
    payload: dict[str, Any],
    *,
    reason: str,
    count: int,
) -> None:
    for loss in payload["losses"]:
        if loss.get("field") == "observations" and loss.get("reason") == reason:
            loss["count"] = max(int(loss.get("count") or 0), count)
            return
    payload["losses"].append(
        {"field": "observations", "reason": reason, "count": count}
    )


def _minimize_observation(
    observation: dict[str, Any],
    *,
    severe: bool = False,
) -> None:
    evidence = observation.get("runtime_diagnostics")
    if not isinstance(evidence, dict):
        return
    sdk = evidence.get("sdk") if isinstance(evidence.get("sdk"), dict) else {}
    failures = evidence.get("failure_observations")
    sdk_projection = {
        key: sdk[key]
        for key in (
            "exception_type",
            "exception_message",
            "exception_traceback",
            "errors",
        )
        if key in sdk
    }
    retained_failures = failures[:1] if isinstance(failures, list) else []
    if severe:
        sdk_projection.pop("errors", None)
        for key, max_bytes in (
            ("exception_type", 512),
            ("exception_message", 2_048),
            ("exception_traceback", 8_192),
        ):
            if isinstance(sdk_projection.get(key), str):
                sdk_projection[key] = _bounded_utf8(
                    sdk_projection[key], max_bytes=max_bytes
                )
        retained_failures = []
    minimized = {
        "schema_version": evidence.get("schema_version"),
        "error_code": evidence.get("error_code"),
        "failure_source": evidence.get("failure_source"),
        "failure_stage": evidence.get("failure_stage"),
        "sdk": sdk_projection,
        "failure_observations": retained_failures,
        "tool_lifecycles": [],
        "tool_calls": [],
        "tool_policy_denials": [],
        "normalization_losses": [
            {"field": "runtime_diagnostics", "reason": "truncated"}
        ],
    }
    if not severe and isinstance(evidence.get("executor_protocol"), dict):
        minimized["executor_protocol"] = evidence["executor_protocol"]
    observation["runtime_diagnostics"] = minimized


def _bounded_utf8(value: str, *, max_bytes: int) -> str:
    encoded = value.encode("utf-8", errors="replace")
    if len(encoded) <= max_bytes:
        return value
    return encoded[:max_bytes].decode("utf-8", errors="ignore")


def _evidence_has_losses(observations: list[dict[str, Any]]) -> bool:
    return any(
        isinstance(item.get("runtime_diagnostics"), dict)
        and bool(item["runtime_diagnostics"].get("normalization_losses"))
        for item in observations
    )


def _identity(value: object, *, fallback: str = "") -> str:
    text = value if isinstance(value, str) else ""
    return text if _IDENTITY_PATTERN.fullmatch(text) else fallback


def _json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    ).encode("utf-8")
