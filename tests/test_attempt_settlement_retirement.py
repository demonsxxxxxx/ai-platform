"""One-time legacy-data upgrade; no selectable legacy runtime is retained."""
from __future__ import annotations

from contextlib import asynccontextmanager
import json

import pytest

from app import schema_migrations
from app.bootstrap.run_attempt_lifecycle import build_run_attempt_lifecycle_service
from app.runs.infrastructure import lifecycle_postgres
from app.runs.infrastructure.attempt_settlement_upgrade import upgrade_attempt_settlement_authority
from app.runs.domain.settlement_retirement import LEGACY_ATTEMPT_SETTLEMENT_COLUMNS
from tests import test_sandbox_attempt_authority as authority_tests
settlement_upgrade_db = authority_tests.sandbox_attempt_authority_db

CONFIRMATIONS = {
    "legacy_binaries_stopped": True,
    "runs_and_resource_claims_drained": True,
    "backup_restore_drill_complete": True,
}


async def _legacy_completed_history(conn, attempt_id):
    result = {"answer": "retained historical completion"}
    await lifecycle_postgres.complete_run(
        conn, tenant_id="tenant-a", run_id="run-a", result_json=result,
        observability=(None, 0, 0, 0, 0),
    )
    await build_run_attempt_lifecycle_service().terminalize(
        conn, tenant_id="tenant-a", run_id="run-a", attempt_id=attempt_id,
        status="succeeded", terminal_reason="run_succeeded",
    )
    # This fixture is the sole historical input. Current schema/runtime never
    # creates these columns or the phased reader/writer/retirement markers.
    for column, kind in (
        ("executor_terminal_json", "jsonb"),
        ("executor_reconciliation_context_json", "jsonb"),
        ("executor_reconciliation_status", "text"),
        ("executor_reconciliation_claim_token", "text"),
        ("executor_reconciliation_claimed_at", "timestamptz"),
        ("executor_reconciliation_retry_not_before", "timestamptz"),
        ("executor_terminal_reconciliation_attempt_count", "integer"),
    ):
        await conn.execute(f"alter table sandbox_leases add column {column} {kind}")
    await conn.execute(
        """update sandbox_leases set status='released',released_at=now(),
             executor_status='completed',executor_terminal_received_at=now(),executor_reconciled_at=now(),
             executor_terminal_json=%s::jsonb,executor_reconciliation_context_json=%s::jsonb,
             executor_reconciliation_status='finalized',executor_terminal_reconciliation_attempt_count=2,
             executor_cleanup_status='not_required' where id='lease-a'""",
        (json.dumps({"run_id": "run-a", "status": "succeeded", "answer": "original receipt"}),
         json.dumps({"adapter_name": "test", "snapshot": "accepted"})),
    )
    await conn.execute("delete from run_attempt_settlements where tenant_id='tenant-a' and run_id='run-a'")
    for name in ("run_attempt_settlement_storage", "run_attempt_settlement_writer", "run_attempt_settlement_retirement"):
        await conn.execute(f"create table {name}(singleton boolean)")
    return result


@pytest.mark.asyncio
@pytest.mark.parametrize("existing_detail", [False, True])
@pytest.mark.parametrize("cleanup_status", ["not_required", "waiting_settlement"])
async def test_one_time_upgrade_preserves_history_and_removes_exact_old_surface(settlement_upgrade_db, existing_detail, cleanup_status):
    connect, attempt_id, _generation = settlement_upgrade_db
    async with connect() as conn, conn.transaction():
        result = await _legacy_completed_history(conn, attempt_id)
        await conn.execute("update sandbox_leases set executor_cleanup_status=%s", (cleanup_status,))
        await conn.execute("""update sandbox_leases set executor_terminal_json=
            executor_terminal_json || '{"diagnostics":["historical-note"]}'::jsonb""")
        if existing_detail:
            from app.runs.infrastructure.attempt_settlement_upgrade_mapping import backfill_attempt_settlement_projections
            await backfill_attempt_settlement_projections(conn)
            await conn.execute("""update run_attempt_settlements set receipt_json=
                receipt_json || '{"diagnostics":["existing-note"]}'::jsonb""")
    async with connect() as conn, conn.transaction():
        with pytest.raises(RuntimeError, match="operator_confirmation_required"):
            await upgrade_attempt_settlement_authority(conn)
    async with connect() as conn, conn.transaction():
        await upgrade_attempt_settlement_authority(conn, confirmations=CONFIRMATIONS)
        await upgrade_attempt_settlement_authority(conn, confirmations=CONFIRMATIONS)
        row = await (await conn.execute(
            "select receipt_json,result_json,settlement_phase,settlement_attempt_count,accepted_context_json "
            "from run_attempt_settlements where attempt_id=%s", (attempt_id,),
        )).fetchone()
        assert row["result_json"] == result
        assert row["receipt_json"]["answer"] == "original receipt"
        assert row["receipt_json"]["diagnostics"] == (
            ["existing-note", "historical-note"] if existing_detail else ["historical-note"]
        )
        assert row["settlement_phase"] == "settled"
        assert row["settlement_attempt_count"] == 2
        assert row["accepted_context_json"]["snapshot"] == "accepted"
        assert await schema_migrations._attempt_settlement_layout_current(conn)
        for name in ("run_attempt_settlement_storage", "run_attempt_settlement_writer", "run_attempt_settlement_retirement"):
            assert (await (await conn.execute("select to_regclass(%s) as relation", (name,))).fetchone())["relation"] is None
        lease = await (await conn.execute("select status,executor_cleanup_status,lease_payload_json from sandbox_leases where id='lease-a'")).fetchone()
        assert lease["status"] == "released" and lease["executor_cleanup_status"] == "finalized"
        assert lease["lease_payload_json"]["attempt_id"] == attempt_id


