from __future__ import annotations

import hashlib
import json
import os
import posixpath
import secrets
import shutil
import stat
try:
    import fcntl
except ImportError:  # pragma: no cover - Windows has no POSIX file locks
    fcntl = None
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Sequence


_SNAPSHOT_DIRECTORY = ".ai-platform-artifact-snapshot-v1"
_SNAPSHOT_SCHEMA = "ai-platform.artifact-snapshot.v1"
_SNAPSHOT_MANIFEST = ".ai-platform-artifact-snapshot-v1.manifest.json"
_SNAPSHOT_LOCK = ".ai-platform-artifact-snapshot-v1.lock"


class OpenSandboxHostBindError(ValueError):
    """The requested host workspace bind is outside its authoritative lease."""


@dataclass(frozen=True)
class HostBindDeliveryFile:
    relative_path: str
    size_bytes: int





def _normalized_absolute(path: str | os.PathLike[str], *, label: str) -> Path:
    raw_text = os.fspath(path)
    raw = Path(raw_text)
    if not raw.is_absolute() and not (
        os.name == "nt" and str(raw_text).startswith(("/", "\\"))
    ):
        raise OpenSandboxHostBindError(f"{label} must be an absolute normalized path")
    text = str(raw_text)
    if "\x00" in text:
        raise OpenSandboxHostBindError(f"{label} must be an absolute normalized path")
    if os.name == "nt" and text.startswith("/"):
        if posixpath.normpath(text) != text:
            raise OpenSandboxHostBindError(f"{label} must be an absolute normalized path")
        normalized_text = text
    else:
        normalized_text = os.path.normpath(text)
        if normalized_text != text:
            raise OpenSandboxHostBindError(f"{label} must be an absolute normalized path")
    raw_parts = raw.parts[1:] if raw.anchor else raw.parts
    if any(part in {"", ".", ".."} for part in raw_parts):
        raise OpenSandboxHostBindError(f"{label} must be an absolute normalized path")
    return Path(os.path.abspath(normalized_text))


def _expected_attempt_root(root: Path, request: Any) -> Path:
    return (
        root
        / "tenants"
        / request.tenant_id
        / "workspaces"
        / request.workspace_id
        / "users"
        / request.user_id
        / "sessions"
        / request.session_id
        / "runs"
        / request.run_id
        / "attempts"
        / request.attempt_id
    )


def resolve_opensandbox_host_bind_source(
    workspace_root: str | os.PathLike[str],
    request: Any,
    workspace: Any,
    *,
    require_existing: bool = False,
) -> Path:
    """Resolve the one workspace directory authorized by this Run Attempt lease."""

    root = _normalized_absolute(workspace_root, label="workspace root")
    if (
        workspace.tenant_id,
        workspace.workspace_id,
        workspace.user_id,
        workspace.session_id,
        workspace.run_id,
    ) != (
        request.tenant_id,
        request.workspace_id,
        request.user_id,
        request.session_id,
        request.run_id,
    ):
        raise OpenSandboxHostBindError("workspace lease scope does not match the runtime request")

    expected_attempt = _expected_attempt_root(root, request)
    expected_workspace = expected_attempt / "workspace"
    expected_inputs = expected_workspace / "inputs"
    expected_logs = expected_attempt / "logs"
    actual_paths = {
        "attempt root": workspace.host_root,
        "leased workspace": workspace.workspace_host_path,
        "workspace inputs": workspace.inputs_host_path,
        "attempt logs": workspace.logs_host_path,
    }
    expected_paths = {
        "attempt root": expected_attempt,
        "leased workspace": expected_workspace,
        "workspace inputs": expected_inputs,
        "attempt logs": expected_logs,
    }
    for label, raw_path in actual_paths.items():
        actual = _normalized_absolute(raw_path, label=label)
        if actual != expected_paths[label]:
            raise OpenSandboxHostBindError(f"{label} does not match the authoritative Attempt workspace")

    if workspace.workspace_container_path != "/workspace":
        raise OpenSandboxHostBindError("workspace container path must be /workspace")

    if require_existing:
        try:
            resolved_root = root.resolve(strict=True)
            resolved_workspace = expected_workspace.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise OpenSandboxHostBindError("leased workspace is unavailable") from exc
        if resolved_root != root or resolved_workspace != expected_workspace:
            raise OpenSandboxHostBindError("leased workspace contains a symlinked path")
        try:
            resolved_workspace.relative_to(resolved_root)
        except ValueError as exc:
            raise OpenSandboxHostBindError("leased workspace escaped the workspace root") from exc
        if not resolved_workspace.is_dir():
            raise OpenSandboxHostBindError("leased workspace is not a directory")

    return expected_workspace


