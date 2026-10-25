from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import time
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.rows import dict_row

from app import schema_migrations
from app.bootstrap.attempt_settlement_projection import (
    build_attempt_settlement_projection_service,
)
from app.runs.infrastructure import postgres as attempt_postgres
from app.runs.infrastructure.attempt_settlement_projection_postgres import (
    AttemptSettlementProjectionConflict,
)
from app.runs.infrastructure import attempt_settlement_projection_postgres as settlement_store
from app.runs.domain.attempt_settlement_projection import (
    project_legacy_attempt_settlement,
)
from tests.support.db_transactions import event_loop_policy as event_loop_policy
from tests.support.frozen_receipts import frozen_spec


NOW = datetime(2026, 10, 25, 4, 0, tzinfo=timezone.utc)


def attempt(*, status="running", generation=4, started=NOW, **overrides):
    return {
        "id": "attempt-a",
        "tenant_id": "tenant-a",
        "run_id": "run-a",
        "status": status,
        "owner_kind": "queue_worker",
        "owner_id": "worker-a",
        "owner_generation": generation,
        "started_at": started,
        "finished_at": None,
        "error_code": None,
        "terminal_reason": "",
        **overrides,
    }


def run(*, status="running", cancel_requested_at=None, **overrides):
    return {
        "id": "run-a",
        "tenant_id": "tenant-a",
        "status": status,
        "cancel_requested_at": cancel_requested_at,
        "result_json": {},
        "error_code": None,
        "finished_at": None,
        **overrides,
    }


def lease(
    *,
    status="active",
    reconciliation_status="pending",
    generation=4,
    receipt=None,
    context=None,
    **overrides,
):
    return {
        "id": "lease-a",
        "tenant_id": "tenant-a",
        "run_id": "run-a",
        "attempt_id": "attempt-a",
        "status": status,
        "lease_payload_json": {"owner_generation": generation},
        "executor_status": "completed" if receipt else "accepted",
        "executor_terminal_json": receipt,
        "executor_reconciliation_context_json": context,
        "executor_reconciliation_intent_context_json": context,
        "executor_reconciliation_status": reconciliation_status,
        "executor_reconciliation_claim_token": None,
        "executor_reconciliation_claimed_at": None,
        "executor_reconciliation_retry_not_before": None,
        "executor_terminal_reconciliation_attempt_count": 2,
        "released_at": NOW if status == "released" else None,
        **overrides,
    }


