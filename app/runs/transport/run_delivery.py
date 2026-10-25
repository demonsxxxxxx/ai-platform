"""Translate request-scoped Run delivery receipts for the HTTP adapters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException

from app.runs.application.control_queue_admission import (
    QueueAdmissionRecoveryFailed,
    QueueAdmissionUnconfirmed,
    ensure_control_queue_admission,
)

@dataclass(frozen=True)
class RunQueueAdmission:
    queue_position: int
    queue_admission_ordinal: int
    message_id: str
    source: str


def delivery_service(request: Any) -> Any | None:
    return getattr(request.app.state, "run_delivery_intents", None)


def bind_queue_insight(normalize_status: Any, lookup: Any) -> Any:
    async def project(status: str, tenant_id: str, *, user_id: str | None = None) -> Any:
        if normalize_status(status) != "queued":
            return None
        return await lookup(tenant_id, user_id=user_id)

    return project


async def record_accepted_run(
    service: Any | None,
    connection: Any,
    payload: dict[str, Any],
    *,
    source: str,
) -> None:
    if service is not None and service.enabled:
        await service.record(connection, payload, source=source)


def admission_from_receipt(receipt: Any) -> RunQueueAdmission:
    return RunQueueAdmission(
        queue_position=int(receipt.queue_position),
        queue_admission_ordinal=int(receipt.queue_admission_ordinal),
        message_id=str(receipt.message_id),
        source=str(receipt.source),
    )


class PermanentQueueDeliveryRejected(RuntimeError):
    pass


async def require_immediate_delivery(
    service: Any,
    *,
    tenant_id: str,
    run_id: str,
) -> int | None:
    delivery = await service.deliver_one(tenant_id=tenant_id, run_id=run_id)
    if delivery.receipt is not None:
        return int(delivery.receipt.queue_position)
    if delivery.error_code == "queue_payload_invalid":
        raise PermanentQueueDeliveryRejected("queue_enqueue_failed")
    return None


async def ensure_control_queue_admission_http(
    queue_payload: dict[str, Any],
    *,
    check_existing: bool,
    principal: Any,
    transaction: Any,
    reauthorize: Any,
    read_queue_admission: Any,
    enqueue_run: Any,
    delivery_intents: Any | None,
    queue_rejected_error: type[BaseException],
) -> Any:
    try:
        return await ensure_control_queue_admission(
            queue_payload,
            check_existing=check_existing,
            principal=principal,
            transaction=transaction,
            reauthorize=reauthorize,
            read_queue_admission=read_queue_admission,
            enqueue_run=enqueue_run,
            delivery_intents=delivery_intents,
            queue_rejected_error=queue_rejected_error,
        )
    except QueueAdmissionRecoveryFailed as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except QueueAdmissionUnconfirmed as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
