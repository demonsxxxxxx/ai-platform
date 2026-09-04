from typing import Any, Literal, NotRequired, TypedDict

from pydantic import BaseModel, ConfigDict, field_validator

from app.agent_apps.application.skill_set_pinning import pin_agent_skill_set
from app.agent_apps.domain.profile_definition import (
    discard_legacy_agent_profile_model_id,
    normalize_agent_avatar_seed,
    normalize_agent_profile_display_items,
    normalize_agent_skill_set as _normalize_agent_skill_set,
    safe_agent_avatar_seed,
)
from app.skills.api import is_internal_dependency_skill
from app.validation import assert_safe_id


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


class AgentProfileSkillReference(BaseModel):
    """A profile Skill name, with an optional legacy version for old revisions."""

    model_config = ConfigDict(extra="forbid")

    skill_id: str
    expected_version: str | None = None

    @field_validator("skill_id")
    @classmethod
    def validate_skill_name(cls, value: str) -> str:
        return assert_safe_id(value, "skill_id")

    @field_validator("expected_version")
    @classmethod
    def validate_legacy_version(cls, value: str | None) -> str | None:
        return assert_safe_id(value, "expected_version") if value is not None else None


class AgentProfilePublicProjection(TypedDict):
    """Safe ordinary-user market projection owned by the Agent Apps context."""

    agent_id: str
    expected_revision: int
    name: str
    description: str
    welcome_message: str
    starter_prompts: list[str]
    capability_summary: str
    recommended_tasks: list[str]
    supported_input_types: list[Literal["text", "file"]]
    expected_outputs: list[str]
    permissions_and_data_access_notice: str
    avatar_ref: AgentProfileAvatarRef
    avatar_seed: str
    category: Literal["general", "support", "writing", "research", "operations"]
    market_tag: str
    published_at: Any | None
    completed_tasks: NotRequired[int]
    is_favorite: NotRequired[bool]


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
    "AgentProfilePublicProjection",
    "AgentProfileSkillReference",
    "discard_legacy_agent_profile_model_id",
    "normalize_agent_avatar_seed",
    "normalize_agent_profile_display_items",
    "normalize_agent_skill_set",
    "normalize_market_tag",
    "pin_agent_skill_set",
    "safe_agent_avatar_ref",
    "safe_agent_avatar_seed",
]
