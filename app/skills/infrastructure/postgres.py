from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from psycopg import AsyncConnection

from app.platform.postgres.errors import RepositoryAuthorizationError, RepositoryConflictError
from app.platform.public_payload import sanitize_public_payload
from app.skills.execution_profiles import (
    SkillExecutionProfileError,
    canonical_skill_execution_profile,
)
from app.skills.pinning import (
    SkillVersionMaterializationError,
    build_skill_snapshot_governance,
)

_ALLOWED_EXECUTOR_TYPES = frozenset({"claude-agent-worker"})
_TRUSTED_BUILTIN_MCP_TOOL_ID = "ragflow-knowledge-search"


class SkillRunSnapshotError(ValueError):
    pass


@dataclass(frozen=True)
class ReplaySkillManifestPlan:
    manifests: tuple[dict[str, Any], ...]
    mcp_tool_ids: tuple[str, ...]


def _without_private_material(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _without_private_material(item)
            for key, item in value.items()
            if str(key) not in {"files", "storage_key", "host_path", "local_path"}
        }
    if isinstance(value, list):
        return [_without_private_material(item) for item in value]
    return value


def _release_decision_sha256(release_decision: dict[str, Any] | None) -> str:
    canonical = json.dumps(
        release_decision if isinstance(release_decision, dict) else {},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _canonical_builtin_tool_identities(skill_manifest: dict[str, Any]) -> list[str]:
    try:
        profile = canonical_skill_execution_profile(skill_manifest)
    except SkillExecutionProfileError as exc:
        raise SkillRunSnapshotError("run_skill_snapshot_identity_mismatch") from exc
    declared = profile["builtin_tool_identities"]
    raw = skill_manifest.get("builtin_tool_identities")
    if raw is None and not declared:
        return []
    if not isinstance(raw, list) or any(not isinstance(item, str) for item in raw):
        raise SkillRunSnapshotError("run_skill_snapshot_identity_mismatch")
    requested = set(raw)
    if any(identity not in declared for identity in requested):
        raise SkillRunSnapshotError("run_skill_snapshot_identity_mismatch")
    canonical = [identity for identity in declared if identity in requested]
    if canonical != declared:
        raise SkillRunSnapshotError("run_skill_snapshot_identity_mismatch")
    return canonical


def canonical_builtin_tool_identities(skill_manifest: dict[str, Any]) -> list[str]:
    """Return the exact server-owned builtin capability declaration for a pin."""

    try:
        return _canonical_builtin_tool_identities(skill_manifest)
    except SkillRunSnapshotError as exc:
        raise RepositoryConflictError("run_skill_snapshot_identity_mismatch") from exc


def run_skill_snapshot_source_json(
    skill_manifest: dict[str, Any],
    *,
    release_decision: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Project immutable, non-secret Skill source identity for run provenance."""

    source = sanitize_public_payload(
        skill_manifest.get("source") if isinstance(skill_manifest.get("source"), dict) else {}
    )
    projected = _without_private_material(source if isinstance(source, dict) else {})
    projected.pop("version", None)
    manifest_release_decision = skill_manifest.get("release_decision")
    if manifest_release_decision is not None:
        if not isinstance(manifest_release_decision, dict):
            raise RepositoryConflictError("run_skill_snapshot_identity_mismatch")
        effective_release_decision = manifest_release_decision
    else:
        effective_release_decision = release_decision
    try:
        governance = build_skill_snapshot_governance(
            skill_manifest,
            release_decision=effective_release_decision,
        )
        execution_profile = canonical_skill_execution_profile(skill_manifest)
        builtin_tool_identities = _canonical_builtin_tool_identities(skill_manifest)
    except (
        SkillVersionMaterializationError,
        SkillExecutionProfileError,
        SkillRunSnapshotError,
    ) as exc:
        raise RepositoryConflictError("run_skill_snapshot_identity_mismatch") from exc
    projected["snapshot_governance"] = _without_private_material(governance)
    projected["release_decision_sha256"] = _release_decision_sha256(effective_release_decision)
    projected["builtin_tool_identities"] = builtin_tool_identities
    projected["execution_profile"] = execution_profile
    raw_mcp_tool_ids = skill_manifest.get("mcp_tool_ids")
    if raw_mcp_tool_ids is None:
        raw_mcp_tool_ids = []
    if not isinstance(raw_mcp_tool_ids, list) or any(
        not isinstance(item, str) or not item for item in raw_mcp_tool_ids
    ):
        raise RepositoryConflictError("run_skill_snapshot_identity_mismatch")
    projected["mcp_tool_ids"] = list(dict.fromkeys(raw_mcp_tool_ids))
    return projected


def _build_replay_skill_manifest_plan(
    *,
    skill_id: str,
    pinned_version: str,
    pinned_executor_type: str,
    skill_manifests: list[dict[str, Any]],
    skill_set: list[dict[str, Any]] | None,
) -> ReplaySkillManifestPlan:
    if (
        pinned_executor_type not in _ALLOWED_EXECUTOR_TYPES
        or not skill_id
        or not pinned_version
    ):
        raise SkillRunSnapshotError("capability_not_authorized")
    requested_skill_versions: dict[str, str] = {skill_id: pinned_version}
    if skill_set is not None:
        if not skill_set:
            raise SkillRunSnapshotError("capability_not_authorized")
        requested_skill_versions = {}
        for selection in skill_set:
            if not isinstance(selection, dict):
                raise SkillRunSnapshotError("capability_not_authorized")
            selected_skill_id = str(selection.get("skill_id") or "")
            selected_version = str(selection.get("expected_version") or "")
            if (
                not selected_skill_id
                or not selected_version
                or selected_skill_id in requested_skill_versions
            ):
                raise SkillRunSnapshotError("capability_not_authorized")
            requested_skill_versions[selected_skill_id] = selected_version
        if requested_skill_versions.get(skill_id) != pinned_version:
            raise SkillRunSnapshotError("capability_not_authorized")

    root_manifests: dict[str, dict[str, Any]] = {}
    manifests_by_skill_id: dict[str, dict[str, Any]] = {}
    normalized_manifests: list[dict[str, Any]] = []
    for manifest in skill_manifests:
        if not isinstance(manifest, dict):
            raise SkillRunSnapshotError("capability_not_authorized")
        _canonical_builtin_tool_identities(manifest)
        manifest_skill_id = str(manifest.get("skill_id") or "")
        version = str(manifest.get("version") or manifest.get("skill_version") or "")
        content_hash = str(manifest.get("content_hash") or "")
        dependency_ids = manifest.get("dependency_ids")
        if (
            not manifest_skill_id
            or not version
            or version != content_hash
            or not isinstance(manifest.get("files"), list)
            or not manifest["files"]
            or not isinstance(dependency_ids, list)
            or any(not isinstance(item, str) or not item for item in dependency_ids)
            or len(dependency_ids) != len(set(dependency_ids))
            or manifest_skill_id in dependency_ids
            or manifest_skill_id in manifests_by_skill_id
        ):
            raise SkillRunSnapshotError("capability_not_authorized")
        manifests_by_skill_id[manifest_skill_id] = manifest
        if requested_skill_versions.get(manifest_skill_id) == version:
            if manifest_skill_id in root_manifests:
                raise SkillRunSnapshotError("capability_not_authorized")
            root_manifests[manifest_skill_id] = manifest
        normalized_manifests.append(manifest)
    if set(root_manifests) != set(requested_skill_versions):
        raise SkillRunSnapshotError("capability_not_authorized")

    reachable_skill_ids: set[str] = set()
    visiting_skill_ids: set[str] = set()

    def visit_manifest(reachable_skill_id: str) -> None:
        if reachable_skill_id in visiting_skill_ids:
            raise SkillRunSnapshotError("capability_not_authorized")
        if reachable_skill_id in reachable_skill_ids:
            return
        reachable_manifest = manifests_by_skill_id.get(reachable_skill_id)
        if reachable_manifest is None:
            raise SkillRunSnapshotError("capability_not_authorized")
        visiting_skill_ids.add(reachable_skill_id)
        for dependency_id in reachable_manifest["dependency_ids"]:
            visit_manifest(dependency_id)
        visiting_skill_ids.remove(reachable_skill_id)
        reachable_skill_ids.add(reachable_skill_id)

    for root_skill_id in requested_skill_versions:
        visit_manifest(root_skill_id)
    if reachable_skill_ids != set(manifests_by_skill_id):
        raise SkillRunSnapshotError("capability_not_authorized")

    mcp_tool_ids: list[str] = []
    for selected_skill_id in requested_skill_versions:
        raw_tool_ids = root_manifests[selected_skill_id].get("mcp_tool_ids")
        if not isinstance(raw_tool_ids, list) or any(
            not isinstance(item, str) or not item for item in raw_tool_ids
        ):
            raise SkillRunSnapshotError("capability_not_authorized")
        if (
            selected_skill_id == _TRUSTED_BUILTIN_MCP_TOOL_ID
            and _TRUSTED_BUILTIN_MCP_TOOL_ID not in raw_tool_ids
        ):
            raise SkillRunSnapshotError("capability_not_authorized")
        for tool_id in raw_tool_ids:
            if tool_id not in mcp_tool_ids:
                mcp_tool_ids.append(tool_id)
    return ReplaySkillManifestPlan(tuple(normalized_manifests), tuple(mcp_tool_ids))


def _project_skill_version(row: dict[str, Any]) -> dict[str, Any]:
    source = row.get("source_json")
    dependency_ids = row.get("dependency_ids")
    return {
        "skill_id": row["skill_id"],
        "version": row["version"],
        "content_hash": row["content_hash"],
        "description": row.get("description") or "",
        "source": source if isinstance(source, dict) else {},
        "dependency_ids": (
            [str(item) for item in dependency_ids] if isinstance(dependency_ids, list) else []
        ),
        "status": row.get("status") or "active",
        "created_by": row.get("created_by"),
        "created_at": row.get("created_at"),
    }


async def get_skill_version(
    conn: AsyncConnection,
    *,
    skill_id: str,
    version: str,
) -> dict[str, Any] | None:
    cursor = await conn.execute(
        """
        select
          skill_id,
          version,
          content_hash,
          description,
          source_json,
          dependency_ids,
          status,
          created_by,
          created_at
        from skill_versions
        where skill_id = %s and version = %s
        """,
        (skill_id, version),
    )
    row = await cursor.fetchone()
    return _project_skill_version(row) if row is not None else None


async def validate_replay_skill_manifests(
    conn: AsyncConnection,
    *,
    skill_id: str,
    pinned_version: str,
    pinned_executor_type: str,
    skill_manifests: list[dict[str, Any]],
    skill_set: list[dict[str, Any]] | None = None,
) -> list[str]:
    """Validate an exact historical package while allowing ordinary deprecation."""

    try:
        plan = _build_replay_skill_manifest_plan(
            skill_id=skill_id,
            pinned_version=pinned_version,
            pinned_executor_type=pinned_executor_type,
            skill_manifests=skill_manifests,
            skill_set=skill_set,
        )
    except SkillRunSnapshotError as exc:
        raise RepositoryAuthorizationError("capability_not_authorized") from exc
    for manifest in plan.manifests:
        manifest_skill_id = str(manifest.get("skill_id") or "")
        version = str(manifest.get("version") or manifest.get("skill_version") or "")
        content_hash = str(manifest.get("content_hash") or "")
        exact_version = await get_skill_version(
            conn,
            skill_id=manifest_skill_id,
            version=version,
        )
        if exact_version is None:
            source = manifest.get("source") if isinstance(manifest.get("source"), dict) else {}
            if str(source.get("kind") or "") != "builtin":
                raise RepositoryAuthorizationError("capability_not_authorized")
            continue
        if (
            str(exact_version.get("version") or "") != version
            or str(exact_version.get("content_hash") or "") != content_hash
            or str(exact_version.get("status") or "").lower()
            not in {"active", "released", "deprecated"}
        ):
            raise RepositoryAuthorizationError("capability_not_authorized")
    return list(plan.mcp_tool_ids)
