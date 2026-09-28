from __future__ import annotations

from typing import Any


EXECUTOR_CONVERSATION_CONTEXT_SCHEMA_VERSION_V2 = "ai-platform.executor-conversation-context.v2"


def empty_executor_conversation_context() -> dict[str, Any]:
    return {
        "schema_version": EXECUTOR_CONVERSATION_CONTEXT_SCHEMA_VERSION_V2,
        "messages": [],
        "selected_message_count": 0,
        "selected_turn_count": 0,
        "dropped_turn_count": 0,
        "estimated_bytes": 0,
    }
