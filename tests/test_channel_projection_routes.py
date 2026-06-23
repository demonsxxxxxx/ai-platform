from contextlib import asynccontextmanager

from fastapi.testclient import TestClient

from app.main import create_app
from app.settings import Settings


def user_headers(permissions: str = "channel:read") -> dict[str, str]:
    return {
        "X-AI-User-ID": "ordinary",
        "X-AI-Roles": "user",
        "X-AI-Tenant-ID": "default",
        "X-AI-Department-ID": "qa",
        "X-AI-Permissions": permissions,
    }


def assert_no_secret_material(value: object) -> None:
    serialized = str(value).lower()
    assert "https://hooks.example/secret" not in serialized
    assert "vault://channels/default-chat" not in serialized
    assert "sk-live-secret-value" not in serialized
    assert "credential_ref" not in serialized
    assert "webhook_url" not in serialized


def admin_headers(permissions: str = "channel:read,channel:admin") -> dict[str, str]:
    return {
        "X-AI-User-ID": "channel-admin",
        "X-AI-Roles": "admin",
        "X-AI-Tenant-ID": "default",
        "X-AI-Department-ID": "platform",
        "X-AI-Permissions": permissions,
    }


def install_channel_route_fakes(monkeypatch) -> list[tuple[str, dict[str, object]]]:
    from app.routes import channels

    class FakeConnection:
        pass

    @asynccontextmanager
    async def fake_transaction():
        yield FakeConnection()

    calls: list[tuple[str, dict[str, object]]] = []

    async def fake_audit(conn, **kwargs):
        calls.append(("audit", kwargs))
        return "audit-channel-contract"

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr(channels, "transaction", fake_transaction)
    monkeypatch.setattr(channels.repositories, "append_audit_log", fake_audit)
    return calls


def test_public_channel_catalog_projects_safe_tenant_scoped_items(monkeypatch):
    install_channel_route_fakes(monkeypatch)
    client = TestClient(create_app())

    response = client.get("/api/channels/catalog", headers=user_headers())

    assert response.status_code == 200
    body = response.json()
    assert body["total"] >= 1
    assert body["tenant_id"] == "default"
    assert body["workspace_id"] == "default"
    assert body["channels"][0] == {
        "channel_id": "default-chat",
        "workspace_id": "default",
        "display_name": "Default Chat",
        "channel_type": "chat",
        "enabled": True,
        "capabilities": ["chat:read", "chat:write", "file:upload"],
        "connection_state": "not_configured",
        "redaction_policy": "secrets_never_projected",
        "retention_policy": "tenant_default",
        "last_actor": None,
        "created_at": None,
        "updated_at": None,
    }
    serialized = str(body).lower()
    assert "webhook" not in serialized
    assert "credential" not in serialized
    assert "secret" not in serialized.replace("secrets_never_projected", "")
    assert "token" not in serialized
    assert "api_key" not in serialized


def test_channel_catalog_requires_public_permission(monkeypatch):
    install_channel_route_fakes(monkeypatch)
    client = TestClient(create_app())

    response = client.get("/api/channels/catalog", headers=user_headers(""))

    assert response.status_code == 403
    assert response.json()["detail"] == "missing_permission:channel:read"


def test_channel_admin_lifecycle_fails_closed_for_ordinary_user(monkeypatch):
    install_channel_route_fakes(monkeypatch)
    client = TestClient(create_app())

    response = client.post(
        "/api/admin/channels/default-chat/test",
        json={"dry_run": True},
        headers=user_headers("channel:read"),
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "missing_permission:channel:admin"


def test_channel_catalog_projects_requested_workspace_scope(monkeypatch):
    install_channel_route_fakes(monkeypatch)
    client = TestClient(create_app())

    response = client.get("/api/channels/catalog?workspace_id=workspace-a", headers=user_headers())

    assert response.status_code == 200
    body = response.json()
    assert body["tenant_id"] == "default"
    assert body["workspace_id"] == "workspace-a"
    assert {channel["workspace_id"] for channel in body["channels"]} == {"workspace-a"}


def test_channel_admin_test_records_audited_masked_projection(monkeypatch):
    calls = install_channel_route_fakes(monkeypatch)
    client = TestClient(create_app())

    response = client.post(
        "/api/admin/channels/default-chat/test",
        json={"dry_run": True},
        headers=admin_headers(),
    )

    assert response.status_code == 200
    assert response.json() == {
        "channel_id": "default-chat",
        "workspace_id": "default",
        "operation": "test",
        "status": "queued",
        "audit_id": "audit-channel-contract",
        "message": "Channel test accepted for audited execution",
    }
    audit_payload = [payload for name, payload in calls if name == "audit"][0]
    assert audit_payload["tenant_id"] == "default"
    assert audit_payload["user_id"] == "channel-admin"
    assert audit_payload["action"] == "channel.admin.test_requested"
    assert audit_payload["target_type"] == "channel"
    assert audit_payload["target_id"] == "default-chat"
    assert audit_payload["payload_json"] == {
        "operation": "test",
        "dry_run": True,
        "workspace_id": "default",
        "department_id": "platform",
        "secret_material_projected": False,
    }


def test_channel_admin_lifecycle_operations_are_permission_gated_and_redacted(monkeypatch):
    calls = install_channel_route_fakes(monkeypatch)
    client = TestClient(create_app())
    operations = [
        ("post", "/api/admin/channels?workspace_id=workspace-a", {"channel_id": "new-chat", "enabled": True}, "create"),
        ("post", "/api/admin/channels/default-chat/enable", {}, "enable"),
        ("post", "/api/admin/channels/default-chat/disable", {}, "disable"),
        (
            "put",
            "/api/admin/channels/default-chat/credentials",
            {"credential_ref": "vault://channels/default-chat", "webhook_url": "https://hooks.example/secret"},
            "update_credentials",
        ),
        ("put", "/api/admin/channels/default-chat/retention", {"retention_policy": "30d"}, "update_retention"),
    ]

    for method, path, payload, operation in operations:
        denied = getattr(client, method)(path, json=payload, headers=user_headers("channel:read"))
        assert denied.status_code == 403
        assert denied.json()["detail"] == "missing_permission:channel:admin"

        response = getattr(client, method)(path, json=payload, headers=admin_headers())
        assert response.status_code == 200
        body = response.json()
        assert body["channel_id"] == payload.get("channel_id", "default-chat")
        assert body["operation"] == operation
        assert body["status"] == "queued"
        assert body["audit_id"] == "audit-channel-contract"

    audit_payloads = [payload for name, payload in calls if name == "audit"]
    assert [payload["payload_json"]["operation"] for payload in audit_payloads[-5:]] == [
        "create",
        "enable",
        "disable",
        "update_credentials",
        "update_retention",
    ]
    assert audit_payloads[-5]["payload_json"]["workspace_id"] == "workspace-a"
    assert audit_payloads[-5]["target_id"] == "new-chat"
    assert_no_secret_material(audit_payloads)
    assert all(payload["payload_json"]["secret_material_projected"] is False for payload in audit_payloads[-5:])


def test_channel_admin_credentials_rejects_unknown_secret_fields_without_echoing_values(monkeypatch):
    calls = install_channel_route_fakes(monkeypatch)
    client = TestClient(create_app())

    response = client.put(
        "/api/admin/channels/default-chat/credentials",
        json={
            "credential_ref": "vault://channels/default-chat",
            "api_key": "sk-live-secret-value",
        },
        headers=admin_headers(),
    )

    assert response.status_code == 422
    body = response.json()
    assert body["detail"][0]["type"] == "extra_forbidden"
    assert_no_secret_material(body)
    assert calls == []
