from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
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
        "owner_generation": generation,
        "started_at": started,
        "finished_at": None,
        "error_code": None,
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


@pytest.fixture(scope="module")
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


@pytest.mark.asyncio
async def test_real_postgres_expand_repeat_backfill_compare_and_receipt_fence(attempt_settlement_db):
    connect, original_generation, first, second, status = attempt_settlement_db
    projection_service = build_attempt_settlement_projection_service()
    assert first["status"] == "applied"
    assert first["version"] == "2026.10.25.6"
    assert second["status"] == "current"
    assert status["ready"] is True
    assert status["target_version"] == "2026.10.25.6"

    async with connect() as conn, conn.transaction():
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

