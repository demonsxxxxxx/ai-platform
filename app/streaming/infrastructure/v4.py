"""Durable Agent-kernel v4 event projection and publication helpers.

The callback receipt and these rows are written on the caller's PostgreSQL
transaction.  Redis is a bounded transport only; the reserved metadata below
is never included in a public envelope.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from redis.exceptions import ResponseError

from app.runs.api import CHAT_PUBLIC_PROJECTION_VERSION, public_terminal_projection
from app.streaming import postgres
from app.streaming.application.callback_events_v4 import (
    V4CallbackItem,
    callback_item_to_v4,
)
from app.streaming.domain.live import redis_id_tuple as _redis_id_tuple
from app.streaming.domain.live import stream_key
from app.streaming.domain.public_events_v4 import (
    V4_METADATA_KEY,
    V4_METADATA_VERSION,
    V4_PUBLIC_STAGE,
    V4ProjectionError,
    V4StreamEntry,
    _APPLICATION_EVENT_TYPES,
    _MESSAGE_EVENT_TYPES,
    _RUN_DOMAIN_EVENT_TYPES,
    _immutable_v4_payload,
    _nonempty,
    _safe_ref,
    _stable_event_id,
    _stable_run_event_id,
    _validate_control_payload,
    _validate_payload,
    build_public_v4_control,
    build_v4_control,
    opaque_message_id,
    project_public_envelope_v4,
    project_public_v4,
    stream_end_event_id,
    strip_internal_envelope,
    validate_internal_envelope_v4 as _validate_internal_envelope,
)
from app.streaming.domain.transport import (
    ResumeDecision,
    StreamCursor,
    StreamGap,
    canonical_json_bytes,
)
from app.streaming.redis import (
    RedisStreamBridge,
    StreamContractError,
    StreamTransportUnavailable,
    StreamAuthority,
    get_stream_authority,
)


@dataclass(frozen=True, slots=True)
class V4Publication:
    event_id: str
    redis_id: str
    envelope: dict[str, object]


_V4_REPLAY_PAGE_LUA = r"""
local rows = redis.call('XRANGE', KEYS[1], ARGV[1], ARGV[2], 'COUNT', tonumber(ARGV[3]))
local predecessor = ARGV[4]
if predecessor == '' then
  return {1, rows}
end
if #rows == 0 or rows[1][1] ~= predecessor then
  return {0, rows}
