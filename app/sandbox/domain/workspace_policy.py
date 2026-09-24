"""Domain policy for writable and collectible paths in a run workspace."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import PurePosixPath

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

_READ_PRIVATE_FILES = frozenset(
    name
    for name in _COLLECTION_PRIVATE_FILES
    if name != PLATFORM_CLAUDE_INSTRUCTIONS_FILENAME.casefold()
)
_READ_PRIVATE_DIRECTORIES = frozenset(
    name for name in _COLLECTION_PRIVATE_DIRECTORIES if name not in {".claude", "inputs"}
)


def workspace_read_name_private(name: object) -> bool:
    """Return whether one path component is private to the sandbox runtime."""

    if not isinstance(name, str) or not name:
        return True
    lowered = name.casefold()
    return lowered in _READ_PRIVATE_DIRECTORIES or lowered in _READ_PRIVATE_FILES


def workspace_read_allowed(relative_path: str | PurePosixPath) -> bool:
    """Keep platform-private workspace entries out of SDK read/search results."""

    path = PurePosixPath(relative_path)
    if path.is_absolute() or any(part in {"", ".."} for part in path.parts):
        return False
    if not path.parts:
        return True
    lowered = tuple(part.casefold() for part in path.parts)
    if lowered[0] == ".claude":
        if len(lowered) < 2 or lowered[1] != "skills":
            return False
        searchable_parts = lowered[2:]
    else:
        searchable_parts = lowered
    if any(workspace_read_name_private(part) for part in searchable_parts):
        return False
    return not workspace_read_name_private(lowered[-1])


def workspace_mutation_allowed(relative_path: str | PurePosixPath) -> bool:
    """Allow Skill writes throughout the workspace except platform-owned roots."""
    path = PurePosixPath(relative_path)
    if not path.parts or path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        return False
    lowered = tuple(part.casefold() for part in path.parts)
    if lowered[0] in _MUTATION_PROTECTED_ROOTS:
        return False
    return not (len(lowered) == 1 and lowered[0] in _MUTATION_PROTECTED_ROOT_FILES)


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


def workspace_delivery_file_allowed(
    relative_path: str | PurePosixPath,
    *,
    allowed_skill_names: Iterable[str] = (),
) -> bool:
    """Allow an explicitly declared deliverable without exposing Skill sources.

    Ordinary workspace outputs keep the existing collection policy.  A staged
    Skill may additionally declare a file below its own ``output`` directory;
    every other path below ``.claude`` remains private.
    """

    path = PurePosixPath(relative_path)
    if workspace_collection_file_allowed(path):
        return True
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        return False
    parts = path.parts
    if len(parts) < 5 or tuple(part.casefold() for part in parts[:2]) != (
        ".claude",
        "skills",
    ):
        return False
    allowed = {
        name
        for name in allowed_skill_names
        if isinstance(name, str) and name
    }
    if parts[2] not in allowed or parts[3].casefold() != "output":
        return False
    descendants = tuple(part.casefold() for part in parts[4:])
    if any(part in _COLLECTION_PRIVATE_DIRECTORIES for part in descendants[:-1]):
        return False
    return descendants[-1] not in _COLLECTION_PRIVATE_FILES
