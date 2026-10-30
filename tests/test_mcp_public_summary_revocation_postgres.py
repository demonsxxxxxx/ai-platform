"""Two actual admin transactions cannot resurrect a revoked disclosure declaration."""
# ruff: noqa: F401,F811 -- imported isolated PostgreSQL fixture.
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from psycopg.types.json import Jsonb

from app.auth import AuthPrincipal
from app.bootstrap.mcp import configure_mcp_runtime
from tests.test_mcp_public_operation_summary import DECLARATION
from tests.test_recovery_operations_postgres import operation_db


@pytest.mark.asyncio
async def test_two_admin_interleaving_omitted_update_cannot_restore_committed_revocation(operation_db, monkeypatch):
    from app.routes import mcp
    connect, *_ = operation_db
    configure_mcp_runtime()
    @asynccontextmanager
    async def transaction():
        async with connect() as conn, conn.transaction():
            yield conn
    monkeypatch.setattr(mcp, "transaction", transaction)
    shared = Path("app/schema.sql").read_text(encoding="utf-8")
    declaration = {"create_project": DECLARATION}
    async with transaction() as conn:
        await conn.execute("""
            create table tenants(id text primary key);
            insert into tenants values('tenant-a');
            create table users(id text primary key, tenant_id text, display_name text,
                auth_source text, external_id text);
            create table audit_logs(id text, tenant_id text, user_id text, action text,
                target_type text, target_id text, trace_id text, schema_version text, payload_json jsonb);
        """)
        await conn.execute(shared[shared.index("create table if not exists mcp_servers"):shared.index("create table if not exists mcp_tools")])
        await conn.execute("insert into tenant_capability_distribution_backfills values('tenant-a',now())")
        await conn.execute("insert into mcp_servers(id,tenant_id,name) values('server','tenant-a','vendor')")
        await conn.execute("""insert into tenant_capability_distributions(id,tenant_id,capability_kind,capability_id,metadata_json)
            values('distribution','tenant-a','mcp_server','vendor',%s)""", (Jsonb({"public_operation_summaries": declaration}),))
    read_before_revoke, resume_a = asyncio.Event(), asyncio.Event()
    original_get = mcp.identity_capability_distributions.get_capability_distribution_row
    pending_a = None
    async def read_distribution(conn, **kwargs):
        row = await original_get(conn, **kwargs)
        if asyncio.current_task() is pending_a:
            assert row["metadata_json"]["public_operation_summaries"] == declaration
            read_before_revoke.set()
            await resume_a.wait()
        return row
    monkeypatch.setattr(mcp.identity_capability_distributions, "get_capability_distribution_row", read_distribution)
    principals = [AuthPrincipal(tenant_id="tenant-a", user_id=f"admin-{name}", display_name=name, roles=["admin"], source="test") for name in ("a", "b")]
    async def write(principal, payload):
        return await mcp._write_server(principal, mcp.McpServerLifecycleRequest.model_validate(payload),
            name="vendor", is_system=False, action="mcp.server.updated")
    pending_a = asyncio.create_task(write(principals[0], {}))
    try:
        try:
            await asyncio.wait_for(read_before_revoke.wait(), 3)
        except TimeoutError:
            if pending_a.done():
                await pending_a
            raise
        # B uses a different user row, commits revocation before A acquires the server lock.
        revoked = await asyncio.wait_for(write(principals[1], {"public_operation_summaries": {}}), 3)
        assert revoked["public_operation_summaries"] == {}
        async with connect() as conn:
            row = await (await conn.execute("select metadata_json from tenant_capability_distributions")).fetchone()
            assert row["metadata_json"]["public_operation_summaries"] == {}
        resume_a.set()
        result = await asyncio.wait_for(pending_a, 3)
        assert result["public_operation_summaries"] == {}
        async with connect() as conn:
            row = await (await conn.execute("select metadata_json,updated_by from tenant_capability_distributions")).fetchone()
            assert row["updated_by"] == "admin-a"
            assert row["metadata_json"]["public_operation_summaries"] == {}
            users = await (await conn.execute("select id from users order by id")).fetchall()
            assert [user["id"] for user in users] == ["admin-a", "admin-b"]
    finally:
        resume_a.set()
        if not pending_a.done():
            pending_a.cancel()
        await asyncio.gather(pending_a, return_exceptions=True)
