from __future__ import annotations

import base64
import hashlib
import json
import re
from typing import Any

from app.skills.domain.snapshot_paths import skill_snapshot_components_fit
from app.skills.dependencies import validate_skill_dependency_ids
from app.skills.execution_profiles import resolve_skill_execution_profile
from app.skills.lifecycle import is_admin_materializable_status

MAX_SKILL_SNAPSHOT_FILE_BYTES = 8 * 1024 * 1024
MAX_SKILL_SNAPSHOT_TOTAL_BYTES = 16 * 1024 * 1024
SKILL_PINNED_SNAPSHOT_GOVERNANCE_SCHEMA_VERSION_V1 = (
    "ai-platform.skill-pinned-snapshot-governance.v1"
)
SKILL_PINNED_SNAPSHOT_GOVERNANCE_SCHEMA_VERSION_V2 = (
    "ai-platform.skill-pinned-snapshot-governance.v2"
)
SKILL_PINNED_SNAPSHOT_GOVERNANCE_SCHEMA_VERSION = (
    SKILL_PINNED_SNAPSHOT_GOVERNANCE_SCHEMA_VERSION_V2
)
SKILL_MATERIALIZATION_REF_SCHEMA_VERSION = "ai-platform.skill-materialization-ref.v1"
_SKILL_MATERIALIZATION_REF_KEYS = frozenset(
    {
        "schema_version",
        "skill_id",
        "version",
        "content_hash",
        "materialization_sha256",
    }
)
_SAFE_SKILL_REF_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class SkillVersionMaterializationError(ValueError):
    pass


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


def _safe_manifest_file_summary(item: dict[str, Any]) -> dict[str, Any]:
    raw_path = str(item.get("relative_path") or item.get("path") or "").replace("\\", "/")
    relative_path = raw_path.strip("/")
    path_segments = relative_path.split("/")
    if (
        not relative_path
        or raw_path.startswith("/")
        or ":" in raw_path
        or any(segment == ".." for segment in path_segments)
        or not skill_snapshot_components_fit(path_segments)
    ):
        raise SkillVersionMaterializationError("skill_version_not_materializable")
    encoded = str(item.get("content_base64") or "")
    try:
        content = base64.b64decode(encoded.encode("ascii"), validate=True)
    except Exception as exc:
        raise SkillVersionMaterializationError("skill_version_not_materializable") from exc
    raw_size = item.get("size_bytes")
    if raw_size is None:
        size_bytes = len(content)
    else:
        try:
            size_bytes = int(raw_size)
        except (TypeError, ValueError) as exc:
            raise SkillVersionMaterializationError("skill_version_not_materializable") from exc
        if size_bytes < 0 or size_bytes != len(content):
            raise SkillVersionMaterializationError("skill_version_not_materializable")
    return {
        "relative_path": relative_path,
        "size_bytes": size_bytes,
        "sha256": hashlib.sha256(content).hexdigest(),
    }


def _safe_file_summaries(files: object) -> list[dict[str, Any]]:
    if not isinstance(files, list):
        return []
    summaries: list[dict[str, Any]] = []
    for item in files:
        if not isinstance(item, dict):
            raise SkillVersionMaterializationError("skill_version_not_materializable")
        summaries.append(_safe_manifest_file_summary(item))
    return summaries


def _release_lock_summary(release_decision: dict[str, Any] | None) -> dict[str, Any]:
    decision = release_decision if isinstance(release_decision, dict) else {}
    policy_mode = "release_policy" if bool(decision.get("policy_active")) else "manifest_pin"
    summary: dict[str, Any] = {
        "schema_version": str(decision.get("schema_version") or ""),
        "mode": policy_mode,
    }
    return summary