def normalize_host_bind_relative_path(value: str) -> str:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise OpenSandboxHostBindError("workspace response file path is invalid")
    normalized = value.replace("\\", "/")
    posix = PurePosixPath(normalized)
    windows = PureWindowsPath(value)
    if (
        posix.is_absolute()
        or windows.is_absolute()
        or windows.drive
        or any(part in {"", ".", ".."} for part in normalized.split("/"))
        or posix.as_posix() != normalized
    ):
        raise OpenSandboxHostBindError("workspace response file path is invalid")
    return normalized


def _directory_flags() -> int:
    return int(os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0))


def _file_flags() -> int:
    return int(
        os.O_RDONLY
        | os.O_NOFOLLOW
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )


def _secure_descriptor_walk_supported() -> bool:
    return bool(
        getattr(os, "O_DIRECTORY", None)
        and getattr(os, "O_NOFOLLOW", None)
        and os.open in os.supports_dir_fd
    )


def read_host_bind_control_file(
    workspace_path: Path,
    filename: str,
    *,
    max_bytes: int = 4096,
) -> str:
    """Read one fixed root control file through no-follow descriptors."""

    if filename in {"", ".", ".."} or "/" in filename or "\\" in filename:
        raise OpenSandboxHostBindError("workspace control file name is invalid")
    if not _secure_descriptor_walk_supported():
        raise OpenSandboxHostBindError("secure workspace control validation is unavailable")
    try:
        root_fd = os.open(workspace_path, _directory_flags())
        try:
            file_fd = os.open(filename, _file_flags(), dir_fd=root_fd)
        finally:
            os.close(root_fd)
        try:
            node = os.fstat(file_fd)
            if not stat.S_ISREG(node.st_mode) or node.st_nlink != 1 or node.st_size > max_bytes:
                raise OpenSandboxHostBindError("workspace control file is invalid")
            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = os.read(file_fd, min(4096, max_bytes + 1 - total))
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise OpenSandboxHostBindError("workspace control file is invalid")
                chunks.append(chunk)
        finally:
            os.close(file_fd)
        return b"".join(chunks).decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise OpenSandboxHostBindError("workspace control file is unavailable") from exc


def _assert_delivery_directory(
    node: os.stat_result,
    *,
    expected_uid: int,
    expected_gid: int,
) -> None:
    if (
        not stat.S_ISDIR(node.st_mode)
        or int(node.st_uid) != expected_uid
        or int(node.st_gid) != expected_gid
        or stat.S_IMODE(node.st_mode) & 0o022
    ):
        raise OpenSandboxHostBindError("workspace response directory metadata is invalid")


def _open_delivery_file(
    root_fd: int,
    relative_path: str,
    *,
    expected_uid: int,
    expected_gid: int,
) -> tuple[int, os.stat_result]:
    parts = PurePosixPath(normalize_host_bind_relative_path(relative_path)).parts
    parent_fd = os.dup(root_fd)
    file_fd: int | None = None
    try:
        for part in parts[:-1]:
            next_fd = os.open(part, _directory_flags(), dir_fd=parent_fd)
            _assert_delivery_directory(
                os.fstat(next_fd),
                expected_uid=expected_uid,
                expected_gid=expected_gid,
            )
            os.close(parent_fd)
            parent_fd = next_fd
        file_fd = os.open(parts[-1], _file_flags(), dir_fd=parent_fd)
        node = os.fstat(file_fd)
        if (
            not stat.S_ISREG(node.st_mode)
            or node.st_nlink != 1
            or int(node.st_uid) != expected_uid
            or int(node.st_gid) != expected_gid
            or stat.S_IMODE(node.st_mode) & 0o022
        ):
            raise OpenSandboxHostBindError("workspace response file metadata is invalid")
        result = file_fd
        file_fd = None
        return result, node
    except OSError as exc:
        raise OpenSandboxHostBindError("workspace response file is unavailable") from exc
    finally:
        if file_fd is not None:
            os.close(file_fd)
        os.close(parent_fd)


