"""Worker-local cache for exact, already-authorized attachment bytes.

Only Attempt copies are mounted into executors. Cache entries are never linked
into a workspace, and a cache hit is not proof of authorization or integrity.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile
from typing import Any

# Direct-mapped, bounded disk use: one committed object per slot. Collisions
# cause misses, not data reuse; the caller verifies the exact digest on every hit.
_SLOT_COUNT = 32
_SHA256 = re.compile(r"[0-9a-f]{64}")


class AttachmentCache:
    def __init__(self, root: Path, *, scope: tuple[str, str, str, str]):
        self.root = root
        self.scope = scope

    def _slot(self, file_id: str, row: dict[str, Any]) -> tuple[Path, bytes, str] | None:
        digest = str(row.get("sha256") or "").casefold()
        if not _SHA256.fullmatch(digest):
            return None
        key = hashlib.sha256(json.dumps([*self.scope, file_id, digest]).encode()).digest()
        return self.root / f"{int.from_bytes(key[:4]) % _SLOT_COUNT:02d}", key, digest

    def restore(self, file_id: str, row: dict[str, Any], target: Path, size_bytes: int) -> bool:
        entry = self._slot(file_id, row)
        if entry is None or self.root.is_symlink():
            return False
        slot, key, expected_digest = entry
        if slot.is_symlink():
            return False
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        try:
            fd = os.open(slot, flags)
        except OSError:
            return False
        digest = hashlib.sha256()
        remaining = size_bytes
        created = False
        try:
            with os.fdopen(fd, "rb") as cached:
                info = os.fstat(cached.fileno())
                if (not stat.S_ISREG(info.st_mode) or info.st_size != len(key) + size_bytes
                        or cached.read(len(key)) != key):
                    return False
                with target.open("xb") as staged:
                    created = True
                    while remaining:
                        chunk = cached.read(min(remaining, 1024 * 1024))
                        if not chunk:
                            break
                        staged.write(chunk)
                        digest.update(chunk)
                        remaining -= len(chunk)
                    if not remaining and not cached.read(1) and digest.hexdigest() == expected_digest:
                        return True
        except FileExistsError:
            raise
        except OSError:
            pass
        if created:
            target.unlink(missing_ok=True)
        return False

    def remember(self, file_id: str, row: dict[str, Any], staged: Path) -> None:
        entry = self._slot(file_id, row)
        if entry is None:
            return
        path, key, _ = entry
        temporary: Path | None = None
        try:
            self.root.mkdir(mode=0o700, exist_ok=True)
            if self.root.is_symlink() or (os.name == "posix" and self.root.stat().st_mode & 0o077):
                return
            with tempfile.NamedTemporaryFile(dir=self.root, delete=False) as pending:
                temporary = Path(pending.name)
                pending.write(key)
                with staged.open("rb") as source:
                    shutil.copyfileobj(source, pending, length=1024 * 1024)
            # Readers see the complete old or complete new object, never a partial copy.
            temporary.replace(path)
        except OSError:
            # Cache writes must not make an otherwise valid attachment unavailable.
            pass
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass
