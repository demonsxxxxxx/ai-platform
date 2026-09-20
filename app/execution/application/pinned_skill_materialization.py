from __future__ import annotations

from pathlib import Path

from app.skills.api import skill_snapshot_components_fit


class PinnedSkillMismatch(ValueError):
    def __init__(self, message: str, *, actual_content_hash: str = "") -> None:
        super().__init__(message)
        self.actual_content_hash = actual_content_hash


def validate_pinned_skill_relative_path(relative_path: str, *, skill_name: str) -> None:
    path = Path(relative_path)
    if (
        not relative_path
        or path.is_absolute()
        or ".." in path.parts
        or not skill_snapshot_components_fit(path.parts)
    ):
        raise ValueError(f"invalid pinned skill file path: {skill_name}")
