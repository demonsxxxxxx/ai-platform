from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.control_plane_contracts import sanitize_public_payload, sanitize_public_text, standard_trace_id

TOOL_PERMISSION_CARD_SCHEMA_VERSION = "ai-platform.tool-permission-card.v1"
TOOL_PERMISSION_PRIVATE_PAYLOAD_KEYS = {
    "".join(ch for ch in key if ch.isalnum()).lower()
    for key in {
        "command",
        "raw_command",
        "command_text",
        "command_sha256",
        "decision_payload",
        "input_sha256",
        "fingerprint",
        "command_fingerprint",
        "input_fingerprint",
        "request_payload",
    }
}


def sanitize_tool_permission_payload(value: Any) -> dict[str, Any]:
    sanitized = sanitize_public_payload(value if isinstance(value, dict) else {})
    if not isinstance(sanitized, dict):
        return {}
    redacted = _redact_tool_permission_private_payload(sanitized)
    return redacted if isinstance(redacted, dict) else {}


def _redact_tool_permission_private_payload(value: Any) -> Any:
    if isinstance(value, dict):
        cleaned = {}
        for key, item in value.items():
            normalized_key = "".join(ch for ch in str(key) if ch.isalnum()).lower()
            if normalized_key in TOOL_PERMISSION_PRIVATE_PAYLOAD_KEYS:
                continue
            cleaned[key] = _redact_tool_permission_private_payload(item)
        return cleaned
    if isinstance(value, list):
        return [_redact_tool_permission_private_payload(item) for item in value]
    return value


def permission_response(row: dict[str, Any]) -> dict[str, Any]:
    """Return an owner-visible permission record without governance controls."""

    run_id = str(row["run_id"])
    request_id = str(row["id"])
    return {
        "permission_request_id": request_id,
        "tenant_id": str(row["tenant_id"]),
        "workspace_id": str(row["workspace_id"]),
        "user_id": str(row["user_id"]),
        "session_id": str(row["session_id"]),
        "run_id": run_id,
        "trace_id": str(row.get("trace_id") or standard_trace_id(run_id)),
        "tool_id": str(row["tool_id"]),
        "tool_call_id": str(row["tool_call_id"]),
        "action": str(row.get("action") or "execute"),
        "risk_level": _risk_level(row.get("risk_level")),
        "write_capable": bool(row.get("write_capable")),
        "status": str(row.get("status") or "pending"),
        "decision": row.get("decision"),
        "reason": sanitize_public_text(row.get("reason")),
        "created_at": row.get("created_at"),
        "decided_at": row.get("decided_at"),
        "expires_at": row.get("expires_at"),
    }


def inbox_permission_response(row: dict[str, Any]) -> dict[str, Any]:
    """Return the tenant-inbox allowlist without owner-controlled request details."""
    run_id = str(row["run_id"])
    request_id = str(row["id"])
    tool_id = _public_text(row.get("tool_id")) or "tool"
    return {
        "request_id": request_id,
        "run_id": run_id,
        "tool_id": tool_id,
        "tool_display": tool_id,
        "risk_level": _risk_level(row.get("risk_level")),
        "write_capable": bool(row.get("write_capable")),
        "status": str(row.get("status") or "pending"),
        "expires_at": row.get("expires_at"),
        "allowed_decisions": inbox_allowed_decisions(row),
    }


def inbox_allowed_decisions(row: dict[str, Any]) -> list[str]:
    """Historical records intentionally expose no runtime decision controls."""

    _ = row
    return []


def _permission_request_expired(value: object) -> bool:
    """Fail closed for an invalid persisted expiry and close elapsed cards truthfully."""

    if value is None:
        return True
    if isinstance(value, datetime):
        expires_at = value
    elif isinstance(value, str):
        try:
            expires_at = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return True
    else:
        return True
    if expires_at.tzinfo is None:
        return True
    return expires_at <= datetime.now(timezone.utc)


def tool_permission_public_event_payload(
    *,
    run_id: str,
    event_type: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    card = tool_permission_card_from_payload(run_id=run_id, event_type=event_type, payload=payload)
    if card is None:
        return sanitize_tool_permission_payload(payload)
    return {
        "visible_to_user": bool(payload.get("visible_to_user", True)),
        "tool_permission_card": card,
    }


def tool_permission_card_from_payload(
    *,
    run_id: str,
    event_type: str,
    payload: dict[str, Any],
) -> dict[str, Any] | None:
    sanitized = sanitize_tool_permission_payload(payload)
    request_id = _public_text(sanitized.get("permission_request_id") or sanitized.get("request_id"))
    if not request_id:
        return None
    decision = _public_text(sanitized.get("decision")) or None
    status = _public_text(sanitized.get("status"))
    if not status:
        status = "decided" if event_type == "tool_permission_decided" or decision else "pending"
    card = {
        "schema_version": TOOL_PERMISSION_CARD_SCHEMA_VERSION,
        "permission_request_id": request_id,
        "run_id": str(run_id),
        "tool_id": _public_text(sanitized.get("tool_id")) or "tool",
        "tool_call_id": _public_text(sanitized.get("tool_call_id")) or "",
        "action": _public_text(sanitized.get("action")) or "execute",
        "risk_level": _risk_level(sanitized.get("risk_level")),
        "write_capable": bool(sanitized.get("write_capable")),
        "reason": _public_text(sanitized.get("reason")),
        "status": status,
        "decision": decision,
    }
    if sanitized.get("created_at") is not None:
        card["created_at"] = sanitized.get("created_at")
    if sanitized.get("decided_at") is not None:
        card["decided_at"] = sanitized.get("decided_at")
    if sanitized.get("expires_at") is not None:
        card["expires_at"] = sanitized.get("expires_at")
    return card


def _public_text(value: object) -> str:
    return sanitize_public_text(value)


def _risk_level(value: object) -> str:
    risk_level = _public_text(value) or "low"
    return risk_level if risk_level in {"low", "medium", "high"} else "low"
