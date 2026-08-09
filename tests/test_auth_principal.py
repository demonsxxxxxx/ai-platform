import hashlib
import hmac
import json
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient

from app import auth as auth_module
from app.auth import (
    AuthPrincipal,
    is_ai_admin,
    principal_from_trusted_headers,
    require_principal,
    sign_principal_session,
    verify_principal_session,
)


def session_settings():
    return SimpleNamespace(
        ai_session_secret="synthetic-secret",
        ai_session_max_age_seconds=28800,
        auth_context_cookie_name="ai_platform_auth_context",
        company_authority_freshness_seconds=900,
        deployment_environment="development",
        trusted_principal_secret="gateway-secret",
        frontend_poc_auth_enabled=False,
    )


def sign_legacy_session(payload: dict[str, object]) -> str:
    header_part = auth_module._b64url_encode(
        json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode("utf-8")
    )
    payload_part = auth_module._b64url_encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signing_input = f"{header_part}.{payload_part}"
    signature = hmac.new(
        session_settings().ai_session_secret.encode("utf-8"),
        signing_input.encode("ascii"),
        hashlib.sha256,
    ).digest()
    return f"{signing_input}.{auth_module._b64url_encode(signature)}"


def legacy_payload(*, source: str, authz_policy_version: int | None = None) -> dict[str, object]:
    payload: dict[str, object] = {
        "user_id": "synthetic-user",
        "display_name": "Synthetic User",
        "tenant_id": "default",
        "roles": ["user"],
        "permissions": ["chat:read"],
        "source": source,
        "iat": int(time.time()),
        "exp": int(time.time()) + 3600,
    }
    if authz_policy_version is not None:
        payload["authz_policy_version"] = authz_policy_version
    return payload


@pytest.mark.parametrize(
    ("role", "expected_role", "expected_admin"),
    [
        (" platform_admin ", "platform_admin", True),
        ("PLATFORM_ADMIN", "platform_admin", True),
        ("platform-admin", "platform-admin", False),
        ("platform admin", "platform admin", False),
        (" break_glass_admin ", "break_glass_admin", True),
        ("break-glass-admin", "break-glass-admin", False),
    ],
)
def test_role_identity_is_case_insensitive_exact_and_punctuation_preserving(role, expected_role, expected_admin):
    principal = AuthPrincipal(
        user_id="user-a",
        display_name="User A",
        tenant_id="tenant-a",
        roles=[role],
    )

    assert hasattr(auth_module, "normalize_roles")
    assert auth_module.normalize_roles(principal.roles) == [expected_role]
    assert is_ai_admin(principal) is expected_admin


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


def test_current_company_session_policy_version_roundtrips(monkeypatch):
    monkeypatch.setattr("app.auth.get_settings", session_settings)
    principal = AuthPrincipal(
        "synthetic-admin",
        "Synthetic Admin",
        "default",
        roles=["admin"],
        permissions=["chat:read"],
        source="company-login",
    )

    assert verify_principal_session(sign_principal_session(principal)) == principal


def test_legacy_company_session_without_policy_version_fails_closed(monkeypatch):
    monkeypatch.setattr("app.auth.get_settings", session_settings)

    with pytest.raises(HTTPException) as exc_info:
        verify_principal_session(sign_legacy_session(legacy_payload(source="company-login")))

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "stale_company_session"


def test_mismatched_company_session_policy_version_fails_closed(monkeypatch):
    monkeypatch.setattr("app.auth.get_settings", session_settings)

    with pytest.raises(HTTPException) as exc_info:
        verify_principal_session(
            sign_legacy_session(legacy_payload(source="company-login", authz_policy_version=-1))
        )

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "stale_company_session"


def test_non_company_signed_session_remains_compatible_without_policy_version(monkeypatch):
    monkeypatch.setattr("app.auth.get_settings", session_settings)

    principal = verify_principal_session(sign_legacy_session(legacy_payload(source="trusted-header")))

    assert principal.source == "trusted-header"
    assert principal.roles == ["user"]


