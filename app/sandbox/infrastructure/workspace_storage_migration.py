from __future__ import annotations

import hashlib
import json
import os
import stat
import sys
from dataclasses import dataclass
from pathlib import Path


_SCHEMA_VERSION = "ai-platform.workspace-storage-migration.v1"
_INCOMPLETE_MARKER = ".ai-platform-workspace-migration-v1.incomplete"
_COMPLETE_MARKER = ".ai-platform-workspace-migration-v1.json"
_MARKERS = frozenset({_INCOMPLETE_MARKER, _COMPLETE_MARKER})
_MARKER_TEMP_DIRECTORY = ".ai-platform-workspace-migration-v1.tmp"
_MARKER_TEMP_PREFIXES = tuple(f"{marker}.tmp-" for marker in _MARKERS)
_CHUNK_BYTES = 1024 * 1024


class WorkspaceStorageMigrationError(RuntimeError):
    pass


@dataclass(frozen=True)
class MigrationInventory:
    directories: int
    files: int
    bytes: int
    digest: str


class _InventoryBuilder:
    def __init__(self) -> None:
        self.directories = 0
        self.files = 0
        self.bytes = 0
        self._digest = hashlib.sha256()

    def add(self, relative_path: str, node: os.stat_result, content_digest: str | None) -> None:
        kind = "d" if stat.S_ISDIR(node.st_mode) else "f"
        size = 0 if kind == "d" else int(node.st_size)
        row = "\0".join(
            (
                kind,
                relative_path,
                f"{stat.S_IMODE(node.st_mode):04o}",
                str(int(node.st_uid)),
                str(int(node.st_gid)),
                str(size),
                content_digest or "",
            )
        )
        self._digest.update(row.encode("utf-8") + b"\n")
        if kind == "d":
            self.directories += 1
        else:
            self.files += 1
            self.bytes += size

    def finish(self) -> MigrationInventory:
        return MigrationInventory(
            directories=self.directories,
            files=self.files,
            bytes=self.bytes,
            digest=self._digest.hexdigest(),
        )


def _reject_symlinked_path_chain(path: Path) -> None:
    current = path
    while True:
        try:
            node = current.lstat()
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise WorkspaceStorageMigrationError("workspace migration path is unavailable") from exc
        else:
            if stat.S_ISLNK(node.st_mode) or not stat.S_ISDIR(node.st_mode):
                raise WorkspaceStorageMigrationError("workspace migration path contains an unsafe parent")
        if current.parent == current:
            return
        current = current.parent


def _node(path: Path) -> os.stat_result:
    try:
        node = path.lstat()
    except OSError as exc:
        raise WorkspaceStorageMigrationError("workspace migration path is unavailable") from exc
    if stat.S_ISLNK(node.st_mode) or not (
        stat.S_ISDIR(node.st_mode) or stat.S_ISREG(node.st_mode)
    ):
        raise WorkspaceStorageMigrationError("workspace migration found an unsupported node")
    if stat.S_ISREG(node.st_mode) and node.st_nlink != 1:
        raise WorkspaceStorageMigrationError("workspace migration found a hard-linked file")
    return node


def _same_node(left: os.stat_result, right: os.stat_result) -> bool:
    return (
        left.st_dev,
        left.st_ino,
        left.st_mode,
        left.st_nlink,
        left.st_uid,
        left.st_gid,
        left.st_size,
        left.st_mtime_ns,
    ) == (
        right.st_dev,
        right.st_ino,
        right.st_mode,
        right.st_nlink,
        right.st_uid,
        right.st_gid,
        right.st_size,
        right.st_mtime_ns,
    )


def _open_readonly(path: Path) -> int:
    flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    try:
        return os.open(path, flags)
    except OSError as exc:
        raise WorkspaceStorageMigrationError("workspace migration file is unreadable") from exc


def _hash_file(path: Path, expected: os.stat_result) -> str:
    digest = hashlib.sha256()
    descriptor = _open_readonly(path)
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or opened.st_dev != expected.st_dev
            or opened.st_ino != expected.st_ino
            or opened.st_size != expected.st_size
            or opened.st_mtime_ns != expected.st_mtime_ns
        ):
            raise WorkspaceStorageMigrationError("workspace migration source changed during read")
        while chunk := os.read(descriptor, _CHUNK_BYTES):
            digest.update(chunk)
        closed = os.fstat(descriptor)
        if (
            closed.st_size != expected.st_size
            or closed.st_mtime_ns != expected.st_mtime_ns
        ):
            raise WorkspaceStorageMigrationError("workspace migration source changed during read")
    finally:
        os.close(descriptor)
    return digest.hexdigest()


