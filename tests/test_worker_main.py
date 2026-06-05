import pytest

import app.worker_main as worker_main
from app.queue import QueueMessage
from app.worker import WorkerOutcome
from app.worker_main import run_once


_ORIGINAL_MEMORY_CLEANUP_FOR_WORKER = worker_main.cleanup_expired_memory_records_for_worker


@pytest.fixture(autouse=True)
def default_sandbox_cleanup(monkeypatch):
    async def cleanup_expired_sandbox_leases():
        return []

    async def cleanup_expired_memory_records_for_worker(settings=None):
        return []

    monkeypatch.setattr(
        "app.worker_main.cleanup_expired_sandbox_leases",
        cleanup_expired_sandbox_leases,
        raising=False,
    )
    monkeypatch.setattr(
        "app.worker_main.cleanup_expired_memory_records_for_worker",
        cleanup_expired_memory_records_for_worker,
        raising=False,
    )


@pytest.mark.asyncio
async def test_run_once_acknowledges_completed_message(monkeypatch):
    calls = []

    async def reclaim_expired_leases():
        calls.append(("reclaim",))

    async def lease_run(timeout_seconds=5, worker_id="worker", max_processing_runs=None):
        calls.append(("lease", worker_id))
        return QueueMessage(raw="raw-run", payload={"run_id": "run-a"}, message_id="msg-a")

    async def process_run_payload(payload, registry=None, worker_id=None):
        calls.append(("process", payload["run_id"], worker_id))
        return WorkerOutcome(status="succeeded", run_id="run-a")

    async def ack_run(raw, message_id=None):
        calls.append(("ack", raw, message_id))

    async def fail_leased_run(raw, *, error_code, error_message, message_id=None, worker_id=None):
        calls.append(("fail", raw, error_code, error_message))

    async def heartbeat_run(message_id, worker_id):
        calls.append(("heartbeat", message_id, worker_id))

    monkeypatch.setattr("app.worker_main.queue.reclaim_expired_leases", reclaim_expired_leases)
    monkeypatch.setattr("app.worker_main.queue.lease_run", lease_run)
    monkeypatch.setattr("app.worker_main.process_run_payload", process_run_payload)
    monkeypatch.setattr("app.worker_main.queue.ack_run", ack_run)
    monkeypatch.setattr("app.worker_main.queue.fail_leased_run", fail_leased_run)
    monkeypatch.setattr("app.worker_main.queue.heartbeat_run", heartbeat_run)

    outcome = await run_once(timeout_seconds=1, worker_id="worker-a", heartbeat_interval_seconds=60)

    assert outcome.status == "succeeded"
    assert calls == [("reclaim",), ("lease", "worker-a"), ("process", "run-a", "worker-a"), ("ack", "raw-run", "msg-a")]


@pytest.mark.asyncio
async def test_run_once_dead_letters_unhandled_outcome(monkeypatch):
    calls = []

    async def reclaim_expired_leases():
        calls.append(("reclaim",))

    async def lease_run(timeout_seconds=5, worker_id="worker", max_processing_runs=None):
        return QueueMessage(raw="raw-run", payload={"run_id": "run-a"}, message_id="msg-a")

    async def process_run_payload(payload, registry=None, worker_id=None):
        return WorkerOutcome(status="dead_letter", run_id=None, error_code="bad", error_message="bad payload")

    async def ack_run(raw, message_id=None):
        calls.append(("ack", raw))

    async def fail_leased_run(raw, *, error_code, error_message, message_id=None, worker_id=None):
        calls.append(("fail", raw, error_code, error_message, message_id, worker_id))

    async def heartbeat_run(message_id, worker_id):
        calls.append(("heartbeat", message_id, worker_id))

    monkeypatch.setattr("app.worker_main.queue.reclaim_expired_leases", reclaim_expired_leases)
    monkeypatch.setattr("app.worker_main.queue.lease_run", lease_run)
    monkeypatch.setattr("app.worker_main.process_run_payload", process_run_payload)
    monkeypatch.setattr("app.worker_main.queue.ack_run", ack_run)
    monkeypatch.setattr("app.worker_main.queue.fail_leased_run", fail_leased_run)
    monkeypatch.setattr("app.worker_main.queue.heartbeat_run", heartbeat_run)

    outcome = await run_once(timeout_seconds=1, worker_id="worker-a", heartbeat_interval_seconds=60)

    assert outcome.status == "dead_letter"
    assert calls == [("reclaim",), ("fail", "raw-run", "bad", "bad payload", "msg-a", "worker-a")]


