from __future__ import annotations

from collections.abc import Iterable


def skill_snapshot_components_fit(parts: Iterable[str]) -> bool:
    try:
        return all(len(part.encode("utf-8")) <= 255 for part in parts)
    except UnicodeEncodeError:
        return False


def is_materializable_skill_snapshot_path(raw_path: str) -> bool:
    """Match manifest-pin eligibility; callers retain their path normalization."""
    relative_path = raw_path.strip("/")
    path_segments = relative_path.split("/")
    return (
        bool(relative_path)
        and not raw_path.startswith("/")
        and ":" not in raw_path
        and all(segment != ".." for segment in path_segments)
        and skill_snapshot_components_fit(path_segments)
    )
