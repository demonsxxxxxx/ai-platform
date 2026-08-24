from contextlib import asynccontextmanager

import pytest
from fastapi import HTTPException, Response
from fastapi.testclient import TestClient

from app import repositories as repository_module
from app.auth import AuthPrincipal
from app.main import create_app
from app.models import ChatSubmissionResponse
from app.repositories import RepositoryConflictError
from app.run_admission_policy import (
    contains_persisted_platform_multi_agent_control,
    contains_platform_multi_agent_control,
)
from app.routes.chat import get_chat_submission, retry_chat_submission_admission
from app.routes.lambchat_compat import CHAT_PUBLIC_RUN_EVENT_PROJECTIONS
from app.routes.runs import prepare_copied_run_for_queue
from app.settings import Settings


USER_HEADERS = {
    "x-ai-user-id": "user-a",
    "x-ai-user-name": "User A",
    "x-ai-tenant-id": "tenant-a",
}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(
        "app.auth.get_settings",
        lambda: Settings(frontend_poc_auth_enabled=True),
    )
    return TestClient(create_app())


@pytest.mark.parametrize(
    "payload",
    [
        {"execution_mode": "multi_agent"},
        {"executionMode": "multi-agent"},
        {"multi_agent_steps": []},
        {"multiAgentSteps": []},
        {"multi_agent_dispatch": {}},
        {"multiAgentDispatch": {}},
        {"execution_mode": "multi_agent", "executionMode": "single"},
    ],
)
def test_platform_multi_agent_input_policy_recognizes_retired_controls(payload):
    assert contains_platform_multi_agent_control(payload) is True


def test_platform_multi_agent_input_policy_does_not_reject_sdk_semantic_events():
    assert contains_platform_multi_agent_control(
        {"message": "coordinate this task", "event_type": "subagent_started"}
    ) is False


@pytest.mark.parametrize(
    "input_json",
    [
        {"execution_mode": "multi_agent"},
        {"input": {"execution_mode": "multi_agent"}},
        {"multi_agent_steps": [{"step_key": "review"}]},
        {"input": {"multi_agent_steps": [{"step_key": "review"}]}},
        {"multi_agent_dispatch": {"parent_run_id": "run-parent"}},
        {"input": {"multi_agent_dispatch": {"parent_run_id": "run-parent"}}},
    ],
    ids=[
        "root-execution-mode",
        "nested-execution-mode",
        "root-steps",
        "nested-steps",
        "root-dispatch",
        "nested-dispatch",
    ],
)
def test_persisted_input_policy_recognizes_root_and_nested_historical_controls(input_json):
    assert contains_persisted_platform_multi_agent_control(input_json) is True


