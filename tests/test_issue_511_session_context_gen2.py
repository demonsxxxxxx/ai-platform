from contextlib import asynccontextmanager

import pytest
from fastapi import HTTPException

from app.auth import AuthPrincipal
from app.models import QueueRunPayload
from app.routes import lambchat_compat
from app.routes.runs import (
    _compensate_enqueue_failure,
    copy_run,
    resume_run,
    retry_run,
    run_context_ref_from_snapshot_row,
)
from app.worker import _ensure_worker_context_snapshot


@asynccontextmanager
async def _fake_transaction():
    yield object()


def _principal() -> AuthPrincipal:
    return AuthPrincipal(user_id="user-a", display_name="User A", tenant_id="tenant-a", roles=["admin"])


@pytest.mark.asyncio
async def test_worker_missing_physical_snapshot_never_rebuilds_context(monkeypatch):
    payload = QueueRunPayload(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        session_id="session-a",
        run_id="run-a",
        agent_id="agent-a",
        skill_id="skill-a",
        executor_type="fake",
        skill_version="v1",
        release_decision={
            "schema_version": "ai-platform.skill-release-decision.v1",
            "selected_version": "v1",
        },
        skill_manifests=[{"skill_id": "skill-a", "content_hash": "v1"}],
        context_snapshot_id="ctx-missing",
        context_snapshot={"context_snapshot_id": "ctx-missing"},
    )
    calls = []

    async def missing_snapshot(_conn, **kwargs):
        calls.append(kwargs)
        return None

    monkeypatch.setattr("app.worker.repositories.get_context_snapshot_for_worker", missing_snapshot)

    context_ref = await _ensure_worker_context_snapshot(object(), payload, trace_id="trace-run-a")

    assert context_ref is None
    assert calls == [{
        "tenant_id": "tenant-a",
        "workspace_id": "workspace-a",
        "user_id": "user-a",
        "session_id": "session-a",
        "run_id": "run-a",
        "context_snapshot_id": "ctx-missing",
    }]


@pytest.mark.asyncio
async def test_worker_materializes_complete_snapshot_authorized_conversation(monkeypatch):
    payload = QueueRunPayload(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        session_id="session-a",
        run_id="run-current",
        agent_id="agent-a",
        skill_id="skill-a",
        executor_type="fake",
        skill_version="v1",
        release_decision={
            "schema_version": "ai-platform.skill-release-decision.v1",
            "selected_version": "v1",
        },
        skill_manifests=[{"skill_id": "skill-a", "content_hash": "v1"}],
        context_snapshot_id="ctx-current",
        context_snapshot={"context_snapshot_id": "ctx-current"},
    )
    assistant = "analysis " + ("x" * 900) + "\nA. continue\nB. wait"
    rows = [
        {
            "id": "msg-user-prior",
            "run_id": "run-prior",
            "role": "user",
            "content": "Is this file enough?",
            "created_at": "2026-08-17T00:00:01Z",
        },
        {
            "id": "msg-assistant-prior",
            "run_id": "run-prior",
            "role": "assistant",
            "content": assistant,
            "created_at": "2026-08-17T00:00:02Z",
        },
        {
            "id": "msg-user-current",
            "run_id": "run-current",
            "role": "user",
            "content": "A",
            "created_at": "2026-08-17T00:00:03Z",
        },
    ]

    async def get_snapshot(_conn, **kwargs):
        return {
            "id": kwargs["context_snapshot_id"],
            "schema_version": "ai-platform.context-snapshot.v1",
            "included_message_ids": [row["id"] for row in rows],
            "included_file_ids": [],
            "included_artifact_ids": [],
            "included_memory_record_ids": [],
            "payload_json": {},
        }

    async def list_messages(_conn, **kwargs):
        assert kwargs["limit"] == 3
        return rows

    monkeypatch.setattr(
        "app.worker.repositories.get_context_snapshot_for_worker",
        get_snapshot,
    )
    monkeypatch.setattr(
        "app.worker.repositories.list_scoped_context_messages",
        list_messages,
    )

    context_ref = await _ensure_worker_context_snapshot(
        object(),
        payload,
        trace_id="trace-run-current",
    )

    assert context_ref is not None
    conversation = context_ref["conversation_context"]
    assert [message["role"] for message in conversation["messages"]] == [
        "user",
        "assistant",
    ]
    assert conversation["messages"][1]["content"] == assistant
    assert all(message["content"] != "A" for message in conversation["messages"])


