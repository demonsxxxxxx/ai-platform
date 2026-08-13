from __future__ import annotations


INTERNAL_DEPENDENCY_SKILL_IDS = frozenset(
    {
        "minimax-docx",
        "reference-fact-extraction",
    }
)


def is_internal_dependency_skill(skill_id: str) -> bool:
    return skill_id in INTERNAL_DEPENDENCY_SKILL_IDS
