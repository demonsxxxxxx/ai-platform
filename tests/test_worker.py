from contextlib import asynccontextmanager
import hashlib
import json

import pytest

from app.executors.base import ArtifactManifest, ExecutorResult
from app.executors.fake import FakeFailureAdapter, FakeSuccessAdapter
from app.executors.registry import AdapterRegistry
from app.repositories import RepositoryConflictError
from app.worker import _multi_agent_result_summary, _record_run_step_from_event, process_run_payload


RELEASE_DECISION_SCHEMA_VERSION = "ai-platform.skill-release-decision.v1"


def release_decision(version: str) -> dict:
    return {
        "schema_version": RELEASE_DECISION_SCHEMA_VERSION,
        "policy_active": False,
        "selected_version": version,
        "selected_track": "manifest_pin",
    }


def primary_manifest(skill_id: str, version: str) -> dict:
    return {"skill_id": skill_id, "content_hash": version}


def primary_manifest_version(skill_id: str, manifests: list[dict]) -> str:
    for manifest in manifests:
        if manifest.get("skill_id") == skill_id:
            return str(manifest.get("content_hash") or manifest.get("version") or "")
    return ""


@asynccontextmanager
async def fake_transaction():
    yield object()


async def fake_append_message(*args, **kwargs):
    return "msg-a"


@pytest.fixture(autouse=True)
def default_cancel_not_requested(monkeypatch):
    async def is_cancel_requested(conn, *, tenant_id, run_id):
        return False

    monkeypatch.setattr("app.worker.repositories.is_cancel_requested", is_cancel_requested, raising=False)

    async def get_context_snapshot_for_worker(conn, **kwargs):
        return {
            "id": kwargs["context_snapshot_id"],
            "tenant_id": kwargs["tenant_id"],
            "workspace_id": kwargs["workspace_id"],
            "user_id": kwargs["user_id"],
            "session_id": kwargs["session_id"],
            "run_id": kwargs["run_id"],
            "trace_id": "trace_run_a",
            "schema_version": "ai-platform.context-snapshot.v1",
            "context_kind": "executor",
            "included_message_ids": [],
            "included_file_ids": ["file-a"],
            "included_artifact_ids": [],
            "included_memory_record_ids": [],
            "redaction_summary_json": {},
            "payload_json": {
                "schema_version": "ai-platform.context-snapshot.v1",
                "source": "test",
                "message_count": 0,
                "file_count": 1,
                "memory_record_count": 0,
            },
            "created_at": None,
        }

    monkeypatch.setattr(
        "app.worker.repositories.get_context_snapshot_for_worker",
        get_context_snapshot_for_worker,
        raising=False,
    )

    async def finalize_multi_agent_parent_run_if_ready(conn, **kwargs):
        return None

    monkeypatch.setattr(
        "app.worker.repositories.finalize_multi_agent_parent_run_if_ready",
        finalize_multi_agent_parent_run_if_ready,
        raising=False,
    )
    monkeypatch.setattr("app.worker._PARENT_ROLLUP_RETRY_DELAY_SECONDS", 0, raising=False)


@pytest.mark.asyncio
async def test_reused_step_event_clears_checkpoint_reuse_pending(monkeypatch):
    calls = []

    async def upsert_run_step(conn, **kwargs):
        calls.append(kwargs)
        return "step-a"

    monkeypatch.setattr("app.worker.repositories.upsert_run_step", upsert_run_step, raising=False)

    await _record_run_step_from_event(
        object(),
        tenant_id="tenant-a",
        run_id="run-a",
        event_type="agent_step_reused",
        message="coding agent reused checkpoint",
        payload={
            "role": "coding",
            "step_key": "code",
            "step_index": 1,
            "checkpoint_reused": True,
            "output": "code output",
        },
    )

    assert calls[0]["payload_json"]["checkpoint_reused"] is True
    assert calls[0]["payload_json"]["checkpoint_reuse_pending"] is False


@pytest.mark.asyncio
async def test_completed_step_event_materializes_source_step_id_for_checkpoint(monkeypatch):
    calls = []

    async def upsert_run_step(conn, **kwargs):
        calls.append(kwargs)
        return "step-created"

    monkeypatch.setattr("app.worker.repositories.upsert_run_step", upsert_run_step, raising=False)

    await _record_run_step_from_event(
        object(),
        tenant_id="tenant-a",
        run_id="run-a",
        event_type="agent_step_completed",
        message="coding agent completed",
        payload={
            "role": "coding",
            "step_key": "code",
            "step_index": 1,
            "checkpoint_id": "checkpoint-run-a-code",
            "output": "code output",
        },
    )

    assert calls[0]["payload_json"]["checkpoint_id"] == "checkpoint-run-a-code"
    assert "source_step_id" not in calls[0]["payload_json"]
    assert calls[1]["payload_json"] == {"source_step_id": "step-created"}


@pytest.mark.asyncio
@pytest.mark.parametrize("event_type", ["agent_step_started", "agent_step_completed", "agent_step_failed", "agent_step_blocked"])
async def test_non_pending_step_event_clears_checkpoint_reuse_pending(monkeypatch, event_type):
    calls = []

    async def upsert_run_step(conn, **kwargs):
        calls.append(kwargs)
        return "step-a"

    monkeypatch.setattr("app.worker.repositories.upsert_run_step", upsert_run_step, raising=False)

    await _record_run_step_from_event(
        object(),
        tenant_id="tenant-a",
        run_id="run-a",
        event_type=event_type,
        message="agent step progressed",
        payload={
            "role": "coding",
            "step_key": "code",
            "step_index": 1,
            "checkpoint_reuse_pending": True,
        },
    )

    assert calls[0]["payload_json"]["checkpoint_reuse_pending"] is False


def base_payload(**overrides):
    skill_id = overrides.get("skill_id", "qa-file-reviewer")
    default_version = f"hash-{skill_id}"
    payload = {
        "tenant_id": "tenant-a",
        "workspace_id": "workspace-a",
        "user_id": "user-a",
        "session_id": "session-a",
        "run_id": "run-a",
        "agent_id": "qa-word-review",
        "skill_id": skill_id,
        "file_ids": ["file-a"],
        "input": {"mode": "file"},
        "executor_type": "fake",
        "skill_version": default_version,
        "release_decision": release_decision(default_version),
        "skill_manifests": [primary_manifest(skill_id, default_version)],
        "context_snapshot_id": "ctx-existing",
        "context_snapshot": {
            "schema_version": "ai-platform.context-snapshot.v1",
            "context_snapshot_id": "ctx-existing",
            "source": "test",
            "message_count": 0,
            "file_count": 1,
            "memory_record_count": 0,
        },
    }
    payload.update(overrides)
    manifests = payload.get("skill_manifests") or []
    if "skill_version" not in overrides:
        locked_version = primary_manifest_version(payload["skill_id"], manifests) or payload.get("skill_version")
    else:
        locked_version = payload.get("skill_version")
    if locked_version:
        payload["skill_version"] = locked_version
        if "release_decision" not in overrides:
            payload["release_decision"] = release_decision(locked_version)
    return payload


def test_multi_agent_result_summary_counts_pending_and_cancelled_steps_like_sse_snapshot():
    summary = _multi_agent_result_summary(
        [
            {
                "step_key": "code",
                "status": "pending",
                "role": "coding",
                "sequence": 1,
                "payload_json": {},
            },
            {
                "step_key": "verify",
                "status": "cancelled",
                "role": "test",
                "sequence": 2,
                "payload_json": {},
            },
        ]
    )

    assert summary["counts"] == {
        "total": 2,
        "pending": 1,
        "succeeded": 0,
        "failed": 0,
        "running": 0,
        "cancelled": 1,
        "reused": 0,
        "blocked": 0,
    }


def test_multi_agent_result_summary_normalizes_legacy_canceled_step_status():
    summary = _multi_agent_result_summary(
        [
            {
                "step_key": "verify",
                "status": "canceled",
                "role": "test",
                "sequence": 1,
                "payload_json": {},
            },
        ]
    )

    assert summary["steps"][0]["status"] == "cancelled"
    assert summary["counts"]["cancelled"] == 1


def test_multi_agent_result_summary_preserves_step_governance_context():
    summary = _multi_agent_result_summary(
        [
            {
                "step_key": "verify",
                "status": "succeeded",
                "role": "test",
                "sequence": 2,
                "payload_json": {
                    "depends_on": ["code"],
                    "output": "verify output",
                    "skill_ids": ["qa-file-reviewer"],
                    "mcp_tool_ids": ["ragflow-knowledge-search"],
                    "resource_limits": {"max_tool_calls": 3},
                    "sandbox_mode": "ephemeral",
                    "browser_enabled": True,
                },
            }
        ]
    )

    assert summary["steps"][0]["skill_ids"] == ["qa-file-reviewer"]
    assert summary["steps"][0]["mcp_tool_ids"] == ["ragflow-knowledge-search"]
    assert summary["steps"][0]["resource_limits"] == {"max_tool_calls": 3}
    assert summary["steps"][0]["sandbox_mode"] == "ephemeral"
    assert summary["steps"][0]["browser_enabled"] is True


@pytest.mark.asyncio
async def test_worker_completes_successful_adapter_run(monkeypatch):
    calls = []

    async def mark_run_running(conn, *, tenant_id, run_id):
        calls.append(("running", tenant_id, run_id))
        return True

    async def append_event(conn, *, tenant_id, run_id, event_type, stage, message, payload=None):
        calls.append(("event", event_type, stage, message))

    async def create_artifact(conn, **kwargs):
        calls.append(("artifact", kwargs["artifact_type"], kwargs["storage_key"]))

    async def complete_run(conn, *, tenant_id, run_id, result_json):
        assert "payload" not in result_json["executor"]
        assert result_json["skills"] == {
            "allowed_skills": [],
            "staged_skills": [],
            "used_skills": [],
        }
        calls.append(("complete", result_json["executor"]["adapter_version"]))

    monkeypatch.setattr("app.worker.transaction", fake_transaction)
    monkeypatch.setattr("app.worker.repositories.mark_run_running", mark_run_running)
    monkeypatch.setattr("app.worker.repositories.append_event", append_event)
    monkeypatch.setattr("app.worker.repositories.create_artifact", create_artifact)
    monkeypatch.setattr("app.worker.repositories.complete_run", complete_run)
    monkeypatch.setattr("app.worker.repositories.append_message", fake_append_message)

    outcome = await process_run_payload(base_payload(), AdapterRegistry({"fake": FakeSuccessAdapter()}))

    assert outcome.status == "succeeded"
    assert ("running", "tenant-a", "run-a") in calls
    assert any(item[0] == "artifact" for item in calls)
    assert ("complete", "fake-adapter/1") in calls
    assert calls[-1] == ("event", "status", "worker", "Run succeeded")


@pytest.mark.asyncio
async def test_worker_reconciles_multi_agent_child_after_success(monkeypatch):
    calls = []

    child_input = {
        "mode": "file",
        "multi_agent_dispatch": {
            "parent_run_id": "run-parent",
            "parent_step_id": "step-code",
            "dispatch_id": "dispatch-code",
            "step_key": "code",
        },
    }

    async def mark_run_running(conn, *, tenant_id, run_id):
        return True

    async def append_event(conn, **kwargs):
        calls.append(("event", kwargs["event_type"]))
        return "evt-a"

    async def create_artifact(conn, **kwargs):
        return None

    async def complete_run(conn, *, tenant_id, run_id, result_json):
        calls.append(("complete", run_id, result_json["message"]))

    async def reconcile(conn, **kwargs):
        calls.append(("reconcile", kwargs))
        return {"parent_run_id": "run-parent"}

    monkeypatch.setattr("app.worker.transaction", fake_transaction)
    monkeypatch.setattr("app.worker.repositories.mark_run_running", mark_run_running)
    monkeypatch.setattr("app.worker.repositories.append_event", append_event)
    monkeypatch.setattr("app.worker.repositories.create_artifact", create_artifact)
    monkeypatch.setattr("app.worker.repositories.complete_run", complete_run)
    monkeypatch.setattr("app.worker.repositories.reconcile_multi_agent_child_run_terminal_state", reconcile)
    monkeypatch.setattr("app.worker.repositories.append_message", fake_append_message)

    outcome = await process_run_payload(
        base_payload(run_id="run-child", input=child_input),
        AdapterRegistry({"fake": FakeSuccessAdapter()}),
    )

    assert outcome.status == "succeeded"
    complete_index = next(index for index, item in enumerate(calls) if item[0] == "complete")
    reconcile_index = next(index for index, item in enumerate(calls) if item[0] == "reconcile")
    assert complete_index < reconcile_index
    reconcile_call = calls[reconcile_index][1]
    assert reconcile_call["tenant_id"] == "tenant-a"
    assert reconcile_call["child_run_id"] == "run-child"
    assert reconcile_call["child_status"] == "succeeded"
    assert reconcile_call["result_json"]["message"].startswith("fake run completed for run-child")


@pytest.mark.asyncio
async def test_worker_retries_multi_agent_parent_rollup_after_child_transaction_commit(monkeypatch):
    calls = []
    tx_counter = 0
    tx_events = []

    @asynccontextmanager
    async def recording_transaction():
        nonlocal tx_counter
        tx_counter += 1
        tx_label = f"tx-{tx_counter}"
        tx_events.append(("enter", tx_label))
        try:
            yield tx_label
        except BaseException:
            tx_events.append(("rollback", tx_label))
            raise
        else:
            tx_events.append(("commit", tx_label))
        finally:
            tx_events.append(("exit", tx_label))

    child_input = {
        "mode": "file",
        "multi_agent_dispatch": {
            "parent_run_id": "run-parent",
            "parent_step_id": "step-code",
            "dispatch_id": "dispatch-code",
            "step_key": "code",
        },
    }

    async def mark_run_running(conn, *, tenant_id, run_id):
        return True

    async def append_event(conn, **kwargs):
        calls.append(("event", conn, kwargs["event_type"]))
        return "evt-a"

    async def create_artifact(conn, **kwargs):
        return None

    async def complete_run(conn, *, tenant_id, run_id, result_json):
        calls.append(("complete", conn, run_id))

    async def reconcile(conn, **kwargs):
        calls.append(("reconcile", conn, kwargs))
        return {"parent_run_id": "run-parent"}

    async def finalize(conn, **kwargs):
        calls.append(("finalize", conn, kwargs))
        if len([item for item in calls if item[0] == "finalize"]) == 1:
            return None
        return {"parent_run_id": "run-parent", "status": "succeeded"}

    monkeypatch.setattr("app.worker.transaction", recording_transaction)
    monkeypatch.setattr("app.worker.repositories.mark_run_running", mark_run_running)
    monkeypatch.setattr("app.worker.repositories.append_event", append_event)
    monkeypatch.setattr("app.worker.repositories.create_artifact", create_artifact)
    monkeypatch.setattr("app.worker.repositories.complete_run", complete_run)
    monkeypatch.setattr("app.worker.repositories.reconcile_multi_agent_child_run_terminal_state", reconcile)
    monkeypatch.setattr("app.worker.repositories.finalize_multi_agent_parent_run_if_ready", finalize)
    monkeypatch.setattr("app.worker.repositories.append_message", fake_append_message)

    outcome = await process_run_payload(
        base_payload(run_id="run-child", input=child_input),
        AdapterRegistry({"fake": FakeSuccessAdapter()}),
    )

    assert outcome.status == "succeeded"
    reconcile_call = next(item for item in calls if item[0] == "reconcile")
    finalize_calls = [item for item in calls if item[0] == "finalize"]
    assert len(finalize_calls) == 2
    assert finalize_calls[0][1] != reconcile_call[1]
    assert finalize_calls[1][1] != reconcile_call[1]
    assert finalize_calls[0][2] == {
        "tenant_id": "tenant-a",
        "parent_run_id": "run-parent",
        "triggered_by_child_run_id": "run-child",
    }
    assert finalize_calls[1][2] == finalize_calls[0][2]
    assert tx_events.index(("exit", reconcile_call[1])) < tx_events.index(("enter", finalize_calls[0][1]))
    assert tx_events.index(("exit", finalize_calls[0][1])) < tx_events.index(("enter", finalize_calls[1][1]))


@pytest.mark.asyncio
async def test_worker_reconciles_multi_agent_child_after_failure(monkeypatch):
    calls = []

    child_input = {
        "mode": "file",
        "multi_agent_dispatch": {
            "parent_run_id": "run-parent",
            "parent_step_id": "step-code",
            "dispatch_id": "dispatch-code",
            "step_key": "code",
        },
    }

    async def mark_run_running(conn, *, tenant_id, run_id):
        return True

    async def append_event(conn, **kwargs):
        calls.append(("event", kwargs["event_type"]))
        return "evt-a"

    async def fail_run(conn, *, tenant_id, run_id, error_code, error_message, result_json=None):
        calls.append(("fail", run_id, error_code, error_message, result_json))

    async def reconcile(conn, **kwargs):
        calls.append(("reconcile", kwargs))
        return {"parent_run_id": "run-parent"}

    monkeypatch.setattr("app.worker.transaction", fake_transaction)
    monkeypatch.setattr("app.worker.repositories.mark_run_running", mark_run_running)
    monkeypatch.setattr("app.worker.repositories.append_event", append_event)
    monkeypatch.setattr("app.worker.repositories.fail_run", fail_run)
    monkeypatch.setattr("app.worker.repositories.reconcile_multi_agent_child_run_terminal_state", reconcile)

    outcome = await process_run_payload(
        base_payload(run_id="run-child", input=child_input),
        AdapterRegistry({"fake": FakeFailureAdapter()}),
    )

    assert outcome.status == "failed"
    fail_index = next(index for index, item in enumerate(calls) if item[0] == "fail")
    reconcile_index = next(index for index, item in enumerate(calls) if item[0] == "reconcile")
    assert fail_index < reconcile_index
    reconcile_call = calls[reconcile_index][1]
    assert reconcile_call["child_run_id"] == "run-child"
    assert reconcile_call["child_status"] == "failed"
    assert reconcile_call["error_code"] == "fake_failure"
    assert reconcile_call["error_message"] == "fake run failed for run-child"


