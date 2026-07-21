import pytest
from redis.exceptions import TimeoutError as RedisTimeoutError
from pydantic import ValidationError
import json

from app import queue
from app.models import QueueRunPayload


RELEASE_DECISION_SCHEMA_VERSION = "ai-platform.skill-release-decision.v1"


def release_decision(version: str) -> dict:
    return {
        "schema_version": RELEASE_DECISION_SCHEMA_VERSION,
        "policy_active": False,
        "selected_version": version,
        "selected_track": "manifest_pin",
    }


def primary_manifest(skill_id: str, version: str) -> dict:
    return {"skill_id": skill_id, "content_hash": version}


def queue_payload(**overrides) -> QueueRunPayload:
    skill_id = overrides.get("skill_id", "qa-file-reviewer")
    version = overrides.get("skill_version") or f"hash-{skill_id}"
    data = {
        "tenant_id": "tenant-a",
        "workspace_id": "workspace-a",
        "user_id": "user-a",
        "session_id": "session-a",
        "run_id": "run-a",
        "agent_id": "qa-word-review",
        "skill_id": skill_id,
        "file_ids": ["file-a"],
        "input": {"mode": "file"},
        "executor_type": "fake",
        "skill_version": version,
        "release_decision": release_decision(version),
        "skill_manifests": [primary_manifest(skill_id, version)],
    }
    data.update(overrides)
    if "release_decision" not in overrides:
        data["release_decision"] = release_decision(data["skill_version"])
    return QueueRunPayload(**data)


def indexed_message_ids(fake, field: str) -> list[str]:
    return queue._decode_run_index_message_ids(fake.run_index.get(field))


