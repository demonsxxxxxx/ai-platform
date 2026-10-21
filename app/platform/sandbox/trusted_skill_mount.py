"""Filesystem admission for an already-authorized staged Skill mount.

The provider owns authorization and lease lifecycle. This adapter owns path
containment, removal of host project settings, and stable directory identity.
"""
from __future__ import annotations

import hashlib
import os
import stat
from dataclasses import dataclass
from pathlib import Path

from app.platform.sandbox.errors import ContainerStartFailedError


_CLAUDE_PROJECT_SETTING_NAMES = ("settings.json", "settings.local.json")


@dataclass(frozen=True)
class TrustedSkillMount:
    host_path: Path
    container_path: str
    fingerprint: str


def _real_directory(path: Path, *, error: str) -> tuple[Path, os.stat_result]:
    try:
        node = path.lstat()
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError, ValueError) as exc:
        raise ContainerStartFailedError(error) from exc
    if stat.S_ISLNK(node.st_mode) or not stat.S_ISDIR(node.st_mode):
        raise ContainerStartFailedError(error)
    return resolved, node


def _scrub_host_project_settings(claude_dir: Path) -> None:
    for name in _CLAUDE_PROJECT_SETTING_NAMES:
        setting = claude_dir / name
        try:
            node = setting.lstat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise ContainerStartFailedError("staged Skill settings cannot be inspected") from exc
        if not (stat.S_ISREG(node.st_mode) or stat.S_ISLNK(node.st_mode)):
            raise ContainerStartFailedError("staged Skill settings path is invalid")
        try:
            setting.unlink()
        except OSError as exc:
            raise ContainerStartFailedError("staged Skill settings cannot be scrubbed") from exc
        try:
            setting.lstat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise ContainerStartFailedError("staged Skill settings cannot be validated") from exc
        raise ContainerStartFailedError("staged Skill settings cannot be scrubbed")


def prepare_trusted_skill_mount(
    *,
    workspace_host_path: str,
    host_root: str,
    workspace_container_path: str,
) -> TrustedSkillMount:
    """Derive, scrub, and validate the staged Skill mount only from the trusted lease."""

    workspace_path = Path(workspace_host_path)
    workspace_root, workspace_node = _real_directory(
        workspace_path,
        error="staged Skill workspace is invalid",
    )
    try:
        trusted_host_root = Path(host_root).resolve(strict=True)
        workspace_root.relative_to(trusted_host_root)
    except (OSError, RuntimeError, ValueError) as exc:
        raise ContainerStartFailedError("staged Skill workspace escapes trusted run root") from exc

    claude_path = workspace_path / ".claude"
    claude_root, claude_node = _real_directory(
        claude_path,
        error="staged Skill .claude directory is invalid",
    )
    skills_root, skills_node = _real_directory(
        claude_path / "skills",
        error="staged Skill directory is invalid",
    )
    try:
        claude_root.relative_to(workspace_root)
        skills_root.relative_to(claude_root)
    except ValueError as exc:
        raise ContainerStartFailedError("staged Skill directory escapes trusted workspace") from exc

    _scrub_host_project_settings(claude_path)
    final_claude_root, final_claude_node = _real_directory(
        claude_path,
        error="staged Skill .claude directory changed during validation",
    )
    final_skills_root, final_skills_node = _real_directory(
        claude_path / "skills",
        error="staged Skill directory changed during validation",
    )
    if (
        final_claude_root != claude_root
        or final_skills_root != skills_root
        or (final_claude_node.st_dev, final_claude_node.st_ino)
        != (claude_node.st_dev, claude_node.st_ino)
        or (final_skills_node.st_dev, final_skills_node.st_ino)
        != (skills_node.st_dev, skills_node.st_ino)
    ):
        raise ContainerStartFailedError("staged Skill directory changed during validation")

    fingerprint_payload = "\0".join(
        str(value)
        for value in (
            workspace_node.st_dev,
            workspace_node.st_ino,
            claude_node.st_dev,
            claude_node.st_ino,
            skills_node.st_dev,
            skills_node.st_ino,
        )
    )
    return TrustedSkillMount(
        host_path=claude_root,
        container_path=f"{workspace_container_path.rstrip('/')}/.claude",
        fingerprint=hashlib.sha256(fingerprint_payload.encode("ascii")).hexdigest(),
    )