@pytest.mark.asyncio
async def test_upgrade_preserves_cancelled_before_dispatch_with_no_receipt(settlement_upgrade_db):
    connect, attempt_id, generation = settlement_upgrade_db
    async with connect() as conn, conn.transaction():
        await _legacy_completed_history(conn, attempt_id)
        await conn.execute("""update runs set status='cancelled',cancel_requested_at=now(),
            result_json='{"answer":"stopped before dispatch"}'::jsonb where id='run-a'""")
        await conn.execute("""update run_attempts set status='cancelled',terminal_reason='run_cancelled',
            owner_generation=%s where id=%s""", (generation + 2, attempt_id))
        await conn.execute("""update sandbox_leases set executor_status='pending',
            executor_terminal_json=null,executor_terminal_received_at=null,executor_reconciled_at=null,
            executor_reconciliation_context_json=null,executor_reconciliation_status='waiting_terminal',
            release_reason='run_cancelled' where id='lease-a'""")
    async with connect() as conn, conn.transaction():
        await upgrade_attempt_settlement_authority(conn, confirmations=CONFIRMATIONS)
        row = await (await conn.execute("""select mapping_state,settlement_phase,dispatch_state,
            receipt_json,accepted_context_json,result_json from run_attempt_settlements where attempt_id=%s""",
            (attempt_id,))).fetchone()
        assert row == dict(mapping_state="mapped", settlement_phase="settled", dispatch_state="unknown",
                           receipt_json=None, accepted_context_json=None, result_json={"answer": "stopped before dispatch"})
        assert await schema_migrations._attempt_settlement_layout_current(conn)
        await upgrade_attempt_settlement_authority(conn)


@pytest.mark.asyncio
@pytest.mark.parametrize("blocker", ["open_run", "unrecovered_claim", "missing_attempt_binding", "immutable_conflict", "cleanup_claim", "unsettled_receipt"])
async def test_upgrade_blocks_unsafe_history_without_dropping_data(settlement_upgrade_db, blocker):
    connect, attempt_id, _generation = settlement_upgrade_db
    async with connect() as conn, conn.transaction():
        await _legacy_completed_history(conn, attempt_id)
        if blocker == "open_run":
            await conn.execute("update runs set status='running' where id='run-a'")
        elif blocker == "unrecovered_claim":
            await conn.execute(
                """insert into execution_supervision_claims(claim_token,domain,owner_mode,worker_id,
                   owner_version,started_at,expires_at) values ('00000000-0000-0000-0000-000000000001',
                   'settlement','legacy','old-worker',1,now()-interval '2 hours',now()-interval '1 hour')"""
            )
        elif blocker == "missing_attempt_binding":
            await conn.execute("update sandbox_leases set attempt_id=null where id='lease-a'")
        elif blocker == "cleanup_claim":
            await conn.execute("""update sandbox_leases set executor_cleanup_status='waiting_settlement',
                executor_cleanup_claim_token='00000000-0000-0000-0000-000000000001',
                executor_cleanup_claimed_at=now() where id='lease-a'""")
        elif blocker == "unsettled_receipt":
            await conn.execute("""update sandbox_leases set executor_cleanup_status='waiting_settlement',
                executor_reconciled_at=null where id='lease-a'""")
        else:
            await conn.execute(
                "update sandbox_leases set executor_cleanup_status='waiting_settlement',executor_terminal_json='{}'::jsonb where id='lease-a'"
            )
    async with connect() as conn, conn.transaction():
        with pytest.raises(RuntimeError, match="attempt_settlement_upgrade_"):
            await upgrade_attempt_settlement_authority(conn, confirmations=CONFIRMATIONS)
    async with connect() as conn:
        columns = await (await conn.execute(
            "select attname from pg_attribute where attrelid='sandbox_leases'::regclass "
            "and attname=any(%s::text[]) and not attisdropped", (list(LEGACY_ATTEMPT_SETTLEMENT_COLUMNS),),
        )).fetchall()
        assert len(columns) == 7
        assert (await (await conn.execute("select count(*) as count from sandbox_leases")).fetchone())["count"] == 1
        if blocker in {"immutable_conflict", "cleanup_claim", "unsettled_receipt"}:
            assert (await (await conn.execute("select executor_cleanup_status from sandbox_leases where id='lease-a'")).fetchone())["executor_cleanup_status"] == "waiting_settlement"