@pytest.mark.asyncio
async def test_worker_reconciles_multi_agent_child_after_cancel(monkeypatch):
    calls = []
    cancel_checks = 0

    child_input = {
        "mode": "file",
        "multi_agent_dispatch": {
            "parent_run_id": "run-parent",
            "parent_step_id": "step-code",
            "dispatch_id": "dispatch-code",
            "step_key": "code",
        },
    }

    class StreamingAdapter:
        async def submit_run(self, payload, event_sink=None):
            await event_sink(
                event_type="assistant_delta",
                stage="message",
                message="partial",
                payload={"delta": "partial"},
            )
            return ExecutorResult(
                status="succeeded",
                adapter_version="adapter/1",
                executor_type="fake",
                executor_version="fake/1",
                capabilities={"streaming": True},
                result={"message": "should not complete"},
            )

    async def mark_run_running(conn, *, tenant_id, run_id):
        return True

    async def is_cancel_requested(conn, *, tenant_id, run_id):
        nonlocal cancel_checks
        cancel_checks += 1
        return cancel_checks >= 2

    async def append_event(conn, **kwargs):
        calls.append(("event", kwargs["event_type"]))
        return "evt-a"

    async def cancel_run(conn, *, tenant_id, run_id, result_json=None):
        calls.append(("cancel", run_id, result_json))

    async def reconcile(conn, **kwargs):
        calls.append(("reconcile", kwargs))
        return {"parent_run_id": "run-parent"}

    monkeypatch.setattr("app.worker.transaction", fake_transaction)
    monkeypatch.setattr("app.worker.repositories.mark_run_running", mark_run_running)
    monkeypatch.setattr("app.worker.repositories.is_cancel_requested", is_cancel_requested)
    monkeypatch.setattr("app.worker.repositories.append_event", append_event)
    monkeypatch.setattr("app.worker.repositories.cancel_run", cancel_run)
    monkeypatch.setattr("app.worker.repositories.reconcile_multi_agent_child_run_terminal_state", reconcile)

    outcome = await process_run_payload(
        base_payload(run_id="run-child", input=child_input),
        AdapterRegistry({"fake": StreamingAdapter()}),
    )

    assert outcome.status == "cancelled"
    cancel_index = next(index for index, item in enumerate(calls) if item[0] == "cancel")
    reconcile_index = next(index for index, item in enumerate(calls) if item[0] == "reconcile")
    assert cancel_index < reconcile_index
    reconcile_call = calls[reconcile_index][1]
    assert reconcile_call["child_run_id"] == "run-child"
    assert reconcile_call["child_status"] == "cancelled"
    assert reconcile_call["result_json"] == {"message": "任务已取消"}


@pytest.mark.asyncio
async def test_worker_does_not_reconcile_ordinary_run(monkeypatch):
    calls = []

    async def mark_run_running(conn, *, tenant_id, run_id):
        return True

    async def append_event(conn, **kwargs):
        return "evt-a"

    async def create_artifact(conn, **kwargs):
        return None

    async def complete_run(conn, *, tenant_id, run_id, result_json):
        calls.append(("complete", run_id))

    async def reconcile(conn, **kwargs):
        calls.append(("reconcile", kwargs))
        return {"parent_run_id": "run-parent"}

    monkeypatch.setattr("app.worker.transaction", fake_transaction)
    monkeypatch.setattr("app.worker.repositories.mark_run_running", mark_run_running)
    monkeypatch.setattr("app.worker.repositories.append_event", append_event)
    monkeypatch.setattr("app.worker.repositories.create_artifact", create_artifact)
    monkeypatch.setattr("app.worker.repositories.complete_run", complete_run)
    monkeypatch.setattr("app.worker.repositories.reconcile_multi_agent_child_run_terminal_state", reconcile)
    monkeypatch.setattr("app.worker.repositories.append_message", fake_append_message)

    outcome = await process_run_payload(base_payload(), AdapterRegistry({"fake": FakeSuccessAdapter()}))

    assert outcome.status == "succeeded"
    assert ("complete", "run-a") in calls
    assert not any(item[0] == "reconcile" for item in calls)


@pytest.mark.asyncio
async def test_worker_reconciles_multi_agent_child_after_executor_exception(monkeypatch):
    calls = []

    child_input = {
        "mode": "file",
        "multi_agent_dispatch": {
            "parent_run_id": "run-parent",
            "parent_step_id": "step-code",
            "dispatch_id": "dispatch-code",
            "step_key": "code",
        },
    }

    class RaisingAdapter:
        async def submit_run(self, payload, event_sink=None):
            raise RuntimeError("executor crashed")

    async def mark_run_running(conn, *, tenant_id, run_id):
        return True

    async def append_event(conn, **kwargs):
        calls.append(("event", kwargs["event_type"]))
        return "evt-a"

    async def fail_run(conn, *, tenant_id, run_id, error_code, error_message, result_json=None):
        calls.append(("fail", run_id, error_code, error_message, result_json))

    async def reconcile(conn, **kwargs):
        calls.append(("reconcile", kwargs))
        return {"parent_run_id": "run-parent"}

    monkeypatch.setattr("app.worker.transaction", fake_transaction)
    monkeypatch.setattr("app.worker.repositories.mark_run_running", mark_run_running)
    monkeypatch.setattr("app.worker.repositories.append_event", append_event)
    monkeypatch.setattr("app.worker.repositories.fail_run", fail_run)
    monkeypatch.setattr("app.worker.repositories.reconcile_multi_agent_child_run_terminal_state", reconcile)

    outcome = await process_run_payload(
        base_payload(run_id="run-child", input=child_input),
        AdapterRegistry({"fake": RaisingAdapter()}),
    )

    assert outcome.status == "failed"
    fail_index = next(index for index, item in enumerate(calls) if item[0] == "fail")
    reconcile_index = next(index for index, item in enumerate(calls) if item[0] == "reconcile")
    assert fail_index < reconcile_index
    reconcile_call = calls[reconcile_index][1]
    assert reconcile_call["child_run_id"] == "run-child"
    assert reconcile_call["child_status"] == "failed"
    assert reconcile_call["error_code"] == "executor_failure"
    assert reconcile_call["error_message"] == "executor crashed"


@pytest.mark.asyncio
async def test_worker_reconciles_multi_agent_child_after_unknown_executor(monkeypatch):
    calls = []

    child_input = {
        "mode": "file",
        "multi_agent_dispatch": {
            "parent_run_id": "run-parent",
            "parent_step_id": "step-code",
            "dispatch_id": "dispatch-code",
            "step_key": "code",
        },
    }

    async def mark_run_running(conn, *, tenant_id, run_id):
        return True

    async def append_event(conn, **kwargs):
        calls.append(("event", kwargs["event_type"]))
        return "evt-a"

    async def fail_run(conn, *, tenant_id, run_id, error_code, error_message, result_json=None):
        calls.append(("fail", run_id, error_code, error_message, result_json))

    async def reconcile(conn, **kwargs):
        calls.append(("reconcile", kwargs))
        return {"parent_run_id": "run-parent"}

    monkeypatch.setattr("app.worker.transaction", fake_transaction)
    monkeypatch.setattr("app.worker.repositories.mark_run_running", mark_run_running)
    monkeypatch.setattr("app.worker.repositories.append_event", append_event)
    monkeypatch.setattr("app.worker.repositories.fail_run", fail_run)
    monkeypatch.setattr("app.worker.repositories.reconcile_multi_agent_child_run_terminal_state", reconcile)

    outcome = await process_run_payload(
        base_payload(run_id="run-child", executor_type="missing", input=child_input),
        AdapterRegistry({"fake": FakeSuccessAdapter()}),
    )

    assert outcome.status == "failed"
    fail_index = next(index for index, item in enumerate(calls) if item[0] == "fail")
    reconcile_index = next(index for index, item in enumerate(calls) if item[0] == "reconcile")
    assert fail_index < reconcile_index
    reconcile_call = calls[reconcile_index][1]
    assert reconcile_call["child_run_id"] == "run-child"
    assert reconcile_call["child_status"] == "failed"
    assert reconcile_call["error_code"] == "unknown_executor_type"


@pytest.mark.asyncio
async def test_worker_retries_parent_rollup_after_early_unknown_executor_reconciliation(monkeypatch):
    calls = []
    tx_counter = 0
    tx_events = []

    @asynccontextmanager
    async def recording_transaction():
        nonlocal tx_counter
        tx_counter += 1
        tx_label = f"tx-{tx_counter}"
        tx_events.append(("enter", tx_label))
        try:
            yield tx_label
        except BaseException:
            tx_events.append(("rollback", tx_label))
            raise
        else:
            tx_events.append(("commit", tx_label))
        finally:
            tx_events.append(("exit", tx_label))

    child_input = {
        "mode": "file",
        "multi_agent_dispatch": {
            "parent_run_id": "run-parent",
            "parent_step_id": "step-code",
            "dispatch_id": "dispatch-code",
            "step_key": "code",
        },
    }

    async def mark_run_running(conn, *, tenant_id, run_id):
        return True

    async def append_event(conn, **kwargs):
        calls.append(("event", conn, kwargs["event_type"]))
        return "evt-a"

    async def fail_run(conn, *, tenant_id, run_id, error_code, error_message, result_json=None):
        calls.append(("fail", conn, error_code))

    async def reconcile(conn, **kwargs):
        calls.append(("reconcile", conn, kwargs))
        return {"parent_run_id": "run-parent"}

    async def finalize(conn, **kwargs):
        calls.append(("finalize", conn, kwargs))
        return {"parent_run_id": "run-parent", "status": "failed"}

    monkeypatch.setattr("app.worker.transaction", recording_transaction)
    monkeypatch.setattr("app.worker.repositories.mark_run_running", mark_run_running)
    monkeypatch.setattr("app.worker.repositories.append_event", append_event)
    monkeypatch.setattr("app.worker.repositories.fail_run", fail_run)
    monkeypatch.setattr("app.worker.repositories.reconcile_multi_agent_child_run_terminal_state", reconcile)
    monkeypatch.setattr("app.worker.repositories.finalize_multi_agent_parent_run_if_ready", finalize)

    outcome = await process_run_payload(
        base_payload(run_id="run-child", executor_type="missing", input=child_input),
        AdapterRegistry({"fake": FakeSuccessAdapter()}),
    )

    assert outcome.status == "failed"
    assert outcome.error_code == "unknown_executor_type"
    reconcile_call = next(item for item in calls if item[0] == "reconcile")
    finalize_call = next(item for item in calls if item[0] == "finalize")
    assert finalize_call[1] != reconcile_call[1]
    assert finalize_call[2] == {
        "tenant_id": "tenant-a",
        "parent_run_id": "run-parent",
        "triggered_by_child_run_id": "run-child",
    }
    assert ("commit", reconcile_call[1]) in tx_events
    assert ("rollback", reconcile_call[1]) not in tx_events
    assert tx_events.index(("exit", reconcile_call[1])) < tx_events.index(("enter", finalize_call[1]))


@pytest.mark.asyncio
async def test_worker_passes_skill_manifest_pins_to_executor(monkeypatch):
    captured = {}

    class CaptureAdapter:
        async def submit_run(self, payload, event_sink=None):
            captured["payload"] = payload
            return ExecutorResult(
                status="succeeded",
                adapter_version="capture-adapter/1",
                executor_type="claude-agent-worker",
                executor_version="capture/1",
                capabilities={},
                result={"message": "done"},
            )

    async def mark_run_running(conn, *, tenant_id, run_id):
        return True

    async def append_event(conn, **kwargs):
        return "evt-a"

    async def complete_run(conn, **kwargs):
        return None

    monkeypatch.setattr("app.worker.transaction", fake_transaction)
    monkeypatch.setattr("app.worker.repositories.mark_run_running", mark_run_running)
    monkeypatch.setattr("app.worker.repositories.append_event", append_event)
    monkeypatch.setattr("app.worker.repositories.complete_run", complete_run)
    monkeypatch.setattr("app.worker.repositories.create_artifact", lambda *args, **kwargs: None)
    monkeypatch.setattr("app.worker.repositories.append_message", fake_append_message)

    outcome = await process_run_payload(
        base_payload(
            executor_type="claude-agent-worker",
            skill_version="hash-primary",
            skill_manifests=[{"skill_id": "qa-file-reviewer", "content_hash": "hash-primary"}],
        ),
        AdapterRegistry({"claude-agent-worker": CaptureAdapter()}),
    )

    assert outcome.status == "succeeded"
    assert captured["payload"].skill_version == "hash-primary"
    assert captured["payload"].skill_manifests == [{"skill_id": "qa-file-reviewer", "content_hash": "hash-primary"}]


@pytest.mark.asyncio
async def test_worker_refreshes_missing_context_snapshot_before_executor(monkeypatch):
    calls = []
    captured = {}

    class CaptureAdapter:
        async def submit_run(self, payload, event_sink=None):
            captured["payload"] = payload
            return ExecutorResult(
                status="succeeded",
                adapter_version="capture-adapter/1",
                executor_type="claude-agent-worker",
                executor_version="capture/1",
                capabilities={},
                result={"message": "done"},
            )

    async def mark_run_running(conn, *, tenant_id, run_id):
        calls.append(("running", tenant_id, run_id))
        return True

    async def append_event(conn, **kwargs):
        calls.append(("event", kwargs["event_type"], kwargs["stage"]))
        return "evt-a"

    async def record_context(conn, **kwargs):
        calls.append(("context", kwargs))
        return {
            "schema_version": "ai-platform.context-snapshot.v1",
            "context_snapshot_id": "ctx_worker_refresh",
            "source": kwargs["source"],
            "message_count": len(kwargs.get("message_ids") or []),
            "file_count": len(kwargs.get("file_ids") or []),
            "memory_record_count": 0,
        }

    async def complete_run(conn, **kwargs):
        calls.append(("complete", kwargs["run_id"]))

    monkeypatch.setattr("app.worker.transaction", fake_transaction)
    monkeypatch.setattr("app.worker.repositories.mark_run_running", mark_run_running)
    monkeypatch.setattr("app.worker.repositories.append_event", append_event)
    monkeypatch.setattr("app.worker.record_initial_context_snapshot", record_context)
    monkeypatch.setattr("app.worker.repositories.complete_run", complete_run)
    monkeypatch.setattr("app.worker.repositories.create_artifact", lambda *args, **kwargs: None)
    monkeypatch.setattr("app.worker.repositories.append_message", fake_append_message)

    outcome = await process_run_payload(
        base_payload(
            executor_type="claude-agent-worker",
            context_snapshot_id="",
            context_snapshot={},
        ),
        AdapterRegistry({"claude-agent-worker": CaptureAdapter()}),
    )

    assert outcome.status == "succeeded"
    context_call = next(item[1] for item in calls if item[0] == "context")
    assert context_call["source"] == "worker_refresh"
    assert context_call["tenant_id"] == "tenant-a"
    assert context_call["workspace_id"] == "workspace-a"
    assert context_call["user_id"] == "user-a"
    assert context_call["session_id"] == "session-a"
    assert context_call["run_id"] == "run-a"
    assert context_call["trace_id"] == "trace_run_a"
    assert context_call["agent_id"] == "qa-word-review"
    assert context_call["skill_id"] == "qa-file-reviewer"
    assert context_call["input_payload"] == {"mode": "file"}
    assert context_call["message_ids"] == []
    assert context_call["file_ids"] == ["file-a"]
    assert captured["payload"].context_snapshot_id == "ctx_worker_refresh"
    assert captured["payload"].context_snapshot["source"] == "worker_refresh"
    assert ("complete", "run-a") in calls


@pytest.mark.asyncio
async def test_worker_uses_scoped_db_context_snapshot_instead_of_queue_copy(monkeypatch):
    captured = {}

    class CaptureAdapter:
        async def submit_run(self, payload, event_sink=None):
            captured["payload"] = payload
            return ExecutorResult(
                status="succeeded",
                adapter_version="capture-adapter/1",
                executor_type="claude-agent-worker",
                executor_version="capture/1",
                capabilities={},
                result={"message": "done"},
            )

    async def mark_run_running(conn, *, tenant_id, run_id):
        return True

    async def append_event(conn, **kwargs):
        return "evt-a"

    async def get_context_snapshot_for_worker(conn, **kwargs):
        assert kwargs == {
            "tenant_id": "tenant-a",
            "workspace_id": "workspace-a",
            "user_id": "user-a",
            "session_id": "session-a",
            "run_id": "run-a",
            "context_snapshot_id": "ctx-existing",
        }
        return {
            "id": "ctx-existing",
            "tenant_id": "tenant-a",
            "workspace_id": "workspace-a",
            "user_id": "user-a",
            "session_id": "session-a",
            "run_id": "run-a",
            "trace_id": "trace_run_a",
            "schema_version": "ai-platform.context-snapshot.v1",
            "context_kind": "executor",
            "included_message_ids": [],
            "included_file_ids": ["file-a"],
            "included_artifact_ids": [],
            "included_memory_record_ids": [],
            "redaction_summary_json": {},
            "payload_json": {
                "schema_version": "ai-platform.context-snapshot.v1",
                "source": "db_scoped",
                "message_count": 0,
                "file_count": 1,
                "memory_record_count": 0,
            },
            "created_at": None,
        }

    async def fail_record_context(*args, **kwargs):
        raise AssertionError("verified queue snapshots must be reconstructed from DB scope")

    async def complete_run(conn, **kwargs):
        return None

    monkeypatch.setattr("app.worker.transaction", fake_transaction)
    monkeypatch.setattr("app.worker.repositories.mark_run_running", mark_run_running)
    monkeypatch.setattr("app.worker.repositories.append_event", append_event)
    monkeypatch.setattr("app.worker.repositories.get_context_snapshot_for_worker", get_context_snapshot_for_worker)
    monkeypatch.setattr("app.worker.record_initial_context_snapshot", fail_record_context)
    monkeypatch.setattr("app.worker.repositories.complete_run", complete_run)
    monkeypatch.setattr("app.worker.repositories.create_artifact", lambda *args, **kwargs: None)
    monkeypatch.setattr("app.worker.repositories.append_message", fake_append_message)

    outcome = await process_run_payload(
        base_payload(
            executor_type="claude-agent-worker",
            context_snapshot={"source": "tampered_queue_copy"},
        ),
        AdapterRegistry({"claude-agent-worker": CaptureAdapter()}),
    )

    assert outcome.status == "succeeded"
    assert captured["payload"].context_snapshot_id == "ctx-existing"
    assert captured["payload"].context_snapshot["source"] == "stored_context_snapshot"
    assert captured["payload"].context_snapshot["used_context_summary"]["source"] == "stored_context_snapshot"
    assert "tampered_queue_copy" not in json.dumps(captured["payload"].context_snapshot, ensure_ascii=False)


