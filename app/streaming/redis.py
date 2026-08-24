"""Bounded Redis Streams transport for public SSE frames."""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from psycopg import AsyncConnection
from redis.asyncio import Redis
from redis.exceptions import ResponseError

from app.settings import get_settings
from app.streaming.api import (
    STREAM_KEY_PREFIX as STREAM_KEY_PREFIX,
    redis_id_tuple as _redis_id_tuple,
    stream_key,
    stream_live_channel,
)
from app.streaming.contracts import (
    PUBLIC_EVENT_TYPES as PUBLIC_EVENT_TYPES,
    STREAM_DESIGN_ID,
    STREAM_EVENT_SCHEMA,
    STREAM_GAP_SCHEMA as STREAM_GAP_SCHEMA,
    STREAM_PROJECTION_VERSION,
    ResumeDecision,
    StreamContractError,
    StreamCursor,
    StreamEntry,
    StreamEnvelope,
    StreamGap,
    StreamProjectionError as StreamProjectionError,
    _rfc3339_utc,
    canonical_json_bytes,
    committed_public_stream_event,
    new_envelope,
    stable_event_id as stable_event_id,
    tenant_scope,
    validate_public_payload as validate_public_payload,
)

SSE_PUBLISH_MAX_CONNECTIONS = 16
SSE_STREAM_MAXLEN = 10000
SSE_STREAM_ACTIVE_IDLE_TTL_MS = 7200000
SSE_STREAM_TERMINAL_TTL_MS = 7200000
SSE_STREAM_READ_COUNT = 128
SSE_AUTHORITY_LEASE_SECONDS = 15
_REDIS_CONNECT_TIMEOUT_SECONDS = 2
_REDIS_PUBLISH_TIMEOUT_SECONDS = 5

_APPEND_WITH_TTL_LUA = """
local phase=redis.call('HGET',KEYS[2],'phase')
local request_protocol=ARGV[8]
if not request_protocol or request_protocol == '' then request_protocol='v3' end
if phase then
  local stored_protocol=redis.call('HGET',KEYS[2],'open_protocol')
  if not stored_protocol or stored_protocol == '' then stored_protocol='v3' end
  if stored_protocol ~= request_protocol then
    return redis.error_reply('stream_protocol_conflict')
  end
end
if ARGV[5] == 'stream_open' then
  if phase
     and redis.call('HGET',KEYS[2],'open_event_id') == ARGV[2]
     and redis.call('HGET',KEYS[2],'open_digest') == ARGV[6] then
    if phase == 'open' then
      redis.call('PEXPIRE',KEYS[1],ARGV[4]);redis.call('PEXPIRE',KEYS[2],ARGV[4])
    end
    return redis.call('HGET',KEYS[2],'open_redis_id')
  end
  if phase or redis.call('XLEN',KEYS[1]) ~= 0 then return redis.error_reply('stream_open_conflict') end
elseif ARGV[5] == 'terminal' then
  if (phase == 'terminal' or phase == 'ended')
     and redis.call('HGET',KEYS[2],'terminal_event_id') == ARGV[2]
     and redis.call('HGET',KEYS[2],'terminal_digest') == ARGV[6] then
    redis.call('PEXPIRE',KEYS[1],ARGV[4]);redis.call('PEXPIRE',KEYS[2],ARGV[4])
    return redis.call('HGET',KEYS[2],'terminal_redis_id')
  end
  if phase ~= 'open' then return redis.error_reply('stream_terminal_conflict') end
elseif ARGV[5] == 'end' then
  if phase == 'ended'
     and redis.call('HGET',KEYS[2],'end_event_id') == ARGV[2]
     and redis.call('HGET',KEYS[2],'end_digest') == ARGV[6] then
    redis.call('PEXPIRE',KEYS[1],ARGV[4]);redis.call('PEXPIRE',KEYS[2],ARGV[4])
    return redis.call('HGET',KEYS[2],'end_redis_id')
  end
  local terminal_event_id=redis.call('HGET',KEYS[2],'terminal_event_id')
  if phase ~= 'terminal' or terminal_event_id ~= ARGV[7] then return redis.error_reply('stream_end_without_terminal') end
else
  if phase ~= 'open' then return redis.error_reply('stream_terminal_closed') end
  if request_protocol == 'v4'
     and redis.call('HGET',KEYS[2],'last_event_id') == ARGV[2] then
    if redis.call('HGET',KEYS[2],'last_event_digest') ~= ARGV[6] then
      return redis.error_reply('stream_event_receipt_conflict')
    end
    redis.call('PEXPIRE',KEYS[1],ARGV[4]);redis.call('PEXPIRE',KEYS[2],ARGV[4])
    return redis.call('HGET',KEYS[2],'last_event_redis_id')
  end
end
local id=redis.call('XADD',KEYS[1],'MAXLEN','~',ARGV[1],'*','envelope',ARGV[3])
if ARGV[5] == 'stream_open' then
  if request_protocol == 'v4' then
    redis.call('HSET',KEYS[2],'phase','open','open_event_id',ARGV[2],'open_digest',ARGV[6],'open_redis_id',id,'open_protocol',request_protocol)
  else
    redis.call('HSET',KEYS[2],'phase','open','open_event_id',ARGV[2],'open_digest',ARGV[6],'open_redis_id',id)
  end
end
if ARGV[5] == 'terminal' then
  redis.call('HSET',KEYS[2],'phase','terminal','terminal_event_id',ARGV[2],'terminal_digest',ARGV[6],'terminal_redis_id',id)
end
if ARGV[5] == 'end' then
  redis.call('HSET',KEYS[2],'phase','ended','end_event_id',ARGV[2],'end_digest',ARGV[6],'end_redis_id',id)
elseif request_protocol == 'v4' and ARGV[5] ~= 'stream_open' and ARGV[5] ~= 'terminal' then
  redis.call('HSET',KEYS[2],'last_event_id',ARGV[2],'last_event_digest',ARGV[6],'last_event_redis_id',id)
end
redis.call('PEXPIRE',KEYS[1],ARGV[4]);redis.call('PEXPIRE',KEYS[2],ARGV[4])
if ARGV[9] ~= 'no_live' then
  redis.call('PUBLISH',KEYS[3],cjson.encode({redis_id=id,envelope=ARGV[3]}))
end
return id
""".strip()

