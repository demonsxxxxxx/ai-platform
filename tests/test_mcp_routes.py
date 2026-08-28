import base64
from contextlib import asynccontextmanager
import json
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest

from app.main import create_app
from app.auth import AuthPrincipal
from app.mcp.application.live_catalog import (
    LiveMcpCatalogService,
    LiveMcpServerResult,
    LiveMcpTool,
)
from app.mcp.domain.errors import McpRuntimeContextError
from app.mcp.domain.headers import normalize_static_mcp_headers
from app.mcp.domain.tool_references import build_mcp_tool_reference
from app.mcp.infrastructure.catalog import McpToolDiscoveryError
from app.mcp.infrastructure.runtime import (
    McpPrincipalJwtStore,
    open_mcp_server_credentials,
    seal_mcp_server_credentials,
)
from app.repositories import RepositoryConflictError, RepositoryNotFoundError
from app.settings import Settings


def _mcp_test_jwt(*, exp: int) -> str:
    def encode(value: dict[str, object]) -> str:
        return base64.urlsafe_b64encode(
            json.dumps(value, separators=(",", ":")).encode("utf-8")
        ).decode("ascii").rstrip("=")

    return f"{encode({'alg': 'RS256'})}.{encode({'sub': 'user-a', 'exp': exp})}.signature"


class _McpTestRedisBackend:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.expirations: dict[str, int] = {}

    def client(self):
        backend = self

        class Client:
            async def set(self, key, value, *, ex):
                backend.values[key] = str(value)
                backend.expirations[key] = int(ex)

            async def get(self, key):
                return backend.values.get(key)

            async def delete(self, key):
                backend.values.pop(key, None)
                backend.expirations.pop(key, None)

            async def aclose(self):
                return None

        return Client()


def _install_mcp_test_keyring(monkeypatch) -> None:
    settings = Settings(
        mcp_encryption_keys_json=json.dumps({"current": "11" * 32}),
        mcp_encryption_current_key_id="current",
    )
    monkeypatch.setattr("app.mcp.infrastructure.runtime.get_settings", lambda: settings)


@pytest.mark.asyncio
async def test_principal_jwt_store_encrypts_overwrites_and_uses_jwt_exp(monkeypatch):
    _install_mcp_test_keyring(monkeypatch)
    backend = _McpTestRedisBackend()
    monkeypatch.setattr("app.mcp.infrastructure.runtime.get_redis_client", backend.client)
    now = [2_000_000_000]
    store = McpPrincipalJwtStore(clock=lambda: now[0])
    principal = AuthPrincipal(
        tenant_id="tenant-a",
        user_id="user-a",
        display_name="user-a",
        source="company-login",
    )
    first = _mcp_test_jwt(exp=now[0] + 900)
    second = _mcp_test_jwt(exp=now[0] + 600)

    await store.put(principal, first)

    assert len(backend.values) == 1
    key = next(iter(backend.values))
    assert "tenant-a" not in key
    assert "user-a" not in key
    assert first not in backend.values[key]
    assert backend.expirations[key] == 900
    assert await store.get(principal) == first

    await store.put(principal, second)

    assert len(backend.values) == 1
    assert second not in backend.values[key]
    assert backend.expirations[key] == 600
    assert await store.get(principal) == second
    other = AuthPrincipal(
        tenant_id="tenant-a",
        user_id="user-b",
        display_name="user-b",
        source="company-login",
    )
    with pytest.raises(McpRuntimeContextError) as missing:
        await store.get(other)
    assert missing.value.code == "mcp_principal_jwt_missing"

    now[0] += 601
    with pytest.raises(McpRuntimeContextError) as expired:
        await store.get(principal)
    assert expired.value.code == "mcp_principal_jwt_expired"
    assert backend.values == {}


def test_mcp_server_credentials_are_encrypted_and_bound_to_tenant_server(monkeypatch):
    _install_mcp_test_keyring(monkeypatch)
    envelope = seal_mcp_server_credentials(
        tenant_id="tenant-a",
        server_id="gateway-a",
        endpoint="https://gateway.example/mcp",
        static_headers={"X-Static-Key": "private-value"},
    )

    assert "gateway.example" not in envelope
    assert "private-value" not in envelope
    assert open_mcp_server_credentials(
        tenant_id="tenant-a",
        server_id="gateway-a",
        envelope=envelope,
    ) == (
        "https://gateway.example/mcp",
        {"X-Static-Key": "private-value"},
    )
    with pytest.raises(McpRuntimeContextError) as mismatch:
        open_mcp_server_credentials(
            tenant_id="tenant-a",
            server_id="gateway-b",
            envelope=envelope,
        )
    assert mismatch.value.code == "mcp_server_credentials_invalid"


@pytest.mark.parametrize("header_name", ["JWT-Authorization", "jwt-authorization"])
def test_static_mcp_headers_cannot_override_dynamic_jwt_header(header_name):
    with pytest.raises(McpRuntimeContextError) as conflict:
        normalize_static_mcp_headers({header_name: "static-token"})
    assert conflict.value.code == "mcp_header_conflict"


class _LiveCatalogDiscovery:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def discover_definitions(
        self,
        endpoint,
        *,
        static_headers,
        jwt_authorization,
    ):
        self.calls.append(
            {
                "endpoint": endpoint,
                "static_headers": dict(static_headers),
                "jwt_authorization": jwt_authorization,
            }
        )
        return self.definitions

    definitions = (
        {
            "name": "pmm.query_projects",
            "description": "Query permitted projects.",
            "inputSchema": {"type": "object"},
            "annotations": {"readOnlyHint": True},
        },
    )


def _live_catalog_service(discovery):
    async def target_resolver(_tenant_id, _server_id):
        return SimpleNamespace(
            endpoint="https://gateway.example/mcp",
            static_headers={"X-Static": "configured"},
        )

    return LiveMcpCatalogService(
        target_resolver=target_resolver,
        discovery=discovery,
    )


@pytest.mark.asyncio
async def test_live_catalog_discovers_on_every_call_and_returns_immediate_changes():
    discovery = _LiveCatalogDiscovery()
    catalog = _live_catalog_service(discovery)
    first = await catalog.list_server_tools(
        tenant_id="default",
        user_id="alice",
        server_id="gateway",
        jwt="alice.jwt",
    )
    discovery.definitions = (
        {
            "name": "pmm.get_project",
            "description": "Get a permitted project.",
            "inputSchema": {"type": "object"},
        },
    )
    second = await catalog.list_server_tools(
        tenant_id="default",
        user_id="alice",
        server_id="gateway",
        jwt="alice.jwt",
    )

    assert first.tools[0].tool_id == "gateway::pmm.query_projects"
    assert first.tools[0].write_capable is False
    assert second.tools[0].tool_id == "gateway::pmm.get_project"
    assert [call["jwt_authorization"] for call in discovery.calls] == [
        "Bearer alice.jwt",
        "Bearer alice.jwt",
    ]
    assert all(
        call["static_headers"] == {"X-Static": "configured"}
        for call in discovery.calls
    )


