from typing import Any, Literal, TypedDict

from app.skills.domain.internal_dependencies import (
    INTERNAL_DEPENDENCY_SKILL_IDS,
    is_internal_dependency_skill,
)
from app.skills.domain.version_labels import (
    next_uploaded_skill_display_version,
    resolve_uploaded_skill_display_versions,
)


class AdminSkillSummaryResponse(TypedDict):
    skill_id: str
    name: str
    description: str
    lifecycle_status: Literal["active"]
    distribution_status: Literal["active", "disabled"]
    visible_to_user: bool
    latest_version: str | None
    latest_version_status: str | None
    current_version: str | None
    rollout_percent: int | None
    latest_display_version: str | None
    current_display_version: str | None
    latest_uploaded_at: str | None


class AdminSkillListResponse(TypedDict):
    items: list[AdminSkillSummaryResponse]


_skill_display_version_persistence: Any | None = None


def configure_skill_display_version_persistence(persistence: Any) -> None:
    global _skill_display_version_persistence
    _skill_display_version_persistence = persistence


def _display_version_persistence() -> Any:
    if _skill_display_version_persistence is None:
        raise RuntimeError("skill_display_version_persistence_not_configured")
    return _skill_display_version_persistence


async def lock_skill_for_version_upload(conn: Any, *, skill_id: str) -> None:
    await _display_version_persistence().lock_skill_for_version_upload(
        conn,
        skill_id=skill_id,
    )


async def list_uploaded_skill_display_version_rows(
    conn: Any,
    *,
    skill_ids: list[str],
) -> list[dict[str, Any]]:
    return await _display_version_persistence().list_uploaded_skill_display_version_rows(
        conn,
        skill_ids=skill_ids,
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
    "AdminSkillListResponse",
    "AdminSkillSummaryResponse",
    "INTERNAL_DEPENDENCY_SKILL_IDS",
    "configure_skill_display_version_persistence",
    "is_internal_dependency_skill",
    "list_uploaded_skill_display_version_rows",
    "lock_skill_for_version_upload",
    "next_uploaded_skill_display_version",
    "resolve_uploaded_skill_display_versions",
    "restore_admitted_skill_manifest_authority",
]