_SCRIPT_CONTRACT_ERRORS = frozenset(
    {
        "stream_end_without_terminal",
        "stream_event_receipt_conflict",
        "stream_open_conflict",
        "stream_protocol_conflict",
        "stream_terminal_closed",
        "stream_terminal_conflict",
    }
)


class StreamTransportUnavailable(RuntimeError):
    pass


async def publish_committed_stream_event(
    bridge: "RedisStreamBridge",
    *,
    authority: "StreamAuthority",
    row: Mapping[str, object],
) -> bool:
    """Append one exact post-commit safe projection to the live stream."""

    projection = committed_public_stream_event(row)
    if projection is None:
        return False
    event_type, payload = projection
    event_id = row.get("id")
    emitted_at = row.get("created_at")
    if (
        authority.state != "confirmed"
        or row.get("run_id") != authority.run_id
        or not isinstance(event_id, str)
        or not event_id
        or not isinstance(emitted_at, str)
        or not emitted_at
    ):
        raise StreamContractError("stream_committed_event_invalid")
    await bridge.append(
        new_envelope(
            event_id=event_id,
            tenant_scope_value=authority.tenant_scope,
            run_id=authority.run_id,
            attempt_id=authority.attempt_id,
            stream_incarnation=authority.stream_incarnation,
            event_type=event_type,
            payload=payload,
            emitted_at=emitted_at,
        )
    )
    return True


@dataclass(frozen=True, slots=True)
class RedisV4CandidateInspection:
    """Narrow same-owner readback for one reserved v4 candidate."""

    stream_exists: bool
    state_exists: bool
    rows: tuple[tuple[object, Mapping[object, object]], ...]
    state: Mapping[object, object]