@pytest.mark.asyncio
async def test_live_catalog_gateway_failure_does_not_return_previous_tools(monkeypatch):
    discovery = _LiveCatalogDiscovery()
    catalog = _live_catalog_service(discovery)
    first = await catalog.list_server_tools(
        tenant_id="default",
        user_id="alice",
        server_id="gateway",
        jwt="alice.jwt",
    )

    async def unavailable(*_args, **_kwargs):
        raise McpToolDiscoveryError("transport_failure")

    monkeypatch.setattr(discovery, "discover_definitions", unavailable)
    failed = await catalog.list_server_tools(
        tenant_id="default",
        user_id="alice",
        server_id="gateway",
        jwt="alice.jwt",
    )

    assert first.tools[0].tool_id == "gateway::pmm.query_projects"
    assert failed.tools == ()
    assert failed.unavailable_reason == "discovery_failed"


def headers(
    roles: str = "user",
    permissions: str = "skill:read,marketplace:read",
    *,
    department_id: str = "qa",
) -> dict[str, str]:
    return {
        "X-AI-User-ID": "ordinary",
        "X-AI-Roles": roles,
        "X-AI-Tenant-ID": "default",
        "X-AI-Department-ID": department_id,
        "X-AI-Permissions": permissions,
    }


def install_mcp_route_fakes(
    monkeypatch,
    *,
    seed_registry_ragflow: bool = True,
    distribution_rows: list[dict[str, object]] | None = None,
    tool_rows: list[dict[str, object]] | None = None,
) -> list[tuple[str, dict[str, object]]]:
    from app.routes import mcp

    class FakeConnection:
        pass

    @asynccontextmanager
    async def fake_transaction():
        yield FakeConnection()

    calls: list[tuple[str, dict[str, object]]] = []
    servers: dict[str, dict[str, object]] = {}
    if seed_registry_ragflow:
        servers["ragflow"] = {
            "name": "ragflow",
            "transport": "streamable_http",
            "status": "active",
            "is_system": True,
            "allowed_roles": ["user"],
            "role_quotas": {},
            "department_ids": [],
            "credential_state": "platform_managed",
            "created_at": None,
            "updated_at": "2026-06-23T00:00:00Z",
        }

    distributions: dict[str, dict[str, object]] = {
        name: {
            "capability_kind": "mcp_server",
            "capability_id": name,
            "status": "active",
            "visible_to_user": True,
            "scope_mode": "allowlist",
            "department_ids": list(row.get("department_ids") or []),
            "allowed_roles": list(row.get("allowed_roles") or []),
            "metadata_json": {},
        }
        for name, row in servers.items()
    }
    if distribution_rows is not None:
        distributions = {str(row["capability_id"]): dict(row) for row in distribution_rows}
    registry_tools = (tool_rows if tool_rows is not None else [
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
    ])
    for tool in registry_tools if tool_rows is not None else []:
        server_id = str(tool.get("server_id") or "")
        if server_id and server_id not in servers:
            servers[server_id] = {
                "name": server_id,
                "transport": "streamable_http",
                "status": "active",
                "is_system": False,
                "allowed_roles": ["user"],
                "role_quotas": {},
                "department_ids": [],
                "credential_state": "configured",
                "created_at": None,
                "updated_at": "2026-06-23T00:00:00Z",
            }
            distributions[server_id] = {
                "capability_kind": "mcp_server",
                "capability_id": server_id,
                "status": "active",
                "visible_to_user": True,
                "scope_mode": "allowlist",
                "department_ids": [],
                "allowed_roles": ["user"],
                "metadata_json": {},
            }

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
        return [dict(row) for row in registry_tools]

    async def fake_get_authorized_session(conn, **kwargs):
        calls.append(("get_authorized_session", dict(kwargs)))
        if kwargs["session_id"] != "session-1":
            return None
        selected = (
            [
                build_mcp_tool_reference(
                    str(registry_tools[0].get("server_id") or "gateway"),
                    str(
                        registry_tools[0].get("public_tool_name")
                        or registry_tools[0].get("tool_id")
                        or "tool"
                    ),
                )
            ]
            if registry_tools
            else []
        )
        return {
            "id": "session-1",
            "latest_run_input_json": {"input": {"mcp_tool_ids": selected}},
        }

    async def fake_list_servers(conn, *, tenant_id, include_disabled=True):
        calls.append(
            (
                "list_servers",
                {
                    "tenant_id": tenant_id,
                    "include_disabled": include_disabled,
                    "conn_type": type(conn).__name__,
                },
            )
        )
        rows = []
        for row in servers.values():
            if row.get("status") == "deleted":
                continue
            if not include_disabled and row.get("status") != "active":
                continue
            rows.append(dict(row))
        return rows

    async def fake_list_distributions(conn, *, tenant_id, capability_kind, include_disabled=True):
        calls.append(("list_distributions", {"tenant_id": tenant_id, "capability_kind": capability_kind}))
        return [dict(row) for row in distributions.values() if row["capability_kind"] == capability_kind]

    async def fake_get_distribution(conn, *, tenant_id, capability_kind, capability_id):
        calls.append(("get_distribution", {"tenant_id": tenant_id, "capability_kind": capability_kind, "capability_id": capability_id}))
        row = distributions.get(capability_id)
        return dict(row) if row and row["capability_kind"] == capability_kind else None

    async def fake_list_server_names(conn, *, tenant_id):
        calls.append(("list_server_names", {"tenant_id": tenant_id}))
        return [str(row["name"]) for row in servers.values() if row.get("status") != "deleted"]

    async def fake_upsert_server(conn, **kwargs):
        calls.append(("upsert_server", dict(kwargs)))
        existing = servers.get(kwargs["name"])
        if existing is not None and bool(existing.get("is_system")) != bool(kwargs["is_system"]):
            raise mcp.repositories.RepositoryConflictError("mcp_server_scope_conflict")
        server = {
            "name": kwargs["name"],
            "transport": kwargs["transport"],
            "status": "active" if kwargs["enabled"] else "disabled",
            "is_system": kwargs["is_system"],
            "allowed_roles": kwargs["allowed_roles"],
            "role_quotas": kwargs["role_quotas"],
            "department_ids": kwargs["department_ids"],
            "credential_state": "configured" if kwargs["credential_fingerprint"] else "not_configured",
            "credential_metadata": kwargs["credential_metadata"],
            "created_at": "2026-06-23T01:00:00Z",
            "updated_at": "2026-06-23T01:00:00Z",
        }
        servers[kwargs["name"]] = server
        return dict(server)

    async def fake_upsert_distribution(conn, **kwargs):
        calls.append(("upsert_distribution", dict(kwargs)))
        row = {
            "capability_kind": kwargs["capability_kind"],
            "capability_id": kwargs["capability_id"],
            "status": kwargs["status"],
            "visible_to_user": kwargs["visible_to_user"],
            "scope_mode": kwargs["scope_mode"],
            "department_ids": list(kwargs["department_ids"]),
            "allowed_roles": list(kwargs["allowed_roles"]),
            "metadata_json": dict(kwargs["metadata_json"]),
        }
        distributions[kwargs["capability_id"]] = row
        return dict(row)

    async def fake_set_distribution_status(conn, **kwargs):
        calls.append(("set_distribution_status", dict(kwargs)))
        row = dict(
            distributions.get(
                kwargs["capability_id"],
                {
                    "capability_kind": kwargs["capability_kind"],
                    "capability_id": kwargs["capability_id"],
                    "visible_to_user": True,
                    "scope_mode": "allowlist",
                    "department_ids": [],
                    "allowed_roles": [],
                    "metadata_json": {},
                },
            )
        )
        row["status"] = kwargs["status"]
        distributions[kwargs["capability_id"]] = row
        return dict(row)

    async def fake_archive_distribution(conn, **kwargs):
        calls.append(("archive_distribution", dict(kwargs)))
        row = dict(distributions.get(kwargs["capability_id"], {}))
        row.update(
            capability_kind=kwargs["capability_kind"],
            capability_id=kwargs["capability_id"],
            status="disabled",
            visible_to_user=False,
            metadata_json={"archived_at": "2026-07-15T00:00:00.000Z", "archived_by": kwargs["archived_by"]},
        )
        distributions[kwargs["capability_id"]] = row
        return dict(row)

    async def fake_toggle_server(conn, **kwargs):
        calls.append(("toggle_server", dict(kwargs)))
        server = dict(servers[kwargs["name"]])
        enabled = kwargs.get("enabled")
        if enabled is None:
            enabled = server.get("status") != "active"
        server["status"] = "active" if enabled else "disabled"
        server["updated_at"] = "2026-06-23T02:00:00Z"
        servers[kwargs["name"]] = server
        return dict(server)

    async def fake_delete_server(conn, **kwargs):
        calls.append(("delete_server", dict(kwargs)))
        server = dict(servers[kwargs["name"]])
        server["status"] = "deleted"
        server["updated_at"] = "2026-06-23T03:00:00Z"
        servers[kwargs["name"]] = server
        return dict(server)

    async def fake_record_credential(conn, **kwargs):
        calls.append(("record_credential", dict(kwargs)))
        if kwargs["server_name"] in servers:
            server = dict(servers[kwargs["server_name"]])
            server["credential_state"] = "configured" if kwargs["credential_fingerprint"] else "not_configured"
            server["credential_metadata"] = kwargs["metadata"]
            servers[kwargs["server_name"]] = server
        return {"id": "mcpcred-test", **kwargs}

    async def fake_ensure_user(conn, **kwargs):
        calls.append(("ensure_user", dict(kwargs)))
        return {"id": kwargs["user_id"], "tenant_id": kwargs["tenant_id"]}

    async def fake_append_audit_log(conn, **kwargs):
        calls.append(("audit", dict(kwargs)))
        return "aud-test"

    class FakePrincipalJwtStore:
        async def get(self, principal):
            calls.append(
                (
                    "principal_jwt",
                    {"tenant_id": principal.tenant_id, "user_id": principal.user_id},
                )
            )
            return "current-user.jwt"

    class FakeRouteLiveCatalog:
        async def list_server_tools(self, *, server_id, **_kwargs):
            tools = []
            for row in registry_tools:
                if str(row.get("server_id") or "") != server_id:
                    continue
                public_name = str(
                    row.get("public_tool_name") or row.get("tool_id") or ""
                )
                tools.append(
                    LiveMcpTool(
                        tool_id=build_mcp_tool_reference(server_id, public_name),
                        server_id=server_id,
                        public_tool_name=public_name,
                        label=public_name,
                        description=str(row.get("description") or ""),
                    )
                )
            return LiveMcpServerResult(server_id=server_id, tools=tuple(tools))

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr(
        mcp,
        "seal_mcp_server_credentials",
        lambda **_kwargs: "sealed-mcp-credential-envelope",
    )
    monkeypatch.setattr(mcp, "transaction", fake_transaction)
    monkeypatch.setattr(mcp.repositories, "list_workbench_mcp_tools", fake_list, raising=False)
    monkeypatch.setattr(
        mcp.repositories,
        "get_authorized_session",
        fake_get_authorized_session,
    )
    monkeypatch.setattr(mcp.repositories, "list_mcp_server_registry", fake_list_servers, raising=False)
    monkeypatch.setattr(mcp.repositories, "list_tenant_mcp_server_registry", fake_list_servers, raising=False)
    monkeypatch.setattr(mcp.repositories, "list_mcp_server_registry_names", fake_list_server_names, raising=False)
    monkeypatch.setattr(mcp.repositories, "list_capability_distribution_rows", fake_list_distributions, raising=False)
    monkeypatch.setattr(mcp.repositories, "get_capability_distribution_row", fake_get_distribution, raising=False)
    monkeypatch.setattr(mcp.repositories, "upsert_mcp_server_registry", fake_upsert_server, raising=False)
    monkeypatch.setattr(
        mcp.repositories,
        "upsert_capability_distribution_row",
        fake_upsert_distribution,
        raising=False,
    )
    monkeypatch.setattr(
        mcp.repositories,
        "set_capability_distribution_status",
        fake_set_distribution_status,
        raising=False,
    )
    monkeypatch.setattr(mcp.repositories, "archive_capability_distribution_row", fake_archive_distribution, raising=False)
    monkeypatch.setattr(mcp.repositories, "toggle_mcp_server_registry", fake_toggle_server, raising=False)
    monkeypatch.setattr(mcp.repositories, "delete_mcp_server_registry", fake_delete_server, raising=False)
    monkeypatch.setattr(mcp.repositories, "record_mcp_server_credential", fake_record_credential, raising=False)
    monkeypatch.setattr(mcp.mcp_repository, "list_mcp_server_registry", fake_list_servers)
    monkeypatch.setattr(mcp.mcp_repository, "upsert_mcp_server_registry", fake_upsert_server)
    monkeypatch.setattr(mcp.mcp_repository, "toggle_mcp_server_registry", fake_toggle_server)
    monkeypatch.setattr(mcp.mcp_repository, "delete_mcp_server_registry", fake_delete_server)
    monkeypatch.setattr(mcp.mcp_repository, "record_mcp_server_credential", fake_record_credential)
    monkeypatch.setattr(mcp.repositories, "ensure_user", fake_ensure_user)
    monkeypatch.setattr(mcp.repositories, "append_audit_log", fake_append_audit_log)
    monkeypatch.setattr(mcp, "get_mcp_principal_jwt_store", lambda: FakePrincipalJwtStore())
    monkeypatch.setattr(mcp, "LIVE_MCP_CATALOG", FakeRouteLiveCatalog())
    return calls


