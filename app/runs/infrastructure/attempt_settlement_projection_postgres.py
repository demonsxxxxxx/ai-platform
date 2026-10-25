"""PostgreSQL persistence and bounded comparison for Attempt settlement details."""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
from typing import Any

from app.runs.domain.attempt_settlement_projection import (
    ATTEMPT_SETTLEMENT_PROJECTION_FIELDS,
    ATTEMPT_SETTLEMENT_SCHEMA_VERSION,
    CleanupObservation,
    AttemptSettlementProjectionConflict,
    AttemptSettlementProjection,
    project_legacy_attempt_settlement,
)


MAX_ATTEMPT_SETTLEMENT_PAGE_SIZE = 800
DEFAULT_ATTEMPT_SETTLEMENT_PAGE_SIZE = 100
ATTEMPT_SETTLEMENT_STORAGE_VERSIONS = frozenset({"sandbox_lease.v1", "attempt.v1"})
ATTEMPT_SETTLEMENT_WRITER_VERSIONS = frozenset({"sandbox_lease.v1", "attempt.v1"})
_JSON_FIELDS = frozenset({
    "receipt_json",
    "intent_recovery_context_json",
    "accepted_context_json",
    "result_json",
})
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
    "owner_kind",
    "owner_id",
    "owner_generation",
    "started_at",
    "finished_at",
    "error_code",
    "terminal_reason",
)
_IMMUTABLE_FIELDS = (
    "source_lease_id",
    "receipt_identity",
    "receipt_sha256",
    "receipt_owner_generation",
    "intent_recovery_context_json",
    "accepted_context_json",
    "result_sha256",
)


def _canonical_digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True,
            separators=(",", ":"), allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _mapping(value: Any) -> dict[str, Any] | None:
    return dict(value) if isinstance(value, Mapping) else None


