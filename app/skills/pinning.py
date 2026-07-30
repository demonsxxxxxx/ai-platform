from __future__ import annotations

import base64
import hashlib
from pathlib import Path
from typing import Any

from app.skills.dependencies import skill_dependency_ids, with_skill_dependencies
from app.skills.deliverables import (
    SkillDeliverableContractError,
    parse_skill_deliverable_contract,
    validate_skill_deliverable_contract,
)
from app.skills.execution_profiles import resolve_skill_execution_profile
from app.skills.lifecycle import is_admin_materializable_status
from app.skills.registry import BuiltinSkill, iter_skill_files, parse_skill_markdown_front_matter

MAX_SKILL_SNAPSHOT_FILE_BYTES = 8 * 1024 * 1024
MAX_SKILL_SNAPSHOT_TOTAL_BYTES = 16 * 1024 * 1024
SKILL_PINNED_SNAPSHOT_GOVERNANCE_SCHEMA_VERSION = (
    "ai-platform.skill-pinned-snapshot-governance.v1"
)
class SkillVersionMaterializationError(ValueError):
    pass


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


def _requested_skill_ids(skill_id: str, input_payload: dict[str, Any]) -> list[str]:
    requested = _string_list(input_payload.get("skill_ids"))
    if skill_id:
        requested.insert(0, skill_id)
    return list(dict.fromkeys(requested))


def _snapshot_files(path: Path) -> list[dict[str, Any]]:
    files: list[dict[str, Any]] = []
    total_bytes = 0
    for item in sorted(
        iter_skill_files(path),
        key=lambda child: child.relative_to(path).as_posix(),
    ):
        relative_path = item.relative_to(path).as_posix()
        if relative_path.startswith("../") or relative_path == "..":
            raise ValueError("skill snapshot path escaped skill root")
        content = item.read_bytes()
        if len(content) > MAX_SKILL_SNAPSHOT_FILE_BYTES:
            raise ValueError(f"skill snapshot file too large: {relative_path}")
        total_bytes += len(content)
        if total_bytes > MAX_SKILL_SNAPSHOT_TOTAL_BYTES:
            raise ValueError("skill snapshot too large")
        files.append(
            {
                "relative_path": relative_path,
                "content_base64": base64.b64encode(content).decode("ascii"),
                "size_bytes": len(content),
            }
        )
    return files


