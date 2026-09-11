from dataclasses import dataclass
from typing import Any, Literal, TypedDict

from app.agent_apps.application.skill_set_pinning import pin_agent_skill_set
from app.agent_apps.domain.profile_definition import (
    normalize_agent_avatar_seed,
    normalize_agent_profile_display_items,
    normalize_agent_skill_reference,
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


class AgentProfileSkillReference(TypedDict):
    """A Skill selected by stable public name."""

    skill_id: str


@dataclass(init=False)
class AgentProfileAdminProjection:
    """Framework-neutral admin projection."""

    def __init__(self, values: dict[str, Any] | None = None, **kwargs: Any) -> None:
        self.__dict__.update(values or {})
        self.__dict__.update(kwargs)

    def model_dump(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return dict(self.__dict__)


class AgentProfilePublicProjection(TypedDict):
    """Safe ordinary-user market projection owned by the Agent Apps context."""

    agent_id: str
    expected_revision: int
    name: str
    description: str
    starter_prompts: list[str]
    avatar_ref: AgentProfileAvatarRef
    avatar_seed: str
    market_tags: list[str]
    published_at: Any | None
    completed_tasks: int
    is_favorite: bool


def normalize_agent_skill_set(skill_set):
    normalized_skill_set = [normalize_agent_skill_reference(skill) for skill in skill_set]
    return _normalize_agent_skill_set(
        normalized_skill_set,
        is_internal_dependency_skill,
    )

def safe_agent_avatar_ref(value: object, *, fallback: str = "builtin:agent") -> str:
    candidate = value.strip() if isinstance(value, str) else ""
    return candidate if candidate in AGENT_PROFILE_AVATAR_REFS else fallback


__all__ = [
    "AGENT_PROFILE_AVATAR_REFS",
    "AgentProfileAdminProjection",
    "AgentProfileAvatarRef",
    "AgentProfilePublicProjection",
    "AgentProfileSkillReference",
    "normalize_agent_avatar_seed",
    "normalize_agent_profile_display_items",
    "normalize_agent_skill_reference",
    "normalize_agent_skill_set",
    "pin_agent_skill_set",
    "safe_agent_avatar_ref",
    "safe_agent_avatar_seed",
]
