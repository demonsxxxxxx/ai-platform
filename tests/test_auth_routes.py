from contextlib import asynccontextmanager
from types import SimpleNamespace

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
import pytest

from app.auth import AuthPrincipal, is_ai_admin, require_principal, sign_principal_session, verify_principal_session
from app.main import create_app


def auth_settings(**overrides):
    values = {
        "ai_session_secret": "secret",
        "ai_session_max_age_seconds": 28800,
        "ai_session_cookie_name": "ai_platform_session",
        "ai_session_cookie_secure": False,
        "default_tenant_id": "default",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


@asynccontextmanager
async def fake_transaction():
    yield object()


def test_ai_admin_maps_admin_and_developer_roles():
    assert is_ai_admin(AuthPrincipal("u1", "Admin", "default", roles=["admin"]))
    assert is_ai_admin(AuthPrincipal("u2", "Dev", "default", roles=["developer"]))
    assert not is_ai_admin(AuthPrincipal("u3", "User", "default", roles=["user"]))


def test_ai_admin_maps_platform_admin_aliases():
    assert is_ai_admin(AuthPrincipal("u1", "Platform Admin", "default", roles=["platform_admin"]))
    assert is_ai_admin(AuthPrincipal("u2", "Break Glass", "default", roles=["break_glass_admin"]))
    assert is_ai_admin(AuthPrincipal("u3", "Legacy Dev", "default", roles=["developer"]))
    assert not is_ai_admin(AuthPrincipal("u4", "Auditor", "default", roles=["auditor"]))
    assert not is_ai_admin(AuthPrincipal("u5", "Tenant Admin", "default", roles=["tenant_admin"]))
    assert not is_ai_admin(AuthPrincipal("u6", "Skill Developer", "default", roles=["skill_developer"]))
    assert not is_ai_admin(AuthPrincipal("u7", "Runtime Operator", "default", roles=["runtime_operator"]))


def test_signed_session_roundtrip_preserves_principal(monkeypatch):
    monkeypatch.setattr("app.auth.get_settings", lambda: auth_settings())

    token = sign_principal_session(
        AuthPrincipal(
            user_id="W001",
            display_name="Zhang San",
            tenant_id="default",
            roles=["user"],
            permissions=["agent:use"],
            source="company-login",
        )
    )
    principal = verify_principal_session(token)

    assert len(token.split(".")) == 3
    assert principal.user_id == "W001"
    assert principal.display_name == "Zhang San"
    assert principal.roles == ["user"]
    assert principal.source == "company-login"


def test_require_principal_accepts_signed_session_cookie(monkeypatch):
    monkeypatch.setattr("app.auth.get_settings", lambda: auth_settings())
    token = sign_principal_session(AuthPrincipal("W001", "Zhang San", "default", source="company-login"))
    app = FastAPI()

    @app.get("/probe")
    async def probe(principal: AuthPrincipal = Depends(require_principal)):
        return {"user_id": principal.user_id, "source": principal.source}

    response = TestClient(app).get("/probe", cookies={"ai_platform_session": token})

    assert response.status_code == 200
    assert response.json() == {"user_id": "W001", "source": "company-login"}


def test_login_sets_cookie_and_returns_ai_role(monkeypatch):
    async def fake_login(username, password):
        assert username == "dev001"
        assert password == "pw"
        return {"workId": "dev001", "userName": "dev001", "cnName": "Developer"}

    async def fake_user_info(work_id):
        assert work_id == "dev001"
        return {"roles": ["developer"], "permissions": ["agent:use"]}

    async def fake_ensure_user(conn, *, tenant_id, user_id, display_name=None):
        assert user_id == "dev001"

    async def fake_append_audit_log(conn, **kwargs):
        assert kwargs["action"] == "auth.login"
        assert kwargs["payload_json"]["source"] == "company-login"
        assert kwargs["payload_json"]["work_id"] == "dev001"
        assert kwargs["payload_json"]["roles"] == ["developer"]
        assert "agent:use" in kwargs["payload_json"]["permissions"]
        assert "agent:admin" in kwargs["payload_json"]["permissions"]
        assert kwargs["payload_json"]["is_admin"] is True
        return "aud_1"

    settings = auth_settings(ai_session_cookie_secure=True)
    monkeypatch.setattr("app.auth.get_settings", lambda: settings)
    monkeypatch.setattr("app.routes.auth.get_settings", lambda: settings)
    monkeypatch.setattr("app.routes.auth.call_existing_login", fake_login)
    monkeypatch.setattr("app.routes.auth.call_existing_user_info", fake_user_info)
    monkeypatch.setattr("app.routes.auth.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.auth.ensure_user", fake_ensure_user)
    monkeypatch.setattr("app.routes.auth.append_audit_log", fake_append_audit_log)

    response = TestClient(create_app()).post("/api/ai/auth/login", json={"user_name": "dev001", "password": "pw"})

    assert response.status_code == 200
    assert response.json()["is_admin"] is True
    assert response.json()["user_id"] == "dev001"
    assert "ai_platform_session=" in response.headers["set-cookie"]
    set_cookie = response.headers["set-cookie"].lower()
    assert "httponly" in set_cookie
    assert "samesite=lax" in set_cookie
    assert "max-age=28800" in set_cookie
    assert "path=/" in set_cookie
    assert "secure" in set_cookie


def test_company_user_login_gets_baseline_ai_permissions(monkeypatch):
    async def fake_login(username, password):
        return {"workId": "user001", "userName": "user001", "cnName": "Normal User"}

    async def fake_user_info(work_id):
        return {"roles": ["user"], "permissions": []}

    async def noop(*args, **kwargs):
        return None

    monkeypatch.setattr("app.auth.get_settings", lambda: auth_settings())
    monkeypatch.setattr("app.routes.auth.get_settings", lambda: auth_settings())
    monkeypatch.setattr("app.routes.auth.call_existing_login", fake_login)
    monkeypatch.setattr("app.routes.auth.call_existing_user_info", fake_user_info)
    monkeypatch.setattr("app.routes.auth.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.auth.ensure_user", noop)
    monkeypatch.setattr("app.routes.auth.append_audit_log", noop)

    client = TestClient(create_app())
    response = client.post("/api/ai/auth/login", json={"user_name": "user001", "password": "pw"})

    assert response.status_code == 200
    body = response.json()
    assert body["is_admin"] is False
    assert body["permissions"] == [
        "agent:use",
        "chat:read",
        "chat:write",
        "session:read",
        "session:write",
        "skill:read",
        "marketplace:read",
        "artifact:download",
        "file:upload",
        "file:upload:document",
    ]

    me_response = client.get("/api/ai/auth/me")

    assert me_response.status_code == 200
    assert me_response.json()["permissions"] == body["permissions"]


def test_company_login_does_not_project_large_enterprise_permissions_into_session(monkeypatch):
    async def fake_login(username, password):
        return {"workId": "user001", "userName": "user001", "cnName": "Normal User"}

    async def fake_user_info(work_id):
        return {
            "roles": ["user"],
            "permissions": [f"TaskManagement:legacy-permission-{index}" for index in range(3000)],
        }

    async def noop(*args, **kwargs):
        return None

    monkeypatch.setattr("app.auth.get_settings", lambda: auth_settings())
    monkeypatch.setattr("app.routes.auth.get_settings", lambda: auth_settings())
    monkeypatch.setattr("app.routes.auth.call_existing_login", fake_login)
    monkeypatch.setattr("app.routes.auth.call_existing_user_info", fake_user_info)
    monkeypatch.setattr("app.routes.auth.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.auth.ensure_user", noop)
    monkeypatch.setattr("app.routes.auth.append_audit_log", noop)

    client = TestClient(create_app())
    response = client.post("/api/ai/auth/login", json={"user_name": "user001", "password": "pw"})

    assert response.status_code == 200
    body = response.json()
    assert body["permissions"] == [
        "agent:use",
        "chat:read",
        "chat:write",
        "session:read",
        "session:write",
        "skill:read",
        "marketplace:read",
        "artifact:download",
        "file:upload",
        "file:upload:document",
    ]
    assert len(response.headers["set-cookie"]) < 8192

    me_response = client.get("/api/ai/auth/me")

    assert me_response.status_code == 200
    assert me_response.json()["permissions"] == body["permissions"]


def test_company_login_survives_user_info_failure_as_ordinary_user(monkeypatch):
    async def fake_login(username, password):
        return {"workId": "user001", "userName": "user001", "cnName": "Normal User"}

    async def failing_user_info(work_id):
        raise RuntimeError("user-info unavailable")

    async def noop(*args, **kwargs):
        return None

    monkeypatch.setattr("app.auth.get_settings", lambda: auth_settings())
    monkeypatch.setattr("app.routes.auth.get_settings", lambda: auth_settings())
    monkeypatch.setattr("app.routes.auth.call_existing_login", fake_login)
    monkeypatch.setattr("app.routes.auth.call_existing_user_info", failing_user_info)
    monkeypatch.setattr("app.routes.auth.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.auth.ensure_user", noop)
    monkeypatch.setattr("app.routes.auth.append_audit_log", noop)

    response = TestClient(create_app()).post("/api/auth/login", json={"username": "user001", "password": "pw"})

    assert response.status_code == 200
    body = response.json()
    assert body["access_token"]
    assert body["token_type"] == "bearer"


def test_company_login_admin_allowlist_grants_admin_when_user_info_has_no_roles(monkeypatch):
    async def fake_login(username, password):
        return {"workId": "dev001", "userName": "dev001", "cnName": "Developer"}

    async def fake_user_info(work_id):
        return {"roles": [], "permissions": []}

    async def noop(*args, **kwargs):
        return None

    settings = auth_settings(ai_admin_work_ids="dev001")
    monkeypatch.setattr("app.auth.get_settings", lambda: settings)
    monkeypatch.setattr("app.routes.auth.get_settings", lambda: settings)
    monkeypatch.setattr("app.routes.lambchat_compat.get_settings", lambda: settings)
    monkeypatch.setattr("app.routes.auth.call_existing_login", fake_login)
    monkeypatch.setattr("app.routes.auth.call_existing_user_info", fake_user_info)
    monkeypatch.setattr("app.routes.auth.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.auth.ensure_user", noop)
    monkeypatch.setattr("app.routes.auth.append_audit_log", noop)

    client = TestClient(create_app())
    login_response = client.post("/api/auth/login", json={"username": "dev001", "password": "pw"})

    assert login_response.status_code == 200
    token = login_response.json()["access_token"]
    me_response = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})

    assert me_response.status_code == 200
    body = me_response.json()
    assert body["roles"] == ["developer"]
    assert "agent:admin" in body["permissions"]


