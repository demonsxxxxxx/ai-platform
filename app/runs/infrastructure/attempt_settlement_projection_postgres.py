"""PostgreSQL persistence for the sole Attempt settlement authority."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from app.runs.domain.attempt_settlement_projection import (
    ATTEMPT_SETTLEMENT_PROJECTION_FIELDS,
    AttemptSettlementProjection,
    AttemptSettlementProjectionConflict,
    CleanupObservation,
)

MAX_ATTEMPT_SETTLEMENT_PAGE_SIZE = 800
DEFAULT_ATTEMPT_SETTLEMENT_PAGE_SIZE = 100
_JSON_FIELDS = frozenset({
    "receipt_json",
    "intent_recovery_context_json",
    "accepted_context_json",
    "result_json",
})
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

    `insert_only=True` belongs only to the one-time history upgrade. Runtime
    writers share the caller transaction; immutable receipt, accepted-context
    and committed-result conflicts fail closed.
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
    # Accepted context is immutable and cannot regress to an absent observation.
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


async def read_attempt_settlement(
    conn: Any,
    *,
    tenant_id: str,
    run_id: str,
    attempt_id: str,
    for_update: bool = False,
) -> dict[str, Any] | None:
    suffix = " for update" if for_update else ""
    row = await (
        await conn.execute(
            """
            select * from run_attempt_settlements
            where tenant_id = %s and run_id = %s and attempt_id = %s
            """ + suffix,
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
            select detail.* from run_attempt_settlements detail
            join runs owner_run on owner_run.tenant_id=detail.tenant_id and owner_run.id=detail.run_id
            where receipt_json is not null
              and accepted_context_json is not null
              and not exists (
                select 1 from sandbox_leases lease
                where lease.tenant_id = detail.tenant_id
                  and lease.run_id = detail.run_id
                  and lease.attempt_id = detail.attempt_id
                  and lease.id = detail.source_lease_id
                  and lease.status = 'quarantined'
              )
              and mapping_state = 'mapped'
              and (
                settlement_phase = 'pending'
                or (settlement_phase = 'retry' and next_retry_at <= now())
                or (settlement_phase = 'claimed'
                    and settlement_claimed_at < now() - make_interval(secs => %s))
              )
            order by coalesce(detail.settlement_claimed_at, detail.updated_at, detail.created_at),
                     detail.tenant_id, detail.run_id, detail.attempt_id
            limit %s for update of owner_run skip locked
            """,
            (stale_after_seconds, limit),
        )
    ).fetchall()
    # Unknown/conflict mappings stay visible to admin diagnostics and exact
    # dispatch/probe recovery, but cannot poison mapped Runs' settlement work.
    return [dict(row) for row in rows]


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
