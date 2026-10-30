"""Sandbox-owned validation policy for persisted reconciliation checkpoints."""

from __future__ import annotations

import json
from typing import Any


SANDBOX_RECONCILIATION_STAGE_MAX_BYTES = 256 * 1024
_SANDBOX_RECONCILIATION_STAGE_TOO_LARGE = "sandbox_reconciliation_stage_too_large"


class SandboxCheckpointSizeLimitError(ValueError):
    """Raised when a checkpoint stage is invalid JSON or exceeds its byte limit."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def ensure_reconciliation_stage_size(value: Any) -> None:
    """Enforce the persisted Sandbox checkpoint JSON bound using UTF-8 bytes."""
    try:
        serialized = json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        )
        size_bytes = len(serialized.encode("utf-8"))
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        invalid_code = (
            f"{_SANDBOX_RECONCILIATION_STAGE_TOO_LARGE.removesuffix('_too_large')}_invalid"
        )
        raise SandboxCheckpointSizeLimitError(invalid_code) from exc
    if size_bytes > SANDBOX_RECONCILIATION_STAGE_MAX_BYTES:
        raise SandboxCheckpointSizeLimitError(_SANDBOX_RECONCILIATION_STAGE_TOO_LARGE)