def test_company_login_admin_allowlist_matches_submitted_username(monkeypatch):
    async def fake_login(username, password):
        return {"workId": "W001", "userName": username, "cnName": "Developer"}

    async def fake_user_info(work_id):
        return {"roles": [], "permissions": []}

    async def noop(*args, **kwargs):
        return None

    settings = auth_settings(ai_admin_work_ids="xinlin.jiang")
    monkeypatch.setattr("app.auth.get_settings", lambda: settings)
    monkeypatch.setattr("app.routes.auth.get_settings", lambda: settings)
    monkeypatch.setattr("app.routes.lambchat_compat.get_settings", lambda: settings)
    monkeypatch.setattr("app.routes.auth.call_existing_login", fake_login)
    monkeypatch.setattr("app.routes.auth.call_existing_user_info", fake_user_info)
    monkeypatch.setattr("app.routes.auth.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.auth.ensure_user", noop)
    monkeypatch.setattr("app.routes.auth.append_audit_log", noop)

    client = TestClient(create_app())
    login_response = client.post("/api/auth/login", json={"username": "xinlin.jiang", "password": "pw"})

    assert login_response.status_code == 200
    token = login_response.json()["access_token"]
    me_response = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})

    assert me_response.status_code == 200
    assert me_response.json()["roles"] == ["developer"]


