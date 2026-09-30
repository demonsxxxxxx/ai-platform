"""Real PostgreSQL coverage projection for an unexecuted first conversation turn."""

import os
import uuid

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
import pytest

from app.context.domain.provider_sessions import (
    ProviderSessionConflictError,
    ProviderSessionScope,
)
from app.context.infrastructure.provider_epochs import read_provider_coverage


@pytest.fixture
async def coverage_db():
    dsn = os.getenv("AI_PLATFORM_S0A_SCHEMA_TEST_DSN", "")
    if not dsn:
        pytest.skip("AI_PLATFORM_S0A_SCHEMA_TEST_DSN is not configured")
    schema = "coverage_" + uuid.uuid4().hex
    async with await psycopg.AsyncConnection.connect(
        dsn, autocommit=True, row_factory=dict_row
    ) as conn:
        try:
            await conn.execute(
                sql.SQL("create schema {}").format(sql.Identifier(schema))
            )
            await conn.execute(
                sql.SQL("set search_path to {}").format(sql.Identifier(schema))
            )
            # Only columns read by this projection; lifecycle transitions have
            # separate owning integration coverage against the full schema.
            await conn.execute("""
                create table sessions(id text, tenant_id text, workspace_id text, user_id text, agent_id text);
                create table runs(id text, tenant_id text, workspace_id text, user_id text,
                    session_id text, agent_id text, session_generation int, status text, started_at timestamptz);
                create table run_attempts(tenant_id text, run_id text, status text, started_at timestamptz);
                create table messages(tenant_id text, session_id text, run_id text, role text);
                create table provider_session_heads(tenant_id text, workspace_id text, user_id text,
                    session_id text, agent_id text, engine text, current_epoch_id text, active_run_id text);
                create table provider_session_epochs(id text, tenant_id text, workspace_id text,
                    user_id text, session_id text, agent_id text, engine text, state text,
                    writer_run_id text, writer_attempt_id text, writer_owner_generation int,
                    coverage_source_sha256 text, coverage_message_count int,
                    coverage_through_generation int, entry_count int, transcript_bytes int);
                insert into sessions values('session', 'tenant', 'workspace', 'user', 'agent');
                insert into runs values('current', 'tenant', 'workspace', 'user', 'session', 'agent', 2, 'queued', null);
                insert into runs values('prior', 'tenant', 'workspace', 'user', 'session', 'agent', 1, 'cancelled', null);
                insert into messages values('tenant', 'session', 'prior', 'user');
                insert into provider_session_heads values('tenant', 'workspace', 'user', 'session', 'agent', 'claude', null, 'current');
            """)
            yield conn
        finally:
            await conn.execute(
                sql.SQL("drop schema {} cascade").format(sql.Identifier(schema))
            )


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["cancelled", "failed"])
@pytest.mark.parametrize("terminal_attempt", [False, True])
async def test_unexecuted_terminal_first_run_does_not_poison_next_turn(
    coverage_db, status, terminal_attempt
):
    await coverage_db.execute(
        "update runs set status = %s where id = %s", (status, "prior")
    )
    if terminal_attempt:
        await coverage_db.execute(
            "insert into run_attempts values(%s, %s, %s, null)",
            ("tenant", "prior", status),
        )
    result = await read_provider_coverage(
        coverage_db,
        scope=ProviderSessionScope("tenant", "workspace", "user", "session", "agent"),
        run_id="current",
        session_generation=2,
    )
    assert result["message_count"] == 0
    assert result["coverage_through_generation"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        "update runs set started_at = now() where id = 'prior'",
        "update runs set status = 'running' where id = 'prior'",
        "delete from runs where id = 'prior'",
        "update messages set role = 'assistant'",
        "insert into run_attempts values('tenant', 'prior', 'failed', now())",
        "insert into run_attempts values('tenant', 'prior', 'claimed', null)",
        "update runs set workspace_id = 'other' where id = 'prior'",
    ],
)
async def test_unknown_or_executed_first_run_remains_ineligible_for_empty_start(
    coverage_db, change
):
    await coverage_db.execute(change)
    with pytest.raises(
        ProviderSessionConflictError, match="provider_session_requires_new_conversation"
    ):
        await read_provider_coverage(
            coverage_db,
            scope=ProviderSessionScope(
                "tenant", "workspace", "user", "session", "agent"
            ),
            run_id="current",
            session_generation=2,
        )
