"""Application contract for dormant v4 successor-stream preparation."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Protocol

from app.streaming.domain.public_events_v4 import (
    build_v4_control,
    stream_end_event_id,
    validate_internal_envelope_v4,
)
from app.streaming.domain.live import redis_id_tuple, stream_key
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
class V4SuccessorRebuildReceipt:
    """Exact Redis receipt for one complete reserved successor candidate."""

    stream_key: str
    stream_incarnation: int
    entry_count: int
    open_event_id: str
    terminal_event_id: str
    end_event_id: str
    last_redis_id: str
    last_envelope_bytes: bytes

    def __post_init__(self) -> None:
        for name in (
            "stream_key",
            "open_event_id",
            "terminal_event_id",
            "end_event_id",
            "last_redis_id",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not value:
                raise ValueError(f"v4_rebuild_receipt_{name}_invalid")
        if (
            isinstance(self.stream_incarnation, bool)
            or not isinstance(self.stream_incarnation, int)
            or self.stream_incarnation < 1
        ):
            raise ValueError("v4_rebuild_receipt_incarnation_invalid")
        if (
            isinstance(self.entry_count, bool)
            or not isinstance(self.entry_count, int)
            or self.entry_count < 3
        ):
            raise ValueError("v4_rebuild_receipt_entry_count_invalid")
        if not isinstance(self.last_envelope_bytes, bytes) or not self.last_envelope_bytes:
            raise ValueError("v4_rebuild_receipt_last_envelope_bytes_invalid")
        try:
            envelope = validate_internal_envelope_v4(
                json.loads(self.last_envelope_bytes.decode("utf-8"))
            )
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            raise ValueError("v4_rebuild_receipt_last_envelope_bytes_invalid") from exc
        if canonical_json_bytes(envelope) != self.last_envelope_bytes:
            raise ValueError("v4_rebuild_receipt_last_envelope_bytes_invalid")
        try:
            redis_id_tuple(self.last_redis_id)
        except ValueError as exc:
            raise ValueError("v4_rebuild_receipt_redis_id_invalid") from exc


@dataclass(frozen=True, slots=True)
class V4ReadySuccessorRebuild:
    """Narrow post-CAS capability; canonical candidate bytes stay private."""

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
    stream_key: str
    end_event_id: str
    last_redis_id: str
    last_envelope_bytes: bytes = field(repr=False)
    claim_expires_at: datetime
    claim_token: str = field(repr=False)

    def __post_init__(self) -> None:
        for name in (
            "rebuild_id",
            "tenant_id",
            "run_id",
            "attempt_id",
            "tenant_scope",
            "source_authority_fingerprint",
            "stream_key",
            "end_event_id",
            "last_redis_id",
            "claim_token",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not value:
                raise ValueError(f"v4_ready_rebuild_{name}_invalid")
        if (
            len(self.source_authority_fingerprint) != 64
            or any(
                character not in "0123456789abcdef"
                for character in self.source_authority_fingerprint
            )
        ):
            raise ValueError("v4_ready_rebuild_fingerprint_invalid")
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
                raise ValueError(f"v4_ready_rebuild_{name}_invalid")
        if self.successor_incarnation <= self.source_incarnation:
            raise ValueError("v4_ready_rebuild_incarnation_invalid")
        if self.successor_authorization_epoch <= self.source_authorization_epoch:
            raise ValueError("v4_ready_rebuild_epoch_invalid")
        if self.source_through_sequence > self.source_cursor_sequence:
            raise ValueError("v4_ready_rebuild_source_cursor_invalid")
        if not isinstance(self.claim_expires_at, datetime):
            raise ValueError("v4_ready_rebuild_expiry_invalid")
        if self.claim_expires_at.tzinfo is None or self.claim_expires_at.utcoffset() is None:
            raise ValueError("v4_ready_rebuild_expiry_timezone_invalid")
        try:
            redis_id_tuple(self.last_redis_id)
        except ValueError as exc:
            raise ValueError("v4_ready_rebuild_redis_id_invalid") from exc
        if not isinstance(self.last_envelope_bytes, bytes) or not self.last_envelope_bytes:
            raise ValueError("v4_ready_rebuild_last_envelope_bytes_invalid")
        try:
            envelope = validate_internal_envelope_v4(
                json.loads(self.last_envelope_bytes.decode("utf-8"))
            )
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            raise ValueError("v4_ready_rebuild_last_envelope_bytes_invalid") from exc
        if canonical_json_bytes(envelope) != self.last_envelope_bytes:
            raise ValueError("v4_ready_rebuild_last_envelope_bytes_invalid")


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
    claim_token: str = field(repr=False)
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
        if (
            len(self.source_authority_fingerprint) != 64
            or any(
                character not in "0123456789abcdef"
                for character in self.source_authority_fingerprint
            )
        ):
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
        if self.source_cursor_sequence < self.source_through_sequence:
            raise ValueError("v4_rebuild_claim_source_cursor_invalid")
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
            or opening["source"]
            != {
                "kind": "stream_authority",
                "authority_id": self.successor_open_event_id,
            }
        ):
            raise ValueError("v4_rebuild_claim_open_mismatch")
        if not isinstance(self.items, tuple) or not self.items:
            raise ValueError("v4_rebuild_claim_items_invalid")
        if any(not isinstance(item, V4SuccessorRebuildItem) for item in self.items):
            raise ValueError("v4_rebuild_claim_items_invalid")
        sequences = tuple(item.sequence for item in self.items)
        event_ids: set[str] = set()
        for item in self.items:
            envelope = validate_internal_envelope_v4(
                json.loads(item.canonical_envelope_bytes.decode("utf-8"))
            )
            if (
                envelope["tenant_scope"] != self.tenant_scope
                or envelope["run_id"] != self.run_id
                or envelope["attempt_id"] != self.attempt_id
                or envelope["stream_incarnation"] != self.successor_incarnation
                or envelope["event_id"] != item.event_id
                or envelope["event_type"] != item.event_type
                or envelope["seq"] != item.sequence
                or envelope["source"]
                != {
                    "kind": "run_event",
                    "run_event_id": item.event_id,
                    "sequence": item.sequence,
                }
                or item.event_id in event_ids
            ):
                raise ValueError("v4_rebuild_claim_item_mismatch")
            event_ids.add(item.event_id)
        if (
            sequences != tuple(sorted(set(sequences)))
            or sequences[-1] != self.source_through_sequence
        ):
            raise ValueError("v4_rebuild_claim_item_order_invalid")
        if not isinstance(self.claim_expires_at, datetime):
            raise ValueError("v4_rebuild_claim_expiry_invalid")
        if self.claim_expires_at.tzinfo is None or self.claim_expires_at.utcoffset() is None:
            raise ValueError("v4_rebuild_claim_expiry_timezone_invalid")


class V4SuccessorRebuildTransport(Protocol):
    """Transaction-external transport for one frozen successor candidate."""

    async def build(
        self, claim: V4SuccessorRebuildClaim
    ) -> V4SuccessorRebuildReceipt: ...


class V4SuccessorRebuilds(Protocol):
    """Port for one atomic, dormant successor snapshot preparation and readiness."""

    async def prepare(
        self,
        *,
        tenant_id: str,
        run_id: str,
        attempt_id: str,
        source_incarnation: int,
        claim_ttl: timedelta,
    ) -> V4SuccessorRebuildClaim | None: ...

    async def mark_ready(
        self,
        claim: V4SuccessorRebuildClaim,
        *,
        receipt: V4SuccessorRebuildReceipt,
    ) -> bool: ...


def _successor_end_bytes(
    claim: V4SuccessorRebuildClaim,
    terminal: V4SuccessorRebuildItem,
) -> bytes:
    terminal_envelope = validate_internal_envelope_v4(
        json.loads(terminal.canonical_envelope_bytes.decode("utf-8"))
    )
    return canonical_json_bytes(
        build_v4_control(
            event_id=stream_end_event_id(terminal.event_id),
            tenant_scope=claim.tenant_scope,
            run_id=claim.run_id,
            attempt_id=claim.attempt_id,
            stream_incarnation=claim.successor_incarnation,
            event_type="stream.end",
            payload={"terminal_event_id": terminal.event_id},
            source={"kind": "terminal_intent", "terminal_event_id": terminal.event_id},
            causation_event_id=terminal.event_id,
            emitted_at=terminal_envelope["emitted_at"],
        )
    )


def _terminal_item(claim: V4SuccessorRebuildClaim) -> V4SuccessorRebuildItem:
    terminal_items = tuple(
        item
        for item in claim.items
        if item.event_type in {"run.succeeded", "run.failed", "run.cancelled"}
    )
    if len(terminal_items) != 1 or terminal_items[-1] != claim.items[-1]:
        raise ValueError("v4_rebuild_terminal_item_invalid")
    return terminal_items[0]


def _validate_receipt_for_claim(
    claim: V4SuccessorRebuildClaim,
    receipt: V4SuccessorRebuildReceipt,
) -> None:
    if not isinstance(receipt, V4SuccessorRebuildReceipt):
        raise TypeError("v4_rebuild_receipt_type_invalid")
    terminal = _terminal_item(claim)
    expected_key = stream_key(
        tenant_scope_value=claim.tenant_scope,
        run_id=claim.run_id,
        stream_incarnation=claim.successor_incarnation,
    )
    if (
        receipt.stream_key != expected_key
        or receipt.stream_incarnation != claim.successor_incarnation
        or receipt.entry_count != len(claim.items) + 2
        or receipt.open_event_id != claim.successor_open_event_id
        or receipt.terminal_event_id != terminal.event_id
        or receipt.end_event_id != stream_end_event_id(terminal.event_id)
        or receipt.last_envelope_bytes != _successor_end_bytes(claim, terminal)
    ):
        raise ValueError("v4_rebuild_receipt_mismatch")


async def build_v4_successor_rebuild(
    rebuilds: V4SuccessorRebuilds,
    transport: V4SuccessorRebuildTransport,
    *,
    tenant_id: str,
    run_id: str,
    attempt_id: str,
    source_incarnation: int,
    claim_ttl: timedelta,
) -> V4ReadySuccessorRebuild | None:
    """Prepare, build outside PostgreSQL, then atomically mark ready."""

    claim = await prepare_v4_successor_rebuild(
        rebuilds,
        tenant_id=tenant_id,
        run_id=run_id,
        attempt_id=attempt_id,
        source_incarnation=source_incarnation,
        claim_ttl=claim_ttl,
    )
    if claim is None:
        return None
    receipt = await transport.build(claim)
    _validate_receipt_for_claim(claim, receipt)
    if not await rebuilds.mark_ready(claim, receipt=receipt):
        return None
    return V4ReadySuccessorRebuild(
        rebuild_id=claim.rebuild_id,
        tenant_id=claim.tenant_id,
        run_id=claim.run_id,
        attempt_id=claim.attempt_id,
        tenant_scope=claim.tenant_scope,
        source_incarnation=claim.source_incarnation,
        source_authorization_epoch=claim.source_authorization_epoch,
        successor_incarnation=claim.successor_incarnation,
        successor_authorization_epoch=claim.successor_authorization_epoch,
        source_authority_fingerprint=claim.source_authority_fingerprint,
        source_cursor_sequence=claim.source_cursor_sequence,
        source_through_sequence=claim.source_through_sequence,
        stream_key=receipt.stream_key,
        end_event_id=receipt.end_event_id,
        last_redis_id=receipt.last_redis_id,
        last_envelope_bytes=receipt.last_envelope_bytes,
        claim_expires_at=claim.claim_expires_at,
        claim_token=claim.claim_token,
    )


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
    "V4ReadySuccessorRebuild",
    "V4SuccessorRebuildClaim",
    "V4SuccessorRebuildItem",
    "V4SuccessorRebuildReceipt",
    "V4SuccessorRebuildTransport",
    "V4SuccessorRebuilds",
    "build_v4_successor_rebuild",
    "prepare_v4_successor_rebuild",
]
