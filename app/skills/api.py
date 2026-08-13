from __future__ import annotations

from typing import Any

from app.skills.dependencies import INTERNAL_DEPENDENCY_SKILL_IDS
from app.skills.infrastructure.postgres import validate_replay_skill_manifests as _validate_replay


def is_internal_dependency_skill(skill_id: str) -> bool:
    return skill_id in INTERNAL_DEPENDENCY_SKILL_IDS


async def validate_replay_skill_manifests(
    conn: Any,
    *,
    skill_id: str,
    pinned_version: str,
    pinned_executor_type: str,
    skill_manifests: list[dict[str, Any]],
    skill_set: list[dict[str, Any]] | None = None,
) -> list[str]:
    return await _validate_replay(
        conn,
        skill_id=skill_id,
        pinned_version=pinned_version,
        pinned_executor_type=pinned_executor_type,
        skill_manifests=skill_manifests,
        skill_set=skill_set,
    )


__all__ = ["is_internal_dependency_skill", "validate_replay_skill_manifests"]
