from contextlib import asynccontextmanager

from fastapi.testclient import TestClient

from app.main import create_app
from app.settings import Settings


def headers(roles: str = "user", permissions: str = "skill:read,marketplace:read") -> dict[str, str]:
    return {
        "X-AI-User-ID": "ordinary",
        "X-AI-Roles": roles,
        "X-AI-Tenant-ID": "default",
        "X-AI-Department-ID": "qa",
        "X-AI-Permissions": permissions,
    }


def install_mcp_route_fakes(monkeypatch) -> list[tuple[str, dict[str, object]]]:
    from app.routes import mcp

    class FakeConnection:
        pass

    @asynccontextmanager
    async def fake_transaction():
        yield FakeConnection()

    calls: list[tuple[str, dict[str, object]]] = []

    async def fake_list(conn, *, tenant_id, include_disabled=True):
        calls.append(
            (
                "list",
                {
                    "tenant_id": tenant_id,
                    "include_disabled": include_disabled,
                    "conn_type": type(conn).__name__,
                },
            )
        )
        return [
            {
                "tool_id": "ragflow-knowledge-search",
                "server_id": "ragflow",
                "name": "RAGFlow Search",
                "description": "Search governed knowledge bases.",
                "effective_status": "active",
                "status": "active",
                "visible_to_user": True,
                "write_capable": False,
                "risk_level": "low",
                "updated_at": "2026-06-23T00:00:00Z",
            }
        ]

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr(mcp, "transaction", fake_transaction)
    monkeypatch.setattr(mcp.repositories, "list_workbench_mcp_tools", fake_list)
    return calls


def test_mcp_read_contract_projects_visible_tools_without_lifecycle_write(monkeypatch):
    calls = install_mcp_route_fakes(monkeypatch)
    client = TestClient(create_app())

    list_response = client.get("/api/mcp/", headers=headers())
    assert list_response.status_code == 200
    assert list_response.json() == {
        "servers": [
            {
                "name": "ragflow",
                "transport": "streamable_http",
                "enabled": True,
                "is_system": True,
                "can_edit": False,
                "allowed_roles": ["user"],
                "role_quotas": {},
                "created_at": None,
                "updated_at": "2026-06-23T00:00:00Z",
            }
        ],
        "total": 1,
        "skip": 0,
        "limit": 50,
    }

    detail_response = client.get("/api/mcp/ragflow", headers=headers())
    assert detail_response.status_code == 200
    assert detail_response.json()["name"] == "ragflow"
    assert detail_response.json()["can_edit"] is False

    tools_response = client.get("/api/mcp/ragflow/tools", headers=headers())
    assert tools_response.status_code == 200
    assert tools_response.json() == {
        "server_name": "ragflow",
        "tools": [
            {
                "name": "ragflow-knowledge-search",
                "description": "Search governed knowledge bases.",
                "parameters": [],
                "system_disabled": False,
                "user_disabled": False,
            }
        ],
        "count": 1,
    }
    assert calls[0][1]["tenant_id"] == "default"


def test_mcp_lifecycle_routes_are_admin_gated_then_fail_closed(monkeypatch):
    install_mcp_route_fakes(monkeypatch)
    client = TestClient(create_app())

    create_denied = client.post(
        "/api/mcp/",
        json={"name": "custom", "transport": "streamable_http"},
        headers=headers(),
    )
    assert create_denied.status_code == 403
    assert create_denied.json()["detail"] == "not_ai_admin"

    create_response = client.post(
        "/api/mcp/",
        json={"name": "custom", "transport": "streamable_http"},
        headers=headers(roles="admin"),
    )
    assert create_response.status_code == 409
    assert create_response.json()["detail"] == "mcp_lifecycle_contract_not_backed"

    toggle_response = client.patch("/api/mcp/ragflow/toggle", headers=headers(roles="admin"))
    assert toggle_response.status_code == 409
    assert toggle_response.json()["detail"] == "mcp_lifecycle_contract_not_backed"

    admin_create_response = client.post(
        "/api/admin/mcp/",
        json={"name": "system", "transport": "streamable_http"},
        headers=headers(roles="admin"),
    )
    assert admin_create_response.status_code == 409
    assert admin_create_response.json()["detail"] == "mcp_lifecycle_contract_not_backed"

    admin_update_response = client.put(
        "/api/admin/mcp/ragflow",
        json={"enabled": False},
        headers=headers(roles="admin"),
    )
    assert admin_update_response.status_code == 409
    assert admin_update_response.json()["detail"] == "mcp_lifecycle_contract_not_backed"


def test_mcp_lifecycle_route_matrix_fails_closed_after_admin_gate(monkeypatch):
    install_mcp_route_fakes(monkeypatch)
    client = TestClient(create_app())

    non_admin_invalid_name = client.put(
        "/api/mcp/bad!",
        json={"enabled": False},
        headers=headers(),
    )
    assert non_admin_invalid_name.status_code == 403
    assert non_admin_invalid_name.json()["detail"] == "not_ai_admin"

    routes = [
        ("post", "/api/mcp/import", {"servers": {}}),
        ("put", "/api/mcp/ragflow", {"enabled": False}),
        ("delete", "/api/mcp/ragflow", None),
        ("patch", "/api/mcp/ragflow/tools/ragflow-knowledge-search", {"enabled": False}),
        ("delete", "/api/admin/mcp/ragflow", None),
        ("post", "/api/admin/mcp/ragflow/promote", {"target_user_id": "ordinary"}),
        ("post", "/api/admin/mcp/ragflow/demote", {"target_user_id": "ordinary"}),
    ]
    for method, path, body in routes:
        if method == "delete":
            response = client.delete(path, headers=headers(roles="admin"))
        else:
            response = getattr(client, method)(
                path,
                json=body,
                headers=headers(roles="admin"),
            )
        assert response.status_code == 409
        assert response.json()["detail"] == "mcp_lifecycle_contract_not_backed"
