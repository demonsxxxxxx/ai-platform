from typing import Any

from app.agent_apps.api import safe_agent_avatar_seed
from app.models import AgentConversationIdentity, ChatSessionResponse
from app.projection_redaction import public_agent_id_for_projection


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
    profile_name = row.get("agent_profile_name")
    agent_conversation = None
    if (
        isinstance(profile_revision, int)
        and profile_revision > 0
        and isinstance(profile_name, str)
        and profile_name
    ):
        avatar_ref = str(row.get("agent_profile_avatar_ref") or "")
        avatar_seed = safe_agent_avatar_seed(
            row.get("agent_profile_avatar_seed"),
            fallback=raw_agent_id,
        )
        category = str(row.get("agent_profile_category") or "")
        agent_conversation = AgentConversationIdentity(
            agent_id=raw_agent_id,
            revision=profile_revision,
            name=profile_name,
            description=str(row.get("agent_profile_description") or ""),
            welcome_message=str(row.get("agent_profile_welcome_message") or ""),
            starter_prompts=_safe_strings(row.get("agent_profile_starter_prompts")),
            capability_summary=str(row.get("agent_profile_capability_summary") or ""),
            recommended_tasks=_safe_strings(row.get("agent_profile_recommended_tasks")),
            supported_input_types=["text", "file"],
            expected_outputs=_safe_strings(row.get("agent_profile_expected_outputs")),
            permissions_and_data_access_notice=str(
                row.get("agent_profile_permissions_and_data_access_notice") or ""
            ),
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
            category=(
                category
                if category
                in {"general", "support", "writing", "research", "operations"}
                else "general"
            ),
            published_at=row.get("agent_profile_published_at"),
        )
    return ChatSessionResponse(
        session_id=str(row["id"]),
        workspace_id=str(row["workspace_id"]),
        agent_id=public_agent_id_for_projection(raw_agent_id) or raw_agent_id,
        title=str(row.get("title") or ""),
        purpose=(
            "builder_test"
            if str(row.get("purpose") or "") == "builder_test"
            else "conversation"
        ),
        agent_conversation=agent_conversation,
        created_at=row.get("created_at"),
        updated_at=row.get("updated_at"),
    )
