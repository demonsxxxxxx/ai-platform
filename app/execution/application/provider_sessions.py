from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.context.api import (
    PROVIDER_SESSION_RESUME_CONTEXT_KEY,
    claude_provider_session_id_for_session,
)


def claude_provider_session_dispatch(
    payload: object,
    context_pack: Mapping[str, Any],
) -> dict[str, object]:
    """Bind private provider identity and Context-owned resume evidence for Harness."""
    conversation_context = context_pack.get("conversation_context")
    marker = (
        conversation_context.get(PROVIDER_SESSION_RESUME_CONTEXT_KEY, False)
        if isinstance(conversation_context, Mapping)
        else False
    )
    if type(marker) is not bool:
        raise ValueError("provider_session_resume_required_invalid")
    return {
        "sdk_session_id": claude_provider_session_id_for_session(
            tenant_id=getattr(payload, "tenant_id", ""),
            workspace_id=getattr(payload, "workspace_id", ""),
            user_id=getattr(payload, "user_id", ""),
            session_id=getattr(payload, "session_id", ""),
            agent_id=getattr(payload, "agent_id", ""),
        ),
        "provider_session_resume_required": marker,
    }


__all__ = ["claude_provider_session_dispatch"]
