from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from hashlib import sha256
import inspect
import json
from typing import Any

import pytest

from app.execution.application.run_delivery_intents import (
    FrozenQueueMessage,
    QueueDeliveryError,
    QueueDeliveryReceipt,
    RunDeliveryIntents,
)
import app.execution.application.run_delivery_intents as delivery_module
from app.execution.infrastructure.run_delivery_queue import RedisRunDeliveryQueue


CLAIM = "123e4567-e89b-12d3-a456-426614174000"


def intent_record(
    *,
    run_id: str = "run",
    state: str = "claimed",
    claim_token: str | None = CLAIM,
    payload_json: str | None = None,
    receipt: QueueDeliveryReceipt | None = None,
) -> dict[str, Any]:
    frozen = payload_json or json.dumps(
        {"tenant_id": "tenant", "run_id": run_id, "profile_revision": "old"},
        sort_keys=True,
    )
    message_id = sha256(frozen.encode()).hexdigest()
    return {
        "tenant_id": "tenant",
        "run_id": run_id,
        "session_id": "session",
        "source": "create_run",
        "submission_id": None,
        "user_message_id": None,
        "queue_payload_json": frozen,
        "queue_message_id": message_id,
        "payload_sha256": message_id,
        "state": state,
        "claim_token": claim_token,
        "attempt_count": 1,
        "queue_position": receipt.queue_position if receipt else None,
        "queue_admission_ordinal": receipt.queue_admission_ordinal if receipt else None,
        "queue_admission_source": receipt.source if receipt else None,
    }


@asynccontextmanager
async def transaction():
    yield object()


class Store:
    def __init__(
        self,
        *,
        claims: list[dict[str, Any]] | None = None,
        current: bool = True,
        receipt_written: bool = True,
        read_record: dict[str, Any] | None = None,
    ):
        self.claims = list(claims or [])
        self.current = current
        self.receipt_written = receipt_written
        self.read_record = read_record
        self.suppressed: list[tuple[str, str]] = []
        self.rescheduled: list[tuple[str, int, str]] = []
        self.receipt_intents: list[str] = []
        self.historical_candidates: list[dict[str, Any]] = []
        self.inserted: list[dict[str, Any]] = []
        self.historical_limits: list[int] = []

    async def lock_historical_backfill(self, connection):
        return None

    async def list_historical_candidates(self, connection, *, limit):
        self.historical_limits.append(limit)
        candidates = self.historical_candidates[:limit]
        self.historical_candidates = self.historical_candidates[limit:]
        return candidates

    async def insert(self, connection, **kwargs):
        self.inserted.append(dict(kwargs))
        return dict(kwargs)

    async def claim_due(self, connection, **kwargs):
        return self.claims

    async def get(self, connection, *, tenant_id, run_id):
        if self.read_record is not None:
            return self.read_record
        return self.claims[0] if self.claims else None

    async def claim_is_current(self, connection, intent, *, for_enqueue=True):
        return self.current

    async def record_receipt(self, connection, *, intent, receipt):
        self.receipt_intents.append(intent.payload_json)
        return self.receipt_written

    async def reschedule(self, connection, *, intent, retry_after_seconds, error_code):
        self.rescheduled.append((intent.payload_json, retry_after_seconds, error_code))
        return True

    async def suppress(self, connection, *, intent, reason):
        self.suppressed.append((intent.run_id, reason))
        return True


class Queue:
    def __init__(
        self, *, readback_result=None, enqueue_result=None, readback_error=None
    ):
        self.readback_result = readback_result
        self.enqueue_result = enqueue_result
        self.readback_error = readback_error
        self.readbacks: list[tuple[str, str]] = []
        self.enqueues: list[tuple[str, str]] = []
        self.active = 0
        self.max_active = 0

    async def _active(self, awaitable):
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            return await awaitable
        finally:
            self.active -= 1

    async def readback(self, payload_json, *, message_id):
        self.readbacks.append((payload_json, message_id))

        async def operation():
            if self.readback_error:
                raise self.readback_error
            result = (
                self.readback_result()
                if callable(self.readback_result)
                else self.readback_result
            )
            return await result if inspect.isawaitable(result) else result

        return await self._active(operation())

    async def enqueue(self, payload_json, *, message_id):
        self.enqueues.append((payload_json, message_id))

        async def operation():
            if callable(self.enqueue_result):
                result = self.enqueue_result(payload_json, message_id)
            elif self.enqueue_result is None:
                result = QueueDeliveryReceipt(1, 1, message_id, "redis_metadata")
            else:
                result = self.enqueue_result
            return await result if inspect.isawaitable(result) else result

        return await self._active(operation())