end
table.remove(rows, 1)
return {1, rows}
"""


async def append_application_v4_row(
    conn: Any,
    *,
    tenant_id: str,
    run_id: str,
    attempt_id: str,
    batch_id: str,
    callback_index: int,
    batch_index: int,
    event_type: str,
    payload: Mapping[str, object],
    authority: StreamAuthority,
    execution_lease_id: str | None,
    event_id: str | None = None,
    terminal_intent_id: str | None = None,
    message_id: str | None = None,
    trace_ref: str | None = None,
    causation_event_id: str | None = None,
    source_event_id: str | None = None,
    source_run_id: str | None = None,
) -> Mapping[str, object]:
    """Append one idempotent application row without touching Redis."""

    if not isinstance(event_type, str) or event_type not in _APPLICATION_EVENT_TYPES:
        raise V4ProjectionError("v4_callback_item_invalid")
    is_run_domain = event_type in _RUN_DOMAIN_EVENT_TYPES
    if execution_lease_id is None:
        expected_authority_state = (
            "confirmed" if event_type == "run.cancel_requested" else "terminal"
        )
        if not is_run_domain or (
            authority.state != expected_authority_state
            and not (
                authority.state == "admission_pending"
                and event_type in {"run.cancel_requested", "run.cancelled", "run.succeeded", "run.failed"}
            )
        ):
            raise V4ProjectionError("v4_run_authority_scope_mismatch")
    else:
        _nonempty(execution_lease_id, "execution_lease_id")
        if authority.state != "confirmed":
            raise V4ProjectionError("v4_callback_authority_scope_mismatch")
    if (
        tenant_id != authority.tenant_id
        or run_id != authority.run_id
        or attempt_id != authority.attempt_id
        or (not is_run_domain and authority.state != "confirmed")
        or not isinstance(batch_id, str)
        or not batch_id
    ):
        raise V4ProjectionError("v4_callback_authority_scope_mismatch")
    if is_run_domain and event_id is None:
        event_id = _stable_run_event_id(
            tenant_id,
            run_id,
            attempt_id,
            authority.stream_incarnation,
            event_type,
        )
    if event_id is not None:
        _safe_ref(event_id, name="event_id")
    if terminal_intent_id is not None:
        _safe_ref(terminal_intent_id, name="terminal_intent_id")
    if event_type in {"run.succeeded", "run.failed", "run.cancelled"}:
        terminal_payload_id = payload.get("terminal_event_id") if isinstance(payload, Mapping) else None
        if event_id != terminal_payload_id:
            raise V4ProjectionError("v4_terminal_event_id_mismatch")
        if terminal_intent_id is None or terminal_intent_id != event_id:
            raise V4ProjectionError("v4_terminal_intent_identity_mismatch")

    if (
        isinstance(callback_index, bool)
        or not isinstance(callback_index, int)
        or callback_index < 0
        or isinstance(batch_index, bool)
        or not isinstance(batch_index, int)
        or batch_index < 0
    ):
        raise V4ProjectionError("v4_callback_item_invalid")
    _validate_payload(event_type, payload)
    if event_type in _MESSAGE_EVENT_TYPES:
        if not isinstance(message_id, str):
            raise V4ProjectionError("v4_callback_message_id_invalid")
        _safe_ref(message_id, name="message_id")
    elif message_id is not None:
        _safe_ref(message_id, name="message_id")
    if source_event_id is not None:
        _safe_ref(source_event_id, name="source_event_id")
    if source_run_id is not None:
        _safe_ref(source_run_id, name="source_run_id")
        if source_run_id != run_id:
            raise V4ProjectionError("v4_callback_source_run_mismatch")
    if trace_ref is not None:
        _safe_ref(trace_ref, name="trace_ref")
    if causation_event_id is not None:
        _safe_ref(causation_event_id, name="causation_event_id")
    metadata = {
        "version": V4_METADATA_VERSION,
        "callback_batch_id": batch_id,
        "callback_index": callback_index,
        "batch_index": batch_index,
        "attempt_id": attempt_id,
        "stream_incarnation": authority.stream_incarnation,
        "authorization_epoch": authority.authorization_epoch,
        "execution_lease_id": execution_lease_id,
        "message_id": message_id,
        "trace_ref": trace_ref,
        "causation_event_id": causation_event_id,
        "source_event_id": source_event_id,
        "source_run_id": source_run_id,
        "terminal_intent_id": terminal_intent_id,
        "publication_state": "pending",
        "publication_attempts": 0,
        "lease_fence": "active" if execution_lease_id is not None else "not_required",
        "cancellation_fence": "not_requested",
    }
    expected_payload = {**dict(payload), V4_METADATA_KEY: metadata}
    event = postgres.LedgerEvent(
        event_type=event_type,
        stage=V4_PUBLIC_STAGE,
        payload=expected_payload,
        visible_to_user=True,
        trace_id=trace_ref,
    )
    event_id = event_id or _stable_event_id(
        tenant_id, run_id, attempt_id, batch_id, callback_index, batch_index
    )
    existing_result = await conn.execute(
        "select id, tenant_id, run_id, sequence, event_type, visible_to_user, payload_json, stream_publication_state, stream_publication_attempts, stream_publication_next_attempt_at, created_at from run_events where id = %s for update",
        (event_id,),
    )
    existing = await existing_result.fetchone()
    if existing is not None:
        if not isinstance(existing, Mapping) or any(
            (
                existing.get("tenant_id") != tenant_id,
                existing.get("run_id") != run_id,
                existing.get("event_type") != event_type,
                existing.get("visible_to_user") is not True,
                _immutable_v4_payload(existing.get("payload_json"))
                != _immutable_v4_payload(expected_payload),
            )
        ):
            raise V4ProjectionError("v4_callback_existing_row_conflict")
        return existing
    receipt = await postgres.append_event(
        conn,
        tenant_id=tenant_id,
        run_id=run_id,
        event=event,
        event_id=event_id,
    )
    await conn.execute(
        """
        update run_events
        set stream_publication_state = 'pending',
            stream_publication_attempts = 0,
            stream_publication_next_attempt_at = now(),
            stream_publication_last_error = null
        where id = %s
        """,
        (receipt.event_id,),
    )
    return {
        "id": receipt.event_id,
        "tenant_id": tenant_id,
        "run_id": run_id,
        "sequence": receipt.cursor.sequence,
        "event_type": event_type,
        "visible_to_user": True,
        "payload_json": dict(event.payload),
        "stream_publication_state": "pending",
        "stream_publication_attempts": 0,
        "stream_publication_next_attempt_at": datetime.now(timezone.utc),
        "created_at": receipt.created_at,
    }


async def append_run_v4_row(
    conn: Any,
    *,
    tenant_id: str,
    run_id: str,
    attempt_id: str,
    event_type: str,
    payload: Mapping[str, object],
    batch_id: str,
    event_id: str | None = None,
    terminal_intent_id: str | None = None,
    trace_ref: str | None = None,
) -> Mapping[str, object] | None:
    """Append one Run-owned v4 row on the caller's PostgreSQL transaction."""

    authority = await get_stream_authority(
        conn, tenant_id=tenant_id, run_id=run_id, for_update=True
    )
    if authority is None:
        return None
    if not attempt_id:
        attempt_id = authority.attempt_id
    if event_type in {"run.succeeded", "run.failed", "run.cancelled"}:
        if event_id is None or terminal_intent_id != event_id:
            raise V4ProjectionError("v4_terminal_intent_identity_mismatch")
    return await append_application_v4_row(
        conn,
        tenant_id=tenant_id,
        run_id=run_id,
        attempt_id=attempt_id,
        batch_id=batch_id,
        callback_index=0,
        batch_index=0,
        event_type=event_type,
        payload=payload,
        authority=authority,
        execution_lease_id=None,
        event_id=event_id,
        terminal_intent_id=terminal_intent_id,
        trace_ref=trace_ref,
        source_event_id=terminal_intent_id,
        source_run_id=run_id,
    )


