"""Storage version cutover orchestration for Attempt settlement projections."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.runs.infrastructure.attempt_settlement_projection_store_postgres import (
    ATTEMPT_SETTLEMENT_STORAGE_VERSIONS,
    DEFAULT_ATTEMPT_SETTLEMENT_PAGE_SIZE,
    _page_limit,
    _require_legacy_settlement_mapping_phase,
    backfill_attempt_settlement_projections,
    compare_attempt_settlement_snapshot,
    get_attempt_settlement_storage_version,
)


def _cutover_ready(summary: Mapping[str, Any]) -> bool:
    return bool(
        summary.get("snapshot_complete") is True
        and int(summary.get("mapped_count") or 0) == int(summary.get("attempt_count") or 0)
        and all(
            int(summary.get(field) or 0) == 0
            for field in ("missing_count", "unknown_count", "conflict_count", "mismatch_count")
        )
    )


async def _unbound_execution_lease_count(conn: Any) -> int:
    """Keep legacy execution evidence outside the Attempt ledger in the denominator."""
    await _require_legacy_settlement_mapping_phase(conn)
    row = await (await conn.execute(
        """
        select count(*)::bigint as count from sandbox_leases lease
        where (lease.executor_terminal_json is not null
               or lease.executor_reconciliation_context_json is not null
               or lease.executor_heartbeat_at is not null
               or lease.executor_status is distinct from 'pending')
          and not exists (
            select 1 from run_attempts attempt
            where attempt.tenant_id = lease.tenant_id
              and attempt.run_id = lease.run_id and attempt.id = lease.attempt_id
          )
        """
    )).fetchone()
    return int(row["count"])



async def prepare_attempt_settlement_storage_cutover(
    transaction_factory: Any,
    *,
    page_limit: int = DEFAULT_ATTEMPT_SETTLEMENT_PAGE_SIZE,
    max_attempts: int = 10000,
) -> dict[str, Any]:
    """Backfill in bounded transactions and report a stable, non-authorizing compare."""
    page_limit = _page_limit(page_limit)
    if isinstance(max_attempts, bool) or not isinstance(max_attempts, int) or max_attempts < 1:
        raise ValueError("attempt_settlement_cutover_budget_invalid")
    after: tuple[str, str, str] | None = None
    backfilled = 0
    pages = 0
    while True:
        async with transaction_factory() as conn:
            version = await get_attempt_settlement_storage_version(conn)
            if version == "attempt.v1":
                return {
                    "status": "already_active",
                    "storage_version": version,
                    "backfilled_attempt_count": backfilled,
                    "page_count": pages,
                }
            page = await backfill_attempt_settlement_projections(
                conn, after=after, limit=page_limit
            )
        pages += 1
        backfilled += int(page["attempt_count"])
        if backfilled > max_attempts:
            return {
                "status": "budget_exceeded",
                "storage_version": "sandbox_lease.v1",
                "backfilled_attempt_count": backfilled,
                "page_count": pages,
                "max_attempts": max_attempts,
            }
        after = page["next_after"]
        if after is None:
            break

    async with transaction_factory() as conn:
        await conn.execute("set transaction isolation level repeatable read")
        comparison = await compare_attempt_settlement_snapshot(
            conn, page_limit=page_limit
        )
        unbound_count = await _unbound_execution_lease_count(conn)
    return {
        "status": "ready" if _cutover_ready(comparison) and unbound_count == 0 else "blocked",
        "storage_version": "sandbox_lease.v1",
        "backfilled_attempt_count": backfilled,
        "page_count": pages,
        "comparison": comparison,
        "unbound_execution_lease_count": unbound_count,
        "activation_authorized": False,
    }


async def activate_attempt_settlement_storage(
    conn: Any,
    *,
    operator_confirmations: Mapping[str, bool] | None = None,
    page_limit: int = DEFAULT_ATTEMPT_SETTLEMENT_PAGE_SIZE,
    max_attempts: int = 10000,
    lock_timeout_seconds: int = 10,
    statement_timeout_seconds: int = 300,
) -> dict[str, Any]:
    """Fence source writers, recompare the entire live denominator, then set the reader."""
    required_confirmations = {
        "run_and_sandbox_writers_drained",
        "all_source_writers_n40_compatible",
        "real_sdk_acceptance_complete",
        "real_sandbox_lifecycle_acceptance_complete",
        "fourteen_lifecycle_scenarios_complete",
    }
    if (
        operator_confirmations is None
        or set(operator_confirmations) != required_confirmations
        or any(operator_confirmations.get(name) is not True for name in required_confirmations)
    ):
        raise RuntimeError("attempt_settlement_operator_confirmations_required")
    page_limit = _page_limit(page_limit)
    if (
        isinstance(max_attempts, bool) or not isinstance(max_attempts, int) or max_attempts < 1
        or isinstance(lock_timeout_seconds, bool) or not isinstance(lock_timeout_seconds, int)
        or not 1 <= lock_timeout_seconds <= 120
        or isinstance(statement_timeout_seconds, bool) or not isinstance(statement_timeout_seconds, int)
        or not 1 <= statement_timeout_seconds <= 1800
    ):
        raise ValueError("attempt_settlement_cutover_budget_invalid")
    await conn.execute("set transaction isolation level repeatable read")
    # SET is a utility statement: the first MVCC snapshot must be taken after
    # the writer fence, including commits we wait for while acquiring it.
    await conn.execute(f"set local lock_timeout = '{lock_timeout_seconds}s'")
    await conn.execute(f"set local statement_timeout = '{statement_timeout_seconds}s'")
    # This order fences every source that contributes to the mapping, including
    # the dispatch receipt. All writers must use the Runs API lock order too.
    for relation in (
        "runs", "sandbox_leases", "run_attempts", "sandbox_execution_dispatches",
        "run_attempt_settlements",
    ):
        await conn.execute(f"lock table {relation} in share row exclusive mode")
    marker = await (
        await conn.execute(
            "select storage_version from run_attempt_settlement_storage "
            "where singleton = true for update"
        )
    ).fetchone()
    current_version = str(marker.get("storage_version") or "") if marker else ""
    if current_version not in ATTEMPT_SETTLEMENT_STORAGE_VERSIONS:
        raise RuntimeError("attempt_settlement_storage_marker_invalid")
    writer_marker = await (
        await conn.execute(
            "select writer_version from run_attempt_settlement_writer where singleton = true"
        )
    ).fetchone()
    retirement_marker = await (
        await conn.execute(
            "select state from run_attempt_settlement_retirement where singleton = true"
        )
    ).fetchone()
    writer_version = str(writer_marker.get("writer_version") or "") if writer_marker else ""
    retirement_state = str(retirement_marker.get("state") or "") if retirement_marker else ""
    if writer_version == "attempt.v1" or retirement_state == "retired":
        return {
            "status": "already_active" if current_version == "attempt.v1" else "blocked",
            "storage_version": current_version,
            "writer_version": writer_version,
            "retirement_state": retirement_state or None,
            "legacy_comparison": "not_applicable_after_writer_activation",
            "activation_authorized": False,
        }
    count_row = await (
        await conn.execute("select count(*)::bigint as attempt_count from run_attempts")
    ).fetchone()
    attempt_count = int(count_row.get("attempt_count") or 0) if count_row else 0
    if attempt_count > max_attempts:
        return {
            "status": "budget_exceeded",
            "storage_version": current_version,
            "attempt_count": attempt_count,
            "max_attempts": max_attempts,
            "activation_authorized": False,
        }
    comparison = await compare_attempt_settlement_snapshot(conn, page_limit=page_limit)
    unbound_count = await _unbound_execution_lease_count(conn)
    ready = (
        _cutover_ready(comparison) and attempt_count == int(comparison["attempt_count"])
        and unbound_count == 0
    )
    if not ready:
        return {
            "status": "blocked",
            "storage_version": current_version,
            "attempt_count": attempt_count,
            "comparison": comparison,
            "unbound_execution_lease_count": unbound_count,
            "activation_authorized": False,
        }
    if current_version == "attempt.v1":
        return {
            "status": "already_active",
            "storage_version": current_version,
            "attempt_count": attempt_count,
            "comparison": comparison,
            "activation_authorized": True,
        }
    await conn.execute(
        "update run_attempt_settlement_storage "
        "set storage_version = 'attempt.v1', updated_at = clock_timestamp() "
        "where singleton = true and storage_version = 'sandbox_lease.v1'"
    )
    return {
        "status": "activated",
        "storage_version": "attempt.v1",
        "attempt_count": attempt_count,
        "comparison": comparison,
        "activation_authorized": True,
        "operator_acceptance_verified_by_code": False,
    }
