import json
from pathlib import Path

import pytest

from app import repositories
from app.mcp.infrastructure import postgres as mcp_repository
from app.platform.postgres.errors import RepositoryConflictError, RepositoryNotFoundError


class _Cursor:
    def __init__(self, row=None, rows=()):
        self._row = row
        self._rows = rows

    async def fetchone(self):
        return self._row

    async def fetchall(self):
        return self._rows


@pytest.mark.asyncio
async def test_dynamic_server_registry_projection_excludes_legacy_catalog_state():
    row = {
        "tenant_id": "tenant-a",
        "name": "gateway",
        "transport": "streamable_http",
        "endpoint_redacted": "",
        "status": "active",
        "is_system": False,
        "allowed_roles": ["qa"],
        "role_quotas_json": {"qa": {"daily_limit": 3}},
        "department_ids": ["qa"],
        "credential_state": "configured",
        "credential_metadata_json": {"header_names": ["Authorization"]},
        "catalog_generation": 7,
        "catalog_status": "legacy",
        "created_at": "2026-06-23T00:00:00Z",
        "updated_at": "2026-06-23T00:00:00Z",
    }

    class Connection:
        async def execute(self, query, params):
            assert "from mcp_servers" in query
            assert "catalog_" not in query
            assert params == ("tenant-a", "qa", False)
            return _Cursor(rows=(row,))

    assert await mcp_repository.list_mcp_server_registry(
        Connection(),
        tenant_id="tenant-a",
        department_id="qa",
        include_disabled=False,
    ) == [
        {
            "tenant_id": "tenant-a",
            "name": "gateway",
            "transport": "streamable_http",
            "endpoint_redacted": "",
            "status": "active",
            "is_system": False,
            "allowed_roles": ["qa"],
            "role_quotas": {"qa": {"daily_limit": 3}},
            "department_ids": ["qa"],
            "credential_state": "configured",
            "credential_metadata": {"header_names": ["Authorization"]},
            "created_at": "2026-06-23T00:00:00Z",
            "updated_at": "2026-06-23T00:00:00Z",
        }
    ]


@pytest.mark.asyncio
async def test_dynamic_server_upsert_persists_no_endpoint_material():
    row = {
        "tenant_id": "tenant-a",
        "name": "gateway",
        "transport": "streamable_http",
        "endpoint_redacted": "",
        "status": "active",
        "is_system": False,
        "allowed_roles": ["qa"],
        "role_quotas_json": {},
        "department_ids": ["qa"],
        "credential_state": "configured",
        "credential_metadata_json": {"header_names": ["Authorization"]},
        "created_at": "2026-06-23T00:00:00Z",
        "updated_at": "2026-06-23T00:00:00Z",
    }

    class Connection:
        async def execute(self, query, params):
            assert "insert into mcp_servers" in query
            assert "where mcp_servers.is_system = excluded.is_system" in query
            assert params[1:5] == ("tenant-a", "gateway", "streamable_http", "")
            assert "https://mcp.example/sse" not in params
            assert "credential-sha" in params
            return _Cursor(row=row)

    result = await mcp_repository.upsert_mcp_server_registry(
        Connection(),
        tenant_id="tenant-a",
        name="gateway",
        transport="streamable_http",
        enabled=True,
        is_system=False,
        endpoint_redacted="https://mcp.example/sse",
        allowed_roles=["qa"],
        role_quotas={},
        department_ids=["qa"],
        credential_state="configured",
        credential_metadata={"header_names": ["Authorization"]},
        credential_fingerprint="credential-sha",
        updated_by="admin-a",
    )

    assert result["endpoint_redacted"] == ""
    assert result["name"] == "gateway"


@pytest.mark.asyncio
async def test_mcp_distribution_upsert_does_not_mutate_catalog_state():
    queries: list[str] = []
    distribution = {
        "id": "capdist_gateway",
        "tenant_id": "tenant-a",
        "capability_kind": "mcp_server",
        "capability_id": "gateway",
        "status": "active",
        "visible_to_user": True,
        "scope_mode": "allowlist",
        "department_ids": ["qa"],
        "allowed_roles": ["reviewer"],
        "metadata_json": {},
        "updated_by": "admin-a",
        "created_at": "2026-09-11T00:00:00Z",
        "updated_at": "2026-09-11T00:00:00Z",
    }

    class Connection:
        async def execute(self, query, params):
            queries.append(query)
            if "from mcp_servers" in query:
                assert params == ("tenant-a", "gateway")
                return _Cursor({"name": "gateway"})
            if "pg_advisory_xact_lock" in query:
                return _Cursor()
            if "select metadata_json" in query:
                assert params == ("tenant-a", "gateway")
                return _Cursor()
            if "insert into tenant_capability_distributions" in query:
                assert params[1:4] == ("tenant-a", "mcp_server", "gateway")
                return _Cursor(distribution)
            raise AssertionError("unexpected MCP distribution query")

    result = await mcp_repository.upsert_mcp_server_distribution(
        Connection(),
        tenant_id="tenant-a",
        server_name="gateway",
        status="active",
        visible_to_user=True,
        scope_mode="allowlist",
        department_ids=["qa"],
        allowed_roles=["reviewer"],
        metadata_json={},
        updated_by="admin-a",
    )

    assert result["capability_id"] == "gateway"
    assert result["allowed_roles"] == ["reviewer"]
    assert all("catalog_" not in query for query in queries)