@pytest.mark.asyncio
async def test_worker_uses_scoped_db_context_snapshot_when_queue_copy_missing(monkeypatch):
    captured = {}

    class CaptureAdapter:
        async def submit_run(self, payload, event_sink=None):
            captured["payload"] = payload
            return ExecutorResult(
                status="succeeded",
                adapter_version="capture-adapter/1",
                executor_type="claude-agent-worker",
                executor_version="capture/1",
                capabilities={},
                result={"message": "done"},
            )

    async def mark_run_running(conn, *, tenant_id, run_id):
        return True

    async def append_event(conn, **kwargs):
        return "evt-context-id-only"

    async def get_context_snapshot_for_worker(conn, **kwargs):
        assert kwargs["context_snapshot_id"] == "ctx-existing"
        return {
            "id": "ctx-existing",
            "tenant_id": kwargs["tenant_id"],
            "workspace_id": kwargs["workspace_id"],
            "user_id": kwargs["user_id"],
            "session_id": kwargs["session_id"],
            "run_id": kwargs["run_id"],
            "trace_id": "trace_run_a",
            "schema_version": "ai-platform.context-snapshot.v1",
            "context_kind": "executor",
            "included_message_ids": ["msg-a"],
            "included_file_ids": ["file-a"],
            "included_artifact_ids": [],
            "included_memory_record_ids": [],
            "redaction_summary_json": {},
            "payload_json": {"window": "current"},
            "created_at": None,
        }

    async def fail_record_context(*args, **kwargs):
        raise AssertionError("context_snapshot_id-only payload must resolve scoped DB snapshot")

    async def complete_run(conn, **kwargs):
        return None

    monkeypatch.setattr("app.worker.transaction", fake_transaction)
    monkeypatch.setattr("app.worker.repositories.mark_run_running", mark_run_running)
    monkeypatch.setattr("app.worker.repositories.append_event", append_event)
    monkeypatch.setattr("app.worker.repositories.get_context_snapshot_for_worker", get_context_snapshot_for_worker)
    monkeypatch.setattr("app.worker.record_initial_context_snapshot", fail_record_context)
    monkeypatch.setattr("app.worker.repositories.complete_run", complete_run)
    monkeypatch.setattr("app.worker.repositories.create_artifact", lambda *args, **kwargs: None)
    monkeypatch.setattr("app.worker.repositories.append_message", fake_append_message)

    outcome = await process_run_payload(
        base_payload(
            executor_type="claude-agent-worker",
            context_snapshot_id="ctx-existing",
            context_snapshot={},
        ),
        AdapterRegistry({"claude-agent-worker": CaptureAdapter()}),
    )

    assert outcome.status == "succeeded"
    assert captured["payload"].context_snapshot_id == "ctx-existing"
    assert captured["payload"].context_snapshot["used_context_summary"] == {
        "source": "stored_context_snapshot",
        "input_keys": ["attachments", "window"],
        "memory_policy_source": "not_recorded",
        "long_term_memory_read": False,
    }


@pytest.mark.asyncio
async def test_worker_preserves_stored_safe_summary_metadata_when_payload_has_only_provenance(monkeypatch):
    captured = {}

    class CaptureAdapter:
        async def submit_run(self, payload, event_sink=None):
            captured["payload"] = payload
            return ExecutorResult(
                status="succeeded",
                adapter_version="capture-adapter/1",
                executor_type="claude-agent-worker",
                executor_version="capture/1",
                capabilities={},
                result={"message": "done"},
            )

    async def mark_run_running(conn, *, tenant_id, run_id):
        return True

    async def append_event(conn, **kwargs):
        return "evt-context"

    async def get_context_snapshot_for_worker(conn, **kwargs):
        return {
            "id": "ctx-existing",
            "tenant_id": "tenant-a",
            "workspace_id": "workspace-a",
            "user_id": "user-a",
            "session_id": "session-a",
            "run_id": "run-a",
            "trace_id": "trace_run_a",
            "schema_version": "ai-platform.context-snapshot.v1",
            "context_kind": "executor",
            "included_message_ids": ["msg-a"],
            "included_file_ids": ["file-a"],
            "included_artifact_ids": [],
            "included_memory_record_ids": [],
            "redaction_summary_json": {},
            "payload_json": {
                "memory_policy": {
                    "source": "stored",
                    "memory_enabled": False,
                    "long_term_memory_enabled": False,
                    "retention_days": 30,
                },
                "used_context_summary": {
                    "source": "runs_api",
                    "input_keys": ["message", "attachments", "raw_storage_key"],
                    "memory_policy_source": "stored",
                    "long_term_memory_read": True,
                },
                "execution_tier": "document_worker",
                "latest_artifact_version": "v7",
                "context_pack_generated_at": "2026-06-12T01:23:45Z",
            },
            "created_at": None,
        }

    async def fail_record_context(*args, **kwargs):
        raise AssertionError("context_snapshot_id-only payload must resolve scoped DB snapshot")

    async def complete_run(conn, **kwargs):
        return None

    monkeypatch.setattr("app.worker.transaction", fake_transaction)
    monkeypatch.setattr("app.worker.repositories.mark_run_running", mark_run_running)
    monkeypatch.setattr("app.worker.repositories.append_event", append_event)
    monkeypatch.setattr("app.worker.repositories.get_context_snapshot_for_worker", get_context_snapshot_for_worker)
    monkeypatch.setattr("app.worker.record_initial_context_snapshot", fail_record_context)
    monkeypatch.setattr("app.worker.repositories.complete_run", complete_run)
    monkeypatch.setattr("app.worker.repositories.create_artifact", lambda *args, **kwargs: None)
    monkeypatch.setattr("app.worker.repositories.append_message", fake_append_message)

    outcome = await process_run_payload(
        base_payload(
            executor_type="claude-agent-worker",
            context_snapshot_id="ctx-existing",
            context_snapshot={},
        ),
        AdapterRegistry({"claude-agent-worker": CaptureAdapter()}),
    )

    assert outcome.status == "succeeded"
    assert captured["payload"].context_snapshot["used_context_summary"] == {
        "source": "runs_api",
        "input_keys": ["attachments", "message"],
        "memory_policy_source": "stored",
        "long_term_memory_read": True,
    }
    assert captured["payload"].context_snapshot["execution_tier"] == "document_worker"
    assert captured["payload"].context_snapshot["latest_artifact_version"] == "v7"
    assert captured["payload"].context_snapshot["context_pack_generated_at"] == "2026-06-12T01:23:45Z"
    serialized = json.dumps(captured["payload"].context_snapshot, ensure_ascii=False).lower()
    assert "raw_storage_key" not in serialized


@pytest.mark.asyncio
async def test_worker_preserves_safe_top_level_legacy_context_source(monkeypatch):
    captured = {}

    class CaptureAdapter:
        async def submit_run(self, payload, event_sink=None):
            captured["payload"] = payload
            return ExecutorResult(
                status="succeeded",
                adapter_version="capture-adapter/1",
                executor_type="claude-agent-worker",
                executor_version="capture/1",
                capabilities={},
                result={"message": "done"},
            )

    async def mark_run_running(conn, *, tenant_id, run_id):
        return True

    async def append_event(conn, **kwargs):
        return "evt-context"

    async def get_context_snapshot_for_worker(conn, **kwargs):
        return {
            "id": "ctx-existing",
            "tenant_id": "tenant-a",
            "workspace_id": "workspace-a",
            "user_id": "user-a",
            "session_id": "session-a",
            "run_id": "run-a",
            "trace_id": "trace_run_a",
            "schema_version": "ai-platform.context-snapshot.v1",
            "context_kind": "executor",
            "included_message_ids": ["msg-a"],
            "included_file_ids": [],
            "included_artifact_ids": [],
            "included_memory_record_ids": [],
            "redaction_summary_json": {},
            "payload_json": {
                "source": "chat_stream",
                "message": "hello",
                "context_pack_generated_at": "2026-06-12T01:23:45Z",
            },
            "created_at": None,
        }

    async def fail_record_context(*args, **kwargs):
        raise AssertionError("context_snapshot_id-only payload must resolve scoped DB snapshot")

    async def complete_run(conn, **kwargs):
        return None

    monkeypatch.setattr("app.worker.transaction", fake_transaction)
    monkeypatch.setattr("app.worker.repositories.mark_run_running", mark_run_running)
    monkeypatch.setattr("app.worker.repositories.append_event", append_event)
    monkeypatch.setattr("app.worker.repositories.get_context_snapshot_for_worker", get_context_snapshot_for_worker)
    monkeypatch.setattr("app.worker.record_initial_context_snapshot", fail_record_context)
    monkeypatch.setattr("app.worker.repositories.complete_run", complete_run)
    monkeypatch.setattr("app.worker.repositories.create_artifact", lambda *args, **kwargs: None)
    monkeypatch.setattr("app.worker.repositories.append_message", fake_append_message)

    outcome = await process_run_payload(
        base_payload(
            executor_type="claude-agent-worker",
            context_snapshot_id="ctx-existing",
            context_snapshot={},
        ),
        AdapterRegistry({"claude-agent-worker": CaptureAdapter()}),
    )

    assert outcome.status == "succeeded"
    assert captured["payload"].context_snapshot["source"] == "chat_stream"
    assert captured["payload"].context_snapshot["used_context_summary"] == {
        "source": "chat_stream",
        "input_keys": ["message"],
        "memory_policy_source": "not_recorded",
        "long_term_memory_read": False,
    }
    assert captured["payload"].context_snapshot["context_pack_generated_at"] == "2026-06-12T01:23:45Z"
    serialized = json.dumps(captured["payload"].context_snapshot, ensure_ascii=False).lower()
    assert "stored_context_snapshot" not in serialized


@pytest.mark.asyncio
async def test_worker_rebuilds_db_context_snapshot_with_public_provenance(monkeypatch):
    captured = {}

    class CaptureAdapter:
        async def submit_run(self, payload, event_sink=None):
            captured["payload"] = payload
            return ExecutorResult(
                status="succeeded",
                adapter_version="capture-adapter/1",
                executor_type="claude-agent-worker",
                executor_version="capture/1",
                capabilities={},
                result={"message": "done"},
            )

    async def mark_run_running(conn, *, tenant_id, run_id):
        return True

    async def append_event(conn, **kwargs):
        return "evt-context"

    async def get_context_snapshot_for_worker(conn, **kwargs):
        return {
            "id": "ctx-existing",
            "tenant_id": "tenant-a",
            "workspace_id": "workspace-a",
            "user_id": "user-a",
            "session_id": "session-a",
            "run_id": "run-a",
            "trace_id": "trace_run_a",
            "schema_version": "ai-platform.context-snapshot.v1",
            "context_kind": "executor",
            "included_message_ids": ["msg-a"],
            "included_file_ids": ["file-a"],
            "included_artifact_ids": ["artifact-a"],
            "included_memory_record_ids": [],
            "redaction_summary_json": {},
            "payload_json": {
                "schema_version": "ai-platform.context-snapshot.v1",
                "source": "forged_db_source",
                "agent_id": "general-agent",
                "input_keys": ["raw_storage_key"],
                "message_count": 99,
                "file_count": 99,
                "artifact_count": 99,
                "memory_record_count": 99,
                "memory_policy": {
                    "source": "stored forged-source",
                    "memory_enabled": False,
                    "long_term_memory_enabled": True,
                    "retention_days": "bad",
                },
                "memoryPolicy": {
                    "source": "forged-camel-memory-policy",
                    "memory_enabled": True,
                    "retention_days": 1,
                },
                "window": "current",
                "used_context_summary": {
                    "source": "forged_nested_source",
                    "input_keys": ["storage_key"],
                    "long_term_memory_read": True,
                },
                "provenance": {"source": "forged-provenance"},
                "Provenance": {"source": "forged-title-provenance"},
                "provenance%5Fsummary": {"source": "forged-encoded-provenance"},
                "summary": "legacy summary",
                "Summary": "legacy title summary",
                "summary%5Fpayload": {"source": "forged-encoded-summary"},
                "raw_storage_key": "tenant/private/object",
                "raw%5Fstorage%5Fkey": "s3://encoded/private",
                "sandbox%5Fworkdir": "/tmp/encoded-private",
                "executor%5Fprivate%5Fpayload": {"token": "encoded-private"},
                "used%5Fcontext%5Fsummary": {"source": "forged-encoded"},
            },
            "created_at": None,
        }

    async def fail_record_context(*args, **kwargs):
        raise AssertionError("verified queue snapshots must be reconstructed from DB scope")

    async def complete_run(conn, **kwargs):
        return None

    monkeypatch.setattr("app.worker.transaction", fake_transaction)
    monkeypatch.setattr("app.worker.repositories.mark_run_running", mark_run_running)
    monkeypatch.setattr("app.worker.repositories.append_event", append_event)
    monkeypatch.setattr("app.worker.repositories.get_context_snapshot_for_worker", get_context_snapshot_for_worker)
    monkeypatch.setattr("app.worker.record_initial_context_snapshot", fail_record_context)
    monkeypatch.setattr("app.worker.repositories.complete_run", complete_run)
    monkeypatch.setattr("app.worker.repositories.create_artifact", lambda *args, **kwargs: None)
    monkeypatch.setattr("app.worker.repositories.append_message", fake_append_message)

    outcome = await process_run_payload(
        base_payload(executor_type="claude-agent-worker"),
        AdapterRegistry({"claude-agent-worker": CaptureAdapter()}),
    )

    assert outcome.status == "succeeded"
    context_snapshot = captured["payload"].context_snapshot
    assert context_snapshot["source"] == "stored_context_snapshot"
    assert context_snapshot["referenced_materials"] == {
        "message_count": 1,
        "file_count": 1,
        "artifact_count": 1,
        "memory_record_count": 0,
    }
    assert context_snapshot["used_context_summary"] == {
        "source": "stored_context_snapshot",
        "input_keys": ["attachments", "window"],
        "memory_policy_source": "not_recorded",
        "long_term_memory_read": False,
    }
    assert context_snapshot["latest_artifact_version"] is None
    assert context_snapshot["execution_tier"] == "sdk_only_writing"
    assert context_snapshot["context_pack_generated_at"]
    assert context_snapshot["memory_policy"] == {
        "source": "stored",
        "memory_enabled": False,
        "long_term_memory_enabled": False,
        "retention_days": 90,
    }
    serialized = json.dumps(context_snapshot, ensure_ascii=False)
    assert "forged_db_source" not in serialized
    assert "forged_nested_source" not in serialized
    assert "forged-encoded" not in serialized
    assert "forged-provenance" not in serialized
    assert "legacy summary" not in serialized
    assert "raw_storage_key" not in serialized
    assert "tenant/private/object" not in serialized
    assert "raw%5Fstorage%5Fkey" not in serialized
    assert "s3://encoded/private" not in serialized
    assert "sandbox%5Fworkdir" not in serialized
    assert "encoded-private" not in serialized


@pytest.mark.asyncio
async def test_worker_refreshes_unscoped_context_snapshot_before_executor(monkeypatch):
    calls = []
    captured = {}

    class CaptureAdapter:
        async def submit_run(self, payload, event_sink=None):
            captured["payload"] = payload
            return ExecutorResult(
                status="succeeded",
                adapter_version="capture-adapter/1",
                executor_type="claude-agent-worker",
                executor_version="capture/1",
                capabilities={},
                result={"message": "done"},
            )

    async def mark_run_running(conn, *, tenant_id, run_id):
        return True

    async def append_event(conn, **kwargs):
        calls.append(("event", kwargs["event_type"]))
        return "evt-a"

    async def get_context_snapshot_for_worker(conn, **kwargs):
        calls.append(("lookup", kwargs["context_snapshot_id"]))
        return None

    async def record_context(conn, **kwargs):
        calls.append(("context", kwargs["source"]))
        return {
            "schema_version": "ai-platform.context-snapshot.v1",
            "context_snapshot_id": "ctx_worker_refresh",
            "source": kwargs["source"],
            "message_count": 0,
            "file_count": 1,
            "memory_record_count": 0,
        }

    async def complete_run(conn, **kwargs):
        return None

    monkeypatch.setattr("app.worker.transaction", fake_transaction)
    monkeypatch.setattr("app.worker.repositories.mark_run_running", mark_run_running)
    monkeypatch.setattr("app.worker.repositories.append_event", append_event)
    monkeypatch.setattr("app.worker.repositories.get_context_snapshot_for_worker", get_context_snapshot_for_worker)
    monkeypatch.setattr("app.worker.record_initial_context_snapshot", record_context)
    monkeypatch.setattr("app.worker.repositories.complete_run", complete_run)
    monkeypatch.setattr("app.worker.repositories.create_artifact", lambda *args, **kwargs: None)
    monkeypatch.setattr("app.worker.repositories.append_message", fake_append_message)

    outcome = await process_run_payload(
        base_payload(
            executor_type="claude-agent-worker",
            context_snapshot_id="ctx-cross-tenant",
            context_snapshot={"source": "cross_tenant_queue_copy"},
        ),
        AdapterRegistry({"claude-agent-worker": CaptureAdapter()}),
    )

    assert outcome.status == "succeeded"
    assert ("lookup", "ctx-cross-tenant") in calls
    assert ("context", "worker_refresh") in calls
    assert captured["payload"].context_snapshot_id == "ctx_worker_refresh"
    assert captured["payload"].context_snapshot["source"] == "worker_refresh"


@pytest.mark.asyncio
async def test_worker_rejects_queue_payload_identity_mismatch_before_context_or_executor(monkeypatch):
    calls = []

    class ForbiddenAdapter:
        async def submit_run(self, payload, event_sink=None):
            raise AssertionError("identity-mismatched queue payload must not reach executor")

    async def mark_run_running(conn, *, tenant_id, run_id):
        return {
            "id": run_id,
            "tenant_id": tenant_id,
            "workspace_id": "workspace-db",
            "user_id": "user-db",
            "session_id": "session-db",
            "agent_id": "qa-word-review",
            "skill_id": "qa-file-reviewer",
        }

    async def append_event(conn, **kwargs):
        calls.append(("event", kwargs["event_type"], kwargs.get("payload") or {}))
        return "evt-a"

    async def fail_run(conn, **kwargs):
        calls.append(("fail", kwargs["error_code"], kwargs["error_message"]))

    async def fail_record_context(*args, **kwargs):
        raise AssertionError("identity-mismatched queue payload must not refresh context snapshot")

    monkeypatch.setattr("app.worker.transaction", fake_transaction)
    monkeypatch.setattr("app.worker.repositories.mark_run_running", mark_run_running)
    monkeypatch.setattr("app.worker.repositories.append_event", append_event)
    monkeypatch.setattr("app.worker.repositories.fail_run", fail_run)
    monkeypatch.setattr("app.worker.record_initial_context_snapshot", fail_record_context)

    outcome = await process_run_payload(
        base_payload(
            executor_type="claude-agent-worker",
            workspace_id="workspace-queue",
            user_id="user-queue",
            session_id="session-queue",
        ),
        AdapterRegistry({"claude-agent-worker": ForbiddenAdapter()}),
    )

    assert outcome.status == "failed"
    assert outcome.error_code == "queue_payload_identity_mismatch"
    assert ("fail", "queue_payload_identity_mismatch", "Queue payload identity does not match run record") in calls
    assert any(item[0] == "event" and item[1] == "error" for item in calls)


