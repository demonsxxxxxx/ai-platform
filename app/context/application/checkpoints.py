"""Context-owned checkpoint selection and exact ready loading."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

CheckpointLoader = Callable[..., Awaitable[dict[str, Any] | None]]
CheckpointUsageLoader = Callable[..., Awaitable[dict[str, Any]]]
_loader: CheckpointLoader | None = None
_usage_loader: CheckpointUsageLoader | None = None


def configure_checkpoint_loader(loader: CheckpointLoader) -> None:
    global _loader
    _loader = loader


def configure_checkpoint_usage_loader(loader: CheckpointUsageLoader) -> None:
    global _usage_loader
    _usage_loader = loader


async def load_checkpoint_usage_for_run(conn: Any, *, tenant_id: str,
                                        run_id: str) -> dict[str, Any]:
    if _usage_loader is None:
        raise ValueError("conversation_checkpoint_usage_loader_unavailable")
    return await _usage_loader(conn, tenant_id=tenant_id, run_id=run_id)


async def load_ready_checkpoint(conn: Any, *, scope: dict[str, str], run_id: str,
                                checkpoint_id: str | None = None) -> dict[str, Any] | None:
    if _loader is None:
        raise ValueError("conversation_checkpoint_loader_unavailable")
    return await _loader(conn, scope=scope, run_id=run_id, checkpoint_id=checkpoint_id)