def _settlement_from_row(row: Mapping[str, Any]) -> AttemptSettlementProjection:
    return AttemptSettlementProjection(
        {field: row.get(field) for field in ATTEMPT_SETTLEMENT_PROJECTION_FIELDS},
        CleanupObservation(),
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
    lease_suffix = " for update of lease" if lock else ""
    leases = await (
        await conn.execute(
            f"""
            select {", ".join(f"lease.{field}" for field in _SOURCE_LEASE_FIELDS)},
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


async def lock_attempt_settlement_projection_sources(
    conn: Any,
    *,
    tenant_id: str,
    run_id: str,
    attempt_id: str,
) -> bool:
    """Acquire the source-row locks in the shared lifecycle order.

    Sandbox writers call this before changing any lease fact represented by the
    settlement detail.  The bounded identity targets one Attempt; the lease
    query locks only that Attempt's rows and orders them by lease id.
    """

    run = await (
        await conn.execute(
            "select id from runs where tenant_id = %s and id = %s for update",
            (tenant_id, run_id),
        )
    ).fetchone()
    await conn.execute(
        """
        select id from sandbox_leases
        where tenant_id = %s and run_id = %s and attempt_id = %s
        order by id asc for update
        """,
        (tenant_id, run_id, attempt_id),
    )
    attempt = await (
        await conn.execute(
            """
            select id from run_attempts
            where tenant_id = %s and run_id = %s and id = %s for update
            """,
            (tenant_id, run_id, attempt_id),
        )
    ).fetchone()
    return attempt is not None and run is not None


async def refresh_attempt_settlement_projection(
    conn: Any,
    *,
    tenant_id: str,
    run_id: str,
    attempt_id: str,
) -> AttemptSettlementProjection | None:
    """Project the current source rows after the caller's transaction write."""

    attempt, run, leases = await _load_locked_attempt_facts(
        conn,
        key=(tenant_id, run_id, attempt_id),
        lock=False,
    )
    if attempt is None or run is None:
        return None
    return project_legacy_attempt_settlement(attempt, run, leases)


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
    # The explicit acceptance writer stores this immutable context in Runs even
    # where the old Sandbox row cannot distinguish acceptance from an intent-only
    # recovery context. A later source refresh must retain that proven fact.
    if values.get("accepted_context_json") is None and current.get("accepted_context_json") is not None:
        values["accepted_context_json"] = current["accepted_context_json"]
        if (
            values.get("dispatch_state") == "accepted"
            and values.get("settlement_phase") != "unknown"
        ):
            values["mapping_state"] = "mapped"
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
        if values["receipt_json"] is not None:
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


async def _attempt_snapshot_identity(
    conn: Any,
    *,
    tenant_id: str | None,
) -> dict[str, Any]:
    row = await (
        await conn.execute(
            """
            select count(*) over ()::bigint as attempt_count,
                   tenant_id as max_tenant_id,
                   run_id as max_run_id,
                   id as max_attempt_id
            from run_attempts
            where (%s::text is null or tenant_id = %s)
            order by tenant_id desc, run_id desc, id desc
            limit 1
            """,
            (tenant_id, tenant_id),
        )
    ).fetchone()
    max_key = None
    if row and row.get("max_tenant_id") is not None:
        max_key = (
            str(row["max_tenant_id"]),
            str(row["max_run_id"]),
            str(row["max_attempt_id"]),
        )
    return {
        "attempt_count": int(row.get("attempt_count") or 0) if row else 0,
        "max_key": tuple(max_key) if max_key is not None else None,
    }


async def compare_attempt_settlement_snapshot(
    conn: Any,
    *,
    tenant_id: str | None = None,
    page_limit: int = DEFAULT_ATTEMPT_SETTLEMENT_PAGE_SIZE,
) -> dict[str, Any]:
    """Aggregate every page under the caller's stable MVCC snapshot.

    Callers must begin a REPEATABLE READ or SERIALIZABLE transaction before
    issuing any other query. This scan takes no row locks and performs no
    external I/O; it keeps only page counters in memory.
    """

    isolation = await (
        await conn.execute("select current_setting('transaction_isolation') as level")
    ).fetchone()
    isolation_level = str(isolation.get("level") or "") if isolation else ""
    if isolation_level not in {"repeatable read", "serializable"}:
        raise ValueError("attempt_settlement_snapshot_requires_stable_transaction")
    page_limit = _page_limit(page_limit)
    snapshot_identity = await _attempt_snapshot_identity(conn, tenant_id=tenant_id)
    summary: dict[str, Any] = {
        "attempt_count": 0,
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
    cursor = None
    while True:
        page = await compare_attempt_settlement_projections(
            conn, tenant_id=tenant_id, after=cursor, limit=page_limit
        )
        for field in summary:
            summary[field] += int(page[field])
        cursor = page["next_after"]
        if cursor is None:
            break
    ending_identity = await _attempt_snapshot_identity(conn, tenant_id=tenant_id)
    summary.update(
        cleanup_owner="sandbox_leases",
        comparison_scope="stable_snapshot",
        snapshot_isolation=isolation_level,
        snapshot_attempt_count=snapshot_identity["attempt_count"],
        snapshot_max_key=snapshot_identity["max_key"],
        snapshot_complete=(
            summary["attempt_count"] == snapshot_identity["attempt_count"]
            and ending_identity == snapshot_identity
        ),
        next_after=None,
    )
    return summary


async def get_attempt_settlement_storage_version(conn: Any) -> str:
    """Read the durable storage authority; missing or unknown markers fail closed."""
    row = await (
        await conn.execute(
            "select storage_version from run_attempt_settlement_storage where singleton = true"
        )
    ).fetchone()
    version = str(row.get("storage_version") or "") if row else ""
    if version not in ATTEMPT_SETTLEMENT_STORAGE_VERSIONS:
        raise RuntimeError("attempt_settlement_storage_marker_invalid")
    return version


async def lock_attempt_settlement_writer_fence(conn: Any) -> None:
    """Join the source-writer fence before reading the durable storage marker."""
    for relation in (
        "runs", "sandbox_leases", "run_attempts", "sandbox_execution_dispatches",
        "run_attempt_settlements", "run_attempt_settlement_storage",
        "run_attempt_settlement_writer", "run_stale_reconciliation_claims",
    ):
        await conn.execute(f"lock table {relation} in row exclusive mode")


async def read_attempt_settlement(
    conn: Any,
    *,
    tenant_id: str,
    run_id: str,
    attempt_id: str,
) -> dict[str, Any] | None:
    row = await (
        await conn.execute(
            """
            select * from run_attempt_settlements
            where tenant_id = %s and run_id = %s and attempt_id = %s
            """,
            (tenant_id, run_id, attempt_id),
        )
    ).fetchone()
    return dict(row) if row is not None else None


async def list_due_attempt_settlements(
    conn: Any,
    *,
    limit: int,
    stale_after_seconds: int,
) -> list[dict[str, Any]]:
    """Select receipt settlement work from the Attempt-owned reader projection."""
    limit = _page_limit(limit)
    if (isinstance(stale_after_seconds, bool) or not isinstance(stale_after_seconds, int)
            or stale_after_seconds < 0):
        raise ValueError("attempt_settlement_stale_after_invalid")
    missing = await (
        await conn.execute(
            """
            select 1
            from run_attempts attempt
            left join run_attempt_settlements detail
              on detail.tenant_id = attempt.tenant_id
             and detail.run_id = attempt.run_id
             and detail.attempt_id = attempt.id
            where detail.attempt_id is null
            limit 1
            """
        )
    ).fetchone()
    if missing is not None:
        raise RuntimeError("attempt_settlement_projection_missing")
    rows = await (
        await conn.execute(
            """
            select * from run_attempt_settlements
            where receipt_json is not null
              and intent_recovery_context_json is not null
              and (
                mapping_state <> 'mapped'
                or settlement_phase = 'pending'
                or (settlement_phase = 'retry' and next_retry_at <= now())
                or (settlement_phase = 'claimed'
                    and settlement_claimed_at < now() - make_interval(secs => %s))
              )
            order by coalesce(settlement_claimed_at, updated_at, created_at),
                     tenant_id, run_id, attempt_id
            limit %s
            """,
            (stale_after_seconds, limit),
        )
    ).fetchall()
    result = [dict(row) for row in rows]
    for row in result:
        if row.get("mapping_state") != "mapped":
            raise RuntimeError("attempt_settlement_projection_" + str(row.get("mapping_state") or "unknown"))
    return result


async def next_attempt_settlement_retry_delay(conn: Any) -> float | None:
    missing = await (
        await conn.execute(
            """
            select 1 from run_attempts attempt
            left join run_attempt_settlements detail
              on detail.tenant_id = attempt.tenant_id
             and detail.run_id = attempt.run_id
             and detail.attempt_id = attempt.id
            where detail.attempt_id is null limit 1
            """
        )
    ).fetchone()
    if missing is not None:
        raise RuntimeError("attempt_settlement_projection_missing")
    row = await (
        await conn.execute(
            """
            select extract(epoch from (min(next_retry_at) - now())) as delay_seconds
            from run_attempt_settlements
            where mapping_state = 'mapped' and settlement_phase = 'retry'
            """
        )
    ).fetchone()
    delay = row.get("delay_seconds") if row else None
    return max(float(delay), 0.0) if delay is not None else None



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

