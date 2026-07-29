from contextlib import asynccontextmanager

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


def test_mcp_chat_catalog_route_hides_remote_server_and_transport_identity(monkeypatch):
    """Exercise the public MCP route rather than a naming-only responsibility mirror."""

    class FakeConnection:
        pass

    @asynccontextmanager
    async def fake_transaction():
        yield FakeConnection()

    async def no_authorized_tools(conn, **kwargs):
        return []

    async def unavailable_catalog(conn, **kwargs):
        return [{"label": "prod-server-private.example", "reason": "discovery_failed"}]

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr(mcp, "transaction", fake_transaction)
    monkeypatch.setattr(mcp.mcp_repository, "list_authorized_chat_mcp_tools", no_authorized_tools)
    monkeypatch.setattr(mcp.mcp_repository, "list_chat_mcp_catalog_unavailable", unavailable_catalog)
    client = TestClient(create_app())

    response = client.get("/api/mcp/chat-tools", headers=_headers())

    assert response.status_code == 200
    assert response.json() == {
        "tools": [],
        "unavailable": [{"label": "已配置 MCP 服务", "reason": "discovery_failed"}],
        "count": 0,
    }
    assert "prod-server-private.example" not in response.text
