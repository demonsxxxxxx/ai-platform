from fastapi.testclient import TestClient

from app.main import create_app
from app.settings import Settings


def _settings():
    return Settings(frontend_poc_auth_enabled=True)


def _headers(roles):
    return {
        "X-AI-User-ID": "user-a",
        "X-AI-Roles": roles,
        "X-AI-Tenant-ID": "default",
    }


def test_admin_apply_schema_rejects_ordinary_user(monkeypatch):
    called = []

    async def fake_apply_schema():
        called.append("apply")

    monkeypatch.setattr("app.auth.get_settings", _settings)
    monkeypatch.setattr("app.db.apply_schema", fake_apply_schema)
    monkeypatch.setattr("app.routes.health.apply_schema", fake_apply_schema, raising=False)

    response = TestClient(create_app()).post("/api/ai/admin/apply-schema", headers=_headers("user"))

    assert response.status_code == 403
    assert response.json()["detail"] == "not_ai_admin"
    assert called == []


def test_admin_apply_schema_allows_platform_admin(monkeypatch):
    called = []

    async def fake_apply_schema():
        called.append("apply")

    monkeypatch.setattr("app.auth.get_settings", _settings)
    monkeypatch.setattr("app.db.apply_schema", fake_apply_schema)
    monkeypatch.setattr("app.routes.health.apply_schema", fake_apply_schema, raising=False)

    response = TestClient(create_app()).post("/api/ai/admin/apply-schema", headers=_headers("platform_admin"))

    assert response.status_code == 200
    assert response.json() == {"status": "schema_applied"}
    assert called == ["apply"]
