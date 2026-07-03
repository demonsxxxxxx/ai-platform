from typing import Any

from app.control_plane_contracts import sanitize_public_text


def _safe_error_token(value: object, fallback: str, max_length: int = 120) -> str:
    token = sanitize_public_text(value)
    if not token or "[redacted-" in token:
        return fallback
    return token[:max_length]


def queue_payload_invalid_detail(exc: ValueError) -> str | dict[str, Any]:
    errors_fn = getattr(exc, "errors", None)
    if not callable(errors_fn):
        return "queue_payload_invalid"
    try:
        raw_errors = errors_fn()
    except Exception:
        return "queue_payload_invalid"
    if not isinstance(raw_errors, list):
        return "queue_payload_invalid"

    errors: list[dict[str, Any]] = []
    for item in raw_errors[:8]:
        if not isinstance(item, dict):
            continue
        loc = item.get("loc")
        if isinstance(loc, (list, tuple)):
            safe_loc = [
                part
                if isinstance(part, int)
                else _safe_error_token(str(part), "field")
                for part in loc
            ]
        elif loc is None:
            safe_loc = []
        else:
            safe_loc = [_safe_error_token(str(loc), "field")]
        message = sanitize_public_text(item.get("msg") or item.get("type") or "validation_error")
        error_type = _safe_error_token(item.get("type") or "validation_error", "validation_error")
        errors.append(
            {
                "loc": safe_loc,
                "type": error_type,
                "message": (message or "validation_error")[:240],
            }
        )

    if not errors:
        return "queue_payload_invalid"
    return {"code": "queue_payload_invalid", "errors": errors}
