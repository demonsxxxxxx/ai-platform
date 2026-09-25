from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence

from app.sandbox.domain.workspace_policy import workspace_delivery_file_allowed


def opensandbox_delivery_files(
    response_files: Sequence[str],
    *,
    allowed_skill_names: Iterable[str],
    safe_relative_path: Callable[[str], str],
    max_files: int,
) -> list[str]:
    if isinstance(response_files, (str, bytes)):
        raise ValueError("OpenSandbox workspace collection selection is invalid")
    declared_paths = list(response_files)
    if len(declared_paths) > max_files:
        raise ValueError("workspace artifacts exceed the file count limit")

    selected: set[str] = set()
    normalized_paths: list[str] = []
    for raw_path in declared_paths:
        if not isinstance(raw_path, str):
            raise ValueError("OpenSandbox workspace collection selection is invalid")
        relative_path = safe_relative_path(raw_path)
        if relative_path in selected or not workspace_delivery_file_allowed(
            relative_path,
            allowed_skill_names=allowed_skill_names,
        ):
            raise ValueError("OpenSandbox workspace collection selection is invalid")
        selected.add(relative_path)
        normalized_paths.append(relative_path)
    return normalized_paths
