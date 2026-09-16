from __future__ import annotations

from collections.abc import Iterable


def skill_snapshot_components_fit(parts: Iterable[str]) -> bool:
    try:
        return all(len(part.encode("utf-8")) <= 255 for part in parts)
    except UnicodeEncodeError:
        return False
