"""Runs-owned Attempt settlement writer operations."""

from __future__ import annotations

from collections.abc import Mapping
import json
from typing import Any

from app.runs.domain.attempt_settlement_projection import (
    ATTEMPT_SETTLEMENT_SCHEMA_VERSION,
    AttemptSettlementProjection,
    AttemptSettlementProjectionConflict,
    CleanupObservation,
)
from app.runs.infrastructure.attempt_settlement_projection_postgres import (
    ATTEMPT_SETTLEMENT_WRITER_VERSIONS,
    _ATTEMPT_FIELDS,
    _RUN_FIELDS,
    _canonical_digest,
    _mapping,
    _settlement_from_row,
    list_due_attempt_settlements,
    lock_attempt_settlement_projection_sources,
    persist_attempt_settlement_projection,
)


# Resource identity and cleanup data remain Sandbox-owned. The settlement writer
# intentionally does not select the retired reconciliation columns from leases.
_ACTIVE_RESOURCE_LEASE_FIELDS = (
    "id", "tenant_id", "workspace_id", "user_id", "session_id", "run_id",
    "attempt_id", "trace_id", "sandbox_mode", "provider", "status",
    "browser_enabled", "lease_payload_json", "runtime_container_id",
    "runtime_container_name", "runtime_executor_url",
    "runtime_workspace_container_path", "runtime_handle_verified_at",
    "executor_status", "executor_heartbeat_at", "executor_probe_claim_token",
    "executor_probe_claimed_at", "executor_cleanup_status",
    "executor_cleanup_claim_token", "executor_cleanup_claimed_at",
    "executor_cleanup_retry_not_before", "heartbeat_at", "expires_at",
    "released_at", "release_reason", "created_at", "updated_at",
)


async def get_attempt_settlement_writer_version(conn: Any) -> str:
    row = await (
        await conn.execute(
            "select writer_version from run_attempt_settlement_writer where singleton = true"
        )
    ).fetchone()
    version = str(row.get("writer_version") or "") if row else ""
    if version not in ATTEMPT_SETTLEMENT_WRITER_VERSIONS:
        raise RuntimeError("attempt_settlement_writer_marker_invalid")
    return version