@pytest.mark.asyncio
async def test_run_once_acknowledges_cancelled_message(monkeypatch):
    calls = []

    async def reclaim_expired_leases():
        calls.append(("reclaim",))

    async def lease_run(timeout_seconds=5, worker_id="worker", max_processing_runs=None):
        calls.append(("lease", worker_id))
        return QueueMessage(raw="raw-run", payload={"run_id": "run-a"}, message_id="msg-a")

    async def process_run_payload(payload, registry=None, worker_id=None):
        calls.append(("process", payload["run_id"], worker_id))
        return WorkerOutcome(status="cancelled", run_id="run-a")

    async def ack_run(raw, message_id=None):
        calls.append(("ack", raw, message_id))

    async def fail_leased_run(raw, *, error_code, error_message, message_id=None, worker_id=None):
        calls.append(("fail", raw, error_code, error_message, message_id, worker_id))

    async def heartbeat_run(message_id, worker_id):
        calls.append(("heartbeat", message_id, worker_id))

    monkeypatch.setattr("app.worker_main.queue.reclaim_expired_leases", reclaim_expired_leases)
    monkeypatch.setattr("app.worker_main.queue.lease_run", lease_run)
    monkeypatch.setattr("app.worker_main.process_run_payload", process_run_payload)
    monkeypatch.setattr("app.worker_main.queue.ack_run", ack_run)
    monkeypatch.setattr("app.worker_main.queue.fail_leased_run", fail_leased_run)
    monkeypatch.setattr("app.worker_main.queue.heartbeat_run", heartbeat_run)

    outcome = await run_once(timeout_seconds=1, worker_id="worker-a", heartbeat_interval_seconds=60)

    assert outcome.status == "cancelled"
    assert calls == [
        ("reclaim",),
        ("lease", "worker-a"),
        ("process", "run-a", "worker-a"),
        ("ack", "raw-run", "msg-a"),
    ]


@pytest.mark.asyncio
async def test_run_once_dead_letters_process_exception(monkeypatch):
    calls = []

    async def reclaim_expired_leases():
        calls.append(("reclaim",))

    async def lease_run(timeout_seconds=5, worker_id="worker", max_processing_runs=None):
        calls.append(("lease", worker_id))
        return QueueMessage(raw="raw-run", payload={"run_id": "run-a"}, message_id="msg-a")

    async def process_run_payload(payload, registry=None, worker_id=None):
        calls.append(("process", payload["run_id"], worker_id))
        raise RuntimeError("boom")

    async def ack_run(raw, message_id=None):
        calls.append(("ack", raw, message_id))

    async def fail_leased_run(raw, *, error_code, error_message, message_id=None, worker_id=None):
        calls.append(("fail", raw, error_code, error_message, message_id, worker_id))

    async def heartbeat_run(message_id, worker_id):
        calls.append(("heartbeat", message_id, worker_id))

    monkeypatch.setattr("app.worker_main.queue.reclaim_expired_leases", reclaim_expired_leases)
    monkeypatch.setattr("app.worker_main.queue.lease_run", lease_run)
    monkeypatch.setattr("app.worker_main.process_run_payload", process_run_payload)
    monkeypatch.setattr("app.worker_main.queue.ack_run", ack_run)
    monkeypatch.setattr("app.worker_main.queue.fail_leased_run", fail_leased_run)
    monkeypatch.setattr("app.worker_main.queue.heartbeat_run", heartbeat_run)

    outcome = await run_once(timeout_seconds=1, worker_id="worker-a", heartbeat_interval_seconds=60)

    assert outcome.status == "dead_letter"
    assert outcome.run_id == "run-a"
    assert outcome.error_code == "worker_process_exception"
    assert outcome.error_message == "boom"
    assert calls == [
        ("reclaim",),
        ("lease", "worker-a"),
        ("process", "run-a", "worker-a"),
        ("fail", "raw-run", "worker_process_exception", "boom", "msg-a", "worker-a"),
    ]


@pytest.mark.asyncio
async def test_run_once_returns_idle_without_message(monkeypatch):
    async def reclaim_expired_leases():
        return {"reclaimed": 0, "dead_lettered": 0}

    async def lease_run(timeout_seconds=5, worker_id="worker", max_processing_runs=None):
        return None

    monkeypatch.setattr("app.worker_main.queue.reclaim_expired_leases", reclaim_expired_leases)
    monkeypatch.setattr("app.worker_main.queue.lease_run", lease_run)

    outcome = await run_once(timeout_seconds=1, worker_id="worker-a")

    assert outcome.status == "idle"
    assert outcome.run_id is None


