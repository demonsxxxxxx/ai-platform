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
