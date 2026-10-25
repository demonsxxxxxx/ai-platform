"""Operator-gated, one-way retirement of Sandbox settlement columns."""

from __future__ import annotations

from collections.abc import Mapping
import json
from typing import Any

from app import schema_migrations


LEGACY_COLUMNS = schema_migrations.LEGACY_ATTEMPT_SETTLEMENT_COLUMNS
LEGACY_INDEXES = tuple(sorted(schema_migrations.LEGACY_ATTEMPT_SETTLEMENT_INDEXES))
LEGACY_CONSTRAINT = schema_migrations.LEGACY_ATTEMPT_SETTLEMENT_CONSTRAINT[1]
RETIREMENT_VERSION = schema_migrations.RUN_ATTEMPT_SETTLEMENT_RETIREMENT_SCHEMA_VERSION

_FENCE_TABLES = (
    "runs",
    "sandbox_leases",
    "run_attempts",
    "sandbox_execution_dispatches",
    "run_attempt_settlements",
    "run_attempt_settlement_storage",
    "run_attempt_settlement_writer",
    "run_attempt_settlement_retirement",
    "execution_supervision_domains",
    "execution_supervision_claims",
    "run_stale_reconciliation_claims",
    "run_delivery_intents",
)


async def _marker(conn: Any) -> dict[str, Any] | None:
    row = await (
        await conn.execute(
            "select state, retirement_version, minimum_compatible_version, "
            "operator_declaration, retired_at from run_attempt_settlement_retirement "
            "where singleton = true"
        )
    ).fetchone()
    return dict(row) if row is not None else None


async def _layout(conn: Any) -> dict[str, Any]:
    row = await (
        await conn.execute(
            """
            select
              (select count(*) from jsonb_array_elements_text(%s::jsonb) expected(name)
               join pg_attribute attributes
                 on attributes.attrelid = to_regclass('sandbox_leases')
                and attributes.attname = expected.name
                and attributes.attnum > 0 and not attributes.attisdropped) as legacy_column_count,
              (select count(*) from jsonb_array_elements_text(%s::jsonb) expected(name)
               where to_regclass(expected.name) is not null) as legacy_index_count,
              (select count(*) from pg_constraint
               where conrelid = to_regclass('sandbox_leases')
                 and conname = %s) as legacy_constraint_count
            """,
            (json.dumps(LEGACY_COLUMNS), json.dumps(LEGACY_INDEXES), LEGACY_CONSTRAINT),
        )
    ).fetchone() or {}
    return {
        "legacy_column_count": int(row.get("legacy_column_count") or 0),
        "legacy_index_count": int(row.get("legacy_index_count") or 0),
        "legacy_constraint_count": int(row.get("legacy_constraint_count") or 0),
    }


def _layout_consistent(state: str, layout: Mapping[str, Any]) -> bool:
    expected = (0, 0, 0) if state == "retired" else (8, 3, 1)
    actual = tuple(int(layout.get(name) or 0) for name in (
        "legacy_column_count", "legacy_index_count", "legacy_constraint_count",
    ))
    return actual == expected


async def _versions(conn: Any) -> tuple[str, str]:
    reader = await (
        await conn.execute(
            "select storage_version from run_attempt_settlement_storage where singleton = true"
        )
    ).fetchone()
    writer = await (
        await conn.execute(
            "select writer_version from run_attempt_settlement_writer where singleton = true"
        )
    ).fetchone()
    return (
        str(reader.get("storage_version") or "") if reader else "",
        str(writer.get("writer_version") or "") if writer else "",
    )


