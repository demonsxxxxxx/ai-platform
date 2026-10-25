"""PostgreSQL persistence and bounded comparison for Attempt settlement details."""

from __future__ import annotations

from collections.abc import Mapping
import json
from typing import Any

from app.runs.domain.attempt_settlement_projection import (
    ATTEMPT_SETTLEMENT_PROJECTION_FIELDS,
    AttemptSettlementProjectionConflict,
    AttemptSettlementProjection,
    project_legacy_attempt_settlement,
)


MAX_ATTEMPT_SETTLEMENT_PAGE_SIZE = 800
DEFAULT_ATTEMPT_SETTLEMENT_PAGE_SIZE = 100
_JSON_FIELDS = frozenset({"receipt_json", "accepted_context_json", "result_json"})
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
_RUN_FIELDS = (
    "id",
    "tenant_id",
    "status",
    "cancel_requested_at",
    "result_json",
    "error_code",
    "finished_at",
)
_ATTEMPT_FIELDS = (
    "id",
    "tenant_id",
    "run_id",
    "status",
    "owner_generation",
    "started_at",
    "finished_at",
    "error_code",
)
_IMMUTABLE_FIELDS = (
    "receipt_identity",
    "receipt_sha256",
    "receipt_owner_generation",
    "accepted_context_json",
    "result_sha256",
)


def _page_limit(limit: int) -> int:
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_ATTEMPT_SETTLEMENT_PAGE_SIZE:
        raise ValueError("attempt_settlement_projection_page_limit_invalid")
    return limit


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
    # lease id, then the original Attempt. Backfill and the future writer port
    # both use this order; the settlement row is written after these locks.
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
    lease_suffix = " for update" if lock else ""
    leases = await (
        await conn.execute(
            f"""
            select {", ".join(_SOURCE_LEASE_FIELDS)}
            from sandbox_leases
            where tenant_id = %s and run_id = %s and attempt_id = %s
            order by id asc{lease_suffix}
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


def _json_value(value: Any) -> Any:
    if value is None or isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _insert_statement() -> str:
    columns = ", ".join(ATTEMPT_SETTLEMENT_PROJECTION_FIELDS)
    placeholders = ", ".join(
        "%s::jsonb" if field in _JSON_FIELDS else "%s"
        for field in ATTEMPT_SETTLEMENT_PROJECTION_FIELDS
    )
    conflict = " on conflict (tenant_id, run_id, attempt_id) do nothing"
    return (
        f"insert into run_attempt_settlements({columns}) values ({placeholders})"
        f"{conflict} returning attempt_id"
    )


async def _insert_projection(
    conn: Any,
    values: Mapping[str, Any],
) -> bool:
    ordered = tuple(_json_value(values.get(field)) if field in _JSON_FIELDS else values.get(field)
                    for field in ATTEMPT_SETTLEMENT_PROJECTION_FIELDS)
    cursor = await conn.execute(_insert_statement(), ordered)
    return await cursor.fetchone() is not None


def _same_json(left: Any, right: Any) -> bool:
    return left == right


async def persist_attempt_settlement_projection(
    conn: Any,
    projection: AttemptSettlementProjection,
    *,
    insert_only: bool = False,
) -> bool:
    """Persist one exact Attempt detail without touching RunAttempt authority.

    `insert_only=True` is used by backfill. The eventual same-transaction legacy
    writer calls this Runs-owned port with `insert_only=False`; immutable receipt,
    accepted-context and committed-result conflicts fail closed.
    """

    values = projection.to_mapping()
    inserted = await _insert_projection(conn, values)
    if inserted or insert_only:
        return inserted

    tenant_id, run_id, attempt_id = values["tenant_id"], values["run_id"], values["attempt_id"]
    current_cursor = await conn.execute(
        """
        select * from run_attempt_settlements
        where tenant_id = %s and run_id = %s and attempt_id = %s
        for update
        """,
        (tenant_id, run_id, attempt_id),
    )
    current = await current_cursor.fetchone()
    if current is None:
        raise AttemptSettlementProjectionConflict("attempt_settlement_projection_row_missing")
    current = dict(current)
    if current.get("mapping_state") == "mapped" and values.get("mapping_state") != "mapped":
        raise AttemptSettlementProjectionConflict("attempt_settlement_projection_mapping_regressed")
    for field in _IMMUTABLE_FIELDS:
        existing = current.get(field)
        incoming = values.get(field)
        if existing is not None and incoming is not None and not _same_json(existing, incoming):
            raise AttemptSettlementProjectionConflict(
                f"attempt_settlement_projection_immutable_conflict:{field}"
            )
    if int(values.get("settlement_attempt_count") or 0) < int(current.get("settlement_attempt_count") or 0):
        raise AttemptSettlementProjectionConflict("attempt_settlement_projection_retry_regressed")

    merged = dict(values)
    for field in _IMMUTABLE_FIELDS:
        if merged.get(field) is None:
            merged[field] = current.get(field)
    update_fields = tuple(field for field in ATTEMPT_SETTLEMENT_PROJECTION_FIELDS
                          if field not in {"tenant_id", "run_id", "attempt_id", "schema_version"})
    assignments = ", ".join(
        f"{field} = %s::jsonb" if field in _JSON_FIELDS else f"{field} = %s"
        for field in update_fields
    )
    update_values = tuple(
        _json_value(merged.get(field)) if field in _JSON_FIELDS else merged.get(field)
        for field in update_fields
    )
    await conn.execute(
        f"""
        update run_attempt_settlements
        set {assignments}, updated_at = now()
        where tenant_id = %s and run_id = %s and attempt_id = %s
        """,
        (*update_values, tenant_id, run_id, attempt_id),
    )
    return False


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


def _same_field(expected: Any, actual: Any) -> bool:
    return expected == actual


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
        if any(not _same_field(expected[field], stored.get(field)) for field in ATTEMPT_SETTLEMENT_PROJECTION_FIELDS):
            counts["mismatch_count"] += 1

    counts["cleanup_owner"] = "sandbox_leases"
    counts["comparison_scope"] = "page"
    counts["next_after"] = _cursor_after(keys, limit)
    return counts

