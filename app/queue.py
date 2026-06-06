from dataclasses import dataclass
import hashlib
import json
import time
from typing import Any

from redis.asyncio import Redis
from redis.exceptions import TimeoutError as RedisTimeoutError

from app.models import QueueRunPayload
from app.settings import get_settings


DEFAULT_QUEUE_KEY_PREFIX = "ai-platform:runs"
QUEUE_KEY = f"{DEFAULT_QUEUE_KEY_PREFIX}:queued"
PROCESSING_KEY = f"{DEFAULT_QUEUE_KEY_PREFIX}:processing"
PROCESSING_META_KEY = f"{DEFAULT_QUEUE_KEY_PREFIX}:processing-meta"
RETRY_META_KEY = f"{DEFAULT_QUEUE_KEY_PREFIX}:retry-meta"
DEAD_LETTER_KEY = f"{DEFAULT_QUEUE_KEY_PREFIX}:dead-letter"
WORKER_HEARTBEAT_KEY = f"{DEFAULT_QUEUE_KEY_PREFIX}:worker-heartbeat"
DEFAULT_VISIBILITY_TIMEOUT_SECONDS = 900
DEFAULT_MAX_ATTEMPTS = 3


LEASE_QUOTA_SCRIPT = """
-- ai-platform:lease-run-with-quota:v1
local queued_key = KEYS[1]
local processing_key = KEYS[2]
local processing_meta_key = KEYS[3]
local retry_meta_key = KEYS[4]
local worker_heartbeat_key = KEYS[5]

local raw = ARGV[1]
local scan_limit = tonumber(ARGV[2])
local absolute_index = tonumber(ARGV[3])
local message_id = ARGV[4]
local worker_id = ARGV[5]
local now = tonumber(ARGV[6])
local max_processing_runs = tonumber(ARGV[7])
local tenant_processing_limit = tonumber(ARGV[8])
local user_processing_limit = tonumber(ARGV[9])
local tenant_id = ARGV[10]
local user_id = ARGV[11]
local run_id = ARGV[12]

if max_processing_runs > 0 and redis.call("llen", processing_key) >= max_processing_runs then
  return cjson.encode({status = "capacity_full"})
end

local queue_length = redis.call("llen", queued_key)
if absolute_index < 0 or absolute_index >= queue_length then
  return cjson.encode({status = "conflict"})
end
if redis.call("lindex", queued_key, absolute_index) ~= raw then
  return cjson.encode({status = "conflict"})
end

local tenant_processing = 0
local user_processing = 0
local processing_items = redis.call("lrange", processing_key, 0, -1)
for _, processing_raw in ipairs(processing_items) do
  local ok, payload = pcall(cjson.decode, processing_raw)
  if ok and type(payload) == "table" then
    if tostring(payload["tenant_id"] or "") == tenant_id then
      tenant_processing = tenant_processing + 1
      if tostring(payload["user_id"] or "") == user_id then
        user_processing = user_processing + 1
      end
    end
  end
end

if tenant_processing_limit > 0 and tenant_processing >= tenant_processing_limit then
  return cjson.encode({
    status = "quota_blocked",
    tenant_processing = tenant_processing,
    user_processing = user_processing,
  })
end
if user_processing_limit > 0 and user_processing >= user_processing_limit then
  return cjson.encode({
    status = "quota_blocked",
    tenant_processing = tenant_processing,
    user_processing = user_processing,
  })
end

local attempts = 1
local attempts_source = redis.call("hget", retry_meta_key, message_id)
if not attempts_source then
  attempts_source = redis.call("hget", processing_meta_key, message_id)
end
if attempts_source then
  local ok, meta = pcall(cjson.decode, attempts_source)
  if ok and type(meta) == "table" then
    local parsed_attempts = tonumber(meta["attempts"] or 0)
    if parsed_attempts then
      attempts = parsed_attempts + 1
    else
      attempts = 1
    end
    if attempts < 1 then
      attempts = 1
    end
  end
end

local sentinel = "__ai_platform_queue_move__:" .. message_id .. ":" .. tostring(now) .. ":" .. worker_id
redis.call("lset", queued_key, absolute_index, sentinel)
redis.call("lrem", queued_key, 1, sentinel)
redis.call("lpush", processing_key, raw)

local quota_snapshot = {
  tenant_processing = tenant_processing,
  tenant_processing_limit = tenant_processing_limit,
  tenant_processing_saturated = tenant_processing_limit > 0 and tenant_processing >= tenant_processing_limit,
  user_processing = user_processing,
  user_processing_limit = user_processing_limit,
  user_processing_saturated = user_processing_limit > 0 and user_processing >= user_processing_limit,
}
local meta = {
  attempts = attempts,
  leased_at = now,
  heartbeat_at = now,
  worker_id = worker_id,
  run_id = run_id,
  tenant_id = tenant_id,
  user_id = user_id,
  quota_snapshot = quota_snapshot,
}
local encoded_meta = cjson.encode(meta)
redis.call("hset", processing_meta_key, message_id, encoded_meta)
redis.call("hset", retry_meta_key, message_id, encoded_meta)
redis.call("hset", worker_heartbeat_key, worker_id, tostring(now))

return cjson.encode({
  status = "leased",
  attempts = attempts,
  tenant_processing = tenant_processing,
  user_processing = user_processing,
})
"""


