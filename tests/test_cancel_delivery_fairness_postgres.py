"""PostgreSQL lock and bounded-scan coverage for cancel delivery fairness."""
# ruff: noqa: F401,F811 -- imported shared PostgreSQL fixture.

import hashlib
import json
from contextlib import asynccontextmanager

import psycopg
import pytest
from psycopg import sql
from psycopg.rows import dict_row

from app.runs.infrastructure.postgres import request_run_attempt_cancel
from app.sandbox.application import cancel_delivery as cancel_delivery_application
from app.sandbox.application.cancel_delivery import SandboxCancelDeliveryService
from app.sandbox.infrastructure import cancel_delivery_postgres
from tests.test_sandbox_cancel_delivery_postgres import executor_acceptance_database, _execution_spec
from tests.support.repository_fixtures import _run_control_postgres_dsn


async def _seed_candidate(
    conn,
    *,
    run_id: str,
    attempt_id: str,
    lease_id: str,
) -> None:
    if run_id != "run-a":
        await conn.execute(
            """
            insert into runs(
              id, tenant_id, workspace_id, session_id, user_id, agent_id,
              status, execution_kind
            ) values (%s, 'tenant-a', 'workspace-a', 'session-a', 'user-a',
                      'agent-a', 'queued', 'harness_chat')
            """,
            (run_id,),
        )
        await conn.execute(
            """
            insert into sandbox_leases(
              id, tenant_id, workspace_id, user_id, session_id, run_id,
              attempt_id, sandbox_mode
            ) values (%s, 'tenant-a', 'workspace-a', 'user-a', 'session-a',
                      %s, %s, 'ephemeral')
            """,
            (lease_id, run_id, attempt_id),
        )

    spec = {**json.loads(_execution_spec().canonical_json), "run_id": run_id}
    canonical = json.dumps(spec, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    await conn.execute(
        """
        insert into run_attempts(
          id, tenant_id, run_id, ordinal, status, owner_kind, owner_id,
          owner_generation, queue_attempt_id, execution_spec_schema_version,
          execution_spec_json, execution_spec_canonical_json, execution_spec_sha256,
          started_at
        ) values (
          %s, 'tenant-a', %s, 1, 'created', 'queue_worker', %s, 1, %s,
          %s, %s::jsonb, %s, %s, null
        )
        """,
        (
            attempt_id,
            run_id,
            f"worker-{attempt_id}",
            f"queue-{attempt_id}",
            spec["schema_version"],
            json.dumps(spec),
            canonical,
            digest,
        ),
    )
    await conn.execute(
        "update run_attempts set status='queued', owner_generation=2, queue_message_id=%s where id=%s",
        (f"queue-message-{attempt_id}", attempt_id),
    )
    await conn.execute("update run_attempts set status='claimed', owner_generation=3 where id=%s", (attempt_id,))
    await conn.execute("update run_attempts set status='running', owner_generation=4, started_at=now() where id=%s", (attempt_id,))
    await conn.execute(
        """
        update sandbox_leases
        set attempt_id = %s, provider = 'docker', executor_status = 'running',
            lease_payload_json = %s::jsonb,
            runtime_container_id = %s,
            runtime_container_name = %s,
            runtime_executor_url = 'http://executor.test',
            runtime_workspace_container_path = '/workspace',
            runtime_handle_verified_at = clock_timestamp(),
            expires_at = clock_timestamp() + interval '1 hour'
        where id = %s
        """,
        (
            attempt_id,
            json.dumps({"attempt_id": attempt_id, "owner_generation": 4}),
            f"container-{attempt_id}",
            f"executor-{attempt_id}",
            lease_id,
        ),
    )
    await conn.execute(
        """
        insert into sandbox_execution_dispatches(
          dispatch_id, tenant_id, run_id, attempt_id, owner_generation,
          lease_id, provider, provider_task_id, status
        ) values (%s, 'tenant-a', %s, %s, 4, %s, 'docker', %s, 'accepted')
        """,
        (
            f"dispatch-{attempt_id}",
            run_id,
            attempt_id,
            lease_id,
            attempt_id,
        ),
    )


async def _request_and_stage(conn, *, run_id: str, attempt_id: str) -> None:
    await conn.execute(
        "update runs set cancel_requested_at = clock_timestamp() "
        "where tenant_id = 'tenant-a' and id = %s",
        (run_id,),
    )
    attempt = await request_run_attempt_cancel(
        conn,
        tenant_id="tenant-a",
        run_id=run_id,
        attempt_id=attempt_id,
    )
    assert attempt is not None
    assert attempt["status"] == "cancel_requested"
    assert attempt["owner_generation"] == 5
    assert (
        await cancel_delivery_postgres.stage_in_transaction(
            conn,
            tenant_id="tenant-a",
            run_id=run_id,
            attempt_id=attempt_id,
        )
        == 1
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


async def _blocker_for(conn):
    schema = (await (await conn.execute("select current_schema() as name")).fetchone())[
        "name"
    ]
    blocker = await psycopg.AsyncConnection.connect(
        _run_control_postgres_dsn(),
        autocommit=True,
        row_factory=dict_row,
    )
    await blocker.execute(
        sql.SQL("set search_path to {}").format(sql.Identifier(schema))
    )
    return blocker


async def _response(row):
    return {
        "status": "cancel_requested",
        "run_id": row["run_id"],
        "attempt_id": row["attempt_id"],
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("locked_row", ["lease", "attempt"])
async def test_locked_front_lease_or_attempt_does_not_starve_healthy_candidate(
    executor_acceptance_database,
    locked_row,
):
    conn = executor_acceptance_database
    blocker = await _blocker_for(conn)
    sent_attempts = []

    async def send(row):
        sent_attempts.append(row["attempt_id"])
        return await _response(row)

    try:
        async with conn.transaction():
            await _seed_candidate(
                conn,
                run_id="run-a",
                attempt_id="attempt-a",
                lease_id="lease-a",
            )
            await _seed_candidate(
                conn,
                run_id="run-b",
                attempt_id="attempt-b",
                lease_id="lease-b",
            )
            await _request_and_stage(conn, run_id="run-a", attempt_id="attempt-a")
            await _request_and_stage(conn, run_id="run-b", attempt_id="attempt-b")

        service = _service(conn, send)
        async with blocker.transaction():
            if locked_row == "lease":
                await (
                    await blocker.execute(
                        "select id from sandbox_leases where id='lease-a' for update"
                    )
                ).fetchone()
            else:
                await (
                    await blocker.execute(
                        "select id from run_attempts where id='attempt-a' for update"
                    )
                ).fetchone()

            assert await service.deliver_once(limit=1) == 1

        assert sent_attempts == ["attempt-b"]
        states = await (
            await conn.execute(
                "select id, cancel_delivery_status from sandbox_leases "
                "where id in ('lease-a', 'lease-b') order by id"
            )
        ).fetchall()
        assert states == [
            {"id": "lease-a", "cancel_delivery_status": "pending"},
            {"id": "lease-b", "cancel_delivery_status": "accepted"},
        ]
    finally:
        await blocker.close()


@pytest.mark.asyncio
async def test_global_scan_cursor_advances_across_bounded_attempt_lock_page(
    executor_acceptance_database,
    monkeypatch,
):
    conn = executor_acceptance_database
    blocker = await _blocker_for(conn)
    monkeypatch.setattr(cancel_delivery_application, "_SCAN_BUDGET", 2)
    sent_attempts = []

    async def send(row):
        sent_attempts.append(row["attempt_id"])
        return await _response(row)

    try:
        async with conn.transaction():
            for index in range(3):
                await _seed_candidate(
                    conn,
                    run_id=f"run-{index:03d}",
                    attempt_id=f"attempt-{index:03d}",
                    lease_id=f"lease-{index:03d}",
                )
            for index in range(3):
                await _request_and_stage(
                    conn,
                    run_id=f"run-{index:03d}",
                    attempt_id=f"attempt-{index:03d}",
                )

        calls = []
        original_claim_due = cancel_delivery_postgres.claim_due

        async def record_claim_due(connection, **kwargs):
            calls.append(dict(kwargs))
            return await original_claim_due(connection, **kwargs)

        cancel_delivery_postgres.claim_due = record_claim_due
        try:
            service = _service(conn, send)
            async with blocker.transaction():
                await (
                    await blocker.execute(
                        """
                        select id from run_attempts
                        where id = any(%s::text[])
                        order by id asc
                        for update
                        """,
                        (["attempt-000", "attempt-001"],),
                    )
                ).fetchall()

                assert await service.deliver_once(limit=1) == 0
                assert await service.deliver_once(limit=1) == 1
        finally:
            cancel_delivery_postgres.claim_due = original_claim_due

        assert sent_attempts == ["attempt-002"]
        assert [call["after_lease_key"] for call in calls] == [
            None,
            ("tenant-a", "run-001", "lease-001"),
        ]
        assert [call["scan_budget"] for call in calls] == [2, 2]
        assert [call["limit"] for call in calls] == [1, 1]
    finally:
        await blocker.close()