async def _compatibility_counts(conn: Any) -> dict[str, int]:
    detail = await (
        await conn.execute(
            """
            select count(*)::bigint as count
            from run_attempts attempt
            left join run_attempt_settlements settlement
              on settlement.tenant_id = attempt.tenant_id
             and settlement.run_id = attempt.run_id
             and settlement.attempt_id = attempt.id
            where settlement.attempt_id is null
               or settlement.mapping_state is distinct from 'mapped'
            """
        )
    ).fetchone()
    history = await (
        await conn.execute(
            """
            select count(*)::bigint as count
            from sandbox_leases lease
            left join run_attempt_settlements settlement
              on settlement.tenant_id = lease.tenant_id
             and settlement.run_id = lease.run_id
             and settlement.attempt_id = lease.attempt_id
             and settlement.source_lease_id = lease.id
            where
              (lease.executor_terminal_json is not null
               and (settlement.attempt_id is null
                    or settlement.mapping_state is distinct from 'mapped'
                    or (settlement.receipt_json - 'diagnostics') is distinct from
                       (lease.executor_terminal_json - 'diagnostics')))
              or
              (lease.executor_reconciliation_intent_context_json is not null
               and (settlement.attempt_id is null
                    or settlement.intent_recovery_context_json is distinct from
                       lease.executor_reconciliation_intent_context_json))
              or
              (lease.executor_reconciliation_context_json is not null
               and (settlement.attempt_id is null
                    or (settlement.accepted_context_json is distinct from
                        lease.executor_reconciliation_context_json
                        and settlement.intent_recovery_context_json is distinct from
                        lease.executor_reconciliation_context_json)))
            """
        )
    ).fetchone()
    unbound = await (
        await conn.execute(
            """
            select count(*)::bigint as count from sandbox_leases lease
            where (lease.executor_terminal_json is not null
                   or lease.executor_reconciliation_context_json is not null
                   or lease.executor_reconciliation_intent_context_json is not null)
              and not exists (
                select 1 from run_attempts attempt
                where attempt.tenant_id = lease.tenant_id
                  and attempt.run_id = lease.run_id and attempt.id = lease.attempt_id
              )
            """
        )
    ).fetchone()
    claims = await (
        await conn.execute(
            """
            select
              (select count(*) from run_attempt_settlements
               where settlement_phase = 'claimed' or settlement_claim_token is not null) as settlement,
              (select count(*) from sandbox_leases
               where executor_reconciliation_status = 'claimed'
                  or executor_reconciliation_claim_token is not null) as legacy_settlement,
              (select count(*) from sandbox_leases
               where executor_probe_claim_token is not null
                  or executor_probe_claimed_at is not null) as probe,
              (select count(*) from sandbox_leases
               where executor_cleanup_status = 'claimed'
                  or executor_cleanup_claim_token is not null
                  or executor_cleanup_claimed_at is not null) as cleanup,
              (select count(*) from execution_supervision_claims
               where recovered_at is null) as supervision,
              (select count(*) from run_stale_reconciliation_claims) as stale_reconciliation,
              (select count(*) from run_delivery_intents
               where state = 'claimed' or claim_token is not null) as delivery,
              (select count(*) from sandbox_leases
               where executor_cleanup_status not in ('not_required', 'finalized')) as unresolved_cleanup,
              (select count(*) from run_attempt_settlements
               where settlement_phase in (
                 'pending', 'retry', 'claimed', 'unknown',
                 'awaiting_receipt', 'awaiting_context'
               )) as pending_settlement,
              (select count(*) from runs
               where status in ('queued', 'running', 'cancel_requested')) as open_runs,
              (select count(*) from sandbox_leases where status = 'active') as active_leases,
              (select count(*) from sandbox_execution_dispatches
               where status in ('intent', 'unknown')) as unresolved_dispatches
            """
        )
    ).fetchone() or {}
    return {
        "unmapped_attempt_count": int(detail.get("count") or 0) if detail else 0,
        "unretained_legacy_history_count": int(history.get("count") or 0) if history else 0,
        "unbound_legacy_history_count": int(unbound.get("count") or 0) if unbound else 0,
        **{f"{key}_count": int(value or 0) for key, value in claims.items()},
    }


def _eligibility_ready(
    *,
    marker: Mapping[str, Any] | None,
    layout: Mapping[str, Any],
    reader_version: str,
    writer_version: str,
    counts: Mapping[str, int],
) -> bool:
    return bool(
        marker
        and marker.get("state") == "expanded"
        and marker.get("retirement_version") == RETIREMENT_VERSION
        and _layout_consistent("expanded", layout)
        and reader_version == "attempt.v1"
        and writer_version == "attempt.v1"
        and all(value == 0 for value in counts.values())
    )


