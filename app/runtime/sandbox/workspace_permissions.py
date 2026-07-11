from __future__ import annotations

import os
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


RUNTIME_UID = 10001
RUNTIME_GID = 10001
RUNTIME_USER = "ai-platform"
RUNTIME_WORKSPACE_ROOT = Path("/runtime-workspaces")
_SENTINEL_NAME = ".ai-platform-runtime-write-probe"
_SENTINEL_PAYLOAD = b"ai-platform-runtime-workspace-v1\n"


class WorkspacePermissionError(RuntimeError):
    """Raised when the fixed runtime workspace cannot be migrated safely."""


@dataclass(frozen=True)
class WorkspaceNode:
    """Immutable filesystem metadata captured before workspace migration."""

    relative_path: str
    uid: int
    gid: int
    mode: int
    device: int
    inode: int = 0
    link_count: int = 1


@dataclass(frozen=True)
class _OpenWorkspaceNode:
    node: WorkspaceNode
    parent_fd: int | None
    name: str | None
    fd: int | None = None


def validate_workspace_snapshot(*, root_device: int, nodes: Iterable[WorkspaceNode]) -> None:
    """Validate a complete no-follow workspace snapshot before any ownership mutation."""

    snapshot = list(nodes)
    if not snapshot or snapshot[0].relative_path != ".":
        raise WorkspacePermissionError("workspace root snapshot is missing")
    for node in snapshot:
        if node.device != root_device:
            raise WorkspacePermissionError(f"workspace entry crosses filesystem boundary: {node.relative_path}")
        if (node.uid, node.gid) not in {(0, 0), (RUNTIME_UID, RUNTIME_GID)}:
            raise WorkspacePermissionError(f"foreign workspace owner: {node.relative_path}")
        if not (stat.S_ISDIR(node.mode) or stat.S_ISREG(node.mode)):
            raise WorkspacePermissionError(f"unsupported workspace entry type: {node.relative_path}")
        if stat.S_ISREG(node.mode) and node.link_count != 1:
            raise WorkspacePermissionError(f"workspace hard links are not allowed: {node.relative_path}")
        if node.mode & (stat.S_ISUID | stat.S_ISGID | stat.S_ISVTX):
            raise WorkspacePermissionError(f"unsafe workspace mode: {node.relative_path}")
        if node.mode & (stat.S_IWGRP | stat.S_IWOTH):
            raise WorkspacePermissionError(f"unsafe workspace mode: {node.relative_path}")
        if not node.mode & stat.S_IWUSR:
            raise WorkspacePermissionError(f"workspace entry is not owner-writable: {node.relative_path}")
        if stat.S_ISDIR(node.mode) and not node.mode & stat.S_IXUSR:
            raise WorkspacePermissionError(f"workspace directory is not owner-searchable: {node.relative_path}")


def _node_from_stat(relative_path: str, stat_result: os.stat_result) -> WorkspaceNode:
    return WorkspaceNode(
        relative_path=relative_path,
        uid=int(stat_result.st_uid),
        gid=int(stat_result.st_gid),
        mode=int(stat_result.st_mode),
        device=int(stat_result.st_dev),
        inode=int(stat_result.st_ino),
        link_count=int(stat_result.st_nlink),
    )


def _secure_open_flags(*, directory: bool = False) -> int:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    if directory:
        flags |= getattr(os, "O_DIRECTORY", 0)
    return flags