class RedisStreamBridge:
    def __init__(self, *, publish_client: Any | None = None) -> None:
        settings = get_settings() if publish_client is None else None
        redis_url = str(settings.redis_url) if settings is not None else ""
        common_options = {
            "decode_responses": True,
            "socket_connect_timeout": _REDIS_CONNECT_TIMEOUT_SECONDS,
        }
        self._owns_publish_client = publish_client is None
        self._publish_client = publish_client or Redis.from_url(
            redis_url,
            max_connections=SSE_PUBLISH_MAX_CONNECTIONS,
            socket_timeout=_REDIS_PUBLISH_TIMEOUT_SECONDS,
            **common_options,
        )

    async def aclose(self) -> None:
        if self._owns_publish_client:
            await self._publish_client.aclose()

    async def inspect_v4_candidate(
        self,
        *,
        tenant_scope_value: str,
        run_id: str,
        stream_incarnation: int,
    ) -> RedisV4CandidateInspection:
        """Read one v4 candidate through the bridge's owned Redis client."""

        key = stream_key(
            tenant_scope_value=tenant_scope_value,
            run_id=run_id,
            stream_incarnation=stream_incarnation,
        )
        state_key = f"{key}:state"
        try:
            stream_exists = bool(await self._publish_client.exists(key))
            state_exists = bool(await self._publish_client.exists(state_key))
            rows = tuple(await self._publish_client.xrange(key, min="-", max="+"))
            state = dict(await self._publish_client.hgetall(state_key))
        except Exception as exc:
            raise StreamTransportUnavailable("stream_candidate_inspection_unavailable") from exc
        return RedisV4CandidateInspection(
            stream_exists=stream_exists,
            state_exists=state_exists,
            rows=rows,
            state=state,
        )

    async def append_canonical(
        self,
        *,
        tenant_scope_value: str,
        run_id: str,
        stream_incarnation: int,
        event_id: str,
        event_type: str,
        envelope_bytes: bytes,
        terminal_event_id: str = "",
        protocol: str = "v4",
        publish_live: bool = True,
    ) -> str:
        """Append a validated canonical envelope through the frozen Lua authority."""
        if protocol not in {"v3", "v4"}:
            raise StreamContractError("stream_protocol_invalid")
        if not isinstance(publish_live, bool):
            raise StreamContractError("stream_live_mode_invalid")

        key = stream_key(
            tenant_scope_value=tenant_scope_value,
            run_id=run_id,
            stream_incarnation=stream_incarnation,
        )
        live_channel = stream_live_channel(
            tenant_scope_value=tenant_scope_value,
            run_id=run_id,
            stream_incarnation=stream_incarnation,
        )
        terminal = event_type in {"terminal", "end", "stream.end", "run.succeeded", "run.cancelled", "run.failed"}
        transport_type = {
            "stream.open": "stream_open",
            "stream.end": "end",
        }.get(event_type, "terminal" if terminal else event_type)
        ttl = SSE_STREAM_TERMINAL_TTL_MS if terminal else SSE_STREAM_ACTIVE_IDLE_TTL_MS
        try:
            args: list[object] = [
                _APPEND_WITH_TTL_LUA,
                3,
                key,
                f"{key}:state",
                live_channel,
                SSE_STREAM_MAXLEN,
                event_id,
                envelope_bytes.decode("utf-8"),
                ttl,
                transport_type,
                _sha256(envelope_bytes),
                terminal_event_id,
                protocol,
            ]
            if not publish_live:
                args.append("no_live")
            redis_id = await self._publish_client.eval(*args)
            return redis_id.decode() if isinstance(redis_id, bytes) else str(redis_id)
        except ResponseError as exc:
            reason = next((value for value in _SCRIPT_CONTRACT_ERRORS if value in str(exc)), None)
            if reason is not None:
                raise StreamContractError(reason) from exc
            raise StreamTransportUnavailable("stream_append_unavailable") from exc
        except Exception as exc:
            raise StreamTransportUnavailable("stream_append_unavailable") from exc

    async def append(
        self, envelope: StreamEnvelope, *, terminal: bool = False
    ) -> StreamCursor:
        if terminal != (envelope.event_type in {"terminal", "end"}):
            raise StreamContractError("stream_ttl_class_mismatch")
        ttl = SSE_STREAM_TERMINAL_TTL_MS if terminal else SSE_STREAM_ACTIVE_IDLE_TTL_MS
        key = stream_key(
            tenant_scope_value=envelope.tenant_scope,
            run_id=envelope.run_id,
            stream_incarnation=envelope.stream_incarnation,
        )
        live_channel = stream_live_channel(
            tenant_scope_value=envelope.tenant_scope,
            run_id=envelope.run_id,
            stream_incarnation=envelope.stream_incarnation,
        )
        envelope_bytes = envelope.canonical_bytes
        digest = _sha256(envelope_bytes)
        terminal_event_id = str(envelope.payload.get("terminal_event_id") or "")
        try:
            redis_id = await self._publish_client.eval(
                _APPEND_WITH_TTL_LUA,
                3,
                key,
                f"{key}:state",
                live_channel,
                SSE_STREAM_MAXLEN,
                envelope.event_id,
                envelope_bytes.decode(),
                ttl,
                envelope.event_type,
                digest,
                terminal_event_id,
            )
            redis_id = redis_id.decode() if isinstance(redis_id, bytes) else redis_id
            return StreamCursor(envelope.run_id, envelope.stream_incarnation, redis_id)
        except StreamContractError:
            raise
        except ResponseError as exc:
            reason = next(
                (value for value in _SCRIPT_CONTRACT_ERRORS if value in str(exc)), None
            )
            if reason is not None:
                raise StreamContractError(reason) from exc
            raise StreamTransportUnavailable("stream_append_unavailable") from exc
        except Exception as exc:
            raise StreamTransportUnavailable("stream_append_unavailable") from exc

    async def retained_bounds(
        self, *, tenant_scope_value: str, run_id: str, stream_incarnation: int
    ) -> tuple[StreamEntry, StreamEntry] | None:
        key = stream_key(
            tenant_scope_value=tenant_scope_value,
            run_id=run_id,
            stream_incarnation=stream_incarnation,
        )
        try:
            first = await self._publish_client.xrange(key, min="-", max="+", count=1)
            last = await self._publish_client.xrevrange(key, max="+", min="-", count=1)
        except Exception as exc:
            raise StreamTransportUnavailable("stream_bounds_unavailable") from exc
        if not first and not last:
            return None
        if not first or not last:
            raise StreamTransportUnavailable("stream_bounds_unproven")
        return self._decode(first[0], run_id, stream_incarnation), self._decode(
            last[0], run_id, stream_incarnation
        )

    async def resolve_resume(
        self,
        *,
        tenant_scope_value: str,
        run_id: str,
        current_stream_incarnation: int,
        last_event_id: str | None,
    ) -> ResumeDecision:
        cursor = (
            StreamCursor.parse(last_event_id, run_id=run_id) if last_event_id else None
        )
        if cursor and cursor.stream_incarnation > current_stream_incarnation:
            raise StreamContractError("stream_cursor_future_incarnation")
        if cursor and cursor.stream_incarnation < current_stream_incarnation:
            return ResumeDecision(
                None,
                StreamGap(
                    "stream_incarnation_mismatch",
                    cursor.event_id,
                    cursor.stream_incarnation,
                    current_stream_incarnation,
                ),
            )
        bounds = await self.retained_bounds(
            tenant_scope_value=tenant_scope_value,
            run_id=run_id,
            stream_incarnation=current_stream_incarnation,
        )
        if bounds is None:
            return ResumeDecision(
                None,
                StreamGap(
                    "stream_missing",
                    cursor.event_id if cursor else None,
                    cursor.stream_incarnation if cursor else None,
                    current_stream_incarnation,
                ),
            )
        first, last = bounds
        if cursor is None:
            gap = (
                None
                if first.envelope.event_type == "stream_open"
                else StreamGap(
                    "retained_history_unavailable",
                    None,
                    None,
                    current_stream_incarnation,
                    first.cursor.event_id,
                    last.cursor.event_id,
                )
            )
            return ResumeDecision("0-0" if gap is None else None, gap)
        if _redis_id_tuple(cursor.redis_id) > _redis_id_tuple(last.cursor.redis_id):
            raise StreamContractError("stream_cursor_future_redis_id")
        if _redis_id_tuple(cursor.redis_id) < _redis_id_tuple(first.cursor.redis_id):
            return ResumeDecision(
                None,
                StreamGap(
                    "retained_history_unavailable",
                    cursor.event_id,
                    cursor.stream_incarnation,
                    current_stream_incarnation,
                    first.cursor.event_id,
                    last.cursor.event_id,
                ),
            )
        key = stream_key(
            tenant_scope_value=tenant_scope_value,
            run_id=run_id,
            stream_incarnation=current_stream_incarnation,
        )
        try:
            exact = await self._publish_client.xrange(
                key, min=cursor.redis_id, max=cursor.redis_id, count=1
            )
        except Exception as exc:
            raise StreamTransportUnavailable(
                "stream_cursor_lookup_unavailable"
            ) from exc
        gap = (
            None
            if exact
            else StreamGap(
                "stream_continuity_unproven",
                cursor.event_id,
                cursor.stream_incarnation,
                current_stream_incarnation,
                first.cursor.event_id,
                last.cursor.event_id,
            )
        )
        return ResumeDecision(cursor.redis_id if gap is None else None, gap)

    async def replay_page(
        self,
        *,
        tenant_scope_value: str,
        run_id: str,
        stream_incarnation: int,
        after_redis_id: str,
        through_redis_id: str,
    ) -> tuple[StreamEntry, ...]:
        after = _redis_id_tuple(after_redis_id)
        through = _redis_id_tuple(through_redis_id)
        if after >= through:
            return ()
        key = stream_key(
            tenant_scope_value=tenant_scope_value,
            run_id=run_id,
            stream_incarnation=stream_incarnation,
        )
        try:
            rows = await self._publish_client.xrange(
                key,
                min=f"({after_redis_id}",
                max=through_redis_id,
                count=SSE_STREAM_READ_COUNT,
            )
        except Exception as exc:
            raise StreamTransportUnavailable("stream_replay_unavailable") from exc
        return tuple(
            self._decode(row, run_id, stream_incarnation) for row in rows or ()
        )

    def decode_live_publication(
        self,
        *,
        redis_id: str,
        envelope_json: str,
        run_id: str,
        stream_incarnation: int,
    ) -> StreamEntry:
        return self._decode(
            (redis_id, {"envelope": envelope_json}),
            run_id,
            stream_incarnation,
        )

    @staticmethod
    def _decode(row: object, run_id: str, incarnation: int) -> StreamEntry:
        if (
            not isinstance(row, (tuple, list))
            or len(row) != 2
            or not isinstance(row[1], Mapping)
        ):
            raise StreamContractError("stream_entry_invalid")
        redis_id, fields = row
        redis_id = redis_id.decode() if isinstance(redis_id, bytes) else redis_id
        envelope = StreamEnvelope.from_json(
            fields.get("envelope", fields.get(b"envelope"))
        )
        if envelope.run_id != run_id or envelope.stream_incarnation != incarnation:
            raise StreamContractError("stream_entry_authority_mismatch")
        return StreamEntry(StreamCursor(run_id, incarnation, redis_id), envelope)


