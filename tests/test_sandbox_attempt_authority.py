from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import time
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.rows import dict_row

from app.bootstrap.attempt_settlement_projection import build_attempt_settlement_projection_service
from app.runs.infrastructure import postgres as attempt_postgres
from tests.support.frozen_receipts import frozen_spec


@pytest.fixture
async def sandbox_attempt_authority_db():
    name = f"sandbox-authority-{uuid4().hex[:12]}"
    started = subprocess.run(
        [
            "docker", "run", "--detach", "--rm", "--name", name,
            "-e", "POSTGRES_USER=sandbox_test",
            "-e", "POSTGRES_PASSWORD=sandbox_test_only",
            "-e", "POSTGRES_DB=sandbox_test",
            "-p", "127.0.0.1::5432", "postgres:16-alpine",
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    container_id = started.stdout.strip()
    admin = None
    try:
        published = subprocess.run(
            ["docker", "port", container_id, "5432/tcp"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout.strip()
        port = published.rsplit(":", 1)[1]
        dsn = f"postgresql://sandbox_test:sandbox_test_only@127.0.0.1:{port}/sandbox_test"
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            try:
                admin = await psycopg.AsyncConnection.connect(
                    dsn, autocommit=True, row_factory=dict_row
                )
                break
            except psycopg.OperationalError:
                await asyncio.sleep(0.25)
        if admin is None:
            raise TimeoutError("test-owned PostgreSQL did not become ready")

        schema_name = "sandbox_authority_" + uuid4().hex
        await admin.execute(sql.SQL("create schema {}").format(sql.Identifier(schema_name)))

        @asynccontextmanager
        async def connect():
            async with await psycopg.AsyncConnection.connect(
                dsn, options=f"-c search_path={schema_name}", row_factory=dict_row
            ) as conn:
                yield conn

        async with connect() as conn:
            await conn.execute(Path("app/schema.sql").read_text(encoding="utf-8"))
            await conn.execute(
                """
                insert into tenants(id, name) values ('tenant-a', 'Tenant');
                insert into workspaces(id, tenant_id, name) values ('workspace-a', 'tenant-a', 'Workspace');
                insert into users(id, tenant_id, display_name) values ('user-a', 'tenant-a', 'User');
                insert into agents(id, tenant_id, name, agent_type) values ('agent-a', 'tenant-a', 'Agent', 'chat');
                insert into sessions(id, tenant_id, workspace_id, user_id, agent_id, title)
                  values ('session-a', 'tenant-a', 'workspace-a', 'user-a', 'agent-a', 'Session');
                insert into model_gateway_revisions(revision, base_url, api_key_ciphertext, key_fingerprint, created_by)
                  values (3, 'https://gateway.example.test', decode('00', 'hex'), 'aaaaaaaaaaaaaaaa', 'fixture');
                insert into runs(id, tenant_id, workspace_id, user_id, session_id, agent_id,
                  execution_kind, status, model_id, model_value, model_gateway_revision,
                  auto_compact_window, max_output_tokens)
                  values ('run-a', 'tenant-a', 'workspace-a', 'user-a', 'session-a', 'agent-a',
                    'harness_chat', 'queued', 'model-a', 'claude-test', 3, 200000, 4096);
                insert into run_context_snapshots(id, tenant_id, workspace_id, user_id, session_id, run_id)
                  values ('context-a', 'tenant-a', 'workspace-a', 'user-a', 'session-a', 'run-a');
                update runs set context_snapshot_id='context-a', input_json='{
                  "executor_type":"claude-agent-worker", "context_snapshot_id":"context-a",
                  "context_snapshot":{"context_snapshot_id":"context-a"}}' where id='run-a';
                """
            )
            attempt_row = await attempt_postgres.start_worker_run_attempt(
                conn,
                tenant_id="tenant-a",
                run_id="run-a",
                queue_attempt_id="qat-sandbox-authority",
                worker_id="worker-sandbox-authority",
                execution_spec=frozen_spec(
                    run_id="run-a",
                    context_snapshot_id="context-a",
                    context_snapshot={"context_snapshot_id": "context-a"},
                ),
            )
            context = {"adapter_name": "test", "snapshot": "accepted"}
            await conn.execute(
                "insert into sandbox_leases(id, tenant_id, workspace_id, user_id, session_id, run_id, "
                "attempt_id, sandbox_mode, provider, status, lease_payload_json, executor_status, "
                "executor_reconciliation_context_json, executor_reconciliation_intent_context_json, "
                "executor_reconciliation_status) values ('lease-a', 'tenant-a', 'workspace-a', "
                "'user-a', 'session-a', 'run-a', %s, 'ephemeral', 'fake', 'active', %s::jsonb, "
                "'accepted', %s::jsonb, %s::jsonb, 'waiting_terminal')",
                (
                    attempt_row["id"],
                    json.dumps({
                        "attempt_id": attempt_row["id"],
                        "owner_generation": str(attempt_row["owner_generation"]),
                    }),
                    json.dumps(context),
                    json.dumps({"adapter_name": "test"}),
                ),
            )
            projection = build_attempt_settlement_projection_service()
            await projection.initialize_attempt_in_transaction(
                conn,
                tenant_id="tenant-a",
                run_id="run-a",
                attempt_id=str(attempt_row["id"]),
                source_lease_id="lease-a",
            )
            await projection.record_accepted_context_in_transaction(
                conn,
                tenant_id="tenant-a",
                run_id="run-a",
                attempt_id=str(attempt_row["id"]),
                lease_id="lease-a",
                accepted_context=context,
            )
            await projection.record_intent_in_transaction(
                conn,
                tenant_id="tenant-a",
                run_id="run-a",
                attempt_id=str(attempt_row["id"]),
                lease_id="lease-a",
                intent_context={"adapter_name": "test"},
            )
            attempt_id = str(attempt_row["id"])
            owner_generation = int(attempt_row["owner_generation"])

        yield connect, attempt_id, owner_generation
        await admin.execute(sql.SQL("drop schema {} cascade").format(sql.Identifier(schema_name)))
    finally:
        if admin is not None:
            await admin.close()
        subprocess.run(
            ["docker", "stop", container_id],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )


@pytest.mark.asyncio
async def test_attempt_writer_receipt_is_atomic_and_does_not_mutate_legacy_settlement_columns(
    sandbox_attempt_authority_db, monkeypatch,
):
    from app.bootstrap.attempt_settlement_projection import (
        build_attempt_settlement_projection_service,
    )
    from app.bootstrap.sandbox_lease_ports import (
        build_sandbox_executor_reconciliation_port,
    )
    from app.runs.application.attempt_settlement_projection import (
        AttemptSettlementProjectionService,
    )
    from app.sandbox.domain.lease_errors import SandboxExecutorTerminalConflictError
    from app.sandbox.application.attempt_settlement_shadow import (
        AttemptSettlementShadowLeaseRepository,
    )
    from app.sandbox.infrastructure import leases_postgres

    connect, attempt_id, owner_generation = sandbox_attempt_authority_db
    projection = build_attempt_settlement_projection_service()
    lease_port = build_sandbox_executor_reconciliation_port()
    async with connect() as conn, conn.transaction():
        await conn.execute(
            "update sandbox_leases set status='active', released_at=null, "
            "executor_status='accepted', executor_terminal_json=null, "
            "executor_terminal_received_at=null, executor_reconciliation_status='waiting_terminal', "
            "executor_reconciliation_claim_token=null, executor_reconciliation_claimed_at=null, "
            "executor_reconciliation_retry_not_before=null, executor_cleanup_status='not_required' "
            "where id='lease-a'"
        )
        await projection.backfill(conn, tenant_id="tenant-a", limit=10)
        await conn.execute(
            "update run_attempt_settlement_writer set writer_version='attempt.v1' "
            "where singleton=true"
        )

    sentinels = {
        "executor_terminal_json": None,
        "executor_reconciliation_context_json": {"legacy": "accepted"},
        "executor_reconciliation_intent_context_json": {"legacy": "intent"},
        "executor_reconciliation_status": "waiting_terminal",
        "executor_reconciliation_claim_token": "legacy-settlement-token",
        "executor_reconciliation_claimed_at": datetime(2026, 10, 25, tzinfo=timezone.utc),
        "executor_reconciliation_retry_not_before": datetime(
            2026, 10, 26, tzinfo=timezone.utc
        ),
        "executor_terminal_reconciliation_attempt_count": 17,
    }
    async with connect() as conn, conn.transaction():
        await conn.execute(
            "update sandbox_leases set "
            "executor_terminal_json=%s::jsonb, "
            "executor_reconciliation_context_json=%s::jsonb, "
            "executor_reconciliation_intent_context_json=%s::jsonb, "
            "executor_reconciliation_status=%s, executor_reconciliation_claim_token=%s, "
            "executor_reconciliation_claimed_at=%s, "
            "executor_reconciliation_retry_not_before=%s, "
            "executor_terminal_reconciliation_attempt_count=%s where id='lease-a'",
            (
                None,
                '{"legacy":"accepted"}',
                '{"legacy":"intent"}',
                sentinels["executor_reconciliation_status"],
                sentinels["executor_reconciliation_claim_token"],
                sentinels["executor_reconciliation_claimed_at"],
                sentinels["executor_reconciliation_retry_not_before"],
                sentinels["executor_terminal_reconciliation_attempt_count"],
            ),
        )

    receipt = {"run_id": "run-a", "status": "succeeded", "answer": "terminal"}

    async def fail_receipt_write(self, conn, **kwargs):
        raise RuntimeError("injected_runs_receipt_failure")

    with monkeypatch.context() as patcher:
        patcher.setattr(
            AttemptSettlementProjectionService,
            "record_terminal_receipt_in_transaction",
            fail_receipt_write,
        )
        with pytest.raises(RuntimeError, match="injected_runs_receipt_failure"):
            async with connect() as conn, conn.transaction():
                await lease_port.record_sandbox_executor_terminal(
                    conn,
                    tenant_id="tenant-a",
                    run_id="run-a",
                    attempt_id=attempt_id,
                    lease_id="lease-a",
                    executor_status="completed",
                    terminal_result=receipt,
                )

    async with connect() as conn, conn.transaction():
        resource = await (
            await conn.execute(
                "select executor_status, executor_terminal_json, "
                "executor_terminal_received_at from sandbox_leases where id='lease-a'"
            )
        ).fetchone()
        detail = await (
            await conn.execute(
                "select receipt_json from run_attempt_settlements "
                "where tenant_id='tenant-a' and run_id='run-a'"
            )
        ).fetchone()
        assert resource == {
            "executor_status": "accepted",
            "executor_terminal_json": None,
            "executor_terminal_received_at": None,
        }
        assert detail == {"receipt_json": None}

    async with connect() as conn, conn.transaction():
        await lease_port.record_sandbox_executor_terminal(
            conn,
            tenant_id="tenant-a",
            run_id="run-a",
            attempt_id=attempt_id,
            lease_id="lease-a",
            executor_status="completed",
            terminal_result=receipt,
        )
        actual = await (
            await conn.execute(
                "select executor_terminal_json, executor_reconciliation_context_json, "
                "executor_reconciliation_intent_context_json, executor_reconciliation_status, "
                "executor_reconciliation_claim_token, executor_reconciliation_claimed_at, "
                "executor_reconciliation_retry_not_before, "
                "executor_terminal_reconciliation_attempt_count from sandbox_leases where id='lease-a'"
            )
        ).fetchone()
        detail = await (
            await conn.execute(
                "select receipt_json, receipt_owner_generation, settlement_phase "
                "from run_attempt_settlements where tenant_id='tenant-a' and run_id='run-a'"
            )
        ).fetchone()
        assert actual == sentinels
        assert detail == {
            "receipt_json": receipt,
            "receipt_owner_generation": owner_generation,
            "settlement_phase": "pending",
        }
        await lease_port.record_sandbox_executor_terminal(
            conn,
            tenant_id="tenant-a",
            run_id="run-a",
            attempt_id=attempt_id,
            lease_id="lease-a",
            executor_status="completed",
            terminal_result=receipt,
        )
        with pytest.raises(
            SandboxExecutorTerminalConflictError,
            match="sandbox_executor_terminal_conflict",
        ):
            await lease_port.record_sandbox_executor_terminal(
                conn,
                tenant_id="tenant-a",
                run_id="run-a",
                attempt_id=attempt_id,
                lease_id="lease-a",
                executor_status="completed",
                terminal_result={**receipt, "answer": "conflict"},
            )
        claimed = await lease_port.claim_sandbox_executor_reconciliations(
            conn,
            claim_token="runs-settlement-claim",
            limit=1,
            stale_after_seconds=30,
        )
        assert [row["id"] for row in claimed] == ["lease-a"]
        assert claimed[0]["executor_reconciliation_claim_token"] == "runs-settlement-claim"
        assert await lease_port.retry_sandbox_executor_reconciliation(
            conn,
            lease_id="lease-a",
            claim_token="runs-settlement-claim",
            error="transient",
        ) is True
        detail = await (
            await conn.execute(
                "select settlement_phase, settlement_claim_token, settlement_attempt_count "
                "from run_attempt_settlements where tenant_id='tenant-a' and run_id='run-a'"
            )
        ).fetchone()
        assert detail == {
            "settlement_phase": "retry",
            "settlement_claim_token": None,
            "settlement_attempt_count": 1,
        }
        assert await lease_port.mark_sandbox_executor_reconciliation_cleanup_pending(
            conn,
            lease_id="lease-a",
            claim_token="runs-settlement-claim",
            error="unsettled",
        ) is False
        resource_state = await (
            await conn.execute(
                "select executor_cleanup_status, executor_cleanup_claim_token "
                "from sandbox_leases where id='lease-a'"
            )
        ).fetchone()
        assert resource_state == {
            "executor_cleanup_status": "waiting_settlement",
            "executor_cleanup_claim_token": None,
        }

    resource_reader = AttemptSettlementShadowLeaseRepository(
        repository=leases_postgres,
        settlement_projection=projection,
        enabled=lambda: False,
    )
    async with connect() as conn, conn.transaction():
        resource_lease = await resource_reader.create_sandbox_lease(
            conn,
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            user_id="user-a",
            session_id="session-a",
            run_id="run-a",
            attempt_id=None,
            trace_id="trace-resource-only",
            sandbox_mode="ephemeral",
            provider="fake",
            browser_enabled=False,
            ttl_seconds=60,
            resource_limits_json={},
            user_visible_payload_json={},
            lease_payload_json={"resource_only": True},
        )
        assert resource_lease["attempt_id"] is None


@pytest.mark.asyncio
async def test_receiptless_probe_view_keeps_resource_claim_and_context():
    from app.sandbox.application.attempt_settlement_shadow import (
        AttemptSettlementShadowLeaseRepository,
    )

    now = datetime(2026, 10, 25, tzinfo=timezone.utc)
    source = {
        "id": "lease-probe",
        "tenant_id": "tenant-a",
        "run_id": "run-a",
        "attempt_id": "attempt-a",
        "executor_terminal_json": None,
        "executor_reconciliation_context_json": {"accepted": "snapshot"},
        "executor_reconciliation_intent_context_json": {"intent": "snapshot"},
        "executor_reconciliation_status": "waiting_terminal",
        "executor_probe_claim_token": "probe-token",
        "executor_probe_claimed_at": now,
        "executor_cleanup_claim_token": "cleanup-token",
        "executor_cleanup_claimed_at": now,
    }
    detail = {
        "mapping_state": "mapped",
        "settlement_phase": "awaiting_receipt",
        "dispatch_state": "accepted",
        "receipt_json": None,
        "accepted_context_json": None,
        "intent_recovery_context_json": None,
        "next_retry_at": None,
    }

    class Repository:
        async def get_sandbox_lease(self, _conn, **_kwargs):
            return dict(source)

    class Settlement:
        async def storage_version(self, _conn):
            return "attempt.v1"

        async def read_attempt(self, _conn, **_kwargs):
            return dict(detail)

    reader = AttemptSettlementShadowLeaseRepository(
        repository=Repository(),
        settlement_projection=Settlement(),
        enabled=lambda: False,
    )
    row = await reader.get_sandbox_lease(None, lease_id="lease-probe")
    assert row["executor_reconciliation_status"] == "claimed"
    assert row["executor_reconciliation_claim_token"] == "probe-token"
    assert row["executor_reconciliation_claimed_at"] == now
    assert row["executor_reconciliation_context_json"] == {"accepted": "snapshot"}
    assert row["executor_reconciliation_intent_context_json"] == {"intent": "snapshot"}
    assert row["executor_cleanup_claim_token"] == "cleanup-token"

    detail.update(
        settlement_phase="retry",
        receipt_json={"run_id": "run-a", "status": "succeeded"},
        accepted_context_json={"accepted": "runs"},
        intent_recovery_context_json={"intent": "runs"},
        next_retry_at=now,
    )
    retry_row = await reader.get_sandbox_lease(None, lease_id="lease-probe")
    assert retry_row["executor_reconciliation_status"] == "retry"
    assert retry_row["executor_reconciliation_claim_token"] is None
    assert retry_row["executor_reconciliation_retry_not_before"] == now
    assert retry_row["executor_probe_claim_token"] == "probe-token"
    assert retry_row["executor_cleanup_claim_token"] == "cleanup-token"
