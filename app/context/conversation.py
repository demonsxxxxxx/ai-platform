from __future__ import annotations

import json
from typing import Any

from app.context_manifest import utf8_token_estimate

EXECUTOR_CONVERSATION_CONTEXT_SCHEMA_VERSION = (
    "ai-platform.executor-conversation-context.v1"
)
DEFAULT_CONVERSATION_HISTORY_BYTES = 8192
_ALLOWED_CONVERSATION_ROLES = {"user", "assistant"}


class ConversationContextError(ValueError):
    pass


def empty_executor_conversation_context(
    *, max_history_bytes: int = DEFAULT_CONVERSATION_HISTORY_BYTES
) -> dict[str, Any]:
    return {
        "schema_version": EXECUTOR_CONVERSATION_CONTEXT_SCHEMA_VERSION,
        "messages": [],
        "selected_message_count": 0,
        "selected_turn_count": 0,
        "dropped_turn_count": 0,
        "estimated_bytes": 0,
        "max_history_bytes": max(0, int(max_history_bytes)),
    }


def _message_order_key(row: dict[str, Any]) -> tuple[str, str]:
    created_at = row.get("created_at")
    if hasattr(created_at, "isoformat"):
        created_at = created_at.isoformat()
    return str(created_at or ""), str(row.get("id") or "")


def _message_cost(message: dict[str, str]) -> int:
    return utf8_token_estimate(
        json.dumps(
            {"role": message["role"], "content": message["content"]},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        + "\n"
    )


def _group_complete_turns(messages: list[dict[str, str]]) -> list[list[dict[str, str]]]:
    turns: list[list[dict[str, str]]] = []
    current: list[dict[str, str]] = []
    for message in messages:
        if message["role"] == "user":
            if current:
                turns.append(current)
            current = [message]
        elif current:
            current.append(message)
    if current:
        turns.append(current)
    return turns


def build_executor_conversation_context(
    rows: list[dict[str, Any]],
    *,
    selected_message_ids: list[str],
    current_run_id: str,
    max_history_bytes: int = DEFAULT_CONVERSATION_HISTORY_BYTES,
) -> dict[str, Any]:
    """Materialize and trim snapshot-authorized history as complete user turns."""

    normalized_ids = [str(message_id or "").strip() for message_id in selected_message_ids]
    if any(not message_id for message_id in normalized_ids) or len(normalized_ids) != len(
        set(normalized_ids)
    ):
        raise ConversationContextError("conversation_context_selected_ids_invalid")

    actual_ids = [str(row.get("id") or "").strip() for row in rows]
    if any(not message_id for message_id in actual_ids) or len(actual_ids) != len(
        set(actual_ids)
    ):
        raise ConversationContextError("conversation_context_materialization_invalid")
    if set(actual_ids) != set(normalized_ids):
        raise ConversationContextError("conversation_context_materialization_incomplete")
    if rows != sorted(rows, key=_message_order_key):
        raise ConversationContextError("conversation_context_materialization_reordered")

    history: list[dict[str, str]] = []
    for row in rows:
        if str(row.get("run_id") or "") == current_run_id:
            continue
        role = str(row.get("role") or "").strip().lower()
        if role not in _ALLOWED_CONVERSATION_ROLES:
            continue
        content = row.get("content")
        if not isinstance(content, str):
            raise ConversationContextError("conversation_context_content_invalid")
        history.append(
            {
                "message_id": str(row["id"]),
                "run_id": str(row.get("run_id") or ""),
                "role": role,
                "content": content,
            }
        )

    turns = _group_complete_turns(history)
    if not turns:
        return empty_executor_conversation_context(max_history_bytes=max_history_bytes)

    budget = max(0, int(max_history_bytes))
    selected_turns: list[list[dict[str, str]]] = []
    estimated_bytes = 0
    for turn in reversed(turns):
        turn_bytes = sum(_message_cost(message) for message in turn)
        if selected_turns and estimated_bytes + turn_bytes > budget:
            break
        selected_turns.insert(0, turn)
        estimated_bytes += turn_bytes

    selected_messages = [message for turn in selected_turns for message in turn]
    return {
        "schema_version": EXECUTOR_CONVERSATION_CONTEXT_SCHEMA_VERSION,
        "messages": selected_messages,
        "selected_message_count": len(selected_messages),
        "selected_turn_count": len(selected_turns),
        "dropped_turn_count": len(turns) - len(selected_turns),
        "estimated_bytes": estimated_bytes,
        "max_history_bytes": budget,
    }