def service(store: Store, queue: Queue, *, batch_size: int = 25) -> RunDeliveryIntents:
    async def failure_owner(*_args, **_kwargs):
        return True

    return RunDeliveryIntents(
        enabled=True,
        store=store,
        codec=object(),
        queue=queue,
        transaction=transaction,
        scan_batch_size=batch_size,
        claim_ttl_seconds=60,
        retry_base_seconds=5,
        retry_cap_seconds=60,
        permanent_rejection_owner=failure_owner,
    )


class HistoricalCodec:
    def freeze(self, payload):
        payload_json = json.dumps(dict(payload), sort_keys=True, separators=(",", ":"))
        return FrozenQueueMessage(payload_json, sha256(payload_json.encode()).hexdigest())


def historical_candidate(*, source: str = "chat_stream") -> dict[str, Any]:
    run_id = "run-history"
    input_json = {
        "file_ids": [],
        "input": {"message": "from persisted input"},
        "executor_type": "harness-chat",
        "execution_kind": "harness_chat",
        "skill_version": None,
        "release_decision": {},
        "skill_manifests": [],
        "context_snapshot_id": "ctx-history",
        "context_snapshot": {
            "context_snapshot_id": "ctx-history",
            "source": source,
        },
        "model_id": "model-frozen",
        "model_value": "provider/frozen",
        "schema_version": "ai-platform.run-payload.v2",
        "agent_profile": {
            "agent_id": "agent-a",
            "revision": 4,
            "content_hash": "old-profile-hash",
        },
    }
    candidate = {
        "tenant_id": "tenant-a",
        "workspace_id": "workspace-a",
        "user_id": "user-a",
        "session_id": "session-a",
        "run_id": run_id,
        "agent_id": "agent-a",
        "execution_kind": "harness_chat",
        "skill_id": None,
        "input_json": input_json,
        "context_snapshot_id": "ctx-history",
        "admitted_agent_profile_revision": 4,
        "admitted_agent_profile_hash": "old-profile-hash",
        "copied_from_run_id": None,
        "chat_submission_ids": ["submission-history"],
        "chat_submission_records": [
            {
                "submission_id": "submission-history",
                "tenant_id": "tenant-a",
                "user_id": "user-a",
                "session_id": "session-a",
                "run_id": run_id,
                "state": "accepted_pending_enqueue",
                "queue_message_id": None,
            }
        ],
        "user_message_ids": ["message-history"],
        "control_operations": [],
    }
    if source in {"retry_run", "resume_run"}:
        candidate["copied_from_run_id"] = "run-parent"
        candidate["chat_submission_ids"] = []
        candidate["chat_submission_records"] = []
        candidate["user_message_ids"] = []
        action = source.removesuffix("_run")
        candidate["control_operations"] = [
            {
                "action": action,
                "operation_id": "123e4567-e89b-42d3-a456-426614174000",
                "child_run_id": run_id,
            }
        ]
    return candidate


@pytest.mark.asyncio
async def test_historical_chat_backfill_uses_frozen_input_and_submission_origin():
    store = Store()
    store.historical_candidates = [historical_candidate()]
    manager = RunDeliveryIntents(
        enabled=True,
        store=store,
        codec=HistoricalCodec(),
        queue=Queue(),
        transaction=transaction,
        scan_batch_size=25,
        claim_ttl_seconds=60,
        retry_base_seconds=5,
        retry_cap_seconds=60,
        permanent_rejection_owner=lambda *_args, **_kwargs: asyncio.sleep(0, result=True),
    )

    mapped = await manager.prepare_historical_delivery_intents()

    assert mapped == 1
    record = store.inserted[0]
    payload = json.loads(record["payload_json"])
    assert record["source"] == "chat"
    assert record["submission_id"] == "submission-history"
    assert record["user_message_id"] == "message-history"
    assert payload["input"] == {"message": "from persisted input"}
    assert payload["model_value"] == "provider/frozen"
    assert payload["agent_profile"]["content_hash"] == "old-profile-hash"