DEAD_LETTER_INVALID_QUOTA_SCRIPT = """
-- ai-platform:dead-letter-invalid-quota:v1
local queued_key = KEYS[1]
local processing_meta_key = KEYS[2]
local retry_meta_key = KEYS[3]
local dead_letter_key = KEYS[4]

local raw = ARGV[1]
local scan_limit = tonumber(ARGV[2])
local absolute_index = tonumber(ARGV[3])
local message_id = ARGV[4]
local worker_id = ARGV[5]
local now = tonumber(ARGV[6])
local error_message = ARGV[7]

local queue_length = redis.call("llen", queued_key)
if absolute_index < 0 or absolute_index >= queue_length then
  return cjson.encode({status = "conflict"})
end
if redis.call("lindex", queued_key, absolute_index) ~= raw then
  return cjson.encode({status = "conflict"})
end

local attempts = 1
local attempts_source = redis.call("hget", retry_meta_key, message_id)
if not attempts_source then
  attempts_source = redis.call("hget", processing_meta_key, message_id)
end
if attempts_source then
  local ok, meta = pcall(cjson.decode, attempts_source)
  if ok and type(meta) == "table" then
    local parsed_attempts = tonumber(meta["attempts"] or 0)
    if parsed_attempts then
      attempts = parsed_attempts + 1
    else
      attempts = 1
    end
    if attempts < 1 then
      attempts = 1
    end
  end
end

local sentinel = "__ai_platform_queue_dead_letter__:" .. message_id .. ":" .. tostring(now) .. ":" .. worker_id
redis.call("lset", queued_key, absolute_index, sentinel)
redis.call("lrem", queued_key, 1, sentinel)
redis.call("rpush", dead_letter_key, cjson.encode({
  schema_version = "ai-platform.dead-letter.v1",
  error_code = "invalid_queue_payload",
  error_message = error_message,
  attempts = attempts,
  worker_id = worker_id,
  raw = raw,
  created_at = now,
}))
redis.call("hdel", retry_meta_key, message_id)

return cjson.encode({status = "dead_lettered", attempts = attempts})
"""


@dataclass(frozen=True)
class QueueKeys:
    queued: str
    processing: str
    processing_meta: str
    retry_meta: str
    dead_letter: str
    worker_heartbeat: str


@dataclass(frozen=True)
class QueueMessage:
    raw: str
    payload: dict[str, Any]
    message_id: str


async def get_redis() -> Redis:
    settings = get_settings()
    return Redis.from_url(settings.redis_url, decode_responses=True)


def get_queue_keys() -> QueueKeys:
    prefix = get_settings().queue_key_prefix.strip().rstrip(":") or DEFAULT_QUEUE_KEY_PREFIX
    return QueueKeys(
        queued=f"{prefix}:queued",
        processing=f"{prefix}:processing",
        processing_meta=f"{prefix}:processing-meta",
        retry_meta=f"{prefix}:retry-meta",
        dead_letter=f"{prefix}:dead-letter",
        worker_heartbeat=f"{prefix}:worker-heartbeat",
    )


