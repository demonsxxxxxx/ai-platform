from contextlib import asynccontextmanager
from datetime import date
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.auth import AuthPrincipal, require_principal, is_ai_admin
from app.runs.transport.model_token_usage import build_model_token_usage_router


def test_admin_routes_scope_dates_and_pagination(monkeypatch):
    calls = []

    @asynccontextmanager
    async def transaction():
        yield object()

    class Service:
        async def query(self, conn, **kwargs):
            calls.append(kwargs)
            return {"data": [], "total": 0, "meta": {"coverage": {"status": "unavailable"}}}

    app = FastAPI()
    app.include_router(build_model_token_usage_router(
        service=Service(), transaction_factory=transaction,
        principal_dependency=require_principal, is_admin=is_ai_admin,
    ), prefix="/api/ai")
    principal = AuthPrincipal(user_id="user", display_name="User",tenant_id="tenant",roles=["user"])
    app.dependency_overrides[require_principal] = lambda: principal
    with TestClient(app) as client:
        assert client.get("/api/ai/admin/usage/users").status_code == 403
        principal = AuthPrincipal(user_id="admin",display_name="Admin",tenant_id="tenant",roles=["admin"])
        assert client.get("/api/ai/admin/usage/users?start_date=2026-10-20&end_date=2026-10-20&q=12&limit=7&offset=14").status_code == 200
        assert calls[-1] == dict(tenant_id="tenant",start_date=date(2026,10,20),end_date=date(2026,10,20),q="12",limit=7,offset=14)
        assert client.get("/api/ai/admin/usage/users/user-b/models?start_date=2026-10-20").status_code == 200
        assert calls[-1]["user_id"] == "user-b" and calls[-1]["tenant_id"] == "tenant"
        assert client.get("/api/ai/admin/usage/users?limit=101").status_code == 422
        assert client.get("/api/ai/admin/usage/users?start_date=bad").status_code == 422


def test_extreme_iso_dates_return_422_without_querying_persistence(monkeypatch):
    from app.runs.application.model_token_usage import ModelTokenUsageService

    @asynccontextmanager
    async def transaction():
        yield object()

    class Persistence:
        async def query(self, *args, **kwargs):
            raise AssertionError("invalid window reached SQL")

    app = FastAPI()
    app.include_router(build_model_token_usage_router(
        service=ModelTokenUsageService(Persistence()), transaction_factory=transaction,
        principal_dependency=require_principal, is_admin=is_ai_admin,
    ), prefix="/api/ai")
    app.dependency_overrides[require_principal] = lambda: AuthPrincipal(
        user_id="admin",display_name="Admin",tenant_id="tenant",roles=["admin"])
    with TestClient(app) as client:
        for path in ("/api/ai/admin/usage/users", "/api/ai/admin/usage/users/user/models"):
            for query in ("end_date=0001-01-01", "start_date=0001-01-01", "end_date=9999-12-31"):
                response = client.get(path + "?" + query)
                assert response.status_code == 422
                assert response.json()["detail"] == "usage_date_range_invalid"


def test_bootstrap_injects_trusted_usage_dependencies(monkeypatch):
    from app.bootstrap import model_token_usage

    captured = {}
    monkeypatch.setattr(model_token_usage, "build_model_token_usage_router", lambda **kwargs: captured.update(kwargs))
    service = object()
    model_token_usage.build_model_token_usage_admin_router(service)
    assert captured == {
        "service": service, "transaction_factory": model_token_usage.transaction,
        "principal_dependency": require_principal, "is_admin": is_ai_admin,
    }