@pytest.mark.asyncio
async def test_run_once_passes_global_worker_capacity_to_queue(monkeypatch):
    calls = []

    async def reclaim_expired_leases():
        calls.append(("reclaim",))

    async def lease_run(timeout_seconds=5, worker_id="worker", max_processing_runs=None):
        calls.append(("lease", timeout_seconds, worker_id, max_processing_runs))
        return None

    class Settings:
        max_active_worker_runs = 3

    monkeypatch.setattr("app.worker_main.get_settings", lambda: Settings())
    monkeypatch.setattr("app.worker_main.queue.reclaim_expired_leases", reclaim_expired_leases)
    monkeypatch.setattr("app.worker_main.queue.lease_run", lease_run)

    outcome = await run_once(timeout_seconds=1, worker_id="worker-a")

    assert outcome.status == "idle"
    assert calls == [("reclaim",), ("lease", 1, "worker-a", 3)]


@pytest.mark.asyncio
async def test_run_once_cleans_expired_sandbox_leases_before_leasing_queue(monkeypatch):
    calls = []

    async def cleanup_expired_sandbox_leases():
        calls.append(("sandbox_cleanup",))
        return []

    async def reclaim_expired_leases():
        calls.append(("queue_reclaim",))

    async def lease_run(timeout_seconds=5, worker_id="worker", max_processing_runs=None):
        calls.append(("lease", worker_id))
        return None

    monkeypatch.setattr("app.worker_main.cleanup_expired_sandbox_leases", cleanup_expired_sandbox_leases, raising=False)
    monkeypatch.setattr("app.worker_main.queue.reclaim_expired_leases", reclaim_expired_leases)
    monkeypatch.setattr("app.worker_main.queue.lease_run", lease_run)

    outcome = await run_once(timeout_seconds=1, worker_id="worker-a")

    assert outcome.status == "idle"
    assert calls == [("sandbox_cleanup",), ("queue_reclaim",), ("lease", "worker-a")]


@pytest.mark.asyncio
async def test_run_once_cleans_expired_memory_records_when_due(monkeypatch):
    calls = []

    class Settings:
        max_active_worker_runs = 3
        default_tenant_id = "tenant-a"
        default_workspace_id = "workspace-a"
        memory_retention_worker_cleanup_enabled = True
        memory_retention_worker_cleanup_interval_seconds = 300.0
        memory_retention_worker_cleanup_limit = 25

    class Transaction:
        async def __aenter__(self):
            return "conn"

        async def __aexit__(self, exc_type, exc, tb):
            return False

    async def cleanup_expired_sandbox_leases():
        calls.append(("sandbox_cleanup",))

    async def cleanup_expired_memory_records(conn, *, tenant_id, workspace_id, limit):
        calls.append(("memory_cleanup", conn, tenant_id, workspace_id, limit))
        return [
            {
                "id": "mem-expired",
                "tenant_id": tenant_id,
                "workspace_id": workspace_id,
                "user_id": "user-a",
                "content": "do not audit this secret content",
                "metadata_json": {"api_key": "hidden"},
            }
        ]

    async def append_audit_log(conn, **kwargs):
        calls.append(("audit", kwargs))
        return "audit-a"

    async def reclaim_expired_leases():
        calls.append(("queue_reclaim",))

    async def lease_run(timeout_seconds=5, worker_id="worker", max_processing_runs=None):
        calls.append(("lease", worker_id, max_processing_runs))
        return None

    monkeypatch.setattr(
        "app.worker_main.cleanup_expired_memory_records_for_worker",
        _ORIGINAL_MEMORY_CLEANUP_FOR_WORKER,
    )
    monkeypatch.setattr("app.worker_main._next_memory_cleanup_at", 0.0, raising=False)
    monkeypatch.setattr("app.worker_main.get_settings", lambda: Settings())
    monkeypatch.setattr("app.worker_main.transaction", lambda: Transaction())
    monkeypatch.setattr("app.worker_main.cleanup_expired_sandbox_leases", cleanup_expired_sandbox_leases, raising=False)
    monkeypatch.setattr("app.worker_main.repositories.cleanup_expired_memory_records", cleanup_expired_memory_records)
    monkeypatch.setattr("app.worker_main.repositories.append_audit_log", append_audit_log)
    monkeypatch.setattr("app.worker_main.queue.reclaim_expired_leases", reclaim_expired_leases)
    monkeypatch.setattr("app.worker_main.queue.lease_run", lease_run)

    outcome = await run_once(timeout_seconds=1, worker_id="worker-a")

    assert outcome.status == "idle"
    assert calls[0] == ("sandbox_cleanup",)
    assert calls[1] == ("memory_cleanup", "conn", "tenant-a", "workspace-a", 25)
    assert calls[2][0] == "audit"
    assert calls[2][1]["action"] == "worker.memory.retention.cleanup"
    assert calls[2][1]["user_id"] is None
    assert calls[2][1]["target_type"] == "memory_retention"
    assert calls[2][1]["target_id"] == "workspace-a"
    assert calls[2][1]["payload_json"] == {
        "workspace_id": "workspace-a",
        "deleted_count": 1,
        "memory_record_ids": ["mem-expired"],
        "target_user_ids": ["user-a"],
        "reason": "retention_expired",
        "source": "worker",
    }
    assert calls[3:] == [("queue_reclaim",), ("lease", "worker-a", 3)]
    assert "do not audit this secret content" not in str(calls[2])
    assert "hidden" not in str(calls[2])


