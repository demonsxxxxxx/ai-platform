"""Execution evidence for admitted Skill snapshots."""

from collections.abc import Callable
from typing import Any

from app.skills.api import restore_admitted_skill_manifest_authority

InvokedSkillIds = Callable[[dict[str, Any]], set[str]]


def native_used_skills_from_result(result: Any, *, invoked_skill_ids: InvokedSkillIds) -> list[str]:
    semantic_evidence = {**result.result, **result.executor_payload}
    exact_used = invoked_skill_ids(semantic_evidence)
    raw = semantic_evidence.get("used_skills")
    if not isinstance(raw, list):
        return []
    used: list[str] = []
    for item in raw:
        skill_name = str(item).strip()
        if skill_name in exact_used and skill_name not in used:
            used.append(skill_name)
    return used


def skill_snapshot_from_result(result: Any, *, invoked_skill_ids: InvokedSkillIds) -> dict[str, list[str]]:
    source = {**result.executor_payload, **result.result}
    snapshot: dict[str, list[str]] = {
        "allowed_skills": [],
        "staged_skills": [],
        "used_skills": [],
    }
    for key in ("allowed_skills", "staged_skills"):
        value = source.get(key)
        if isinstance(value, list):
            snapshot[key] = [str(item) for item in value]
    snapshot["used_skills"] = native_used_skills_from_result(result, invoked_skill_ids=invoked_skill_ids)
    return snapshot


def skill_manifests_from_result(result: Any, *, invoked_skill_ids: InvokedSkillIds) -> list[dict[str, Any]]:
    source = {**result.executor_payload, **result.result}
    raw = source.get("skill_manifests")
    if not isinstance(raw, list):
        return []
    used_skills = set(native_used_skills_from_result(result, invoked_skill_ids=invoked_skill_ids))
    used_skills_source = str(result.executor_payload.get("used_skills_source") or "").strip()
    manifests: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        manifest = dict(item)
        skill_id = str(manifest.get("skill_id") or "").strip()
        manifest["used"] = bool(skill_id and skill_id in used_skills)
        if manifest["used"]:
            manifest["used_skills_source"] = used_skills_source
        manifests.append(manifest)
    return manifests


def skill_manifests_for_persistence(
    result: Any,
    admitted_manifests: list[dict[str, Any]],
    *,
    invoked_skill_ids: InvokedSkillIds,
) -> list[dict[str, Any]]:
    return restore_admitted_skill_manifest_authority(
        skill_manifests_from_result(result, invoked_skill_ids=invoked_skill_ids),
        admitted_manifests=admitted_manifests,
    )
