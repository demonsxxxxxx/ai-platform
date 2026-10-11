"""Run a host-bind snapshot without relinquishing its physical work owner."""

from collections.abc import Callable
from typing import Any

from app.sandbox.domain.host_bind import snapshot_host_bind_delivery_files


async def snapshot_with_physical_owner(
    *args: Any, storage_io: Callable[..., Any], completion_factory: Callable[[], Any],
    **kwargs: Any,
) -> Any:
    completion = completion_factory()
    try:
        return await storage_io(
            snapshot_host_bind_delivery_files, *args,
            timeout_seconds=None, completion=completion, **kwargs,
        )
    except BaseException:
        # Admission failure has no submitted job. Once submitted, cancellation
        # cannot release the caller's claim before flock/copy/fsync actually end.
        await completion.settle()
        raise
