from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

RUNTIME_UID = 10001
RUNTIME_GID = 10001
RUNTIME_USER = "ai-platform"
RUNTIME_WORKSPACE_ROOT = Path("/runtime-workspaces")
_SENTINEL_NAME = ".ai-platform-runtime-write-probe"
_SENTINEL_PAYLOAD = b"ai-platform-runtime-workspace-v1\n"
_NAMESPACE_LAYOUT = (
    "tenants",
    None,
    "workspaces",
    None,
    "users",
    None,
    "sessions",
    None,
    "runs",
    None,
    "attempts",
    None,
)


class WorkspacePermissionError(RuntimeError):
    """Raised when the runtime workspace namespace cannot be prepared safely."""


def _secure_open_flags(*, directory: bool = False) -> int:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    if directory:
        flags |= getattr(os, "O_DIRECTORY", 0)
    return flags


def _validate_namespace_directory_identity(
    relative_path: str,
    *,
    device: int,
    uid: int,
    gid: int,
    root_device: int,
) -> None:
    if device != root_device:
        raise WorkspacePermissionError(f"workspace namespace crosses filesystem boundary: {relative_path}")
    if (uid, gid) not in {(0, 0), (RUNTIME_UID, RUNTIME_GID)}:
        raise WorkspacePermissionError(f"foreign workspace namespace owner: {relative_path}")


def _prepare_namespace_directory(
    descriptor: int,
    relative_path: str,
    *,
    root_device: int,
    require_owner_write: bool = False,
) -> None:
    current = os.fstat(descriptor)
    if not stat.S_ISDIR(current.st_mode):
        raise WorkspacePermissionError(f"workspace namespace entry is not a directory: {relative_path}")
    _validate_namespace_directory_identity(
        relative_path,
        device=int(current.st_dev),
        uid=int(current.st_uid),
        gid=int(current.st_gid),
        root_device=root_device,
    )
    if require_owner_write and not current.st_mode & stat.S_IWUSR:
        raise WorkspacePermissionError("runtime workspace root is not owner-writable")

    if (current.st_uid, current.st_gid) != (RUNTIME_UID, RUNTIME_GID):
        try:
            os.fchown(descriptor, RUNTIME_UID, RUNTIME_GID)
        except OSError as exc:
            raise WorkspacePermissionError(f"workspace namespace ownership migration failed: {relative_path}") from exc
    try:
        # Keep platform path components private and make them traversable by the
        # runtime identity. Attempt workspace contents are intentionally outside
        # this initializer's scope.
        os.fchmod(descriptor, 0o700)
    except OSError as exc:
        raise WorkspacePermissionError(f"workspace namespace permissions cannot be prepared: {relative_path}") from exc

    updated = os.fstat(descriptor)
    if (updated.st_dev, updated.st_uid, updated.st_gid) != (
        root_device,
        RUNTIME_UID,
        RUNTIME_GID,
    ):
        raise WorkspacePermissionError(f"workspace namespace identity changed during initialization: {relative_path}")


def _open_namespace_child(
    parent_fd: int,
    name: str,
    relative_path: str,
    *,
    root_device: int,
    required: bool,
) -> int | None:
    try:
        before = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise WorkspacePermissionError(f"workspace namespace entry cannot be inspected: {relative_path}") from exc

    if stat.S_ISLNK(before.st_mode):
        raise WorkspacePermissionError(f"workspace namespace entry is a symbolic link: {relative_path}")
    if not stat.S_ISDIR(before.st_mode):
        if required:
            raise WorkspacePermissionError(f"workspace namespace entry is not a directory: {relative_path}")
        return None

    try:
        descriptor = os.open(name, _secure_open_flags(directory=True), dir_fd=parent_fd)
    except OSError as exc:
        raise WorkspacePermissionError(f"workspace namespace directory cannot be opened safely: {relative_path}") from exc
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISDIR(opened.st_mode) or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise WorkspacePermissionError(f"workspace namespace entry changed during initialization: {relative_path}")
        _prepare_namespace_directory(descriptor, relative_path, root_device=root_device)
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _walk_workspace_namespace(directory_fd: int, relative_root: str, layout_index: int, root_device: int) -> None:
    if layout_index == len(_NAMESPACE_LAYOUT):
        return

    expected = _NAMESPACE_LAYOUT[layout_index]
    if expected is not None:
        relative_path = expected if relative_root == "." else f"{relative_root}/{expected}"
        child_fd = _open_namespace_child(
            directory_fd,
            expected,
            relative_path,
            root_device=root_device,
            required=True,
        )
        if child_fd is None:
            return
        try:
            _walk_workspace_namespace(child_fd, relative_path, layout_index + 1, root_device)
        finally:
            os.close(child_fd)
        return

    try:
        with os.scandir(directory_fd) as entries:
            names = sorted(entry.name for entry in entries)
    except OSError as exc:
        raise WorkspacePermissionError(f"workspace namespace directory cannot be read: {relative_root}") from exc

    for name in names:
        if name in {".", ".."} or "/" in name or "\\" in name:
            raise WorkspacePermissionError("workspace namespace entry name is invalid")
        relative_path = name if relative_root == "." else f"{relative_root}/{name}"
        child_fd = _open_namespace_child(
            directory_fd,
            name,
            relative_path,
            root_device=root_device,
            required=False,
        )
        if child_fd is None:
            continue
        try:
            _walk_workspace_namespace(child_fd, relative_path, layout_index + 1, root_device)
        finally:
            os.close(child_fd)


