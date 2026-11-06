"""One-time mapping of pre-Attempt settlement data during schema upgrade.

Never imported by the running API, Worker or reconciler.
"""
from __future__ import annotations
import json
from typing import Any
from app.runs.infrastructure.attempt_settlement_upgrade_projection import project_legacy_attempt_settlement
from app.runs.infrastructure.attempt_settlement_projection_postgres import (
    DEFAULT_ATTEMPT_SETTLEMENT_PAGE_SIZE, _page_limit, _RUN_FIELDS, _ATTEMPT_FIELDS,
    persist_attempt_settlement_projection, ATTEMPT_SETTLEMENT_PROJECTION_FIELDS,
)

_SOURCE_LEASE_FIELDS = (
    "id",
    "tenant_id",
    "run_id",
    "attempt_id",
    "status",
    "lease_payload_json",
    "executor_status",
    "executor_heartbeat_at",
    "executor_terminal_json",
    "executor_terminal_received_at",
    "executor_reconciliation_context_json",
    "executor_reconciliation_intent_context_json",
    "executor_reconciliation_status",
    "executor_reconciliation_claim_token",
    "executor_reconciliation_claimed_at",
    "executor_reconciliation_retry_not_before",
    "executor_terminal_reconciliation_attempt_count",
    "released_at",
    "release_reason",
    "executor_reconciled_at",
    "updated_at",
)


def _after_tuple(after: tuple[str, str, str] | None) -> tuple[str | None, str | None, str | None]:
    if after is None:
        return None, None, None
    if (
        not isinstance(after, tuple)
        or len(after) != 3
        or any(not isinstance(value, str) or not value for value in after)
    ):
        raise ValueError("attempt_settlement_projection_cursor_invalid")
    return after


async def _page_attempt_keys(
    conn: Any,
    *,
    tenant_id: str | None,
    after: tuple[str, str, str] | None,
    limit: int,
) -> list[tuple[str, str, str]]:
    limit = _page_limit(limit)
    after_values = _after_tuple(after)
    cursor = await conn.execute(
        """
        select tenant_id, run_id, id as attempt_id
        from run_attempts
        where (%s::text is null or tenant_id = %s)
          and (
            %s::text is null
            or (tenant_id, run_id, id) > (%s::text, %s::text, %s::text)
          )
        order by tenant_id asc, run_id asc, id asc
        limit %s
        """,
        (tenant_id, tenant_id, after_values[0], *after_values, limit),
    )
    return [
        (str(row["tenant_id"]), str(row["run_id"]), str(row["attempt_id"]))
        for row in await cursor.fetchall()
    ]


async def _load_locked_attempt_facts(
    conn: Any,
    *,
    key: tuple[str, str, str],
    lock: bool,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, list[dict[str, Any]]]:
    tenant_id, run_id, attempt_id = key
    suffix = " for update" if lock else ""

    # Keep the shared lifecycle lock order: Run, all exact Sandbox receipts by
    # lease id, then the original Attempt. The settlement row follows these locks.
    run = await (
        await conn.execute(
            f"""
            select {", ".join(_RUN_FIELDS)}
            from runs
            where tenant_id = %s and id = %s{suffix}
            """,
            (tenant_id, run_id),
        )
    ).fetchone()
    lease_suffix = " for update of lease" if lock else ""
    leases = await (
        await conn.execute(
            f"""
            select {", ".join(
                f"to_jsonb(lease)->'{field}' as {field}" if field in {"executor_terminal_json", "executor_reconciliation_context_json", "executor_reconciliation_intent_context_json"}
                else f"(to_jsonb(lease)->>'{field}')::integer as {field}" if field == "executor_terminal_reconciliation_attempt_count"
                else f"to_jsonb(lease)->>'{field}' as {field}" if field in {"executor_reconciliation_status", "executor_reconciliation_claim_token"}
                else f"(to_jsonb(lease)->>'{field}')::timestamptz as {field}" if field in {"executor_reconciliation_claimed_at", "executor_reconciliation_retry_not_before"}
                else f"lease.{field}" for field in _SOURCE_LEASE_FIELDS
            )},
                   dispatch.status as dispatch_status
            from sandbox_leases lease
            left join sandbox_execution_dispatches dispatch
              on dispatch.tenant_id = lease.tenant_id
             and dispatch.run_id = lease.run_id
             and dispatch.attempt_id = lease.attempt_id
             and dispatch.lease_id = lease.id
            where lease.tenant_id = %s and lease.run_id = %s and lease.attempt_id = %s
            order by lease.id asc{lease_suffix}
            """,
            (tenant_id, run_id, attempt_id),
        )
    ).fetchall()
    attempt = await (
        await conn.execute(
            f"""
            select {", ".join(_ATTEMPT_FIELDS)}
            from run_attempts
            where tenant_id = %s and run_id = %s and id = %s{suffix}
            """,
            (tenant_id, run_id, attempt_id),
        )
    ).fetchone()
    return (
        dict(attempt) if attempt is not None else None,
        dict(run) if run is not None else None,
        [dict(row) for row in leases],
    )


