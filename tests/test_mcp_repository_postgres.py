from pathlib import Path

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


def test_fresh_schema_and_repository_have_no_gateway_catalog_persistence_path():
    root = Path(__file__).resolve().parents[1]
    schema = (root / "app" / "schema.sql").read_text(encoding="utf-8").lower()
    repository = (root / "app" / "mcp" / "repository.py").read_text(encoding="utf-8").lower()

    assert "mcp_tool_catalog_entries" not in schema
    assert "mcp_tool_catalog_entries" not in repository
    assert "publish_mcp_tool_catalog" not in repository
    assert "begin_mcp_catalog_sync" not in repository


@pytest.mark.asyncio
async def test_run_identity_reader_returns_only_grant_cleanup_identity():
    conn = _RelayConnection(
        {"tenant_id": "tenant-a", "user_id": "user-a", "run_id": "run-a"}
    )

    identity = await mcp_postgres.get_run_mcp_identity(
        conn,
        tenant_id="tenant-a",
        run_id="run-a",
    )

    assert identity == {"tenant_id": "tenant-a", "user_id": "user-a", "run_id": "run-a"}
    assert conn.sql == (
        "select tenant_id, user_id, id as run_id from runs "
        "where tenant_id = %s and id = %s"
    )
    assert conn.params == ("tenant-a", "run-a")


@pytest.mark.asyncio
@pytest.mark.parametrize("credential_envelope", [None, ""])
async def test_record_mcp_server_credential_normalizes_empty_envelope(credential_envelope):
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
async def test_mcp_relay_target_does_not_read_platform_tool_catalog():
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
        "active_tool_names": [],
    }
    assert "mcp_tool_catalog_entries" not in conn.sql
    assert "mcp_tools" not in conn.sql
    assert "tool_policies" not in conn.sql
    assert conn.params == ("tenant-a", "gateway")
