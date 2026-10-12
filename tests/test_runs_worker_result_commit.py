from tests.support.terminal_timing import terminal_stage_logs as terminal_stage_logs
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from app.runs.api import WorkerResultCommitCommand, WorkerResultCommitService
from app.runs.infrastructure.worker_result_commit_timing import TimedWorkerResultCommitService


@pytest.mark.asyncio
@pytest.mark.parametrize("target", ["succeeded", "failed", "cancelled"])
@pytest.mark.parametrize("service_type", [WorkerResultCommitService, TimedWorkerResultCommitService])
@pytest.mark.usefixtures("terminal_stage_logs")
async def test_lost_terminal_cas_rolls_back_all_worker_result_facts(target, service_type, monkeypatch, caplog):
    from app.platform import tracing
    now = [10.0]
    monkeypatch.setattr(tracing, "monotonic", lambda: now[0])
    transactions = []
    releases = []

    @asynccontextmanager
    async def transaction():
        writes = []
        try:
            yield writes
        except BaseException:
            now[0] += 1.0
            transactions.append(("rollback", writes.copy()))
            raise
        else:
            now[0] += 2.0
            transactions.append(("commit", writes.copy()))

    async def lock_run(conn, **kwargs):
        conn.append("locked_run")
        return {"status": "running"}

    async def record_artifacts(conn, *args, **kwargs):
        conn.append("artifacts")

    async def record_skills(conn, *args, **kwargs):
        conn.append("skills")

    async def record_assistant(conn, *args, **kwargs):
        conn.append("assistant")

    async def record_user_event(conn, **kwargs):
        conn.append(kwargs["event_type"])

    async def noop(*args, **kwargs):
        return None

    async def release(conn, *, reason):
        releases.append(reason)

    class Attempt:
        async def is_cancel_requested(self, conn):
            return target == "cancelled"

        async def complete(self, conn, **kwargs):
            conn.append("cas_failed")
            return False

        async def fail(self, conn, **kwargs):
            conn.append("cas_failed")
            return False

        async def cancel(self, conn, **kwargs):
            conn.append("cas_failed")
            return False

        async def classify_success_commit_block(self, conn):
            conn.append("classified")
            return "stale_terminal_state"

    service = service_type(
        transaction_factory=transaction, lock_run=lock_run,
        reconciliation_claim_current=noop, materialize_answer=noop,
        persist_artifacts=record_artifacts, persist_skill_snapshots=record_skills,
        persist_assistant=record_assistant, append_user_event=record_user_event,
        append_hidden_event=noop, persist_failure_event=noop,
        public_failure_message=lambda result: "safe failure",
        prefers_cancelled=lambda result: True,
    )
    command = WorkerResultCommitCommand(
        payload=SimpleNamespace(tenant_id="tenant", run_id="run"),
        result=SimpleNamespace(
            status="succeeded" if target == "succeeded" else "failed",
            artifacts=[], executor_payload={}, result={},
        ),
        result_payload={"message": "safe"}, artifact_records=[],
        skill_snapshot={"used_skills": []}, terminal_event_kwargs={},
        attempt_id="attempt", trace_id="trace",
    )
    outcome = await service.commit(
        command, attempt_authority=Attempt(), capabilities=object(), release_runtime_lease=release
    )

    assert outcome.status == "skipped"
    assert outcome.publish_run_event is True
    expected_writes = ["locked_run", "artifacts", "skills"]
    if target == "succeeded":
        expected_writes += ["assistant", "assistant_message_created"]
    expected_writes += ["cas_failed"]
    assert transactions == [("rollback", expected_writes)] + (
        [("commit", ["classified"])] if target == "succeeded" else []
    )
    assert releases == []
    timings = [record for record in caplog.records if record.msg == "run_terminal_stage"]
    assert len(timings) == int(service_type is TimedWorkerResultCommitService)
    if timings:
        assert timings[0].terminal_status == "skipped"
        assert timings[0].terminal_phase_outcome == "ok"
        assert timings[0].terminal_phase_duration_ms == (3000 if target == "succeeded" else 1000)