@pytest.mark.asyncio
async def test_historical_backfill_pages_candidates_in_bounded_batches():
    store = Store()
    store.historical_candidates = []
    for run_id in ("run-history-a", "run-history-b", "run-history-c"):
        candidate = historical_candidate()
        candidate["run_id"] = run_id
        candidate["chat_submission_records"][0]["run_id"] = run_id
        store.historical_candidates.append(candidate)
    manager = RunDeliveryIntents(
        enabled=True,
        store=store,
        codec=HistoricalCodec(),
        queue=Queue(),
        transaction=transaction,
        scan_batch_size=1,
        claim_ttl_seconds=60,
        retry_base_seconds=5,
        retry_cap_seconds=60,
        permanent_rejection_owner=lambda *_args, **_kwargs: asyncio.sleep(0, result=True),
    )

    assert await manager.prepare_historical_delivery_intents() == 3
    assert len(store.inserted) == 3
    assert store.historical_limits == [1, 1, 1, 1]


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["retry_run", "resume_run"])
async def test_historical_control_child_maps_by_persisted_operation(source):
    store = Store()
    store.historical_candidates = [historical_candidate(source=source)]
    manager = RunDeliveryIntents(
        enabled=True,
        store=store,
        codec=HistoricalCodec(),
        queue=Queue(),
        transaction=transaction,
        scan_batch_size=25,
        claim_ttl_seconds=60,
        retry_base_seconds=5,
        retry_cap_seconds=60,
        permanent_rejection_owner=lambda *_args, **_kwargs: asyncio.sleep(0, result=True),
    )

    assert await manager.prepare_historical_delivery_intents() == 1
    assert store.inserted[0]["source"] == source.removesuffix("_run")
    assert store.inserted[0]["submission_id"] is None


@pytest.mark.asyncio
async def test_unprovable_historical_run_blocks_enablement_instead_of_staying_pending():
    candidate = historical_candidate()
    candidate["input_json"].pop("context_snapshot_id")
    store = Store()
    store.historical_candidates = [candidate]
    manager = RunDeliveryIntents(
        enabled=True,
        store=store,
        codec=HistoricalCodec(),
        queue=Queue(),
        transaction=transaction,
        scan_batch_size=25,
        claim_ttl_seconds=60,
        retry_base_seconds=5,
        retry_cap_seconds=60,
        permanent_rejection_owner=lambda *_args, **_kwargs: asyncio.sleep(0, result=True),
    )

    with pytest.raises(RuntimeError, match="run_delivery_history_unprovable:1"):
        await manager.prepare_historical_delivery_intents()
    assert store.inserted == []


@pytest.mark.asyncio
async def test_historical_backfill_reads_existing_redis_receipt_without_second_enqueue():
    store = Store()
    store.historical_candidates = [historical_candidate()]
    queue = Queue()
    manager = RunDeliveryIntents(
        enabled=True,
        store=store,
        codec=HistoricalCodec(),
        queue=queue,
        transaction=transaction,
        scan_batch_size=25,
        claim_ttl_seconds=60,
        retry_base_seconds=5,
        retry_cap_seconds=60,
        permanent_rejection_owner=lambda *_args, **_kwargs: asyncio.sleep(0, result=True),
    )
    await manager.prepare_historical_delivery_intents()
    inserted = store.inserted[0]
    receipt = QueueDeliveryReceipt(0, 0, inserted["message_id"], "redis_readback_processing")
    queue.readback_result = receipt
    store.claims = [
        intent_record(
            run_id="run-history",
            state="claimed",
            payload_json=inserted["payload_json"],
        )
    ]

    result = await manager.deliver_one(tenant_id="tenant-a", run_id="run-history")

    assert result.state == "delivered"
    assert queue.enqueues == []
    assert queue.readbacks == [(inserted["payload_json"], inserted["message_id"])]


@pytest.mark.asyncio
async def test_queued_historical_submission_without_receipt_identity_maps_by_original_run_facts():
    candidate = historical_candidate()
    candidate["chat_submission_records"][0]["state"] = "queued"
    candidate["chat_submission_records"][0]["queue_message_id"] = None
    store = Store()
    store.historical_candidates = [candidate]
    manager = RunDeliveryIntents(
        enabled=True,
        store=store,
        codec=HistoricalCodec(),
        queue=Queue(),
        transaction=transaction,
        scan_batch_size=25,
        claim_ttl_seconds=60,
        retry_base_seconds=5,
        retry_cap_seconds=60,
        permanent_rejection_owner=lambda *_args, **_kwargs: asyncio.sleep(0, result=True),
    )

    assert await manager.prepare_historical_delivery_intents() == 1
    assert store.inserted[0]["submission_id"] == "submission-history"


