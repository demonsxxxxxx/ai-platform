from types import SimpleNamespace

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.auth import AuthPrincipal, principal_from_trusted_headers, require_principal


def test_principal_from_trusted_headers_requires_user_id():
    headers = {"x-ai-user-name": "QA User"}
    principal = principal_from_trusted_headers(headers)
    assert principal is None


def test_principal_from_trusted_headers_maps_roles_and_permissions():
    headers = {
        "x-ai-user-id": "u-001",
        "x-ai-user-name": "QA User",
        "x-ai-tenant-id": "tenant-a",
        "x-ai-department-id": "qa",
        "x-ai-roles": "user,qa_reviewer",
        "x-ai-permissions": "agent:use,artifact:download",
    }
    principal = principal_from_trusted_headers(headers)

    assert isinstance(principal, AuthPrincipal)
    assert principal.user_id == "u-001"
    assert principal.tenant_id == "tenant-a"
    assert principal.department_id == "qa"
    assert principal.roles == ["user", "qa_reviewer"]
    assert principal.permissions == ["agent:use", "artifact:download"]


def test_require_principal_rejects_forged_headers_without_gateway_secret(monkeypatch):
    monkeypatch.setattr(
        "app.auth.get_settings",
        lambda: SimpleNamespace(trusted_principal_secret="", frontend_poc_auth_enabled=False),
    )
    app = FastAPI()

    @app.get("/probe")
    async def probe(principal: AuthPrincipal = Depends(require_principal)):
        return {"user_id": principal.user_id}

    response = TestClient(app).get("/probe", headers={"x-ai-user-id": "forged"})

    assert response.status_code == 503
    assert response.json()["detail"] == "trusted_principal_secret_not_configured"


def test_require_principal_rejects_gateway_principal_with_missing_secret(monkeypatch):
    monkeypatch.setattr(
        "app.auth.get_settings",
        lambda: SimpleNamespace(trusted_principal_secret="secret", frontend_poc_auth_enabled=False),
    )
    app = FastAPI()

    @app.get("/probe")
    async def probe(principal: AuthPrincipal = Depends(require_principal)):
        return {"user_id": principal.user_id}

    response = TestClient(app).get("/probe", headers={"x-ai-user-id": "forged"})

    assert response.status_code == 403
    assert response.json()["detail"] == "invalid_gateway_principal_secret"


def test_require_principal_rejects_gateway_principal_with_wrong_secret(monkeypatch):
    monkeypatch.setattr(
        "app.auth.get_settings",
        lambda: SimpleNamespace(trusted_principal_secret="secret", frontend_poc_auth_enabled=False),
    )
    app = FastAPI()

    @app.get("/probe")
    async def probe(principal: AuthPrincipal = Depends(require_principal)):
        return {"user_id": principal.user_id}

    response = TestClient(app).get(
        "/probe",
        headers={"x-ai-user-id": "forged", "x-ai-gateway-secret": "wrong"},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "invalid_gateway_principal_secret"


def test_require_principal_accepts_gateway_signed_principal(monkeypatch):
    monkeypatch.setattr(
        "app.auth.get_settings",
        lambda: SimpleNamespace(trusted_principal_secret="secret", frontend_poc_auth_enabled=False),
    )
    app = FastAPI()

    @app.get("/probe")
    async def probe(principal: AuthPrincipal = Depends(require_principal)):
        return {"user_id": principal.user_id}

    response = TestClient(app).get(
        "/probe",
        headers={
            "x-ai-user-id": "u-001",
            "x-ai-gateway-secret": "secret",
        },
    )

    assert response.status_code == 200
    assert response.json() == {"user_id": "u-001"}


def test_require_principal_accepts_frontend_poc_principal_only_when_enabled(monkeypatch):
    monkeypatch.setattr(
        "app.auth.get_settings",
        lambda: SimpleNamespace(trusted_principal_secret="secret", frontend_poc_auth_enabled=True),
    )
    app = FastAPI()

    @app.get("/probe")
    async def probe(principal: AuthPrincipal = Depends(require_principal)):
        return {"user_id": principal.user_id, "source": principal.source}

    response = TestClient(app).get("/probe", headers={"x-ai-user-id": "poc-user"})

    assert response.status_code == 200
    assert response.json() == {"user_id": "poc-user", "source": "frontend-poc"}
