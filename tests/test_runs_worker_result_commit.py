from tests.support.terminal_timing import terminal_stage_logs as terminal_stage_logs
import logging
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from app.runs.api import WorkerResultCommitCommand, WorkerResultCommitService
from app.runs.infrastructure.worker_result_commit_timing import TimedWorkerResultCommitService


def _log_payload(record):
    metadata = set(logging.makeLogRecord({}).__dict__) | {"message", "asctime"}
    return {
        "message": record.getMessage(),
        "extra": {key: value for key, value in record.__dict__.items() if key not in metadata},
    }


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

    async def settle_continuation(conn, *, settlement, **kwargs):
        conn.append(f"continuation_{settlement['sdk_settlement']['status']}")

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
        settle_continuation=settle_continuation if target == "succeeded" else None,
        release_continuation_lease=noop,
    )
    command = WorkerResultCommitCommand(
        payload=SimpleNamespace(tenant_id="tenant", run_id="run"),
        result=SimpleNamespace(
            status="succeeded" if target == "succeeded" else "failed",
            artifacts=[{"id": "artifact"}], executor_payload={
                "continuation_settlement": {"sdk_settlement": {}}
            } if target == "succeeded" else {}, result={},
        ),
        result_payload={"message": "safe"}, artifact_records=[{"id": "artifact"}],
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
        expected_writes += ["assistant", "continuation_succeeded", "assistant_message_created"]
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
async def test_locked_cancel_settles_late_sdk_success_without_answer_coverage():
    transactions = []
    releases = []
    settlements = []
    cancellation_results = []
    materialized_receipts = []

    @asynccontextmanager
    async def transaction():
        writes = []
        try:
            yield writes
        except BaseException:
            transactions.append(("rollback", writes.copy()))
            raise
        else:
            transactions.append(("commit", writes.copy()))

    async def lock_run(conn, **_kwargs):
        conn.append("locked_run")
        return {"status": "running"}

    answer_receipt = {"schema_version": "ai-platform.assistant-answer-receipt.v1"}

    async def materialize_answer(_capabilities, _conn, **kwargs):
        materialized_receipts.append(kwargs["answer_receipt"])
        return SimpleNamespace(
            result=kwargs["result"],
            result_payload={**kwargs["result_payload"], "answer_receipt": answer_receipt},
            artifact_records=kwargs["artifact_records"],
            assistant_message_for_persistence="SDK answer must not be persisted",
            assistant_message_metadata={"answer_receipt": answer_receipt},
        )

    async def persist_artifacts(conn, *_args, **_kwargs):
        conn.append("artifacts")

    async def persist_skills(conn, *_args, **_kwargs):
        conn.append("skills")

    async def persist_assistant(*_args, **_kwargs):
        pytest.fail("cancelled business result must not persist an assistant coverage anchor")

    async def append_user_event(*_args, **_kwargs):
        pytest.fail("cancelled business result must not publish an assistant success event")

    async def settle(conn, *, settlement, **_kwargs):
        settlements.append(settlement)
        conn.append("context_cancelled")

    async def release_continuation(conn, *, reason, **_kwargs):
        releases.append(("continuation", reason))
        conn.append("continuation_lease_released")

    async def release_runtime(conn, *, reason):
        releases.append(("runtime", reason))
        conn.append("runtime_lease_released")

    async def claim_current(_conn, **_kwargs):
        return True

    class Attempt:
        async def is_cancel_requested(self, _conn):
            return True

        async def complete(self, *_args, **_kwargs):
            pytest.fail("a late SDK success must not use the Runs success CAS after cancellation")

        async def cancel(self, conn, *, result_json, **_kwargs):
            cancellation_results.append(result_json)
            conn.append("cancel_cas")
            return True

    original_settlement = {
        "stop_evidence": "provider_stopped",
        "workspace_receipt": {"state": "saved", "version_id": "version-a"},
        "sdk_settlement": {
            "status": "completed",
            "terminal_reason": "completed",
            "input_accepted": True,
            "mirror_complete": True,
            "received_result": True,
            "ack_unknown": False,
        },
    }
    service = WorkerResultCommitService(
        transaction_factory=transaction,
        lock_run=lock_run,
        reconciliation_claim_current=claim_current,
        materialize_answer=materialize_answer,
        persist_artifacts=persist_artifacts,
        persist_skill_snapshots=persist_skills,
        persist_assistant=persist_assistant,
        append_user_event=append_user_event,
        append_hidden_event=append_user_event,
        persist_failure_event=append_user_event,
        public_failure_message=lambda _result: "safe failure",
        prefers_cancelled=lambda _result: False,
        settle_continuation=settle,
        release_continuation_lease=release_continuation,
    )
    command = WorkerResultCommitCommand(
        payload=SimpleNamespace(tenant_id="tenant", run_id="run"),
        result=SimpleNamespace(
            status="succeeded",
            artifacts=[{"id": "artifact"}],
            executor_payload={
                "runtime_terminal_status": "completed",
                "answer_receipt": answer_receipt,
                "continuation_settlement": original_settlement,
            },
            result={"message": "SDK answer must not become the Run result"},
        ),
        result_payload={"message": "SDK answer must not become the Run result"},
        artifact_records=[{"id": "artifact"}],
        skill_snapshot={"used_skills": []},
        terminal_event_kwargs={},
        attempt_id="attempt",
        trace_id="trace",
        reconciliation_lease_id="lease",
        reconciliation_claim_token="claim",
    )

    outcome = await service.commit(
        command,
        attempt_authority=Attempt(),
        capabilities=object(),
        release_runtime_lease=release_runtime,
    )

    assert outcome.status == "cancelled"
    assert command.result.status == "succeeded"
    assert materialized_receipts == [answer_receipt]
    assert command.result.executor_payload["answer_receipt"] == answer_receipt
    assert command.result.executor_payload["continuation_settlement"] == original_settlement
    assert settlements == [{
        **original_settlement,
        "sdk_settlement": {**original_settlement["sdk_settlement"], "status": "cancelled"},
    }]
    assert cancellation_results == [{"message": "任务已取消"}]
    assert transactions == [("commit", [
        "locked_run", "context_cancelled", "artifacts", "skills", "cancel_cas",
        "continuation_lease_released", "runtime_lease_released",
    ])]
    assert releases == [("continuation", "run_cancelled"), ("runtime", "run_cancelled")]


@pytest.mark.asyncio
async def test_empty_assistant_anchor_keeps_provider_coverage_identity(monkeypatch):
    from app.runs.application import run_terminalization as owner
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
    logged = repr(_log_payload(records[0]))
    assert "private answer" not in logged
    assert "private cancellation" not in logged
    assert "private database failure" not in logged


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


@pytest.mark.asyncio
async def test_continuation_settlement_rolls_back_when_cancel_fallback_cas_loses():
    transactions = []

    @asynccontextmanager
    async def transaction():
        writes = []
        try:
            yield writes
        except BaseException:
            transactions.append(("rollback", writes.copy()))
            raise
        else:
            transactions.append(("commit", writes.copy()))

    async def lock_run(*_args, **_kwargs):
        return {"status": "running"}

    async def noop(*_args, **_kwargs):
        return None

    async def persist_artifacts(conn, *_args, **_kwargs):
        conn.append("artifacts")

    async def persist_skills(conn, *_args, **_kwargs):
        conn.append("skills")

    async def persist_assistant(conn, *_args, **_kwargs):
        conn.append("assistant")

    async def append_user_event(conn, **kwargs):
        conn.append(kwargs["event_type"])

    async def settle(conn, *, settlement, **_kwargs):
        conn.append(("context", settlement["sdk_settlement"]["status"]))

    class Attempt:
        async def is_cancel_requested(self, _conn):
            return False

        async def complete(self, conn, **_kwargs):
            conn.append("complete_cas")
            return False

        async def classify_success_commit_block(self, conn):
            conn.append("classify")
            return "cancel_requested"

        async def cancel(self, conn, **_kwargs):
            conn.append("cancel_cas")
            return False

    service = WorkerResultCommitService(
        transaction_factory=transaction,
        lock_run=lock_run,
        reconciliation_claim_current=noop,
        materialize_answer=noop,
        persist_artifacts=persist_artifacts,
        persist_skill_snapshots=persist_skills,
        persist_assistant=persist_assistant,
        append_user_event=append_user_event,
        append_hidden_event=noop,
        persist_failure_event=noop,
        public_failure_message=lambda _result: "safe failure",
        prefers_cancelled=lambda _result: False,
        settle_continuation=settle,
        release_continuation_lease=noop,
    )
    command = WorkerResultCommitCommand(
        payload=SimpleNamespace(tenant_id="tenant", run_id="run"),
        result=SimpleNamespace(
            status="succeeded",
            artifacts=[],
            executor_payload={"continuation_settlement": {"sdk_settlement": {}}},
            result={},
        ),
        result_payload={"message": "safe"},
        artifact_records=[],
        skill_snapshot={"used_skills": []},
        terminal_event_kwargs={},
        attempt_id="attempt",
        trace_id="trace",
    )

    outcome = await service.commit(
        command,
        attempt_authority=Attempt(),
        capabilities=object(),
        release_runtime_lease=noop,
    )

    assert outcome.status == "skipped"
    assert transactions == [
        ("rollback", [
            "artifacts", "skills", "assistant", ("context", "succeeded"),
            "assistant_message_created", "complete_cas",
        ]),
        ("rollback", ["classify", ("context", "cancelled"), "cancel_cas"]),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("delta", [-1, 0, 1, 49, 100_000])
@pytest.mark.parametrize("unit", ["x", "汉\"\\"])
async def test_receipt_answer_budget(delta, unit):
    from app.execution.api import materialize_worker_answer, AnswerPersistenceLimits
    from app.executors.base import ExecutorResult
    from app.platform.postgres.limits import (
        MESSAGE_CONTENT_MAX_BYTES, RUN_RESULT_MAX_BYTES, json_size_bytes, ensure_json_size,
    )

    receipt = {"schema_version": "test-receipt", "run_id": "run", "attempt_id": "attempt", "sha256": "a" * 64}
    payload = {
        "artifacts": [{"id": "artifact", "display_name": "报告", "download_url": "/artifacts/artifact"}],
        "executor": {"executor_type": "claude_agent", "capabilities": ["tools"]},
        "sdk_used": True, "allowed_skills": [], "staged_skills": [],
        "latency_ms": 7, "token_counts": {"input": 3, "output": 5, "total": 8},
        "cost": {"estimated_cost_minor": 17},
    }
    overhead = json_size_bytes({**payload, "message": "", "answer_receipt": receipt})
    text_budget = RUN_RESULT_MAX_BYTES - overhead + delta
    unit_size = json_size_bytes(unit) - 2
    message = unit * (text_budget // unit_size) + "x" * (text_budget % unit_size)
    assert json_size_bytes({**payload, "message": message, "answer_receipt": receipt}) == RUN_RESULT_MAX_BYTES + delta
    calls = []
    committed = []
    assistant = []

    @asynccontextmanager
    async def transaction():
        try:
            yield object()
        except BaseException:
            calls.append("rollback")
            raise
        else:
            calls.append("commit")

    async def noop(*args, **kwargs):
        return None

    async def lock_run(*args, **kwargs):
        return {"status": "running"}

    async def load_answer(conn, **kwargs):
        assert kwargs == {"tenant_id": "tenant", "run_id": "run", "attempt_id": "attempt", "receipt": receipt}
        calls.append("verified")
        return SimpleNamespace(text=message)

    async def materialize(capabilities, conn, **kwargs):
        return await materialize_worker_answer(capabilities, conn, **kwargs, limits=AnswerPersistenceLimits(
            MESSAGE_CONTENT_MAX_BYTES, RUN_RESULT_MAX_BYTES, json_size_bytes,
        ))

    async def persist_assistant(conn, result, dispatch, records, skills, text, metadata, attempt_id, result_payload):
        assistant.append((text, metadata))

    class Attempt:
        async def is_cancel_requested(self, conn):
            return False

        async def complete(self, conn, **kwargs):
            ensure_json_size(kwargs["result_json"], max_bytes=RUN_RESULT_MAX_BYTES, code="run_result_too_large")
            committed.append(kwargs["result_json"])
            return True

    service = WorkerResultCommitService(
        transaction_factory=transaction, lock_run=lock_run,
        reconciliation_claim_current=noop, materialize_answer=materialize,
        persist_artifacts=noop, persist_skill_snapshots=noop,
        persist_assistant=persist_assistant, append_user_event=noop,
        append_hidden_event=noop, persist_failure_event=noop,
        public_failure_message=lambda result: "safe failure", prefers_cancelled=lambda result: False,
    )
    command = WorkerResultCommitCommand(
        payload=SimpleNamespace(tenant_id="tenant", run_id="run"),
        result=ExecutorResult(status="succeeded", adapter_version="1", executor_type="test", executor_version="1", capabilities={}, result={}, artifacts=[], executor_payload={"answer_receipt": receipt}),
        result_payload=payload, artifact_records=[], skill_snapshot={}, terminal_event_kwargs={},
        attempt_id="attempt", trace_id="trace",
    )
    outcome = await service.commit(command, attempt_authority=Attempt(), capabilities=SimpleNamespace(
        event_persistence=SimpleNamespace(load_answer_by_receipt=load_answer),
    ), release_runtime_lease=noop)
    assert outcome.status == "succeeded"
    assert calls == ["verified", "commit"]
    stored = committed[0]
    for key, value in payload.items():
        assert stored[key] == value
    assert stored["answer_receipt"] == receipt
    if delta <= 0:
        assert stored["message"] == message
        assert json_size_bytes(stored) == RUN_RESULT_MAX_BYTES + delta
    else:
        assert stored["answer_body_source"] == "run_events_v4"
        assert stored["message"] != message
        assert json_size_bytes(stored) <= RUN_RESULT_MAX_BYTES
    assert assistant[0][1]["answer_receipt"] == receipt
    assert assistant[0][0] == (message if len(message.encode("utf-8")) <= MESSAGE_CONTENT_MAX_BYTES else stored["message"])
    assert command.result_payload == payload


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["invalid", "retryable", "replay", "oversized_metadata"])
async def test_receipt_budget_preserves_failure_replay_and_rollback(case):
    from app.execution.api import materialize_worker_answer, AnswerPersistenceLimits
    from app.executors.base import ExecutorResult
    from app.streaming.api import AssistantAnswerReceiptError
    from app.platform.postgres.limits import (
        MESSAGE_CONTENT_MAX_BYTES, RUN_RESULT_MAX_BYTES, json_size_bytes,
        ensure_json_size, PersistenceSizeLimitError,
    )

    writes = []
    transactions = []
    captured = []
    receipt = {"schema_version": "test-receipt", "run_id": "run", "attempt_id": "attempt"}

    @asynccontextmanager
    async def transaction():
        pending = []
        try:
            yield pending
        except BaseException:
            transactions.append("rollback")
            raise
        else:
            transactions.append("commit")
            writes.extend(pending)

    async def noop(*args, **kwargs):
        return None

    async def lock_run(*args, **kwargs):
        return {"status": "succeeded" if case == "replay" else "running"}

    async def load_answer(conn, **kwargs):
        assert kwargs == {"tenant_id": "tenant", "run_id": "run", "attempt_id": "attempt", "receipt": receipt}
        if case in {"invalid", "retryable"}:
            raise AssistantAnswerReceiptError(retryable=case == "retryable")
        assert case == "oversized_metadata"
        return SimpleNamespace(text="verified answer")

    async def materialize(capabilities, conn, **kwargs):
        return await materialize_worker_answer(capabilities, conn, **kwargs, limits=AnswerPersistenceLimits(
            MESSAGE_CONTENT_MAX_BYTES, RUN_RESULT_MAX_BYTES, json_size_bytes,
        ))

    async def persist(conn, *args, **kwargs):
        conn.append("persist")

    class Attempt:
        async def is_cancel_requested(self, conn):
            return case != "oversized_metadata"

        async def complete(self, conn, **kwargs):
            assert case == "oversized_metadata"
            ensure_json_size(kwargs["result_json"], max_bytes=RUN_RESULT_MAX_BYTES, code="run_result_too_large")
            pytest.fail("oversized non-message metadata must not bypass the Runs limit")

        async def fail(self, conn, **kwargs):
            assert case == "invalid"
            captured.append(kwargs)
            conn.append("failed")
            return True

        async def classify_success_commit_block(self, conn):
            assert case == "replay"
            return "stale_terminal_state"

    service = WorkerResultCommitService(
        transaction_factory=transaction, lock_run=lock_run,
        reconciliation_claim_current=noop, materialize_answer=materialize,
        persist_artifacts=persist, persist_skill_snapshots=persist,
        persist_assistant=persist, append_user_event=noop,
        append_hidden_event=noop, persist_failure_event=noop,
        public_failure_message=lambda result: "safe failure", prefers_cancelled=lambda result: False,
    )
    command = WorkerResultCommitCommand(
        payload=SimpleNamespace(tenant_id="tenant", run_id="run"),
        result=ExecutorResult(status="succeeded", adapter_version="1", executor_type="test", executor_version="1", capabilities={}, result={}, artifacts=[], executor_payload={"answer_receipt": receipt}),
        result_payload={"extra": "x" * RUN_RESULT_MAX_BYTES} if case == "oversized_metadata" else {"artifacts": []},
        artifact_records=[], skill_snapshot={}, terminal_event_kwargs={}, attempt_id="attempt", trace_id="trace",
    )
    async def commit():
        return await service.commit(command, attempt_authority=Attempt(), capabilities=SimpleNamespace(
            event_persistence=SimpleNamespace(load_answer_by_receipt=load_answer),
        ), release_runtime_lease=noop)

    if case in {"retryable", "oversized_metadata"}:
        error_type = AssistantAnswerReceiptError if case == "retryable" else PersistenceSizeLimitError
        with pytest.raises(error_type):
            await commit()
        assert transactions == ["rollback"]
        assert writes == []
    else:
        outcome = await commit()
        if case == "invalid":
            assert outcome.status == "failed"
            assert captured[0]["error_code"] == "assistant_answer_receipt_invalid"
            assert "answer_receipt" not in captured[0]["result_json"]
            assert writes == ["persist", "persist", "failed"]
        else:
            assert outcome.status == "skipped"
            assert writes == []
            assert transactions == ["rollback", "commit"]