def test_pending_projection_preserves_receipt_context_retry_and_owner_generation():
    receipt = {
        "status": "succeeded",
        "answer": "complete",
        "diagnostics": ["mutable reconciliation note"],
    }
    retry_at = datetime(2026, 10, 25, 4, 1, tzinfo=timezone.utc)
    projected = project_legacy_attempt_settlement(
        attempt(),
        run(),
        [
            lease(
                reconciliation_status="retry",
                receipt=receipt,
                context={"snapshot": "accepted-context"},
                executor_reconciliation_retry_not_before=retry_at,
            )
        ],
    )

    values = projected.to_mapping()
    canonical_receipt = {"answer": "complete", "status": "succeeded"}
    assert values["mapping_state"] == "mapped"
    assert values["dispatch_state"] == "accepted"
    assert values["accepted_context_json"] == {"snapshot": "accepted-context"}
    assert values["intent_recovery_context_json"] == {"snapshot": "accepted-context"}
    assert values["receipt_json"] == canonical_receipt
    assert values["receipt_identity"] == "sandbox_terminal:lease-a:attempt-a"
    assert values["receipt_sha256"] == hashlib.sha256(
        json.dumps(canonical_receipt, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    assert values["settlement_phase"] == "retry"
    assert values["settlement_attempt_count"] == 2
    assert values["next_retry_at"] == retry_at
    assert values["settlement_claim_token"] is None
    assert values["attempt_owner_generation_snapshot"] == 4
    assert values["receipt_owner_generation"] == 4


def test_terminal_receipt_before_accepted_context_remains_explicit():
    projected = project_legacy_attempt_settlement(
        attempt(),
        run(),
        [
            lease(
                reconciliation_status="waiting_terminal",
                receipt={"status": "succeeded"},
                context=None,
            )
        ],
    )

    values = projected.to_mapping()
    assert values["mapping_state"] == "mapped"
    assert values["dispatch_state"] == "receipt_before_acceptance"
    assert values["settlement_phase"] == "awaiting_context"
    assert values["accepted_context_json"] is None


def test_accepted_context_without_terminal_receipt_keeps_receipt_tuple_empty():
    projected = project_legacy_attempt_settlement(
        attempt(),
        run(),
        [
            lease(
                reconciliation_status="waiting_terminal",
                receipt=None,
                context={"snapshot": "accepted-context"},
            )
        ],
    )

    values = projected.to_mapping()
    assert values["mapping_state"] == "mapped"
    assert values["dispatch_state"] == "accepted"
    assert values["settlement_phase"] == "awaiting_receipt"
    assert values["accepted_context_json"] == {"snapshot": "accepted-context"}
    assert values["receipt_identity"] is None
    assert values["receipt_json"] is None
    assert values["receipt_sha256"] is None
    assert values["receipt_owner_generation"] is None


@pytest.mark.parametrize("legacy_phase", ["cleanup_pending", "failed", "finalized"])
def test_settled_result_keeps_sandbox_cleanup_claim_outside_attempt_projection(legacy_phase):
    terminal_attempt_status = "failed" if legacy_phase == "failed" else "succeeded"
    terminal_run_status = terminal_attempt_status
    projected = project_legacy_attempt_settlement(
        attempt(
            status=terminal_attempt_status,
            generation=5,
            finished_at=NOW,
            error_code="terminal_reconciliation_failed" if legacy_phase == "failed" else None,
        ),
        run(
            status=terminal_run_status,
            result_json={"answer": "committed"},
            error_code="terminal_reconciliation_failed" if legacy_phase == "failed" else None,
            finished_at=NOW,
        ),
        [
            lease(
                status="released",
                reconciliation_status=legacy_phase,
                generation=4,
                receipt={"status": "succeeded"},
                context={"snapshot": "accepted-context"},
                executor_reconciliation_claim_token=(
                    "cleanup-claim" if legacy_phase in {"cleanup_pending", "failed"} else None
                ),
                executor_reconciliation_claimed_at=(
                    NOW if legacy_phase in {"cleanup_pending", "failed"} else None
                ),
            )
        ],
    )

    values = projected.to_mapping()
    assert values["mapping_state"] == "mapped"
    assert values["settlement_phase"] == "settled"
    assert values["result_json"] == {"answer": "committed"}
    assert values["result_status"] == terminal_run_status
    assert values["settlement_claim_token"] is None
    assert projected.cleanup.released_lease_count == 1
    assert projected.cleanup.cleanup_claim_count == (
        1 if legacy_phase in {"cleanup_pending", "failed"} else 0
    )
    assert projected.cleanup.released_pending_settlement_count == 0
    assert "cleanup_claim_token" not in values


def test_released_lease_can_still_be_pending_settlement():
    projected = project_legacy_attempt_settlement(
        attempt(),
        run(),
        [
            lease(
                status="released",
                reconciliation_status="claimed",
                receipt={"status": "succeeded"},
                context={"snapshot": "accepted-context"},
                executor_reconciliation_claim_token="settlement-claim",
                executor_reconciliation_claimed_at=NOW,
            )
        ],
    )

    values = projected.to_mapping()
    assert values["mapping_state"] == "mapped"
    assert values["settlement_phase"] == "claimed"
    assert values["settlement_claim_token"] == "settlement-claim"
    assert projected.cleanup.released_pending_settlement_count == 1


def test_unknown_and_conflicting_attempts_are_never_hidden():
    unknown = project_legacy_attempt_settlement(
        attempt(), run(), [lease(generation=9, receipt={"status": "succeeded"}, context={})]
    )
    conflict = project_legacy_attempt_settlement(
        attempt(),
        run(),
        [
            lease(receipt={"status": "succeeded"}, context={}),
            lease(id="lease-b", receipt={"status": "failed"}, context={}),
        ],
    )

    assert unknown.to_mapping()["mapping_state"] == "unknown"
    assert conflict.to_mapping()["mapping_state"] == "conflict"
    assert conflict.cleanup.lease_count == 2


def test_stale_maintenance_generation_gap_requires_reconciler_transition_facts():
    receipt = {"status": "failed", "answer": "interrupted"}
    recovered = project_legacy_attempt_settlement(
        attempt(
            status="failed",
            generation=6,
            started=NOW,
            finished_at=NOW,
            owner_kind="reconciler",
            owner_id="stale-run-maintenance",
            terminal_reason="run_failed",
            error_code="stale_run_interrupted",
        ),
        run(
            status="failed",
            result_json={"answer": "interrupted"},
            error_code="stale_run_interrupted",
            finished_at=NOW,
        ),
        [lease(generation=4, receipt=receipt, context={"snapshot": "accepted"}, reconciliation_status="failed")],
    )
    assert recovered.to_mapping()["mapping_state"] == "mapped"
    assert recovered.to_mapping()["settlement_phase"] == "settled"

    old_owner = project_legacy_attempt_settlement(
        attempt(
            status="failed",
            generation=6,
            finished_at=NOW,
            owner_kind="queue_worker",
            owner_id="old-worker",
            terminal_reason="run_failed",
            error_code="stale_run_interrupted",
        ),
        run(
            status="failed",
            result_json={"answer": "interrupted"},
            error_code="stale_run_interrupted",
            finished_at=NOW,
        ),
        [lease(generation=4, receipt=receipt, context={"snapshot": "accepted"}, reconciliation_status="failed")],
    )
    assert old_owner.to_mapping()["mapping_state"] == "unknown"


def test_probe_claim_without_receipt_does_not_become_settlement_claim():
    projected = project_legacy_attempt_settlement(
        attempt(),
        run(),
        [
            lease(
                reconciliation_status="claimed",
                receipt=None,
                context={"snapshot": "accepted"},
                executor_reconciliation_claim_token="probe-token",
                executor_reconciliation_claimed_at=NOW,
            )
        ],
    )

    values = projected.to_mapping()
    assert values["mapping_state"] == "mapped"
    assert values["settlement_phase"] == "awaiting_receipt"
    assert values["settlement_claim_token"] is None
    assert values["settlement_claimed_at"] is None


@pytest.mark.asyncio
async def test_settlement_view_preserves_sandbox_probe_and_cleanup_claims():
    from app.sandbox.application.attempt_settlement_shadow import (
        AttemptSettlementShadowLeaseRepository,
    )

    probe_source = lease(
        reconciliation_status="claimed",
        receipt=None,
        context={"snapshot": "accepted"},
        executor_reconciliation_claim_token="probe-token",
        executor_reconciliation_claimed_at=NOW,
    )
    cleanup_source = lease(
        status="released",
        run_id="run-b",
        attempt_id="attempt-b",
        reconciliation_status="cleanup_pending",
        receipt={"status": "succeeded"},
        context={"snapshot": "accepted"},
        executor_reconciliation_claim_token="cleanup-token",
        executor_reconciliation_claimed_at=NOW,
    )
    cleanup_projection = project_legacy_attempt_settlement(
        attempt(
            id="attempt-b", run_id="run-b", status="succeeded", generation=5,
            finished_at=NOW,
        ),
        run(id="run-b", status="succeeded", result_json={"answer": "done"}, finished_at=NOW),
        [cleanup_source],
    ).to_mapping()
    probe_projection = project_legacy_attempt_settlement(
        attempt(), run(), [probe_source]
    ).to_mapping()

    class Repository:
        async def get_sandbox_lease(self, _conn, *, run_id, **_kwargs):
            return dict({"run-a": probe_source, "run-b": cleanup_source}[run_id])

    class Settlement:
        async def storage_version(self, _conn):
            return "attempt.v1"

        async def read_attempt(self, _conn, *, run_id, **_kwargs):
            return {"run-a": probe_projection, "run-b": cleanup_projection}[run_id]

    reader = AttemptSettlementShadowLeaseRepository(
        repository=Repository(),
        settlement_projection=Settlement(),
        enabled=lambda: False,
    )
    probe = await reader.get_sandbox_lease(None, run_id="run-a")
    cleanup = await reader.get_sandbox_lease(None, run_id="run-b")

    assert probe["settlement_phase"] == "awaiting_receipt"
    assert probe["executor_reconciliation_status"] == "claimed"
    assert probe["executor_reconciliation_claim_token"] == "probe-token"
    assert cleanup["settlement_phase"] == "settled"
    assert cleanup["executor_reconciliation_status"] == "cleanup_pending"
    assert cleanup["executor_reconciliation_claim_token"] == "cleanup-token"


@pytest.mark.asyncio
async def test_resource_lease_reader_does_not_require_attempt_settlement_detail():
    from app.sandbox.application.attempt_settlement_shadow import (
        AttemptSettlementShadowLeaseRepository,
    )

    source = lease()

    class Repository:
        async def get_sandbox_lease(self, _conn, **_kwargs):
            return dict(source)

    class MissingSettlement:
        async def storage_version(self, _conn):
            return "attempt.v1"

        async def read_attempt(self, _conn, **_kwargs):
            raise RuntimeError("attempt_settlement_projection_missing")

    reader = AttemptSettlementShadowLeaseRepository(
        repository=Repository(),
        settlement_projection=MissingSettlement(),
        enabled=lambda: False,
    )

    with pytest.raises(RuntimeError, match="attempt_settlement_projection_missing"):
        await reader.get_sandbox_lease(None, lease_id="lease-a")
    resource = await reader.get_resource_sandbox_lease(None, lease_id="lease-a")
    assert resource["id"] == "lease-a"


@pytest.mark.asyncio
async def test_attempt_settlement_activation_requires_explicit_operator_acceptance():
    from app.runs.infrastructure.attempt_settlement_projection_postgres import (
        activate_attempt_settlement_storage,
    )

    with pytest.raises(RuntimeError, match="attempt_settlement_operator_confirmations_required"):
        await activate_attempt_settlement_storage(None)


@pytest.fixture
async def attempt_settlement_db():
    """Use an isolated PostgreSQL instance and expand a pre-N30 table shape."""

    name = f"attempt-settlement-{uuid4().hex[:12]}"
    started = subprocess.run(
        [
            "docker", "run", "--detach", "--rm", "--name", name,
            "-e", "POSTGRES_USER=settlement_test",
            "-e", "POSTGRES_PASSWORD=settlement_test_only",
            "-e", "POSTGRES_DB=settlement_test",
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
        dsn = f"postgresql://settlement_test:settlement_test_only@127.0.0.1:{port}/settlement_test"
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

        schema = "attempt_settlement_" + uuid4().hex
        await admin.execute(sql.SQL("create schema {}").format(sql.Identifier(schema)))

        @asynccontextmanager
        async def connect():
            async with await psycopg.AsyncConnection.connect(
                dsn, options=f"-c search_path={schema}", row_factory=dict_row
            ) as conn:
                yield conn

        @asynccontextmanager
        async def transaction_factory():
            async with connect() as conn, conn.transaction():
                yield conn

        async def index_connection_factory():
            conn = await psycopg.AsyncConnection.connect(
                dsn, options=f"-c search_path={schema}", row_factory=dict_row
            )
            await conn.set_autocommit(True)
            return conn

        async with connect() as conn:
            await conn.execute(Path("app/schema.sql").read_text(encoding="utf-8"))
            # Emulate the last pre-N30 shape while keeping real PG data and constraints.
            # The N42 claim table is outside this historical fixture and must
            # leave no foreign-key dependency on the Attempt identity index.
            await conn.execute("drop table if exists run_stale_reconciliation_claims")
            await conn.execute("drop table run_attempt_settlements cascade")
            await conn.execute(
                "alter table run_attempts drop constraint run_attempts_tenant_id_run_id_id_key"
            )
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
                update runs set context_snapshot_id = 'context-a', input_json = '{
                  "executor_type":"claude-agent-worker", "context_snapshot_id":"context-a",
                  "context_snapshot":{"context_snapshot_id":"context-a"}}' where id = 'run-a';
                """
            )
            attempt_row = await attempt_postgres.start_worker_run_attempt(
                conn,
                tenant_id="tenant-a",
                run_id="run-a",
                queue_attempt_id="qat-a",
                worker_id="worker-a",
                execution_spec=frozen_spec(),
            )
            assert attempt_row["status"] == "running"
            generation = attempt_row["owner_generation"]
            await conn.execute(
                """
                insert into sandbox_leases(
                  id, tenant_id, workspace_id, user_id, session_id, run_id, attempt_id,
                  sandbox_mode, provider, status, lease_payload_json, released_at,
                  executor_status, executor_terminal_json, executor_terminal_received_at,
                  executor_reconciliation_context_json, executor_reconciliation_status,
                  executor_reconciliation_retry_not_before,
                  executor_terminal_reconciliation_attempt_count
                ) values (
                  'lease-a', 'tenant-a', 'workspace-a', 'user-a', 'session-a', 'run-a', %s,
                  'ephemeral', 'fake', 'released', %s::jsonb, now(),
                  'completed', '{"status":"succeeded","answer":"first"}'::jsonb, now(),
                  '{"snapshot":"accepted-context"}'::jsonb, 'retry', now() + interval '1 second', 2
                )
                """,
                (attempt_row["id"], json.dumps({"owner_generation": generation})),
            )
            await conn.execute(
                "insert into schema_migrations(version, checksum_sha256) values (%s, %s)",
                ("2026.10.25.2", "a" * 64),
            )

        first = await schema_migrations.apply_migrations(
            transaction_factory=transaction_factory,
            index_connection_factory=index_connection_factory,
        )
        second = await schema_migrations.apply_migrations(
            transaction_factory=transaction_factory,
            index_connection_factory=index_connection_factory,
        )
        async with connect() as conn:
            status = await schema_migrations.schema_status(conn)
        yield connect, generation, first, second, status
        await admin.execute(sql.SQL("drop schema {} cascade").format(sql.Identifier(schema)))
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


@pytest.fixture
async def attempt_settlement_cutover_schema(attempt_settlement_db):
    """Create a clean schema for real two-connection cutover races."""

    base_connect, *_ = attempt_settlement_db
    schema = "attempt_cutover_" + uuid4().hex

    async with base_connect() as conn:
        await conn.execute(sql.SQL("create schema {}").format(sql.Identifier(schema)))
        await conn.commit()
        await conn.execute(sql.SQL("set search_path to {}").format(sql.Identifier(schema)))
        await conn.commit()
        schema_source = Path("app/schema.sql").read_text(encoding="utf-8")
        for relation in ("mcp_servers", "mcp_tools"):
            schema_source = schema_source.replace(
                f"where conname = '{relation}_endpoint_not_persisted'",
                f"where conname = '{relation}_endpoint_not_persisted' "
                f"and conrelid = '{relation}'::regclass",
            )
        schema_source = schema_source.replace(
            "where conname = 'mcp_servers_integration_kind_valid'",
            "where conname = 'mcp_servers_integration_kind_valid' "
            "and conrelid = 'mcp_servers'::regclass",
        )
        await conn.execute(schema_source)
        await conn.commit()

    @asynccontextmanager
    async def connect_schema():
        async with base_connect() as conn:
            await conn.execute(sql.SQL("set search_path to {}").format(sql.Identifier(schema)))
            await conn.commit()
            yield conn

    @asynccontextmanager
    async def transaction_schema():
        async with connect_schema() as conn, conn.transaction():
            yield conn

    async with transaction_schema() as conn:
        await conn.execute("insert into tenants(id, name) values ('tenant-a', 'Tenant')")
        await conn.execute(
            "insert into workspaces(id, tenant_id, name) values ('workspace-a', 'tenant-a', 'Workspace')"
        )
        await conn.execute(
            "insert into users(id, tenant_id, display_name) values ('user-a', 'tenant-a', 'User')"
        )
        await conn.execute(
            "insert into agents(id, tenant_id, name, agent_type) "
            "values ('agent-a', 'tenant-a', 'Agent', 'chat')"
        )
        await conn.execute(
            "insert into sessions(id, tenant_id, workspace_id, user_id, agent_id, title) "
            "values ('session-a', 'tenant-a', 'workspace-a', 'user-a', 'agent-a', 'Session')"
        )
        await conn.execute(
            "insert into model_gateway_revisions(revision, base_url, api_key_ciphertext, key_fingerprint, created_by) "
            "values (3, 'https://gateway.example.test', decode('00', 'hex'), 'aaaaaaaaaaaaaaaa', 'fixture')"
        )
        await conn.execute(
            """
            insert into runs(id, tenant_id, workspace_id, user_id, session_id, agent_id,
              execution_kind, status, model_id, model_value, model_gateway_revision,
              auto_compact_window, max_output_tokens)
            values ('run-cutover', 'tenant-a', 'workspace-a', 'user-a', 'session-a', 'agent-a',
              'harness_chat', 'queued', 'model-a', 'claude-test', 3, 200000, 4096)
            """
        )
        await conn.execute(
            "insert into run_context_snapshots(id, tenant_id, workspace_id, user_id, session_id, run_id) "
            "values ('context-cutover', 'tenant-a', 'workspace-a', 'user-a', 'session-a', 'run-cutover')"
        )
        await conn.execute(
            "update runs set context_snapshot_id='context-cutover', input_json=%s::jsonb where id='run-cutover'",
            (json.dumps({
                "executor_type": "claude-agent-worker",
                "context_snapshot_id": "context-cutover",
                "context_snapshot": {"context_snapshot_id": "context-cutover"},
            }),),
        )
        attempt_row = await attempt_postgres.start_worker_run_attempt(
            conn,
            tenant_id="tenant-a",
            run_id="run-cutover",
            queue_attempt_id="qat-cutover",
            worker_id="worker-cutover",
            execution_spec=frozen_spec(
                run_id="run-cutover",
                context_snapshot_id="context-cutover",
                context_snapshot={"context_snapshot_id": "context-cutover"},
            ),
        )
        await conn.execute(
            """
            insert into sandbox_leases(
              id, tenant_id, workspace_id, user_id, session_id, run_id, attempt_id,
              sandbox_mode, provider, status, lease_payload_json,
              executor_status, executor_terminal_json, executor_terminal_received_at,
              executor_reconciliation_context_json, executor_reconciliation_intent_context_json,
              executor_reconciliation_status, executor_reconciliation_retry_not_before,
              executor_terminal_reconciliation_attempt_count
            ) values (
              'lease-cutover', 'tenant-a', 'workspace-a', 'user-a', 'session-a', 'run-cutover', %s,
              'ephemeral', 'fake', 'active', %s::jsonb,
              'completed', %s::jsonb, now(), %s::jsonb, %s::jsonb,
              'retry', now() - interval '1 second', 1
            )
            """,
            (
                attempt_row["id"],
                json.dumps({"attempt_id": attempt_row["id"], "owner_generation": str(attempt_row["owner_generation"])}),
                json.dumps({"run_id": "run-cutover", "status": "succeeded", "answer": "ready"}),
                json.dumps({"adapter_name": "test", "snapshot": "accepted"}),
                json.dumps({"adapter_name": "test", "snapshot": "accepted"}),
            ),
        )
        projection = await settlement_store.refresh_attempt_settlement_projection(
            conn,
            tenant_id="tenant-a",
            run_id="run-cutover",
            attempt_id=str(attempt_row["id"]),
        )
        assert projection is not None and projection.to_mapping()["mapping_state"] == "mapped"
        await settlement_store.persist_attempt_settlement_projection(conn, projection)

    try:
        yield connect_schema, transaction_schema, str(attempt_row["id"])
    finally:
        async with base_connect() as conn:
            await conn.execute(sql.SQL("drop schema {} cascade").format(sql.Identifier(schema)))
            await conn.commit()


@pytest.mark.asyncio
async def test_real_postgres_cutover_waits_for_writer_refresh_and_uses_fresh_snapshot(
    attempt_settlement_db, attempt_settlement_cutover_schema,
):
    from app.runs.application.attempt_lifecycle import RunAttemptLifecycleService

    base_connect, *_ = attempt_settlement_db
    connect_schema, transaction_schema, attempt_id = attempt_settlement_cutover_schema
    scope = {
        "tenant_id": "tenant-a",
        "run_id": "run-cutover",
        "attempt_id": attempt_id,
    }
    confirmations = {
        "run_and_sandbox_writers_drained": True,
        "all_source_writers_n40_compatible": True,
        "real_sdk_acceptance_complete": True,
        "real_sandbox_lifecycle_acceptance_complete": True,
        "fourteen_lifecycle_scenarios_complete": True,
    }

    writer_locked = asyncio.Event()
    allow_unprojected_write = asyncio.Event()

    async def source_writer_without_projection():
        async with transaction_schema() as conn:
            from app.runs.infrastructure.attempt_settlement_projection_postgres import (
                lock_attempt_settlement_writer_fence,
            )
            await lock_attempt_settlement_writer_fence(conn)
            await settlement_store.lock_attempt_settlement_projection_sources(conn, **scope)
            writer_locked.set()
            await allow_unprojected_write.wait()
            await conn.execute(
                "update sandbox_leases "
                "set executor_terminal_reconciliation_attempt_count = "
                "executor_terminal_reconciliation_attempt_count + 1 "
                "where id='lease-cutover'"
            )

    activation_pid: list[int] = []
    activation_started = asyncio.Event()

    async def activate():
        async with connect_schema() as conn, conn.transaction():
            activation_pid.append(conn.pgconn.backend_pid)
            activation_started.set()
            return await settlement_store.activate_attempt_settlement_storage(
                conn,
                operator_confirmations=confirmations,
                lock_timeout_seconds=8,
            )

    async def wait_for_activation_blocker() -> None:
        assert activation_pid
        deadline = asyncio.get_running_loop().time() + 5
        while asyncio.get_running_loop().time() < deadline:
            async with base_connect() as conn:
                row = await (
                    await conn.execute(
                        "select pg_blocking_pids(%s) as blockers",
                        (activation_pid[0],),
                    )
                ).fetchone()
            if row and row["blockers"]:
                return
            await asyncio.sleep(0.05)
        raise AssertionError("activation did not wait on the source writer lock")

    source_writer_task = asyncio.create_task(source_writer_without_projection())
    activation_task = None
    try:
        await asyncio.wait_for(writer_locked.wait(), timeout=5)
        activation_task = asyncio.create_task(activate())
        await asyncio.wait_for(activation_started.wait(), timeout=5)
        await wait_for_activation_blocker()
        assert not activation_task.done()
        allow_unprojected_write.set()
        await asyncio.wait_for(source_writer_task, timeout=5)
        blocked = await asyncio.wait_for(activation_task, timeout=10)
        assert blocked["status"] == "blocked"
        assert blocked["storage_version"] == "sandbox_lease.v1"
        assert blocked["comparison"]["mismatch_count"] == 1

        async with transaction_schema() as conn:
            projection = await settlement_store.refresh_attempt_settlement_projection(
                conn, **scope
            )
            assert projection is not None
            await settlement_store.persist_attempt_settlement_projection(conn, projection)

        cancel_started = asyncio.Event()
        allow_cancel_write = asyncio.Event()

        class PausingPersistence:
            def __getattr__(self, name):
                return getattr(attempt_postgres, name)

            async def request_run_attempt_cancel(self, conn, **kwargs):
                cancel_started.set()
                await allow_cancel_write.wait()
                return await attempt_postgres.request_run_attempt_cancel(conn, **kwargs)

        lifecycle = RunAttemptLifecycleService(
            persistence=PausingPersistence(),
            settlement_projection=build_attempt_settlement_projection_service(),
            settlement_shadow_enabled=lambda: True,
        )

        async def cancel_source_attempt():
            async with transaction_schema() as conn:
                await conn.execute(
                    "update runs set cancel_requested_at=coalesce(cancel_requested_at, now()) "
                    "where tenant_id='tenant-a' and id='run-cutover'"
                )
                return await lifecycle.request_cancel(conn, **scope)

        cancel_task = asyncio.create_task(cancel_source_attempt())
        activation_pid.clear()
        activation_started.clear()
        second_activation_task = None
        try:
            await asyncio.wait_for(cancel_started.wait(), timeout=5)
            second_activation_task = asyncio.create_task(activate())
            await asyncio.wait_for(activation_started.wait(), timeout=5)
            await wait_for_activation_blocker()
            assert not second_activation_task.done()
            allow_cancel_write.set()
            cancelled = await asyncio.wait_for(cancel_task, timeout=5)
            assert cancelled is not None and cancelled["status"] == "cancel_requested"
            activated = await asyncio.wait_for(second_activation_task, timeout=10)
            assert activated["status"] == "activated"
            assert activated["storage_version"] == "attempt.v1"
            assert activated["comparison"]["missing_count"] == 0
            assert activated["comparison"]["unknown_count"] == 0
            assert activated["comparison"]["mismatch_count"] == 0
        finally:
            allow_cancel_write.set()
            await asyncio.gather(cancel_task, return_exceptions=True)
            if second_activation_task is not None:
                await asyncio.gather(second_activation_task, return_exceptions=True)

        shadow_off_lifecycle = RunAttemptLifecycleService(
            persistence=attempt_postgres,
            settlement_projection=build_attempt_settlement_projection_service(),
            settlement_shadow_enabled=lambda: False,
        )
        async with transaction_schema() as conn:
            await conn.execute(
                "update runs set status='cancelled', result_json=%s::jsonb, finished_at=now() "
                "where tenant_id='tenant-a' and id='run-cutover'",
                (json.dumps({"message": "cancelled"}),),
            )
            terminal = await shadow_off_lifecycle.terminalize(
                conn,
                **scope,
                status="cancelled",
                terminal_reason="run_cancelled",
            )
            detail = await (
                await conn.execute(
                    "select mapping_state, attempt_status_snapshot, run_status_snapshot "
                    "from run_attempt_settlements where tenant_id=%s and run_id=%s and attempt_id=%s",
                    (scope["tenant_id"], scope["run_id"], scope["attempt_id"]),
                )
            ).fetchone()
            comparison = await settlement_store.compare_attempt_settlement_projections(
                conn, tenant_id="tenant-a", limit=100
            )
        assert terminal is not None and terminal["status"] == "cancelled"
        assert detail == {
            "mapping_state": "mapped",
            "attempt_status_snapshot": "cancelled",
            "run_status_snapshot": "cancelled",
        }
        assert comparison["mismatch_count"] == 0
        assert comparison["missing_count"] == 0
        assert comparison["unknown_count"] == 0

        from app.runs.application.attempt_settlement_projection import AttemptSettlementReadError
        from app.sandbox.application.attempt_settlement_shadow import (
            AttemptSettlementShadowLeaseRepository,
        )
        from app.sandbox.infrastructure import leases_postgres

        resource_port = AttemptSettlementShadowLeaseRepository(
            repository=leases_postgres,
            settlement_projection=build_attempt_settlement_projection_service(),
            enabled=lambda: False,
        )
        async with transaction_schema() as conn:
            await conn.execute(
                "delete from run_attempt_settlements where tenant_id=%s and run_id=%s and attempt_id=%s",
                (scope["tenant_id"], scope["run_id"], scope["attempt_id"]),
            )
            with pytest.raises(AttemptSettlementReadError, match="projection_missing"):
                await resource_port.get_sandbox_lease(
                    conn,
                    tenant_id="tenant-a",
                    user_id="user-a",
                    run_id="run-cutover",
                    lease_id="lease-cutover",
                )
            resource = await resource_port.get_resource_sandbox_lease(
                conn,
                tenant_id="tenant-a",
                user_id="user-a",
                run_id="run-cutover",
                lease_id="lease-cutover",
            )
            assert resource is not None and resource["status"] == "active"
            await conn.execute(
                "update sandbox_leases set executor_terminal_json=null, "
                "executor_reconciliation_status='waiting_terminal' where id='lease-cutover'"
            )
            released = await resource_port.release_sandbox_lease(
                conn,
                tenant_id="tenant-a",
                user_id="user-a",
                run_id="run-cutover",
                lease_id="lease-cutover",
                reason="test_cleanup",
            )
        assert released is not None and released["status"] == "released"
    finally:
        allow_unprojected_write.set()
        await asyncio.gather(source_writer_task, return_exceptions=True)
        if activation_task is not None:
            await asyncio.gather(activation_task, return_exceptions=True)


@pytest.mark.asyncio
async def test_real_postgres_shadow_claim_and_terminalize_share_source_lock_order(
    attempt_settlement_db, monkeypatch,
):
    from types import SimpleNamespace

    import app.bootstrap.sandbox_lease_ports as sandbox_lease_ports
    from app.bootstrap.sandbox_lease_ports import build_sandbox_executor_reconciliation_port
    from app.runs.application.attempt_lifecycle import RunAttemptLifecycleService

    connect, *_ = attempt_settlement_db
    monkeypatch.setattr(
        sandbox_lease_ports,
        "get_settings",
        lambda: SimpleNamespace(run_attempt_settlement_shadow_enabled=True),
    )
    lease_port = build_sandbox_executor_reconciliation_port()
    lifecycle = RunAttemptLifecycleService(
        persistence=attempt_postgres,
        settlement_projection=build_attempt_settlement_projection_service(),
    )
    claim_ready = asyncio.Event()
    release_claim = asyncio.Event()
    terminalize_started = asyncio.Event()
    run_id = "run-shadow-lock"
    context_id = "context-shadow-lock"
    lease_id = "lease-shadow-lock"

    async with connect() as conn, conn.transaction():
        await conn.execute(
            "insert into runs(id, tenant_id, workspace_id, user_id, session_id, agent_id, "
            "execution_kind, status, model_id, model_value, model_gateway_revision, "
            "auto_compact_window, max_output_tokens) values "
            "(%s, 'tenant-a', 'workspace-a', 'user-a', 'session-a', 'agent-a', "
            "'harness_chat', 'queued', 'model-a', 'claude-test', 3, 200000, 4096)",
            (run_id,),
        )
        await conn.execute(
            "insert into run_context_snapshots(id, tenant_id, workspace_id, user_id, session_id, run_id) "
            "values (%s, 'tenant-a', 'workspace-a', 'user-a', 'session-a', %s)",
            (context_id, run_id),
        )
        await conn.execute(
            "update runs set context_snapshot_id=%s, input_json=%s::jsonb where id=%s",
            (
                context_id,
                json.dumps({
                    "executor_type": "claude-agent-worker",
                    "context_snapshot_id": context_id,
                    "context_snapshot": {"context_snapshot_id": context_id},
                }),
                run_id,
            ),
        )
        attempt_row = await attempt_postgres.start_worker_run_attempt(
            conn,
            tenant_id="tenant-a",
            run_id=run_id,
            queue_attempt_id="qat-shadow-lock",
            worker_id="worker-shadow-lock",
            execution_spec=frozen_spec(
                run_id=run_id,
                context_snapshot_id=context_id,
                context_snapshot={"context_snapshot_id": context_id},
            ),
        )
        attempt_id = str(attempt_row["id"])
        await conn.execute(
            "insert into sandbox_leases(id, tenant_id, workspace_id, user_id, session_id, run_id, "
            "attempt_id, sandbox_mode, provider, status, lease_payload_json, executor_status, "
            "executor_terminal_json, executor_terminal_received_at, executor_reconciliation_context_json, "
            "executor_reconciliation_intent_context_json, executor_reconciliation_status, "
            "executor_reconciliation_retry_not_before, executor_terminal_reconciliation_attempt_count) "
            "values (%s, 'tenant-a', 'workspace-a', 'user-a', 'session-a', %s, %s, 'ephemeral', "
            "'fake', 'released', %s::jsonb, 'failed', %s::jsonb, now(), %s::jsonb, %s::jsonb, "
            "'retry', now() - interval '1 second', 1)",
            (
                lease_id,
                run_id,
                attempt_id,
                json.dumps({"owner_generation": attempt_row["owner_generation"]}),
                json.dumps({"run_id": run_id, "status": "failed", "answer": "terminal"}),
                json.dumps({"adapter_name": "test", "snapshot": "accepted"}),
                json.dumps({"adapter_name": "test", "snapshot": "accepted"}),
            ),
        )

    async def claim_and_hold_transaction():
        async with connect() as conn, conn.transaction():
            rows = await lease_port.claim_sandbox_executor_reconciliations(
                conn,
                claim_token="claim-while-terminalizing",
                limit=1,
                stale_after_seconds=30,
            )
            assert [row["id"] for row in rows] == [lease_id]
            claim_ready.set()
            await release_claim.wait()
        return rows

    async def terminalize_in_parallel():
        await claim_ready.wait()
        async with connect() as conn, conn.transaction():
            terminalize_started.set()
            return await lifecycle.terminalize(
                conn,
                tenant_id="tenant-a",
                run_id=run_id,
                attempt_id=attempt_id,
                status="failed",
                terminal_reason="run_failed",
            )

    claim_task = asyncio.create_task(claim_and_hold_transaction())
    terminalize_task = asyncio.create_task(terminalize_in_parallel())
    try:
        done, _pending = await asyncio.wait((claim_task,), timeout=5)
        if claim_task in done:
            await claim_task
        await asyncio.wait_for(claim_ready.wait(), timeout=5)
        await asyncio.wait_for(terminalize_started.wait(), timeout=5)
        await asyncio.sleep(0.1)
        release_claim.set()
        claim_rows, terminal_attempt = await asyncio.wait_for(
            asyncio.gather(claim_task, terminalize_task), timeout=10
        )
        assert claim_rows[0]["executor_reconciliation_claim_token"] == "claim-while-terminalizing"
        assert terminal_attempt["status"] == "failed"
        async with connect() as conn, conn.transaction():
            detail = await (
                await conn.execute(
                    "select mapping_state, attempt_status_snapshot, settlement_phase, "
                    "settlement_claim_token from run_attempt_settlements "
                    "where tenant_id='tenant-a' and run_id=%s",
                    (run_id,),
                )
            ).fetchone()
            assert detail == {
                "mapping_state": "mapped",
                "attempt_status_snapshot": "failed",
                "settlement_phase": "claimed",
                "settlement_claim_token": "claim-while-terminalizing",
            }
    finally:
        claim_ready.set()
        release_claim.set()
        await asyncio.wait_for(
            asyncio.gather(claim_task, terminalize_task, return_exceptions=True),
            timeout=10,
        )
        async with connect() as conn, conn.transaction():
            await conn.execute(
                "delete from run_attempt_settlements where tenant_id='tenant-a' and run_id=%s",
                (run_id,),
            )
            await conn.execute(
                "delete from sandbox_leases where tenant_id='tenant-a' and run_id=%s",
                (run_id,),
            )
            await conn.execute(
                "delete from run_attempts where tenant_id='tenant-a' and run_id=%s",
                (run_id,),
            )
            await conn.execute(
                "delete from run_context_snapshots where tenant_id='tenant-a' and run_id=%s",
                (run_id,),
            )
            await conn.execute("delete from runs where tenant_id='tenant-a' and id=%s", (run_id,))


@pytest.mark.asyncio
async def test_real_postgres_expand_repeat_backfill_compare_and_receipt_fence(attempt_settlement_db):
    connect, original_generation, first, second, status = attempt_settlement_db
    projection_service = build_attempt_settlement_projection_service()
    assert first["status"] == "applied"
    assert first["version"] == "2026.10.25.10"
    assert second["status"] == "current"
    assert status["ready"] is True
    assert status["target_version"] == "2026.10.25.10"

    async with connect() as conn, conn.transaction():
        storage_marker = await (
            await conn.execute(
                "select storage_version from run_attempt_settlement_storage where singleton=true"
            )
        ).fetchone()
        assert storage_marker == {"storage_version": "sandbox_lease.v1"}
        await conn.execute(
            """
            insert into runs(id, tenant_id, workspace_id, user_id, session_id, agent_id,
              execution_kind, status, model_id, model_value, model_gateway_revision,
              auto_compact_window, max_output_tokens)
            values ('run-b', 'tenant-a', 'workspace-a', 'user-a', 'session-a', 'agent-a',
              'harness_chat', 'queued', 'model-a', 'claude-test', 3, 200000, 4096)
            """
        )
        await conn.execute(
            """
            insert into run_context_snapshots(id, tenant_id, workspace_id, user_id, session_id, run_id)
            values ('context-b', 'tenant-a', 'workspace-a', 'user-a', 'session-a', 'run-b')
            """
        )
        await conn.execute(
            """
            update runs set context_snapshot_id = 'context-b', input_json = '{
              "executor_type":"claude-agent-worker", "context_snapshot_id":"context-b",
              "context_snapshot":{"context_snapshot_id":"context-b"}}' where id = 'run-b'
            """
        )
        no_lease_attempt = await attempt_postgres.start_worker_run_attempt(
            conn,
            tenant_id="tenant-a",
            run_id="run-b",
            queue_attempt_id="qat-b",
            worker_id="worker-b",
            execution_spec=frozen_spec(
                run_id="run-b",
                context_snapshot_id="context-b",
                context_snapshot={"context_snapshot_id": "context-b"},
            ),
        )
        assert no_lease_attempt["status"] == "running"

        await conn.execute(
            """
            insert into runs(id, tenant_id, workspace_id, user_id, session_id, agent_id,
              execution_kind, status, model_id, model_value, model_gateway_revision,
              auto_compact_window, max_output_tokens)
            values ('run-c', 'tenant-a', 'workspace-a', 'user-a', 'session-a', 'agent-a',
              'harness_chat', 'queued', 'model-a', 'claude-test', 3, 200000, 4096)
            """
        )
        await conn.execute(
            """
            insert into run_context_snapshots(id, tenant_id, workspace_id, user_id, session_id, run_id)
            values ('context-c', 'tenant-a', 'workspace-a', 'user-a', 'session-a', 'run-c')
            """
        )
        await conn.execute(
            """
            update runs set context_snapshot_id = 'context-c', input_json = '{
              "executor_type":"claude-agent-worker", "context_snapshot_id":"context-c",
              "context_snapshot":{"context_snapshot_id":"context-c"}}' where id = 'run-c'
            """
        )
        accepted_attempt = await attempt_postgres.start_worker_run_attempt(
            conn,
            tenant_id="tenant-a",
            run_id="run-c",
            queue_attempt_id="qat-c",
            worker_id="worker-c",
            execution_spec=frozen_spec(
                run_id="run-c",
                context_snapshot_id="context-c",
                context_snapshot={"context_snapshot_id": "context-c"},
            ),
        )
        await conn.execute(
            """
            insert into sandbox_leases(
              id, tenant_id, workspace_id, user_id, session_id, run_id, attempt_id,
              sandbox_mode, provider, status, lease_payload_json, executor_status,
              executor_reconciliation_context_json, executor_reconciliation_status
            ) values (
              'lease-c', 'tenant-a', 'workspace-a', 'user-a', 'session-a', 'run-c', %s,
              'ephemeral', 'fake', 'active', %s::jsonb, 'accepted',
              '{"snapshot":"accepted-context"}'::jsonb, 'waiting_terminal'
            )
            """,
            (accepted_attempt["id"], json.dumps({"owner_generation": accepted_attempt["owner_generation"]})),
        )

    async with connect() as conn, conn.transaction():
        attempt_id = (
            await (await conn.execute("select id from run_attempts where run_id='run-a'")).fetchone()
        )["id"]
        before = await (
            await conn.execute(
                "select status, owner_generation from run_attempts where id = %s",
                (attempt_id,),
            )
        ).fetchone()
        assert before["status"] == "running"
        assert before["owner_generation"] == original_generation

        initial_compare = await projection_service.compare(conn, limit=800)
        assert initial_compare["comparison_scope"] == "page"
        assert "switch_ready" not in initial_compare
        assert initial_compare["attempt_count"] == 3
        assert initial_compare["missing_count"] == 3
        assert initial_compare["unknown_count"] == 1

        backfill = await projection_service.backfill(conn, limit=800)
        assert backfill["attempt_count"] == 3
        assert backfill["inserted_count"] == 3
        assert backfill["unknown_count"] == 1
        assert backfill["cleanup_owner"] == "sandbox_leases"
        assert backfill["cleanup"]["released_lease_count"] == 1
        assert backfill["cleanup"]["released_pending_settlement_count"] == 1

        repeated = await projection_service.backfill(conn, limit=800)
        assert repeated["inserted_count"] == 0
        assert repeated["already_present_count"] == 3
        mapped_compare = await projection_service.compare(conn, limit=800)
        assert mapped_compare["comparison_scope"] == "page"
        assert "switch_ready" not in mapped_compare
        assert mapped_compare["attempt_count"] == 3
        assert mapped_compare["mapped_count"] == 2
        assert mapped_compare["unknown_count"] == 1
        assert mapped_compare["missing_count"] == 0
        assert mapped_compare["mismatch_count"] == 0

        accepted_row, accepted_run, accepted_leases = await settlement_store._load_locked_attempt_facts(
            conn,
            key=("tenant-a", "run-c", accepted_attempt["id"]),
            lock=False,
        )
        accepted_projection = project_legacy_attempt_settlement(
            accepted_row, accepted_run, accepted_leases
        )
        assert accepted_projection.to_mapping()["receipt_owner_generation"] is None
        await projection_service.persist_in_transaction(conn, accepted_projection)
        accepted_persisted = await (
            await conn.execute(
                """
                select dispatch_state, settlement_phase, receipt_identity, receipt_json,
                  receipt_sha256, receipt_owner_generation, accepted_context_json
                from run_attempt_settlements where tenant_id = 'tenant-a' and run_id = 'run-c'
                """
            )
        ).fetchone()
        assert accepted_persisted == {
            "dispatch_state": "accepted",
            "settlement_phase": "awaiting_receipt",
            "receipt_identity": None,
            "receipt_json": None,
            "receipt_sha256": None,
            "receipt_owner_generation": None,
            "accepted_context_json": {"snapshot": "accepted-context"},
        }

        first_page = await projection_service.compare(conn, limit=1)
        assert first_page["comparison_scope"] == "page"
        assert first_page["attempt_count"] == 1
        assert first_page["unknown_count"] == 0
        assert first_page["next_after"] == ("tenant-a", "run-a", attempt_id)
        assert "switch_ready" not in first_page
        await conn.execute(
            """
            insert into runs(id, tenant_id, workspace_id, user_id, session_id, agent_id,
              execution_kind, status, model_id, model_value, model_gateway_revision,
              auto_compact_window, max_output_tokens)
            values ('run-0', 'tenant-a', 'workspace-a', 'user-a', 'session-a', 'agent-a',
              'harness_chat', 'queued', 'model-a', 'claude-test', 3, 200000, 4096)
            """
        )
        await conn.execute(
            """
            insert into run_context_snapshots(id, tenant_id, workspace_id, user_id, session_id, run_id)
            values ('context-0', 'tenant-a', 'workspace-a', 'user-a', 'session-a', 'run-0')
            """
        )
        await conn.execute(
            """
            update runs set context_snapshot_id = 'context-0', input_json = '{
              "executor_type":"claude-agent-worker", "context_snapshot_id":"context-0",
              "context_snapshot":{"context_snapshot_id":"context-0"}}' where id = 'run-0'
            """
        )
        await attempt_postgres.start_worker_run_attempt(
            conn,
            tenant_id="tenant-a",
            run_id="run-0",
            queue_attempt_id="qat-0",
            worker_id="worker-0",
            execution_spec=frozen_spec(
                run_id="run-0",
                context_snapshot_id="context-0",
                context_snapshot={"context_snapshot_id": "context-0"},
            ),
        )
        scanned_pages = [first_page]
        scanned_attempts = first_page["attempt_count"]
        cursor = first_page["next_after"]
        while cursor is not None:
            page = await projection_service.compare(conn, after=cursor, limit=1)
            scanned_pages.append(page)
            scanned_attempts += page["attempt_count"]
            cursor = page["next_after"]
        assert [page["unknown_count"] for page in scanned_pages] == [0, 1, 0, 0]
        assert all(page["comparison_scope"] == "page" for page in scanned_pages)
        assert all("switch_ready" not in page for page in scanned_pages)
        total_attempts = await (
            await conn.execute("select count(*) as count from run_attempts")
        ).fetchone()
        assert scanned_attempts == 3
        assert total_attempts["count"] == 4

        unchanged = await (
            await conn.execute(
                "select status, owner_generation from run_attempts where id = %s",
                (attempt_id,),
            )
        ).fetchone()
        assert unchanged == before

        await conn.execute(
            """
            update run_attempts
            set status = 'succeeded', owner_generation = owner_generation + 1,
                finished_at = now(), updated_at = now()
            where id = %s
            """,
            (attempt_id,),
        )
        await conn.execute(
            """
            update sandbox_leases
            set executor_reconciliation_status = 'cleanup_pending',
                executor_reconciliation_claim_token = 'cleanup-claim',
                executor_reconciliation_claimed_at = now()
            where id = 'lease-a'
            """
        )
        terminal_attempt, terminal_run, terminal_leases = await settlement_store._load_locked_attempt_facts(
            conn,
            key=("tenant-a", "run-a", attempt_id),
            lock=False,
        )
        cleanup_projection = project_legacy_attempt_settlement(
            terminal_attempt, terminal_run, terminal_leases
        )
        assert cleanup_projection.to_mapping()["settlement_phase"] == "settled"
        assert cleanup_projection.to_mapping()["settlement_claim_token"] is None
        assert cleanup_projection.cleanup.cleanup_pending_count == 1
        assert cleanup_projection.cleanup.cleanup_claim_count == 1
        generation_before_detail_update = terminal_attempt["owner_generation"]
        await projection_service.persist_in_transaction(conn, cleanup_projection)
        generation_after_detail_update = await (
            await conn.execute(
                "select owner_generation from run_attempts where id = %s",
                (attempt_id,),
            )
        ).fetchone()
        assert generation_after_detail_update["owner_generation"] == generation_before_detail_update
        cleanup_compare = await projection_service.compare(conn, limit=800)
        assert cleanup_compare["attempt_count"] == 4
        assert cleanup_compare["cleanup_pending_count"] == 1
        assert cleanup_compare["cleanup_claim_count"] == 1
        assert cleanup_compare["released_pending_settlement_count"] == 0

        await conn.execute(
            """
            update sandbox_leases
            set executor_terminal_json = '{"status":"succeeded","answer":"replacement"}'::jsonb
            where id = 'lease-a'
            """
        )
        attempt_row, run_row, leases = await settlement_store._load_locked_attempt_facts(
            conn,
            key=("tenant-a", "run-a", attempt_id),
            lock=False,
        )
        changed_projection = project_legacy_attempt_settlement(attempt_row, run_row, leases)
        with pytest.raises(AttemptSettlementProjectionConflict, match="immutable_conflict:receipt_sha256"):
            await projection_service.persist_in_transaction(conn, changed_projection)
        retained = await (
            await conn.execute(
                "select receipt_json, receipt_sha256 from run_attempt_settlements where attempt_id = %s",
                (attempt_id,),
            )
        ).fetchone()
        assert retained["receipt_json"] == {"answer": "first", "status": "succeeded"}
        assert retained["receipt_sha256"] != changed_projection.to_mapping()["receipt_sha256"]
        mismatch = await projection_service.compare(conn, limit=800)
        assert mismatch["attempt_count"] == 4
        assert mismatch["unknown_count"] == 2
        assert mismatch["mismatch_count"] == 1
        assert mismatch["comparison_scope"] == "page"
        assert "switch_ready" not in mismatch

        after = await (
            await conn.execute("select status, owner_generation from run_attempts where id = %s", (attempt_id,))
        ).fetchone()
        assert after == {
            "status": "succeeded",
            "owner_generation": generation_before_detail_update,
        }


@pytest.mark.asyncio
async def test_real_postgres_shadow_writers_and_stable_snapshot(
    attempt_settlement_db, monkeypatch,
):
    from types import SimpleNamespace

    import app.bootstrap.sandbox_lease_ports as sandbox_lease_ports
    from app.bootstrap.sandbox_lease_ports import build_sandbox_executor_callback_lease_port
    from app.runs.application.attempt_lifecycle import RunAttemptLifecycleService

    connect, *_ = attempt_settlement_db
    projection_service = build_attempt_settlement_projection_service()
    monkeypatch.setattr(
        sandbox_lease_ports,
        "get_settings",
        lambda: SimpleNamespace(run_attempt_settlement_shadow_enabled=True),
    )
    lease_port = build_sandbox_executor_callback_lease_port()

    async def create_attempt(conn, run_id: str, context_id: str, queue_id: str):
        await conn.execute(
            """
            insert into runs(id, tenant_id, workspace_id, user_id, session_id, agent_id,
              execution_kind, status, model_id, model_value, model_gateway_revision,
              auto_compact_window, max_output_tokens)
            values (%s, 'tenant-a', 'workspace-a', 'user-a', 'session-a', 'agent-a',
              'harness_chat', 'queued', 'model-a', 'claude-test', 3, 200000, 4096)
            """,
            (run_id,),
        )
        await conn.execute(
            """
            insert into run_context_snapshots(id, tenant_id, workspace_id, user_id, session_id, run_id)
            values (%s, 'tenant-a', 'workspace-a', 'user-a', 'session-a', %s)
            """,
            (context_id, run_id),
        )
        await conn.execute(
            """
            update runs set context_snapshot_id = %s, input_json = %s::jsonb where id = %s
            """,
            (
                context_id,
                json.dumps({
                    "executor_type": "claude-agent-worker",
                    "context_snapshot_id": context_id,
                    "context_snapshot": {"context_snapshot_id": context_id},
                }),
                run_id,
            ),
        )
        return await attempt_postgres.start_worker_run_attempt(
            conn,
            tenant_id="tenant-a",
            run_id=run_id,
            queue_attempt_id=queue_id,
            worker_id=f"worker-{run_id}",
            execution_spec=frozen_spec(
                run_id=run_id,
                context_snapshot_id=context_id,
                context_snapshot={"context_snapshot_id": context_id},
            ),
        )

    async def create_lease(conn, *, run_id: str, attempt_id: str, lease_id: str, generation: int):
        await conn.execute(
            """
            insert into sandbox_leases(
              id, tenant_id, workspace_id, user_id, session_id, run_id, attempt_id,
              sandbox_mode, provider, status, lease_payload_json, executor_status,
              executor_reconciliation_status, executor_terminal_reconciliation_attempt_count,
              executor_heartbeat_at
            ) values (
              %s, 'tenant-a', 'workspace-a', 'user-a', 'session-a', %s, %s,
              'ephemeral', 'fake', 'active', %s::jsonb, 'accepted', 'waiting_terminal', 0,
              now()
            )
            """,
            (lease_id, run_id, attempt_id, json.dumps({"owner_generation": generation})),
        )

    async with connect() as conn, conn.transaction():
        live_attempt = await create_attempt(conn, "run-d", "context-d", "qat-d")
        await create_lease(
            conn,
            run_id="run-d",
            attempt_id=live_attempt["id"],
            lease_id="lease-d",
            generation=live_attempt["owner_generation"],
        )

    accepted_context = {"adapter_name": "test", "accepted_context": "persisted"}

    async def accept_on_own_connection():
        async with connect() as conn, conn.transaction():
            return await lease_port.record_sandbox_executor_accepted(
                conn,
                tenant_id="tenant-a",
                run_id="run-d",
                attempt_id=live_attempt["id"],
                lease_id="lease-d",
                reconciliation_context=accepted_context,
            )

    accepted, repeated_accept = await asyncio.gather(
        accept_on_own_connection(), accept_on_own_connection()
    )
    assert accepted["executor_terminal_json"] is None
    assert repeated_accept["executor_reconciliation_context_json"] == accepted_context
    async with connect() as conn, conn.transaction():
        detail = await (
            await conn.execute(
                "select * from run_attempt_settlements where tenant_id='tenant-a' and run_id='run-d'"
            )
        ).fetchone()
        source = await (
            await conn.execute("select owner_generation from run_attempts where id=%s", (live_attempt["id"],))
        ).fetchone()
        assert detail["dispatch_state"] == "accepted"
        assert detail["settlement_phase"] == "awaiting_receipt"
        assert detail["accepted_context_json"] == accepted_context
        assert detail["receipt_identity"] is None
        assert source["owner_generation"] == live_attempt["owner_generation"]

        terminal_attempt = await create_attempt(conn, "run-e", "context-e", "qat-e")
        await create_lease(
            conn,
            run_id="run-e",
            attempt_id=terminal_attempt["id"],
            lease_id="lease-e",
            generation=terminal_attempt["owner_generation"],
        )
        intent_attempt = await create_attempt(conn, "run-intent", "context-intent", "qat-intent")
        await create_lease(
            conn,
            run_id="run-intent",
            attempt_id=intent_attempt["id"],
            lease_id="lease-intent",
            generation=intent_attempt["owner_generation"],
        )

    from app.sandbox.application.attempt_settlement_shadow import (
        AttemptSettlementShadowDispatchRepository,
    )
    from app.sandbox.application.dispatch_intent import SandboxDispatchIdentity
    from app.sandbox.infrastructure import dispatch_postgres

    dispatch_shadow = AttemptSettlementShadowDispatchRepository(
        repository=dispatch_postgres,
        settlement_projection=projection_service,
        enabled=lambda: True,
    )
    intent_context = {"adapter_name": "test", "snapshot": "frozen-before-dispatch"}
    accepted_with_timings = {
        **intent_context,
        "dispatch_timings": {"sandbox_executor_dispatch_latency_ms": 12},
    }
    intent_identity = SandboxDispatchIdentity(
        tenant_id="tenant-a",
        run_id="run-intent",
        attempt_id=intent_attempt["id"],
        owner_generation=intent_attempt["owner_generation"],
        lease_id="lease-intent",
        provider="fake",
        provider_task_id=intent_attempt["id"],
    )
    async with connect() as conn, conn.transaction():
        await conn.execute(
            "update sandbox_leases set runtime_handle_verified_at=now(), lease_payload_json=%s::jsonb where id='lease-intent'",
            (json.dumps({"attempt_id": intent_attempt["id"], "owner_generation": str(intent_attempt["owner_generation"])}),),
        )
        await dispatch_shadow.persist_intent(
            conn,
            identity=intent_identity,
            reconciliation_context=intent_context,
        )
        await dispatch_shadow.record_outcome(conn, identity=intent_identity, outcome="accepted")
        await lease_port.record_sandbox_executor_accepted(
            conn,
            tenant_id="tenant-a",
            run_id="run-intent",
            attempt_id=intent_attempt["id"],
            lease_id="lease-intent",
            reconciliation_context=accepted_with_timings,
        )
        intent_detail = await (
            await conn.execute(
                "select dispatch_state, intent_recovery_context_json, accepted_context_json "
                "from run_attempt_settlements where tenant_id='tenant-a' and run_id='run-intent'"
            )
        ).fetchone()
        stored_intent_context = await (
            await conn.execute(
                "select executor_reconciliation_intent_context_json "
                "from sandbox_leases where id='lease-intent'"
            )
        ).fetchone()
        assert intent_detail == {
            "dispatch_state": "accepted",
            "intent_recovery_context_json": intent_context,
            "accepted_context_json": accepted_with_timings,
        }
        assert stored_intent_context == {
            "executor_reconciliation_intent_context_json": intent_context
        }

    from app.bootstrap.sandbox_lease_ports import build_sandbox_executor_reconciliation_port

    reconciler_port = build_sandbox_executor_reconciliation_port()
    async with connect() as conn, conn.transaction():
        await conn.execute(
            "update sandbox_leases set executor_probe_claim_token='probe-live', "
            "executor_probe_claimed_at=now() where id in ('lease-e', 'lease-intent')"
        )
        await conn.execute(
            "update sandbox_leases set executor_heartbeat_at=now() - interval '1 minute', "
            "updated_at=now() - interval '1 minute' where id='lease-d'"
        )
        probes = await reconciler_port.claim_sandbox_executor_suspects(
            conn,
            claim_token="probe-d",
            limit=10,
            stale_after_seconds=30,
        )
        assert [row["id"] for row in probes] == ["lease-d"]
        probe_detail = await (
            await conn.execute(
                "select settlement_phase, settlement_claim_token, settlement_claimed_at "
                "from run_attempt_settlements where tenant_id='tenant-a' and run_id='run-d'"
            )
        ).fetchone()
        assert probe_detail == {
            "settlement_phase": "awaiting_receipt",
            "settlement_claim_token": None,
            "settlement_claimed_at": None,
        }

    async with connect() as conn, conn.transaction():
        receipt = {"run_id": "run-e", "status": "succeeded", "answer": "early"}
        await lease_port.record_sandbox_executor_terminal(
            conn,
            tenant_id="tenant-a",
            run_id="run-e",
            attempt_id=terminal_attempt["id"],
            lease_id="lease-e",
            executor_status="completed",
            terminal_result=receipt,
        )
        early = await (
            await conn.execute(
                "select * from run_attempt_settlements where tenant_id='tenant-a' and run_id='run-e'"
            )
        ).fetchone()
        assert early["dispatch_state"] == "receipt_before_acceptance"
        assert early["settlement_phase"] == "awaiting_context"

    from app.runs.application.attempt_settlement_projection import AttemptSettlementProjectionService

    async def fail_shadow(self, conn, projection, *, insert_only=False):
        raise RuntimeError("injected_attempt_settlement_shadow_failure")

    with monkeypatch.context() as patcher:
        patcher.setattr(AttemptSettlementProjectionService, "persist_in_transaction", fail_shadow)
        with pytest.raises(RuntimeError, match="injected_attempt_settlement_shadow_failure"):
            async with connect() as conn, conn.transaction():
                await lease_port.record_sandbox_executor_accepted(
                    conn,
                    tenant_id="tenant-a",
                    run_id="run-e",
                    attempt_id=terminal_attempt["id"],
                    lease_id="lease-e",
                    reconciliation_context={"adapter_name": "test", "accepted_context": "late"},
                )
    async with connect() as conn, conn.transaction():
        source = await (
            await conn.execute("select executor_reconciliation_context_json from sandbox_leases where id='lease-e'")
        ).fetchone()
        detail = await (
            await conn.execute(
                "select dispatch_state, accepted_context_json, settlement_phase "
                "from run_attempt_settlements where tenant_id='tenant-a' and run_id='run-e'"
            )
        ).fetchone()
        assert source["executor_reconciliation_context_json"] is None
        assert detail == {
            "dispatch_state": "receipt_before_acceptance",
            "accepted_context_json": None,
            "settlement_phase": "awaiting_context",
        }

        await lease_port.record_sandbox_executor_accepted(
            conn,
            tenant_id="tenant-a",
            run_id="run-e",
            attempt_id=terminal_attempt["id"],
            lease_id="lease-e",
            reconciliation_context={"adapter_name": "test", "accepted_context": "late"},
        )
        from app.bootstrap.sandbox_lease_ports import build_sandbox_executor_reconciliation_port

        reconciler_port = build_sandbox_executor_reconciliation_port()
        await conn.execute(
            "update sandbox_leases set executor_reconciliation_retry_not_before=now() + interval '1 day' "
            "where id='lease-a'"
        )
        claims = await reconciler_port.claim_sandbox_executor_reconciliations(
            conn, claim_token="claim-e", limit=10, stale_after_seconds=60,
        )
        assert any(row["id"] == "lease-e" for row in claims)
        claimed_detail = await (
            await conn.execute(
                "select settlement_phase, settlement_claim_token from run_attempt_settlements "
                "where tenant_id='tenant-a' and run_id='run-e'"
            )
        ).fetchone()
        assert claimed_detail == {"settlement_phase": "claimed", "settlement_claim_token": "claim-e"}

    async with connect() as conn, conn.transaction():
        assert await reconciler_port.retry_sandbox_executor_reconciliation(
            conn, lease_id="lease-e", claim_token="claim-e", error="transient",
        ) is True
        retry_detail = await (
            await conn.execute(
                "select settlement_phase, next_retry_at, settlement_attempt_count "
                "from run_attempt_settlements where tenant_id='tenant-a' and run_id='run-e'"
            )
        ).fetchone()
        assert retry_detail["settlement_phase"] == "retry"
        assert retry_detail["next_retry_at"] is not None
        assert retry_detail["settlement_attempt_count"] == 1
        await conn.execute("update sandbox_leases set status='released', released_at=now() where id='lease-e'")
        released_compare = await projection_service.compare(conn, limit=800)
        assert released_compare["released_pending_settlement_count"] >= 1

    async with connect() as conn, conn.transaction():
        run = await attempt_postgres.get_run(
            conn, tenant_id="tenant-a", run_id="run-d", for_update=True
        )
        assert run is not None
        lifecycle = RunAttemptLifecycleService(
            persistence=attempt_postgres,
            settlement_projection=projection_service,
        )
        before_generation = live_attempt["owner_generation"]
        await lifecycle.terminalize(
            conn,
            tenant_id="tenant-a",
            run_id="run-d",
            attempt_id=live_attempt["id"],
            status="succeeded",
            terminal_reason="run_succeeded",
        )
        terminal = await (
            await conn.execute("select status, owner_generation from run_attempts where id=%s", (live_attempt["id"],))
        ).fetchone()
        detail = await (
            await conn.execute(
                "select attempt_status_snapshot, attempt_owner_generation_snapshot, run_status_snapshot "
                "from run_attempt_settlements where tenant_id='tenant-a' and run_id='run-d'"
            )
        ).fetchone()
        assert terminal == {"status": "succeeded", "owner_generation": before_generation + 1}
        assert detail == {
            "attempt_status_snapshot": "succeeded",
            "attempt_owner_generation_snapshot": before_generation + 1,
            "run_status_snapshot": "succeeded",
        }

    async with connect() as conn, conn.transaction():
        cancellation_attempt = await create_attempt(
            conn, "run-cancel", "context-cancel", "qat-cancel"
        )
        await create_lease(
            conn,
            run_id="run-cancel",
            attempt_id=cancellation_attempt["id"],
            lease_id="lease-cancel",
            generation=cancellation_attempt["owner_generation"],
        )
        lifecycle = RunAttemptLifecycleService(
            persistence=attempt_postgres,
            settlement_projection=projection_service,
        )
        cancelled = await lifecycle.request_cancel(
            conn,
            tenant_id="tenant-a",
            run_id="run-cancel",
            attempt_id=cancellation_attempt["id"],
        )
        assert cancelled["status"] == "cancel_requested"
        cancelled_detail = await (
            await conn.execute(
                "select attempt_status_snapshot, attempt_owner_generation_snapshot "
                "from run_attempt_settlements where tenant_id='tenant-a' and run_id='run-cancel'"
            )
        ).fetchone()
        assert cancelled_detail == {
            "attempt_status_snapshot": "cancel_requested",
            "attempt_owner_generation_snapshot": cancellation_attempt["owner_generation"] + 1,
        }

    async with connect() as conn, conn.transaction():
        stale_attempt = await create_attempt(conn, "run-stale", "context-stale", "qat-stale")
        await create_lease(
            conn,
            run_id="run-stale",
            attempt_id=stale_attempt["id"],
            lease_id="lease-stale",
            generation=stale_attempt["owner_generation"],
        )
        stale_scope = {
            "tenant_id": "tenant-a",
            "run_id": "run-stale",
            "attempt_id": stale_attempt["id"],
            "lease_id": "lease-stale",
        }
        await lease_port.record_sandbox_executor_accepted(
            conn,
            **stale_scope,
            reconciliation_context={"adapter_name": "test", "snapshot": "stale-run"},
        )
        await lease_port.record_sandbox_executor_terminal(
            conn,
            tenant_id="tenant-a",
            run_id="run-stale",
            attempt_id=stale_attempt["id"],
            lease_id="lease-stale",
            executor_status="failed",
            terminal_result={"run_id": "run-stale", "status": "failed"},
        )
        lifecycle = RunAttemptLifecycleService(
            persistence=attempt_postgres,
            settlement_projection=projection_service,
        )
        expired = await lifecycle.prepare_stale_reconciliation(
            conn,
            tenant_id="tenant-a",
            run_id="run-stale",
            terminal_status="failed",
            reconciler_id="stale-run-maintenance",
        )
        assert expired["status"] == "expired"
        assert expired["owner_generation"] == stale_attempt["owner_generation"] + 1
        assert expired["owner_kind"] == "reconciler"
        from app.bootstrap.run_lifecycle import build_run_lifecycle_service

        run_lifecycle = build_run_lifecycle_service()
        progress = await run_lifecycle.fail_run(
            conn,
            tenant_id="tenant-a",
            run_id="run-stale",
            error_code="stale_run_interrupted",
            error_message="stale run recovery",
            result_json={"answer": "interrupted"},
        )
        assert progress.completed is True and progress.status == "failed"
        failed = await lifecycle.terminalize(
            conn,
            tenant_id="tenant-a",
            run_id="run-stale",
            attempt_id=stale_attempt["id"],
            status="failed",
            terminal_reason="run_failed",
            error_code="stale_run_interrupted",
        )
        assert failed["status"] == "failed"
        recovered_detail = await (
            await conn.execute(
                "select mapping_state, attempt_owner_generation_snapshot, settlement_phase "
                "from run_attempt_settlements where tenant_id='tenant-a' and run_id='run-stale'"
            )
        ).fetchone()
        assert recovered_detail == {
            "mapping_state": "mapped",
            "attempt_owner_generation_snapshot": stale_attempt["owner_generation"] + 2,
            "settlement_phase": "pending",
        }

    async with connect() as conn, conn.transaction():
        await conn.execute("set transaction isolation level repeatable read")
        snapshot = await projection_service.compare_snapshot(conn, page_limit=1)
        assert snapshot["comparison_scope"] == "stable_snapshot"
        assert snapshot["snapshot_complete"] is True
        assert snapshot["attempt_count"] == snapshot["snapshot_attempt_count"]
        assert "switch_ready" not in snapshot

    async with connect() as conn, conn.transaction():
        await conn.execute(
            "update sandbox_leases set executor_reconciliation_context_json = "
            "executor_reconciliation_context_json || '{\"comparison_probe\":true}'::jsonb "
            "where id='lease-intent'"
        )
    async with connect() as conn, conn.transaction():
        await conn.execute("set transaction isolation level repeatable read")
        changed_snapshot = await projection_service.compare_snapshot(conn, page_limit=1)
        assert changed_snapshot["snapshot_complete"] is True
        assert changed_snapshot["snapshot_attempt_count"] == snapshot["snapshot_attempt_count"]
        assert changed_snapshot["snapshot_max_key"] == snapshot["snapshot_max_key"]
        assert changed_snapshot["mismatch_count"] > snapshot["mismatch_count"]

    async with connect() as conn, conn.transaction():
        extra_attempt = await create_attempt(conn, "run-f", "context-f", "qat-f")
        assert extra_attempt["status"] == "running"
        await create_lease(
            conn,
            run_id="run-f",
            attempt_id=extra_attempt["id"],
            lease_id="lease-f1",
            generation=extra_attempt["owner_generation"],
        )
        await create_lease(
            conn,
            run_id="run-f",
            attempt_id=extra_attempt["id"],
            lease_id="lease-f2",
            generation=extra_attempt["owner_generation"],
        )
    async with connect() as conn, conn.transaction():
        comparison = await projection_service.compare(conn, limit=800)
        assert comparison["conflict_count"] == 1
        assert comparison["unknown_count"] >= 1


@pytest.mark.parametrize(
    "constraint_name",
    [
        "chk_run_attempt_settlements_identity",
        "chk_run_attempt_settlements_receipt",
        "chk_run_attempt_settlements_claim",
        "chk_run_attempt_settlements_retry",
        "chk_run_attempt_settlements_attempt_count",
        "chk_run_attempt_settlements_result",
    ],
)
@pytest.mark.asyncio
async def test_real_postgres_rejects_weak_attempt_settlement_constraints(
    attempt_settlement_db, constraint_name
):
    connect, *_ = attempt_settlement_db
    expected = next(
        item
        for item in schema_migrations.CRITICAL_CONSTRAINT_DEFINITIONS
        if item[0] == "run_attempt_settlements" and item[1] == constraint_name
    )
    async with connect() as conn, conn.transaction():
        await conn.execute(
            sql.SQL("alter table run_attempt_settlements drop constraint {}")
            .format(sql.Identifier(constraint_name))
        )
        await conn.execute(
            sql.SQL("alter table run_attempt_settlements add constraint {} check (true)")
            .format(sql.Identifier(constraint_name))
        )
        weakened = await schema_migrations.schema_status(conn)
        assert weakened["constraint_definitions_current"] is False
        assert weakened["ready"] is False

        await conn.execute(
            sql.SQL("alter table run_attempt_settlements drop constraint {}")
            .format(sql.Identifier(constraint_name))
        )
        await conn.execute(
            sql.SQL("alter table run_attempt_settlements add constraint {} ")
            .format(sql.Identifier(constraint_name))
            + sql.SQL(expected[3])
        )
        restored = await schema_migrations.schema_status(conn)
        assert restored["constraint_definitions_current"] is True
        assert restored["ready"] is True


@pytest.mark.asyncio
async def test_real_postgres_owner_cancel_maps_accepted_running_attempt_without_expanding_authority(
    attempt_settlement_db, monkeypatch,
):
    from types import SimpleNamespace

    import app.bootstrap.sandbox_lease_ports as sandbox_lease_ports
    from app.bootstrap.sandbox_lease_ports import build_sandbox_executor_callback_lease_port
    from app.identity.infrastructure.audit_postgres import append_audit_log
    from app.platform.postgres.errors import RepositoryConflictError
    from app.runs.application.attempt_lifecycle import RunAttemptLifecycleService
    from app.runs.infrastructure.postgres import PostgresRunCancellationPersistence
    from app.sandbox.infrastructure.leases_postgres import list_active_sandbox_leases_for_run
    from app.streaming.infrastructure.run_events_postgres import append_event

    connect, *_ = attempt_settlement_db
    monkeypatch.setattr(
        sandbox_lease_ports,
        "get_settings",
        lambda: SimpleNamespace(run_attempt_settlement_shadow_enabled=True),
    )
    projection_service = build_attempt_settlement_projection_service()
    lifecycle = RunAttemptLifecycleService(
        persistence=attempt_postgres,
        settlement_projection=projection_service,
    )
    cancellation = PostgresRunCancellationPersistence(
        attempt_lifecycle=lifecycle,
        append_event=append_event,
        append_audit_log=append_audit_log,
        list_active_sandbox_leases=list_active_sandbox_leases_for_run,
    )
    lease_port = build_sandbox_executor_callback_lease_port()
    suffix = uuid4().hex[:12]
    run_id = f"run-owner-cancel-{suffix}"
    context_id = f"context-owner-cancel-{suffix}"
    lease_id = f"lease-owner-cancel-{suffix}"
    queue_attempt_id = f"qat-owner-cancel-{suffix}"
    worker_id = f"worker-owner-cancel-{suffix}"
    queue_message_id = "c" * 64
    accepted_context = {"adapter_name": "test", "snapshot": "accepted-before-cancel"}

    async with connect() as conn, conn.transaction():
        await conn.execute(
            "insert into runs(id, tenant_id, workspace_id, user_id, session_id, agent_id, "
            "execution_kind, status, model_id, model_value, model_gateway_revision, "
            "auto_compact_window, max_output_tokens) values "
            "(%s, 'tenant-a', 'workspace-a', 'user-a', 'session-a', 'agent-a', "
            "'harness_chat', 'queued', 'model-a', 'claude-test', 3, 200000, 4096)",
            (run_id,),
        )
        await conn.execute(
            "insert into run_context_snapshots(id, tenant_id, workspace_id, user_id, session_id, run_id) "
            "values (%s, 'tenant-a', 'workspace-a', 'user-a', 'session-a', %s)",
            (context_id, run_id),
        )
        await conn.execute(
            "update runs set context_snapshot_id=%s, input_json=%s::jsonb where id=%s",
            (
                context_id,
                json.dumps({
                    "executor_type": "claude-agent-worker",
                    "context_snapshot_id": context_id,
                    "context_snapshot": {"context_snapshot_id": context_id},
                }),
                run_id,
            ),
        )
        attempt_row = await attempt_postgres.start_worker_run_attempt(
            conn,
            tenant_id="tenant-a",
            run_id=run_id,
            queue_attempt_id=queue_attempt_id,
            worker_id=worker_id,
            execution_spec=frozen_spec(
                run_id=run_id,
                context_snapshot_id=context_id,
                context_snapshot={"context_snapshot_id": context_id},
            ),
            queue_message_id=queue_message_id,
            lease_expires_at=NOW + timedelta(minutes=5),
            last_heartbeat_at=NOW,
        )
        assert attempt_row["status"] == "running"
        initial_generation = attempt_row["owner_generation"]
        await conn.execute(
            "insert into sandbox_leases(id, tenant_id, workspace_id, user_id, session_id, run_id, "
            "attempt_id, sandbox_mode, provider, status, lease_payload_json, executor_status, "
            "executor_reconciliation_status) values (%s, 'tenant-a', 'workspace-a', 'user-a', "
            "'session-a', %s, %s, 'ephemeral', 'fake', 'active', %s::jsonb, 'accepted', "
            "'waiting_terminal')",
            (
                lease_id,
                run_id,
                attempt_row["id"],
                json.dumps({
                    "attempt_id": attempt_row["id"],
                    "owner_generation": str(initial_generation),
                }),
            ),
        )
        await lease_port.record_sandbox_executor_accepted(
            conn,
            tenant_id="tenant-a",
            run_id=run_id,
            attempt_id=attempt_row["id"],
            lease_id=lease_id,
            reconciliation_context=accepted_context,
        )
        existing_detail = await (
            await conn.execute(
                "select mapping_state, dispatch_state, accepted_context_json "
                "from run_attempt_settlements where tenant_id='tenant-a' and run_id=%s",
                (run_id,),
            )
        ).fetchone()
        assert existing_detail == {
            "mapping_state": "mapped",
            "dispatch_state": "accepted",
            "accepted_context_json": accepted_context,
        }

        authority = await cancellation.begin_owner_request(
            conn,
            tenant_id="tenant-a",
            run_id=run_id,
            owner_user_id="user-a",
        )
        assert authority is not None
        assert authority.newly_requested is True
        assert authority.prior_status == "running"
        assert authority.attempt_id == attempt_row["id"]

        current = await (
            await conn.execute(
                "select status, owner_kind, owner_id, owner_generation "
                "from run_attempts where tenant_id='tenant-a' and run_id=%s",
                (run_id,),
            )
        ).fetchone()
        assert current == {
            "status": "cancel_requested",
            "owner_kind": "queue_worker",
            "owner_id": worker_id,
            "owner_generation": initial_generation + 1,
        }
        run_snapshot = await (
            await conn.execute(
                "select status, cancel_requested_at from runs where tenant_id='tenant-a' and id=%s",
                (run_id,),
            )
        ).fetchone()
        assert run_snapshot["status"] == "running"
        assert run_snapshot["cancel_requested_at"] is not None
        mapped_after_cancel = await (
            await conn.execute(
                "select mapping_state, dispatch_state, accepted_context_json, "
                "attempt_status_snapshot, attempt_owner_generation_snapshot, "
                "run_status_snapshot, cancel_requested_at_snapshot, settlement_phase "
                "from run_attempt_settlements where tenant_id='tenant-a' and run_id=%s",
                (run_id,),
            )
        ).fetchone()
        assert mapped_after_cancel == {
            "mapping_state": "mapped",
            "dispatch_state": "accepted",
            "accepted_context_json": accepted_context,
            "attempt_status_snapshot": "cancel_requested",
            "attempt_owner_generation_snapshot": initial_generation + 1,
            "run_status_snapshot": "running",
            "cancel_requested_at_snapshot": run_snapshot["cancel_requested_at"],
            "settlement_phase": "awaiting_receipt",
        }

        with pytest.raises(RepositoryConflictError, match="run_attempt_transition_conflict"):
            await attempt_postgres.transition_run_attempt(
                conn,
                tenant_id="tenant-a",
                run_id=run_id,
                attempt_id=attempt_row["id"],
                expected_status="running",
                requested_status="succeeded",
                expected_owner_kind="queue_worker",
                expected_owner_id=worker_id,
                expected_owner_generation=initial_generation,
                terminal_reason="run_succeeded",
            )
        retained = await (
            await conn.execute(
                "select status, owner_generation from run_attempts "
                "where tenant_id='tenant-a' and run_id=%s",
                (run_id,),
            )
        ).fetchone()
        assert retained == {
            "status": "cancel_requested",
            "owner_generation": initial_generation + 1,
        }


@pytest.mark.asyncio
async def test_real_postgres_reverse_batch_release_does_not_deadlock_ordered_backfill(
    attempt_settlement_db, monkeypatch,
):
    from contextlib import asynccontextmanager

    import app.executor_reconciler as executor_reconciler
    import app.runs.infrastructure.attempt_settlement_projection_postgres as settlement_store
    from app.bootstrap.sandbox_lease_ports import build_sandbox_executor_reconciliation_port
    from app.sandbox.infrastructure import executor_reconciliation_postgres

    connect, *_ = attempt_settlement_db
    projection_service = build_attempt_settlement_projection_service()
    lease_port = build_sandbox_executor_reconciliation_port()
    suffix = uuid4().hex[:12]
    run_a = f"run-zz-n31-release-a-{suffix}"
    run_b = f"run-zz-n31-release-b-{suffix}"
    context_a = f"context-n31-release-a-{suffix}"
    context_b = f"context-n31-release-b-{suffix}"
    lease_a = f"lease-n31-release-a-{suffix}"
    lease_b = f"lease-n31-release-b-{suffix}"
    claim_token = f"reverse-batch-{suffix}"
    recovered_claim_token = f"recovered-batch-{suffix}"
    initial_generations: dict[str, int] = {}

    async with connect() as conn, conn.transaction():
        for run_id, context_id, lease_id, suffix_letter in (
            (run_a, context_a, lease_a, "a"),
            (run_b, context_b, lease_b, "b"),
        ):
            await conn.execute(
                "insert into runs(id, tenant_id, workspace_id, user_id, session_id, agent_id, "
                "execution_kind, status, model_id, model_value, model_gateway_revision, "
                "auto_compact_window, max_output_tokens) values "
                "(%s, 'tenant-a', 'workspace-a', 'user-a', 'session-a', 'agent-a', "
                "'harness_chat', 'queued', 'model-a', 'claude-test', 3, 200000, 4096)",
                (run_id,),
            )
            await conn.execute(
                "insert into run_context_snapshots(id, tenant_id, workspace_id, user_id, session_id, run_id) "
                "values (%s, 'tenant-a', 'workspace-a', 'user-a', 'session-a', %s)",
                (context_id, run_id),
            )
            await conn.execute(
                "update runs set context_snapshot_id=%s, input_json=%s::jsonb where id=%s",
                (
                    context_id,
                    json.dumps({
                        "executor_type": "claude-agent-worker",
                        "context_snapshot_id": context_id,
                        "context_snapshot": {"context_snapshot_id": context_id},
                    }),
                    run_id,
                ),
            )
            attempt_row = await attempt_postgres.start_worker_run_attempt(
                conn,
                tenant_id="tenant-a",
                run_id=run_id,
                queue_attempt_id=f"qat-n31-release-{suffix_letter}-{suffix}",
                worker_id=f"worker-n31-release-{suffix_letter}-{suffix}",
                execution_spec=frozen_spec(
                    run_id=run_id,
                    context_snapshot_id=context_id,
                    context_snapshot={"context_snapshot_id": context_id},
                ),
            )
            initial_generations[run_id] = attempt_row["owner_generation"]
            terminal = {
                "run_id": run_id,
                "status": "succeeded",
                "answer": f"terminal-{suffix_letter}",
            }
            accepted_context = {
                "adapter_name": "test",
                "snapshot": f"accepted-{suffix_letter}",
            }
            await conn.execute(
                "insert into sandbox_leases(id, tenant_id, workspace_id, user_id, session_id, run_id, "
                "attempt_id, sandbox_mode, provider, status, lease_payload_json, executor_status, "
                "executor_terminal_json, executor_terminal_received_at, "
                "executor_reconciliation_context_json, executor_reconciliation_intent_context_json, "
                "executor_reconciliation_status, executor_reconciliation_claim_token, "
                "executor_reconciliation_claimed_at, executor_terminal_reconciliation_attempt_count) "
                "values (%s, 'tenant-a', 'workspace-a', 'user-a', 'session-a', %s, %s, "
                "'ephemeral', 'fake', 'released', %s::jsonb, 'completed', %s::jsonb, now(), "
                "%s::jsonb, %s::jsonb, 'claimed', %s, now(), 1)",
                (
                    lease_id,
                    run_id,
                    attempt_row["id"],
                    json.dumps({
                        "attempt_id": attempt_row["id"],
                        "owner_generation": str(attempt_row["owner_generation"]),
                    }),
                    json.dumps(terminal),
                    json.dumps(accepted_context),
                    json.dumps(accepted_context),
                    claim_token,
                ),
            )

    @asynccontextmanager
    async def isolated_transaction():
        async with connect() as conn, conn.transaction():
            yield conn

    monkeypatch.setattr(executor_reconciler, "transaction", isolated_transaction)
    source_a_locked = asyncio.Event()
    allow_backfill_to_continue = asyncio.Event()
    original_loader = settlement_store._load_locked_attempt_facts

    async def pause_after_ordered_a_lock(conn, *, key, lock):
        result = await original_loader(conn, key=key, lock=lock)
        if (
            lock
            and key[1] == run_a
            and asyncio.current_task() is not None
            and asyncio.current_task().get_name() == "n31-ordered-backfill"
        ):
            source_a_locked.set()
            await allow_backfill_to_continue.wait()
        return result

    monkeypatch.setattr(
        settlement_store, "_load_locked_attempt_facts", pause_after_ordered_a_lock
    )

    async def run_ordered_backfill():
        async with connect() as conn, conn.transaction():
            return await projection_service.backfill(conn, limit=800)

    backfill_task = asyncio.create_task(
        run_ordered_backfill(), name="n31-ordered-backfill"
    )
    release_task = None
    try:
        await asyncio.wait_for(source_a_locked.wait(), timeout=5)
        release_task = asyncio.create_task(
            executor_reconciler._release_claimed_terminal_batch(
                [{"id": lease_b}, {"id": lease_a}],
                claim_token,
                lease_port=lease_port,
            )
        )

        deadline = asyncio.get_running_loop().time() + 5
        while True:
            async with connect() as conn:
                released_b = await (
                    await conn.execute(
                        "select executor_reconciliation_status, "
                        "executor_reconciliation_claim_token from sandbox_leases where id=%s",
                        (lease_b,),
                    )
                ).fetchone()
            if released_b == {
                "executor_reconciliation_status": "retry",
                "executor_reconciliation_claim_token": None,
            }:
                break
            if asyncio.get_running_loop().time() >= deadline:
                raise AssertionError("reverse batch did not commit Run B release independently")
            await asyncio.sleep(0.05)

        assert release_task is not None and not release_task.done()
        allow_backfill_to_continue.set()
        backfill_result, _ = await asyncio.wait_for(
            asyncio.gather(backfill_task, release_task), timeout=15
        )
        assert backfill_result["attempt_count"] >= 2
    finally:
        allow_backfill_to_continue.set()
        pending = [backfill_task]
        if release_task is not None:
            pending.append(release_task)
        await asyncio.wait_for(
            asyncio.gather(*pending, return_exceptions=True), timeout=15
        )

    async with connect() as conn, conn.transaction():
        lease_rows = await (
            await conn.execute(
                "select id, executor_reconciliation_status, executor_reconciliation_claim_token, "
                "executor_reconciliation_retry_not_before from sandbox_leases "
                "where id = any(%s::text[]) order by id",
                ([lease_a, lease_b],),
            )
        ).fetchall()
        assert [row["executor_reconciliation_status"] for row in lease_rows] == [
            "retry",
            "retry",
        ]
        assert all(row["executor_reconciliation_claim_token"] is None for row in lease_rows)
        assert all(row["executor_reconciliation_retry_not_before"] is not None for row in lease_rows)
        attempt_rows = await (
            await conn.execute(
                "select run_id, owner_generation from run_attempts "
                "where tenant_id='tenant-a' and run_id = any(%s::text[]) order by run_id",
                ([run_a, run_b],),
            )
        ).fetchall()
        assert {row["run_id"]: row["owner_generation"] for row in attempt_rows} == initial_generations
        details = await (
            await conn.execute(
                "select run_id, mapping_state from run_attempt_settlements "
                "where tenant_id='tenant-a' and run_id = any(%s::text[]) order by run_id",
                ([run_a, run_b],),
            )
        ).fetchall()
        assert {row["run_id"]: row["mapping_state"] for row in details} == {
            run_a: "mapped",
            run_b: "mapped",
        }

    async def claim_recovered_batch():
        async with connect() as conn, conn.transaction():
            return await executor_reconciliation_postgres.claim_sandbox_executor_reconciliations(
                conn,
                claim_token=recovered_claim_token,
                limit=2,
                stale_after_seconds=30,
                candidate_lease_ids=[lease_a, lease_b],
            )

    deadline = asyncio.get_running_loop().time() + 5
    while True:
        async with connect() as conn:
            not_due = await (
                await conn.execute(
                    "select count(*) as count from sandbox_leases "
                    "where id = any(%s::text[]) "
                    "and executor_reconciliation_retry_not_before > now()",
                    ([lease_a, lease_b],),
                )
            ).fetchone()
        if not_due["count"] == 0:
            break
        if asyncio.get_running_loop().time() >= deadline:
            raise AssertionError("released claims did not become due for recovery")
        await asyncio.sleep(0.05)
    recovered = await claim_recovered_batch()
    assert {row["id"] for row in recovered} == {lease_a, lease_b}
    assert all(
        row["executor_reconciliation_claim_token"] == recovered_claim_token
        for row in recovered
    )
    async with connect() as conn, conn.transaction():
        attempt_rows = await (
            await conn.execute(
                "select run_id, owner_generation from run_attempts "
                "where tenant_id='tenant-a' and run_id = any(%s::text[]) order by run_id",
                ([run_a, run_b],),
            )
        ).fetchall()
        assert {row["run_id"]: row["owner_generation"] for row in attempt_rows} == initial_generations


@pytest.mark.asyncio
async def test_real_postgres_attempt_reader_drives_diagnostics_and_finalizer_authority(
    attempt_settlement_db,
):
    from app.bootstrap.sandbox_lease_ports import (
        build_sandbox_executor_reconciliation_port,
        build_sandbox_run_lease_reader,
    )
    from app.sandbox.application.reconciliation_receipt import (
        SandboxReconciliationReceiptAuthority,
    )
    from app.sandbox.infrastructure import leases_postgres
    from app.runs.infrastructure.attempt_settlement_projection_postgres import (
        activate_attempt_settlement_storage,
    )

    connect, *_ = attempt_settlement_db
    projection_service = build_attempt_settlement_projection_service()
    claim_token = "n40-reader-claim"
    async with connect() as conn, conn.transaction():
        run_id = "run-cutover-reader"
        lease_id = "lease-cutover-reader"
        await conn.execute(
            """
            insert into runs(id, tenant_id, workspace_id, user_id, session_id, agent_id,
              execution_kind, status, model_id, model_value, model_gateway_revision,
              auto_compact_window, max_output_tokens)
            values (%s, 'tenant-a', 'workspace-a', 'user-a', 'session-a', 'agent-a',
              'harness_chat', 'queued', 'model-a', 'claude-test', 3, 200000, 4096)
            """,
            (run_id,),
        )
        await conn.execute(
            "insert into run_context_snapshots(id, tenant_id, workspace_id, user_id, session_id, run_id) "
            "values ('context-cutover-reader', 'tenant-a', 'workspace-a', 'user-a', 'session-a', %s)",
            (run_id,),
        )
        await conn.execute(
            "update runs set context_snapshot_id='context-cutover-reader', input_json=%s::jsonb where id=%s",
            (json.dumps({
                "executor_type": "claude-agent-worker",
                "context_snapshot_id": "context-cutover-reader",
                "context_snapshot": {"context_snapshot_id": "context-cutover-reader"},
            }), run_id),
        )
        attempt_row = await attempt_postgres.start_worker_run_attempt(
            conn,
            tenant_id="tenant-a",
            run_id=run_id,
            queue_attempt_id="qat-cutover-reader",
            worker_id="worker-cutover-reader",
            execution_spec=frozen_spec(
                run_id=run_id,
                context_snapshot_id="context-cutover-reader",
                context_snapshot={"context_snapshot_id": "context-cutover-reader"},
            ),
        )
        attempt_id = str(attempt_row["id"])
        generation = int(attempt_row["owner_generation"])
        receipt = {"run_id": run_id, "status": "succeeded", "answer": "attempt-reader"}
        context = {"adapter_name": "test", "accepted_context": "attempt-reader"}
        await conn.execute(
            """
            insert into sandbox_leases(
              id, tenant_id, workspace_id, user_id, session_id, run_id, attempt_id,
              sandbox_mode, provider, status, lease_payload_json,
              executor_status, executor_terminal_json, executor_terminal_received_at,
              executor_reconciliation_context_json, executor_reconciliation_intent_context_json,
              executor_reconciliation_status, executor_reconciliation_claim_token,
              executor_reconciliation_claimed_at, executor_terminal_reconciliation_attempt_count
            ) values (
              %s, 'tenant-a', 'workspace-a', 'user-a', 'session-a', %s, %s,
              'ephemeral', 'fake', 'active', %s::jsonb,
              'completed', %s::jsonb, now(), %s::jsonb, %s::jsonb,
              'claimed', %s, now(), 1
            )
            """,
            (
                lease_id,
                run_id,
                attempt_id,
                json.dumps({"attempt_id": attempt_id, "owner_generation": str(generation)}),
                json.dumps(receipt),
                json.dumps(context),
                json.dumps(context),
                claim_token,
            ),
        )
        projection = await settlement_store.refresh_attempt_settlement_projection(
            conn, tenant_id="tenant-a", run_id=run_id, attempt_id=attempt_id
        )
        assert projection is not None and projection.to_mapping()["mapping_state"] == "mapped"
        await projection_service.persist_in_transaction(conn, projection)

    async with connect() as conn, conn.transaction():
        activation = await activate_attempt_settlement_storage(
            conn,
            operator_confirmations={
                "run_and_sandbox_writers_drained": True,
                "all_source_writers_n40_compatible": True,
                "real_sdk_acceptance_complete": True,
                "real_sandbox_lifecycle_acceptance_complete": True,
                "fourteen_lifecycle_scenarios_complete": True,
            },
        )
        assert activation["status"] == "blocked"
        assert activation["storage_version"] == "sandbox_lease.v1"
        # The isolated fixture's original Attempt has no detail yet. It must
        # remain in the denominator and block activation of this new reader.
        assert activation["comparison"]["missing_count"] == 1

    async with connect() as conn, conn.transaction():
        await conn.execute(
            "update run_attempt_settlement_storage set storage_version='attempt.v1' where singleton=true"
        )
        # Deliberately diverge the legacy claim alias after activation. Every
        # consumer below must take the Runs detail as its reader authority.
        await conn.execute(
            "update sandbox_leases set executor_reconciliation_claim_token='legacy-stale' "
            "where id=%s",
            (lease_id,),
        )

    async with connect() as conn, conn.transaction():
        leases = await build_sandbox_run_lease_reader().list_sandbox_leases_for_run(
            conn, tenant_id="tenant-a", run_id="run-cutover-reader"
        )
        lease_row = next(row for row in leases if row["id"] == "lease-cutover-reader")
        assert lease_row["settlement_storage_version"] == "attempt.v1"
        assert lease_row["executor_reconciliation_claim_token"] == claim_token
        assert lease_row["settlement_phase"] == "claimed"

        reconciler_port = build_sandbox_executor_reconciliation_port()
        assert await reconciler_port.is_sandbox_executor_reconciliation_claim_current(
            conn, lease_id="lease-cutover-reader", claim_token=claim_token
        ) is True

        authority = SandboxReconciliationReceiptAuthority(
            lock_receipt=leases_postgres.lock_scoped_reconciliation_receipt,
            lock_resource=leases_postgres.lock_scoped_reconciliation_resource,
            settlement_reader=projection_service,
        )
        assert await authority.is_current(
            conn,
            tenant_id="tenant-a",
            run_id="run-cutover-reader",
            attempt_id=lease_row["attempt_id"],
            lease_id="lease-cutover-reader",
            claim_token=claim_token,
            expected_owner_generation=generation,
            scope={
                "workspace_id": "workspace-a",
                "user_id": "user-a",
                "session_id": "session-a",
                "agent_id": "agent-a",
            },
        ) is True



@pytest.mark.asyncio
async def test_cutover_accounts_for_execution_receipts_without_an_attempt(attempt_settlement_db):
    from app.runs.infrastructure.attempt_settlement_projection_postgres import (
        activate_attempt_settlement_storage,
        prepare_attempt_settlement_storage_cutover,
    )

    connect, *_ = attempt_settlement_db
    async with connect() as conn, conn.transaction():
        await build_attempt_settlement_projection_service().backfill(conn, limit=800)
        await conn.execute(
            """insert into sandbox_leases(
                 id,tenant_id,workspace_id,user_id,session_id,run_id,attempt_id,
                 sandbox_mode,provider,status,lease_payload_json,executor_status,
                 executor_terminal_json,executor_terminal_received_at)
               select 'lease-unbound',tenant_id,workspace_id,user_id,session_id,run_id,null,
                 sandbox_mode,provider,'released',lease_payload_json,'failed',
                 '{"run_id":"run-a","status":"failed","error_code":"legacy"}'::jsonb,now()
               from sandbox_leases where id='lease-a'"""
        )
    report = await prepare_attempt_settlement_storage_cutover(connect)
    assert report['status'] == 'blocked'
    assert report['unbound_execution_lease_count'] == 1
    assert report['comparison']['mapped_count'] == report['comparison']['attempt_count']
    assert report['comparison']['missing_count'] == 0
    confirmations = dict.fromkeys((
        'run_and_sandbox_writers_drained', 'all_source_writers_n40_compatible',
        'real_sdk_acceptance_complete', 'real_sandbox_lifecycle_acceptance_complete',
        'fourteen_lifecycle_scenarios_complete',
    ), True)
    async with connect() as conn, conn.transaction():
        activation = await activate_attempt_settlement_storage(conn, operator_confirmations=confirmations)
        assert activation['status'] == 'blocked'
        assert activation['unbound_execution_lease_count'] == 1
        marker = await (await conn.execute(
            'select storage_version from run_attempt_settlement_storage where singleton=true'
        )).fetchone()
        assert marker['storage_version'] == 'sandbox_lease.v1'
