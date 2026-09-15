from typing import Any

from app.agent_apps.api import safe_agent_avatar_seed
from app.models import AgentConversationIdentity, ChatSessionResponse
from app.projection_redaction import (
    PUBLIC_RETIRED_AGENT_ID,
    PUBLIC_RETIRED_SESSION_TITLE,
    is_retired_agent_for_projection,
    public_agent_id_for_projection,
)


def _safe_strings(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return list(
        dict.fromkeys(
            item.strip()
            for item in value
            if isinstance(item, str) and item.strip()
        )
    )


def session_response(row: dict[str, Any]) -> ChatSessionResponse:
    """Project one authorized Session without leaking private Agent configuration."""

    raw_agent_id = str(row["agent_id"])
    profile_revision = row.get("admitted_agent_profile_revision")
    retired_agent = is_retired_agent_for_projection(
        raw_agent_id,
        row.get("agent_default_skill_id"),
        row.get("agent_profile_has_retired_skill"),
    )
    public_agent_id = public_agent_id_for_projection(
        raw_agent_id,
        row.get("agent_default_skill_id"),
    )
    if retired_agent:
        public_agent_id = PUBLIC_RETIRED_AGENT_ID
    elif public_agent_id is None:
        public_agent_id = raw_agent_id
    profile_name = row.get("agent_profile_name")
    agent_conversation = None
    if (
        isinstance(profile_revision, int)
        and profile_revision > 0
        and isinstance(profile_name, str)
        and profile_name
        and not retired_agent
    ):
        avatar_ref = str(row.get("agent_profile_avatar_ref") or "")
        avatar_seed = safe_agent_avatar_seed(
            row.get("agent_profile_avatar_seed"),
            fallback=raw_agent_id,
        )
        agent_conversation = AgentConversationIdentity(
            agent_id=raw_agent_id,
            revision=profile_revision,
            name=profile_name,
            description=str(row.get("agent_profile_description") or ""),
            starter_prompts=_safe_strings(row.get("agent_profile_starter_prompts")),
            avatar_ref=(
                avatar_ref
                if avatar_ref
                in {
                    "builtin:agent",
                    "builtin:assistant",
                    "builtin:document",
                    "builtin:research",
                    "builtin:cartoon",
                    "builtin:emoji",
                    "builtin:pixel",
                    "builtin:portrait",
                    "builtin:abstract",
                    "builtin:planet",
                    "builtin:clay",
                    "builtin:icon",
                }
                else "builtin:agent"
            ),
            avatar_seed=avatar_seed,
            published_at=row.get("agent_profile_published_at"),
        )
    return ChatSessionResponse(
        session_id=str(row["id"]),
        workspace_id=str(row["workspace_id"]),
        agent_id=public_agent_id,
        title=(
            PUBLIC_RETIRED_SESSION_TITLE
            if retired_agent
            else str(row.get("title") or "")
        ),
        purpose=(
            "builder_test"
            if str(row.get("purpose") or "") == "builder_test"
            else "conversation"
        ),
        agent_conversation=agent_conversation,
        created_at=row.get("created_at"),
        updated_at=row.get("updated_at"),
    )