@pytest.mark.asyncio
@pytest.mark.parametrize("body,artifacts,ready", [("", [], False), (" \n", [], False), ("safe", [], True), ("", [{"id": "artifact"}], True)])
@pytest.mark.parametrize("service_type", [WorkerResultCommitService, TimedWorkerResultCommitService])
async def test_success_preserves_empty_terminal_anchor_without_inventing_response_ready(body, artifacts, ready, service_type):
    writes = []
    @asynccontextmanager
    async def transaction():
        yield writes
    async def lock(conn, **kwargs):
        assert kwargs == {"tenant_id": "tenant", "run_id": "run", "for_update": True}
        return {"status": "running"}
    async def noop(*args, **kwargs):
        return None
    async def assistant(conn, result, payload, records, skills, content, metadata, attempt_id, result_payload):
        assert attempt_id == "attempt"
        assert result_payload["message"] == body
        assert content is None  # no fabricated body or answer receipt
        conn.append("assistant_anchor")
    async def event(conn, **kwargs):
        conn.append(kwargs["event_type"])
    class Attempt:
        async def is_cancel_requested(self, conn): return False
        async def complete(self, conn, **kwargs):
            assert kwargs["result_json"]["message"] == body
            conn.append("completed")
            return True
    service = service_type(transaction_factory=transaction, lock_run=lock,
        reconciliation_claim_current=noop, materialize_answer=noop, persist_artifacts=noop,
        persist_skill_snapshots=noop, persist_assistant=assistant, append_user_event=event,
        append_hidden_event=noop, persist_failure_event=noop, public_failure_message=lambda result: "safe",
        prefers_cancelled=lambda result: False)
    command = WorkerResultCommitCommand(payload=SimpleNamespace(tenant_id="tenant", run_id="run"),
        result=SimpleNamespace(status="succeeded", artifacts=artifacts, executor_payload={}, result={"message": body}),
        result_payload={"message": body}, artifact_records=artifacts, skill_snapshot={"used_skills": []},
        terminal_event_kwargs={}, attempt_id="attempt", trace_id="trace")
    outcome = await service.commit(command, attempt_authority=Attempt(), capabilities=object(), release_runtime_lease=noop)
    assert outcome.status == "succeeded"
    assert writes == ["assistant_anchor"] + (["assistant_message_created"] if ready else []) + ["completed", "run_succeeded"]


@pytest.mark.asyncio
async def test_empty_assistant_anchor_keeps_provider_coverage_identity(monkeypatch):
    from app.runs.application import provider_terminalization as owner
    calls = []
    async def append(conn, **kwargs):
        calls.append(("append", kwargs))
        assert kwargs["content"] == ""
        return "assistant-anchor"
    async def commit(conn, **kwargs): calls.append(("coverage", kwargs))
    monkeypatch.setattr(owner, "commit_provider_turn", commit)
    message_id = await owner.persist_assistant_with_provider_coverage(object(), append_message=append,
        tenant_id="tenant", session_id="session", run_id="run", attempt_id="attempt",
        executor_type="claude-agent-worker", content="", metadata_json={}, provider_final_sequence=7)
    assert message_id == "assistant-anchor"
    assert calls[1] == ("coverage", {"tenant_id": "tenant", "run_id": "run", "attempt_id": "attempt", "assistant_message_id": "assistant-anchor", "final_sequence": 7})



