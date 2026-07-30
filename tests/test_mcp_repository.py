import json

import pytest

from app.mcp import repository as mcp_repository
from app.mcp.catalog import (
    MCP_TOOL_ANNOTATION_READ_ONLY,
    MCP_TOOL_ANNOTATION_UNKNOWN,
    MCP_TOOL_ANNOTATION_WRITE_CAPABLE,
    McpDiscoveredTool,
)


@pytest.mark.asyncio
async def test_expired_sync_takeover_claims_new_attempt_and_fences_old_attempt(monkeypatch):
    """A reclaimed lease increments the attempt, preventing the old owner from publishing."""

    class Cursor:
        def __init__(self, row):
            self._row = row

        async def fetchone(self):
            return self._row

    class Connection:
        def __init__(self):
            self.calls = []

        async def execute(self, sql, params):
            self.calls.append((sql, params))
            if "for update" in sql:
                return Cursor(
                    {
                        "tenant_id": "tenant-a",
                        "name": "knowledge",
                        "status": "active",
                        "catalog_generation": 7,
                        "catalog_sync_attempt": 2,
                        "catalog_status": "syncing",
                        "catalog_unavailable_reason": "refresh_required",
                        "catalog_revision": 3,
                        "catalog_discovered_count": 1,
                        "catalog_selectable_count": 1,
                    }
                )
            return Cursor(
                {
                    "catalog_generation": 7,
                    "catalog_sync_attempt": 3,
                    "catalog_status": "syncing",
                    "catalog_unavailable_reason": "refresh_required",
                    "catalog_revision": 3,
                    "catalog_discovered_count": 1,
                    "catalog_selectable_count": 1,
                }
            )

    conn = Connection()
    started = await mcp_repository.begin_mcp_catalog_sync(
        conn,
        tenant_id="tenant-a",
        server_name="knowledge",
        observed_generation=7,
        actor_id="admin-a",
    )

    assert started["started"] is True
    assert started["catalog_sync_attempt"] == 3
    assert "catalog_sync_lease_expires_at <= clock_timestamp()" in conn.calls[1][0]

    async def current_server(conn, **kwargs):
        return {
            "status": "active",
            "catalog_generation": 7,
            "catalog_sync_attempt": 3,
            "catalog_status": "syncing",
            "catalog_sync_lease_active": True,
            "catalog_revision": 3,
            "catalog_discovered_count": 1,
            "catalog_selectable_count": 1,
        }

    monkeypatch.setattr(mcp_repository, "_locked_server", current_server)
    stale = await mcp_repository.publish_mcp_tool_catalog(
        object(),
        tenant_id="tenant-a",
        server_name="knowledge",
        observed_generation=7,
        observed_attempt=2,
        endpoint="https://mcp.example/tools",
        tools=(),
        actor_id="admin-a",
    )

    assert stale["catalog_unavailable_reason"] == "stale_generation"
    assert stale["published"] is False


@pytest.mark.asyncio
async def test_expired_sync_lease_rejects_outcome_and_publication_before_mutation(monkeypatch):
    class Connection:
        async def execute(self, *_args, **_kwargs):
            raise AssertionError("an expired lease must not mutate catalog rows")

    async def expired_server(conn, **kwargs):
        return {
            "status": "active",
            "catalog_generation": 7,
            "catalog_sync_attempt": 3,
            "catalog_status": "syncing",
            "catalog_sync_lease_active": False,
            "catalog_revision": 3,
            "catalog_discovered_count": 1,
            "catalog_selectable_count": 1,
        }

    monkeypatch.setattr(mcp_repository, "_locked_server", expired_server)

    outcome = await mcp_repository.record_mcp_catalog_sync_outcome(
        Connection(),
        tenant_id="tenant-a",
        server_name="knowledge",
        observed_generation=7,
        observed_attempt=3,
        reason="transport_failure",
        actor_id="admin-a",
    )
    publication = await mcp_repository.publish_mcp_tool_catalog(
        Connection(),
        tenant_id="tenant-a",
        server_name="knowledge",
        observed_generation=7,
        observed_attempt=3,
        endpoint="https://mcp.example/tools",
        tools=(),
        actor_id="admin-a",
    )

    assert outcome["catalog_unavailable_reason"] == "stale_generation"
    assert publication["catalog_unavailable_reason"] == "stale_generation"
    assert publication["published"] is False


