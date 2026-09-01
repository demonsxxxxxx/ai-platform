from typing import Literal

from app.agent_apps.application.skill_set_pinning import pin_agent_skill_set
from app.agent_apps.domain.profile_definition import (
    discard_legacy_agent_profile_model_id,
    normalize_agent_avatar_seed,
    normalize_agent_profile_display_items,
    normalize_agent_skill_set as _normalize_agent_skill_set,
    safe_agent_avatar_seed,
)
from app.skills.api import is_internal_dependency_skill


AgentProfileAvatarRef = Literal[
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
]
AGENT_PROFILE_AVATAR_REFS = frozenset(AgentProfileAvatarRef.__args__)


def normalize_market_tag(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("market_tag_invalid")
    normalized = value.strip()
    if not normalized:
        return ""
    return normalize_agent_profile_display_items([normalized], "market_tag", item_limit=80)[0]


def safe_agent_avatar_ref(value: object, *, fallback: str = "builtin:agent") -> str:
    candidate = value.strip() if isinstance(value, str) else ""
    return candidate if candidate in AGENT_PROFILE_AVATAR_REFS else fallback


def normalize_agent_skill_set(skill_set, selected_skill):
    return _normalize_agent_skill_set(
        skill_set,
        selected_skill,
        is_internal_dependency_skill,
    )

__all__ = [
    "AGENT_PROFILE_AVATAR_REFS",
    "AgentProfileAvatarRef",
    "discard_legacy_agent_profile_model_id",
    "normalize_agent_avatar_seed",
    "normalize_agent_profile_display_items",
    "normalize_agent_skill_set",
    "normalize_market_tag",
    "pin_agent_skill_set",
    "safe_agent_avatar_ref",
    "safe_agent_avatar_seed",
]
