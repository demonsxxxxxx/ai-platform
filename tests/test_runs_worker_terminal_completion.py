"""Terminal orchestration preserves the atomic commit and error boundaries."""

import asyncio
from types import SimpleNamespace

import pytest

from app.execution.api import WorkerRunCancelled
from app.runs.api import (
    WorkerOutcome, WorkerResultCommitOutcome, WorkerTerminalCompletionService,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_at", [None, "normalize", "project", "commit", "publish", "interrupt"])
async def test_completion_has_one_commit_and_never_terminalizes_publication_failure(failure_at):
    calls = []
    error = RuntimeError("synthetic terminal boundary")
    cancellation = asyncio.CancelledError()
    result = object()
    command = object()
    committed = WorkerResultCommitOutcome("succeeded")

    def normalize(value):
        assert value is result
        calls.append("normalize")
        if failure_at == "normalize":
            raise error
        return value

    def project(value, *, latency_ms):
        assert value is result and latency_ms == 42
        calls.append("project")
        if failure_at == "project":
            raise error
        return command

    async def commit(value, **kwargs):
        assert value is command
        calls.append("commit")
        assert not service.runtime_lease_released
        if failure_at == "commit":
            raise error
        if failure_at == "interrupt":
            raise cancellation
        return committed

    async def publish_result(value, outcome):
        assert value is command and outcome is committed
        assert service.runtime_lease_released
        calls.append("publish_result")
        if failure_at == "publish":
            raise error

    def classify(exc):
        assert exc is error  # preserve the original exception and cause
        calls.append("classify")
        return "executor_failure", "safe failure", {}

    async def fail_or_cancel(**kwargs):
        calls.append("fail_or_cancel")
        return WorkerResultCommitOutcome("failed", "executor_failure", "safe failure")

    async def publish():
        calls.append("publish_failure")
        assert service.runtime_lease_released

    service = WorkerTerminalCompletionService(
        payload=SimpleNamespace(run_id="run"), attempt_id="attempt",
        attempt_authority=object(), capabilities=object(), release_runtime_lease=object(),
        result_commit=SimpleNamespace(commit=commit),
        execution_terminal=SimpleNamespace(fail_or_cancel=fail_or_cancel),
        normalize_result=normalize, build_command=project,
        exception_failure=classify, cancelled_error=WorkerRunCancelled,
        publish=publish, publish_result=publish_result,
    )
    if failure_at in {"project", "commit", "publish", "interrupt"}:
        expected = cancellation if failure_at == "interrupt" else error
        with pytest.raises(type(expected)) as raised:
            await service.complete(result, latency_ms=42)
        assert raised.value is expected
        assert "fail_or_cancel" not in calls
        assert service.runtime_lease_released is (failure_at == "publish")
    else:
        outcome = await service.complete(result, latency_ms=42)
        assert outcome == (WorkerOutcome("failed", "run", "executor_failure", "safe failure")
                           if failure_at == "normalize" else WorkerOutcome("succeeded", "run"))
    assert calls == {
        None: ["normalize", "project", "commit", "publish_result"],
        "normalize": ["normalize", "classify", "fail_or_cancel", "publish_failure"],
        "project": ["normalize", "project"],
        "commit": ["normalize", "project", "commit"],
        "interrupt": ["normalize", "project", "commit"],
        "publish": ["normalize", "project", "commit", "publish_result"],
    }[failure_at]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_at", [None, "normalize", "cancel", "commit"])
@pytest.mark.parametrize("outer_transaction", [True, False])
async def test_operator_outer_transaction_owns_publication_and_rolls_back_original_failures(failure_at, outer_transaction):
    calls = []
    error = RuntimeError("synthetic operator failure")

    def normalize(result):
        if failure_at == "normalize":
            raise error
        if failure_at == "cancel":
            raise WorkerRunCancelled()
        return result

    async def commit(*args, **kwargs):
        calls.append("commit")
        if failure_at == "commit":
            raise error
        return WorkerResultCommitOutcome("cancelled")

    async def forbidden(*args, **kwargs):
        raise AssertionError("outer transaction retains terminal and publication authority")

    async def published(*args):
        assert not outer_transaction
        calls.append("published")

    service = WorkerTerminalCompletionService(
        payload=SimpleNamespace(run_id="run"), attempt_id="attempt",
        attempt_authority=object(), capabilities=object(), release_runtime_lease=object(),
        result_commit=SimpleNamespace(commit=commit), execution_terminal=object(),
        normalize_result=normalize, build_command=lambda result, **kwargs: result,
        exception_failure=forbidden, cancelled_error=WorkerRunCancelled,
        publish=forbidden, publish_result=published, outer_terminal_transaction=outer_transaction,
        terminal_fallback_allowed=False,
    )
    if failure_at:
        with pytest.raises(WorkerRunCancelled if failure_at == "cancel" else RuntimeError) as caught:
            await service.complete(object(), latency_ms=0)
        if failure_at != "cancel":
            assert caught.value is error
    else:
        assert await service.complete(object(), latency_ms=0) == WorkerOutcome("cancelled", "run")
    expected = ["commit"] if failure_at in {None, "commit"} else []
    if failure_at is None and not outer_transaction:
        expected.append("published")
    assert calls == expected


@pytest.mark.asyncio
async def test_predispatch_failure_carries_exact_continuation_proof_into_current_terminal_owner():
    proof = {"stop_evidence": "provider_not_created"}
    error = RuntimeError("synthetic predispatch failure")
    error.predispatch_continuation_settlement = proof
    calls = []

    async def fail_or_cancel(**kwargs):
        assert kwargs["continuation_settlement"] is proof
        calls.append("terminal")
        return WorkerResultCommitOutcome("failed", "predispatch", "safe")

    async def publish():
        calls.append("publish")

    service = WorkerTerminalCompletionService(
        payload=SimpleNamespace(run_id="run"), attempt_id="attempt",
        attempt_authority=object(), capabilities=object(), release_runtime_lease=object(),
        result_commit=object(), execution_terminal=SimpleNamespace(fail_or_cancel=fail_or_cancel),
        normalize_result=object(), build_command=object(),
        exception_failure=lambda exc: ("predispatch", "safe", {}), cancelled_error=WorkerRunCancelled,
        publish=publish, publish_result=object(),
    )
    assert await service.fail(error) == WorkerOutcome("failed", "run", "predispatch", "safe")
    assert calls == ["terminal", "publish"]
