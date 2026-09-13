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
    return {
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
    observation["runtime_diagnostics"] = {
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