class FakeRedis:
    def __init__(self, raw=None, lengths=None, processing=None, queued=None, meta=None, retry=None, workers=None, lease_timeout=False):
        self.raw = raw
        self.lengths = lengths or {}
        self.processing = processing or []
        self.queued = queued or []
        self.meta = meta or {}
        self.retry = retry or {}
        self.workers = workers or {}
        self.metadata_by_message_id = {}
        self.run_index = {}
        self.order_scores = {}
        self.sequence = 0
        self.fences = {}
        self.fence_ttls = {}
        self.lease_timeout = lease_timeout
        self.pushed = []
        self.left_pushed = []
        self.removed = []
        self.hset_calls = []
        self.hdel_calls = []
        self.lrange_calls = []
        self.eval_calls = []
        self.closed = False

    async def llen(self, key):
        configured = self.lengths.get(key)
        if isinstance(configured, list):
            if len(configured) > 1:
                return configured.pop(0)
            return configured[0]
        if configured is not None:
            return configured
        if key == queue.QUEUE_KEY:
            return len(self.queued)
        if key == queue.PROCESSING_KEY:
            return len(self.processing)
        return 0

    def _is_queued_meta_key(self, key):
        return str(key).endswith(":queued-meta")

    def _is_queued_run_index_key(self, key):
        return str(key).endswith(":queued-run-index")

    def _is_queued_order_key(self, key):
        return str(key).endswith(":queued-order")

    def _is_queued_sequence_key(self, key):
        return str(key).endswith(":queued-sequence")

    async def rpush(self, key, value):
        self.pushed.append((key, value))
        if key == queue.QUEUE_KEY:
            self.queued.append(value)
        return self.lengths.get(key, 0) + len(self.pushed)

    async def lpush(self, key, value):
        self.left_pushed.append((key, value))
        if key == queue.QUEUE_KEY:
            self.queued.insert(0, value)
            return len(self.queued)
        if key == queue.PROCESSING_KEY:
            self.processing.insert(0, value)
            return len(self.processing)
        return len(self.queued)

    async def brpoplpush(self, source, destination, timeout=0):
        self.source = source
        self.destination = destination
        self.timeout = timeout
        if self.lease_timeout:
            raise RedisTimeoutError("Timeout reading from redis:6379")
        raw = self.raw
        if raw is None and self.queued:
            raw = self.queued.pop()
        if raw is not None and destination == queue.PROCESSING_KEY:
            self.processing.append(raw)
        return raw

    async def lrange(self, key, start, end):
        self.lrange_calls.append((key, start, end))
        target = list(self.queued if key == queue.QUEUE_KEY else self.processing)
        length = len(target)
        start_index = start if start >= 0 else max(length + start, 0)
        end_index = end if end >= 0 else length + end
        if end == -1:
            end_index = length - 1
        if start_index >= length or end_index < start_index:
            return []
        return target[start_index : end_index + 1]

    async def lrem(self, key, count, value):
        self.removed.append((key, count, value))
        target = self.queued if key == queue.QUEUE_KEY else self.processing
        before = len(target)
        if count == 0:
            target[:] = [item for item in target if item != value]
        else:
            remaining = abs(count)
            kept = []
            for item in target:
                if item == value and remaining > 0:
                    remaining -= 1
                    continue
                kept.append(item)
            target[:] = kept
        return before - len(target)

    async def hget(self, key, field):
        if key == queue.PROCESSING_META_KEY:
            return self.meta.get(field)
        if key == queue.RETRY_META_KEY:
            return self.retry.get(field)
        if key == queue.WORKER_HEARTBEAT_KEY:
            return self.workers.get(field)
        if self._is_queued_meta_key(key):
            return self.metadata_by_message_id.get(field)
        if self._is_queued_run_index_key(key):
            return self.run_index.get(field)
        return None

    async def hgetall(self, key):
        if key == queue.WORKER_HEARTBEAT_KEY:
            return dict(self.workers)
        if key == queue.PROCESSING_META_KEY:
            return dict(self.meta)
        if key == queue.RETRY_META_KEY:
            return dict(self.retry)
        if self._is_queued_meta_key(key):
            return dict(self.metadata_by_message_id)
        if self._is_queued_run_index_key(key):
            return dict(self.run_index)
        return {}

    async def hscan(self, key, cursor=0, count=None):
        if int(cursor) != 0:
            return 0, {}
        return 0, await self.hgetall(key)

    async def hset(self, key, field, value):
        self.hset_calls.append((key, field, value))
        if key == queue.PROCESSING_META_KEY:
            self.meta[field] = value
        if key == queue.RETRY_META_KEY:
            self.retry[field] = value
        if key == queue.WORKER_HEARTBEAT_KEY:
            self.workers[field] = value
        if self._is_queued_meta_key(key):
            self.metadata_by_message_id[field] = value
        if self._is_queued_run_index_key(key):
            self.run_index[field] = value

    async def hdel(self, key, field):
        self.hdel_calls.append((key, field))
        if key == queue.PROCESSING_META_KEY:
            self.meta.pop(field, None)
        if key == queue.RETRY_META_KEY:
            self.retry.pop(field, None)
        if self._is_queued_meta_key(key):
            self.metadata_by_message_id.pop(field, None)
        if self._is_queued_run_index_key(key):
            self.run_index.pop(field, None)

    async def incr(self, key):
        if self._is_queued_sequence_key(key):
            self.sequence += 1
            return self.sequence
        return 1

    async def zadd(self, key, mapping):
        if self._is_queued_order_key(key):
            self.order_scores.update(mapping)
        return len(mapping)

    async def zrank(self, key, member):
        if not self._is_queued_order_key(key):
            return None
        ordered = sorted(self.order_scores.items(), key=lambda item: (item[1], item[0]))
        for index, (candidate, _score) in enumerate(ordered):
            if candidate == member:
                return index
        return None

    async def zrem(self, key, member):
        if self._is_queued_order_key(key):
            return 1 if self.order_scores.pop(member, None) is not None else 0
        return 0

    async def eval(self, script, numkeys, *keys_and_args):
        self.eval_calls.append((script, numkeys, keys_and_args))
        if "enqueue-run-with-metadata" in script:
            (
                queued_key,
                queued_meta_key,
                queued_run_index_key,
                queued_order_key,
                queued_sequence_key,
                processing_meta_key,
                retry_meta_key,
                fence_key,
            ) = keys_and_args[:numkeys]
            raw, message_id, run_index_field, metadata_json = keys_and_args[numkeys:]
            if fence_key in self.fences:
                return json.dumps({"status": "reconciliation_fenced"})
            existing_message_ids = indexed_message_ids(self, run_index_field)
            if message_id in existing_message_ids and message_id in self.metadata_by_message_id:
                metadata = json.loads(self.metadata_by_message_id[message_id])
                return json.dumps(
                    {
                        "status": "already_enqueued",
                        "position": await self.zrank(queued_order_key, message_id) + 1,
                        "sequence": metadata.get("sequence"),
                    }
                )
            if message_id in self.meta or message_id in self.retry:
                return json.dumps({"status": "already_leased", "position": 0, "sequence": 0})
            message_ids = [candidate for candidate in existing_message_ids if candidate != message_id]
            message_ids.append(message_id)
            self.sequence += 1
            self.queued.append(raw)
            metadata = json.loads(metadata_json)
            metadata["sequence"] = self.sequence
            metadata["raw"] = raw
            if self._is_queued_meta_key(queued_meta_key):
                self.metadata_by_message_id[message_id] = json.dumps(metadata, ensure_ascii=False)
            if self._is_queued_run_index_key(queued_run_index_key):
                self.run_index[run_index_field] = json.dumps(message_ids, ensure_ascii=False)
            if self._is_queued_order_key(queued_order_key):
                self.order_scores[message_id] = self.sequence
            self.pushed.append((queued_key, raw))
            return json.dumps({"status": "enqueued", "position": len(self.queued), "sequence": self.sequence})
        if "remove-queued-with-metadata" in script:
            queued_key, queued_meta_key, queued_run_index_key, queued_order_key = keys_and_args[:numkeys]
            run_index_field, tenant_id, run_id = keys_and_args[numkeys:]
            message_ids = indexed_message_ids(self, run_index_field)
            if not message_ids:
                return json.dumps({"status": "missing_index", "removed": 0})
            removed = 0
            matched = 0
            for message_id in message_ids:
                raw_metadata = self.metadata_by_message_id.get(message_id)
                if not raw_metadata:
                    self.order_scores.pop(message_id, None)
                    continue
                try:
                    metadata = json.loads(raw_metadata)
                except json.JSONDecodeError:
                    self.metadata_by_message_id.pop(message_id, None)
                    self.order_scores.pop(message_id, None)
                    continue
                if metadata.get("tenant_id") != tenant_id or metadata.get("run_id") != run_id:
                    continue
                matched += 1
                raw = metadata.get("raw") or ""
                removed += await self.lrem(queued_key, 0, raw) if raw else 0
                self.metadata_by_message_id.pop(message_id, None)
                self.order_scores.pop(message_id, None)
            self.run_index.pop(run_index_field, None)
            if matched == 0:
                return json.dumps({"status": "missing_metadata", "removed": 0})
            return json.dumps({"status": "removed", "removed": removed})
        if "lease-run-with-quota" in script:
            (
                queued_key,
                processing_key,
                processing_meta_key,
                retry_meta_key,
                worker_heartbeat_key,
                queued_meta_key,
                queued_run_index_key,
                queued_order_key,
                fence_key,
            ) = keys_and_args[:numkeys]
            (
                raw,
                scan_limit,
                absolute_index,
                message_id,
                worker_id,
                now,
                max_processing_runs,
                tenant_processing_limit,
                user_processing_limit,
                tenant_id,
                user_id,
                run_id,
            ) = keys_and_args[numkeys:]
            scan_limit = int(scan_limit)
            absolute_index = int(absolute_index)
            max_processing_runs = int(max_processing_runs)
            tenant_processing_limit = int(tenant_processing_limit)
            user_processing_limit = int(user_processing_limit)
            if fence_key in self.fences:
                return json.dumps({"status": "reconciliation_fenced"})
            if max_processing_runs > 0 and len(self.processing) >= max_processing_runs:
                return json.dumps({"status": "capacity_full"})
            if absolute_index < 0 or absolute_index >= len(self.queued):
                return json.dumps({"status": "conflict"})
            if self.queued[absolute_index] != raw:
                return json.dumps({"status": "conflict"})
            tenant_processing = 0
            user_processing = 0
            for processing_raw in self.processing:
                try:
                    payload = QueueRunPayload.model_validate_json(processing_raw)
                except Exception:
                    continue
                if payload.tenant_id == tenant_id:
                    tenant_processing += 1
                    if payload.user_id == user_id:
                        user_processing += 1
            if tenant_processing_limit > 0 and tenant_processing >= tenant_processing_limit:
                return json.dumps(
                    {
                        "status": "quota_blocked",
                        "tenant_processing": tenant_processing,
                        "user_processing": user_processing,
                    }
                )
            if user_processing_limit > 0 and user_processing >= user_processing_limit:
                return json.dumps(
                    {
                        "status": "quota_blocked",
                        "tenant_processing": tenant_processing,
                        "user_processing": user_processing,
                    }
                )
            retry_meta = self.retry.get(message_id) or self.meta.get(message_id)
            attempts = 1
            if retry_meta:
                try:
                    attempts = int(json.loads(retry_meta).get("attempts", 0)) + 1
                except (TypeError, ValueError, json.JSONDecodeError):
                    attempts = 1
            self.queued.pop(absolute_index)
            if self._is_queued_meta_key(queued_meta_key):
                self.metadata_by_message_id.pop(message_id, None)
            if self._is_queued_run_index_key(queued_run_index_key):
                field = f"{tenant_id}:{run_id}"
                remaining = [candidate for candidate in indexed_message_ids(self, field) if candidate != message_id]
                if remaining:
                    self.run_index[field] = json.dumps(remaining, ensure_ascii=False)
                else:
                    self.run_index.pop(field, None)
            if self._is_queued_order_key(queued_order_key):
                self.order_scores.pop(message_id, None)
            self.processing.insert(0, raw)
            quota_snapshot = {
                "tenant_processing": tenant_processing,
                "tenant_processing_limit": tenant_processing_limit,
                "tenant_processing_saturated": tenant_processing_limit > 0 and tenant_processing >= tenant_processing_limit,
                "user_processing": user_processing,
                "user_processing_limit": user_processing_limit,
                "user_processing_saturated": user_processing_limit > 0 and user_processing >= user_processing_limit,
            }
            meta = {
                "message_id": message_id,
                "raw": raw,
                "attempts": attempts,
                "leased_at": float(now),
                "heartbeat_at": float(now),
                "worker_id": worker_id,
                "run_id": run_id,
                "tenant_id": tenant_id,
                "user_id": user_id,
                "quota_snapshot": quota_snapshot,
            }
            encoded = json.dumps(meta, ensure_ascii=False)
            if processing_meta_key == queue.PROCESSING_META_KEY:
                self.meta[message_id] = encoded
            if retry_meta_key == queue.RETRY_META_KEY:
                self.retry[message_id] = encoded
            if worker_heartbeat_key == queue.WORKER_HEARTBEAT_KEY:
                self.workers[worker_id] = str(float(now))
            return json.dumps(
                {
                    "status": "leased",
                    "attempts": attempts,
                    "tenant_processing": tenant_processing,
                    "user_processing": user_processing,
                }
            )
        if "dead-letter-invalid-quota" in script:
            (
                queued_key,
                processing_meta_key,
                retry_meta_key,
                dead_letter_key,
                queued_meta_key,
                queued_run_index_key,
                queued_order_key,
            ) = keys_and_args[:numkeys]
            raw, scan_limit, absolute_index, message_id, worker_id, now, error_message = keys_and_args[numkeys:]
            scan_limit = int(scan_limit)
            absolute_index = int(absolute_index)
            if absolute_index < 0 or absolute_index >= len(self.queued):
                return json.dumps({"status": "conflict"})
            if self.queued[absolute_index] != raw:
                return json.dumps({"status": "conflict"})
            retry_meta = self.retry.get(message_id) or self.meta.get(message_id)
            attempts = 1
            if retry_meta:
                try:
                    attempts = int(json.loads(retry_meta).get("attempts", 0)) + 1
                except (TypeError, ValueError, json.JSONDecodeError):
                    attempts = 1
            self.queued.pop(absolute_index)
            raw_queued_meta = self.metadata_by_message_id.pop(message_id, None)
            if raw_queued_meta:
                try:
                    queued_meta = json.loads(raw_queued_meta)
                    field = f"{queued_meta.get('tenant_id')}:{queued_meta.get('run_id')}"
                    remaining = [candidate for candidate in indexed_message_ids(self, field) if candidate != message_id]
                    if remaining:
                        self.run_index[field] = json.dumps(remaining, ensure_ascii=False)
                    else:
                        self.run_index.pop(field, None)
                except json.JSONDecodeError:
                    pass
            self.order_scores.pop(message_id, None)
            self.pushed.append(
                (
                    dead_letter_key,
                    json.dumps(
                        {
                            "schema_version": "ai-platform.dead-letter.v1",
                            "error_code": "invalid_queue_payload",
                            "error_message": error_message,
                            "attempts": attempts,
                            "worker_id": worker_id,
                            "raw": raw,
                            "created_at": float(now),
                        },
                        ensure_ascii=False,
                    ),
                )
            )
            self.retry.pop(message_id, None)
            return json.dumps({"status": "dead_lettered", "attempts": attempts})
        if "acquire-run-reconciliation-fence" in script:
            (
                queued_key,
                processing_key,
                queued_meta_key,
                processing_meta_key,
                retry_meta_key,
                worker_heartbeat_key,
                queued_run_index_key,
                fence_key,
            ) = keys_and_args[:numkeys]
            tenant_id, run_id, run_index_field, owner_token, now, worker_ttl, scan_limit, ttl_ms = (
                keys_and_args[numkeys:]
            )
            now = float(now)
            worker_ttl = float(worker_ttl)
            scan_limit = int(scan_limit)
            if fence_key in self.fences:
                return json.dumps({"status": "fenced"})
            if len(self.queued) > scan_limit or len(self.processing) > scan_limit:
                return json.dumps({"status": "inconclusive"})
            for raw in [*self.queued, *self.processing]:
                try:
                    payload = QueueRunPayload.model_validate_json(raw)
                except Exception:
                    return json.dumps({"status": "inconclusive"})
                if payload.tenant_id == tenant_id and payload.run_id == run_id:
                    return json.dumps({"status": "owned"})
            if self.run_index.get(run_index_field):
                return json.dumps({"status": "owned"})
            for raw_metadata in self.metadata_by_message_id.values():
                try:
                    metadata = json.loads(raw_metadata)
                except json.JSONDecodeError:
                    return json.dumps({"status": "inconclusive"})
                if metadata.get("tenant_id") == tenant_id and metadata.get("run_id") == run_id:
                    return json.dumps({"status": "owned"})
            for metadata_store in (self.meta, self.retry):
                if len(metadata_store) > scan_limit:
                    return json.dumps({"status": "inconclusive"})
                for message_id, raw_metadata in metadata_store.items():
                    try:
                        metadata = json.loads(raw_metadata)
                    except json.JSONDecodeError:
                        return json.dumps({"status": "inconclusive"})
                    if metadata.get("tenant_id") != tenant_id or metadata.get("run_id") != run_id:
                        continue
                    worker_id = str(metadata.get("worker_id") or "")
                    if not worker_id:
                        return json.dumps({"status": "inconclusive"})
                    activity_values = []
                    for key in ("heartbeat_at", "leased_at"):
                        if key in metadata:
                            try:
                                activity = float(metadata[key])
                            except (TypeError, ValueError):
                                return json.dumps({"status": "inconclusive"})
                            if activity > now:
                                return json.dumps({"status": "inconclusive"})
                            activity_values.append(activity)
                    if not activity_values:
                        return json.dumps({"status": "inconclusive"})
                    heartbeat = self.workers.get(worker_id)
                    if heartbeat is not None:
                        try:
                            heartbeat_at = float(heartbeat)
                        except (TypeError, ValueError):
                            return json.dumps({"status": "inconclusive"})
                        if heartbeat_at > now:
                            return json.dumps({"status": "inconclusive"})
                    else:
                        heartbeat_at = None
                    if max(activity_values) >= now - worker_ttl and heartbeat_at is not None and now - heartbeat_at <= worker_ttl:
                        raw = metadata.get("raw")
                        if (
                            metadata.get("message_id") != message_id
                            or not isinstance(raw, str)
                            or raw not in self.processing
                        ):
                            return json.dumps({"status": "inconclusive"})
                        try:
                            correlated = QueueRunPayload.model_validate_json(raw)
                        except Exception:
                            return json.dumps({"status": "inconclusive"})
                        if correlated.tenant_id != tenant_id or correlated.run_id != run_id:
                            return json.dumps({"status": "inconclusive"})
                        return json.dumps({"status": "owned"})
            self.fences[fence_key] = owner_token
            self.fence_ttls[fence_key] = int(ttl_ms)
            return json.dumps({"status": "claimed"})
        if "release-run-reconciliation-fence" in script:
            fence_key = keys_and_args[0]
            owner_token = keys_and_args[numkeys]
            if self.fences.get(fence_key) != owner_token:
                return 0
            self.fences.pop(fence_key, None)
            self.fence_ttls.pop(fence_key, None)
            return 1
        if "renew-run-reconciliation-fence" in script:
            fence_key = keys_and_args[0]
            owner_token, ttl_ms = keys_and_args[numkeys:]
            if self.fences.get(fence_key) != owner_token:
                return json.dumps({"status": "owner_lost"})
            self.fence_ttls[fence_key] = int(ttl_ms)
            return json.dumps({"status": "renewed"})
        if "requeue-run-with-fence" in script:
            (
                queued_key,
                processing_key,
                queued_meta_key,
                queued_run_index_key,
                queued_order_key,
                queued_sequence_key,
                retry_meta_key,
                fence_key,
            ) = keys_and_args[:numkeys]
            raw, message_id, run_index_field, metadata_json, retry_metadata_json, remove_processing = (
                keys_and_args[numkeys:]
            )
            if fence_key in self.fences:
                return json.dumps({"status": "reconciliation_fenced"})
            if str(remove_processing) == "1":
                await self.lrem(processing_key, 1, raw)
            self.queued.append(raw)
            self.pushed.append((queued_key, raw))
            self.sequence += 1
            metadata = json.loads(metadata_json)
            metadata["sequence"] = self.sequence
            metadata["raw"] = raw
            self.metadata_by_message_id[message_id] = json.dumps(metadata, ensure_ascii=False)
            self.run_index[run_index_field] = json.dumps([message_id], ensure_ascii=False)
            self.order_scores[message_id] = self.sequence
            if retry_metadata_json:
                self.retry[message_id] = retry_metadata_json
            return json.dumps({"status": "requeued"})
        if "dead-letter-expired-lease-with-fence" in script:
            processing_key, processing_meta_key, retry_meta_key, dead_letter_key, fence_key = keys_and_args[:numkeys]
            raw, message_id, dead_letter_json, remove_processing_meta = keys_and_args[numkeys:]
            if fence_key in self.fences:
                return json.dumps({"status": "reconciliation_fenced"})
            await self.lrem(processing_key, 1, raw)
            if str(remove_processing_meta) == "1":
                await self.hdel(processing_meta_key, message_id)
            await self.rpush(dead_letter_key, dead_letter_json)
            await self.hdel(retry_meta_key, message_id)
            return json.dumps({"status": "dead_lettered"})
        if "record-legacy-lease-with-fence" in script:
            (
                processing_key,
                queued_key,
                processing_meta_key,
                retry_meta_key,
                worker_heartbeat_key,
                queued_meta_key,
                queued_run_index_key,
                queued_order_key,
                queued_sequence_key,
                fence_key,
            ) = keys_and_args[:numkeys]
            raw, message_id, metadata_json, worker_id, now, run_index_field = keys_and_args[numkeys:]
            if fence_key in self.fences:
                await self.lrem(processing_key, 1, raw)
                self.queued.append(raw)
                self.pushed.append((queued_key, raw))
                self.sequence += 1
                metadata = json.loads(metadata_json)
                metadata["sequence"] = self.sequence
                metadata["raw"] = raw
                self.metadata_by_message_id[message_id] = json.dumps(metadata, ensure_ascii=False)
                self.run_index[run_index_field] = json.dumps([message_id], ensure_ascii=False)
                self.order_scores[message_id] = self.sequence
                return json.dumps({"status": "reconciliation_fenced"})
            self.meta[message_id] = metadata_json
            self.retry[message_id] = metadata_json
            self.workers[worker_id] = str(now)
            self.hset_calls.extend(
                [
                    (processing_meta_key, message_id, metadata_json),
                    (retry_meta_key, message_id, metadata_json),
                    (worker_heartbeat_key, worker_id, str(now)),
                ]
            )
            return json.dumps({"status": "leased"})
        if "heartbeat-run-with-fence" in script:
            processing_meta_key, worker_heartbeat_key, fence_key = keys_and_args[:numkeys]
            message_id, worker_id, now = keys_and_args[numkeys:]
            if fence_key in self.fences:
                return json.dumps({"status": "reconciliation_fenced"})
            raw_metadata = self.meta.get(message_id)
            if not raw_metadata:
                return json.dumps({"status": "missing"})
            metadata = json.loads(raw_metadata)
            metadata["heartbeat_at"] = float(now)
            metadata["worker_id"] = worker_id
            self.meta[message_id] = json.dumps(metadata, ensure_ascii=False)
            self.workers[worker_id] = str(now)
            return json.dumps({"status": "heartbeat"})
        return json.dumps({"status": "not_implemented"})

    async def aclose(self):
        self.closed = True


