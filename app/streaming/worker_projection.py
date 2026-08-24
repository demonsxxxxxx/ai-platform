"""Post-commit worker publication seams for Redis SSE v3."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from contextlib import AbstractAsyncContextManager
import logging
from typing import Any

from app import repositories
from app.execution.api import (
    context_file_failure_event_fields,
    context_file_failure_event_payload,
    context_file_failure_log_extra,
    validated_context_file_diagnostic,
)
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
from app.streaming.v4 import (
    V4RedisStreamBridge,
    list_pending_v4_rows,
    mark_v4_attempt,
    mark_v4_published,
    mark_v4_retry_error,
    project_public_v4,
    rebind_v4_incarnation,
    suppress_v4_event,
)


TransactionFactory = Callable[[], AbstractAsyncContextManager[Any]]
logger = logging.getLogger(__name__)
V4_MAX_PUBLICATION_ATTEMPTS = 8
V4_TERMINAL_EVENT_TYPES = frozenset({"run.succeeded", "run.failed", "run.cancelled"})
V4_TERMINAL_STATUSES = frozenset({"succeeded", "failed", "cancelled"})


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


async def _retry_or_suppress_v4_event(
    conn: Any,
    *,
    event_id: str,
    attempts: int,
    reason: str,
) -> None:
    """Keep transient publication failures pending for autonomous retry."""

    del attempts
    await mark_v4_retry_error(conn, event_id=event_id, error=reason)


async def publish_pending_v4_events(
    transaction_factory: TransactionFactory,
    *,
    limit: int = 32,
    bridge: V4RedisStreamBridge | None = None,
) -> int:
    """Publish the earliest due row for each Run while retaining row locks.

    The transaction deliberately spans validation, the bounded Redis append, and
    the fenced PostgreSQL disposition update.  A transport failure only updates
    retry metadata, so the durable row remains eligible for maintenance.
    """

    if isinstance(limit, bool) or not 1 <= limit <= 256:
        raise ValueError("v4_pending_limit_invalid")
    publisher = bridge or V4RedisStreamBridge()
    owns_publisher = bridge is None
    published = 0
    try:
        for _ in range(limit):
            async with transaction_factory() as conn:
                pending = await list_pending_v4_rows(conn, limit=1)
                if not pending:
                    break
                row = dict(pending[0])
                event_id = row.get("id")
                tenant_id = row.get("tenant_id")
                run_id = row.get("run_id")
                if not all(
                    isinstance(value, str) and value
                    for value in (event_id, tenant_id, run_id)
                ):
                    continue
                event_type = str(row.get("event_type") or "")
                attempts = int(row.get("stream_publication_attempts") or 0) + 1
                await mark_v4_attempt(conn, event_id=event_id)

                current_run = await repositories.get_run_identity(conn, run_id=run_id)
                if (
                    current_run is None
                    or str(current_run.get("tenant_id") or "") != tenant_id
                ):
                    await suppress_v4_event(
                        conn, event_id=event_id, reason="run_authority_missing"
                    )
                    continue
                authority = await get_stream_authority(
                    conn, tenant_id=tenant_id, run_id=run_id
                )
                if authority is None:
                    await _retry_or_suppress_v4_event(
                        conn,
                        event_id=event_id,
                        attempts=attempts,
                        reason="stream_authority_missing",
                    )
                    continue

                run_status = str(current_run.get("status") or "").lower()
                terminal_sequence: int | None = None
                if run_status in V4_TERMINAL_STATUSES:
                    terminal_cursor = await conn.execute(
                        """
                        select min(sequence) as sequence
                        from run_events
                        where tenant_id = %s and run_id = %s
                          and event_type in ('run.succeeded', 'run.failed', 'run.cancelled')
                        """,
                        (tenant_id, run_id),
                    )
                    terminal_row = await terminal_cursor.fetchone() or {}
                    value = terminal_row.get("sequence")
                    if isinstance(value, int) and not isinstance(value, bool):
                        terminal_sequence = value

                sequence = row.get("sequence")
                if not isinstance(sequence, int) or isinstance(sequence, bool):
                    await suppress_v4_event(
                        conn, event_id=event_id, reason="sequence_invalid"
                    )
                    continue
                is_terminal = event_type in V4_TERMINAL_EVENT_TYPES
                preterminal_row = (
                    not is_terminal
                    and run_status in V4_TERMINAL_STATUSES
                    and terminal_sequence is not None
                    and sequence < terminal_sequence
                )
                if await repositories.is_cancel_requested(
                    conn, tenant_id=tenant_id, run_id=run_id
                ) and event_type != "run.cancel_requested" and not is_terminal and not preterminal_row:
                    await suppress_v4_event(
                        conn, event_id=event_id, reason="cancellation_fence"
                    )
                    continue
                if is_terminal:
                    expected_status = event_type.removeprefix("run.")
                    if run_status != expected_status:
                        await suppress_v4_event(
                            conn, event_id=event_id, reason="terminal_status_fence"
                        )
                        continue
                    if authority.state != "terminal":
                        await _retry_or_suppress_v4_event(
                            conn,
                            event_id=event_id,
                            attempts=attempts,
                            reason="terminal_authority_pending",
                        )
                        continue
                elif run_status in V4_TERMINAL_STATUSES and not preterminal_row:
                    await suppress_v4_event(
                        conn, event_id=event_id, reason="terminal_fence"
                    )
                    continue
                elif not preterminal_row:
                    if event_type != "run.cancel_requested" and authority.state != "confirmed":
                        await _retry_or_suppress_v4_event(
                            conn,
                            event_id=event_id,
                            attempts=attempts,
                            reason="stream_authority_not_confirmed",
                        )
                        continue
                    if (
                        event_type == "run.cancel_requested"
                        and authority.state not in {"confirmed", "degraded", "terminal"}
                    ):
                        await _retry_or_suppress_v4_event(
                            conn,
                            event_id=event_id,
                            attempts=attempts,
                            reason="cancel_authority_pending",
                        )
                        continue
                if authority.revocation_state != "active":
                    await suppress_v4_event(
                        conn, event_id=event_id, reason="revocation_fence"
                    )
                    continue

                payload_json = row.get("payload_json")
                metadata = (
                    payload_json.get("__stream_v4")
                    if isinstance(payload_json, Mapping)
                    else None
                )
                if not isinstance(metadata, Mapping):
                    await suppress_v4_event(
                        conn, event_id=event_id, reason="metadata_invalid"
                    )
                    continue
                if metadata.get("attempt_id") != authority.attempt_id:
                    await suppress_v4_event(
                        conn, event_id=event_id, reason="attempt_fence"
                    )
                    continue
                if metadata.get("authorization_epoch") != authority.authorization_epoch:
                    await suppress_v4_event(
                        conn, event_id=event_id, reason="authorization_fence"
                    )
                    continue

                requires_live_lease = not is_terminal and not preterminal_row
                execution_lease_id = metadata.get("execution_lease_id")
                if requires_live_lease:
                    if not isinstance(execution_lease_id, str) or not execution_lease_id:
                        await suppress_v4_event(
                            conn, event_id=event_id, reason="lease_fence"
                        )
                        continue
                    lease_cursor = await conn.execute(
                        """
                        select id from sandbox_leases
                        where id = %s and tenant_id = %s and run_id = %s
                          and attempt_id = %s and status = 'active'
                          and (expires_at is null or expires_at > now())
                        """,
                        (
                            execution_lease_id,
                            tenant_id,
                            run_id,
                            authority.attempt_id,
                        ),
                    )
                    if await lease_cursor.fetchone() is None:
                        await suppress_v4_event(
                            conn, event_id=event_id, reason="lease_fence"
                        )
                        continue
                if metadata.get("stream_incarnation") != authority.stream_incarnation:
                    await rebind_v4_incarnation(
                        conn,
                        event_id=event_id,
                        stream_incarnation=authority.stream_incarnation,
                        authorization_epoch=authority.authorization_epoch,
                    )
                    rebound_payload = dict(payload_json)
                    rebound_metadata = dict(metadata)
                    rebound_metadata["stream_incarnation"] = authority.stream_incarnation
                    rebound_metadata["authorization_epoch"] = authority.authorization_epoch
                    rebound_payload["__stream_v4"] = rebound_metadata
                    row["payload_json"] = rebound_payload

                envelope = project_public_v4(row, authority=authority)
                if envelope is None:
                    await suppress_v4_event(
                        conn, event_id=event_id, reason="projection_rejected"
                    )
                    continue
                try:
                    redis_id = await publisher.append(envelope)
                except StreamContractError as exc:
                    await suppress_v4_event(
                        conn, event_id=event_id, reason=str(exc)[:120]
                    )
                    continue
                except StreamTransportUnavailable as exc:
                    await _retry_or_suppress_v4_event(
                        conn,
                        event_id=event_id,
                        attempts=attempts,
                        reason=type(exc).__name__,
                    )
                    continue
                if await mark_v4_published(
                    conn, event_id=event_id, redis_id=redis_id
                ):
                    published += 1
    finally:
        if owns_publisher:
            await publisher.aclose()
    return published


async def persist_worker_failure_event(
    conn: Any,
    *,
    tenant_id: str,
    run_id: str,
    result: Any,
    attempt_id: str,
    trace_id: str,
    error_code: str,
) -> None:
    """Persist one hidden terminal event while retaining only validated diagnostics."""

    executor_payload = result.executor_payload if isinstance(result.executor_payload, Mapping) else {}
    diagnostic = validated_context_file_diagnostic(executor_payload)
    if diagnostic is not None:
        logger.error(
            "Context file preprocessing failed",
            extra=context_file_failure_log_extra(
                diagnostic,
                run_id=run_id,
                attempt_id=attempt_id,
                trace_id=trace_id,
            ),
        )
    await repositories.append_event(
        conn,
        tenant_id=tenant_id,
        run_id=run_id,
        event_type="error",
        stage="worker",
        message="Run failed",
        payload={
            "artifact_count": len(result.artifacts),
            "visible_to_user": False,
            **context_file_failure_event_payload(diagnostic),
        },
        **context_file_failure_event_fields(
            diagnostic,
            trace_id=trace_id,
            error_code=error_code,
        ),
    )


async def finalize_parent_and_publish(
    transaction_factory: TransactionFactory,
    finalize_parent: Callable[[TransactionFactory, Any, Any], Awaitable[None]],
    payload: Any,
    reconciled_parent: Any,
) -> None:
    await finalize_parent(transaction_factory, payload, reconciled_parent)
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