@pytest.mark.asyncio
async def test_worker_rejects_incomplete_snapshot_message_materialization(monkeypatch):
    payload = QueueRunPayload(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        session_id="session-a",
        run_id="run-current",
        agent_id="agent-a",
        skill_id="skill-a",
        executor_type="fake",
        skill_version="v1",
        release_decision={
            "schema_version": "ai-platform.skill-release-decision.v1",
            "selected_version": "v1",
        },
        skill_manifests=[{"skill_id": "skill-a", "content_hash": "v1"}],
        context_snapshot_id="ctx-current",
        context_snapshot={"context_snapshot_id": "ctx-current"},
    )

    async def get_snapshot(_conn, **kwargs):
        return {
            "id": kwargs["context_snapshot_id"],
            "schema_version": "ai-platform.context-snapshot.v1",
            "included_message_ids": ["msg-user", "msg-assistant"],
            "included_file_ids": [],
            "included_artifact_ids": [],
            "included_memory_record_ids": [],
            "payload_json": {},
        }

    async def list_messages(_conn, **_kwargs):
        return [
            {
                "id": "msg-user",
                "run_id": "run-prior",
                "role": "user",
                "content": "question",
                "created_at": "2026-08-17T00:00:01Z",
            }
        ]

    monkeypatch.setattr(
        "app.worker.repositories.get_context_snapshot_for_worker",
        get_snapshot,
    )
    monkeypatch.setattr(
        "app.worker.repositories.list_scoped_context_messages",
        list_messages,
    )

    assert (
        await _ensure_worker_context_snapshot(
            object(), payload, trace_id="trace-run-current"
        )
        is None
    )


