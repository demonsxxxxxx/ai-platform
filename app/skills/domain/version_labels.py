from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

_DISPLAY_VERSION_PATTERN = re.compile(r"^1\.0\.(\d+)$")


def resolve_uploaded_skill_display_versions(
    rows: Iterable[Mapping[str, Any]],
) -> dict[tuple[str, str], str]:
    """Assign stable 1.0.patch labels to uploaded versions in creation order."""

    resolved: dict[tuple[str, str], str] = {}
    used_patches: dict[str, set[int]] = {}
    next_patches: dict[str, int] = {}
    for row in rows:
        skill_id = str(row.get("skill_id") or "")
        version = str(row.get("version") or "")
        if not skill_id or not version:
            continue
        used = used_patches.setdefault(skill_id, set())
        match = _DISPLAY_VERSION_PATTERN.fullmatch(
            str(row.get("display_version") or "")
        )
        patch = int(match.group(1)) if match else next_patches.get(skill_id, 0)
        while patch in used:
            patch += 1
        used.add(patch)
        next_patches[skill_id] = max(next_patches.get(skill_id, 0), patch + 1)
        resolved[(skill_id, version)] = f"1.0.{patch}"
    return resolved


def next_uploaded_skill_display_version(
    skill_id: str,
    rows: Iterable[Mapping[str, Any]],
) -> str:
    resolved = resolve_uploaded_skill_display_versions(rows)
    patches = [
        int(label.rsplit(".", 1)[1])
        for (row_skill_id, _version), label in resolved.items()
        if row_skill_id == skill_id
    ]
    return f"1.0.{max(patches, default=-1) + 1}"
