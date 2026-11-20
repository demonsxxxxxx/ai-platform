"""Real PostgreSQL races at MCP governance and Gateway preference boundaries."""
# ruff: noqa: F401,F811 -- imported isolated PostgreSQL fixture.
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from psycopg.types.json import Jsonb

from app.auth import AuthPrincipal
from app.bootstrap.mcp import configure_mcp_runtime
from tests.support.db_transactions import event_loop_policy as event_loop_policy
from tests.test_mcp_public_operation_summary import DECLARATION
from tests.test_recovery_operations_postgres import operation_db


async def prepare_registry(operation_db, monkeypatch):
    from app.routes import mcp
    from tests.test_mcp_routes import _install_mcp_test_keyring
    connect, *_ = operation_db
    configure_mcp_runtime()
    _install_mcp_test_keyring(monkeypatch)
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
                auth_source text, external_id text, status text not null default 'active',
                metadata_json jsonb not null default '{}'::jsonb);
            create table audit_logs(id text, tenant_id text, user_id text, action text,
                target_type text, target_id text, trace_id text, schema_version text, payload_json jsonb);
        """)
        await conn.execute(shared[shared.index("create table if not exists mcp_servers"):shared.index("create table if not exists mcp_tools")])
        await conn.execute("insert into tenant_capability_distribution_backfills values('tenant-a',now())")
        await conn.execute("insert into mcp_servers(id,tenant_id,name) values('server','tenant-a','vendor')")
        await conn.execute("""insert into tenant_capability_distributions(id,tenant_id,capability_kind,capability_id,metadata_json)
            values('distribution','tenant-a','mcp_server','vendor',%s)""", (Jsonb({"public_operation_summaries": declaration}),))
    return connect


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["revoke", "switch_mode"])
async def test_gateway_discovery_rechecks_committed_governance_changes(operation_db, monkeypatch, change):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from fastapi import HTTPException

    from app.bootstrap.mcp import _McpRuntimeServices
    from app.department_directory import normalize_department_directory
    from app.mcp.api import LiveMcpServerResult, LiveMcpTool
    from app.routes import mcp

    connect = await prepare_registry(operation_db, monkeypatch)

    async def department_directory():
        return normalize_department_directory([
            {"value": "110", "parentId": "1", "label": "QA", "children": []},
            {"value": "210", "parentId": "1", "label": "RD", "children": []},
        ])

    monkeypatch.setattr(mcp, "fetch_department_directory", department_directory)
    owner = AuthPrincipal("user-a", "Synthetic", "tenant-a", department_ids=("110",))
    admin = AuthPrincipal("admin-a", "Synthetic admin", "tenant-a", roles=["admin"])
    await mcp._write_server(
        admin, mcp.McpServerLifecycleRequest(
            integration_kind="enterprise_gateway", url="https://synthetic.example/mcp", department_ids=["110"],
        ), name="vendor", is_system=False, action="mcp.server.updated",
    )
    on = mcp.GatewayConversationPreference(use_in_conversation=True)
    off = mcp.GatewayConversationPreference(use_in_conversation=False)
    await mcp.update_gateway_conversation_preference("vendor", off, owner)

    services = _McpRuntimeServices()
    jwt = AsyncMock(return_value="synthetic.user.jwt")
    monkeypatch.setattr(services, "principal_jwt_store", SimpleNamespace(get=jwt))
    if change == "switch_mode":
        await mcp._write_server(
            admin, mcp.McpServerLifecycleRequest(url="https://ordinary.example/mcp"),
            name="other", is_system=False, action="mcp.server.updated",
        )
    started, resume = asyncio.Event(), asyncio.Event()

    async def discover(_target, *, tenant_id, user_id, server_id, jwt):
        assert (tenant_id, user_id, server_id, jwt) == ("tenant-a", "user-a", "vendor", "synthetic.user.jwt")
        started.set()
        await resume.wait()
        return LiveMcpServerResult("vendor", (LiveMcpTool("vendor::lookup", "vendor", "lookup", "lookup", ""),))

    monkeypatch.setattr(services.live_catalog, "discover_gateway_task_tools", discover)

    async def select():
        async with connect() as conn, conn.transaction():
            return await services.repository_call(
                "get_task_gateway_mcp_selection", conn, principal=owner, selected_tool_ids=["other::stale"],
            )

    assert (await select()).tool_ids == ()
    jwt.assert_not_awaited()
    await mcp.update_gateway_conversation_preference("vendor", on, owner)
    pending = asyncio.create_task(select())
    try:
        await asyncio.wait_for(started.wait(), 3)
        if change == "revoke":
            await asyncio.wait_for(mcp._write_server(
                admin, mcp.McpServerLifecycleRequest(department_ids=["210"], credentials_action="keep"),
                name="vendor", is_system=False, action="mcp.server.updated",
            ), 3)
        else:
            await asyncio.wait_for(mcp._write_server(
                admin, mcp.McpServerLifecycleRequest(integration_kind="enterprise_gateway", credentials_action="keep"),
                name="other", is_system=False, action="mcp.server.updated",
            ), 3)
            await mcp.update_gateway_conversation_preference("other", off, owner)
        resume.set()
        result = await asyncio.wait_for(pending, 3)
        if change == "revoke":
            assert result.server_ids == {"vendor"}
            assert result.tool_ids == ()
            with pytest.raises(HTTPException) as denied:
                await mcp.update_gateway_conversation_preference("vendor", on, owner)
            assert denied.value.status_code == 404
        else:
            assert result.server_ids == {"vendor", "other"}
            assert result.without_gateway(["other::stale"]) == []
            assert result.tool_ids == ("vendor::lookup",)
    finally:
        resume.set()
        if not pending.done():
            pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)


@pytest.mark.asyncio
async def test_two_admin_interleaving_omitted_update_cannot_restore_committed_revocation(operation_db, monkeypatch):
    from app.routes import mcp
    connect = await prepare_registry(operation_db, monkeypatch)
    read_before_revoke, resume_a = asyncio.Event(), asyncio.Event()
    original_get = mcp.mcp_repository.get_mcp_server_registry_entry
    pending_a = None
    async def read_server(conn, **kwargs):
        if asyncio.current_task() is pending_a:
            read_before_revoke.set()
            await resume_a.wait()
        return await original_get(conn, **kwargs)
    monkeypatch.setattr(mcp.mcp_repository, "get_mcp_server_registry_entry", read_server)
    principals = [AuthPrincipal(tenant_id="tenant-a", user_id=f"admin-{name}", display_name=name, roles=["admin"], source="test") for name in ("a", "b")]
    async def write(principal, payload):
        return await mcp._write_server(principal, mcp.McpServerLifecycleRequest.model_validate(payload),
            name="vendor", is_system=False, action="mcp.server.updated")
    pending_a = asyncio.create_task(write(principals[0], {}))
    try:
        await asyncio.wait_for(read_before_revoke.wait(), 3)
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


@pytest.mark.asyncio
@pytest.mark.parametrize("replacement", ["replace", "clear"])
async def test_keep_mode_validation_reads_credentials_after_server_lock(operation_db, monkeypatch, replacement):
    from fastapi import HTTPException
    from app.routes import mcp
    connect = await prepare_registry(operation_db, monkeypatch)
    principals = [AuthPrincipal(tenant_id="tenant-a", user_id=f"admin-{name}", display_name=name,
                                roles=["admin"], source="test") for name in ("a", "b")]
    async def write(principal, **payload):
        return await mcp._write_server(principal, mcp.McpServerLifecycleRequest.model_validate(payload),
            name="vendor", is_system=False, action="mcp.server.updated")
    await write(principals[0], url="https://synthetic.example/mcp")
    original_get = mcp.mcp_repository.get_mcp_server_registry_entry
    before_lock, resume = asyncio.Event(), asyncio.Event()
    pending = None
    async def get_server(conn, **kwargs):
        if asyncio.current_task() is pending:
            assert kwargs["for_update"] is True
            before_lock.set()
            await resume.wait()
        return await original_get(conn, **kwargs)
    monkeypatch.setattr(mcp.mcp_repository, "get_mcp_server_registry_entry", get_server)
    pending = asyncio.create_task(write(principals[0], credentials_action="keep", integration_kind="enterprise_gateway"))
    try:
        await asyncio.wait_for(before_lock.wait(), 3)
        payload = {"credentials_action": replacement}
        if replacement == "replace":
            payload.update(url="https://synthetic.example/new", headers={"Authorization": "Bearer synthetic-only"})
        await asyncio.wait_for(write(principals[1], **payload), 3)
        resume.set()
        if replacement == "replace":
            with pytest.raises(HTTPException) as raised:
                await asyncio.wait_for(pending, 3)
            assert raised.value.detail == "mcp_header_conflict"
        else:
            result = await asyncio.wait_for(pending, 3)
            assert result["credential_state"] == "not_configured"
        async with connect() as conn:
            row = await (await conn.execute("""select s.integration_kind,s.credential_state,
                s.credential_fingerprint as registry_fingerprint,c.credential_fingerprint,c.credential_envelope
                from mcp_servers s join mcp_server_credentials c
                on c.tenant_id=s.tenant_id and c.server_name=s.name""")).fetchone()
        assert row["registry_fingerprint"] == row["credential_fingerprint"]
        if replacement == "replace":
            assert row["integration_kind"] == "mcp" and row["credential_state"] == "configured"
            _, headers = mcp.open_mcp_server_credentials(tenant_id="tenant-a", server_id="vendor", envelope=row["credential_envelope"])
            assert headers == {"Authorization": "Bearer synthetic-only"}
        else:
            assert row["integration_kind"] == "enterprise_gateway" and not row["credential_envelope"]
    finally:
        resume.set()
        if not pending.done():
            pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)
