"""Private Redis wake-up signal for durable executor reconciliation."""

from __future__ import annotations

from app.redis_client import get_redis_client

_EXECUTOR_RECONCILIATION_SIGNAL_KEY = "ai-platform:executor-terminal:v1:reconcile"
_EXECUTOR_RECONCILIATION_SIGNAL_MAXLEN = 1024


class ExecutorSignalUnavailable(RuntimeError):
    pass


async def publish_executor_terminal_signal() -> None:
    client = get_redis_client()
    try:
        await client.xadd(
            _EXECUTOR_RECONCILIATION_SIGNAL_KEY,
            {"wake": "1"},
            maxlen=_EXECUTOR_RECONCILIATION_SIGNAL_MAXLEN,
            approximate=True,
        )
    except Exception as exc:
        raise ExecutorSignalUnavailable("executor_terminal_signal_unavailable") from exc
    finally:
        await client.aclose()


async def wait_for_executor_reconciliation_signal(*, block_ms: int) -> bool:
    client = get_redis_client()
    try:
        rows = await client.xread(
            {_EXECUTOR_RECONCILIATION_SIGNAL_KEY: "$"},
            count=1,
            block=block_ms,
        )
    except Exception as exc:
        raise ExecutorSignalUnavailable("executor_reconciliation_signal_unavailable") from exc
    finally:
        await client.aclose()
    return bool(rows)