@pytest.mark.asyncio
async def test_empty_claim_never_uses_another_workers_claim_token_and_only_reads_receipt():
    active_claim = intent_record()
    queue = Queue()
    store = Store(read_record=active_claim)
    result = await service(store, queue).deliver_one(tenant_id="tenant", run_id="run")

    assert result.state == "pending"
    assert result.receipt is None
    assert queue.readbacks == []
    assert queue.enqueues == []

    confirmed = QueueDeliveryReceipt(
        3, 17, active_claim["queue_message_id"], "redis_readback_queued"
    )
    store.read_record = intent_record(
        state="delivered", claim_token=None, receipt=confirmed
    )
    result = await service(store, queue).deliver_one(tenant_id="tenant", run_id="run")
    assert result.state == "delivered" and result.receipt == confirmed
    assert queue.readbacks == [] and queue.enqueues == []


@pytest.mark.asyncio
async def test_stale_claim_does_not_cross_the_queue_boundary():
    queue = Queue()
    store = Store(claims=[intent_record()], current=False)
    result = await service(store, queue).deliver_one(tenant_id="tenant", run_id="run")

    assert result.state == "suppressed"
    assert queue.readbacks == [] and queue.enqueues == []
    assert store.suppressed == [("run", "run_not_eligible_for_delivery")]


@pytest.mark.asyncio
async def test_queue_acceptance_with_lost_database_ack_is_reconciled_by_readback():
    message_id = intent_record()["queue_message_id"]
    accepted = QueueDeliveryReceipt(2, 8, message_id, "redis_readback_processing")
    queue = Queue(readback_result=accepted)
    store = Store(
        claims=[intent_record()],
        receipt_written=False,
        read_record=intent_record(
            state="delivered", claim_token=None, receipt=accepted
        ),
    )

    result = await service(store, queue).deliver_one(tenant_id="tenant", run_id="run")

    assert result.state == "delivered" and result.receipt == accepted
    assert len(queue.readbacks) == 1
    assert queue.enqueues == []
    assert store.receipt_intents == [intent_record()["queue_payload_json"]]


@pytest.mark.asyncio
async def test_scanner_replays_the_frozen_payload_without_rebuilding_it():
    original = intent_record(payload_json='{"run_id":"run","profile_revision":"v1"}')
    queue = Queue(
        enqueue_result=QueueDeliveryReceipt(
            1, 1, original["queue_message_id"], "redis_metadata"
        )
    )
    store = Store(claims=[original])

    result = await service(store, queue).scan_once()

    assert result.claimed == 1 and result.delivered == 1
    assert queue.readbacks == [
        (original["queue_payload_json"], original["queue_message_id"])
    ]
    assert queue.enqueues == [
        (original["queue_payload_json"], original["queue_message_id"])
    ]
    assert store.receipt_intents == [original["queue_payload_json"]]


@pytest.mark.asyncio
async def test_scan_queue_io_has_a_global_concurrency_limit(monkeypatch):
    records = [intent_record(run_id=f"run-{n}") for n in range(16)]

    async def delayed_none():
        await asyncio.sleep(0.005)
        return None

    queue = Queue(
        readback_result=delayed_none,
        enqueue_result=lambda payload, message_id: QueueDeliveryReceipt(
            1, 1, message_id, "redis_metadata"
        ),
    )
    store = Store(claims=records)
    result = await service(store, queue, batch_size=16).scan_once()

    assert result.claimed == 16 and result.delivered == 16
    # Each row's queue lookups are serialized behind the same eight I/O slots.
    assert queue.max_active <= 8


@pytest.mark.asyncio
async def test_temporary_queue_error_is_backed_off_without_losing_frozen_intent():
    original = intent_record()
    queue = Queue(readback_error=OSError("temporary redis outage"))
    store = Store(claims=[original])

    result = await service(store, queue).scan_once()

    assert result.rescheduled == 1
    assert store.rescheduled == [
        (original["queue_payload_json"], 5, "queue_readback_unknown")
    ]
    assert original["queue_payload_json"] == store.rescheduled[0][0]