async def attempt_settlement_retirement_status(conn: Any) -> dict[str, Any]:
    marker = await _marker(conn)
    state = str(marker.get("state") or "") if marker else ""
    layout = await _layout(conn)
    reader_version, writer_version = await _versions(conn)
    layout_current = _layout_consistent(state, layout) if state in {"expanded", "retired"} else False
    schema_marker = await (
        await conn.execute(
            "select state from schema_migrations where version = %s",
            (schema_migrations.TARGET_SCHEMA_VERSION,),
        )
    ).fetchone()
    schema_current = bool(schema_marker and schema_marker.get("state") == "applied")
    return {
        "status": "current" if layout_current and schema_current else "inconsistent",
        "retirement_state": state or None,
        "retirement_version": marker.get("retirement_version") if marker else None,
        "minimum_compatible_version": marker.get("minimum_compatible_version") if marker else None,
        "reader_version": reader_version,
        "writer_version": writer_version,
        "layout": layout,
        "layout_current": layout_current,
        "schema_marker_applied": schema_current,
        "retired_at": marker.get("retired_at") if marker else None,
    }


async def prepare_attempt_settlement_retirement(conn: Any) -> dict[str, Any]:
    status = await attempt_settlement_retirement_status(conn)
    if status["retirement_state"] == "retired":
        return {
            **status,
            "status": "already_retired" if status["status"] == "current" else "blocked",
            "activation_authorized": False,
        }
    marker = await _marker(conn)
    counts: dict[str, int] = {}
    if marker and marker.get("state") == "expanded" and status["layout_current"]:
        counts = await _compatibility_counts(conn)
    ready = (
        status["status"] == "current"
        and status["reader_version"] == "attempt.v1"
        and status["writer_version"] == "attempt.v1"
        and bool(counts)
        and all(value == 0 for value in counts.values())
    )
    return {
        **status,
        "status": "ready" if ready else "blocked",
        "compatibility_counts": counts,
        "fenced": False,
        "activation_authorized": False,
        "operator_declaration_verified_by_code": False,
    }