def test_company_unsuccessful_login_status_returns_401(monkeypatch):
    async def fake_login(username, password):
        return {"userName": None, "cnName": None, "workId": None, "status": "unsuccessfully!"}

    monkeypatch.setattr("app.routes.auth.call_existing_login", fake_login)

    response = TestClient(create_app()).post("/api/auth/login", json={"username": "user001", "password": "bad"})

    assert response.status_code == 401
    assert response.json()["detail"] == "company_login_failed"


def test_company_developer_login_gets_admin_ai_permissions(monkeypatch):
    async def fake_login(username, password):
        return {"workId": "dev001", "userName": "dev001", "cnName": "Developer"}

    async def fake_user_info(work_id):
        return {"roles": ["developer"], "permissions": ["custom:business"]}

    async def noop(*args, **kwargs):
        return None

    monkeypatch.setattr("app.auth.get_settings", lambda: auth_settings())
    monkeypatch.setattr("app.routes.auth.get_settings", lambda: auth_settings())
    monkeypatch.setattr("app.routes.lambchat_compat.get_settings", lambda: auth_settings())
    monkeypatch.setattr("app.routes.auth.call_existing_login", fake_login)
    monkeypatch.setattr("app.routes.auth.call_existing_user_info", fake_user_info)
    monkeypatch.setattr("app.routes.auth.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.auth.ensure_user", noop)
    monkeypatch.setattr("app.routes.auth.append_audit_log", noop)

    client = TestClient(create_app())
    login_response = client.post("/api/auth/login", json={"username": "dev001", "password": "pw"})

    assert login_response.status_code == 200
    token = login_response.json()["access_token"]
    me_response = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})

    assert me_response.status_code == 200
    body = me_response.json()
    assert "custom:business" not in body["permissions"]
    assert "agent:admin" in body["permissions"]
    assert "model:admin" in body["permissions"]
    assert "settings:manage" in body["permissions"]


