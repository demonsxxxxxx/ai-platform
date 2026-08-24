"""Application contract for durable v4 publication claims."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

from app.streaming.domain.public_events_v4 import validate_internal_envelope_v4
from app.streaming.domain.transport import canonical_json_bytes


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


class V4PublicationClaims(Protocol):
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


__all__ = ["V4PublicationClaim", "V4PublicationClaims"]
