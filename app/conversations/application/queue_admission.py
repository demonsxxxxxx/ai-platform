"""Reconcile one chat queue write against its immutable Redis identity."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, TypeVar


TAdmission = TypeVar("TAdmission")


@dataclass(frozen=True)
class ChatQueueDelivery:
    receipt: Any | None = None
    error: Exception | str | None = None
    durable_intent: bool = False


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


async def deliver_accepted_chat_run(
    queue_payload: dict[str, Any],
    *,
    check_existing: bool,
    delivery_intents: Any | None,
    enqueue: Callable[[dict[str, Any]], Awaitable[TAdmission]],
    read: Callable[[dict[str, Any]], Awaitable[TAdmission | None]],
    rejection_type: type[Exception],
) -> ChatQueueDelivery:
    """Deliver the accepted Run without rebuilding its frozen queue identity."""

    if delivery_intents is not None and delivery_intents.enabled:
        from app.execution.api import deliver_run_delivery_intent

        outcome = await deliver_run_delivery_intent(
            delivery_intents,
            tenant_id=str(queue_payload["tenant_id"]),
            run_id=str(queue_payload["run_id"]),
        )
        if outcome.receipt is not None:
            return ChatQueueDelivery(receipt=outcome.receipt, durable_intent=True)
        return ChatQueueDelivery(
            error=outcome.error_code or "queue_admission_unconfirmed",
            durable_intent=True,
        )
    receipt, error = await attempt_chat_queue_admission(
        queue_payload,
        check_existing=check_existing,
        enqueue=enqueue,
        read=read,
        rejection_type=rejection_type,
    )
    return ChatQueueDelivery(receipt=receipt, error=error)
