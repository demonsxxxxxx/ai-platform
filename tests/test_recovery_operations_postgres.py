"""Real PostgreSQL selectors for the Execution effect-evidence fragment and manual producer."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg.rows import dict_row

from app.execution.infrastructure.recovery_operations_postgres import PostgresRecoveryOperations
from app.execution.api import (
    RecoveryInvocation, RecoveryInvocationAuthority,
    RecoveryOperationScope, RecoveryOperationsService,
)
from app.runs.api import RunInputsService
from app.runs.infrastructure import inputs_postgres
from tests.support.db_transactions import event_loop_policy  # noqa: F401


@pytest.fixture
async def operation_db():
    dsn = os.getenv("AI_PLATFORM_S0A_SCHEMA_TEST_DSN")
    if not dsn:
        pytest.skip("AI_PLATFORM_S0A_SCHEMA_TEST_DSN is not configured")
    schema = "execution_effect_" + uuid4().hex
    admin = await psycopg.AsyncConnection.connect(dsn, autocommit=True, row_factory=dict_row)
    await admin.execute(f"create schema {schema}")

    @asynccontextmanager
    async def connect():
        conn = await psycopg.AsyncConnection.connect(dsn, options=f"-c search_path={schema}", row_factory=dict_row)
        try:
            yield conn
        finally:
            await conn.close()

    scope = RecoveryOperationScope("tenant-a", "workspace-a", "user-a", "session-a")
    source = RecoveryInvocation(scope, "source-run", "source-attempt", 1, "source-native", "mcp__vendor__write")
    current = RecoveryInvocation(scope, "recovery-run", "recovery-attempt", 2, "current-native", source.tool_identity)
    repo = PostgresRecoveryOperations()
    inputs = RunInputsService(inputs_postgres, lambda text: text)

    async def authority(conn, *, invocation):
        cursor = await conn.execute(
            """select r.id from runs r join run_attempts a on a.run_id=r.id and a.tenant_id=r.tenant_id
               where r.tenant_id=%s and r.workspace_id=%s and r.user_id=%s and r.session_id=%s
                 and r.id=%s and a.id=%s and a.owner_generation=%s
                 and r.status='running' and r.cancel_requested_at is null and a.status='running'
               for update of r, a""",
            (*invocation.scope.mapping().values(), invocation.run_id, invocation.attempt_id, invocation.owner_generation),
        )
        return RecoveryInvocationAuthority(invocation.run_id == "recovery-run") if await cursor.fetchone() else None

    async def frozen(conn, *, invocation, operation, frozen_binding_ref):
        return invocation == current and frozen_binding_ref == "host-frozen-join" and operation["native_tool_use_id"] == "source-native"

    service = RecoveryOperationsService(repo, authority, frozen, inputs.publish_platform_question, lambda text: text)
    inputs = replace(inputs, on_manual_answer=service.on_manual_answer)
    root = Path(__file__).resolve().parents[1]
    try:
        async with connect() as conn, conn.transaction():
            await conn.execute("""
              create table runs (tenant_id text, id text, user_id text, workspace_id text,
                session_id text, status text, cancel_requested_at timestamptz, primary key(tenant_id,id));
              create table run_attempts (id text primary key, tenant_id text, run_id text,
                owner_generation bigint, status text, ordinal integer);
              insert into runs values
                ('tenant-a','source-run','user-a','workspace-a','session-a','running',null),
                ('tenant-a','recovery-run','user-a','workspace-a','session-a','running',null);
              insert into run_attempts values
                ('source-attempt','tenant-a','source-run',1,'running',1),
                ('recovery-attempt','tenant-a','recovery-run',2,'running',1);
            """)
            shared = (root / "app/schema.sql").read_text(encoding="utf-8")
            start = shared.index("create table if not exists run_input_sessions")
            await conn.execute(shared[start:shared.index("create or replace function", start)])
            await conn.execute((root / "app/execution/infrastructure/recovery_operations_schema.sql").read_text(encoding="utf-8"))
            await conn.execute("insert into run_input_sessions(tenant_id,run_id,attempt_id) values ('tenant-a','recovery-run','recovery-attempt')")
            receipt = await service.record_started(conn, invocation=source, tool_facts={"write_capable": True}, public_summary="合成写操作")
            key = receipt["operation_key"]
            await service.bind_invocation(conn, invocation=current, operation_key=key,
                                          frozen_binding_ref="host-frozen-join", public_summary="合成重试调用")
        yield connect, service, inputs, source, current, key
    finally:
        await admin.execute(f"drop schema {schema} cascade")
        await admin.close()


async def test_question_answer_verification_and_current_permission_commit_atomically(operation_db):
    connect, service, inputs, source, current, key = operation_db
    async with connect() as conn, conn.transaction():
        await service.record_reported(conn, invocation=source, operation_key=key, status="completed")
        evidence = await service.query(conn, invocation=current)
        assert evidence["decision"] == "wait"
        qid = evidence["question_id"]
    async with connect() as conn, conn.transaction():
        await inputs.submit(conn, tenant_id="tenant-a", user_id="user-a", run_id=current.run_id,
                            input_id=str(uuid4()), question_id=qid, answers={"q0": "o2"})
    async with connect() as conn, conn.transaction():
        evidence = await service.query(conn, invocation=current)
        assert evidence["decision"] == "allow" and evidence["permission_basis"] == "retry_not_effective"
        await service.record_started(conn, invocation=current, tool_facts={"write_capable": True}, public_summary="当前调用")
    async with connect() as conn, conn.transaction():
        assert (await service.query(conn, invocation=current))["reason_code"] == "operation_already_started"
        row = await service.persistence.get_operation(conn, scope=current.scope, operation_key=key)
        assert row["reported_status"] == "completed" and row["verified_outcome"] == "not_effective"
        assert row["verified_kind"] == "manual_answer" and row["verified_reference"]


async def test_failed_answer_transaction_does_not_publish_verification_or_permission(operation_db):
    connect, service, inputs, source, current, key = operation_db
    async with connect() as conn, conn.transaction():
        qid = (await service.query(conn, invocation=current))["question_id"]
    async def failing_hook(conn, **kwargs):
        await service.on_manual_answer(conn, **kwargs)
        raise RuntimeError("synthetic failure after evidence write")
    inputs = replace(inputs, on_manual_answer=failing_hook)
    async with connect() as conn:
        with pytest.raises(RuntimeError, match="synthetic failure"):
            async with conn.transaction():
                await inputs.submit(conn, tenant_id="tenant-a", user_id="user-a", run_id=current.run_id,
                                    input_id=str(uuid4()), question_id=qid, answers={"q0": "o2"})
    async with connect() as conn, conn.transaction():
        assert (await service.query(conn, invocation=current))["decision"] == "wait"
        row = await service.persistence.get_operation(conn, scope=current.scope, operation_key=key)
        assert row["verified_outcome"] == "unknown"
        assert (await inputs.persistence.get_question(conn, tenant_id="tenant-a", run_id=current.run_id,
                                                    attempt_id=current.attempt_id, question_id=qid))["status"] == "pending"


async def test_completed_source_is_not_permission_to_repeat_and_default_answer_has_no_authority(operation_db):
    connect, service, inputs, source, current, key = operation_db
    async with connect() as conn, conn.transaction():
        qid = (await service.query(conn, invocation=current))["question_id"]
        await inputs.submit(conn, tenant_id="tenant-a", user_id="user-a", run_id=current.run_id,
                            input_id=str(uuid4()), question_id=qid, answers={"q0": "o2"}, source="timeout_default")
        assert (await service.query(conn, invocation=current))["decision"] == "wait"
        await service.persistence.verify(conn, scope=current.scope, operation_key=key, outcome="succeeded",
                                         kind="authoritative_query", reference="synthetic-authoritative-receipt")
        # No later not-effective report can undo a verified success.
        await service.persistence.verify(conn, scope=current.scope, operation_key=key, outcome="not_effective",
                                         kind="manual_answer", reference="synthetic-late-answer")
    async with connect() as conn, conn.transaction():
        evidence = await service.query(conn, invocation=current)
        assert evidence["decision"] == "deny" and evidence["reason_code"] == "operation_already_completed"
        row = await service.persistence.get_operation(conn, scope=current.scope, operation_key=key)
        assert row["verified_outcome"] == "succeeded"
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            async with conn.transaction():
                await conn.execute("update execution_recovery_invocations set session_id='other-session'")
