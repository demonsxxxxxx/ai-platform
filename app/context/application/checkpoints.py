"""Context-owned checkpoint selection and exact ready loading."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

CheckpointLoader = Callable[..., Awaitable[dict[str, Any] | None]]
_loader: CheckpointLoader | None = None


def configure_checkpoint_loader(loader: CheckpointLoader) -> None:
    global _loader
    _loader = loader


async def load_ready_checkpoint(conn: Any, *, scope: dict[str, str], run_id: str,
                                checkpoint_id: str | None = None) -> dict[str, Any] | None:
    if _loader is None:
        raise ValueError("conversation_checkpoint_loader_unavailable")
    return await _loader(conn, scope=scope, run_id=run_id, checkpoint_id=checkpoint_id)
