from contextlib import asynccontextmanager

from fastapi.testclient import TestClient

from app.main import create_app
from app.settings import Settings
from app.tool_permission_projection import inbox_allowed_decisions


@asynccontextmanager
async def fake_transaction():
    yield object()


def headers(*, user_id="user-a", roles="user", tenant_id="tenant-a", permissions=""):
    values = {
        "X-AI-User-ID": user_id,
        "X-AI-User-Name": user_id,
        "X-AI-Roles": roles,
        "X-AI-Tenant-ID": tenant_id,
    }
    if permissions:
        values["X-AI-Permissions"] = permissions
    return values


def historical_row(**overrides):
    row = {
        "id": "tpr-historical",
        "tenant_id": "tenant-a",
        "workspace_id": "workspace-a",
        "user_id": "user-a",
        "session_id": "session-a",
        "run_id": "run-a",
        "trace_id": "trace-a",
        "tool_id": "tool-a",
        "tool_call_id": "call-a",
        "risk_level": "high",
        "write_capable": True,
        "status": "expired",
        "decision": "deny",
        "reason": "historical",
    }
    row.update(overrides)
    return row


def test_all_runtime_approval_writes_return_410_without_repository_mutation(monkeypatch):
    async def fail_create(*args, **kwargs):
        raise AssertionError("retired route must not create a request")

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.tool_permissions.repositories.create_tool_permission_request", fail_create)
    client = TestClient(create_app())
    ordinary = headers()
    admin = headers(roles="admin", permissions="settings:manage")

    responses = [
        client.post("/api/ai/runs/run-a/tool-permissions/request", headers=ordinary, json={"tool_id": "tool-a"}),
        client.post("/api/ai/runs/run-a/tool-permissions/tpr-a/decision", headers=admin, json={"decision": "deny"}),
        client.post("/api/ai/tool-permissions/inbox/tpr-a/decision", headers=admin, json={"decision": "deny"}),
    ]

    assert [response.status_code for response in responses] == [410, 410, 410]
    assert {response.json()["detail"] for response in responses} == {"tool_permission_runtime_approval_removed"}


def test_historical_reads_remain_authorized_redacted_and_have_no_decision_controls(monkeypatch):
    async def list_history(conn, *, tenant_id, status, limit):
        assert (tenant_id, status, limit) == ("tenant-a", "all", 50)
        return [historical_row()]

    async def get_history(conn, *, tenant_id, user_id, run_id, request_id):
        assert (tenant_id, user_id, run_id, request_id) == ("tenant-a", "user-a", "run-a", "tpr-historical")
        return historical_row()

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.tool_permissions.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.tool_permissions.repositories.list_tool_permission_inbox_for_tenant", list_history)
    monkeypatch.setattr("app.routes.tool_permissions.repositories.get_tool_permission_request", get_history)
    client = TestClient(create_app())

    inbox = client.get("/api/ai/tool-permissions/inbox", headers=headers(roles="admin", permissions="settings:manage"))
    owned = client.get("/api/ai/runs/run-a/tool-permissions/tpr-historical", headers=headers())

    assert inbox.status_code == owned.status_code == 200
    assert inbox.json()["permission_requests"][0]["allowed_decisions"] == []
    assert owned.json()["permission_request"]["decision"] == "deny"
    assert inbox_allowed_decisions(historical_row(status="pending")) == []
