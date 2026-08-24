"""Redis adapter for dormant v4 successor candidate construction."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping

from app.streaming import redis as stream_redis
from app.streaming.application.recovery_v4 import (
    V4SuccessorRebuildClaim,
    V4SuccessorRebuildReceipt,
)
from app.streaming.domain.live import redis_id_tuple, stream_key
from app.streaming.domain.public_events_v4 import (
    build_v4_control,
    stream_end_event_id,
    validate_internal_envelope_v4,
)
from app.streaming.domain.transport import canonical_json_bytes


_TERMINAL_EVENT_TYPES = frozenset(
    {"run.succeeded", "run.failed", "run.cancelled"}
)


class RedisV4SuccessorRebuildTransport:
    """Build one reserved successor without publishing live fan-out messages."""

    def __init__(self, bridge: stream_redis.RedisStreamBridge | None = None) -> None:
        self._bridge = bridge or stream_redis.RedisStreamBridge()
        self._owns_bridge = bridge is None

    async def aclose(self) -> None:
        if self._owns_bridge:
            await self._bridge.aclose()

    async def build(
        self, claim: V4SuccessorRebuildClaim
    ) -> V4SuccessorRebuildReceipt:
        if not isinstance(claim, V4SuccessorRebuildClaim):
            raise TypeError("v4_rebuild_claim_type_invalid")
        entry_count = len(claim.items) + 2
        if entry_count > stream_redis.SSE_STREAM_MAXLEN:
            raise stream_redis.StreamContractError("v4_rebuild_candidate_too_large")

        opening = _decode_claim_envelope(
            claim.successor_open_bytes,
            expected_event_id=claim.successor_open_event_id,
            expected_event_type="stream.open",
            claim=claim,
        )
        item_envelopes = tuple(
            _decode_claim_envelope(
                item.canonical_envelope_bytes,
                expected_event_id=item.event_id,
                expected_event_type=item.event_type,
                expected_sequence=item.sequence,
                claim=claim,
            )
            for item in claim.items
        )
        terminal_indexes = tuple(
            index
            for index, item in enumerate(claim.items)
            if item.event_type in _TERMINAL_EVENT_TYPES
        )
        if terminal_indexes != (len(claim.items) - 1,):
            raise stream_redis.StreamContractError("v4_rebuild_terminal_item_invalid")
        terminal = item_envelopes[-1]
        terminal_event_id = str(terminal["event_id"])
        if terminal["payload"].get("terminal_event_id") != terminal_event_id:
            raise stream_redis.StreamContractError("v4_rebuild_terminal_event_invalid")
        end = build_v4_control(
            event_id=stream_end_event_id(terminal_event_id),
            tenant_scope=claim.tenant_scope,
            run_id=claim.run_id,
            attempt_id=claim.attempt_id,
            stream_incarnation=claim.successor_incarnation,
            event_type="stream.end",
            payload={"terminal_event_id": terminal_event_id},
            source={"kind": "terminal_intent", "terminal_event_id": terminal_event_id},
            causation_event_id=terminal_event_id,
            emitted_at=terminal["emitted_at"],
        )
        end_bytes = canonical_json_bytes(end)
        expected_bytes = (
            claim.successor_open_bytes,
            *(item.canonical_envelope_bytes for item in claim.items),
            end_bytes,
        )
        key = stream_key(
            tenant_scope_value=claim.tenant_scope,
            run_id=claim.run_id,
            stream_incarnation=claim.successor_incarnation,
        )
        try:
            initial = await self._bridge.inspect_v4_candidate(
                tenant_scope_value=claim.tenant_scope,
                run_id=claim.run_id,
                stream_incarnation=claim.successor_incarnation,
            )
            if initial.stream_exists or initial.state_exists:
                raise stream_redis.StreamContractError(
                    "v4_rebuild_candidate_preexisting"
                )
            redis_ids: list[str] = []
            redis_ids.append(
                await self._append(
                    claim=claim,
                    key=key,
                    envelope=opening,
                    envelope_bytes=claim.successor_open_bytes,
                    terminal_event_id="",
                )
            )
            for item, envelope in zip(claim.items, item_envelopes, strict=True):
                redis_ids.append(
                    await self._append(
                        claim=claim,
                        key=key,
                        envelope=envelope,
                        envelope_bytes=item.canonical_envelope_bytes,
                        terminal_event_id=(
                            item.event_id if item.event_type in _TERMINAL_EVENT_TYPES else ""
                        ),
                    )
                )
            redis_ids.append(
                await self._append(
                    claim=claim,
                    key=key,
                    envelope=end,
                    envelope_bytes=end_bytes,
                    terminal_event_id=terminal_event_id,
                )
            )
            inspection = await self._bridge.inspect_v4_candidate(
                tenant_scope_value=claim.tenant_scope,
                run_id=claim.run_id,
                stream_incarnation=claim.successor_incarnation,
            )
            await self._verify_candidate(
                inspection=inspection,
                claim=claim,
                expected_bytes=expected_bytes,
                redis_ids=redis_ids,
                terminal_event_id=terminal_event_id,
                end_event_id=str(end["event_id"]),
            )
        except stream_redis.StreamContractError:
            raise
        except stream_redis.StreamTransportUnavailable:
            raise
        except Exception as exc:
            raise stream_redis.StreamTransportUnavailable(
                "v4_rebuild_candidate_unavailable"
            ) from exc

        return V4SuccessorRebuildReceipt(
            stream_key=key,
            stream_incarnation=claim.successor_incarnation,
            entry_count=entry_count,
            open_event_id=claim.successor_open_event_id,
            terminal_event_id=terminal_event_id,
            end_event_id=str(end["event_id"]),
            last_redis_id=redis_ids[-1],
            last_envelope_bytes=end_bytes,
        )

    async def _append(
        self,
        *,
        claim: V4SuccessorRebuildClaim,
        key: str,
        envelope: Mapping[str, object],
        envelope_bytes: bytes,
        terminal_event_id: str,
    ) -> str:
        if key != stream_key(
            tenant_scope_value=claim.tenant_scope,
            run_id=claim.run_id,
            stream_incarnation=claim.successor_incarnation,
        ):
            raise stream_redis.StreamContractError("v4_rebuild_key_mismatch")
        event_type = str(envelope["event_type"])
        return await self._bridge.append_canonical(
            tenant_scope_value=claim.tenant_scope,
            run_id=claim.run_id,
            stream_incarnation=claim.successor_incarnation,
            event_id=str(envelope["event_id"]),
            event_type=event_type,
            envelope_bytes=envelope_bytes,
            terminal_event_id=terminal_event_id,
            protocol="v4",
            publish_live=False,
        )

    async def _verify_candidate(
        self,
        *,
        inspection: stream_redis.RedisV4CandidateInspection,
        claim: V4SuccessorRebuildClaim,
        expected_bytes: tuple[bytes, ...],
        redis_ids: list[str],
        terminal_event_id: str,
        end_event_id: str,
    ) -> None:
        if not inspection.stream_exists or not inspection.state_exists:
            raise stream_redis.StreamTransportUnavailable(
                "v4_rebuild_candidate_state_missing"
            )
        if len(inspection.rows) != len(expected_bytes):
            raise stream_redis.StreamTransportUnavailable(
                "v4_rebuild_candidate_rows_mismatch"
            )
        actual_ids: list[str] = []
        for row, expected in zip(inspection.rows, expected_bytes, strict=True):
            redis_id, fields = row
            redis_id = _text(redis_id)
            redis_id_tuple(redis_id)
            if not isinstance(fields, Mapping) or {
                _text(field_name) for field_name in fields
            } != {"envelope"}:
                raise stream_redis.StreamTransportUnavailable(
                    "v4_rebuild_candidate_fields_invalid"
                )
            raw_envelope = fields.get("envelope")
            if raw_envelope is None:
                raw_envelope = fields.get(b"envelope")
            actual = _text(raw_envelope).encode("utf-8")
            if actual != expected:
                raise stream_redis.StreamTransportUnavailable(
                    "v4_rebuild_candidate_bytes_mismatch"
                )
            actual_ids.append(redis_id)
        if actual_ids != redis_ids:
            raise stream_redis.StreamTransportUnavailable(
                "v4_rebuild_candidate_receipts_mismatch"
            )
        state = {
            _text(key): _text(value)
            for key, value in inspection.state.items()
        }
        opening_digest = hashlib.sha256(expected_bytes[0]).hexdigest()
        terminal_digest = hashlib.sha256(expected_bytes[-2]).hexdigest()
        end_digest = hashlib.sha256(expected_bytes[-1]).hexdigest()
        expected_state = {
            "phase": "ended",
            "open_protocol": "v4",
            "open_event_id": claim.successor_open_event_id,
            "open_digest": opening_digest,
            "open_redis_id": actual_ids[0],
            "terminal_event_id": terminal_event_id,
            "terminal_digest": terminal_digest,
            "terminal_redis_id": actual_ids[-2],
            "end_event_id": end_event_id,
            "end_digest": end_digest,
            "end_redis_id": actual_ids[-1],
        }
        ordinary_indexes = tuple(
            index
            for index, item in enumerate(claim.items)
            if item.event_type not in _TERMINAL_EVENT_TYPES
        )
        if ordinary_indexes:
            last_ordinary = ordinary_indexes[-1]
            expected_state.update(
                {
                    "last_event_id": claim.items[last_ordinary].event_id,
                    "last_event_digest": hashlib.sha256(
                        claim.items[last_ordinary].canonical_envelope_bytes
                    ).hexdigest(),
                    "last_event_redis_id": actual_ids[last_ordinary + 1],
                }
            )
        if state != expected_state:
            raise stream_redis.StreamTransportUnavailable(
                "v4_rebuild_candidate_state_mismatch"
            )


def _decode_claim_envelope(
    envelope_bytes: bytes,
    *,
    expected_event_id: str,
    expected_event_type: str,
    claim: V4SuccessorRebuildClaim,
    expected_sequence: int | None = None,
) -> dict[str, object]:
    try:
        raw = envelope_bytes.decode("utf-8")
        envelope = validate_internal_envelope_v4(json.loads(raw))
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise stream_redis.StreamContractError(
            "v4_rebuild_candidate_envelope_invalid"
        ) from exc
    if (
        canonical_json_bytes(envelope) != envelope_bytes
        or envelope["event_id"] != expected_event_id
        or envelope["event_type"] != expected_event_type
        or envelope["tenant_scope"] != claim.tenant_scope
        or envelope["run_id"] != claim.run_id
        or envelope["attempt_id"] != claim.attempt_id
        or envelope["stream_incarnation"] != claim.successor_incarnation
        or (expected_sequence is not None and envelope["seq"] != expected_sequence)
    ):
        raise stream_redis.StreamContractError("v4_rebuild_candidate_envelope_mismatch")
    if expected_event_type == "stream.open" and envelope["source"] != {
        "kind": "stream_authority",
        "authority_id": expected_event_id,
    }:
        raise stream_redis.StreamContractError("v4_rebuild_candidate_open_mismatch")
    if expected_event_type != "stream.open" and envelope["source"] != {
        "kind": "run_event",
        "run_event_id": expected_event_id,
        "sequence": expected_sequence,
    }:
        raise stream_redis.StreamContractError("v4_rebuild_candidate_source_mismatch")
    return envelope


def _text(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8")
    if not isinstance(value, str):
        raise ValueError("v4_rebuild_redis_text_invalid")
    return value


__all__ = ["RedisV4SuccessorRebuildTransport"]