def test_chat_mcp_catalog_projects_only_canonical_public_fields_and_session_selection(monkeypatch):
    calls = install_mcp_route_fakes(
        monkeypatch,
        tool_rows=[
            {
                "tool_id": "tenant-search",
                "server_id": "private-server-production",
                "name": "https://user:credential@private.example/mcp",
                "description": "Bearer secret-token from /internal/private/path",
                "endpoint": "https://private.example/mcp",
                "credential_state": "platform-managed-secret",
            }
        ],
    )
    client = TestClient(create_app())

    response = client.get("/api/mcp/chat-tools?session_id=session-1", headers=headers())

    assert response.status_code == 200
    assert response.json() == {
        "tools": [
            {
                "tool_id": "private-server-production::tenant-search",
                "label": "tenant-search",
                "description": "Bearer secret-token from /internal/private/path",
                "category": "mcp",
                "server": "private-server-production",
                "cached": False,
            }
        ],
        "unavailable": [],
        "count": 1,
        "selected_mcp_tool_ids": ["private-server-production::tenant-search"],
    }
    encoded = response.text
    assert "private.example" not in encoded
    assert "platform-managed-secret" not in encoded
    assert ("principal_jwt", {"tenant_id": "default", "user_id": "ordinary"}) in calls


def test_chat_mcp_catalog_truthfully_returns_actionable_empty_selection(monkeypatch):
    install_mcp_route_fakes(monkeypatch, tool_rows=[])
    client = TestClient(create_app())

    response = client.get("/api/mcp/chat-tools?session_id=session-1", headers=headers())

    assert response.status_code == 200
    assert response.json() == {
        "tools": [],
        "unavailable": [],
        "count": 0,
        "selected_mcp_tool_ids": [],
    }


