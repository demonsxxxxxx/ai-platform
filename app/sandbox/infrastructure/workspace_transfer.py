from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Protocol

from app.platform.sandbox.errors import (
    ContainerStartFailedError,
    SandboxRuntimeError,
)


class SandboxRuntimeRequest(Protocol):
    tool_policy_subjects: list[dict[str, Any]]
    skill_ids: list[str]


class WorkspaceLease(Protocol):
    workspace_host_path: str
    host_root: str


OPENSANDBOX_STAGE_MAX_FILES = 1024
OPENSANDBOX_STAGE_MAX_FILE_BYTES = 128 * 1024 * 1024
OPENSANDBOX_STAGE_MAX_TOTAL_BYTES = 256 * 1024 * 1024
OPENSANDBOX_STAGE_MAX_DIRECTORIES = 512


@dataclass(frozen=True)
class _WorkspaceFileSnapshot:
    device: int
    inode: int
    mode: int
    link_count: int
    size: int
    modified_ns: int


@dataclass(frozen=True)
class _WorkspaceDirectorySnapshot:
    device: int
    inode: int
    mode: int


@dataclass(frozen=True)
class OpenSandboxWorkspaceFile:
    relative_path: str
    source_path: Path
    snapshot: _WorkspaceFileSnapshot
    ancestor_directories: tuple[tuple[Path, _WorkspaceDirectorySnapshot], ...]


def safe_workspace_relative_path(value: str) -> str:
    """Validate one controller-owned workspace-relative POSIX path."""

    if not isinstance(value, str) or not value or "\x00" in value or "\\" in value:
        raise ContainerStartFailedError("workspace transfer path is invalid")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ContainerStartFailedError("workspace transfer path is invalid")
    normalized = path.as_posix()
    if normalized != value:
        raise ContainerStartFailedError("workspace transfer path is invalid")
    return normalized


def workspace_file_snapshot(path: Path) -> _WorkspaceFileSnapshot:
    try:
        node = path.lstat()
    except OSError as exc:
        raise ContainerStartFailedError(
            "workspace transfer source is unavailable"
        ) from exc
    if (
        not stat.S_ISREG(node.st_mode)
        or stat.S_ISLNK(node.st_mode)
        or node.st_nlink != 1
    ):
        raise ContainerStartFailedError("workspace transfer source is invalid")
    return _WorkspaceFileSnapshot(
        device=int(node.st_dev),
        inode=int(node.st_ino),
        mode=int(node.st_mode),
        link_count=int(node.st_nlink),
        size=int(node.st_size),
        modified_ns=int(node.st_mtime_ns),
    )


def assert_workspace_directory(path: Path) -> _WorkspaceDirectorySnapshot:
    try:
        node = path.lstat()
    except OSError as exc:
        raise ContainerStartFailedError(
            "workspace transfer source is unavailable"
        ) from exc
    if stat.S_ISLNK(node.st_mode) or not stat.S_ISDIR(node.st_mode):
        raise ContainerStartFailedError("workspace transfer source is invalid")
    return _WorkspaceDirectorySnapshot(
        device=int(node.st_dev),
        inode=int(node.st_ino),
        mode=int(node.st_mode),
    )


def _directory_snapshot_from_stat(
    node: os.stat_result,
) -> _WorkspaceDirectorySnapshot:
    if not stat.S_ISDIR(node.st_mode):
        raise ContainerStartFailedError("workspace transfer source is invalid")
    return _WorkspaceDirectorySnapshot(
        device=int(node.st_dev),
        inode=int(node.st_ino),
        mode=int(node.st_mode),
    )


def _workspace_file_snapshot_from_stat(
    node: os.stat_result,
) -> _WorkspaceFileSnapshot:
    if not stat.S_ISREG(node.st_mode) or node.st_nlink != 1:
        raise ContainerStartFailedError("workspace transfer source is invalid")
    return _WorkspaceFileSnapshot(
        device=int(node.st_dev),
        inode=int(node.st_ino),
        mode=int(node.st_mode),
        link_count=int(node.st_nlink),
        size=int(node.st_size),
        modified_ns=int(node.st_mtime_ns),
    )


def secure_workspace_transfer_supported() -> bool:
    return bool(
        getattr(os, "O_DIRECTORY", None)
        and getattr(os, "O_NOFOLLOW", None)
        and os.open in os.supports_dir_fd
        and os.rename in os.supports_dir_fd
    )


def require_secure_workspace_transfer() -> None:
    if not secure_workspace_transfer_supported():
        raise ContainerStartFailedError(
            "OpenSandbox secure workspace transfer is unavailable on this controller"
        )