def _safe_manifest_file_summary(item: dict[str, Any]) -> dict[str, Any]:
    raw_path = str(item.get("relative_path") or item.get("path") or "").replace("\\", "/")
    relative_path = raw_path.strip("/")
    path_segments = relative_path.split("/")
    if (
        not relative_path
        or raw_path.startswith("/")
        or ":" in raw_path
        or any(segment == ".." for segment in path_segments)
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
    result = {
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
        "does_not_close_b4_or_211": True,
    }
    raw_contract = manifest.get("deliverable_contract")
    if raw_contract is not None:
        try:
            result["deliverable_contract"] = validate_skill_deliverable_contract(raw_contract)
        except SkillDeliverableContractError as exc:
            raise SkillVersionMaterializationError("skill_version_not_materializable") from exc
    return result


def _deliverable_contract_from_files(files: object) -> dict[str, object] | None:
    """Read a declaration only from immutable ``SKILL.md`` snapshot bytes."""

    if not isinstance(files, list):
        raise SkillVersionMaterializationError("skill_version_not_materializable")
    encoded_skill_md = next(
        (
            item.get("content_base64")
            for item in files
            if isinstance(item, dict) and item.get("relative_path") == "SKILL.md"
        ),
        None,
    )
    if not isinstance(encoded_skill_md, str):
        raise SkillVersionMaterializationError("skill_version_not_materializable")
    try:
        metadata = parse_skill_markdown_front_matter(
            base64.b64decode(encoded_skill_md.encode("ascii"), validate=True).decode("utf-8")
        )
        return parse_skill_deliverable_contract(metadata)
    except (UnicodeDecodeError, ValueError, SkillDeliverableContractError) as exc:
        raise SkillVersionMaterializationError("skill_version_not_materializable") from exc


def _deliverable_contract_from_version(
    skill_version: dict[str, Any],
    *,
    source: dict[str, Any],
    files: object,
) -> dict[str, object] | None:
    """Bind upload declarations to immutable package evidence before pinning."""

    raw_package_contract = source.get("package_contract")
    if raw_package_contract is None:
        return _deliverable_contract_from_files(files)
    if not isinstance(raw_package_contract, dict):
        raise SkillVersionMaterializationError("skill_version_not_materializable")
    file_contract = _deliverable_contract_from_files(files)
    if "deliverable_contract" not in raw_package_contract:
        if file_contract is not None:
            raise SkillVersionMaterializationError("skill_version_not_materializable")
        return None
    if (
        raw_package_contract.get("schema_version") != "ai-platform.skill-package-contract.v1"
        or str(raw_package_contract.get("skill_id") or "")
        != str(skill_version.get("skill_id") or "")
        or str(raw_package_contract.get("version") or "")
        != str(skill_version.get("content_hash") or "")
        or str(raw_package_contract.get("content_hash") or "")
        != str(skill_version.get("content_hash") or "")
        or not str(raw_package_contract.get("package_sha256") or "")
        or not str(raw_package_contract.get("storage_key") or "")
        or not str(raw_package_contract.get("uploaded_by") or "")
    ):
        raise SkillVersionMaterializationError("skill_version_not_materializable")
    raw_contract = raw_package_contract.get("deliverable_contract")
    if raw_contract is None:
        if file_contract is not None:
            raise SkillVersionMaterializationError("skill_version_not_materializable")
        return None
    try:
        validated = validate_skill_deliverable_contract(raw_contract)
    except SkillDeliverableContractError as exc:
        raise SkillVersionMaterializationError("skill_version_not_materializable") from exc
    if file_contract != validated:
        raise SkillVersionMaterializationError("skill_version_not_materializable")
    return validated


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


def build_skill_manifest_pins(
    *,
    skill_id: str,
    input_payload: dict[str, Any],
    builtin_skills: list[BuiltinSkill],
) -> list[dict[str, Any]]:
    by_id = {skill.name: skill for skill in builtin_skills}
    available = set(by_id)
    selected = [item for item in _requested_skill_ids(skill_id, input_payload) if item in available]
    if not selected:
        return []
    selected = with_skill_dependencies(selected, available)
    selected_set = set(selected)
    manifests: list[dict[str, Any]] = []
    for item in selected:
        skill = by_id[item]
        execution_profile = resolve_skill_execution_profile(
            skill_id=skill.name,
            source_kind=str(skill.source.get("kind") or "") if isinstance(skill.source, dict) else "",
            lifecycle_status="released",
        )
        manifest = {
            "skill_id": skill.name,
            "description": skill.description,
            "version": skill.version,
            "content_hash": skill.version,
            "source": skill.source,
            "files": _snapshot_files(skill.path),
            "dependency_ids": skill_dependency_ids(skill.name, selected_set),
            "lifecycle_status": "released",
            "execution_profile": execution_profile,
            "builtin_tool_identities": execution_profile["builtin_tool_identities"],
            "allowed": True,
            "staged": False,
            "used": False,
        }
        deliverable_contract = _deliverable_contract_from_files(manifest["files"])
        if deliverable_contract is not None:
            manifest["deliverable_contract"] = deliverable_contract
        manifests.append(manifest)
    return manifests


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

    manifest_source = {key: value for key, value in source.items() if key not in {"files", "dependency_manifests"}}
    lifecycle_status = str(skill_version.get("status") or "")
    execution_profile = resolve_skill_execution_profile(
        skill_id=str(skill_version.get("skill_id") or ""),
        source_kind=str(manifest_source.get("kind") or ""),
        lifecycle_status=lifecycle_status,
    )
    manifest = {
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
    deliverable_contract = _deliverable_contract_from_version(
        skill_version,
        source=source,
        files=files,
    )
    if deliverable_contract is not None:
        manifest["deliverable_contract"] = deliverable_contract
    return manifest


def build_uploaded_skill_manifest_pin(skill_version: dict[str, Any]) -> dict[str, Any]:
    return _build_skill_version_manifest_pin(skill_version, allowed_kinds={"uploaded"})


def build_skill_version_manifest_pin(skill_version: dict[str, Any]) -> dict[str, Any]:
    return _build_skill_version_manifest_pin(skill_version, allowed_kinds={"builtin", "uploaded"})


def build_skill_version_dependency_manifest_pins(skill_version: dict[str, Any]) -> list[dict[str, Any]]:
    dependency_ids = _string_list(skill_version.get("dependency_ids"))
    if not dependency_ids:
        return []
    source = skill_version.get("source")
    if not isinstance(source, dict):
        raise _materialization_error()
    raw_manifests = source.get("dependency_manifests")
    if not isinstance(raw_manifests, list):
        raise _materialization_error()

    by_id: dict[str, dict[str, Any]] = {}
    for raw_manifest in raw_manifests:
        if not isinstance(raw_manifest, dict):
            raise _materialization_error()
        dependency_id = str(raw_manifest.get("skill_id") or "")
        if not dependency_id or dependency_id in by_id:
            raise _materialization_error()
        manifest_source = raw_manifest.get("source")
        files = raw_manifest.get("files")
        if not isinstance(manifest_source, dict) or not isinstance(files, list) or not files:
            raise _materialization_error()
        by_id[dependency_id] = build_skill_version_manifest_pin(
            {
                "skill_id": dependency_id,
                "version": str(raw_manifest.get("version") or ""),
                "content_hash": str(raw_manifest.get("content_hash") or ""),
                "description": str(raw_manifest.get("description") or ""),
                "source": {**manifest_source, "files": files},
                "dependency_ids": _string_list(raw_manifest.get("dependency_ids")),
                "status": "active",
            }
        )

    if set(by_id) != set(dependency_ids):
        raise _materialization_error()
    return [by_id[dependency_id] for dependency_id in dependency_ids]


def validate_skill_version_dependency_policy(
    skill_version: dict[str, Any],
    *,
    available_skill_ids: set[str],
) -> None:
    try:
        expected_dependency_ids = skill_dependency_ids(
            str(skill_version.get("skill_id") or ""),
            available_skill_ids,
        )
    except ValueError as exc:
        raise _materialization_error() from exc
    if _string_list(skill_version.get("dependency_ids")) != expected_dependency_ids:
        raise _materialization_error()


def build_skill_version_policy_manifest_pins(
    skill_version: dict[str, Any],
    *,
    available_skill_ids: set[str],
) -> list[dict[str, Any]]:
    validate_skill_version_dependency_policy(
        skill_version,
        available_skill_ids=available_skill_ids,
    )
    primary_pin = build_skill_version_manifest_pin(skill_version)
    return [primary_pin] + build_skill_version_dependency_manifest_pins(skill_version)


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