def test_chat_mcp_catalog_discovers_again_on_each_request(monkeypatch):
    from app.routes import mcp

    install_mcp_route_fakes(monkeypatch)
    discovery = _LiveCatalogDiscovery()
    monkeypatch.setattr(mcp, "LIVE_MCP_CATALOG", _live_catalog_service(discovery))
    client = TestClient(create_app())

    first = client.get("/api/mcp/chat-tools", headers=headers())
    discovery.definitions = (
        {
            "name": "pmm.get_project",
            "description": "Get a permitted project.",
            "inputSchema": {"type": "object"},
        },
    )
    second = client.get("/api/mcp/chat-tools", headers=headers())

    assert first.status_code == 200
    assert second.status_code == 200
    assert [tool["tool_id"] for tool in first.json()["tools"]] == [
        "ragflow::pmm.query_projects"
    ]
    assert [tool["tool_id"] for tool in second.json()["tools"]] == [
        "ragflow::pmm.get_project"
    ]
    assert len(discovery.calls) == 2
    assert all(
        call["jwt_authorization"] == "Bearer current-user.jwt"
        for call in discovery.calls
    )


def test_chat_mcp_catalog_keeps_healthy_server_when_another_is_unavailable(
    monkeypatch,
):
    from app.routes import mcp

    install_mcp_route_fakes(
        monkeypatch,
        seed_registry_ragflow=False,
        tool_rows=[
            {"server_id": "gateway-a", "tool_id": "unavailable-tool"},
            {"server_id": "gateway-b", "tool_id": "healthy-tool"},
        ],
    )

    class PartiallyUnavailableCatalog:
        async def list_server_tools(self, *, server_id, **_kwargs):
            if server_id == "gateway-a":
                return LiveMcpServerResult(server_id, (), "discovery_failed")
            tool = LiveMcpTool(
                tool_id=build_mcp_tool_reference(server_id, "healthy-tool"),
                server_id=server_id,
                public_tool_name="healthy-tool",
                label="healthy-tool",
                description="Healthy tool.",
            )
            return LiveMcpServerResult(server_id, (tool,))

    monkeypatch.setattr(mcp, "LIVE_MCP_CATALOG", PartiallyUnavailableCatalog())
    response = TestClient(create_app()).get(
        "/api/mcp/chat-tools",
        headers=headers(),
    )

    assert response.status_code == 200
    assert [tool["tool_id"] for tool in response.json()["tools"]] == [
        "gateway-b::healthy-tool"
    ]
    assert response.json()["unavailable"] == [
        {"label": "gateway-a", "reason": "discovery_failed"}
    ]


def test_explicit_catalog_sync_route_is_removed(monkeypatch):
    calls = install_mcp_route_fakes(monkeypatch)
    client = TestClient(create_app())

    response = client.post(
        "/api/mcp/ragflow/catalog/sync",
        json={"url": "https://mcp.example/tools"},
        headers=headers(roles="admin"),
    )

    assert response.status_code == 404
    assert "mcp.example" not in response.text
    assert not any(name == "catalog_sync" for name, _ in calls)


def test_mcp_read_contract_bounds_ordinary_catalog_and_keeps_tool_discovery(monkeypatch):
    calls = install_mcp_route_fakes(monkeypatch)
    client = TestClient(create_app())

    list_response = client.get("/api/mcp/", headers=headers())
    assert list_response.status_code == 200
    assert list_response.json() == {
        "servers": [
            {
                "name": "ragflow",
                "status": "active",
                "enabled": True,
                "can_edit": False,
            }
        ],
        "total": 1,
        "skip": 0,
        "limit": 50,
    }

    detail_response = client.get("/api/mcp/ragflow", headers=headers())
    assert detail_response.status_code == 200
    assert detail_response.json() == list_response.json()["servers"][0]

    export_response = client.get("/api/mcp/export", headers=headers())
    assert export_response.status_code == 200
    assert export_response.json() == {"servers": {"ragflow": detail_response.json()}}
    forbidden_fields = {
        "credential_state",
        "credential_metadata",
        "allowed_roles",
        "allowed_departments",
        "role_quotas",
        "created_at",
        "updated_at",
        "transport",
        "is_system",
    }
    assert forbidden_fields.isdisjoint(detail_response.json())

    tools_response = client.get("/api/mcp/ragflow/tools", headers=headers())
    assert tools_response.status_code == 200
    assert tools_response.json() == {
        "server_name": "ragflow",
        "tools": [
            {
                "name": "ragflow::ragflow-knowledge-search",
                "description": "Search governed knowledge bases.",
                "server": "ragflow",
                "cached": False,
                "parameters": [],
                "system_disabled": False,
                "user_disabled": False,
            }
        ],
        "count": 1,
        "unavailable_reason": None,
    }
    assert calls[0][1]["tenant_id"] == "default"