@pytest.mark.asyncio
async def test_worker_rejects_missing_db_identity_fields_before_context_or_executor(monkeypatch):
    calls = []

    class ForbiddenAdapter:
        async def submit_run(self, payload, event_sink=None):
            raise AssertionError("DB identity with missing user_id must not reach executor")

    async def mark_run_running(conn, *, tenant_id, run_id):
        return {
            "id": run_id,
            "tenant_id": tenant_id,
            "workspace_id": "workspace-a",
            "user_id": None,
            "session_id": "session-a",
            "agent_id": "qa-word-review",
            "skill_id": "qa-file-reviewer",
        }

    async def append_event(conn, **kwargs):
        calls.append(("event", kwargs["event_type"], kwargs.get("payload") or {}))
        return "evt-a"

    async def fail_run(conn, **kwargs):
        calls.append(("fail", kwargs["error_code"]))

    async def fail_record_context(*args, **kwargs):
        raise AssertionError("missing DB identity must not refresh context snapshot")

    monkeypatch.setattr("app.worker.transaction", fake_transaction)
    monkeypatch.setattr("app.worker.repositories.mark_run_running", mark_run_running)
    monkeypatch.setattr("app.worker.repositories.append_event", append_event)
    monkeypatch.setattr("app.worker.repositories.fail_run", fail_run)
    monkeypatch.setattr("app.worker.record_initial_context_snapshot", fail_record_context)

    outcome = await process_run_payload(
        base_payload(executor_type="claude-agent-worker"),
        AdapterRegistry({"claude-agent-worker": ForbiddenAdapter()}),
    )

    assert outcome.status == "failed"
    assert outcome.error_code == "queue_payload_identity_mismatch"
    assert ("fail", "queue_payload_identity_mismatch") in calls
    assert any("user_id" in item[2].get("mismatch_fields", []) for item in calls if item[0] == "event")


@pytest.mark.asyncio
async def test_worker_fails_queued_run_when_scope_guard_rejects_running_lock(monkeypatch):
    calls = []

    class ForbiddenAdapter:
        async def submit_run(self, payload, event_sink=None):
            raise AssertionError("scope-invalid queued run must not reach executor")

    async def mark_run_running(conn, *, tenant_id, run_id):
        calls.append(("lock", tenant_id, run_id))
        return None

    async def get_run(conn, *, tenant_id, run_id):
        calls.append(("get_run", tenant_id, run_id))
        return {
            "id": run_id,
            "tenant_id": tenant_id,
            "workspace_id": "workspace-a",
            "user_id": None,
            "session_id": "session-a",
            "agent_id": "qa-word-review",
            "skill_id": "qa-file-reviewer",
            "status": "queued",
        }

    async def append_event(conn, **kwargs):
        calls.append(("event", kwargs["event_type"], kwargs.get("payload") or {}))
        return "evt-a"

    async def fail_run(conn, **kwargs):
        calls.append(("fail", kwargs["error_code"], kwargs["error_message"]))

    async def fail_record_context(*args, **kwargs):
        raise AssertionError("scope-invalid queued run must not refresh context snapshot")

    monkeypatch.setattr("app.worker.transaction", fake_transaction)
    monkeypatch.setattr("app.worker.repositories.mark_run_running", mark_run_running)
    monkeypatch.setattr("app.worker.repositories.get_run", get_run)
    monkeypatch.setattr("app.worker.repositories.append_event", append_event)
    monkeypatch.setattr("app.worker.repositories.fail_run", fail_run)
    monkeypatch.setattr("app.worker.record_initial_context_snapshot", fail_record_context)

    outcome = await process_run_payload(
        base_payload(executor_type="claude-agent-worker"),
        AdapterRegistry({"claude-agent-worker": ForbiddenAdapter()}),
    )

    assert outcome.status == "failed"
    assert outcome.error_code == "queue_payload_identity_mismatch"
    assert ("fail", "queue_payload_identity_mismatch", "Queued run identity is invalid") in calls
    assert any(item[0] == "event" and item[1] == "error" for item in calls)


@pytest.mark.asyncio
async def test_worker_uses_db_run_input_when_queue_execution_fields_are_tampered(monkeypatch):
    captured = {}
    calls = []
    version = "hash-qa-file-reviewer"

    class CaptureAdapter:
        async def submit_run(self, payload, event_sink=None):
            captured["payload"] = payload
            return ExecutorResult(
                status="succeeded",
                adapter_version="capture-adapter/1",
                executor_type="claude-agent-worker",
                executor_version="capture/1",
                capabilities={},
                result={"message": "done"},
            )

    async def mark_run_running(conn, *, tenant_id, run_id):
        return {
            "id": run_id,
            "tenant_id": tenant_id,
            "workspace_id": "workspace-a",
            "user_id": "user-a",
            "session_id": "session-a",
            "agent_id": "qa-word-review",
            "skill_id": "qa-file-reviewer",
            "trace_id": "trace_run_a",
            "input_json": {
                "input": {"mode": "db", "message": "authoritative"},
                "file_ids": ["file-db"],
                "executor_type": "claude-agent-worker",
                "skill_version": version,
                "release_decision": release_decision(version),
            },
        }

    async def append_event(conn, **kwargs):
        calls.append(("event", kwargs["event_type"], kwargs.get("payload") or {}))
        return "evt-a"

    async def get_context_snapshot_for_worker(conn, **kwargs):
        return None

    async def record_context(conn, **kwargs):
        calls.append(("context", kwargs["input_payload"], kwargs["file_ids"]))
        return {
            "schema_version": "ai-platform.context-snapshot.v1",
            "context_snapshot_id": "ctx_worker_refresh",
            "source": kwargs["source"],
            "message_count": 0,
            "file_count": len(kwargs["file_ids"]),
            "memory_record_count": 0,
        }

    async def complete_run(conn, **kwargs):
        return None

    monkeypatch.setattr("app.worker.transaction", fake_transaction)
    monkeypatch.setattr("app.worker.repositories.mark_run_running", mark_run_running)
    monkeypatch.setattr("app.worker.repositories.append_event", append_event)
    monkeypatch.setattr("app.worker.repositories.get_context_snapshot_for_worker", get_context_snapshot_for_worker)
    monkeypatch.setattr("app.worker.record_initial_context_snapshot", record_context)
    monkeypatch.setattr("app.worker.repositories.complete_run", complete_run)
    monkeypatch.setattr("app.worker.repositories.create_artifact", lambda *args, **kwargs: None)
    monkeypatch.setattr("app.worker.repositories.append_message", fake_append_message)

    outcome = await process_run_payload(
        base_payload(
            executor_type="fake",
            input={"mode": "queue-tampered"},
            file_ids=["file-queue"],
            context_snapshot_id="ctx-cross-scope",
            context_snapshot={"source": "queue-tampered"},
        ),
        AdapterRegistry({"claude-agent-worker": CaptureAdapter()}),
    )

    assert outcome.status == "succeeded"
    assert ("context", {"mode": "db", "message": "authoritative"}, ["file-db"]) in calls
    assert captured["payload"].input == {"mode": "db", "message": "authoritative"}
    assert captured["payload"].file_ids == ["file-db"]
    assert captured["payload"].skill_version == version
    assert captured["payload"].release_decision == release_decision(version)


@pytest.mark.asyncio
async def test_worker_does_not_refresh_missing_context_for_unknown_executor(monkeypatch):
    calls = []

    async def mark_run_running(conn, *, tenant_id, run_id):
        calls.append(("running", tenant_id, run_id))
        return True

    async def append_event(conn, **kwargs):
        calls.append(("event", kwargs["event_type"], kwargs["stage"]))
        return "evt-a"

    async def fail_record_context(*args, **kwargs):
        raise AssertionError("unknown executor must fail before refreshing context")

    async def fail_run(conn, **kwargs):
        calls.append(("fail", kwargs["error_code"]))

    monkeypatch.setattr("app.worker.transaction", fake_transaction)
    monkeypatch.setattr("app.worker.repositories.mark_run_running", mark_run_running)
    monkeypatch.setattr("app.worker.repositories.append_event", append_event)
    monkeypatch.setattr("app.worker.record_initial_context_snapshot", fail_record_context)
    monkeypatch.setattr("app.worker.repositories.fail_run", fail_run)

    outcome = await process_run_payload(
        base_payload(
            executor_type="missing-executor",
            context_snapshot_id="",
            context_snapshot={},
        ),
        AdapterRegistry({}),
    )

    assert outcome.status == "failed"
    assert outcome.error_code == "unknown_executor_type"
    assert ("fail", "unknown_executor_type") in calls
    assert not any(item[0] == "context" for item in calls)


@pytest.mark.asyncio
async def test_worker_persists_run_skill_snapshots(monkeypatch):
    snapshots = []

    class SkillSnapshotAdapter:
        async def submit_run(self, payload, event_sink=None):
            return ExecutorResult(
                status="succeeded",
                adapter_version="test-adapter/1",
                executor_type="claude-agent-worker",
                executor_version="test-executor/1",
                capabilities={"skills": True},
                result={
                    "message": "done",
                    "allowed_skills": ["qa-file-reviewer"],
                    "staged_skills": ["qa-file-reviewer"],
                },
                executor_payload={
                    "used_skills": ["qa-file-reviewer"],
                    "used_skills_source": "executor_hook",
                    "skill_manifests": [
                        {
                            "skill_id": "qa-file-reviewer",
                            "version": "hash-a",
                            "content_hash": "hash-a",
                            "source": {"kind": "builtin"},
                            "dependency_ids": ["minimax-docx"],
                            "allowed": True,
                            "staged": True,
                            "used": True,
                        }
                    ],
                },
            )

    async def mark_run_running(conn, *, tenant_id, run_id):
        return True

    async def append_event(conn, **kwargs):
        return "evt-a"

    async def complete_run(conn, **kwargs):
        assert "skill_manifests" not in kwargs["result_json"]
        return None

    async def upsert_run_skill_snapshot(conn, **kwargs):
        snapshots.append(kwargs)

    monkeypatch.setattr("app.worker.transaction", fake_transaction)
    monkeypatch.setattr("app.worker.repositories.mark_run_running", mark_run_running)
    monkeypatch.setattr("app.worker.repositories.append_event", append_event)
    monkeypatch.setattr("app.worker.repositories.complete_run", complete_run)
    monkeypatch.setattr("app.worker.repositories.create_artifact", lambda *args, **kwargs: None)
    monkeypatch.setattr("app.worker.repositories.append_message", fake_append_message)
    monkeypatch.setattr("app.worker.repositories.upsert_run_skill_snapshot", upsert_run_skill_snapshot)

    outcome = await process_run_payload(base_payload(), AdapterRegistry({"fake": SkillSnapshotAdapter()}))

    assert outcome.status == "succeeded"
    assert snapshots == [
        {
            "tenant_id": "tenant-a",
            "run_id": "run-a",
            "skill_id": "qa-file-reviewer",
            "skill_version": "hash-a",
            "content_hash": "hash-a",
            "source_json": {"kind": "builtin"},
            "dependency_ids": ["minimax-docx"],
            "allowed": True,
            "staged": True,
            "used": True,
            "used_skills_source": "executor_hook",
            "inferred_used": False,
        }
    ]


@pytest.mark.asyncio
async def test_worker_rejects_used_skill_without_native_provenance(monkeypatch):
    snapshots = []
    completed = {}

    class InferredSkillAdapter:
        async def submit_run(self, payload, event_sink=None):
            return ExecutorResult(
                status="succeeded",
                adapter_version="test-adapter/1",
                executor_type="claude-agent-worker",
                executor_version="test-executor/1",
                capabilities={"skills": True},
                result={
                    "message": "done",
                    "allowed_skills": ["qa-file-reviewer"],
                    "staged_skills": ["qa-file-reviewer"],
                    "used_skills": ["qa-file-reviewer"],
                    "used_skills_source": "inferred",
                    "inferred_used_skills": ["qa-file-reviewer"],
                    "skill_manifests": [
                        {
                            "skill_id": "qa-file-reviewer",
                            "version": "hash-a",
                            "content_hash": "hash-a",
                            "source": {"kind": "builtin"},
                            "dependency_ids": [],
                            "allowed": True,
                            "staged": True,
                            "used": True,
                        }
                    ],
                },
                executor_payload={"inferred_used_skills": ["qa-file-reviewer"]},
            )

    async def mark_run_running(conn, *, tenant_id, run_id):
        return True

    async def append_event(conn, **kwargs):
        return "evt-a"

    async def complete_run(conn, *, tenant_id, run_id, result_json):
        completed["result_json"] = result_json

    async def upsert_run_skill_snapshot(conn, **kwargs):
        snapshots.append(kwargs)

    monkeypatch.setattr("app.worker.transaction", fake_transaction)
    monkeypatch.setattr("app.worker.repositories.mark_run_running", mark_run_running)
    monkeypatch.setattr("app.worker.repositories.append_event", append_event)
    monkeypatch.setattr("app.worker.repositories.complete_run", complete_run)
    monkeypatch.setattr("app.worker.repositories.create_artifact", lambda *args, **kwargs: None)
    monkeypatch.setattr("app.worker.repositories.append_message", fake_append_message)
    monkeypatch.setattr("app.worker.repositories.upsert_run_skill_snapshot", upsert_run_skill_snapshot)

    outcome = await process_run_payload(base_payload(), AdapterRegistry({"fake": InferredSkillAdapter()}))

    assert outcome.status == "succeeded"
    assert completed["result_json"]["used_skills"] == []
    assert "used_skills_source" not in completed["result_json"]
    assert "inferred_used_skills" not in completed["result_json"]
    assert completed["result_json"]["skills"]["used_skills"] == []
    assert snapshots[0]["used"] is False
    assert snapshots[0]["used_skills_source"] == "inferred"
    assert snapshots[0]["inferred_used"] is True


@pytest.mark.asyncio
async def test_worker_persists_g2_executor_contract_latency_and_token_placeholders(monkeypatch):
    calls = []

    class G2Adapter:
        async def submit_run(self, payload, event_sink=None):
            calls.append(("payload_trace", payload.trace_id, payload.schema_version))
            return ExecutorResult(
                status="succeeded",
                adapter_version="adapter/1",
                executor_type="fake",
                executor_version="fake/1",
                capabilities={"streaming": False},
                result={"message": "done"},
                executor_payload={
                    "input_token_count": 11,
                    "output_token_count": 13,
                    "estimated_cost_minor": 17,
                },
            )

    async def mark_run_running(conn, *, tenant_id, run_id):
        return True

    async def append_event(conn, **kwargs):
        calls.append(
            (
                "event",
                kwargs["event_type"],
                kwargs.get("latency_ms"),
                kwargs.get("input_token_count"),
                kwargs.get("output_token_count"),
                kwargs.get("estimated_cost_minor"),
                kwargs.get("trace_id"),
            )
        )
        return "evt-a"

    async def complete_run(conn, **kwargs):
        result = kwargs["result_json"]
        calls.append(("complete", result["latency_ms"], result["token_counts"], result["cost"]))

    monkeypatch.setattr("app.worker.transaction", fake_transaction)
    monkeypatch.setattr("app.worker.repositories.mark_run_running", mark_run_running)
    monkeypatch.setattr("app.worker.repositories.append_event", append_event)
    monkeypatch.setattr("app.worker.repositories.complete_run", complete_run)
    monkeypatch.setattr("app.worker.repositories.create_artifact", lambda *args, **kwargs: None)
    monkeypatch.setattr("app.worker.repositories.append_message", fake_append_message)
    monkeypatch.setattr("app.worker.time.monotonic", iter([10.0, 10.25]).__next__, raising=False)

    outcome = await process_run_payload(base_payload(), AdapterRegistry({"fake": G2Adapter()}))

    assert outcome.status == "succeeded"
    assert ("payload_trace", "trace_run_a", "ai-platform.run-payload.v1") in calls
    assert ("complete", 250, {"input": 11, "output": 13, "total": 24}, {"estimated_cost_minor": 17}) in calls
    succeeded_event = next(item for item in calls if item[0] == "event" and item[1] == "run_succeeded")
    assert succeeded_event[2:] == (250, 11, 13, 17, "trace_run_a")


