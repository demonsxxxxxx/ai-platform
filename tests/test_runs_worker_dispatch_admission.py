from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from app.runs.api import WorkerDispatchAdmissionService


@pytest.mark.asyncio
async def test_rejected_queued_run_lock_fails_and_publishes_only_after_admission_commit():
    completed = []

    @asynccontextmanager
    async def transaction():
        writes = []
        try:
            yield writes
        except BaseException:
            completed.append(("rollback", writes.copy()))
            raise
        else:
            completed.append(("commit", writes.copy()))

    async def lock_queued_run(conn, **kwargs):
        conn.append("lock_rejected")
        return None

    async def get_run(conn, **kwargs):
        conn.append("loaded_existing")
        return {"status": "queued"}

    async def prepare_pending(conn, **kwargs):
        conn.append("pending_authority")

    async def append_event(conn, **kwargs):
        conn.append(kwargs["event_type"])

    async def should_not_authorize(*args, **kwargs):
        raise AssertionError("Cannot authorize a run without its queued lock")

    class Attempt:
        async def fail(self, conn, **kwargs):
            assert kwargs["error_code"] == "queue_payload_identity_mismatch"
            conn.append("terminal_fail")
            return True

    service = WorkerDispatchAdmissionService(
        transaction_factory=transaction,
        lock_queued_run=lock_queued_run,
        get_run=get_run,
        prepare_pending_authority=prepare_pending,
        authorize_locked_candidate=should_not_authorize,
        append_hidden_event=append_event,
        executor_resolution_error=lambda _: None,
        predispatch_failure_result=lambda *args, **kwargs: {"message": "safe"},
    )
    decision = await service.admit(
        payload=SimpleNamespace(tenant_id="tenant", run_id="run"),
        run_identity={"tenant_id": "tenant", "run_id": "run"},
        trace_id="trace", attempt_id="attempt",
        attempt_authority=Attempt(), capabilities=object(), current_principal=None,
    )

    assert decision.outcome.status == "failed"
    assert decision.publish_after_commit is True
    assert completed == [(
        "commit", ["lock_rejected", "loaded_existing", "pending_authority", "terminal_fail", "error"]
    )]