def _mcp_distribution(
    *,
    status: str = "active",
    visible_to_user: bool = True,
    department_ids: list[str] | None = None,
    allowed_roles: list[str] | None = None,
) -> dict[str, object]:
    return {
        "capability_kind": "mcp_server",
        "capability_id": "ragflow",
        "status": status,
        "visible_to_user": visible_to_user,
        "scope_mode": "allowlist",
        "department_ids": department_ids or [],
        "allowed_roles": allowed_roles or [],
        "metadata_json": {},
    }


def test_mcp_distribution_allows_matching_department_and_casefolded_exact_role(monkeypatch):
    install_mcp_route_fakes(
        monkeypatch,
        distribution_rows=[_mcp_distribution(department_ids=["qa"], allowed_roles=["qa-operator"])],
    )
    client = TestClient(create_app())
    authorized = headers(roles="QA-OPERATOR", department_id="qa")

    assert client.get("/api/mcp/", headers=authorized).json()["total"] == 1
    assert client.get("/api/mcp/ragflow", headers=authorized).status_code == 200
    assert client.get("/api/mcp/ragflow/tools", headers=authorized).status_code == 200


def test_mcp_distribution_omits_cross_department_and_returns_not_found_for_direct_reads(monkeypatch):
    install_mcp_route_fakes(monkeypatch, distribution_rows=[_mcp_distribution(department_ids=["qa"])])
    client = TestClient(create_app())
    unauthorized = headers(department_id="rd")

    assert client.get("/api/mcp/", headers=unauthorized).json()["servers"] == []
    assert client.get("/api/mcp/ragflow", headers=unauthorized).status_code == 404
    assert client.get("/api/mcp/ragflow/tools", headers=unauthorized).status_code == 404


def test_mcp_cross_tenant_server_and_tool_reads_fail_closed(monkeypatch):
    install_mcp_route_fakes(monkeypatch)

    async def tenant_scoped_servers(conn, *, tenant_id, include_disabled=True):
        if tenant_id != "default":
            return []
        return [
            {
                "name": "ragflow",
                "transport": "streamable_http",
                "status": "active",
                "is_system": True,
            }
        ]

    monkeypatch.setattr(
        "app.routes.mcp.mcp_repository.list_mcp_server_registry",
        tenant_scoped_servers,
    )
    client = TestClient(create_app())
    foreign_headers = {**headers(), "X-AI-Tenant-ID": "tenant-b"}

    assert client.get("/api/mcp/", headers=foreign_headers).json()["servers"] == []
    assert client.get("/api/mcp/ragflow", headers=foreign_headers).status_code == 404
    assert client.get("/api/mcp/ragflow/tools", headers=foreign_headers).status_code == 404


def test_mcp_distribution_denies_role_hidden_disabled_and_missing_rows(monkeypatch):
    client = TestClient(create_app())
    cases = [
        [_mcp_distribution(allowed_roles=["qa_operator"])],
        [_mcp_distribution(visible_to_user=False)],
        [_mcp_distribution(status="disabled")],
        [],
    ]
    for distributions in cases:
        install_mcp_route_fakes(monkeypatch, distribution_rows=distributions)
        denied = headers(roles="viewer")
        assert client.get("/api/mcp/ragflow", headers=denied).status_code == 404
        assert client.get("/api/mcp/ragflow/tools", headers=denied).status_code == 404


def test_mcp_tool_discovery_uses_gateway_effective_catalog_not_local_tool_lifecycle(monkeypatch):
    install_mcp_route_fakes(
        monkeypatch,
        distribution_rows=[_mcp_distribution(department_ids=["qa"])],
        tool_rows=[
            {
                "tool_id": "ragflow-knowledge-search",
                "server_id": "ragflow",
                "name": "RAGFlow Search",
                "description": "Search governed knowledge bases.",
                "effective_status": "disabled",
                "status": "disabled",
                "visible_to_user": True,
                "write_capable": True,
                "risk_level": "high",
            }
        ],
    )
    client = TestClient(create_app())

    response = client.get("/api/mcp/ragflow/tools", headers=headers(department_id="qa"))
    assert response.status_code == 200
    assert [tool["name"] for tool in response.json()["tools"]] == [
        "ragflow::ragflow-knowledge-search"
    ]


def test_mcp_tool_discovery_leaves_tool_risk_and_write_acl_to_gateway(monkeypatch):
    install_mcp_route_fakes(
        monkeypatch,
        distribution_rows=[_mcp_distribution(department_ids=["qa"])],
        tool_rows=[
            {
                "tool_id": "ragflow-knowledge-search",
                "server_id": "ragflow",
                "name": "RAGFlow Search",
                "description": "Search governed knowledge bases.",
                "effective_status": "active",
                "status": "active",
                "visible_to_user": True,
                "write_capable": True,
                "risk_level": "high",
            }
        ],
    )
    client = TestClient(create_app())

    response = client.get("/api/mcp/ragflow/tools", headers=headers(department_id="qa"))

    assert response.status_code == 200
    assert [tool["name"] for tool in response.json()["tools"]] == [
        "ragflow::ragflow-knowledge-search"
    ]


def test_mcp_admin_bypass_read_audits_target_scope(monkeypatch):
    calls = install_mcp_route_fakes(monkeypatch, distribution_rows=[_mcp_distribution(status="disabled", visible_to_user=False)])
    client = TestClient(create_app())

    response = client.get("/api/mcp/ragflow", headers=headers(roles="admin", department_id="platform"))

    assert response.status_code == 200
    audit = next(payload for name, payload in calls if name == "audit")
    assert audit["action"] == "capability_distribution.admin_bypass"
    assert audit["target_type"] == "mcp_server"
    assert audit["target_id"] == "ragflow"
    assert audit["payload_json"]["admin_bypass"] is True
    assert audit["payload_json"]["decision_reason"] == "admin_bypass"


def test_mcp_response_projects_authoritative_distribution_over_registry_scope(monkeypatch):
    install_mcp_route_fakes(
        monkeypatch,
        distribution_rows=[
            _mcp_distribution(
                status="disabled",
                visible_to_user=False,
                department_ids=["rd"],
                allowed_roles=["reviewer"],
            )
        ],
    )
    client = TestClient(create_app())

    response = client.get(
        "/api/mcp/",
        headers=headers(roles="admin", department_id="platform"),
    )

    assert response.status_code == 200
    server = response.json()["servers"][0]
    assert server["status"] == "disabled"
    assert server["enabled"] is False
    assert server["visible_to_user"] is False
    assert server["allowed_roles"] == ["reviewer"]
    assert server["allowed_departments"] == ["rd"]


