import asyncio
import os
from pathlib import Path
import uuid

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
import pytest

from app import repositories
from app.persistence_limits import RUN_INPUT_MAX_BYTES, RUN_STEP_PAYLOAD_MAX_BYTES


POSTGRES_DSN_ENV = "AI_PLATFORM_S0A_SCHEMA_TEST_DSN"


def _postgres_dsn() -> str:
    dsn = os.getenv(POSTGRES_DSN_ENV, "").strip()
    if not dsn:
        pytest.skip(f"{POSTGRES_DSN_ENV} is not configured")
    return dsn


async def _connection(dsn: str, schema_name: str) -> psycopg.AsyncConnection:
    return await psycopg.AsyncConnection.connect(
        dsn,
        options=f"-c search_path={schema_name}",
        row_factory=dict_row,
    )


async def _seed_run(admin: psycopg.AsyncConnection) -> None:
    await admin.execute("insert into tenants(id, name) values ('tenant-a', 'Tenant A')")
    await admin.execute("insert into workspaces(id, tenant_id, name) values ('workspace-a', 'tenant-a', 'A')")
    await admin.execute("insert into users(id, tenant_id, display_name) values ('user-a', 'tenant-a', 'A')")
    await admin.execute(
        "insert into agents(id, tenant_id, name, agent_type) values ('agent-a', 'tenant-a', 'A', 'chat')"
    )
    await admin.execute("insert into skills(id, name, version, executor_type) values ('skill-a', 'A', '1', 'fake')")
    await admin.execute(
        """
        insert into sessions(id, tenant_id, workspace_id, user_id, agent_id, title, status)
        values ('session-a', 'tenant-a', 'workspace-a', 'user-a', 'agent-a', 'A', 'active')
        """
    )
    await admin.execute(
        """
        insert into runs(
          id, tenant_id, workspace_id, session_id, user_id, agent_id, skill_id, status, input_json
        ) values (
          'run-a', 'tenant-a', 'workspace-a', 'session-a', 'user-a', 'agent-a', 'skill-a', 'running',
          jsonb_build_object('legacy', repeat('x', %s))
        )
        """,
        (RUN_INPUT_MAX_BYTES - 40,),
    )


@pytest.mark.asyncio
async def test_postgres_final_json_bounds_are_atomic_unicode_safe_and_concurrent():
    dsn = _postgres_dsn()
    schema_name = f"persistence_limits_{uuid.uuid4().hex}"
    admin = await psycopg.AsyncConnection.connect(dsn, autocommit=True, row_factory=dict_row)
    first = None
    second = None
    try:
        await admin.execute(sql.SQL("create schema {}").format(sql.Identifier(schema_name)))
        await admin.execute(sql.SQL("set search_path to {}").format(sql.Identifier(schema_name)))
        await admin.execute(Path("app/schema.sql").read_text(encoding="utf-8"))
        await _seed_run(admin)
        await admin.execute(
            """
            insert into run_steps(
              id, tenant_id, run_id, step_key, step_kind, status, title, sequence, payload_json
            ) values (
              'step-large', 'tenant-a', 'run-a', 'large', 'agent', 'running', 'Large', 1,
              jsonb_build_object('old', repeat('x', %s))
            )
            """,
            (RUN_STEP_PAYLOAD_MAX_BYTES - 30,),
        )

        first = await _connection(dsn, schema_name)
        with pytest.raises(repositories.RepositoryConflictError, match="run_input_too_large"):
            async with first.transaction():
                await repositories.update_run_input_execution_snapshot(
                    first,
                    tenant_id="tenant-a",
                    run_id="run-a",
                    execution_snapshot={"input": {"追加": "🚀" * 10}},
                )
        with pytest.raises(repositories.RepositoryConflictError, match="run_step_payload_too_large"):
            async with first.transaction():
                await repositories.upsert_run_step(
                    first,
                    tenant_id="tenant-a",
                    run_id="run-a",
                    step_key="large",
                    step_kind="agent",
                    status="running",
                    title="Large",
                    role=None,
                    sequence=1,
                    payload_json={"new": "药" * 10},
                )

        cursor = await first.execute(
            """
            select input_json ? 'input' as input_changed,
                   (select payload_json ? 'new' from run_steps where id = 'step-large') as step_changed
            from runs where id = 'run-a'
            """
        )
        assert await cursor.fetchone() == {"input_changed": False, "step_changed": False}
        await first.commit()

        async with first.transaction():
            await repositories.upsert_run_step(
                first,
                tenant_id="tenant-a",
                run_id="run-a",
                step_key="concurrent",
                step_kind="agent",
                status="running",
                title="Concurrent",
                role=None,
                sequence=2,
                payload_json={"seed": True},
            )
        second = await _connection(dsn, schema_name)

        async def merge(conn, payload):
            async with conn.transaction():
                return await repositories.upsert_run_step(
                    conn,
                    tenant_id="tenant-a",
                    run_id="run-a",
                    step_key="concurrent",
                    step_kind="agent",
                    status="running",
                    title="Concurrent",
                    role=None,
                    sequence=2,
                    payload_json=payload,
                )

        await asyncio.gather(merge(first, {"cjk": "药"}), merge(second, {"emoji": "🚀"}))
        cursor = await first.execute("select payload_json from run_steps where step_key = 'concurrent'")
        assert (await cursor.fetchone())["payload_json"] == {
            "seed": True,
            "cjk": "药",
            "emoji": "🚀",
        }
    finally:
        if first is not None:
            await first.close()
        if second is not None:
            await second.close()
        await admin.execute(sql.SQL("drop schema if exists {} cascade").format(sql.Identifier(schema_name)))
        await admin.close()