class SseAuthorityConflictError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class StreamAuthority:
    tenant_id: str
    run_id: str
    attempt_id: str
    tenant_scope: str
    stream_incarnation: int
    state: str
    open_event_id: str
    open_payload_bytes: str
    open_payload_digest: str
    authorization_epoch: int
    revocation_state: str


@dataclass(frozen=True, slots=True)
class SseAuthorityLease:
    lease_id: str
    tenant_id: str
    run_id: str
    api_instance_id: str
    connection_id: str
    authorization_epoch: int
    lease_not_after: datetime

    def allows_frame(self, *, now: datetime) -> bool:
        """Check the authority-clock deadline of this already authorized lease."""
        now = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
        deadline = (
            self.lease_not_after
            if self.lease_not_after.tzinfo
            else self.lease_not_after.replace(tzinfo=timezone.utc)
        )
        return now < deadline


@dataclass(frozen=True, slots=True)
class TerminalPublicationIntent:
    intent_id: str
    tenant_id: str
    run_id: str
    attempt_id: str
    stream_incarnation: int
    terminal_event_id: str
    end_event_id: str
    terminal_payload_bytes: str
    terminal_payload_digest: str
    end_payload_bytes: str
    end_payload_digest: str
    emitted_at: str
    state: str = "pending"


def _sha256(value: str | bytes) -> str:
    return hashlib.sha256(
        value.encode() if isinstance(value, str) else value
    ).hexdigest()