def test_mcp_admin_read_and_export_keep_redacted_governance_metadata(monkeypatch):
    install_mcp_route_fakes(
        monkeypatch,
        distribution_rows=[_mcp_distribution(department_ids=["rd"], allowed_roles=["reviewer"])],
    )
    client = TestClient(create_app())
    admin_headers = headers(roles="admin", department_id="platform")

    detail_response = client.get("/api/mcp/ragflow", headers=admin_headers)
    export_response = client.get("/api/mcp/export", headers=admin_headers)

    assert detail_response.status_code == 200
    assert detail_response.json()["can_edit"] is True
    assert detail_response.json()["allowed_roles"] == ["reviewer"]
    assert detail_response.json()["allowed_departments"] == ["rd"]
    assert detail_response.json()["credential_state"] == "platform_managed"
    assert "credential_metadata" not in export_response.json()["servers"]["ragflow"]


def test_authorized_mcp_registration_entries_exclude_denied_parent_servers():
    from app.routes import mcp

    entries = [
        {"tool_id": "qa-tool", "server_id": "qa-server", "effective_status": "active"},
        {"tool_id": "rd-tool", "server_id": "rd-server", "effective_status": "active"},
    ]
    distributions = {
        "qa-server": _mcp_distribution(department_ids=["qa"]) | {"capability_id": "qa-server"},
        "rd-server": _mcp_distribution(department_ids=["rd"]) | {"capability_id": "rd-server"},
    }
    principal = mcp.AuthPrincipal(
        tenant_id="default",
        user_id="ordinary",
        display_name="ordinary",
        department_id="qa",
        roles=["user"],
        permissions=[],
    )

    assert mcp.authorized_mcp_registration_entries(
        principal=principal,
        registry_entries=entries,
        distributions_by_server=distributions,
    ) == [entries[0]]


def test_authorized_mcp_registration_entries_require_active_parent_server():
    from app.routes import mcp

    entry = {
        "tool_id": "qa-tool",
        "server_id": "qa-server",
        "status": "active",
        "effective_status": "active",
        "server_status": "disabled",
    }
    distribution = _mcp_distribution(department_ids=["qa"]) | {"capability_id": "qa-server"}
    principal = mcp.AuthPrincipal(
        tenant_id="default",
        user_id="ordinary",
        display_name="ordinary",
        department_id="qa",
        roles=["user"],
        permissions=[],
    )

    assert mcp.authorized_mcp_registration_entries(
        principal=principal,
        registry_entries=[entry],
        distributions_by_server={"qa-server": distribution},
    ) == []


def test_mcp_lifecycle_routes_are_admin_gated_then_backed_with_redacted_credentials(monkeypatch):
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
        json={
            "name": "custom",
            "transport": "streamable_http",
            "enabled": True,
            "url": "https://mcp.example/sse?token=plain-secret",
            "headers": {"Authorization": "Bearer plain-secret"},
            "env_keys": ["MCP_SECRET"],
            "allowed_roles": [" QA-Operator ", "qa-operator"],
            "department_ids": [" QA ", "qa"],
        },
        headers=headers(roles="admin"),
    )
    assert create_response.status_code == 200
    created = create_response.json()
    assert created["name"] == "custom"
    assert created["enabled"] is True
    assert created["can_edit"] is True
    assert created["is_system"] is False
    assert created["allowed_roles"] == ["qa-operator"]
    assert created["allowed_departments"] == ["QA", "qa"]
    assert created["credential_state"] == "configured"
    assert "plain-secret" not in str(created)
    assert "Bearer" not in str(created)
    assert "https://mcp.example" not in str(created)

    toggle_response = client.patch("/api/mcp/ragflow/toggle", headers=headers(roles="admin"))
    assert toggle_response.status_code == 200
    assert toggle_response.json()["server"]["enabled"] is False

    admin_create_response = client.post(
        "/api/admin/mcp/",
        json={"name": "system", "transport": "streamable_http", "enabled": True},
        headers=headers(roles="admin"),
    )
    assert admin_create_response.status_code == 200
    assert admin_create_response.json()["is_system"] is True

    admin_update_response = client.put(
        "/api/admin/mcp/ragflow",
        json={"enabled": False, "allowed_roles": ["admin"], "department_ids": ["qa"]},
        headers=headers(roles="admin"),
    )
    assert admin_update_response.status_code == 200
    assert admin_update_response.json()["enabled"] is False


def test_shared_mcp_update_and_toggle_require_ai_admin(monkeypatch):
    calls = install_mcp_route_fakes(monkeypatch)
    client = TestClient(create_app())

    update_response = client.put(
        "/api/mcp/ragflow",
        json={"enabled": False, "transport": "streamable_http"},
        headers=headers(),
    )
    toggle_response = client.patch(
        "/api/mcp/ragflow/toggle",
        json={"enabled": False},
        headers=headers(),
    )

    assert update_response.status_code == 403
    assert update_response.json()["detail"] == "not_ai_admin"
    assert toggle_response.status_code == 403
    assert toggle_response.json()["detail"] == "not_ai_admin"
    assert not any(name in {"upsert_server", "toggle_server"} for name, _ in calls)


def test_shared_mcp_lifecycle_writes_authoritative_distribution(monkeypatch):
    calls = install_mcp_route_fakes(monkeypatch, seed_registry_ragflow=False)
    client = TestClient(create_app())

    created = client.post(
        "/api/mcp/",
        json={
            "name": "scoped",
            "enabled": False,
            "allowed_roles": ["qa_operator"],
            "department_ids": ["qa"],
        },
        headers=headers(roles="admin"),
    )
    updated = client.put(
        "/api/mcp/scoped",
        json={
            "enabled": True,
            "allowed_roles": ["reviewer"],
            "department_ids": ["rd"],
        },
        headers=headers(roles="admin"),
    )
    toggled = client.patch(
        "/api/mcp/scoped/toggle",
        json={"enabled": False},
        headers=headers(roles="admin"),
    )
    deleted = client.delete("/api/mcp/scoped", headers=headers(roles="admin"))

    assert [response.status_code for response in (created, updated, toggled, deleted)] == [200, 200, 200, 200]
    distribution_writes = [
        call
        for call in calls
        if call[0] in {"upsert_distribution", "set_distribution_status", "archive_distribution"}
    ]
    assert [name for name, _ in distribution_writes] == [
        "upsert_distribution",
        "upsert_distribution",
        "set_distribution_status",
        "archive_distribution",
    ]
    assert distribution_writes[0][1]["status"] == "disabled"
    assert distribution_writes[0][1]["allowed_roles"] == ["qa_operator"]
    assert distribution_writes[0][1]["department_ids"] == ["qa"]
    assert distribution_writes[1][1]["status"] == "active"
    assert distribution_writes[1][1]["allowed_roles"] == ["reviewer"]
    assert distribution_writes[1][1]["department_ids"] == ["rd"]
    assert distribution_writes[2][1]["status"] == "disabled"
    assert distribution_writes[3][1] == {
        "tenant_id": "default",
        "capability_kind": "mcp_server",
        "capability_id": "scoped",
        "archived_by": "ordinary",
    }
    assert sum(name == "set_distribution_status" for name, _ in calls) == 1


