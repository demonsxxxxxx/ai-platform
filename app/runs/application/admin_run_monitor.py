"""Framework-neutral projection helpers for the admin Run Monitor."""

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from app.streaming.api import AssistantTextMessageProjection, V4_METADATA_KEY

_V4_MESSAGE_TYPES = frozenset({"message.delta", "message.completed"})
_V4_PART_TYPES = frozenset({"message.part.delta", "message.part.classified"})
_V4_COMMENTARY_TYPE = "commentary.delta"
_LEGACY_MESSAGE_TYPE = "assistant_delta"
_TOOL_START_TYPES = frozenset({"mcp_tool_call_started", "tool_call_started", "tool.started"})
_TOOL_SUCCESS_TYPES = frozenset({"mcp_tool_call_completed", "tool_call_completed", "tool.completed"})
_TOOL_FAILURE_TYPES = frozenset({"mcp_tool_call_failed", "tool_call_failed", "tool.failed"})
_TOOL_DENIAL_TYPES = frozenset({"mcp_tool_denied", "tool_denied", "tool.denied", "tool_permission_denied"})
_TOOL_TYPES = _TOOL_START_TYPES | _TOOL_SUCCESS_TYPES | _TOOL_FAILURE_TYPES | _TOOL_DENIAL_TYPES


def _sequence(event: Mapping[str, Any], index: int) -> tuple[int, int]:
    value = event.get("sequence")
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value, index
    return index + 1, index


def _message_identity(event: Mapping[str, Any]) -> tuple[str, str, int, int] | None:
    payload = event.get("payload")
    if not isinstance(payload, Mapping):
        return None
    metadata = payload.get(V4_METADATA_KEY)
    if not isinstance(metadata, Mapping):
        return None
    message_id = metadata.get("message_id")
    if not isinstance(message_id, str) or not message_id:
        return None
    return (
        message_id,
        metadata.get("attempt_id") if isinstance(metadata.get("attempt_id"), str) else "",
        metadata.get("stream_incarnation") if type(metadata.get("stream_incarnation")) is int else 0,
        metadata.get("authorization_epoch") if type(metadata.get("authorization_epoch")) is int else 0,
    )


def _text_field(event: Mapping[str, Any], key: str) -> str | None:
    payload = event.get("payload")
    if not isinstance(payload, Mapping):
        return None
    value = payload.get(key)
    return value if isinstance(value, str) and value else None


def _latest_answer(messages: Sequence[Mapping[str, Any]]) -> str:
    answers = [message for message in messages if message["kind"] == "answer"]
    return max(answers, key=lambda message: message["sequence"])["text"] if answers else ""


def assemble_admin_public_messages(
    events: Sequence[Mapping[str, Any]],
    *,
    sanitize_text: Callable[[str], str],
    part_messages: Sequence[AssistantTextMessageProjection] = (),
) -> list[dict[str, Any]]:
    """Project each visible Assistant message after whole-message redaction.

    Message identities are only grouping keys. They and private v4 metadata must
    not become display fields. Legacy deltas lack an identity and form one block.
    """

    ordered = sorted(enumerate(events), key=lambda item: _sequence(item[1], item[0]))
    has_v4_answer = any(
        event.get("visible_to_user") is True and event.get("type") in _V4_MESSAGE_TYPES | _V4_PART_TYPES
        for _, event in ordered
    )
    part_identities = {
        _message_identity(event) for _, event in ordered
        if event.get("visible_to_user") is True and event.get("type") in _V4_PART_TYPES
    }
    blocks: dict[tuple[str, object, str], dict[str, Any]] = {}
    seen_legacy_ids: set[str] = set()
    for index, event in ordered:
        if event.get("visible_to_user") is not True:
            continue
        event_type = event.get("type")
        if event_type in _V4_MESSAGE_TYPES | {_V4_COMMENTARY_TYPE}:
            identity = _message_identity(event)
            if identity is None:
                continue
            if event_type in _V4_MESSAGE_TYPES and identity in part_identities:
                continue  # Part lifecycles are owned by Streaming's validated reducer.
            kind = "commentary" if event_type == _V4_COMMENTARY_TYPE else "answer"
            summary_id = ""
            if kind == "commentary":
                commentary_id = _text_field(event, "summary_id")
                if commentary_id is None:
                    continue
                summary_id = commentary_id
            key = (kind, identity, summary_id)
        elif event_type == _LEGACY_MESSAGE_TYPE and not has_v4_answer:
            event_id = event.get("event_id")
            if isinstance(event_id, str) and event_id:
                if event_id in seen_legacy_ids:
                    continue
                seen_legacy_ids.add(event_id)
            kind = "answer"
            key = (kind, "legacy", "")
        else:
            continue
        block = blocks.setdefault(
            key,
            {
                "kind": kind,
                "sequence": _sequence(event, index)[0],
                "created_at": event.get("created_at"),
                "last_sequence": _sequence(event, index)[0],
                "last_created_at": event.get("created_at"),
                "parts": [],
                "completed": None,
            },
        )
        block["last_sequence"] = _sequence(event, index)[0]
        block["last_created_at"] = event.get("created_at")
        if event_type == "message.completed":
            content = _text_field(event, "content")
            if content is not None:
                block["completed"] = content
        else:
            delta = _text_field(event, "delta")
            if delta is None and event_type == _LEGACY_MESSAGE_TYPE:
                value = event.get("message")
                delta = value if isinstance(value, str) and value else None
            if delta is not None:
                block["parts"].append(delta)

    messages: list[dict[str, Any]] = []
    for block in blocks.values():
        body = block["completed"] or "".join(block["parts"])
        if not body:
            continue
        sanitized = sanitize_text(body)
        if not isinstance(sanitized, str) or not sanitized:
            continue
        messages.append(
            {
                "ordinal": len(messages) + 1,
                "kind": block["kind"],
                "text": sanitized,
                "sequence": block["last_sequence"] if block["kind"] == "answer" else block["sequence"],
                "created_at": block["last_created_at"] if block["kind"] == "answer" else block["created_at"],
            }
        )
    for message in part_messages:
        if message.status != "complete" or not message.text:
            continue
        sanitized = sanitize_text(message.text)
        if not isinstance(sanitized, str) or not sanitized:
            continue
        messages.append({
            "ordinal": 0, "kind": "answer", "text": sanitized,
            "sequence": message.sequence, "created_at": message.created_at,
        })
    messages.sort(key=lambda message: message["sequence"])
    for ordinal, message in enumerate(messages, 1):
        message["ordinal"] = ordinal
    return messages