async def retire_attempt_settlement_legacy_columns(
    conn: Any,
    *,
    operator_confirmations: Mapping[str, bool],
    minimum_compatible_version: str,
    max_attempts: int = 10000,
    lock_timeout_seconds: int = 10,
    statement_timeout_seconds: int = 300,
) -> dict[str, Any]:
    required_confirmations = {
        "legacy_binaries_stopped",
        "runs_and_resource_claims_drained",
        "backup_restore_drill_complete",
    }
    if (
        set(operator_confirmations) != required_confirmations
        or any(operator_confirmations.get(name) is not True for name in required_confirmations)
    ):
        raise RuntimeError("attempt_settlement_retirement_operator_confirmations_required")
    if (
        not isinstance(minimum_compatible_version, str)
        or not minimum_compatible_version.strip()
        or len(minimum_compatible_version.strip()) > 128
        or isinstance(max_attempts, bool)
        or not isinstance(max_attempts, int)
        or max_attempts < 1
        or isinstance(lock_timeout_seconds, bool)
        or not isinstance(lock_timeout_seconds, int)
        or not 1 <= lock_timeout_seconds <= 30
        or isinstance(statement_timeout_seconds, bool)
        or not isinstance(statement_timeout_seconds, int)
        or not 1 <= statement_timeout_seconds <= 1800
    ):
        raise ValueError("attempt_settlement_retirement_budget_invalid")

    # Advisory lock acquisition executes a SELECT. Use statement snapshots until
    # all source tables are fenced; their locks then keep the complete comparison
    # stable while including commits that finished during lock acquisition.
    await conn.execute("set transaction isolation level read committed")
    await conn.execute(f"set local lock_timeout = '{lock_timeout_seconds}s'")
    await conn.execute(f"set local statement_timeout = '{statement_timeout_seconds}s'")
    await conn.execute("select pg_advisory_xact_lock(%s)", (schema_migrations.INDEX_MIGRATION_LOCK_ID,))
    await conn.execute("select pg_advisory_xact_lock(%s)", (schema_migrations.MIGRATION_LOCK_ID,))
    # Match the schema coordinator's INDEX -> core advisory lock order and the
    # writer's Run -> Sandbox -> Attempt -> dispatch -> detail/marker table order.
    for table in _FENCE_TABLES:
        await conn.execute(f"lock table {table} in share row exclusive mode")

    marker = await _marker(conn)
    layout = await _layout(conn)
    reader_version, writer_version = await _versions(conn)
    if (
        marker is None
        or marker.get("retirement_version") != RETIREMENT_VERSION
        or marker.get("state") not in {"expanded", "retired"}
        or not (await schema_migrations.schema_status(conn)).get("ready")
    ):
        return {
            "status": "blocked", "reason": "retirement_schema_not_ready",
            "activation_authorized": False,
        }
    if marker and marker.get("state") == "retired":
        if _layout_consistent("retired", layout):
            return {
                "status": "already_retired",
                "retirement_state": "retired",
                "reader_version": reader_version,
                "writer_version": writer_version,
                "operator_declaration_verified_by_code": False,
            }
        return {
            "status": "blocked",
            "reason": "retirement_marker_layout_mismatch",
            "retirement_state": "retired",
            "layout": layout,
            "activation_authorized": False,
        }
    if not _layout_consistent("expanded", layout):
        return {
            "status": "blocked",
            "reason": "expanded_legacy_layout_inconsistent",
            "layout": layout,
            "activation_authorized": False,
        }
    if reader_version != "attempt.v1" or writer_version != "attempt.v1":
        return {
            "status": "blocked",
            "reason": "attempt_settlement_authority_not_fully_active",
            "reader_version": reader_version,
            "writer_version": writer_version,
            "activation_authorized": False,
        }

    attempt_row = await (
        await conn.execute("select count(*)::bigint as count from run_attempts")
    ).fetchone() or {}
    attempt_count = int(attempt_row.get("count") or 0)
    if attempt_count > max_attempts:
        return {
            "status": "budget_exceeded",
            "attempt_count": attempt_count,
            "max_attempts": max_attempts,
            "activation_authorized": False,
        }
    counts = await _compatibility_counts(conn)
    if any(value != 0 for value in counts.values()):
        return {
            "status": "blocked",
            "reason": "retirement_preconditions_not_met",
            "attempt_count": attempt_count,
            "compatibility_counts": counts,
            "activation_authorized": False,
        }

    # Deliberately no CASCADE: an unexpected dependency aborts the transaction
    # and preserves every source value for operator review.
    for index_name in LEGACY_INDEXES:
        await conn.execute(f"drop index if exists {index_name}")
    await conn.execute(f"alter table sandbox_leases drop constraint {LEGACY_CONSTRAINT}")
    await conn.execute(
        "alter table sandbox_leases " + ", ".join(
            f"drop column {column}" for column in LEGACY_COLUMNS
        )
    )
    await conn.execute(
        "delete from schema_index_migrations where index_name = any(%s::text[])",
        (list(LEGACY_INDEXES),),
    )
    declaration = {
        "legacy_binaries_stopped": True,
        "runs_and_resource_claims_drained": True,
        "backup_restore_drill_complete": True,
        "minimum_compatible_version": minimum_compatible_version.strip(),
        "verified_by_code": False,
    }
    await conn.execute(
        """
        update run_attempt_settlement_retirement
        set state = 'retired', retirement_version = %s,
            minimum_compatible_version = %s, operator_declaration = %s::jsonb,
            retired_at = clock_timestamp(), updated_at = clock_timestamp()
        where singleton = true and state = 'expanded'
        """,
        (RETIREMENT_VERSION, minimum_compatible_version.strip(), json.dumps(declaration)),
    )
    return {
        "status": "retired",
        "retirement_state": "retired",
        "retirement_version": RETIREMENT_VERSION,
        "reader_version": reader_version,
        "writer_version": writer_version,
        "attempt_count": attempt_count,
        "compatibility_counts": counts,
        "operator_declaration": declaration,
        "operator_declaration_verified_by_code": False,
    }
