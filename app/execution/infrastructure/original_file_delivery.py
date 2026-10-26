"""Materialize opaque MCP original files into one bound Agent workspace."""

from __future__ import annotations

import asyncio
import os
import secrets
from pathlib import Path
from typing import Any

from app.context.api import (
    MAX_CONTEXT_FILE_STAGE_BYTES,
    MAX_CONTEXT_FILE_STAGE_TOTAL_BYTES,
    MAX_PRIMARY_FILE_IDS,
)
from app.kernel.identifiers import assert_safe_id, assert_safe_principal_user_id
from app.path_safety import ensure_creatable_inside, filesystem_component_fits

_MAX_INPUT_FILE_COUNT = MAX_PRIMARY_FILE_IDS
_MAX_INPUT_TOTAL_BYTES = MAX_CONTEXT_FILE_STAGE_TOTAL_BYTES
_MAX_FILE_BYTES = min(8 * 1024 * 1024, MAX_CONTEXT_FILE_STAGE_BYTES)


class OriginalFileDeliveryError(ValueError):
    """Raised when an opaque remote file cannot be safely delivered locally."""


class OriginalFileDelivery:
    """Own one exact Agent cwd and Run/Attempt binding for opaque file writes."""

    def __init__(
        self,
        *,
        cwd: Path,
        tenant_id: str,
        workspace_id: str,
        user_id: str,
        session_id: str,
        run_id: str,
        attempt_id: str,
        lease_id: str = "",
        max_total_bytes: int = _MAX_INPUT_TOTAL_BYTES,
    ) -> None:
        resolved_cwd = Path(cwd)
        if resolved_cwd.is_symlink() or not resolved_cwd.is_dir():
            raise OriginalFileDeliveryError("original_file_workspace_invalid")
        self._cwd = resolved_cwd.resolve(strict=True)
        self._binding = (
            assert_safe_id(tenant_id, "tenant_id"),
            assert_safe_id(workspace_id, "workspace_id"),
            assert_safe_principal_user_id(user_id),
            assert_safe_id(session_id, "session_id"),
            assert_safe_id(run_id, "run_id"),
            assert_safe_id(attempt_id, "attempt_id"),
            assert_safe_id(lease_id, "lease_id") if lease_id else "",
        )
        if type(max_total_bytes) is not int or max_total_bytes < 0:
            raise ValueError("original_file_total_limit_invalid")
        self._max_total_bytes = min(max_total_bytes, _MAX_INPUT_TOTAL_BYTES)
        self._lock = asyncio.Lock()
        self._owned_paths: set[Path] = set()

    @property
    def binding(self) -> tuple[str, str, str, str, str, str, str]:
        return self._binding

    @staticmethod
    def _ensure_creatable(root: Path, path: Path) -> None:
        try:
            ensure_creatable_inside(root, path, "original_file_workspace_escape")
        except (OSError, ValueError) as exc:
            raise OriginalFileDeliveryError("original_file_workspace_escape") from exc

    @staticmethod
    def _safe_name(value: object) -> str:
        raw = str(value or "").replace("\\", "/").rsplit("/", 1)[-1]
        candidate = "".join(
            character if character.isalnum() or character in "._-" else "_"
            for character in raw
        ).strip(" .")
        if candidate in {"", ".", ".."}:
            raise OriginalFileDeliveryError("original_file_name_invalid")
        if not filesystem_component_fits(candidate):
            raise OriginalFileDeliveryError("original_file_name_invalid")
        return candidate

    def _inputs_usage(self) -> tuple[int, int]:
        inputs = self._cwd / "inputs"
        if not inputs.exists() or inputs.is_symlink():
            return 0, 0
        count = 0
        total = 0
        pending = [inputs]
        while pending:
            directory = pending.pop()
            try:
                with os.scandir(directory) as entries:
                    for entry in entries:
                        if entry.is_symlink():
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            pending.append(Path(entry.path))
                        elif entry.is_file(follow_symlinks=False):
                            count += 1
                            total += entry.stat(follow_symlinks=False).st_size
            except OSError as exc:
                raise OriginalFileDeliveryError("original_file_input_usage_unavailable") from exc
        return count, total

    async def __call__(self, metadata: dict[str, Any], raw_bytes: bytes) -> dict[str, Any]:
        if not isinstance(metadata, dict) or not isinstance(raw_bytes, bytes):
            raise OriginalFileDeliveryError("original_file_delivery_invalid")
        if not self._binding[-1]:
            raise OriginalFileDeliveryError("original_file_binding_invalid")
        declared_size = metadata.get("size_bytes")
        if type(declared_size) is not int or declared_size != len(raw_bytes):
            raise OriginalFileDeliveryError("original_file_size_mismatch")
        if not 0 <= declared_size <= _MAX_FILE_BYTES:
            raise OriginalFileDeliveryError("original_file_too_large")
        async with self._lock:
            inputs = self._cwd / "inputs"
            self._ensure_creatable(self._cwd, inputs)
            try:
                inputs.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                raise OriginalFileDeliveryError("original_file_delivery_failed") from exc
            count, total = self._inputs_usage()
            if count >= _MAX_INPUT_FILE_COUNT or total + len(raw_bytes) > self._max_total_bytes:
                raise OriginalFileDeliveryError("original_file_input_quota_exceeded")
            safe_name = self._safe_name(metadata.get("name"))
            owned_root = inputs / ".mcp-original"
            self._ensure_creatable(inputs, owned_root)
            try:
                owned_root.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                raise OriginalFileDeliveryError("original_file_delivery_failed") from exc
            self._ensure_creatable(inputs, owned_root)
            target_dir = owned_root / secrets.token_hex(12)
            target = target_dir / safe_name
            self._ensure_creatable(owned_root, target_dir)
            try:
                target_dir.mkdir(parents=False, exist_ok=False)
            except OSError as exc:
                raise OriginalFileDeliveryError("original_file_delivery_failed") from exc
            self._ensure_creatable(owned_root, target)
            self._owned_paths.add(target_dir)
            temporary = target_dir / f".{safe_name}.{secrets.token_hex(8)}.part"
            committed = False
            try:
                with temporary.open("xb") as stream:
                    stream.write(raw_bytes)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.link(temporary, target)
                temporary.unlink()
                os.chmod(target, 0o444)
                committed = True
            except FileExistsError as exc:
                raise OriginalFileDeliveryError("original_file_name_conflict") from exc
            except OSError as exc:
                raise OriginalFileDeliveryError("original_file_delivery_failed") from exc
            finally:
                temporary.unlink(missing_ok=True)
                if not committed:
                    try:
                        self._remove_owned_tree(target_dir)
                        self._owned_paths.discard(target_dir)
                    except OSError:
                        # Keep the path tracked so the executor can report and retry cleanup.
                        pass
            return {
                "workspace_path": target.relative_to(self._cwd).as_posix(),
                "format": metadata.get("format"),
                "source": metadata.get("source"),
                "name": safe_name,
                "size_bytes": len(raw_bytes),
                "content_type": metadata.get("content_type"),
            }

    @staticmethod
    def _remove_owned_tree(path: Path) -> None:
        """Remove only this delivery subtree; never follow symlinked children."""

        if path.is_symlink() or path.is_file():
            if not path.is_symlink():
                os.chmod(path, 0o666)
            path.unlink(missing_ok=True)
            return
        if not path.is_dir():
            return
        with os.scandir(path) as entries:
            for entry in entries:
                child = Path(entry.path)
                if entry.is_symlink():
                    child.unlink(missing_ok=True)
                elif entry.is_dir(follow_symlinks=False):
                    OriginalFileDelivery._remove_owned_tree(child)
                else:
                    os.chmod(child, 0o666)
                    child.unlink(missing_ok=True)
        os.chmod(path, 0o777)
        path.rmdir()

    @property
    def cleanup_incomplete(self) -> bool:
        return bool(self._owned_paths)

    async def cleanup(self) -> bool:
        async with self._lock:
            remaining: set[Path] = set()
            for directory in sorted(self._owned_paths, key=lambda path: len(path.parts), reverse=True):
                try:
                    self._remove_owned_tree(directory)
                except OSError:
                    remaining.add(directory)
            self._owned_paths = remaining
            return not remaining
