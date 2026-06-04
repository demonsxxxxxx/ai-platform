from contextlib import asynccontextmanager

from fastapi.testclient import TestClient

from app.main import create_app


def auth_settings():
    return type("S", (), {"trusted_principal_secret": "test-secret", "frontend_poc_auth_enabled": False})()


@asynccontextmanager
async def fake_transaction():
    yield object()


async def fake_agent_app_rows(conn, *, tenant_id):
    return [
        {
            "app_id": "translate",
            "name": "文档翻译",
            "agent_type": "file",
            "default_skill_id": "baoyu-translate",
            "input_modes": ["docx"],
            "output_modes": ["translated_docx"],
            "status": "active",
        }
    ]


def test_lambchat_projection_contains_no_executor_secrets(monkeypatch):
    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.agent_apps.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.agent_apps.repositories.list_agent_app_projections", fake_agent_app_rows)
    client = TestClient(create_app())

    response = client.get(
        "/api/ai/agent-apps",
        headers={
            "x-ai-user-id": "user-a",
            "x-ai-user-name": "User A",
            "x-ai-tenant-id": "default",
            "x-ai-roles": "developer",
            "x-ai-gateway-secret": "test-secret",
        },
    )

    assert response.status_code == 200
    payload_text = response.text.lower()
    assert "api_key" not in payload_text
    assert "token" not in payload_text
    assert "password" not in payload_text
    assert "runtime_211_base_url" not in payload_text
    assert "claude" not in payload_text
