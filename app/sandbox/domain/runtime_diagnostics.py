import hashlib
import json
import re
from collections.abc import Callable
from typing import Any


SDK_RUNTIME_DIAGNOSTICS_SCHEMA_VERSION = "ai-platform.sdk-runtime-diagnostics.v1"
SDK_RUNTIME_DIAGNOSTICS_MAX_BYTES = 128 * 1024
SDK_RUNTIME_DIAGNOSTIC_TEXT_MAX_BYTES = 8_192
SDK_RUNTIME_DIAGNOSTIC_VALUE_MAX_BYTES = 4_096
SDK_RUNTIME_DIAGNOSTIC_IDENTITY_MAX_BYTES = 128
SDK_RUNTIME_DIAGNOSTIC_LIFECYCLE_LIMIT = 128
SDK_RUNTIME_DIAGNOSTIC_DETAIL_LIMIT = 8
SDK_RUNTIME_DIAGNOSTIC_FAILURE_LIMIT = 8
SDK_RUNTIME_DIAGNOSTIC_LOSS_LIMIT = 32

_STRUCTURED_VALUE_PATTERN = re.compile(r"[a-z][a-z0-9_]{0,63}")
_LOSS_FIELD_PATTERN = re.compile(r"[a-z][a-z0-9_.\[\]]{0,127}")
_LOSS_REASONS = frozenset(
    {
        "invalid_field",
        "invalid_payload",
        "truncated",
        "unknown_fields_dropped",
        "unsupported_schema",
    }
)
_SDK_VALUE_FIELDS = frozenset(
    {
        "errors",
        "result_subtype",
        "stop_reason",
        "terminal_reason",
        "permission_denials",
    }
)


def _valid_unicode_text(value: object) -> str:
    return str(value or "").encode("utf-8", errors="replace").decode("utf-8")


def _json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def runtime_diagnostic_text(
    value: object,
    *,
    max_bytes: int = SDK_RUNTIME_DIAGNOSTIC_TEXT_MAX_BYTES,
) -> str:
    text = _valid_unicode_text(value)
    if len(_json_bytes(text)) <= max_bytes:
        return text
    low, high = 0, len(text)
    while low < high:
        middle = (low + high + 1) // 2
        if len(_json_bytes(text[:middle])) <= max_bytes:
            low = middle
        else:
            high = middle - 1
    return text[:low]


def _bounded_text(
    value: object,
    *,
    max_bytes: int,
    preserve_tail: bool,
) -> tuple[str, tuple[int, int] | None]:
    text = _valid_unicode_text(value)
    original_bytes = len(_json_bytes(text))
    if original_bytes <= max_bytes:
        return text, None
    if not preserve_tail:
        bounded = runtime_diagnostic_text(text, max_bytes=max_bytes)
        return bounded, (original_bytes, len(_json_bytes(bounded)))

    marker = "\n... [truncated] ...\n"
    low, high = 0, len(text)
    bounded = runtime_diagnostic_text(text, max_bytes=max_bytes)
    while low <= high:
        keep = (low + high) // 2
        head, tail = (keep + 1) // 2, keep // 2
        candidate = text[:head] + marker + (text[-tail:] if tail else "")
        if len(_json_bytes(candidate)) <= max_bytes:
            bounded = candidate
            low = keep + 1
        else:
            high = keep - 1
    return bounded, (original_bytes, len(_json_bytes(bounded)))


def runtime_diagnostic_value(value: object) -> object:
    try:
        serialized = json.dumps(
            value,
            ensure_ascii=False,
            default=str,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError):
        return None
    encoded = serialized.encode("utf-8", errors="replace")
    if len(encoded) <= SDK_RUNTIME_DIAGNOSTIC_VALUE_MAX_BYTES:
        return json.loads(encoded.decode("utf-8"))
    return {
        "truncated": True,
        "size_bytes": len(encoded),
        "sha256": hashlib.sha256(encoded).hexdigest(),
    }


def _append_loss(
    losses: list[dict[str, object]],
    *,
    field: str,
    reason: str,
    **measurements: object,
) -> None:
    if not _LOSS_FIELD_PATTERN.fullmatch(field) or reason not in _LOSS_REASONS:
        return
    loss: dict[str, object] = {"field": field, "reason": reason}
    for key in (
        "original_bytes",
        "retained_bytes",
        "original",
        "retained",
        "count",
    ):
        raw = measurements.get(key)
        if type(raw) is int and raw >= 0:
            loss[key] = min(raw, 1_000_000_000)
    if loss not in losses:
        losses.append(loss)
        del losses[:-SDK_RUNTIME_DIAGNOSTIC_LOSS_LIMIT]