@pytest.mark.parametrize(
    "input_payload",
    [
        {"execution_mode": "multi_agent"},
        {"multi_agent_steps": [{"step_key": "review"}]},
        {"multi_agent_dispatch": {"parent_run_id": "run-parent"}},
    ],
)
def test_runs_api_rejects_retired_platform_multi_agent_input_before_persistence(
    monkeypatch,
    client,
    input_payload,
):
    def fail_transaction():
        raise AssertionError("retired platform orchestration must fail before persistence")

    monkeypatch.setattr("app.routes.runs.transaction", fail_transaction)

    response = client.post(
        "/api/ai/runs",
        headers=USER_HEADERS,
        json={
            "workspace_id": "default",
            "agent_id": "general-agent",
            "capability_id": "general_chat",
            "input": input_payload,
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "platform_multi_agent_not_supported"


@pytest.mark.parametrize(
    "input_payload",
    [
        {"execution_mode": "multi_agent"},
        {"multi_agent_steps": [{"step_key": "review"}]},
        {"multi_agent_dispatch": {"parent_run_id": "run-parent"}},
    ],
)
def test_chat_api_rejects_retired_platform_multi_agent_input_before_persistence(
    monkeypatch,
    client,
    input_payload,
):
    def fail_transaction():
        raise AssertionError("retired platform orchestration must fail before persistence")

    monkeypatch.setattr("app.routes.chat.transaction", fail_transaction)

    response = client.post(
        "/api/ai/chat/stream",
        headers=USER_HEADERS,
        json={
            "workspace_id": "default",
            "message": "review this",
            "input": input_payload,
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "platform_multi_agent_not_supported"


@pytest.mark.asyncio
async def test_copied_run_rejects_historical_platform_multi_agent_input_before_reauthorization():
    with pytest.raises(RepositoryConflictError, match="platform_multi_agent_not_supported"):
        await prepare_copied_run_for_queue(
            object(),
            copied={"input": {"execution_mode": "multi_agent"}},
            principal=AuthPrincipal(
                user_id="user-a",
                display_name="User A",
                tenant_id="tenant-a",
            ),
            source="copy_run",
        )


@pytest.mark.parametrize("action", ["retry", "resume"])
@pytest.mark.parametrize(
    "persisted_input_json",
    [
        {"execution_mode": "multi_agent"},
        {"input": {"execution_mode": "multi_agent"}},
        {"multi_agent_steps": [{"step_key": "review"}]},
        {"input": {"multi_agent_steps": [{"step_key": "review"}]}},
        {"multi_agent_dispatch": {"parent_run_id": "run-parent"}},
        {"input": {"multi_agent_dispatch": {"parent_run_id": "run-parent"}}},
    ],
    ids=[
        "root-execution-mode",
        "nested-execution-mode",
        "root-steps",
        "nested-steps",
        "root-dispatch",
        "nested-dispatch",
    ],
)
def test_committed_run_control_recovery_terminalizes_retired_snapshot_without_enqueue(
    monkeypatch,
    client,
    action,
    persisted_input_json,
):
    operation_id = "7ea93033-30f5-40ea-8a33-2f3c6e7b21c4"
    terminalizations: list[dict[str, object]] = []

    @asynccontextmanager
    async def transaction():
        yield object()

    async def acquire_lock(_conn, **_kwargs):
        return None

    async def get_operation(_conn, **kwargs):
        assert kwargs["action"] == action
        return {
            "source_run_id": "run-parent",
            "action": action,
            "operation_id": operation_id,
            "run_id": "run-retired-child",
            "session_id": "session-parent",
            "status": "queued",
            "error_code": None,
            "workspace_id": "default",
            "user_id": "user-a",
            "agent_id": "general-agent",
            "skill_id": "general-chat",
            "input_json": persisted_input_json,
        }

    async def terminalize(_conn, **kwargs):
        terminalizations.append(kwargs)

    async def forbidden(*_args, **_kwargs):
        raise AssertionError("retired committed recovery must not enqueue, remove, or create facts")

    monkeypatch.setattr("app.routes.runs.transaction", transaction)
    monkeypatch.setattr(
        "app.routes.runs.repositories.acquire_run_control_operation_lock",
        acquire_lock,
    )
    monkeypatch.setattr("app.routes.runs.repositories.get_run_control_operation", get_operation)
    monkeypatch.setattr(
        "app.routes.runs.terminalize_retired_platform_multi_agent_run",
        terminalize,
    )
    monkeypatch.setattr("app.routes.runs.repositories.enforce_user_active_run_admission", forbidden)
    monkeypatch.setattr(f"app.routes.runs.repositories.{action}_run_as_new_task", forbidden)
    monkeypatch.setattr("app.routes.runs.repositories.record_run_control_operation", forbidden)
    monkeypatch.setattr("app.routes.runs.reauthorize_pinned_run_for_replay", forbidden)
    monkeypatch.setattr("app.routes.runs.read_queue_admission", forbidden)
    monkeypatch.setattr("app.routes.runs.enqueue_run", forbidden)
    monkeypatch.setattr("app.routes.runs.remove_queued_run", forbidden)

    response = client.post(
        f"/api/ai/runs/run-parent/{action}?operation_id={operation_id}",
        headers=USER_HEADERS,
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "platform_multi_agent_not_supported"
    assert terminalizations == [
        {
            "tenant_id": "tenant-a",
            "run_id": "run-retired-child",
        }
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "persisted_input_json",
    [
        {"execution_mode": "multi_agent"},
        {"input": {"execution_mode": "multi_agent"}},
        {"multi_agent_steps": [{"step_key": "review"}]},
        {"input": {"multi_agent_steps": [{"step_key": "review"}]}},
        {"multi_agent_dispatch": {"parent_run_id": "run-parent"}},
        {"input": {"multi_agent_dispatch": {"parent_run_id": "run-parent"}}},
    ],
    ids=[
        "root-execution-mode",
        "nested-execution-mode",
        "root-steps",
        "nested-steps",
        "root-dispatch",
        "nested-dispatch",
    ],
)
async def test_chat_retry_terminalizes_retired_persisted_snapshot_before_enqueue(
    monkeypatch,
    persisted_input_json,
):
    submission_id = "7ea93033-30f5-40ea-8a33-2f3c6e7b21c4"
    submission = {
        "submission_id": submission_id,
        "state": "accepted_pending_enqueue",
        "run_id": "run-durable",
        "outcome_json": {},
    }
    run = {
        "id": "run-durable",
        "status": "queued",
        "workspace_id": "default",
        "session_id": "session-durable",
        "agent_id": "general-agent",
        "skill_id": "general-chat",
        "trace_id": "trace-retired-chat",
        "input_json": persisted_input_json,
    }
    calls: list[tuple[str, dict[str, object]]] = []
    committed_transactions = 0

    @asynccontextmanager
    async def transaction():
        nonlocal committed_transactions
        yield object()
        committed_transactions += 1

    async def get_submission(*_args, **_kwargs):
        return submission

    async def get_run(*_args, **_kwargs):
        return run

    async def terminalize(_conn, **kwargs):
        calls.append(("run", kwargs))

    async def finalize(_conn, **kwargs):
        calls.append(("submission", kwargs))

    async def recover_submission(*_args, **_kwargs):
        return ChatSubmissionResponse(submission_id=submission_id, state="accepted_pending_enqueue")

    async def forbidden(*_args, **_kwargs):
        raise AssertionError("retired persisted input must not reach queue admission")

    monkeypatch.setattr("app.routes.chat.transaction", transaction)
    monkeypatch.setattr(repository_module, "get_chat_submission", get_submission)
    monkeypatch.setattr(repository_module, "get_authorized_run", get_run)
    monkeypatch.setattr("app.routes.chat.terminalize_retired_platform_multi_agent_run", terminalize)
    monkeypatch.setattr(repository_module, "finalize_chat_submission", finalize)
    monkeypatch.setattr("app.routes.chat._recover_preledger_chat_submission", recover_submission)
    monkeypatch.setattr("app.routes.chat._attempt_chat_queue_admission", forbidden)

    response_headers = Response()
    with pytest.raises(HTTPException) as exc_info:
        await retry_chat_submission_admission(
            submission_id,
            response=response_headers,
            principal=AuthPrincipal(user_id="user-a", display_name="User A", tenant_id="tenant-a"),
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "platform_multi_agent_not_supported"
    assert exc_info.value.headers == {"Cache-Control": "private, no-store"}
    assert committed_transactions == 1
    assert [kind for kind, _kwargs in calls] == ["run", "submission"]
    assert calls[1][1]["state"] == "admission_rejected"
    assert calls[1][1]["rejection_code"] == "platform_multi_agent_not_supported"


@pytest.mark.asyncio
async def test_chat_submission_resolver_stably_returns_retired_admission_code(monkeypatch):
    submission_id = "7ea93033-30f5-40ea-8a33-2f3c6e7b21c4"

    @asynccontextmanager
    async def transaction():
        yield object()

    async def get_submission(*_args, **_kwargs):
        return {
            "submission_id": submission_id,
            "state": "admission_rejected",
            "rejection_code": "platform_multi_agent_not_supported",
            "outcome_json": {},
        }

    monkeypatch.setattr("app.routes.chat.transaction", transaction)
    monkeypatch.setattr(repository_module, "get_chat_submission", get_submission)

    response = Response()
    with pytest.raises(HTTPException) as exc_info:
        await get_chat_submission(
            submission_id,
            response=response,
            principal=AuthPrincipal(user_id="user-a", display_name="User A", tenant_id="tenant-a"),
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "platform_multi_agent_not_supported"
    assert response.headers["Cache-Control"] == "private, no-store"


@pytest.mark.parametrize(
    "path",
    [
        "/api/ai/runs/run-parent/multi-agent/dispatch/claims",
        "/api/ai/runs/run-parent/multi-agent/dispatch/claims/dispatch-a/handoff",
        "/api/ai/runs/run-parent/multi-agent/dispatch/tick",
    ],
)
def test_platform_multi_agent_control_routes_are_not_mounted(client, path):
    response = client.post(path, headers=USER_HEADERS)

    assert response.status_code == 404


def test_lambchat_keeps_sdk_subagent_events_without_legacy_platform_child_event():
    assert "run_multi_agent_child_created" not in CHAT_PUBLIC_RUN_EVENT_PROJECTIONS
    assert "subagent_started" in CHAT_PUBLIC_RUN_EVENT_PROJECTIONS
    assert "run_child_created" in CHAT_PUBLIC_RUN_EVENT_PROJECTIONS
