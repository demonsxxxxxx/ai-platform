"""Private Redis wake-up signal for durable executor reconciliation."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from app.redis_client import get_redis_client

_EXECUTOR_RECONCILIATION_SIGNAL_KEY = "ai-platform:executor-terminal:v1:reconcile"
_EXECUTOR_RECONCILIATION_SIGNAL_MAXLEN = 1024
logger = logging.getLogger(__name__)


class ExecutorSignalUnavailable(RuntimeError):
    pass


def _stream_entry_id(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    milliseconds, separator, sequence = value.partition("-")
    if (
        separator != "-"
        or not milliseconds
        or not sequence
        or not milliseconds.isascii()
        or not sequence.isascii()
        or not milliseconds.isdigit()
        or not sequence.isdigit()
    ):
        return None
    return value


@dataclass(slots=True)
class ExecutorReconciliationSignalCursor:
    """Last signal observed by one reconciler process."""

    last_id: str


async def initialize_executor_reconciliation_signal_cursor() -> ExecutorReconciliationSignalCursor:
    client = None
    operation_failed = False
    try:
        client = get_redis_client()
        rows = await client.xrevrange(
            _EXECUTOR_RECONCILIATION_SIGNAL_KEY,
            max="+",
            min="-",
            count=1,
        )
        if not rows:
            return ExecutorReconciliationSignalCursor("0-0")
        entry_id = _stream_entry_id(
            rows[0][0] if isinstance(rows[0], (tuple, list)) else None
        )
        if entry_id is None:
            raise ValueError("executor_reconciliation_signal_cursor_invalid")
        return ExecutorReconciliationSignalCursor(entry_id)
    except Exception as exc:
        operation_failed = True
        raise ExecutorSignalUnavailable(
            "executor_reconciliation_signal_cursor_unavailable"
        ) from exc
    finally:
        if client is not None:
            await _close_signal_client(client, operation_failed=operation_failed)


async def _close_signal_client(client, *, operation_failed: bool) -> None:
    try:
        await client.aclose()
    except Exception as exc:
        if operation_failed:
            logger.warning("executor_signal_redis_close_failed", exc_info=True)
            return
        raise ExecutorSignalUnavailable("executor_signal_close_unavailable") from exc


async def publish_executor_terminal_signal() -> None:
    client = None
    operation_failed = False
    try:
        client = get_redis_client()
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
        if client is not None:
            await _close_signal_client(client, operation_failed=operation_failed)


async def wait_for_executor_reconciliation_signal(
    *,
    block_ms: int,
    cursor: ExecutorReconciliationSignalCursor,
) -> bool:
    client = None
    operation_failed = False
    try:
        client = get_redis_client()
        after_id = _stream_entry_id(cursor.last_id)
        if after_id is None:
            raise ValueError("executor_reconciliation_signal_cursor_invalid")
        rows = await client.xread(
            {_EXECUTOR_RECONCILIATION_SIGNAL_KEY: after_id},
            count=1,
            block=block_ms,
        )
        if rows:
            entries = rows[-1][1] if isinstance(rows[-1], (tuple, list)) else None
            entry = (
                entries[-1]
                if isinstance(entries, (list, tuple)) and entries
                else None
            )
            entry_id = _stream_entry_id(
                entry[0] if isinstance(entry, (tuple, list)) else None
            )
            if entry_id is None:
                raise ValueError("executor_reconciliation_signal_cursor_invalid")
            cursor.last_id = entry_id
    except Exception as exc:
        operation_failed = True
        raise ExecutorSignalUnavailable("executor_reconciliation_signal_unavailable") from exc
    finally:
        if client is not None:
            await _close_signal_client(client, operation_failed=operation_failed)
    return bool(rows)
