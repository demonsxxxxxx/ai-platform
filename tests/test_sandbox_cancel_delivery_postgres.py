"""Durable executor cancel delivery against the real Sandbox schema."""
# ruff: noqa: F401,F811 -- pytest fixtures are registered through imports and parameters.
from contextlib import asynccontextmanager
import json
import uuid

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
import pytest

import app.schema_migrations as schema_migrations
from app.runs.domain.execution_spec import (
    EXECUTION_SPEC_SCHEMA_VERSION,
    compile_execution_spec,
)
from app.runs.infrastructure.postgres import (
    create_run_attempt,
    request_run_attempt_cancel,
    transition_run_attempt,
)
from app.sandbox.application.cancel_delivery import SandboxCancelDeliveryService
from app.sandbox.infrastructure import cancel_delivery_postgres
from tests.support.db_transactions import event_loop_policy as event_loop_policy
from tests.support.repository_fixtures import _run_control_postgres_dsn


@pytest.fixture
async def executor_acceptance_database(event_loop_policy):
    """Build the full schema with a queued parent Run and synthetic identity."""
    schema = f"cancel_delivery_{uuid.uuid4().hex}"
    conn = await psycopg.AsyncConnection.connect(
        _run_control_postgres_dsn(),
        autocommit=True,
        row_factory=dict_row,
    )
    try:
        await conn.execute(sql.SQL("create schema {}").format(sql.Identifier(schema)))
        await conn.execute(sql.SQL("set search_path to {}").format(sql.Identifier(schema)))
        await conn.execute(schema_migrations.schema_sql())
        await conn.execute("insert into tenants(id, name) values ('tenant-a', 'A')")
        await conn.execute(
            "insert into workspaces(id, tenant_id, name) "
            "values ('workspace-a', 'tenant-a', 'A')"
        )
        await conn.execute(
            "insert into users(id, tenant_id, display_name) "
            "values ('user-a', 'tenant-a', 'A')"
        )
        await conn.execute(
            "insert into agents(id, tenant_id, name, agent_type) "
            "values ('agent-a', 'tenant-a', 'A', 'chat')"
        )
        await conn.execute(
            "insert into sessions(id, tenant_id, workspace_id, user_id, agent_id, title) "
            "values ('session-a', 'tenant-a', 'workspace-a', 'user-a', 'agent-a', 'A')"
        )
        await conn.execute(
            "insert into runs(id, tenant_id, workspace_id, session_id, user_id, agent_id, "
            "status, execution_kind, queued_at) values ("
            "'run-a', 'tenant-a', 'workspace-a', 'session-a', 'user-a', 'agent-a', "
            "'queued', 'harness_chat', clock_timestamp())"
        )
        await conn.execute(
            "insert into sandbox_leases(id, tenant_id, workspace_id, user_id, session_id, "
            "run_id, attempt_id, sandbox_mode) values ("
            "'lease-a', 'tenant-a', 'workspace-a', 'user-a', 'session-a', 'run-a', "
            "'attempt-a', 'ephemeral')"
        )
        yield conn
    finally:
        await conn.execute(
            sql.SQL("drop schema if exists {} cascade").format(sql.Identifier(schema))
        )
        await conn.close()


def _execution_spec():
    return compile_execution_spec(
        {
            "schema_version": EXECUTION_SPEC_SCHEMA_VERSION,
            "run_payload_schema_version": "ai-platform.run-payload.v2",
            "tenant_id": "tenant-a",
            "workspace_id": "workspace-a",
            "user_id": "user-a",
            "session_id": "session-a",
            "run_id": "run-a",
            "agent_id": "agent-a",
            "execution_kind": "harness_chat",
            "skill_id": None,
            "file_ids": [],
            "input": {"message": "cancel this run"},
            "executor_type": "docker",
            "trace_id": "trace-a",
            "skill_version": "",
            "release_decision": {},
            "skill_manifests": [],
            "context_snapshot_id": "context-a",
            "context_snapshot": {"context_snapshot_id": "context-a"},
            "context_pack": {},
            "model_id": "model-a",
            "model_value": "model-a",
            "agent_profile": {},
        }
    )