@pytest.mark.asyncio
async def test_mcp_distribution_mutations_preserve_archive_and_missing_guards():
    class Connection:
        def __init__(self, metadata):
            self.metadata = metadata

        async def execute(self, query, params):
            if "from mcp_servers" in query:
                return _Cursor({"name": "gateway"})
            if "pg_advisory_xact_lock" in query:
                return _Cursor()
            if "select metadata_json" in query:
                return _Cursor(
                    None if self.metadata is None else {"metadata_json": self.metadata}
                )
            raise AssertionError("guarded mutation must stop before its write")

    with pytest.raises(RepositoryConflictError, match="capability_distribution_archived"):
        await mcp_repository.upsert_mcp_server_distribution(
            Connection({"archived_at": "2026-09-11T00:00:00.000Z"}),
            tenant_id="tenant-a",
            server_name="gateway",
            status="active",
            visible_to_user=True,
            scope_mode="allowlist",
            department_ids=[],
            allowed_roles=[],
            metadata_json={},
            updated_by="admin-a",
        )

    with pytest.raises(RepositoryNotFoundError, match="capability_distribution_not_found"):
        await mcp_repository.toggle_mcp_server_distribution(
            Connection(None),
            tenant_id="tenant-a",
            server_name="gateway",
            enabled=True,
            updated_by="admin-a",
        )


@pytest.mark.asyncio
async def test_dynamic_server_credential_persists_only_encrypted_envelope():
    class Connection:
        async def execute(self, query, params):
            assert "insert into mcp_server_credentials" in query
            assert "credential_envelope" in query
            assert params == (
                "tenant-a",
                "gateway",
                "credential-sha",
                json.dumps({"header_names": ["Authorization"]}, ensure_ascii=False),
                "sealed-envelope",
                "admin-a",
            )
            return _Cursor()

    await mcp_repository.record_mcp_server_credential(
        Connection(),
        tenant_id="tenant-a",
        server_name="gateway",
        credential_fingerprint="credential-sha",
        metadata={"header_names": ["Authorization"]},
        credential_envelope="sealed-envelope",
        updated_by="admin-a",
    )


@pytest.mark.asyncio
async def test_dynamic_server_credential_can_be_loaded_for_admin_detail_read():
    class Connection:
        async def execute(self, query, params):
            assert "select credential_fingerprint" in query
            assert "from mcp_server_credentials" in query
            assert params == ("tenant-a", "gateway")
            return _Cursor(
                {
                    "credential_fingerprint": "credential-sha",
                    "metadata_json": {"endpoint_configured": True},
                    "credential_envelope": "sealed-envelope",
                }
            )

    assert await mcp_repository.get_mcp_server_credential(
        Connection(),
        tenant_id="tenant-a",
        server_name="gateway",
    ) == {
        "credential_fingerprint": "credential-sha",
        "metadata_json": {"endpoint_configured": True},
        "credential_envelope": "sealed-envelope",
    }


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


def test_active_gateway_paths_do_not_use_catalog_persistence():
    root = Path(__file__).parents[1]
    source_paths = [
        root / "app" / "mcp" / "api.py",
        root / "app" / "mcp" / "application" / "live_catalog.py",
        root / "app" / "mcp" / "application" / "runtime_registry.py",
        root / "app" / "mcp" / "infrastructure" / "catalog.py",
        root / "app" / "mcp" / "infrastructure" / "postgres.py",
        root / "app" / "mcp" / "infrastructure" / "runtime.py",
        root / "app" / "routes" / "mcp.py",
    ]

    for source_path in source_paths:
        source = source_path.read_text(encoding="utf-8").lower()
        for operation in ("from", "join", "insert into", "update"):
            assert f"{operation} mcp_tool_catalog_entries" not in source
        assert "catalog_generation" not in source
        assert "class PostgresMcpCatalogStore" not in source
    assert not (root / "app" / "mcp" / "catalog.py").exists()
