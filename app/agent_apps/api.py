from app.agent_apps.application.skill_set_pinning import pin_agent_skill_set
from app.agent_apps.domain.profile_definition import (
    normalize_agent_avatar_seed,
    normalize_agent_profile_display_items,
    normalize_agent_skill_set as _normalize_agent_skill_set,
)
from app.skills.api import is_internal_dependency_skill


def normalize_agent_skill_set(skill_set, selected_skill):
    return _normalize_agent_skill_set(
        skill_set,
        selected_skill,
        is_internal_dependency_skill,
    )

__all__ = [
    "normalize_agent_avatar_seed",
    "normalize_agent_profile_display_items",
    "normalize_agent_skill_set",
    "pin_agent_skill_set",
]
