import app.identity.infrastructure.audit_postgres as _owner_identity_infrastructure_audit_postgres
from contextlib import asynccontextmanager

import pytest

from fastapi.testclient import TestClient

from app.main import create_app
from app.settings import Settings


def user_headers(permissions: str = "user:read,feedback:read,notification:read") -> dict[str, str]:
    return {
        "X-AI-User-ID": "ordinary",
        "X-AI-User-Name": "Ordinary",
        "X-AI-Roles": "user",
        "X-AI-Tenant-ID": "default",
        "X-AI-Department-ID": "110",
        "X-AI-Permissions": permissions,
    }


def admin_headers(
    permissions: str = (
        "user:read,user:admin,"
        "feedback:read,feedback:admin,notification:read,notification:admin"
    ),
) -> dict[str, str]:
    return {
        "X-AI-User-ID": "workbench-admin",
        "X-AI-Roles": "admin",
        "X-AI-Tenant-ID": "default",
        "X-AI-Department-ID": "410",
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
    from app.identity.api import DepartmentAuthority, resolve_department_scope

    async def department_source(work_id, **_kwargs):
        memberships = {"ordinary": ["110"], "workbench-admin": ["410"]}
        ids = memberships.get(work_id, [])
        return resolve_department_scope(ids, {"110": None, "210": None, "410": None}) if ids else DepartmentAuthority()

    monkeypatch.setattr("app.principal_authority.resolve_department_authority", department_source)

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
    monkeypatch.setattr(_owner_identity_infrastructure_audit_postgres, 'append_audit_log', fake_audit)
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
        "permissions": ["feedback:read", "notification:read", "user:read"],
        "tenant_id": "default",
        "department_id": "110",
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


def test_retired_generic_settings_routes_are_absent(monkeypatch):
    install_workbench_route_fakes(monkeypatch)
    client = TestClient(create_app())

    for response in (
        client.get("/api/settings/", headers=admin_headers()),
        client.put("/api/settings/ui.locale", json={"value": "zh-CN"}, headers=admin_headers()),
        client.post("/api/settings/reset/ui.locale", headers=admin_headers()),
    ):
        assert response.status_code == 404


RETIRED_ENDPOINTS = [
    ("GET", "/api/feedback/?skip=10&limit=5", "feedback:read", "feedback_unavailable"),
    ("GET", "/api/feedback/stats", "feedback:read", "feedback_unavailable"),
    ("PUT", "/api/feedback/feedback-example", "feedback:admin", "feedback_unavailable"),
    ("DELETE", "/api/feedback/feedback-example", "feedback:admin", "feedback_unavailable"),
    ("GET", "/api/notifications/active", "notification:read", "notifications_unavailable"),
    ("POST", "/api/notifications/announcement-example/dismiss", "notification:read", "notifications_unavailable"),
    ("GET", "/api/notifications/admin?skip=10&limit=5", "notification:admin", "notifications_unavailable"),
    ("POST", "/api/notifications/", "notification:admin", "notifications_unavailable"),
    ("PUT", "/api/notifications/announcement-example", "notification:admin", "notifications_unavailable"),
    ("DELETE", "/api/notifications/announcement-example", "notification:admin", "notifications_unavailable"),
    ("POST", "/api/notifications/announcement-example/replay", "notification:admin", "notifications_unavailable"),
]


def install_unavailable_route_guards(monkeypatch):
    calls = install_workbench_route_fakes(monkeypatch)

    @asynccontextmanager
    async def unexpected_transaction():
        raise AssertionError("Retired feedback/announcement endpoints must not open a transaction")
        yield  # pragma: no cover

    monkeypatch.setattr("app.routes.workbench_projections.transaction", unexpected_transaction)
    return calls


@pytest.mark.parametrize("method,path,permission,detail", RETIRED_ENDPOINTS)
def test_unimplemented_endpoints_reject_without_claiming_data_or_queueing(
    monkeypatch, method, path, permission, detail,
):
    calls = install_unavailable_route_guards(monkeypatch)
    client = TestClient(create_app())
    # The retired endpoints do not consume payloads, echo sensitive material, or
    # pretend a summary audit row is an accepted command for some future worker.
    payload = {"comment": "sk-live-secret-value", "audience": {"tenant_id": "other-tenant"}}
    for tenant_id in ("tenant-a", "tenant-b"):
        for headers in (user_headers(permission), admin_headers("")):
            headers["X-AI-Tenant-ID"] = tenant_id
            response = client.request(method, path, headers=headers, json=payload)
            assert response.status_code == 501
            assert response.json() == {"detail": detail}
            assert_no_sensitive_material(response.json())
    assert calls == []


@pytest.mark.parametrize("method,path,permission,detail", RETIRED_ENDPOINTS)
def test_unimplemented_endpoints_preserve_auth_and_permission_boundaries(
    monkeypatch, method, path, permission, detail,
):
    calls = install_unavailable_route_guards(monkeypatch)
    client = TestClient(create_app())
    anonymous = client.request(method, path)
    if path == "/api/notifications/active":
        assert anonymous.status_code == 200
        assert anonymous.json() == {"notifications": []}
    else:
        assert anonymous.status_code == 401
        assert anonymous.json() == {"detail": "missing_authenticated_principal"}

    denied = client.request(method, path, headers=user_headers(""))
    assert denied.status_code == 403
    assert denied.json() == {"detail": f"missing_permission:{permission}"}
    assert calls == []


@pytest.mark.parametrize("method,path,permission,detail", RETIRED_ENDPOINTS)
def test_unimplemented_endpoints_preserve_deployment_tenant_scope(
    monkeypatch, method, path, permission, detail,
):
    calls = install_unavailable_route_guards(monkeypatch)
    settings = Settings(frontend_poc_auth_enabled=True).model_copy(
        update={"deployment_environment": "production", "default_tenant_id": "tenant-a"}
    )
    monkeypatch.setattr("app.auth.get_settings", lambda: settings)
    client = TestClient(create_app())
    headers = admin_headers()
    headers["X-AI-Tenant-ID"] = "tenant-b"
    response = client.request(method, path, headers=headers)
    assert response.status_code == 403
    assert response.json() == {"detail": "tenant_scope_not_allowed"}
    assert calls == []


@pytest.mark.parametrize(
    "method,path,permission,field",
    [
        ("PUT", "/api/feedback/bad%20id", "feedback:admin", "feedback_id"),
        ("DELETE", "/api/feedback/bad%20id", "feedback:admin", "feedback_id"),
        ("POST", "/api/notifications/bad%20id/dismiss", "notification:read", "notification_id"),
        ("PUT", "/api/notifications/bad%20id", "notification:admin", "notification_id"),
        ("DELETE", "/api/notifications/bad%20id", "notification:admin", "notification_id"),
        ("POST", "/api/notifications/bad%20id/replay", "notification:admin", "notification_id"),
    ],
)
def test_retired_mutations_validate_ids_after_permission(monkeypatch, method, path, permission, field):
    calls = install_unavailable_route_guards(monkeypatch)
    client = TestClient(create_app())
    denied = client.request(method, path, headers=user_headers(""))
    assert denied.status_code == 403
    assert denied.json() == {"detail": f"missing_permission:{permission}"}
    invalid = client.request(method, path, headers=admin_headers())
    assert invalid.status_code == 400
    assert invalid.json() == {"detail": f"{field} contains unsupported characters"}
    assert calls == []


def test_repeat_announcement_dismiss_never_acknowledges_or_recreates_demo_popup(monkeypatch):
    calls = install_unavailable_route_guards(monkeypatch)
    client = TestClient(create_app())
    for user_id in ("ordinary-a", "ordinary-b"):
        headers = {**user_headers(), "X-AI-User-ID": user_id}
        for _ in range(2):
            read = client.get("/api/notifications/active", headers=headers)
            assert read.status_code == 501
            assert read.json() == {"detail": "notifications_unavailable"}
            dismiss = client.post("/api/notifications/platform-announcement/dismiss", headers=headers)
            assert dismiss.status_code == 501
            assert dismiss.json() == {"detail": "notifications_unavailable"}
    assert calls == []


def test_anonymous_announcement_compat_does_not_swallow_invalid_auth(monkeypatch):
    from fastapi import HTTPException
    from app.routes import workbench_projections

    calls = install_unavailable_route_guards(monkeypatch)

    async def invalid_principal(request):
        raise HTTPException(status_code=401, detail="invalid_session")

    monkeypatch.setattr(workbench_projections, "require_principal", invalid_principal)
    response = TestClient(create_app()).get("/api/notifications/active")
    assert response.status_code == 401
    assert response.json() == {"detail": "invalid_session"}
    assert calls == []
