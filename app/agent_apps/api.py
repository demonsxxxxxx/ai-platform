from app.agent_apps.infrastructure import postgres as agent_profile_persistence
from app.agent_apps.application.skill_set_pinning import pin_agent_skill_set
from app.agent_apps.domain.profile_definition import (
    discard_legacy_agent_profile_model_id,
    normalize_agent_avatar_seed,
    normalize_agent_profile_display_items,
    normalize_agent_skill_set as _normalize_agent_skill_set,
    safe_agent_avatar_seed,
)
from app.skills.api import is_internal_dependency_skill


def normalize_market_tag(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("market_tag_invalid")
    normalized = value.strip()
    if not normalized:
        return ""
    return normalize_agent_profile_display_items([normalized], "market_tag", item_limit=80)[0]


async def list_agent_profile_favorite_ids(conn, *, tenant_id: str, user_id: str) -> set[str]:
    return await agent_profile_persistence.list_agent_profile_favorite_ids(
        conn, tenant_id=tenant_id, user_id=user_id
    )


async def set_agent_profile_favorite(
    conn, *, tenant_id: str, user_id: str, agent_id: str, favorite: bool
) -> None:
    await agent_profile_persistence.set_agent_profile_favorite(
        conn,
        tenant_id=tenant_id,
        user_id=user_id,
        agent_id=agent_id,
        favorite=favorite,
    )


def normalize_agent_skill_set(skill_set, selected_skill):
    return _normalize_agent_skill_set(
        skill_set,
        selected_skill,
        is_internal_dependency_skill,
    )

__all__ = [
    "discard_legacy_agent_profile_model_id",
    "list_agent_profile_favorite_ids",
    "normalize_agent_avatar_seed",
    "normalize_agent_profile_display_items",
    "normalize_agent_skill_set",
    "normalize_market_tag",
    "pin_agent_skill_set",
    "safe_agent_avatar_seed",
    "set_agent_profile_favorite",
]