def _apply_metadata(path: Path, source: os.stat_result) -> None:
    try:
        os.chown(path, source.st_uid, source.st_gid, follow_symlinks=False)
        os.chmod(path, stat.S_IMODE(source.st_mode), follow_symlinks=False)
    except OSError as exc:
        raise WorkspaceStorageMigrationError("workspace migration cannot preserve metadata") from exc


def _copy_or_verify_file(source: Path, target: Path, source_node: os.stat_result) -> str:
    source_digest = _hash_file(source, source_node)
    try:
        target_node = target.lstat()
    except FileNotFoundError:
        target_node = None
    except OSError as exc:
        raise WorkspaceStorageMigrationError("workspace migration target is unavailable") from exc

    if target_node is None:
        temporary = target.with_name(f".{target.name}.ai-platform-migration-tmp")
        try:
            try:
                temporary_node = temporary.lstat()
            except FileNotFoundError:
                pass
            else:
                if (
                    stat.S_ISLNK(temporary_node.st_mode)
                    or not stat.S_ISREG(temporary_node.st_mode)
                    or temporary_node.st_nlink != 1
                ):
                    raise WorkspaceStorageMigrationError("workspace migration temporary target is invalid")
                temporary.unlink()
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
            destination = os.open(temporary, flags, 0o600)
            source_descriptor: int | None = None
            try:
                source_descriptor = _open_readonly(source)
                copied_digest = hashlib.sha256()
                try:
                    copied_node = os.fstat(source_descriptor)
                    if (
                        not stat.S_ISREG(copied_node.st_mode)
                        or copied_node.st_nlink != 1
                        or copied_node.st_dev != source_node.st_dev
                        or copied_node.st_ino != source_node.st_ino
                    ):
                        raise WorkspaceStorageMigrationError("workspace migration source changed during copy")
                    while chunk := os.read(source_descriptor, _CHUNK_BYTES):
                        copied_digest.update(chunk)
                        view = memoryview(chunk)
                        while view:
                            written = os.write(destination, view)
                            if written <= 0:
                                raise WorkspaceStorageMigrationError("workspace migration target write failed")
                            view = view[written:]
                    os.fsync(destination)
                finally:
                    if source_descriptor is not None:
                        os.close(source_descriptor)
            finally:
                os.close(destination)
            if copied_digest.hexdigest() != source_digest:
                raise WorkspaceStorageMigrationError("workspace migration source changed during copy")
            if not _same_node(_node(source), source_node):
                raise WorkspaceStorageMigrationError("workspace migration source changed during copy")
            _apply_metadata(temporary, source_node)
            verified_temporary = _node(temporary)
            if (
                verified_temporary.st_size != source_node.st_size
                or stat.S_IMODE(verified_temporary.st_mode) != stat.S_IMODE(source_node.st_mode)
                or verified_temporary.st_uid != source_node.st_uid
                or verified_temporary.st_gid != source_node.st_gid
                or _hash_file(temporary, verified_temporary) != source_digest
            ):
                raise WorkspaceStorageMigrationError("workspace migration temporary target verification failed")
            os.replace(temporary, target)
            _fsync_directory(target.parent)
        except WorkspaceStorageMigrationError:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            raise
        except OSError as exc:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            raise WorkspaceStorageMigrationError("workspace migration cannot publish target file") from exc
    else:
        if not stat.S_ISREG(target_node.st_mode) or target_node.st_nlink != 1:
            raise WorkspaceStorageMigrationError("workspace migration target contains an invalid node")
        if _hash_file(target, target_node) != source_digest:
            raise WorkspaceStorageMigrationError("workspace migration target content mismatch")
        if not _same_node(_node(source), source_node):
            raise WorkspaceStorageMigrationError("workspace migration source changed during verification")
        _apply_metadata(target, source_node)

    verified = _node(target)
    if (
        verified.st_size != source_node.st_size
        or stat.S_IMODE(verified.st_mode) != stat.S_IMODE(source_node.st_mode)
        or verified.st_uid != source_node.st_uid
        or verified.st_gid != source_node.st_gid
        or _hash_file(target, verified) != source_digest
    ):
        raise WorkspaceStorageMigrationError("workspace migration target verification failed")
    return source_digest


