from fastapi.testclient import TestClient

from app.main import create_app


def auth_settings():
    return type(
        "Settings",
        (),
        {
            "trusted_principal_secret": "test-secret",
            "frontend_poc_auth_enabled": False,
        },
    )()


def auth_headers():
    return {
        "x-ai-user-id": "W001",
        "x-ai-user-name": "Zhang San",
        "x-ai-tenant-id": "default",
        "x-ai-roles": "user",
        "x-ai-gateway-secret": "test-secret",
    }


def test_profile_routes_persist_metadata_and_keep_principal_fields_authoritative(
    monkeypatch,
):
    stored = {"theme": "dark"}

    class FakeService:
        async def get(self, **scope):
            assert scope == {
                "tenant_id": "default",
                "user_id": "W001",
                "display_name": "Zhang San",
            }
            return dict(stored)

        async def merge(self, *, patch, **scope):
            assert scope == {
                "tenant_id": "default",
                "user_id": "W001",
                "display_name": "Zhang San",
            }
            stored.update(patch)
            return dict(stored)

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr(
        "app.bootstrap.identity.ProfileMetadataService",
        lambda _store: FakeService(),
    )
    client = TestClient(create_app())

    update_response = client.put(
        "/api/auth/profile/metadata",
        headers=auth_headers(),
        json={
            "metadata": {
                "company_navigation_favorite_ids": ["内网登录:OA"],
            }
        },
    )
    profile_response = client.get("/api/auth/profile", headers=auth_headers())

    assert update_response.status_code == 200
    assert profile_response.status_code == 200
    assert profile_response.json() == update_response.json()
    assert profile_response.json()["id"] == "W001"
    assert profile_response.json()["metadata"] == {
        "theme": "dark",
        "company_navigation_favorite_ids": ["内网登录:OA"],
        "display_name": "Zhang San",
        "source": "trusted-header",
    }
