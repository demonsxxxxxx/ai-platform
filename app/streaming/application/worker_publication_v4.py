"""Application-owned v4 worker admission and post-commit publication."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Any, Protocol

from app.streaming.application.callback_events_v4 import V4CallbackItem
from app.streaming.application.durable_v4 import (
    V4PendingAdmission,
    V4PendingAdmissionPort,
    V4PublicationTransport,
    V4PublicationStreamExpired,
    V4PublicationTransportUnavailable,
    validate_v4_transport_receipt,
)


from app.streaming.domain.public_events_v4 import V4ProjectionError, project_public_v4

TransactionFactory = Callable[[], AbstractAsyncContextManager[Any]]






@dataclass(frozen=True, slots=True)
class ReconstructedAssistantAnswer:
    """Complete public answer reconstructed from durable v4 deltas."""

    text: str


class AssistantAnswerReceiptError(ValueError):
    """A terminal answer receipt cannot be reconciled with durable v4 rows."""

    code = "assistant_answer_receipt_invalid"

    def __init__(self, *, retryable: bool = False) -> None:
        self.retryable = retryable
        super().__init__(self.code)


class WorkerEventPersistence(Protocol):
    async def append_terminal_row(
        self,
        conn: Any,
        *,
        tenant_id: str,
        run_id: str,
    ) -> Any | None: ...

    async def append_callback_rows(
        self,
        conn: Any,
        *,
        tenant_id: str,
        run_id: str,
        attempt_id: str,
        batch_id: str,
        items: tuple[V4CallbackItem, ...],
        authority: Any,
        execution_lease_id: str,
    ) -> tuple[Any, ...]: ...

    async def load_answer_by_receipt(
        self,
        conn: Any,
        *,
        tenant_id: str,
        run_id: str,
        attempt_id: str,
        receipt: Mapping[str, object],
    ) -> ReconstructedAssistantAnswer: ...

    async def load_latest_run_event(
        self, *, tenant_id: str, run_id: str, before_sequence: int | None = None
    ) -> bytes | None: ...

    async def persist_event_and_check_cancel(
        self,
        *,
        run_payload: Any,
        persist_event: bool,
        event_type: str,
        stage: str,
        message: str,
        payload: dict[str, Any] | None,
        record_run_step: Callable[..., Awaitable[None]],
    ) -> bool: ...


@dataclass(frozen=True, slots=True)
class WorkerV4Capabilities:
    """Explicit worker capabilities composed by the process owner."""

    pending_admissions: V4PendingAdmissionPort
    event_persistence: WorkerEventPersistence
    publication_transport: V4PublicationTransport


async def append_run_terminal_v4_row(
    capabilities: WorkerV4Capabilities,
    conn: Any,
    *,
    tenant_id: str,
    run_id: str,
    did_transition: bool,
) -> Any | None:
    """Append one terminal row only for the transition owned by this transaction."""

    if not did_transition:
        return None
    return await capabilities.event_persistence.append_terminal_row(
        conn,
        tenant_id=tenant_id,
        run_id=run_id,
    )


async def append_callback_v4_rows(
    capabilities: WorkerV4Capabilities,
    conn: Any,
    *,
    tenant_id: str,
    run_id: str,
    attempt_id: str,
    batch_id: str,
    items: tuple[V4CallbackItem, ...],
    authority: Any,
    execution_lease_id: str,
) -> tuple[Any, ...]:
    """Append callback rows through the explicitly composed worker port."""

    return await capabilities.event_persistence.append_callback_rows(
        conn,
        tenant_id=tenant_id,
        run_id=run_id,
        attempt_id=attempt_id,
        batch_id=batch_id,
        items=items,
        authority=authority,
        execution_lease_id=execution_lease_id,
    )


async def publish_callback_rows(
    capabilities: WorkerV4Capabilities,
    rows: tuple[Mapping[str, object], ...],
    *,
    authority: Any,
) -> None:
    """Publish exactly the committed callback batch before acknowledging it."""
    if not rows:
        return
    envelopes = []
    for row in rows:
        envelope = project_public_v4(row, authority=authority)
        if envelope is None:
            raise V4ProjectionError("callback_committed_batch_invalid")
        envelopes.append(envelope)
    predecessor = await capabilities.event_persistence.load_latest_run_event(
        tenant_id=authority.tenant_id, run_id=authority.run_id,
        before_sequence=envelopes[0]["seq"],
    )
    if predecessor is not None:
        try:
            validate_v4_transport_receipt(await capabilities.publication_transport.publish(predecessor))
        except V4PublicationStreamExpired as exc:
            raise V4PublicationTransportUnavailable("callback_stream_expired") from exc
    redis_id = await capabilities.publication_transport.publish_callback_batch(tuple(envelopes))
    validate_v4_transport_receipt(redis_id)


async def admit_v4_stream(
    capabilities: WorkerV4Capabilities,
    *,
    tenant_id: str,
    run_id: str,
    attempt_id: str,
) -> Any:
    """Create, publish, and confirm v4 admission before executor dispatch."""

    pending = await capabilities.pending_admissions.prepare_pending_authority(
        tenant_id=tenant_id,
        run_id=run_id,
        attempt_id=attempt_id,
    )
    try:
        redis_id = await capabilities.publication_transport.publish(
            pending.open_payload_bytes
        )
        validate_v4_transport_receipt(redis_id)
    except V4PublicationTransportUnavailable:
        raise
    return await capabilities.pending_admissions.confirm_pending_admission(
        pending,
        redis_id=redis_id,
    )






async def persist_worker_event(
    capabilities: WorkerV4Capabilities,
    *,
    run_payload: Any,
    persist_event: bool,
    event_type: str,
    stage: str,
    message: str,
    payload: dict[str, Any] | None,
    record_run_step: Callable[..., Awaitable[None]],
) -> bool:
    """Persist the business event and inspect cancellation in its transaction."""

    return await capabilities.event_persistence.persist_event_and_check_cancel(
        run_payload=run_payload,
        persist_event=persist_event,
        event_type=event_type,
        stage=stage,
        message=message,
        payload=payload,
        record_run_step=record_run_step,
    )


async def drain_pending_v4_events(
    capabilities: WorkerV4Capabilities,
    *,
    tenant_id: str,
    run_id: str,
    attempt_id: str,
) -> int:
    """Drain the current attempt's committed v4 rows before terminal hydration."""

    published = 0
    while True:
        batch_published = await publish_pending_v4_events(
            capabilities,
            tenant_id=tenant_id,
            run_id=run_id,
            attempt_id=attempt_id,
        )
        published += batch_published
        if batch_published == 0:
            return published


