"""Attempt-local artifact publication using no-follow directory descriptors."""

import asyncio
from contextlib import contextmanager
import os
from pathlib import Path
import stat
import uuid

from app.context.domain.artifact_transfer import (
    ArtifactTransferError,
    artifact_workspace_parts,
)


class ArtifactWorkspace:
    def __init__(self):
        self._locks: dict[tuple[str, ...], asyncio.Lock] = {}
        self._published: dict[tuple[str, ...], tuple] = {}

    def lock(self, root, artifact_id, name):
        key = self._key(root, artifact_id, name)
        return self._locks.setdefault(key, asyncio.Lock())

    def _key(self, root, artifact_id, name):
        return (
            str(Path(root).absolute()),
            *artifact_workspace_parts(artifact_id, name),
        )

    @contextmanager
    def target(self, root, artifact_id, name, *, source_identity):
        if os.open not in os.supports_dir_fd or not hasattr(os, "O_NOFOLLOW"):
            raise ArtifactTransferError("context_artifact_safe_filesystem_required")
        parts = artifact_workspace_parts(artifact_id, name)
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        descriptors = []
        try:
            # Walk the root too: a symlink in an ancestor is not authority.
            absolute = Path(root).absolute()
            fd = os.open(absolute.anchor, flags)
            descriptors.append(fd)
            for part in (*absolute.parts[1:], *parts[:-1]):
                try:
                    os.mkdir(part, mode=0o700, dir_fd=fd)
                except FileExistsError:
                    pass
                fd = os.open(part, flags, dir_fd=fd)
                descriptors.append(fd)
            yield ArtifactWorkspaceTarget(
                self, self._key(root, artifact_id, name), fd, parts[-1], source_identity
            )
        finally:
            for fd in reversed(descriptors):
                os.close(fd)


class ArtifactWorkspaceTarget:
    def __init__(self, owner, key, directory_fd, name, source_identity):
        self.owner, self.key, self.directory_fd, self.name = (
            owner,
            key,
            directory_fd,
            name,
        )
        self.source_identity = source_identity
        self.temporary = None

    def reuse(self):
        try:
            node = os.stat(self.name, dir_fd=self.directory_fd, follow_symlinks=False)
        except FileNotFoundError:
            return None
        if (
            not stat.S_ISREG(node.st_mode)
            or node.st_nlink != 1
            or self.owner._published.get(self.key) != self.source_identity
        ):
            raise ArtifactTransferError("context_artifact_target_conflict")
        return node.st_size

    @contextmanager
    def output(self):
        temporary = f".artifact-{uuid.uuid4().hex}.part"
        fd = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=self.directory_fd,
        )
        self.temporary = temporary
        failed = False
        try:
            output = os.fdopen(fd, "wb")
        except BaseException:
            os.close(fd)
            os.unlink(temporary, dir_fd=self.directory_fd)
            self.temporary = None
            raise
        try:
            try:
                yield output
                output.flush()
                os.fsync(output.fileno())
                node = os.fstat(output.fileno())
            except BaseException:
                try:
                    output.close()
                except OSError:
                    pass  # Preserve cancellation or the original write failure.
                raise
            try:
                current = os.stat(
                    temporary, dir_fd=self.directory_fd, follow_symlinks=False
                )
                if (
                    not stat.S_ISREG(current.st_mode)
                    or current.st_nlink != 1
                    or (current.st_dev, current.st_ino) != (node.st_dev, node.st_ino)
                ):
                    raise ArtifactTransferError("context_artifact_target_conflict")
                self.publish(output.fileno())
            except BaseException:
                try:
                    output.close()
                except OSError:
                    pass  # Preserve the publication failure; never roll back a final path.
                raise
            else:
                output.close()
        except BaseException:
            failed = True
            raise
        finally:
            if self.temporary is not None:
                try:
                    os.unlink(self.temporary, dir_fd=self.directory_fd)
                except OSError:
                    if not failed:
                        raise
                self.temporary = None

    def publish(self, verified_fd):
        # Link the still-open verified inode, never the replaceable temporary path.
        try:
            os.link(
                f"/proc/self/fd/{verified_fd}",
                self.name,
                dst_dir_fd=self.directory_fd,
                follow_symlinks=True,
            )
        except FileExistsError:
            raise
        except OSError as exc:
            raise ArtifactTransferError("context_artifact_safe_filesystem_required") from exc
        # link is the publication point; later target edits/replacements are user-owned.
        self.owner._published[self.key] = self.source_identity


def artifact_stage_result(metadata, *, bytes_staged, reused):
    return {
        "artifact_id": metadata["artifact_id"],
        "name": metadata["name"],
        "source_run_id": metadata.get("source_run_id", ""),
        "workspace_path": "/".join(
            artifact_workspace_parts(metadata["artifact_id"], metadata["name"])
        ),
        "bytes_staged": bytes_staged,
        "max_bytes": metadata["max_bytes"],
        "reused": reused,
        "audit": {
            "action": "context_retrieval.stage_run_artifact_to_workspace",
            "bytes_read": 0 if reused else bytes_staged,
            "result": "reused" if reused else "staged",
        },
        "redaction": {"object_locator_refs_removed": True},
    }