@pytest.mark.asyncio
async def test_worker_persists_artifact_manifest_contract(monkeypatch):
    created = []
    events = []

    class ArtifactAdapter:
        async def submit_run(self, payload, event_sink=None):
            return ExecutorResult(
                status="succeeded",
                adapter_version="adapter/1",
                executor_type="fake",
                executor_version="fake/1",
                capabilities={"streaming": False},
                result={"message": "done"},
                artifacts=[
                    ArtifactManifest(
                        artifact_type="reviewed_docx",
                        label="批注 Word",
                        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                        storage_key="tenants/tenant-a/runs/run-a/artifacts/1/reviewed.docx",
                        size_bytes=10,
                        manifest={
                            "local_path": "/tmp/worker/output.docx",
                            "source_file_id": "file-a",
                            "source_step_id": "step-a",
                            "producer_kind": "subagent",
                            "producer_role": "reviewer",
                            "checkpoint_id": "checkpoint-a",
                            "subagent_id": "subagent-a",
                            "skill_id": "qa-file-reviewer",
                            "command_sha256": "b" * 64,
                        },
                    )
                ],
            )

    async def mark_run_running(conn, *, tenant_id, run_id):
        return True

    async def create_artifact(conn, **kwargs):
        created.append(kwargs)

    async def append_event(conn, **kwargs):
        events.append(kwargs)
        return "evt-a"

    async def complete_run(conn, **kwargs):
        return None

    monkeypatch.setattr("app.worker.transaction", fake_transaction)
    monkeypatch.setattr("app.worker.repositories.mark_run_running", mark_run_running)
    monkeypatch.setattr("app.worker.repositories.append_event", append_event)
    monkeypatch.setattr("app.worker.repositories.create_artifact", create_artifact)
    monkeypatch.setattr("app.worker.repositories.complete_run", complete_run)
    monkeypatch.setattr("app.worker.repositories.append_message", fake_append_message)

    outcome = await process_run_payload(base_payload(), AdapterRegistry({"fake": ArtifactAdapter()}))

    assert outcome.status == "succeeded"
    assert created[0]["trace_id"] == "trace_run_a"
    assert created[0]["manifest_json"]["schema_version"] == "ai-platform.artifact-manifest.v1"
    assert created[0]["manifest_json"]["artifact_type"] == "reviewed_docx"
    assert created[0]["manifest_json"]["source_file_id"] == "file-a"
    assert "local_path" not in created[0]["manifest_json"]
    artifact_event = next(item for item in events if item["event_type"] == "artifact_created")
    assert artifact_event["payload"]["artifact_id"] == created[0]["artifact_id"]
    assert artifact_event["payload"]["artifact_type"] == "reviewed_docx"
    assert artifact_event["payload"]["download_url"] == f"/api/ai/artifacts/{created[0]['artifact_id']}/download"
    assert artifact_event["payload"]["lineage"] == {
        "source_run_id": "run-a",
        "source_file_id": "file-a",
        "source_step_id": "step-a",
        "producer_kind": "subagent",
        "producer_role": "reviewer",
        "checkpoint_id": "checkpoint-a",
        "subagent_id": "subagent-a",
    }
    assert "skill_id" not in str(artifact_event["payload"])
    assert "command_sha256" not in str(artifact_event["payload"])
    assert "/tmp/" not in str(artifact_event["payload"])


@pytest.mark.asyncio
async def test_worker_marks_adapter_reported_failure(monkeypatch):
    calls = []

    async def mark_run_running(conn, *, tenant_id, run_id):
        return True

    async def append_event(conn, *, tenant_id, run_id, event_type, stage, message, payload=None):
        calls.append(("event", event_type, stage, message))

    async def fail_run(conn, *, tenant_id, run_id, error_code, error_message, result_json=None):
        calls.append(("fail", error_code, error_message))

    monkeypatch.setattr("app.worker.transaction", fake_transaction)
    monkeypatch.setattr("app.worker.repositories.mark_run_running", mark_run_running)
    monkeypatch.setattr("app.worker.repositories.append_event", append_event)
    monkeypatch.setattr("app.worker.repositories.fail_run", fail_run)

    outcome = await process_run_payload(base_payload(), AdapterRegistry({"fake": FakeFailureAdapter()}))

    assert outcome.status == "failed"
    assert outcome.error_code == "fake_failure"
    assert outcome.error_message == "fake run failed for run-a"
    assert any(item[0] == "fail" and item[1] == "fake_failure" for item in calls)


@pytest.mark.asyncio
async def test_worker_records_non_secret_runtime_evidence(monkeypatch):
    events = []

    async def mark_run_running(conn, *, tenant_id, run_id):
        return True

    async def append_event(conn, *, tenant_id, run_id, event_type, stage, message, payload=None):
        events.append({"event_type": event_type, "stage": stage, "message": message, "payload": payload or {}})

    async def complete_run(conn, *, tenant_id, run_id, result_json):
        return None

    async def create_artifact(conn, **kwargs):
        return None

    monkeypatch.setattr("app.worker.transaction", fake_transaction)
    monkeypatch.setattr("app.worker.repositories.mark_run_running", mark_run_running)
    monkeypatch.setattr("app.worker.repositories.append_event", append_event)
    monkeypatch.setattr("app.worker.repositories.complete_run", complete_run)
    monkeypatch.setattr("app.worker.repositories.create_artifact", create_artifact)
    monkeypatch.setattr("app.worker.repositories.append_message", fake_append_message)
    monkeypatch.setattr("app.worker.repositories.new_id", lambda prefix: "art_runtime_evidence")

    outcome = await process_run_payload(
        base_payload(file_ids=[], skill_id="general-chat", agent_id="general-agent"),
        AdapterRegistry({"fake": FakeSuccessAdapter()}),
        worker_id="worker-test-1",
    )

    assert outcome.status == "succeeded"
    started = next(item for item in events if item["message"] == "Run started")
    assert started["payload"]["worker_id"] == "worker-test-1"
    assert started["payload"]["executor_type"] == "fake"
    assert "claude_agent_sdk_enabled" in started["payload"]
    assert "claude_agent_model" in started["payload"]
    payload_text = str(started["payload"]).lower()
    assert "token" not in payload_text
    assert "secret" not in payload_text
    assert "api_key" not in payload_text


@pytest.mark.asyncio
async def test_worker_rejects_bad_queue_payload_without_touching_database(monkeypatch):
    touched = False

    async def mark_run_running(conn, *, tenant_id, run_id):
        nonlocal touched
        touched = True
        return True

    monkeypatch.setattr("app.worker.repositories.mark_run_running", mark_run_running)

    outcome = await process_run_payload({"run_id": "../bad"}, AdapterRegistry({"fake": FakeSuccessAdapter()}))

    assert outcome.status == "dead_letter"
    assert outcome.error_code == "invalid_queue_payload"
    assert touched is False


@pytest.mark.asyncio
async def test_worker_skips_stale_queue_payload_when_run_row_is_missing(monkeypatch):
    calls = []

    async def mark_run_running(conn, *, tenant_id, run_id):
        calls.append(("running", tenant_id, run_id))
        return False

    async def get_run(conn, *, tenant_id, run_id):
        calls.append(("get_run", tenant_id, run_id))
        return None

    async def append_event(conn, **kwargs):
        raise AssertionError("stale queue payload without a run row must not write run_events")

    monkeypatch.setattr("app.worker.transaction", fake_transaction)
    monkeypatch.setattr("app.worker.repositories.mark_run_running", mark_run_running)
    monkeypatch.setattr("app.worker.repositories.get_run", get_run)
    monkeypatch.setattr("app.worker.repositories.append_event", append_event)

    outcome = await process_run_payload(base_payload(), AdapterRegistry({"fake": FakeSuccessAdapter()}))

    assert outcome.status == "skipped"
    assert outcome.error_code == "stale_queue_payload"
    assert calls == [
        ("running", "tenant-a", "run-a"),
        ("get_run", "tenant-a", "run-a"),
    ]


@pytest.mark.asyncio
async def test_worker_honors_cancel_before_executor_start(monkeypatch):
    calls = []

    class ShouldNotRunAdapter:
        async def submit_run(self, payload, event_sink=None):
            calls.append(("adapter", payload.run_id))
            return ExecutorResult(
                status="succeeded",
                adapter_version="adapter/1",
                executor_type="fake",
                executor_version="fake/1",
                capabilities={},
                result={"message": "should not run"},
            )

    async def mark_run_running(conn, *, tenant_id, run_id):
        return True

    async def is_cancel_requested(conn, *, tenant_id, run_id):
        return True

    async def cancel_run(conn, *, tenant_id, run_id, result_json=None):
        calls.append(("cancel", result_json))

    async def append_event(conn, *, tenant_id, run_id, event_type, stage, message, payload=None):
        calls.append(("event", event_type, stage, message))

    monkeypatch.setattr("app.worker.transaction", fake_transaction)
    monkeypatch.setattr("app.worker.repositories.mark_run_running", mark_run_running)
    monkeypatch.setattr("app.worker.repositories.is_cancel_requested", is_cancel_requested)
    monkeypatch.setattr("app.worker.repositories.cancel_run", cancel_run)
    monkeypatch.setattr("app.worker.repositories.append_event", append_event)

    outcome = await process_run_payload(base_payload(), AdapterRegistry({"fake": ShouldNotRunAdapter()}))

    assert outcome.status == "cancelled"
    assert ("adapter", "run-a") not in calls
    assert any(item[0] == "cancel" for item in calls)


@pytest.mark.asyncio
async def test_worker_parks_top_level_multi_agent_parent_for_dispatcher_without_running_adapter(monkeypatch):
    calls = []

    class Settings:
        multi_agent_dispatch_worker_enabled = True
        claude_agent_sdk_enabled = False
        claude_agent_model = "test-model"

    class ShouldNotRunAdapter:
        async def submit_run(self, payload, event_sink=None):
            raise AssertionError("parked multi-agent parent must not execute adapter steps")

    locked_run = {
        "id": "run-a",
        "tenant_id": "tenant-a",
        "workspace_id": "workspace-a",
        "user_id": "user-a",
        "session_id": "session-a",
        "agent_id": "general-agent",
        "skill_id": "general-chat",
        "trace_id": "trace-run-a",
        "input_json": {
            "input": {
                "message": "build feature",
                "execution_mode": "multi_agent",
                "multi_agent_steps": [{"step_key": "code", "depends_on": []}],
            },
            "file_ids": [],
            "executor_type": "fake",
            "skill_version": "hash-general-chat",
            "release_decision": release_decision("hash-general-chat"),
        },
    }

    async def mark_run_running(conn, *, tenant_id, run_id):
        calls.append(("running", tenant_id, run_id))
        return locked_run

    async def mark_parent_awaiting_dispatch(conn, *, tenant_id, run_id, worker_id):
        calls.append(("park", tenant_id, run_id, worker_id))
        return True

    async def append_event(conn, **kwargs):
        calls.append(("event", kwargs["event_type"], kwargs["stage"], kwargs.get("payload") or {}))
        return "evt-a"

    monkeypatch.setattr("app.worker.transaction", fake_transaction)
    monkeypatch.setattr("app.worker.get_settings", lambda: Settings(), raising=False)
    monkeypatch.setattr("app.worker.repositories.mark_run_running", mark_run_running)
    monkeypatch.setattr(
        "app.worker.repositories.mark_multi_agent_dispatch_parent_awaiting_dispatch",
        mark_parent_awaiting_dispatch,
        raising=False,
    )
    monkeypatch.setattr("app.worker.repositories.append_event", append_event)

    outcome = await process_run_payload(
        base_payload(
            file_ids=[],
            agent_id="general-agent",
            skill_id="general-chat",
            input={"message": "build feature"},
            executor_type="fake",
            skill_manifests=[primary_manifest("general-chat", "hash-general-chat")],
        ),
        AdapterRegistry({"fake": ShouldNotRunAdapter()}),
        worker_id="worker-a",
    )

    assert outcome.status == "skipped"
    assert calls[0] == ("running", "tenant-a", "run-a")
    assert ("park", "tenant-a", "run-a", "worker-a") in calls
    parked_events = [item for item in calls if item[0] == "event" and item[1] == "multi_agent_dispatch_parent_parked"]
    assert parked_events
    assert parked_events[0][3]["visible_to_user"] is False
    assert parked_events[0][3]["orchestration_state"] == "awaiting_dispatch"


@pytest.mark.asyncio
async def test_worker_stops_running_executor_after_cancel_requested_on_event_boundary(monkeypatch):
    calls = []
    cancel_checks = 0

    class StreamingAdapter:
        async def submit_run(self, payload, event_sink=None):
            await event_sink(
                event_type="assistant_delta",
                stage="message",
                message="partial",
                payload={"delta": "partial"},
            )
            calls.append(("adapter", "continued_after_cancel"))
            return ExecutorResult(
                status="succeeded",
                adapter_version="adapter/1",
                executor_type="fake",
                executor_version="fake/1",
                capabilities={"streaming": True},
                result={"message": "should not complete"},
            )

    async def mark_run_running(conn, *, tenant_id, run_id):
        return True

    async def is_cancel_requested(conn, *, tenant_id, run_id):
        nonlocal cancel_checks
        cancel_checks += 1
        return cancel_checks >= 2

    async def cancel_run(conn, *, tenant_id, run_id, result_json=None):
        calls.append(("cancel", result_json))

    async def append_event(conn, *, tenant_id, run_id, event_type, stage, message, payload=None):
        calls.append(("event", event_type, stage, message))

    async def complete_run(conn, **kwargs):
        calls.append(("complete", kwargs["result_json"]))

    monkeypatch.setattr("app.worker.transaction", fake_transaction)
    monkeypatch.setattr("app.worker.repositories.mark_run_running", mark_run_running)
    monkeypatch.setattr("app.worker.repositories.is_cancel_requested", is_cancel_requested)
    monkeypatch.setattr("app.worker.repositories.cancel_run", cancel_run)
    monkeypatch.setattr("app.worker.repositories.append_event", append_event)
    monkeypatch.setattr("app.worker.repositories.complete_run", complete_run)
    monkeypatch.setattr("app.worker.repositories.append_message", fake_append_message)

    outcome = await process_run_payload(base_payload(), AdapterRegistry({"fake": StreamingAdapter()}))

    assert outcome.status == "cancelled"
    assert ("adapter", "continued_after_cancel") not in calls
    assert any(item[0] == "cancel" for item in calls)
    assert not any(item[0] == "complete" for item in calls)
    assert ("event", "run_cancelled", "control", "任务已取消") in calls


@pytest.mark.asyncio
async def test_worker_records_unknown_executor_as_failed(monkeypatch):
    calls = []

    async def append_event(conn, *, tenant_id, run_id, event_type, stage, message, payload=None):
        calls.append(("event", event_type, stage))

    async def fail_run(conn, *, tenant_id, run_id, error_code, error_message, result_json=None):
        calls.append(("fail", error_code, error_message))

    async def mark_run_running(conn, *, tenant_id, run_id):
        calls.append(("running", tenant_id, run_id))
        return True

    monkeypatch.setattr("app.worker.transaction", fake_transaction)
    monkeypatch.setattr("app.worker.repositories.mark_run_running", mark_run_running)
    monkeypatch.setattr("app.worker.repositories.append_event", append_event)
    monkeypatch.setattr("app.worker.repositories.fail_run", fail_run)

    outcome = await process_run_payload(base_payload(executor_type="missing"), AdapterRegistry({"fake": FakeSuccessAdapter()}))

    assert outcome.status == "failed"
    assert outcome.error_code == "unknown_executor_type"
    assert any(item[0] == "fail" and item[1] == "unknown_executor_type" for item in calls)


@pytest.mark.asyncio
async def test_worker_honors_explicit_empty_registry(monkeypatch):
    calls = []

    async def append_event(conn, *, tenant_id, run_id, event_type, stage, message, payload=None):
        calls.append(("event", event_type, stage))

    async def fail_run(conn, *, tenant_id, run_id, error_code, error_message, result_json=None):
        calls.append(("fail", error_code, error_message))

    async def mark_run_running(conn, *, tenant_id, run_id):
        calls.append(("running", tenant_id, run_id))
        return True

    monkeypatch.setattr("app.worker.transaction", fake_transaction)
    monkeypatch.setattr("app.worker.repositories.mark_run_running", mark_run_running)
    monkeypatch.setattr("app.worker.repositories.append_event", append_event)
    monkeypatch.setattr("app.worker.repositories.fail_run", fail_run)

    outcome = await process_run_payload(
        base_payload(executor_type="claude-agent-worker"),
        AdapterRegistry({}),
    )

    assert outcome.status == "failed"
    assert outcome.error_code == "unknown_executor_type"
    assert any(item[0] == "fail" and item[1] == "unknown_executor_type" for item in calls)


@pytest.mark.asyncio
async def test_worker_honors_falsy_registry_double(monkeypatch):
    calls = []

    class FalsyRegistry:
        def __bool__(self):
            return False

        def get(self, executor_type):
            calls.append(("get", executor_type))
            return FakeSuccessAdapter()

    async def mark_run_running(conn, *, tenant_id, run_id):
        calls.append(("running", tenant_id, run_id))
        return True

    async def append_event(conn, *, tenant_id, run_id, event_type, stage, message, payload=None):
        calls.append(("event", event_type, stage))

    async def create_artifact(conn, **kwargs):
        calls.append(("artifact", kwargs["artifact_type"]))

    async def complete_run(conn, *, tenant_id, run_id, result_json):
        calls.append(("complete", result_json["executor"]["adapter_version"]))

    async def fail_run(conn, *, tenant_id, run_id, error_code, error_message, result_json=None):
        calls.append(("fail", error_code))

    monkeypatch.setattr("app.worker.transaction", fake_transaction)
    monkeypatch.setattr("app.worker.repositories.mark_run_running", mark_run_running)
    monkeypatch.setattr("app.worker.repositories.append_event", append_event)
    monkeypatch.setattr("app.worker.repositories.create_artifact", create_artifact)
    monkeypatch.setattr("app.worker.repositories.complete_run", complete_run)
    monkeypatch.setattr("app.worker.repositories.fail_run", fail_run)
    monkeypatch.setattr("app.worker.repositories.append_message", fake_append_message)

    outcome = await process_run_payload(
        base_payload(executor_type="fake"),
        FalsyRegistry(),
    )

    assert outcome.status == "succeeded"
    assert ("get", "fake") in calls
    assert ("complete", "fake-adapter/1") in calls


@pytest.mark.asyncio
async def test_worker_skips_unknown_executor_payload_for_terminal_run(monkeypatch):
    calls = []

    async def mark_run_running(conn, *, tenant_id, run_id):
        calls.append(("running", tenant_id, run_id))
        return False

    async def get_run(conn, *, tenant_id, run_id):
        calls.append(("get_run", tenant_id, run_id))
        return {"id": run_id, "status": "succeeded"}

    async def fail_run(conn, **kwargs):
        calls.append(("fail", kwargs["error_code"]))
        raise AssertionError("terminal run must not be overwritten by stale unknown executor payload")

    async def append_event(conn, *, tenant_id, run_id, event_type, stage, message, payload=None):
        calls.append(("event", event_type, stage))

    monkeypatch.setattr("app.worker.transaction", fake_transaction)
    monkeypatch.setattr("app.worker.repositories.mark_run_running", mark_run_running)
    monkeypatch.setattr("app.worker.repositories.get_run", get_run)
    monkeypatch.setattr("app.worker.repositories.fail_run", fail_run)
    monkeypatch.setattr("app.worker.repositories.append_event", append_event)

    outcome = await process_run_payload(
        base_payload(executor_type="missing"),
        AdapterRegistry({"fake": FakeSuccessAdapter()}),
    )

    assert outcome.status == "skipped"
    assert not any(item[0] == "fail" for item in calls)
    assert calls == [
        ("running", "tenant-a", "run-a"),
        ("get_run", "tenant-a", "run-a"),
        ("event", "skip", "worker"),
    ]