@pytest.mark.parametrize("path", ["/api/mcp/ragflow", "/api/admin/mcp/ragflow"])
def test_mcp_delete_routes_archive_distribution_and_repair_actor_evidence(monkeypatch, path):
    calls = install_mcp_route_fakes(
        monkeypatch,
        distribution_rows=[
            {
                "capability_kind": "mcp_server",
                "capability_id": "ragflow",
                "status": "disabled",
                "visible_to_user": False,
                "scope_mode": "allowlist",
                "department_ids": [],
                "allowed_roles": [],
                "metadata_json": {
                    "archived_at": "2026-07-15T00:00:00.000Z",
                    "archived_by": "   ",
                },
            }
        ],
    )
    client = TestClient(create_app())

    response = client.delete(path, headers=headers(roles="admin"))

    assert response.status_code == 200
    assert response.json()["visible_to_user"] is False
    archive_calls = [payload for name, payload in calls if name == "archive_distribution"]
    assert archive_calls == [
        {
            "tenant_id": "default",
            "capability_kind": "mcp_server",
            "capability_id": "ragflow",
            "archived_by": "ordinary",
        }
    ]
    assert not any(name == "set_distribution_status" for name, _ in calls)
    assert any(
        name == "audit" and payload["action"].endswith("mcp.server.deleted")
        for name, payload in calls
    )


@pytest.mark.parametrize(
    ("method", "path", "payload"),
    [
        (
            "post",
            "/api/admin/mcp/",
            {
                "name": "blank-role",
                "transport": "streamable_http",
                "allowed_roles": [""],
            },
        ),
        (
            "put",
            "/api/mcp/ragflow",
            {
                "enabled": True,
                "allowed_roles": ["   "],
            },
        ),
    ],
)
def test_mcp_lifecycle_rejects_blank_roles_before_repository_writes(monkeypatch, method, path, payload):
    calls = install_mcp_route_fakes(monkeypatch)
    client = TestClient(create_app())

    response = getattr(client, method)(path, json=payload, headers=headers(roles="admin"))

    assert response.status_code == 422
    assert not any(
        name in {"upsert_server", "upsert_distribution", "record_credential", "audit"}
        for name, _ in calls
    )


def test_mcp_lifecycle_validation_errors_do_not_echo_secret_inputs(monkeypatch):
    install_mcp_route_fakes(monkeypatch)
    client = TestClient(create_app())

    response = client.post(
        "/api/admin/mcp/",
        json={
            "name": "secret-invalid",
            "transport": "streamable_http",
            "headers": {"Authorization": 123},
            "unexpected_secret": "raw-secret",
        },
        headers=headers(roles="admin"),
    )

    assert response.status_code == 422
    serialized = str(response.json())
    assert "raw-secret" not in serialized
    assert "Authorization" not in serialized
    assert "123" not in serialized
    assert "unexpected_secret" in serialized


def test_mcp_lifecycle_redacts_url_userinfo_before_persistence(monkeypatch):
    calls = install_mcp_route_fakes(monkeypatch)
    client = TestClient(create_app())

    response = client.post(
        "/api/admin/mcp/",
        json={
            "name": "userinfo",
            "transport": "streamable_http",
            "url": "https://user:raw-secret@mcp.example:8443/sse?token=raw-query-secret",
        },
        headers=headers(roles="admin"),
    )

    assert response.status_code == 200
    serialized = str(response.json())
    assert "raw-secret" not in serialized
    assert "raw-query-secret" not in serialized
    upsert_call = next(payload for name, payload in calls if name == "upsert_server")
    assert upsert_call["endpoint_redacted"] == ""
    assert "raw-secret" not in str(upsert_call)
    assert "raw-query-secret" not in str(upsert_call)


def test_mcp_lifecycle_does_not_implicitly_promote_or_demote_by_reupsert(monkeypatch):
    install_mcp_route_fakes(monkeypatch, seed_registry_ragflow=False)
    client = TestClient(create_app())

    admin_create = client.post(
        "/api/admin/mcp/",
        json={"name": "fixed-scope", "transport": "streamable_http"},
        headers=headers(roles="admin"),
    )
    assert admin_create.status_code == 200
    assert admin_create.json()["is_system"] is True

    public_reupsert = client.post(
        "/api/mcp/",
        json={"name": "fixed-scope", "transport": "streamable_http"},
        headers=headers(roles="admin"),
    )
    assert public_reupsert.status_code == 409
    assert public_reupsert.json()["detail"] == "mcp_server_scope_conflict"

    public_create = client.post(
        "/api/mcp/",
        json={"name": "user-scope", "transport": "streamable_http"},
        headers=headers(roles="admin"),
    )
    assert public_create.status_code == 200
    assert public_create.json()["is_system"] is False

    admin_reupsert = client.put(
        "/api/admin/mcp/user-scope",
        json={"transport": "streamable_http"},
        headers=headers(roles="admin"),
    )
    assert admin_reupsert.status_code == 409
    assert admin_reupsert.json()["detail"] == "mcp_server_scope_conflict"


def test_mcp_lifecycle_audit_and_repository_payloads_never_include_raw_credentials(monkeypatch):
    calls = install_mcp_route_fakes(monkeypatch)
    client = TestClient(create_app())

    response = client.post(
        "/api/admin/mcp/",
        json={
            "name": "secreted",
            "transport": "streamable_http",
            "url": "https://mcp.example/sse?api_key=raw-secret",
            "headers": {"X-Api-Key": "raw-secret"},
            "command": "run --token raw-secret",
            "env_keys": ["RAW_SECRET"],
        },
        headers=headers(roles="admin"),
    )

    assert response.status_code == 200
    serialized_response = str(response.json())
    assert "raw-secret" not in serialized_response
    assert "run --token" not in serialized_response
    lifecycle_calls = [payload for name, payload in calls if name in {"upsert_server", "record_credential", "audit"}]
    assert lifecycle_calls
    serialized_calls = str(lifecycle_calls)
    assert "raw-secret" not in serialized_calls
    assert "run --token" not in serialized_calls
    assert "X-Api-Key" not in serialized_calls


