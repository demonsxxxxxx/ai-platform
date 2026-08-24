from __future__ import annotations

import base64
import binascii
from pathlib import Path
import shutil
from typing import Any, Callable


MAX_SKILL_SNAPSHOT_FILE_BYTES = 8 * 1024 * 1024
MAX_SKILL_SNAPSHOT_TOTAL_BYTES = 16 * 1024 * 1024


class PinnedSkillMismatch(ValueError):
    def __init__(self, message: str, *, actual_content_hash: str = "") -> None:
        super().__init__(message)
        self.actual_content_hash = actual_content_hash


def pinned_skill_manifests(skill_manifests: object) -> dict[str, dict[str, Any]]:
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
    *,
    path_guard: Callable[[str | Path, str | Path, str], None],
    skill_factory: Callable[..., Any],
    content_hash_reader: Callable[[Path], str],
) -> Any:
    if Path(skill_name).name != skill_name:
        raise ValueError(f"invalid pinned skill name: {skill_name}")
    expected_hash = str(pin.get("content_hash") or pin.get("version") or "")
    if not expected_hash:
        raise ValueError(f"pinned skill missing content hash: {skill_name}")
    target = snapshot_root / skill_name
    workspace_root = snapshot_root.parents[1]
    path_guard(workspace_root, target, "pinned skill path must stay inside the run workspace")
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=True)
    path_guard(workspace_root, target, "pinned skill path must stay inside the run workspace")
    total_bytes = 0
    for item in pin.get("files") or []:
        if not isinstance(item, dict):
            raise ValueError(f"invalid pinned skill file entry: {skill_name}")  # noqa: TRY004
        relative_path = str(item.get("relative_path") or "")
        if not relative_path or Path(relative_path).is_absolute() or ".." in Path(relative_path).parts:
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
        path_guard(target, output, f"invalid pinned skill file path: {skill_name}")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(content)
    if not (target / "SKILL.md").is_file():
        raise ValueError(f"pinned skill missing SKILL.md: {skill_name}")
    actual_hash = content_hash_reader(target)
    if actual_hash != expected_hash:
        shutil.rmtree(target, ignore_errors=True)
        raise PinnedSkillMismatch(
            f"pinned skill content hash mismatch: {skill_name}",
            actual_content_hash=actual_hash,
        )
    return skill_factory(
        name=skill_name,
        description=str(pin.get("description") or ""),
        path=target,
        version=expected_hash,
        source=pin.get("source") if isinstance(pin.get("source"), dict) else {},
        entry={"kind": "run-snapshot", "path": str(target)},
    )


def select_pinned_skills(
    skills: list[Any],
    allowed_skill_names: list[str],
    pins: dict[str, dict[str, Any]],
    snapshot_root: Path,
    *,
    path_guard: Callable[[str | Path, str | Path, str], None],
    skill_factory: Callable[..., Any],
    content_hash_reader: Callable[[Path], str],
) -> tuple[list[Any], list[dict[str, str]]]:
    selected: list[Any] = []
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
                selected.append(
                    materialize_pinned_skill(
                        skill_name,
                        pin,
                        snapshot_root,
                        path_guard=path_guard,
                        skill_factory=skill_factory,
                        content_hash_reader=content_hash_reader,
                    )
                )
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
