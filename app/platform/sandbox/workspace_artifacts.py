"""Rules for selecting user files from a platform run workspace."""

from __future__ import annotations

from pathlib import PurePosixPath

from app.sandbox.api import (
    workspace_collection_directory_allowed,
    workspace_collection_file_allowed,
)


def workspace_directory_allowed(relative_path: str | PurePosixPath) -> bool:
    """Return whether a relative workspace directory may be traversed."""
    return workspace_collection_directory_allowed(relative_path)


def workspace_file_allowed(relative_path: str | PurePosixPath) -> bool:
    """Return whether a relative workspace file is eligible for collection."""
    return workspace_collection_file_allowed(relative_path)
