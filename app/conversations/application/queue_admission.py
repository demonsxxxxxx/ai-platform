"""Reconcile one chat queue write against its immutable Redis identity."""

from collections.abc import Awaitable, Callable
from typing import Any, TypeVar


TAdmission = TypeVar("TAdmission")


async def attempt_chat_queue_admission(
    queue_payload: dict[str, Any],
    *,
    check_existing: bool,
    enqueue: Callable[[dict[str, Any]], Awaitable[TAdmission]],
    read: Callable[[dict[str, Any]], Awaitable[TAdmission | None]],
    rejection_type: type[Exception],
) -> tuple[TAdmission | None, Exception | None]:
    """Preserve an ambiguous queue write unless exact Redis readback confirms it."""

    try:
        if check_existing:
            existing = await read(queue_payload)
            if existing is not None:
                return existing, None
        return await enqueue(queue_payload), None
    except Exception as exc:  # noqa: BLE001 - external queue outcome may be unknown
        if not isinstance(exc, rejection_type):
            try:
                existing = await read(queue_payload)
            except Exception:  # noqa: BLE001 - bounded best-effort reconciliation only
                existing = None
            if existing is not None:
                return existing, None
        return None, exc
