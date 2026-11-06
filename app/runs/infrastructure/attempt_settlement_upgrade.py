"""Atomic, one-time upgrade from Sandbox settlement columns to Attempt authority.

The normal runtime never imports the legacy mapping adapters. Operators stop old
binaries, drain work and validate recovery before invoking the schema upgrader.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.runs.domain.settlement_retirement import (
    LEGACY_ATTEMPT_SETTLEMENT_COLUMNS,
    LEGACY_ATTEMPT_SETTLEMENT_CONSTRAINT,
    LEGACY_ATTEMPT_SETTLEMENT_INDEXES,
)
from app.runs.infrastructure.attempt_settlement_upgrade_mapping import (
    backfill_attempt_settlement_projections,
    compare_attempt_settlement_projections,
)

UPGRADE_CONFIRMATIONS = (
    "legacy_binaries_stopped",
    "runs_and_resource_claims_drained",
    "backup_restore_drill_complete",
)
_REMOVED_MARKERS = (
    "run_attempt_settlement_storage", "run_attempt_settlement_writer",
    "run_attempt_settlement_retirement",
)


async def upgrade_attempt_settlement_authority(
    conn: Any, *, confirmations: Mapping[str, bool] | None = None,
) -> None:
    """Map and verify all history before dropping the exact old settlement surface."""
    columns = await (await conn.execute(
        """select attname from pg_attribute
           where attrelid=to_regclass('sandbox_leases') and attnum>0 and not attisdropped
             and attname=any(%s::text[])""", (list(LEGACY_ATTEMPT_SETTLEMENT_COLUMNS),),
    )).fetchall()
    if not columns:
        return
    # Supported historical schemas have six original fields, optionally the
    # later retry deadline and immutable intent context. Anything else is partial.
    names = {row["attname"] for row in columns}
    required = set(LEGACY_ATTEMPT_SETTLEMENT_COLUMNS) - {
        "executor_reconciliation_intent_context_json", "executor_reconciliation_retry_not_before",
    }
    if not required.issubset(names):
        raise RuntimeError("attempt_settlement_upgrade_partial_legacy_layout")
    if not confirmations or any(confirmations.get(key) is not True for key in UPGRADE_CONFIRMATIONS):
        raise RuntimeError("attempt_settlement_upgrade_operator_confirmation_required")
    await conn.execute("set local lock_timeout='5s'")
    # This order matches lifecycle writers. ACCESS EXCLUSIVE also fences legacy
    # binaries which do not know the Attempt writer protocol.
    for relation in (
        "runs", "sandbox_leases", "run_attempts", "sandbox_execution_dispatches",
        "run_attempt_settlements", "execution_supervision_domains", "execution_supervision_claims",
        "run_stale_reconciliation_claims", "run_delivery_intents",
    ):
        await conn.execute(f"lock table {relation} in access exclusive mode")
    counts = await (await conn.execute(
        """select
          (select count(*) from runs where status in ('queued','running','cancel_requested')) as open_runs,
          (select count(*) from run_attempts where status not in ('succeeded','failed','cancelled')) as open_attempts,
          (select count(*) from sandbox_leases where status<>'released') as active_leases,
          (select count(*) from sandbox_leases where executor_probe_claim_token is not null
             or executor_probe_claimed_at is not null or executor_cleanup_claim_token is not null
             or executor_cleanup_claimed_at is not null
             or executor_cleanup_status not in ('not_required','finalized')
             or to_jsonb(sandbox_leases)->>'executor_reconciliation_claim_token' is not null) as resource_claims,
          (select count(*) from execution_supervision_claims where recovered_at is null) as supervision_claims,
          (select count(*) from run_stale_reconciliation_claims) as stale_claims,
          (select count(*) from run_delivery_intents where state='claimed' or claim_token is not null) as delivery_claims,
          (select count(*) from sandbox_execution_dispatches where status in ('intent','unknown')) as dispatches,
          (select count(*) from run_attempt_settlements where settlement_claim_token is not null
             or settlement_phase='claimed') as settlement_claims""",
    )).fetchone() or {}
    if any(int(value or 0) for value in counts.values()):
        raise RuntimeError("attempt_settlement_upgrade_requires_drain")
    # Transfer resource-only observations before discarding the old mixed
    # columns. A finalized receipt is not a new cleanup operation.
    await conn.execute(
        """update sandbox_leases set executor_cleanup_status=case
             when executor_terminal_json is null then 'not_required'
             when executor_reconciliation_status='finalized' then 'finalized'
             when executor_reconciliation_status in ('cleanup_pending','failed') then executor_reconciliation_status
             else 'waiting_settlement' end
           where executor_cleanup_status='not_required'"""
    )
    # No writer can change this denominator while the lifecycle tables are fenced.
    after = None
    while True:
        page = await backfill_attempt_settlement_projections(conn, after=after, limit=800)
        if page["unknown_count"] or page["conflict_count"]:
            raise RuntimeError("attempt_settlement_upgrade_history_unmapped")
        after = page["next_after"]
        if after is None:
            break
    after = None
    while True:
        page = await compare_attempt_settlement_projections(conn, after=after, limit=800)
        if any(page[key] for key in ("missing_count", "unknown_count", "conflict_count", "mismatch_count")):
            raise RuntimeError("attempt_settlement_upgrade_history_mismatch")
        after = page["next_after"]
        if after is None:
            break
    unbound = await (await conn.execute(
        """select count(*) as count from sandbox_leases lease
           left join run_attempt_settlements detail on detail.tenant_id=lease.tenant_id
             and detail.run_id=lease.run_id and detail.attempt_id=lease.attempt_id
             and detail.source_lease_id=lease.id
           where (to_jsonb(lease)->>'executor_terminal_json' is not null
               or to_jsonb(lease)->>'executor_reconciliation_context_json' is not null
               or to_jsonb(lease)->>'executor_reconciliation_intent_context_json' is not null)
             and (detail.attempt_id is null or detail.mapping_state<>'mapped')""",
    )).fetchone()
    if unbound and int(unbound["count"]):
        raise RuntimeError("attempt_settlement_upgrade_unbound_history")
    await conn.execute("drop trigger if exists trg_reject_legacy_attempt_settlement_write on sandbox_leases")
    await conn.execute("drop function if exists reject_legacy_attempt_settlement_write()")
    for index in sorted(LEGACY_ATTEMPT_SETTLEMENT_INDEXES):
        await conn.execute(f"drop index if exists {index}")
    await conn.execute(f"alter table sandbox_leases drop constraint if exists {LEGACY_ATTEMPT_SETTLEMENT_CONSTRAINT[1]}")
    for column in LEGACY_ATTEMPT_SETTLEMENT_COLUMNS:
        await conn.execute(f"alter table sandbox_leases drop column if exists {column}")
    await conn.execute("delete from schema_index_migrations where index_name=any(%s::text[])", (list(LEGACY_ATTEMPT_SETTLEMENT_INDEXES),))
    for relation in _REMOVED_MARKERS:
        await conn.execute(f"drop table if exists {relation}")