@pytest.mark.asyncio
async def test_run_once_does_not_audit_memory_cleanup_when_no_records_deleted(monkeypatch):
    calls = []

    class Settings:
        max_active_worker_runs = 3
        default_tenant_id = "tenant-a"
        default_workspace_id = "workspace-a"
        memory_retention_worker_cleanup_enabled = True
        memory_retention_worker_cleanup_interval_seconds = 300.0
        memory_retention_worker_cleanup_limit = 25

    class Transaction:
        async def __aenter__(self):
            return "conn"

        async def __aexit__(self, exc_type, exc, tb):
            return False

    async def cleanup_expired_memory_records(conn, *, tenant_id, workspace_id, limit):
        calls.append(("memory_cleanup", tenant_id, workspace_id, limit))
        return []

    async def append_audit_log(conn, **kwargs):
        calls.append(("audit", kwargs))

    async def reclaim_expired_leases():
        calls.append(("queue_reclaim",))

    async def lease_run(timeout_seconds=5, worker_id="worker", max_processing_runs=None):
        calls.append(("lease", worker_id))
        return None

    monkeypatch.setattr(
        "app.worker_main.cleanup_expired_memory_records_for_worker",
        _ORIGINAL_MEMORY_CLEANUP_FOR_WORKER,
    )
    monkeypatch.setattr("app.worker_main._next_memory_cleanup_at", 0.0, raising=False)
    monkeypatch.setattr("app.worker_main.get_settings", lambda: Settings())
    monkeypatch.setattr("app.worker_main.transaction", lambda: Transaction())
    monkeypatch.setattr("app.worker_main.repositories.cleanup_expired_memory_records", cleanup_expired_memory_records)
    monkeypatch.setattr("app.worker_main.repositories.append_audit_log", append_audit_log)
    monkeypatch.setattr("app.worker_main.queue.reclaim_expired_leases", reclaim_expired_leases)
    monkeypatch.setattr("app.worker_main.queue.lease_run", lease_run)

    outcome = await run_once(timeout_seconds=1, worker_id="worker-a")

    assert outcome.status == "idle"
    assert calls == [("memory_cleanup", "tenant-a", "workspace-a", 25), ("queue_reclaim",), ("lease", "worker-a")]


