from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

from app.skills.dependencies import skill_dependency_ids, with_skill_dependencies
from app.skills.registry import BuiltinSkill, iter_skill_files

MAX_SKILL_SNAPSHOT_FILE_BYTES = 8 * 1024 * 1024
MAX_SKILL_SNAPSHOT_TOTAL_BYTES = 16 * 1024 * 1024


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
        manifests.append(
            {
                "skill_id": skill.name,
                "description": skill.description,
                "version": skill.version,
                "content_hash": skill.version,
                "source": skill.source,
                "files": _snapshot_files(skill.path),
                "dependency_ids": skill_dependency_ids(skill.name, selected_set),
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
    if str(skill_version.get("status") or "") != "active":
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
    return {
        "skill_id": str(skill_version.get("skill_id") or ""),
        "description": str(skill_version.get("description") or ""),
        "version": version,
        "content_hash": content_hash,
        "source": manifest_source,
        "files": files,
        "dependency_ids": _string_list(skill_version.get("dependency_ids")),
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
