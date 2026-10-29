"""Explicit activation of the Runs-owned Attempt settlement writer."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.runs.infrastructure.attempt_settlement_projection_postgres import (
    DEFAULT_ATTEMPT_SETTLEMENT_PAGE_SIZE,
    ATTEMPT_SETTLEMENT_WRITER_VERSIONS,
    _cutover_ready,
    _page_limit,
    _unbound_execution_lease_count,
    compare_attempt_settlement_snapshot,
    get_attempt_settlement_storage_version,
    prepare_attempt_settlement_storage_cutover,
)
from app.runs.infrastructure.attempt_settlement_writer_postgres import (
    get_attempt_settlement_writer_version,
)


async def prepare_attempt_settlement_writer_cutover(
    transaction_factory: Any,
    *,
    page_limit: int = DEFAULT_ATTEMPT_SETTLEMENT_PAGE_SIZE,
    max_attempts: int = 10000,
) -> dict[str, Any]:
    """Backfill and compare the current writer target without changing authority."""
    async with transaction_factory() as conn:
        writer_version = await get_attempt_settlement_writer_version(conn)
        reader_version = await get_attempt_settlement_storage_version(conn)
    if writer_version == "attempt.v1":
        return {
            "status": "already_active",
            "writer_version": writer_version,
            "reader_version": reader_version,
            "legacy_comparison": "not_applicable_after_writer_activation",
            "activation_authorized": False,
        }
    reader_result = await prepare_attempt_settlement_storage_cutover(
        transaction_factory, page_limit=page_limit, max_attempts=max_attempts,
    )
    async with transaction_factory() as conn:
        await conn.execute("set transaction isolation level repeatable read")
        writer_version = await get_attempt_settlement_writer_version(conn)
        reader_version = await get_attempt_settlement_storage_version(conn)
        comparison = await compare_attempt_settlement_snapshot(conn, page_limit=page_limit)
        unbound_count = await _unbound_execution_lease_count(conn)
    ready = (
        reader_result.get("status") in {"ready", "already_active"}
        and reader_version == "attempt.v1"
        and writer_version == "sandbox_lease.v1"
        and _cutover_ready(comparison)
        and unbound_count == 0
    )
    return {
        "status": "ready" if ready else "blocked",
        "writer_version": writer_version,
        "reader_version": reader_version,
        "comparison": comparison,
        "unbound_execution_lease_count": unbound_count,
        "reader_preparation": reader_result,
        "activation_authorized": False,
    }


async def _inflight_settlement_claim_counts(conn: Any) -> dict[str, int]:
    detail = await (await conn.execute(
        """
        select count(*)::bigint as count
        from run_attempt_settlements
        where settlement_phase = 'claimed'
        """
    )).fetchone()
    legacy = await (await conn.execute(
        """
        select count(*)::bigint as count
        from sandbox_leases
        where executor_reconciliation_status = 'claimed'
           or executor_reconciliation_claim_token is not null
           or executor_probe_claim_token is not null
           or executor_cleanup_claim_token is not null
           or executor_cleanup_status = 'claimed'
        """
    )).fetchone()
    run_claim = await (await conn.execute(
        """
        select count(*)::bigint as count
        from run_stale_reconciliation_claims
        where claim_deadline_at > clock_timestamp()
        """
    )).fetchone()
    return {
        "attempt_settlement_claim_count": int(detail.get("count") or 0) if detail else 0,
        "legacy_settlement_or_cleanup_claim_count": int(legacy.get("count") or 0) if legacy else 0,
        "active_run_reconciliation_claim_count": int(run_claim.get("count") or 0) if run_claim else 0,
    }


async def activate_attempt_settlement_writer(
    conn: Any,
    *,
    operator_confirmations: Mapping[str, bool] | None = None,
    page_limit: int = DEFAULT_ATTEMPT_SETTLEMENT_PAGE_SIZE,
    max_attempts: int = 10000,
    lock_timeout_seconds: int = 10,
    statement_timeout_seconds: int = 300,
) -> dict[str, Any]:
    """Stop source-column writes after an explicit fleet drain and fresh compare."""
    required_confirmations = {
        "legacy_writers_drained",
        "inflight_settlement_and_cleanup_operations_drained",
        "real_sdk_acceptance_complete",
        "real_sandbox_lifecycle_acceptance_complete",
        "fourteen_lifecycle_scenarios_complete",
    }
    if (
        operator_confirmations is None
        or set(operator_confirmations) != required_confirmations
        or any(operator_confirmations.get(name) is not True for name in required_confirmations)
    ):
        raise RuntimeError("attempt_settlement_writer_operator_confirmations_required")
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
    await conn.execute(f"set local lock_timeout = '{lock_timeout_seconds}s'")
    await conn.execute(f"set local statement_timeout = '{statement_timeout_seconds}s'")
    # Match the writer lock order so every pre-cutover transaction completes
    # its source projection before this marker can become authoritative.
    for relation in (
        "runs", "sandbox_leases", "run_attempts", "sandbox_execution_dispatches",
        "run_attempt_settlements", "run_attempt_settlement_storage",
        "run_attempt_settlement_writer", "run_stale_reconciliation_claims",
    ):
        await conn.execute(f"lock table {relation} in share row exclusive mode")
    reader = await (
        await conn.execute(
            "select storage_version from run_attempt_settlement_storage "
            "where singleton = true for update"
        )
    ).fetchone()
    reader_version = str(reader.get("storage_version") or "") if reader else ""
    if reader_version != "attempt.v1":
        return {
            "status": "blocked",
            "writer_version": await get_attempt_settlement_writer_version(conn),
            "reader_version": reader_version,
            "reason": "attempt_settlement_reader_not_active",
            "activation_authorized": False,
        }
    marker = await (
        await conn.execute(
            "select writer_version from run_attempt_settlement_writer "
            "where singleton = true for update"
        )
    ).fetchone()
    current = str(marker.get("writer_version") or "") if marker else ""
    if current not in ATTEMPT_SETTLEMENT_WRITER_VERSIONS:
        raise RuntimeError("attempt_settlement_writer_marker_invalid")
    if current == "attempt.v1":
        return {
            "status": "already_active",
            "writer_version": current,
            "reader_version": reader_version,
            "legacy_comparison": "not_applicable_after_writer_activation",
            "activation_authorized": False,
        }
    count_row = await (
        await conn.execute("select count(*)::bigint as attempt_count from run_attempts")
    ).fetchone()
    attempt_count = int(count_row.get("attempt_count") or 0) if count_row else 0
    if attempt_count > max_attempts:
        return {
            "status": "budget_exceeded", "writer_version": current,
            "reader_version": reader_version, "attempt_count": attempt_count,
            "max_attempts": max_attempts, "activation_authorized": False,
        }
    claim_counts = await _inflight_settlement_claim_counts(conn)
    comparison = await compare_attempt_settlement_snapshot(conn, page_limit=page_limit)
    unbound_count = await _unbound_execution_lease_count(conn)
    ready = (
        _cutover_ready(comparison)
        and attempt_count == int(comparison.get("attempt_count") or 0)
        and unbound_count == 0
        and all(count == 0 for count in claim_counts.values())
    )
    if not ready:
        return {
            "status": "blocked", "writer_version": current,
            "reader_version": reader_version, "attempt_count": attempt_count,
            "comparison": comparison,
            "unbound_execution_lease_count": unbound_count,
            "inflight_claim_counts": claim_counts,
            "activation_authorized": False,
        }
    await conn.execute(
        "update run_attempt_settlement_writer "
        "set writer_version = 'attempt.v1', updated_at = clock_timestamp() "
        "where singleton = true and writer_version = 'sandbox_lease.v1'"
    )
    return {
        "status": "activated", "writer_version": "attempt.v1",
        "reader_version": reader_version, "attempt_count": attempt_count,
        "comparison": comparison, "inflight_claim_counts": claim_counts,
        "activation_authorized": True,
        "operator_acceptance_verified_by_code": False,
    }
