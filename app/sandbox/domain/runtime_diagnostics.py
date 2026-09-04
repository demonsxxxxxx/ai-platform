import hashlib
import json
import re
from typing import Any


SDK_RUNTIME_DIAGNOSTICS_SCHEMA_VERSION = "ai-platform.sdk-runtime-diagnostics.v1"
SDK_RUNTIME_DIAGNOSTICS_MAX_BYTES = 128 * 1024
SDK_RUNTIME_DIAGNOSTIC_TEXT_MAX_BYTES = 8_192
SDK_RUNTIME_DIAGNOSTIC_VALUE_MAX_BYTES = 4_096
SDK_RUNTIME_DIAGNOSTIC_IDENTITY_MAX_BYTES = 128
SDK_RUNTIME_DIAGNOSTIC_LIFECYCLE_LIMIT = 128
SDK_RUNTIME_DIAGNOSTIC_DETAIL_LIMIT = 8

_STRUCTURED_VALUE_PATTERN = re.compile(r"[a-z][a-z0-9_]{0,63}")
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


def runtime_diagnostic_value(value: object) -> object:
    serialized = json.dumps(
        value,
        ensure_ascii=False,
        default=str,
        separators=(",", ":"),
    )
    encoded = serialized.encode("utf-8", errors="replace")
    if len(encoded) <= SDK_RUNTIME_DIAGNOSTIC_VALUE_MAX_BYTES:
        return json.loads(encoded.decode("utf-8"))
    return {
        "truncated": True,
        "size_bytes": len(encoded),
        "sha256": hashlib.sha256(encoded).hexdigest(),
    }


def _structured_value(value: object) -> str:
    text = value if isinstance(value, str) else ""
    return text if _STRUCTURED_VALUE_PATTERN.fullmatch(text) else ""


