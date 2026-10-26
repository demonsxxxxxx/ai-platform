from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from app.runs.api import RunTerminalizationProgress
from app.runs.application.cancellation import (
    CancelRequestAuthority,
    CancelRequestResult,
    RunCancellationPostProcessor,
    RunCancellationQueueCleanupError,
    RunCancellationUseCase,
)
from app.runs.application import run_terminalization


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
        return RunTerminalizationProgress(True, "cancelled", True)


def _authority(
    *,
    role: str = "owner",
    newly: bool = True,
    prior_status: str = "queued",
    attempt_id: str | None = "attempt-a",
):
    return CancelRequestAuthority(
        run_id="run-a",
        attempt_id=attempt_id,
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
async def test_cancel_composition_uses_its_independent_terminal_append_once(monkeypatch):
    order: list[str] = []
    lifecycle = SimpleNamespace()

    async def progress_run(conn, *, tenant_id, run_id):
        order.append("run.progress")
        assert (tenant_id, run_id) == ("tenant-a", "run-a")
        return RunTerminalizationProgress(True, "cancelled", True)

    lifecycle.progress_run_terminalization = progress_run

    async def release_lineage(_conn, *, tenant_id, run_id):
        order.append("provider.release")
        assert (tenant_id, run_id) == ("tenant-a", "run-a")

    async def unexpected_v4_append(*_args, **_kwargs):
        pytest.fail("the cancellation event writer owns this terminal append")

    monkeypatch.setattr(
        "app.runs.application.run_terminalization.release_provider_lineage",
        release_lineage,
    )
    monkeypatch.setattr(
        "app.runs.application.run_terminalization.append_run_terminal_v4_row",
        unexpected_v4_append,
    )

    async def progress_terminalization(conn, *, tenant_id, run_id):
        return await run_terminalization.progress_run_terminalization(
            conn,
            lifecycle=lifecycle,
            tenant_id=tenant_id,
            run_id=run_id,
        )

    use_case = RunCancellationUseCase(
        transaction_factory=_TransactionFactory(order),
        persistence=_Persistence(order, _authority()),
        event_writer=_EventWriter(order),
        progress_terminalization=progress_terminalization,
    )

    result = await use_case.request_owner_cancel(
        tenant_id="tenant-a", run_id="run-a", owner_user_id="owner-a"
    )

    assert result is not None and result.status == "cancelled"
    assert order.count("v4.run.terminal") == 1
    assert order == [
        "transaction.begin",
        "legacy.cancel_requested",
        "v4.admission_pending",
        "v4.run.cancel_requested",
        "run.progress",
        "provider.release",
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


@pytest.mark.asyncio
async def test_queued_legacy_cancel_without_attempt_skips_attempt_stream_admission():
    order: list[str] = []
    event_writer = _EventWriter(order)
    use_case = RunCancellationUseCase(
        transaction_factory=_TransactionFactory(order),
        persistence=_Persistence(order, _authority(attempt_id=None)),
        event_writer=event_writer,
        progress_terminalization=_Progressor(order),
    )

    result = await use_case.request_owner_cancel(
        tenant_id="tenant-a",
        run_id="run-a",
        owner_user_id="owner-a",
    )

    assert result is not None and result.status == "cancelled"
    assert result.attempt_id is None
    assert order == [
        "transaction.begin",
        "legacy.cancel_requested",
        "v4.run.cancel_requested",
        "v4.run.cancelled",
        "v4.run.terminal",
        "audit",
        "transaction.commit",
    ]


def _post_processor(order, *, failures=None):
    failures = failures or {}
    captured = {}

    def operation(name):
        async def call(*args, **kwargs):
            order.append(name)
            captured[name] = kwargs
            if name in failures:
                raise failures[name]
        return call

    def warn(message, run_id, error):
        captured.setdefault("warnings", []).append((message, run_id, error))

    return RunCancellationPostProcessor(
        queue_cleanup=operation("queue"),
        sandbox_cleanup=SimpleNamespace(cleanup_cancelled_run_leases=operation("sandbox")),
        provider_factory=object(), transaction_factory=object(),
        admit_stream=operation("admit"), publish_event=operation("publish"), warn=warn,
    ), captured


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["owner", "admin"])
async def test_post_cancel_is_after_commit_and_preserves_actor_scope(role):
    order = []
    processor, captured = _post_processor(order)
    use_case = RunCancellationUseCase(
        transaction_factory=_TransactionFactory(order),
        persistence=_Persistence(order, _authority(role=role)),
        event_writer=_EventWriter(order), progress_terminalization=_Progressor(order),
        post_processor=processor,
    )
    actor = "owner-a" if role == "owner" else "admin-a"
    reason = "cancel_requested" if role == "owner" else "admin_cancel_requested"
    if role == "owner":
        cancellation = await use_case.request_owner_cancel(
            tenant_id="tenant-a", run_id="run-a", owner_user_id=actor,
        )
    else:
        cancellation = await use_case.request_admin_cancel(
            tenant_id="tenant-a", run_id="run-a", admin_user_id=actor,
        )
    assert order[-1] == "transaction.commit"
    result = await use_case.post_cancel(
        cancellation, tenant_id="tenant-a", user_id=actor,
        requested_by_role=role, reason=reason, worker_capabilities=object(),
    )
    assert order[-5:] == ["transaction.commit", "admit", "publish", "queue", "sandbox"]
    assert result == {"run_id": "run-a", "status": "cancelled"}
    assert captured["sandbox"]["tenant_id"] == "tenant-a"
    assert captured["sandbox"]["run_id"] == "run-a"
    assert captured["sandbox"]["user_id"] == actor
    assert captured["sandbox"]["requested_by_role"] == role
    assert captured["sandbox"]["reason"] == reason


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["cancelled", "cancel_requested"])
async def test_missing_attempt_skips_publication_and_only_cancelled_status_removes_queue(status):
    order = []
    processor, _ = _post_processor(order)
    args = dict(tenant_id="tenant-a", user_id="owner-a", requested_by_role="owner",
                reason="cancel_requested", worker_capabilities=object())
    assert await processor.post_cancel(None, **args) is None
    assert order == []
    receipt = CancelRequestResult("run-a", status, None, (), None)
    assert await processor.post_cancel(receipt, **args) == {"run_id": "run-a", "status": status}
    assert order == (["queue", "sandbox"] if status == "cancelled" else ["sandbox"])


