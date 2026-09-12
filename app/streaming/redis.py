"""Bounded Redis Streams transport for public SSE frames."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from psycopg import AsyncConnection
from redis.asyncio import Redis
from redis.exceptions import ResponseError

from app.settings import get_settings
from app.streaming.api import (
    StreamContractError,
    canonical_json_bytes,
    stream_key,
    tenant_scope as tenant_scope,
)

SSE_PUBLISH_MAX_CONNECTIONS = 16
SSE_READ_MAX_CONNECTIONS = 256
SSE_STREAM_MAXLEN = 10000
SSE_STREAM_ACTIVE_IDLE_TTL_MS = 7200000
SSE_STREAM_TERMINAL_TTL_MS = 7200000
SSE_AUTHORITY_LEASE_SECONDS = 15
_REDIS_CONNECT_TIMEOUT_SECONDS = 2
_REDIS_PUBLISH_TIMEOUT_SECONDS = 5

_APPEND_WITH_TTL_LUA = """
local phase=redis.call('HGET',KEYS[2],'phase')
local request_protocol=ARGV[8]
if request_protocol ~= 'v4' then return redis.error_reply('stream_protocol_invalid') end
if phase then
  local stored_protocol=redis.call('HGET',KEYS[2],'open_protocol')
  if stored_protocol ~= request_protocol then
    return redis.error_reply('stream_protocol_conflict')
  end
  if redis.call('XLEN',KEYS[1]) == 0 then return redis.error_reply('stream_missing') end
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
  if not phase and redis.call('XLEN',KEYS[1]) == 0 then return redis.error_reply('stream_missing') end
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
  if redis.call('HGET',KEYS[2],'last_event_id') == ARGV[2] then
    if redis.call('HGET',KEYS[2],'last_event_digest') ~= ARGV[6] then
      return redis.error_reply('stream_event_receipt_conflict')
    end
    redis.call('PEXPIRE',KEYS[1],ARGV[4]);redis.call('PEXPIRE',KEYS[2],ARGV[4])
    return redis.call('HGET',KEYS[2],'last_event_redis_id')
  end
end
if ARGV[5] == 'terminal' or ARGV[5] == 'run.cancel_requested' then
  local source=cjson.decode(ARGV[3]).source
  if tonumber(redis.call('HGET',KEYS[2],'callback_sequence') or '0') < (source.callback_sequence or 0) then
    return redis.error_reply('stream_callback_pending')
  end
end
local id=redis.call('XADD',KEYS[1],'MAXLEN','~',ARGV[1],'*','envelope',ARGV[3])
if ARGV[5] == 'stream_open' then
  redis.call('HSET',KEYS[2],'phase','open','open_event_id',ARGV[2],'open_digest',ARGV[6],'open_redis_id',id,'open_protocol',request_protocol)
end
if ARGV[5] == 'terminal' then
  redis.call('HSET',KEYS[2],'phase','terminal','terminal_event_id',ARGV[2],'terminal_digest',ARGV[6],'terminal_redis_id',id)
end
if ARGV[5] == 'end' then
  redis.call('HSET',KEYS[2],'phase','ended','end_event_id',ARGV[2],'end_digest',ARGV[6],'end_redis_id',id)
elseif ARGV[5] ~= 'stream_open' and ARGV[5] ~= 'terminal' then
  redis.call('HSET',KEYS[2],'last_event_id',ARGV[2],'last_event_digest',ARGV[6],'last_event_redis_id',id)
end
redis.call('PEXPIRE',KEYS[1],ARGV[4]);redis.call('PEXPIRE',KEYS[2],ARGV[4])
return id
""".strip()

_SCRIPT_CONTRACT_ERRORS = frozenset(
    {
        "stream_end_without_terminal",
        "stream_event_receipt_conflict",
        "stream_missing",
        "stream_open_conflict",
        "stream_protocol_conflict",
        "stream_protocol_invalid",
        "stream_terminal_closed",
        "stream_terminal_conflict",
    }
)


class StreamTransportUnavailable(RuntimeError):
    pass


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
        self._read_client = publish_client if publish_client is not None else Redis.from_url(
            redis_url,
            max_connections=SSE_READ_MAX_CONNECTIONS,
            socket_timeout=10,
            **common_options,
        )

    async def aclose(self) -> None:
        if self._owns_publish_client:
            try:
                await self._read_client.aclose()
            finally:
                await self._publish_client.aclose()


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
    ) -> str:
        """Append a validated v4 envelope through the shared Lua operation."""

        key = stream_key(
            tenant_scope_value=tenant_scope_value,
            run_id=run_id,
            stream_incarnation=stream_incarnation,
        )
        terminal = event_type in {"stream.end", "run.succeeded", "run.cancelled", "run.failed"}
        transport_type = {
            "stream.open": "stream_open",
            "stream.end": "end",
        }.get(event_type, "terminal" if terminal else event_type)
        ttl = SSE_STREAM_TERMINAL_TTL_MS if terminal else SSE_STREAM_ACTIVE_IDLE_TTL_MS
        try:
            args: list[object] = [
                _APPEND_WITH_TTL_LUA,
                2,
                key,
                f"{key}:state",
                SSE_STREAM_MAXLEN,
                event_id,
                envelope_bytes.decode("utf-8"),
                ttl,
                transport_type,
                _sha256(envelope_bytes),
                terminal_event_id,
                "v4",
            ]
            redis_id = await self._publish_client.eval(*args)
            return redis_id.decode() if isinstance(redis_id, bytes) else str(redis_id)
        except ResponseError as exc:
            reason = next((value for value in _SCRIPT_CONTRACT_ERRORS if value in str(exc)), None)
            if reason is not None:
                raise StreamContractError(reason) from exc
            raise StreamTransportUnavailable("stream_append_unavailable") from exc
        except Exception as exc:
            raise StreamTransportUnavailable("stream_append_unavailable") from exc


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


def _semantic_id(kind: str, *parts: object) -> str:
    return f"sev_{_sha256(canonical_json_bytes([kind, *parts]))}"


async def create_or_get_stream_admission_v4(
    conn: AsyncConnection[dict[str, object]],
    *,
    tenant_id: str,
    run_id: str,
    attempt_id: str,
    tenant_scope: str,
) -> StreamAuthority:
    """Persist and revalidate the canonical stream.open admission receipt."""

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
        """update sse_stream_authorities
           set state = case
                 when state = 'terminal' then 'terminal'
                 else 'confirmed'
               end,
               admission_confirmed_at=clock_timestamp(),
               updated_at=clock_timestamp()
           where tenant_id=%s and run_id=%s and attempt_id=%s
             and stream_incarnation=%s
             and state in ('admission_pending','confirmed','terminal')
             and open_event_id=%s and open_payload_digest=%s
           returning *""",
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


CHAT_ASSISTANT_DELTA_SOURCE = "worker_answer_delta_v1"
_ASSISTANT_DELTA_INPUT_STAGES = frozenset({"message", "assistant"})
