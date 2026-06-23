from contextlib import asynccontextmanager

from fastapi.testclient import TestClient

from app.main import create_app
from app.settings import Settings


def user_headers(permissions: str = "user:read,settings:read,feedback:read,notification:read") -> dict[str, str]:
    return {
        "X-AI-User-ID": "ordinary",
        "X-AI-User-Name": "Ordinary",
        "X-AI-Roles": "user",
        "X-AI-Tenant-ID": "default",
        "X-AI-Department-ID": "qa",
        "X-AI-Permissions": permissions,
    }


def admin_headers(
    permissions: str = (
        "user:read,user:admin,settings:read,settings:admin,"
        "feedback:read,feedback:admin,notification:read,notification:admin"
    ),
) -> dict[str, str]:
    return {
        "X-AI-User-ID": "workbench-admin",
        "X-AI-Roles": "admin",
        "X-AI-Tenant-ID": "default",
        "X-AI-Department-ID": "platform",
        "X-AI-Permissions": permissions,
    }


def assert_no_sensitive_material(value: object) -> None:
    serialized = str(value).lower()
    assert "sk-live-secret-value" not in serialized
    assert "gateway-token-secret" not in serialized
    assert "password" not in serialized
    assert "raw_payload" not in serialized
    assert "private_payload" not in serialized
    assert "token_secret" not in serialized


def install_workbench_route_fakes(monkeypatch) -> list[tuple[str, dict[str, object]]]:
    from app.routes import workbench_projections

    class FakeConnection:
        pass

    @asynccontextmanager
    async def fake_transaction():
        yield FakeConnection()

    calls: list[tuple[str, dict[str, object]]] = []

    async def fake_audit(conn, **kwargs):
        calls.append(("audit", kwargs))
        return "audit-workbench-contract"

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr(workbench_projections, "transaction", fake_transaction)
    monkeypatch.setattr(workbench_projections.repositories, "append_audit_log", fake_audit)
    return calls


def test_users_projection_returns_safe_directory_for_ordinary_user(monkeypatch):
    install_workbench_route_fakes(monkeypatch)
    client = TestClient(create_app())

    response = client.get("/api/users/?limit=10&search=Ord", headers=user_headers())

    assert response.status_code == 200
    body = response.json()
    assert body["total"] >= 1
    assert body["governance"]["projection"] == "safe_user_directory"
    assert body["users"][0] == {
        "id": "ordinary",
        "username": "ordinary",
        "email": None,
        "full_name": "Ordinary",
        "is_active": True,
        "is_superuser": False,
        "roles": ["user"],
        "permissions": ["feedback:read", "notification:read", "settings:read", "user:read"],
        "tenant_id": "default",
        "department_id": "qa",
        "created_at": None,
        "updated_at": None,
    }
    assert_no_sensitive_material(body)


def test_users_admin_writes_are_permission_gated_and_audited(monkeypatch):
    calls = install_workbench_route_fakes(monkeypatch)
    client = TestClient(create_app())
    operations = [
        ("post", "/api/users/", {"username": "new-user", "roles": ["user"]}, "create"),
        ("put", "/api/users/ordinary", {"full_name": "Updated", "is_active": True}, "update"),
        ("delete", "/api/users/ordinary", {}, "delete"),
    ]

    for method, path, payload, operation in operations:
        denied = client.request(method.upper(), path, json=payload, headers=user_headers("user:read"))
        assert denied.status_code == 403
        assert denied.json()["detail"] == "missing_permission:user:admin"

        response = client.request(method.upper(), path, json=payload, headers=admin_headers())
        assert response.status_code == 200
        body = response.json()
        assert body["operation"] == operation
        assert body["status"] == "queued"
        assert body["audit_id"] == "audit-workbench-contract"
        assert_no_sensitive_material(body)

    audit_payloads = [payload for name, payload in calls if name == "audit"]
    assert [payload["payload_json"]["operation"] for payload in audit_payloads] == ["create", "update", "delete"]
    assert all(payload["payload_json"]["secret_material_projected"] is False for payload in audit_payloads)
    assert_no_sensitive_material(audit_payloads)

    secret_response = client.post(
        "/api/users/",
        json={"username": "leaky-user", "password": "sk-live-secret-value"},
        headers=admin_headers(),
    )
    assert secret_response.status_code == 422
    assert_no_sensitive_material(secret_response.json())


def test_settings_projection_splits_personal_and_system_state(monkeypatch):
    install_workbench_route_fakes(monkeypatch)
    client = TestClient(create_app())

    response = client.get("/api/settings/", headers=user_headers())

    assert response.status_code == 200
    body = response.json()
    assert set(body["settings"]) == {"personal_preferences", "system_runtime"}
    assert body["settings"]["personal_preferences"]["items"][0]["key"] == "ui.locale"
    assert body["settings"]["system_runtime"]["items"][0]["value"] == "[redacted]"
    assert body["settings"]["system_runtime"]["items"][0]["audit_required"] is True
    assert body["governance"]["rollback_available"] is True
    assert_no_sensitive_material(body)