def _browser_company_snapshot(*, policy_version: int = 1, checked_at: str | None = None) -> dict[str, object]:
    return {
        "user_id": "browser-user",
        "display_name": "Browser User",
        "tenant_id": "default",
        "department_id": "qa",
        "roles": ["user"],
        "permissions": ["agent:use"],
        "source": "company-login",
        "authz_policy_version": policy_version,
        "authority_source": "company-user-info",
        "authority_checked_at": checked_at or datetime.now(timezone.utc).isoformat(),
    }


def _browser_authority_app() -> FastAPI:
    app = FastAPI()

    @app.get("/auth/me")
    async def auth_me(principal: AuthPrincipal = Depends(require_principal)):
        return {
            "user_id": principal.user_id,
            "authz_policy_version": principal.authz_policy_version,
            "authority_source": principal.authority_source,
            "authority_checked_at": principal.authority_checked_at,
        }

    @app.post("/runs")
    async def run_admission(principal: AuthPrincipal = Depends(require_principal)):
        return {
            "department_id": principal.department_id,
            "roles": principal.roles,
            "permissions": principal.permissions,
            "authz_policy_version": principal.authz_policy_version,
            "authority_source": principal.authority_source,
            "authority_checked_at": principal.authority_checked_at,
        }

    return app


def test_browser_principal_preserves_authority_facts_through_me_and_run_admission(monkeypatch):
    snapshot = _browser_company_snapshot()

    async def principal_for_context(_handle, _settings):
        return snapshot

    monkeypatch.setattr("app.auth.get_settings", session_settings)
    monkeypatch.setattr("app.auth.principal_for_context", principal_for_context)
    client = TestClient(_browser_authority_app())
    client.cookies.set("ai_platform_auth_context", "context-handle")

    me_response = client.get("/auth/me")
    admission_response = client.post("/runs")

    assert me_response.status_code == 200
    assert admission_response.status_code == 200
    for field in ("authz_policy_version", "authority_source", "authority_checked_at"):
        assert me_response.json()[field] == snapshot[field]
        assert admission_response.json()[field] == snapshot[field]


@pytest.mark.parametrize(
    ("snapshot", "detail"),
    [
        (_browser_company_snapshot(policy_version=2), "stale_company_session"),
        (
            _browser_company_snapshot(
                checked_at=(datetime.now(timezone.utc) - timedelta(minutes=16)).isoformat()
            ),
            "stale_company_authority",
        ),
    ],
)
def test_browser_principal_stale_policy_or_authority_fails_closed_for_me_and_admission(
    monkeypatch,
    snapshot,
    detail,
):
    async def principal_for_context(_handle, _settings):
        return snapshot

    monkeypatch.setattr("app.auth.get_settings", session_settings)
    monkeypatch.setattr("app.auth.principal_for_context", principal_for_context)
    client = TestClient(_browser_authority_app())
    client.cookies.set("ai_platform_auth_context", "context-handle")

    for method, path in ((client.get, "/auth/me"), (client.post, "/runs")):
        response = method(path)
        assert response.status_code == 401
        assert response.json()["detail"] == detail


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


def test_require_principal_rejects_cross_deployment_tenant_from_trusted_gateway(monkeypatch):
    monkeypatch.setattr(
        "app.auth.get_settings",
        lambda: SimpleNamespace(
            trusted_principal_secret="secret",
            frontend_poc_auth_enabled=False,
            default_tenant_id="default",
            deployment_environment="production",
        ),
    )
    app = FastAPI()

    @app.get("/probe")
    async def probe(principal: AuthPrincipal = Depends(require_principal)):
        return {"user_id": principal.user_id}

    response = TestClient(app).get(
        "/probe",
        headers={
            "x-ai-user-id": "u-001",
            "x-ai-tenant-id": "customer-a",
            "x-ai-gateway-secret": "secret",
        },
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "tenant_scope_not_allowed"


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