@pytest.mark.asyncio
async def test_run_enqueue_compensation_uses_the_durable_failed_transition(monkeypatch):
    calls = []

    async def mark_failed(_conn, **kwargs):
        calls.append(kwargs)

    monkeypatch.setattr("app.routes.runs.transaction", _fake_transaction)
    monkeypatch.setattr("app.routes.runs.repositories.mark_run_enqueue_failed", mark_failed)

    await _compensate_enqueue_failure(principal=_principal(), run_id="run-a", trace_id="trace-run-a")

    assert calls == [{
        "tenant_id": "tenant-a",
        "user_id": "user-a",
        "run_id": "run-a",
        "trace_id": "trace-run-a",
    }]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("route", "repository_method", "source_run_id"),
    [
        (copy_run, "copy_run_as_new_task", "run-copy-source"),
        (retry_run, "retry_run_as_new_task", "run-retry-source"),
        (resume_run, "resume_run_as_new_task", "run-resume-source"),
    ],
)
async def test_copied_run_enqueue_failures_commit_compensation_after_creation(
    monkeypatch,
    route,
    repository_method,
    source_run_id,
):
    """Copy compensates, while idempotent retry/resume retain their committed child."""

    committed: list[list[tuple[str, str]]] = []

    class TransactionState:
        def __init__(self) -> None:
            self.pending: list[tuple[str, str]] = []

    @asynccontextmanager
    async def tracked_transaction():
        state = TransactionState()
        try:
            yield state
        except BaseException:
            raise
        else:
            committed.append(list(state.pending))

    async def allow_admission(_conn, **_kwargs):
        return None

    async def allow_reauthorization(_conn, **_kwargs):
        return None

    async def acquire_operation_lock(_conn, **_kwargs):
        return None

    async def no_existing_operation(_conn, **_kwargs):
        return None

    async def record_operation(_conn, **_kwargs):
        return "event-operation"

    async def create_copied_run(conn, **_kwargs):
        conn.pending.append(("run_created", "run-enqueue-failure"))
        return {"run_id": "run-enqueue-failure", "session_id": "session-a"}

    async def prepared_queue_payload(_conn, **_kwargs):
        return {"run_id": "run-enqueue-failure"}

    async def fail_enqueue(_payload):
        raise RuntimeError("queue unavailable")

    async def mark_enqueue_failed(conn, **kwargs):
        conn.pending.append(("run_failed", str(kwargs["run_id"])))
        return True

    monkeypatch.setattr("app.routes.runs.transaction", tracked_transaction)
    monkeypatch.setattr("app.routes.runs.enforce_user_active_run_limit", allow_admission)
    monkeypatch.setattr("app.routes.runs.reauthorize_pinned_run_for_replay", allow_reauthorization)
    monkeypatch.setattr(
        "app.routes.runs.repositories.acquire_run_control_operation_lock",
        acquire_operation_lock,
    )
    monkeypatch.setattr(
        "app.routes.runs.repositories.get_run_control_operation",
        no_existing_operation,
    )
    monkeypatch.setattr(
        "app.routes.runs.repositories.record_run_control_operation",
        record_operation,
    )
    monkeypatch.setattr(f"app.routes.runs.repositories.{repository_method}", create_copied_run)
    monkeypatch.setattr("app.routes.runs.prepare_copied_run_for_queue", prepared_queue_payload)
    monkeypatch.setattr("app.routes.runs.enqueue_run", fail_enqueue)
    monkeypatch.setattr("app.routes.runs.repositories.mark_run_enqueue_failed", mark_enqueue_failed)

    with pytest.raises(HTTPException) as exc_info:
        await route(source_run_id, principal=_principal())

    assert exc_info.value.status_code == 503
    assert committed == (
        [
            [("run_created", "run-enqueue-failure")],
            [("run_failed", "run-enqueue-failure")],
        ]
        if repository_method == "copy_run_as_new_task"
        else [[("run_created", "run-enqueue-failure")]]
    )


@pytest.mark.asyncio
async def test_legacy_only_session_has_no_implicit_current_status(monkeypatch):
    async def get_session(_conn, **_kwargs):
        return {"id": "session-a"}

    async def list_runs(_conn, **_kwargs):
        return [{"id": "run-legacy", "status": "running", "session_generation": None}]

    monkeypatch.setattr("app.routes.lambchat_compat.transaction", _fake_transaction)
    monkeypatch.setattr("app.routes.lambchat_compat.repositories.get_authorized_lambchat_session", get_session)
    monkeypatch.setattr("app.routes.lambchat_compat.repositories.list_authorized_session_runs", list_runs)

    response = await lambchat_compat.chat_status("session-a", principal=_principal())

    assert response == {"session_id": "session-a", "run_id": None, "status": "idle", "raw_status": "idle"}


def test_public_run_context_projection_contains_only_allowlisted_window():
    projection = run_context_ref_from_snapshot_row(
        {
            "included_message_ids": ["msg-private"],
            "included_file_ids": ["file-private"],
            "included_artifact_ids": [],
            "included_memory_record_ids": [],
            "payload_json": {
                "context_snapshot_id": "ctx-private",
                "storage_key": "tenants/private/context.json",
                "context_manifest": {
                    "schema_version": "ai-platform.context-manifest.v1",
                    "selection": {
                        "status": "trimmed",
                        "history_candidate_count": 3,
                        "history_inline_count": 2,
                        "history_trimmed_count": 1,
                    },
                    "files": [
                        {"name": r"C:\uploads\approved-report.txt"},
                        {"name": "/private/object-store/报价😀.xlsx"},
                    ],
                },
            },
        }
    )

    assert projection == {
        "context_window": {
            "status": "trimmed",
            "selection_version": "session-context-v1",
            "history_candidate_count": 3,
            "history_inline_count": 2,
            "history_trimmed_count": 1,
            "legacy_history_excluded": False,
            "selected_file_names": ["approved-report.txt", "报价😀.xlsx"],
        }
    }
