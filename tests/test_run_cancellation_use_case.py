from __future__ import annotations

from contextlib import asynccontextmanager

import pytest

from app.runs.api import RunTerminalizationProgress
from app.runs.application.cancellation import (
    CancelRequestAuthority,
    CancelRequestResult,
    RunCancellationUseCase,
)


class _TransactionFactory:
    def __init__(self, order: list[str]) -> None:
        self._order = order
        self.conn = object()

    def __call__(self):
        @asynccontextmanager
        async def transaction():
            self._order.append("transaction.begin")
            try:
                yield self.conn
            except Exception:
                self._order.append("transaction.rollback")
                raise
            else:
                self._order.append("transaction.commit")

        return transaction()


class _Persistence:
    def __init__(self, order: list[str], authority: CancelRequestAuthority | None) -> None:
        self._order = order
        self._authority = authority

    async def begin_owner_request(self, conn, **kwargs):
        self._order.append("legacy.cancel_requested")
        return self._authority

    async def begin_admin_request(self, conn, **kwargs):
        self._order.append("legacy.cancel_requested")
        return self._authority

    async def finish_request(self, conn, *, authority, progress, **kwargs):
        self._order.append("audit")
        return CancelRequestResult(
            run_id=authority.run_id,
            status=progress.status if progress is not None else "cancel_requested",
            trace_ref=authority.trace_ref,
            active_sandbox_leases=(),
            initial_terminalization_progress=progress,
            attempt_id=authority.attempt_id,
        )


class _EventWriter:
    def __init__(self, order: list[str], *, fail: bool = False) -> None:
        self._order = order
        self._fail = fail
        self.sources: list[str] = []

    async def prepare_pending_authority(self, conn, *, tenant_id, authority):
        self._order.append("v4.admission_pending")

    async def append_terminal(self, conn, *, tenant_id, run_id):
        self._order.append("v4.run.terminal")

    async def append_cancel_requested(self, conn, *, tenant_id, run_id, source, trace_ref):
        self._order.append("v4.run.cancel_requested")
        self.sources.append(source)
        if self._fail:
            raise RuntimeError("v4_write_failed")


class _Progressor:
    def __init__(self, order: list[str]) -> None:
        self._order = order
        self.calls = 0

    async def __call__(self, conn, **kwargs):
        self.calls += 1
        self._order.append("v4.run.cancelled")
        return RunTerminalizationProgress(True, "cancelled", True, True)


def _authority(*, role: str = "owner", newly: bool = True, prior_status: str = "queued"):
    return CancelRequestAuthority(
        run_id="run-a",
        attempt_id="attempt-a",
        prior_status=prior_status,
        trace_ref="trace-a",
        target_user_id="owner-a",
        actor_user_id="owner-a" if role == "owner" else "admin-a",
        requested_by_role=role,
        source="user" if role == "owner" else "system",
        newly_requested=newly,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("role", "expected_source"),
    [("owner", "user"), ("admin", "system")],
)
async def test_cancel_request_commits_authoritative_order_and_source(role, expected_source):
    order: list[str] = []
    transaction_factory = _TransactionFactory(order)
    persistence = _Persistence(order, _authority(role=role))
    event_writer = _EventWriter(order)
    progressor = _Progressor(order)
    use_case = RunCancellationUseCase(
        transaction_factory=transaction_factory,
        persistence=persistence,
        event_writer=event_writer,
        progress_terminalization=progressor,
    )

    if role == "owner":
        result = await use_case.request_owner_cancel(
            tenant_id="tenant-a", run_id="run-a", owner_user_id="owner-a"
        )
    else:
        result = await use_case.request_admin_cancel(
            tenant_id="tenant-a", run_id="run-a", admin_user_id="admin-a"
        )

    assert result is not None and result.status == "cancelled"
    assert event_writer.sources == [expected_source]
    assert order == [
        "transaction.begin",
        "legacy.cancel_requested",
        "v4.admission_pending",
        "v4.run.cancel_requested",
        "v4.run.cancelled",
        "v4.run.terminal",
        "audit",
        "transaction.commit",
    ]


@pytest.mark.asyncio
async def test_repeat_request_skips_duplicate_v4_but_progresses_pending_terminalization():
    order: list[str] = []
    progressor = _Progressor(order)
    use_case = RunCancellationUseCase(
        transaction_factory=_TransactionFactory(order),
        persistence=_Persistence(order, _authority(newly=False, prior_status="running")),
        event_writer=_EventWriter(order),
        progress_terminalization=progressor,
    )

    result = await use_case.request_owner_cancel(
        tenant_id="tenant-a", run_id="run-a", owner_user_id="owner-a"
    )

    assert result is not None and result.status == "cancelled"
    assert progressor.calls == 1
    assert order == [
        "transaction.begin",
        "legacy.cancel_requested",
        "v4.admission_pending",
        "v4.run.cancelled",
        "v4.run.terminal",
        "audit",
        "transaction.commit",
    ]


@pytest.mark.asyncio
async def test_v4_failure_rolls_back_before_progress_and_audit():
    order: list[str] = []
    use_case = RunCancellationUseCase(
        transaction_factory=_TransactionFactory(order),
        persistence=_Persistence(order, _authority()),
        event_writer=_EventWriter(order, fail=True),
        progress_terminalization=_Progressor(order),
    )

    with pytest.raises(RuntimeError, match="v4_write_failed"):
        await use_case.request_owner_cancel(
            tenant_id="tenant-a", run_id="run-a", owner_user_id="owner-a"
        )

    assert order == [
        "transaction.begin",
        "legacy.cancel_requested",
        "v4.admission_pending",
        "v4.run.cancel_requested",
        "transaction.rollback",
    ]


@pytest.mark.asyncio
async def test_unauthorized_cancel_commits_no_events_or_audit():
    order: list[str] = []
    progressor = _Progressor(order)
    event_writer = _EventWriter(order)
    use_case = RunCancellationUseCase(
        transaction_factory=_TransactionFactory(order),
        persistence=_Persistence(order, None),
        event_writer=event_writer,
        progress_terminalization=progressor,
    )

    result = await use_case.request_owner_cancel(
        tenant_id="tenant-a", run_id="missing", owner_user_id="owner-a"
    )

    assert result is None
    assert event_writer.sources == []
    assert progressor.calls == 0
    assert order == ["transaction.begin", "legacy.cancel_requested", "transaction.commit"]