def payload_json():
    return queue_payload().model_dump_json()


async def _async_value(value):
    return value


def _fence_settings(prefix=queue.DEFAULT_QUEUE_KEY_PREFIX):
    return type(
        "Settings",
        (),
        {
            "queue_key_prefix": prefix,
            "worker_heartbeat_ttl_seconds": 60.0,
        },
    )()


@pytest.mark.asyncio
async def test_atomic_fence_blocks_enqueue_lease_and_retry_until_token_release(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr("app.queue.get_redis", lambda: _async_value(fake))
    monkeypatch.setattr("app.queue.get_settings", _fence_settings)
    monkeypatch.setattr("app.queue._now", lambda: 120.0)

    fence = await queue.acquire_run_reconciliation_fence(
        tenant_id="tenant-a",
        run_id="run-fenced",
        scan_limit=10,
        ttl_seconds=300,
        owner_token="token-a",
    )
    assert fence is not None

    fenced_payload = queue_payload(run_id="run-fenced").model_dump()
    with pytest.raises(queue.QueueAdmissionRejected, match="run_reconciliation_in_progress"):
        await queue.enqueue_run_with_metadata(fenced_payload)

    raw = queue_payload(run_id="run-fenced").model_dump_json()
    fake.queued.append(raw)
    leased = await queue.lease_run(
        timeout_seconds=0,
        worker_id="worker-a",
        tenant_processing_limit=1,
        lease_scan_limit=1,
    )
    assert leased is None
    assert raw in fake.queued

    fake.queued.clear()
    fake.raw = raw
    legacy_leased = await queue.lease_run(timeout_seconds=0, worker_id="worker-legacy")
    assert legacy_leased is None
    assert raw in fake.queued
    assert raw not in fake.processing

    fake.queued.clear()
    fake.processing.append(raw)
    requeued = await queue._requeue_run_with_fence(
        fake,
        queue.get_queue_keys(),
        raw=raw,
        retry_metadata={"tenant_id": "tenant-a", "run_id": "run-fenced", "worker_id": "worker-old"},
        remove_processing=True,
    )
    assert requeued is False
    assert raw in fake.processing

    wrong = queue.RunReconciliationFence("tenant-a", "run-fenced", "wrong", fence.fence_key)
    assert await queue.release_run_reconciliation_fence(wrong) is False
    assert fence.fence_key in fake.fences
    assert await queue.release_run_reconciliation_fence(fence) is True
    assert fence.fence_key not in fake.fences


@pytest.mark.asyncio
async def test_reconciliation_fence_renewal_requires_opaque_owner_token_and_replaces_ttl(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr("app.queue.get_redis", lambda: _async_value(fake))
    monkeypatch.setattr("app.queue.get_settings", _fence_settings)
    monkeypatch.setattr("app.queue._now", lambda: 120.0)

    fence = await queue.acquire_run_reconciliation_fence(
        tenant_id="tenant-a",
        run_id="run-renew",
        scan_limit=10,
        ttl_seconds=30,
        owner_token="token-a",
    )

    assert fence is not None
    assert fake.fence_ttls[fence.fence_key] == 30_000
    acquire_script, acquire_key_count, acquire_call = fake.eval_calls[-1]
    assert acquire_script == queue.ACQUIRE_RECONCILIATION_FENCE_SCRIPT
    assert acquire_key_count == 8
    assert acquire_call[7] == fence.fence_key
    assert acquire_call[8:12] == ("tenant-a", "run-renew", "tenant-a:run-renew", "token-a")
    assert await queue.renew_run_reconciliation_fence(fence, ttl_seconds=90) is True
    assert fake.fence_ttls[fence.fence_key] == 90_000
    renew_script, renew_key_count, renew_call = fake.eval_calls[-1]
    assert renew_script == queue.RENEW_RECONCILIATION_FENCE_SCRIPT
    assert renew_key_count == 1
    assert renew_call == (fence.fence_key, "token-a", 90_000)
    wrong = queue.RunReconciliationFence("tenant-a", "run-renew", "token-b", fence.fence_key)
    assert await queue.renew_run_reconciliation_fence(wrong, ttl_seconds=90) is False
    assert fake.fences[fence.fence_key] == "token-a"
    fake.fences.pop(fence.fence_key)
    fake.fence_ttls.pop(fence.fence_key)
    assert await queue.renew_run_reconciliation_fence(fence, ttl_seconds=90) is False


def test_reconciliation_lua_scripts_keep_their_production_key_and_argv_contracts():
    assert "redis.call(\"hdel\", hash_key" not in queue.ACQUIRE_RECONCILIATION_FENCE_SCRIPT
    assert "redis.call(\"set\", fence_key, owner_token, \"NX\", \"PX\", fence_ttl_ms)" in queue.ACQUIRE_RECONCILIATION_FENCE_SCRIPT
    assert "redis.call(\"get\", fence_key) ~= owner_token" in queue.RENEW_RECONCILIATION_FENCE_SCRIPT
    assert "redis.call(\"set\", fence_key, owner_token, \"XX\", \"PX\", fence_ttl_ms)" in queue.RENEW_RECONCILIATION_FENCE_SCRIPT
    assert "KEYS[8]" in queue.ACQUIRE_RECONCILIATION_FENCE_SCRIPT
    assert "ARGV[8]" in queue.ACQUIRE_RECONCILIATION_FENCE_SCRIPT
    assert "KEYS[1]" in queue.RENEW_RECONCILIATION_FENCE_SCRIPT
    assert "ARGV[2]" in queue.RENEW_RECONCILIATION_FENCE_SCRIPT
    assert queue.DEAD_LETTER_EXPIRED_LEASE_WITH_FENCE_SCRIPT.index('redis.call("exists", fence_key)') < queue.DEAD_LETTER_EXPIRED_LEASE_WITH_FENCE_SCRIPT.index('redis.call("lrem", processing_key')
    assert queue.DEAD_LETTER_EXPIRED_LEASE_WITH_FENCE_SCRIPT.index('redis.call("exists", fence_key)') < queue.DEAD_LETTER_EXPIRED_LEASE_WITH_FENCE_SCRIPT.index('redis.call("rpush", dead_letter_key')


@pytest.mark.asyncio
async def test_reconciliation_claim_ignores_stale_reused_worker_metadata_without_deleting_it(monkeypatch):
    raw = queue_payload(run_id="run-stale-meta").model_dump_json()
    message_id = queue.message_id_for_raw(raw)
    fake = FakeRedis(
        meta={
            message_id: json.dumps(
                {
                    "message_id": message_id,
                    "raw": raw,
                    "tenant_id": "tenant-a",
                    "run_id": "run-stale-meta",
                    "worker_id": "reused-worker",
                    "leased_at": 1.0,
                    "heartbeat_at": 1.0,
                }
            )
        },
        workers={"reused-worker": "120.0"},
    )
    monkeypatch.setattr("app.queue.get_redis", lambda: _async_value(fake))
    monkeypatch.setattr("app.queue.get_settings", _fence_settings)
    monkeypatch.setattr("app.queue._now", lambda: 120.0)

    fence = await queue.acquire_run_reconciliation_fence(
        tenant_id="tenant-a",
        run_id="run-stale-meta",
        scan_limit=10,
        ttl_seconds=30,
        owner_token="token-stale",
    )

    assert fence is not None
    assert fake.meta[message_id]
    assert fake.hdel_calls == []


@pytest.mark.asyncio
async def test_reconciliation_claim_fails_closed_for_fresh_uncorrelated_metadata(monkeypatch):
    raw = queue_payload(run_id="run-partial-meta").model_dump_json()
    message_id = queue.message_id_for_raw(raw)
    fake = FakeRedis(
        meta={
            message_id: json.dumps(
                {
                    "message_id": message_id,
                    "raw": raw,
                    "tenant_id": "tenant-a",
                    "run_id": "run-partial-meta",
                    "worker_id": "worker-a",
                    "leased_at": 120.0,
                    "heartbeat_at": 120.0,
                }
            )
        },
        workers={"worker-a": "120.0"},
    )
    monkeypatch.setattr("app.queue.get_redis", lambda: _async_value(fake))
    monkeypatch.setattr("app.queue.get_settings", _fence_settings)
    monkeypatch.setattr("app.queue._now", lambda: 120.0)

    assert await queue.acquire_run_reconciliation_fence(
        tenant_id="tenant-a",
        run_id="run-partial-meta",
        scan_limit=10,
        ttl_seconds=30,
    ) is None
    assert fake.meta[message_id]
    assert fake.hdel_calls == []


@pytest.mark.asyncio
async def test_atomic_fence_fails_closed_for_live_and_partial_legacy_state(monkeypatch):
    raw = queue_payload(run_id="run-live").model_dump_json()
    fake = FakeRedis(queued=[raw])
    monkeypatch.setattr("app.queue.get_redis", lambda: _async_value(fake))
    monkeypatch.setattr("app.queue.get_settings", _fence_settings)
    assert await queue.acquire_run_reconciliation_fence(
        tenant_id="tenant-a", run_id="run-live", scan_limit=10, ttl_seconds=300
    ) is None

    malformed = FakeRedis(queued=["not-json"])
    monkeypatch.setattr("app.queue.get_redis", lambda: _async_value(malformed))
    assert await queue.acquire_run_reconciliation_fence(
        tenant_id="tenant-a", run_id="run-live", scan_limit=10, ttl_seconds=300
    ) is None

    oversized = FakeRedis(queued=[queue_payload(run_id=f"other-{index}").model_dump_json() for index in range(2)])
    monkeypatch.setattr("app.queue.get_redis", lambda: _async_value(oversized))
    assert await queue.acquire_run_reconciliation_fence(
        tenant_id="tenant-a", run_id="run-live", scan_limit=1, ttl_seconds=300
    ) is None


def test_queue_keys_follow_configured_prefix(monkeypatch):
    class Settings:
        queue_key_prefix = "ai-platform:test:runs"

    monkeypatch.setattr("app.queue.get_settings", lambda: Settings())

    keys = queue.get_queue_keys()

    assert keys.queued == "ai-platform:test:runs:queued"
    assert keys.processing == "ai-platform:test:runs:processing"
    assert keys.processing_meta == "ai-platform:test:runs:processing-meta"
    assert keys.retry_meta == "ai-platform:test:runs:retry-meta"
    assert keys.dead_letter == "ai-platform:test:runs:dead-letter"
    assert keys.worker_heartbeat == "ai-platform:test:runs:worker-heartbeat"
    assert keys.queued_meta == "ai-platform:test:runs:queued-meta"
    assert keys.queued_run_index == "ai-platform:test:runs:queued-run-index"
    assert keys.queued_order == "ai-platform:test:runs:queued-order"
    assert keys.queued_sequence == "ai-platform:test:runs:queued-sequence"
    assert keys.reconciliation_fence_prefix == "ai-platform:test:runs:reconciliation-fence"


@pytest.mark.asyncio
async def test_enqueue_run_uses_configured_prefix(monkeypatch):
    class Settings:
        queue_key_prefix = "ai-platform:test:runs"

    fake = FakeRedis()

    async def get_redis():
        return fake

    monkeypatch.setattr("app.queue.get_settings", lambda: Settings())
    monkeypatch.setattr("app.queue.get_redis", get_redis)

    position = await queue.enqueue_run(QueueRunPayload.model_validate_json(payload_json()).model_dump())

    assert fake.pushed[0][0] == "ai-platform:test:runs:queued"
    assert position == 1


@pytest.mark.asyncio
async def test_enqueue_run_writes_indexed_queue_metadata(monkeypatch):
    payload = queue_payload(run_id="run-indexed", tenant_id="tenant-a").model_dump()
    fake = FakeRedis()

    async def get_redis():
        return fake

    monkeypatch.setattr("app.queue.get_redis", get_redis)

    position = await queue.enqueue_run(payload)

    assert position == 1
    assert fake.metadata_by_message_id
    message_id = next(iter(fake.metadata_by_message_id))
    metadata = json.loads(fake.metadata_by_message_id[message_id])
    expected_raw = QueueRunPayload.model_validate(payload).model_dump_json()
    assert metadata["run_id"] == "run-indexed"
    assert metadata["tenant_id"] == "tenant-a"
    assert metadata["raw"] == expected_raw
    assert indexed_message_ids(fake, "tenant-a:run-indexed") == [message_id]
    assert fake.order_scores[message_id] == 1


@pytest.mark.asyncio
async def test_enqueue_run_preserves_multiple_message_ids_for_same_run(monkeypatch):
    first_payload = queue_payload(run_id="run-indexed", tenant_id="tenant-a", input={"mode": "first"}).model_dump()
    second_payload = queue_payload(run_id="run-indexed", tenant_id="tenant-a", input={"mode": "second"}).model_dump()
    fake = FakeRedis()

    async def get_redis():
        return fake

    monkeypatch.setattr("app.queue.get_redis", get_redis)

    await queue.enqueue_run(first_payload)
    await queue.enqueue_run(second_payload)

    first_raw = QueueRunPayload.model_validate(first_payload).model_dump_json()
    second_raw = QueueRunPayload.model_validate(second_payload).model_dump_json()
    assert indexed_message_ids(fake, "tenant-a:run-indexed") == [
        queue.message_id_for_raw(first_raw),
        queue.message_id_for_raw(second_raw),
    ]


@pytest.mark.asyncio
async def test_enqueue_run_with_metadata_returns_trusted_admission_ordinal(monkeypatch):
    first_payload = queue_payload(run_id="run-admit-a", tenant_id="tenant-a").model_dump()
    second_payload = queue_payload(run_id="run-admit-b", tenant_id="tenant-a").model_dump()
    fake = FakeRedis()

    async def get_redis():
        return fake

    monkeypatch.setattr("app.queue.get_redis", get_redis)

    first = await queue.enqueue_run_with_metadata(first_payload)
    second_position = await queue.enqueue_run(second_payload)

    assert first.queue_position == 1
    assert first.queue_admission_ordinal == 1
    assert first.source == "redis_metadata"
    assert first.message_id == queue.message_id_for_raw(
        QueueRunPayload.model_validate(first_payload).model_dump_json()
    )
    assert second_position == 2
    assert fake.closed is True


@pytest.mark.asyncio
async def test_enqueue_run_with_metadata_deduplicates_an_identical_run_admission(monkeypatch):
    payload = queue_payload(run_id="run-deduplicated", tenant_id="tenant-a").model_dump()
    fake = FakeRedis()

    async def get_redis():
        return fake

    monkeypatch.setattr("app.queue.get_redis", get_redis)

    first = await queue.enqueue_run_with_metadata(payload)
    second = await queue.enqueue_run_with_metadata(payload)

    assert first.message_id == second.message_id
    assert first.queue_admission_ordinal == second.queue_admission_ordinal
    assert fake.queued == [QueueRunPayload.model_validate(payload).model_dump_json()]


@pytest.mark.asyncio
async def test_read_queue_admission_recovers_exact_queued_identity_without_reenqueue(monkeypatch):
    payload = queue_payload(run_id="run-readback", tenant_id="tenant-a").model_dump()
    fake = FakeRedis()

    async def get_redis():
        return fake

    monkeypatch.setattr("app.queue.get_redis", get_redis)
    enqueued = await queue.enqueue_run_with_metadata(payload)
    readback = await queue.read_queue_admission(payload)

    assert readback is not None
    assert readback.message_id == enqueued.message_id
    assert readback.queue_position == 1
    assert readback.queue_admission_ordinal == enqueued.queue_admission_ordinal
    assert readback.source == "redis_readback_queued"
    assert len(fake.eval_calls) == 1
    assert fake.lrange_calls == []


@pytest.mark.asyncio
async def test_read_queue_admission_recovers_exact_processing_identity_without_queue_scan(monkeypatch):
    payload = queue_payload(run_id="run-readback-processing", tenant_id="tenant-a").model_dump()
    fake = FakeRedis()
    raw = QueueRunPayload.model_validate(payload).model_dump_json()
    message_id = queue.message_id_for_raw(raw)
    fake.meta[message_id] = json.dumps({"tenant_id": "tenant-a", "run_id": "run-readback-processing"})

    async def get_redis():
        return fake

    monkeypatch.setattr("app.queue.get_redis", get_redis)
    readback = await queue.read_queue_admission(payload)

    assert readback is not None
    assert readback.message_id == message_id
    assert readback.source == "redis_readback_processing"
    assert fake.lrange_calls == []


@pytest.mark.asyncio
async def test_enqueue_run_with_metadata_does_not_requeue_a_processing_identity(monkeypatch):
    payload = queue_payload(run_id="run-processing", tenant_id="tenant-a").model_dump()
    fake = FakeRedis()
    raw = QueueRunPayload.model_validate(payload).model_dump_json()
    message_id = queue.message_id_for_raw(raw)
    fake.meta[message_id] = json.dumps({"tenant_id": "tenant-a", "run_id": "run-processing"})

    async def get_redis():
        return fake

    monkeypatch.setattr("app.queue.get_redis", get_redis)

    admission = await queue.enqueue_run_with_metadata(payload)

    assert admission.source == "redis_existing_lease"
    assert fake.queued == []


@pytest.mark.asyncio
async def test_lease_run_moves_valid_payload_to_processing(monkeypatch):
    fake = FakeRedis(raw=payload_json())

    async def get_redis():
        return fake

    monkeypatch.setattr("app.queue.get_redis", get_redis)

    message = await queue.lease_run(timeout_seconds=3, worker_id="worker-a")

    assert message is not None
    assert message.payload["run_id"] == "run-a"
    assert message.message_id == queue.message_id_for_raw(message.raw)
    assert fake.source == queue.QUEUE_KEY
    assert fake.destination == queue.PROCESSING_KEY
    assert fake.timeout == 3
    assert fake.hset_calls[0][0] == queue.PROCESSING_META_KEY
    assert json.loads(fake.hset_calls[0][2])["worker_id"] == "worker-a"
    assert fake.closed is True


@pytest.mark.asyncio
async def test_remove_queued_run_removes_matching_tenant_run_payloads(monkeypatch):
    raw_a = payload_json()
    raw_b = queue_payload(
        tenant_id="tenant-b",
        workspace_id="workspace-a",
        user_id="user-a",
        session_id="session-a",
        run_id="run-a",
        agent_id="qa-word-review",
        skill_id="qa-file-reviewer",
        file_ids=[],
        input={},
        executor_type="fake",
    ).model_dump_json()
    fake = FakeRedis(queued=[raw_a, raw_b, "not-json"])
    message_id = queue.message_id_for_raw(raw_a)
    fake.metadata_by_message_id[message_id] = json.dumps(
        {
            "run_id": "run-a",
            "tenant_id": "tenant-a",
            "user_id": "user-a",
            "raw": raw_a,
            "sequence": 1,
        }
    )
    fake.run_index["tenant-a:run-a"] = json.dumps([message_id])
    fake.order_scores[message_id] = 1

    async def get_redis():
        return fake

    monkeypatch.setattr("app.queue.get_redis", get_redis)

    removed = await queue.remove_queued_run(tenant_id="tenant-a", run_id="run-a")

    assert removed == 1
    assert fake.removed == [(queue.QUEUE_KEY, 0, raw_a)]
    assert message_id not in fake.metadata_by_message_id
    assert fake.closed is True


@pytest.mark.asyncio
async def test_remove_queued_run_uses_indexed_metadata_without_full_lrange(monkeypatch):
    raw = queue_payload(run_id="run-remove", tenant_id="tenant-a").model_dump_json()
    message_id = queue.message_id_for_raw(raw)
    fake = FakeRedis(queued=[raw])
    fake.metadata_by_message_id[message_id] = json.dumps(
        {
            "run_id": "run-remove",
            "tenant_id": "tenant-a",
            "user_id": "user-a",
            "raw": raw,
            "sequence": 1,
        }
    )
    fake.run_index["tenant-a:run-remove"] = json.dumps([message_id])
    fake.order_scores[message_id] = 1

    async def get_redis():
        return fake

    monkeypatch.setattr("app.queue.get_redis", get_redis)

    removed = await queue.remove_queued_run(tenant_id="tenant-a", run_id="run-remove")

    assert removed == 1
    assert raw not in fake.queued
    assert message_id not in fake.metadata_by_message_id
    assert "tenant-a:run-remove" not in fake.run_index
    assert message_id not in fake.order_scores
    assert (queue.QUEUE_KEY, 0, -1) not in fake.lrange_calls
    assert fake.closed is True


def test_remove_queued_script_removes_all_duplicate_raw_payloads():
    assert 'redis.call("lrem", queued_key, 0, raw)' in queue.REMOVE_QUEUED_WITH_METADATA_SCRIPT
    assert 'redis.call("lrem", queued_key, 1, raw)' not in queue.REMOVE_QUEUED_WITH_METADATA_SCRIPT


@pytest.mark.asyncio
async def test_remove_queued_run_removes_duplicate_indexed_payloads_without_full_lrange(monkeypatch):
    raw = queue_payload(run_id="run-duplicate", tenant_id="tenant-a").model_dump_json()
    message_id = queue.message_id_for_raw(raw)
    fake = FakeRedis(queued=[raw, raw])
    fake.metadata_by_message_id[message_id] = json.dumps(
        {
            "run_id": "run-duplicate",
            "tenant_id": "tenant-a",
            "user_id": "user-a",
            "raw": raw,
            "sequence": 1,
        }
    )
    fake.run_index["tenant-a:run-duplicate"] = json.dumps([message_id])
    fake.order_scores[message_id] = 1

    async def get_redis():
        return fake

    monkeypatch.setattr("app.queue.get_redis", get_redis)

    removed = await queue.remove_queued_run(tenant_id="tenant-a", run_id="run-duplicate")

    assert removed == 2
    assert raw not in fake.queued
    assert message_id not in fake.metadata_by_message_id
    assert "tenant-a:run-duplicate" not in fake.run_index
    assert message_id not in fake.order_scores
    assert (queue.QUEUE_KEY, 0, -1) not in fake.lrange_calls
    assert fake.closed is True


@pytest.mark.asyncio
async def test_remove_queued_run_removes_same_run_with_different_raw_payloads(monkeypatch):
    first = queue_payload(run_id="run-same", tenant_id="tenant-a", input={"mode": "first"}).model_dump_json()
    second = queue_payload(run_id="run-same", tenant_id="tenant-a", input={"mode": "second"}).model_dump_json()
    first_message_id = queue.message_id_for_raw(first)
    second_message_id = queue.message_id_for_raw(second)
    fake = FakeRedis(queued=[first, second])
    for sequence, (raw, message_id) in enumerate([(first, first_message_id), (second, second_message_id)], start=1):
        fake.metadata_by_message_id[message_id] = json.dumps(
            {
                "run_id": "run-same",
                "tenant_id": "tenant-a",
                "user_id": "user-a",
                "raw": raw,
                "sequence": sequence,
            }
        )
        fake.order_scores[message_id] = sequence
    fake.run_index["tenant-a:run-same"] = json.dumps([first_message_id, second_message_id])

    async def get_redis():
        return fake

    monkeypatch.setattr("app.queue.get_redis", get_redis)

    removed = await queue.remove_queued_run(tenant_id="tenant-a", run_id="run-same")

    assert removed == 2
    assert first not in fake.queued
    assert second not in fake.queued
    assert first_message_id not in fake.metadata_by_message_id
    assert second_message_id not in fake.metadata_by_message_id
    assert "tenant-a:run-same" not in fake.run_index
    assert not fake.order_scores
    assert (queue.QUEUE_KEY, 0, -1) not in fake.lrange_calls


@pytest.mark.asyncio
async def test_remove_queued_run_uses_bounded_fallback_for_legacy_unindexed_entries(monkeypatch):
    raw = queue_payload(run_id="run-legacy", tenant_id="tenant-a").model_dump_json()
    fake = FakeRedis(queued=[raw])

    class Settings:
        queue_key_prefix = queue.DEFAULT_QUEUE_KEY_PREFIX
        queue_metadata_fallback_scan_limit = 25

    async def get_redis():
        return fake

    monkeypatch.setattr("app.queue.get_redis", get_redis)
    monkeypatch.setattr("app.queue.get_settings", lambda: Settings())

    removed = await queue.remove_queued_run(tenant_id="tenant-a", run_id="run-legacy")

    assert removed == 1
    assert raw not in fake.queued
    assert (queue.QUEUE_KEY, -25, -1) in fake.lrange_calls
    assert (queue.QUEUE_KEY, 0, -1) not in fake.lrange_calls


@pytest.mark.asyncio
async def test_remove_queued_run_uses_bounded_fallback_when_index_metadata_is_missing(monkeypatch):
    raw = queue_payload(run_id="run-stale-index", tenant_id="tenant-a").model_dump_json()
    message_id = queue.message_id_for_raw(raw)
    fake = FakeRedis(queued=[raw])
    fake.run_index["tenant-a:run-stale-index"] = json.dumps([message_id])
    fake.order_scores[message_id] = 1

    class Settings:
        queue_key_prefix = queue.DEFAULT_QUEUE_KEY_PREFIX
        queue_metadata_fallback_scan_limit = 25

    async def get_redis():
        return fake

    monkeypatch.setattr("app.queue.get_redis", get_redis)
    monkeypatch.setattr("app.queue.get_settings", lambda: Settings())

    removed = await queue.remove_queued_run(tenant_id="tenant-a", run_id="run-stale-index")

    assert removed == 1
    assert raw not in fake.queued
    assert "tenant-a:run-stale-index" not in fake.run_index
    assert message_id not in fake.order_scores
    assert (queue.QUEUE_KEY, -25, -1) in fake.lrange_calls
    assert (queue.QUEUE_KEY, 0, -1) not in fake.lrange_calls


@pytest.mark.asyncio
async def test_lease_run_returns_idle_when_processing_capacity_is_full(monkeypatch):
    fake = FakeRedis(lengths={queue.PROCESSING_KEY: 3}, raw=payload_json())

    async def get_redis():
        return fake

    monkeypatch.setattr("app.queue.get_redis", get_redis)

    message = await queue.lease_run(timeout_seconds=1, worker_id="worker-a", max_processing_runs=3)

    assert message is None
    assert not hasattr(fake, "source")
    assert fake.closed is True


@pytest.mark.asyncio
async def test_lease_run_returns_idle_when_blocking_pop_times_out(monkeypatch):
    fake = FakeRedis(lease_timeout=True)

    async def get_redis():
        return fake

    monkeypatch.setattr("app.queue.get_redis", get_redis)

    message = await queue.lease_run(timeout_seconds=1, worker_id="worker-a")

    assert message is None
    assert fake.source == queue.QUEUE_KEY
    assert fake.destination == queue.PROCESSING_KEY
    assert fake.closed is True


@pytest.mark.asyncio
async def test_lease_run_requeues_message_when_processing_capacity_fills_during_blocking_pop(monkeypatch):
    raw = payload_json()
    fake = FakeRedis(
        raw=raw,
        lengths={queue.PROCESSING_KEY: [0, 4]},
        processing=["processing-a", "processing-b", "processing-c"],
    )

    async def get_redis():
        return fake

    monkeypatch.setattr("app.queue.get_redis", get_redis)

    message = await queue.lease_run(timeout_seconds=1, worker_id="worker-a", max_processing_runs=3)

    assert message is None
    assert fake.source == queue.QUEUE_KEY
    assert (queue.PROCESSING_KEY, 1, raw) in fake.removed
    assert fake.left_pushed == []
    assert (queue.QUEUE_KEY, raw) in fake.pushed
    assert all(call[0] not in {queue.PROCESSING_META_KEY, queue.RETRY_META_KEY} for call in fake.hset_calls)
    message_id = queue.message_id_for_raw(raw)
    assert indexed_message_ids(fake, "tenant-a:run-a") == [message_id]
    assert message_id in fake.metadata_by_message_id
    assert fake.closed is True


@pytest.mark.asyncio
async def test_lease_run_requeues_capacity_race_to_tail_with_matching_metadata_order(monkeypatch):
    older = queue_payload(run_id="run-older").model_dump_json()
    raw = queue_payload(run_id="run-race").model_dump_json()
    older_message_id = queue.message_id_for_raw(older)
    fake = FakeRedis(
        queued=[older, raw],
        lengths={queue.PROCESSING_KEY: [0, 4]},
        processing=["processing-a", "processing-b", "processing-c"],
    )
    fake.sequence = 1
    fake.metadata_by_message_id[older_message_id] = json.dumps(
        {
            "run_id": "run-older",
            "tenant_id": "tenant-a",
            "user_id": "user-a",
            "raw": older,
            "sequence": 1,
        }
    )
    fake.run_index["tenant-a:run-older"] = json.dumps([older_message_id])
    fake.order_scores[older_message_id] = 1

    async def get_redis():
        return fake

    monkeypatch.setattr("app.queue.get_redis", get_redis)

    message = await queue.lease_run(timeout_seconds=1, worker_id="worker-a", max_processing_runs=3)

    assert message is None
    assert fake.queued == [older, raw]
    assert fake.left_pushed == []
    assert (queue.QUEUE_KEY, raw) in fake.pushed
    position = await queue.get_run_queue_position(tenant_id="tenant-a", run_id="run-race")
    assert position == 2
    assert fake.closed is True


@pytest.mark.asyncio
async def test_lease_run_dead_letters_invalid_payload(monkeypatch):
    fake = FakeRedis(raw='{"run_id": "../bad"}')

    async def get_redis():
        return fake

    monkeypatch.setattr("app.queue.get_redis", get_redis)

    message = await queue.lease_run(timeout_seconds=1)

    assert message is None
    assert fake.removed == [(queue.PROCESSING_KEY, 1, '{"run_id": "../bad"}')]
    assert fake.pushed[0][0] == queue.DEAD_LETTER_KEY
    assert json.loads(fake.pushed[0][1])["error_code"] == "invalid_queue_payload"


@pytest.mark.asyncio
async def test_lease_run_legacy_invalid_payload_removes_queued_metadata(monkeypatch):
    raw = '{"run_id": "../bad"}'
    message_id = queue.message_id_for_raw(raw)
    fake = FakeRedis(queued=[raw])
    fake.metadata_by_message_id[message_id] = json.dumps(
        {
            "run_id": "run-bad",
            "tenant_id": "tenant-a",
            "user_id": "user-a",
            "raw": raw,
            "sequence": 1,
        }
    )
    fake.run_index["tenant-a:run-bad"] = json.dumps([message_id])
    fake.order_scores[message_id] = 1

    async def get_redis():
        return fake

    monkeypatch.setattr("app.queue.get_redis", get_redis)

    message = await queue.lease_run(timeout_seconds=1, worker_id="worker-a")

    assert message is None
    assert message_id not in fake.metadata_by_message_id
    assert "tenant-a:run-bad" not in fake.run_index
    assert message_id not in fake.order_scores
    assert fake.pushed[0][0] == queue.DEAD_LETTER_KEY


@pytest.mark.asyncio
async def test_lease_run_skips_saturated_tenant_and_leases_next_candidate(monkeypatch):
    blocked = queue_payload(run_id="run-blocked", tenant_id="tenant-a", user_id="user-a").model_dump_json()
    allowed = queue_payload(run_id="run-allowed", tenant_id="tenant-b", user_id="user-b").model_dump_json()
    active = queue_payload(run_id="run-active", tenant_id="tenant-a", user_id="user-active").model_dump_json()
    fake = FakeRedis(
        queued=[allowed, blocked],
        processing=[active],
    )

    async def get_redis():
        return fake

    monkeypatch.setattr("app.queue.get_redis", get_redis)

    message = await queue.lease_run(
        timeout_seconds=1,
        worker_id="worker-a",
        max_processing_runs=3,
        tenant_processing_limit=1,
        user_processing_limit=0,
        lease_scan_limit=2,
    )

    assert message is not None
    assert message.payload["run_id"] == "run-allowed"
    assert blocked in fake.queued
    assert allowed not in fake.queued
    assert allowed in fake.processing


@pytest.mark.asyncio
async def test_lease_run_skips_saturated_user_and_leases_next_candidate(monkeypatch):
    blocked = queue_payload(run_id="run-blocked", tenant_id="tenant-a", user_id="user-a").model_dump_json()
    allowed = queue_payload(run_id="run-allowed", tenant_id="tenant-a", user_id="user-b").model_dump_json()
    active = queue_payload(run_id="run-active", tenant_id="tenant-a", user_id="user-a").model_dump_json()
    fake = FakeRedis(
        queued=[allowed, blocked],
        processing=[active],
    )

    async def get_redis():
        return fake

    monkeypatch.setattr("app.queue.get_redis", get_redis)

    message = await queue.lease_run(
        timeout_seconds=1,
        worker_id="worker-a",
        max_processing_runs=3,
        tenant_processing_limit=0,
        user_processing_limit=1,
        lease_scan_limit=2,
    )

    assert message is not None
    assert message.payload["run_id"] == "run-allowed"
    assert blocked in fake.queued
    assert allowed in fake.processing


@pytest.mark.asyncio
async def test_lease_run_scans_next_window_when_tail_candidates_are_saturated(monkeypatch):
    blocked = queue_payload(run_id="run-blocked", tenant_id="tenant-a", user_id="user-a").model_dump_json()
    allowed = queue_payload(run_id="run-allowed", tenant_id="tenant-b", user_id="user-b").model_dump_json()
    active = queue_payload(run_id="run-active", tenant_id="tenant-a", user_id="user-active").model_dump_json()
    fake = FakeRedis(
        queued=[allowed, blocked],
        processing=[active],
    )

    async def get_redis():
        return fake

    monkeypatch.setattr("app.queue.get_redis", get_redis)

    message = await queue.lease_run(
        timeout_seconds=1,
        worker_id="worker-a",
        max_processing_runs=3,
        tenant_processing_limit=1,
        user_processing_limit=0,
        lease_scan_limit=1,
    )

    assert message is not None
    assert message.payload["run_id"] == "run-allowed"
    assert fake.queued == [blocked]
    assert allowed in fake.processing
    assert active in fake.processing


@pytest.mark.asyncio
async def test_lease_run_does_not_scan_beyond_fairness_horizon(monkeypatch):
    allowed_outside_horizon = queue_payload(
        run_id="run-outside-horizon",
        tenant_id="tenant-b",
        user_id="user-b",
    ).model_dump_json()
    blocked_items = [
        queue_payload(run_id=f"run-blocked-{index}", tenant_id="tenant-a", user_id=f"user-{index}").model_dump_json()
        for index in range(4)
    ]
    active = queue_payload(run_id="run-active", tenant_id="tenant-a", user_id="user-active").model_dump_json()
    fake = FakeRedis(
        queued=[allowed_outside_horizon, *blocked_items],
        processing=[active],
    )

    async def get_redis():
        return fake

    monkeypatch.setattr("app.queue.get_redis", get_redis)

    message = await queue.lease_run(
        timeout_seconds=1,
        worker_id="worker-a",
        max_processing_runs=3,
        tenant_processing_limit=1,
        user_processing_limit=0,
        lease_scan_limit=1,
    )

    assert message is None
    assert allowed_outside_horizon in fake.queued
    assert allowed_outside_horizon not in fake.processing
    assert fake.lrange_calls == [
        (queue.QUEUE_KEY, 4, 4),
        (queue.QUEUE_KEY, 3, 3),
        (queue.QUEUE_KEY, 2, 2),
        (queue.QUEUE_KEY, 1, 1),
    ]


@pytest.mark.asyncio
async def test_lease_run_dead_letters_invalid_payload_during_bounded_scan(monkeypatch):
    invalid = '{"run_id": "../bad"}'
    allowed = queue_payload(run_id="run-allowed", tenant_id="tenant-b", user_id="user-b").model_dump_json()
    fake = FakeRedis(queued=[allowed, invalid])

    async def get_redis():
        return fake

    monkeypatch.setattr("app.queue.get_redis", get_redis)

    message = await queue.lease_run(
        timeout_seconds=1,
        worker_id="worker-a",
        max_processing_runs=3,
        tenant_processing_limit=1,
        user_processing_limit=1,
        lease_scan_limit=2,
    )

    assert message is not None
    assert message.payload["run_id"] == "run-allowed"
    assert invalid not in fake.queued
    assert fake.pushed[0][0] == queue.DEAD_LETTER_KEY
    assert json.loads(fake.pushed[0][1])["error_code"] == "invalid_queue_payload"


@pytest.mark.asyncio
async def test_lease_run_continues_after_invalid_payload_shrinks_scan_window(monkeypatch):
    older = queue_payload(run_id="run-older", tenant_id="tenant-c", user_id="user-c").model_dump_json()
    allowed = queue_payload(run_id="run-allowed", tenant_id="tenant-b", user_id="user-b").model_dump_json()
    invalid = '{"run_id": "../bad"}'
    fake = FakeRedis(queued=[older, allowed, invalid])

    async def get_redis():
        return fake

    monkeypatch.setattr("app.queue.get_redis", get_redis)

    message = await queue.lease_run(
        timeout_seconds=1,
        worker_id="worker-a",
        max_processing_runs=3,
        tenant_processing_limit=1,
        user_processing_limit=1,
        lease_scan_limit=2,
    )

    assert message is not None
    assert message.payload["run_id"] == "run-allowed"
    assert invalid not in fake.queued
    assert allowed not in fake.queued
    assert older in fake.queued
    assert allowed in fake.processing


def test_quota_lua_attempts_parsing_falls_back_for_malformed_attempts_meta():
    unsafe_expression = 'tonumber(meta["attempts"] or 0) + 1'

    assert unsafe_expression not in queue.LEASE_QUOTA_SCRIPT
    assert unsafe_expression not in queue.DEAD_LETTER_INVALID_QUOTA_SCRIPT
    assert "parsed_attempts" in queue.LEASE_QUOTA_SCRIPT
    assert "parsed_attempts" in queue.DEAD_LETTER_INVALID_QUOTA_SCRIPT


@pytest.mark.asyncio
async def test_lease_run_quota_path_uses_atomic_redis_script(monkeypatch):
    raw = queue_payload(run_id="run-a", tenant_id="tenant-a", user_id="user-a").model_dump_json()
    fake = FakeRedis(queued=[raw])

    async def get_redis():
        return fake

    monkeypatch.setattr("app.queue.get_redis", get_redis)

    await queue.lease_run(
        timeout_seconds=1,
        worker_id="worker-a",
        max_processing_runs=3,
        tenant_processing_limit=1,
        user_processing_limit=1,
        lease_scan_limit=1,
    )

    assert fake.eval_calls, "quota lease must use a Redis script for atomic quota check and move"


@pytest.mark.asyncio
async def test_lease_run_quota_removes_queued_metadata_for_leased_run(monkeypatch):
    raw = queue_payload(run_id="run-indexed", tenant_id="tenant-a", user_id="user-a").model_dump_json()
    message_id = queue.message_id_for_raw(raw)
    fake = FakeRedis(queued=[raw])
    fake.metadata_by_message_id[message_id] = json.dumps(
        {
            "run_id": "run-indexed",
            "tenant_id": "tenant-a",
            "user_id": "user-a",
            "raw": raw,
            "sequence": 1,
        }
    )
    fake.run_index["tenant-a:run-indexed"] = json.dumps([message_id])
    fake.order_scores[message_id] = 1

    async def get_redis():
        return fake

    monkeypatch.setattr("app.queue.get_redis", get_redis)

    message = await queue.lease_run(
        timeout_seconds=1,
        worker_id="worker-a",
        max_processing_runs=3,
        tenant_processing_limit=1,
        user_processing_limit=1,
        lease_scan_limit=1,
    )

    assert message is not None
    assert message.payload["run_id"] == "run-indexed"
    assert message_id not in fake.metadata_by_message_id
    assert "tenant-a:run-indexed" not in fake.run_index
    assert message_id not in fake.order_scores


@pytest.mark.asyncio
async def test_ack_and_fail_remove_from_processing(monkeypatch):
    raw = payload_json()
    message_id = queue.message_id_for_raw(raw)
    fake = FakeRedis(meta={message_id: json.dumps({"attempts": 2})})

    async def get_redis():
        return fake

    monkeypatch.setattr("app.queue.get_redis", get_redis)

    await queue.ack_run("raw-1")
    await queue.fail_leased_run(raw, error_code="boom", error_message="failed")

    assert (queue.PROCESSING_KEY, 1, "raw-1") in fake.removed
    assert (queue.PROCESSING_KEY, 1, raw) in fake.removed
    assert fake.pushed[0][0] == queue.DEAD_LETTER_KEY
    assert json.loads(fake.pushed[0][1])["attempts"] == 2


@pytest.mark.asyncio
async def test_get_queue_status_reports_depths_and_keys(monkeypatch):
    fake = FakeRedis(
        lengths={
            queue.QUEUE_KEY: 7,
            queue.PROCESSING_KEY: 2,
            queue.DEAD_LETTER_KEY: 1,
        },
        workers={"worker-a": "123.0"},
    )

    async def get_redis():
        return fake

    monkeypatch.setattr("app.queue.get_redis", get_redis)
    monkeypatch.setattr("app.queue._now", lambda: 130.0)

    status = await queue.get_queue_status()

    assert status == {
        "depths": {
            "queued": 7,
            "processing": 2,
            "dead_letter": 1,
        },
        "processing_state": {
            "active": 0,
            "stale": 0,
            "reclaimable": 0,
            "missing_metadata": 0,
        },
        "keys": {
            "queued": queue.QUEUE_KEY,
            "processing": queue.PROCESSING_KEY,
            "processing_meta": queue.PROCESSING_META_KEY,
            "retry_meta": queue.RETRY_META_KEY,
            "dead_letter": queue.DEAD_LETTER_KEY,
            "worker_heartbeat": queue.WORKER_HEARTBEAT_KEY,
        },
        "workers": ["worker-a"],
    }
    assert fake.closed is True


@pytest.mark.asyncio
async def test_get_queue_status_filters_stale_worker_heartbeats(monkeypatch):
    class Settings:
        queue_key_prefix = "ai-platform:runs"
        worker_heartbeat_ttl_seconds = 30.0

    fake = FakeRedis(
        lengths={
            queue.QUEUE_KEY: 0,
            queue.PROCESSING_KEY: 0,
            queue.DEAD_LETTER_KEY: 0,
        },
        workers={"fresh": "100.0", "stale": "10.0", "bad": "not-a-time"},
    )

    async def get_redis():
        return fake

    monkeypatch.setattr("app.queue.get_settings", lambda: Settings())
    monkeypatch.setattr("app.queue.get_redis", get_redis)
    monkeypatch.setattr("app.queue._now", lambda: 120.0)

    status = await queue.get_queue_status()

    assert status["workers"] == ["fresh"]


@pytest.mark.asyncio
async def test_get_queue_insight_counts_tenant_queued_and_processing(monkeypatch):
    tenant_a_raw = QueueRunPayload.model_validate_json(payload_json()).model_dump_json()
    tenant_b_raw = queue_payload(
        tenant_id="tenant-b",
        workspace_id="workspace-b",
        user_id="user-b",
        session_id="session-b",
        run_id="run-b",
        agent_id="general-agent",
        skill_id="general-chat",
        file_ids=[],
        input={"message": "hello"},
        executor_type="fake",
    ).model_dump_json()
    tenant_a_message_id = queue.message_id_for_raw(tenant_a_raw)
    tenant_b_message_id = queue.message_id_for_raw(tenant_b_raw)
    fake = FakeRedis(
        lengths={
            queue.QUEUE_KEY: 2,
            queue.PROCESSING_KEY: 2,
            queue.DEAD_LETTER_KEY: 1,
        },
        queued=[tenant_a_raw, tenant_b_raw],
        processing=[tenant_a_raw, tenant_b_raw],
        meta={
            tenant_a_message_id: json.dumps({"tenant_id": "tenant-a", "worker_id": "worker-a"}),
            tenant_b_message_id: json.dumps({"tenant_id": "tenant-b", "worker_id": "worker-b"}),
        },
        workers={"worker-a": "100.0", "worker-b": "101.0"},
    )

    async def get_redis():
        return fake

    monkeypatch.setattr("app.queue.get_redis", get_redis)
    monkeypatch.setattr("app.queue._now", lambda: 130.0)

    insight = await queue.get_queue_insight("tenant-a")

    assert insight == {
        "tenant_id": "tenant-a",
        "reason": "workers_busy",
        "depths": {
            "queued": 2,
            "processing": 2,
            "dead_letter": 1,
            "tenant_queued": 1,
            "tenant_processing": 1,
        },
        "workers": {"active": 2},
        "processing_state": {
            "active": 1,
            "stale": 0,
            "reclaimable": 0,
            "missing_metadata": 0,
        },
        "capacity": {
            "max_active_worker_runs": 3,
            "processing_saturated": False,
            "available_worker_slots": 1,
            "queue_tenant_processing_limit": 0,
            "queue_user_processing_limit": 0,
            "queue_lease_scan_limit": 50,
        },
        "queue_sample": {
            "queued_scan_limit": 500,
            "queued_sampled": 2,
            "queued_sample_complete": True,
        },
        "throttling": {
            "tenant_processing": 1,
            "tenant_processing_limit": 0,
            "tenant_processing_saturated": False,
            "user_processing_limit": 0,
            "users": {},
        },
    }
    assert fake.closed is True


@pytest.mark.asyncio
async def test_get_run_queue_position_returns_one_based_position(monkeypatch):
    run_a_raw = QueueRunPayload.model_validate_json(payload_json()).model_dump_json()
    run_b_raw = queue_payload(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        session_id="session-a",
        run_id="run-b",
        agent_id="general-agent",
        skill_id="general-chat",
        file_ids=[],
        input={"message": "hello"},
        executor_type="fake",
    ).model_dump_json()
    other_tenant_raw = queue_payload(
        tenant_id="tenant-b",
        workspace_id="workspace-b",
        user_id="user-b",
        session_id="session-b",
        run_id="run-c",
        agent_id="general-agent",
        skill_id="general-chat",
        file_ids=[],
        input={"message": "hello"},
        executor_type="fake",
    ).model_dump_json()
    fake = FakeRedis(queued=[run_a_raw, other_tenant_raw, run_b_raw])
    for sequence, raw in enumerate([run_a_raw, other_tenant_raw, run_b_raw], start=1):
        payload = QueueRunPayload.model_validate_json(raw)
        message_id = queue.message_id_for_raw(raw)
        fake.metadata_by_message_id[message_id] = json.dumps(
            {
                "run_id": payload.run_id,
                "tenant_id": payload.tenant_id,
                "user_id": payload.user_id,
                "raw": raw,
                "sequence": sequence,
            }
        )
        fake.run_index[f"{payload.tenant_id}:{payload.run_id}"] = json.dumps([message_id])
        fake.order_scores[message_id] = sequence

    async def get_redis():
        return fake

    monkeypatch.setattr("app.queue.get_redis", get_redis)

    position = await queue.get_run_queue_position(tenant_id="tenant-a", run_id="run-b")

    assert position == 3
    assert fake.closed is True


@pytest.mark.asyncio
async def test_get_run_queue_position_uses_index_without_full_lrange(monkeypatch):
    raw = queue_payload(run_id="run-indexed", tenant_id="tenant-a").model_dump_json()
    message_id = queue.message_id_for_raw(raw)
    fake = FakeRedis(queued=[raw])
    fake.metadata_by_message_id[message_id] = json.dumps(
        {
            "run_id": "run-indexed",
            "tenant_id": "tenant-a",
            "user_id": "user-a",
            "raw": raw,
            "sequence": 1,
        }
    )
    fake.run_index["tenant-a:run-indexed"] = json.dumps([message_id])
    fake.order_scores[message_id] = 1

    async def get_redis():
        return fake

    monkeypatch.setattr("app.queue.get_redis", get_redis)

    position = await queue.get_run_queue_position(tenant_id="tenant-a", run_id="run-indexed")

    assert position == 1
    assert (queue.QUEUE_KEY, 0, -1) not in fake.lrange_calls
    assert fake.closed is True


@pytest.mark.asyncio
async def test_get_run_queue_position_cleans_stale_order_when_metadata_missing(monkeypatch):
    message_id = "stale-message"
    fake = FakeRedis()
    fake.run_index["tenant-a:run-stale"] = json.dumps([message_id])
    fake.order_scores[message_id] = 1

    async def get_redis():
        return fake

    monkeypatch.setattr("app.queue.get_redis", get_redis)

    position = await queue.get_run_queue_position(tenant_id="tenant-a", run_id="run-stale")

    assert position is None
    assert "tenant-a:run-stale" not in fake.run_index
    assert message_id not in fake.order_scores
    assert fake.closed is True


@pytest.mark.asyncio
async def test_get_queue_insight_uses_only_fresh_worker_heartbeats(monkeypatch):
    class Settings:
        queue_key_prefix = "ai-platform:runs"
        max_active_worker_runs = 3
        worker_heartbeat_ttl_seconds = 30.0

    raw = payload_json()
    fake = FakeRedis(
        lengths={
            queue.QUEUE_KEY: 1,
            queue.PROCESSING_KEY: 1,
            queue.DEAD_LETTER_KEY: 0,
        },
        queued=[raw],
        processing=[raw],
        meta={queue.message_id_for_raw(raw): json.dumps({"tenant_id": "tenant-a", "worker_id": "fresh"})},
        workers={"fresh": "100.0", "stale-a": "10.0", "stale-b": "1.0"},
    )

    async def get_redis():
        return fake

    monkeypatch.setattr("app.queue.get_settings", lambda: Settings())
    monkeypatch.setattr("app.queue.get_redis", get_redis)
    monkeypatch.setattr("app.queue._now", lambda: 120.0)

    insight = await queue.get_queue_insight("tenant-a")

    assert insight["workers"]["active"] == 1
    assert insight["reason"] == "workers_busy"


@pytest.mark.asyncio
async def test_get_queue_insight_reports_worker_capacity_full(monkeypatch):
    class Settings:
        queue_key_prefix = "ai-platform:runs"
        max_active_worker_runs = 3

    processing_a_raw = queue_payload(run_id="run-processing-a", tenant_id="tenant-a", user_id="user-a").model_dump_json()
    processing_b_raw = queue_payload(run_id="run-processing-b", tenant_id="tenant-b", user_id="user-b").model_dump_json()
    processing_c_raw = queue_payload(run_id="run-processing-c", tenant_id="tenant-c", user_id="user-c").model_dump_json()
    fake = FakeRedis(
        lengths={
            queue.QUEUE_KEY: 1,
            queue.PROCESSING_KEY: 3,
            queue.DEAD_LETTER_KEY: 0,
        },
        queued=[payload_json()],
        processing=[processing_a_raw, processing_b_raw, processing_c_raw],
        meta={
            queue.message_id_for_raw(processing_a_raw): json.dumps(
                {"tenant_id": "tenant-a", "worker_id": "worker-a", "heartbeat_at": 100.0}
            ),
            queue.message_id_for_raw(processing_b_raw): json.dumps(
                {"tenant_id": "tenant-b", "worker_id": "worker-b", "heartbeat_at": 101.0}
            ),
            queue.message_id_for_raw(processing_c_raw): json.dumps(
                {"tenant_id": "tenant-c", "worker_id": "worker-c", "heartbeat_at": 102.0}
            ),
        },
        workers={"worker-a": "100.0", "worker-b": "101.0", "worker-c": "102.0"},
    )

    async def get_redis():
        return fake

    monkeypatch.setattr("app.queue.get_settings", lambda: Settings())
    monkeypatch.setattr("app.queue.get_redis", get_redis)
    monkeypatch.setattr("app.queue._now", lambda: 120.0)

    insight = await queue.get_queue_insight("tenant-a")

    assert insight["reason"] == "worker_capacity_full"
    assert insight["capacity"] == {
        "max_active_worker_runs": 3,
        "processing_saturated": True,
        "available_worker_slots": 0,
        "queue_tenant_processing_limit": 0,
        "queue_user_processing_limit": 0,
        "queue_lease_scan_limit": 50,
    }


@pytest.mark.asyncio
async def test_get_queue_insight_distinguishes_active_and_reclaimable_processing_leases(monkeypatch):
    class Settings:
        queue_key_prefix = "ai-platform:runs"
        max_active_worker_runs = 3
        worker_heartbeat_ttl_seconds = 30.0
        queue_lease_visibility_timeout_seconds = 60
        queue_tenant_processing_limit = 0
        queue_user_processing_limit = 0
        queue_lease_scan_limit = 50
        queue_insight_scan_limit = 25

    queued_raw = queue_payload(run_id="run-queued", tenant_id="tenant-a", user_id="user-a").model_dump_json()
    active_raw = queue_payload(run_id="run-active", tenant_id="tenant-a", user_id="user-a").model_dump_json()
    reclaimable_raw = queue_payload(run_id="run-reclaimable", tenant_id="tenant-a", user_id="user-b").model_dump_json()
    active_message_id = queue.message_id_for_raw(active_raw)
    reclaimable_message_id = queue.message_id_for_raw(reclaimable_raw)
    fake = FakeRedis(
        lengths={queue.QUEUE_KEY: 1, queue.PROCESSING_KEY: 2, queue.DEAD_LETTER_KEY: 0},
        queued=[queued_raw],
        processing=[active_raw, reclaimable_raw],
        meta={
            active_message_id: json.dumps(
                {
                    "tenant_id": "tenant-a",
                    "user_id": "user-a",
                    "worker_id": "worker-active",
                    "heartbeat_at": 100.0,
                }
            ),
            reclaimable_message_id: json.dumps(
                {
                    "tenant_id": "tenant-a",
                    "user_id": "user-b",
                    "worker_id": "worker-gone",
                    "heartbeat_at": 1.0,
                }
            ),
        },
        workers={"worker-active": "100.0"},
    )

    async def get_redis():
        return fake

    monkeypatch.setattr("app.queue.get_settings", lambda: Settings())
    monkeypatch.setattr("app.queue.get_redis", get_redis)
    monkeypatch.setattr("app.queue._now", lambda: 120.0)

    insight = await queue.get_queue_insight("tenant-a", include_user_breakdown=True)

    assert insight["reason"] == "processing_lease_reclaimable"
    assert insight["processing_state"] == {
        "active": 1,
        "stale": 1,
        "reclaimable": 1,
        "missing_metadata": 0,
    }
    assert insight["depths"]["processing"] == 2
    assert insight["workers"] == {"active": 1}


@pytest.mark.asyncio
async def test_get_queue_insight_reports_quota_throttling(monkeypatch):
    class Settings:
        queue_key_prefix = "ai-platform:runs"
        max_active_worker_runs = 3
        worker_heartbeat_ttl_seconds = 30.0
        queue_tenant_processing_limit = 1
        queue_user_processing_limit = 1
        queue_lease_scan_limit = 25

    raw = queue_payload(run_id="run-a", tenant_id="tenant-a", user_id="user-a").model_dump_json()
    fake = FakeRedis(
        lengths={queue.QUEUE_KEY: 1, queue.PROCESSING_KEY: 1, queue.DEAD_LETTER_KEY: 0},
        queued=[raw],
        processing=[raw],
        meta={"msg-a": json.dumps({"tenant_id": "tenant-a", "user_id": "user-a", "worker_id": "worker-a"})},
        workers={"worker-a": "100.0"},
    )

    async def get_redis():
        return fake

    monkeypatch.setattr("app.queue.get_settings", lambda: Settings())
    monkeypatch.setattr("app.queue.get_redis", get_redis)
    monkeypatch.setattr("app.queue._now", lambda: 120.0)

    insight = await queue.get_queue_insight("tenant-a", include_user_breakdown=True)

    assert insight["reason"] == "tenant_quota_full"
    assert insight["capacity"]["queue_tenant_processing_limit"] == 1
    assert insight["capacity"]["queue_user_processing_limit"] == 1
    assert insight["capacity"]["queue_lease_scan_limit"] == 25
    assert insight["throttling"]["tenant_processing"] == 1
    assert insight["throttling"]["tenant_processing_saturated"] is True
    assert insight["throttling"]["users"]["user-a"]["processing"] == 1
    assert insight["throttling"]["users"]["user-a"]["processing_saturated"] is True


@pytest.mark.asyncio
async def test_get_queue_insight_reports_user_quota_reason(monkeypatch):
    class Settings:
        queue_key_prefix = "ai-platform:runs"
        max_active_worker_runs = 3
        worker_heartbeat_ttl_seconds = 30.0
        queue_tenant_processing_limit = 0
        queue_user_processing_limit = 1
        queue_lease_scan_limit = 25
        queue_insight_scan_limit = 25

    raw = queue_payload(run_id="run-a", tenant_id="tenant-a", user_id="user-a").model_dump_json()
    fake = FakeRedis(
        lengths={queue.QUEUE_KEY: 1, queue.PROCESSING_KEY: 1, queue.DEAD_LETTER_KEY: 0},
        queued=[raw],
        processing=[raw],
        meta={"msg-a": json.dumps({"tenant_id": "tenant-a", "user_id": "user-a", "worker_id": "worker-a"})},
        workers={"worker-a": "100.0"},
    )

    async def get_redis():
        return fake

    monkeypatch.setattr("app.queue.get_settings", lambda: Settings())
    monkeypatch.setattr("app.queue.get_redis", get_redis)
    monkeypatch.setattr("app.queue._now", lambda: 120.0)

    insight = await queue.get_queue_insight("tenant-a", user_id="user-a")

    assert insight["reason"] == "user_quota_full"
    assert insight["throttling"]["current_user"]["queued"] == 1


@pytest.mark.asyncio
async def test_get_queue_insight_admin_reports_user_quota_reason(monkeypatch):
    class Settings:
        queue_key_prefix = "ai-platform:runs"
        max_active_worker_runs = 3
        worker_heartbeat_ttl_seconds = 30.0
        queue_tenant_processing_limit = 0
        queue_user_processing_limit = 1
        queue_lease_scan_limit = 25
        queue_insight_scan_limit = 25

    raw = queue_payload(run_id="run-a", tenant_id="tenant-a", user_id="user-a").model_dump_json()
    fake = FakeRedis(
        lengths={queue.QUEUE_KEY: 1, queue.PROCESSING_KEY: 1, queue.DEAD_LETTER_KEY: 0},
        queued=[raw],
        processing=[raw],
        workers={"worker-a": "100.0"},
    )

    async def get_redis():
        return fake

    monkeypatch.setattr("app.queue.get_settings", lambda: Settings())
    monkeypatch.setattr("app.queue.get_redis", get_redis)
    monkeypatch.setattr("app.queue._now", lambda: 120.0)

    insight = await queue.get_queue_insight("tenant-a", include_user_breakdown=True)

    assert insight["reason"] == "user_quota_full"
    assert insight["throttling"]["users"]["user-a"]["queued"] == 1
    assert insight["throttling"]["users"]["user-a"]["processing_saturated"] is True


@pytest.mark.asyncio
async def test_get_queue_insight_public_projection_hides_other_user_ids(monkeypatch):
    class Settings:
        queue_key_prefix = "ai-platform:runs"
        max_active_worker_runs = 3
        worker_heartbeat_ttl_seconds = 30.0
        queue_tenant_processing_limit = 0
        queue_user_processing_limit = 1
        queue_lease_scan_limit = 25
        queue_insight_scan_limit = 25

    own_raw = queue_payload(run_id="run-own", tenant_id="tenant-a", user_id="user-a").model_dump_json()
    other_raw = queue_payload(run_id="run-other", tenant_id="tenant-a", user_id="user-b").model_dump_json()
    fake = FakeRedis(
        lengths={queue.QUEUE_KEY: 2, queue.PROCESSING_KEY: 1, queue.DEAD_LETTER_KEY: 0},
        queued=[own_raw, other_raw],
        processing=[other_raw],
        workers={"worker-b": "100.0"},
    )

    async def get_redis():
        return fake

    monkeypatch.setattr("app.queue.get_settings", lambda: Settings())
    monkeypatch.setattr("app.queue.get_redis", get_redis)
    monkeypatch.setattr("app.queue._now", lambda: 120.0)

    insight = await queue.get_queue_insight("tenant-a", user_id="user-a")

    assert insight["throttling"]["users"] == {}
    assert insight["throttling"]["current_user"] == {
        "queued": 1,
        "processing": 0,
        "processing_saturated": False,
    }
    assert "user-b" not in json.dumps(insight, ensure_ascii=False)


@pytest.mark.asyncio
async def test_lease_run_quota_ignores_stale_processing_meta(monkeypatch):
    raw = queue_payload(run_id="run-a", tenant_id="tenant-a", user_id="user-a").model_dump_json()
    fake = FakeRedis(
        queued=[raw],
        meta={"stale": json.dumps({"tenant_id": "tenant-a", "user_id": "user-a", "worker_id": "gone"})},
    )

    async def get_redis():
        return fake

    monkeypatch.setattr("app.queue.get_redis", get_redis)

    message = await queue.lease_run(
        timeout_seconds=1,
        worker_id="worker-a",
        max_processing_runs=3,
        tenant_processing_limit=1,
        user_processing_limit=1,
        lease_scan_limit=1,
    )

    assert message is not None
    assert message.payload["run_id"] == "run-a"
    assert fake.processing == [raw]


@pytest.mark.asyncio
async def test_get_queue_insight_uses_bounded_queued_scan(monkeypatch):
    class Settings:
        queue_key_prefix = "ai-platform:runs"
        max_active_worker_runs = 3
        worker_heartbeat_ttl_seconds = 30.0
        queue_tenant_processing_limit = 0
        queue_user_processing_limit = 0
        queue_lease_scan_limit = 50
        queue_insight_scan_limit = 2

    queued = [
        queue_payload(run_id=f"run-{index}", tenant_id="tenant-a", user_id="user-a").model_dump_json()
        for index in range(5)
    ]
    fake = FakeRedis(
        lengths={queue.QUEUE_KEY: 5, queue.PROCESSING_KEY: 0, queue.DEAD_LETTER_KEY: 0},
        queued=queued,
    )

    async def get_redis():
        return fake

    monkeypatch.setattr("app.queue.get_settings", lambda: Settings())
    monkeypatch.setattr("app.queue.get_redis", get_redis)

    insight = await queue.get_queue_insight("tenant-a")

    queued_lrange_calls = [call for call in fake.lrange_calls if call[0] == queue.QUEUE_KEY]
    assert queued_lrange_calls == [(queue.QUEUE_KEY, -2, -1)]
    assert insight["queue_sample"] == {
        "queued_scan_limit": 2,
        "queued_sampled": 2,
        "queued_sample_complete": False,
    }


@pytest.mark.asyncio
async def test_get_queue_insight_skips_malformed_entries(monkeypatch):
    fake = FakeRedis(
        lengths={
            queue.QUEUE_KEY: 1,
            queue.PROCESSING_KEY: 1,
            queue.DEAD_LETTER_KEY: 0,
        },
        queued=["not-json"],
        meta={"bad-meta": "not-json"},
        workers={},
    )

    async def get_redis():
        return fake

    monkeypatch.setattr("app.queue.get_redis", get_redis)

    insight = await queue.get_queue_insight("tenant-a")

    assert insight["depths"]["queued"] == 1
    assert insight["depths"]["processing"] == 1
    assert insight["depths"]["tenant_queued"] == 0
    assert insight["depths"]["tenant_processing"] == 0
    assert insight["reason"] == "queued_behind_existing_work"


@pytest.mark.asyncio
async def test_get_queue_insight_reports_worker_available_for_empty_queue(monkeypatch):
    fake = FakeRedis()

    async def get_redis():
        return fake

    monkeypatch.setattr("app.queue.get_redis", get_redis)

    insight = await queue.get_queue_insight("tenant-a")

    assert insight["depths"]["queued"] == 0
    assert insight["depths"]["processing"] == 0
    assert insight["reason"] == "worker_available"


@pytest.mark.asyncio
async def test_heartbeat_updates_processing_meta_and_worker(monkeypatch):
    raw = payload_json()
    message_id = queue.message_id_for_raw(raw)
    fake = FakeRedis(meta={message_id: json.dumps({"attempts": 1, "worker_id": "old"})})

    async def get_redis():
        return fake

    monkeypatch.setattr("app.queue.get_redis", get_redis)
    monkeypatch.setattr("app.queue._now", lambda: 100.0)

    await queue.heartbeat_run(message_id, worker_id="worker-a")

    updated_meta = json.loads(fake.meta[message_id])
    assert updated_meta["heartbeat_at"] == 100.0
    assert updated_meta["worker_id"] == "worker-a"
    assert fake.workers["worker-a"] == "100.0"


@pytest.mark.asyncio
async def test_reclaim_expired_lease_requeues_before_max_attempts(monkeypatch):
    raw = payload_json()
    message_id = queue.message_id_for_raw(raw)
    fake = FakeRedis(
        processing=[raw],
        meta={message_id: json.dumps({"attempts": 1, "heartbeat_at": 1.0, "worker_id": "worker-a"})},
    )

    async def get_redis():
        return fake

    monkeypatch.setattr("app.queue.get_redis", get_redis)

    result = await queue.reclaim_expired_leases(visibility_timeout_seconds=10, max_attempts=3, now=20.0)

    assert result == {"reclaimed": 1, "dead_lettered": 0}
    assert (queue.PROCESSING_KEY, 1, raw) in fake.removed
    assert (queue.QUEUE_KEY, raw) in fake.pushed
    assert (queue.PROCESSING_META_KEY, message_id) in fake.hdel_calls
    assert indexed_message_ids(fake, "tenant-a:run-a") == [message_id]
    assert message_id in fake.metadata_by_message_id
    position = await queue.get_run_queue_position(tenant_id="tenant-a", run_id="run-a")
    assert position == 1


@pytest.mark.asyncio
async def test_reclaimed_message_preserves_attempts_until_dead_letter(monkeypatch):
    raw = payload_json()
    message_id = queue.message_id_for_raw(raw)
    fake = FakeRedis(
        processing=[raw],
        meta={message_id: json.dumps({"attempts": 1, "heartbeat_at": 1.0, "worker_id": "worker-a"})},
        retry={},
    )

    async def get_redis():
        return fake

    monkeypatch.setattr("app.queue.get_redis", get_redis)
    monkeypatch.setattr("app.queue._now", lambda: 25.0)

    first_reclaim = await queue.reclaim_expired_leases(
        visibility_timeout_seconds=10,
        max_attempts=2,
        now=20.0,
    )

    assert first_reclaim == {"reclaimed": 1, "dead_lettered": 0}
    assert json.loads(fake.retry[message_id])["attempts"] == 1

    message = await queue.lease_run(timeout_seconds=1, worker_id="worker-b")

    assert message is not None
    assert json.loads(fake.meta[message_id])["attempts"] == 2

    second_reclaim = await queue.reclaim_expired_leases(
        visibility_timeout_seconds=10,
        max_attempts=2,
        now=40.0,
    )

    assert second_reclaim == {"reclaimed": 0, "dead_lettered": 1}
    assert fake.pushed[-1][0] == queue.DEAD_LETTER_KEY
    dead_letter = json.loads(fake.pushed[-1][1])
    assert dead_letter["attempts"] == 2
    assert dead_letter["error_code"] == "lease_expired_max_attempts"
    assert (queue.RETRY_META_KEY, message_id) in fake.hdel_calls


@pytest.mark.asyncio
async def test_reclaim_missing_processing_meta_counts_retry_until_dead_letter(monkeypatch):
    raw = payload_json()
    message_id = queue.message_id_for_raw(raw)
    fake = FakeRedis(
        processing=[raw],
        retry={message_id: json.dumps({"attempts": 1, "worker_id": "worker-a"})},
    )

    async def get_redis():
        return fake

    monkeypatch.setattr("app.queue.get_redis", get_redis)

    result = await queue.reclaim_expired_leases(visibility_timeout_seconds=10, max_attempts=2, now=20.0)

    assert result == {"reclaimed": 0, "dead_lettered": 1}
    assert fake.pushed[-1][0] == queue.DEAD_LETTER_KEY
    dead_letter = json.loads(fake.pushed[-1][1])
    assert dead_letter["attempts"] == 2
    assert dead_letter["error_code"] == "lease_expired_max_attempts"
    assert (queue.RETRY_META_KEY, message_id) in fake.hdel_calls


@pytest.mark.asyncio
async def test_reclaim_expired_lease_dead_letters_after_max_attempts(monkeypatch):
    raw = payload_json()
    message_id = queue.message_id_for_raw(raw)
    fake = FakeRedis(
        processing=[raw],
        meta={message_id: json.dumps({"attempts": 3, "heartbeat_at": 1.0, "worker_id": "worker-a"})},
    )

    async def get_redis():
        return fake

    monkeypatch.setattr("app.queue.get_redis", get_redis)

    result = await queue.reclaim_expired_leases(visibility_timeout_seconds=10, max_attempts=3, now=20.0)

    assert result == {"reclaimed": 0, "dead_lettered": 1}
    assert fake.pushed[0][0] == queue.DEAD_LETTER_KEY
    assert json.loads(fake.pushed[0][1])["error_code"] == "lease_expired_max_attempts"


@pytest.mark.asyncio
async def test_reclaim_expired_max_attempts_defers_metadata_present_run_with_live_fence(monkeypatch):
    raw = payload_json()
    message_id = queue.message_id_for_raw(raw)
    fake = FakeRedis(
        processing=[raw],
        meta={message_id: json.dumps({"attempts": 3, "heartbeat_at": 1.0, "worker_id": "worker-a"})},
    )
    monkeypatch.setattr("app.queue.get_redis", lambda: _async_value(fake))

    fence_key = queue.reconciliation_fence_key(tenant_id="tenant-a", run_id="run-a")
    fake.fences[fence_key] = "opaque-token-with-active-ttl"

    result = await queue.reclaim_expired_leases(visibility_timeout_seconds=10, max_attempts=3, now=20.0)

    assert result == {"reclaimed": 0, "dead_lettered": 0}
    assert fake.processing == [raw]
    assert fake.meta[message_id]
    assert fake.pushed == []
    assert fake.removed == []
    assert fake.hdel_calls == []
    script, key_count, call = fake.eval_calls[-1]
    assert script == queue.DEAD_LETTER_EXPIRED_LEASE_WITH_FENCE_SCRIPT
    assert key_count == 5
    assert call[4] == fence_key


@pytest.mark.asyncio
async def test_reclaim_expired_max_attempts_defers_missing_metadata_run_with_live_fence(monkeypatch):
    raw = payload_json()
    message_id = queue.message_id_for_raw(raw)
    fake = FakeRedis(
        processing=[raw],
        retry={message_id: json.dumps({"attempts": 2, "worker_id": "worker-a"})},
    )
    monkeypatch.setattr("app.queue.get_redis", lambda: _async_value(fake))

    fence_key = queue.reconciliation_fence_key(tenant_id="tenant-a", run_id="run-a")
    fake.fences[fence_key] = "opaque-token-with-active-ttl"

    result = await queue.reclaim_expired_leases(visibility_timeout_seconds=10, max_attempts=3, now=20.0)

    assert result == {"reclaimed": 0, "dead_lettered": 0}
    assert fake.processing == [raw]
    assert fake.retry[message_id]
    assert fake.pushed == []
    assert fake.removed == []
    assert fake.hdel_calls == []


@pytest.mark.asyncio
async def test_live_fence_keeps_heartbeat_and_max_attempt_reclaim_from_racing(monkeypatch):
    raw = payload_json()
    message_id = queue.message_id_for_raw(raw)
    original_meta = json.dumps(
        {
            "message_id": message_id,
            "raw": raw,
            "attempts": 3,
            "heartbeat_at": 1.0,
            "leased_at": 1.0,
            "worker_id": "worker-a",
            "tenant_id": "tenant-a",
            "run_id": "run-a",
        }
    )
    fake = FakeRedis(processing=[raw], meta={message_id: original_meta})
    monkeypatch.setattr("app.queue.get_redis", lambda: _async_value(fake))
    monkeypatch.setattr("app.queue._now", lambda: 20.0)

    fence_key = queue.reconciliation_fence_key(tenant_id="tenant-a", run_id="run-a")
    fake.fences[fence_key] = "owner-token"
    fake.fence_ttls[fence_key] = 30_000

    await queue.heartbeat_run(message_id, worker_id="worker-new")
    result = await queue.reclaim_expired_leases(visibility_timeout_seconds=10, max_attempts=3, now=20.0)

    assert fake.meta[message_id] == original_meta
    assert result == {"reclaimed": 0, "dead_lettered": 0}
    assert fake.processing == [raw]
    assert fake.pushed == []


@pytest.mark.asyncio
async def test_reclaim_invalid_missing_metadata_keeps_no_fence_dead_letter_semantics(monkeypatch):
    raw = "not-json"
    fake = FakeRedis(processing=[raw])
    monkeypatch.setattr("app.queue.get_redis", lambda: _async_value(fake))

    result = await queue.reclaim_expired_leases(visibility_timeout_seconds=10, max_attempts=1, now=20.0)

    assert result == {"reclaimed": 0, "dead_lettered": 1}
    assert fake.processing == []
    assert fake.pushed[0][0] == queue.DEAD_LETTER_KEY
    assert json.loads(fake.pushed[0][1])["raw"] == raw


def test_queue_payload_rejects_missing_executor_type():
    try:
        QueueRunPayload.model_validate(
            {
                "tenant_id": "default",
                "workspace_id": "default",
                "user_id": "user_1",
                "session_id": "ses_1",
                "run_id": "run_1",
                "agent_id": "translate",
                "skill_id": "baoyu-translate",
            }
        )
    except ValidationError as exc:
        assert "executor_type" in str(exc)
    else:
        raise AssertionError("ValidationError expected")


def test_queue_payload_rejects_missing_user_id():
    try:
        QueueRunPayload.model_validate(
            {
                "tenant_id": "default",
                "workspace_id": "default",
                "session_id": "ses_1",
                "run_id": "run_1",
                "agent_id": "translate",
                "skill_id": "baoyu-translate",
                "executor_type": "fake",
            }
        )
    except ValidationError as exc:
        assert "user_id" in str(exc)
    else:
        raise AssertionError("ValidationError expected")