def _write_all(descriptor: int, content: bytes) -> None:
    view = memoryview(content)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise OpenSandboxHostBindError("workspace response snapshot write failed")
        view = view[written:]


def _source_identity(node: os.stat_result) -> tuple[int, ...]:
    return (
        int(node.st_dev),
        int(node.st_ino),
        int(node.st_mode),
        int(node.st_nlink),
        int(node.st_uid),
        int(node.st_gid),
        int(node.st_size),
        int(node.st_mtime_ns),
    )


def _copy_stable_delivery_file(
    source_fd: int,
    source_node: os.stat_result,
    target: Path,
    *,
    max_file_bytes: int,
) -> tuple[int, str]:
    if source_node.st_size > max_file_bytes:
        raise OpenSandboxHostBindError("workspace response exceeds the per-file byte limit")
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    target_fd = os.open(
        target,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
        0o600,
    )
    first_digest = hashlib.sha256()
    total = 0
    try:
        while chunk := os.read(source_fd, 1024 * 1024):
            total += len(chunk)
            if total > max_file_bytes:
                raise OpenSandboxHostBindError("workspace response exceeds the per-file byte limit")
            first_digest.update(chunk)
            _write_all(target_fd, chunk)
        os.fsync(target_fd)
    finally:
        os.close(target_fd)
    if total != int(source_node.st_size) or _source_identity(os.fstat(source_fd)) != _source_identity(source_node):
        raise OpenSandboxHostBindError("workspace response file changed during snapshot")

    os.lseek(source_fd, 0, os.SEEK_SET)
    second_digest = hashlib.sha256()
    second_total = 0
    while chunk := os.read(source_fd, 1024 * 1024):
        second_total += len(chunk)
        if second_total > max_file_bytes:
            raise OpenSandboxHostBindError("workspace response exceeds the per-file byte limit")
        second_digest.update(chunk)
    if (
        second_total != total
        or second_digest.digest() != first_digest.digest()
        or _source_identity(os.fstat(source_fd)) != _source_identity(source_node)
    ):
        raise OpenSandboxHostBindError("workspace response file changed during snapshot")
    return total, first_digest.hexdigest()


def _fsync_tree_directories(root: Path) -> None:
    for directory, _subdirectories, _files in os.walk(root, topdown=False):
        descriptor = os.open(directory, _directory_flags())
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def _open_snapshot_lock(attempt_root: Path) -> int:
    if fcntl is None:
        raise OpenSandboxHostBindError("workspace response snapshot locking is unavailable")
    try:
        descriptor = os.open(
            attempt_root / _SNAPSHOT_LOCK,
            os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
            0o600,
        )
        os.fchmod(descriptor, 0o600)
        node = os.fstat(descriptor)
        if not stat.S_ISREG(node.st_mode) or node.st_nlink != 1:
            raise OpenSandboxHostBindError("workspace response snapshot lock is invalid")
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        return descriptor
    except OpenSandboxHostBindError:
        try:
            os.close(descriptor)
        except (UnboundLocalError, OSError):
            pass
        raise
    except OSError as exc:
        try:
            os.close(descriptor)
        except (UnboundLocalError, OSError):
            pass
        raise OpenSandboxHostBindError("workspace response snapshot lock failed") from exc


