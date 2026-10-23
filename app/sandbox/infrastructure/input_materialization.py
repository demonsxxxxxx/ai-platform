"""Invocation-owned, inode-preserving publication of validated input files.

This is per-file atomic publication, not a filesystem batch transaction. The
caller must settle all physical I/O before unwinding this owner. Concurrent
workspace teardown and noncooperating writers are outside this ownership seam.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
import shutil
import stat
import uuid

_LOGGER = logging.getLogger(__name__)


def _directory_flags() -> int:
    if (
        os.name != "posix"
        or not getattr(os, "O_NOFOLLOW", 0)
        or not getattr(os, "O_DIRECTORY", 0)
        or os.open not in os.supports_dir_fd
        or os.link not in os.supports_dir_fd
    ):
        raise OSError("secure input publication is unavailable")
    return os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)


def _open_directory(path: Path) -> int:
    """Anchor each ancestor without following a symlink."""
    path = path.absolute()
    descriptor = os.open(path.anchor, _directory_flags())
    try:
        for component in path.parts[1:]:
            if component in {"", ".", ".."}:
                raise ValueError("invalid workspace directory")
            child = os.open(component, _directory_flags(), dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _identity(descriptor: int) -> tuple[int, int]:
    node = os.fstat(descriptor)
    return node.st_dev, node.st_ino


class InputMaterializationBatch:
    """Keep staged and published inode ownership separate from path existence."""

    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace
        self.workspace_fd = _open_directory(workspace)
        self.parent_fd = -1
        self.stage_fd = -1
        self.inputs_fd = -1
        self.stage_name: str | None = None
        self.created_inputs = False
        self.publishing_name: str | None = None
        self.published: list[tuple[str, tuple[int, int]]] = []
        try:
            self.parent_fd = _open_directory(workspace.absolute().parent)
            try:
                self.inputs_fd = os.open(
                    "inputs", _directory_flags(), dir_fd=self.workspace_fd
                )
            except FileNotFoundError:
                pass
        except BaseException:
            self.close()
            raise

    def assert_available(self, name: str) -> None:
        if self.inputs_fd < 0:
            return
        try:
            node = os.stat(name, dir_fd=self.inputs_fd, follow_symlinks=False)
            if stat.S_ISLNK(node.st_mode):
                raise OSError("input target is a symlink")
        except FileNotFoundError:
            return
        raise FileExistsError("input target already exists")

    def start_staging(self) -> None:
        # Outside workspace, hence outside both executor/native-tool workspace mounts.
        name = ".input-stage-" + uuid.uuid4().hex
        os.mkdir(name, mode=0o700, dir_fd=self.parent_fd)
        self.stage_name = name
        self.stage_fd = os.open(
            self.stage_name, _directory_flags(), dir_fd=self.parent_fd
        )
        os.fchmod(self.stage_fd, 0o700)

    def _create_file(self, name: str) -> int:
        return os.open(
            name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o644,
            dir_fd=self.stage_fd,
        )

    def copy_file(self, name: str, source: Path) -> None:
        with (
            source.open("rb") as incoming,
            os.fdopen(self._create_file(name), "wb") as output,
        ):
            shutil.copyfileobj(incoming, output, length=64 * 1024)

    def write_file(self, name: str, content: bytes) -> None:
        with os.fdopen(self._create_file(name), "wb") as output:
            output.write(content)

    def read_file(self, name: str) -> bytes:
        descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=self.stage_fd)
        with os.fdopen(descriptor, "rb") as incoming:
            return incoming.read()

    def _assert_workspace_identity(self) -> None:
        descriptor = _open_directory(self.workspace)
        try:
            if _identity(descriptor) != _identity(self.workspace_fd):
                raise OSError("workspace identity changed")
        finally:
            os.close(descriptor)

    def publish(self, names: list[str]) -> None:
        """No await: cancellation is admitted before this bounded commit section."""
        self._assert_workspace_identity()
        if self.inputs_fd < 0:
            os.mkdir("inputs", mode=0o755, dir_fd=self.workspace_fd)
            self.created_inputs = True
            self.inputs_fd = os.open(
                "inputs", _directory_flags(), dir_fd=self.workspace_fd
            )
        current = os.stat("inputs", dir_fd=self.workspace_fd, follow_symlinks=False)
        if (current.st_dev, current.st_ino) != _identity(self.inputs_fd):
            raise OSError("inputs identity changed")
        for name in names:
            self.publishing_name = name
            node = os.stat(name, dir_fd=self.stage_fd, follow_symlinks=False)
            if not stat.S_ISREG(node.st_mode):
                raise OSError("staged input is not a regular file")
            # link is same-device, complete-inode, and refuses any existing target.
            os.link(
                name,
                name,
                src_dir_fd=self.stage_fd,
                dst_dir_fd=self.inputs_fd,
                follow_symlinks=False,
            )
            self.published.append((name, (node.st_dev, node.st_ino)))

    def rollback(self) -> None:
        for name, identity in reversed(self.published):
            try:
                node = os.stat(name, dir_fd=self.inputs_fd, follow_symlinks=False)
                if (node.st_dev, node.st_ino) == identity:
                    os.unlink(name, dir_fd=self.inputs_fd)
            except FileNotFoundError:
                pass
            except OSError:
                _LOGGER.warning("input_materialization_rollback_failed")
        if self.created_inputs and self.inputs_fd >= 0:
            try:
                node = os.stat(
                    "inputs", dir_fd=self.workspace_fd, follow_symlinks=False
                )
                if (node.st_dev, node.st_ino) == _identity(self.inputs_fd):
                    os.rmdir("inputs", dir_fd=self.workspace_fd)
            except FileNotFoundError:
                pass
            except OSError:
                _LOGGER.warning("input_materialization_rollback_failed")

    def close(self) -> None:
        """Cleanup cannot replace a validation/storage/cancellation failure."""
        if self.stage_fd >= 0:
            try:
                for name in os.listdir(self.stage_fd):
                    os.unlink(name, dir_fd=self.stage_fd)
            except OSError:
                _LOGGER.warning("input_materialization_private_cleanup_failed")
        if self.stage_name is not None:
            try:
                os.rmdir(self.stage_name, dir_fd=self.parent_fd)
            except OSError:
                _LOGGER.warning("input_materialization_private_cleanup_failed")
            self.stage_name = None
        for attribute in ("stage_fd", "inputs_fd", "parent_fd", "workspace_fd"):
            descriptor = getattr(self, attribute, -1)
            if descriptor >= 0:
                try:
                    os.close(descriptor)
                except OSError:
                    _LOGGER.warning("input_materialization_descriptor_cleanup_failed")
                setattr(self, attribute, -1)
