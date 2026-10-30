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
                        "owner_generation": attempt_row["owner_generation"],
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
            "update sandbox_leases set status='active', released_at=null, "
            "executor_status='accepted', executor_terminal_json=null, "
            "executor_terminal_received_at=null, executor_reconciliation_status='waiting_terminal', "
            "executor_reconciliation_claim_token=null, executor_reconciliation_claimed_at=null, "
            "executor_reconciliation_retry_not_before=null, executor_cleanup_status='not_required' "
            "where id='lease-a'"
        )
        await projection.backfill(conn, tenant_id="tenant-a", limit=10)
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
        await conn.execute(
            "update run_attempt_settlement_writer set writer_version='attempt.v1' "
            "where singleton=true"
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
        "accepted_context_json": {"accepted": "runs"},
        "intent_recovery_context_json": {"intent": "runs"},
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
    assert row["executor_reconciliation_context_json"] == {"accepted": "runs"}
    assert row["executor_reconciliation_intent_context_json"] == {"intent": "runs"}
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


@pytest.mark.asyncio
async def test_expired_settlement_claim_cannot_quarantine_after_takeover(
    sandbox_attempt_authority_db,
):
    from app.bootstrap.sandbox_lease_ports import build_sandbox_executor_reconciliation_port

    connect, attempt_id, owner_generation = sandbox_attempt_authority_db
    lease_port = build_sandbox_executor_reconciliation_port()
    projection = build_attempt_settlement_projection_service()
    receipt = {"run_id": "run-a", "status": "failed", "message": "executor failed"}

    async with connect() as conn, conn.transaction():
        assert await projection.record_terminal_receipt_in_transaction(
            conn,
            tenant_id="tenant-a",
            run_id="run-a",
            attempt_id=attempt_id,
            lease_id="lease-a",
            receipt=receipt,
            receipt_owner_generation=owner_generation,
        )
        await conn.execute(
            "update sandbox_leases set executor_status='failed', "
            "executor_terminal_received_at=now(), executor_cleanup_status='waiting_settlement' "
            "where id='lease-a'"
        )
        await conn.execute(
            "update run_attempt_settlement_writer set writer_version='attempt.v1' where singleton=true"
        )

    async with connect() as conn, conn.transaction():
        claim_a = await lease_port.claim_sandbox_executor_reconciliations(
            conn,
            claim_token="settlement-a",
            limit=1,
            stale_after_seconds=30,
        )
        assert len(claim_a) == 1
        settlement = await (
            await conn.execute(
                "select settlement_phase, settlement_claim_token "
                "from run_attempt_settlements where tenant_id='tenant-a' "
                "and run_id='run-a' and attempt_id=%s",
                (attempt_id,),
            )
        ).fetchone()
        assert settlement == {
            "settlement_phase": "claimed",
            "settlement_claim_token": "settlement-a",
        }

    async with connect() as conn, conn.transaction():
        await conn.execute(
            "update run_attempt_settlements set settlement_claimed_at=now() - interval '2 minutes' "
            "where tenant_id='tenant-a' and run_id='run-a' and attempt_id=%s "
            "and settlement_claim_token='settlement-a'",
            (attempt_id,),
        )
        claim_b = await lease_port.claim_sandbox_executor_reconciliations(
            conn,
            claim_token="settlement-b",
            limit=1,
            stale_after_seconds=30,
        )
        assert len(claim_b) == 1
        settlement = await (
            await conn.execute(
                "select settlement_phase, settlement_claim_token "
                "from run_attempt_settlements where tenant_id='tenant-a' "
                "and run_id='run-a' and attempt_id=%s",
                (attempt_id,),
            )
        ).fetchone()
        assert settlement == {
            "settlement_phase": "claimed",
            "settlement_claim_token": "settlement-b",
        }

    async with connect() as conn, conn.transaction():
        before = await (
            await conn.execute(
                "select status, executor_cleanup_status, executor_cleanup_claim_token, "
                "executor_cleanup_claimed_at, executor_cleanup_retry_not_before, "
                "executor_reconciliation_error, released_at, updated_at "
                "from sandbox_leases where id='lease-a'"
            )
        ).fetchone()
        assert await lease_port.quarantine_sandbox_executor_reconciliation(
            conn,
            lease_id="lease-a",
            claim_token="settlement-a",
            error="stale_claim",
        ) is False
        after = await (
            await conn.execute(
                "select status, executor_cleanup_status, executor_cleanup_claim_token, "
                "executor_cleanup_claimed_at, executor_cleanup_retry_not_before, "
                "executor_reconciliation_error, released_at, updated_at "
                "from sandbox_leases where id='lease-a'"
            )
        ).fetchone()
        assert after == before

    async with connect() as conn, conn.transaction():
        assert await lease_port.quarantine_sandbox_executor_reconciliation(
            conn,
            lease_id="lease-a",
            claim_token="settlement-b",
            error="invalid_runtime",
        ) is True
        quarantined = await (
            await conn.execute(
                "select status, executor_cleanup_status, executor_cleanup_claim_token, "
                "executor_probe_claim_token, released_at "
                "from sandbox_leases where id='lease-a'"
            )
        ).fetchone()
    assert quarantined == {
        "status": "quarantined",
        "executor_cleanup_status": "failed",
        "executor_cleanup_claim_token": None,
        "executor_probe_claim_token": None,
        "released_at": None,
    }
    async with connect() as conn, conn.transaction():
        await conn.execute(
            "update run_attempt_settlements set settlement_claimed_at=now() - interval '2 minutes' "
            "where tenant_id='tenant-a' and run_id='run-a' and attempt_id=%s",
            (attempt_id,),
        )
        claim_c = await lease_port.claim_sandbox_executor_reconciliations(
            conn, claim_token="settlement-c", limit=10, stale_after_seconds=30,
        )
        assert claim_c == []


@pytest.mark.asyncio
async def test_attempt_writer_probes_unknown_intent_through_claim_and_respects_heartbeat(
    sandbox_attempt_authority_db, monkeypatch,
):
    from types import SimpleNamespace

    from app.bootstrap.sandbox_lease_ports import build_sandbox_executor_reconciliation_port
    from app.execution.domain.supervision import SupervisionDomain
    from app.execution.infrastructure.supervision_postgres import observe_persisted_candidates
    from app.executors.base import RUN_PAYLOAD_SCHEMA_VERSION_V2
    import app.executor_reconciler as executor_reconciler

    connect, attempt_id, owner_generation = sandbox_attempt_authority_db
    lease_port = build_sandbox_executor_reconciliation_port()
    context = {
        "adapter_name": "test",
        "adapter_context": {},
        "run_payload": {
            "tenant_id": "tenant-a",
            "workspace_id": "workspace-a",
            "user_id": "user-a",
            "session_id": "session-a",
            "run_id": "run-a",
            "attempt_id": attempt_id,
            "agent_id": "agent-a",
            "skill_id": None,
            "file_ids": [],
            "input": {},
            "owner_generation": owner_generation,
            "execution_kind": "harness_chat",
            "trace_id": "trace-a",
            "skill_manifests": [],
            "schema_version": RUN_PAYLOAD_SCHEMA_VERSION_V2,
        },
    }
    legacy_updated = datetime(2026, 10, 20, tzinfo=timezone.utc)
    legacy_retry = datetime(2026, 10, 26, tzinfo=timezone.utc)
    legacy_values = {
        "executor_terminal_json": {"legacy": "terminal"},
        "executor_reconciliation_context_json": {"legacy": "accepted"},
        "executor_reconciliation_intent_context_json": {"legacy": "intent"},
        "executor_reconciliation_status": "failed",
        "executor_reconciliation_claim_token": "legacy-claim",
        "executor_reconciliation_claimed_at": legacy_updated,
        "executor_reconciliation_retry_not_before": legacy_retry,
        "executor_terminal_reconciliation_attempt_count": 17,
    }
    async with connect() as conn, conn.transaction():
        await conn.execute(
            "update run_attempt_settlements set dispatch_state='unknown', "
            "accepted_context_json=null, intent_recovery_context_json=%s::jsonb, "
            "settlement_phase='awaiting_receipt' where tenant_id='tenant-a' and run_id='run-a'",
            (json.dumps(context),),
        )
        await conn.execute(
            "update sandbox_leases set executor_status='pending', "
            "executor_heartbeat_at=now() - interval '2 minutes', executor_probe_claim_token=null, "
            "executor_probe_claimed_at=null, executor_terminal_json=%s::jsonb, "
            "executor_reconciliation_context_json=%s::jsonb, "
            "executor_reconciliation_intent_context_json=%s::jsonb, "
            "executor_reconciliation_status=%s, executor_reconciliation_claim_token=%s, "
            "executor_reconciliation_claimed_at=%s, executor_reconciliation_retry_not_before=%s, "
            "executor_terminal_reconciliation_attempt_count=%s where id='lease-a'",
            (
                json.dumps(legacy_values["executor_terminal_json"]),
                json.dumps(legacy_values["executor_reconciliation_context_json"]),
                json.dumps(legacy_values["executor_reconciliation_intent_context_json"]),
                legacy_values["executor_reconciliation_status"],
                legacy_values["executor_reconciliation_claim_token"],
                legacy_values["executor_reconciliation_claimed_at"],
                legacy_values["executor_reconciliation_retry_not_before"],
                legacy_values["executor_terminal_reconciliation_attempt_count"],
            ),
        )
        await conn.execute(
            "update run_attempt_settlement_writer set writer_version='attempt.v1' where singleton=true"
        )

    @asynccontextmanager
    async def transaction_factory():
        async with connect() as conn, conn.transaction():
            yield conn

    monkeypatch.setattr(executor_reconciler, "transaction", transaction_factory)
    monkeypatch.setattr(
        executor_reconciler._sandbox_cleanup,
        "container_lease_from_persisted_row",
        lambda *_args, **_kwargs: SimpleNamespace(provider="fake"),
    )
    requests = []
    claims_seen = []

    class Provider:
        async def executor_control_endpoint(self, _lease, request):
            requests.append(request)
            async with connect() as conn, conn.transaction():
                row = await (
                    await conn.execute(
                        "select executor_probe_claim_token, executor_probe_claimed_at "
                        "from sandbox_leases where id='lease-a'"
                    )
                ).fetchone()
            claims_seen.append(row)
            return "http://executor.test", {}

    class Client:
        def __init__(self, *, timeout_seconds):
            assert timeout_seconds == 10.0

        async def get_status(self, *_args, **_kwargs):
            return {"status": "pending"}

        async def cancel(self, *_args, **_kwargs):
            raise AssertionError("queued Run must not cancel the executor")

    monkeypatch.setattr(executor_reconciler, "_container_provider_for_lease", lambda _lease: Provider())
    monkeypatch.setattr(executor_reconciler, "SandboxExecutorClient", Client)

    batch_limits = {domain: 10 for domain in SupervisionDomain}
    async with connect() as conn, conn.transaction():
        observed = await observe_persisted_candidates(
            conn,
            now=datetime.now(timezone.utc),
            batch_limits=batch_limits,
            probe_stale_seconds=45,
            settlement_claim_stale_seconds=30,
        )
    assert any(
        candidate.domain == SupervisionDomain.PROBE
        and candidate.candidate_id == "lease-a"
        for candidate in observed
    )

    assert await executor_reconciler.probe_suspect_executor_tasks_once(
        lease_port=lease_port,
    ) == 0
    assert len(requests) == len(claims_seen) == 1
    assert requests[0].run_id == "run-a" and requests[0].attempt_id == attempt_id
    assert claims_seen[0]["executor_probe_claim_token"]
    assert claims_seen[0]["executor_probe_claimed_at"] is not None

    async with connect() as conn, conn.transaction():
        persisted = await (
            await conn.execute(
                "select executor_terminal_json, executor_reconciliation_context_json, "
                "executor_reconciliation_intent_context_json, executor_reconciliation_status, "
                "executor_reconciliation_claim_token, executor_reconciliation_claimed_at, "
                "executor_reconciliation_retry_not_before, "
                "executor_terminal_reconciliation_attempt_count, executor_probe_claim_token "
                "from sandbox_leases where id='lease-a'"
            )
        ).fetchone()
        assert persisted == {
            **legacy_values,
            "executor_probe_claim_token": None,
        }
        await conn.execute(
            "update sandbox_leases set executor_heartbeat_at=now(), updated_at=now() where id='lease-a'"
        )
        await conn.execute(
            "update execution_supervision_domains set probe_normal_scan_cursor=null where domain='probe'"
        )
        fresh_candidates = await observe_persisted_candidates(
            conn,
            now=datetime.now(timezone.utc),
            batch_limits=batch_limits,
            probe_stale_seconds=45,
            settlement_claim_stale_seconds=30,
        )
    assert not any(
        candidate.domain == SupervisionDomain.PROBE
        and candidate.candidate_id == "lease-a"
        for candidate in fresh_candidates
    )
    assert await executor_reconciler.probe_suspect_executor_tasks_once(
        lease_port=lease_port,
    ) == 0
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_terminal_reconciler_refreshes_cancelled_facts_and_cleanup_recovers_handoff_gap(
    sandbox_attempt_authority_db, monkeypatch,
):
    from types import SimpleNamespace

    from app.bootstrap.sandbox_lease_ports import build_sandbox_executor_reconciliation_port
    from app.execution.domain.supervision import SupervisionDomain
    from app.execution.infrastructure.supervision_postgres import observe_persisted_candidates
    from app.executors.base import RUN_PAYLOAD_SCHEMA_VERSION_V2
    from app.runs.application.attempt_lifecycle import RunAttemptLifecycleService
    import app.executor_reconciler as executor_reconciler
    from app.bootstrap.run_lifecycle import build_run_lifecycle_service

    connect, attempt_id, owner_generation = sandbox_attempt_authority_db
    lease_port = build_sandbox_executor_reconciliation_port()
    context = {
        "adapter_name": "test",
        "adapter_context": {},
        "run_payload": {
            "tenant_id": "tenant-a",
            "workspace_id": "workspace-a",
            "user_id": "user-a",
            "session_id": "session-a",
            "run_id": "run-a",
            "attempt_id": attempt_id,
            "agent_id": "agent-a",
            "skill_id": None,
            "file_ids": [],
            "input": {},
            "owner_generation": owner_generation,
            "execution_kind": "harness_chat",
            "trace_id": "trace-a",
            "skill_manifests": [],
            "schema_version": RUN_PAYLOAD_SCHEMA_VERSION_V2,
        },
    }
    async with connect() as conn, conn.transaction():
        await conn.execute(
            "update sandbox_leases set executor_terminal_json=%s::jsonb, "
            "executor_reconciliation_context_json=%s::jsonb, "
            "executor_reconciliation_intent_context_json=%s::jsonb, "
            "executor_reconciliation_status='failed', executor_reconciliation_claim_token='legacy-claim', "
            "executor_reconciliation_claimed_at='2026-10-20T00:00:00Z', "
            "executor_reconciliation_retry_not_before='2026-10-26T00:00:00Z', "
            "executor_terminal_reconciliation_attempt_count=17 where id='lease-a'",
            (
                json.dumps({"legacy": "terminal"}),
                json.dumps({"legacy": "accepted"}),
                json.dumps({"legacy": "intent"}),
            ),
        )
        await conn.execute(
            "update run_attempt_settlement_writer set writer_version='attempt.v1' where singleton=true"
        )
        await conn.execute(
            "update run_attempt_settlements set accepted_context_json=%s::jsonb, "
            "dispatch_state='accepted' where tenant_id='tenant-a' and run_id='run-a'",
            (json.dumps(context),),
        )
        projection = build_attempt_settlement_projection_service()
        attempt_lifecycle = RunAttemptLifecycleService(
            persistence=attempt_postgres,
            settlement_projection=projection,
        )
        lifecycle = build_run_lifecycle_service()
        await conn.execute(
            "update runs set cancel_requested_at=now() where id='run-a'"
        )
        requested = await attempt_lifecycle.request_cancel(
            conn,
            tenant_id="tenant-a",
            run_id="run-a",
            attempt_id=attempt_id,
        )
        assert requested is not None and requested["status"] == "cancel_requested"
        cancellation = await lifecycle.cancel_run(
            conn,
            tenant_id="tenant-a",
            run_id="run-a",
            result_json={"message": "cancelled"},
        )
        assert cancellation.is_terminal()
        terminal_attempt = await attempt_lifecycle.terminalize(
            conn,
            tenant_id="tenant-a",
            run_id="run-a",
            attempt_id=attempt_id,
            status="cancelled",
            terminal_reason="run_cancelled",
        )
        assert terminal_attempt is not None and terminal_attempt["status"] == "cancelled"
        pre_receipt = await (
            await conn.execute(
                "select settlement_phase, settlement_claim_token "
                "from run_attempt_settlements where tenant_id='tenant-a' and run_id='run-a'"
            )
        ).fetchone()
        assert pre_receipt == {"settlement_phase": "settled", "settlement_claim_token": None}

    async with connect() as conn, conn.transaction():
        await lease_port.record_sandbox_executor_terminal(
            conn,
            tenant_id="tenant-a",
            run_id="run-a",
            attempt_id=attempt_id,
            lease_id="lease-a",
            executor_status="cancelled",
            terminal_result={
                "run_id": "run-a",
                "status": "cancelled",
                "message": "cancelled",
            },
        )

    @asynccontextmanager
    async def transaction_factory():
        async with connect() as conn, conn.transaction():
            yield conn

    monkeypatch.setattr(executor_reconciler, "transaction", transaction_factory)
    monkeypatch.setattr(
        executor_reconciler._sandbox_cleanup,
        "container_lease_from_persisted_row",
        lambda *_args, **_kwargs: SimpleNamespace(provider="fake"),
    )
    reconciled = await executor_reconciler.reconcile_pending_executor_terminals_once(
        lease_port=lease_port,
        attempt_lifecycle=None,
        lifecycle=None,
    )
    assert reconciled == 1

    async with connect() as conn, conn.transaction():
        detail = await (
            await conn.execute(
                "select settlement_phase, settlement_claim_token, result_status, result_json "
                "from run_attempt_settlements where tenant_id='tenant-a' and run_id='run-a'"
            )
        ).fetchone()
        lease = await (
            await conn.execute(
                "select status, executor_cleanup_status, executor_cleanup_claim_token, "
                "executor_terminal_json, executor_reconciliation_context_json, "
                "executor_reconciliation_intent_context_json, executor_reconciliation_status, "
                "executor_reconciliation_claim_token, executor_reconciliation_claimed_at, "
                "executor_reconciliation_retry_not_before, "
                "executor_terminal_reconciliation_attempt_count "
                "from sandbox_leases where id='lease-a'"
            )
        ).fetchone()
        business = await (
            await conn.execute(
                "select runs.status as run_status, runs.result_json, attempts.status as attempt_status "
                "from runs join run_attempts attempts on attempts.run_id=runs.id where runs.id='run-a'"
            )
        ).fetchone()
    assert detail["settlement_phase"] == "settled"
    assert detail["settlement_claim_token"] is None
    assert detail["result_status"] == "cancelled"
    assert detail["result_json"] == {"message": "cancelled"}
    assert lease["executor_cleanup_status"] == "cleanup_pending"
    assert lease["executor_cleanup_claim_token"] is None
    assert lease["status"] == "active"
    assert lease["executor_terminal_json"] == {"legacy": "terminal"}
    assert lease["executor_reconciliation_context_json"] == {"legacy": "accepted"}
    assert lease["executor_reconciliation_intent_context_json"] == {"legacy": "intent"}
    assert lease["executor_reconciliation_status"] == "failed"
    assert lease["executor_reconciliation_claim_token"] == "legacy-claim"
    assert lease["executor_reconciliation_claimed_at"] is not None
    assert lease["executor_reconciliation_retry_not_before"] is not None
    assert lease["executor_terminal_reconciliation_attempt_count"] == 17
    assert business == {
        "run_status": "cancelled",
        "result_json": {"message": "cancelled"},
        "attempt_status": "cancelled",
    }

    async with connect() as conn, conn.transaction():
        await conn.execute(
            "update sandbox_leases set executor_cleanup_status='waiting_settlement', "
            "executor_cleanup_claim_token=null, executor_cleanup_claimed_at=null, "
            "executor_cleanup_retry_not_before=null where id='lease-a'"
        )
        await conn.execute(
            "update execution_supervision_domains set scan_cursor=null where domain='cleanup'"
        )
        candidates = await observe_persisted_candidates(
            conn,
            now=datetime.now(timezone.utc),
            batch_limits={domain: 10 for domain in SupervisionDomain},
            probe_stale_seconds=45,
            settlement_claim_stale_seconds=30,
        )
    assert any(
        candidate.domain == SupervisionDomain.CLEANUP
        and candidate.candidate_id == "lease-a"
        for candidate in candidates
    )

    cleanup = executor_reconciler._sandbox_cleanup

    async def stopped(_provider, _lease):
        return SimpleNamespace(status="stopped")

    async def no_event(*_args, **_kwargs):
        return None

    monkeypatch.setattr(
        cleanup,
        "container_lease_from_persisted_row",
        lambda *_args, **_kwargs: SimpleNamespace(provider="fake"),
    )
    monkeypatch.setattr(cleanup, "_stop_failed_reconciliation_lease", stopped)
    monkeypatch.setattr(cleanup, "_append_event", no_event)
    released = await cleanup.cleanup_failed_sandbox_executor_reconciliation_leases(
        provider_factory=lambda _provider: object(),
        transaction_factory=transaction_factory,
    )
    assert [row["id"] for row in released] == ["lease-a"]

    async with connect() as conn, conn.transaction():
        final = await (
            await conn.execute(
                "select status, executor_cleanup_status, executor_terminal_json, "
                "executor_reconciliation_context_json, executor_reconciliation_intent_context_json, "
                "executor_reconciliation_status, executor_reconciliation_claim_token, "
                "executor_reconciliation_claimed_at, executor_reconciliation_retry_not_before, "
                "executor_terminal_reconciliation_attempt_count from sandbox_leases where id='lease-a'"
            )
        ).fetchone()
        business = await (
            await conn.execute(
                "select runs.status as run_status, runs.result_json, attempts.status as attempt_status "
                "from runs join run_attempts attempts on attempts.run_id=runs.id where runs.id='run-a'"
            )
        ).fetchone()
    assert final["status"] == "released"
    assert final["executor_cleanup_status"] == "finalized"
    assert final["executor_terminal_json"] == {"legacy": "terminal"}
    assert final["executor_reconciliation_context_json"] == {"legacy": "accepted"}
    assert final["executor_reconciliation_intent_context_json"] == {"legacy": "intent"}
    assert final["executor_reconciliation_status"] == "failed"
    assert final["executor_reconciliation_claim_token"] == "legacy-claim"
    assert final["executor_reconciliation_claimed_at"] is not None
    assert final["executor_reconciliation_retry_not_before"] is not None
    assert final["executor_terminal_reconciliation_attempt_count"] == 17
    assert business == {
        "run_status": "cancelled",
        "result_json": {"message": "cancelled"},
        "attempt_status": "cancelled",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy_settlement_compat", [True, False])
async def test_cleanup_claim_release_binds_three_parameters_in_both_compatibility_modes(
    sandbox_attempt_authority_db, legacy_settlement_compat,
):
    from app.sandbox.infrastructure import leases_postgres as repository

    connect, _attempt_id, _owner_generation = sandbox_attempt_authority_db
    async with connect() as conn, conn.transaction():
        await conn.execute(
            "update sandbox_leases set executor_cleanup_status='claimed', "
            "executor_cleanup_claim_token='cleanup-claim', executor_cleanup_claimed_at=now(), "
            "executor_reconciliation_status='claimed', "
            "executor_reconciliation_claim_token='legacy-claim', "
            "executor_reconciliation_claimed_at=now(), "
            "executor_reconciliation_retry_not_before=null where id='lease-a'"
        )
        assert await repository.release_failed_sandbox_executor_reconciliation_cleanup_claim(
            conn,
            lease_id="lease-a",
            claim_token="cleanup-claim",
            error="stop_failed",
            legacy_settlement_compat=legacy_settlement_compat,
        )
        row = await (
            await conn.execute(
                "select executor_cleanup_status, executor_cleanup_claim_token, "
                "executor_reconciliation_status, executor_reconciliation_claim_token, "
                "executor_reconciliation_retry_not_before from sandbox_leases where id='lease-a'"
            )
        ).fetchone()
    assert row["executor_cleanup_status"] == "retry"
    assert row["executor_cleanup_claim_token"] is None
    if legacy_settlement_compat:
        assert row["executor_reconciliation_status"] == "claimed"
        assert row["executor_reconciliation_claim_token"] is None
        assert row["executor_reconciliation_retry_not_before"] is not None
    else:
        assert row["executor_reconciliation_status"] == "claimed"
        assert row["executor_reconciliation_claim_token"] == "legacy-claim"
        assert row["executor_reconciliation_retry_not_before"] is None
