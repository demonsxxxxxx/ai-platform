"""Run status vocabulary and its PostgreSQL constraint contract."""

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
import os
import re
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
import pytest

from app import schema_migrations
from app.runs.domain.execution_spec import (
    EXECUTION_SPEC_SCHEMA_VERSION,
    compile_execution_spec,
)
from app.runs.domain.run_status import RUN_STATUS_VALUES
from app.runs.infrastructure.postgres import create_run_attempt, transition_run_attempt
from tests.support.db_transactions import event_loop_policy as event_loop_policy


POSTGRES_DSN_ENV = "AI_PLATFORM_S0A_SCHEMA_TEST_DSN"
RUN_STATUS_CONSTRAINT = "chk_runs_status"
RUN_STATUS_UPGRADE_MARKER = "-- Run status constraint upgrade:"


def _postgres_dsn() -> str:
    dsn = os.getenv(POSTGRES_DSN_ENV, "").strip()
    if not dsn:
        pytest.skip(f"{POSTGRES_DSN_ENV} is not configured")
    return dsn


@asynccontextmanager
async def _migrated_schema():
    dsn = _postgres_dsn()
    schema_name = f"run_status_{uuid4().hex}"
    admin = await psycopg.AsyncConnection.connect(
        dsn, autocommit=True, row_factory=dict_row
    )
    await admin.execute(sql.SQL("create schema {}").format(sql.Identifier(schema_name)))

    @asynccontextmanager
    async def transaction_factory():
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

    async def index_connection_factory():
        return await psycopg.AsyncConnection.connect(
            dsn,
            autocommit=True,
            options=f"-c search_path={schema_name}",
            row_factory=dict_row,
        )

    try:
        await schema_migrations.apply_migrations(
            transaction_factory=transaction_factory,
            index_connection_factory=index_connection_factory,
        )
        yield schema_name, transaction_factory, index_connection_factory
    finally:
        await admin.execute(
            sql.SQL("drop schema if exists {} cascade").format(
                sql.Identifier(schema_name)
            )
        )
        await admin.close()


def _run_status_upgrade_sql() -> str:
    source = schema_migrations.schema_sql()
    return source[source.index(RUN_STATUS_UPGRADE_MARKER) :]


def _run_status_definition() -> str:
    return next(
        definition
        for relation, name, _, definition in schema_migrations.CRITICAL_CONSTRAINT_DEFINITIONS
        if relation == "runs" and name == RUN_STATUS_CONSTRAINT
    )


async def _seed_run_parents(conn) -> None:
    await conn.execute(
        "insert into tenants(id, name) values ('status-tenant', 'Status Tenant')"
    )
    await conn.execute(
        "insert into workspaces(id, tenant_id, name) "
        "values ('status-workspace', 'status-tenant', 'Status Workspace')"
    )
    await conn.execute(
        "insert into users(id, tenant_id, display_name) "
        "values ('status-user', 'status-tenant', 'Status User')"
    )
    await conn.execute(
        "insert into agents(id, tenant_id, name, agent_type) "
        "values ('status-agent', 'status-tenant', 'Status Agent', 'chat')"
    )
    await conn.execute(
        "insert into skills(id, name, version, executor_type) "
        "values ('status-skill', 'Status Skill', '1.0.0', 'fake')"
    )
    await conn.execute(
        "insert into sessions(id, tenant_id, workspace_id, user_id, agent_id, title) "
        "values ('status-session', 'status-tenant', 'status-workspace', "
        "'status-user', 'status-agent', 'Status Session')"
    )


async def _insert_run(conn, run_id: str, status: str) -> None:
    await conn.execute(
        """
        insert into runs(
          id, tenant_id, workspace_id, session_id, user_id, agent_id,
          execution_kind, skill_id, status
        ) values (
          %s, 'status-tenant', 'status-workspace', 'status-session',
          'status-user', 'status-agent', 'skill', 'status-skill', %s
        )
        """,
        (run_id, status),
    )


