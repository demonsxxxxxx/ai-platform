from dataclasses import dataclass
from typing import Any, Callable, Literal, TypedDict

from app.agent_apps.application.skill_set_pinning import pin_agent_skill_set
from app.agent_apps.domain.profile_definition import (
    normalize_agent_avatar_seed,
    normalize_agent_profile_display_items,
    normalize_agent_skill_reference,
    normalize_agent_skill_set as _normalize_agent_skill_set,
    safe_agent_avatar_seed,
)
from app.skills.api import is_internal_dependency_skill


class _ConfiguredProxy:
    def __init__(self, name: str) -> None:
        self._name = name
        self._target: Any | None = None

    def configure(self, target: Any) -> None:
        self._target = target

    def __getattr__(self, name: str) -> Callable[..., Any]:
        def invoke(*args: Any, **kwargs: Any) -> Any:
            if self._target is None:
                raise RuntimeError(f"{self._name}_not_configured")
            return getattr(self._target, name)(*args, **kwargs)

        return invoke


agent_profile_repository = _ConfiguredProxy("agent_profile_repository")
_authority_factory: Callable[..., Any] | None = None


def configure_agent_profile_persistence(repository: Any) -> None:
    agent_profile_repository.configure(repository)


def configure_agent_profile_authority(factory: Callable[..., Any]) -> None:
    global _authority_factory
    _authority_factory = factory


class AgentProfileAuthority:
    """Public context proxy; bootstrap supplies the concrete authority."""

    def __init__(self, **kwargs: Any) -> None:
        self._authority_kwargs = kwargs
        self._authority: Any | None = None

    def __getattr__(self, name: str) -> Callable[..., Any]:
        def invoke(*args: Any, **kwargs: Any) -> Any:
            if self._authority is None:
                if _authority_factory is None:
                    raise RuntimeError("agent_profile_authority_not_configured")
                self._authority = _authority_factory(**self._authority_kwargs)
            return getattr(self._authority, name)(*args, **kwargs)

        return invoke


async def reauthorize_bound_profile_for_worker_dispatch(conn, **kwargs: Any) -> Any:
    return await AgentProfileAuthority().resolve_bound_for_worker_dispatch(conn, **kwargs)



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
    "AgentProfileAuthority",
    "AgentProfileAvatarRef",
    "AgentProfilePublicProjection",
    "AgentProfileSkillReference",
    "agent_profile_repository",
    "configure_agent_profile_authority",
    "configure_agent_profile_persistence",
    "normalize_agent_avatar_seed",
    "normalize_agent_profile_display_items",
    "normalize_agent_skill_reference",
    "normalize_agent_skill_set",
    "pin_agent_skill_set",
    "reauthorize_bound_profile_for_worker_dispatch",
    "safe_agent_avatar_ref",
    "safe_agent_avatar_seed",
]