def _safe_text(value: object, sanitize_text: Callable[[str], str]) -> str:
    if not isinstance(value, str) or not value.strip():
        return ""
    sanitized = sanitize_text(value)
    return sanitized.strip() if isinstance(sanitized, str) else ""


def _tool_invocation_id(event: Mapping[str, Any]) -> str | None:
    payload = event.get("payload")
    if isinstance(payload, Mapping):
        for key in ("operation_id", "tool_call_id", "tool_use_id"):
            value = payload.get(key)
            if isinstance(value, str) and value:
                return value
    return None


def _tool_identity(event: Mapping[str, Any], index: int) -> str:
    invocation_id = _tool_invocation_id(event)
    if invocation_id is not None:
        return invocation_id
    event_id = event.get("event_id")
    return event_id if isinstance(event_id, str) and event_id else f"tool-{index}"


def _tool_status(event_type: str) -> str:
    if event_type in _TOOL_SUCCESS_TYPES:
        return "succeeded"
    if event_type in _TOOL_FAILURE_TYPES:
        return "failed"
    if event_type in _TOOL_DENIAL_TYPES:
        return "denied"
    return "running"


def _bounded_non_negative_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def build_admin_worker_execution(
    events: Sequence[Mapping[str, Any]],
    *,
    sanitize_text: Callable[[str], str],
    part_messages: Sequence[AssistantTextMessageProjection] = (),
) -> dict[str, Any]:
    """Project one Run into its safe, human-readable Worker execution."""

    ordered = sorted(enumerate(events), key=lambda item: _sequence(item[1], item[0]))
    actions: list[dict[str, Any]] = []
    action_indexes: dict[str, int] = {}
    model: dict[str, Any] = {}

    for index, event in ordered:
        if event.get("visible_to_user") is False:
            continue
        event_type = str(event.get("type") or "")
        payload = event.get("payload")
        payload = payload if isinstance(payload, Mapping) else {}

        if event_type == "model.completed":
            model = {
                "turn_count": _bounded_non_negative_int(payload.get("turn_count")),
                "duration_ms": _bounded_non_negative_int(payload.get("duration_ms")),
                "stop_category": _safe_text(payload.get("stop_category"), sanitize_text),
            }
            continue
        if event_type not in _TOOL_TYPES:
            continue

        identity = _tool_identity(event, index)
        action_index = action_indexes.get(identity)
        if action_index is None:
            label = ""
            for key in ("display_name", "tool_name", "name"):
                label = _safe_text(payload.get(key), sanitize_text)
                if label:
                    break
            action = {
                "ordinal": len(actions) + 1,
                "sequence": _sequence(event, index)[0],
                "invocation_id": (
                    _safe_text(identity, sanitize_text)[:512]
                    if _tool_invocation_id(event) is not None
                    else None
                ),
                "label": label or "工具调用",
                "category": _safe_text(payload.get("category"), sanitize_text),
                "status": _tool_status(event_type),
                "input_summary": _safe_text(payload.get("input_summary"), sanitize_text),
                "result_summary": "",
                "duration_ms": _bounded_non_negative_int(payload.get("duration_ms")),
                "started_at": event.get("created_at") if event_type in _TOOL_START_TYPES else None,
                "finished_at": None,
            }
            actions.append(action)
            action_index = len(actions) - 1
            action_indexes[identity] = action_index

        action = actions[action_index]
        action["status"] = _tool_status(event_type)
        label = _safe_text(payload.get("display_name"), sanitize_text)
        if label:
            action["label"] = label
        category = _safe_text(payload.get("category"), sanitize_text)
        if category:
            action["category"] = category
        input_summary = _safe_text(payload.get("input_summary"), sanitize_text)
        if input_summary:
            action["input_summary"] = input_summary
        duration_ms = _bounded_non_negative_int(payload.get("duration_ms"))
        if duration_ms is not None:
            action["duration_ms"] = duration_ms
        if event_type not in _TOOL_START_TYPES:
            result_summary = ""
            for key in ("result_summary", "failure_category", "denial_code"):
                result_summary = _safe_text(payload.get(key), sanitize_text)
                if result_summary:
                    break
            if not result_summary:
                result_summary = _safe_text(event.get("error_code"), sanitize_text)
            action["result_summary"] = result_summary
            action["finished_at"] = event.get("created_at")

    messages = assemble_admin_public_messages(
        events, sanitize_text=sanitize_text, part_messages=part_messages,
    )
    response = _latest_answer(messages)
    incomplete = sum(message.status == "incomplete" for message in part_messages)
    invalid = sum(message.status == "invalid" for message in part_messages)
    return {
        "response": response,
        "messages": messages,
        "answer_projection": {
            "status": "available" if response else "invalid" if invalid else "incomplete" if incomplete else "unknown",
            "incomplete_messages": incomplete,
            "invalid_messages": invalid,
        },
        "actions": actions,
        "model": model,
    }


__all__ = ["build_admin_worker_execution"]
