from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import time
import uuid

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
import pytest

from app.runs.application.diagnostics import RunDiagnosticsService
from app.runs.infrastructure.diagnostics_postgres import (
    PostgresRunDiagnosticsRepository,
)
from app.sandbox.api import (
    SDK_RUNTIME_DIAGNOSTICS_SCHEMA_VERSION,
    normalize_sdk_runtime_diagnostics,
)


POSTGRES_DSN_ENV = "AI_PLATFORM_S0A_SCHEMA_TEST_DSN"


def _postgres_dsn() -> str:
    dsn = os.getenv(POSTGRES_DSN_ENV, "").strip()
    if not dsn:
        pytest.skip(f"{POSTGRES_DSN_ENV} is not configured")
    return dsn


async def _seed_run(conn: psycopg.AsyncConnection) -> None:
    await conn.execute("insert into tenants(id, name) values ('tenant-a', 'Tenant A')")
    await conn.execute(
        "insert into workspaces(id, tenant_id, name) values ('workspace-a', 'tenant-a', 'A')"
    )
    await conn.execute(
        "insert into users(id, tenant_id, display_name) values ('user-a', 'tenant-a', 'A')"
    )
    await conn.execute(
        "insert into agents(id, tenant_id, name, agent_type) values "
        "('agent-a', 'tenant-a', 'A', 'chat')"
    )
    await conn.execute(
        "insert into skills(id, name, version, executor_type) values "
        "('skill-a', 'A', '1', 'fake')"
    )
    await conn.execute(
        """
        insert into sessions(id, tenant_id, workspace_id, user_id, agent_id, title, status)
        values ('session-a', 'tenant-a', 'workspace-a', 'user-a', 'agent-a', 'A', 'active')
        """
    )
    await conn.execute(
        """
        insert into runs(
          id, tenant_id, workspace_id, session_id, user_id, agent_id, skill_id, status
        ) values (
          'run-a', 'tenant-a', 'workspace-a', 'session-a', 'user-a',
          'agent-a', 'skill-a', 'running'
        )
        """
    )


def _runtime_diagnostics(error_code: str) -> dict:
    return {
        "schema_version": SDK_RUNTIME_DIAGNOSTICS_SCHEMA_VERSION,
        "error_code": error_code,
        "failure_source": "sdk_exception",
        "failure_stage": "model_wait",
        "sdk": {
            "exception_type": "TimeoutError",
            "exception_message": f"{error_code} detail",
        },
    }


@asynccontextmanager
async def _isolated_postgres_run():
    dsn = _postgres_dsn()
    schema_name = f"run_diagnostics_{uuid.uuid4().hex}"
    admin = await psycopg.AsyncConnection.connect(
        dsn,
        autocommit=True,
        row_factory=dict_row,
    )
    conn = None
    try:
        await admin.execute(
            sql.SQL("create schema {}").format(sql.Identifier(schema_name))
        )
        await admin.execute(
            sql.SQL("set search_path to {}").format(sql.Identifier(schema_name))
        )
        await admin.execute(Path("app/schema.sql").read_text(encoding="utf-8"))
        await _seed_run(admin)
        conn = await psycopg.AsyncConnection.connect(
            dsn,
            options=f"-c search_path={schema_name}",
            row_factory=dict_row,
        )
        yield dsn, schema_name, admin, conn
    finally:
        if conn is not None:
            await conn.close()
        await admin.execute(
            sql.SQL("drop schema if exists {} cascade").format(
                sql.Identifier(schema_name)
            )
        )
        await admin.close()