@pytest.mark.asyncio
async def test_run_once_skips_memory_cleanup_until_interval_elapsed(monkeypatch):
    calls = []

    class Settings:
        max_active_worker_runs = 3
        default_tenant_id = "tenant-a"
        default_workspace_id = "workspace-a"
        memory_retention_worker_cleanup_enabled = True
        memory_retention_worker_cleanup_interval_seconds = 3600.0
        memory_retention_worker_cleanup_limit = 25

    class Transaction:
        async def __aenter__(self):
            return "conn"

        async def __aexit__(self, exc_type, exc, tb):
            return False

    async def cleanup_expired_memory_records(conn, *, tenant_id, workspace_id, limit):
        calls.append(("memory_cleanup",))
        return []

    async def append_audit_log(conn, **kwargs):
        calls.append(("audit", kwargs))

    async def reclaim_expired_leases():
        calls.append(("queue_reclaim",))

    async def lease_run(timeout_seconds=5, worker_id="worker", max_processing_runs=None):
        calls.append(("lease", worker_id))
        return None

    monkeypatch.setattr(
        "app.worker_main.cleanup_expired_memory_records_for_worker",
        _ORIGINAL_MEMORY_CLEANUP_FOR_WORKER,
    )
    monkeypatch.setattr("app.worker_main._next_memory_cleanup_at", 0.0, raising=False)
    monkeypatch.setattr("app.worker_main.get_settings", lambda: Settings())
    monkeypatch.setattr("app.worker_main.transaction", lambda: Transaction())
    monkeypatch.setattr("app.worker_main.repositories.cleanup_expired_memory_records", cleanup_expired_memory_records)
    monkeypatch.setattr("app.worker_main.repositories.append_audit_log", append_audit_log)
    monkeypatch.setattr("app.worker_main.queue.reclaim_expired_leases", reclaim_expired_leases)
    monkeypatch.setattr("app.worker_main.queue.lease_run", lease_run)

    first = await run_once(timeout_seconds=1, worker_id="worker-a")
    second = await run_once(timeout_seconds=1, worker_id="worker-a")

    assert first.status == "idle"
    assert second.status == "idle"
    assert calls == [
        ("memory_cleanup",),
        ("queue_reclaim",),
        ("lease", "worker-a"),
        ("queue_reclaim",),
        ("lease", "worker-a"),
    ]


@pytest.mark.asyncio
async def test_run_once_skips_memory_cleanup_when_disabled(monkeypatch):
    calls = []

    class Settings:
        max_active_worker_runs = 3
        default_tenant_id = "tenant-a"
        default_workspace_id = "workspace-a"
        memory_retention_worker_cleanup_enabled = False
        memory_retention_worker_cleanup_interval_seconds = 300.0
        memory_retention_worker_cleanup_limit = 25

    class Transaction:
        async def __aenter__(self):
            raise AssertionError("disabled memory cleanup must not open a transaction")

        async def __aexit__(self, exc_type, exc, tb):
            return False

    async def cleanup_expired_memory_records(conn, **kwargs):
        raise AssertionError("disabled memory cleanup must not scan memory records")

    async def reclaim_expired_leases():
        calls.append(("queue_reclaim",))

    async def lease_run(timeout_seconds=5, worker_id="worker", max_processing_runs=None):
        calls.append(("lease", worker_id))
        return None

    monkeypatch.setattr(
        "app.worker_main.cleanup_expired_memory_records_for_worker",
        _ORIGINAL_MEMORY_CLEANUP_FOR_WORKER,
    )
    monkeypatch.setattr("app.worker_main._next_memory_cleanup_at", 0.0, raising=False)
    monkeypatch.setattr("app.worker_main.get_settings", lambda: Settings())
    monkeypatch.setattr("app.worker_main.transaction", lambda: Transaction())
    monkeypatch.setattr("app.worker_main.repositories.cleanup_expired_memory_records", cleanup_expired_memory_records)
    monkeypatch.setattr("app.worker_main.queue.reclaim_expired_leases", reclaim_expired_leases)
    monkeypatch.setattr("app.worker_main.queue.lease_run", lease_run)

    outcome = await run_once(timeout_seconds=1, worker_id="worker-a")

    assert outcome.status == "idle"
    assert calls == [("queue_reclaim",), ("lease", "worker-a")]


@pytest.mark.asyncio
async def test_run_once_does_not_reclaim_queue_when_sandbox_cleanup_fails(monkeypatch):
    calls = []

    async def cleanup_expired_sandbox_leases():
        calls.append(("sandbox_cleanup",))
        raise RuntimeError("sandbox cleanup failed")

    async def reclaim_expired_leases():
        calls.append(("queue_reclaim",))

    async def lease_run(timeout_seconds=5, worker_id="worker", max_processing_runs=None):
        calls.append(("lease", worker_id))
        return None

    monkeypatch.setattr("app.worker_main.cleanup_expired_sandbox_leases", cleanup_expired_sandbox_leases, raising=False)
    monkeypatch.setattr("app.worker_main.queue.reclaim_expired_leases", reclaim_expired_leases)
    monkeypatch.setattr("app.worker_main.queue.lease_run", lease_run)

    with pytest.raises(RuntimeError, match="sandbox cleanup failed"):
        await run_once(timeout_seconds=1, worker_id="worker-a")

    assert calls == [("sandbox_cleanup",)]