@pytest.mark.asyncio
async def test_catalog_publication_activates_unknown_tools_as_high_risk_through_catalog_managed_policy_rows(monkeypatch):
    class Cursor:
        def __init__(self, *, row=None, rows=()):
            self._row = row
            self._rows = rows

        async def fetchone(self):
            return self._row

        async def fetchall(self):
            return self._rows

    class Connection:
        def __init__(self):
            self.calls = []

        async def execute(self, sql, params):
            self.calls.append((sql, params))
            if "select entries.tool_id" in sql:
                return Cursor(rows=[])
            if "update mcp_servers" in sql:
                return Cursor(
                    row={
                        "catalog_status": "available",
                        "catalog_unavailable_reason": "",
                        "catalog_revision": 5,
                        "catalog_discovered_count": 3,
                        "catalog_selectable_count": 3,
                    }
                )
            return Cursor()

    async def active_server(conn, **kwargs):
        return {
            "status": "active",
            "catalog_generation": 7,
            "catalog_sync_attempt": 3,
            "catalog_status": "syncing",
            "catalog_sync_lease_active": True,
            "catalog_revision": 4,
            "catalog_discovered_count": 0,
            "catalog_selectable_count": 0,
        }

    async def append_audit_log(*args, **kwargs):
        return "audit-catalog"

    monkeypatch.setattr(mcp_repository, "_locked_server", active_server)
    monkeypatch.setattr(mcp_repository._repositories(), "append_audit_log", append_audit_log)
    conn = Connection()

    result = await mcp_repository.publish_mcp_tool_catalog(
        conn,
        tenant_id="tenant-a",
        server_name="compatible-server",
        observed_generation=7,
        observed_attempt=3,
        endpoint="https://mcp.example/tools",
        tools=(
            McpDiscoveredTool("read_tool", "schema-read", True, MCP_TOOL_ANNOTATION_READ_ONLY),
            McpDiscoveredTool("write_tool", "schema-write", False, MCP_TOOL_ANNOTATION_WRITE_CAPABLE),
            McpDiscoveredTool("unknown_tool", "schema-unknown", False, MCP_TOOL_ANNOTATION_UNKNOWN),
        ),
        actor_id="admin-a",
    )

    registry_writes = [params for sql, params in conn.calls if "insert into mcp_tools" in sql]
    registry_by_remote_name = {json.loads(params[5])[0]: params[6:9] for params in registry_writes}
    policy_writes = [params for sql, params in conn.calls if "insert into tool_policies" in sql]
    policy_by_reason = {params[4]: ("active", params[2], params[3]) for params in policy_writes}

    assert result["catalog_status"] == "available"
    assert result["catalog_selectable_count"] == 3
    assert registry_by_remote_name == {
        "read_tool": ("active", False, "low"),
        "write_tool": ("active", True, "high"),
        "unknown_tool": ("active", True, "high"),
    }
    assert policy_by_reason == {
        "mcp_catalog_read_only": ("active", False, "low"),
        "mcp_catalog_write_capable": ("active", True, "high"),
        "mcp_catalog_annotation_unknown": ("active", True, "high"),
    }
    policy_sql = next(sql for sql, _params in conn.calls if "insert into tool_policies" in sql)
    assert "where tool_policies.reason = any(%s)" in policy_sql


def test_catalog_manifest_policy_reason_preserves_managed_transitions_and_ignores_custom_policy():
    assert mcp_repository._catalog_manifest_policy_reason(
        "mcp_catalog_read_only", "mcp_catalog_annotation_unknown"
    ) == "mcp_catalog_annotation_unknown"
    assert mcp_repository._catalog_manifest_policy_reason(
        "admin_owned_policy", "mcp_catalog_annotation_unknown"
    ) is None
    assert mcp_repository._catalog_manifest_policy_reason(
        "", "mcp_catalog_annotation_unknown"
    ) == "mcp_catalog_annotation_unknown"


