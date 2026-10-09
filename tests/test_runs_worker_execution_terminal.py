from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from app.runs.api import WorkerExecutionTerminalService


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel_requested", [False, True])
async def test_worker_executor_failure_terminalizes_once_and_publishes_only_after_commit(cancel_requested):
    writes = []
    transactions = []

    @asynccontextmanager
    async def transaction():
        conn = object()
        try:
            yield conn
        except BaseException:
            transactions.append("rollback")
            raise
        else:
            transactions.append("commit")

    class Attempt:
        async def is_cancel_requested(self, conn):
            writes.append("check_cancel")
            return cancel_requested

        async def cancel(self, conn, **kwargs):
            writes.append("cancel")
            return True

    async def pending(conn, **kwargs):
        writes.append("pending")

    async def fail(conn, **kwargs):
        writes.append("fail")
        return True

    async def event(conn, **kwargs):
        writes.append(kwargs["event_type"])

    async def release(conn, *, reason):
        writes.append(reason)

    service = WorkerExecutionTerminalService(
        transaction_factory=transaction,
        prepare_pending_authority=pending, append_event=event, fail_run=fail,
    )
    result = await service.fail_or_cancel(
        payload=SimpleNamespace(tenant_id="tenant", run_id="run", executor_type="fake"),
        attempt_id="attempt", attempt=Attempt(), capabilities=object(),
        failure_code="executor_failure", failure_message="safe",
        failure_result={}, release_runtime_lease=release,
    )
    assert result.status == ("cancelled" if cancel_requested else "failed")
    assert transactions == ["commit"]
    assert writes == (
        ["pending", "check_cancel", "cancel", "run_cancelled"]
        if cancel_requested else
        ["pending", "check_cancel", "fail", "error", "run_failed"]
    )
