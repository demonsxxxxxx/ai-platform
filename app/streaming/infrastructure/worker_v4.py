"""Infrastructure adapters for the application-owned v4 worker ports."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager
from typing import Any

from app.streaming.application.callback_events_v4 import V4CallbackItem
from app.streaming.application.durable_v4 import (
    V4PendingAdmission,
    V4PendingAdmissionPort,
    V4PublicationStreamExpired,
    V4PublicationTransportUnavailable,
)
from app.streaming.application.worker_publication_v4 import (
    ReconstructedAssistantAnswer,
    WorkerEventPersistence,
)
from app.streaming.domain.public_events_v4 import V4ProjectionError, project_public_v4
from app.streaming.domain.transport import canonical_json_bytes
from app.streaming.redis import (
    StreamContractError,
    StreamTransportUnavailable,
    confirm_stream_admission,
    create_or_get_stream_admission_v4,
    get_stream_authority,
    tenant_scope,
)
from app.streaming.infrastructure import v4 as _v4
from app.streaming.infrastructure.run_v4_events import append_current_run_terminal_v4_row
from app.streaming.infrastructure.v4 import V4RedisStreamBridge


TransactionFactory = Callable[[], AbstractAsyncContextManager[Any]]


class PostgresWorkerEventPersistence(WorkerEventPersistence):
    """Persist worker events and cancellation state behind the worker boundary."""

    def __init__(
        self,
        transaction_factory: TransactionFactory,
        *,
        append_event: Callable[..., Awaitable[Any]],
        is_cancel_requested: Callable[..., Awaitable[bool]],
        load_terminal_event_fact: Callable[..., Awaitable[Any]],
    ) -> None:
        self._transaction_factory = transaction_factory
        self._append_event = append_event
        self._is_cancel_requested = is_cancel_requested
        self._load_terminal_event_fact = load_terminal_event_fact

    async def append_terminal_row(
        self,
        conn: Any,
        *,
        tenant_id: str,
        run_id: str,
    ) -> Any | None:
        return await append_current_run_terminal_v4_row(
            conn,
            tenant_id=tenant_id,
            run_id=run_id,
            load_terminal_event_fact=self._load_terminal_event_fact,
        )

    async def load_latest_run_event(
        self, *, tenant_id: str, run_id: str, before_sequence: int | None = None
    ) -> bytes | None:
        async with self._transaction_factory() as conn:
            await self.append_terminal_row(conn, tenant_id=tenant_id, run_id=run_id)
            authority = await get_stream_authority(conn, tenant_id=tenant_id, run_id=run_id)
            if (
                authority is None
                or authority.revocation_state != "active"
                or authority.state not in {"confirmed", "terminal"}
            ):
                return None
            result = await conn.execute(
                """
                select id, tenant_id, run_id, sequence, event_type,
                       visible_to_user, payload_json, created_at
                from run_events
                where tenant_id = %s and run_id = %s and visible_to_user = true
                  and event_type in ('run.cancel_requested', 'run.succeeded', 'run.failed', 'run.cancelled')
                  and (%s::bigint is null or sequence < %s)
                  and payload_json -> '__stream_v4' ->> 'attempt_id' = %s
                  and payload_json -> '__stream_v4' ->> 'stream_incarnation' = %s
                order by sequence desc limit 1
                """,
                (tenant_id, run_id, before_sequence, before_sequence, authority.attempt_id, str(authority.stream_incarnation)),
            )
            row = await result.fetchone()
            if row is None:
                return None
            envelope = project_public_v4(row, authority=authority)
            if envelope is None:
                raise V4ProjectionError("v4_run_event_projection_invalid")
            callbacks = await conn.execute(
                """
                select coalesce(max(sequence), 0) as sequence from run_events
                where tenant_id = %s and run_id = %s and visible_to_user = true
                  and sequence < %s
                  and payload_json -> '__stream_v4' ->> 'attempt_id' = %s
                  and payload_json -> '__stream_v4' ->> 'stream_incarnation' = %s
                  and payload_json -> '__stream_v4' ->> 'execution_lease_id' is not null
                """,
                (tenant_id, run_id, row["sequence"], authority.attempt_id, str(authority.stream_incarnation)),
            )
            callback_sequence = (await callbacks.fetchone())["sequence"]
            if callback_sequence:
                envelope["source"]["callback_sequence"] = callback_sequence
            return canonical_json_bytes(envelope)

    async def append_callback_rows(
        self,
        conn: Any,
        *,
        tenant_id: str,
        run_id: str,
        attempt_id: str,
        batch_id: str,
        items: Sequence[V4CallbackItem],
        authority: Any,
        execution_lease_id: str,
    ) -> tuple[Any, ...]:
        return await _v4.append_callback_v4_rows(
            conn,
            tenant_id=tenant_id,
            run_id=run_id,
            attempt_id=attempt_id,
            batch_id=batch_id,
            items=items,
            authority=authority,
            execution_lease_id=execution_lease_id,
        )

    async def load_answer_by_receipt(
        self,
        conn: Any,
        *,
        tenant_id: str,
        run_id: str,
        attempt_id: str,
        receipt: Mapping[str, object],
    ) -> ReconstructedAssistantAnswer:
        return await _v4.load_answer_by_receipt(
            conn,
            tenant_id=tenant_id,
            run_id=run_id,
            attempt_id=attempt_id,
            receipt=receipt,
        )

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
    ) -> bool:
        async with self._transaction_factory() as conn:
            if persist_event:
                merged = {"visible_to_user": True, "severity": "info"}
                if payload:
                    merged.update(payload)
                await self._append_event(
                    conn,
                    tenant_id=run_payload.tenant_id,
                    run_id=run_payload.run_id,
                    event_type=event_type,
                    stage=stage,
                    message=message,
                    payload=merged,
                )
                await record_run_step(
                    conn,
                    tenant_id=run_payload.tenant_id,
                    run_id=run_payload.run_id,
                    event_type=event_type,
                    message=message,
                    payload=payload,
                )
            return await self._is_cancel_requested(
                conn,
                tenant_id=run_payload.tenant_id,
                run_id=run_payload.run_id,
            )


class RedisV4PublicationTransport:
    """Publish canonical bytes through the lifecycle-owned v4 bridge."""

    def __init__(self, bridge: V4RedisStreamBridge) -> None:
        self._bridge = bridge

    async def publish_callback_batch(
        self, envelopes: tuple[Mapping[str, object], ...]
    ) -> str:
        from app.streaming.infrastructure.callback_stream import append_callback_batch

        try:
            return await append_callback_batch(self._bridge._bridge, envelopes)
        except StreamTransportUnavailable as exc:
            raise V4PublicationTransportUnavailable("stream_callback_append_unavailable") from exc

    async def publish(self, canonical_envelope_bytes: bytes) -> str:
        try:
            envelope = json.loads(canonical_envelope_bytes.decode("utf-8"))
            return await self._bridge.append(envelope)
        except StreamContractError as exc:
            if str(exc) == "stream_missing" and envelope.get("event_type") in {
                "run.succeeded",
                "run.failed",
                "run.cancelled",
            }:
                raise V4PublicationStreamExpired from exc
            raise
        except StreamTransportUnavailable as exc:
            raise V4PublicationTransportUnavailable(type(exc).__name__) from exc


class PostgresV4PendingAdmissions(V4PendingAdmissionPort):
    def __init__(self, transaction_factory: TransactionFactory, *, authority_secret: str) -> None:
        if not isinstance(authority_secret, str) or not authority_secret:
            raise ValueError("v4_authority_secret_invalid")
        self._transaction_factory = transaction_factory
        self._authority_secret = authority_secret

    async def prepare_pending_authority(
        self,
        *,
        tenant_id: str,
        run_id: str,
        attempt_id: str,
    ) -> V4PendingAdmission:
        async with self._transaction_factory() as conn:
            return await self.prepare_pending_authority_in_transaction(
                conn,
                tenant_id=tenant_id,
                run_id=run_id,
                attempt_id=attempt_id,
            )

    async def prepare_pending_authority_in_transaction(
        self,
        transaction: Any,
        *,
        tenant_id: str,
        run_id: str,
        attempt_id: str,
    ) -> V4PendingAdmission:
        run_cursor = await transaction.execute(
            "select id, status from runs where tenant_id = %s and id = %s for update",
            (tenant_id, run_id),
        )
        run = await run_cursor.fetchone()
        if run is None:
            raise RuntimeError("v4_pending_admission_run_missing")
        if str(run.get("status") or "") in {"succeeded", "failed", "cancelled"}:
            existing = await get_stream_authority(
                transaction,
                tenant_id=tenant_id,
                run_id=run_id,
                for_update=True,
            )
            if existing is None:
                raise RuntimeError("v4_pending_admission_terminal_run")
        scope = tenant_scope(tenant_id, secret=self._authority_secret)
        authority = await create_or_get_stream_admission_v4(
            transaction,
            tenant_id=tenant_id,
            run_id=run_id,
            attempt_id=attempt_id,
            tenant_scope=scope,
        )
        payload = authority.open_payload_bytes.encode("utf-8")
        return V4PendingAdmission(
            tenant_id=authority.tenant_id,
            tenant_scope=authority.tenant_scope,
            run_id=authority.run_id,
            attempt_id=authority.attempt_id,
            stream_incarnation=authority.stream_incarnation,
            open_event_id=authority.open_event_id,
            open_payload_bytes=payload,
            open_payload_digest=authority.open_payload_digest,
        )


    async def confirm_pending_admission(
        self,
        admission: V4PendingAdmission,
        *,
        redis_id: str,
    ) -> Any:
        del redis_id
        async with self._transaction_factory() as conn:
            authority = await get_stream_authority(
                conn,
                tenant_id=admission.tenant_id,
                run_id=admission.run_id,
                for_update=True,
            )
            if (
                authority is None
                or authority.attempt_id != admission.attempt_id
                or authority.stream_incarnation != admission.stream_incarnation
                or authority.open_event_id != admission.open_event_id
                or authority.open_payload_digest != admission.open_payload_digest
            ):
                raise RuntimeError("v4_pending_admission_fenced")
            return await confirm_stream_admission(conn, authority=authority)




__all__ = [
    "PostgresV4PendingAdmissions",
    "PostgresWorkerEventPersistence",
    "RedisV4PublicationTransport",
]
