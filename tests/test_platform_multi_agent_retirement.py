import pytest
from fastapi.testclient import TestClient

from app.auth import AuthPrincipal
from app.main import create_app
from app.repositories import RepositoryConflictError
from app.run_admission_policy import contains_platform_multi_agent_control
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


@pytest.mark.parametrize(
    "path",
    [
        "/api/ai/runs/run-parent/multi-agent/dispatch/claims",
        "/api/ai/runs/run-parent/multi-agent/dispatch/claims/dispatch-a/handoff",
        "/api/ai/runs/run-parent/multi-agent/dispatch/tick",
        "/api/ai/admin/runtime/multi-agent/dispatch/cleanup",
    ],
)
def test_platform_multi_agent_control_routes_are_not_mounted(client, path):
    response = client.post(path, headers=USER_HEADERS)

    assert response.status_code == 404


def test_lambchat_keeps_sdk_subagent_events_without_legacy_platform_child_event():
    assert "run_multi_agent_child_created" not in CHAT_PUBLIC_RUN_EVENT_PROJECTIONS
    assert "subagent_started" in CHAT_PUBLIC_RUN_EVENT_PROJECTIONS
    assert "run_child_created" in CHAT_PUBLIC_RUN_EVENT_PROJECTIONS
