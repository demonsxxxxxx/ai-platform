from __future__ import annotations

from collections.abc import Callable, Sequence
import re
from typing import Protocol, TypeVar


class SkillSelection(Protocol):
    skill_id: str


SelectionT = TypeVar("SelectionT", bound=SkillSelection)
_SAFE_SKILL_REFERENCE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


def normalize_agent_skill_reference(value: object) -> dict[str, str]:
    if not isinstance(value, dict) or set(value) - {"skill_id", "expected_version"}:
        raise ValueError("agent_profile_skill_reference_invalid")
    skill_id = value.get("skill_id")
    if not isinstance(skill_id, str) or _SAFE_SKILL_REFERENCE.fullmatch(skill_id) is None:
        raise ValueError("skill_id_invalid")
    normalized = {"skill_id": skill_id}
    expected_version = value.get("expected_version")
    if expected_version is not None:
        if not isinstance(expected_version, str) or _SAFE_SKILL_REFERENCE.fullmatch(expected_version) is None:
            raise ValueError("expected_version_invalid")
        normalized["expected_version"] = expected_version
    return normalized


def _skill_id(selection: SelectionT) -> str:
    return selection["skill_id"] if isinstance(selection, dict) else selection.skill_id


def normalize_agent_skill_set(
    skill_set: Sequence[SelectionT],
    selected_skill: SelectionT | None,
    is_internal_dependency: Callable[[str], bool],
) -> tuple[list[SelectionT], SelectionT]:
    skills = list(skill_set) or ([selected_skill] if selected_skill is not None else [])
    if not skills:
        raise ValueError("skill_set must contain at least one Skill")
    skill_ids = [_skill_id(skill) for skill in skills]
    if len(skill_ids) != len(set(skill_ids)):
        raise ValueError("skill_set contains duplicate skill_id values")
    if any(is_internal_dependency(skill_id) for skill_id in skill_ids):
        raise ValueError("skill_set cannot contain internal dependency Skills")
    if "general-chat" in skill_ids and skill_ids != ["general-chat"]:
        raise ValueError("general-chat cannot be combined with executable Skills")
    if selected_skill is not None and selected_skill != skills[0]:
        raise ValueError("selected_skill must match the first skill_set item")
    return skills, skills[0]


def normalize_agent_avatar_seed(value: str) -> str:
    if any(ord(character) < 32 for character in value):
        raise ValueError("avatar_seed contains control characters")
    normalized = value.strip()
    return normalized


def safe_agent_avatar_seed(value: object, *, fallback: str) -> str:
    if not isinstance(value, str):
        return fallback
    try:
        candidate = normalize_agent_avatar_seed(value)
    except ValueError:
        return fallback
    if not candidate or len(candidate) > 128:
        return fallback
    return candidate


def discard_legacy_agent_profile_model_id(value: object) -> object:
    if not isinstance(value, dict) or "model_id" not in value:
        return value
    normalized = dict(value)
    normalized.pop("model_id")
    return normalized


def normalize_agent_profile_display_items(
    values: Sequence[str],
    field_name: str,
    *,
    item_limit: int,
) -> list[str]:
    normalized: list[str] = []
    for value in values:
        candidate = value.strip()
        if not candidate:
            raise ValueError(f"{field_name} contains an empty item")
        if len(candidate) > item_limit:
            raise ValueError(f"{field_name} item exceeds {item_limit} characters")
        if any(ord(character) < 32 for character in candidate):
            raise ValueError(f"{field_name} contains control characters")
        if candidate in normalized:
            raise ValueError(f"{field_name} contains duplicates")
        normalized.append(candidate)
    return normalized