def _cursor_after(keys: list[tuple[str, str, str]], limit: int) -> tuple[str, str, str] | None:
    return keys[-1] if len(keys) == limit else None


async def backfill_attempt_settlement_projections(
    conn: Any,
    *,
    tenant_id: str | None = None,
    after: tuple[str, str, str] | None = None,
    limit: int = DEFAULT_ATTEMPT_SETTLEMENT_PAGE_SIZE,
) -> dict[str, Any]:
    """Insert one bounded page of mappings; repeats never overwrite a receipt."""

    limit = _page_limit(limit)
    keys = await _page_attempt_keys(conn, tenant_id=tenant_id, after=after, limit=limit)
    inserted = 0
    existing = 0
    unknown = 0
    conflicts = 0
    cleanup = {
        "lease_count": 0,
        "released_lease_count": 0,
        "cleanup_pending_count": 0,
        "cleanup_failed_count": 0,
        "cleanup_claim_count": 0,
        "released_pending_settlement_count": 0,
    }
    for key in keys:
        attempt, run, leases = await _load_locked_attempt_facts(conn, key=key, lock=True)
        if attempt is None or run is None:
            unknown += 1
            continue
        projection = project_legacy_attempt_settlement(attempt, run, leases)
        values = projection.to_mapping()
        was_inserted = await persist_attempt_settlement_projection(conn, projection, insert_only=True)
        if values["receipt_json"] is not None:
            if not was_inserted:
                stored = await (await conn.execute(
                    """select receipt_json from run_attempt_settlements
                       where tenant_id=%s and run_id=%s and attempt_id=%s""", key,
                )).fetchone()
                original = values["receipt_json"]
                current = stored.get("receipt_json") if stored else None
                if isinstance(current, dict) and (
                    {k: v for k, v in current.items() if k != "diagnostics"}
                    == {k: v for k, v in original.items() if k != "diagnostics"}
                ):
                    old_notes, new_notes = original.get("diagnostics"), current.get("diagnostics")
                    if old_notes is not None and old_notes != new_notes:
                        if new_notes is None:
                            notes = old_notes
                        elif isinstance(old_notes, list) and isinstance(new_notes, list):
                            notes = new_notes + [note for note in old_notes if note not in new_notes]
                        else:
                            raise RuntimeError("attempt_settlement_upgrade_diagnostics_conflict")
                        await conn.execute(
                            """update run_attempt_settlements
                               set receipt_json=jsonb_set(receipt_json,'{diagnostics}',%s::jsonb)
                               where tenant_id=%s and run_id=%s and attempt_id=%s""",
                            (json.dumps(notes), *key),
                        )
            source_lease = next(
                (lease for lease in leases if lease.get("id") == values["source_lease_id"]),
                None,
            )
            received_at = (
                source_lease.get("executor_terminal_received_at")
                or source_lease.get("updated_at")
                or source_lease.get("created_at")
                if source_lease is not None else None
            )
            if received_at is not None:
                await conn.execute(
                    """
                    update run_attempt_settlements
                    set receipt_recorded_at = coalesce(receipt_recorded_at, %s)
                    where tenant_id = %s and run_id = %s and attempt_id = %s
                    """,
                    (received_at, *key),
                )
        inserted += was_inserted
        existing += not was_inserted
        unknown += values["mapping_state"] == "unknown"
        conflicts += values["mapping_state"] == "conflict"
        for field in cleanup:
            cleanup[field] += getattr(projection.cleanup, field)
    return {
        "attempt_count": len(keys),
        "inserted_count": inserted,
        "already_present_count": existing,
        "unknown_count": unknown,
        "conflict_count": conflicts,
        "cleanup_owner": "sandbox_leases",
        "cleanup": cleanup,
        "next_after": _cursor_after(keys, limit),
    }


