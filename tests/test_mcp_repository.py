import pytest

from app import repositories
from app.mcp.infrastructure import postgres as mcp_repository


class _Cursor:
    def __init__(self, row=None, rows=()):
        self._row = row
        self._rows = rows

    async def fetchone(self):
        return self._row

    async def fetchall(self):
        return self._rows


@pytest.mark.asyncio
async def test_dynamic_tool_reference_resolves_only_registered_server():
    class Connection:
        async def execute(self, query, params):
            assert "from mcp_servers" in query
            assert "mcp_tools" not in query
            assert params == ("tenant-a", "gateway")
            return _Cursor(
                {"name": "gateway", "transport": "streamable_http", "status": "active"}
            )

    entry = await mcp_repository.get_mcp_tool_registry_entry(
        Connection(),
        tenant_id="tenant-a",
        tool_id="gateway::pmm.query_projects",
    )

    assert entry == {
        "tool_id": "gateway::pmm.query_projects",
        "server_id": "gateway",
        "name": "pmm.query_projects",
        "description": "",
        "transport_type": "streamable_http",
        "endpoint": "",
        "auth_mode": "none",
        "allowed_tools": ["pmm.query_projects"],
        "registry_status": "active",
        "policy_status": "active",
        "server_status": "active",
        "effective_status": "active",
        "visible_to_user": True,
        "write_capable": True,
        "risk_level": "high",
        "discovery_state": "unresolved",
    }


@pytest.mark.asyncio
async def test_invalid_dynamic_reference_never_queries_database():
    class Connection:
        async def execute(self, *_args, **_kwargs):
            raise AssertionError("invalid references must fail before database access")

    assert await mcp_repository.get_mcp_tool_registry_entry(
        Connection(),
        tenant_id="tenant-a",
        tool_id="unqualified-tool",
    ) is None


@pytest.mark.asyncio
async def test_builtin_ragflow_keeps_strict_code_owned_registry_path(monkeypatch):
    row = {
        "tool_id": "ragflow-knowledge-search",
        "server_id": "ragflow",
        "name": "RAGFlow Search",
        "description": "Search governed knowledge.",
        "transport_type": "http",
        "endpoint": "",
        "auth_mode": "platform-managed",
        "allowed_tools": ["ragflow_search"],
        "registry_status": "active",
        "policy_status": "active",
        "registry_write_capable": False,
        "policy_write_capable": False,
        "registry_risk_level": "low",
        "policy_risk_level": "low",
        "registry_visible_to_user": True,
        "policy_visible_to_user": True,
    }

    class Connection:
        async def execute(self, query, params):
            assert "from mcp_tools" in query
            assert "ragflow-knowledge-search" in query
            assert params == (
                "tenant-a",
                "ragflow-knowledge-search",
                "tenant-a",
            )
            return _Cursor(row)

    monkeypatch.setattr(
        repositories,
        "_tool_policy_projection",
        lambda value, *, tenant_id: {
            **value,
            "tenant_id": tenant_id,
            "effective_status": "active",
            "write_capable": False,
            "risk_level": "low",
            "visible_to_user": True,
        },
    )
    entry = await repositories.get_mcp_tool_registry_entry(
        Connection(),
        tenant_id="tenant-a",
        tool_id="ragflow-knowledge-search",
    )

    assert entry is not None
    assert mcp_repository.is_trusted_builtin_mcp_tool(entry)
    assert mcp_repository.mcp_runtime_metadata_usable(entry)


@pytest.mark.asyncio
async def test_runtime_target_requires_active_server_and_distribution():
    class Connection:
        async def execute(self, query, params):
            assert "credential_envelope" in query
            assert "tenant_capability_distributions" in query
            assert "distributions.status = 'active'" in query
            assert params == ("tenant-a", "gateway")
            return _Cursor(
                {"transport": "streamable_http", "credential_envelope": "sealed"}
            )

    assert await mcp_repository.get_mcp_server_runtime_target(
        Connection(),
        tenant_id="tenant-a",
        server_name="gateway",
    ) == {"transport": "streamable_http", "credential_envelope": "sealed"}


def test_only_code_owned_ragflow_has_legacy_mcp_tools_authority():
    authority_sql = mcp_repository.mcp_tool_tenant_authority_sql()
    assert "ragflow-knowledge-search" in authority_sql
    assert "ragflow_search" in authority_sql
    assert "mcp_tool_catalog_entries" not in authority_sql

    builtin = {
        "tool_id": "ragflow-knowledge-search",
        "server_id": "ragflow",
        "transport_type": "http",
        "endpoint": "",
        "auth_mode": "platform-managed",
        "allowed_tools": ["ragflow_search"],
        "write_capable": False,
    }
    assert mcp_repository.is_trusted_builtin_mcp_tool(builtin)
    assert not mcp_repository.is_trusted_builtin_mcp_tool(
        {**builtin, "server_id": "forged"}
    )
