"""Framework-neutral projection helpers for the admin Run Monitor."""

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from app.streaming.api import V4_METADATA_KEY

_V4_MESSAGE_TYPES = frozenset({"message.delta", "message.completed"})
_LEGACY_MESSAGE_TYPE = "assistant_delta"


def _sequence(event: Mapping[str, Any], index: int) -> tuple[int, int]:
    value = event.get("sequence")
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value, index
    return index + 1, index


def _message_identity(event: Mapping[str, Any]) -> str | None:
    payload = event.get("payload")
    if not isinstance(payload, Mapping):
        return None
    metadata = payload.get(V4_METADATA_KEY)
    if not isinstance(metadata, Mapping):
        return None
    message_id = metadata.get("message_id")
    return message_id if isinstance(message_id, str) and message_id else None


def _text_field(event: Mapping[str, Any], key: str) -> str | None:
    payload = event.get("payload")
    if not isinstance(payload, Mapping):
        return None
    value = payload.get(key)
    return value if isinstance(value, str) and value else None


def assemble_admin_model_output(
    events: Sequence[Mapping[str, Any]],
    *,
    sanitize_text: Callable[[str], str],
) -> str:
    """Assemble only authorized assistant-body events, then sanitize the whole body.

    The complete-body sanitization is required because one private token or path can
    be split across otherwise harmless persisted chunks. A v4 message identity is
    also used to select the latest message and prevent compatibility mirroring from
    duplicating its output.
    """

    ordered = sorted(enumerate(events), key=lambda item: _sequence(item[1], item[0]))
    has_v4_message = any(
        event.get("type") in _V4_MESSAGE_TYPES
        for _, event in ordered
    )
    if has_v4_message:
        messages: dict[str, list[str]] = {}
        latest_identity: str | None = None
        latest_position = (-1, -1)
        completed: dict[str, str] = {}
        for index, event in ordered:
            if event.get("type") not in _V4_MESSAGE_TYPES:
                continue
            if event.get("visible_to_user") is not True:
                continue
            identity = _message_identity(event)
            if identity is None:
                continue
            position = _sequence(event, index)
            if position > latest_position:
                latest_identity, latest_position = identity, position
            messages.setdefault(identity, [])
            if event.get("type") == "message.delta":
                delta = _text_field(event, "delta")
                if delta is not None:
                    messages[identity].append(delta)
            else:
                content = _text_field(event, "content")
                if content is not None:
                    completed[identity] = content
        if latest_identity is None:
            return ""
        body = completed.get(latest_identity, "".join(messages.get(latest_identity, [])))
    else:
        body_parts: list[str] = []
        seen_ids: set[str] = set()
        for index, event in ordered:
            if event.get("type") != _LEGACY_MESSAGE_TYPE or event.get("visible_to_user") is not True:
                continue
            event_id = event.get("event_id")
            if isinstance(event_id, str) and event_id:
                if event_id in seen_ids:
                    continue
                seen_ids.add(event_id)
            delta = _text_field(event, "delta")
            if delta is None:
                message = event.get("message")
                delta = message if isinstance(message, str) and message else None
            if delta is not None:
                body_parts.append(delta)
        body = "".join(body_parts)

    if not body:
        return ""
    sanitized = sanitize_text(body)
    return sanitized if isinstance(sanitized, str) else ""


__all__ = ["assemble_admin_model_output"]
