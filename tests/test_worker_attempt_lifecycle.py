from datetime import datetime, timedelta, timezone

import pytest

from app.execution.application.worker_attempt_lifecycle import (
    WorkerAttemptLifecycle,
    WorkerAttemptLifecyclePorts,
    WorkerQueueLease,
)
from app.runs.api import RunTerminalizationProgress


def _lifecycle_ports(**overrides):
    async def forbidden(*_args, **_kwargs):
        raise AssertionError("unexpected lifecycle port")

    values = {
        "lock_run": forbidden,
        "complete_run": forbidden,
        "fail_run": forbidden,
        "cancel_run": forbidden,
        "is_reconciliation_claim_current": forbidden,
        "get_attempt": forbidden,
        "get_attempt_for_queue_attempt": forbidden,
        "start_attempt": forbidden,
        "assert_current_attempt": forbidden,
        "request_attempt_cancel": forbidden,
        "terminalize_attempt": forbidden,
        "is_cancel_requested": forbidden,
        "classify_success_commit_block": forbidden,
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
async def test_complete_moves_private_diagnostics_before_terminal_result():
    calls = []

    async def lock_run(_conn, **_kwargs):
        return {"id": "run-a", "status": "running"}

    async def current_attempt(_conn, **_kwargs):
        return {"id": "attempt-a", "status": "running"}

    async def record_diagnostics(_conn, **kwargs):
        calls.append(("diagnostics", kwargs))
        return {"message": "done"}

    async def complete_run(_conn, **kwargs):
        calls.append(("complete", kwargs))
        return True

    async def terminalize(_conn, **kwargs):
        calls.append(("attempt", kwargs))
        return {"status": "succeeded"}

    lifecycle = WorkerAttemptLifecycle.from_leased_attempt(
        tenant_id="tenant-a",
        run_id="run-a",
        leased_attempt_id="qat-a",
        worker_id="worker-a",
        is_reconciliation=False,
        ports=_lifecycle_ports(
            lock_run=lock_run,
            assert_current_attempt=current_attempt,
            record_result_diagnostics=record_diagnostics,
            complete_run=complete_run,
            terminalize_attempt=terminalize,
        ),
    )

    completed = await lifecycle.complete(
        object(),
        capabilities=object(),
        result_json={"message": "done", "runtime_diagnostics": {"private": True}},
    )

    assert completed is True
    assert [name for name, _ in calls] == ["diagnostics", "complete", "attempt"]
    assert calls[0][1]["attempt_id"] == "attempt-a"
    assert calls[1][1]["result_json"] == {"message": "done"}


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


@pytest.mark.asyncio
async def test_fail_persists_private_diagnostics_before_terminal_result_on_same_connection():
    calls = []
    conn = object()
    lifecycle = None

    async def lock_run(actual_conn, **_kwargs):
        assert actual_conn is conn
        return {"id": "run-a", "status": "running"}

    async def assert_current(actual_conn, **_kwargs):
        assert actual_conn is conn
        assert lifecycle is not None
        return {"id": lifecycle.attempt_id, "status": "running"}

    async def record_diagnostics(actual_conn, **kwargs):
        calls.append(("diagnostics", actual_conn, kwargs))
        return {"message": "safe"}

    async def fail_run(actual_conn, **kwargs):
        calls.append(("fail", actual_conn, kwargs))
        return RunTerminalizationProgress(True, "failed")

    async def terminalize(actual_conn, **kwargs):
        calls.append(("attempt", actual_conn, kwargs))
        return {"status": "failed"}

    lifecycle = WorkerAttemptLifecycle.from_leased_attempt(
        tenant_id="tenant-a",
        run_id="run-a",
        leased_attempt_id="qat-a",
        worker_id="worker-a",
        is_reconciliation=False,
        ports=_lifecycle_ports(
            lock_run=lock_run,
            assert_current_attempt=assert_current,
            record_result_diagnostics=record_diagnostics,
            fail_run=fail_run,
            terminalize_attempt=terminalize,
        ),
    )

    progress = await lifecycle.fail(
        conn,
        capabilities=object(),
        error_code="executor_http_failure",
        error_message="Executor request failed",
        result_json={"message": "safe", "runtime_diagnostics": {"private": True}},
    )

    assert progress is True
    assert [item[0] for item in calls] == ["diagnostics", "fail", "attempt"]
    assert all(item[1] is conn for item in calls)
    assert calls[0][2]["attempt_id"] == lifecycle.attempt_id
    assert calls[1][2]["result_json"] == {"message": "safe"}


@pytest.mark.asyncio
async def test_fail_strips_private_diagnostics_when_current_attempt_is_absent():
    calls = []

    async def lock_run(_conn, **_kwargs):
        return {"id": "run-a", "status": "running"}

    async def no_current_attempt(_conn, **_kwargs):
        return None

    async def record_diagnostics(_conn, **kwargs):
        calls.append(("diagnostics", kwargs))
        return {"message": "safe"}

    async def fail_run(_conn, **kwargs):
        calls.append(("fail", kwargs))
        return RunTerminalizationProgress(True, "failed")

    lifecycle = WorkerAttemptLifecycle.from_leased_attempt(
        tenant_id="tenant-a",
        run_id="run-a",
        leased_attempt_id="qat-a",
        worker_id="worker-a",
        is_reconciliation=False,
        ports=_lifecycle_ports(
            lock_run=lock_run,
            assert_current_attempt=no_current_attempt,
            record_result_diagnostics=record_diagnostics,
            fail_run=fail_run,
        ),
    )

    progress = await lifecycle.fail(
        object(),
        capabilities=object(),
        error_code="executor_failure",
        error_message="Executor failed",
        result_json={"runtime_diagnostics": {"private": True}},
    )

    assert progress is True
    assert calls[0][1]["attempt_id"] is None
    assert calls[1][1]["result_json"] == {"message": "safe"}