async def finalize_parent_and_publish(
    transaction_factory: TransactionFactory,
    capabilities: WorkerV4Capabilities,
    finalize_parent: Callable[[TransactionFactory, Any, Any], Awaitable[Any]],
    payload: Any,
    reconciled_parent: Any,
) -> None:
    finalized_parent = await finalize_parent(transaction_factory, payload, reconciled_parent)
    await publish_run_event(
        capabilities,
        tenant_id=payload.tenant_id,
        run_id=payload.run_id,
    )
    parent_run_id = (
        finalized_parent.get("parent_run_id")
        if isinstance(finalized_parent, dict)
        else getattr(finalized_parent, "parent_run_id", None)
    )
    if isinstance(parent_run_id, str) and parent_run_id and parent_run_id != payload.run_id:
        await publish_run_event(
            capabilities,
            tenant_id=payload.tenant_id,
            run_id=parent_run_id,
        )


async def publish_run_event(
    capabilities: WorkerV4Capabilities,
    *,
    tenant_id: str,
    run_id: str,
) -> bool:
    """Publish a committed Run fact without a publication queue or disposition."""

    envelope = await capabilities.event_persistence.load_latest_run_event(
        tenant_id=tenant_id, run_id=run_id,
    )
    if envelope is None:
        return False
    try:
        receipt = await capabilities.publication_transport.publish(envelope)
        validate_v4_transport_receipt(receipt)
    except (V4PublicationTransportUnavailable, V4PublicationStreamExpired):
        # Business finalization is already committed. SSE closes on authoritative
        # terminal state and the existing frontend hydration supplies the result.
        return False
    return True


__all__ = [
    "AssistantAnswerReceiptError",
    "ReconstructedAssistantAnswer",
    "V4PendingAdmission",
    "V4PendingAdmissionPort",
    "WorkerEventPersistence",
    "WorkerV4Capabilities",
    "admit_v4_stream",
    "drain_pending_v4_events",
    "finalize_parent_and_publish",
    "persist_worker_event",
    "publish_run_event",
]
