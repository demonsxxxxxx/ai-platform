"""Recover a committed retry/resume queue admission by its immutable Run identity."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.execution.api import deliver_run_delivery_intent

@dataclass(frozen=True)
class QueueAdmission:
    queue_position: int
    queue_admission_ordinal: int
    message_id: str
    source: str


class QueueAdmissionUnconfirmed(RuntimeError):
    pass


class QueueAdmissionRecoveryFailed(RuntimeError):
    pass


async def ensure_control_queue_admission(
    queue_payload: dict[str, Any],
    *,
    check_existing: bool,
    principal: Any,
    transaction: Callable[..., Any],
    reauthorize: Callable[..., Any],
    read_queue_admission: Callable[..., Any],
    enqueue_run: Callable[..., Any],
    delivery_intents: Any | None,
    queue_rejected_error: type[BaseException],
) -> Any:
    if delivery_intents is not None and delivery_intents.enabled:
        # This Run child was already accepted with a frozen execution snapshot.
        # Transport recovery must preserve that decision, not reauthorize or
        # rebuild it from mutable profile state.
        delivery = await deliver_run_delivery_intent(
            delivery_intents,
            tenant_id=principal.tenant_id,
            run_id=str(queue_payload["run_id"]),
        )
        if delivery.receipt is None:
            raise QueueAdmissionUnconfirmed("queue_admission_unconfirmed")
        receipt = delivery.receipt
        return QueueAdmission(
            queue_position=int(receipt.queue_position),
            queue_admission_ordinal=int(receipt.queue_admission_ordinal),
            message_id=str(receipt.message_id),
            source=str(receipt.source),
        )

    async with transaction() as conn:
        await reauthorize(
            conn,
            principal=principal,
            run_id=str(queue_payload["run_id"]),
        )
        if check_existing:
            try:
                existing = await read_queue_admission(queue_payload)
            except queue_rejected_error as exc:
                raise QueueAdmissionRecoveryFailed(
                    "queue_admission_recovery_failed"
                ) from exc
            except Exception:
                existing = None
            if existing is not None:
                return existing

        try:
            position = await enqueue_run(queue_payload)
            return QueueAdmission(int(position), 0, "", "idempotent_enqueue")
        except Exception as enqueue_error:
            try:
                recovered = await read_queue_admission(queue_payload)
            except Exception:
                recovered = None
            if recovered is not None:
                return recovered
            raise QueueAdmissionUnconfirmed(
                "queue_admission_unconfirmed"
            ) from enqueue_error