async def append_run_cancel_requested_v4_row(
    conn: Any,
    *,
    tenant_id: str,
    run_id: str,
    source: str,
    trace_ref: str | None = None,
) -> Mapping[str, object] | None:
    """Append the first authoritative cancellation request on its Run transaction."""

    if source not in {"user", "system"}:
        raise V4ProjectionError("v4_run_cancel_source_invalid")
    return await append_run_v4_row(
        conn,
        tenant_id=tenant_id,
        run_id=run_id,
        attempt_id="",
        event_type="run.cancel_requested",
        payload={"source": source},
        batch_id=f"cancel-request-{run_id}",
        trace_ref=trace_ref,
    )


def _run_terminal_payload(
    *,
    status: str,
    terminal_event_id: str,
    error_code: object = None,
    reason_code: str = "user_cancelled",
) -> dict[str, object]:
    if status == "succeeded":
        return {"terminal_event_id": terminal_event_id, "hydrate_required": True}
    if status == "cancelled":
        if reason_code not in {"user_cancelled", "policy_cancelled", "timeout"}:
            raise V4ProjectionError("v4_run_cancel_reason_invalid")
        return {
            "terminal_event_id": terminal_event_id,
            "hydrate_required": True,
            "reason_code": reason_code,
        }
    if status != "failed":
        raise V4ProjectionError("v4_run_terminal_status_invalid")
    projection = public_terminal_projection(status, error_code)
    if projection is None or projection.get("detail_kind") != "failed":
        raise V4ProjectionError("v4_run_public_terminal_projection_unavailable")
    detail_code = str(projection.get("detail_code") or "")
    default_message = str(projection.get("message") or "")
    if not detail_code or not default_message:
        raise V4ProjectionError("v4_run_public_terminal_projection_invalid")
    return {
        "terminal_event_id": terminal_event_id,
        "hydrate_required": True,
        "projection_version": CHAT_PUBLIC_PROJECTION_VERSION,
        "code": detail_code,
        "default_message": default_message,
        "detail": None,
    }


async def append_run_terminal_v4_row(
    conn: Any,
    *,
    tenant_id: str,
    run_id: str,
    attempt_id: str,
    status: str,
    terminal_event_id: str,
    error_code: object = None,
    reason_code: str = "user_cancelled",
    trace_ref: str | None = None,
) -> Mapping[str, object] | None:
    """Append the terminal Run event using the existing terminal intent identity."""

    return await append_run_v4_row(
        conn,
        tenant_id=tenant_id,
        run_id=run_id,
        attempt_id=attempt_id,
        event_type=f"run.{status}",
        payload=_run_terminal_payload(
            status=status,
            terminal_event_id=terminal_event_id,
            error_code=error_code,
            reason_code=reason_code,
        ),
        batch_id=terminal_event_id,
        event_id=terminal_event_id,
        terminal_intent_id=terminal_event_id,
        trace_ref=trace_ref,
    )


