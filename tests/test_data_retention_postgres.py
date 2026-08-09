import asyncio
import os
from pathlib import Path
import uuid

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
import pytest

from app import repositories


POSTGRES_DSN_ENV = "AI_PLATFORM_S0A_SCHEMA_TEST_DSN"


def _postgres_dsn() -> str:
    dsn = os.getenv(POSTGRES_DSN_ENV, "").strip()
    if not dsn:
        pytest.skip(f"{POSTGRES_DSN_ENV} is not configured")
    return dsn


async def _scoped_connection(dsn: str, schema_name: str) -> psycopg.AsyncConnection:
    return await psycopg.AsyncConnection.connect(
        dsn,
        options=f"-c search_path={schema_name}",
        row_factory=dict_row,
    )


@pytest.mark.asyncio
async def test_snapshot_artifact_lock_prevents_concurrent_retention_tombstone():
    dsn = _postgres_dsn()
    schema_name = f"retention_race_{uuid.uuid4().hex}"
    admin = await psycopg.AsyncConnection.connect(dsn, autocommit=True, row_factory=dict_row)
    snapshot_conn = None
    retention_conn = None
    try:
        await admin.execute(sql.SQL("create schema {}").format(sql.Identifier(schema_name)))
        await admin.execute(sql.SQL("set search_path to {}").format(sql.Identifier(schema_name)))
        await admin.execute(Path("app/schema.sql").read_text(encoding="utf-8"))
        await admin.execute("insert into tenants(id, name) values ('tenant-a', 'Tenant A')")
        await admin.execute("insert into workspaces(id, tenant_id, name) values ('workspace-a', 'tenant-a', 'A')")
        await admin.execute("insert into users(id, tenant_id, display_name) values ('user-a', 'tenant-a', 'A')")
        await admin.execute(
            "insert into agents(id, tenant_id, name, agent_type) values ('agent-a', 'tenant-a', 'A', 'chat')"
        )
        await admin.execute(
            "insert into skills(id, name, version, executor_type) values ('skill-a', 'A', '1', 'fake')"
        )
        await admin.execute(
            """
            insert into sessions(id, tenant_id, workspace_id, user_id, agent_id, title, status)
            values ('session-a', 'tenant-a', 'workspace-a', 'user-a', 'agent-a', 'A', 'archived')
            """
        )
        await admin.execute(
            """
            insert into runs(id, tenant_id, workspace_id, session_id, user_id, agent_id, skill_id, status)
            values ('run-a', 'tenant-a', 'workspace-a', 'session-a', 'user-a', 'agent-a', 'skill-a', 'succeeded')
            """
        )
        await admin.execute(
            """
            insert into artifacts(
              id, tenant_id, run_id, artifact_type, label, content_type,
              storage_key, size_bytes, expires_at
            ) values (
              'artifact-a', 'tenant-a', 'run-a', 'text', 'A', 'text/plain',
              'artifacts/a', 1, clock_timestamp() + interval '0.5 seconds'
            )
            """
        )

        snapshot_conn = await _scoped_connection(dsn, schema_name)
        retention_conn = await _scoped_connection(dsn, schema_name)
        async with snapshot_conn.transaction():
            await repositories.create_context_snapshot(
                snapshot_conn,
                tenant_id="tenant-a",
                workspace_id="workspace-a",
                user_id="user-a",
                session_id="session-a",
                run_id="run-a",
                trace_id="trace-a",
                context_kind="executor",
                included_message_ids=[],
                included_file_ids=[],
                included_artifact_ids=["artifact-a"],
                included_memory_record_ids=[],
                redaction_summary_json={},
                payload_json={},
            )
            await asyncio.sleep(0.6)
            async with retention_conn.transaction():
                assert await repositories.queue_expired_artifacts_for_deletion(retention_conn) == []

        async with retention_conn.transaction():
            assert await repositories.queue_expired_artifacts_for_deletion(retention_conn) == []
        cursor = await retention_conn.execute(
            """
            select artifacts.lifecycle_state,
                   (select count(*) from object_deletion_outbox) as outbox_count,
                   (select count(*) from run_context_snapshots) as snapshot_count
            from artifacts where id = 'artifact-a'
            """
        )
        assert await cursor.fetchone() == {
            "lifecycle_state": "active",
            "outbox_count": 0,
            "snapshot_count": 1,
        }
    finally:
        if snapshot_conn is not None:
            await snapshot_conn.close()
        if retention_conn is not None:
            await retention_conn.close()
        await admin.execute(sql.SQL("drop schema if exists {} cascade").format(sql.Identifier(schema_name)))
        await admin.close()
