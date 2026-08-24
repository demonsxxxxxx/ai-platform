import pytest

from app.mcp import repository as mcp_repository


class _ServerCursor:
    def __init__(self, row):
        self.row = row

    async def fetchone(self):
        return self.row


class _ServerConnection:
    def __init__(self, row=None):
        self.row = row or {
            "name": "compatible-server",
            "transport": "streamable_http",
            "status": "active",
        }
        self.calls = []

    async def execute(self, sql, params):
        self.calls.append((" ".join(sql.split()), params))
        return _ServerCursor(self.row)


@pytest.mark.asyncio
async def test_lightweight_reference_uses_server_distribution_without_platform_tool_catalog(monkeypatch):
    async def active_distribution(conn, **kwargs):
        return {
            "capability_kind": "mcp_server",
            "capability_id": kwargs["capability_id"],
            "status": "active",
            "visible_to_user": True,
            "scope_mode": "allowlist",
            "department_ids": ["qa"],
            "allowed_roles": ["user"],
            "metadata_json": {},
        }

    repositories = mcp_repository._repositories()
    monkeypatch.setattr(repositories, "get_capability_distribution_row", active_distribution)
    conn = _ServerConnection()

    authorized = await mcp_repository.authorize_selected_chat_mcp_tools(
        conn,
        tenant_id="tenant-a",
        tool_ids=["compatible-server::unknown_tool"],
        principal_department_id="qa",
        principal_roles=["user"],
        is_admin=False,
        permissions=[],
    )
    with pytest.raises(repositories.RepositoryAuthorizationError):
        await mcp_repository.authorize_selected_chat_mcp_tools(
            conn,
            tenant_id="tenant-a",
            tool_ids=["compatible-server::unknown_tool"],
            principal_department_id="rd",
            principal_roles=["user"],
            is_admin=False,
            permissions=[],
        )

    assert [tool["tool_id"] for tool in authorized] == ["compatible-server::unknown_tool"]


@pytest.mark.asyncio
async def test_lightweight_reference_resolves_only_through_registered_server(monkeypatch):
    conn = _ServerConnection()

    tool = await mcp_repository.get_mcp_tool_registry_entry(
        conn,
        tenant_id="tenant-a",
        tool_id="compatible-server::unknown_tool",
    )

    assert conn.calls[0][1] == ("tenant-a", "compatible-server")
    assert "mcp_tool_catalog_entries" not in conn.calls[0][0]
    assert tool["tool_id"] == "compatible-server::unknown_tool"
    assert tool["allowed_tools"] == ["unknown_tool"]
    assert tool["write_capable"] is True
    assert tool["risk_level"] == "high"
    assert tool["discovery_state"] == "unresolved"
    assert "catalog_revision" not in tool
    assert "input_schema" not in tool


@pytest.mark.asyncio
async def test_invalid_reference_is_rejected_without_registry_or_catalog_lookup(monkeypatch):
    class MustNotRun:
        async def execute(self, *_args, **_kwargs):
            raise AssertionError("invalid references must fail before repository access")

    assert await mcp_repository.get_mcp_tool_registry_entry(
        MustNotRun(),
        tenant_id="tenant-a",
        tool_id="legacy-unqualified-tool",
    ) is None


def test_runtime_metadata_rejects_persisted_plaintext_endpoint():
    tool = {
        "tool_id": "compatible-server::unknown_tool",
        "server_id": "compatible-server",
        "transport_type": "streamable_http",
        "endpoint": "https://mcp.example/tools",
        "auth_mode": "none",
        "allowed_tools": ["unknown_tool"],
    }

    assert mcp_repository.mcp_runtime_metadata_usable(tool) is False
    assert mcp_repository.mcp_runtime_metadata_usable({**tool, "endpoint": ""}) is True


def test_only_code_owned_ragflow_builtin_has_legacy_mcp_tools_authority():
    builtin = {
        "tool_id": "ragflow-knowledge-search",
        "server_id": "ragflow",
        "transport_type": "http",
        "endpoint": "",
        "auth_mode": "platform-managed",
        "allowed_tools": ["ragflow_search"],
        "write_capable": False,
    }

    assert mcp_repository.is_trusted_builtin_mcp_tool(builtin) is True
    assert mcp_repository.mcp_runtime_metadata_usable(builtin) is True
    assert mcp_repository.is_trusted_builtin_mcp_tool({**builtin, "server_id": "forged-ragflow"}) is False
    authority_sql = mcp_repository.mcp_tool_tenant_authority_sql()
    assert "ragflow-knowledge-search" in authority_sql
    assert "mcp_tool_catalog_entries" not in authority_sql
    assert authority_sql.count("%s") == 1
