"""Post-commit worker publication seams for Redis SSE v2.1."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from contextlib import AbstractAsyncContextManager
from typing import Any

from app import repositories
from app.streaming.redis import (
    RedisStreamBridge,
    RunStreamPublisher,
    StreamContractError,
    StreamTransportUnavailable,
    get_stream_authority,
    get_terminal_intent,
    mark_terminal_intent_published,
    publish_terminal_intent,
)


TransactionFactory = Callable[[], AbstractAsyncContextManager[Any]]


async def publish_committed_run_event(
    transaction_factory: TransactionFactory,
    *,
    stream_publisher: RunStreamPublisher,
    tenant_id: str,
    run_id: str,
    row: Mapping[str, object],
) -> bool:
    """Refresh exact run/attempt authority after commit, then publish outside PG."""

    async with transaction_factory() as conn:
        current_run = await repositories.get_run_identity(conn, run_id=run_id)
        if (
            current_run is None
            or str(current_run.get("tenant_id") or "") != tenant_id
            or str(current_run.get("status") or "").lower()
            in {"succeeded", "failed", "cancelled", "canceled"}
        ):
            return False
        await stream_publisher.refresh(conn)
    try:
        return await stream_publisher.publish_committed_event(row)
    except StreamContractError as exc:
        if str(exc) != "stream_terminal_closed":
            raise
        return False


async def persist_and_publish_worker_event(
    transaction_factory: TransactionFactory,
    *,
    stream_publisher: RunStreamPublisher,
    run_payload: Any,
    persist_event: bool,
    event_type: str,
    stage: str,
    message: str,
    payload: dict[str, Any] | None,
    record_run_step: Callable[..., Awaitable[None]],
) -> bool:
    """Persist one event, commit, then publish its closed public projection."""

    committed: Mapping[str, object] | None = None
    async with transaction_factory() as conn:
        if persist_event:
            merged = {"visible_to_user": True, "severity": "info"}
            if payload:
                merged.update(payload)
            result = await repositories.append_event(
                conn,
                tenant_id=run_payload.tenant_id,
                run_id=run_payload.run_id,
                event_type=event_type,
                stage=stage,
                message=message,
                payload=merged,
                return_record=True,
            )
            committed = result if isinstance(result, Mapping) else None
            await record_run_step(
                conn,
                tenant_id=run_payload.tenant_id,
                run_id=run_payload.run_id,
                event_type=event_type,
                message=message,
                payload=payload,
            )
        cancelled = await repositories.is_cancel_requested(
            conn,
            tenant_id=run_payload.tenant_id,
            run_id=run_payload.run_id,
        )
    if committed is not None:
        await publish_committed_run_event(
            transaction_factory,
            stream_publisher=stream_publisher,
            tenant_id=run_payload.tenant_id,
            run_id=run_payload.run_id,
            row=committed,
        )
    return cancelled


async def finalize_parent_and_publish(
    transaction_factory: TransactionFactory,
    finalize_parent: Callable[[Any, Any], Awaitable[None]],
    payload: Any,
    reconciled_parent: Any,
) -> None:
    await finalize_parent(payload, reconciled_parent)
    await publish_pending_run_terminal(
        transaction_factory,
        tenant_id=payload.tenant_id,
        run_id=payload.run_id,
    )


async def publish_pending_run_terminal(
    transaction_factory: TransactionFactory,
    *,
    tenant_id: str,
    run_id: str,
) -> bool:
    """Best-effort post-commit terminal publish; durable intent remains retryable."""

    try:
        async with transaction_factory() as conn:
            authority = await get_stream_authority(
                conn, tenant_id=tenant_id, run_id=run_id
            )
            intent = await get_terminal_intent(conn, tenant_id=tenant_id, run_id=run_id)
    except Exception:  # noqa: BLE001 - the durable pending intent owns retry.
        return False
    if authority is None or intent is None or intent.state != "pending":
        return False
    bridge = RedisStreamBridge()
    try:
        await publish_terminal_intent(bridge, authority=authority, intent=intent)
    except (StreamContractError, StreamTransportUnavailable):
        return False
    finally:
        await bridge.aclose()
    try:
        async with transaction_factory() as conn:
            await mark_terminal_intent_published(conn, intent=intent)
    except Exception:  # noqa: BLE001 - exact Redis IDs make the next retry safe.
        return False
    return True