def directory_open_flags() -> int:
    return int(
        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    )


def _file_open_flags() -> int:
    return int(os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0))


def open_workspace_directory_fd(
    path: Path,
    expected_snapshot: _WorkspaceDirectorySnapshot | None = None,
) -> int:
    require_secure_workspace_transfer()
    try:
        descriptor = os.open(path, directory_open_flags())
    except OSError as exc:
        raise ContainerStartFailedError(
            "workspace transfer source is unavailable"
        ) from exc
    try:
        snapshot = _directory_snapshot_from_stat(os.fstat(descriptor))
        if expected_snapshot is not None and snapshot != expected_snapshot:
            raise ContainerStartFailedError(
                "workspace transfer source changed during read"
            )
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def open_workspace_relative_parent_fd(
    root_descriptor: int,
    relative_path: str,
    *,
    create: bool,
) -> tuple[int, str]:
    """Open a no-follow parent chain below a pinned workspace descriptor."""

    parts = PurePosixPath(safe_workspace_relative_path(relative_path)).parts
    descriptor = os.dup(root_descriptor)
    try:
        for part in parts[:-1]:
            if create:
                try:
                    os.mkdir(part, mode=0o700, dir_fd=descriptor)
                except FileExistsError:
                    pass
                except OSError as exc:
                    raise ContainerStartFailedError(
                        "workspace output destination is unavailable"
                    ) from exc
            try:
                next_descriptor = os.open(
                    part, directory_open_flags(), dir_fd=descriptor
                )
            except OSError as exc:
                raise ContainerStartFailedError(
                    "workspace output destination is invalid"
                ) from exc
            os.close(descriptor)
            descriptor = next_descriptor
        return descriptor, parts[-1]
    except BaseException:
        os.close(descriptor)
        raise


