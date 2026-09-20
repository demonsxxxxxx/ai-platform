from __future__ import annotations

import json
from typing import Any


EXECUTOR_CONVERSATION_CONTEXT_SCHEMA_VERSION = (
    "ai-platform.executor-conversation-context.v1"
)
EXECUTOR_CONVERSATION_CONTEXT_SCHEMA_VERSION_V2 = "ai-platform.executor-conversation-context.v2"
_ALLOWED_CONVERSATION_ROLES = {"user", "assistant"}


class ConversationContextError(ValueError):
    pass


def empty_executor_conversation_context() -> dict[str, Any]:
    return {
        "schema_version": EXECUTOR_CONVERSATION_CONTEXT_SCHEMA_VERSION,
        "messages": [],
        "selected_message_count": 0,
        "selected_turn_count": 0,
        "dropped_turn_count": 0,
        "estimated_bytes": 0,
        "max_history_bytes": None,
    }


def _message_order_key(row: dict[str, Any]) -> tuple[str, str]:
    created_at = row.get("created_at")
    if hasattr(created_at, "isoformat"):
        created_at = created_at.isoformat()
    return str(created_at or ""), str(row.get("id") or "")


def _message_cost(message: dict[str, str]) -> int:
    rendered = (
        json.dumps(
            {"role": message["role"], "content": message["content"]},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        + "\n"
    )
    return len(rendered.encode("utf-8"))


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


def build_executor_conversation_context_v2(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Render an exact verified source tail without an old-history byte selector."""
    history = [
        {"message_id": row["id"], "run_id": row["run_id"], "role": row["role"], "content": row["content"]}
        for row in rows
    ]
    if history and history[0]["role"] != "user":
        raise ConversationContextError("conversation_source_turn_invalid")
    turns = _group_complete_turns(history)
    if sum(map(len, turns)) != len(history):
        raise ConversationContextError("conversation_source_turn_invalid")
    return {
        "schema_version": EXECUTOR_CONVERSATION_CONTEXT_SCHEMA_VERSION_V2,
        "messages": history,
        "selected_message_count": len(history),
        "selected_turn_count": len(turns),
        "dropped_turn_count": 0,
        "estimated_bytes": sum(_message_cost(message) for message in history),
    }


def build_executor_conversation_context(
    rows: list[dict[str, Any]],
    *,
    selected_message_ids: list[str],
    current_run_id: str,
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
    ordered_rows = sorted(rows, key=_message_order_key)

    history: list[dict[str, str]] = []
    for row in ordered_rows:
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
        return empty_executor_conversation_context()

    selected_turns = turns
    estimated_bytes = sum(
        _message_cost(message) for turn in selected_turns for message in turn
    )

    selected_messages = [message for turn in selected_turns for message in turn]
    return {
        "schema_version": EXECUTOR_CONVERSATION_CONTEXT_SCHEMA_VERSION,
        "messages": selected_messages,
        "selected_message_count": len(selected_messages),
        "selected_turn_count": len(selected_turns),
        "dropped_turn_count": 0,
        "estimated_bytes": estimated_bytes,
        "max_history_bytes": None,
    }