async def append_callback_v4_rows(
    conn: Any,
    *,
    tenant_id: str,
    run_id: str,
    attempt_id: str,
    batch_id: str,
    items: Sequence[V4CallbackItem],
    authority: StreamAuthority,
    execution_lease_id: str,
) -> tuple[Mapping[str, object], ...]:
    """Append callback-derived v4 rows in the callback receipt transaction."""

    rows: list[Mapping[str, object]] = []
    for item in items:
        if item.source_run_id is not None and item.source_run_id != run_id:
            raise V4ProjectionError("v4_callback_source_run_mismatch")
        rows.append(
            await append_application_v4_row(
                conn,
                tenant_id=tenant_id,
                run_id=run_id,
                attempt_id=attempt_id,
                batch_id=batch_id,
                callback_index=item.callback_index,
                batch_index=item.batch_index,
                event_type=item.event_type,
                payload=item.payload,
                authority=authority,
                execution_lease_id=execution_lease_id,
                message_id=item.message_id,
                trace_ref=item.trace_ref,
                causation_event_id=item.causation_event_id,
                source_event_id=item.source_event_id,
                source_run_id=item.source_run_id,
            )
        )
    return tuple(rows)


async def list_pending_v4_rows(
    conn: Any,
    *,
    limit: int = 64,
) -> tuple[Mapping[str, object], ...]:
    if isinstance(limit, bool) or not 1 <= limit <= 256:
        raise ValueError("v4_pending_limit_invalid")
    result = await conn.execute(
        """
        select id, tenant_id, run_id, sequence, event_type, visible_to_user,
               payload_json, stream_publication_state, stream_publication_attempts,
               stream_publication_next_attempt_at, created_at
        from run_events
        where visible_to_user = true
          and stream_publication_state = 'pending'
          and (stream_publication_next_attempt_at is null or stream_publication_next_attempt_at <= now())
          and not exists (
            select 1
            from run_events predecessor
            where predecessor.tenant_id = run_events.tenant_id
              and predecessor.run_id = run_events.run_id
              and predecessor.visible_to_user = true
              and predecessor.stream_publication_state = 'pending'
              and predecessor.sequence < run_events.sequence
          )
        order by run_id asc, sequence asc
        limit %s
        for update skip locked
        """,
        (limit,),
    )
    return tuple(await result.fetchall())


async def mark_v4_published(
    conn: Any,
    *,
    event_id: str,
    redis_id: str,
) -> bool:
    """Record transport identity only after XADD succeeds."""

    _nonempty(event_id, "event_id")
    _nonempty(redis_id, "redis_id")
    result = await conn.execute(
        """
        update run_events
        set stream_publication_state = 'published',
            stream_publication_redis_id = %s,
            stream_publication_next_attempt_at = null,
            stream_publication_last_error = null,
            payload_json = jsonb_set(
              payload_json, '{__stream_v4,publication_state}', to_jsonb('published'::text)
            )
        where id = %s
          and stream_publication_state = 'pending'
        returning id
        """,
        (redis_id, event_id),
    )
    return await result.fetchone() is not None


async def mark_v4_attempt(
    conn: Any,
    *,
    event_id: str,
) -> None:
    await conn.execute(
        """
        update run_events
        set stream_publication_attempts = coalesce(stream_publication_attempts, 0) + 1,
            stream_publication_next_attempt_at = now() + interval '5 seconds',
            payload_json = jsonb_set(
              payload_json, '{__stream_v4,publication_attempts}',
              to_jsonb(coalesce((payload_json -> '__stream_v4' ->> 'publication_attempts')::integer, 0) + 1)
            )
        where id = %s and stream_publication_state = 'pending'
        """,
        (event_id,),
    )


async def mark_v4_retry_error(
    conn: Any,
    *,
    event_id: str,
    error: str,
) -> None:
    await conn.execute(
        """
        update run_events
        set stream_publication_last_error = %s
        where id = %s and stream_publication_state = 'pending'
        """,
        (_nonempty(error, "publication_error")[:120], event_id),
    )


