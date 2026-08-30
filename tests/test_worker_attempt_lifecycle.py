import pytest

from app.execution.application.worker_attempt_lifecycle import (
    WorkerAttemptLifecycle,
    WorkerAttemptLifecyclePorts,
)


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
        ports=WorkerAttemptLifecyclePorts(
            lock_run=lock_run,
            complete_run=forbidden,
            fail_run=forbidden,
            cancel_run=forbidden,
            drain_terminalization=forbidden,
            is_reconciliation_claim_current=forbidden,
            get_attempt=forbidden,
            get_attempt_for_queue_attempt=forbidden,
            start_attempt=forbidden,
            assert_current_attempt=forbidden,
            request_attempt_cancel=forbidden,
            terminalize_attempt=forbidden,
            conflict_error=ValueError,
        ),
    )

    completed = await lifecycle.complete(
        object(),
        capabilities=object(),
        result_json={"message": "must not commit"},
    )

    assert completed is False
    assert calls == ["lock_run"]
