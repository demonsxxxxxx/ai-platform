from contextlib import asynccontextmanager

import pytest
from fastapi import HTTPException

from app.auth import AuthPrincipal
from app.models import MultiAgentDispatchClaimRequest, QueueRunPayload
from app.routes import lambchat_compat
from app.routes.runs import (
    _compensate_enqueue_failure,
    claim_multi_agent_dispatch,
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
async def test_multi_agent_dispatch_is_rejected_before_transaction_or_candidate_claim(monkeypatch):
    async def forbidden_transaction():
        raise AssertionError("deferred dispatch must not open a candidate transaction")
        yield object()

    monkeypatch.setattr("app.routes.runs.transaction", forbidden_transaction)

    with pytest.raises(HTTPException) as exc_info:
        await claim_multi_agent_dispatch(
            "run-a",
            MultiAgentDispatchClaimRequest(step_key="step-a"),
            principal=_principal(),
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "multi_agent_dispatch_not_available"


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
    """Copy, retry, and resume share a committed post-enqueue failure transition."""

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
    monkeypatch.setattr(f"app.routes.runs.repositories.{repository_method}", create_copied_run)
    monkeypatch.setattr("app.routes.runs.prepare_copied_run_for_queue", prepared_queue_payload)
    monkeypatch.setattr("app.routes.runs.enqueue_run", fail_enqueue)
    monkeypatch.setattr("app.routes.runs.repositories.mark_run_enqueue_failed", mark_enqueue_failed)

    with pytest.raises(HTTPException) as exc_info:
        await route(source_run_id, principal=_principal())

    assert exc_info.value.status_code == 503
    assert committed == [
        [("run_created", "run-enqueue-failure")],
        [("run_failed", "run-enqueue-failure")],
    ]


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
