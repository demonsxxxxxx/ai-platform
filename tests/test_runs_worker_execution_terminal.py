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


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", [None, "cas", "context", "release"])
async def test_predispatch_context_and_terminal_share_transaction_and_rollback(fault):
    writes = []
    transactions = []
    conn = object()

    @asynccontextmanager
    async def transaction():
        try:
            yield conn
        except BaseException:
            transactions.append("rollback")
            raise
        else:
            transactions.append("commit")

    async def pending(actual, **kwargs):
        assert actual is conn

    class Attempt:
        async def is_cancel_requested(self, actual):
            return False

    async def settle(actual, **kwargs):
        assert actual is conn
        writes.append("context")
        if fault == "context":
            raise ValueError("stale generation")

    async def fail(actual, **kwargs):
        assert actual is conn
        writes.append("terminal_cas")
        return fault != "cas"

    async def event(actual, **kwargs):
        assert actual is conn

    async def release(actual, **kwargs):
        assert actual is conn
        writes.append("worker_lease")

    async def release_continuation(actual, **kwargs):
        assert actual is conn
        writes.append("provider_lease_and_lineage")
        if fault == "release":
            raise ValueError("lease release failed")

    service = WorkerExecutionTerminalService(
        transaction_factory=transaction, prepare_pending_authority=pending,
        append_event=event, fail_run=fail, settle_continuation=settle,
        release_continuation_lease=release_continuation,
    )
    async def execute():
        return await service.fail_or_cancel(
            payload=SimpleNamespace(tenant_id="tenant", run_id="run", executor_type="fake"),
            attempt_id="attempt", attempt=Attempt(), capabilities=object(),
            failure_code="executor_failure", failure_message="safe", failure_result={},
            release_runtime_lease=release, continuation_settlement={"synthetic": True},
        )
    if fault in {"context", "release"}:
        with pytest.raises(ValueError):
            await execute()
    else:
        outcome = await execute()
        assert outcome.status == ("skipped" if fault == "cas" else "failed")
    assert transactions == ["commit" if fault is None else "rollback"]
    assert writes[:1] == ["context"]
    if fault == "cas":
        assert writes == ["context", "terminal_cas"]
    if fault is None:
        assert writes == ["context", "terminal_cas", "worker_lease", "provider_lease_and_lineage"]