def test_settings_admin_writes_fail_closed_and_do_not_echo_secret_values(monkeypatch):
    calls = install_workbench_route_fakes(monkeypatch)
    client = TestClient(create_app())

    denied = client.put(
        "/api/settings/gateway.api_key",
        json={"value": "sk-live-secret-value"},
        headers=user_headers("settings:read"),
    )
    assert denied.status_code == 403
    assert denied.json()["detail"] == "missing_permission:settings:admin"

    response = client.put(
        "/api/settings/gateway.api_key",
        json={"value": "sk-live-secret-value", "rollback_id": "rb-1"},
        headers=admin_headers(),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["key"] == "gateway.api_key"
    assert body["value"] == "[redacted]"
    assert body["audit"]["audit_id"] == "audit-workbench-contract"
    assert_no_sensitive_material(body)
    assert_no_sensitive_material(calls)

    reset = client.post("/api/settings/reset/gateway.api_key", headers=admin_headers())
    assert reset.status_code == 200
    assert reset.json()["status"] == "queued"

    nested_secret = client.put(
        "/api/settings/ui.locale",
        json={"value": {"token_secret": "gateway-token-secret", "locale": "zh-CN"}},
        headers=admin_headers(),
    )
    assert nested_secret.status_code == 200
    assert nested_secret.json()["key"] == "ui.locale"
    assert nested_secret.json()["value"] == "[redacted]"
    assert_no_sensitive_material(nested_secret.json())
    assert_no_sensitive_material(calls)


def test_feedback_projection_and_admin_workflow_are_safe(monkeypatch):
    calls = install_workbench_route_fakes(monkeypatch)
    client = TestClient(create_app())

    listing = client.get("/api/feedback/?limit=5", headers=user_headers())
    assert listing.status_code == 200
    body = listing.json()
    assert body["total"] >= 1
    assert body["items"][0]["assignment_state"] == "unassigned"
    assert body["items"][0]["audit_history"] == []
    assert body["stats"]["total_count"] >= 1
    assert_no_sensitive_material(body)

    denied = client.put(
        "/api/feedback/fb-1",
        json={"assignment_state": "closed", "private_payload": "sk-live-secret-value"},
        headers=user_headers("feedback:read"),
    )
    assert denied.status_code == 403
    assert denied.json()["detail"] == "missing_permission:feedback:admin"

    updated = client.put(
        "/api/feedback/fb-1",
        json={"assignee_id": "reviewer-a", "status": "closed", "labels": ["bug", "resolved"]},
        headers=admin_headers(),
    )
    assert updated.status_code == 200
    assert updated.json()["status"] == "queued"
    assert updated.json()["audit_id"] == "audit-workbench-contract"
    assert_no_sensitive_material(updated.json())
    assert_no_sensitive_material(calls)


def test_notifications_public_and_admin_projection(monkeypatch):
    calls = install_workbench_route_fakes(monkeypatch)
    client = TestClient(create_app())

    public_response = client.get("/api/notifications/active", headers=user_headers())
    assert public_response.status_code == 200
    public_body = public_response.json()
    assert public_body[0]["id"] == "platform-announcement"
    assert public_body[0]["read_state"] == "unread"
    assert "audience" not in public_body[0]
    assert_no_sensitive_material(public_body)

    admin_list = client.get("/api/notifications/admin?limit=10", headers=admin_headers())
    assert admin_list.status_code == 200
    admin_body = admin_list.json()
    assert admin_body["items"][0]["audience"] == {"tenant_id": "default", "departments": []}
    assert admin_body["items"][0]["audit_history"] == []

    denied = client.post(
        "/api/notifications/",
        json={"title_i18n": {"en": "Secret"}, "token_secret": "gateway-token-secret"},
        headers=user_headers("notification:read"),
    )
    assert denied.status_code == 403
    assert denied.json()["detail"] == "missing_permission:notification:admin"

    created = client.post(
        "/api/notifications/",
        json={
            "title_i18n": {"en": "Maintenance"},
            "content_i18n": {"en": "Window"},
            "audience": {"tenant_id": "default", "departments": ["qa"], "token_secret": "gateway-token-secret"},
            "expires_at": "2026-06-30T00:00:00Z",
            "replay": True,
        },
        headers=admin_headers(),
    )
    assert created.status_code == 200
    assert created.json()["operation"] == "create"
    assert created.json()["audit_id"] == "audit-workbench-contract"
    assert_no_sensitive_material(created.json())

    replay = client.post("/api/notifications/platform-announcement/replay", headers=admin_headers())
    assert replay.status_code == 200
    assert replay.json()["operation"] == "replay"
    assert_no_sensitive_material(calls)