def message_id_for_raw(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _now() -> float:
    return time.time()


def _dead_letter_json(
    *,
    raw: str,
    error_code: str,
    error_message: str,
    attempts: int | None = None,
    worker_id: str | None = None,
) -> str:
    return json.dumps(
        {
            "schema_version": "ai-platform.dead-letter.v1",
            "error_code": error_code,
            "error_message": error_message,
            "attempts": attempts,
            "worker_id": worker_id,
            "raw": raw,
            "created_at": _now(),
        },
        ensure_ascii=False,
    )


async def enqueue_run(payload: dict[str, Any]) -> int:
    validated = QueueRunPayload.model_validate(payload)
    keys = get_queue_keys()
    redis = await get_redis()
    try:
        position = await redis.rpush(keys.queued, validated.model_dump_json())
        return int(position or 1)
    finally:
        await redis.aclose()


async def remove_queued_run(*, tenant_id: str, run_id: str) -> int:
    keys = get_queue_keys()
    redis = await get_redis()
    removed_total = 0
    try:
        queued_items = await redis.lrange(keys.queued, 0, -1)
        for raw in queued_items:
            try:
                payload = QueueRunPayload.model_validate_json(raw)
            except Exception:
                continue
            if payload.tenant_id != tenant_id or payload.run_id != run_id:
                continue
            removed_total += int(await redis.lrem(keys.queued, 0, raw) or 0)
        return removed_total
    finally:
        await redis.aclose()


async def get_queue_status() -> dict[str, Any]:
    keys = get_queue_keys()
    settings = get_settings()
    redis = await get_redis()
    try:
        queued = await redis.llen(keys.queued)
        processing = await redis.llen(keys.processing)
        dead_letter = await redis.llen(keys.dead_letter)
        worker_heartbeats = await redis.hgetall(keys.worker_heartbeat)
        active_worker_heartbeats = _active_worker_heartbeats(
            worker_heartbeats,
            now=_now(),
            ttl_seconds=float(getattr(settings, "worker_heartbeat_ttl_seconds", 60.0)),
        )
        return {
            "depths": {
                "queued": int(queued),
                "processing": int(processing),
                "dead_letter": int(dead_letter),
            },
            "keys": {
                "queued": keys.queued,
                "processing": keys.processing,
                "processing_meta": keys.processing_meta,
                "retry_meta": keys.retry_meta,
                "dead_letter": keys.dead_letter,
                "worker_heartbeat": keys.worker_heartbeat,
            },
            "workers": sorted(active_worker_heartbeats.keys()),
        }
    finally:
        await redis.aclose()


def _count_queued_for_tenant(raw_items: list[str], tenant_id: str) -> int:
    count = 0
    for raw in raw_items:
        try:
            payload = QueueRunPayload.model_validate_json(raw)
        except Exception:
            continue
        if payload.tenant_id == tenant_id:
            count += 1
    return count


def _count_processing_for_tenant(meta_items: dict[str, str], tenant_id: str) -> int:
    count = 0
    for raw_meta in meta_items.values():
        try:
            meta = json.loads(raw_meta)
        except (TypeError, json.JSONDecodeError):
            continue
        if meta.get("tenant_id") == tenant_id:
            count += 1
    return count


def _queued_quota_counts(raw_items: list[str], tenant_id: str) -> tuple[int, dict[str, int]]:
    tenant_queued = 0
    user_queued: dict[str, int] = {}
    for raw in raw_items:
        try:
            payload = QueueRunPayload.model_validate_json(raw)
        except Exception:
            continue
        if payload.tenant_id != tenant_id:
            continue
        tenant_queued += 1
        user_queued[payload.user_id] = user_queued.get(payload.user_id, 0) + 1
    return tenant_queued, user_queued


def _processing_quota_counts_from_raw_items(
    raw_items: list[str],
) -> tuple[dict[str, int], dict[tuple[str, str], int]]:
    tenant_counts: dict[str, int] = {}
    user_counts: dict[tuple[str, str], int] = {}
    for raw in raw_items:
        try:
            payload = QueueRunPayload.model_validate_json(raw)
        except Exception:
            continue
        tenant_counts[payload.tenant_id] = tenant_counts.get(payload.tenant_id, 0) + 1
        key = (payload.tenant_id, payload.user_id)
        user_counts[key] = user_counts.get(key, 0) + 1
    return tenant_counts, user_counts


def _processing_quota_counts(meta_items: dict[str, str]) -> tuple[dict[str, int], dict[tuple[str, str], int]]:
    tenant_counts: dict[str, int] = {}
    user_counts: dict[tuple[str, str], int] = {}
    for raw_meta in meta_items.values():
        try:
            meta = json.loads(raw_meta)
        except (TypeError, json.JSONDecodeError):
            continue
        tenant_id = str(meta.get("tenant_id") or "")
        user_id = str(meta.get("user_id") or "")
        if tenant_id:
            tenant_counts[tenant_id] = tenant_counts.get(tenant_id, 0) + 1
        if tenant_id and user_id:
            key = (tenant_id, user_id)
            user_counts[key] = user_counts.get(key, 0) + 1
    return tenant_counts, user_counts


def _quota_snapshot(
    payload: QueueRunPayload,
    *,
    tenant_counts: dict[str, int],
    user_counts: dict[tuple[str, str], int],
    tenant_processing_limit: int,
    user_processing_limit: int,
) -> dict[str, Any]:
    tenant_processing = tenant_counts.get(payload.tenant_id, 0)
    user_processing = user_counts.get((payload.tenant_id, payload.user_id), 0)
    return {
        "tenant_processing": tenant_processing,
        "tenant_processing_limit": tenant_processing_limit,
        "tenant_processing_saturated": tenant_processing_limit > 0 and tenant_processing >= tenant_processing_limit,
        "user_processing": user_processing,
        "user_processing_limit": user_processing_limit,
        "user_processing_saturated": user_processing_limit > 0 and user_processing >= user_processing_limit,
    }


def _quota_allows(snapshot: dict[str, Any]) -> bool:
    return not bool(snapshot["tenant_processing_saturated"] or snapshot["user_processing_saturated"])


def _next_attempts(*, retry_meta: str | None, existing_meta: str | None) -> int:
    attempts = 1
    if retry_meta:
        try:
            attempts = int(json.loads(retry_meta).get("attempts", 0)) + 1
        except (TypeError, ValueError, json.JSONDecodeError):
            attempts = 1
    elif existing_meta:
        try:
            attempts = int(json.loads(existing_meta).get("attempts", 0)) + 1
        except (TypeError, ValueError, json.JSONDecodeError):
            attempts = 1
    return attempts


def _decode_redis_script_result(raw_result: object) -> dict[str, Any]:
    if isinstance(raw_result, dict):
        return raw_result
    if isinstance(raw_result, bytes):
        raw_result = raw_result.decode("utf-8", errors="replace")
    if not isinstance(raw_result, str):
        return {"status": "invalid_result"}
    try:
        result = json.loads(raw_result)
    except json.JSONDecodeError:
        return {"status": "invalid_result"}
    return result if isinstance(result, dict) else {"status": "invalid_result"}


def _active_worker_heartbeats(
    worker_heartbeats: dict[str, str],
    *,
    now: float,
    ttl_seconds: float,
) -> dict[str, str]:
    if ttl_seconds <= 0:
        return dict(worker_heartbeats)
    active = {}
    for worker_id, raw_timestamp in worker_heartbeats.items():
        try:
            heartbeat_at = float(raw_timestamp)
        except (TypeError, ValueError):
            continue
        if now - heartbeat_at <= ttl_seconds:
            active[worker_id] = raw_timestamp
    return active


def _capacity_snapshot(*, processing: int, max_active_worker_runs: int) -> dict[str, Any]:
    if max_active_worker_runs <= 0:
        return {
            "max_active_worker_runs": max_active_worker_runs,
            "processing_saturated": False,
            "available_worker_slots": None,
        }
    return {
        "max_active_worker_runs": max_active_worker_runs,
        "processing_saturated": processing >= max_active_worker_runs,
        "available_worker_slots": max(max_active_worker_runs - processing, 0),
    }


def _queue_throttling_snapshot(
    *,
    tenant_id: str,
    tenant_counts: dict[str, int],
    user_counts: dict[tuple[str, str], int],
    user_queued_counts: dict[str, int],
    tenant_processing_limit: int,
    user_processing_limit: int,
    user_id: str | None,
    include_user_breakdown: bool,
) -> dict[str, Any]:
    tenant_processing = tenant_counts.get(tenant_id, 0)
    snapshot: dict[str, Any] = {
        "tenant_processing": tenant_processing,
        "tenant_processing_limit": tenant_processing_limit,
        "tenant_processing_saturated": tenant_processing_limit > 0 and tenant_processing >= tenant_processing_limit,
        "user_processing_limit": user_processing_limit,
    }
    if user_id:
        user_processing = user_counts.get((tenant_id, user_id), 0)
        snapshot["current_user"] = {
            "queued": user_queued_counts.get(user_id, 0),
            "processing": user_processing,
            "processing_saturated": user_processing_limit > 0 and user_processing >= user_processing_limit,
        }
    if include_user_breakdown:
        user_ids = sorted(
            {
                user
                for candidate_tenant_id, user in user_counts
                if candidate_tenant_id == tenant_id
            }
            | set(user_queued_counts)
        )
        snapshot["users"] = {
            user: {
                "queued": user_queued_counts.get(user, 0),
                "processing": user_counts.get((tenant_id, user), 0),
                "processing_saturated": user_processing_limit > 0
                and user_counts.get((tenant_id, user), 0) >= user_processing_limit,
            }
            for user in user_ids
        }
    else:
        snapshot["users"] = {}
    return snapshot


def _queue_reason(*, queued: int, processing: int, active_workers: int, max_active_worker_runs: int) -> str:
    if max_active_worker_runs > 0 and processing >= max_active_worker_runs:
        return "worker_capacity_full"
    if active_workers > processing:
        return "worker_available"
    if active_workers > 0 and processing >= active_workers:
        return "workers_busy"
    return "queued_behind_existing_work"


async def get_queue_insight(
    tenant_id: str,
    *,
    user_id: str | None = None,
    include_user_breakdown: bool = False,
) -> dict[str, Any]:
    keys = get_queue_keys()
    settings = get_settings()
    redis = await get_redis()
    try:
        queued_depth = int(await redis.llen(keys.queued))
        processing_depth = int(await redis.llen(keys.processing))
        dead_letter_depth = int(await redis.llen(keys.dead_letter))
        queued_scan_limit = int(getattr(settings, "queue_insight_scan_limit", 500))
        queued_items = (
            await redis.lrange(keys.queued, -queued_scan_limit, -1)
            if queued_scan_limit > 0
            else []
        )
        processing_items = await redis.lrange(keys.processing, 0, -1)
        worker_heartbeats = await redis.hgetall(keys.worker_heartbeat)
        active_worker_heartbeats = _active_worker_heartbeats(
            worker_heartbeats,
            now=_now(),
            ttl_seconds=float(getattr(settings, "worker_heartbeat_ttl_seconds", 60.0)),
        )
        active_workers = len(active_worker_heartbeats)
        max_active_worker_runs = int(settings.max_active_worker_runs)
        capacity = _capacity_snapshot(
            processing=processing_depth,
            max_active_worker_runs=max_active_worker_runs,
        )
        tenant_processing_limit = int(getattr(settings, "queue_tenant_processing_limit", 0))
        user_processing_limit = int(getattr(settings, "queue_user_processing_limit", 0))
        lease_scan_limit = int(getattr(settings, "queue_lease_scan_limit", 50))
        queue_sample = {
            "queued_scan_limit": queued_scan_limit,
            "queued_sampled": len(queued_items),
            "queued_sample_complete": queued_depth <= len(queued_items),
        }
        capacity.update(
            {
                "queue_tenant_processing_limit": tenant_processing_limit,
                "queue_user_processing_limit": user_processing_limit,
                "queue_lease_scan_limit": lease_scan_limit,
            }
        )
        tenant_counts, user_counts = _processing_quota_counts_from_raw_items(processing_items)
        tenant_queued, user_queued_counts = _queued_quota_counts(queued_items, tenant_id)
        throttling = _queue_throttling_snapshot(
            tenant_id=tenant_id,
            tenant_counts=tenant_counts,
            user_counts=user_counts,
            user_queued_counts=user_queued_counts,
            tenant_processing_limit=tenant_processing_limit,
            user_processing_limit=user_processing_limit,
            user_id=user_id,
            include_user_breakdown=include_user_breakdown,
        )
        reason = _queue_reason(
            queued=queued_depth,
            processing=processing_depth,
            active_workers=active_workers,
            max_active_worker_runs=max_active_worker_runs,
        )
        if tenant_queued > 0 and throttling["tenant_processing_saturated"]:
            reason = "tenant_quota_full"
        elif user_id:
            current_user = throttling.get("current_user") if isinstance(throttling.get("current_user"), dict) else {}
            if current_user.get("queued", 0) > 0 and current_user.get("processing_saturated"):
                reason = "user_quota_full"
        elif include_user_breakdown:
            users = throttling.get("users") if isinstance(throttling.get("users"), dict) else {}
            if any(
                isinstance(user_state, dict)
                and user_state.get("queued", 0) > 0
                and user_state.get("processing_saturated")
                for user_state in users.values()
            ):
                reason = "user_quota_full"
        return {
            "tenant_id": tenant_id,
            "reason": reason,
            "depths": {
                "queued": queued_depth,
                "processing": processing_depth,
                "dead_letter": dead_letter_depth,
                "tenant_queued": tenant_queued,
                "tenant_processing": tenant_counts.get(tenant_id, 0),
            },
            "workers": {"active": active_workers},
            "capacity": capacity,
            "queue_sample": queue_sample,
            "throttling": throttling,
        }
    finally:
        await redis.aclose()


async def get_run_queue_position(*, tenant_id: str, run_id: str) -> int | None:
    keys = get_queue_keys()
    redis = await get_redis()
    try:
        queued_items = await redis.lrange(keys.queued, 0, -1)
        for index, raw in enumerate(queued_items, start=1):
            try:
                payload = QueueRunPayload.model_validate_json(raw)
            except Exception:
                continue
            if payload.tenant_id == tenant_id and payload.run_id == run_id:
                return index
        return None
    finally:
        await redis.aclose()


async def _record_leased_payload(
    redis: Redis,
    keys: QueueKeys,
    *,
    message_id: str,
    payload: dict[str, Any],
    attempts: int,
    worker_id: str,
    now: float,
    quota_snapshot: dict[str, Any] | None = None,
) -> None:
    retry_meta_payload = {
        "attempts": attempts,
        "leased_at": now,
        "heartbeat_at": now,
        "worker_id": worker_id,
        "run_id": payload["run_id"],
        "tenant_id": payload["tenant_id"],
        "user_id": payload["user_id"],
    }
    if quota_snapshot is not None:
        retry_meta_payload["quota_snapshot"] = quota_snapshot
    await redis.hset(
        keys.processing_meta,
        message_id,
        json.dumps(retry_meta_payload, ensure_ascii=False),
    )
    await redis.hset(keys.retry_meta, message_id, json.dumps(retry_meta_payload, ensure_ascii=False))
    await redis.hset(keys.worker_heartbeat, worker_id, str(now))


async def _dead_letter_invalid_queue_payload(
    redis: Redis,
    keys: QueueKeys,
    *,
    raw: str,
    message_id: str,
    attempts: int,
    worker_id: str,
    remove_from_key: str,
    error_message: str,
) -> None:
    await redis.lrem(remove_from_key, 1, raw)
    await redis.rpush(
        keys.dead_letter,
        _dead_letter_json(
            raw=raw,
            error_code="invalid_queue_payload",
            error_message=error_message,
            attempts=attempts,
            worker_id=worker_id,
        ),
    )
    await redis.hdel(keys.retry_meta, message_id)


async def _dead_letter_invalid_queued_payload_atomic(
    redis: Redis,
    keys: QueueKeys,
    *,
    raw: str,
    message_id: str,
    worker_id: str,
    lease_scan_limit: int,
    absolute_index: int,
    error_message: str,
) -> dict[str, Any]:
    result = await redis.eval(
        DEAD_LETTER_INVALID_QUOTA_SCRIPT,
        4,
        keys.queued,
        keys.processing_meta,
        keys.retry_meta,
        keys.dead_letter,
        raw,
        lease_scan_limit,
        absolute_index,
        message_id,
        worker_id,
        _now(),
        error_message,
    )
    return _decode_redis_script_result(result)


async def _lease_run_legacy(
    redis: Redis,
    keys: QueueKeys,
    *,
    timeout_seconds: int = 5,
    worker_id: str = "worker",
    max_processing_runs: int | None = None,
) -> QueueMessage | None:
    if max_processing_runs is not None and max_processing_runs > 0:
        processing_depth = int(await redis.llen(keys.processing))
        if processing_depth >= max_processing_runs:
            return None
    try:
        raw = await redis.brpoplpush(keys.queued, keys.processing, timeout=timeout_seconds)
    except RedisTimeoutError:
        return None
    if raw is None:
        return None
    if max_processing_runs is not None and max_processing_runs > 0:
        processing_depth = int(await redis.llen(keys.processing))
        if processing_depth > max_processing_runs:
            await redis.lrem(keys.processing, 1, raw)
            await redis.lpush(keys.queued, raw)
            return None
    message_id = message_id_for_raw(raw)
    now = _now()
    existing_meta = await redis.hget(keys.processing_meta, message_id)
    retry_meta = await redis.hget(keys.retry_meta, message_id)
    attempts = _next_attempts(retry_meta=retry_meta, existing_meta=existing_meta)
    try:
        payload = QueueRunPayload.model_validate_json(raw).model_dump()
    except Exception as exc:
        await _dead_letter_invalid_queue_payload(
            redis,
            keys,
            raw=raw,
            message_id=message_id,
            attempts=attempts,
            worker_id=worker_id,
            remove_from_key=keys.processing,
            error_message=str(exc),
        )
        return None
    await _record_leased_payload(
        redis,
        keys,
        message_id=message_id,
        payload=payload,
        attempts=attempts,
        worker_id=worker_id,
        now=now,
    )
    return QueueMessage(raw=raw, payload=payload, message_id=message_id)


async def _lease_run_with_quota(
    redis: Redis,
    keys: QueueKeys,
    *,
    worker_id: str,
    max_processing_runs: int | None,
    tenant_processing_limit: int,
    user_processing_limit: int,
    lease_scan_limit: int,
) -> QueueMessage | None:
    if max_processing_runs is not None and max_processing_runs > 0:
        processing_depth = int(await redis.llen(keys.processing))
        if processing_depth >= max_processing_runs:
            return None
    if lease_scan_limit <= 0:
        return None

    queued_depth = int(await redis.llen(keys.queued))
    scan_start = max(queued_depth - lease_scan_limit, 0)
    queued_items = await redis.lrange(keys.queued, scan_start, -1)
    if not queued_items:
        return None

    for raw_index, raw in reversed(list(enumerate(queued_items))):
        absolute_index = scan_start + raw_index
        message_id = message_id_for_raw(raw)
        try:
            payload_model = QueueRunPayload.model_validate_json(raw)
        except Exception as exc:
            await _dead_letter_invalid_queued_payload_atomic(
                redis,
                keys,
                raw=raw,
                message_id=message_id,
                worker_id=worker_id,
                lease_scan_limit=lease_scan_limit,
                absolute_index=absolute_index,
                error_message=str(exc),
            )
            continue

        result = _decode_redis_script_result(
            await redis.eval(
                LEASE_QUOTA_SCRIPT,
                5,
                keys.queued,
                keys.processing,
                keys.processing_meta,
                keys.retry_meta,
                keys.worker_heartbeat,
                raw,
                lease_scan_limit,
                absolute_index,
                message_id,
                worker_id,
                _now(),
                int(max_processing_runs or 0),
                tenant_processing_limit,
                user_processing_limit,
                payload_model.tenant_id,
                payload_model.user_id,
                payload_model.run_id,
            )
        )
        status = str(result.get("status") or "")
        if status == "capacity_full":
            return None
        if status in {"conflict", "quota_blocked"}:
            continue
        if status != "leased":
            continue
        payload = payload_model.model_dump()
        return QueueMessage(raw=raw, payload=payload, message_id=message_id)
    return None


async def lease_run(
    timeout_seconds: int = 5,
    *,
    worker_id: str = "worker",
    max_processing_runs: int | None = None,
    tenant_processing_limit: int | None = None,
    user_processing_limit: int | None = None,
    lease_scan_limit: int | None = None,
) -> QueueMessage | None:
    keys = get_queue_keys()
    redis = await get_redis()
    try:
        tenant_limit = int(tenant_processing_limit or 0)
        user_limit = int(user_processing_limit or 0)
        quota_mode = tenant_limit > 0 or user_limit > 0
        if not quota_mode:
            return await _lease_run_legacy(
                redis,
                keys,
                timeout_seconds=timeout_seconds,
                worker_id=worker_id,
                max_processing_runs=max_processing_runs,
            )
        if lease_scan_limit is None:
            lease_scan_limit = int(getattr(get_settings(), "queue_lease_scan_limit", 50))
        return await _lease_run_with_quota(
            redis,
            keys,
            worker_id=worker_id,
            max_processing_runs=max_processing_runs,
            tenant_processing_limit=tenant_limit,
            user_processing_limit=user_limit,
            lease_scan_limit=int(lease_scan_limit),
        )
    finally:
        await redis.aclose()


async def ack_run(raw: str, *, message_id: str | None = None) -> None:
    keys = get_queue_keys()
    redis = await get_redis()
    try:
        await redis.lrem(keys.processing, 1, raw)
        await redis.hdel(keys.processing_meta, message_id or message_id_for_raw(raw))
        await redis.hdel(keys.retry_meta, message_id or message_id_for_raw(raw))
    finally:
        await redis.aclose()


async def fail_leased_run(
    raw: str,
    *,
    error_code: str,
    error_message: str,
    message_id: str | None = None,
    worker_id: str | None = None,
) -> None:
    keys = get_queue_keys()
    redis = await get_redis()
    try:
        resolved_message_id = message_id or message_id_for_raw(raw)
        meta = await redis.hget(keys.processing_meta, resolved_message_id)
        retry_meta = await redis.hget(keys.retry_meta, resolved_message_id)
        attempts = None
        if meta:
            try:
                attempts = int(json.loads(meta).get("attempts", 0))
            except (TypeError, ValueError, json.JSONDecodeError):
                attempts = None
        if attempts is None and retry_meta:
            try:
                attempts = int(json.loads(retry_meta).get("attempts", 0))
            except (TypeError, ValueError, json.JSONDecodeError):
                attempts = None
        await redis.lrem(keys.processing, 1, raw)
        await redis.hdel(keys.processing_meta, resolved_message_id)
        await redis.hdel(keys.retry_meta, resolved_message_id)
        await redis.rpush(
            keys.dead_letter,
            _dead_letter_json(
                raw=raw,
                error_code=error_code,
                error_message=error_message,
                attempts=attempts,
                worker_id=worker_id,
            ),
        )
    finally:
        await redis.aclose()


async def heartbeat_run(message_id: str, *, worker_id: str) -> None:
    keys = get_queue_keys()
    redis = await get_redis()
    try:
        raw_meta = await redis.hget(keys.processing_meta, message_id)
        now = _now()
        if raw_meta:
            try:
                meta = json.loads(raw_meta)
            except json.JSONDecodeError:
                meta = {}
            meta["heartbeat_at"] = now
            meta["worker_id"] = worker_id
            await redis.hset(keys.processing_meta, message_id, json.dumps(meta, ensure_ascii=False))
        await redis.hset(keys.worker_heartbeat, worker_id, str(now))
    finally:
        await redis.aclose()


async def reclaim_expired_leases(
    *,
    visibility_timeout_seconds: int = DEFAULT_VISIBILITY_TIMEOUT_SECONDS,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    now: float | None = None,
) -> dict[str, int]:
    redis = await get_redis()
    reclaimed = 0
    dead_lettered = 0
    checked_at = _now() if now is None else now
    keys = get_queue_keys()
    try:
        processing = await redis.lrange(keys.processing, 0, -1)
        for raw in processing:
            message_id = message_id_for_raw(raw)
            raw_meta = await redis.hget(keys.processing_meta, message_id)
            if not raw_meta:
                await redis.lrem(keys.processing, 1, raw)
                retry_meta = await redis.hget(keys.retry_meta, message_id)
                retry_payload: dict[str, Any] = {}
                attempts = 1
                if retry_meta:
                    try:
                        retry_payload = json.loads(retry_meta)
                        attempts = int(retry_payload.get("attempts") or 0) + 1
                    except (TypeError, ValueError, json.JSONDecodeError):
                        retry_payload = {}
                        attempts = 1
                if attempts >= max_attempts:
                    await redis.rpush(
                        keys.dead_letter,
                        _dead_letter_json(
                            raw=raw,
                            error_code="lease_expired_max_attempts",
                            error_message="Leased queue message exceeded max attempts",
                            attempts=attempts,
                            worker_id=retry_payload.get("worker_id"),
                        ),
                    )
                    await redis.hdel(keys.retry_meta, message_id)
                    dead_lettered += 1
                else:
                    await redis.hset(
                        keys.retry_meta,
                        message_id,
                        json.dumps(
                            {
                                **retry_payload,
                                "attempts": attempts,
                                "requeued_at": checked_at,
                            },
                            ensure_ascii=False,
                        ),
                    )
                    await redis.rpush(keys.queued, raw)
                    reclaimed += 1
                continue
            try:
                meta = json.loads(raw_meta)
            except json.JSONDecodeError:
                meta = {}
            heartbeat_at = float(meta.get("heartbeat_at") or meta.get("leased_at") or 0)
            if checked_at - heartbeat_at <= visibility_timeout_seconds:
                continue
            attempts = int(meta.get("attempts") or 0)
            await redis.lrem(keys.processing, 1, raw)
            await redis.hdel(keys.processing_meta, message_id)
            if attempts >= max_attempts:
                await redis.rpush(
                    keys.dead_letter,
                    _dead_letter_json(
                        raw=raw,
                        error_code="lease_expired_max_attempts",
                        error_message="Leased queue message exceeded max attempts",
                        attempts=attempts,
                        worker_id=meta.get("worker_id"),
                    ),
                )
                await redis.hdel(keys.retry_meta, message_id)
                dead_lettered += 1
            else:
                await redis.hset(
                    keys.retry_meta,
                    message_id,
                    json.dumps(
                        {
                            **meta,
                            "attempts": attempts,
                            "requeued_at": checked_at,
                        },
                        ensure_ascii=False,
                    ),
                )
                await redis.rpush(keys.queued, raw)
                reclaimed += 1
        return {"reclaimed": reclaimed, "dead_lettered": dead_lettered}
    finally:
        await redis.aclose()


async def dequeue_run(timeout_seconds: int = 5) -> dict[str, Any] | None:
    message = await lease_run(timeout_seconds=timeout_seconds)
    if message is None:
        return None
    await ack_run(message.raw, message_id=message.message_id)
    return message.payload
