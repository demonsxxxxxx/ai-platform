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


def _queue_reason(*, queued: int, processing: int, active_workers: int, max_active_worker_runs: int) -> str:
    if max_active_worker_runs > 0 and processing >= max_active_worker_runs:
        return "worker_capacity_full"
    if active_workers > processing:
        return "worker_available"
    if active_workers > 0 and processing >= active_workers:
        return "workers_busy"
    return "queued_behind_existing_work"


async def get_queue_insight(tenant_id: str) -> dict[str, Any]:
    keys = get_queue_keys()
    settings = get_settings()
    redis = await get_redis()
    try:
        queued_depth = int(await redis.llen(keys.queued))
        processing_depth = int(await redis.llen(keys.processing))
        dead_letter_depth = int(await redis.llen(keys.dead_letter))
        queued_items = await redis.lrange(keys.queued, 0, -1)
        processing_meta = await redis.hgetall(keys.processing_meta)
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
        return {
            "tenant_id": tenant_id,
            "reason": _queue_reason(
                queued=queued_depth,
                processing=processing_depth,
                active_workers=active_workers,
                max_active_worker_runs=max_active_worker_runs,
            ),
            "depths": {
                "queued": queued_depth,
                "processing": processing_depth,
                "dead_letter": dead_letter_depth,
                "tenant_queued": _count_queued_for_tenant(queued_items, tenant_id),
                "tenant_processing": _count_processing_for_tenant(processing_meta, tenant_id),
            },
            "workers": {"active": active_workers},
            "capacity": capacity,
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


async def lease_run(
    timeout_seconds: int = 5,
    *,
    worker_id: str = "worker",
    max_processing_runs: int | None = None,
) -> QueueMessage | None:
    keys = get_queue_keys()
    redis = await get_redis()
    try:
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
        try:
            payload = QueueRunPayload.model_validate_json(raw).model_dump()
        except Exception as exc:
            await redis.lrem(keys.processing, 1, raw)
            await redis.rpush(
                keys.dead_letter,
                _dead_letter_json(
                    raw=raw,
                    error_code="invalid_queue_payload",
                    error_message=str(exc),
                    attempts=attempts,
                    worker_id=worker_id,
                ),
            )
            await redis.hdel(keys.retry_meta, message_id)
            return None
        retry_meta_payload = {
            "attempts": attempts,
            "leased_at": now,
            "heartbeat_at": now,
            "worker_id": worker_id,
            "run_id": payload["run_id"],
            "tenant_id": payload["tenant_id"],
        }
        await redis.hset(
            keys.processing_meta,
            message_id,
            json.dumps(retry_meta_payload, ensure_ascii=False),
        )
        await redis.hset(keys.retry_meta, message_id, json.dumps(retry_meta_payload, ensure_ascii=False))
        await redis.hset(keys.worker_heartbeat, worker_id, str(now))
        return QueueMessage(raw=raw, payload=payload, message_id=message_id)
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
