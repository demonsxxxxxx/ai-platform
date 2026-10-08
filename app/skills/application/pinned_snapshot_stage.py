"""Stage and verify the immutable files pinned by a Run."""

import base64
import binascii
from collections.abc import Callable
from pathlib import Path
from typing import Any

from app.skills.domain.pinned_snapshot import (
    PinnedSkillMismatch,
    validate_pinned_skill_relative_path,
)


def stage_pinned_skill_snapshot(
    skill_name: str,
    pin: dict[str, Any],
    snapshot_root: Path,
    *,
    prepare_target: Callable[[Path, Path], None],
    write_file: Callable[[Path, Path, bytes, str], None],
    has_skill_markdown: Callable[[Path], bool],
    content_hash: Callable[[Path], str],
    remove_invalid_target: Callable[[Path], None],
    max_file_bytes: int,
    max_total_bytes: int,
) -> tuple[Path, str]:
    if Path(skill_name).name != skill_name:
        raise ValueError(f"invalid pinned skill name: {skill_name}")
    expected_hash = str(pin.get("content_hash") or pin.get("version") or "")
    if not expected_hash:
        raise ValueError(f"pinned skill missing content hash: {skill_name}")
    target = snapshot_root / skill_name
    prepare_target(snapshot_root.parent, target)
    total_bytes = 0
    for item in pin.get("files") or []:
        if not isinstance(item, dict):
            raise ValueError(f"invalid pinned skill file entry: {skill_name}")  # noqa: TRY004
        relative_path = str(item.get("relative_path") or "")
        validate_pinned_skill_relative_path(relative_path, skill_name=skill_name)
        content = base64.b64decode(str(item.get("content_base64") or ""), validate=True)
        if "size_bytes" not in item:
            raise ValueError(f"pinned skill file missing size_bytes: {skill_name}")
        if int(item["size_bytes"]) != len(content):
            raise ValueError(f"pinned skill file size mismatch: {skill_name}")
        if len(content) > max_file_bytes:
            raise ValueError(f"pinned skill file too large: {skill_name}")
        total_bytes += len(content)
        if total_bytes > max_total_bytes:
            raise ValueError(f"pinned skill snapshot too large: {skill_name}")
        write_file(target, target / relative_path, content, skill_name)
    if not has_skill_markdown(target):
        raise ValueError(f"pinned skill missing SKILL.md: {skill_name}")
    actual_hash = content_hash(target)
    if actual_hash != expected_hash:
        remove_invalid_target(target)
        raise PinnedSkillMismatch(
            f"pinned skill content hash mismatch: {skill_name}",
            actual_content_hash=actual_hash,
        )
    return target, expected_hash


def select_pinned_skill_snapshots(
    skills: list[Any],
    allowed_skill_names: list[str],
    pins: dict[str, dict[str, Any]],
    snapshot_root: Path,
    *,
    materialize: Callable[[str, dict[str, Any], Path], Any],
) -> tuple[list[Any], list[dict[str, Any]]]:
    selected: list[Any] = []
    mismatches: list[dict[str, Any]] = []
    by_name = {skill.name: skill for skill in skills}
    for skill_name in allowed_skill_names:
        skill = by_name.get(skill_name)
        pin = pins.get(skill_name)
        if not pin:
            mismatches.append({
                "skill_id": skill_name,
                "expected_content_hash": "",
                "actual_content_hash": skill.version if skill else "",
                "reason": "missing_pinned_manifest",
            })
            continue
        expected = str(pin.get("content_hash") or pin.get("version") or "")
        if pin.get("files"):
            try:
                selected.append(materialize(skill_name, pin, snapshot_root))
            except PinnedSkillMismatch as exc:
                mismatches.append({
                    "skill_id": skill_name,
                    "expected_content_hash": expected,
                    "actual_content_hash": exc.actual_content_hash,
                    "reason": str(exc),
                })
            except (binascii.Error, ValueError) as exc:
                mismatches.append({
                    "skill_id": skill_name,
                    "expected_content_hash": expected,
                    "actual_content_hash": "",
                    "reason": str(exc),
                })
            continue
        mismatches.append({
            "skill_id": skill_name,
            "expected_content_hash": expected,
            "actual_content_hash": skill.version if skill else "",
            "reason": "missing_pinned_snapshot" if expected else "missing_pinned_content_hash",
        })
    return selected, mismatches