@pytest.mark.asyncio
@pytest.mark.parametrize("ending", ["success", "commit_failure", "cancel"])
@pytest.mark.usefixtures("terminal_stage_logs")
async def test_timed_result_commit_includes_transaction_exit_without_changing_errors(monkeypatch, caplog, ending):
    import asyncio
    from app.platform import tracing
    now = [10.0]
    monkeypatch.setattr(tracing, "monotonic", lambda: now[0])
    failure = asyncio.CancelledError("private cancellation") if ending == "cancel" else RuntimeError("private database failure")
    writes = []

    @asynccontextmanager
    async def transaction():
        yield writes
        now[0] += 2.5
        await asyncio.sleep(0)
        if ending != "success":
            raise failure
        writes.append("transaction_exit")

    async def lock(conn, **kwargs):
        return {"status": "running"}

    async def noop(*args, **kwargs):
        pass

    class Attempt:
        async def is_cancel_requested(self, conn):
            return False
        async def complete(self, conn, **kwargs):
            writes.append("terminal_write")
            return True

    service = TimedWorkerResultCommitService(
        transaction_factory=transaction, lock_run=lock,
        reconciliation_claim_current=noop, materialize_answer=noop,
        persist_artifacts=noop, persist_skill_snapshots=noop,
        persist_assistant=noop, append_user_event=noop,
        append_hidden_event=noop, persist_failure_event=noop,
        public_failure_message=lambda result: "safe failure",
        prefers_cancelled=lambda result: False,
    )
    command = WorkerResultCommitCommand(
        payload=SimpleNamespace(tenant_id="tenant", run_id="run"),
        result=SimpleNamespace(status="succeeded", artifacts=[], executor_payload={}, result={}),
        result_payload={"message": "private answer"}, artifact_records=[],
        skill_snapshot={"used_skills": []}, terminal_event_kwargs={},
        attempt_id="attempt", trace_id="trace",
    )
    if ending == "success":
        outcome = await service.commit(command, attempt_authority=Attempt(), capabilities=object(), release_runtime_lease=noop)
        assert outcome.status == "succeeded"
        assert writes == ["terminal_write", "transaction_exit"]
    else:
        with pytest.raises(type(failure)) as caught:
            await service.commit(command, attempt_authority=Attempt(), capabilities=object(), release_runtime_lease=noop)
        assert caught.value is failure
        assert writes == ["terminal_write"]
    records = [record for record in caplog.records if record.msg == "run_terminal_stage"]
    assert len(records) == 1
    assert records[0].terminal_phase == "result_business_commit"
    assert records[0].terminal_phase_duration_ms == 2500
    assert records[0].terminal_phase_outcome == {"success": "ok", "commit_failure": "error", "cancel": "cancelled"}[ending]
    assert "private" not in repr(vars(records[0]))


def test_worker_result_commit_bootstrap_selects_timing_without_changing_public_contract():
    import inspect
    from app.bootstrap.worker_result_commit import build_worker_result_commit_service
    service = build_worker_result_commit_service(lambda: None)
    assert isinstance(service, WorkerResultCommitService)
    assert type(service) is TimedWorkerResultCommitService
    assert inspect.signature(TimedWorkerResultCommitService) == inspect.signature(WorkerResultCommitService)
    assert inspect.signature(TimedWorkerResultCommitService.commit) == inspect.signature(WorkerResultCommitService.commit)


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel_path", ["failed_result", "blocked_success"])
@pytest.mark.parametrize("amount", [None, 0, 17])
async def test_cancel_preserves_only_normalized_observations_without_answer_or_private_payload(cancel_path, amount):
    @asynccontextmanager
    async def transaction():
        yield object()

    async def noop(*args, **kwargs):
        return None

    async def lock_run(*args, **kwargs):
        return {"status": "running"}

    captured = []

    class Attempt:
        async def is_cancel_requested(self, conn):
            return cancel_path == "failed_result"

        async def complete(self, conn, **kwargs):
            return False

        async def classify_success_commit_block(self, conn):
            return "cancel_requested"

        async def cancel(self, conn, **kwargs):
            captured.append(kwargs["result_json"])
            return True

    service = WorkerResultCommitService(
        transaction_factory=transaction, lock_run=lock_run,
        reconciliation_claim_current=noop, materialize_answer=noop,
        persist_artifacts=noop, persist_skill_snapshots=noop,
        persist_assistant=noop, append_user_event=noop,
        append_hidden_event=noop, persist_failure_event=noop,
        public_failure_message=lambda result: "safe failure",
        prefers_cancelled=lambda result: True,
    )
    observation = {"latency_ms": 7, "token_counts": {"input": 3, "output": 5, "total": 8},
                   "cost": {"estimated_cost_minor": amount},
                   "runtime_private_payload": {"sdk_usage": {"cost_coverage": "partial" if amount is None else "complete"}}}
    command = WorkerResultCommitCommand(
        payload=SimpleNamespace(tenant_id="tenant", run_id="run"),
        result=SimpleNamespace(status="failed" if cancel_path == "failed_result" else "succeeded",
                               artifacts=[], executor_payload={}, result={}),
        result_payload={"message": "PRIVATE_ANSWER", "private_payload": "PRIVATE_PAYLOAD", **observation},
        artifact_records=[], skill_snapshot={"used_skills": []}, terminal_event_kwargs={},
        attempt_id="attempt", trace_id="trace",
    )
    outcome = await service.commit(command, attempt_authority=Attempt(), capabilities=object(), release_runtime_lease=noop)
    assert outcome.status == "cancelled"
    assert captured == [{"message": "任务已取消", **observation}]
    assert "PRIVATE" not in repr(captured)
