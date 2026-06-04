from pathlib import Path


def ensure_path_inside(root: str | Path, path: str | Path, message: str) -> None:
    root_path = Path(root)
    target_path = Path(path)
    if root_path.exists() and root_path.is_symlink():
        raise ValueError(message)
    root_resolved = root_path.resolve(strict=False)
    target_resolved = target_path.resolve(strict=False)
    try:
        target_resolved.relative_to(root_resolved)
    except ValueError as exc:
        raise ValueError(message) from exc


def ensure_creatable_inside(root: str | Path, path: str | Path, message: str) -> None:
    root_path = Path(root)
    target_path = Path(path)
    ensure_path_inside(root_path, target_path, message)
    current = root_path
    try:
        relative_parts = target_path.relative_to(root_path).parts
    except ValueError as exc:
        raise ValueError(message) from exc
    for part in relative_parts:
        current = current / part
        if current.exists() and current.is_symlink():
            raise ValueError(message)
        if current.exists():
            ensure_path_inside(root_path, current, message)