def _cleanup_snapshot_temporaries(attempt_root: Path) -> None:
    prefixes = (
        f".{_SNAPSHOT_DIRECTORY}.tmp-",
        f".{_SNAPSHOT_MANIFEST}.tmp-",
    )
    try:
        entries = list(os.scandir(attempt_root))
    except OSError as exc:
        raise OpenSandboxHostBindError("workspace response snapshot is unavailable") from exc
    for entry in entries:
        prefix = next((value for value in prefixes if entry.name.startswith(value)), None)
        if prefix is None:
            continue
        suffix = entry.name[len(prefix) :]
        if len(suffix) != 32 or any(character not in "0123456789abcdef" for character in suffix):
            raise OpenSandboxHostBindError("workspace response snapshot temporary is invalid")
        try:
            node = entry.stat(follow_symlinks=False)
        except OSError as exc:
            raise OpenSandboxHostBindError("workspace response snapshot temporary is unavailable") from exc
        path = Path(entry.path)
        if prefix == f".{_SNAPSHOT_DIRECTORY}.tmp-":
            if stat.S_ISLNK(node.st_mode) or not stat.S_ISDIR(node.st_mode):
                raise OpenSandboxHostBindError("workspace response snapshot temporary is invalid")
            shutil.rmtree(path)
        else:
            if stat.S_ISLNK(node.st_mode) or not stat.S_ISREG(node.st_mode) or node.st_nlink != 1:
                raise OpenSandboxHostBindError("workspace response snapshot temporary is invalid")
            path.unlink()


def _validate_existing_snapshot(
    snapshot_root: Path,
    response_files: Sequence[str],
    *,
    max_files: int,
    max_file_bytes: int,
    max_total_bytes: int,
    expected_uid: int,
    expected_gid: int,
) -> Path:
    try:
        payload = json.loads(
            read_host_bind_control_file(
                snapshot_root.parent,
                _SNAPSHOT_MANIFEST,
                max_bytes=64 * 1024,
            )
        )
    except (json.JSONDecodeError, TypeError) as exc:
        raise OpenSandboxHostBindError("workspace response snapshot manifest is invalid") from exc
    expected_paths = [normalize_host_bind_relative_path(path) for path in response_files]
    if (
        not isinstance(payload, dict)
        or payload.get("schema_version") != _SNAPSHOT_SCHEMA
        or payload.get("paths") != expected_paths
    ):
        raise OpenSandboxHostBindError("workspace response snapshot manifest is invalid")
    validated = validate_host_bind_delivery_files(
        snapshot_root,
        expected_paths,
        max_files=max_files,
        max_file_bytes=max_file_bytes,
        max_total_bytes=max_total_bytes,
        expected_uid=expected_uid,
        expected_gid=expected_gid,
    )
    if payload.get("sizes") != [item.size_bytes for item in validated]:
        raise OpenSandboxHostBindError("workspace response snapshot manifest is invalid")
    return snapshot_root


