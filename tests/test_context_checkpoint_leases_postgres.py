import hashlib
import os
import uuid
from contextlib import asynccontextmanager

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
import pytest

from app import schema_migrations
from app.context.infrastructure.checkpoint_build_postgres import (
    claim_checkpoint_build,
    fail_expired_checkpoint_builds,
    renew_checkpoint_lease,
    save_checkpoint_progress,
)


POSTGRES_DSN_ENV = "AI_PLATFORM_S0A_SCHEMA_TEST_DSN"
SCOPE = {
    "tenant_id": "tenant-a",
    "workspace_id": "workspace-a",
    "user_id": "user-a",
    "session_id": "session-a",
    "agent_id": "agent-a",
}


def _postgres_dsn() -> str:
    dsn = os.getenv(POSTGRES_DSN_ENV, "").strip()
    if not dsn:
        if os.getenv("GITHUB_ACTIONS", "").strip().lower() == "true":
            raise RuntimeError(f"{POSTGRES_DSN_ENV} must be configured in GitHub Actions")
        pytest.skip(f"{POSTGRES_DSN_ENV} is not configured")
    return dsn


def _transaction_factory(dsn: str, schema_name: str):
    @asynccontextmanager
    async def factory():
        conn = await psycopg.AsyncConnection.connect(
            dsn,
            options=f"-c search_path={schema_name}",
            row_factory=dict_row,
        )
        try:
            async with conn.transaction():
                yield conn
        finally:
            await conn.close()

    return factory


def _index_connection_factory(dsn: str, schema_name: str):
    async def factory():
        return await psycopg.AsyncConnection.connect(
            dsn,
            autocommit=True,
            options=f"-c search_path={schema_name}",
            row_factory=dict_row,
        )

    return factory


async def _seed_checkpoint_source(conn: psycopg.AsyncConnection) -> None:
    await conn.execute("insert into tenants(id, name) values ('tenant-a', 'Tenant A')")
    await conn.execute(
        "insert into workspaces(id, tenant_id, name) "
        "values ('workspace-a', 'tenant-a', 'Workspace A')"
    )
    await conn.execute(
        "insert into users(id, tenant_id, display_name) "
        "values ('user-a', 'tenant-a', 'User A')"
    )
    await conn.execute(
        "insert into agents(id, tenant_id, name, agent_type) "
        "values ('agent-a', 'tenant-a', 'Agent A', 'chat')"
    )
    await conn.execute(
        "insert into sessions(id, tenant_id, workspace_id, user_id, agent_id, title) "
        "values ('session-a', 'tenant-a', 'workspace-a', 'user-a', 'agent-a', 'Session A')"
    )
    await conn.execute(
        """
        insert into runs(
          id, tenant_id, workspace_id, session_id, user_id, agent_id,
          execution_kind, status, model_id, model_value, model_gateway_revision,
          max_input_tokens, max_output_tokens, session_generation
        ) values (
          'run-a', 'tenant-a', 'workspace-a', 'session-a', 'user-a', 'agent-a',
          'harness_chat', 'queued', 'model-a', 'provider-model-a', 1,
          32000, 2048, 1
        )
        """
    )
    await conn.execute(
        """
        insert into run_context_snapshots(
          id, tenant_id, workspace_id, user_id, session_id, run_id,
          context_kind, conversation_authority_json
        ) values (
          'ctx-a', 'tenant-a', 'workspace-a', 'user-a', 'session-a', 'run-a',
          'executor', jsonb_build_object('source_sha256', %s::text)
        )
        """,
        ("b" * 64,),
    )
    await conn.execute(
        """
        insert into provider_session_heads(
          tenant_id, workspace_id, user_id, session_id, agent_id, engine, active_run_id
        ) values (
          'tenant-a', 'workspace-a', 'user-a', 'session-a', 'agent-a', 'claude', 'run-a'
        )
        """
    )


@pytest.mark.asyncio
async def test_expired_checkpoint_lease_is_failed_reopened_and_old_writer_is_fenced():
    dsn = _postgres_dsn()
    schema_name = f"checkpoint_lease_{uuid.uuid4().hex}"
    admin = await psycopg.AsyncConnection.connect(dsn, autocommit=True, row_factory=dict_row)
    transaction_factory = _transaction_factory(dsn, schema_name)
    try:
        await admin.execute(sql.SQL("create schema {}").format(sql.Identifier(schema_name)))
        await schema_migrations.apply_migrations(
            transaction_factory=transaction_factory,
            index_connection_factory=_index_connection_factory(dsn, schema_name),
        )
        async with transaction_factory() as conn:
            await _seed_checkpoint_source(conn)
            first = await claim_checkpoint_build(
                conn,
                scope=SCOPE,
                run_id="run-a",
                source_snapshot_id="ctx-a",
                build_key_sha256="a" * 64,
                source_sha256="b" * 64,
                predecessor_checkpoint_id=None,
                predecessor_sha256="c" * 64,
            )
            assert first is not None
            boundary = {"created_at": "2026-09-19T00:00:00+00:00", "id": "msg-a"}
            await save_checkpoint_progress(
                conn,
                checkpoint_id=first["id"],
                lease_id=first["builder_lease_id"],
                run_id="run-a",
                source_sha256="d" * 64,
                covered_message_count=2,
                covered_turn_count=1,
                range_start=boundary,
                range_end=boundary,
                summary="Preserved partial summary.",
                summary_sha256=hashlib.sha256(b"Preserved partial summary.").hexdigest(),
                input_tokens=20,
                output_tokens=5,
            )
            await conn.execute(
                "update conversation_context_checkpoints "
                "set lease_not_after = clock_timestamp() - interval '1 second' where id = %s",
                (first["id"],),
            )

            assert await fail_expired_checkpoint_builds(conn, limit=10) == 1
            failed = await (
                await conn.execute(
                    "select state, builder_lease_id, lease_not_after "
                    "from conversation_context_checkpoints where id = %s",
                    (first["id"],),
                )
            ).fetchone()
            assert failed == {
                "state": "failed",
                "builder_lease_id": None,
                "lease_not_after": None,
            }

            reopened = await claim_checkpoint_build(
                conn,
                scope=SCOPE,
                run_id="run-a",
                source_snapshot_id="ctx-a",
                build_key_sha256="a" * 64,
                source_sha256="b" * 64,
                predecessor_checkpoint_id=None,
                predecessor_sha256="c" * 64,
            )
            assert reopened is not None
            assert reopened["id"] == first["id"]
            assert reopened["builder_lease_id"] != first["builder_lease_id"]
            assert reopened["covered_message_count"] == 2
            assert reopened["summary_text"] == "Preserved partial summary."

            with pytest.raises(ValueError, match="conversation_checkpoint_builder_fenced"):
                await renew_checkpoint_lease(
                    conn,
                    checkpoint_id=first["id"],
                    lease_id=first["builder_lease_id"],
                    run_id="run-a",
                )
            await renew_checkpoint_lease(
                conn,
                checkpoint_id=reopened["id"],
                lease_id=reopened["builder_lease_id"],
                run_id="run-a",
            )
    finally:
        await admin.execute(
            sql.SQL("drop schema if exists {} cascade").format(sql.Identifier(schema_name))
        )
        await admin.close()