@pytest.mark.asyncio
async def test_permanent_readback_rejection_suppresses_and_calls_run_failure_owner():
    original = intent_record()
    queue = Queue(
        readback_error=QueueDeliveryError(
            "queue_payload_invalid", definitive_rejection=True
        )
    )
    store = Store(claims=[original])
    failures = []

    async def failure_owner(connection, *, intent, reason):
        failures.append((connection, intent.run_id, reason))
        return True

    deliveries = RunDeliveryIntents(
        enabled=True,
        store=store,
        codec=object(),
        queue=queue,
        transaction=transaction,
        scan_batch_size=25,
        claim_ttl_seconds=60,
        retry_base_seconds=5,
        retry_cap_seconds=60,
        permanent_rejection_owner=failure_owner,
    )

    result = await deliveries.scan_once()

    assert result.suppressed == 1
    assert result.rescheduled == 0
    assert store.suppressed == [("run", "queue_payload_invalid")]
    assert len(failures) == 1
    assert failures[0][1:] == ("run", "queue_payload_invalid")
    assert queue.enqueues == []


@pytest.mark.asyncio
async def test_queue_io_timeout_reschedules_the_same_durable_intent(monkeypatch):
    original = intent_record()

    async def stuck_readback():
        await asyncio.sleep(1)
        return None

    monkeypatch.setattr(delivery_module, "_QUEUE_IO_TIMEOUT_SECONDS", 0.001)
    queue = Queue(readback_result=stuck_readback)
    store = Store(claims=[original])

    result = await service(store, queue).scan_once()

    assert result.rescheduled == 1
    assert store.rescheduled[0][0] == original["queue_payload_json"]
    assert store.rescheduled[0][2] == "queue_readback_unknown"


@pytest.mark.asyncio
async def test_redis_adapter_uses_composition_injected_payload_and_queue_ports():
    observed = []

    class Payload:
        def __init__(self, value):
            self.value = value
            self.skill_manifests = []

        @classmethod
        def model_validate(cls, value):
            return cls(dict(value))

        @classmethod
        def model_validate_json(cls, value):
            return cls(json.loads(value))

        def model_dump_json(self):
            return json.dumps(self.value, sort_keys=True)

        def model_dump(self, *, mode):
            assert mode == "json"
            return dict(self.value)

    async def enqueue(payload):
        observed.append(("enqueue", payload))
        message_id = sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        return type(
            "Metadata",
            (),
            {
                "queue_position": 1,
                "queue_admission_ordinal": 4,
                "message_id": message_id,
                "source": "redis_metadata",
            },
        )()

    async def readback(payload):
        observed.append(("readback", payload))
        return None

    queue = RedisRunDeliveryQueue(
        payload_model=Payload,
        payload_errors=(ValueError,),
        validate_skill_manifest_refs=lambda manifests: None,
        enqueue_run_with_metadata=enqueue,
        read_queue_admission=readback,
        queue_admission_rejected=RuntimeError,
    )
    frozen = queue.freeze({"tenant_id": "tenant", "run_id": "run"})
    assert frozen.message_id == sha256(frozen.payload_json.encode()).hexdigest()
    assert (
        await queue.readback(frozen.payload_json, message_id=frozen.message_id) is None
    )
    queued_receipt = await queue.enqueue(
        frozen.payload_json, message_id=frozen.message_id
    )
    assert queued_receipt.message_id == frozen.message_id
    assert observed == [
        ("readback", {"run_id": "run", "tenant_id": "tenant"}),
        ("enqueue", {"run_id": "run", "tenant_id": "tenant"}),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize('cancel_caller', [False, True])
async def test_delivery_work_group_keeps_blocked_children_until_they_finish(cancel_caller):
    from app.execution.application.task_completion import gather_completed

    started = asyncio.Event()
    release = asyncio.Event()
    finished = asyncio.Event()

    async def blocked_enqueue():
        started.set()
        await release.wait()
        finished.set()

    async def failed_receipt():
        await started.wait()
        raise RuntimeError('receipt transaction failed')

    task = asyncio.create_task(gather_completed(blocked_enqueue(), failed_receipt()))
    await started.wait()
    await asyncio.sleep(0)
    if cancel_caller:
        task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    assert not finished.is_set()
    release.set()
    with pytest.raises(asyncio.CancelledError if cancel_caller else RuntimeError):
        await task
    assert finished.is_set()