def _copy_tree(source_root: Path, target_root: Path) -> MigrationInventory:
    inventory = _InventoryBuilder()

    def visit(source: Path, target: Path, relative: str) -> None:
        source_node = _node(source)
        if not stat.S_ISDIR(source_node.st_mode):
            raise WorkspaceStorageMigrationError("workspace migration source root is invalid")
        try:
            target.mkdir(mode=0o700, exist_ok=True)
        except OSError as exc:
            raise WorkspaceStorageMigrationError("workspace migration cannot create target directory") from exc
        target_node = _node(target)
        if not stat.S_ISDIR(target_node.st_mode):
            raise WorkspaceStorageMigrationError("workspace migration target contains an invalid node")
        try:
            with os.scandir(source) as entries:
                children = sorted(entries, key=lambda entry: entry.name)
        except OSError as exc:
            raise WorkspaceStorageMigrationError("workspace migration source cannot be listed") from exc
        for child in children:
            if not relative and (
                child.name in _MARKERS
                or child.name == _MARKER_TEMP_DIRECTORY
                or _marker_temporary_name(child.name)
            ):
                raise WorkspaceStorageMigrationError("workspace migration source uses a reserved marker name")
            child_relative = child.name if not relative else f"{relative}/{child.name}"
            source_child = source / child.name
            target_child = target / child.name
            node = _node(source_child)
            if stat.S_ISDIR(node.st_mode):
                visit(source_child, target_child, child_relative)
            else:
                digest = _copy_or_verify_file(source_child, target_child, node)
                inventory.add(child_relative, node, digest)
        if not _same_node(_node(source), source_node):
            raise WorkspaceStorageMigrationError("workspace migration source changed during traversal")
        _apply_metadata(target, source_node)
        _fsync_directory(target)
        if relative:
            inventory.add(relative, source_node, None)

    visit(source_root, target_root, "")
    return inventory.finish()


def _inventory_tree(root: Path, *, skip_markers: bool = False) -> MigrationInventory:
    inventory = _InventoryBuilder()

    def visit(directory: Path, relative: str) -> None:
        directory_node = _node(directory)
        if not stat.S_ISDIR(directory_node.st_mode):
            raise WorkspaceStorageMigrationError("workspace migration target root is invalid")
        try:
            with os.scandir(directory) as entries:
                children = sorted(entries, key=lambda entry: entry.name)
        except OSError as exc:
            raise WorkspaceStorageMigrationError("workspace migration target cannot be listed") from exc
        for child in children:
            if not relative and (
                child.name in _MARKERS
                or child.name == _MARKER_TEMP_DIRECTORY
                or _marker_temporary_name(child.name)
            ):
                if skip_markers and (
                    child.name in _MARKERS or child.name == _MARKER_TEMP_DIRECTORY
                ):
                    continue
                raise WorkspaceStorageMigrationError("workspace migration source uses a reserved marker name")
            child_relative = child.name if not relative else f"{relative}/{child.name}"
            path = directory / child.name
            node = _node(path)
            if stat.S_ISDIR(node.st_mode):
                visit(path, child_relative)
                inventory.add(child_relative, node, None)
            else:
                inventory.add(child_relative, node, _hash_file(path, node))

    visit(root, "")
    return inventory.finish()


def _write_all(descriptor: int, content: bytes) -> None:
    view = memoryview(content)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise WorkspaceStorageMigrationError("workspace migration marker write failed")
        view = view[written:]


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(path, flags)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except OSError as exc:
        raise WorkspaceStorageMigrationError("workspace migration directory sync failed") from exc


def _write_marker(path: Path, payload: dict[str, object]) -> None:
    staging = path.parent / _MARKER_TEMP_DIRECTORY
    try:
        staging.mkdir(mode=0o700, exist_ok=True)
        staging_node = staging.lstat()
        if (
            stat.S_ISLNK(staging_node.st_mode)
            or not stat.S_ISDIR(staging_node.st_mode)
            or stat.S_IMODE(staging_node.st_mode) & 0o077
        ):
            raise WorkspaceStorageMigrationError("workspace migration marker temporary is invalid")
    except WorkspaceStorageMigrationError:
        raise
    except OSError as exc:
        raise WorkspaceStorageMigrationError("workspace migration marker temporary is unavailable") from exc
    temporary = staging / f"{path.name}.tmp-{os.getpid()}"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(temporary, flags, 0o600)
        try:
            encoded = (json.dumps(payload, sort_keys=True) + "\n").encode("utf-8")
            _write_all(descriptor, encoded)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.replace(temporary, path)
        _fsync_directory(staging)
        try:
            staging.rmdir()
        except OSError:
            pass
        _fsync_directory(path.parent)
    except OSError as exc:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        try:
            staging.rmdir()
        except OSError:
            pass
        raise WorkspaceStorageMigrationError("workspace migration marker write failed") from exc