@pytest.mark.asyncio
@pytest.mark.parametrize("blocker", [None, "missing_detail", "stale_claim", "mismatched_result"])
async def test_upgrade_finishes_previously_retired_layout_without_reconstructing_history(
    settlement_upgrade_db, blocker,
):
    from app.runs.infrastructure.attempt_settlement_upgrade_mapping import backfill_attempt_settlement_projections

    connect, attempt_id, _generation = settlement_upgrade_db
    async with connect() as conn, conn.transaction():
        await _legacy_completed_history(conn, attempt_id)
        await backfill_attempt_settlement_projections(conn)
        for column in LEGACY_ATTEMPT_SETTLEMENT_COLUMNS:
            await conn.execute(f"alter table sandbox_leases drop column if exists {column}")
        await conn.execute("""create function reject_legacy_attempt_settlement_write() returns trigger
            language plpgsql as $$ begin return new; end $$""")
        await conn.execute("""create trigger trg_reject_legacy_attempt_settlement_write
            before update on sandbox_leases for each row
            execute function reject_legacy_attempt_settlement_write()""")
        await conn.execute("create index idx_sandbox_leases_executor_reconcile on sandbox_leases(id)")
        await conn.execute("""alter table sandbox_leases add constraint
            chk_sandbox_leases_executor_reconciliation_status check (true)""")
        await conn.execute("""insert into schema_index_migrations(index_name,target_version,checksum_sha256,state)
            values ('idx_sandbox_leases_executor_reconcile','old','old','ready')""")
        if blocker == "missing_detail":
            await conn.execute("delete from run_attempt_settlements where attempt_id=%s", (attempt_id,))
        elif blocker == "stale_claim":
            await conn.execute("""update sandbox_leases set executor_probe_claim_token=
                '00000000-0000-0000-0000-000000000001',executor_probe_claimed_at=now()-interval '2 hours'""")
        elif blocker == "mismatched_result":
            await conn.execute("update run_attempt_settlements set result_json='{}'::jsonb where attempt_id=%s", (attempt_id,))
        before = await (await conn.execute("select * from run_attempt_settlements where attempt_id=%s", (attempt_id,))).fetchone()
    async with connect() as conn, conn.transaction():
        with pytest.raises(RuntimeError, match="operator_confirmation_required"):
            await upgrade_attempt_settlement_authority(conn)
    if blocker:
        async with connect() as conn, conn.transaction():
            with pytest.raises(RuntimeError, match="requires_drain|retired_history_invalid"):
                await upgrade_attempt_settlement_authority(conn, confirmations=CONFIRMATIONS)
        async with connect() as conn:
            assert (await (await conn.execute("select to_regclass('run_attempt_settlement_storage') as marker")).fetchone())["marker"]
        return
    async with connect() as conn, conn.transaction():
        await upgrade_attempt_settlement_authority(conn, confirmations=CONFIRMATIONS)
        await upgrade_attempt_settlement_authority(conn)
        after = await (await conn.execute("select * from run_attempt_settlements where attempt_id=%s", (attempt_id,))).fetchone()
        assert after == before
        assert await schema_migrations._attempt_settlement_layout_current(conn)
        status = await (await conn.execute("""select
            to_regclass('run_attempt_settlement_storage') as storage,
            to_regclass('run_attempt_settlement_writer') as writer,
            to_regclass('run_attempt_settlement_retirement') as retirement,
            to_regprocedure('reject_legacy_attempt_settlement_write()') as fence,
            to_regclass('idx_sandbox_leases_executor_reconcile') as old_index,
            (select count(*) from schema_index_migrations where index_name='idx_sandbox_leases_executor_reconcile') as index_ledger""")).fetchone()
        assert status == dict(storage=None, writer=None, retirement=None, fence=None, old_index=None, index_ledger=0)
        lease = await (await conn.execute("select status,executor_cleanup_status from sandbox_leases where id='lease-a'")).fetchone()
        assert lease == dict(status="released", executor_cleanup_status="finalized")


@pytest.mark.asyncio
async def test_fresh_schema_apply_is_idempotent_and_attempt_only(settlement_upgrade_db):
    connect, _attempt_id, _generation = settlement_upgrade_db

    @asynccontextmanager
    async def transaction_factory():
        async with connect() as conn, conn.transaction():
            yield conn

    async def index_connection_factory():
        # Return an owned connection outside the helper's context lifetime.
        import psycopg
        from psycopg.rows import dict_row
        async with connect() as conn:
            dsn = conn.info.dsn
            schema = (await (await conn.execute("select current_schema() as schema")).fetchone())["schema"]
        return await psycopg.AsyncConnection.connect(dsn, password="sandbox_test_only", autocommit=True, options=f"-c search_path={schema}", row_factory=dict_row)

    first = await schema_migrations.apply_migrations(transaction_factory=transaction_factory, index_connection_factory=index_connection_factory)
    second = await schema_migrations.apply_migrations(transaction_factory=transaction_factory, index_connection_factory=index_connection_factory)
    assert first["status"] == "applied" and second["status"] == "current"
    async with connect() as conn:
        status = await schema_migrations.schema_status(conn)
    assert status["ready"] is True
    assert status["settlement_layout_current"] is True
