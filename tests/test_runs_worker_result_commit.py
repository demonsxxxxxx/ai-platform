from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from app.runs.api import WorkerResultCommitCommand, WorkerResultCommitService


@pytest.mark.asyncio
@pytest.mark.parametrize("target", ["succeeded", "failed", "cancelled"])
async def test_lost_terminal_cas_rolls_back_all_worker_result_facts(target):
    transactions = []
    releases = []

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

    service = WorkerResultCommitService(
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

@pytest.mark.asyncio
@pytest.mark.parametrize("body,artifacts,ready", [("", [], False), (" \n", [], False), ("safe", [], True), ("", [{"id": "artifact"}], True)])
async def test_success_preserves_empty_terminal_anchor_without_inventing_response_ready(body, artifacts, ready):
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
    service = WorkerResultCommitService(transaction_factory=transaction, lock_run=lock,
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
