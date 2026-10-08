from collections.abc import Callable, Sequence
from typing import Any, Literal, TypedDict

from app.skills.application.run_admission import (
    MAX_SKILL_RUN_MANIFESTS,
    SkillRunAdmission,
    SkillRunAdmissionService,
)
from app.skills.application.worker_dispatch_authorization import (
    WorkerSkillCandidate as WorkerSkillCandidate,
    WorkerSkillCatalog as WorkerSkillCatalog,
    WorkerSkillDispatchAuthorization as WorkerSkillDispatchAuthorization,
)
from app.skills.application.worker_dispatch_catalog import (
    materialize_worker_locked_skill_snapshots as materialize_worker_locked_skill_snapshots,
    merged_worker_pinned_manifests as _merged_worker_pinned_manifests,
    resolve_worker_runtime_catalog as _resolve_worker_runtime_catalog,
    worker_catalog_public_metadata as worker_catalog_public_metadata,
    worker_pinned_manifests as worker_pinned_manifests,
    worker_catalog_binding as worker_catalog_binding,
    worker_payload_with_authorized_catalog as worker_payload_with_authorized_catalog,
)
from app.skills.application.pinned_snapshot_stage import (
    select_pinned_skill_snapshots as select_pinned_skill_snapshots,
    stage_pinned_skill_snapshot as stage_pinned_skill_snapshot,
)
from app.skills.registry import BuiltinSkill as BuiltinSkill
from app.skills.stager import SkillStager as SkillStager
from app.skills.domain.catalog_error import (
    AuthorizedSkillCatalogError as AuthorizedSkillCatalogError,
)
from app.skills.stager import materialize_worker_pinned_skill as materialize_worker_pinned_skill
from app.skills.application.worker_skill_snapshot_persistence import (
    persist_worker_skill_snapshots as persist_worker_skill_snapshots,
)
from app.skills.application.skill_markdown import (
    parse_skill_markdown_front_matter as parse_skill_markdown_front_matter,
)
from app.skills.application.run_admission import (
    SkillRunVersionMismatch as SkillRunVersionMismatch,
)
from app.skills.domain.pinned_snapshot import (
    PinnedSkillMismatch as PinnedSkillMismatch,
    pin_manifests_for_result as pin_manifests_for_result,
    skill_manifests_from_catalog as skill_manifests_from_catalog,
    staged_skill_manifests as staged_skill_manifests,
    validate_pinned_skill_relative_path as validate_pinned_skill_relative_path,
)
from app.skills.domain.snapshot_paths import (
    skill_snapshot_components_fit as skill_snapshot_components_fit,
)
from app.skills.domain.executable_names import (
    is_valid_executable_skill_name as is_valid_executable_skill_name,
)
from app.skills.domain.version_labels import (
    next_uploaded_skill_display_version,
    resolve_uploaded_skill_display_versions,
)


def resolve_worker_runtime_catalog(payload: Any) -> Any | None:
    from app.skills.catalog import (
        AuthorizedSkillCatalogBinding,
        load_runtime_authorized_skill_catalog,
    )

    return _resolve_worker_runtime_catalog(
        payload, catalog_binding_type=AuthorizedSkillCatalogBinding,
        catalog_error_type=AuthorizedSkillCatalogError,
        load_catalog=load_runtime_authorized_skill_catalog,
    )


def merged_worker_pinned_manifests(
    payload: Any, catalog: Any | None,
) -> dict[str, dict[str, Any]]:
    return _merged_worker_pinned_manifests(
        payload, catalog, catalog_error_type=AuthorizedSkillCatalogError,
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
_skill_run_admission_service: SkillRunAdmissionService | None = None
_skill_mcp_pinner: Callable[..., list[dict[str, Any]]] | None = None


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


def configure_skill_run_admission(
    service: SkillRunAdmissionService,
    mcp_pinner: Callable[..., list[dict[str, Any]]],
) -> None:
    global _skill_run_admission_service, _skill_mcp_pinner
    _skill_run_admission_service = service
    _skill_mcp_pinner = mcp_pinner


async def admit_skill_run(
    conn: Any,
    *,
    skill: dict[str, Any],
    skill_id: str,
    input_payload: dict[str, Any],
    tenant_id: str,
    rollout_key: str,
    expected_version: str | None = None,
    department_id: str = "",
    roles: list[str] | None = None,
    permissions: list[str] | None = None,
) -> SkillRunAdmission:
    """Resolve release policy and lock the Skill manifest for one run."""
    if _skill_run_admission_service is None:
        raise RuntimeError("skill_run_admission_not_configured")
    return await _skill_run_admission_service.admit(
        conn,
        skill=skill,
        skill_id=skill_id,
        input_payload=input_payload,
        tenant_id=tenant_id,
        rollout_key=rollout_key,
        expected_version=expected_version,
        department_id=department_id, roles=roles, permissions=permissions,
    )


async def admit_skill_set(
    conn: Any,
    *,
    roots: Sequence[tuple[str, dict[str, Any], str | None]],
    input_payload: dict[str, Any],
    tenant_id: str,
    rollout_key: str,
    department_id: str = "",
    roles: list[str] | None = None,
    permissions: list[str] | None = None,
) -> list[SkillRunAdmission]:
    """Resolve an already-authorized root set with one shared dependency closure."""
    if _skill_run_admission_service is None:
        raise RuntimeError("skill_run_admission_not_configured")
    return await _skill_run_admission_service.admit_set(
        conn,
        roots=roots,
        department_id=department_id, roles=roles, permissions=permissions,
        input_payload=input_payload,
        tenant_id=tenant_id,
        rollout_key=rollout_key,
    )


def pin_skill_run_mcp_tools(
    skill_manifests: list[dict[str, Any]],
    *,
    skill_id: str,
    mcp_tool_ids: list[str],
) -> list[dict[str, Any]]:
    """Attach the authorized MCP selection after Skill version checks pass."""
    if _skill_mcp_pinner is None:
        raise RuntimeError("skill_run_admission_not_configured")
    return _skill_mcp_pinner(
        skill_manifests,
        skill_id=skill_id,
        mcp_tool_ids=mcp_tool_ids,
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
    "MAX_SKILL_RUN_MANIFESTS",
    "SkillRunAdmission",
    "SkillRunVersionMismatch",
    "PinnedSkillMismatch",
    "stage_pinned_skill_snapshot",
    "select_pinned_skill_snapshots",
    "pin_manifests_for_result",
    "persist_worker_skill_snapshots",
    "skill_manifests_from_catalog",
    "staged_skill_manifests",
    "validate_pinned_skill_relative_path",
    "admit_skill_run",
    "admit_skill_set",
    "configure_skill_run_admission",
    "configure_skill_display_version_persistence",
    "list_uploaded_skill_display_version_rows",
    "lock_skill_for_version_upload",
    "is_valid_executable_skill_name",
    "next_uploaded_skill_display_version",
    "pin_skill_run_mcp_tools",
    "resolve_uploaded_skill_display_versions",
    "restore_admitted_skill_manifest_authority",
]