@pytest.mark.asyncio
async def test_cancel_handoff_runs_after_successful_resource_release_and_not_after_stop_failure():
    calls = []
    handoffs = []

    async def sandbox_cleanup(_leases, **_kwargs):
        calls.append("sandbox")
        return [{"id": "lease-a"}]

    async def handoff(**kwargs):
        calls.append("handoff")
        handoffs.append(kwargs)
        return True

    processor = RunCancellationPostProcessor(
        queue_cleanup=lambda **_kwargs: asyncio.sleep(0),
        sandbox_cleanup=SimpleNamespace(cleanup_cancelled_run_leases=sandbox_cleanup),
        provider_factory=object(), transaction_factory=object(),
        admit_stream=lambda *_args, **_kwargs: asyncio.sleep(0),
        publish_event=lambda *_args, **_kwargs: asyncio.sleep(0),
        warn=lambda *_args: None,
        handoff_cancelled_attempt=handoff,
    )
    receipt = CancelRequestResult(
        "run-a", "cancel_requested", None, ({"id": "lease-a"},), None,
        "attempt-a", 5,
    )
    await processor.post_cancel(
        receipt, tenant_id="tenant-a", user_id="owner-a", requested_by_role="owner",
        reason="cancel_requested", worker_capabilities=object(),
    )
    assert calls == ["sandbox", "handoff"]
    assert handoffs == [{
        "tenant_id": "tenant-a", "run_id": "run-a", "attempt_id": "attempt-a",
        "expected_owner_generation": 5, "resource_lease_ids": ("lease-a",),
    }]

    async def repeat_cleanup(_leases, **_kwargs):
        calls.append("repeat-sandbox")
        return []

    repeat = RunCancellationPostProcessor(
        queue_cleanup=lambda **_kwargs: asyncio.sleep(0),
        sandbox_cleanup=SimpleNamespace(cleanup_cancelled_run_leases=repeat_cleanup),
        provider_factory=object(), transaction_factory=object(),
        admit_stream=lambda *_args, **_kwargs: asyncio.sleep(0),
        publish_event=lambda *_args, **_kwargs: asyncio.sleep(0),
        warn=lambda *_args: None,
        handoff_cancelled_attempt=handoff,
    )
    await repeat.post_cancel(
        receipt, tenant_id="tenant-a", user_id="owner-a", requested_by_role="owner",
        reason="cancel_requested", worker_capabilities=object(),
    )
    assert calls == ["sandbox", "handoff", "repeat-sandbox", "handoff"]
    assert handoffs[-1]["resource_lease_ids"] == ()

    async def failed_cleanup(*_args, **_kwargs):
        raise RuntimeError("stop failed")

    failed = RunCancellationPostProcessor(
        queue_cleanup=lambda **_kwargs: asyncio.sleep(0),
        sandbox_cleanup=SimpleNamespace(cleanup_cancelled_run_leases=failed_cleanup),
        provider_factory=object(), transaction_factory=object(),
        admit_stream=lambda *_args, **_kwargs: asyncio.sleep(0),
        publish_event=lambda *_args, **_kwargs: asyncio.sleep(0),
        warn=lambda *_args: None,
        handoff_cancelled_attempt=handoff,
    )
    with pytest.raises(RuntimeError, match="stop failed"):
        await failed.post_cancel(
            receipt, tenant_id="tenant-a", user_id="owner-a", requested_by_role="owner",
            reason="cancel_requested", worker_capabilities=object(),
        )
    assert calls == ["sandbox", "handoff", "repeat-sandbox", "handoff"]


