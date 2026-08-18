"""Private Redis wake-up signal for durable executor reconciliation."""

from __future__ import annotations

import logging

from app.redis_client import get_redis_client

_EXECUTOR_RECONCILIATION_SIGNAL_KEY = "ai-platform:executor-terminal:v1:reconcile"
_EXECUTOR_RECONCILIATION_SIGNAL_MAXLEN = 1024
logger = logging.getLogger(__name__)


class ExecutorSignalUnavailable(RuntimeError):
    pass


async def _close_signal_client(client, *, operation_failed: bool) -> None:
    try:
        await client.aclose()
    except Exception as exc:
        if operation_failed:
            logger.warning("executor_signal_redis_close_failed", exc_info=True)
            return
        raise ExecutorSignalUnavailable("executor_signal_close_unavailable") from exc


async def publish_executor_terminal_signal() -> None:
    client = get_redis_client()
    operation_failed = False
    try:
        await client.xadd(
            _EXECUTOR_RECONCILIATION_SIGNAL_KEY,
            {"wake": "1"},
            maxlen=_EXECUTOR_RECONCILIATION_SIGNAL_MAXLEN,
            approximate=True,
        )
    except Exception as exc:
        operation_failed = True
        raise ExecutorSignalUnavailable("executor_terminal_signal_unavailable") from exc
    finally:
        await _close_signal_client(client, operation_failed=operation_failed)


async def wait_for_executor_reconciliation_signal(*, block_ms: int) -> bool:
    client = get_redis_client()
    operation_failed = False
    try:
        rows = await client.xread(
            {_EXECUTOR_RECONCILIATION_SIGNAL_KEY: "$"},
            count=1,
            block=block_ms,
        )
    except Exception as exc:
        operation_failed = True
        raise ExecutorSignalUnavailable("executor_reconciliation_signal_unavailable") from exc
    finally:
        await _close_signal_client(client, operation_failed=operation_failed)
    return bool(rows)