def _open_workspace_file_fd(entry: OpenSandboxWorkspaceFile) -> int:
    if not entry.ancestor_directories:
        raise ContainerStartFailedError("workspace transfer source is invalid")
    root, root_snapshot = entry.ancestor_directories[0]
    descriptor = open_workspace_directory_fd(root, root_snapshot)
    current_path = root
    expected_directories = dict(entry.ancestor_directories)
    try:
        for part in PurePosixPath(entry.relative_path).parts[:-1]:
            current_path = current_path / part
            expected = expected_directories.get(current_path)
            if expected is None:
                raise ContainerStartFailedError("workspace transfer source is invalid")
            next_descriptor = os.open(part, directory_open_flags(), dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
            if _directory_snapshot_from_stat(os.fstat(descriptor)) != expected:
                raise ContainerStartFailedError(
                    "workspace transfer source changed during read"
                )
        try:
            file_descriptor = os.open(
                PurePosixPath(entry.relative_path).name,
                _file_open_flags(),
                dir_fd=descriptor,
            )
        except OSError as exc:
            raise ContainerStartFailedError(
                "workspace transfer source cannot be read"
            ) from exc
    finally:
        os.close(descriptor)
    try:
        if (
            _workspace_file_snapshot_from_stat(os.fstat(file_descriptor))
            != entry.snapshot
        ):
            raise ContainerStartFailedError(
                "workspace transfer source changed during read"
            )
        return file_descriptor
    except BaseException:
        os.close(file_descriptor)
        raise


def _tool_policy_subject_authorized(subject: dict[str, Any], identity: str) -> bool:
    declared = subject.get("declared_identities")
    declared_identities = {
        str(item)
        for item in declared
        if isinstance(item, str) and item
    } if isinstance(declared, list) else set()
    return (
        str(subject.get("identity") or "") == identity
        and all(subject.get(key) is True for key in ("registered", "declared", "active", "distributed"))
        and identity in declared_identities
    )


def _authorized_staged_skill_names(request: SandboxRuntimeRequest) -> set[str]:
    names: set[str] = set()
    for subject in request.tool_policy_subjects:
        if not isinstance(subject, dict) or not _tool_policy_subject_authorized(
            subject,
            "Skill",
        ):
            continue
        allowed = subject.get("allowed_skill_names")
        if isinstance(allowed, list):
            names.update(name for name in allowed if isinstance(name, str) and name)
    return names


def _staged_skill_mount_required(request: SandboxRuntimeRequest) -> bool:
    return bool(_authorized_staged_skill_names(request))


def _stage_skills_required(request: SandboxRuntimeRequest) -> bool:
    return _staged_skill_mount_required(request) or any(
        skill_id != "general-chat" for skill_id in request.skill_ids
    )


def build_opensandbox_workspace_manifest(
    workspace: WorkspaceLease,
    *,
    stage_skills_required: bool,
) -> tuple[list[str], list[OpenSandboxWorkspaceFile]]:
    """Capture a bounded, no-follow manifest for remote workspace transfer."""

    root = Path(workspace.workspace_host_path)
    root_snapshot = assert_workspace_directory(root)
    try:
        root.resolve(strict=True).relative_to(
            Path(workspace.host_root).resolve(strict=True)
        )
    except (OSError, RuntimeError, ValueError) as exc:
        raise ContainerStartFailedError(
            "workspace transfer source escapes attempt root"
        ) from exc

    directories = {"inputs", "outputs", "outputs/delivery", ".ai-platform"}
    files: list[OpenSandboxWorkspaceFile] = []
    total_bytes = 0

    def add_file(
        path: Path,
        relative_path: str,
        ancestor_directories: tuple[tuple[Path, _WorkspaceDirectorySnapshot], ...],
    ) -> None:
        nonlocal total_bytes
        snapshot = workspace_file_snapshot(path)
        if snapshot.size > OPENSANDBOX_STAGE_MAX_FILE_BYTES:
            raise ContainerStartFailedError(
                "workspace transfer exceeds file byte limit"
            )
        total_bytes += snapshot.size
        if total_bytes > OPENSANDBOX_STAGE_MAX_TOTAL_BYTES:
            raise ContainerStartFailedError(
                "workspace transfer exceeds total byte limit"
            )
        if len(files) >= OPENSANDBOX_STAGE_MAX_FILES:
            raise ContainerStartFailedError(
                "workspace transfer exceeds file count limit"
            )
        files.append(
            OpenSandboxWorkspaceFile(
                relative_path=safe_workspace_relative_path(relative_path),
                source_path=path,
                snapshot=snapshot,
                ancestor_directories=ancestor_directories,
            )
        )

    def walk(
        directory: Path,
        relative_root: str,
        ancestor_directories: tuple[tuple[Path, _WorkspaceDirectorySnapshot], ...],
    ) -> None:
        directory_snapshot = assert_workspace_directory(directory)
        stable_ancestors = (
            *ancestor_directories,
            (directory, directory_snapshot),
        )
        try:
            children = sorted(directory.iterdir(), key=lambda item: item.name)
        except OSError as exc:
            raise ContainerStartFailedError(
                "workspace transfer source cannot be read"
            ) from exc
        if assert_workspace_directory(directory) != directory_snapshot:
            raise ContainerStartFailedError(
                "workspace transfer source changed during manifest"
            )
        for child in children:
            name = child.name
            if (
                not name
                or name in {".", ".."}
                or "\x00" in name
                or "/" in name
                or "\\" in name
            ):
                raise ContainerStartFailedError("workspace transfer path is invalid")
            relative_path = name if not relative_root else f"{relative_root}/{name}"
            try:
                node = child.lstat()
            except OSError as exc:
                raise ContainerStartFailedError(
                    "workspace transfer source is unavailable"
                ) from exc
            if stat.S_ISLNK(node.st_mode):
                raise ContainerStartFailedError("workspace transfer source is invalid")
            if stat.S_ISDIR(node.st_mode):
                directories.add(safe_workspace_relative_path(relative_path))
                if len(directories) > OPENSANDBOX_STAGE_MAX_DIRECTORIES:
                    raise ContainerStartFailedError(
                        "workspace transfer exceeds directory limit"
                    )
                walk(child, relative_path, stable_ancestors)
            elif stat.S_ISREG(node.st_mode):
                add_file(child, relative_path, stable_ancestors)
            else:
                raise ContainerStartFailedError("workspace transfer source is invalid")
        if assert_workspace_directory(directory) != directory_snapshot:
            raise ContainerStartFailedError(
                "workspace transfer source changed during manifest"
            )

    try:
        root_children = sorted(root.iterdir(), key=lambda item: item.name)
    except OSError as exc:
        raise ContainerStartFailedError(
            "workspace transfer source cannot be read"
        ) from exc
    if assert_workspace_directory(root) != root_snapshot:
        raise ContainerStartFailedError(
            "workspace transfer source changed during manifest"
        )
    named_source_directories = {"inputs", ".ai-platform"}
    if stage_skills_required:
        named_source_directories.add(".claude")
    for child in root_children:
        try:
            node = child.lstat()
        except OSError as exc:
            raise ContainerStartFailedError(
                "workspace transfer source is unavailable"
            ) from exc
        if stat.S_ISLNK(node.st_mode):
            raise ContainerStartFailedError("workspace transfer source is invalid")
        if stat.S_ISREG(node.st_mode):
            add_file(child, child.name, ((root, root_snapshot),))
            continue
        if not stat.S_ISDIR(node.st_mode):
            raise ContainerStartFailedError("workspace transfer source is invalid")
        if child.name in named_source_directories:
            if child.name == ".claude":
                skills_root = child / "skills"
                claude_snapshot = assert_workspace_directory(child)
                assert_workspace_directory(skills_root)
                directories.update({".claude", ".claude/skills"})
                walk(
                    skills_root,
                    ".claude/skills",
                    ((root, root_snapshot), (child, claude_snapshot)),
                )
            else:
                directories.add(safe_workspace_relative_path(child.name))
                walk(child, child.name, ((root, root_snapshot),))
    if stage_skills_required and not (root / ".claude" / "skills").is_dir():
        raise ContainerStartFailedError(
            "workspace transfer Skill source is unavailable"
        )
    if assert_workspace_directory(root) != root_snapshot:
        raise ContainerStartFailedError(
            "workspace transfer source changed during manifest"
        )
    return (
        sorted(directories, key=lambda item: (item.count("/"), item)),
        sorted(files, key=lambda item: item.relative_path),
    )


def read_stable_workspace_file(entry: OpenSandboxWorkspaceFile) -> bytes:
    """Read via an anchored no-follow descriptor chain and prove stability."""

    for directory, snapshot in entry.ancestor_directories:
        if assert_workspace_directory(directory) != snapshot:
            raise ContainerStartFailedError(
                "workspace transfer source changed during read"
            )
    before = workspace_file_snapshot(entry.source_path)
    if before != entry.snapshot:
        raise ContainerStartFailedError("workspace transfer source changed during read")
    try:
        descriptor = _open_workspace_file_fd(entry)
        try:
            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = os.read(descriptor, 64 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > OPENSANDBOX_STAGE_MAX_FILE_BYTES:
                    raise ContainerStartFailedError(
                        "workspace transfer exceeds file byte limit"
                    )
                chunks.append(chunk)
            if (
                total != entry.snapshot.size
                or _workspace_file_snapshot_from_stat(os.fstat(descriptor))
                != entry.snapshot
            ):
                raise ContainerStartFailedError(
                    "workspace transfer source changed during read"
                )
        finally:
            os.close(descriptor)
    except SandboxRuntimeError:
        raise
    except OSError as exc:
        raise ContainerStartFailedError(
            "workspace transfer source cannot be read"
        ) from exc
    if workspace_file_snapshot(entry.source_path) != entry.snapshot:
        raise ContainerStartFailedError("workspace transfer source changed during read")
    for directory, snapshot in entry.ancestor_directories:
        if assert_workspace_directory(directory) != snapshot:
            raise ContainerStartFailedError(
                "workspace transfer source changed during read"
            )
    return b"".join(chunks)


def _build_opensandbox_workspace_manifest(
    request: SandboxRuntimeRequest,
    workspace: WorkspaceLease,
) -> tuple[list[str], list[OpenSandboxWorkspaceFile]]:
    return build_opensandbox_workspace_manifest(
        workspace,
        stage_skills_required=_stage_skills_required(request),
    )


_OPENSANDBOX_STAGE_MAX_DIRECTORIES = OPENSANDBOX_STAGE_MAX_DIRECTORIES
_OPENSANDBOX_STAGE_MAX_FILES = OPENSANDBOX_STAGE_MAX_FILES
_OPENSANDBOX_STAGE_MAX_FILE_BYTES = OPENSANDBOX_STAGE_MAX_FILE_BYTES
_OPENSANDBOX_STAGE_MAX_TOTAL_BYTES = OPENSANDBOX_STAGE_MAX_TOTAL_BYTES
_OpenSandboxWorkspaceFile = OpenSandboxWorkspaceFile
_assert_workspace_directory = assert_workspace_directory
_directory_open_flags = directory_open_flags
_open_workspace_directory_fd = open_workspace_directory_fd
_open_workspace_relative_parent_fd = open_workspace_relative_parent_fd
_read_stable_workspace_file = read_stable_workspace_file
_require_secure_workspace_transfer = require_secure_workspace_transfer
_safe_workspace_relative_path = safe_workspace_relative_path
_secure_workspace_transfer_supported = secure_workspace_transfer_supported
_workspace_file_snapshot = workspace_file_snapshot
