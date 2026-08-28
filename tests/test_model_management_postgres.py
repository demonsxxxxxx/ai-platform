from __future__ import annotations

import asyncio
import base64
import os
from pathlib import Path
import uuid

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
import pytest

from app.execution.infrastructure.model_management import (
    activate_connection_and_sync,
    resolve_run_model,
)
from app.execution.infrastructure.model_security import api_key_fingerprint
from app.runs.infrastructure.postgres import inherit_run_model


POSTGRES_DSN_ENV = "AI_PLATFORM_AGENT_PROFILE_TEST_DSN"


def _postgres_dsn() -> str:
    dsn = os.getenv(POSTGRES_DSN_ENV, "").strip()
    if not dsn:
        pytest.skip(f"{POSTGRES_DSN_ENV} is not configured")
    return dsn


async def _set_search_path(conn: psycopg.AsyncConnection, schema_name: str) -> None:
    await conn.execute(
        sql.SQL("set search_path to {}, public").format(sql.Identifier(schema_name))
    )


@pytest.mark.asyncio
async def test_model_resolution_shared_lock_allows_readers_and_blocks_activation() -> None:
    dsn = _postgres_dsn()
    schema_name = f"model_lock_{uuid.uuid4().hex}"
    schema_sql = Path("app/schema.sql").read_text(encoding="utf-8")
    connections = [
        await psycopg.AsyncConnection.connect(dsn, autocommit=True, row_factory=dict_row)
        for _ in range(3)
    ]
    first, second, activator = connections
    encryption_key = base64.urlsafe_b64encode(b"k" * 32).decode("ascii")
    try:
        await first.execute(sql.SQL("create schema {}").format(sql.Identifier(schema_name)))
        for conn in connections:
            await _set_search_path(conn, schema_name)
        await first.execute(schema_sql)
        await first.execute("insert into tenants(id, name) values ('tenant-a', 'Tenant A')")
        await first.execute(
            "insert into users(id, tenant_id, display_name) values ('admin-a', 'tenant-a', 'Admin A')"
        )
        await activate_connection_and_sync(
            first,
            base_url="https://gateway.example",
            api_key="first-key",
            key_fingerprint=api_key_fingerprint("first-key"),
            encryption_key=encryption_key,
            actor_user_id="admin-a",
            upstream_model_ids=["openai/gpt-5"],
        )
        await first.execute(
            "update model_catalog_entries set enabled = true, is_default = true"
        )

        activation_started = asyncio.Event()

        async def activate_successor():
            async with activator.transaction():
                activation_started.set()
                return await activate_connection_and_sync(
                    activator,
                    base_url="https://gateway-2.example",
                    api_key="second-key",
                    key_fingerprint=api_key_fingerprint("second-key"),
                    encryption_key=encryption_key,
                    actor_user_id="admin-a",
                    upstream_model_ids=["openai/gpt-5"],
                )

        async with first.transaction():
            first_selection = await resolve_run_model(
                first,
                model_id=None,
                model_value="openai/gpt-5",
            )
            async with second.transaction():
                second_selection = await asyncio.wait_for(
                    resolve_run_model(
                        second,
                        model_id=None,
                        model_value="openai/gpt-5",
                    ),
                    timeout=1,
                )
                activation = asyncio.create_task(activate_successor())
                await activation_started.wait()
                await asyncio.sleep(0.1)
                assert not activation.done()
            await asyncio.sleep(0.1)
            assert not activation.done()
            assert first_selection == second_selection

        revision, _models = await asyncio.wait_for(activation, timeout=2)
        assert revision == 2
    finally:
        await first.execute(
            sql.SQL("drop schema if exists {} cascade").format(sql.Identifier(schema_name))
        )
        for conn in connections:
            await conn.close()


