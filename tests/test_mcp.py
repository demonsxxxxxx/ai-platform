from fastapi.testclient import TestClient

from app.main import create_app
from app.routes import mcp
from app.settings import Settings


def _headers() -> dict[str, str]:
    return {
        "X-AI-User-ID": "ordinary",
        "X-AI-Roles": "user",
        "X-AI-Tenant-ID": "default",
        "X-AI-Department-ID": "qa",
        "X-AI-Permissions": "skill:read,marketplace:read",
    }


def test_mcp_chat_catalog_route_projects_server_id_without_transport_details(monkeypatch):
    """Expose the stable Server source without leaking its private connection target."""

    async def unavailable_catalog(_principal):
        return [], [{"label": "gateway-prod", "reason": "discovery_failed"}]

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr(mcp, "_chat_tool_catalog", unavailable_catalog)
    client = TestClient(create_app())

    response = client.get("/api/mcp/chat-tools", headers=_headers())

    assert response.status_code == 200
    assert response.json() == {
        "tools": [],
        "unavailable": [{"label": "gateway-prod", "reason": "discovery_failed"}],
        "count": 0,
    }
    assert "private.example" not in response.text
    assert "JWT-Authorization" not in response.text
