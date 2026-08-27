import os
from pathlib import Path
import uuid

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
import pytest

from app.mcp import repository as mcp_repository


POSTGRES_DSN_ENV = "AI_PLATFORM_MCP_CATALOG_TEST_DSN"


def _postgres_dsn() -> str:
    dsn = os.getenv(POSTGRES_DSN_ENV, "").strip()
    if not dsn:
        pytest.skip(f"{POSTGRES_DSN_ENV} is not configured")
    return dsn


@pytest.mark.asyncio
async def test_postgres_keeps_only_server_credentials_and_lightweight_tool_refs():
    dsn = _postgres_dsn()
    schema_name = f"mcp_runtime_test_{uuid.uuid4().hex}"
    schema_source = Path("app/schema.sql").read_text(encoding="utf-8")
    conn = await psycopg.AsyncConnection.connect(
        dsn,
        autocommit=True,
        row_factory=dict_row,
    )
    try:
        await conn.execute(sql.SQL("create schema {}").format(sql.Identifier(schema_name)))
        await conn.execute(
            sql.SQL("set search_path to {}").format(sql.Identifier(schema_name))
        )
        await conn.execute(schema_source)
        await conn.execute(
            "insert into tenants(id, name) values ('tenant-mcp', 'MCP Test')"
        )
        await conn.execute(
            """
            insert into users(id, tenant_id, display_name)
            values ('user-mcp', 'tenant-mcp', 'MCP User')
            """
        )
        await conn.execute(
            """
            insert into mcp_servers(
              id, tenant_id, name, transport, status, endpoint_redacted,
              credential_state, updated_by
            ) values (
              'server-mcp', 'tenant-mcp', 'gateway', 'streamable_http',
              'active', '', 'configured', 'user-mcp'
            )
            """
        )
        await conn.execute(
            """
            insert into tenant_capability_distributions(
              tenant_id, capability_kind, capability_id, status,
              visible_to_user, scope_mode, updated_by
            ) values (
              'tenant-mcp', 'mcp_server', 'gateway', 'active',
              true, 'allowlist', 'user-mcp'
            )
            """
        )
        await conn.execute(
            """
            insert into mcp_server_credentials(
              id, tenant_id, server_name, credential_fingerprint,
              credential_envelope, updated_by
            ) values (
              'credential-mcp', 'tenant-mcp', 'gateway', 'fingerprint',
              'sealed-envelope', 'user-mcp'
            )
            """
        )

        runtime_target = await mcp_repository.get_mcp_server_runtime_target(
            conn,
            tenant_id="tenant-mcp",
            server_name="gateway",
        )
        tool = await mcp_repository.get_mcp_tool_registry_entry(
            conn,
            tenant_id="tenant-mcp",
            tool_id="gateway::pmm.query_projects",
        )
        catalog_table = await (
            await conn.execute("select to_regclass('mcp_tool_catalog_entries') as relation")
        ).fetchone()

        assert runtime_target == {
            "transport": "streamable_http",
            "credential_envelope": "sealed-envelope",
        }
        assert tool is not None
        assert tool["tool_id"] == "gateway::pmm.query_projects"
        assert tool["endpoint"] == ""
        assert catalog_table["relation"] is None
    finally:
        try:
            await conn.execute("set search_path to public")
            await conn.execute(
                sql.SQL("drop schema {} cascade").format(sql.Identifier(schema_name))
            )
        finally:
            await conn.close()
