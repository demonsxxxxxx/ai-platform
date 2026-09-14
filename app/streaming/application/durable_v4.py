"""Application contract for durable v4 publication claims."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from app.streaming.domain.public_events_v4 import validate_internal_envelope_v4
from app.streaming.domain.transport import canonical_json_bytes






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


    async def confirm_pending_admission(
        self,
        admission: V4PendingAdmission,
        *,
        redis_id: str,
    ) -> Any: ...












class V4PublicationStreamExpired(RuntimeError):
    """The exact terminal Redis stream and its state have both expired."""


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

    async def publish_callback_batch(
        self, envelopes: tuple[Mapping[str, object], ...]
    ) -> str: ...


def validate_v4_transport_receipt(redis_id: str) -> str:
    if (
        not isinstance(redis_id, str)
        or not redis_id
        or redis_id != redis_id.strip()
        or len(redis_id) > 256
    ):
        raise RuntimeError("v4_publication_receipt_invalid")
    return redis_id




__all__ = [
    "V4PendingAdmission",
    "V4PendingAdmissionPort",
    "V4PublicationTransport",
    "V4PublicationStreamExpired",
    "V4PublicationTransportUnavailable",
    "validate_v4_transport_receipt",
]
