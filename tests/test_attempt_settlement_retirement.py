from __future__ import annotations

import hashlib
import json
import re
from contextlib import asynccontextmanager

import psycopg
import pytest

from app import schema_migrations
from app.runs.infrastructure import attempt_settlement_writer_postgres as writer
from app.runs.infrastructure.attempt_settlement_projection_postgres import (
    activate_attempt_settlement_storage,
    backfill_attempt_settlement_projections,
    prepare_attempt_settlement_storage_cutover,
    read_attempt_settlement,
)
from app.runs.infrastructure.attempt_settlement_retirement_postgres import (
    attempt_settlement_retirement_status,
    prepare_attempt_settlement_retirement,
    retire_attempt_settlement_legacy_columns,
)
from tests.test_attempt_settlement_projection import attempt_settlement_db


_CONFIRMATIONS = {
    "legacy_binaries_stopped": True,
    "runs_and_resource_claims_drained": True,
    "backup_restore_drill_complete": True,
}
_READER_CONFIRMATIONS = {
    "run_and_sandbox_writers_drained": True,
    "all_source_writers_n40_compatible": True,
    "real_sdk_acceptance_complete": True,
    "real_sandbox_lifecycle_acceptance_complete": True,
    "fourteen_lifecycle_scenarios_complete": True,
}


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


async def _retire(conn):
    return await retire_attempt_settlement_legacy_columns(
        conn,
        operator_confirmations=_CONFIRMATIONS,
        minimum_compatible_version="attempt.v1 / ai-platform 2026.10.25.11",
    )


