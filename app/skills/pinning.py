from __future__ import annotations

import base64
import binascii
import hashlib
import json
from pathlib import Path
import re
import shutil
from typing import Any

from app.path_safety import ensure_creatable_inside
from app.skills.dependencies import skill_dependency_ids, with_skill_dependencies
from app.skills.execution_profiles import resolve_skill_execution_profile
from app.skills.lifecycle import is_admin_materializable_status
from app.skills.registry import BuiltinSkill, iter_skill_files, skill_content_hash

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


class PinnedSkillMismatch(ValueError):
    def __init__(self, message: str, *, actual_content_hash: str = "") -> None:
        super().__init__(message)
        self.actual_content_hash = actual_content_hash


def pinned_skill_manifests(
    skill_manifests: object,
) -> dict[str, dict[str, Any]]:
    if not isinstance(skill_manifests, list):
        return {}
    return {
        str(item.get("skill_id")).strip(): item
        for item in skill_manifests
        if isinstance(item, dict) and str(item.get("skill_id") or "").strip()
    }


def materialize_pinned_skill(
    skill_name: str,
    pin: dict[str, Any],
    snapshot_root: Path,
) -> BuiltinSkill:
    if Path(skill_name).name != skill_name:
        raise ValueError(f"invalid pinned skill name: {skill_name}")
    expected_hash = str(pin.get("content_hash") or pin.get("version") or "")
    if not expected_hash:
        raise ValueError(f"pinned skill missing content hash: {skill_name}")
    target = snapshot_root / skill_name
    workspace_root = snapshot_root.parents[1]
    ensure_creatable_inside(
        workspace_root,
        target,
        "pinned skill path must stay inside the run workspace",
    )
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=True)
    ensure_creatable_inside(
        workspace_root,
        target,
        "pinned skill path must stay inside the run workspace",
    )
    total_bytes = 0
    for item in pin.get("files") or []:
        if not isinstance(item, dict):
            raise ValueError(f"invalid pinned skill file entry: {skill_name}")  # noqa: TRY004
        relative_path = str(item.get("relative_path") or "")
        if (
            not relative_path
            or Path(relative_path).is_absolute()
            or ".." in Path(relative_path).parts
        ):
            raise ValueError(f"invalid pinned skill file path: {skill_name}")
        content = base64.b64decode(str(item.get("content_base64") or ""), validate=True)
        if "size_bytes" not in item:
            raise ValueError(f"pinned skill file missing size_bytes: {skill_name}")
        if int(item["size_bytes"]) != len(content):
            raise ValueError(f"pinned skill file size mismatch: {skill_name}")
        if len(content) > MAX_SKILL_SNAPSHOT_FILE_BYTES:
            raise ValueError(f"pinned skill file too large: {skill_name}")
        total_bytes += len(content)
        if total_bytes > MAX_SKILL_SNAPSHOT_TOTAL_BYTES:
            raise ValueError(f"pinned skill snapshot too large: {skill_name}")
        output = target / relative_path
        ensure_creatable_inside(
            target,
            output,
            f"invalid pinned skill file path: {skill_name}",
        )
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(content)
    if not (target / "SKILL.md").is_file():
        raise ValueError(f"pinned skill missing SKILL.md: {skill_name}")
    actual_hash = skill_content_hash(target)
    if actual_hash != expected_hash:
        shutil.rmtree(target, ignore_errors=True)
        raise PinnedSkillMismatch(
            f"pinned skill content hash mismatch: {skill_name}",
            actual_content_hash=actual_hash,
        )
    return BuiltinSkill(
        name=skill_name,
        description=str(pin.get("description") or ""),
        path=target,
        version=expected_hash,
        source=pin.get("source") if isinstance(pin.get("source"), dict) else {},
        entry={"kind": "run-snapshot", "path": str(target)},
    )


def select_pinned_skills(
    skills: list[BuiltinSkill],
    allowed_skill_names: list[str],
    pins: dict[str, dict[str, Any]],
    snapshot_root: Path,
) -> tuple[list[BuiltinSkill], list[dict[str, str]]]:
    selected: list[BuiltinSkill] = []
    mismatches: list[dict[str, str]] = []
    by_name = {skill.name: skill for skill in skills}
    for skill_name in allowed_skill_names:
        skill = by_name.get(skill_name)
        pin = pins.get(skill_name)
        if not pin:
            mismatches.append(
                {
                    "skill_id": skill_name,
                    "expected_content_hash": "",
                    "actual_content_hash": skill.version if skill else "",
                    "reason": "missing_pinned_manifest",
                }
            )
            continue
        expected = str(pin.get("content_hash") or pin.get("version") or "")
        if pin.get("files"):
            try:
                selected.append(materialize_pinned_skill(skill_name, pin, snapshot_root))
            except PinnedSkillMismatch as exc:
                mismatches.append(
                    {
                        "skill_id": skill_name,
                        "expected_content_hash": expected,
                        "actual_content_hash": exc.actual_content_hash,
                        "reason": str(exc),
                    }
                )
            except (binascii.Error, ValueError) as exc:
                mismatches.append(
                    {
                        "skill_id": skill_name,
                        "expected_content_hash": expected,
                        "actual_content_hash": "",
                        "reason": str(exc),
                    }
                )
            continue
        if not expected:
            mismatches.append(
                {
                    "skill_id": skill_name,
                    "expected_content_hash": "",
                    "actual_content_hash": skill.version if skill else "",
                    "reason": "missing_pinned_content_hash",
                }
            )
            continue
        if not pin.get("files"):
            mismatches.append(
                {
                    "skill_id": skill_name,
                    "expected_content_hash": expected,
                    "actual_content_hash": skill.version if skill else "",
                    "reason": "missing_pinned_snapshot",
                }
            )
            continue
        if expected and (skill is None or skill.version != expected):
            mismatches.append(
                {
                    "skill_id": skill_name,
                    "expected_content_hash": expected,
                    "actual_content_hash": skill.version if skill else "",
                }
            )
            continue
    return selected, mismatches


def pin_manifests_for_result(
    pins: dict[str, dict[str, Any]],
    allowed_skill_names: list[str],
) -> list[dict[str, Any]]:
    manifests: list[dict[str, Any]] = []
    for skill_name in allowed_skill_names:
        pin = pins.get(skill_name)
        if not pin:
            continue
        manifest = {key: value for key, value in pin.items() if key != "files"}
        version = str(manifest.get("version") or pin.get("content_hash") or "")
        content_hash = str(manifest.get("content_hash") or pin.get("version") or version)
        manifest["version"] = version
        manifest["content_hash"] = content_hash
        manifest.setdefault("dependency_ids", [])
        manifest["allowed"] = bool(manifest.get("allowed", True))
        manifest["staged"] = False
        manifest["used"] = False
        manifests.append(manifest)
    return manifests


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
        manifests.append(
            {
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
        )
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
