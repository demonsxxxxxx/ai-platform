"""Persist admitted Skill usage receipts during the Run terminal transaction."""

from collections.abc import Callable
from typing import Any


async def persist_worker_skill_snapshots(
    conn: Any,
    manifests: list[dict[str, Any]],
    *,
    tenant_id: str,
    run_id: str,
    release_decision: dict[str, Any] | None,
    source_json: Callable[..., dict[str, Any]],
    upsert_snapshot: Callable[..., Any],
) -> None:
    for item in manifests:
        skill_id = str(item.get("skill_id") or "").strip()
        if not skill_id:
            continue
        raw_dependencies = item.get("dependency_ids")
        dependency_ids = (
            [str(value) for value in raw_dependencies]
            if isinstance(raw_dependencies, list)
            else []
        )
        await upsert_snapshot(
            conn,
            tenant_id=tenant_id,
            run_id=run_id,
            skill_id=skill_id,
            skill_version=str(item.get("version") or item.get("skill_version") or ""),
            content_hash=str(item.get("content_hash") or item.get("version") or ""),
            source_json=source_json(item, release_decision=release_decision),
            dependency_ids=dependency_ids,
            allowed=bool(item.get("allowed")),
            staged=bool(item.get("staged")),
            used=bool(item.get("used")),
            used_skills_source=str(item.get("used_skills_source") or "").strip(),
        )