@pytest.mark.asyncio
async def test_retirement_blocks_gaps_and_claims_then_drops_exact_columns_idempotently(
    attempt_settlement_db,
):
    connect, *_ = attempt_settlement_db

    @asynccontextmanager
    async def transaction_factory():
        async with connect() as conn, conn.transaction():
            yield conn

    async with connect() as conn, conn.transaction():
        await backfill_attempt_settlement_projections(conn, limit=100)
        await conn.execute(
            "update run_attempt_settlement_storage set storage_version = 'attempt.v1'"
        )
        await conn.execute(
            "update run_attempt_settlement_writer set writer_version = 'attempt.v1'"
        )

    async with connect() as conn, conn.transaction():
        await conn.execute(
            "update run_attempt_settlement_retirement set state = 'retired', "
            "retired_at = now(), minimum_compatible_version = 'attempt.v1', "
            "operator_declaration = '{}'::jsonb where singleton = true"
        )
        forged = await schema_migrations.schema_status(conn)
        assert forged["ready"] is False
        assert forged["retirement_layout_current"] is False
    async with connect() as conn, conn.transaction():
        await conn.execute(
            "update run_attempt_settlement_retirement set state = 'expanded', "
            "retired_at = null, minimum_compatible_version = null, "
            "operator_declaration = null where singleton = true"
        )

    async with connect() as conn, conn.transaction():
        with pytest.raises(RuntimeError, match="legacy_mapping_unavailable"):
            await backfill_attempt_settlement_projections(conn, limit=100)
        with pytest.raises(psycopg.Error, match="legacy_attempt_settlement_write_rejected"):
            async with conn.transaction():
                await conn.execute(
                    "update sandbox_leases set executor_reconciliation_status = 'pending' "
                    "where id = 'lease-a'"
                )

    async with connect() as conn, conn.transaction():
        await conn.execute(
            "update run_attempt_settlements set mapping_state = 'unknown' "
            "where run_id = 'run-a'"
        )
    async with connect() as conn, conn.transaction():
        blocked = await _retire(conn)
        assert blocked["status"] == "blocked"
        assert blocked["compatibility_counts"]["unmapped_attempt_count"] == 1
        assert blocked["compatibility_counts"]["open_runs_count"] == 1
        assert blocked["compatibility_counts"]["pending_settlement_count"] == 1
    async with connect() as conn, conn.transaction():
        await conn.execute(
            "update run_attempt_settlements set mapping_state = 'mapped' "
            "where run_id = 'run-a'"
        )
        await conn.execute(
            "update run_attempt_settlements set settlement_phase = 'claimed', "
            "settlement_claim_token = 'n43-inflight', settlement_claimed_at = now(), "
            "next_retry_at = null "
            "where run_id = 'run-a'"
        )
    async with connect() as conn, conn.transaction():
        blocked = await _retire(conn)
        assert blocked["status"] == "blocked"
        assert blocked["compatibility_counts"]["settlement_count"] == 1

    async with connect() as conn, conn.transaction():
        await conn.execute(
            "update run_attempts set status = 'failed', owner_generation = owner_generation + 1, "
            "terminal_reason = 'run_failed', error_code = 'synthetic_failure', "
            "finished_at = now() where run_id = 'run-a'"
        )
        result = {"status": "failed", "reason": "synthetic retirement fixture"}
        await conn.execute(
            "update run_attempt_settlements set settlement_phase = 'settled', "
            "settlement_claim_token = null, settlement_claimed_at = null, next_retry_at = null, "
            "result_status = 'failed', result_json = %s::jsonb, result_sha256 = %s, settled_at = now() "
            "where run_id = 'run-a'",
            (json.dumps(result), _digest(result)),
        )
    async with connect() as conn, conn.transaction():
        prepared = await prepare_attempt_settlement_retirement(conn)
        assert prepared["status"] == "ready"
        assert prepared["compatibility_counts"]["unretained_legacy_history_count"] == 0
        assert prepared["fenced"] is False

    async with connect() as conn, conn.transaction():
        retired = await _retire(conn)
        assert retired["status"] == "retired"
        assert retired["compatibility_counts"]["unmapped_attempt_count"] == 0
        again = await _retire(conn)
        assert again["status"] == "already_retired"

    async with connect() as conn, conn.transaction():
        reader_prepared = await prepare_attempt_settlement_storage_cutover(
            transaction_factory,
        )
        assert reader_prepared["status"] == "already_active"
    async with connect() as conn, conn.transaction():
        reader_activated = await activate_attempt_settlement_storage(
            conn, operator_confirmations=_READER_CONFIRMATIONS,
        )
        assert reader_activated["status"] == "already_active"
    async with connect() as conn, conn.transaction():
        await conn.execute(schema_migrations.schema_sql())
        status = await attempt_settlement_retirement_status(conn)
        assert status["status"] == "current"
        assert status["retirement_state"] == "retired"
        schema_status = await schema_migrations.schema_status(conn)
        definitions = (
            schema_migrations.MODEL_CRITICAL_CONSTRAINT_DEFINITIONS
            + tuple(
                item for item in schema_migrations.CRITICAL_CONSTRAINT_DEFINITIONS
                if item[:2] != schema_migrations.LEGACY_ATTEMPT_SETTLEMENT_CONSTRAINT
            )
        )
        mismatches = []
        for relation, name, _kind, expected in definitions:
            actual_row = await (
                await conn.execute(
                    "select pg_get_constraintdef(oid, true) as definition from pg_constraint "
                    "where conrelid = to_regclass(%s) and conname = %s",
                    (relation, name),
                )
            ).fetchone()
            actual = str(actual_row["definition"]) if actual_row else "<missing>"
            normalize = lambda value: re.sub(r"^check\(\((.*)\)\)$", r"check(\1)", re.sub(r"\s+", "", value.lower()))
            if normalize(actual) != normalize(expected):
                mismatches.append((name, expected, actual))
        assert not mismatches, mismatches
        assert schema_status["ready"] is True, json.dumps(schema_status, indent=2, default=str)
        assert schema_status["retirement_layout_current"] is True
        columns = await (
            await conn.execute(
                "select attname from pg_attribute where attrelid = 'sandbox_leases'::regclass "
                "and attnum > 0 and not attisdropped"
            )
        ).fetchall()
        names = {row["attname"] for row in columns}
        assert names.isdisjoint(schema_migrations.LEGACY_ATTEMPT_SETTLEMENT_COLUMNS)
        assert {
            "executor_terminal_received_at",
            "executor_heartbeat_at",
            "executor_probe_claim_token",
            "executor_cleanup_status",
        }.issubset(names)
        stored = await read_attempt_settlement(
            conn, tenant_id="tenant-a", run_id="run-a",
            attempt_id=(await (await conn.execute(
                "select id from run_attempts where run_id = 'run-a'"
            )).fetchone())["id"],
        )
        assert stored["mapping_state"] == "mapped"
        assert stored["receipt_json"] == {"status": "succeeded", "answer": "first"}
        attempt_id = stored["attempt_id"]
        assert await writer.record_attempt_settlement_dispatch_outcome(
            conn, tenant_id="tenant-a", run_id="run-a", attempt_id=attempt_id,
            lease_id="lease-a", outcome="accepted",
        ) is True
        reread = await read_attempt_settlement(
            conn, tenant_id="tenant-a", run_id="run-a", attempt_id=attempt_id,
        )
        assert reread["dispatch_state"] == "accepted"

