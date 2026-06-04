from contextlib import asynccontextmanager

from fastapi.testclient import TestClient

from app.main import create_app


def auth_settings():
    return type("S", (), {"trusted_principal_secret": "test-secret", "frontend_poc_auth_enabled": False})()


def headers(user_id: str) -> dict[str, str]:
    return {
        "x-ai-user-id": user_id,
        "x-ai-user-name": user_id,
        "x-ai-tenant-id": "default",
        "x-ai-gateway-secret": "test-secret",
    }


def test_artifact_download_rejects_different_user(monkeypatch):
    @asynccontextmanager
    async def fake_transaction():
        yield object()

    async def fake_get_authorized_artifact(conn, *, tenant_id, user_id, artifact_id):
        assert tenant_id == "default"
        assert user_id == "user-b"
        assert artifact_id == "art_sample"
        return None

    class ForbiddenStorage:
        def get_bytes(self, *, storage_key):
            raise AssertionError("artifact bytes must not be read for unauthorized principal")

    monkeypatch.setattr("app.routes.files.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.files.get_authorized_artifact", fake_get_authorized_artifact)
    monkeypatch.setattr("app.routes.files.ObjectStorage", ForbiddenStorage)
    monkeypatch.setattr("app.auth.get_settings", auth_settings)

    client = TestClient(create_app())
    response = client.get("/api/ai/artifacts/art_sample/download", headers=headers("user-b"))

    assert response.status_code in {403, 404}