def _authority(row: Mapping[str, object]) -> StreamAuthority:
    try:
        return StreamAuthority(
            *(
                str(row[key])
                for key in ("tenant_id", "run_id", "attempt_id", "tenant_scope")
            ),
            int(row["stream_incarnation"]),
            str(row["state"]),
            *(
                str(row[key])
                for key in (
                    "open_event_id",
                    "open_payload_bytes",
                    "open_payload_digest",
                )
            ),
            int(row["authorization_epoch"]),
            str(row["revocation_state"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise SseAuthorityConflictError("sse_stream_authority_unavailable") from exc


def _intent(row: Mapping[str, object]) -> TerminalPublicationIntent:
    try:
        return TerminalPublicationIntent(
            str(row["id"]),
            *(str(row[key]) for key in ("tenant_id", "run_id", "attempt_id")),
            int(row["stream_incarnation"]),
            *(
                str(row[key])
                for key in (
                    "terminal_event_id",
                    "end_event_id",
                    "terminal_payload_bytes",
                    "terminal_payload_digest",
                    "end_payload_bytes",
                    "end_payload_digest",
                    "emitted_at",
                    "state",
                )
            ),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise SseAuthorityConflictError("sse_terminal_intent_unavailable") from exc


def _semantic_id(kind: str, *parts: object) -> str:
    return f"sev_{_sha256(canonical_json_bytes([kind, *parts]))}"


def stream_open_event_id(
    *, tenant_scope: str, run_id: str, attempt_id: str, incarnation: int
) -> str:
    return _semantic_id(
        "ai-platform-stream-open-v3", tenant_scope, run_id, attempt_id, incarnation
    )


def terminal_event_ids(
    *, tenant_scope: str, run_id: str, attempt_id: str, incarnation: int
) -> tuple[str, str]:
    base = (tenant_scope, run_id, attempt_id, incarnation)
    return _semantic_id(
        "ai-platform-stream-terminal-v3", *base, "terminal"
    ), _semantic_id("ai-platform-stream-terminal-v3", *base, "end")


async def create_or_get_stream_admission(
    conn: AsyncConnection[dict[str, object]],
    *,
    tenant_id: str,
    run_id: str,
    attempt_id: str,
    tenant_scope: str,
) -> StreamAuthority:
    result = await conn.execute(
        "select * from sse_stream_authorities where tenant_id = %s and run_id = %s for update",
        (tenant_id, run_id),
    )
    row = await result.fetchone()
    if row is not None:
        current = _authority(row)
        if current.attempt_id != attempt_id or current.tenant_scope != tenant_scope:
            raise SseAuthorityConflictError("sse_stream_attempt_conflict")
        return current
    incarnation = 1
    event_id = stream_open_event_id(
        tenant_scope=tenant_scope,
        run_id=run_id,
        attempt_id=attempt_id,
        incarnation=incarnation,
    )
    envelope = StreamEnvelope(
        event_id,
        tenant_scope,
        run_id,
        attempt_id,
        incarnation,
        "stream_open",
        {"design_id": STREAM_DESIGN_ID},
        datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    )
    payload = envelope.canonical_bytes.decode()
    result = await conn.execute(
        """insert into sse_stream_authorities(tenant_id,run_id,attempt_id,design_id,projection_version,tenant_scope,stream_incarnation,state,open_event_id,open_payload_bytes,open_payload_digest) values (%s,%s,%s,%s,%s,%s,%s,'admission_pending',%s,%s,%s) returning *""",
        (
            tenant_id,
            run_id,
            attempt_id,
            STREAM_DESIGN_ID,
            STREAM_PROJECTION_VERSION,
            tenant_scope,
            incarnation,
            event_id,
            payload,
            _sha256(payload),
        ),
    )
    row = await result.fetchone()
    if row is None:
        raise SseAuthorityConflictError("sse_stream_admission_unavailable")
    return _authority(row)


async def create_or_get_stream_admission_v4(
    conn: AsyncConnection[dict[str, object]],
    *,
    tenant_id: str,
    run_id: str,
    attempt_id: str,
    tenant_scope: str,
) -> StreamAuthority:
    """Persist a strict v4 stream.open authority without changing v3 callers."""

    from app.streaming.api import build_v4_control

    result = await conn.execute(
        "select * from sse_stream_authorities where tenant_id = %s and run_id = %s for update",
        (tenant_id, run_id),
    )
    row = await result.fetchone()
    if row is not None:
        current = _authority(row)
        if current.attempt_id != attempt_id or current.tenant_scope != tenant_scope:
            raise SseAuthorityConflictError("sse_stream_attempt_conflict")
        try:
            from app.streaming.api import validate_internal_envelope_v4

            raw = current.open_payload_bytes
            if (
                row.get("design_id") != "ai-platform.redis-streams-sse-event-channel.v4"
                or row.get("projection_version") != "public-stream-v4"
                or not raw
                or _sha256(raw) != current.open_payload_digest
                or raw != canonical_json_bytes(json.loads(raw)).decode()
            ):
                raise ValueError("authority_metadata_mismatch")
            envelope = validate_internal_envelope_v4(json.loads(raw))
            if (
                envelope["event_type"] != "stream.open"
                or envelope["event_id"] != current.open_event_id
                or envelope["tenant_scope"] != current.tenant_scope
                or envelope["run_id"] != current.run_id
                or envelope["attempt_id"] != current.attempt_id
                or envelope["stream_incarnation"] != current.stream_incarnation
                or envelope["projection_version"] != "public-stream-v4"
                or envelope["payload"]
                != {"design_id": "ai-platform.redis-streams-sse-event-channel.v4"}
                or envelope["source"]
                != {"kind": "stream_authority", "authority_id": current.open_event_id}
            ):
                raise ValueError("authority_envelope_mismatch")
        except Exception as exc:
            raise SseAuthorityConflictError("sse_stream_protocol_conflict") from exc
        return current
    incarnation = 1
    event_id = _semantic_id(
        "ai-platform-stream-open-v4", tenant_scope, run_id, attempt_id, incarnation
    )
    envelope = build_v4_control(
        event_id=event_id,
        tenant_scope=tenant_scope,
        run_id=run_id,
        attempt_id=attempt_id,
        stream_incarnation=incarnation,
        event_type="stream.open",
        payload={"design_id": "ai-platform.redis-streams-sse-event-channel.v4"},
        source={"kind": "stream_authority", "authority_id": event_id},
    )
    payload = canonical_json_bytes(envelope).decode()
    result = await conn.execute(
        """insert into sse_stream_authorities(tenant_id,run_id,attempt_id,design_id,projection_version,tenant_scope,stream_incarnation,state,open_event_id,open_payload_bytes,open_payload_digest) values (%s,%s,%s,%s,%s,%s,%s,'admission_pending',%s,%s,%s) returning *""",
        (
            tenant_id,
            run_id,
            attempt_id,
            "ai-platform.redis-streams-sse-event-channel.v4",
            "public-stream-v4",
            tenant_scope,
            incarnation,
            event_id,
            payload,
            _sha256(payload),
        ),
    )
    row = await result.fetchone()
    if row is None:
        raise SseAuthorityConflictError("sse_stream_admission_unavailable")
    return _authority(row)


async def confirm_stream_admission(
    conn: AsyncConnection[dict[str, object]], *, authority: StreamAuthority
) -> StreamAuthority:
    result = await conn.execute(
        """update sse_stream_authorities set state='confirmed',admission_confirmed_at=clock_timestamp(),updated_at=clock_timestamp() where tenant_id=%s and run_id=%s and attempt_id=%s and stream_incarnation=%s and state in ('admission_pending','confirmed') and open_event_id=%s and open_payload_digest=%s returning *""",
        (
            authority.tenant_id,
            authority.run_id,
            authority.attempt_id,
            authority.stream_incarnation,
            authority.open_event_id,
            authority.open_payload_digest,
        ),
    )
    row = await result.fetchone()
    if row is None:
        raise SseAuthorityConflictError("sse_stream_admission_fenced")
    return _authority(row)


async def get_stream_authority(
    conn: AsyncConnection[dict[str, object]],
    *,
    tenant_id: str,
    run_id: str,
    for_update: bool = False,
) -> StreamAuthority | None:
    result = await conn.execute(
        f"select * from sse_stream_authorities where tenant_id=%s and run_id=%s {'for update' if for_update else ''}",
        (tenant_id, run_id),
    )
    row = await result.fetchone()
    return _authority(row) if row is not None else None


async def acquire_sse_authority_lease(
    conn: AsyncConnection[dict[str, object]],
    *,
    tenant_id: str,
    run_id: str,
    api_instance_id: str,
    connection_id: str,
    lease_seconds: int,
) -> SseAuthorityLease:
    if isinstance(lease_seconds, bool) or not 1 <= lease_seconds <= 15:
        raise ValueError("sse_authority_lease_seconds_invalid")
    authority = await get_stream_authority(
        conn, tenant_id=tenant_id, run_id=run_id, for_update=True
    )
    if authority is None or authority.state not in {
        "confirmed",
        "degraded",
        "terminal",
    }:
        raise SseAuthorityConflictError("sse_stream_not_confirmed")
    if authority.revocation_state != "active":
        raise SseAuthorityConflictError("sse_authority_revoked")
    result = await conn.execute(
        """insert into sse_authority_leases(id,tenant_id,run_id,api_instance_id,connection_id,authorization_epoch,lease_not_after) values (%s,%s,%s,%s,%s,%s,clock_timestamp()+(%s*interval '1 second')) on conflict(tenant_id,run_id,api_instance_id,connection_id) do update set id=excluded.id,authorization_epoch=excluded.authorization_epoch,lease_not_after=excluded.lease_not_after,closed_at=null,close_reason=null,updated_at=clock_timestamp() returning *""",
        (
            f"sle_{uuid.uuid4().hex}",
            tenant_id,
            run_id,
            api_instance_id,
            connection_id,
            authority.authorization_epoch,
            lease_seconds,
        ),
    )
    row = await result.fetchone()
    if row is None:
        raise SseAuthorityConflictError("sse_authority_lease_unavailable")
    return SseAuthorityLease(
        str(row["id"]),
        tenant_id,
        run_id,
        api_instance_id,
        connection_id,
        int(row["authorization_epoch"]),
        row["lease_not_after"],
    )


async def close_sse_authority_lease(
    conn: AsyncConnection[dict[str, object]], *, lease_id: str, reason: str
) -> bool:
    result = await conn.execute(
        "update sse_authority_leases set closed_at=coalesce(closed_at,clock_timestamp()),close_reason=%s,updated_at=clock_timestamp() where id=%s returning id",
        (reason, lease_id),
    )
    return await result.fetchone() is not None


async def commit_sse_revocation(
    conn: AsyncConnection[dict[str, object]], *, tenant_id: str, run_id: str
) -> StreamAuthority:
    result = await conn.execute(
        "update sse_stream_authorities set authorization_epoch=authorization_epoch+1,revocation_state='committed',revocation_committed_at=clock_timestamp(),updated_at=clock_timestamp() where tenant_id=%s and run_id=%s and revocation_state='active' returning *",
        (tenant_id, run_id),
    )
    row = await result.fetchone()
    if row is None:
        raise SseAuthorityConflictError("sse_revocation_conflict")
    return _authority(row)


def freeze_terminal_intent(
    *,
    tenant_id: str,
    run_id: str,
    attempt_id: str,
    tenant_scope: str,
    stream_incarnation: int,
    status: str,
) -> TerminalPublicationIntent:
    if status not in {"succeeded", "failed", "cancelled"}:
        raise ValueError("sse_terminal_status_invalid")
    terminal_id, end_id = terminal_event_ids(
        tenant_scope=tenant_scope,
        run_id=run_id,
        attempt_id=attempt_id,
        incarnation=stream_incarnation,
    )
    terminal = canonical_json_bytes(
        {"event_id": terminal_id, "hydrate_required": True, "status": status}
    ).decode()
    end = canonical_json_bytes({"terminal_event_id": terminal_id}).decode()
    return TerminalPublicationIntent(
        f"sti_{uuid.uuid4().hex}",
        tenant_id,
        run_id,
        attempt_id,
        stream_incarnation,
        terminal_id,
        end_id,
        terminal,
        _sha256(terminal),
        end,
        _sha256(end),
        _rfc3339_utc(datetime.now(timezone.utc)),
    )


async def persist_terminal_intent(
    conn: AsyncConnection[dict[str, object]], *, intent: TerminalPublicationIntent
) -> TerminalPublicationIntent:
    result = await conn.execute(
        """insert into sse_terminal_publication_intents(id,tenant_id,run_id,attempt_id,stream_incarnation,schema_version,projection_version,terminal_event_id,end_event_id,terminal_payload_bytes,terminal_payload_digest,terminal_payload_size,end_payload_bytes,end_payload_digest,end_payload_size,emitted_at,state) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'pending') on conflict(tenant_id,run_id,attempt_id) do nothing returning *""",
        (
            intent.intent_id,
            intent.tenant_id,
            intent.run_id,
            intent.attempt_id,
            intent.stream_incarnation,
            STREAM_EVENT_SCHEMA,
            STREAM_PROJECTION_VERSION,
            intent.terminal_event_id,
            intent.end_event_id,
            intent.terminal_payload_bytes,
            intent.terminal_payload_digest,
            len(intent.terminal_payload_bytes.encode()),
            intent.end_payload_bytes,
            intent.end_payload_digest,
            len(intent.end_payload_bytes.encode()),
            intent.emitted_at,
        ),
    )
    row = await result.fetchone()
    if row is None:
        result = await conn.execute(
            "select * from sse_terminal_publication_intents where tenant_id=%s and run_id=%s and attempt_id=%s for update",
            (intent.tenant_id, intent.run_id, intent.attempt_id),
        )
        row = await result.fetchone()
        existing = _intent(row) if row is not None else None
        expected = (
            intent.stream_incarnation,
            intent.terminal_event_id,
            intent.end_event_id,
            intent.terminal_payload_digest,
            intent.end_payload_digest,
        )
        actual = (
            (
                existing.stream_incarnation,
                existing.terminal_event_id,
                existing.end_event_id,
                existing.terminal_payload_digest,
                existing.end_payload_digest,
            )
            if existing
            else None
        )
        if actual != expected:
            raise SseAuthorityConflictError("sse_terminal_intent_conflict")
        return existing
    return _intent(row)


async def ensure_run_terminal_intent(
    conn: AsyncConnection[dict[str, object]],
    *,
    tenant_id: str,
    run_id: str,
    status: str,
) -> TerminalPublicationIntent | None:
    authority = await get_stream_authority(
        conn, tenant_id=tenant_id, run_id=run_id, for_update=True
    )
    if authority is None:
        return None
    intent = await persist_terminal_intent(
        conn,
        intent=freeze_terminal_intent(
            tenant_id=tenant_id,
            run_id=run_id,
            attempt_id=authority.attempt_id,
            tenant_scope=authority.tenant_scope,
            stream_incarnation=authority.stream_incarnation,
            status=status,
        ),
    )
    await conn.execute(
        "update sse_stream_authorities set state='terminal',updated_at=clock_timestamp() where tenant_id=%s and run_id=%s and attempt_id=%s",
        (tenant_id, run_id, authority.attempt_id),
    )
    return intent


async def get_terminal_intent(
    conn: AsyncConnection[dict[str, object]], *, tenant_id: str, run_id: str
) -> TerminalPublicationIntent | None:
    result = await conn.execute(
        "select * from sse_terminal_publication_intents where tenant_id=%s and run_id=%s",
        (tenant_id, run_id),
    )
    row = await result.fetchone()
    return _intent(row) if row is not None else None


async def mark_terminal_intent_published(
    conn: AsyncConnection[dict[str, object]], *, intent: TerminalPublicationIntent
) -> TerminalPublicationIntent:
    result = await conn.execute(
        "update sse_terminal_publication_intents set state='published',published_at=coalesce(published_at,clock_timestamp()),updated_at=clock_timestamp() where id=%s and terminal_payload_digest=%s and end_payload_digest=%s and state in ('pending','published') returning *",
        (intent.intent_id, intent.terminal_payload_digest, intent.end_payload_digest),
    )
    row = await result.fetchone()
    if row is None:
        raise SseAuthorityConflictError("sse_terminal_intent_fenced")
    return _intent(row)


def _frozen_payload(value: str, *, digest: str, error: str) -> dict[str, object]:
    if not hmac.compare_digest(_sha256(value), digest):
        raise StreamContractError(error)
    try:
        payload = json.loads(value)
    except (TypeError, ValueError) as exc:
        raise StreamContractError(error) from exc
    if not isinstance(payload, dict):
        raise StreamContractError(error)
    return payload


async def publish_terminal_intent(
    bridge: RedisStreamBridge,
    *,
    authority: StreamAuthority,
    intent: TerminalPublicationIntent,
) -> tuple[StreamCursor, StreamCursor]:
    """Publish one committed, frozen terminal intent without holding a PG transaction."""

    if (
        authority.tenant_id != intent.tenant_id
        or authority.run_id != intent.run_id
        or authority.attempt_id != intent.attempt_id
        or authority.stream_incarnation != intent.stream_incarnation
        or authority.state != "terminal"
    ):
        raise StreamContractError("stream_terminal_authority_mismatch")
    terminal_payload = _frozen_payload(
        intent.terminal_payload_bytes,
        digest=intent.terminal_payload_digest,
        error="stream_terminal_payload_digest_mismatch",
    )
    end_payload = _frozen_payload(
        intent.end_payload_bytes,
        digest=intent.end_payload_digest,
        error="stream_end_payload_digest_mismatch",
    )
    terminal_cursor = await bridge.append(
        new_envelope(
            event_id=intent.terminal_event_id,
            tenant_scope_value=authority.tenant_scope,
            run_id=intent.run_id,
            attempt_id=intent.attempt_id,
            stream_incarnation=intent.stream_incarnation,
            event_type="terminal",
            payload=terminal_payload,
            emitted_at=intent.emitted_at,
        ),
        terminal=True,
    )
    end_cursor = await bridge.append(
        new_envelope(
            event_id=intent.end_event_id,
            tenant_scope_value=authority.tenant_scope,
            run_id=intent.run_id,
            attempt_id=intent.attempt_id,
            stream_incarnation=intent.stream_incarnation,
            event_type="end",
            payload=end_payload,
            emitted_at=intent.emitted_at,
        ),
        terminal=True,
    )
    return terminal_cursor, end_cursor


CHAT_ASSISTANT_DELTA_SOURCE = "worker_answer_delta_v1"
_ASSISTANT_DELTA_INPUT_STAGES = frozenset({"message", "assistant"})


def canonical_assistant_delta_event(
    *, stage: str, payload: dict[str, Any] | None
) -> tuple[str, str, dict[str, Any]] | None:
    if stage not in _ASSISTANT_DELTA_INPUT_STAGES or not isinstance(payload, dict):
        return None
    delta = payload.get("delta")
    if not isinstance(delta, str) or not delta:
        return None
    return (
        "answer",
        "",
        {
            "delta": delta,
            "source": CHAT_ASSISTANT_DELTA_SOURCE,
            "visible_to_user": True,
            "severity": "info",
        },
    )


@dataclass(slots=True)
class RunStreamPublisher:
    tenant_id: str
    run_id: str
    attempt_id: str
    authority_secret: str
    bridge: RedisStreamBridge = field(default_factory=RedisStreamBridge)
    authority: StreamAuthority | None = None

    async def prepare(self, conn: Any) -> None:
        self.authority = await create_or_get_stream_admission(
            conn,
            tenant_id=self.tenant_id,
            run_id=self.run_id,
            attempt_id=self.attempt_id,
            tenant_scope=tenant_scope(self.tenant_id, secret=self.authority_secret),
        )

    async def open(self) -> None:
        authority = self._authority()
        await self.bridge.append(StreamEnvelope.from_json(authority.open_payload_bytes))

    async def confirm(self, conn: Any) -> None:
        self.authority = await confirm_stream_admission(
            conn, authority=self._authority()
        )

    async def refresh(self, conn: Any) -> None:
        authority = await get_stream_authority(
            conn,
            tenant_id=self.tenant_id,
            run_id=self.run_id,
        )
        if (
            authority is None
            or authority.attempt_id != self.attempt_id
            or authority.state != "confirmed"
        ):
            raise StreamContractError("sse_stream_attempt_inactive")
        self.authority = authority

    async def publish_committed_event(self, row: Mapping[str, object]) -> bool:
        return await publish_committed_stream_event(
            self.bridge,
            authority=self._authority(),
            row=row,
        )

    async def aclose(self) -> None:
        await self.bridge.aclose()

    def _authority(self) -> StreamAuthority:
        if self.authority is None:
            raise RuntimeError("sse_stream_admission_missing")
        return self.authority
