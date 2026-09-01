from datetime import datetime, timedelta, timezone

import pytest

from app.execution.application.worker_attempt_lifecycle import (
    WorkerAttemptLifecycle,
    WorkerAttemptLifecyclePorts,
    WorkerQueueLease,
)


def _lifecycle_ports(**overrides):
    async def forbidden(*_args, **_kwargs):
        raise AssertionError("unexpected lifecycle port")

    values = {
        "lock_run": forbidden,
        "complete_run": forbidden,
        "fail_run": forbidden,
        "cancel_run": forbidden,
        "drain_terminalization": forbidden,
        "is_reconciliation_claim_current": forbidden,
        "get_attempt": forbidden,
        "get_attempt_for_queue_attempt": forbidden,
        "start_attempt": forbidden,
        "assert_current_attempt": forbidden,
        "request_attempt_cancel": forbidden,
        "terminalize_attempt": forbidden,
        "conflict_error": ValueError,
    }
    values.update(overrides)
    return WorkerAttemptLifecyclePorts(**values)


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal_status", ["failed", "cancelled"])
async def test_complete_rejects_a_different_existing_terminal_run(terminal_status):
    calls = []

    async def lock_run(_conn, **_kwargs):
        calls.append("lock_run")
        return {"id": "run-a", "status": terminal_status}

    async def forbidden(*_args, **_kwargs):
        raise AssertionError("a terminal Run must not be written or re-fenced")

    lifecycle = WorkerAttemptLifecycle.from_leased_attempt(
        tenant_id="tenant-a",
        run_id="run-a",
        leased_attempt_id="qat-a",
        worker_id="worker-a",
        is_reconciliation=False,
        ports=_lifecycle_ports(lock_run=lock_run),
    )

    completed = await lifecycle.complete(
        object(),
        capabilities=object(),
        result_json={"message": "must not commit"},
    )

    assert completed is False
    assert calls == ["lock_run"]


@pytest.mark.asyncio
async def test_bind_execution_spec_persists_the_exact_queue_lease_facts():
    calls = []
    last_heartbeat_at = datetime(2026, 8, 30, 7, 0, tzinfo=timezone.utc)
    queue_lease = WorkerQueueLease(
        queue_message_id="b" * 64,
        last_heartbeat_at=last_heartbeat_at,
        lease_expires_at=last_heartbeat_at + timedelta(minutes=15),
    )
    lifecycle = None

    async def start_attempt(conn, **kwargs):
        calls.append((conn, kwargs))
        assert lifecycle is not None
        return {"id": lifecycle.attempt_id, "status": "running"}

    lifecycle = WorkerAttemptLifecycle.from_leased_attempt(
        tenant_id="tenant-a",
        run_id="run-a",
        leased_attempt_id="qat-a",
        worker_id="worker-a",
        is_reconciliation=False,
        ports=_lifecycle_ports(start_attempt=start_attempt),
        queue_lease=queue_lease,
    )
    execution_spec = object()

    attempt = await lifecycle.bind_execution_spec(object(), execution_spec)

    assert attempt == {"id": lifecycle.attempt_id, "status": "running"}
    assert calls[0][1] == {
        "tenant_id": "tenant-a",
        "run_id": "run-a",
        "queue_attempt_id": "qat-a",
        "worker_id": "worker-a",
        "execution_spec": execution_spec,
        "queue_message_id": "b" * 64,
        "last_heartbeat_at": last_heartbeat_at,
        "lease_expires_at": last_heartbeat_at + timedelta(minutes=15),
    }