def _normalize_sdk(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {}
    normalized = {
        key: runtime_diagnostic_value(raw)
        for key, raw in value.items()
        if key in _SDK_VALUE_FIELDS and raw not in (None, "", [])
    }
    exception_type = runtime_diagnostic_text(
        value.get("exception_type"),
        max_bytes=SDK_RUNTIME_DIAGNOSTIC_IDENTITY_MAX_BYTES,
    )
    if exception_type:
        normalized["exception_type"] = exception_type
    for key in ("exception_message", "exception_traceback"):
        text = runtime_diagnostic_text(value.get(key))
        if text:
            normalized[key] = text
    return normalized


def _normalize_tool_lifecycle(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None
    tool_name = runtime_diagnostic_text(
        value.get("tool_name"),
        max_bytes=SDK_RUNTIME_DIAGNOSTIC_IDENTITY_MAX_BYTES,
    )
    invocation_id = runtime_diagnostic_text(
        value.get("invocation_id"),
        max_bytes=SDK_RUNTIME_DIAGNOSTIC_IDENTITY_MAX_BYTES,
    )
    state = _structured_value(value.get("state"))
    if not tool_name or not invocation_id or not state:
        return None
    normalized: dict[str, object] = {
        "tool_name": tool_name,
        "invocation_id": invocation_id,
        "state": state,
    }
    capability_kind = _structured_value(value.get("capability_kind"))
    if capability_kind:
        normalized["capability_kind"] = capability_kind
    return normalized


def _normalize_tool_call(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None
    tool_name = runtime_diagnostic_text(
        value.get("tool_name"),
        max_bytes=SDK_RUNTIME_DIAGNOSTIC_IDENTITY_MAX_BYTES,
    )
    invocation_id = runtime_diagnostic_text(
        value.get("invocation_id"),
        max_bytes=SDK_RUNTIME_DIAGNOSTIC_IDENTITY_MAX_BYTES,
    )
    if not tool_name or not invocation_id:
        return None
    normalized: dict[str, object] = {
        "tool_name": tool_name,
        "invocation_id": invocation_id,
    }
    for key in ("state", "last_stage"):
        structured = _structured_value(value.get(key))
        if structured:
            normalized[key] = structured
    for key in ("tool_input", "failure"):
        if key in value and value[key] is not None:
            normalized[key] = runtime_diagnostic_value(value[key])
    return normalized


def _normalize_policy_denial(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None
    tool_name = runtime_diagnostic_text(
        value.get("tool_name"),
        max_bytes=SDK_RUNTIME_DIAGNOSTIC_IDENTITY_MAX_BYTES,
    )
    if not tool_name:
        return None
    normalized: dict[str, object] = {"tool_name": tool_name}
    invocation_id = runtime_diagnostic_text(
        value.get("invocation_id"),
        max_bytes=SDK_RUNTIME_DIAGNOSTIC_IDENTITY_MAX_BYTES,
    )
    if invocation_id:
        normalized["invocation_id"] = invocation_id
    reason = runtime_diagnostic_text(value.get("reason"), max_bytes=1_024)
    if reason:
        normalized["reason"] = reason
    if "tool_input" in value and value["tool_input"] is not None:
        normalized["tool_input"] = runtime_diagnostic_value(value["tool_input"])
    return normalized


def _fit_runtime_diagnostics(payload: dict[str, Any]) -> dict[str, Any]:
    original_counts = {
        key: len(payload[key])
        for key in ("tool_lifecycles", "tool_calls", "tool_policy_denials")
    }

    def encoded_size() -> int:
        return len(_json_bytes(payload))

    target_bytes = SDK_RUNTIME_DIAGNOSTICS_MAX_BYTES - 2_048
    # ponytail: bounded lists make repeated encoding cheap; stream sizing only if limits grow.
    for key in ("tool_lifecycles", "tool_calls", "tool_policy_denials"):
        while encoded_size() > target_bytes and len(payload[key]) > 1:
            del payload[key][0]
    retained_counts = {key: len(payload[key]) for key in original_counts}
    if retained_counts != original_counts:
        truncated = dict(payload.get("truncated") or {})
        for key in original_counts:
            if retained_counts[key] != original_counts[key]:
                existing = truncated.get(key)
                truncated[key] = {
                    "original": (
                        existing.get("original", original_counts[key])
                        if isinstance(existing, dict)
                        else original_counts[key]
                    ),
                    "retained": retained_counts[key],
                }
        payload["truncated"] = truncated
    if encoded_size() > SDK_RUNTIME_DIAGNOSTICS_MAX_BYTES:
        payload = {
            key: value
            for key, value in payload.items()
            if key
            not in {
                "sdk",
                "tool_lifecycles",
                "tool_calls",
                "tool_policy_denials",
                "truncated",
            }
        }
        payload.update(
            {
                "sdk": {},
                "tool_lifecycles": [],
                "tool_calls": [],
                "tool_policy_denials": [],
                "truncated": {
                    key: {"original": count, "retained": 0}
                    for key, count in original_counts.items()
                    if count
                },
            }
        )
    if len(_json_bytes(payload)) > SDK_RUNTIME_DIAGNOSTICS_MAX_BYTES:
        return {
            "schema_version": SDK_RUNTIME_DIAGNOSTICS_SCHEMA_VERSION,
            "error_code": payload["error_code"],
        }
    return payload


def normalize_sdk_runtime_diagnostics(value: object) -> dict[str, Any]:
    """Validate and bound private SDK diagnostics at every sandbox boundary."""

    if not isinstance(value, dict):
        return {}
    if value.get("schema_version") != SDK_RUNTIME_DIAGNOSTICS_SCHEMA_VERSION:
        return {}
    error_code = _structured_value(value.get("error_code"))
    if not error_code:
        return {}
    normalized: dict[str, Any] = {
        "schema_version": SDK_RUNTIME_DIAGNOSTICS_SCHEMA_VERSION,
        "error_code": error_code,
        "failure_source": runtime_diagnostic_text(
            value.get("failure_source"),
            max_bytes=SDK_RUNTIME_DIAGNOSTIC_IDENTITY_MAX_BYTES,
        ),
        "failure_stage": runtime_diagnostic_text(
            value.get("failure_stage"),
            max_bytes=SDK_RUNTIME_DIAGNOSTIC_IDENTITY_MAX_BYTES,
        ),
        "sdk": _normalize_sdk(value.get("sdk")),
    }
    for key in ("runner_error_code",):
        structured = _structured_value(value.get(key))
        if structured:
            normalized[key] = structured
    runner_failure_source = runtime_diagnostic_text(
        value.get("runner_failure_source"),
        max_bytes=SDK_RUNTIME_DIAGNOSTIC_IDENTITY_MAX_BYTES,
    )
    if runner_failure_source:
        normalized["runner_failure_source"] = runner_failure_source

    list_specs = (
        (
            "tool_lifecycles",
            SDK_RUNTIME_DIAGNOSTIC_LIFECYCLE_LIMIT,
            _normalize_tool_lifecycle,
        ),
        ("tool_calls", SDK_RUNTIME_DIAGNOSTIC_DETAIL_LIMIT, _normalize_tool_call),
        (
            "tool_policy_denials",
            SDK_RUNTIME_DIAGNOSTIC_DETAIL_LIMIT,
            _normalize_policy_denial,
        ),
    )
    truncated: dict[str, dict[str, int]] = {}
    previous_truncated = value.get("truncated")
    previous_truncated = (
        previous_truncated if isinstance(previous_truncated, dict) else {}
    )
    for key, limit, normalize_item in list_specs:
        raw_items = value.get(key)
        items = raw_items if isinstance(raw_items, list) else []
        projected = [
            item
            for raw_item in items[-limit:]
            if (item := normalize_item(raw_item)) is not None
        ]
        normalized[key] = projected
        previous_count = previous_truncated.get(key)
        previous_original = (
            min(previous_count.get("original"), 1_000_000)
            if isinstance(previous_count, dict)
            and type(previous_count.get("original")) is int
            and previous_count["original"] >= 0
            else 0
        )
        original_count = max(len(items), previous_original)
        if original_count > len(projected):
            truncated[key] = {
                "original": original_count,
                "retained": len(projected),
            }
    if truncated:
        normalized["truncated"] = truncated
    return _fit_runtime_diagnostics(normalized)
