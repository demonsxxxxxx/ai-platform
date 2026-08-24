import pytest

from app.mcp import repository as mcp_repository


@pytest.mark.asyncio
async def test_lightweight_reference_uses_server_distribution_without_platform_tool_catalog(monkeypatch):
    async def get_server(conn, *, tenant_id, name):
        assert tenant_id == "tenant-a"
        assert name == "compatible-server"
        return {"name": name, "transport": "streamable_http", "status": "active"}

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
    monkeypatch.setattr(repositories, "get_mcp_server_registry_entry", get_server)
    monkeypatch.setattr(repositories, "get_capability_distribution_row", active_distribution)

    authorized = await mcp_repository.authorize_selected_chat_mcp_tools(
        object(),
        tenant_id="tenant-a",
        tool_ids=["compatible-server::unknown_tool"],
        principal_department_id="qa",
        principal_roles=["user"],
        is_admin=False,
        permissions=[],
    )
    with pytest.raises(repositories.RepositoryAuthorizationError):
        await mcp_repository.authorize_selected_chat_mcp_tools(
            object(),
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
    calls = []

    async def get_server(conn, *, tenant_id, name):
        calls.append((tenant_id, name))
        return {"name": name, "transport": "streamable_http", "status": "active"}

    monkeypatch.setattr(
        mcp_repository._repositories(),
        "get_mcp_server_registry_entry",
        get_server,
    )

    tool = await mcp_repository.get_mcp_tool_registry_entry(
        object(),
        tenant_id="tenant-a",
        tool_id="compatible-server::unknown_tool",
    )

    assert calls == [("tenant-a", "compatible-server")]
    assert tool["tool_id"] == "compatible-server::unknown_tool"
    assert tool["allowed_tools"] == ["unknown_tool"]
    assert tool["write_capable"] is True
    assert tool["risk_level"] == "high"
    assert tool["discovery_state"] == "unresolved"
    assert "catalog_revision" not in tool
    assert "input_schema" not in tool


@pytest.mark.asyncio
async def test_invalid_reference_is_rejected_without_registry_or_catalog_lookup(monkeypatch):
    async def must_not_run(*args, **kwargs):
        raise AssertionError("invalid references must fail before repository access")

    monkeypatch.setattr(
        mcp_repository._repositories(),
        "get_mcp_server_registry_entry",
        must_not_run,
    )

    assert await mcp_repository.get_mcp_tool_registry_entry(
        object(),
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
    assert "%s" not in authority_sql
