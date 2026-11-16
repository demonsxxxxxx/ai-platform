"""Runs read-time validation of private model-request facts."""
import re
from typing import Any


def project_model_call(value: object) -> dict[str, Any] | None:
    if not isinstance(value, dict) or not isinstance(value.get("call_id"), str) or not re.fullmatch(r"mcall_[0-9a-f]{32}", value["call_id"]):
        return None
    result: dict[str, Any] = {"call_id": value["call_id"], "source": "platform_model_proxy.v1", "provider_internal_retry": "not_observable"}
    for name in ("started_at", "finished_at"):
        raw = value.get(name)
        result[name] = raw if isinstance(raw, str) and re.fullmatch(r"[0-9T:+.Z-]{10,40}", raw) else None
    for name in ("duration_ms", "first_response_ms", "http_status", "input_tokens", "output_tokens", "text_characters"):
        raw = value.get(name)
        result[name] = raw if type(raw) is int and 0 <= raw <= 2**63 - 1 else None
    result["state"] = value.get("state") if isinstance(value.get("state"), str) and value.get("state") in {"started", "completed", "failed", "closed_before_eof"} else "unknown"
    result["stop_reason"] = value.get("stop_reason") if isinstance(value.get("stop_reason"), str) and value.get("stop_reason") in {"end_turn", "max_tokens", "stop_sequence", "tool_use", "pause_turn", "refusal", "model_context_window_exceeded"} else None
    for name in ("message_started", "message_stopped", "partial"):
        result[name] = value.get(name) if type(value.get(name)) is bool else None
    result["operation_ids"] = [item for item in value.get("operation_ids", [])[:128] if isinstance(item, str) and re.fullmatch(r"op_[0-9a-f]{32}", item)] if isinstance(value.get("operation_ids"), list) else []
    return result
