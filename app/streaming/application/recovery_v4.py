"""Application contract for dormant v4 successor-stream preparation."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

from app.streaming.domain.public_events_v4 import validate_internal_envelope_v4
from app.streaming.domain.transport import canonical_json_bytes


@dataclass(frozen=True, slots=True)
class V4SuccessorRebuildItem:
    """One immutable durable event projected for a successor incarnation."""

    event_id: str
    sequence: int
    event_type: str
    canonical_envelope_bytes: bytes
    envelope_digest: str

    def __post_init__(self) -> None:
        if not isinstance(self.event_id, str) or not self.event_id:
            raise ValueError("v4_rebuild_item_event_id_invalid")
        if isinstance(self.sequence, bool) or not isinstance(self.sequence, int) or self.sequence < 1:
            raise ValueError("v4_rebuild_item_sequence_invalid")
        if not isinstance(self.event_type, str) or not self.event_type:
            raise ValueError("v4_rebuild_item_event_type_invalid")
        if not isinstance(self.canonical_envelope_bytes, bytes) or not self.canonical_envelope_bytes:
            raise ValueError("v4_rebuild_item_envelope_invalid")
        if (
            not isinstance(self.envelope_digest, str)
            or len(self.envelope_digest) != 64
            or hashlib.sha256(self.canonical_envelope_bytes).hexdigest() != self.envelope_digest
        ):
            raise ValueError("v4_rebuild_item_digest_invalid")
        try:
            envelope = validate_internal_envelope_v4(
                json.loads(self.canonical_envelope_bytes.decode("utf-8"))
            )
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            raise ValueError("v4_rebuild_item_envelope_invalid") from exc
        if (
            canonical_json_bytes(envelope) != self.canonical_envelope_bytes
            or envelope["event_id"] != self.event_id
            or envelope["seq"] != self.sequence
            or envelope["event_type"] != self.event_type
        ):
            raise ValueError("v4_rebuild_item_envelope_mismatch")


@dataclass(frozen=True, slots=True)
class V4SuccessorRebuildClaim:
    """Exclusive PostgreSQL preparation result; it does not activate the candidate."""

    rebuild_id: str
    tenant_id: str
    run_id: str
    attempt_id: str
    tenant_scope: str
    source_incarnation: int
    source_authorization_epoch: int
    successor_incarnation: int
    successor_authorization_epoch: int
    source_authority_fingerprint: str
    source_cursor_sequence: int
    source_through_sequence: int
    successor_open_event_id: str
    successor_open_bytes: bytes
    successor_open_digest: str
    items: tuple[V4SuccessorRebuildItem, ...]
    claim_token: str
    claim_expires_at: datetime

    def __post_init__(self) -> None:
        for name in (
            "rebuild_id",
            "tenant_id",
            "run_id",
            "attempt_id",
            "tenant_scope",
            "source_authority_fingerprint",
            "successor_open_event_id",
            "claim_token",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not value:
                raise ValueError(f"v4_rebuild_claim_{name}_invalid")
        if len(self.source_authority_fingerprint) != 64:
            raise ValueError("v4_rebuild_claim_source_fingerprint_invalid")
        for name in (
            "source_incarnation",
            "source_authorization_epoch",
            "successor_incarnation",
            "successor_authorization_epoch",
            "source_cursor_sequence",
            "source_through_sequence",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"v4_rebuild_claim_{name}_invalid")
        if self.successor_incarnation <= self.source_incarnation:
            raise ValueError("v4_rebuild_claim_successor_incarnation_invalid")
        if self.successor_authorization_epoch <= self.source_authorization_epoch:
            raise ValueError("v4_rebuild_claim_successor_epoch_invalid")
        if not isinstance(self.successor_open_bytes, bytes) or not self.successor_open_bytes:
            raise ValueError("v4_rebuild_claim_open_invalid")
        if (
            not isinstance(self.successor_open_digest, str)
            or len(self.successor_open_digest) != 64
            or hashlib.sha256(self.successor_open_bytes).hexdigest()
            != self.successor_open_digest
        ):
            raise ValueError("v4_rebuild_claim_open_digest_invalid")
        try:
            opening = validate_internal_envelope_v4(
                json.loads(self.successor_open_bytes.decode("utf-8"))
            )
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            raise ValueError("v4_rebuild_claim_open_invalid") from exc
        if (
            canonical_json_bytes(opening) != self.successor_open_bytes
            or opening["event_type"] != "stream.open"
            or opening["event_id"] != self.successor_open_event_id
            or opening["tenant_scope"] != self.tenant_scope
            or opening["run_id"] != self.run_id
            or opening["attempt_id"] != self.attempt_id
            or opening["stream_incarnation"] != self.successor_incarnation
        ):
            raise ValueError("v4_rebuild_claim_open_mismatch")
        if not isinstance(self.items, tuple) or not self.items:
            raise ValueError("v4_rebuild_claim_items_invalid")
        if any(not isinstance(item, V4SuccessorRebuildItem) for item in self.items):
            raise ValueError("v4_rebuild_claim_items_invalid")
        sequences = tuple(item.sequence for item in self.items)
        if sequences != tuple(sorted(set(sequences))) or sequences[-1] != self.source_through_sequence:
            raise ValueError("v4_rebuild_claim_item_order_invalid")
        if not isinstance(self.claim_expires_at, datetime):
            raise ValueError("v4_rebuild_claim_expiry_invalid")
        if self.claim_expires_at.tzinfo is None or self.claim_expires_at.utcoffset() is None:
            raise ValueError("v4_rebuild_claim_expiry_timezone_invalid")


class V4SuccessorRebuilds(Protocol):
    """Port for one atomic, dormant successor snapshot preparation."""

    async def prepare(
        self,
        *,
        tenant_id: str,
        run_id: str,
        attempt_id: str,
        source_incarnation: int,
        claim_ttl: timedelta,
    ) -> V4SuccessorRebuildClaim | None: ...


async def prepare_v4_successor_rebuild(
    rebuilds: V4SuccessorRebuilds,
    *,
    tenant_id: str,
    run_id: str,
    attempt_id: str,
    source_incarnation: int,
    claim_ttl: timedelta,
) -> V4SuccessorRebuildClaim | None:
    """Prepare one exclusive terminal successor without transport or activation."""

    for value in (tenant_id, run_id, attempt_id):
        if (
            not isinstance(value, str)
            or not value
            or value != value.strip()
            or len(value) > 256
        ):
            raise ValueError("v4_rebuild_scope_invalid")
    if (
        isinstance(source_incarnation, bool)
        or not isinstance(source_incarnation, int)
        or source_incarnation < 1
    ):
        raise ValueError("v4_rebuild_source_incarnation_invalid")
    if not isinstance(claim_ttl, timedelta) or claim_ttl <= timedelta(0):
        raise ValueError("v4_rebuild_claim_ttl_invalid")
    return await rebuilds.prepare(
        tenant_id=tenant_id,
        run_id=run_id,
        attempt_id=attempt_id,
        source_incarnation=source_incarnation,
        claim_ttl=claim_ttl,
    )


__all__ = [
    "V4SuccessorRebuildClaim",
    "V4SuccessorRebuildItem",
    "V4SuccessorRebuilds",
    "prepare_v4_successor_rebuild",
]