def _normalize_losses(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    losses: list[dict[str, object]] = []
    for raw in value[-SDK_RUNTIME_DIAGNOSTIC_LOSS_LIMIT:]:
        if isinstance(raw, dict):
            _append_loss(
                losses,
                field=raw.get("field") if isinstance(raw.get("field"), str) else "",
                reason=raw.get("reason") if isinstance(raw.get("reason"), str) else "",
                original_bytes=raw.get("original_bytes"),
                retained_bytes=raw.get("retained_bytes"),
                original=raw.get("original"),
                retained=raw.get("retained"),
                count=raw.get("count"),
            )
    return losses


def runtime_diagnostics_rejection(
    *,
    reason: str,
    field: str = "runtime_diagnostics",
) -> dict[str, Any]:
    """Return a bounded private record explaining why diagnostics were rejected."""

    losses: list[dict[str, object]] = []
    _append_loss(
        losses,
        field=field,
        reason=reason if reason in _LOSS_REASONS else "invalid_payload",
    )
    observation = {
        "error_code": "runtime_diagnostics_rejected",
        "failure_source": "diagnostic_normalizer",
        "failure_stage": "diagnostic_validation",
    }
    return {
        "schema_version": SDK_RUNTIME_DIAGNOSTICS_SCHEMA_VERSION,
        **observation,
        "sdk": {},
        "failure_observations": [observation],
        "tool_lifecycles": [],
        "tool_calls": [],
        "tool_policy_denials": [],
        "normalization_losses": losses,
    }


def _structured_value(value: object) -> str:
    text = value if isinstance(value, str) else ""
    return text if _STRUCTURED_VALUE_PATTERN.fullmatch(text) else ""


def _text_field(
    value: object,
    *,
    field: str,
    losses: list[dict[str, object]],
    max_bytes: int = SDK_RUNTIME_DIAGNOSTIC_TEXT_MAX_BYTES,
    preserve_tail: bool = False,
) -> str:
    if value in (None, ""):
        return ""
    if not isinstance(value, str):
        _append_loss(losses, field=field, reason="invalid_field")
        return ""
    text, truncation = _bounded_text(
        value,
        max_bytes=max_bytes,
        preserve_tail=preserve_tail,
    )
    if truncation:
        _append_loss(
            losses,
            field=field,
            reason="truncated",
            original_bytes=truncation[0],
            retained_bytes=truncation[1],
        )
    return text


def _value_field(
    value: object,
    *,
    field: str,
    losses: list[dict[str, object]],
) -> object:
    projected = runtime_diagnostic_value(value)
    if projected is None and value is not None:
        _append_loss(losses, field=field, reason="invalid_field")
        return None
    if isinstance(projected, dict) and projected.get("truncated") is True:
        _append_loss(
            losses,
            field=field,
            reason="truncated",
            original_bytes=projected.get("size_bytes"),
        )
    return projected


def _normalize_sdk(
    value: object,
    *,
    losses: list[dict[str, object]],
) -> dict[str, object]:
    if not isinstance(value, dict):
        if value not in (None, {}):
            _append_loss(losses, field="sdk", reason="invalid_field")
        return {}
    normalized = {
        key: _value_field(value[key], field=f"sdk.{key}", losses=losses)
        for key in _SDK_VALUE_FIELDS
        if value.get(key) not in (None, "", [])
    }
    for key, max_bytes, preserve_tail in (
        ("exception_type", SDK_RUNTIME_DIAGNOSTIC_IDENTITY_MAX_BYTES, False),
        ("exception_message", SDK_RUNTIME_DIAGNOSTIC_TEXT_MAX_BYTES, True),
        ("exception_traceback", SDK_RUNTIME_DIAGNOSTIC_TEXT_MAX_BYTES, True),
    ):
        text = _text_field(
            value.get(key),
            field=f"sdk.{key}",
            losses=losses,
            max_bytes=max_bytes,
            preserve_tail=preserve_tail,
        )
        if text:
            normalized[key] = text
    known = _SDK_VALUE_FIELDS | {
        "exception_type",
        "exception_message",
        "exception_traceback",
    }
    if unknown_count := len(set(value) - known):
        _append_loss(
            losses,
            field="sdk",
            reason="unknown_fields_dropped",
            count=unknown_count,
        )
    return normalized


def _tool_identity(
    value: dict[str, object],
    *,
    key: str,
    field: str,
    losses: list[dict[str, object]],
) -> str:
    return _text_field(
        value.get(key),
        field=f"{field}.{key}",
        losses=losses,
        max_bytes=SDK_RUNTIME_DIAGNOSTIC_IDENTITY_MAX_BYTES,
    )


def _normalize_tool_item(
    value: object,
    *,
    kind: str,
    losses: list[dict[str, object]],
    field: str,
) -> dict[str, object] | None:
    if not isinstance(value, dict):
        _append_loss(losses, field=field, reason="invalid_field")
        return None
    tool_name = _tool_identity(value, key="tool_name", field=field, losses=losses)
    invocation_id = _tool_identity(
        value,
        key="invocation_id",
        field=field,
        losses=losses,
    )
    state = _structured_value(value.get("state"))
    if not tool_name or (kind != "denial" and not invocation_id):
        _append_loss(losses, field=field, reason="invalid_field")
        return None
    if kind == "lifecycle" and not state:
        _append_loss(losses, field=field, reason="invalid_field")
        return None

    normalized: dict[str, object] = {"tool_name": tool_name}
    if invocation_id:
        normalized["invocation_id"] = invocation_id
    if kind == "lifecycle":
        normalized["state"] = state
        if capability_kind := _structured_value(value.get("capability_kind")):
            normalized["capability_kind"] = capability_kind
    elif kind == "call":
        for key in ("state", "last_stage"):
            if structured := _structured_value(value.get(key)):
                normalized[key] = structured
        for key in ("tool_input", "failure"):
            if key in value and value[key] is not None:
                normalized[key] = _value_field(
                    value[key],
                    field=f"{field}.{key}",
                    losses=losses,
                )
    else:
        reason = _text_field(
            value.get("reason"),
            field=f"{field}.reason",
            losses=losses,
            max_bytes=1_024,
            preserve_tail=True,
        )
        if reason:
            normalized["reason"] = reason
        if "tool_input" in value and value["tool_input"] is not None:
            normalized["tool_input"] = _value_field(
                value["tool_input"],
                field=f"{field}.tool_input",
                losses=losses,
            )
    return normalized


def _normalize_failure_observation(
    value: object,
    *,
    losses: list[dict[str, object]],
    field: str,
) -> dict[str, object] | None:
    if not isinstance(value, dict):
        _append_loss(losses, field=field, reason="invalid_field")
        return None
    error_code = _structured_value(value.get("error_code"))
    if not error_code:
        _append_loss(losses, field=field, reason="invalid_field")
        return None
    normalized: dict[str, object] = {
        "error_code": error_code,
        "failure_source": _text_field(
            value.get("failure_source"),
            field=f"{field}.failure_source",
            losses=losses,
            max_bytes=SDK_RUNTIME_DIAGNOSTIC_IDENTITY_MAX_BYTES,
        ),
        "failure_stage": _text_field(
            value.get("failure_stage"),
            field=f"{field}.failure_stage",
            losses=losses,
            max_bytes=SDK_RUNTIME_DIAGNOSTIC_IDENTITY_MAX_BYTES,
        ),
    }
    exception = value.get("exception")
    if isinstance(exception, dict):
        sdk_exception = _normalize_sdk(
            {
                "exception_type": exception.get("type"),
                "exception_message": exception.get("message"),
                "exception_traceback": exception.get("traceback"),
            },
            losses=losses,
        )
        projected = {
            target: sdk_exception[source]
            for target, source in (
                ("type", "exception_type"),
                ("message", "exception_message"),
                ("traceback", "exception_traceback"),
            )
            if source in sdk_exception
        }
        if projected:
            normalized["exception"] = projected
    elif exception is not None:
        _append_loss(losses, field=f"{field}.exception", reason="invalid_field")
    return normalized


def _failure_identity(value: object) -> tuple[object, object, object] | None:
    if not isinstance(value, dict):
        return None
    return (
        value.get("error_code"),
        value.get("failure_source"),
        value.get("failure_stage"),
    )


def _fit_runtime_diagnostics(payload: dict[str, Any]) -> dict[str, Any]:
    count_keys = (
        "failure_observations",
        "tool_lifecycles",
        "tool_calls",
        "tool_policy_denials",
    )
    original_counts = {key: len(payload[key]) for key in count_keys}

    def encoded_size() -> int:
        return len(_json_bytes(payload))

    target_bytes = SDK_RUNTIME_DIAGNOSTICS_MAX_BYTES - 2_048
    for key in ("tool_lifecycles", "tool_calls", "tool_policy_denials"):
        while encoded_size() > target_bytes and len(payload[key]) > 1:
            del payload[key][0]
    while encoded_size() > target_bytes and len(payload["failure_observations"]) > 1:
        del payload["failure_observations"][1]

    retained_counts = {key: len(payload[key]) for key in count_keys}
    for key in count_keys:
        if retained_counts[key] == original_counts[key]:
            continue
        _append_loss(
            payload["normalization_losses"],
            field=key,
            reason="truncated",
            original=original_counts[key],
            retained=retained_counts[key],
        )

    if encoded_size() > SDK_RUNTIME_DIAGNOSTICS_MAX_BYTES:
        sdk_bytes = len(_json_bytes(payload.get("sdk") or {}))
        payload["sdk"] = {}
        for key in ("tool_lifecycles", "tool_calls", "tool_policy_denials"):
            original = len(payload[key])
            payload[key] = []
            if original:
                _append_loss(
                    payload["normalization_losses"],
                    field=key,
                    reason="truncated",
                    original=original_counts[key],
                    retained=0,
                )
        if sdk_bytes > 2:
            _append_loss(
                payload["normalization_losses"],
                field="sdk",
                reason="truncated",
                original_bytes=sdk_bytes,
                retained_bytes=2,
            )
    if encoded_size() <= SDK_RUNTIME_DIAGNOSTICS_MAX_BYTES:
        return payload

    minimal = {
        "schema_version": SDK_RUNTIME_DIAGNOSTICS_SCHEMA_VERSION,
        "error_code": payload["error_code"],
        "failure_source": payload.get("failure_source", ""),
        "failure_stage": payload.get("failure_stage", ""),
        "sdk": {},
        "failure_observations": payload.get("failure_observations", [])[:1],
        "tool_lifecycles": [],
        "tool_calls": [],
        "tool_policy_denials": [],
        "normalization_losses": payload.get("normalization_losses", [])[
            -SDK_RUNTIME_DIAGNOSTIC_LOSS_LIMIT:
        ],
    }
    _append_loss(
        minimal["normalization_losses"],
        field="runtime_diagnostics",
        reason="truncated",
    )
    return minimal


def _project_list(
    value: object,
    *,
    key: str,
    limit: int,
    projector: Callable[..., dict[str, object] | None],
    losses: list[dict[str, object]],
    previous_truncated: dict[str, object],
) -> list[dict[str, object]]:
    items = value if isinstance(value, list) else []
    projected = []
    for index, raw in enumerate(items[-limit:]):
        item = projector(raw, losses=losses, field=f"{key}[{index}]")
        if item is not None:
            projected.append(item)
    prior = previous_truncated.get(key)
    previous_original = (
        min(prior.get("original"), 1_000_000)
        if isinstance(prior, dict)
        and type(prior.get("original")) is int
        and prior["original"] >= 0
        else 0
    )
    original = max(len(items), previous_original)
    if original <= len(projected):
        return projected
    _append_loss(
        losses,
        field=key,
        reason="truncated",
        original=original,
        retained=len(projected),
    )
    return projected


def normalize_sdk_runtime_diagnostics(value: object) -> dict[str, Any]:
    """Validate and bound private SDK diagnostics at every sandbox boundary."""

    if value is None:
        return {}
    if not isinstance(value, dict):
        return runtime_diagnostics_rejection(reason="invalid_payload")
    if value.get("schema_version") != SDK_RUNTIME_DIAGNOSTICS_SCHEMA_VERSION:
        return runtime_diagnostics_rejection(
            reason="unsupported_schema",
            field="schema_version",
        )
    error_code = _structured_value(value.get("error_code"))
    if not error_code:
        return runtime_diagnostics_rejection(
            reason="invalid_field",
            field="error_code",
        )

    losses = _normalize_losses(value.get("normalization_losses"))
    failure_source = _text_field(
        value.get("failure_source"),
        field="failure_source",
        losses=losses,
        max_bytes=SDK_RUNTIME_DIAGNOSTIC_IDENTITY_MAX_BYTES,
    )
    failure_stage = _text_field(
        value.get("failure_stage"),
        field="failure_stage",
        losses=losses,
        max_bytes=SDK_RUNTIME_DIAGNOSTIC_IDENTITY_MAX_BYTES,
    )
    sdk = _normalize_sdk(value.get("sdk"), losses=losses)

    legacy_code = _structured_value(value.get("runner_error_code"))
    legacy_source = _text_field(
        value.get("runner_failure_source"),
        field="failure_source",
        losses=losses,
        max_bytes=SDK_RUNTIME_DIAGNOSTIC_IDENTITY_MAX_BYTES,
    )
    current_observation = {
        "error_code": error_code,
        "failure_source": failure_source,
        "failure_stage": failure_stage,
    }
    root_observation = (
        {
            "error_code": legacy_code,
            "failure_source": legacy_source,
            "failure_stage": "",
        }
        if legacy_code
        else current_observation
    )

    observations: list[dict[str, object]] = []
    raw_observations = value.get("failure_observations")
    if isinstance(raw_observations, list):
        selected_observations = raw_observations
        if len(raw_observations) > SDK_RUNTIME_DIAGNOSTIC_FAILURE_LIMIT:
            selected_observations = [
                raw_observations[0],
                *raw_observations[-(SDK_RUNTIME_DIAGNOSTIC_FAILURE_LIMIT - 1) :],
            ]
            _append_loss(
                losses,
                field="failure_observations",
                reason="truncated",
                original=len(raw_observations),
                retained=SDK_RUNTIME_DIAGNOSTIC_FAILURE_LIMIT,
            )
        for index, raw in enumerate(selected_observations):
            item = _normalize_failure_observation(
                raw,
                losses=losses,
                field=f"failure_observations[{index}]",
            )
            if item is not None and item not in observations:
                observations.append(item)
    elif raw_observations is not None:
        _append_loss(losses, field="failure_observations", reason="invalid_field")
    if not observations or _failure_identity(observations[0]) != _failure_identity(
        root_observation
    ):
        observations.insert(0, root_observation)
    if legacy_code and current_observation not in observations:
        observations.append(current_observation)
    if len(observations) > SDK_RUNTIME_DIAGNOSTIC_FAILURE_LIMIT:
        original = len(observations)
        observations = [
            observations[0],
            observations[1],
            *observations[-(SDK_RUNTIME_DIAGNOSTIC_FAILURE_LIMIT - 2) :],
        ]
        _append_loss(
            losses,
            field="failure_observations",
            reason="truncated",
            original=original,
            retained=len(observations),
        )

    normalized: dict[str, Any] = {
        "schema_version": SDK_RUNTIME_DIAGNOSTICS_SCHEMA_VERSION,
        "error_code": legacy_code or error_code,
        "failure_source": legacy_source if legacy_code else failure_source,
        "failure_stage": "" if legacy_code else failure_stage,
        "sdk": sdk,
        "failure_observations": observations,
    }
    previous_truncated = value.get("truncated")
    previous_truncated = (
        previous_truncated if isinstance(previous_truncated, dict) else {}
    )
    for key, limit, kind in (
        ("tool_lifecycles", SDK_RUNTIME_DIAGNOSTIC_LIFECYCLE_LIMIT, "lifecycle"),
        ("tool_calls", SDK_RUNTIME_DIAGNOSTIC_DETAIL_LIMIT, "call"),
        ("tool_policy_denials", SDK_RUNTIME_DIAGNOSTIC_DETAIL_LIMIT, "denial"),
    ):
        normalized[key] = _project_list(
            value.get(key),
            key=key,
            limit=limit,
            projector=lambda raw, *, losses, field, kind=kind: _normalize_tool_item(
                raw,
                kind=kind,
                losses=losses,
                field=field,
            ),
            losses=losses,
            previous_truncated=previous_truncated,
        )

    known_fields = {
        "schema_version",
        "error_code",
        "failure_source",
        "failure_stage",
        "sdk",
        "failure_observations",
        "tool_lifecycles",
        "tool_calls",
        "tool_policy_denials",
        "truncated",
        "normalization_losses",
        "runner_error_code",
        "runner_failure_source",
    }
    if unknown_count := len(set(value) - known_fields):
        _append_loss(
            losses,
            field="runtime_diagnostics",
            reason="unknown_fields_dropped",
            count=unknown_count,
        )
    normalized["normalization_losses"] = losses
    return _fit_runtime_diagnostics(normalized)
