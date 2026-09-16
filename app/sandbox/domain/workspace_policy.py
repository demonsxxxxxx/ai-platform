"""Domain policy for writable and collectible paths in a run workspace."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import PurePosixPath
from typing import Any

PLATFORM_CLAUDE_INSTRUCTIONS_FILENAME = "CLAUDE.md"

_MUTATION_PROTECTED_ROOTS = frozenset(
    {
        ".ai-platform",
        ".claude",
        ".claude-config",
        ".home",
        ".pins",
        ".tmp",
        "inputs",
    }
)
_MUTATION_PROTECTED_ROOT_FILES = frozenset(
    {
        PLATFORM_CLAUDE_INSTRUCTIONS_FILENAME.casefold(),
        "claude.local.md",
        ".ai-platform-opensandbox-lease.json",
    }
)

_COLLECTION_PRIVATE_DIRECTORIES = frozenset(
    {
        *_MUTATION_PROTECTED_ROOTS,
        ".native-skill-tmp",
        "logs",
        "runtime",
        "_audit",
        "_debug",
    }
)
_COLLECTION_PRIVATE_FILES = frozenset(
    {
        *_MUTATION_PROTECTED_ROOT_FILES,
        "run-state.json",
        "step-event.json",
        "step-response.json",
    }
)


def workspace_mutation_allowed(relative_path: str | PurePosixPath) -> bool:
    """Allow Skill writes throughout the workspace except platform-owned roots."""
    path = PurePosixPath(relative_path)
    if not path.parts or path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        return False
    lowered = tuple(part.casefold() for part in path.parts)
    if lowered[0] in _MUTATION_PROTECTED_ROOTS:
        return False
    return not (len(lowered) == 1 and lowered[0] in _MUTATION_PROTECTED_ROOT_FILES)


def _opensandbox_entry_value(entry: Any, name: str) -> Any:
    if isinstance(entry, dict):
        if name == "entry_type":
            return entry.get("entry_type", entry.get("type"))
        return entry.get(name)
    if name == "entry_type":
        return getattr(entry, "entry_type", getattr(entry, "type", None))
    return getattr(entry, name, None)


def opensandbox_collection_entry(
    entry: Any,
    workspace_container_path: str,
    *,
    safe_relative_path: Callable[[str], str],
) -> tuple[str, str | None, int]:
    """Validate one SDK entry and omit non-collectible filesystem types."""

    raw_path = _opensandbox_entry_value(entry, "path")
    remote_root = workspace_container_path.rstrip("/")
    if not isinstance(raw_path, str) or "\x00" in raw_path or not raw_path.startswith(f"{remote_root}/"):
        raise ValueError("OpenSandbox workspace collection path is invalid")
    relative_path = safe_relative_path(raw_path[len(remote_root) + 1 :])
    entry_type = str(_opensandbox_entry_value(entry, "entry_type") or "").lower()
    if entry_type in {"symlink", "other"}:
        collectible_type = None
    elif entry_type in {"file", "directory"}:
        collectible_type = entry_type
    else:
        raise ValueError("OpenSandbox workspace collection entry is invalid")
    try:
        size = int(_opensandbox_entry_value(entry, "size"))
    except (TypeError, ValueError) as exc:
        raise ValueError("OpenSandbox workspace collection entry is invalid") from exc
    if size < 0:
        raise ValueError("OpenSandbox workspace collection entry is invalid")
    return relative_path, collectible_type, size


def opensandbox_listing_matches_file(entry: Any, expected_size: int) -> bool:
    """Match a readback entry to one listed regular file."""

    entry_type = _opensandbox_entry_value(entry, "entry_type")
    try:
        size = int(_opensandbox_entry_value(entry, "size"))
    except (TypeError, ValueError):
        return False
    return str(entry_type or "").lower() == "file" and size == expected_size


def workspace_collection_directory_allowed(relative_path: str | PurePosixPath) -> bool:
    """Return whether a remote workspace directory may contain user artifacts."""
    path = PurePosixPath(relative_path)
    if not path.parts or path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        return False
    return not any(part.casefold() in _COLLECTION_PRIVATE_DIRECTORIES for part in path.parts)


def workspace_collection_file_allowed(relative_path: str | PurePosixPath) -> bool:
    """Return whether a workspace file is eligible for artifact collection."""
    path = PurePosixPath(relative_path)
    if not path.parts or path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        return False
    lowered = tuple(part.casefold() for part in path.parts)
    if any(part in _COLLECTION_PRIVATE_DIRECTORIES for part in lowered[:-1]):
        return False
    return lowered[-1] not in _COLLECTION_PRIVATE_FILES