def test_mcp_directory_filters_servers_by_principal_department(monkeypatch):
    install_mcp_route_fakes(
        monkeypatch,
        distribution_rows=[
            _mcp_distribution(),
            _mcp_distribution(department_ids=["qa"]) | {"capability_id": "qa-only"},
        ],
    )
    client = TestClient(create_app())

    create_response = client.post(
        "/api/admin/mcp/",
        json={
            "name": "qa-only",
            "transport": "streamable_http",
            "enabled": True,
            "department_ids": ["qa"],
        },
        headers=headers(roles="admin", department_id="qa"),
    )
    assert create_response.status_code == 200

    qa_response = client.get("/api/mcp/", headers=headers(department_id="qa"))
    assert qa_response.status_code == 200
    assert {server["name"] for server in qa_response.json()["servers"]} == {"qa-only", "ragflow"}

    rd_response = client.get("/api/mcp/", headers=headers(department_id="rd"))
    assert rd_response.status_code == 200
    assert {server["name"] for server in rd_response.json()["servers"]} == {"ragflow"}


def test_mcp_department_limited_registry_override_suppresses_legacy_tool_fallback(monkeypatch):
    install_mcp_route_fakes(monkeypatch, seed_registry_ragflow=False)
    client = TestClient(create_app())

    create_response = client.post(
        "/api/admin/mcp/",
        json={
            "name": "ragflow",
            "transport": "streamable_http",
            "enabled": True,
            "department_ids": ["qa"],
        },
        headers=headers(roles="admin", department_id="qa"),
    )
    assert create_response.status_code == 200

    qa_response = client.get("/api/mcp/", headers=headers(department_id="qa"))
    assert qa_response.status_code == 200
    assert {server["name"] for server in qa_response.json()["servers"]} == {"ragflow"}

    rd_response = client.get("/api/mcp/", headers=headers(department_id="rd"))
    assert rd_response.status_code == 200
    assert "ragflow" not in {server["name"] for server in rd_response.json()["servers"]}

    rd_detail = client.get("/api/mcp/ragflow", headers=headers(department_id="rd"))
    assert rd_detail.status_code == 404
    assert rd_detail.json()["detail"] == "mcp_server_not_found"

    rd_tools = client.get("/api/mcp/ragflow/tools", headers=headers(department_id="rd"))
    assert rd_tools.status_code == 404
    assert rd_tools.json()["detail"] == "mcp_server_not_found"


def test_mcp_directory_merges_registry_servers_with_platform_registered_tools(monkeypatch):
    install_mcp_route_fakes(monkeypatch, seed_registry_ragflow=False)
    client = TestClient(create_app())

    create_response = client.post(
        "/api/admin/mcp/",
        json={
            "name": "custom",
            "transport": "streamable_http",
            "enabled": True,
        },
        headers=headers(roles="admin"),
    )
    assert create_response.status_code == 200

    list_response = client.get("/api/mcp/", headers=headers())
    assert list_response.status_code == 200
    assert {server["name"] for server in list_response.json()["servers"]} == {"custom"}

    detail_response = client.get("/api/mcp/ragflow", headers=headers())
    assert detail_response.status_code == 404


def test_mcp_lifecycle_delete_and_empty_credential_update_clear_public_state(monkeypatch):
    calls = install_mcp_route_fakes(monkeypatch, seed_registry_ragflow=False)
    client = TestClient(create_app())

    create_response = client.post(
        "/api/admin/mcp/",
        json={
            "name": "clearable",
            "transport": "streamable_http",
            "enabled": True,
            "headers": {"Authorization": "Bearer raw-secret"},
        },
        headers=headers(roles="admin"),
    )
    assert create_response.status_code == 200
    assert create_response.json()["credential_state"] == "configured"

    update_response = client.put(
        "/api/admin/mcp/clearable",
        json={"enabled": True, "transport": "streamable_http"},
        headers=headers(roles="admin"),
    )
    assert update_response.status_code == 200
    assert update_response.json()["credential_state"] == "not_configured"
    credential_calls = [payload for name, payload in calls if name == "record_credential"]
    assert credential_calls[-1]["credential_fingerprint"] == ""
    assert credential_calls[-1]["metadata"] == {}

    delete_response = client.delete("/api/admin/mcp/clearable", headers=headers(roles="admin"))
    assert delete_response.status_code == 200
    assert delete_response.json()["enabled"] is False

    list_response = client.get("/api/mcp/", headers=headers())
    assert list_response.status_code == 200
    assert "clearable" not in {server["name"] for server in list_response.json()["servers"]}


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
        ("patch", "/api/mcp/ragflow/tools/ragflow-knowledge-search", {"enabled": False}),
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


@pytest.mark.parametrize(
    ("method", "path", "payload"),
    [
        ("patch", "/api/mcp/ragflow/toggle", {"enabled": False}),
        ("delete", "/api/mcp/ragflow", None),
        ("delete", "/api/admin/mcp/ragflow", None),
    ],
    ids=["toggle", "delete", "admin-delete"],
)
@pytest.mark.parametrize(
    ("error", "expected_status", "expected_detail"),
    [
        (RepositoryNotFoundError("capability_distribution_not_found"), 404, "capability_distribution_not_found"),
        (RepositoryConflictError("capability_distribution_archived"), 409, "capability_distribution_archived"),
        (RepositoryConflictError("unexpected_conflict"), 409, "mcp_server_conflict"),
    ],
    ids=["missing", "archived", "other-conflict"],
)
def test_mcp_status_mutations_map_distribution_errors_without_audit_or_partial_commit(
    monkeypatch,
    method,
    path,
    payload,
    error,
    expected_status,
    expected_detail,
):
    from app.routes import mcp

    calls = install_mcp_route_fakes(monkeypatch)
    transaction_events = []

    @asynccontextmanager
    async def recording_transaction():
        transaction_events.append("enter")
        try:
            yield object()
        except Exception:
            transaction_events.append("rollback")
            raise
        else:
            transaction_events.append("commit")

    async def fail_distribution_mutation(conn, **kwargs):
        seam = "archive_distribution_failed" if method == "delete" else "set_distribution_status_failed"
        calls.append((seam, dict(kwargs)))
        raise error

    monkeypatch.setattr(mcp, "transaction", recording_transaction)
    mutation_name = (
        "archive_capability_distribution_row" if method == "delete" else "set_capability_distribution_status"
    )
    monkeypatch.setattr(mcp.repositories, mutation_name, fail_distribution_mutation)
    client = TestClient(create_app())

    response = (
        client.delete(path, headers=headers(roles="admin"))
        if method == "delete"
        else client.patch(path, json=payload, headers=headers(roles="admin"))
    )

    assert (response.status_code, response.json()["detail"]) == (expected_status, expected_detail)
    assert transaction_events == ["enter", "rollback"]
    assert not any(name == "audit" for name, _ in calls)
    assert [name for name, _ in calls][-1] == (
        "archive_distribution_failed" if method == "delete" else "set_distribution_status_failed"
    )
