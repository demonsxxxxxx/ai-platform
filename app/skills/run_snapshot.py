from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from app.control_plane_contracts import sanitize_public_payload
from app.skills.execution_profiles import (
    SkillExecutionProfileError,
    canonical_skill_execution_profile,
)
from app.skills.pinning import (
    SkillVersionMaterializationError,
    build_skill_snapshot_governance,
)


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


def canonical_builtin_tool_identities(skill_manifest: dict[str, Any]) -> list[str]:
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


def run_skill_snapshot_source_json(
    skill_manifest: dict[str, Any],
    *,
    release_decision: dict[str, Any] | None = None,
) -> dict[str, Any]:
    source = sanitize_public_payload(
        skill_manifest.get("source") if isinstance(skill_manifest.get("source"), dict) else {}
    )
    projected = _without_private_material(source if isinstance(source, dict) else {})
    projected.pop("version", None)
    manifest_release_decision = skill_manifest.get("release_decision")
    if manifest_release_decision is not None:
        if not isinstance(manifest_release_decision, dict):
            raise SkillRunSnapshotError("run_skill_snapshot_identity_mismatch")
        effective_release_decision = manifest_release_decision
    else:
        effective_release_decision = release_decision
    try:
        governance = build_skill_snapshot_governance(
            skill_manifest,
            release_decision=effective_release_decision,
        )
        execution_profile = canonical_skill_execution_profile(skill_manifest)
    except (SkillVersionMaterializationError, SkillExecutionProfileError) as exc:
        raise SkillRunSnapshotError("run_skill_snapshot_identity_mismatch") from exc
    projected["snapshot_governance"] = _without_private_material(governance)
    projected["release_decision_sha256"] = _release_decision_sha256(effective_release_decision)
    projected["builtin_tool_identities"] = canonical_builtin_tool_identities(skill_manifest)
    projected["execution_profile"] = execution_profile
    raw_mcp_tool_ids = skill_manifest.get("mcp_tool_ids")
    if raw_mcp_tool_ids is None:
        raw_mcp_tool_ids = []
    if not isinstance(raw_mcp_tool_ids, list) or any(
        not isinstance(item, str) or not item for item in raw_mcp_tool_ids
    ):
        raise SkillRunSnapshotError("run_skill_snapshot_identity_mismatch")
    projected["mcp_tool_ids"] = list(dict.fromkeys(raw_mcp_tool_ids))
    return projected


def build_replay_skill_manifest_plan(
    *,
    skill_id: str,
    pinned_version: str,
    pinned_executor_type: str,
    skill_manifests: list[dict[str, Any]],
    skill_set: list[dict[str, Any]] | None,
    allowed_executor_types: set[str],
    trusted_builtin_mcp_tool_id: str,
) -> ReplaySkillManifestPlan:
    if pinned_executor_type not in allowed_executor_types or not skill_id or not pinned_version:
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
    normalized_manifests: list[dict[str, Any]] = []
    for manifest in skill_manifests:
        if not isinstance(manifest, dict):
            raise SkillRunSnapshotError("capability_not_authorized")
        canonical_builtin_tool_identities(manifest)
        manifest_skill_id = str(manifest.get("skill_id") or "")
        version = str(manifest.get("version") or manifest.get("skill_version") or "")
        content_hash = str(manifest.get("content_hash") or "")
        if (
            not manifest_skill_id
            or not version
            or version != content_hash
            or not isinstance(manifest.get("files"), list)
            or not manifest["files"]
            or not isinstance(manifest.get("dependency_ids"), list)
        ):
            raise SkillRunSnapshotError("capability_not_authorized")
        if requested_skill_versions.get(manifest_skill_id) == version:
            root_manifests[manifest_skill_id] = manifest
        normalized_manifests.append(manifest)
    if set(root_manifests) != set(requested_skill_versions):
        raise SkillRunSnapshotError("capability_not_authorized")

    mcp_tool_ids: list[str] = []
    for selected_skill_id in requested_skill_versions:
        raw_tool_ids = root_manifests[selected_skill_id].get("mcp_tool_ids")
        if not isinstance(raw_tool_ids, list) or any(
            not isinstance(item, str) or not item for item in raw_tool_ids
        ):
            raise SkillRunSnapshotError("capability_not_authorized")
        if selected_skill_id == trusted_builtin_mcp_tool_id and trusted_builtin_mcp_tool_id not in raw_tool_ids:
            raise SkillRunSnapshotError("capability_not_authorized")
        for tool_id in raw_tool_ids:
            if tool_id not in mcp_tool_ids:
                mcp_tool_ids.append(tool_id)
    return ReplaySkillManifestPlan(tuple(normalized_manifests), tuple(mcp_tool_ids))