async def _prepare_cancel_candidate(conn, *, retired=False):
    attempt = await create_run_attempt(
        conn,
        tenant_id="tenant-a",
        run_id="run-a",
        attempt_id="attempt-a",
        ordinal=1,
        owner_kind="queue_worker",
        owner_id="worker-a",
        queue_attempt_id="queue-attempt-a",
        execution_spec=_execution_spec(),
    )
    attempt = await transition_run_attempt(
        conn,
        tenant_id="tenant-a",
        run_id="run-a",
        attempt_id="attempt-a",
        expected_status="created",
        requested_status="queued",
        expected_owner_kind="queue_worker",
        expected_owner_id="worker-a",
        expected_owner_generation=1,
        queue_message_id="queue-message-a",
    )
    attempt = await transition_run_attempt(
        conn,
        tenant_id="tenant-a",
        run_id="run-a",
        attempt_id="attempt-a",
        expected_status="queued",
        requested_status="claimed",
        expected_owner_kind="queue_worker",
        expected_owner_id="worker-a",
        expected_owner_generation=int(attempt["owner_generation"]),
    )
    attempt = await transition_run_attempt(
        conn,
        tenant_id="tenant-a",
        run_id="run-a",
        attempt_id="attempt-a",
        expected_status="claimed",
        requested_status="running",
        expected_owner_kind="queue_worker",
        expected_owner_id="worker-a",
        expected_owner_generation=int(attempt["owner_generation"]),
    )
    assert attempt["status"] == "running"
    assert attempt["owner_generation"] == 4
    await conn.execute(
        """
        update sandbox_leases
        set provider = 'docker', executor_status = 'running',
            lease_payload_json = %s::jsonb,
            runtime_container_id = 'container-a',
            runtime_container_name = 'executor-a',
            runtime_executor_url = 'http://executor.test',
            runtime_workspace_container_path = '/workspace',
            runtime_handle_verified_at = clock_timestamp(),
            expires_at = clock_timestamp() + interval '1 hour'
        where id = 'lease-a'
        """,
        (json.dumps({"attempt_id": "attempt-a", "owner_generation": 4}),),
    )
    await conn.execute(
        """
        insert into sandbox_execution_dispatches(
          dispatch_id, tenant_id, run_id, attempt_id, owner_generation,
          lease_id, provider, provider_task_id, status
        ) values (
          'dispatch-a', 'tenant-a', 'run-a', 'attempt-a', 4,
          'lease-a', 'docker', 'attempt-a', 'accepted'
        )
        """
    )
    if retired:
        await conn.execute(
            "update run_attempt_settlement_storage set storage_version = 'attempt.v1' "
            "where singleton = true"
        )
        await conn.execute(
            "update run_attempt_settlement_writer set writer_version = 'attempt.v1' "
            "where singleton = true"
        )
        await conn.execute(
            """
            update run_attempt_settlement_retirement
            set state = 'retired', retirement_version = '2026.10.25.11',
                minimum_compatible_version = '2026.10.25.11',
                operator_declaration = '{}'::jsonb,
                retired_at = clock_timestamp()
            where singleton = true
            """
        )
        await conn.execute(
            """
            alter table sandbox_leases
              drop column executor_terminal_json,
              drop column executor_reconciliation_context_json,
              drop column executor_reconciliation_intent_context_json,
              drop column executor_reconciliation_status,
              drop column executor_reconciliation_claim_token,
              drop column executor_reconciliation_claimed_at,
              drop column executor_reconciliation_retry_not_before,
              drop column executor_terminal_reconciliation_attempt_count
            """
        )


