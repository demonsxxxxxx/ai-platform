"""Claim-fenced recovery for already accepted Run queue messages."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Any, Literal, Protocol


DeliveryState = Literal["pending", "claimed", "delivered", "suppressed"]
_MAX_CONCURRENT_QUEUE_IO = 8
_QUEUE_IO_TIMEOUT_SECONDS = 10


@dataclass(frozen=True)
class FrozenQueueMessage:
    payload_json: str
    message_id: str


@dataclass(frozen=True)
class QueueDeliveryReceipt:
    queue_position: int
    queue_admission_ordinal: int
    message_id: str
    source: str


@dataclass(frozen=True)
class RunDeliveryIntent:
    tenant_id: str
    run_id: str
    session_id: str
    source: str
    submission_id: str | None
    user_message_id: str | None
    payload_json: str
    message_id: str
    payload_sha256: str
    state: DeliveryState
    claim_token: str | None
    attempt_count: int
    queue_position: int | None
    queue_admission_ordinal: int | None
    queue_admission_source: str | None

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> "RunDeliveryIntent":
        return cls(
            tenant_id=str(record["tenant_id"]),
            run_id=str(record["run_id"]),
            session_id=str(record["session_id"]),
            source=str(record["source"]),
            submission_id=(
                str(record["submission_id"])
                if record.get("submission_id") is not None
                else None
            ),
            user_message_id=(
                str(record["user_message_id"])
                if record.get("user_message_id") is not None
                else None
            ),
            payload_json=str(record["queue_payload_json"]),
            message_id=str(record["queue_message_id"]),
            payload_sha256=str(record["payload_sha256"]),
            state=str(record["state"]),  # type: ignore[arg-type]
            claim_token=(
                str(record["claim_token"]) if record.get("claim_token") else None
            ),
            attempt_count=int(record.get("attempt_count") or 0),
            queue_position=(
                int(record["queue_position"])
                if record.get("queue_position") is not None
                else None
            ),
            queue_admission_ordinal=(
                int(record["queue_admission_ordinal"])
                if record.get("queue_admission_ordinal") is not None
                else None
            ),
            queue_admission_source=(
                str(record["queue_admission_source"])
                if record.get("queue_admission_source") is not None
                else None
            ),
        )


@dataclass(frozen=True)
class DeliveryAttemptResult:
    state: DeliveryState
    receipt: QueueDeliveryReceipt | None = None
    error_code: str | None = None


@dataclass(frozen=True)
class DeliveryScanResult:
    claimed: int = 0
    delivered: int = 0
    rescheduled: int = 0
    suppressed: int = 0
    stale_claims: int = 0


class QueueDeliveryError(RuntimeError):
    """Queue boundary failure with evidence about deterministic rejection."""

    def __init__(self, code: str, *, definitive_rejection: bool) -> None:
        super().__init__(code)
        self.code = code
        self.definitive_rejection = definitive_rejection


class QueueMessageCodec(Protocol):
    def freeze(self, payload: Mapping[str, Any]) -> FrozenQueueMessage: ...


class RunDeliveryQueue(Protocol):
    async def readback(
        self, payload_json: str, *, message_id: str
    ) -> QueueDeliveryReceipt | None: ...

    async def enqueue(
        self, payload_json: str, *, message_id: str
    ) -> QueueDeliveryReceipt: ...


class RunDeliveryIntentStore(Protocol):
    async def insert(
        self,
        connection: Any,
        *,
        tenant_id: str,
        run_id: str,
        session_id: str,
        source: str,
        submission_id: str | None,
        user_message_id: str | None,
        payload_json: str,
        message_id: str,
        payload_sha256: str,
    ) -> Mapping[str, Any]: ...

    async def get(
        self, connection: Any, *, tenant_id: str, run_id: str
    ) -> Mapping[str, Any] | None: ...

    async def claim_due(
        self,
        connection: Any,
        *,
        limit: int,
        claim_ttl_seconds: int,
        tenant_id: str | None = None,
        run_id: str | None = None,
    ) -> list[Mapping[str, Any]]: ...

    async def claim_is_current(
        self, connection: Any, intent: RunDeliveryIntent
    ) -> bool: ...

    async def record_receipt(
        self,
        connection: Any,
        *,
        intent: RunDeliveryIntent,
        receipt: QueueDeliveryReceipt,
    ) -> bool: ...

    async def reschedule(
        self,
        connection: Any,
        *,
        intent: RunDeliveryIntent,
        retry_after_seconds: int,
        error_code: str,
    ) -> bool: ...

    async def suppress(
        self,
        connection: Any,
        *,
        intent: RunDeliveryIntent,
        reason: str,
    ) -> bool: ...


class RunDeliveryIntents:
    """Persist accepted queue messages, then retry transport with the same identity."""

    def __init__(
        self,
        *,
        enabled: bool,
        store: RunDeliveryIntentStore,
        codec: QueueMessageCodec,
        queue: RunDeliveryQueue,
        transaction: Any,
        scan_batch_size: int,
        claim_ttl_seconds: int,
        retry_base_seconds: int,
        retry_cap_seconds: int,
    ) -> None:
        if scan_batch_size < 1 or claim_ttl_seconds < 1:
            raise ValueError("run_delivery_configuration_invalid")
        if retry_base_seconds < 1 or retry_cap_seconds < retry_base_seconds:
            raise ValueError("run_delivery_retry_configuration_invalid")
        self.enabled = bool(enabled)
        self._store = store
        self._codec = codec
        self._queue = queue
        self._transaction = transaction
        self._scan_batch_size = scan_batch_size
        self._claim_ttl_seconds = claim_ttl_seconds
        self._retry_base_seconds = retry_base_seconds
        self._retry_cap_seconds = retry_cap_seconds
        self._queue_io_slots = asyncio.Semaphore(_MAX_CONCURRENT_QUEUE_IO)

    async def record(
        self,
        connection: Any,
        payload: Mapping[str, Any],
        *,
        source: str,
        submission_id: str | None = None,
        user_message_id: str | None = None,
    ) -> RunDeliveryIntent | None:
        """Insert the frozen payload in the Run's existing admission transaction."""

        if not self.enabled:
            return None
        frozen = self._codec.freeze(payload)
        if not frozen.message_id or len(frozen.message_id) != 64:
            raise ValueError("run_delivery_message_identity_invalid")
        payload_sha256 = sha256(frozen.payload_json.encode("utf-8")).hexdigest()
        if payload_sha256 != frozen.message_id:
            raise ValueError("run_delivery_message_identity_invalid")
        try:
            frozen_payload = json.loads(frozen.payload_json)
        except json.JSONDecodeError as exc:
            raise ValueError("run_delivery_payload_invalid") from exc
        if not isinstance(frozen_payload, dict) or any(
            not isinstance(frozen_payload.get(key), str) or not frozen_payload[key]
            for key in ("tenant_id", "run_id", "session_id")
        ):
            raise ValueError("run_delivery_payload_identity_invalid")
        if any(
            frozen_payload[key] != str(payload.get(key) or "")
            for key in ("tenant_id", "run_id", "session_id")
        ):
            raise ValueError("run_delivery_payload_identity_mismatch")
        record = await self._store.insert(
            connection,
            tenant_id=frozen_payload["tenant_id"],
            run_id=frozen_payload["run_id"],
            session_id=frozen_payload["session_id"],
            source=source,
            submission_id=submission_id,
            user_message_id=user_message_id,
            payload_json=frozen.payload_json,
            message_id=frozen.message_id,
            payload_sha256=payload_sha256,
        )
        return RunDeliveryIntent.from_record(record)

    async def deliver_one(
        self, *, tenant_id: str, run_id: str
    ) -> DeliveryAttemptResult:
        """Claim and deliver one intent for an immediate API response or replay."""

        if not self.enabled:
            return DeliveryAttemptResult(state="pending")
        async with self._transaction() as connection:
            claimed = await self._store.claim_due(
                connection,
                limit=1,
                claim_ttl_seconds=self._claim_ttl_seconds,
                tenant_id=tenant_id,
                run_id=run_id,
            )
            current = (
                claimed[0]
                if claimed
                else await self._store.get(
                    connection, tenant_id=tenant_id, run_id=run_id
                )
            )
        if current is None:
            return DeliveryAttemptResult(
                state="pending", error_code="delivery_intent_missing"
            )
        intent = RunDeliveryIntent.from_record(current)
        if not claimed:
            # A read cannot transfer another scanner's claim. Only the row
            # returned by this call's claim_due may cross the I/O boundary.
            if intent.state == "delivered":
                return DeliveryAttemptResult(
                    state="delivered",
                    receipt=_receipt_from_intent(intent),
                )
            return DeliveryAttemptResult(state="pending")
        if intent.state != "claimed" or not intent.claim_token:
            return DeliveryAttemptResult(
                state=intent.state,
                receipt=_receipt_from_intent(intent),
            )
        return await self._deliver_claim(intent)

    async def scan_once(self) -> DeliveryScanResult:
        """Claim a bounded page; completed and rescheduled rows yield to later IDs."""

        if not self.enabled:
            return DeliveryScanResult()
        async with self._transaction() as connection:
            claimed_records = await self._store.claim_due(
                connection,
                limit=self._scan_batch_size,
                claim_ttl_seconds=self._claim_ttl_seconds,
            )
        intents = [RunDeliveryIntent.from_record(record) for record in claimed_records]
        outcomes = await asyncio.gather(
            *(self._deliver_claim(intent) for intent in intents)
        )
        return DeliveryScanResult(
            claimed=len(intents),
            delivered=sum(result.state == "delivered" for result in outcomes),
            rescheduled=sum(
                result.state == "pending" and result.error_code is not None
                for result in outcomes
            ),
            suppressed=sum(result.state == "suppressed" for result in outcomes),
            stale_claims=sum(
                result.error_code == "delivery_claim_stale" for result in outcomes
            ),
        )

    async def _deliver_claim(self, intent: RunDeliveryIntent) -> DeliveryAttemptResult:
        if not _payload_identity_matches(intent):
            suppressed = await self._suppress(
                intent, "delivery_payload_identity_mismatch"
            )
            return DeliveryAttemptResult(
                state="suppressed" if suppressed else "pending",
                error_code=(
                    "delivery_payload_identity_mismatch"
                    if suppressed
                    else "delivery_claim_stale"
                ),
            )

        async with self._transaction() as connection:
            eligible = await self._store.claim_is_current(connection, intent)
        if not eligible:
            suppressed = await self._suppress(intent, "run_not_eligible_for_delivery")
            return DeliveryAttemptResult(
                state="suppressed" if suppressed else "pending",
                error_code=None if suppressed else "delivery_claim_stale",
            )

        try:
            receipt = await self._readback(intent)
        except Exception:
            return await self._reschedule(intent, "queue_readback_unknown")

        if receipt is None:
            async with self._transaction() as connection:
                eligible = await self._store.claim_is_current(connection, intent)
            if not eligible:
                suppressed = await self._suppress(
                    intent, "run_not_eligible_for_delivery"
                )
                return DeliveryAttemptResult(
                    state="suppressed" if suppressed else "pending",
                    error_code=None if suppressed else "delivery_claim_stale",
                )
            try:
                receipt = await self._enqueue(intent)
            except QueueDeliveryError as exc:
                try:
                    receipt = await self._readback(intent)
                except Exception:
                    return await self._reschedule(intent, "queue_response_unknown")
                if receipt is None:
                    if exc.definitive_rejection:
                        suppressed = await self._suppress(intent, exc.code)
                        return DeliveryAttemptResult(
                            state="suppressed" if suppressed else "pending",
                            error_code=exc.code
                            if suppressed
                            else "delivery_claim_stale",
                        )
                    return await self._reschedule(intent, exc.code)
            except Exception:
                try:
                    receipt = await self._readback(intent)
                except Exception:
                    return await self._reschedule(intent, "queue_response_unknown")
                if receipt is None:
                    return await self._reschedule(intent, "queue_response_unknown")

        if receipt is None:
            return await self._reschedule(intent, "queue_response_unknown")
        if receipt.message_id != intent.message_id:
            return await self._reschedule(intent, "queue_identity_mismatch")
        async with self._transaction() as connection:
            recorded = await self._store.record_receipt(
                connection,
                intent=intent,
                receipt=receipt,
            )
            record = await self._store.get(
                connection,
                tenant_id=intent.tenant_id,
                run_id=intent.run_id,
            )
        if recorded:
            return DeliveryAttemptResult(state="delivered", receipt=receipt)
        if record is not None:
            latest = RunDeliveryIntent.from_record(record)
            if latest.state == "delivered":
                return DeliveryAttemptResult(
                    state="delivered",
                    receipt=_receipt_from_intent(latest),
                )
            if latest.state == "suppressed":
                return DeliveryAttemptResult(state="suppressed")
        return DeliveryAttemptResult(state="pending", error_code="delivery_claim_stale")

    async def _readback(self, intent: RunDeliveryIntent) -> QueueDeliveryReceipt | None:
        async with self._queue_io_slots:
            return await asyncio.wait_for(
                self._queue.readback(intent.payload_json, message_id=intent.message_id),
                timeout=_QUEUE_IO_TIMEOUT_SECONDS,
            )

    async def _enqueue(self, intent: RunDeliveryIntent) -> QueueDeliveryReceipt:
        async with self._queue_io_slots:
            return await asyncio.wait_for(
                self._queue.enqueue(intent.payload_json, message_id=intent.message_id),
                timeout=_QUEUE_IO_TIMEOUT_SECONDS,
            )

    async def _reschedule(
        self, intent: RunDeliveryIntent, error_code: str
    ) -> DeliveryAttemptResult:
        retry_after = min(
            self._retry_cap_seconds,
            self._retry_base_seconds * (2 ** min(max(intent.attempt_count - 1, 0), 20)),
        )
        async with self._transaction() as connection:
            updated = await self._store.reschedule(
                connection,
                intent=intent,
                retry_after_seconds=retry_after,
                error_code=error_code,
            )
            if not updated:
                record = await self._store.get(
                    connection,
                    tenant_id=intent.tenant_id,
                    run_id=intent.run_id,
                )
                if record is not None and str(record.get("state")) == "suppressed":
                    return DeliveryAttemptResult(state="suppressed")
        return DeliveryAttemptResult(
            state="pending",
            error_code=error_code if updated else "delivery_claim_stale",
        )

    async def _suppress(self, intent: RunDeliveryIntent, reason: str) -> bool:
        async with self._transaction() as connection:
            return await self._store.suppress(
                connection,
                intent=intent,
                reason=reason,
            )


def _payload_identity_matches(intent: RunDeliveryIntent) -> bool:
    digest = sha256(intent.payload_json.encode("utf-8")).hexdigest()
    return digest == intent.payload_sha256 == intent.message_id


def _receipt_from_intent(intent: RunDeliveryIntent) -> QueueDeliveryReceipt | None:
    if intent.state != "delivered":
        return None
    if (
        intent.queue_position is None
        or intent.queue_admission_ordinal is None
        or intent.queue_admission_source is None
    ):
        return None
    return QueueDeliveryReceipt(
        queue_position=intent.queue_position,
        queue_admission_ordinal=intent.queue_admission_ordinal,
        message_id=intent.message_id,
        source=intent.queue_admission_source,
    )