async def compare_attempt_settlement_projections(
    conn: Any,
    *,
    tenant_id: str | None = None,
    after: tuple[str, str, str] | None = None,
    limit: int = DEFAULT_ATTEMPT_SETTLEMENT_PAGE_SIZE,
) -> dict[str, Any]:
    """Compare one bounded page; this result cannot establish global readiness."""

    limit = _page_limit(limit)
    keys = await _page_attempt_keys(conn, tenant_id=tenant_id, after=after, limit=limit)
    counts: dict[str, Any] = {
        "attempt_count": len(keys),
        "mapped_count": 0,
        "missing_count": 0,
        "unknown_count": 0,
        "conflict_count": 0,
        "mismatch_count": 0,
        "cleanup_lease_count": 0,
        "released_lease_count": 0,
        "cleanup_pending_count": 0,
        "cleanup_failed_count": 0,
        "cleanup_claim_count": 0,
        "released_pending_settlement_count": 0,
    }
    for key in keys:
        attempt, run, leases = await _load_locked_attempt_facts(conn, key=key, lock=False)
        if attempt is None or run is None:
            counts["unknown_count"] += 1
            counts["missing_count"] += 1
            continue
        projection = project_legacy_attempt_settlement(attempt, run, leases)
        expected = projection.to_mapping()
        counts["unknown_count"] += expected["mapping_state"] == "unknown"
        counts["conflict_count"] += expected["mapping_state"] == "conflict"
        if expected["mapping_state"] == "mapped":
            counts["mapped_count"] += 1
        for field, observation_field in (
            ("lease_count", "cleanup_lease_count"),
            ("released_lease_count", "released_lease_count"),
            ("cleanup_pending_count", "cleanup_pending_count"),
            ("cleanup_failed_count", "cleanup_failed_count"),
            ("cleanup_claim_count", "cleanup_claim_count"),
            ("released_pending_settlement_count", "released_pending_settlement_count"),
        ):
            counts[observation_field] += getattr(projection.cleanup, field)

        stored_cursor = await conn.execute(
            """
            select * from run_attempt_settlements
            where tenant_id = %s and run_id = %s and attempt_id = %s
            """,
            key,
        )
        stored = await stored_cursor.fetchone()
        if stored is None:
            counts["missing_count"] += 1
            continue
        stored = dict(stored)
        for field in ATTEMPT_SETTLEMENT_PROJECTION_FIELDS:
            expected_value, actual_value = expected[field], stored.get(field)
            if field == "receipt_json" and isinstance(expected_value, dict) and isinstance(actual_value, dict):
                expected_value = {k:v for k,v in expected_value.items() if k != "diagnostics"}
                actual_value = {k:v for k,v in actual_value.items() if k != "diagnostics"}
            if expected_value != actual_value:
                counts["mismatch_count"] += 1
                break

    counts["cleanup_owner"] = "sandbox_leases"
    counts["comparison_scope"] = "page"
    counts["next_after"] = _cursor_after(keys, limit)
    return counts