@pytest.mark.asyncio
async def test_worker_blocks_direct_runtime211_queue_payload(monkeypatch):
    calls = []

    class DirectRuntime211Adapter:
        async def submit_run(self, payload, event_sink=None):
            calls.append(("adapter", payload.run_id))
            raise AssertionError("direct runtime211 queue payload must not reach adapter")

    async def fail_run(conn, *, tenant_id, run_id, error_code, error_message, result_json=None):
        calls.append(("fail", error_code, error_message))

    async def append_event(conn, *, tenant_id, run_id, event_type, stage, message, payload=None):
        calls.append(("event", event_type, stage, payload or {}))

    async def mark_run_running(conn, *, tenant_id, run_id):
        calls.append(("running", tenant_id, run_id))
        return True

    monkeypatch.setattr("app.worker.transaction", fake_transaction)
    monkeypatch.setattr("app.worker.repositories.mark_run_running", mark_run_running)
    monkeypatch.setattr("app.worker.repositories.fail_run", fail_run)
    monkeypatch.setattr("app.worker.repositories.append_event", append_event)

    outcome = await process_run_payload(
        base_payload(executor_type="runtime211"),
        AdapterRegistry({"runtime211": DirectRuntime211Adapter()}),
    )

    assert outcome.status == "failed"
    assert outcome.error_code == "legacy_runtime211_direct_executor_disabled"
    assert ("running", "tenant-a", "run-a") in calls
    assert not any(item[0] == "adapter" for item in calls)
    assert any(
        item[0] == "fail" and item[1] == "legacy_runtime211_direct_executor_disabled"
        for item in calls
    )
    denied_event = next(item for item in calls if item[0] == "event" and item[1] == "legacy_runtime211_direct_executor_denied")
    assert denied_event[1] == "legacy_runtime211_direct_executor_denied"
    assert denied_event[2] == "policy"
    assert denied_event[3]["visible_to_user"] is False


@pytest.mark.asyncio
async def test_worker_skips_direct_runtime211_payload_for_terminal_run(monkeypatch):
    calls = []

    class DirectRuntime211Adapter:
        async def submit_run(self, payload, event_sink=None):
            calls.append(("adapter", payload.run_id))
            raise AssertionError("terminal direct runtime211 payload must not reach adapter")

    async def mark_run_running(conn, *, tenant_id, run_id):
        calls.append(("running", tenant_id, run_id))
        return False

    async def get_run(conn, *, tenant_id, run_id):
        calls.append(("get_run", tenant_id, run_id))
        return {"id": run_id, "status": "succeeded"}

    async def fail_run(conn, **kwargs):
        calls.append(("fail", kwargs["error_code"]))
        raise AssertionError("terminal run must not be overwritten by stale runtime211 payload")

    async def append_event(conn, *, tenant_id, run_id, event_type, stage, message, payload=None):
        calls.append(("event", event_type, stage))

    monkeypatch.setattr("app.worker.transaction", fake_transaction)
    monkeypatch.setattr("app.worker.repositories.mark_run_running", mark_run_running)
    monkeypatch.setattr("app.worker.repositories.get_run", get_run)
    monkeypatch.setattr("app.worker.repositories.fail_run", fail_run)
    monkeypatch.setattr("app.worker.repositories.append_event", append_event)

    outcome = await process_run_payload(
        base_payload(executor_type="runtime211"),
        AdapterRegistry({"runtime211": DirectRuntime211Adapter()}),
    )

    assert outcome.status == "skipped"
    assert not any(item[0] in {"adapter", "fail"} for item in calls)
    assert calls == [
        ("running", "tenant-a", "run-a"),
        ("get_run", "tenant-a", "run-a"),
        ("event", "skip", "worker"),
    ]


@pytest.mark.asyncio
async def test_worker_passes_user_id_to_executor_payload(monkeypatch):
    seen = {}

    class IdentityAdapter:
        async def submit_run(self, payload, event_sink=None):
            seen["user_id"] = payload.user_id
            return ExecutorResult(
                status="succeeded",
                adapter_version="adapter/1",
                executor_type="fake",
                executor_version="fake/1",
                capabilities={"streaming": False},
                result={"message": "done"},
            )

    async def mark_run_running(conn, *, tenant_id, run_id):
        return True

    async def append_event(conn, **kwargs):
        return "evt-a"

    async def complete_run(conn, **kwargs):
        return None

    monkeypatch.setattr("app.worker.transaction", fake_transaction)
    monkeypatch.setattr("app.worker.repositories.mark_run_running", mark_run_running)
    monkeypatch.setattr("app.worker.repositories.append_event", append_event)
    monkeypatch.setattr("app.worker.repositories.complete_run", complete_run)
    monkeypatch.setattr("app.worker.repositories.append_message", fake_append_message)

    outcome = await process_run_payload(
        base_payload(user_id="user-a"),
        AdapterRegistry({"fake": IdentityAdapter()}),
    )

    assert outcome.status == "succeeded"
    assert seen["user_id"] == "user-a"


@pytest.mark.asyncio
async def test_worker_records_multi_agent_step_events(monkeypatch):
    step_calls = []

    class StepAdapter:
        async def submit_run(self, payload, event_sink=None):
            await event_sink(
                event_type="agent_step_started",
                stage="agent",
                message="coding agent started",
                payload={"role": "coding", "step_key": "code", "step_index": 1, "depends_on": []},
            )
            await event_sink(
                event_type="agent_step_completed",
                stage="agent",
                message="coding agent completed",
                payload={
                    "role": "coding",
                    "step_key": "code",
                    "step_index": 1,
                    "depends_on": [],
                    "output": "code output",
                },
            )
            return ExecutorResult(
                status="succeeded",
                adapter_version="adapter/1",
                executor_type="fake",
                executor_version="fake/1",
                capabilities={"multi_agent": True},
                result={"message": "done"},
            )

    async def mark_run_running(conn, *, tenant_id, run_id):
        return True

    async def append_event(conn, **kwargs):
        return "evt-a"

    async def upsert_run_step(conn, **kwargs):
        step_calls.append(kwargs)
        return "step-a"

    async def complete_run(conn, **kwargs):
        return None

    async def list_run_steps(conn, *, tenant_id, run_id):
        return []

    monkeypatch.setattr("app.worker.transaction", fake_transaction)
    monkeypatch.setattr("app.worker.repositories.mark_run_running", mark_run_running)
    monkeypatch.setattr("app.worker.repositories.append_event", append_event)
    monkeypatch.setattr("app.worker.repositories.upsert_run_step", upsert_run_step, raising=False)
    monkeypatch.setattr("app.worker.repositories.list_run_steps", list_run_steps)
    monkeypatch.setattr("app.worker.repositories.complete_run", complete_run)
    monkeypatch.setattr("app.worker.repositories.append_message", fake_append_message)

    outcome = await process_run_payload(
        base_payload(file_ids=[], skill_id="general-chat", agent_id="general-agent"),
        AdapterRegistry({"fake": StepAdapter()}),
    )

    assert outcome.status == "succeeded"
    assert [item["status"] for item in step_calls] == ["running", "succeeded"]
    assert step_calls[0]["step_key"] == "code"
    assert step_calls[0]["step_kind"] == "agent"
    assert step_calls[0]["role"] == "coding"
    assert step_calls[1]["payload_json"]["output"] == "code output"


@pytest.mark.asyncio
async def test_worker_records_multi_agent_blocked_step_events(monkeypatch):
    step_calls = []
    failed_result = {}

    class BlockedStepAdapter:
        async def submit_run(self, payload, event_sink=None):
            await event_sink(
                event_type="agent_step_blocked",
                stage="agent",
                message="test agent blocked by unresolved dependencies",
                payload={
                    "role": "test",
                    "step_key": "verify",
                    "step_index": 2,
                    "depends_on": ["unknown"],
                    "missing_dependencies": ["unknown"],
                    "error_code": "multi_agent_dependency_blocked",
                },
            )
            return ExecutorResult(
                status="failed",
                adapter_version="adapter/1",
                executor_type="fake",
                executor_version="fake/1",
                capabilities={"multi_agent": True},
                result={"error_code": "multi_agent_dependency_blocked", "message": "blocked"},
            )

    async def mark_run_running(conn, *, tenant_id, run_id):
        return True

    async def append_event(conn, **kwargs):
        return "evt-a"

    async def upsert_run_step(conn, **kwargs):
        step_calls.append(kwargs)
        return "step-a"

    async def list_run_steps(conn, *, tenant_id, run_id):
        return [
            {
                "id": "step-verify",
                "run_id": run_id,
                "step_key": "verify",
                "step_kind": "agent",
                "status": "failed",
                "title": "test agent blocked by unresolved dependencies",
                "role": "test",
                "sequence": 2,
                "payload_json": {
                    "depends_on": ["unknown"],
                    "missing_dependencies": ["unknown"],
                    "error_code": "multi_agent_dependency_blocked",
                },
                "started_at": None,
                "finished_at": None,
                "created_at": None,
                "updated_at": None,
            },
        ]

    async def fail_run(conn, *, tenant_id, run_id, error_code, error_message, result_json=None):
        failed_result.update(result_json or {})

    monkeypatch.setattr("app.worker.transaction", fake_transaction)
    monkeypatch.setattr("app.worker.repositories.mark_run_running", mark_run_running)
    monkeypatch.setattr("app.worker.repositories.append_event", append_event)
    monkeypatch.setattr("app.worker.repositories.upsert_run_step", upsert_run_step, raising=False)
    monkeypatch.setattr("app.worker.repositories.list_run_steps", list_run_steps)
    monkeypatch.setattr("app.worker.repositories.fail_run", fail_run)

    outcome = await process_run_payload(
        base_payload(file_ids=[], skill_id="general-chat", agent_id="general-agent"),
        AdapterRegistry({"fake": BlockedStepAdapter()}),
    )

    assert outcome.status == "failed"
    assert len(step_calls) == 1
    assert step_calls[0]["status"] == "failed"
    assert step_calls[0]["step_key"] == "verify"
    assert step_calls[0]["role"] == "test"
    assert step_calls[0]["payload_json"]["missing_dependencies"] == ["unknown"]
    assert step_calls[0]["payload_json"]["error_code"] == "multi_agent_dependency_blocked"
    assert failed_result["multi_agent"]["steps"][0]["error_code"] == "multi_agent_dependency_blocked"
    assert failed_result["multi_agent"]["steps"][0]["error"] is None
    assert failed_result["multi_agent"]["steps"][0]["missing_dependencies"] == ["unknown"]
    assert failed_result["multi_agent"]["counts"] == {
        "total": 1,
        "pending": 0,
        "succeeded": 0,
        "failed": 1,
        "running": 0,
        "cancelled": 0,
        "reused": 0,
        "blocked": 1,
    }


@pytest.mark.asyncio
async def test_worker_includes_multi_agent_step_summary_in_success_result(monkeypatch):
    completed_result = {}

    class MultiAgentAdapter:
        async def submit_run(self, payload, event_sink=None):
            await event_sink(
                event_type="agent_step_reused",
                stage="agent",
                message="coding agent reused checkpoint",
                payload={
                    "role": "coding",
                    "step_key": "code",
                    "step_index": 1,
                    "depends_on": [],
                    "output": "checkpointed code output",
                    "checkpoint_reused": True,
                },
            )
            await event_sink(
                event_type="agent_step_completed",
                stage="agent",
                message="test agent completed",
                payload={
                    "role": "test",
                    "step_key": "verify",
                    "step_index": 2,
                    "depends_on": ["code"],
                    "output": "verify output",
                },
            )
            return ExecutorResult(
                status="succeeded",
                adapter_version="adapter/1",
                executor_type="fake",
                executor_version="fake/1",
                capabilities={"multi_agent": True},
                result={"message": "verify output"},
            )

    async def mark_run_running(conn, *, tenant_id, run_id):
        return True

    async def append_event(conn, **kwargs):
        return "evt-a"

    async def upsert_run_step(conn, **kwargs):
        return "step-a"

    async def list_run_steps(conn, *, tenant_id, run_id):
        return [
            {
                "id": "step-code",
                "run_id": run_id,
                "step_key": "code",
                "step_kind": "agent",
                "status": "succeeded",
                "title": "coding agent reused checkpoint",
                "role": "coding",
                "sequence": 1,
                "payload_json": {
                    "depends_on": [],
                    "output": "checkpointed code output",
                    "checkpoint_reused": True,
                },
                "started_at": None,
                "finished_at": None,
                "created_at": None,
                "updated_at": None,
            },
            {
                "id": "step-verify",
                "run_id": run_id,
                "step_key": "verify",
                "step_kind": "agent",
                "status": "succeeded",
                "title": "test agent completed",
                "role": "test",
                "sequence": 2,
                "payload_json": {
                    "depends_on": ["code"],
                    "output": "verify output",
                },
                "started_at": None,
                "finished_at": None,
                "created_at": None,
                "updated_at": None,
            },
        ]

    async def complete_run(conn, *, tenant_id, run_id, result_json):
        completed_result.update(result_json)

    monkeypatch.setattr("app.worker.transaction", fake_transaction)
    monkeypatch.setattr("app.worker.repositories.mark_run_running", mark_run_running)
    monkeypatch.setattr("app.worker.repositories.append_event", append_event)
    monkeypatch.setattr("app.worker.repositories.upsert_run_step", upsert_run_step, raising=False)
    monkeypatch.setattr("app.worker.repositories.list_run_steps", list_run_steps)
    monkeypatch.setattr("app.worker.repositories.complete_run", complete_run)
    monkeypatch.setattr("app.worker.repositories.append_message", fake_append_message)

    outcome = await process_run_payload(
        base_payload(file_ids=[], skill_id="general-chat", agent_id="general-agent"),
        AdapterRegistry({"fake": MultiAgentAdapter()}),
    )

    assert outcome.status == "succeeded"
    assert completed_result["multi_agent"] == {
        "steps": [
            {
                "step_key": "code",
                "status": "succeeded",
                "role": "coding",
                "sequence": 1,
                "depends_on": [],
                "checkpoint_reused": True,
                "output": "checkpointed code output",
                "error_code": None,
                "error": None,
                "missing_dependencies": [],
            },
            {
                "step_key": "verify",
                "status": "succeeded",
                "role": "test",
                "sequence": 2,
                "depends_on": ["code"],
                "checkpoint_reused": False,
                "output": "verify output",
                "error_code": None,
                "error": None,
                "missing_dependencies": [],
            },
        ],
        "reused_step_keys": ["code"],
        "completed_step_outputs": {
            "code": "checkpointed code output",
            "verify": "verify output",
        },
        "counts": {
            "total": 2,
            "pending": 0,
            "succeeded": 2,
            "failed": 0,
            "running": 0,
            "cancelled": 0,
            "reused": 1,
            "blocked": 0,
        },
    }


@pytest.mark.asyncio
async def test_worker_includes_multi_agent_step_summary_in_failed_result(monkeypatch):
    failed_result = {}

    class FailingMultiAgentAdapter:
        async def submit_run(self, payload, event_sink=None):
            await event_sink(
                event_type="agent_step_completed",
                stage="agent",
                message="coding agent completed",
                payload={
                    "role": "coding",
                    "step_key": "code",
                    "step_index": 1,
                    "depends_on": [],
                    "output": "code output",
                },
            )
            await event_sink(
                event_type="agent_step_failed",
                stage="agent",
                message="test agent failed",
                payload={
                    "role": "test",
                    "step_key": "verify",
                    "step_index": 2,
                    "depends_on": ["code"],
                    "error_code": "multi_agent_step_failed",
                    "error": "tests failed",
                },
            )
            return ExecutorResult(
                status="failed",
                adapter_version="adapter/1",
                executor_type="fake",
                executor_version="fake/1",
                capabilities={"multi_agent": True},
                result={"error_code": "multi_agent_step_failed", "message": "tests failed"},
            )

    async def mark_run_running(conn, *, tenant_id, run_id):
        return True

    async def append_event(conn, **kwargs):
        return "evt-a"

    async def upsert_run_step(conn, **kwargs):
        return "step-a"

    async def list_run_steps(conn, *, tenant_id, run_id):
        return [
            {
                "id": "step-code",
                "run_id": run_id,
                "step_key": "code",
                "step_kind": "agent",
                "status": "succeeded",
                "title": "coding agent completed",
                "role": "coding",
                "sequence": 1,
                "payload_json": {
                    "depends_on": [],
                    "output": "code output",
                },
                "started_at": None,
                "finished_at": None,
                "created_at": None,
                "updated_at": None,
            },
            {
                "id": "step-verify",
                "run_id": run_id,
                "step_key": "verify",
                "step_kind": "agent",
                "status": "failed",
                "title": "test agent failed",
                "role": "test",
                "sequence": 2,
                "payload_json": {
                    "depends_on": ["code"],
                    "error_code": "multi_agent_step_failed",
                    "error": "tests failed",
                },
                "started_at": None,
                "finished_at": None,
                "created_at": None,
                "updated_at": None,
            },
        ]

    async def fail_run(conn, *, tenant_id, run_id, error_code, error_message, result_json=None):
        failed_result.update(result_json or {})

    monkeypatch.setattr("app.worker.transaction", fake_transaction)
    monkeypatch.setattr("app.worker.repositories.mark_run_running", mark_run_running)
    monkeypatch.setattr("app.worker.repositories.append_event", append_event)
    monkeypatch.setattr("app.worker.repositories.upsert_run_step", upsert_run_step, raising=False)
    monkeypatch.setattr("app.worker.repositories.list_run_steps", list_run_steps)
    monkeypatch.setattr("app.worker.repositories.fail_run", fail_run)

    outcome = await process_run_payload(
        base_payload(file_ids=[], skill_id="general-chat", agent_id="general-agent"),
        AdapterRegistry({"fake": FailingMultiAgentAdapter()}),
    )

    assert outcome.status == "failed"
    assert failed_result["multi_agent"] == {
        "steps": [
            {
                "step_key": "code",
                "status": "succeeded",
                "role": "coding",
                "sequence": 1,
                "depends_on": [],
                "checkpoint_reused": False,
                "output": "code output",
                "error_code": None,
                "error": None,
                "missing_dependencies": [],
            },
            {
                "step_key": "verify",
                "status": "failed",
                "role": "test",
                "sequence": 2,
                "depends_on": ["code"],
                "checkpoint_reused": False,
                "output": None,
                "error_code": "multi_agent_step_failed",
                "error": "tests failed",
                "missing_dependencies": [],
            },
        ],
        "reused_step_keys": [],
        "completed_step_outputs": {"code": "code output"},
        "counts": {
            "total": 2,
            "pending": 0,
            "succeeded": 1,
            "failed": 1,
            "running": 0,
            "cancelled": 0,
            "reused": 0,
            "blocked": 0,
        },
    }


