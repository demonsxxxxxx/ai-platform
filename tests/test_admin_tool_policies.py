from contextlib import asynccontextmanager

from fastapi.testclient import TestClient

from app.main import create_app
from app.repositories import RepositoryNotFoundError
from app.settings import Settings


@asynccontextmanager
async def fake_transaction():
    yield object()


def admin_headers():
    return {
        "X-AI-User-ID": "tool-admin",
        "X-AI-Roles": "admin",
        "X-AI-Tenant-ID": "tenant-a",
    }


def user_headers():
    return {
        "X-AI-User-ID": "ordinary",
        "X-AI-Roles": "user",
        "X-AI-Tenant-ID": "tenant-a",
    }


def tool_policy_row(**overrides):
    values = {
        "tenant_id": "tenant-a",
        "tool_id": "ragflow-knowledge-search",
        "server_id": "ragflow",
        "name": "RAGFlow search",
        "description": "Read-only knowledge search",
        "registry_status": "active",
        "policy_status": "active",
        "effective_status": "active",
        "registry_write_capable": False,
        "policy_write_capable": False,
        "write_capable": False,
        "registry_risk_level": "low",
        "policy_risk_level": "low",
        "risk_level": "low",
        "registry_visible_to_user": True,
        "policy_visible_to_user": True,
        "visible_to_user": True,
        "source": "tenant",
        "reason": "safe operational note client_secret=redacted",
        "updated_by": "tool-admin",
        "updated_at": "2026-06-05T00:00:00Z",
        "endpoint": "https://internal.example/token=secret",
        "auth_mode": "api-key",
        "request_payload_json": {"command": "rm -rf /"},
    }
    values.update(overrides)
    return values


def test_admin_list_tool_policies_requires_admin(monkeypatch):
    async def fail_list_tool_policies(conn, **kwargs):
        raise AssertionError("ordinary users must not reach tool policy inventory")

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.admin_tool_policies.repositories.list_admin_tool_policies", fail_list_tool_policies, raising=False)
    client = TestClient(create_app())

    response = client.get("/api/ai/admin/tool-policies", headers=user_headers())

    assert response.status_code == 403
    assert response.json()["detail"] == "not_ai_admin"


def test_admin_list_tool_policies_returns_same_tenant_operational_projection(monkeypatch):
    calls = []

    async def fake_list_tool_policies(conn, *, tenant_id, include_disabled, limit):
        calls.append(("list", tenant_id, include_disabled, limit))
        return [tool_policy_row()]

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.admin_tool_policies.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.admin_tool_policies.repositories.list_admin_tool_policies", fake_list_tool_policies, raising=False)
    client = TestClient(create_app())

    response = client.get("/api/ai/admin/tool-policies?include_disabled=true&limit=25", headers=admin_headers())

    assert response.status_code == 200
    body = response.json()
    assert body["contract_version"] == "ai-platform.admin-tool-policies.v1"
    assert body["tenant_id"] == "tenant-a"
    assert body["summary"] == {"returned_count": 1, "limit": 25, "include_disabled": True}
    assert body["tool_policies"][0] == {
        "tool_id": "ragflow-knowledge-search",
        "server_id": "ragflow",
        "name": "RAGFlow search",
        "description": "Read-only knowledge search",
        "registry_status": "active",
        "policy_status": "active",
        "effective_status": "active",
        "write_capable": False,
        "risk_level": "low",
        "visible_to_user": True,
        "source": "tenant",
        "requires_decision": False,
        "reason": "safe operational note client_secret=[redacted-secret]",
        "updated_by": "tool-admin",
        "updated_at": "2026-06-05T00:00:00Z",
    }
    assert calls == [("list", "tenant-a", True, 25)]
    assert "endpoint" not in response.text
    assert "auth_mode" not in response.text
    assert "request_payload_json" not in response.text
    assert "token=secret" not in response.text
    assert "rm -rf" not in response.text


def test_admin_update_tool_policy_audits_and_keeps_risky_tools_fail_closed(monkeypatch):
    calls = []

    async def fake_ensure_user(conn, *, tenant_id, user_id, display_name=None):
        calls.append(("ensure_user", tenant_id, user_id, display_name))

    async def fake_update_tool_policy(conn, **kwargs):
        calls.append(("update", kwargs))
        return tool_policy_row(
            policy_status=kwargs["status"],
            effective_status=kwargs["status"],
            policy_write_capable=kwargs["write_capable"],
            write_capable=True,
            policy_risk_level=kwargs["risk_level"],
            risk_level="high",
            policy_visible_to_user=kwargs["visible_to_user"],
            visible_to_user=kwargs["visible_to_user"],
            reason=kwargs["reason"],
            updated_by=kwargs["updated_by"],
        )

    async def fake_append_audit_log(conn, **kwargs):
        calls.append(("audit", kwargs))
        return "aud-tool-policy"

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.admin_tool_policies.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.admin_tool_policies.repositories.ensure_user", fake_ensure_user)
    monkeypatch.setattr("app.routes.admin_tool_policies.repositories.upsert_admin_tool_policy", fake_update_tool_policy, raising=False)
    monkeypatch.setattr("app.routes.admin_tool_policies.repositories.append_audit_log", fake_append_audit_log)
    client = TestClient(create_app())

    response = client.put(
        "/api/ai/admin/tool-policies/ragflow-knowledge-search",
        headers=admin_headers(),
        json={
            "status": "active",
            "risk_level": "high",
            "write_capable": True,
            "visible_to_user": True,
            "reason": "enable for controlled run token=tool-secret",
        },
    )

    assert response.status_code == 200
    policy = response.json()["tool_policy"]
    assert policy["tool_id"] == "ragflow-knowledge-search"
    assert policy["risk_level"] == "high"
    assert policy["write_capable"] is True
    assert policy["requires_decision"] is True
    assert policy["reason"] == "enable for controlled run token=[redacted-secret]"
    assert calls[0] == ("ensure_user", "tenant-a", "tool-admin", "tool-admin")
    assert calls[1][0] == "update"
    assert calls[1][1]["tenant_id"] == "tenant-a"
    assert calls[1][1]["updated_by"] == "tool-admin"
    assert calls[2][0] == "audit"
    assert calls[2][1]["action"] == "admin.tool_policy.updated"
    assert calls[2][1]["payload_json"] == {
        "tool_id": "ragflow-knowledge-search",
        "status": "active",
        "risk_level": "high",
        "write_capable": True,
        "visible_to_user": True,
        "reason": "enable for controlled run token=[redacted-secret]",
    }
    assert "tool-secret" not in response.text


def test_admin_update_tool_policy_returns_404_for_missing_tool(monkeypatch):
    async def fake_ensure_user(conn, **kwargs):
        return None

    async def fake_update_tool_policy(conn, **kwargs):
        raise RepositoryNotFoundError("mcp_tool_not_found")

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.admin_tool_policies.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.admin_tool_policies.repositories.ensure_user", fake_ensure_user)
    monkeypatch.setattr("app.routes.admin_tool_policies.repositories.upsert_admin_tool_policy", fake_update_tool_policy, raising=False)
    client = TestClient(create_app())

    response = client.put(
        "/api/ai/admin/tool-policies/missing-tool",
        headers=admin_headers(),
        json={"status": "disabled", "reason": "remove"},
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "mcp_tool_not_found"
