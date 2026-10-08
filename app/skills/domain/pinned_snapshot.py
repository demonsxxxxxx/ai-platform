"""Validation contract for the immutable Skill snapshot supplied to a run."""

from pathlib import Path
from typing import Any

from app.skills.domain.snapshot_paths import skill_snapshot_components_fit


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


def skill_manifests_from_catalog(
    manifests: list[dict[str, Any]], *, used_skill_names: list[str]
) -> list[dict[str, Any]]:
    used = set(used_skill_names)
    return [
        {**dict(manifest), "used": str(manifest.get("skill_id") or "") in used}
        for manifest in manifests
    ]


def staged_skill_manifests(
    selected_skills: list[Any],
    *,
    used_skill_names: list[str],
    pins: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    used = set(used_skill_names)
    staged = {skill.name for skill in selected_skills}
    pinned_manifests = dict(pins or {})
    manifests: list[dict[str, Any]] = []
    for skill in selected_skills:
        pin = pinned_manifests.get(skill.name)
        raw_dependencies = pin.get("dependency_ids") if pin is not None else None
        dependency_ids = (
            [str(item) for item in raw_dependencies if str(item) in staged]
            if isinstance(raw_dependencies, list)
            else []
        )
        manifests.append({
            "skill_id": skill.name,
            "description": skill.description,
            "version": skill.version,
            "content_hash": skill.version,
            "source": skill.source,
            "dependency_ids": dependency_ids,
            "allowed": True,
            "staged": True,
            "used": skill.name in used,
        })
    return manifests


def pin_manifests_for_result(
    pins: dict[str, dict[str, Any]], allowed_skill_names: list[str]
) -> list[dict[str, Any]]:
    manifests: list[dict[str, Any]] = []
    for skill_name in allowed_skill_names:
        pin = pins.get(skill_name)
        if not pin:
            continue
        manifest = {key: value for key, value in pin.items() if key != "files"}
        version = str(manifest.get("version") or pin.get("content_hash") or "")
        content_hash = str(manifest.get("content_hash") or pin.get("version") or version)
        manifest["version"] = version
        manifest["content_hash"] = content_hash
        manifest.setdefault("dependency_ids", [])
        manifest["allowed"] = bool(manifest.get("allowed", True))
        manifest["staged"] = False
        manifest["used"] = False
        manifests.append(manifest)
    return manifests