@pytest.mark.asyncio
async def test_publication_and_queue_failures_still_run_sandbox_cleanup():
    order = []
    queue_error = RuntimeError("queue unavailable")
    processor, captured = _post_processor(order, failures={
        "admit": RuntimeError("stream unavailable"),
        "publish": RuntimeError("publication unavailable"), "queue": queue_error,
    })
    receipt = CancelRequestResult("run-a", "cancelled", "trace-a", ({"lease_id": "lease-a"},), None, "attempt-a")
    with pytest.raises(RunCancellationQueueCleanupError) as caught:
        await processor.post_cancel(
            receipt, tenant_id="tenant-a", user_id="owner-a", requested_by_role="owner",
            reason="cancel_requested", worker_capabilities=object(),
        )
    assert caught.value.failures == [queue_error]
    assert order == ["admit", "publish", "queue", "sandbox"]
    assert captured["admit"]["attempt_id"] == "attempt-a"
    assert captured["sandbox"]["trace_id"] == "trace-a"
    assert len(captured["warnings"]) == 2


@pytest.mark.asyncio
async def test_sandbox_failure_retains_precedence_over_queue_failure():
    order = []
    sandbox_error = RuntimeError("sandbox cleanup unavailable")
    processor, _ = _post_processor(order, failures={
        "queue": RuntimeError("queue unavailable"), "sandbox": sandbox_error,
    })
    receipt = CancelRequestResult("run-a", "cancelled", None, (), None)
    with pytest.raises(RuntimeError) as caught:
        await processor.post_cancel(
            receipt, tenant_id="tenant-a", user_id="owner-a", requested_by_role="owner",
            reason="cancel_requested", worker_capabilities=object(),
        )
    assert caught.value is sandbox_error
    assert order == ["queue", "sandbox"]
