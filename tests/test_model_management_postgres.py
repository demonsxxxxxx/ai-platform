from __future__ import annotations

import os
from pathlib import Path
import uuid

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
import pytest

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
async def test_model_inheritance_failure_rolls_back_child_run_and_event() -> None:
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
              execution_kind, status, model_id, model_value
            ) values (
              'run-source', 'tenant-a', 'workspace-a', 'session-a', 'user-a', 'agent-a',
              'harness_chat', 'failed', 'legacy-default', 'legacy-default'
            )
            """
        )

        with pytest.raises(ValueError, match="run_model_child_source_mismatch"):
            async with conn.transaction():
                await conn.execute(
                    """
                    insert into runs(
                      id, tenant_id, workspace_id, session_id, user_id, agent_id,
                      execution_kind, status
                    ) values (
                      'run-child', 'tenant-a', 'workspace-a', 'session-a', 'user-a', 'agent-a',
                      'harness_chat', 'queued'
                    )
                    """
                )
                await conn.execute(
                    """
                    insert into run_events(id, tenant_id, run_id, event_type, stage)
                    values ('event-child', 'tenant-a', 'run-child', 'run.created', 'admission')
                    """
                )
                await inherit_run_model(
                    conn,
                    tenant_id="tenant-a",
                    source_run_id="run-source",
                    child_run_id="run-child",
                )

        child_cursor = await conn.execute(
            "select id from runs where tenant_id = 'tenant-a' and id = 'run-child'"
        )
        assert await child_cursor.fetchone() is None
        event_cursor = await conn.execute(
            "select id from run_events where tenant_id = 'tenant-a' and id = 'event-child'"
        )
        assert await event_cursor.fetchone() is None
    finally:
        await conn.execute(sql.SQL("drop schema if exists {} cascade").format(sql.Identifier(schema_name)))
        await conn.close()