async def suppress_v4_event(
    conn: Any,
    *,
    event_id: str,
    reason: str,
) -> bool:
    _nonempty(event_id, "event_id")
    _nonempty(reason, "suppression_reason")
    result = await conn.execute(
        """
        update run_events
        set stream_publication_state = 'suppressed',
            stream_publication_next_attempt_at = null,
            stream_publication_last_error = %s,
            payload_json = jsonb_set(
              jsonb_set(payload_json, '{__stream_v4,publication_state}', to_jsonb('suppressed'::text)),
              '{__stream_v4,suppression_reason}', to_jsonb(%s::text)
            )
        where id = %s and stream_publication_state = 'pending'
        returning id
        """,
        (reason, reason, event_id),
    )
    return await result.fetchone() is not None


class V4RedisStreamBridge:
    """Use the existing Redis bridge client for the v4 bounded transport."""

    def __init__(self, bridge: RedisStreamBridge | None = None) -> None:
        self._bridge = bridge or RedisStreamBridge()
        self._owns_bridge = bridge is None

    async def aclose(self) -> None:
        if self._owns_bridge:
            await self._bridge.aclose()

    async def append(self, envelope: Mapping[str, object]) -> str:
        try:
            internal = _validate_internal_envelope(envelope)
        except V4ProjectionError:
            raise
        event_type = _nonempty(internal.get("event_type"), "event_type")
        if event_type in {"stream.heartbeat", "stream.gap"}:
            raise StreamContractError("v4_control_not_replayable")
        tenant_scope = _nonempty(internal.get("tenant_scope"), "tenant_scope")
        run_id = _nonempty(internal.get("run_id"), "run_id")
        incarnation = internal.get("stream_incarnation")
        if isinstance(incarnation, bool) or not isinstance(incarnation, int) or incarnation < 1:
            raise V4ProjectionError("v4_stream_incarnation_invalid")
        payload = canonical_json_bytes(dict(internal))
        terminal_event_id = ""
        if event_type in {"run.succeeded", "run.cancelled", "run.failed"}:
            terminal_event_id = _nonempty(internal["event_id"], "event_id")
            if _nonempty(internal["payload"].get("terminal_event_id"), "terminal_event_id") != terminal_event_id:
                raise StreamContractError("v4_terminal_event_id_mismatch")
        elif event_type == "stream.end":
            terminal_event_id = _nonempty(
                _validate_control_payload(event_type, internal["payload"])["terminal_event_id"],
                "terminal_event_id",
            )
        try:
            redis_id = await self._bridge.append_canonical(
                tenant_scope_value=tenant_scope,
                run_id=run_id,
                stream_incarnation=incarnation,
                event_id=_nonempty(internal.get("event_id"), "event_id"),
                event_type=event_type,
                envelope_bytes=payload,
                terminal_event_id=terminal_event_id,
                protocol="v4",
            )
            if event_type not in {"run.succeeded", "run.cancelled", "run.failed"}:
                return redis_id
            terminal_event_id = _nonempty(internal["event_id"], "event_id")
            end = build_v4_control(
                event_id=stream_end_event_id(terminal_event_id),
                tenant_scope=tenant_scope,
                run_id=run_id,
                attempt_id=_nonempty(internal.get("attempt_id"), "attempt_id"),
                stream_incarnation=incarnation,
                event_type="stream.end",
                payload={"terminal_event_id": terminal_event_id},
                source={"kind": "terminal_intent", "terminal_event_id": terminal_event_id},
                causation_event_id=terminal_event_id,
                emitted_at=internal["emitted_at"],
            )
            return await self._bridge.append_canonical(
                tenant_scope_value=tenant_scope,
                run_id=run_id,
                stream_incarnation=incarnation,
                event_id=str(end["event_id"]),
                event_type="stream.end",
                envelope_bytes=canonical_json_bytes(end),
                terminal_event_id=terminal_event_id,
                protocol="v4",
            )
        except StreamContractError:
            raise
        except ResponseError as exc:
            raise StreamTransportUnavailable("v4_stream_append_unavailable") from exc
        except Exception as exc:
            if isinstance(exc, StreamTransportUnavailable):
                raise
            raise StreamTransportUnavailable("v4_stream_append_unavailable") from exc

    async def publish_non_replayable(self, envelope: Mapping[str, object]) -> str:
        """Publish heartbeat/gap live-only and return the latest real cursor."""

        internal = _validate_internal_envelope(envelope)
        if internal["event_type"] not in {"stream.heartbeat", "stream.gap"}:
            raise StreamContractError("v4_control_replayable")
        latest_cursor = await self.latest_cursor(
            tenant_scope_value=str(internal["tenant_scope"]),
            run_id=str(internal["run_id"]),
            attempt_id=str(internal["attempt_id"]),
            stream_incarnation=int(internal["stream_incarnation"]),
        )
        channel = stream_key(
            tenant_scope_value=str(internal["tenant_scope"]),
            run_id=str(internal["run_id"]),
            stream_incarnation=int(internal["stream_incarnation"]),
        ).removesuffix(":events") + ":live"
        try:
            latest = StreamCursor.parse(
                latest_cursor,
                run_id=str(internal["run_id"]),
            )
            publication = json.dumps(
                {
                    "redis_id": latest.redis_id,
                    "envelope": canonical_json_bytes(dict(internal)).decode("utf-8"),
                },
                separators=(",", ":"),
                sort_keys=True,
            )
            await self._bridge._publish_client.publish(channel, publication)
            return latest_cursor
        except Exception as exc:
            raise StreamTransportUnavailable("v4_control_publish_unavailable") from exc

    async def latest_cursor(
        self,
        *,
        tenant_scope_value: str,
        run_id: str,
        attempt_id: str,
        stream_incarnation: int,
    ) -> str:
        """Return the latest retained cursor owned by the current Redis stream."""

        bounds = await self.retained_bounds(
            tenant_scope_value=tenant_scope_value,
            run_id=run_id,
            attempt_id=attempt_id,
            stream_incarnation=stream_incarnation,
        )
        if bounds is None:
            raise StreamTransportUnavailable("v4_stream_cursor_unavailable")
        return bounds[1].cursor.event_id

    async def build_heartbeat(
        self,
        *,
        event_id: str,
        tenant_scope_value: str,
        run_id: str,
        attempt_id: str,
        stream_incarnation: int,
        status: str,
        emitted_at: str | datetime | None = None,
    ) -> tuple[dict[str, object], str]:
        """Build a live-only heartbeat and bind it to a real retained cursor."""

        if status not in {"queued", "running"}:
            raise V4ProjectionError("v4_stream_heartbeat_status_invalid")
        cursor = await self.latest_cursor(
            tenant_scope_value=tenant_scope_value,
            run_id=run_id,
            attempt_id=attempt_id,
            stream_incarnation=stream_incarnation,
        )
        envelope = build_v4_control(
            event_id=event_id,
            tenant_scope=tenant_scope_value,
            run_id=run_id,
            attempt_id=attempt_id,
            stream_incarnation=stream_incarnation,
            event_type="stream.heartbeat",
            payload={"status": status},
            source={"kind": "stream_authority", "authority_id": event_id},
            emitted_at=emitted_at,
        )
        return envelope, cursor

    async def build_gap(
        self,
        *,
        event_id: str,
        tenant_scope_value: str,
        run_id: str,
        attempt_id: str,
        requested_event_id: str | None,
        requested_stream_incarnation: int | None,
        current_stream_incarnation: int,
        reason: str,
        emitted_at: str | datetime | None = None,
    ) -> tuple[dict[str, object], str]:
        """Build a gap from Redis-owned retained bounds, never caller bounds."""

        requested_redis_id: str | None = None
        if requested_event_id is not None:
            try:
                parsed = StreamCursor.parse(requested_event_id, run_id=run_id)
                if requested_stream_incarnation != parsed.stream_incarnation:
                    raise StreamContractError("v4_gap_cursor_incarnation_invalid")
                requested_redis_id = parsed.redis_id
            except StreamContractError:
                try:
                    _redis_id_tuple(requested_event_id)
                except Exception as exc:
                    raise StreamContractError("v4_gap_cursor_invalid") from exc
                requested_redis_id = requested_event_id
        bounds = await self.retained_bounds(
            tenant_scope_value=tenant_scope_value,
            run_id=run_id,
            attempt_id=attempt_id,
            stream_incarnation=current_stream_incarnation,
        )
        if bounds is None:
            if reason != "stream_missing":
                raise StreamTransportUnavailable("v4_gap_bounds_unavailable")
            earliest_event_id = None
            latest_event_id = None
            cursor = StreamCursor(
                run_id,
                current_stream_incarnation,
                "0-0",
            ).event_id
        else:
            first, last = bounds
            earliest_event_id = first.cursor.redis_id
            latest_event_id = last.cursor.redis_id
            cursor = last.cursor.event_id
        envelope = build_v4_control(
            event_id=event_id,
            tenant_scope=tenant_scope_value,
            run_id=run_id,
            attempt_id=attempt_id,
            stream_incarnation=current_stream_incarnation,
            event_type="stream.gap",
            payload={
                "reason": reason,
                "recovery": "reload_durable_state",
                "requested_event_id": requested_redis_id,
                "requested_stream_incarnation": requested_stream_incarnation,
                "current_stream_incarnation": current_stream_incarnation,
                "earliest_available_event_id": earliest_event_id,
                "latest_available_event_id": latest_event_id,
            },
            source={"kind": "stream_authority", "authority_id": event_id},
            emitted_at=emitted_at,
        )
        return envelope, cursor

    async def publish_gap(self, **kwargs: object) -> tuple[dict[str, object], str]:
        envelope, cursor = await self.build_gap(**kwargs)
        published_cursor = await self.publish_non_replayable(envelope)
        if published_cursor != cursor:
            raise StreamContractError("v4_gap_cursor_changed")
        return envelope, cursor

    def _decode(
        self,
        row: tuple[object, Mapping[str, object]],
        *,
        tenant_scope_value: str,
        run_id: str,
        attempt_id: str,
        stream_incarnation: int,
    ) -> V4StreamEntry:
        redis_id = str(row[0])
        _redis_id_tuple(redis_id)
        fields = row[1]
        raw = fields.get("envelope")
        if not isinstance(raw, str):
            raise StreamContractError("v4_stream_envelope_missing")
        try:
            envelope = _validate_internal_envelope(json.loads(raw))
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise StreamContractError("v4_stream_envelope_invalid") from exc
        if (
            envelope["tenant_scope"] != tenant_scope_value
            or envelope["run_id"] != run_id
            or envelope["attempt_id"] != attempt_id
            or envelope["stream_incarnation"] != stream_incarnation
        ):
            raise StreamContractError("v4_stream_authority_mismatch")
        return V4StreamEntry(StreamCursor(run_id, stream_incarnation, redis_id), envelope)

    async def retained_bounds(
        self,
        *,
        tenant_scope_value: str,
        run_id: str,
        attempt_id: str,
        stream_incarnation: int,
    ) -> tuple[V4StreamEntry, V4StreamEntry] | None:
        key = stream_key(
            tenant_scope_value=tenant_scope_value,
            run_id=run_id,
            stream_incarnation=stream_incarnation,
        )
        try:
            first = await self._bridge._publish_client.xrange(key, min="-", max="+", count=1)
            last = await self._bridge._publish_client.xrevrange(key, max="+", min="-", count=1)
        except Exception as exc:
            raise StreamTransportUnavailable("v4_stream_bounds_unavailable") from exc
        if not first and not last:
            return None
        if not first or not last:
            raise StreamTransportUnavailable("v4_stream_bounds_unproven")
        return self._decode(
            first[0],
            tenant_scope_value=tenant_scope_value,
            run_id=run_id,
            attempt_id=attempt_id,
            stream_incarnation=stream_incarnation,
        ), self._decode(
            last[0],
            tenant_scope_value=tenant_scope_value,
            run_id=run_id,
            attempt_id=attempt_id,
            stream_incarnation=stream_incarnation,
        )

    async def resolve_resume(
        self,
        *,
        tenant_scope_value: str,
        run_id: str,
        attempt_id: str,
        current_stream_incarnation: int,
        last_event_id: str | None,
    ) -> ResumeDecision:
        cursor = StreamCursor.parse(last_event_id, run_id=run_id) if last_event_id else None
        if cursor and cursor.stream_incarnation > current_stream_incarnation:
            raise StreamContractError("stream_cursor_future_incarnation")
        if cursor and cursor.stream_incarnation < current_stream_incarnation:
            return ResumeDecision(None, StreamGap("stream_incarnation_mismatch", cursor.event_id, cursor.stream_incarnation, current_stream_incarnation))
        bounds = await self.retained_bounds(
            tenant_scope_value=tenant_scope_value,
            run_id=run_id,
            attempt_id=attempt_id,
            stream_incarnation=current_stream_incarnation,
        )
        if bounds is None:
            return ResumeDecision(None, StreamGap("stream_missing", cursor.event_id if cursor else None, cursor.stream_incarnation if cursor else None, current_stream_incarnation))
        first, last = bounds
        if cursor is None:
            return ResumeDecision("0-0" if first.envelope["event_type"] == "stream.open" else None, None if first.envelope["event_type"] == "stream.open" else StreamGap("retained_history_unavailable", None, None, current_stream_incarnation, first.cursor.event_id, last.cursor.event_id))
        if _redis_id_tuple(cursor.redis_id) > _redis_id_tuple(last.cursor.redis_id):
            raise StreamContractError("stream_cursor_future_redis_id")
        if _redis_id_tuple(cursor.redis_id) < _redis_id_tuple(first.cursor.redis_id):
            return ResumeDecision(None, StreamGap("retained_history_unavailable", cursor.event_id, cursor.stream_incarnation, current_stream_incarnation, first.cursor.event_id, last.cursor.event_id))
        key = stream_key(tenant_scope_value=tenant_scope_value, run_id=run_id, stream_incarnation=current_stream_incarnation)
        try:
            exact = await self._bridge._publish_client.xrange(key, min=cursor.redis_id, max=cursor.redis_id, count=1)
        except Exception as exc:
            raise StreamTransportUnavailable("v4_stream_cursor_lookup_unavailable") from exc
        return ResumeDecision(cursor.redis_id if exact else None, None if exact else StreamGap("stream_continuity_unproven", cursor.event_id, cursor.stream_incarnation, current_stream_incarnation, first.cursor.event_id, last.cursor.event_id))

    async def replay_page(
        self,
        *,
        tenant_scope_value: str,
        run_id: str,
        attempt_id: str,
        stream_incarnation: int,
        after_redis_id: str,
        through_redis_id: str,
    ) -> tuple[V4StreamEntry, ...]:
        after = _redis_id_tuple(after_redis_id)
        through = _redis_id_tuple(through_redis_id)
        if after >= through:
            return ()
        key = stream_key(tenant_scope_value=tenant_scope_value, run_id=run_id, stream_incarnation=stream_incarnation)
        predecessor = "" if after_redis_id == "0-0" else after_redis_id
        minimum = f"({after_redis_id}" if not predecessor else predecessor
        count = 128 if not predecessor else 129
        try:
            result = await self._bridge._publish_client.eval(
                _V4_REPLAY_PAGE_LUA,
                1,
                key,
                minimum,
                through_redis_id,
                count,
                predecessor,
            )
        except Exception as exc:
            raise StreamTransportUnavailable("v4_stream_replay_unavailable") from exc
        if not isinstance(result, (tuple, list)) or len(result) != 2:
            raise StreamTransportUnavailable("v4_stream_replay_result_invalid")
        continuity, rows = result
        if int(continuity) != 1:
            raise StreamContractError("stream_replay_continuity_unproven")
        if not isinstance(rows, (tuple, list)):
            raise StreamTransportUnavailable("v4_stream_replay_result_invalid")
        return tuple(
            self._decode(
                row,
                tenant_scope_value=tenant_scope_value,
                run_id=run_id,
                attempt_id=attempt_id,
                stream_incarnation=stream_incarnation,
            )
            for row in rows or ()
        )

    def decode_live_publication(
        self,
        *,
        redis_id: str,
        envelope_json: str,
        tenant_scope_value: str,
        run_id: str,
        attempt_id: str,
        stream_incarnation: int,
    ) -> V4StreamEntry:
        return self._decode(
            (redis_id, {"envelope": envelope_json}),
            tenant_scope_value=tenant_scope_value,
            run_id=run_id,
            attempt_id=attempt_id,
            stream_incarnation=stream_incarnation,
        )


__all__ = [
    "V4CallbackItem",
    "V4ProjectionError",
    "V4Publication",
    "V4RedisStreamBridge",
    "V4StreamEntry",
    "append_application_v4_row",
    "append_run_v4_row",
    "append_callback_v4_rows",
    "build_public_v4_control",
    "build_v4_control",
    "callback_item_to_v4",
    "list_pending_v4_rows",
    "mark_v4_attempt",
    "mark_v4_published",
    "mark_v4_retry_error",
    "suppress_v4_event",
    "opaque_message_id",
    "project_public_envelope_v4",
    "project_public_v4",
    "stream_end_event_id",
    "strip_internal_envelope",
]
