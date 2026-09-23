from __future__ import annotations

import base64
import binascii
import json
from datetime import datetime
from typing import Any, Callable


_MESSAGE_CURSOR_VERSION = 1


def encode_message_cursor(row: dict[str, Any], *, session_id: str) -> str:
    created_at = row.get("created_at")
    if isinstance(created_at, datetime):
        created_at = created_at.isoformat()
    if not isinstance(created_at, str) or not created_at:
        raise ValueError("message_cursor_invalid")
    payload = {
        "v": _MESSAGE_CURSOR_VERSION,
        "session_id": session_id,
        "created_at": created_at,
        "message_id": str(row.get("id") or ""),
    }
    if not payload["message_id"]:
        raise ValueError("message_cursor_invalid")
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(encoded).decode("ascii").rstrip("=")


def decode_message_cursor(value: str, *, session_id: str) -> tuple[datetime, str]:
    try:
        padding = "=" * (-len(value) % 4)
        payload = json.loads(
            base64.urlsafe_b64decode(f"{value}{padding}").decode("utf-8")
        )
        if (
            not isinstance(payload, dict)
            or payload.get("v") != _MESSAGE_CURSOR_VERSION
            or payload.get("session_id") != session_id
        ):
            raise ValueError
        created_at = datetime.fromisoformat(
            str(payload["created_at"]).replace("Z", "+00:00")
        )
        message_id = str(payload["message_id"])
        if created_at.tzinfo is None or not message_id or len(message_id) > 200:
            raise ValueError
        return created_at, message_id
    except (
        KeyError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
        UnicodeDecodeError,
        binascii.Error,
    ) as exc:
        raise ValueError("message_cursor_invalid") from exc


def message_metadata(
    row: dict[str, object],
    *,
    is_admin: bool,
    redactor: Callable[[object], object],
) -> dict[str, Any]:
    metadata = row.get("metadata_json") or {}
    if not isinstance(metadata, dict):
        return {}
    if is_admin:
        return metadata
    redacted = redactor(metadata)
    return redacted if isinstance(redacted, dict) else {}


def message_content(
    row: dict[str, object],
    *,
    is_admin: bool,
    sanitizer: Callable[[str], str],
) -> str:
    content = str(row["content"])
    if is_admin:
        return content
    return sanitizer(content)
