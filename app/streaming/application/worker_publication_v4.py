"""Application-owned v4 worker admission and post-commit publication."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Protocol

from app.streaming.application.durable_v4 import (
    V4PendingAdmission,
    V4PendingAdmissionPort,
    V4PublicationClaims,
    V4PublicationTransport,
    V4PublicationTransportUnavailable,
    publish_claimed_v4_events,
    publish_pending_v4_admissions,
    validate_v4_transport_receipt,
)


TransactionFactory = Callable[[], AbstractAsyncContextManager[Any]]


@dataclass(frozen=True, slots=True)
class V4StreamAuthority:
    attempt_id: str
    stream_incarnation: int


class V4StreamAuthorityLookup(Protocol):
    async def get(self, *, tenant_id: str, run_id: str) -> V4StreamAuthority | None: ...


class WorkerEventPersistence(Protocol):
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

    authority: V4StreamAuthorityLookup
    pending_admissions: V4PendingAdmissionPort
    event_persistence: WorkerEventPersistence
    publication_claims: V4PublicationClaims
    publication_transport: V4PublicationTransport


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


async def publish_pending_admissions(
    capabilities: WorkerV4Capabilities,
    *,
    limit: int,
) -> int:
    return await publish_pending_v4_admissions(
        capabilities.pending_admissions,
        capabilities.publication_transport,
        limit=limit,
    )


async def publish_pending_v4_events(
    capabilities: WorkerV4Capabilities,
    *,
    tenant_id: str,
    run_id: str,
    attempt_id: str,
    stream_incarnation: int | None = None,
    limit: int = 64,
) -> int:
    """Drain committed v4 rows after their owning PostgreSQL transaction."""

    authority = await capabilities.authority.get(
        tenant_id=tenant_id,
        run_id=run_id,
    )
    if (
        authority is None
        or authority.attempt_id != attempt_id
        or (
            stream_incarnation is not None
            and authority.stream_incarnation != stream_incarnation
        )
    ):
        return 0
    return await publish_claimed_v4_events(
        capabilities.publication_claims,
        capabilities.publication_transport,
        tenant_id=tenant_id,
        run_id=run_id,
        attempt_id=authority.attempt_id,
        stream_incarnation=authority.stream_incarnation,
        limit=limit,
        claim_ttl=timedelta(seconds=30),
        retry_delay=timedelta(seconds=5),
    )


async def persist_and_publish_worker_event(
    capabilities: WorkerV4Capabilities,
    *,
    run_payload: Any,
    attempt_id: str,
    persist_event: bool,
    event_type: str,
    stage: str,
    message: str,
    payload: dict[str, Any] | None,
    record_run_step: Callable[..., Awaitable[None]],
) -> bool:
    """Persist one event, then drain committed v4 rows outside PostgreSQL."""

    cancelled = await capabilities.event_persistence.persist_event_and_check_cancel(
        run_payload=run_payload,
        persist_event=persist_event,
        event_type=event_type,
        stage=stage,
        message=message,
        payload=payload,
        record_run_step=record_run_step,
    )
    await publish_pending_v4_events(
        capabilities,
        tenant_id=run_payload.tenant_id,
        run_id=run_payload.run_id,
        attempt_id=attempt_id,
    )
    return cancelled


async def finalize_parent_and_publish(
    transaction_factory: TransactionFactory,
    capabilities: WorkerV4Capabilities,
    finalize_parent: Callable[[TransactionFactory, Any, Any], Awaitable[None]],
    payload: Any,
    reconciled_parent: Any,
) -> None:
    await finalize_parent(transaction_factory, payload, reconciled_parent)
    await publish_pending_run_terminal(
        capabilities,
        tenant_id=payload.tenant_id,
        run_id=payload.run_id,
    )


async def publish_pending_run_terminal(
    capabilities: WorkerV4Capabilities,
    *,
    tenant_id: str,
    run_id: str,
) -> bool:
    """Drain terminal and other committed v4 rows through the durable claim port."""

    authority = await capabilities.authority.get(
        tenant_id=tenant_id,
        run_id=run_id,
    )
    if authority is None:
        return False
    try:
        return bool(
            await publish_pending_v4_events(
                capabilities,
                tenant_id=tenant_id,
                run_id=run_id,
                attempt_id=authority.attempt_id,
            )
        )
    except V4PublicationTransportUnavailable:
        return False


__all__ = [
    "V4PendingAdmission",
    "V4PendingAdmissionPort",
    "V4StreamAuthority",
    "V4StreamAuthorityLookup",
    "WorkerEventPersistence",
    "WorkerV4Capabilities",
    "admit_v4_stream",
    "finalize_parent_and_publish",
    "persist_and_publish_worker_event",
    "publish_pending_admissions",
    "publish_pending_run_terminal",
    "publish_pending_v4_events",
]