def _marker_temporary_name(name: str) -> bool:
    return any(name.startswith(prefix) for prefix in _MARKER_TEMP_PREFIXES)


def _remove_stale_marker_temporaries(root: Path) -> None:
    staging = root / _MARKER_TEMP_DIRECTORY
    try:
        staging_node = staging.lstat()
    except FileNotFoundError:
        staging_node = None
    except OSError as exc:
        raise WorkspaceStorageMigrationError("workspace migration marker temporary is unavailable") from exc
    if staging_node is None:
        return
    if (
        stat.S_ISLNK(staging_node.st_mode)
        or not stat.S_ISDIR(staging_node.st_mode)
        or stat.S_IMODE(staging_node.st_mode) & 0o077
    ):
        raise WorkspaceStorageMigrationError("workspace migration marker temporary is invalid")
    removed = False
    try:
        entries = list(os.scandir(staging))
        staging_fd = os.open(staging, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError as exc:
        raise WorkspaceStorageMigrationError("workspace migration marker temporary is unavailable") from exc
    try:
        for entry in entries:
            if not _marker_temporary_name(entry.name):
                raise WorkspaceStorageMigrationError("workspace migration marker temporary is invalid")
            suffix = entry.name.rsplit(".tmp-", 1)[-1]
            if not suffix.isdigit():
                raise WorkspaceStorageMigrationError("workspace migration marker temporary is invalid")
            try:
                node = entry.stat(follow_symlinks=False)
            except OSError as exc:
                raise WorkspaceStorageMigrationError("workspace migration marker temporary is unavailable") from exc
            if stat.S_ISLNK(node.st_mode) or not stat.S_ISREG(node.st_mode) or node.st_nlink != 1:
                raise WorkspaceStorageMigrationError("workspace migration marker temporary is invalid")
            os.unlink(entry.name, dir_fd=staging_fd)
            removed = True
    finally:
        os.close(staging_fd)
    if removed:
        _fsync_directory(staging)
    try:
        staging.rmdir()
    except FileNotFoundError:
        pass
    except OSError:
        if removed:
            raise WorkspaceStorageMigrationError("workspace migration marker temporary cannot be removed")
    _fsync_directory(root)


def _read_marker(path: Path, *, expected_status: str) -> dict[str, object] | None:
    try:
        node = path.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise WorkspaceStorageMigrationError("workspace migration marker is unavailable") from exc
    if not stat.S_ISREG(node.st_mode) or node.st_nlink != 1 or node.st_size > 4096:
        raise WorkspaceStorageMigrationError("workspace migration marker is invalid")
    descriptor = _open_readonly(path)
    try:
        chunks: list[bytes] = []
        total = 0
        while chunk := os.read(descriptor, 4097 - total):
            total += len(chunk)
            if total > 4096:
                raise WorkspaceStorageMigrationError("workspace migration marker is invalid")
            chunks.append(chunk)
    finally:
        os.close(descriptor)
    try:
        payload = json.loads(b"".join(chunks).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WorkspaceStorageMigrationError("workspace migration marker is invalid") from exc
    if (
        not isinstance(payload, dict)
        or payload.get("schema_version") != _SCHEMA_VERSION
        or payload.get("status") != expected_status
    ):
        raise WorkspaceStorageMigrationError("workspace migration marker is invalid")
    return payload


def _marker_inventory(payload: dict[str, object]) -> MigrationInventory:
    try:
        inventory = MigrationInventory(
            directories=int(payload["directories"]),
            files=int(payload["files"]),
            bytes=int(payload["bytes"]),
            digest=str(payload["digest"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise WorkspaceStorageMigrationError("workspace migration inventory marker is invalid") from exc
    if (
        inventory.directories < 0
        or inventory.files < 0
        or inventory.bytes < 0
        or len(inventory.digest) != 64
        or any(character not in "0123456789abcdef" for character in inventory.digest)
    ):
        raise WorkspaceStorageMigrationError("workspace migration inventory marker is invalid")
    return inventory


def _inventory_payload(inventory: MigrationInventory, *, status: str) -> dict[str, object]:
    return {
        "schema_version": _SCHEMA_VERSION,
        "status": status,
        "directories": inventory.directories,
        "files": inventory.files,
        "bytes": inventory.bytes,
        "digest": inventory.digest,
    }


def migrate_workspace_storage(source_root: Path, target_root: Path) -> MigrationInventory:
    source_root = Path(os.path.abspath(source_root))
    target_root = Path(os.path.abspath(target_root))
    if source_root == target_root:
        raise WorkspaceStorageMigrationError("workspace migration source and target must differ")
    _reject_symlinked_path_chain(source_root)
    _reject_symlinked_path_chain(target_root)
    if source_root.is_relative_to(target_root) or target_root.is_relative_to(source_root):
        raise WorkspaceStorageMigrationError("workspace migration source and target must not be nested")
    source_node = _node(source_root)
    if not stat.S_ISDIR(source_node.st_mode):
        raise WorkspaceStorageMigrationError("workspace migration source root is invalid")
    target_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    target_node = _node(target_root)
    if not stat.S_ISDIR(target_node.st_mode):
        raise WorkspaceStorageMigrationError("workspace migration target root is invalid")

    _remove_stale_marker_temporaries(target_root)
    complete_marker = target_root / _COMPLETE_MARKER
    incomplete_marker = target_root / _INCOMPLETE_MARKER
    complete_payload = _read_marker(complete_marker, expected_status="complete")
    incomplete_payload = _read_marker(incomplete_marker, expected_status="incomplete")
    if complete_payload is not None:
        inventory = _marker_inventory(complete_payload)
        source_inventory = _inventory_tree(source_root)
        if source_inventory != inventory:
            raise WorkspaceStorageMigrationError("workspace migration completion inventory mismatch")
        _inventory_tree(target_root, skip_markers=True)
        if incomplete_payload is not None:
            if (
                _marker_inventory(incomplete_payload) != inventory
                or _inventory_tree(target_root, skip_markers=True) != inventory
            ):
                raise WorkspaceStorageMigrationError("workspace migration completion inventory mismatch")
            try:
                incomplete_marker.unlink()
                _fsync_directory(target_root)
            except OSError as exc:
                raise WorkspaceStorageMigrationError("workspace migration cannot finalize markers") from exc
        return inventory

    if incomplete_payload is None:
        try:
            with os.scandir(target_root) as entries:
                unexpected = [entry.name for entry in entries if entry.name not in _MARKERS]
        except OSError as exc:
            raise WorkspaceStorageMigrationError("workspace migration target cannot be listed") from exc
        if unexpected:
            raise WorkspaceStorageMigrationError("workspace migration target is not empty")
        source_inventory = _inventory_tree(source_root)
        _write_marker(
            incomplete_marker,
            _inventory_payload(source_inventory, status="incomplete"),
        )
    else:
        source_inventory = _marker_inventory(incomplete_payload)
        if _inventory_tree(source_root) != source_inventory:
            raise WorkspaceStorageMigrationError("workspace migration source changed after start")

    copied_inventory = _copy_tree(source_root, target_root)
    if copied_inventory != source_inventory:
        raise WorkspaceStorageMigrationError("workspace migration source changed during copy")
    target_inventory = _inventory_tree(target_root, skip_markers=True)
    if target_inventory != source_inventory:
        raise WorkspaceStorageMigrationError("workspace migration inventory mismatch")

    _write_marker(
        complete_marker,
        _inventory_payload(source_inventory, status="complete"),
    )
    try:
        incomplete_marker.unlink()
        _fsync_directory(target_root)
    except OSError as exc:
        raise WorkspaceStorageMigrationError("workspace migration cannot finalize markers") from exc
    return source_inventory


def main() -> int:
    source_root = Path(os.environ.get("WORKSPACE_MIGRATION_SOURCE", "/source-workspaces"))
    target_root = Path(os.environ.get("WORKSPACE_MIGRATION_TARGET", "/target-workspaces"))
    try:
        inventory = migrate_workspace_storage(source_root, target_root)
    except WorkspaceStorageMigrationError as exc:
        print(f"workspace migration failed: {exc}", file=sys.stderr)
        return 1
    print(
        "workspace migration complete: "
        f"directories={inventory.directories} files={inventory.files} "
        f"bytes={inventory.bytes} digest={inventory.digest}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
