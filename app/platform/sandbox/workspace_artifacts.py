"""Rules for selecting user files from a platform run workspace."""

from __future__ import annotations

from pathlib import PurePosixPath

_WORKSPACE_INTERNAL_DIRS = frozenset(
    {
        ".ai-platform",
        ".claude",
        ".claude-config",
        ".home",
        ".pins",
        ".tmp",
        "inputs",
        "logs",
        "runtime",
        "_audit",
        "_debug",
        "artifacts",
        "tasks",
    }
)
_WORKSPACE_INTERNAL_FILES = frozenset(
    {"run-state.json", "step-event.json", "step-response.json"}
)


def workspace_directory_allowed(relative_path: str | PurePosixPath) -> bool:
    """Return whether a relative workspace directory may be traversed."""
    path = PurePosixPath(relative_path)
    return bool(path.parts) and path.parts[0] != "review" and not any(
        part in _WORKSPACE_INTERNAL_DIRS for part in path.parts
    )


def workspace_file_allowed(relative_path: str | PurePosixPath) -> bool:
    """Return whether a relative workspace file is eligible for collection."""
    path = PurePosixPath(relative_path)
    if (
        not path.parts
        or path.parts[0] == "review"
        or any(part in _WORKSPACE_INTERNAL_DIRS for part in path.parts[:-1])
    ):
        return False
    if path.name in _WORKSPACE_INTERNAL_FILES:
        return False
    # Preserve the existing outputs contract: only delivery subdirectories
    # are public; other top-level workspace files may be collected directly.
    return path.parts[0] != "outputs" or "delivery" in path.parts[1:-1]