def _capture_workspace_tree(root: Path) -> tuple[int, list[_OpenWorkspaceNode]]:
    if os.name != "posix" or not getattr(os, "O_NOFOLLOW", 0) or not getattr(os, "O_DIRECTORY", 0):
        raise WorkspacePermissionError("secure workspace initialization requires POSIX no-follow filesystem support")
    try:
        root_fd = os.open(root, _secure_open_flags(directory=True))
    except OSError as exc:
        raise WorkspacePermissionError("runtime workspace root is unavailable") from exc

    opened_fds: list[int] = [root_fd]
    root_stat = os.fstat(root_fd)
    handles = [_OpenWorkspaceNode(node=_node_from_stat(".", root_stat), parent_fd=None, name=None, fd=root_fd)]

    def walk(directory_fd: int, relative_root: str) -> None:
        try:
            entries = sorted(os.scandir(directory_fd), key=lambda item: item.name)
        except OSError as exc:
            raise WorkspacePermissionError(f"workspace directory cannot be read: {relative_root}") from exc
        for entry in entries:
            name = entry.name
            if name in {".", ".."} or "/" in name or "\\" in name:
                raise WorkspacePermissionError("workspace entry name is invalid")
            relative_path = name if relative_root == "." else f"{relative_root}/{name}"
            try:
                entry_stat = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            except OSError as exc:
                raise WorkspacePermissionError(f"workspace entry cannot be inspected: {relative_path}") from exc
            node = _node_from_stat(relative_path, entry_stat)
            if stat.S_ISDIR(node.mode):
                try:
                    child_fd = os.open(name, _secure_open_flags(directory=True), dir_fd=directory_fd)
                except OSError as exc:
                    raise WorkspacePermissionError(f"workspace directory cannot be opened safely: {relative_path}") from exc
                opened_fds.append(child_fd)
                verified = os.fstat(child_fd)
                if _node_from_stat(relative_path, verified) != node:
                    raise WorkspacePermissionError(f"workspace entry changed during validation: {relative_path}")
                handles.append(_OpenWorkspaceNode(node=node, parent_fd=directory_fd, name=name, fd=child_fd))
                walk(child_fd, relative_path)
            else:
                try:
                    file_fd = os.open(
                        name,
                        _secure_open_flags() | getattr(os, "O_NONBLOCK", 0),
                        dir_fd=directory_fd,
                    )
                except OSError as exc:
                    raise WorkspacePermissionError(f"workspace file cannot be opened safely: {relative_path}") from exc
                opened_fds.append(file_fd)
                verified = os.fstat(file_fd)
                if _node_from_stat(relative_path, verified) != node:
                    raise WorkspacePermissionError(f"workspace entry changed during validation: {relative_path}")
                handles.append(_OpenWorkspaceNode(node=node, parent_fd=directory_fd, name=name, fd=file_fd))

    try:
        walk(root_fd, ".")
        validate_workspace_snapshot(root_device=int(root_stat.st_dev), nodes=[handle.node for handle in handles])
        return root_fd, handles
    except BaseException:
        for opened_fd in reversed(opened_fds):
            os.close(opened_fd)
        raise


def _revalidate_node(handle: _OpenWorkspaceNode) -> os.stat_result:
    if handle.fd is None:
        raise WorkspacePermissionError(f"workspace inode handle is unavailable: {handle.node.relative_path}")
    current = os.fstat(handle.fd)
    expected = handle.node
    if (
        int(current.st_dev),
        int(current.st_ino),
        int(current.st_uid),
        int(current.st_gid),
        int(current.st_mode),
        int(current.st_nlink),
    ) != (expected.device, expected.inode, expected.uid, expected.gid, expected.mode, expected.link_count):
        raise WorkspacePermissionError(f"workspace entry changed during migration: {expected.relative_path}")
    return current


def _migrate_workspace_owners(handles: list[_OpenWorkspaceNode]) -> None:
    for handle in reversed(handles):
        current = _revalidate_node(handle)
        if (current.st_uid, current.st_gid) == (RUNTIME_UID, RUNTIME_GID):
            continue
        try:
            os.fchown(handle.fd, RUNTIME_UID, RUNTIME_GID)
        except OSError as exc:
            raise WorkspacePermissionError(f"workspace ownership migration failed: {handle.node.relative_path}") from exc


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
    """Safely migrate the fixed compose workspace and verify it as `10001:10001`."""

    root_fd, handles = _capture_workspace_tree(RUNTIME_WORKSPACE_ROOT)
    try:
        _migrate_workspace_owners(handles)
        _drop_runtime_privileges()
        _probe_runtime_workspace(root_fd)
    finally:
        for directory_fd in reversed([handle.fd for handle in handles if handle.fd is not None]):
            os.close(directory_fd)


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
