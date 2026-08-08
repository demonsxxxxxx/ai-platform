"""Bounded Redis Streams transport for public SSE frames."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import uuid
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

from psycopg import AsyncConnection
from redis.asyncio import Redis

from app.settings import get_settings

STREAM_EVENT_SCHEMA = "ai-platform.stream-event.v2.1"
STREAM_GAP_SCHEMA = "ai-platform.stream-gap.v2.1"
STREAM_PROJECTION_VERSION = "public-stream-v2.1"
STREAM_DESIGN_ID = "ai-platform.redis-streams-sse-event-channel.v2.1"
STREAM_KEY_PREFIX = "ai-platform:sse:v2.1"
SSE_PUBLISH_MAX_CONNECTIONS = 16
SSE_BLOCKING_MAX_CONNECTIONS = 128
SSE_STREAM_MAXLEN = 10000
SSE_STREAM_ACTIVE_IDLE_TTL_MS = 7200000
SSE_STREAM_TERMINAL_TTL_MS = 7200000
SSE_STREAM_READ_COUNT = 128
SSE_STREAM_BLOCK_MS = 15000
SSE_AUTHORITY_LEASE_SECONDS = 15
PUBLIC_EVENT_TYPES = frozenset({"stream_open", "assistant_text_delta", "semantic_stage", "semantic_progress", "tool_lifecycle", "approval_required", "artifact_ready", "run_status", "terminal", "end"})
_RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
_REDIS_ID_RE = re.compile(r"^(0|[1-9][0-9]*)-(0|[1-9][0-9]*)$")
_FORBIDDEN = frozenset({"authorization", "callback_token", "callback_token_id", "command", "credentials", "cwd", "hidden_reasoning", "local_path", "prompt", "raw_payload", "reasoning", "storage_key", "tool_arguments", "tool_result"})
_APPEND_WITH_TTL_LUA = """
local prior=redis.call('HGET',KEYS[2],ARGV[2])
if prior then redis.call('PEXPIRE',KEYS[1],ARGV[4]);redis.call('PEXPIRE',KEYS[2],ARGV[4]);return prior end
local id=redis.call('XADD',KEYS[1],'MAXLEN','~',ARGV[1],'*','envelope',ARGV[3])
redis.call('HSET',KEYS[2],ARGV[2],id);redis.call('PEXPIRE',KEYS[1],ARGV[4]);redis.call('PEXPIRE',KEYS[2],ARGV[4]);return id
""".strip()


class StreamContractError(ValueError):
    pass


class StreamTransportUnavailable(RuntimeError):
    pass


class StreamProjectionError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class StreamCursor:
    run_id: str
    stream_incarnation: int
    redis_id: str

    def __post_init__(self) -> None:
        if not _RUN_ID_RE.fullmatch(self.run_id) or isinstance(self.stream_incarnation, bool) or self.stream_incarnation < 1 or not _REDIS_ID_RE.fullmatch(self.redis_id):
            raise StreamContractError("stream_cursor_invalid")

    @property
    def event_id(self) -> str:
        return f"{self.run_id}:{self.stream_incarnation}:{self.redis_id}"

    @classmethod
    def parse(cls, value: str, *, run_id: str) -> StreamCursor:
        if not isinstance(value, str) or not value or value != value.strip():
            raise StreamContractError("stream_cursor_invalid")
        prefix, separator, redis_id = value.rpartition(":")
        parsed_run, separator2, incarnation = prefix.rpartition(":")
        if separator != ":" or separator2 != ":" or parsed_run != run_id:
            raise StreamContractError("stream_cursor_foreign_run")
        if not incarnation.isdecimal() or incarnation.startswith("0"):
            raise StreamContractError("stream_cursor_incarnation_invalid")
        return cls(run_id, int(incarnation), redis_id)


@dataclass(frozen=True, slots=True)
class StreamEnvelope:
    event_id: str
    tenant_scope: str
    run_id: str
    attempt_id: str
    stream_incarnation: int
    event_type: str
    payload: Mapping[str, object]
    emitted_at: str
    schema: str = STREAM_EVENT_SCHEMA
    projection_version: str = STREAM_PROJECTION_VERSION

    def __post_init__(self) -> None:
        if not all(isinstance(value, str) and value and len(value) <= 256 for value in (self.event_id, self.tenant_scope, self.attempt_id)) or not _RUN_ID_RE.fullmatch(self.run_id) or isinstance(self.stream_incarnation, bool) or self.stream_incarnation < 1 or self.schema != STREAM_EVENT_SCHEMA or self.projection_version != STREAM_PROJECTION_VERSION:
            raise StreamContractError("stream_envelope_invalid")
        validate_public_payload(self.event_type, self.payload)

    def as_dict(self) -> dict[str, object]:
        value = asdict(self)
        value["payload"] = dict(self.payload)
        return value

    @property
    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.as_dict())

    @classmethod
    def from_json(cls, value: str | bytes) -> StreamEnvelope:
        try:
            raw = json.loads(value)
            if not isinstance(raw, dict) or not isinstance(raw.get("payload"), dict):
                raise TypeError
            return cls(**raw)
        except (TypeError, ValueError, UnicodeDecodeError) as exc:
            raise StreamContractError("stream_envelope_json_invalid") from exc


@dataclass(frozen=True, slots=True)
class StreamEntry:
    cursor: StreamCursor
    envelope: StreamEnvelope


@dataclass(frozen=True, slots=True)
class StreamGap:
    reason: Literal["retained_history_unavailable", "stream_missing", "stream_continuity_unproven", "stream_incarnation_mismatch"]
    requested_event_id: str | None
    requested_stream_incarnation: int | None
    current_stream_incarnation: int
    earliest_available_event_id: str | None = None
    latest_available_event_id: str | None = None

    def as_public_dict(self) -> dict[str, object]:
        result = {"schema": STREAM_GAP_SCHEMA, "reason": self.reason, "current_stream_incarnation": self.current_stream_incarnation, "recovery": "reload_durable_state"}
        result.update({key: value for key, value in asdict(self).items() if key != "reason" and key != "current_stream_incarnation" and value is not None})
        return result


@dataclass(frozen=True, slots=True)
class ResumeDecision:
    after_redis_id: str | None
    gap: StreamGap | None


def canonical_json_bytes(value: object) -> bytes:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise StreamContractError("stream_json_not_canonicalizable") from exc


def tenant_scope(tenant_id: str, *, secret: str) -> str:
    if not tenant_id or not secret:
        raise StreamContractError("stream_tenant_scope_authority_missing")
    return hmac.new(secret.encode(), tenant_id.encode(), hashlib.sha256).hexdigest()[:32]


def stream_key(*, tenant_scope_value: str, run_id: str, stream_incarnation: int) -> str:
    if not tenant_scope_value or not _RUN_ID_RE.fullmatch(run_id) or stream_incarnation < 1:
        raise StreamContractError("stream_key_invalid")
    return f"{STREAM_KEY_PREFIX}:{{{tenant_scope_value}:{run_id}}}:{stream_incarnation}:events"


def stable_event_id(*, tenant_scope_value: str, run_id: str, attempt_id: str, batch_id: str, item_index: int, projection_version: str = STREAM_PROJECTION_VERSION) -> str:
    if isinstance(item_index, bool) or not isinstance(item_index, int) or item_index < 0:
        raise StreamContractError("stream_event_item_index_invalid")
    return f"sev_{hashlib.sha256(canonical_json_bytes(['ai-platform-stream-event-id-v2.1', tenant_scope_value, run_id, attempt_id, batch_id, item_index, projection_version])).hexdigest()}"


def new_envelope(*, event_id: str, tenant_scope_value: str, run_id: str, attempt_id: str, stream_incarnation: int, event_type: str, payload: Mapping[str, object]) -> StreamEnvelope:
    return StreamEnvelope(event_id, tenant_scope_value, run_id, attempt_id, stream_incarnation, event_type, payload, datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"))


def _validate_value(value: object, depth: int = 0) -> None:
    if depth > 6:
        raise StreamProjectionError("stream_payload_depth_exceeded")
    if value is None or isinstance(value, (bool, int, float)):
        return
    if isinstance(value, str):
        if len(value.encode()) > 8192:
            raise StreamProjectionError("stream_payload_string_too_large")
        return
    if isinstance(value, list) and len(value) <= 64:
        for item in value:
            _validate_value(item, depth + 1)
        return
    if isinstance(value, Mapping) and len(value) <= 64:
        for key, item in value.items():
            if str(key).strip().lower() in _FORBIDDEN:
                raise StreamProjectionError("stream_payload_forbidden_key")
            _validate_value(item, depth + 1)
        return
    raise StreamProjectionError("stream_payload_value_invalid")


def validate_public_payload(event_type: str, payload: Mapping[str, object]) -> None:
    if event_type not in PUBLIC_EVENT_TYPES:
        raise StreamProjectionError("stream_event_type_not_public")
    if not isinstance(payload, Mapping) or event_type == "assistant_text_delta" and (set(payload) != {"delta"} or not isinstance(payload.get("delta"), str) or not payload["delta"]):
        raise StreamProjectionError("stream_payload_invalid")
    _validate_value(payload)
    if len(canonical_json_bytes(dict(payload))) > 16384:
        raise StreamProjectionError("stream_payload_too_large")


def _redis_id_tuple(value: str) -> tuple[int, int]:
    if not _REDIS_ID_RE.fullmatch(value):
        raise StreamContractError("stream_redis_id_invalid")
    return tuple(map(int, value.split("-")))  # type: ignore[return-value]


class RedisStreamBridge:
    def __init__(self, *, publish_client: Any | None = None, blocking_client: Any | None = None) -> None:
        settings = get_settings() if publish_client is None or blocking_client is None else None
        redis_url = str(settings.redis_url) if settings is not None else ""
        options = {"decode_responses": True, "socket_connect_timeout": 2, "socket_timeout": 5}
        self._publish_client = publish_client or Redis.from_url(redis_url, max_connections=SSE_PUBLISH_MAX_CONNECTIONS, **options)
        self._blocking_client = blocking_client or Redis.from_url(redis_url, max_connections=SSE_BLOCKING_MAX_CONNECTIONS, **options)

    async def aclose(self) -> None:
        await self._publish_client.aclose()
        await self._blocking_client.aclose()

    async def append(self, envelope: StreamEnvelope, *, terminal: bool = False) -> StreamCursor:
        ttl = SSE_STREAM_TERMINAL_TTL_MS if terminal else SSE_STREAM_ACTIVE_IDLE_TTL_MS
        key = stream_key(tenant_scope_value=envelope.tenant_scope, run_id=envelope.run_id, stream_incarnation=envelope.stream_incarnation)
        try:
            redis_id = await self._publish_client.eval(_APPEND_WITH_TTL_LUA, 2, key, f"{key}:event-ids", SSE_STREAM_MAXLEN, envelope.event_id, envelope.canonical_bytes.decode(), ttl)
            redis_id = redis_id.decode() if isinstance(redis_id, bytes) else redis_id
            return StreamCursor(envelope.run_id, envelope.stream_incarnation, redis_id)
        except StreamContractError:
            raise
        except Exception as exc:
            raise StreamTransportUnavailable("stream_append_unavailable") from exc

    async def retained_bounds(self, *, tenant_scope_value: str, run_id: str, stream_incarnation: int) -> tuple[StreamEntry, StreamEntry] | None:
        key = stream_key(tenant_scope_value=tenant_scope_value, run_id=run_id, stream_incarnation=stream_incarnation)
        try:
            first = await self._publish_client.xrange(key, min="-", max="+", count=1)
            last = await self._publish_client.xrevrange(key, max="+", min="-", count=1)
        except Exception as exc:
            raise StreamTransportUnavailable("stream_bounds_unavailable") from exc
        if not first and not last:
            return None
        if not first or not last:
            raise StreamTransportUnavailable("stream_bounds_unproven")
        return self._decode(first[0], run_id, stream_incarnation), self._decode(last[0], run_id, stream_incarnation)

    async def resolve_resume(self, *, tenant_scope_value: str, run_id: str, current_stream_incarnation: int, last_event_id: str | None) -> ResumeDecision:
        cursor = StreamCursor.parse(last_event_id, run_id=run_id) if last_event_id else None
        if cursor and cursor.stream_incarnation > current_stream_incarnation:
            raise StreamContractError("stream_cursor_future_incarnation")
        if cursor and cursor.stream_incarnation < current_stream_incarnation:
            return ResumeDecision(None, StreamGap("stream_incarnation_mismatch", cursor.event_id, cursor.stream_incarnation, current_stream_incarnation))
        bounds = await self.retained_bounds(tenant_scope_value=tenant_scope_value, run_id=run_id, stream_incarnation=current_stream_incarnation)
        if bounds is None:
            return ResumeDecision(None, StreamGap("stream_missing", cursor.event_id if cursor else None, cursor.stream_incarnation if cursor else None, current_stream_incarnation))
        first, last = bounds
        if cursor is None:
            gap = None if first.envelope.event_type == "stream_open" else StreamGap("retained_history_unavailable", None, None, current_stream_incarnation, first.cursor.event_id, last.cursor.event_id)
            return ResumeDecision("0-0" if gap is None else None, gap)
        if _redis_id_tuple(cursor.redis_id) > _redis_id_tuple(last.cursor.redis_id):
            raise StreamContractError("stream_cursor_future_redis_id")
        if _redis_id_tuple(cursor.redis_id) < _redis_id_tuple(first.cursor.redis_id):
            return ResumeDecision(None, StreamGap("retained_history_unavailable", cursor.event_id, cursor.stream_incarnation, current_stream_incarnation, first.cursor.event_id, last.cursor.event_id))
        key = stream_key(tenant_scope_value=tenant_scope_value, run_id=run_id, stream_incarnation=current_stream_incarnation)
        try:
            exact = await self._publish_client.xrange(key, min=cursor.redis_id, max=cursor.redis_id, count=1)
        except Exception as exc:
            raise StreamTransportUnavailable("stream_cursor_lookup_unavailable") from exc
        gap = None if exact else StreamGap("stream_continuity_unproven", cursor.event_id, cursor.stream_incarnation, current_stream_incarnation, first.cursor.event_id, last.cursor.event_id)
        return ResumeDecision(cursor.redis_id if gap is None else None, gap)

    async def read(self, *, tenant_scope_value: str, run_id: str, stream_incarnation: int, after_redis_id: str, block_ms: int | None = None) -> tuple[StreamEntry, ...]:
        _redis_id_tuple(after_redis_id)
        key = stream_key(tenant_scope_value=tenant_scope_value, run_id=run_id, stream_incarnation=stream_incarnation)
        block = min(SSE_STREAM_BLOCK_MS, SSE_AUTHORITY_LEASE_SECONDS * 1000, int(block_ms or SSE_STREAM_BLOCK_MS))
        try:
            result = await self._blocking_client.xread({key: after_redis_id}, count=SSE_STREAM_READ_COUNT, block=max(block, 1))
        except Exception as exc:
            raise StreamTransportUnavailable("stream_read_unavailable") from exc
        entries = []
        for returned_key, rows in result or ():
            if (returned_key.decode() if isinstance(returned_key, bytes) else returned_key) != key:
                raise StreamContractError("stream_read_foreign_key")
            entries.extend(self._decode(row, run_id, stream_incarnation) for row in rows)
        return tuple(entries)

    @staticmethod
    def _decode(row: object, run_id: str, incarnation: int) -> StreamEntry:
        if not isinstance(row, (tuple, list)) or len(row) != 2 or not isinstance(row[1], Mapping):
            raise StreamContractError("stream_entry_invalid")
        redis_id, fields = row
        redis_id = redis_id.decode() if isinstance(redis_id, bytes) else redis_id
        envelope = StreamEnvelope.from_json(fields.get("envelope", fields.get(b"envelope")))
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

    def allows_frame(self, *, now: datetime, local_authorization_epoch: int, invalidated_through_epoch: int) -> bool:
        now = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
        deadline = self.lease_not_after if self.lease_not_after.tzinfo else self.lease_not_after.replace(tzinfo=timezone.utc)
        return self.authorization_epoch == local_authorization_epoch and self.authorization_epoch > invalidated_through_epoch and now < deadline


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
    state: str = "pending"


def _sha256(value: str | bytes) -> str:
    return hashlib.sha256(value.encode() if isinstance(value, str) else value).hexdigest()


def _authority(row: Mapping[str, object]) -> StreamAuthority:
    try:
        return StreamAuthority(*(str(row[key]) for key in ("tenant_id", "run_id", "attempt_id", "tenant_scope")), int(row["stream_incarnation"]), str(row["state"]), *(str(row[key]) for key in ("open_event_id", "open_payload_bytes", "open_payload_digest")), int(row["authorization_epoch"]), str(row["revocation_state"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise SseAuthorityConflictError("sse_stream_authority_unavailable") from exc


def _intent(row: Mapping[str, object]) -> TerminalPublicationIntent:
    try:
        return TerminalPublicationIntent(str(row["id"]), *(str(row[key]) for key in ("tenant_id", "run_id", "attempt_id")), int(row["stream_incarnation"]), *(str(row[key]) for key in ("terminal_event_id", "end_event_id", "terminal_payload_bytes", "terminal_payload_digest", "end_payload_bytes", "end_payload_digest", "state")))
    except (KeyError, TypeError, ValueError) as exc:
        raise SseAuthorityConflictError("sse_terminal_intent_unavailable") from exc


def _semantic_id(kind: str, *parts: object) -> str:
    return f"sev_{_sha256(canonical_json_bytes([kind, *parts]))}"


def stream_open_event_id(*, tenant_scope: str, run_id: str, attempt_id: str, incarnation: int) -> str:
    return _semantic_id("ai-platform-stream-open-v2.1", tenant_scope, run_id, attempt_id, incarnation)


def terminal_event_ids(*, tenant_scope: str, run_id: str, attempt_id: str, incarnation: int) -> tuple[str, str]:
    base = (tenant_scope, run_id, attempt_id, incarnation)
    return _semantic_id("ai-platform-stream-terminal-v2.1", *base, "terminal"), _semantic_id("ai-platform-stream-terminal-v2.1", *base, "end")


async def create_or_get_stream_admission(conn: AsyncConnection[dict[str, object]], *, tenant_id: str, run_id: str, attempt_id: str, tenant_scope: str) -> StreamAuthority:
    result = await conn.execute("select * from sse_stream_authorities where tenant_id = %s and run_id = %s for update", (tenant_id, run_id))
    row = await result.fetchone()
    if row is not None:
        current = _authority(row)
        if current.attempt_id != attempt_id or current.tenant_scope != tenant_scope:
            raise SseAuthorityConflictError("sse_stream_attempt_conflict")
        return current
    incarnation = 1
    event_id = stream_open_event_id(tenant_scope=tenant_scope, run_id=run_id, attempt_id=attempt_id, incarnation=incarnation)
    envelope = StreamEnvelope(event_id, tenant_scope, run_id, attempt_id, incarnation, "stream_open", {"design_id": STREAM_DESIGN_ID}, datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"))
    payload = envelope.canonical_bytes.decode()
    result = await conn.execute("""insert into sse_stream_authorities(tenant_id,run_id,attempt_id,design_id,projection_version,tenant_scope,stream_incarnation,state,open_event_id,open_payload_bytes,open_payload_digest) values (%s,%s,%s,%s,%s,%s,%s,'admission_pending',%s,%s,%s) returning *""", (tenant_id, run_id, attempt_id, STREAM_DESIGN_ID, STREAM_PROJECTION_VERSION, tenant_scope, incarnation, event_id, payload, _sha256(payload)))
    row = await result.fetchone()
    if row is None:
        raise SseAuthorityConflictError("sse_stream_admission_unavailable")
    return _authority(row)


async def confirm_stream_admission(conn: AsyncConnection[dict[str, object]], *, authority: StreamAuthority) -> StreamAuthority:
    result = await conn.execute("""update sse_stream_authorities set state='confirmed',admission_confirmed_at=clock_timestamp(),updated_at=clock_timestamp() where tenant_id=%s and run_id=%s and attempt_id=%s and stream_incarnation=%s and state in ('admission_pending','confirmed') and open_event_id=%s and open_payload_digest=%s returning *""", (authority.tenant_id, authority.run_id, authority.attempt_id, authority.stream_incarnation, authority.open_event_id, authority.open_payload_digest))
    row = await result.fetchone()
    if row is None:
        raise SseAuthorityConflictError("sse_stream_admission_fenced")
    return _authority(row)


async def get_stream_authority(conn: AsyncConnection[dict[str, object]], *, tenant_id: str, run_id: str, for_update: bool = False) -> StreamAuthority | None:
    result = await conn.execute(f"select * from sse_stream_authorities where tenant_id=%s and run_id=%s {'for update' if for_update else ''}", (tenant_id, run_id))
    row = await result.fetchone()
    return _authority(row) if row is not None else None


async def acquire_sse_authority_lease(conn: AsyncConnection[dict[str, object]], *, tenant_id: str, run_id: str, api_instance_id: str, connection_id: str, lease_seconds: int) -> SseAuthorityLease:
    if isinstance(lease_seconds, bool) or not 1 <= lease_seconds <= 15:
        raise ValueError("sse_authority_lease_seconds_invalid")
    authority = await get_stream_authority(conn, tenant_id=tenant_id, run_id=run_id, for_update=True)
    if authority is None or authority.state not in {"confirmed", "degraded", "terminal"}:
        raise SseAuthorityConflictError("sse_stream_not_confirmed")
    if authority.revocation_state != "active":
        raise SseAuthorityConflictError("sse_authority_revoked")
    result = await conn.execute("""insert into sse_authority_leases(id,tenant_id,run_id,api_instance_id,connection_id,authorization_epoch,lease_not_after) values (%s,%s,%s,%s,%s,%s,clock_timestamp()+(%s*interval '1 second')) on conflict(tenant_id,run_id,api_instance_id,connection_id) do update set authorization_epoch=excluded.authorization_epoch,lease_not_after=excluded.lease_not_after,closed_at=null,close_reason=null,updated_at=clock_timestamp() returning *""", (f"sle_{uuid.uuid4().hex}", tenant_id, run_id, api_instance_id, connection_id, authority.authorization_epoch, lease_seconds))
    row = await result.fetchone()
    if row is None:
        raise SseAuthorityConflictError("sse_authority_lease_unavailable")
    return SseAuthorityLease(str(row["id"]), tenant_id, run_id, api_instance_id, connection_id, int(row["authorization_epoch"]), row["lease_not_after"])


async def close_sse_authority_lease(conn: AsyncConnection[dict[str, object]], *, lease_id: str, reason: str) -> bool:
    result = await conn.execute("update sse_authority_leases set closed_at=coalesce(closed_at,clock_timestamp()),close_reason=%s,updated_at=clock_timestamp() where id=%s returning id", (reason, lease_id))
    return await result.fetchone() is not None


async def commit_sse_revocation(conn: AsyncConnection[dict[str, object]], *, tenant_id: str, run_id: str) -> StreamAuthority:
    result = await conn.execute("update sse_stream_authorities set authorization_epoch=authorization_epoch+1,revocation_state='committed',revocation_committed_at=clock_timestamp(),updated_at=clock_timestamp() where tenant_id=%s and run_id=%s and revocation_state='active' returning *", (tenant_id, run_id))
    row = await result.fetchone()
    if row is None:
        raise SseAuthorityConflictError("sse_revocation_conflict")
    return _authority(row)


def freeze_terminal_intent(*, tenant_id: str, run_id: str, attempt_id: str, tenant_scope: str, stream_incarnation: int, status: str) -> TerminalPublicationIntent:
    if status not in {"succeeded", "failed", "cancelled"}:
        raise ValueError("sse_terminal_status_invalid")
    terminal_id, end_id = terminal_event_ids(tenant_scope=tenant_scope, run_id=run_id, attempt_id=attempt_id, incarnation=stream_incarnation)
    terminal = canonical_json_bytes({"event_id": terminal_id, "hydrate_required": True, "status": status}).decode()
    end = canonical_json_bytes({"terminal_event_id": terminal_id}).decode()
    return TerminalPublicationIntent(f"sti_{uuid.uuid4().hex}", tenant_id, run_id, attempt_id, stream_incarnation, terminal_id, end_id, terminal, _sha256(terminal), end, _sha256(end))


async def persist_terminal_intent(conn: AsyncConnection[dict[str, object]], *, intent: TerminalPublicationIntent) -> TerminalPublicationIntent:
    result = await conn.execute("""insert into sse_terminal_publication_intents(id,tenant_id,run_id,attempt_id,stream_incarnation,schema_version,projection_version,terminal_event_id,end_event_id,terminal_payload_bytes,terminal_payload_digest,terminal_payload_size,end_payload_bytes,end_payload_digest,end_payload_size,state) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'pending') on conflict(tenant_id,run_id,attempt_id) do nothing returning *""", (intent.intent_id, intent.tenant_id, intent.run_id, intent.attempt_id, intent.stream_incarnation, STREAM_EVENT_SCHEMA, STREAM_PROJECTION_VERSION, intent.terminal_event_id, intent.end_event_id, intent.terminal_payload_bytes, intent.terminal_payload_digest, len(intent.terminal_payload_bytes.encode()), intent.end_payload_bytes, intent.end_payload_digest, len(intent.end_payload_bytes.encode())))
    row = await result.fetchone()
    if row is None:
        result = await conn.execute("select * from sse_terminal_publication_intents where tenant_id=%s and run_id=%s and attempt_id=%s for update", (intent.tenant_id, intent.run_id, intent.attempt_id))
        row = await result.fetchone()
        existing = _intent(row) if row is not None else None
        expected = (intent.stream_incarnation, intent.terminal_event_id, intent.end_event_id, intent.terminal_payload_digest, intent.end_payload_digest)
        actual = (existing.stream_incarnation, existing.terminal_event_id, existing.end_event_id, existing.terminal_payload_digest, existing.end_payload_digest) if existing else None
        if actual != expected:
            raise SseAuthorityConflictError("sse_terminal_intent_conflict")
        return existing
    return _intent(row)


async def ensure_run_terminal_intent(conn: AsyncConnection[dict[str, object]], *, tenant_id: str, run_id: str, status: str) -> TerminalPublicationIntent | None:
    authority = await get_stream_authority(conn, tenant_id=tenant_id, run_id=run_id, for_update=True)
    if authority is None:
        return None
    intent = await persist_terminal_intent(conn, intent=freeze_terminal_intent(tenant_id=tenant_id, run_id=run_id, attempt_id=authority.attempt_id, tenant_scope=authority.tenant_scope, stream_incarnation=authority.stream_incarnation, status=status))
    await conn.execute("update sse_stream_authorities set state='terminal',updated_at=clock_timestamp() where tenant_id=%s and run_id=%s and attempt_id=%s", (tenant_id, run_id, authority.attempt_id))
    return intent


async def get_terminal_intent(conn: AsyncConnection[dict[str, object]], *, tenant_id: str, run_id: str) -> TerminalPublicationIntent | None:
    result = await conn.execute("select * from sse_terminal_publication_intents where tenant_id=%s and run_id=%s", (tenant_id, run_id))
    row = await result.fetchone()
    return _intent(row) if row is not None else None


async def mark_terminal_intent_published(conn: AsyncConnection[dict[str, object]], *, intent: TerminalPublicationIntent) -> TerminalPublicationIntent:
    result = await conn.execute("update sse_terminal_publication_intents set state='published',published_at=coalesce(published_at,clock_timestamp()),updated_at=clock_timestamp() where id=%s and terminal_payload_digest=%s and end_payload_digest=%s and state in ('pending','published') returning *", (intent.intent_id, intent.terminal_payload_digest, intent.end_payload_digest))
    row = await result.fetchone()
    if row is None:
        raise SseAuthorityConflictError("sse_terminal_intent_fenced")
    return _intent(row)


CHAT_ASSISTANT_DELTA_SOURCE = "worker_answer_delta_v1"
_ASSISTANT_DELTA_INPUT_STAGES = frozenset({"message", "assistant"})


def canonical_assistant_delta_event(*, stage: str, payload: dict[str, Any] | None) -> tuple[str, str, dict[str, Any]] | None:
    if stage not in _ASSISTANT_DELTA_INPUT_STAGES or not isinstance(payload, dict):
        return None
    delta = payload.get("delta")
    if not isinstance(delta, str) or not delta:
        return None
    return "answer", "", {"delta": delta, "source": CHAT_ASSISTANT_DELTA_SOURCE, "visible_to_user": True, "severity": "info"}


@dataclass(slots=True)
class RunStreamPublisher:
    tenant_id: str
    run_id: str
    attempt_id: str
    authority_secret: str
    bridge: RedisStreamBridge = field(default_factory=RedisStreamBridge)
    authority: StreamAuthority | None = None
    source_sequence: int = 0

    async def prepare(self, conn: Any) -> None:
        self.authority = await create_or_get_stream_admission(conn, tenant_id=self.tenant_id, run_id=self.run_id, attempt_id=self.attempt_id, tenant_scope=tenant_scope(self.tenant_id, secret=self.authority_secret))

    async def open(self) -> None:
        authority = self._authority()
        await self.bridge.append(StreamEnvelope.from_json(authority.open_payload_bytes))

    async def confirm(self, conn: Any) -> None:
        self.authority = await confirm_stream_admission(conn, authority=self._authority())

    async def publish_assistant_delta(self, delta: str) -> None:
        authority = self._authority()
        if authority.state != "confirmed":
            raise RuntimeError("sse_stream_admission_missing")
        self.source_sequence += 1
        await self.bridge.append(new_envelope(event_id=stable_event_id(tenant_scope_value=authority.tenant_scope, run_id=self.run_id, attempt_id=self.attempt_id, batch_id="worker-event-sink", item_index=self.source_sequence), tenant_scope_value=authority.tenant_scope, run_id=self.run_id, attempt_id=self.attempt_id, stream_incarnation=authority.stream_incarnation, event_type="assistant_text_delta", payload={"delta": delta}))

    async def aclose(self) -> None:
        await self.bridge.aclose()

    def _authority(self) -> StreamAuthority:
        if self.authority is None:
            raise RuntimeError("sse_stream_admission_missing")
        return self.authority