def test_auth_me_returns_cookie_principal(monkeypatch):
    monkeypatch.setattr("app.auth.get_settings", lambda: auth_settings())
    token = sign_principal_session(AuthPrincipal("W002", "Normal User", "default", roles=["user"], source="company-login"))

    response = TestClient(create_app()).get("/api/ai/auth/me", cookies={"ai_platform_session": token})

    assert response.status_code == 200
    assert response.json()["user_id"] == "W002"
    assert response.json()["is_admin"] is False


def test_lambchat_auth_aliases_return_bearer_token(monkeypatch):
    async def fake_login(username, password):
        return {"workId": "dev001", "userName": "dev001", "cnName": "Developer"}

    async def fake_user_info(work_id):
        return {"roles": ["developer"], "permissions": ["agent:use"]}

    async def noop(*args, **kwargs):
        return None

    monkeypatch.setattr("app.auth.get_settings", lambda: auth_settings())
    monkeypatch.setattr("app.routes.auth.get_settings", lambda: auth_settings())
    monkeypatch.setattr("app.routes.lambchat_compat.get_settings", lambda: auth_settings())
    monkeypatch.setattr("app.routes.auth.call_existing_login", fake_login)
    monkeypatch.setattr("app.routes.auth.call_existing_user_info", fake_user_info)
    monkeypatch.setattr("app.routes.auth.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.auth.ensure_user", noop)
    monkeypatch.setattr("app.routes.auth.append_audit_log", noop)

    client = TestClient(create_app())
    login_response = client.post("/api/auth/login", json={"username": "dev001", "password": "pw"})

    assert login_response.status_code == 200
    token = login_response.json()["access_token"]
    assert len(token.split(".")) == 3

    me_response = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})

    assert me_response.status_code == 200
    assert me_response.json()["id"] == "dev001"


def test_lambchat_oauth_providers_disable_registration():
    response = TestClient(create_app()).get("/api/auth/oauth/providers")

    assert response.status_code == 200
    assert response.json()["providers"] == []
    assert response.json()["registration_enabled"] is False
