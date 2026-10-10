from __future__ import annotations

from collections.abc import Sequence
from typing import Any


def select_execution_skill_names(
    *,
    selected_skill_id: str | None,
    requested_skill_ids: list[str],
    available_skill_ids: list[str],
    pinned_manifests: dict[str, dict[str, Any]],
    authorized_skill_ids: Sequence[str] | None = None,
) -> list[str]:
    available = set(available_skill_ids)
    if authorized_skill_ids is not None:
        return [skill_id for skill_id in authorized_skill_ids if skill_id in available]

    requested = list(requested_skill_ids)
    if selected_skill_id and selected_skill_id in available:
        requested.insert(0, selected_skill_id)
    if not requested:
        return []
    selected = list(dict.fromkeys(name for name in requested if name in available))
    if not pinned_manifests:
        return selected

    expanded: list[str] = []

    def add_skill(skill_id: str) -> None:
        if skill_id in expanded:
            return
        expanded.append(skill_id)
        manifest = pinned_manifests.get(skill_id)
        if not manifest:
            return
        dependencies = manifest.get("dependency_ids")
        for dependency_id in dependencies if isinstance(dependencies, list) else []:
            add_skill(str(dependency_id))

    for skill_id in selected:
        add_skill(skill_id)
    return expanded