async def _cancel_and_stage(conn, service):
    await conn.execute(
        "update runs set cancel_requested_at = clock_timestamp() "
        "where tenant_id = 'tenant-a' and id = 'run-a'"
    )
    attempt = await request_run_attempt_cancel(
        conn,
        tenant_id="tenant-a",
        run_id="run-a",
        attempt_id="attempt-a",
    )
    assert attempt is not None
    assert attempt["status"] == "cancel_requested"
    assert attempt["owner_generation"] == 5
    return await service.stage_in_transaction(
        conn,
        tenant_id="tenant-a",
        run_id="run-a",
        attempt_id="attempt-a",
    )


def _service(conn, send_cancel):
    @asynccontextmanager
    async def transaction():
        async with conn.transaction():
            yield conn

    return SandboxCancelDeliveryService(
        cancel_delivery_postgres,
        transaction,
        send_cancel,
    )


@pytest.mark.asyncio
async def test_cancel_stage_shares_run_attempt_transaction_and_exact_ack_is_monotonic(
    executor_acceptance_database,
):
    conn = executor_acceptance_database
    service = _service(conn, lambda row: _response(row))

    async with conn.transaction():
        await _prepare_cancel_candidate(conn)
    with pytest.raises(RuntimeError, match="rollback-stage"):
        async with conn.transaction():
            assert await _cancel_and_stage(conn, service) == 1
            raise RuntimeError("rollback-stage")
    attempt = await (
        await conn.execute(
            "select status, owner_generation from run_attempts where id='attempt-a'"
        )
    ).fetchone()
    assert attempt == {"status": "running", "owner_generation": 4}
    run = await (
        await conn.execute("select cancel_requested_at from runs where id='run-a'")
    ).fetchone()
    assert run["cancel_requested_at"] is None
    lease = await (
        await conn.execute("select cancel_delivery_status from sandbox_leases where id='lease-a'")
    ).fetchone()
    assert lease["cancel_delivery_status"] == "none"

    async with conn.transaction():
        assert await _cancel_and_stage(conn, service) == 1
        assert await service.stage_in_transaction(
            conn,
            tenant_id="tenant-a",
            run_id="run-a",
            attempt_id="attempt-a",
        ) == 0

    assert await service.deliver_once(tenant_id="tenant-a", run_id="run-a") == 1
    state = await (
        await conn.execute(
            """
            select cancel_delivery_status, cancel_delivery_owner_generation,
                   cancel_delivery_claim_token, cancel_delivery_accepted_at
            from sandbox_leases where id='lease-a'
            """
        )
    ).fetchone()
    assert state == {
        "cancel_delivery_status": "accepted",
        "cancel_delivery_owner_generation": 4,
        "cancel_delivery_claim_token": None,
        "cancel_delivery_accepted_at": state["cancel_delivery_accepted_at"],
    }
    assert state["cancel_delivery_accepted_at"] is not None

    async with conn.transaction():
        assert await service.stage_in_transaction(
            conn,
            tenant_id="tenant-a",
            run_id="run-a",
            attempt_id="attempt-a",
        ) == 0