@pytest.mark.asyncio
async def test_legacy_model_inheritance_updates_child_and_failure_rolls_back() -> None:
    dsn = _postgres_dsn()
    schema_name = f"model_snapshot_rollback_{uuid.uuid4().hex}"
    schema_sql = Path("app/schema.sql").read_text(encoding="utf-8")
    conn = await psycopg.AsyncConnection.connect(
        dsn,
        autocommit=True,
        row_factory=dict_row,
    )
    try:
        await conn.execute(sql.SQL("create schema {}").format(sql.Identifier(schema_name)))
        await _set_search_path(conn, schema_name)
        await conn.execute(schema_sql)
        await conn.execute("insert into tenants(id, name) values ('tenant-a', 'Tenant A')")
        await conn.execute(
            "insert into workspaces(id, tenant_id, name) values ('workspace-a', 'tenant-a', 'A')"
        )
        await conn.execute(
            "insert into users(id, tenant_id, display_name) values ('user-a', 'tenant-a', 'User A')"
        )
        await conn.execute(
            """
            insert into agents(id, tenant_id, name, agent_type)
            values ('agent-a', 'tenant-a', 'Agent A', 'chat')
            """
        )
        await conn.execute(
            """
            insert into sessions(id, tenant_id, workspace_id, user_id, agent_id, title)
            values ('session-a', 'tenant-a', 'workspace-a', 'user-a', 'agent-a', 'A')
            """
        )
        await conn.execute(
            """
            insert into runs(
              id, tenant_id, workspace_id, session_id, user_id, agent_id,
              execution_kind, status, input_json
            ) values (
              'run-source', 'tenant-a', 'workspace-a', 'session-a', 'user-a', 'agent-a',
              'harness_chat', 'failed',
              '{"model_id":"legacy-default","model_value":"openai/gpt-5"}'::jsonb
            )
            """
        )

        async with conn.transaction():
            await conn.execute(
                """
                insert into runs(
                  id, tenant_id, workspace_id, session_id, user_id, agent_id,
                  execution_kind, status, copied_from_run_id
                ) values (
                  'run-child', 'tenant-a', 'workspace-a', 'session-a', 'user-a', 'agent-a',
                  'harness_chat', 'queued', 'run-source'
                )
                """
            )
            await inherit_run_model(
                conn,
                tenant_id="tenant-a",
                source_run_id="run-source",
                child_run_id="run-child",
            )

        modernized_cursor = await conn.execute(
            """
            select model_id, model_value, model_gateway_revision
            from runs where tenant_id = 'tenant-a' and id = 'run-child'
            """
        )
        assert await modernized_cursor.fetchone() == {
            "model_id": "legacy-default",
            "model_value": "openai/gpt-5",
            "model_gateway_revision": None,
        }

        with pytest.raises(ValueError, match="run_model_child_source_mismatch"):
            async with conn.transaction():
                await conn.execute(
                    """
                    insert into runs(
                      id, tenant_id, workspace_id, session_id, user_id, agent_id,
                      execution_kind, status
                    ) values (
                      'run-invalid', 'tenant-a', 'workspace-a', 'session-a', 'user-a', 'agent-a',
                      'harness_chat', 'queued'
                    )
                    """
                )
                await conn.execute(
                    """
                    insert into run_events(id, tenant_id, run_id, event_type, stage)
                    values ('event-invalid', 'tenant-a', 'run-invalid', 'run.created', 'admission')
                    """
                )
                await inherit_run_model(
                    conn,
                    tenant_id="tenant-a",
                    source_run_id="run-source",
                    child_run_id="run-invalid",
                )

        child_cursor = await conn.execute(
            "select id from runs where tenant_id = 'tenant-a' and id = 'run-invalid'"
        )
        assert await child_cursor.fetchone() is None
        event_cursor = await conn.execute(
            "select id from run_events where tenant_id = 'tenant-a' and id = 'event-invalid'"
        )
        assert await event_cursor.fetchone() is None
    finally:
        await conn.execute(sql.SQL("drop schema if exists {} cascade").format(sql.Identifier(schema_name)))
        await conn.close()