@pytest.mark.asyncio
async def test_catalog_publication_is_idempotent_after_an_admin_owned_policy_and_stale_removal(monkeypatch):
    class Cursor:
        def __init__(self, *, row=None, rows=()):
            self._row = row
            self._rows = rows

        async def fetchone(self):
            return self._row

        async def fetchall(self):
            return self._rows

    class Connection:
        def __init__(self):
            self.calls = []

        async def execute(self, sql, params):
            self.calls.append((sql, params))
            if "select entries.tool_id" in sql:
                return Cursor(
                    rows=[
                        {
                            "tool_id": "mcpt-compatible",
                            "remote_tool_name": "unknown_tool",
                            "schema_hash": "schema-unknown",
                            "catalog_entry_status": "active",
                            "write_capable": True,
                            "risk_level": "high",
                            "policy_reason": "admin_owned_policy",
                        },
                        {
                            "tool_id": "mcpt-stale-read",
                            "remote_tool_name": "read_tool",
                            "schema_hash": "schema-read",
                            "catalog_entry_status": "stale",
                            "write_capable": False,
                            "risk_level": "low",
                            "policy_reason": "mcp_catalog_read_only",
                        },
                        {
                            "tool_id": "mcpt-stale-write",
                            "remote_tool_name": "write_tool",
                            "schema_hash": "schema-write",
                            "catalog_entry_status": "stale",
                            "write_capable": True,
                            "risk_level": "high",
                            "policy_reason": "mcp_catalog_write_capable",
                        },
                    ]
                )
            if "update mcp_servers" in sql:
                assert params[0] == 5
                return Cursor(
                    row={
                        "catalog_status": "available",
                        "catalog_unavailable_reason": "",
                        "catalog_revision": 5,
                        "catalog_discovered_count": 1,
                        "catalog_selectable_count": 1,
                    }
                )
            return Cursor()

    async def active_server(conn, **kwargs):
        return {
            "status": "active",
            "catalog_generation": 7,
            "catalog_sync_attempt": 4,
            "catalog_status": "syncing",
            "catalog_sync_lease_active": True,
            "catalog_revision": 5,
            "catalog_discovered_count": 1,
            "catalog_selectable_count": 1,
        }

    async def append_audit_log(*args, **kwargs):
        return "audit-catalog"

    monkeypatch.setattr(mcp_repository, "_locked_server", active_server)
    monkeypatch.setattr(mcp_repository._repositories(), "append_audit_log", append_audit_log)

    result = await mcp_repository.publish_mcp_tool_catalog(
        Connection(),
        tenant_id="tenant-a",
        server_name="compatible-server",
        observed_generation=7,
        observed_attempt=4,
        endpoint="https://mcp.example/tools",
        tools=(McpDiscoveredTool("unknown_tool", "schema-unknown", False, MCP_TOOL_ANNOTATION_UNKNOWN),),
        actor_id="admin-a",
    )

    assert result["catalog_revision"] == 5
    assert result["published"] is False


@pytest.mark.asyncio
async def test_annotation_unknown_catalog_tool_uses_existing_chat_distribution_authorization(monkeypatch):
    compatible_tool = {
        "tool_id": "mcpt-compatible",
        "server_id": "compatible-server",
        "transport_type": "streamable_http",
        "endpoint": "https://mcp.example/tools",
        "auth_mode": "none",
        "allowed_tools": ["unknown_tool"],
        "catalog_status": "active",
        "server_catalog_status": "available",
        "effective_status": "active",
        "server_status": "active",
        "visible_to_user": True,
        "write_capable": True,
        "risk_level": "high",
    }

    async def list_catalog_entries(conn, *, tenant_id):
        assert tenant_id == "tenant-a"
        return [compatible_tool]

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
    monkeypatch.setattr(repositories, "list_chat_mcp_tool_catalog_entries", list_catalog_entries)
    monkeypatch.setattr(repositories, "get_capability_distribution_row", active_distribution)

    authorized = await mcp_repository.list_authorized_chat_mcp_tools(
        object(),
        tenant_id="tenant-a",
        principal_department_id="qa",
        principal_roles=["user"],
        is_admin=False,
        permissions=[],
    )
    unauthorized = await mcp_repository.list_authorized_chat_mcp_tools(
        object(),
        tenant_id="tenant-a",
        principal_department_id="rd",
        principal_roles=["user"],
        is_admin=False,
        permissions=[],
    )

    assert [tool["tool_id"] for tool in authorized] == ["mcpt-compatible"]
    assert unauthorized == []


def test_only_the_code_owned_ragflow_builtin_has_legacy_catalog_authority():
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
    assert "catalog_entry.tenant_id = %s" in authority_sql
    assert "catalog_any" not in authority_sql


@pytest.mark.asyncio
async def test_workbench_and_registry_reads_share_the_fail_closed_catalog_authority_predicate():
    class Cursor:
        async def fetchall(self):
            return []

        async def fetchone(self):
            return None

    class Connection:
        def __init__(self):
            self.calls = []

        async def execute(self, sql, params):
            self.calls.append((sql, params))
            return Cursor()

    conn = Connection()
    assert await mcp_repository.list_workbench_mcp_tools(conn, tenant_id="tenant-a") == []
    assert await mcp_repository.get_mcp_tool_registry_entry(
        conn,
        tenant_id="tenant-a",
        tool_id="legacy-untrusted",
    ) is None

    for sql, _params in conn.calls:
        assert "ragflow-knowledge-search" in sql
        assert "catalog_entry.tenant_id = %s" in sql
        assert "catalog_any" not in sql
