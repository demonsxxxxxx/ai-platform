"""Filesystem integration for the pinned Claude CLI's native tool results."""

from __future__ import annotations

import os
from pathlib import Path, PurePosixPath
import re
import stat


CLAUDE_PROJECT_DIRECTORY_NAME = "ai-platform"


def claude_workspace_env(cwd: Path) -> dict[str, str]:
    home = cwd / ".home"
    return {
        "AI_PLATFORM_WORK_DIR": str(cwd),
        "HOME": str(home), "USERPROFILE": str(home),
        "CLAUDE_CONFIG_DIR": str(cwd / ".claude-config"),
        "CLAUDE_CODE_PROJECT_DIR_NAME": CLAUDE_PROJECT_DIRECTORY_NAME,
        "XDG_CONFIG_HOME": str(home / ".config"),
        "XDG_CACHE_HOME": str(home / ".cache"),
        "XDG_DATA_HOME": str(home / ".local" / "share"),
        "TMPDIR": str(cwd / ".tmp"), "TMP": str(cwd / ".tmp"), "TEMP": str(cwd / ".tmp"),
    }


def prepare_claude_tool_results(workspace: Path, sdk_session_id: str) -> PurePosixPath:
    """Prepare only a bound session's output directory, never follow old links."""
    if not isinstance(sdk_session_id, str) or re.fullmatch(r"[A-Za-z0-9_-]{1,128}", sdk_session_id) is None:
        raise ValueError("native_tool_results_session_invalid")
    relative = PurePosixPath(".claude-config", "projects", CLAUDE_PROJECT_DIRECTORY_NAME,
                             sdk_session_id, "tool-results")
    owner = workspace.stat(follow_symlinks=False)
    if not stat.S_ISDIR(owner.st_mode):
        raise ValueError("native_tool_results_workspace_invalid")
    current = workspace
    for part in relative.parts:
        current = current / part
        try:
            current.mkdir(mode=0o755)
        except FileExistsError:
            pass
        node = current.stat(follow_symlinks=False)
        if not stat.S_ISDIR(node.st_mode):
            raise ValueError("native_tool_results_path_invalid")
        if os.name == "posix" and (node.st_uid, node.st_gid) != (owner.st_uid, owner.st_gid):
            os.chown(current, owner.st_uid, owner.st_gid, follow_symlinks=False)
    return relative