def _execution_spec(run_id: str):
    return compile_execution_spec(
        {
            "schema_version": EXECUTION_SPEC_SCHEMA_VERSION,
            "run_payload_schema_version": "ai-platform.run-payload.v1",
            "tenant_id": "status-tenant",
            "workspace_id": "status-workspace",
            "user_id": "status-user",
            "session_id": "status-session",
            "run_id": run_id,
            "agent_id": "status-agent",
            "execution_kind": "skill",
            "skill_id": "status-skill",
            "file_ids": [],
            "input": {"message": "hello"},
            "executor_type": "fake",
            "trace_id": f"trace-{run_id}",
            "skill_version": "1.0.0",
            "release_decision": {
                "schema_version": "ai-platform.skill-release-decision.v1",
                "selected_version": "1.0.0",
            },
            "skill_manifests": [
                {"skill_id": "status-skill", "content_hash": "1.0.0"}
            ],
            "context_snapshot_id": "context-status",
            "context_snapshot": {"context_snapshot_id": "context-status"},
            "context_pack": {},
            "model_id": "model-status",
            "model_value": "model-status",
            "agent_profile": {},
        }
    )


def test_run_status_constraint_source_matches_the_runs_domain_vocabulary():
    table_sql = schema_migrations.schema_sql()
    runs_table = table_sql[
        table_sql.index("create table if not exists runs (") : table_sql.index(
            "create index if not exists idx_runs_tenant_created"
        )
    ]
    check_sql = runs_table.split("constraint chk_runs_status check", 1)[1].split(
        "constraint chk_runs_model_token_limits", 1
    )[0]
    inline_values = set(re.findall(r"'([^']+)'::text", check_sql))
    readiness_values = set(re.findall(r"'([^']+)'::text", _run_status_definition()))
    upgrade_sql = _run_status_upgrade_sql().lower()

    assert inline_values == readiness_values == RUN_STATUS_VALUES
    assert ("runs", RUN_STATUS_CONSTRAINT) in schema_migrations.CRITICAL_CONSTRAINTS
    assert schema_migrations.TARGET_SCHEMA_VERSION == schema_migrations.RUN_STATUS_SCHEMA_VERSION
    assert "set_config('lock_timeout', '2s', true)" in upgrade_sql
    assert "not valid" in upgrade_sql
    assert "validate constraint chk_runs_status" in upgrade_sql
    assert "runs_status_constraint_definition_mismatch" in upgrade_sql
    assert "set status" not in upgrade_sql


@pytest.mark.asyncio
async def test_real_postgres_empty_schema_and_repeat_upgrade_are_ready():
    async with _migrated_schema() as (_, transaction_factory, index_factory):
        repeat = await schema_migrations.apply_migrations(
            transaction_factory=transaction_factory,
            index_connection_factory=index_factory,
        )
        async with transaction_factory() as conn:
            readiness = await schema_migrations.schema_status(conn)
            constraint = await (
                await conn.execute(
                    """
                    select convalidated, pg_get_constraintdef(oid, true) as definition
                    from pg_constraint
                    where conrelid = 'runs'::regclass and conname = %s
                    """,
                    (RUN_STATUS_CONSTRAINT,),
                )
            ).fetchone()

        assert repeat["status"] == "current"
        assert readiness["ready"] is True
        assert constraint is not None and constraint["convalidated"] is True
        assert "dead_letter" in constraint["definition"]


