"""Application contract for durable v4 publication claims."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Protocol

from app.streaming.domain.public_events_v4 import validate_internal_envelope_v4
from app.streaming.domain.transport import canonical_json_bytes


DEFAULT_CLAIM_TTL = timedelta(seconds=30)
DEFAULT_RETRY_DELAY = timedelta(seconds=5)


@dataclass(frozen=True, slots=True)
class V4PublicationClaim:
    """Immutable facts handed from durable publication selection to a publisher."""

    event_id: str
    tenant_id: str
    run_id: str
    attempt_id: str
    tenant_scope: str
    stream_incarnation: int
    authorization_epoch: int
    sequence: int
    canonical_envelope_bytes: bytes
    claim_token: str
    claim_expires_at: datetime

    def __post_init__(self) -> None:
        for name in (
            "event_id",
            "tenant_id",
            "run_id",
            "attempt_id",
            "tenant_scope",
            "claim_token",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not value:
                raise ValueError(f"v4_publication_claim_{name}_invalid")
        if (
            isinstance(self.stream_incarnation, bool)
            or not isinstance(self.stream_incarnation, int)
            or self.stream_incarnation < 1
        ):
            raise ValueError("v4_publication_claim_incarnation_invalid")
        if (
            isinstance(self.authorization_epoch, bool)
            or not isinstance(self.authorization_epoch, int)
            or self.authorization_epoch < 1
        ):
            raise ValueError("v4_publication_claim_authorization_epoch_invalid")
        if (
            isinstance(self.sequence, bool)
            or not isinstance(self.sequence, int)
            or self.sequence < 1
        ):
            raise ValueError("v4_publication_claim_sequence_invalid")
        if not isinstance(self.canonical_envelope_bytes, bytes) or not self.canonical_envelope_bytes:
            raise ValueError("v4_publication_claim_envelope_invalid")
        try:
            decoded = json.loads(self.canonical_envelope_bytes.decode("utf-8"))
            envelope = validate_internal_envelope_v4(decoded)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            raise ValueError("v4_publication_claim_envelope_invalid") from exc
        if (
            canonical_json_bytes(envelope) != self.canonical_envelope_bytes
            or envelope["event_id"] != self.event_id
            or envelope["run_id"] != self.run_id
            or envelope["attempt_id"] != self.attempt_id
            or envelope["tenant_scope"] != self.tenant_scope
            or envelope["stream_incarnation"] != self.stream_incarnation
            or envelope["seq"] != self.sequence
        ):
            raise ValueError("v4_publication_claim_envelope_mismatch")
        if not isinstance(self.claim_expires_at, datetime):
            raise ValueError("v4_publication_claim_expiry_invalid")
        if self.claim_expires_at.tzinfo is None or self.claim_expires_at.utcoffset() is None:
            raise ValueError("v4_publication_claim_expiry_timezone_invalid")


@dataclass(frozen=True, slots=True)
class V4PendingAdmission:
    """Canonical stream.open facts durable before Redis transport succeeds."""

    tenant_id: str
    tenant_scope: str
    run_id: str
    attempt_id: str
    stream_incarnation: int
    open_event_id: str
    open_payload_bytes: bytes
    open_payload_digest: str

    def __post_init__(self) -> None:
        for name in (
            "tenant_id",
            "tenant_scope",
            "run_id",
            "attempt_id",
            "open_event_id",
            "open_payload_digest",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not value:
                raise ValueError(f"v4_pending_admission_{name}_invalid")
        if (
            isinstance(self.stream_incarnation, bool)
            or not isinstance(self.stream_incarnation, int)
            or self.stream_incarnation < 1
        ):
            raise ValueError("v4_pending_admission_incarnation_invalid")
        if not isinstance(self.open_payload_bytes, bytes) or not self.open_payload_bytes:
            raise ValueError("v4_pending_admission_payload_invalid")
        if hashlib.sha256(self.open_payload_bytes).hexdigest() != self.open_payload_digest:
            raise ValueError("v4_pending_admission_digest_invalid")
        try:
            envelope = validate_internal_envelope_v4(
                json.loads(self.open_payload_bytes.decode("utf-8"))
            )
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            raise ValueError("v4_pending_admission_payload_invalid") from exc
        if (
            canonical_json_bytes(envelope) != self.open_payload_bytes
            or envelope["event_type"] != "stream.open"
            or envelope["event_id"] != self.open_event_id
            or envelope["tenant_scope"] != self.tenant_scope
            or envelope["run_id"] != self.run_id
            or envelope["attempt_id"] != self.attempt_id
            or envelope["stream_incarnation"] != self.stream_incarnation
        ):
            raise ValueError("v4_pending_admission_payload_mismatch")


class V4PendingAdmissionPort(Protocol):
    async def prepare_pending_authority(
        self,
        *,
        tenant_id: str,
        run_id: str,
        attempt_id: str,
    ) -> V4PendingAdmission: ...

    async def prepare_pending_authority_in_transaction(
        self,
        transaction: Any,
        *,
        tenant_id: str,
        run_id: str,
        attempt_id: str,
    ) -> V4PendingAdmission: ...

    async def list_pending_admissions(self, *, limit: int) -> tuple[V4PendingAdmission, ...]: ...

    async def confirm_pending_admission(
        self,
        admission: V4PendingAdmission,
        *,
        redis_id: str,
    ) -> Any: ...


@dataclass(frozen=True, slots=True)
class V4PublicationScope:
    tenant_id: str
    run_id: str
    attempt_id: str
    stream_incarnation: int


class V4DuePublicationScopes(Protocol):
    async def list_due_scopes(self, *, limit: int) -> tuple[V4PublicationScope, ...]: ...


class V4PublicationClaims(V4DuePublicationScopes, Protocol):
    """Application port for durable v4 publication ownership."""

    async def claim_next(
        self,
        *,
        tenant_id: str,
        run_id: str,
        attempt_id: str,
        stream_incarnation: int,
        claim_ttl: timedelta,
    ) -> V4PublicationClaim | None: ...

    async def mark_published(self, claim: V4PublicationClaim, *, redis_id: str) -> bool: ...

    async def schedule_retry(
        self,
        claim: V4PublicationClaim,
        *,
        error: str,
        delay: timedelta,
    ) -> bool: ...

    async def release(self, claim: V4PublicationClaim) -> bool: ...


async def publish_pending_v4_admissions(
    pending: V4PendingAdmissionPort,
    transport: V4PublicationTransport,
    *,
    limit: int,
) -> int:
    """Publish and confirm a bounded set of durable stream.open authorities."""

    if isinstance(limit, bool) or not 1 <= limit <= 256:
        raise ValueError("v4_pending_admission_limit_invalid")
    published = 0
    for admission in await pending.list_pending_admissions(limit=limit):
        try:
            redis_id = await transport.publish(admission.open_payload_bytes)
            validate_v4_transport_receipt(redis_id)
        except V4PublicationTransportUnavailable:
            continue
        await pending.confirm_pending_admission(admission, redis_id=redis_id)
        published += 1
    return published


async def publish_due_v4_events(
    claims: V4PublicationClaims,
    transport: V4PublicationTransport,
    *,
    scope_limit: int,
    event_limit: int,
) -> int:
    """Drain bounded indexed scopes with a separate per-scope event bound."""

    if isinstance(scope_limit, bool) or not 1 <= scope_limit <= 256:
        raise ValueError("v4_publication_scope_limit_invalid")
    if isinstance(event_limit, bool) or not 1 <= event_limit <= 256:
        raise ValueError("v4_publication_event_limit_invalid")
    total = 0
    for scope in await claims.list_due_scopes(limit=scope_limit):
        total += await publish_claimed_v4_events(
            claims,
            transport,
            tenant_id=scope.tenant_id,
            run_id=scope.run_id,
            attempt_id=scope.attempt_id,
            stream_incarnation=scope.stream_incarnation,
            limit=event_limit,
            claim_ttl=DEFAULT_CLAIM_TTL,
            retry_delay=DEFAULT_RETRY_DELAY,
        )
    return total


class V4PublicationTransportUnavailable(RuntimeError):
    """A bounded transient transport result safe to persist for retry."""

    def __init__(self, error_code: str) -> None:
        if (
            not isinstance(error_code, str)
            or not error_code
            or error_code != error_code.strip()
            or len(error_code) > 120
        ):
            raise ValueError("v4_publication_transport_error_invalid")
        self.error_code = error_code
        super().__init__(error_code)




class V4PublicationTransport(Protocol):
    """Transport port that receives only validated canonical envelope bytes."""

    async def publish(self, canonical_envelope_bytes: bytes) -> str: ...


def validate_v4_transport_receipt(redis_id: str) -> str:
    if (
        not isinstance(redis_id, str)
        or not redis_id
        or redis_id != redis_id.strip()
        or len(redis_id) > 256
    ):
        raise RuntimeError("v4_publication_receipt_invalid")
    return redis_id


async def publish_claimed_v4_events(
    claims: V4PublicationClaims,
    transport: V4PublicationTransport,
    *,
    tenant_id: str,
    run_id: str,
    attempt_id: str,
    stream_incarnation: int,
    limit: int,
    claim_ttl: timedelta,
    retry_delay: timedelta,
) -> int:
    """Claim in PostgreSQL, publish without a DB lock, then fence disposition."""

    for value in (tenant_id, run_id, attempt_id):
        if (
            not isinstance(value, str)
            or not value
            or value != value.strip()
            or len(value) > 256
        ):
            raise ValueError("v4_publication_scope_invalid")
    if (
        isinstance(stream_incarnation, bool)
        or not isinstance(stream_incarnation, int)
        or stream_incarnation < 1
    ):
        raise ValueError("v4_publication_incarnation_invalid")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 256:
        raise ValueError("v4_publication_limit_invalid")
    if not isinstance(claim_ttl, timedelta) or claim_ttl <= timedelta(0):
        raise ValueError("v4_publication_claim_ttl_invalid")
    if not isinstance(retry_delay, timedelta) or retry_delay < timedelta(0):
        raise ValueError("v4_publication_retry_delay_invalid")

    published = 0
    for _ in range(limit):
        claim = await claims.claim_next(
            tenant_id=tenant_id,
            run_id=run_id,
            attempt_id=attempt_id,
            stream_incarnation=stream_incarnation,
            claim_ttl=claim_ttl,
        )
        if claim is None:
            break
        try:
            redis_id = await transport.publish(claim.canonical_envelope_bytes)
            validate_v4_transport_receipt(redis_id)
        except V4PublicationTransportUnavailable as exc:
            await claims.schedule_retry(
                claim,
                error=exc.error_code,
                delay=retry_delay,
            )
            continue
        except Exception:
            await claims.release(claim)
            raise
        if await claims.mark_published(claim, redis_id=redis_id):
            published += 1
    return published


__all__ = [
    "V4PublicationClaim",
    "V4PendingAdmission",
    "V4PendingAdmissionPort",
    "V4PublicationScope",
    "V4DuePublicationScopes",
    "V4PublicationClaims",
    "V4PublicationTransport",
    "V4PublicationTransportUnavailable",
    "publish_claimed_v4_events",
    "publish_pending_v4_admissions",
    "validate_v4_transport_receipt",
    "publish_due_v4_events",
]
