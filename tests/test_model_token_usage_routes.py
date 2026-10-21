from contextlib import asynccontextmanager
from datetime import date
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.auth import AuthPrincipal, require_principal
from app.runs.transport.model_token_usage import router


def test_admin_routes_scope_dates_and_pagination(monkeypatch):
    calls = []

    @asynccontextmanager
    async def transaction():
        yield object()

    class Service:
        async def query(self, conn, **kwargs):
            calls.append(kwargs)
            return {"data": [], "total": 0, "meta": {"coverage": {"status": "unavailable"}}}

    monkeypatch.setattr("app.runs.transport.model_token_usage.transaction", transaction)
    app = FastAPI()
    app.include_router(router, prefix="/api/ai")
    app.state.model_token_usage_service = Service()
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

    monkeypatch.setattr("app.runs.transport.model_token_usage.transaction", transaction)
    app = FastAPI()
    app.include_router(router, prefix="/api/ai")
    app.state.model_token_usage_service = ModelTokenUsageService(Persistence())
    app.dependency_overrides[require_principal] = lambda: AuthPrincipal(
        user_id="admin",display_name="Admin",tenant_id="tenant",roles=["admin"])
    with TestClient(app) as client:
        for path in ("/api/ai/admin/usage/users", "/api/ai/admin/usage/users/user/models"):
            for query in ("end_date=0001-01-01", "start_date=0001-01-01", "end_date=9999-12-31"):
                response = client.get(path + "?" + query)
                assert response.status_code == 422
                assert response.json()["detail"] == "usage_date_range_invalid"
