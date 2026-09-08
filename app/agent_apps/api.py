from dataclasses import dataclass
from typing import Any, Literal, NotRequired, TypedDict

from app.agent_apps.application.skill_set_pinning import pin_agent_skill_set
from app.agent_apps.domain.profile_definition import (
    discard_legacy_agent_profile_model_id,
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
    """A profile Skill name, with an optional legacy version for old revisions."""

    skill_id: str
    expected_version: NotRequired[str | None]


@dataclass
class AgentProfileDraftDefinition:
    """Framework-neutral draft wrapper shared by transport and lifecycle code."""

    legacy: Any
    market_tags: list[str]
    explicit_fields: frozenset[str]

    @classmethod
    def from_legacy(
        cls,
        legacy: Any,
        *,
        market_tags: object = (),
        explicit_fields: set[str] | frozenset[str] = frozenset(),
    ) -> "AgentProfileDraftDefinition":
        tags = normalize_market_tags(market_tags)
        if not tags:
            tags = normalize_market_tags(getattr(legacy, "market_tag", ""))
        fields = set(getattr(legacy, "model_fields_set", ()))
        fields.update(explicit_fields)
        fields.discard("model_id")
        return cls(legacy=legacy, market_tags=tags, explicit_fields=frozenset(fields))

    @property
    def model_fields_set(self) -> set[str]:
        return set(self.explicit_fields)

    @property
    def market_tag(self) -> str:
        return self.market_tags[0] if self.market_tags else ""

    def model_copy(self, *, update: dict[str, Any] | None = None) -> "AgentProfileDraftDefinition":
        updates = dict(update or {})
        if "market_tags" in updates:
            tags = normalize_market_tags(updates.pop("market_tags"))
            updates["market_tag"] = tags[0] if tags else ""
        elif "market_tag" in updates:
            tags = normalize_market_tags(updates["market_tag"])
        else:
            tags = list(self.market_tags)
        legacy = self.legacy.model_copy(update=updates)
        return self.from_legacy(
            legacy,
            market_tags=tags,
            explicit_fields=set(self.explicit_fields) | set(update or {}),
        )

    def model_dump(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        dumped = dict(self.legacy.model_dump(*args, **kwargs))
        dumped["market_tags"] = list(self.market_tags)
        dumped["market_tag"] = self.market_tags[0] if self.market_tags else ""
        return dumped

    def __getattr__(self, name: str) -> Any:
        return getattr(self.legacy, name)


@dataclass(init=False)
class AgentProfileAdminProjection:
    """Framework-neutral admin projection with the legacy serialization surface."""

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
    market_tags: list[str]
    published_at: Any | None
    completed_tasks: NotRequired[int]
    is_favorite: NotRequired[bool]


def normalize_market_tags(value: object) -> list[str]:
    if isinstance(value, str):
        normalized = value.strip()
        return (
            []
            if not normalized
            else normalize_agent_profile_display_items([normalized], "market_tag", item_limit=80)
        )
    if not isinstance(value, list):
        raise ValueError("market_tags_invalid")
    return normalize_agent_profile_display_items(value, "market_tags", item_limit=80)


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
    normalized_skill_set = [normalize_agent_skill_reference(skill) for skill in skill_set]
    normalized_selected_skill = (
        normalize_agent_skill_reference(selected_skill)
        if selected_skill is not None
        else None
    )
    return _normalize_agent_skill_set(
        normalized_skill_set,
        normalized_selected_skill,
        is_internal_dependency_skill,
    )

__all__ = [
    "AGENT_PROFILE_AVATAR_REFS",
    "AgentProfileAdminProjection",
    "AgentProfileAvatarRef",
    "AgentProfileDraftDefinition",
    "AgentProfilePublicProjection",
    "AgentProfileSkillReference",
    "discard_legacy_agent_profile_model_id",
    "normalize_agent_avatar_seed",
    "normalize_agent_profile_display_items",
    "normalize_agent_skill_reference",
    "normalize_agent_skill_set",
    "normalize_market_tag",
    "normalize_market_tags",
    "pin_agent_skill_set",
    "safe_agent_avatar_ref",
    "safe_agent_avatar_seed",
]
