from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, TypeVar


class SkillSelection(Protocol):
    skill_id: str


SelectionT = TypeVar("SelectionT", bound=SkillSelection)


def normalize_agent_skill_set(
    skill_set: Sequence[SelectionT],
    selected_skill: SelectionT | None,
) -> tuple[list[SelectionT], SelectionT]:
    skills = list(skill_set) or ([selected_skill] if selected_skill is not None else [])
    if not skills:
        raise ValueError("skill_set must contain at least one Skill")
    skill_ids = [skill.skill_id for skill in skills]
    if len(skill_ids) != len(set(skill_ids)):
        raise ValueError("skill_set contains duplicate skill_id values")
    if "general-chat" in skill_ids and skill_ids != ["general-chat"]:
        raise ValueError("general-chat cannot be combined with executable Skills")
    if selected_skill is not None and selected_skill != skills[0]:
        raise ValueError("selected_skill must match the first skill_set item")
    return skills, skills[0]


def normalize_agent_avatar_seed(value: str) -> str:
    normalized = value.strip()
    if "\x00" in normalized or any(ord(character) < 32 for character in normalized):
        raise ValueError("avatar_seed contains control characters")
    return normalized
