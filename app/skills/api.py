from typing import Any

from app.skills.domain.internal_dependencies import (
    INTERNAL_DEPENDENCY_SKILL_IDS,
    is_internal_dependency_skill,
)


_ADMITTED_MANIFEST_COLLECTION_FIELDS = (
    "source",
    "files",
    "dependency_ids",
    "mcp_tool_ids",
    "builtin_tool_identities",
    "execution_profile",
    "release_decision",
    "snapshot_governance",
)


def restore_admitted_skill_manifest_authority(
    usage_manifests: list[dict[str, Any]],
    *,
    admitted_manifests: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Restore immutable admission fields on executor-reported Skill usage receipts."""

    admitted_by_skill: dict[str, dict[str, Any]] = {}
    for item in admitted_manifests:
        if not isinstance(item, dict):
            continue
        skill_id = str(item.get("skill_id") or "").strip()
        if skill_id:
            admitted_by_skill[skill_id] = item

    restored: list[dict[str, Any]] = []
    for item in usage_manifests:
        manifest = dict(item)
        skill_id = str(manifest.get("skill_id") or "").strip()
        admitted = admitted_by_skill.get(skill_id)
        if admitted is None:
            continue

        for key in ("version", "skill_version", "content_hash"):
            manifest.pop(key, None)
        admitted_version = str(
            admitted.get("version")
            or admitted.get("skill_version")
            or admitted.get("content_hash")
            or ""
        ).strip()
        admitted_hash = str(admitted.get("content_hash") or admitted_version).strip()
        if admitted_version:
            manifest["version"] = admitted_version
            manifest["skill_version"] = admitted_version
        if admitted_hash:
            manifest["content_hash"] = admitted_hash

        for field in _ADMITTED_MANIFEST_COLLECTION_FIELDS:
            admitted_value = admitted.get(field)
            if isinstance(admitted_value, (dict, list)):
                manifest[field] = admitted_value
            else:
                manifest.pop(field, None)
        lifecycle_status = admitted.get("lifecycle_status")
        if isinstance(lifecycle_status, str):
            manifest["lifecycle_status"] = lifecycle_status
        else:
            manifest.pop("lifecycle_status", None)
        restored.append(manifest)
    return restored


__all__ = [
    "INTERNAL_DEPENDENCY_SKILL_IDS",
    "is_internal_dependency_skill",
    "restore_admitted_skill_manifest_authority",
]
