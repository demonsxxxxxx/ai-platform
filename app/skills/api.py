from typing import Any, Literal, TypedDict

from app.skills.domain.internal_dependencies import (
    INTERNAL_DEPENDENCY_SKILL_IDS,
    is_internal_dependency_skill,
)
from app.skills.domain.snapshot_paths import (
    skill_snapshot_components_fit as skill_snapshot_components_fit,
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


async def materialize_skill_manifest_pins(
    conn: Any,
    *,
    skill_id: str,
    input_payload: dict[str, Any],
    release_policy_version: object | None,
    get_skill: Any,
    get_effective_skill_version: Any,
    is_user_runnable_status: Any,
    build_skill_version_policy_manifest_pins: Any,
    materialization_error: Any,
) -> list[dict[str, Any]]:
    """Materialize only database-backed immutable Skill manifests for a run."""

    def available_skill_ids(
        root_skill_id: str,
        skill_version: dict[str, Any],
        requested_ids: set[str] | None = None,
    ) -> set[str]:
        available = set(requested_ids or ()) | {root_skill_id}
        dependency_ids = skill_version.get("dependency_ids")
        if isinstance(dependency_ids, list):
            available.update(item for item in dependency_ids if isinstance(item, str))
        source = skill_version.get("source")
        dependency_manifests = source.get("dependency_manifests") if isinstance(source, dict) else None
        if isinstance(dependency_manifests, list):
            available.update(
                str(item.get("skill_id"))
                for item in dependency_manifests
                if isinstance(item, dict) and item.get("skill_id")
            )
        return available

    policy_version = str(release_policy_version or "")
    if policy_version:
        version = await get_effective_skill_version(
            conn,
            skill_id=skill_id,
            version=policy_version,
        )
        if version is None or not is_user_runnable_status(version.get("status")):
            raise materialization_error("skill_version_not_materializable")
        return build_skill_version_policy_manifest_pins(
            version,
            available_skill_ids=available_skill_ids(skill_id, version),
        )

    requested_ids = [skill_id]
    raw_skill_ids = input_payload.get("skill_ids")
    if isinstance(raw_skill_ids, list):
        requested_ids.extend(item for item in raw_skill_ids if isinstance(item, str))
    requested_ids = list(dict.fromkeys(item for item in requested_ids if item))
    requested_id_set = set(requested_ids)
    manifests_by_id: dict[str, dict[str, Any]] = {}
    for requested_id in requested_ids:
        skill = await get_skill(conn, skill_id=requested_id)
        version = str((skill or {}).get("version") or "")
        if not version:
            raise materialization_error("skill_version_not_materializable")
        skill_version = await get_effective_skill_version(
            conn,
            skill_id=requested_id,
            version=version,
        )
        if skill_version is None or not is_user_runnable_status(skill_version.get("status")):
            raise materialization_error("skill_version_not_materializable")
        for manifest in build_skill_version_policy_manifest_pins(
            skill_version,
            available_skill_ids=available_skill_ids(
                requested_id,
                skill_version,
                requested_id_set,
            ),
        ):
            manifest_id = str(manifest.get("skill_id") or "")
            existing = manifests_by_id.get(manifest_id)
            if existing is not None and existing.get("content_hash") != manifest.get("content_hash"):
                raise materialization_error("skill_version_not_materializable")
            manifests_by_id.setdefault(manifest_id, manifest)
    return list(manifests_by_id.values())


__all__ = [
    "AdminSkillListResponse",
    "AdminSkillSummaryResponse",
    "INTERNAL_DEPENDENCY_SKILL_IDS",
    "configure_skill_display_version_persistence",
    "is_internal_dependency_skill",
    "list_uploaded_skill_display_version_rows",
    "lock_skill_for_version_upload",
    "materialize_skill_manifest_pins",
    "next_uploaded_skill_display_version",
    "resolve_uploaded_skill_display_versions",
    "restore_admitted_skill_manifest_authority",
]