def _open_runtime_workspace_root(root: Path) -> tuple[int, int]:
    if os.name != "posix" or not getattr(os, "O_NOFOLLOW", 0) or not getattr(os, "O_DIRECTORY", 0):
        raise WorkspacePermissionError("secure workspace initialization requires POSIX no-follow filesystem support")
    try:
        root_fd = os.open(root, _secure_open_flags(directory=True))
    except OSError as exc:
        raise WorkspacePermissionError("runtime workspace root is unavailable") from exc

    try:
        root_stat = os.fstat(root_fd)
        if not stat.S_ISDIR(root_stat.st_mode):
            raise WorkspacePermissionError("runtime workspace root is unavailable")
        root_device = int(root_stat.st_dev)
        _prepare_namespace_directory(
            root_fd,
            ".",
            root_device=root_device,
            require_owner_write=True,
        )
        return root_fd, root_device
    except BaseException:
        os.close(root_fd)
        raise


def _drop_runtime_privileges() -> None:
    try:
        os.setgroups([])
        os.setgid(RUNTIME_GID)
        os.setuid(RUNTIME_UID)
    except (AttributeError, OSError) as exc:
        raise WorkspacePermissionError("runtime identity drop failed") from exc
    if os.geteuid() != RUNTIME_UID or os.getegid() != RUNTIME_GID:
        raise WorkspacePermissionError("runtime identity drop did not take effect")


def _probe_runtime_workspace(root_fd: int) -> None:
    probe_fd: int | None = None
    created = False
    try:
        probe_fd = os.open(
            _SENTINEL_NAME,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=root_fd,
        )
        created = True
        os.write(probe_fd, _SENTINEL_PAYLOAD)
        os.close(probe_fd)
        probe_fd = None
        probe_fd = os.open(
            _SENTINEL_NAME,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=root_fd,
        )
        if os.read(probe_fd, len(_SENTINEL_PAYLOAD) + 1) != _SENTINEL_PAYLOAD:
            raise WorkspacePermissionError("runtime workspace sentinel readback mismatch")
        os.close(probe_fd)
        probe_fd = None
        os.unlink(_SENTINEL_NAME, dir_fd=root_fd)
        created = False
    except OSError as exc:
        raise WorkspacePermissionError("runtime workspace is not writable by 10001:10001") from exc
    finally:
        if probe_fd is not None:
            os.close(probe_fd)
        if created:
            try:
                os.unlink(_SENTINEL_NAME, dir_fd=root_fd)
            except OSError:
                pass


def initialize_runtime_workspace() -> None:
    """Prepare only the runtime root and platform namespace directory chain."""

    root_fd, root_device = _open_runtime_workspace_root(RUNTIME_WORKSPACE_ROOT)
    try:
        _walk_workspace_namespace(root_fd, ".", 0, root_device)
        _drop_runtime_privileges()
        _probe_runtime_workspace(root_fd)
    finally:
        os.close(root_fd)


def main() -> int:
    """Run the fixed one-shot compose workspace initializer."""

    if len(sys.argv) != 1:
        print("workspace initializer accepts no arguments", file=sys.stderr)
        return 64
    try:
        initialize_runtime_workspace()
    except WorkspacePermissionError as exc:
        print(f"workspace initialization failed: {exc}", file=sys.stderr)
        return 65
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