def build_skill_snapshot_governance(
    manifest: dict[str, Any],
    *,
    release_decision: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the safe governance summary persisted with a pinned run Skill snapshot."""

    source = manifest.get("source") if isinstance(manifest.get("source"), dict) else {}
    files = _safe_file_summaries(manifest.get("files"))
    dependency_ids = _string_list(manifest.get("dependency_ids"))
    return {
        "schema_version": SKILL_PINNED_SNAPSHOT_GOVERNANCE_SCHEMA_VERSION,
        "snapshot_source": "platform_release_lock",
        "release_lock": _release_lock_summary(release_decision),
        "manifest": {
            "source_kind": str(source.get("kind") or ""),
            "selected_file_count": len(files),
        },
        "selected_files": files,
        "dependency_evidence": {
            "status": "review_required" if dependency_ids else "not_required",
            "ref": "skill_dependency_policy",
            "dependency_count": len(dependency_ids),
        },
        "does_not_close_b4_or_deployed_runtime_acceptance": True,
    }


def attach_skill_snapshot_governance(
    skill_manifests: list[dict[str, Any]],
    *,
    release_decision: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Attach safe pinned snapshot governance without mutating existing manifest dicts."""

    attached: list[dict[str, Any]] = []
    for manifest in skill_manifests:
        item = dict(manifest)
        item["snapshot_governance"] = build_skill_snapshot_governance(
            item,
            release_decision=release_decision,
        )
        attached.append(item)
    return attached


def skill_manifest_materialization_sha256(manifest: dict[str, Any]) -> str:
    """Bind a private materialization to its complete canonical package."""

    try:
        canonical = json.dumps(
            manifest,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise SkillVersionMaterializationError("skill_version_not_materializable") from exc
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_skill_manifest_ref(manifest: dict[str, Any]) -> dict[str, Any]:
    """Project a bounded execution reference without package file contents."""

    files = manifest.get("files")
    skill_id = str(manifest.get("skill_id") or "").strip()
    version = str(manifest.get("version") or manifest.get("skill_version") or "").strip()
    content_hash = str(manifest.get("content_hash") or "").strip()
    if (
        not skill_id
        or not version
        or version != content_hash
        or not isinstance(files, list)
        or not files
    ):
        raise SkillVersionMaterializationError("skill_version_not_materializable")
    return {
        "schema_version": SKILL_MATERIALIZATION_REF_SCHEMA_VERSION,
        "skill_id": skill_id,
        "version": version,
        "content_hash": content_hash,
        "materialization_sha256": skill_manifest_materialization_sha256(manifest),
    }


def build_skill_manifest_refs(
    skill_manifests: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    refs = [build_skill_manifest_ref(manifest) for manifest in skill_manifests]
    if len({item["skill_id"] for item in refs}) != len(refs):
        raise SkillVersionMaterializationError("skill_version_not_materializable")
    return refs


def validate_skill_manifest_refs(value: object) -> list[dict[str, Any]]:
    """Accept only bounded references at persisted and Redis transport boundaries."""

    if not isinstance(value, list):
        raise SkillVersionMaterializationError("skill_version_not_materializable")
    refs: list[dict[str, Any]] = []
    for raw in value:
        if not isinstance(raw, dict) or set(raw) != _SKILL_MATERIALIZATION_REF_KEYS:
            raise SkillVersionMaterializationError("skill_version_not_materializable")
        skill_id = raw.get("skill_id")
        version = raw.get("version")
        content_hash = raw.get("content_hash")
        digest = raw.get("materialization_sha256")
        if (
            raw.get("schema_version") != SKILL_MATERIALIZATION_REF_SCHEMA_VERSION
            or not isinstance(skill_id, str)
            or _SAFE_SKILL_REF_ID.fullmatch(skill_id) is None
            or not isinstance(version, str)
            or _SAFE_SKILL_REF_ID.fullmatch(version) is None
            or not isinstance(content_hash, str)
            or content_hash != version
            or not isinstance(digest, str)
            or _SHA256.fullmatch(digest) is None
        ):
            raise SkillVersionMaterializationError("skill_version_not_materializable")
        refs.append(dict(raw))
    if len({ref["skill_id"] for ref in refs}) != len(refs):
        raise SkillVersionMaterializationError("skill_version_not_materializable")
    return refs


def _materialization_error() -> SkillVersionMaterializationError:
    return SkillVersionMaterializationError("skill_version_not_materializable")


def _build_skill_version_manifest_pin(
    skill_version: dict[str, Any],
    *,
    allowed_kinds: set[str],
) -> dict[str, Any]:
    if not is_admin_materializable_status(skill_version.get("status")):
        raise _materialization_error()
    source = skill_version.get("source")
    if not isinstance(source, dict) or str(source.get("kind") or "") not in allowed_kinds:
        raise _materialization_error()
    version = str(skill_version.get("version") or "")
    content_hash = str(skill_version.get("content_hash") or "")
    if not version or content_hash != version:
        raise _materialization_error()
    files = source.get("files")
    if not isinstance(files, list) or not files:
        raise _materialization_error()
    _safe_file_summaries(files)

    manifest_source = {key: value for key, value in source.items() if key not in {"files", "dependency_manifests"}}
    lifecycle_status = str(skill_version.get("status") or "")
    execution_profile = resolve_skill_execution_profile(
        skill_id=str(skill_version.get("skill_id") or ""),
        source_kind=str(manifest_source.get("kind") or ""),
        lifecycle_status=lifecycle_status,
    )
    return {
        "skill_id": str(skill_version.get("skill_id") or ""),
        "description": str(skill_version.get("description") or ""),
        "version": version,
        "content_hash": content_hash,
        "source": manifest_source,
        "files": files,
        "dependency_ids": _string_list(skill_version.get("dependency_ids")),
        "lifecycle_status": lifecycle_status,
        "execution_profile": execution_profile,
        "builtin_tool_identities": execution_profile["builtin_tool_identities"],
        "allowed": True,
        "staged": False,
        "used": False,
    }


def build_uploaded_skill_manifest_pin(skill_version: dict[str, Any]) -> dict[str, Any]:
    return _build_skill_version_manifest_pin(skill_version, allowed_kinds={"uploaded"})


def build_skill_version_manifest_pin(skill_version: dict[str, Any]) -> dict[str, Any]:
    return _build_skill_version_manifest_pin(skill_version, allowed_kinds={"builtin", "uploaded"})


def validate_skill_version_dependency_policy(
    skill_version: dict[str, Any],
    *,
    available_skill_ids: set[str],
) -> None:
    try:
        validate_skill_dependency_ids(
            str(skill_version.get("skill_id") or ""),
            _string_list(skill_version.get("dependency_ids")),
            available_skill_ids,
        )
    except ValueError as exc:
        raise _materialization_error() from exc


def build_skill_version_policy_manifest_pins(
    skill_version: dict[str, Any],
    *,
    available_skill_ids: set[str],
) -> list[dict[str, Any]]:
    """Pin one selected version after validating its declared dependency IDs.

    Current dependency versions are resolved by run admission. Saved package
    dependency snapshots are not an execution input.
    """

    validate_skill_version_dependency_policy(
        skill_version,
        available_skill_ids=available_skill_ids,
    )
    return [build_skill_version_manifest_pin(skill_version)]


def locked_skill_version(
    *,
    skill_id: str,
    skill_manifests: list[dict[str, Any]],
    fallback_version: str,
) -> str:
    for item in skill_manifests:
        if str(item.get("skill_id") or "") != skill_id:
            continue
        version = str(item.get("content_hash") or item.get("version") or "")
        if version:
            return version
    return fallback_version


def governed_locked_skill_version(
    *,
    skill_id: str,
    skill_manifests: list[dict[str, Any]],
    fallback_version: str,
    release_policy_version: object | None = None,
) -> str:
    policy_version = str(release_policy_version or "")
    if policy_version:
        for item in skill_manifests:
            if str(item.get("skill_id") or "") != skill_id:
                continue
            pinned_version = str(item.get("content_hash") or item.get("version") or "")
            if pinned_version == policy_version:
                return pinned_version
            break
        raise SkillVersionMaterializationError("skill_version_not_materializable")

    locked_version = locked_skill_version(
        skill_id=skill_id,
        skill_manifests=skill_manifests,
        fallback_version="",
    )
    if not locked_version:
        raise _materialization_error()
    return locked_version