def test_executor_result_schema_validation_blocks_unstable_adapter_output():
    result = ExecutorResult(
        status="completed",
        adapter_version="fake-adapter/1",
        executor_type="fake",
        executor_version="fake-executor/1",
        capabilities={},
    )

    with pytest.raises(ValueError, match="Unsupported executor status"):
        result.validate()


def test_default_adapter_registry_does_not_expose_embedded_poco_kernel():
    with pytest.raises(KeyError, match="Unknown executor_type: embedded-poco-kernel"):
        AdapterRegistry().get("embedded-poco-kernel")


def test_explicit_empty_adapter_registry_does_not_fall_back_to_defaults():
    with pytest.raises(KeyError, match="Unknown executor_type: claude-agent-worker"):
        AdapterRegistry({}).get("claude-agent-worker")


@pytest.mark.asyncio
async def test_worker_adds_artifact_links_to_success_result_message(monkeypatch):
    calls = []

    class LocalPathAdapter:
        async def submit_run(self, payload, event_sink=None):
            return ExecutorResult(
                status="succeeded",
                adapter_version="adapter/1",
                executor_type="qa-file-reviewer-local",
                executor_version="runner/1",
                capabilities={"artifacts": True},
                result={
                    "message": (
                        "文件审核\n"
                        "详细报告: /tmp/workspace/report.txt\n"
                        "批注文档: /tmp/workspace/reviewed.docx"
                    )
                },
                artifacts=[
                    ArtifactManifest(
                        artifact_type="reviewed_docx",
                        label="审核 Word",
                        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                        storage_key="tenants/tenant-a/workspaces/workspace-a/sessions/session-a/runs/run-a/artifacts/1/reviewed.docx",
                        size_bytes=123,
                        manifest={},
                    )
                ],
            )

    async def mark_run_running(conn, *, tenant_id, run_id):
        return True

    async def append_event(conn, *, tenant_id, run_id, event_type, stage, message, payload=None):
        calls.append(("event", event_type, stage, payload))

    async def create_artifact(conn, **kwargs):
        calls.append(("artifact", kwargs["artifact_id"], kwargs["storage_key"]))

    async def complete_run(conn, *, tenant_id, run_id, result_json):
        calls.append(("complete", result_json))

    monkeypatch.setattr("app.worker.transaction", fake_transaction)
    monkeypatch.setattr("app.worker.repositories.mark_run_running", mark_run_running)
    monkeypatch.setattr("app.worker.repositories.append_event", append_event)
    monkeypatch.setattr("app.worker.repositories.create_artifact", create_artifact)
    monkeypatch.setattr("app.worker.repositories.complete_run", complete_run)
    monkeypatch.setattr("app.worker.repositories.append_message", fake_append_message)
    generated_ids = iter(["art_reviewed"])
    monkeypatch.setattr("app.worker.repositories.new_id", lambda prefix: next(generated_ids))

    outcome = await process_run_payload(base_payload(), AdapterRegistry({"fake": LocalPathAdapter()}))

    assert outcome.status == "succeeded"
    complete_payload = next(item[1] for item in calls if item[0] == "complete")
    assert "/tmp/workspace" not in complete_payload["message"]
    assert "审核 Word: /api/ai/artifacts/art_reviewed/download" in complete_payload["message"]
    assert complete_payload["artifacts"][0]["id"] == "art_reviewed"
    assert complete_payload["artifacts"][0]["download_url"] == "/api/ai/artifacts/art_reviewed/download"
    assert "storage_key" not in complete_payload["artifacts"][0]
    assert "tenants/" not in str(complete_payload)


@pytest.mark.asyncio
async def test_worker_sanitizes_artifact_manifest_paths_before_persisting(monkeypatch):
    created = []

    class PathManifestAdapter:
        async def submit_run(self, payload, event_sink=None):
            return ExecutorResult(
                status="succeeded",
                adapter_version="adapter/1",
                executor_type="fake",
                executor_version="fake/1",
                capabilities={"artifacts": True},
                result={"message": "done"},
                artifacts=[
                    ArtifactManifest(
                        artifact_type="reviewed_docx",
                        label="审核 Word",
                        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                        storage_key="tenants/tenant-a/workspaces/workspace-a/sessions/session-a/runs/run-a/artifacts/1/reviewed.docx",
                        size_bytes=12,
                        manifest={
                            "review_result": "/tmp/workspace/output/review_result.json",
                            "runner": r"C:\Users\Xinlin.jiang\.codex\skills\qa-file-reviewer\scripts\run_qa_review.py",
                            "cwd": "/tmp/workspace/output",
                            "source_executor": "qa-file-reviewer-local",
                        },
                    )
                ],
            )

    async def mark_run_running(conn, *, tenant_id, run_id):
        return True

    async def append_event(conn, **kwargs):
        return "evt-a"

    async def create_artifact(conn, **kwargs):
        created.append(kwargs)

    async def complete_run(conn, **kwargs):
        return None

    monkeypatch.setattr("app.worker.transaction", fake_transaction)
    monkeypatch.setattr("app.worker.repositories.mark_run_running", mark_run_running)
    monkeypatch.setattr("app.worker.repositories.append_event", append_event)
    monkeypatch.setattr("app.worker.repositories.create_artifact", create_artifact)
    monkeypatch.setattr("app.worker.repositories.complete_run", complete_run)
    monkeypatch.setattr("app.worker.repositories.append_message", fake_append_message)
    monkeypatch.setattr("app.worker.repositories.new_id", lambda prefix: "art-a")

    outcome = await process_run_payload(base_payload(), AdapterRegistry({"fake": PathManifestAdapter()}))

    assert outcome.status == "succeeded"
    manifest = created[0]["manifest_json"]
    assert manifest == {
        "schema_version": "ai-platform.artifact-manifest.v1",
        "artifact_type": "reviewed_docx",
        "source_executor": "qa-file-reviewer-local",
    }
    assert "/tmp/" not in str(manifest)
    assert "C:" not in str(manifest)


@pytest.mark.asyncio
async def test_worker_appends_user_visible_execution_timeline(monkeypatch):
    events = []

    class ArtifactAdapter:
        async def submit_run(self, payload, event_sink=None):
            return ExecutorResult(
                status="succeeded",
                adapter_version="adapter/1",
                executor_type="fake",
                executor_version="fake/1",
                capabilities={"artifacts": True, "streaming": False, "skills": True},
                result={"message": "done"},
                artifacts=[
                    ArtifactManifest(
                        artifact_type="reviewed_docx",
                        label="批注 Word",
                        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                        storage_key=(
                            "tenants/tenant-a/workspaces/workspace-a/sessions/session-a/"
                            "runs/run-a/artifacts/1/reviewed.docx"
                        ),
                        size_bytes=12,
                        manifest={},
                    )
                ],
            )

    async def mark_run_running(conn, *, tenant_id, run_id):
        return True

    async def append_event(conn, *, tenant_id, run_id, event_type, stage, message, payload=None):
        events.append({"event_type": event_type, "stage": stage, "message": message, "payload": payload or {}})

    async def create_artifact(conn, **kwargs):
        return None

    async def complete_run(conn, *, tenant_id, run_id, result_json):
        return None

    monkeypatch.setattr("app.worker.transaction", fake_transaction)
    monkeypatch.setattr("app.worker.repositories.mark_run_running", mark_run_running)
    monkeypatch.setattr("app.worker.repositories.append_event", append_event)
    monkeypatch.setattr("app.worker.repositories.create_artifact", create_artifact)
    monkeypatch.setattr("app.worker.repositories.complete_run", complete_run)
    monkeypatch.setattr("app.worker.repositories.append_message", fake_append_message)
    monkeypatch.setattr("app.worker.repositories.new_id", lambda prefix: "art-a")

    outcome = await process_run_payload(base_payload(), AdapterRegistry({"fake": ArtifactAdapter()}))

    assert outcome.status == "succeeded"
    event_types = [item["event_type"] for item in events]
    assert "worker_started" in event_types
    assert "artifact_created" in event_types
    assert "assistant_message_created" in event_types
    assert "run_succeeded" in event_types
    user_visible_types = {"worker_started", "artifact_created", "assistant_message_created", "run_succeeded"}
    assert all(
        item["payload"].get("visible_to_user") is True
        for item in events
        if item["event_type"] in user_visible_types
    )


@pytest.mark.asyncio
async def test_worker_records_general_chat_token_events(monkeypatch):
    events = []

    class StreamingAdapter:
        async def submit_run(self, payload, event_sink=None):
            if event_sink:
                await event_sink(
                    event_type="assistant_delta",
                    stage="message",
                    message="你好",
                    payload={"delta": "你好", "visible_to_user": True},
                )
            return ExecutorResult(
                status="succeeded",
                adapter_version="streaming-test/1",
                executor_type="claude-agent-worker",
                executor_version="test",
                capabilities={"streaming": True},
                result={"message": "你好"},
            )

    class Registry:
        def get(self, executor_type):
            return StreamingAdapter()

    async def mark_run_running(conn, *, tenant_id, run_id):
        return True

    async def append_event(conn, **kwargs):
        events.append(kwargs)
        return f"evt_{len(events)}"

    async def complete_run(conn, **kwargs):
        events.append({"event_type": "complete_run", "result_json": kwargs["result_json"]})

    monkeypatch.setattr("app.worker.transaction", fake_transaction)
    monkeypatch.setattr("app.worker.repositories.mark_run_running", mark_run_running)
    monkeypatch.setattr("app.worker.repositories.append_event", append_event)
    monkeypatch.setattr("app.worker.repositories.complete_run", complete_run)
    monkeypatch.setattr("app.worker.repositories.create_artifact", lambda *args, **kwargs: None)
    monkeypatch.setattr("app.worker.repositories.fail_run", lambda *args, **kwargs: None)
    monkeypatch.setattr("app.worker.repositories.append_message", fake_append_message)

    payload = base_payload(skill_id="general-chat", executor_type="claude-agent-worker")
    outcome = await process_run_payload(payload, registry=Registry(), worker_id="worker-stream")

    assert outcome.status == "succeeded"
    assert any(event["event_type"] == "assistant_delta" for event in events)


@pytest.mark.asyncio
async def test_worker_processes_embedded_poco_kernel_and_persists_stream_events(monkeypatch):
    from app.executors.embedded_poco import EmbeddedPocoAdapter

    events = []
    messages = []

    async def mark_run_running(conn, *, tenant_id, run_id):
        return True

    async def append_event(conn, **kwargs):
        events.append(kwargs)
        return f"evt_{len(events)}"

    async def complete_run(conn, **kwargs):
        events.append({"event_type": "complete_run", "result_json": kwargs["result_json"]})

    async def append_message(conn, **kwargs):
        messages.append(kwargs)
        return "msg-a"

    monkeypatch.setattr("app.worker.transaction", fake_transaction)
    monkeypatch.setattr("app.worker.repositories.mark_run_running", mark_run_running)
    monkeypatch.setattr("app.worker.repositories.append_event", append_event)
    monkeypatch.setattr("app.worker.repositories.complete_run", complete_run)
    monkeypatch.setattr("app.worker.repositories.append_message", append_message)

    outcome = await process_run_payload(
        base_payload(
            agent_id="general-agent",
            skill_id="general-chat",
            file_ids=[],
            input={"message": "hello"},
            executor_type="embedded-poco-kernel",
        ),
        registry=AdapterRegistry({"embedded-poco-kernel": EmbeddedPocoAdapter()}),
        worker_id="worker-embedded",
    )

    assert outcome.status == "succeeded"
    event_types = [event["event_type"] for event in events]
    assert "run_started" in event_types
    assert "assistant_delta" in event_types
    assert "run_completed" in event_types
    assert "assistant_message_created" in event_types
    assert messages[0]["role"] == "assistant"
    assert messages[0]["content"] == "hello"


@pytest.mark.asyncio
async def test_worker_persists_terminal_assistant_message(monkeypatch):
    calls = []

    class MessageAdapter:
        async def submit_run(self, payload, event_sink=None):
            return ExecutorResult(
                status="succeeded",
                adapter_version="adapter/1",
                executor_type="fake",
                executor_version="fake/1",
                capabilities={"streaming": False},
                result={"message": "最终回答"},
            )

    async def mark_run_running(conn, *, tenant_id, run_id):
        return True

    async def append_event(conn, **kwargs):
        return "evt-a"

    async def complete_run(conn, **kwargs):
        calls.append(("complete", kwargs["result_json"]["message"]))

    async def append_message(conn, **kwargs):
        calls.append(("message", kwargs["role"], kwargs["content"], kwargs["run_id"]))
        return "msg-a"

    monkeypatch.setattr("app.worker.transaction", fake_transaction)
    monkeypatch.setattr("app.worker.repositories.mark_run_running", mark_run_running)
    monkeypatch.setattr("app.worker.repositories.append_event", append_event)
    monkeypatch.setattr("app.worker.repositories.complete_run", complete_run)
    monkeypatch.setattr("app.worker.repositories.append_message", append_message)

    outcome = await process_run_payload(base_payload(), AdapterRegistry({"fake": MessageAdapter()}))

    assert outcome.status == "succeeded"
    assert ("complete", "最终回答") in calls
    assert ("message", "assistant", "最终回答", "run-a") in calls


@pytest.mark.asyncio
async def test_worker_audits_read_only_ragflow_tool_call(monkeypatch):
    audits = []
    events = []
    snapshots = []

    class RagflowAdapter:
        async def submit_run(self, payload, event_sink=None):
            return ExecutorResult(
                status="succeeded",
                adapter_version="ragflow-adapter/1",
                executor_type="ragflow",
                executor_version="ragflow-retrieval-http",
                capabilities={"tools": True, "streaming": False},
                result={"message": "answer"},
                executor_payload={
                    "dataset_ids": ["dataset-a"],
                    "reference_ids": [
                        {"index": 1, "dataset_id": "dataset-a", "document_id": "doc-a", "chunk_id": "chunk-a"}
                    ],
                },
            )

    class Registry:
        def get(self, executor_type):
            return RagflowAdapter()

    async def fake_mark_run_running(conn, *, tenant_id, run_id):
        return True

    async def fake_append_event(conn, **kwargs):
        events.append(kwargs["event_type"])
        return f"evt_{len(events)}"

    async def fake_append_audit_log(conn, **kwargs):
        audits.append(kwargs)
        return f"aud_{len(audits)}"

    async def fake_ensure_mcp_tool_active(conn, *, tenant_id, tool_id):
        return {"id": tool_id, "status": "active", "write_capable": False, "risk_level": "low"}

    async def fake_complete_run(conn, **kwargs):
        return None

    async def fake_upsert_run_skill_snapshot(conn, **kwargs):
        snapshots.append(kwargs)

    monkeypatch.setattr("app.worker.transaction", fake_transaction)
    monkeypatch.setattr("app.worker.repositories.mark_run_running", fake_mark_run_running)
    monkeypatch.setattr("app.worker.repositories.append_event", fake_append_event)
    monkeypatch.setattr("app.worker.repositories.append_audit_log", fake_append_audit_log)
    monkeypatch.setattr("app.worker.repositories.ensure_mcp_tool_active", fake_ensure_mcp_tool_active, raising=False)
    monkeypatch.setattr("app.worker.repositories.complete_run", fake_complete_run)
    monkeypatch.setattr("app.worker.repositories.append_message", fake_append_message)
    monkeypatch.setattr("app.worker.repositories.upsert_run_skill_snapshot", fake_upsert_run_skill_snapshot)

    outcome = await process_run_payload(
        base_payload(
            skill_id="ragflow-knowledge-search",
            executor_type="ragflow",
            skill_version="hash-ragflow",
            skill_manifests=[
                {
                    "skill_id": "ragflow-knowledge-search",
                    "version": "hash-ragflow",
                    "content_hash": "hash-ragflow",
                    "source": {"kind": "builtin", "asset_dir": "ragflow-knowledge-search"},
                    "dependency_ids": [],
                    "allowed": True,
                    "staged": False,
                }
            ],
        ),
        registry=Registry(),
        worker_id="worker-ragflow",
    )

    assert outcome.status == "succeeded"
    assert "mcp_tool_call_started" in events
    assert "mcp_tool_call_completed" in events
    assert audits[0]["action"] == "mcp_tool_policy_allowed"
    assert audits[0]["payload_json"]["auto_allowed"] is True
    assert audits[0]["payload_json"]["risk_level"] == "low"
    assert audits[0]["payload_json"]["write_capable"] is False
    assert audits[1]["action"] == "mcp_tool_call_completed"
    assert audits[1]["trace_id"] == "trace_run_a"
    assert audits[1]["payload_json"]["dataset_ids"] == ["dataset-a"]
    assert audits[1]["payload_json"]["reference_ids"] == [
        {"index": 1, "dataset_id": "dataset-a", "document_id": "doc-a", "chunk_id": "chunk-a"}
    ]
    assert snapshots == [
        {
            "tenant_id": "tenant-a",
            "run_id": "run-a",
            "skill_id": "ragflow-knowledge-search",
            "skill_version": "hash-ragflow",
            "content_hash": "hash-ragflow",
            "source_json": {"kind": "builtin", "asset_dir": "ragflow-knowledge-search"},
            "dependency_ids": [],
            "allowed": True,
            "staged": True,
            "used": True,
            "used_skills_source": "executor_native",
            "inferred_used": False,
        }
    ]


