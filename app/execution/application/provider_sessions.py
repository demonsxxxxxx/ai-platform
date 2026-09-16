from __future__ import annotations

from collections.abc import Mapping
import uuid
from typing import Any


def claude_provider_session_dispatch(
    payload: object,
    context_pack: Mapping[str, Any],
) -> dict[str, object]:
    """Pass only the Context-frozen epoch identity to the SDK harness."""
    conversation_context = context_pack.get("conversation_context")
    if not isinstance(conversation_context, Mapping):
        raise ValueError("provider_session_spec_missing")
    mode = conversation_context.get("execution_mode")
    epoch_id = conversation_context.get("provider_epoch_id")
    session_id = conversation_context.get("provider_session_id")
    try:
        provider_id = str(uuid.UUID(session_id))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError("provider_session_identity_invalid") from exc
    if (mode not in {"native_resume", "platform_bootstrap", "empty_start"}
        or not isinstance(epoch_id, str) or not epoch_id.startswith("pe_")
        or not conversation_context.get("source_sha256")):
        raise ValueError("provider_session_spec_invalid")
    return {
        "sdk_session_id": provider_id,
        "provider_session_resume_required": mode == "native_resume",
    }


__all__ = ["claude_provider_session_dispatch"]