@pytest.mark.asyncio
async def test_real_postgres_status_upgrade_preserves_legacy_alias_rows():
    aliases = ("canceled", "dead-letter", "dead_letter", "dead-lettered")
    async with _migrated_schema() as (_, transaction_factory, _):
        async with transaction_factory() as conn:
            await conn.execute(
                "alter table runs drop constraint chk_runs_status"
            )
            await _seed_run_parents(conn)
            for index, status in enumerate(aliases):
                await _insert_run(conn, f"legacy-{index}", status)
        async with transaction_factory() as conn:
            await conn.execute(_run_status_upgrade_sql())
            rows = await (
                await conn.execute(
                    "select id, status from runs order by id"
                )
            ).fetchall()
            readiness = await schema_migrations.schema_status(conn)

        assert [row["status"] for row in rows] == list(aliases)
        assert readiness["ready"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_status", ["unexpected-history-value", None])
async def test_real_postgres_status_upgrade_rejects_unknown_and_null_without_rewrite(
    bad_status,
):
    async with _migrated_schema() as (_, transaction_factory, _):
        async with transaction_factory() as conn:
            await conn.execute(
                "alter table runs drop constraint chk_runs_status"
            )
            if bad_status is None:
                await conn.execute("alter table runs alter column status drop not null")
            await _seed_run_parents(conn)
            await _insert_run(conn, "bad-history", bad_status)

        with pytest.raises(psycopg.errors.CheckViolation):
            async with transaction_factory() as conn:
                await conn.execute(_run_status_upgrade_sql())

        async with transaction_factory() as conn:
            row = await (
                await conn.execute(
                    "select status from runs where id = 'bad-history'"
                )
            ).fetchone()
            constraint = await (
                await conn.execute(
                    "select 1 from pg_constraint "
                    "where conrelid = 'runs'::regclass and conname = %s",
                    (RUN_STATUS_CONSTRAINT,),
                )
            ).fetchone()
        assert row["status"] == bad_status
        assert constraint is None


@pytest.mark.asyncio
async def test_real_postgres_readiness_checks_status_constraint_definition_and_validation():
    async with _migrated_schema() as (_, transaction_factory, _):
        async with transaction_factory() as conn:
            await conn.execute(
                "alter table runs drop constraint chk_runs_status"
            )
            await conn.execute(
                "alter table runs add constraint chk_runs_status "
                "check (status is not null)"
            )
            wrong = await schema_migrations.schema_status(conn)
        assert wrong["ready"] is False
        assert wrong["constraints_current"] is True
        assert wrong["constraint_definitions_current"] is False

        with pytest.raises(psycopg.errors.RaiseException):
            async with transaction_factory() as conn:
                await conn.execute(_run_status_upgrade_sql())

        async with transaction_factory() as conn:
            await conn.execute(
                "alter table runs drop constraint chk_runs_status"
            )
            await conn.execute(
                sql.SQL("alter table runs add constraint {} {} not valid").format(
                    sql.Identifier(RUN_STATUS_CONSTRAINT),
                    sql.SQL(_run_status_definition()),
                )
            )
            unvalidated = await schema_migrations.schema_status(conn)
        assert unvalidated["ready"] is False
        assert unvalidated["constraints_current"] is False
        assert unvalidated["constraint_definitions_current"] is False

        async with transaction_factory() as conn:
            await conn.execute(_run_status_upgrade_sql())
            repaired = await schema_migrations.schema_status(conn)
        assert repaired["ready"] is True


@pytest.mark.asyncio
async def test_real_postgres_run_attempt_projection_remains_legal_under_run_status_check():
    async with _migrated_schema() as (_, transaction_factory, _):
        run_id = "attempt-projection-run"
        async with transaction_factory() as conn:
            await _seed_run_parents(conn)
            await _insert_run(conn, run_id, "queued")
            attempt = await create_run_attempt(
                conn,
                tenant_id="status-tenant",
                run_id=run_id,
                attempt_id="attempt-projection",
                ordinal=1,
                owner_kind="queue_worker",
                owner_id="status-worker",
                queue_attempt_id="status-queue-attempt",
                execution_spec=_execution_spec(run_id),
            )
            assert attempt["status"] == "created"
            now = datetime.now(timezone.utc)
            await transition_run_attempt(
                conn,
                tenant_id="status-tenant",
                run_id=run_id,
                attempt_id="attempt-projection",
                expected_status="created",
                requested_status="queued",
                expected_owner_kind="queue_worker",
                expected_owner_id="status-worker",
                expected_owner_generation=1,
                queue_message_id="b" * 64,
                last_heartbeat_at=now,
                lease_expires_at=now + timedelta(minutes=5),
            )
            await transition_run_attempt(
                conn,
                tenant_id="status-tenant",
                run_id=run_id,
                attempt_id="attempt-projection",
                expected_status="queued",
                requested_status="claimed",
                expected_owner_kind="queue_worker",
                expected_owner_id="status-worker",
                expected_owner_generation=2,
            )
            projected = await (
                await conn.execute(
                    """
                    select runs.status as run_status, run_attempts.status as attempt_status
                    from runs join run_attempts
                      on run_attempts.tenant_id = runs.tenant_id
                     and run_attempts.run_id = runs.id
                    where runs.tenant_id = 'status-tenant' and runs.id = %s
                    """,
                    (run_id,),
                )
            ).fetchone()
            with pytest.raises(psycopg.errors.CheckViolation):
                async with conn.transaction():
                    await conn.execute(
                        "update runs set status = 'completed' where id = %s",
                        (run_id,),
                    )

        assert projected == {"run_status": "running", "attempt_status": "claimed"}