def _snapshot_host_bind_delivery_files_unlocked(
    workspace_path: Path,
    attempt_root: Path,
    response_files: Sequence[str],
    *,
    max_files: int,
    max_file_bytes: int,
    max_total_bytes: int,
    expected_uid: int,
    expected_gid: int,
) -> Path:
    """Publish a stable artifact snapshot outside the sandbox-mounted workspace."""

    if not _secure_descriptor_walk_supported():
        raise OpenSandboxHostBindError("secure workspace response validation is unavailable")
    workspace_path = Path(workspace_path)
    attempt_root = Path(attempt_root)
    if workspace_path.parent != attempt_root or workspace_path.name != "workspace":
        raise OpenSandboxHostBindError("workspace response snapshot scope is invalid")
    if len(response_files) > max_files:
        raise OpenSandboxHostBindError("workspace response exceeds the file count limit")
    normalized_paths = [normalize_host_bind_relative_path(path) for path in response_files]
    if len(set(normalized_paths)) != len(normalized_paths):
        raise OpenSandboxHostBindError("workspace response file path is duplicated")

    snapshot_root = attempt_root / _SNAPSHOT_DIRECTORY
    manifest_path = attempt_root / _SNAPSHOT_MANIFEST
    try:
        snapshot_node = snapshot_root.lstat()
    except FileNotFoundError:
        snapshot_node = None
    except OSError as exc:
        raise OpenSandboxHostBindError("workspace response snapshot is unavailable") from exc
    if snapshot_node is not None:
        if stat.S_ISLNK(snapshot_node.st_mode) or not stat.S_ISDIR(snapshot_node.st_mode):
            raise OpenSandboxHostBindError("workspace response snapshot is invalid")
        try:
            manifest_path.lstat()
        except FileNotFoundError:
            shutil.rmtree(snapshot_root)
            snapshot_node = None
        except OSError as exc:
            raise OpenSandboxHostBindError("workspace response snapshot manifest is unavailable") from exc
        else:
            return _validate_existing_snapshot(
                snapshot_root,
                normalized_paths,
                max_files=max_files,
                max_file_bytes=max_file_bytes,
                max_total_bytes=max_total_bytes,
                expected_uid=expected_uid,
                expected_gid=expected_gid,
            )
    if snapshot_node is None:
        try:
            stale_manifest = manifest_path.lstat()
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise OpenSandboxHostBindError("workspace response snapshot manifest is unavailable") from exc
        else:
            if stat.S_ISLNK(stale_manifest.st_mode) or not stat.S_ISREG(stale_manifest.st_mode) or stale_manifest.st_nlink != 1:
                raise OpenSandboxHostBindError("workspace response snapshot manifest is invalid")
            manifest_path.unlink()

    temporary = attempt_root / f".{_SNAPSHOT_DIRECTORY}.tmp-{secrets.token_hex(16)}"
    temporary_manifest = attempt_root / f".{_SNAPSHOT_MANIFEST}.tmp-{secrets.token_hex(16)}"
    try:
        temporary.mkdir(mode=0o700)
        root_fd = os.open(workspace_path, _directory_flags())
        try:
            _assert_delivery_directory(
                os.fstat(root_fd),
                expected_uid=expected_uid,
                expected_gid=expected_gid,
            )
            sizes: list[int] = []
            total_bytes = 0
            for relative_path in normalized_paths:
                source_fd, source_node = _open_delivery_file(
                    root_fd,
                    relative_path,
                    expected_uid=expected_uid,
                    expected_gid=expected_gid,
                )
                try:
                    size, _digest = _copy_stable_delivery_file(
                        source_fd,
                        source_node,
                        temporary.joinpath(*PurePosixPath(relative_path).parts),
                        max_file_bytes=max_file_bytes,
                    )
                finally:
                    os.close(source_fd)
                total_bytes += size
                if total_bytes > max_total_bytes:
                    raise OpenSandboxHostBindError("workspace response exceeds the total byte limit")
                sizes.append(size)
        finally:
            os.close(root_fd)
        manifest = json.dumps(
            {"schema_version": _SNAPSHOT_SCHEMA, "paths": normalized_paths, "sizes": sizes},
            sort_keys=True,
        ).encode("utf-8") + b"\n"
        manifest_fd = os.open(
            temporary_manifest,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
            0o600,
        )
        try:
            _write_all(manifest_fd, manifest)
            os.fsync(manifest_fd)
        finally:
            os.close(manifest_fd)
        _fsync_tree_directories(temporary)
        os.rename(temporary, snapshot_root)
        os.rename(temporary_manifest, manifest_path)
        parent_fd = os.open(attempt_root, _directory_flags())
        try:
            os.fsync(parent_fd)
        finally:
            os.close(parent_fd)
        temporary = Path()
        temporary_manifest = Path()
    except FileExistsError:
        if temporary != Path():
            shutil.rmtree(temporary, ignore_errors=True)
        if temporary_manifest != Path():
            temporary_manifest.unlink(missing_ok=True)
        return _validate_existing_snapshot(
            snapshot_root,
            normalized_paths,
            max_files=max_files,
            max_file_bytes=max_file_bytes,
            max_total_bytes=max_total_bytes,
            expected_uid=expected_uid,
            expected_gid=expected_gid,
        )
    except (OSError, OpenSandboxHostBindError) as exc:
        if temporary != Path():
            shutil.rmtree(temporary, ignore_errors=True)
        if temporary_manifest != Path():
            temporary_manifest.unlink(missing_ok=True)
        if isinstance(exc, OpenSandboxHostBindError):
            raise
        raise OpenSandboxHostBindError("workspace response snapshot failed") from exc
    return snapshot_root