async def _current_owned_detail(
    conn: Any,
    *,
    tenant_id: str,
    run_id: str,
    attempt_id: str,
    source_lease_id: str | None = None,
    create: bool = False,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Load Run, resource and Attempt in the lifecycle lock order, then detail."""
    await lock_attempt_settlement_projection_sources(
        conn, tenant_id=tenant_id, run_id=run_id, attempt_id=attempt_id
    )
    attempt = await (
        await conn.execute(
            f"""
            select {", ".join(_ATTEMPT_FIELDS)} from run_attempts
            where tenant_id = %s and run_id = %s and id = %s
            """,
            (tenant_id, run_id, attempt_id),
        )
    ).fetchone()
    run = await (
        await conn.execute(
            f"""
            select {", ".join(_RUN_FIELDS)} from runs
            where tenant_id = %s and id = %s
            """,
            (tenant_id, run_id),
        )
    ).fetchone()
    leases = await (
        await conn.execute(
            f"""
            select {", ".join(_ACTIVE_RESOURCE_LEASE_FIELDS)}
            from sandbox_leases
            where tenant_id = %s and run_id = %s and attempt_id = %s
            order by id asc
            """,
            (tenant_id, run_id, attempt_id),
        )
    ).fetchall()
    if attempt is None or run is None:
        raise AttemptSettlementProjectionConflict("attempt_settlement_source_missing")
    attempt, run, leases = dict(attempt), dict(run), [dict(row) for row in leases]
    lease: dict[str, Any] | None = None
    if source_lease_id is not None:
        matches = [row for row in leases if str(row.get("id") or "") == source_lease_id]
        if len(matches) != 1:
            raise AttemptSettlementProjectionConflict("attempt_settlement_source_lease_mismatch")
        lease = matches[0]
    elif len(leases) == 1:
        lease = leases[0]
    elif len(leases) > 1:
        raise AttemptSettlementProjectionConflict("attempt_settlement_source_lease_conflict")

    row = await (
        await conn.execute(
            """
            select * from run_attempt_settlements
            where tenant_id = %s and run_id = %s and attempt_id = %s
            for update
            """,
            (tenant_id, run_id, attempt_id),
        )
    ).fetchone()
    if row is None:
        if not create:
            raise AttemptSettlementProjectionConflict("attempt_settlement_projection_missing")
        generation = attempt.get("owner_generation")
        if type(generation) is not int or generation < 1:
            raise AttemptSettlementProjectionConflict("attempt_settlement_owner_generation_invalid")
        values = {
            "schema_version": ATTEMPT_SETTLEMENT_SCHEMA_VERSION,
            "tenant_id": tenant_id,
            "run_id": run_id,
            "attempt_id": attempt_id,
            "mapping_state": "mapped",
            "source_lease_id": None,
            "receipt_identity": None,
            "receipt_json": None,
            "receipt_sha256": None,
            "receipt_owner_generation": None,
            "dispatch_state": "unknown",
            "intent_recovery_context_json": None,
            "accepted_context_json": None,
            "attempt_status_snapshot": str(attempt.get("status") or "created"),
            "attempt_owner_generation_snapshot": generation,
            "run_status_snapshot": str(run.get("status") or "queued"),
            "cancel_requested_at_snapshot": run.get("cancel_requested_at"),
            "settlement_phase": "awaiting_receipt",
            "settlement_claim_token": None,
            "settlement_claimed_at": None,
            "next_retry_at": None,
            "settlement_attempt_count": 0,
            "result_status": None,
            "result_json": None,
            "result_sha256": None,
            "settlement_error_code": None,
            "settled_at": None,
        }
        await persist_attempt_settlement_projection(
            conn, AttemptSettlementProjection(values, CleanupObservation())
        )
        row = values
    detail = dict(row)
    if source_lease_id is not None:
        existing_source = str(detail.get("source_lease_id") or "")
        if existing_source and existing_source != source_lease_id:
            raise AttemptSettlementProjectionConflict("attempt_settlement_source_lease_conflict")
        if not existing_source:
            detail["source_lease_id"] = source_lease_id
    return attempt, run, dict(lease) if lease is not None else {}, detail


async def initialize_attempt_settlement_detail(
    conn: Any,
    *,
    tenant_id: str,
    run_id: str,
    attempt_id: str,
    source_lease_id: str | None = None,
) -> bool:
    """Initialize the Runs detail in the transaction that creates an Attempt/lease."""
    _attempt, _run, lease, detail = await _current_owned_detail(
        conn, tenant_id=tenant_id, run_id=run_id, attempt_id=attempt_id,
        source_lease_id=source_lease_id, create=True,
    )
    values = dict(detail)
    if source_lease_id is not None:
        values["source_lease_id"] = source_lease_id
    elif lease:
        values["source_lease_id"] = str(lease["id"])
    await persist_attempt_settlement_projection(conn, _settlement_from_row(values))
    return True


async def refresh_attempt_settlement_snapshot(
    conn: Any,
    *,
    tenant_id: str,
    run_id: str,
    attempt_id: str,
) -> bool:
    """Update only Runs/Attempt snapshots; never refresh from legacy lease facts."""
    attempt, run, _lease, detail = await _current_owned_detail(
        conn, tenant_id=tenant_id, run_id=run_id, attempt_id=attempt_id,
    )
    values = dict(detail)
    generation = attempt.get("owner_generation")
    if type(generation) is not int or generation < 1:
        raise AttemptSettlementProjectionConflict("attempt_settlement_owner_generation_invalid")
    values.update(
        mapping_state="mapped",
        attempt_status_snapshot=str(attempt.get("status") or ""),
        attempt_owner_generation_snapshot=generation,
        run_status_snapshot=str(run.get("status") or ""),
        cancel_requested_at_snapshot=run.get("cancel_requested_at"),
    )
    attempt_status = values["attempt_status_snapshot"]
    run_status = values["run_status_snapshot"]
    allowed_run_status = {
        "succeeded": {"succeeded"},
        "failed": {"failed", "dead-letter", "dead_letter", "dead-lettered"},
        "cancelled": {"cancelled", "canceled"},
    }
    if run_status in allowed_run_status.get(attempt_status, set()):
        result = _mapping(run.get("result_json"))
        finished_at = run.get("finished_at") or attempt.get("finished_at")
        if result is not None and finished_at is not None:
            values.update(
                settlement_phase="settled",
                settlement_claim_token=None,
                settlement_claimed_at=None,
                next_retry_at=None,
                result_status=run_status,
                result_json=result,
                result_sha256=_canonical_digest(result),
                settlement_error_code=(run.get("error_code") or attempt.get("error_code")),
                settled_at=finished_at,
            )
    await persist_attempt_settlement_projection(
        conn, _settlement_from_row(values)
    )
    return True


async def _record_owned_context(
    conn: Any,
    *,
    tenant_id: str,
    run_id: str,
    attempt_id: str,
    lease_id: str,
    field: str,
    context: Mapping[str, Any],
) -> bool:
    _attempt, _run, _lease, detail = await _current_owned_detail(
        conn, tenant_id=tenant_id, run_id=run_id, attempt_id=attempt_id,
        source_lease_id=lease_id,
    )
    value = dict(context)
    existing = detail.get(field)
    if existing is not None and existing != value:
        raise AttemptSettlementProjectionConflict(f"attempt_settlement_{field}_conflict")
    values = dict(detail)
    values[field] = value
    values["mapping_state"] = "mapped"
    if field == "accepted_context_json":
        values["dispatch_state"] = "accepted"
        if values.get("receipt_json") is not None and values.get("settlement_phase") in {
            "awaiting_context", "awaiting_receipt", "unknown",
        }:
            values["settlement_phase"] = "pending"
    elif values.get("receipt_json") is not None and values.get("accepted_context_json") is None:
        values["dispatch_state"] = "receipt_before_acceptance"
        if values.get("settlement_phase") == "awaiting_receipt":
            values["settlement_phase"] = "awaiting_context"
    await persist_attempt_settlement_projection(conn, _settlement_from_row(values))
    return True


async def record_attempt_settlement_intent(
    conn: Any,
    *,
    tenant_id: str,
    run_id: str,
    attempt_id: str,
    lease_id: str,
    intent_context: Mapping[str, Any],
) -> bool:
    return await _record_owned_context(
        conn, tenant_id=tenant_id, run_id=run_id, attempt_id=attempt_id,
        lease_id=lease_id, field="intent_recovery_context_json", context=intent_context,
    )


async def record_attempt_settlement_accepted_context(
    conn: Any,
    *,
    tenant_id: str,
    run_id: str,
    attempt_id: str,
    lease_id: str,
    accepted_context: Mapping[str, Any],
) -> bool:
    return await _record_owned_context(
        conn, tenant_id=tenant_id, run_id=run_id, attempt_id=attempt_id,
        lease_id=lease_id, field="accepted_context_json", context=accepted_context,
    )


async def record_attempt_settlement_terminal_receipt(
    conn: Any,
    *,
    tenant_id: str,
    run_id: str,
    attempt_id: str,
    lease_id: str,
    receipt: Mapping[str, Any],
    receipt_owner_generation: int,
) -> bool:
    _attempt, _run, lease, detail = await _current_owned_detail(
        conn, tenant_id=tenant_id, run_id=run_id, attempt_id=attempt_id,
        source_lease_id=lease_id,
    )
    immutable_receipt = {
        key: value for key, value in receipt.items() if key != "diagnostics"
    }
    if not immutable_receipt or str(immutable_receipt.get("run_id") or "") != run_id:
        raise ValueError("sandbox_executor_terminal_run_id_invalid")
    if type(receipt_owner_generation) is not int or receipt_owner_generation < 1:
        raise ValueError("sandbox_executor_terminal_owner_generation_invalid")
    lease_payload = _mapping(lease.get("lease_payload_json"))
    source_generation = None if lease_payload is None else lease_payload.get("owner_generation")
    if str(source_generation or "") != str(receipt_owner_generation):
        raise AttemptSettlementProjectionConflict("attempt_settlement_receipt_generation_mismatch")
    receipt_identity = f"sandbox_terminal:{lease_id}:{attempt_id}"
    receipt_sha256 = _canonical_digest(immutable_receipt)
    existing = _mapping(detail.get("receipt_json"))
    existing_immutable = (
        {key: value for key, value in existing.items() if key != "diagnostics"}
        if existing is not None else None
    )
    if existing is not None:
        if (
            detail.get("receipt_identity") == receipt_identity
            and detail.get("receipt_sha256") == receipt_sha256
            and detail.get("receipt_owner_generation") == receipt_owner_generation
            and existing_immutable == immutable_receipt
        ):
            return False
        raise AttemptSettlementProjectionConflict("sandbox_executor_terminal_conflict")
    values = dict(detail)
    values.update(
        mapping_state="mapped",
        source_lease_id=lease_id,
        receipt_identity=receipt_identity,
        receipt_json=immutable_receipt,
        receipt_sha256=receipt_sha256,
        receipt_owner_generation=receipt_owner_generation,
        dispatch_state=(
            "accepted" if values.get("accepted_context_json") is not None
            else "receipt_before_acceptance"
        ),
        settlement_phase=(
            "pending" if values.get("accepted_context_json") is not None
            else "awaiting_context"
        ),
        settlement_claim_token=None,
        settlement_claimed_at=None,
        next_retry_at=None,
    )
    await persist_attempt_settlement_projection(conn, _settlement_from_row(values))
    await conn.execute(
        """
        update run_attempt_settlements
        set receipt_recorded_at = coalesce(receipt_recorded_at, clock_timestamp())
        where tenant_id = %s and run_id = %s and attempt_id = %s
        """,
        (tenant_id, run_id, attempt_id),
    )
    return True


async def record_attempt_settlement_dispatch_outcome(
    conn: Any,
    *,
    tenant_id: str,
    run_id: str,
    attempt_id: str,
    lease_id: str,
    outcome: str,
) -> bool:
    if outcome not in {"accepted", "rejected", "unknown"}:
        raise ValueError("sandbox_dispatch_outcome_invalid")
    _attempt, _run, _lease, detail = await _current_owned_detail(
        conn, tenant_id=tenant_id, run_id=run_id, attempt_id=attempt_id,
        source_lease_id=lease_id,
    )
    values = dict(detail)
    if values.get("accepted_context_json") is not None:
        values["dispatch_state"] = "accepted"
    elif values.get("receipt_json") is not None:
        values["dispatch_state"] = "receipt_before_acceptance"
    else:
        values["dispatch_state"] = "unknown"
    values["mapping_state"] = "mapped"
    await persist_attempt_settlement_projection(conn, _settlement_from_row(values))
    return True


def _settlement_phase_legacy_alias(phase: str) -> str:
    return {
        "awaiting_receipt": "waiting_terminal",
        "awaiting_context": "waiting_terminal",
        "pending": "pending",
        "claimed": "claimed",
        "retry": "retry",
        "settled": "finalized",
        "failed": "failed",
        "unknown": "unknown",
    }.get(phase, "unknown")


async def claim_due_attempt_settlements(
    conn: Any,
    *,
    claim_token: str,
    limit: int,
    stale_after_seconds: int,
) -> list[dict[str, Any]]:
    candidates = await list_due_attempt_settlements(
        conn, limit=limit, stale_after_seconds=stale_after_seconds
    )
    claimed: list[dict[str, Any]] = []
    for candidate in candidates:
        scope = {
            "tenant_id": str(candidate["tenant_id"]),
            "run_id": str(candidate["run_id"]),
            "attempt_id": str(candidate["attempt_id"]),
        }
        _attempt, _run, lease, detail = await _current_owned_detail(
            conn, **scope, source_lease_id=str(candidate.get("source_lease_id") or ""),
        )
        if not lease:
            raise AttemptSettlementProjectionConflict("attempt_settlement_source_lease_missing")
        if lease.get("status") == "quarantined":
            continue
        phase = str(detail.get("settlement_phase") or "")
        claimable = phase == "pending" or (
            phase == "retry" and detail.get("next_retry_at") is not None
        ) or (
            phase == "claimed" and detail.get("settlement_claimed_at") is not None
        )
        if (
            detail.get("mapping_state") != "mapped"
            or detail.get("receipt_json") is None
            or detail.get("accepted_context_json") is None
            or not claimable
        ):
            continue
        updated = await (
            await conn.execute(
                """
                update run_attempt_settlements
                set settlement_phase = 'claimed', settlement_claim_token = %s,
                    settlement_claimed_at = now(), next_retry_at = null,
                    settlement_attempt_count = settlement_attempt_count + 1,
                    updated_at = now()
                where tenant_id = %s and run_id = %s and attempt_id = %s
                  and source_lease_id = %s
                  and receipt_json is not null and accepted_context_json is not null
                  and mapping_state = 'mapped'
                  and (
                    settlement_phase = 'pending'
                    or (settlement_phase = 'retry' and next_retry_at <= now())
                    or (settlement_phase = 'claimed'
                        and settlement_claimed_at < now() - make_interval(secs => %s))
                  )
                returning *
                """,
                (claim_token, scope["tenant_id"], scope["run_id"],
                 scope["attempt_id"], str(candidate["source_lease_id"]),
                 stale_after_seconds),
            )
        ).fetchone()
        if updated is None:
            continue
        result = {field: lease.get(field) for field in _ACTIVE_RESOURCE_LEASE_FIELDS}
        detail_row = dict(updated)
        result.update(
            settlement_storage_version="attempt.v1",
            settlement_mapping_state=detail_row.get("mapping_state"),
            settlement_phase="claimed",
            settlement_dispatch_state=detail_row.get("dispatch_state"),
            settlement_receipt_present=True,
            executor_terminal_json=detail_row.get("receipt_json"),
            executor_reconciliation_context_json=detail_row.get("accepted_context_json"),
            executor_reconciliation_intent_context_json=detail_row.get("intent_recovery_context_json"),
            executor_reconciliation_status="claimed",
            executor_reconciliation_claim_token=detail_row.get("settlement_claim_token"),
            executor_reconciliation_claimed_at=detail_row.get("settlement_claimed_at"),
            executor_reconciliation_retry_not_before=detail_row.get("next_retry_at"),
            executor_terminal_reconciliation_attempt_count=detail_row.get("settlement_attempt_count"),
            executor_terminal_received_at=detail_row.get("updated_at"),
            executor_reconciliation_attempt_count=detail_row.get("settlement_attempt_count"),
            executor_reconciliation_error=detail_row.get("settlement_error_code") or "",
            executor_reconciled_at=detail_row.get("settled_at"),
        )
        claimed.append(result)
    return claimed


async def is_attempt_settlement_claim_current(
    conn: Any,
    *,
    tenant_id: str,
    run_id: str,
    attempt_id: str,
    lease_id: str,
    claim_token: str,
) -> bool:
    row = await (
        await conn.execute(
            """
            select 1 from run_attempt_settlements
            where tenant_id = %s and run_id = %s and attempt_id = %s
              and source_lease_id = %s and settlement_phase = 'claimed'
              and settlement_claim_token = %s
            """,
            (tenant_id, run_id, attempt_id, lease_id, claim_token),
        )
    ).fetchone()
    return row is not None


async def retry_attempt_settlement_claim(
    conn: Any,
    *,
    tenant_id: str,
    run_id: str,
    attempt_id: str,
    lease_id: str,
    claim_token: str,
) -> bool:
    _attempt, _run, _lease, _detail = await _current_owned_detail(
        conn, tenant_id=tenant_id, run_id=run_id, attempt_id=attempt_id,
        source_lease_id=lease_id,
    )
    cursor = await conn.execute(
        """
        update run_attempt_settlements
        set settlement_phase = 'retry',
            next_retry_at = now() + make_interval(
              secs => least(30, greatest(1, settlement_attempt_count))
            ),
            settlement_claim_token = null, settlement_claimed_at = null,
            updated_at = now()
        where tenant_id = %s and run_id = %s and attempt_id = %s
          and source_lease_id = %s and settlement_phase = 'claimed'
          and settlement_claim_token = %s
        returning attempt_id
        """,
        (tenant_id, run_id, attempt_id, lease_id, claim_token),
    )
    return await cursor.fetchone() is not None


async def append_attempt_settlement_diagnostics(
    conn: Any,
    *,
    tenant_id: str,
    run_id: str,
    attempt_id: str,
    lease_id: str,
    claim_token: str,
    diagnostics: list[str],
) -> bool:
    if not isinstance(diagnostics, list) or any(not isinstance(item, str) for item in diagnostics):
        raise ValueError("attempt_settlement_diagnostics_invalid")
    _attempt, _run, _lease, _detail = await _current_owned_detail(
        conn, tenant_id=tenant_id, run_id=run_id, attempt_id=attempt_id,
        source_lease_id=lease_id,
    )
    cursor = await conn.execute(
        """
        update run_attempt_settlements
        set receipt_json = jsonb_set(receipt_json, '{diagnostics}', %s::jsonb, true),
            updated_at = now()
        where tenant_id = %s and run_id = %s and attempt_id = %s
          and source_lease_id = %s and receipt_json is not null
          and settlement_phase = 'claimed' and settlement_claim_token = %s
        returning attempt_id
        """,
        (json.dumps(diagnostics, ensure_ascii=False), tenant_id, run_id,
         attempt_id, lease_id, claim_token),
    )
    return await cursor.fetchone() is not None


async def get_attempt_settlement_reconciliation_summary(
    conn: Any, *, tenant_id: str, slo_seconds: int,
) -> dict[str, Any]:
    """Aggregate Runs-owned receipt progress without reading legacy settlement columns."""
    cursor = await conn.execute(
        """
        select
          count(*) filter (
            where lease.status in ('active', 'released')
              and detail.receipt_json is not null
              and detail.settlement_phase in ('pending', 'claimed', 'retry')
          ) as pending_receipt_count,
          count(*) filter (
            where lease.status = 'released'
              and detail.receipt_json is not null
              and detail.settlement_phase in ('pending', 'claimed', 'retry')
          ) as released_pending_receipt_count,
          count(*) filter (
            where lease.status in ('active', 'released')
              and detail.receipt_json is not null
              and detail.settlement_phase = 'retry'
          ) as retry_receipt_count,
          coalesce(sum(greatest(detail.settlement_attempt_count - 1, 0)) filter (
            where detail.receipt_json is not null
          ), 0)::bigint as retry_attempt_count,
          max(detail.settlement_attempt_count) filter (
            where detail.receipt_json is not null
          ) as max_attempt_count,
          floor(max(extract(epoch from (now() - coalesce(
            detail.receipt_recorded_at, detail.updated_at
          )))) filter (
            where lease.status in ('active', 'released')
              and detail.receipt_json is not null
              and detail.settlement_phase in ('pending', 'claimed', 'retry')
          ))::bigint as oldest_pending_receipt_age_seconds,
          count(*) filter (
            where lease.status in ('active', 'released')
              and detail.receipt_json is not null
              and detail.settlement_phase in ('pending', 'claimed', 'retry')
              and coalesce(detail.receipt_recorded_at, detail.updated_at)
                < now() - make_interval(secs => %s)
          ) as terminalization_slo_breach_count
        from run_attempt_settlements detail
        join sandbox_leases lease on lease.tenant_id = detail.tenant_id
          and lease.run_id = detail.run_id and lease.attempt_id = detail.attempt_id
          and lease.id = detail.source_lease_id
        where detail.tenant_id = %s
        """,
        (slo_seconds, tenant_id),
    )
    row = await cursor.fetchone() or {}
    age = row.get("oldest_pending_receipt_age_seconds")
    return {
        "pending_receipt_count": int(row.get("pending_receipt_count") or 0),
        "released_pending_receipt_count": int(row.get("released_pending_receipt_count") or 0),
        "retry_receipt_count": int(row.get("retry_receipt_count") or 0),
        "retry_attempt_count": int(row.get("retry_attempt_count") or 0),
        "max_attempt_count": int(row.get("max_attempt_count") or 0),
        "oldest_pending_receipt_age_seconds": int(age) if age is not None else None,
        "terminalization_slo_seconds": int(slo_seconds),
        "terminalization_slo_breach_count": int(row.get("terminalization_slo_breach_count") or 0),
    }