@pytest.mark.asyncio
async def test_worker_does_not_mark_failed_ragflow_result_as_native_used(monkeypatch):
    snapshots = []
    failures = []

    class FailedRagflowAdapter:
        async def submit_run(self, payload, event_sink=None):
            return ExecutorResult(
                status="failed",
                adapter_version="ragflow-adapter/1",
                executor_type="ragflow",
                executor_version="ragflow-retrieval-http",
                capabilities={"tools": True, "streaming": False},
                result={"message": "RAGFlow retrieval failed.", "error_code": "ragflow_api_error"},
                executor_payload={"dataset_ids": ["dataset-a"]},
            )

    class Registry:
        def get(self, executor_type):
            return FailedRagflowAdapter()

    async def fake_mark_run_running(conn, *, tenant_id, run_id):
        return True

    async def fake_ensure_mcp_tool_active(conn, *, tenant_id, tool_id):
        return {"id": tool_id, "status": "active", "write_capable": False, "risk_level": "low"}

    async def fake_append_event(conn, **kwargs):
        return "evt-a"

    async def fake_append_audit_log(conn, **kwargs):
        return "audit-a"

    async def fake_fail_run(conn, **kwargs):
        failures.append(kwargs)

    async def fake_upsert_run_skill_snapshot(conn, **kwargs):
        snapshots.append(kwargs)

    monkeypatch.setattr("app.worker.transaction", fake_transaction)
    monkeypatch.setattr("app.worker.repositories.mark_run_running", fake_mark_run_running)
    monkeypatch.setattr("app.worker.repositories.ensure_mcp_tool_active", fake_ensure_mcp_tool_active, raising=False)
    monkeypatch.setattr("app.worker.repositories.append_event", fake_append_event)
    monkeypatch.setattr("app.worker.repositories.append_audit_log", fake_append_audit_log)
    monkeypatch.setattr("app.worker.repositories.fail_run", fake_fail_run)
    monkeypatch.setattr("app.worker.repositories.upsert_run_skill_snapshot", fake_upsert_run_skill_snapshot)

    outcome = await process_run_payload(
        base_payload(
            skill_id="ragflow-knowledge-search",
            executor_type="ragflow",
            skill_version="hash-ragflow",
            skill_manifests=[
                {
                    "skill_id": "ragflow-knowledge-search",
                    "version": "hash-ragflow",
                    "content_hash": "hash-ragflow",
                    "source": {"kind": "builtin", "asset_dir": "ragflow-knowledge-search"},
                    "dependency_ids": [],
                    "allowed": True,
                    "staged": False,
                }
            ],
        ),
        registry=Registry(),
        worker_id="worker-ragflow",
    )

    assert outcome.status == "failed"
    assert failures[0]["error_code"] == "ragflow_api_error"
    assert snapshots == [
        {
            "tenant_id": "tenant-a",
            "run_id": "run-a",
            "skill_id": "ragflow-knowledge-search",
            "skill_version": "hash-ragflow",
            "content_hash": "hash-ragflow",
            "source_json": {"kind": "builtin", "asset_dir": "ragflow-knowledge-search"},
            "dependency_ids": [],
            "allowed": True,
            "staged": True,
            "used": False,
            "used_skills_source": "",
            "inferred_used": False,
        }
    ]


@pytest.mark.asyncio
async def test_worker_blocks_disabled_mcp_tool_before_dispatch(monkeypatch):
    calls = []

    class RagflowAdapterMustNotRun:
        async def submit_run(self, payload, event_sink=None):
            calls.append(("adapter", payload.run_id))
            raise AssertionError("disabled MCP tool must not reach adapter dispatch")

    class Registry:
        def get(self, executor_type):
            return RagflowAdapterMustNotRun()

    async def mark_run_running(conn, *, tenant_id, run_id):
        calls.append(("running", tenant_id, run_id))
        return True

    async def ensure_mcp_tool_active(conn, *, tenant_id, tool_id):
        calls.append(("policy", tenant_id, tool_id))
        raise RepositoryConflictError("mcp_tool_disabled")

    async def fail_run(conn, *, tenant_id, run_id, error_code, error_message, result_json=None):
        calls.append(("fail", error_code, error_message))

    async def append_event(conn, *, tenant_id, run_id, event_type, stage, message, payload=None):
        calls.append(("event", event_type, stage, payload or {}))

    monkeypatch.setattr("app.worker.transaction", fake_transaction)
    monkeypatch.setattr("app.worker.repositories.mark_run_running", mark_run_running)
    monkeypatch.setattr("app.worker.repositories.ensure_mcp_tool_active", ensure_mcp_tool_active, raising=False)
    monkeypatch.setattr("app.worker.repositories.fail_run", fail_run)
    monkeypatch.setattr("app.worker.repositories.append_event", append_event)

    outcome = await process_run_payload(
        base_payload(skill_id="ragflow-knowledge-search", executor_type="ragflow"),
        registry=Registry(),
        worker_id="worker-ragflow",
    )

    assert outcome.status == "failed"
    assert outcome.error_code == "mcp_tool_disabled"
    assert ("policy", "tenant-a", "ragflow-knowledge-search") in calls
    assert not any(item[0] == "adapter" for item in calls)
    assert any(item[0] == "fail" and item[1] == "mcp_tool_disabled" for item in calls)
    denied_event = next(item for item in calls if item[0] == "event" and item[1] == "mcp_tool_denied")
    assert denied_event[2] == "tool_policy"
    assert denied_event[3]["mcp_tool_id"] == "ragflow-knowledge-search"
    assert denied_event[3]["visible_to_user"] is True


@pytest.mark.asyncio
async def test_worker_blocks_high_risk_mcp_tool_without_permission_decision(monkeypatch):
    calls = []

    class RagflowAdapterMustNotRun:
        async def submit_run(self, payload, event_sink=None):
            calls.append(("adapter", payload.run_id))
            raise AssertionError("high-risk MCP tool must not dispatch without permission")

    class Registry:
        def get(self, executor_type):
            return RagflowAdapterMustNotRun()

    async def mark_run_running(conn, *, tenant_id, run_id):
        calls.append(("running", tenant_id, run_id))
        return True

    async def ensure_mcp_tool_active(conn, *, tenant_id, tool_id):
        calls.append(("policy", tenant_id, tool_id))
        return {"id": tool_id, "status": "active", "write_capable": True, "risk_level": "high"}

    async def get_latest_tool_permission_decision(conn, **kwargs):
        calls.append(("decision_lookup", kwargs["tool_id"]))
        return None

    async def fail_run(conn, *, tenant_id, run_id, error_code, error_message, result_json=None):
        calls.append(("fail", error_code, error_message))

    async def append_event(conn, *, tenant_id, run_id, event_type, stage, message, payload=None):
        calls.append(("event", event_type, stage, payload or {}))

    async def append_audit_log(conn, **kwargs):
        calls.append(("audit", kwargs["action"], kwargs["payload_json"]))
        return "audit-a"

    monkeypatch.setattr("app.worker.transaction", fake_transaction)
    monkeypatch.setattr("app.worker.repositories.mark_run_running", mark_run_running)
    monkeypatch.setattr("app.worker.repositories.ensure_mcp_tool_active", ensure_mcp_tool_active, raising=False)
    monkeypatch.setattr(
        "app.worker.repositories.get_latest_tool_permission_decision",
        get_latest_tool_permission_decision,
        raising=False,
    )
    monkeypatch.setattr("app.worker.repositories.fail_run", fail_run)
    monkeypatch.setattr("app.worker.repositories.append_event", append_event)
    monkeypatch.setattr("app.worker.repositories.append_audit_log", append_audit_log)

    outcome = await process_run_payload(
        base_payload(skill_id="ragflow-knowledge-search", executor_type="ragflow"),
        registry=Registry(),
        worker_id="worker-ragflow",
    )

    assert outcome.status == "failed"
    assert outcome.error_code == "tool_permission_required"
    assert ("decision_lookup", "ragflow-knowledge-search") in calls
    assert not any(item[0] == "adapter" for item in calls)
    assert any(item[0] == "fail" and item[1] == "tool_permission_required" for item in calls)
    denied_event = next(item for item in calls if item[0] == "event" and item[1] == "mcp_tool_denied")
    assert denied_event[2] == "tool_policy"
    assert denied_event[3]["policy"] == "tool_permission_gate"
    assert denied_event[3]["reason"] == "tool_permission_required"
    assert denied_event[3]["risk_level"] == "high"
    assert denied_event[3]["write_capable"] is True
    denied_audit = next(item for item in calls if item[0] == "audit")
    assert denied_audit[1] == "mcp_tool_policy_denied"
    assert denied_audit[2]["reason"] == "tool_permission_required"


@pytest.mark.asyncio
async def test_worker_allows_high_risk_mcp_tool_with_permission_decision(monkeypatch):
    calls = []
    expected_input_sha256 = hashlib.sha256(
        json.dumps({"mode": "file"}, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()

    class RagflowAdapter:
        async def submit_run(self, payload, event_sink=None):
            calls.append(("adapter", payload.run_id))
            return ExecutorResult(
                status="succeeded",
                adapter_version="ragflow-adapter/1",
                executor_type="ragflow",
                executor_version="ragflow-retrieval-http",
                capabilities={"tools": True},
                result={"message": "answer"},
            )

    class Registry:
        def get(self, executor_type):
            return RagflowAdapter()

    async def mark_run_running(conn, *, tenant_id, run_id):
        return True

    async def ensure_mcp_tool_active(conn, *, tenant_id, tool_id):
        return {"id": tool_id, "status": "active", "write_capable": True, "risk_level": "high"}

    async def get_latest_tool_permission_decision(conn, **kwargs):
        calls.append(("decision_lookup", kwargs["tool_id"]))
        assert kwargs["tool_call_id"].startswith("mcp_")
        if kwargs.get("request_payload_json", {}).get("input_sha256") != expected_input_sha256:
            return None
        return {"id": "tpr_allow", "decision": "allow_for_run"}

    async def append_event(conn, **kwargs):
        calls.append(("event", kwargs["event_type"]))
        return "evt-a"

    async def append_audit_log(conn, **kwargs):
        calls.append(("audit", kwargs["action"], kwargs["payload_json"]))
        return "audit-a"

    async def complete_run(conn, **kwargs):
        calls.append(("complete", kwargs["result_json"]["message"]))

    async def upsert_run_skill_snapshot(conn, **kwargs):
        calls.append(("snapshot", kwargs["skill_id"], kwargs["used"]))

    monkeypatch.setattr("app.worker.transaction", fake_transaction)
    monkeypatch.setattr("app.worker.repositories.mark_run_running", mark_run_running)
    monkeypatch.setattr("app.worker.repositories.ensure_mcp_tool_active", ensure_mcp_tool_active, raising=False)
    monkeypatch.setattr(
        "app.worker.repositories.get_latest_tool_permission_decision",
        get_latest_tool_permission_decision,
        raising=False,
    )
    monkeypatch.setattr("app.worker.repositories.append_event", append_event)
    monkeypatch.setattr("app.worker.repositories.append_audit_log", append_audit_log)
    monkeypatch.setattr("app.worker.repositories.complete_run", complete_run)
    monkeypatch.setattr("app.worker.repositories.append_message", fake_append_message)
    monkeypatch.setattr("app.worker.repositories.upsert_run_skill_snapshot", upsert_run_skill_snapshot)

    outcome = await process_run_payload(
        base_payload(
            skill_id="ragflow-knowledge-search",
            executor_type="ragflow",
            skill_version="hash-ragflow",
            skill_manifests=[primary_manifest("ragflow-knowledge-search", "hash-ragflow")],
        ),
        registry=Registry(),
        worker_id="worker-ragflow",
    )

    assert outcome.status == "succeeded"
    assert ("decision_lookup", "ragflow-knowledge-search") in calls
    assert ("adapter", "run-a") in calls
    allowed_audit = next(item for item in calls if item[0] == "audit" and item[1] == "mcp_tool_policy_allowed")
    assert allowed_audit[2]["decision"] == "allow_for_run"
    assert allowed_audit[2]["permission_request_id"] == "tpr_allow"
    assert allowed_audit[2]["auto_allowed"] is False


@pytest.mark.asyncio
async def test_worker_consumes_allow_once_mcp_decision_before_dispatch(monkeypatch):
    calls = []

    class RagflowAdapter:
        async def submit_run(self, payload, event_sink=None):
            calls.append(("adapter", payload.run_id))
            return ExecutorResult(
                status="succeeded",
                adapter_version="ragflow-adapter/1",
                executor_type="ragflow",
                executor_version="ragflow-retrieval-http",
                capabilities={"tools": True},
                result={"message": "answer"},
            )

    class Registry:
        def get(self, executor_type):
            return RagflowAdapter()

    async def mark_run_running(conn, *, tenant_id, run_id):
        return True

    async def ensure_mcp_tool_active(conn, *, tenant_id, tool_id):
        return {"id": tool_id, "status": "active", "write_capable": True, "risk_level": "high"}

    async def get_latest_tool_permission_decision(conn, **kwargs):
        calls.append(("decision_lookup", kwargs["tool_id"]))
        return {"id": "tpr-once", "decision": "allow_once"}

    async def consume_tool_permission_decision(conn, **kwargs):
        calls.append(("consume", kwargs))
        return {"id": kwargs["request_id"], "decision": "allow_once", "status": "consumed"}

    async def append_event(conn, **kwargs):
        calls.append(("event", kwargs["event_type"]))
        return "evt-a"

    async def append_audit_log(conn, **kwargs):
        calls.append(("audit", kwargs["action"], kwargs["payload_json"]))
        return "audit-a"

    async def complete_run(conn, **kwargs):
        calls.append(("complete", kwargs["result_json"]["message"]))

    async def upsert_run_skill_snapshot(conn, **kwargs):
        calls.append(("snapshot", kwargs["skill_id"], kwargs["used"]))

    monkeypatch.setattr("app.worker.transaction", fake_transaction)
    monkeypatch.setattr("app.worker.repositories.mark_run_running", mark_run_running)
    monkeypatch.setattr("app.worker.repositories.ensure_mcp_tool_active", ensure_mcp_tool_active, raising=False)
    monkeypatch.setattr(
        "app.worker.repositories.get_latest_tool_permission_decision",
        get_latest_tool_permission_decision,
        raising=False,
    )
    monkeypatch.setattr(
        "app.worker.repositories.consume_tool_permission_decision",
        consume_tool_permission_decision,
        raising=False,
    )
    monkeypatch.setattr("app.worker.repositories.append_event", append_event)
    monkeypatch.setattr("app.worker.repositories.append_audit_log", append_audit_log)
    monkeypatch.setattr("app.worker.repositories.complete_run", complete_run)
    monkeypatch.setattr("app.worker.repositories.append_message", fake_append_message)
    monkeypatch.setattr("app.worker.repositories.upsert_run_skill_snapshot", upsert_run_skill_snapshot)

    outcome = await process_run_payload(
        base_payload(
            skill_id="ragflow-knowledge-search",
            executor_type="ragflow",
            skill_version="hash-ragflow",
            skill_manifests=[primary_manifest("ragflow-knowledge-search", "hash-ragflow")],
        ),
        registry=Registry(),
        worker_id="worker-ragflow",
    )

    assert outcome.status == "succeeded"
    consume_calls = [item for item in calls if item[0] == "consume"]
    assert consume_calls, "allow_once MCP decision must be consumed before adapter dispatch"
    consume_call = consume_calls[0]
    adapter_call = next(item for item in calls if item[0] == "adapter")
    assert calls.index(consume_call) < calls.index(adapter_call)
    assert consume_call[1] == {
        "tenant_id": "tenant-a",
        "user_id": "user-a",
        "run_id": "run-a",
        "request_id": "tpr-once",
    }
    allowed_audit = next(item for item in calls if item[0] == "audit" and item[1] == "mcp_tool_policy_allowed")
    assert allowed_audit[2]["decision"] == "allow_once"
    assert allowed_audit[2]["permission_request_id"] == "tpr-once"


@pytest.mark.asyncio
async def test_worker_fails_closed_when_allow_once_mcp_decision_cannot_be_consumed(monkeypatch):
    calls = []

    class RagflowAdapterMustNotRun:
        async def submit_run(self, payload, event_sink=None):
            calls.append(("adapter", payload.run_id))
            raise AssertionError("expired or already-consumed allow_once must not reach adapter dispatch")

    class Registry:
        def get(self, executor_type):
            return RagflowAdapterMustNotRun()

    async def mark_run_running(conn, *, tenant_id, run_id):
        return True

    async def ensure_mcp_tool_active(conn, *, tenant_id, tool_id):
        return {"id": tool_id, "status": "active", "write_capable": True, "risk_level": "high"}

    async def get_latest_tool_permission_decision(conn, **kwargs):
        calls.append(("decision_lookup", kwargs["tool_id"]))
        return {"id": "tpr-once", "decision": "allow_once"}

    async def consume_tool_permission_decision(conn, **kwargs):
        calls.append(("consume", kwargs))
        return None

    async def fail_run(conn, *, tenant_id, run_id, error_code, error_message, result_json=None):
        calls.append(("fail", error_code, error_message))

    async def append_event(conn, *, tenant_id, run_id, event_type, stage, message, payload=None):
        calls.append(("event", event_type, stage, payload or {}))

    async def append_audit_log(conn, **kwargs):
        calls.append(("audit", kwargs["action"], kwargs["payload_json"]))
        return "audit-a"

    monkeypatch.setattr("app.worker.transaction", fake_transaction)
    monkeypatch.setattr("app.worker.repositories.mark_run_running", mark_run_running)
    monkeypatch.setattr("app.worker.repositories.ensure_mcp_tool_active", ensure_mcp_tool_active, raising=False)
    monkeypatch.setattr(
        "app.worker.repositories.get_latest_tool_permission_decision",
        get_latest_tool_permission_decision,
        raising=False,
    )
    monkeypatch.setattr(
        "app.worker.repositories.consume_tool_permission_decision",
        consume_tool_permission_decision,
        raising=False,
    )
    monkeypatch.setattr("app.worker.repositories.fail_run", fail_run)
    monkeypatch.setattr("app.worker.repositories.append_event", append_event)
    monkeypatch.setattr("app.worker.repositories.append_audit_log", append_audit_log)

    outcome = await process_run_payload(
        base_payload(skill_id="ragflow-knowledge-search", executor_type="ragflow"),
        registry=Registry(),
        worker_id="worker-ragflow",
    )

    assert outcome.status == "failed"
    assert outcome.error_code == "tool_permission_consumed_or_expired"
    assert not any(item[0] == "adapter" for item in calls)
    assert any(item[0] == "consume" and item[1]["request_id"] == "tpr-once" for item in calls)
    assert any(item[0] == "fail" and item[1] == "tool_permission_consumed_or_expired" for item in calls)
    denied_event = next(item for item in calls if item[0] == "event" and item[1] == "mcp_tool_denied")
    assert denied_event[2] == "tool_policy"
    assert denied_event[3]["reason"] == "tool_permission_consumed_or_expired"
    denied_audit = next(item for item in calls if item[0] == "audit")
    assert denied_audit[1] == "mcp_tool_policy_denied"
    assert denied_audit[2]["reason"] == "tool_permission_consumed_or_expired"
