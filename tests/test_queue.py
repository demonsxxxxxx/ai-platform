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


class FakeRedis:
    def __init__(self, raw=None, lengths=None, processing=None, queued=None, meta=None, retry=None, workers=None, lease_timeout=False):
        self.raw = raw
        self.lengths = lengths or {}
        self.processing = processing or []
        self.queued = queued or []
        self.meta = meta or {}
        self.retry = retry or {}
        self.workers = workers or {}
        self.lease_timeout = lease_timeout
        self.pushed = []
        self.left_pushed = []
        self.removed = []
        self.hset_calls = []
        self.hdel_calls = []
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
        if key == queue.QUEUE_KEY:
            return list(self.queued)
        return list(self.processing)

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
        return None

    async def hgetall(self, key):
        if key == queue.WORKER_HEARTBEAT_KEY:
            return dict(self.workers)
        if key == queue.PROCESSING_META_KEY:
            return dict(self.meta)
        if key == queue.RETRY_META_KEY:
            return dict(self.retry)
        return {}

    async def hset(self, key, field, value):
        self.hset_calls.append((key, field, value))
        if key == queue.PROCESSING_META_KEY:
            self.meta[field] = value
        if key == queue.RETRY_META_KEY:
            self.retry[field] = value
        if key == queue.WORKER_HEARTBEAT_KEY:
            self.workers[field] = value

    async def hdel(self, key, field):
        self.hdel_calls.append((key, field))
        if key == queue.PROCESSING_META_KEY:
            self.meta.pop(field, None)
        if key == queue.RETRY_META_KEY:
            self.retry.pop(field, None)

    async def aclose(self):
        self.closed = True


def payload_json():
    return queue_payload().model_dump_json()


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

    async def get_redis():
        return fake

    monkeypatch.setattr("app.queue.get_redis", get_redis)

    removed = await queue.remove_queued_run(tenant_id="tenant-a", run_id="run-a")

    assert removed == 1
    assert fake.removed == [(queue.QUEUE_KEY, 0, raw_a)]
    assert fake.closed is True


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
    assert fake.left_pushed == [(queue.QUEUE_KEY, raw)]
    assert fake.hset_calls == []
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
        "capacity": {
            "max_active_worker_runs": 3,
            "processing_saturated": False,
            "available_worker_slots": 1,
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

    async def get_redis():
        return fake

    monkeypatch.setattr("app.queue.get_redis", get_redis)

    position = await queue.get_run_queue_position(tenant_id="tenant-a", run_id="run-b")

    assert position == 3
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

    fake = FakeRedis(
        lengths={
            queue.QUEUE_KEY: 1,
            queue.PROCESSING_KEY: 3,
            queue.DEAD_LETTER_KEY: 0,
        },
        queued=[payload_json()],
        meta={
            "msg-a": json.dumps({"tenant_id": "tenant-a", "worker_id": "worker-a"}),
            "msg-b": json.dumps({"tenant_id": "tenant-b", "worker_id": "worker-b"}),
            "msg-c": json.dumps({"tenant_id": "tenant-c", "worker_id": "worker-c"}),
        },
        workers={"worker-a": "100.0", "worker-b": "101.0", "worker-c": "102.0"},
    )

    async def get_redis():
        return fake

    monkeypatch.setattr("app.queue.get_settings", lambda: Settings())
    monkeypatch.setattr("app.queue.get_redis", get_redis)

    insight = await queue.get_queue_insight("tenant-a")

    assert insight["reason"] == "worker_capacity_full"
    assert insight["capacity"] == {
        "max_active_worker_runs": 3,
        "processing_saturated": True,
        "available_worker_slots": 0,
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
