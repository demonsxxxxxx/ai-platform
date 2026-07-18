from contextlib import asynccontextmanager

import pytest
from fastapi import HTTPException

from app.auth import AuthPrincipal
from app.models import MultiAgentDispatchClaimRequest, QueueRunPayload
from app.routes import lambchat_compat
from app.routes.runs import _compensate_enqueue_failure, claim_multi_agent_dispatch, run_context_ref_from_snapshot_row
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
                    "files": [{"name": r"C:\uploads\approved-report.txt"}],
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
            "selected_file_names": ["approved-report.txt"],
        }
    }