@pytest.mark.asyncio
async def test_cancel_delivery_reclaims_stale_claim_and_retries_with_backoff(
    executor_acceptance_database,
):
    conn = executor_acceptance_database
    calls = []

    async def fail(row):
        calls.append(row)
        raise RuntimeError("synthetic provider failure")

    service = _service(conn, fail)
    async with conn.transaction():
        await _prepare_cancel_candidate(conn)
        assert await _cancel_and_stage(conn, service) == 1
        await conn.execute(
            """
            update sandbox_leases
            set cancel_delivery_status = 'claimed',
                cancel_delivery_owner_generation = 4,
                cancel_delivery_claim_token = 'dead-process-claim',
                cancel_delivery_claimed_at = clock_timestamp() - interval '1 hour',
                cancel_delivery_attempt_count = 1
            where id = 'lease-a'
            """
        )

    assert await service.deliver_once() == 1
    retry = await (
        await conn.execute(
            """
            select cancel_delivery_status, cancel_delivery_attempt_count,
                   cancel_delivery_retry_not_before, cancel_delivery_last_error_code
            from sandbox_leases where id='lease-a'
            """
        )
    ).fetchone()
    assert retry["cancel_delivery_status"] == "retry"
    assert retry["cancel_delivery_attempt_count"] == 2
    assert retry["cancel_delivery_retry_not_before"] is not None
    assert retry["cancel_delivery_last_error_code"] == "provider_error"
    assert await service.deliver_once() == 0

    async def accepted(row):
        return await _response(row)

    service._send_cancel = accepted
    await conn.execute(
        "update sandbox_leases set cancel_delivery_retry_not_before = clock_timestamp() - interval '1 second' "
        "where id='lease-a'"
    )
    assert await service.deliver_once() == 1
    assert len(calls) == 1
    state = await (
        await conn.execute(
            "select cancel_delivery_status, cancel_delivery_attempt_count "
            "from sandbox_leases where id='lease-a'"
        )
    ).fetchone()
    assert state == {"cancel_delivery_status": "accepted", "cancel_delivery_attempt_count": 3}


@pytest.mark.asyncio
async def test_attempt_takeover_invalidates_pending_cancel_and_receipt_trigger_works_after_retirement(
    executor_acceptance_database,
):
    conn = executor_acceptance_database
    sends = []

    async def send(row):
        sends.append(row)
        return _response(row)

    service = _service(conn, send)
    async with conn.transaction():
        await _prepare_cancel_candidate(conn)
        assert await _cancel_and_stage(conn, service) == 1
        await conn.execute(
            """
            update run_attempts
            set owner_kind = 'reconciler', owner_id = 'reconciler-a',
                owner_generation = owner_generation + 1
            where id = 'attempt-a'
            """
        )

    assert await service.deliver_once() == 0
    state = await (
        await conn.execute("select cancel_delivery_status from sandbox_leases where id='lease-a'")
    ).fetchone()
    assert state["cancel_delivery_status"] == "none"
    assert sends == []


@pytest.mark.asyncio
async def test_attempt_receipt_clears_cancel_claim_using_stable_columns_after_legacy_retirement(
    executor_acceptance_database,
):
    conn = executor_acceptance_database
    service = _service(conn, lambda row: _response(row))
    async with conn.transaction():
        await _prepare_cancel_candidate(conn, retired=True)
        assert await _cancel_and_stage(conn, service) == 1
        await conn.execute(
            """
            update sandbox_leases
            set cancel_delivery_status = 'claimed',
                cancel_delivery_claim_token = 'receipt-race-claim',
                cancel_delivery_claimed_at = clock_timestamp()
            where id = 'lease-a'
            """
        )
        await conn.execute(
            """
            insert into run_attempt_settlements(
              tenant_id, run_id, attempt_id, mapping_state, dispatch_state,
              attempt_status_snapshot, attempt_owner_generation_snapshot,
              run_status_snapshot, settlement_phase
            ) values (
              'tenant-a', 'run-a', 'attempt-a', 'mapped', 'accepted',
              'cancel_requested', 5, 'running', 'awaiting_receipt'
            )
            """
        )
        await conn.execute(
            """
            update run_attempt_settlements
            set receipt_identity = 'receipt-a', receipt_json = '{"status":"cancelled"}'::jsonb,
                receipt_sha256 = repeat('a', 64), receipt_owner_generation = 4,
                receipt_recorded_at = clock_timestamp()
            where tenant_id = 'tenant-a' and run_id = 'run-a' and attempt_id = 'attempt-a'
            """
        )

    state = await (
        await conn.execute(
            """
            select cancel_delivery_status, cancel_delivery_claim_token
            from sandbox_leases where id='lease-a'
            """
        )
    ).fetchone()
    assert state == {"cancel_delivery_status": "none", "cancel_delivery_claim_token": None}


async def _response(row):
    return {
        "status": "cancel_requested",
        "run_id": row["run_id"],
        "attempt_id": row["attempt_id"],
    }