def snapshot_host_bind_delivery_files(
    workspace_path: Path,
    attempt_root: Path,
    response_files: Sequence[str],
    *,
    max_files: int,
    max_file_bytes: int,
    max_total_bytes: int,
    expected_uid: int,
    expected_gid: int,
) -> Path:
    """Publish one immutable artifact snapshot under an Attempt-level lock."""

    workspace_path = Path(workspace_path)
    attempt_root = Path(attempt_root)
    if not _secure_descriptor_walk_supported():
        raise OpenSandboxHostBindError("secure workspace response validation is unavailable")
    if workspace_path.parent != attempt_root or workspace_path.name != "workspace":
        raise OpenSandboxHostBindError("workspace response snapshot scope is invalid")
    lock_descriptor = _open_snapshot_lock(attempt_root)
    try:
        _cleanup_snapshot_temporaries(attempt_root)
        return _snapshot_host_bind_delivery_files_unlocked(
            workspace_path,
            attempt_root,
            response_files,
            max_files=max_files,
            max_file_bytes=max_file_bytes,
            max_total_bytes=max_total_bytes,
            expected_uid=expected_uid,
            expected_gid=expected_gid,
        )
    finally:
        fcntl.flock(lock_descriptor, fcntl.LOCK_UN)
        os.close(lock_descriptor)


def validate_host_bind_delivery_files(
    workspace_path: Path,
    response_files: Sequence[str],
    *,
    max_files: int,
    max_file_bytes: int,
    max_total_bytes: int,
    expected_uid: int | None = None,
    expected_gid: int | None = None,
) -> tuple[HostBindDeliveryFile, ...]:
    """Validate terminal-declared files in place without reading their contents."""

    if not _secure_descriptor_walk_supported():
        raise OpenSandboxHostBindError("secure workspace response validation is unavailable")
    if len(response_files) > max_files:
        raise OpenSandboxHostBindError("workspace response exceeds the file count limit")

    try:
        root_fd = os.open(workspace_path, _directory_flags())
        root_node = os.fstat(root_fd)
        if expected_uid is None:
            expected_uid = int(root_node.st_uid)
        if expected_gid is None:
            expected_gid = int(root_node.st_gid)
        _assert_delivery_directory(
            root_node,
            expected_uid=expected_uid,
            expected_gid=expected_gid,
        )
    except OSError as exc:
        raise OpenSandboxHostBindError("leased workspace is unavailable") from exc

    validated: list[HostBindDeliveryFile] = []
    total_bytes = 0
    seen: set[str] = set()
    try:
        for raw_path in response_files:
            relative_path = normalize_host_bind_relative_path(raw_path)
            if relative_path in seen:
                raise OpenSandboxHostBindError("workspace response file path is duplicated")
            seen.add(relative_path)
            file_fd, node = _open_delivery_file(
                root_fd,
                relative_path,
                expected_uid=expected_uid,
                expected_gid=expected_gid,
            )
            os.close(file_fd)
            if node.st_size > max_file_bytes:
                raise OpenSandboxHostBindError("workspace response exceeds the per-file byte limit")
            total_bytes += int(node.st_size)
            if total_bytes > max_total_bytes:
                raise OpenSandboxHostBindError("workspace response exceeds the total byte limit")
            validated.append(
                HostBindDeliveryFile(
                    relative_path=relative_path,
                    size_bytes=int(node.st_size),
                )
            )
    finally:
        os.close(root_fd)

    return tuple(validated)
