import pytest

from app.mcp.infrastructure import postgres as mcp_postgres


class _Cursor:
    def __init__(self, rows=()):
        self._rows = list(rows)

    async def fetchall(self):
        return list(self._rows)

    async def fetchone(self):
        return self._rows[0] if self._rows else None


class _RelayConnection:
    def __init__(self, row):
        self.row = row
        self.sql = ""
        self.params = None

    async def execute(self, statement, params=None):
        self.sql = " ".join(str(statement).split()).lower()
        self.params = params
        return _Cursor([self.row] if self.row is not None else [])


@pytest.mark.asyncio
async def test_run_context_reader_returns_only_the_opaque_context_id():
    conn = _RelayConnection({"mcp_context_id": "mcpctx-run-a"})

    context_id = await mcp_postgres.get_run_mcp_context_id(
        conn,
        tenant_id="tenant-a",
        run_id="run-a",
    )

    assert context_id == "mcpctx-run-a"
    assert conn.sql == (
        "select mcp_context_id from runs where tenant_id = %s and id = %s"
    )
    assert conn.params == ("tenant-a", "run-a")


@pytest.mark.asyncio
@pytest.mark.parametrize("credential_envelope", [None, ""])
async def test_record_mcp_server_credential_normalizes_empty_envelope(
    credential_envelope,
):
    conn = _RelayConnection(None)

    await mcp_postgres.record_mcp_server_credential(
        conn,
        tenant_id="tenant-a",
        server_name="command-only",
        credential_fingerprint="",
        metadata={},
        credential_envelope=credential_envelope,
        updated_by="admin-a",
    )

    assert "insert into mcp_server_credentials" in conn.sql
    assert conn.params == (
        "tenant-a",
        "command-only",
        "",
        "{}",
        "",
        "admin-a",
    )


@pytest.mark.asyncio
async def test_mcp_relay_target_uses_current_available_catalog_entries():
    conn = _RelayConnection(
        {
            "credential_envelope": "sealed-envelope",
            "metadata_json": {},
            "active_tool_names": ["remote-search"],
        }
    )

    target = await mcp_postgres.get_mcp_relay_target(
        conn,
        tenant_id="tenant-a",
        server_name="gateway",
    )

    assert target == {
        "credential_envelope": "sealed-envelope",
        "metadata_json": {},
        "active_tool_names": ["remote-search"],
    }
    assert "array_agg(distinct catalog_entry.remote_tool_name)" in conn.sql
    assert "join mcp_tool_catalog_entries catalog_entry" in conn.sql
    assert "catalog_entry.tool_id = mcp_tools.id" in conn.sql
    assert "catalog_entry.catalog_generation = mcp_servers.catalog_generation" in conn.sql
    assert "mcp_servers.catalog_status = 'available'" in conn.sql
    assert "catalog_entry.status = 'active'" in conn.sql
    assert "mcp_tools.status = 'active'" in conn.sql
    assert "tool_policies.status = 'active'" in conn.sql
    assert "mcp_tools.allowed_tools" not in conn.sql
    assert conn.params == ("tenant-a", "gateway")