@pytest.mark.asyncio
async def test_postgres_run_diagnostics_are_tenant_scoped_bounded_and_idempotent():
    dsn = _postgres_dsn()
    schema_name = f"run_diagnostics_{uuid.uuid4().hex}"
    admin = await psycopg.AsyncConnection.connect(
        dsn,
        autocommit=True,
        row_factory=dict_row,
    )
    conn = None
    try:
        await admin.execute(
            sql.SQL("create schema {}").format(sql.Identifier(schema_name))
        )
        await admin.execute(
            sql.SQL("set search_path to {}").format(sql.Identifier(schema_name))
        )
        await admin.execute(Path("app/schema.sql").read_text(encoding="utf-8"))
        await _seed_run(admin)
        conn = await psycopg.AsyncConnection.connect(
            dsn,
            options=f"-c search_path={schema_name}",
            row_factory=dict_row,
        )
        service = RunDiagnosticsService(
            persistence=PostgresRunDiagnosticsRepository(),
            normalize_runtime_diagnostics=normalize_sdk_runtime_diagnostics,
            runtime_diagnostics_schema_version=SDK_RUNTIME_DIAGNOSTICS_SCHEMA_VERSION,
        )
        first_result = {
            "message": "safe",
            "runtime_diagnostics": _runtime_diagnostics("provider_timeout"),
        }
        async with conn.transaction():
            public_result = await service.capture_failure_result(
                conn,
                tenant_id="tenant-a",
                run_id="run-a",
                attempt_id="attempt-a",
                source="worker_executor",
                stage="terminalization",
                error_code="executor_http_failure",
                result_json=first_result,
            )
            await service.capture_failure_result(
                conn,
                tenant_id="tenant-a",
                run_id="run-a",
                attempt_id="attempt-a",
                source="worker_executor",
                stage="terminalization",
                error_code="executor_http_failure",
                result_json=first_result,
            )
        async with conn.transaction():
            await service.capture_failure_result(
                conn,
                tenant_id="tenant-a",
                run_id="run-a",
                attempt_id="attempt-a",
                source="executor_reconciler",
                stage="reconciliation",
                error_code="terminal_reconciliation_failed",
                result_json={
                    "runtime_diagnostics": _runtime_diagnostics(
                        "terminal_reconciliation_failed"
                    )
                },
            )
        async with conn.transaction():
            snapshot = await service.read_admin(
                conn,
                tenant_id="tenant-a",
                run_id="run-a",
            )
            foreign = await service.read_admin(
                conn,
                tenant_id="other-tenant",
                run_id="run-a",
            )
        cursor = await conn.execute(
            "select revision, payload_json from run_diagnostics "
            "where tenant_id = 'tenant-a' and run_id = 'run-a'"
        )
        stored = await cursor.fetchone()

        assert public_result == {"message": "safe"}
        assert stored["revision"] == 2
        assert len(stored["payload_json"]["observations"]) == 2
        assert snapshot["coverage"] == "full"
        assert snapshot["root"]["error_code"] == "provider_timeout"
        assert snapshot["handling"][-1]["error_code"] == (
            "terminal_reconciliation_failed"
        )
        assert foreign is None
        with pytest.raises(psycopg.errors.CheckViolation):
            await admin.execute(
                "update run_diagnostics "
                "set payload_json = payload_json - 'schema_version' "
                "where tenant_id = 'tenant-a' and run_id = 'run-a'"
            )
        with pytest.raises(psycopg.errors.CheckViolation):
            await admin.execute(
                "update run_diagnostics "
                "set payload_json = jsonb_set(payload_json, '{schema_version}', 'null'::jsonb) "
                "where tenant_id = 'tenant-a' and run_id = 'run-a'"
            )
    finally:
        if conn is not None:
            await conn.close()
        await admin.execute(
            sql.SQL("drop schema if exists {} cascade").format(
                sql.Identifier(schema_name)
            )
        )
        await admin.close()


