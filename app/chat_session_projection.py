from typing import Any

from app.models import AgentConversationIdentity, ChatSessionResponse
from app.projection_redaction import public_agent_id_for_projection


def session_response(row: dict[str, Any]) -> ChatSessionResponse:
    """Project one authorized Session without leaking private Agent configuration."""

    raw_agent_id = str(row["agent_id"])
    profile_revision = row.get("admitted_agent_profile_revision")
    profile_name = row.get("agent_profile_name")
    agent_conversation = None
    if (
        isinstance(profile_revision, int)
        and profile_revision > 0
        and isinstance(profile_name, str)
        and profile_name
    ):
        avatar_ref = str(row.get("agent_profile_avatar_ref") or "")
        category = str(row.get("agent_profile_category") or "")
        agent_conversation = AgentConversationIdentity(
            agent_id=raw_agent_id,
            revision=profile_revision,
            name=profile_name,
            description=str(row.get("agent_profile_description") or ""),
            avatar_ref=(
                avatar_ref
                if avatar_ref
                in {
                    "builtin:agent",
                    "builtin:assistant",
                    "builtin:document",
                    "builtin:research",
                }
                else "builtin:agent"
            ),
            category=(
                category
                if category
                in {"general", "support", "writing", "research", "operations"}
                else "general"
            ),
        )
    return ChatSessionResponse(
        session_id=str(row["id"]),
        workspace_id=str(row["workspace_id"]),
        agent_id=public_agent_id_for_projection(raw_agent_id) or raw_agent_id,
        title=str(row.get("title") or ""),
        agent_conversation=agent_conversation,
        created_at=row.get("created_at"),
        updated_at=row.get("updated_at"),
    )
