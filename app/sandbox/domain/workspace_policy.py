"""Domain policy for writable and collectible paths in a run workspace."""

from __future__ import annotations

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