@pytest.mark.asyncio
async def test_postgres_diagnostic_constraint_failure_keeps_outer_terminal_write():
    async with _isolated_postgres_run() as (_dsn, _schema, admin, conn):
        await admin.execute(
            "alter table run_diagnostics add constraint reject_diagnostic_write "
            "check (revision < 0) not valid"
        )
        service = RunDiagnosticsService(
            persistence=PostgresRunDiagnosticsRepository(write_timeout_seconds=0.1),
            normalize_runtime_diagnostics=normalize_sdk_runtime_diagnostics,
            runtime_diagnostics_schema_version=SDK_RUNTIME_DIAGNOSTICS_SCHEMA_VERSION,
        )

        async with conn.transaction():
            cursor = await conn.execute(
                "select current_setting('lock_timeout') as lock_timeout, "
                "current_setting('statement_timeout') as statement_timeout"
            )
            before = dict(await cursor.fetchone())
            public_result = await service.capture_failure_result(
                conn,
                tenant_id="tenant-a",
                run_id="run-a",
                attempt_id="attempt-a",
                source="worker_executor",
                stage="terminalization",
                error_code="executor_failure",
                result_json={
                    "message": "safe",
                    "runtime_diagnostics": _runtime_diagnostics("provider_timeout"),
                },
            )
            cursor = await conn.execute(
                "select current_setting('lock_timeout') as lock_timeout, "
                "current_setting('statement_timeout') as statement_timeout"
            )
            after = dict(await cursor.fetchone())
            await conn.execute(
                "update runs set status = 'failed', result_json = %s::jsonb "
                "where tenant_id = 'tenant-a' and id = 'run-a'",
                (json.dumps(public_result),),
            )

        cursor = await conn.execute(
            "select status, result_json from runs where id = 'run-a'"
        )
        run = await cursor.fetchone()
        cursor = await conn.execute("select count(*) as count from run_diagnostics")
        diagnostic_count = int((await cursor.fetchone())["count"])

        assert before == after
        assert run == {"status": "failed", "result_json": {"message": "safe"}}
        assert diagnostic_count == 0


@pytest.mark.asyncio
async def test_postgres_diagnostic_row_lock_timeout_keeps_outer_transaction_usable():
    async with _isolated_postgres_run() as (dsn, schema_name, _admin, conn):
        service = RunDiagnosticsService(
            persistence=PostgresRunDiagnosticsRepository(write_timeout_seconds=0.05),
            normalize_runtime_diagnostics=normalize_sdk_runtime_diagnostics,
            runtime_diagnostics_schema_version=SDK_RUNTIME_DIAGNOSTICS_SCHEMA_VERSION,
        )
        async with conn.transaction():
            await service.capture_failure_result(
                conn,
                tenant_id="tenant-a",
                run_id="run-a",
                attempt_id="attempt-a",
                source="worker_executor",
                stage="terminalization",
                error_code="executor_failure",
                result_json={
                    "runtime_diagnostics": _runtime_diagnostics("provider_timeout")
                },
            )

        blocker = await psycopg.AsyncConnection.connect(
            dsn,
            options=f"-c search_path={schema_name}",
            row_factory=dict_row,
        )
        try:
            async with blocker.transaction():
                await blocker.execute(
                    "select revision from run_diagnostics "
                    "where tenant_id = 'tenant-a' and run_id = 'run-a' for update"
                )
                async with conn.transaction():
                    cursor = await conn.execute(
                        "select current_setting('lock_timeout') as lock_timeout, "
                        "current_setting('statement_timeout') as statement_timeout"
                    )
                    before = dict(await cursor.fetchone())
                    started_at = time.monotonic()
                    await service.capture_failure_result(
                        conn,
                        tenant_id="tenant-a",
                        run_id="run-a",
                        attempt_id="attempt-a",
                        source="executor_reconciler",
                        stage="terminalization",
                        error_code="terminal_reconciliation_failed",
                        result_json={
                            "runtime_diagnostics": _runtime_diagnostics(
                                "terminal_reconciliation_failed"
                            )
                        },
                    )
                    elapsed = time.monotonic() - started_at
                    cursor = await conn.execute(
                        "select current_setting('lock_timeout') as lock_timeout, "
                        "current_setting('statement_timeout') as statement_timeout"
                    )
                    after = dict(await cursor.fetchone())
                    await conn.execute(
                        "update runs set status = 'failed' "
                        "where tenant_id = 'tenant-a' and id = 'run-a'"
                    )
        finally:
            await blocker.close()

        cursor = await conn.execute(
            "select r.status, d.revision "
            "from runs r join run_diagnostics d on d.run_id = r.id "
            "where r.tenant_id = 'tenant-a' and r.id = 'run-a'"
        )
        state = await cursor.fetchone()

        assert before == after
        assert elapsed < 5
        assert state == {"status": "failed", "revision": 1}
