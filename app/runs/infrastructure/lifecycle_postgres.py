"""SQL-only primitives for Run lifecycle transitions."""

from __future__ import annotations

from typing import Any
import uuid

from psycopg import AsyncConnection

from app.platform.postgres.errors import RepositoryConflictError
from app.platform.postgres.limits import (
    PersistenceSizeLimitError,
    RUN_RESULT_MAX_BYTES,
    ensure_json_size,
)
from app.runs.domain.run_status import is_terminal_run_status
from app.runs.infrastructure.attempt_settlement_projection_postgres import (
    get_attempt_settlement_storage_version,
)
from app.runs.infrastructure.steps_postgres import _cancel_open_run_steps, _fail_open_run_steps


def _pending_terminal_receipt_sql(*, attempt_reader_active: bool) -> str:
    if attempt_reader_active:
        return """
        exists (
          select 1 from sandbox_leases lease
          left join run_attempt_settlements detail
            on detail.tenant_id = lease.tenant_id and detail.run_id = lease.run_id
           and detail.attempt_id = lease.attempt_id and detail.source_lease_id = lease.id
          where lease.tenant_id = runs.tenant_id and lease.run_id = runs.id
            and (
              (detail.attempt_id is null
               and lease.executor_status in ('completed', 'failed', 'cancelled'))
              or (detail.receipt_json is not null
                  and detail.settlement_phase <> 'settled')
            )
        )
        """
    return """
    exists (
      select 1 from sandbox_leases lease
      where lease.tenant_id = runs.tenant_id and lease.run_id = runs.id
        and lease.executor_terminal_json is not null
        and lease.executor_reconciliation_status is distinct from 'finalized'
    )
    """


async def _attempt_settlement_reader_active(conn: AsyncConnection) -> bool:
    return await get_attempt_settlement_storage_version(conn) == "attempt.v1"


def require_run_result_size(result_json: dict[str, Any] | None) -> None:
    try:
        ensure_json_size(
            result_json or {}, max_bytes=RUN_RESULT_MAX_BYTES, code="run_result_too_large"
        )
    except PersistenceSizeLimitError as exc:
        raise RepositoryConflictError(exc.code) from exc


def _json_text(value: dict[str, Any] | None) -> str:
    import json

    return json.dumps(value or {}, ensure_ascii=False)


async def stage_run_terminalization(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    run_id: str,
    target_status: str,
    terminal_reason: str,
    result_json: dict[str, Any] | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
) -> dict[str, Any] | None:
    if target_status not in {"failed", "cancel_requested", "cancelled"}:
        raise ValueError("invalid_run_terminalization_target")
    cursor = await conn.execute(
        """
        update runs
        set terminalization_target = case
              when terminalization_target = 'cancel_requested'
                   and %s = 'cancelled' then 'cancelled'
              else coalesce(terminalization_target, %s)
            end,
            terminalization_reason = case
              when terminalization_target is null
                   or (terminalization_target = 'cancel_requested' and %s = 'cancelled') then %s
              else terminalization_reason
            end,
            terminalization_result_json = case
              when terminalization_target is null
                   or (terminalization_target = 'cancel_requested' and %s = 'cancelled') then %s::jsonb
              else terminalization_result_json
            end,
            terminalization_error_code = case
              when terminalization_target is null
                   or (terminalization_target = 'cancel_requested' and %s = 'cancelled') then %s
              else terminalization_error_code
            end,
            terminalization_error_message = case
              when terminalization_target is null
                   or (terminalization_target = 'cancel_requested' and %s = 'cancelled') then %s
              else terminalization_error_message
            end
        where tenant_id = %s and id = %s
          and status not in ('succeeded', 'failed', 'cancelled')
        returning id, tenant_id, user_id, trace_id, terminalization_target,
                  terminalization_reason, terminalization_result_json,
                  terminalization_error_code, terminalization_error_message
        """,
        (
            target_status, target_status, target_status, terminal_reason,
            target_status, _json_text(result_json), target_status, error_code,
            target_status, error_message, tenant_id, run_id,
        ),
    )
    return await cursor.fetchone()


async def load_staged_terminalization(
    conn: AsyncConnection, *, tenant_id: str, run_id: str
) -> dict[str, Any] | None:
    cursor = await conn.execute(
        """
        select id, tenant_id, user_id, trace_id, status, latency_ms,
               input_token_count, output_token_count, total_token_count,
               estimated_cost_minor, terminalization_target,
               terminalization_reason, terminalization_result_json,
               terminalization_error_code, terminalization_error_message
        from runs
        where tenant_id = %s and id = %s
          and (terminalization_target is not null
               or status in ('succeeded', 'failed', 'cancelled'))
        for update
        """,
        (tenant_id, run_id),
    )
    return await cursor.fetchone()


async def clear_cancel_requested_terminalization(
    conn: AsyncConnection, *, tenant_id: str, run_id: str
) -> dict[str, Any] | None:
    """Clear a temporary cancellation stage while leaving the active Run intact."""
    cursor = await conn.execute(
        """
        update runs
        set terminalization_target = null,
            terminalization_reason = '',
            terminalization_result_json = '{}'::jsonb,
            terminalization_error_code = null,
            terminalization_error_message = null
        where tenant_id = %s and id = %s
          and terminalization_target = 'cancel_requested'
          and status not in ('succeeded', 'failed', 'cancelled')
        returning id, status
        """,
        (tenant_id, run_id),
    )
    row = await cursor.fetchone()
    return dict(row) if row is not None else None


async def finalize_staged_terminalization(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    run_id: str,
    target_status: str,
    latency_ms: int | None = None,
    input_token_count: int = 0,
    output_token_count: int = 0,
    total_token_count: int = 0,
    estimated_cost_minor: int = 0,
) -> dict[str, Any] | None:
    if target_status not in {"failed", "cancelled"}:
        raise ValueError("invalid_run_terminalization_target")
    cursor = await conn.execute(
        """
        update runs
        set status = %s,
            result_json = terminalization_result_json,
            finished_at = now(),
            error_code = case when %s = 'failed' then terminalization_error_code else null end,
            error_message = case when %s = 'failed' then terminalization_error_message else null end,
            latency_ms = coalesce(%s, latency_ms),
            input_token_count = coalesce(nullif(%s, 0), input_token_count),
            output_token_count = coalesce(nullif(%s, 0), output_token_count),
            total_token_count = coalesce(nullif(%s, 0), total_token_count),
            estimated_cost_minor = coalesce(nullif(%s, 0), estimated_cost_minor),
            terminalization_target = null,
            terminalization_reason = '',
            terminalization_result_json = '{}'::jsonb,
            terminalization_error_code = null,
            terminalization_error_message = null
        where tenant_id = %s and id = %s
          and terminalization_target = %s
          and status not in ('succeeded', 'failed', 'cancelled')
        returning id, user_id, trace_id, status, latency_ms, input_token_count,
                  output_token_count, total_token_count, estimated_cost_minor
        """,
        (
            target_status, target_status, target_status, latency_ms,
            input_token_count, output_token_count, total_token_count,
            estimated_cost_minor, tenant_id, run_id, target_status,
        ),
    )
    row = await cursor.fetchone()
    if row is None:
        return None
    if target_status == "failed":
        await _fail_open_run_steps(conn, tenant_id=tenant_id, run_id=run_id)
    else:
        await _cancel_open_run_steps(conn, tenant_id=tenant_id, run_id=run_id)
    count_cursor = await conn.execute(
        "select count(*) as artifact_count from artifacts where tenant_id = %s and run_id = %s",
        (tenant_id, run_id),
    )
    count_row = await count_cursor.fetchone()
    result = dict(row)
    result["artifact_count"] = count_row.get("artifact_count", 0) if count_row else 0
    return result


async def complete_run(
    conn: AsyncConnection, *, tenant_id: str, run_id: str,
    result_json: dict[str, Any], observability: tuple[int | None, int, int, int, int],
) -> bool:
    latency_ms, input_tokens, output_tokens, total_tokens, estimated_cost_minor = observability
    cursor = await conn.execute(
        """
        update runs
        set status = 'succeeded', result_json = %s::jsonb, finished_at = now(),
            error_code = null, error_message = null, latency_ms = %s,
            input_token_count = %s, output_token_count = %s,
            total_token_count = %s, estimated_cost_minor = %s
        where tenant_id = %s and id = %s
          and status not in ('succeeded', 'failed', 'cancelled')
          and cancel_requested_at is null
          and terminalization_target is null
        returning id
        """,
        (_json_text(result_json), latency_ms, input_tokens, output_tokens,
         total_tokens, estimated_cost_minor, tenant_id, run_id),
    )
    return await cursor.fetchone() is not None


async def is_cancel_requested(conn: AsyncConnection, *, tenant_id: str, run_id: str) -> bool:
    cursor = await conn.execute(
        "select cancel_requested_at from runs where tenant_id = %s and id = %s",
        (tenant_id, run_id),
    )
    row = await cursor.fetchone()
    return bool(row and row.get("cancel_requested_at"))


async def classify_success_commit_block(conn: AsyncConnection, *, tenant_id: str, run_id: str) -> str:
    cursor = await conn.execute(
        """select status, cancel_requested_at, terminalization_target
           from runs where tenant_id = %s and id = %s for update""",
        (tenant_id, run_id),
    )
    row = await cursor.fetchone()
    if row is None or is_terminal_run_status(row.get("status")):
        return "stale_terminal_state"
    if row.get("cancel_requested_at") or str(row.get("terminalization_target") or "") in {"cancel_requested", "cancelled"}:
        return "cancel_requested"
    return "stale_terminal_state"


async def list_stale_run_reconciliation_candidates(
    conn: AsyncConnection, *, stale_after_seconds: int,
    cancel_requested_after_seconds: int | None = None, limit: int,
    postgres_claim_mode: bool = False,
) -> list[dict[str, Any]]:
    bounded_staleness = max(int(stale_after_seconds), 1)
    bounded_cancel_staleness = max(
        int(
            bounded_staleness
            if cancel_requested_after_seconds is None
            else cancel_requested_after_seconds
        ),
        1,
    )
    bounded_limit = max(1, min(int(limit), 50))
    if postgres_claim_mode:
        pending_receipt_guard = _pending_terminal_receipt_sql(
            attempt_reader_active=await _attempt_settlement_reader_active(conn)
        )
        cursor = await conn.execute(
            f"""
            select runs.tenant_id, runs.workspace_id, runs.user_id, runs.id as run_id,
                   runs.status, runs.cancel_requested_at,
                   latest_attempt.id as expected_attempt_id,
                   coalesce(latest_attempt.owner_generation, 0) as expected_owner_generation,
                   runs.cancel_requested_at as cancel_requested_before,
                   greatest(
                     coalesce(latest_event.created_at, '-infinity'::timestamptz),
                     coalesce(runs.started_at, '-infinity'::timestamptz),
                     coalesce(runs.queued_at, '-infinity'::timestamptz),
                     runs.created_at
                   ) as stale_before
              from runs
              left join lateral (
                select run_events.created_at
                from run_events
                where run_events.tenant_id = runs.tenant_id
                  and run_events.run_id = runs.id
                order by run_events.created_at desc, run_events.sequence desc
                limit 1
              ) as latest_event on true
              left join lateral (
                select attempt.id, attempt.owner_generation, attempt.status
                from run_attempts attempt
                where attempt.tenant_id = runs.tenant_id
                  and attempt.run_id = runs.id
                order by attempt.ordinal desc
                limit 1
              ) as latest_attempt on true
             where runs.status = 'running'
               and (
                 (runs.cancel_requested_at is not null
                  and runs.cancel_requested_at <= clock_timestamp() - (%s * interval '1 second'))
                 or
                 (runs.cancel_requested_at is null
                  and greatest(
                        coalesce(latest_event.created_at, '-infinity'::timestamptz),
                        coalesce(runs.started_at, '-infinity'::timestamptz),
                        coalesce(runs.queued_at, '-infinity'::timestamptz),
                        runs.created_at
                      ) <= clock_timestamp() - (%s * interval '1 second'))
               )
               and not exists (
                 select 1 from sandbox_leases lease
                 where lease.tenant_id = runs.tenant_id and lease.run_id = runs.id
                   and lease.status = 'active'
               )
               and not ({pending_receipt_guard})
               and not exists (
                 select 1 from run_delivery_intents delivery
                 where delivery.tenant_id = runs.tenant_id and delivery.run_id = runs.id
                   and delivery.state = 'claimed'
                   and delivery.claim_expires_at > clock_timestamp()
               )
               and not exists (
                 select 1 from run_attempts attempt
                 where attempt.tenant_id = runs.tenant_id and attempt.run_id = runs.id
                   and attempt.status in ('claimed', 'running', 'cancel_requested')
                   and attempt.lease_expires_at > clock_timestamp()
               )
               and (
                 latest_attempt.id is null
                 or latest_attempt.status not in ('succeeded', 'failed', 'cancelled')
               )
               and not exists (
                 select 1 from run_stale_reconciliation_claims claim
                 where claim.tenant_id = runs.tenant_id and claim.run_id = runs.id
                   and claim.claim_deadline_at > clock_timestamp()
               )
             order by stale_before asc, runs.tenant_id asc, runs.id asc
             limit %s for update of runs skip locked
            """,
            (bounded_cancel_staleness, bounded_staleness, bounded_limit),
        )
        return list(await cursor.fetchall())

    cursor = await conn.execute(
        """
        select runs.tenant_id, runs.workspace_id, runs.user_id, runs.id as run_id,
               runs.status, runs.cancel_requested_at,
               runs.cancel_requested_at as cancel_requested_before,
               greatest(
                 coalesce(latest_event.created_at, '-infinity'::timestamptz),
                 coalesce(runs.started_at, '-infinity'::timestamptz),
                 coalesce(runs.queued_at, '-infinity'::timestamptz),
                 runs.created_at
               ) as stale_before
           from runs
           left join lateral (
             select run_events.created_at
             from run_events
             where run_events.tenant_id = runs.tenant_id
               and run_events.run_id = runs.id
             order by run_events.created_at desc, run_events.sequence desc
             limit 1
           ) as latest_event on true
           where runs.status in ('queued', 'running')
             and (
               (runs.cancel_requested_at is not null
                and runs.cancel_requested_at <= clock_timestamp() - (%s * interval '1 second'))
               or
               (runs.cancel_requested_at is null
                and greatest(
                      coalesce(latest_event.created_at, '-infinity'::timestamptz),
                      coalesce(runs.started_at, '-infinity'::timestamptz),
                      coalesce(runs.queued_at, '-infinity'::timestamptz),
                      runs.created_at
                    ) <= clock_timestamp() - (%s * interval '1 second'))
             )
             and not exists (
               select 1 from sandbox_leases
               where sandbox_leases.tenant_id = runs.tenant_id
                 and sandbox_leases.run_id = runs.id
                 and sandbox_leases.status = 'active'
             )
           order by stale_before asc, runs.tenant_id asc, runs.id asc
           limit %s for update of runs skip locked
        """,
        (bounded_cancel_staleness, bounded_staleness, bounded_limit),
    )
    return list(await cursor.fetchall())


async def stage_stale_run_reconciliation(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    workspace_id: str,
    user_id: str | None,
    run_id: str,
    expected_status: str,
    stale_before: Any,
    cancel_requested_before: Any | None,
    terminal_status: str,
    error_code: str | None,
    error_message: str | None,
    claim_token: str | None,
    expected_attempt_id: str | None,
    expected_owner_generation: int,
) -> dict[str, Any] | None:
    """Stage one stale Run under the same receipt and claim fences as claiming."""
    if expected_status not in {"queued", "running"}:
        raise ValueError("invalid_stale_run_expected_status")
    if terminal_status not in {"failed", "cancelled"}:
        raise ValueError("invalid_stale_run_terminal_status")

    pending_receipt_guard = _pending_terminal_receipt_sql(
        attempt_reader_active=await _attempt_settlement_reader_active(conn)
    )
    target_result = (
        {"message": "任务已取消", "reconciliation_reason": "stale_run_no_owner"}
        if terminal_status == "cancelled"
        else {
            "message": "Run interrupted because no live execution owner remains.",
            "retryable": True,
            "reconciliation_reason": "stale_run_no_owner",
        }
    )
    cursor = await conn.execute(
        f"""
        update runs
        set terminalization_target = %s,
            terminalization_reason = 'stale_run_no_owner',
            terminalization_result_json = %s::jsonb,
            terminalization_error_code = %s,
            terminalization_error_message = %s
        where tenant_id = %s
          and workspace_id = %s
          and user_id is not distinct from %s
          and id = %s
          and status = %s
          and (terminalization_target is null
               or (terminalization_target = 'cancel_requested' and %s = 'cancelled'))
          and (%s <> 'cancelled' or cancel_requested_at is not null)
          and (%s <> 'failed' or cancel_requested_at is null)
          and (
            %s::uuid is null
            or exists (
              select 1 from run_stale_reconciliation_claims claim
              where claim.tenant_id = runs.tenant_id and claim.run_id = runs.id
                and claim.claim_token = %s::uuid
                and claim.expected_attempt_id is not distinct from %s
                and claim.expected_owner_generation = %s
                and claim.claim_deadline_at > clock_timestamp()
            )
          )
          and not exists (
            select 1 from sandbox_leases
            where sandbox_leases.tenant_id = runs.tenant_id
              and sandbox_leases.run_id = runs.id
              and sandbox_leases.status = 'active'
          )
          and not ({pending_receipt_guard})
          and (
            (%s = 'cancelled' and cancel_requested_at <= %s::timestamptz)
            or
            (%s = 'failed' and greatest(
                  coalesce((select max(created_at) from run_events
                            where run_events.tenant_id = runs.tenant_id
                              and run_events.run_id = runs.id), '-infinity'::timestamptz),
                  coalesce(started_at, '-infinity'::timestamptz),
                  coalesce(queued_at, '-infinity'::timestamptz),
                  created_at
                ) <= %s::timestamptz)
          )
        returning id, trace_id, terminalization_target
        """,
        (
            terminal_status,
            _json_text(target_result),
            error_code,
            error_message,
            tenant_id,
            workspace_id,
            user_id,
            run_id,
            expected_status,
            terminal_status,
            terminal_status,
            terminal_status,
            claim_token,
            claim_token,
            expected_attempt_id,
            expected_owner_generation,
            terminal_status,
            cancel_requested_before,
            terminal_status,
            stale_before,
        ),
    )
    staged = await cursor.fetchone()
    if staged is None:
        if claim_token is None:
            return None
        cursor = await conn.execute(
            f"""
            select runs.id, runs.trace_id, runs.terminalization_target
            from runs
            where runs.tenant_id = %s and runs.workspace_id = %s
              and runs.user_id is not distinct from %s and runs.id = %s
              and runs.status = %s
              and runs.terminalization_target = %s
              and runs.terminalization_reason = 'stale_run_no_owner'
              and exists (
                select 1 from run_stale_reconciliation_claims claim
                where claim.tenant_id = runs.tenant_id and claim.run_id = runs.id
                  and claim.claim_token = %s::uuid
                  and claim.expected_attempt_id is not distinct from %s
                  and claim.expected_owner_generation = %s
                  and claim.claim_deadline_at > clock_timestamp()
              )
              and not exists (
                select 1 from sandbox_leases lease
                where lease.tenant_id = runs.tenant_id and lease.run_id = runs.id
                  and lease.status = 'active'
              )
              and not ({pending_receipt_guard})
            """,
            (
                tenant_id,
                workspace_id,
                user_id,
                run_id,
                expected_status,
                terminal_status,
                claim_token,
                expected_attempt_id,
                expected_owner_generation,
            ),
        )
        staged = await cursor.fetchone()
        if staged is None:
            return None
        staged = {**staged, "already_staged": True}
    return dict(staged)


async def claim_stale_run_reconciliation(
    conn: AsyncConnection,
    *,
    candidate: dict[str, Any],
    worker_id: str,
    stale_after_seconds: int,
    cancel_requested_after_seconds: int,
    claim_ttl_seconds: int,
) -> dict[str, Any] | None:
    """Claim and revalidate one stale Run while holding Run then Attempt locks."""
    tenant_id = str(candidate.get("tenant_id") or "")
    run_id = str(candidate.get("run_id") or "")
    workspace_id = str(candidate.get("workspace_id") or "")
    user_id = candidate.get("user_id")
    bounded_staleness = max(int(stale_after_seconds), 1)
    bounded_cancel_staleness = max(int(cancel_requested_after_seconds), 1)
    bounded_ttl = max(int(claim_ttl_seconds), 1)
    claimer = worker_id.strip()
    expected_attempt_id = candidate.get("expected_attempt_id")
    expected_owner_generation = candidate.get("expected_owner_generation")
    if (
        not tenant_id
        or not run_id
        or not workspace_id
        or not claimer
        or str(candidate.get("status") or "") != "running"
        or (
            expected_attempt_id is None
            and (type(expected_owner_generation) is not int or expected_owner_generation != 0)
        )
        or (
            expected_attempt_id is not None
            and (
                not isinstance(expected_attempt_id, str)
                or not expected_attempt_id
                or type(expected_owner_generation) is not int
                or expected_owner_generation < 1
            )
        )
    ):
        raise ValueError("stale_run_reconciliation_claim_invalid")

    pending_receipt_guard = _pending_terminal_receipt_sql(
        attempt_reader_active=await _attempt_settlement_reader_active(conn)
    )
    run_cursor = await conn.execute(
        f"""
        select runs.id, runs.tenant_id, runs.workspace_id, runs.user_id, runs.status,
               runs.cancel_requested_at, runs.terminalization_target,
               runs.terminalization_reason,
               greatest(
                 coalesce(latest_event.created_at, '-infinity'::timestamptz),
                 coalesce(runs.started_at, '-infinity'::timestamptz),
                 coalesce(runs.queued_at, '-infinity'::timestamptz),
                 runs.created_at
               ) as stale_before,
               (
                 (runs.cancel_requested_at is not null
                  and runs.cancel_requested_at <= clock_timestamp() - (%s * interval '1 second'))
                 or
                 (runs.cancel_requested_at is null
                  and greatest(
                        coalesce(latest_event.created_at, '-infinity'::timestamptz),
                        coalesce(runs.started_at, '-infinity'::timestamptz),
                        coalesce(runs.queued_at, '-infinity'::timestamptz),
                        runs.created_at
                      ) <= clock_timestamp() - (%s * interval '1 second'))
               ) as stale,
               exists (select 1 from sandbox_leases lease
                 where lease.tenant_id = runs.tenant_id and lease.run_id = runs.id
                   and lease.status = 'active') as active_lease,
               ({pending_receipt_guard}) as pending_terminal_receipt,
               exists (select 1 from run_delivery_intents delivery
                 where delivery.tenant_id = runs.tenant_id and delivery.run_id = runs.id
                   and delivery.state = 'claimed'
                   and delivery.claim_expires_at > clock_timestamp()) as delivery_inflight
          from runs
          left join lateral (
            select run_events.created_at
            from run_events
            where run_events.tenant_id = runs.tenant_id and run_events.run_id = runs.id
            order by run_events.created_at desc, run_events.sequence desc
            limit 1
          ) as latest_event on true
         where runs.tenant_id = %s and runs.id = %s
           and runs.workspace_id = %s and runs.user_id is not distinct from %s
           and runs.status = 'running'
         for update of runs
        """,
        (
            bounded_cancel_staleness,
            bounded_staleness,
            tenant_id,
            run_id,
            workspace_id,
            user_id,
        ),
    )
    run = await run_cursor.fetchone()
    if (
        run is None
        or not bool(run.get("stale"))
        or bool(run.get("active_lease"))
        or bool(run.get("pending_terminal_receipt"))
        or bool(run.get("delivery_inflight"))
    ):
        return None
    target = str(run.get("terminalization_target") or "")
    if target not in {"", "cancel_requested", "failed", "cancelled"}:
        return None
    if target in {"failed", "cancelled"} and str(run.get("terminalization_reason") or "") != "stale_run_no_owner":
        return None

    attempt_cursor = await conn.execute(
        """
        select id, owner_generation, status,
               (lease_expires_at > clock_timestamp()) as lease_live
        from run_attempts
        where tenant_id = %s and run_id = %s
        order by ordinal desc
        limit 1
        for update
        """,
        (tenant_id, run_id),
    )
    attempt = await attempt_cursor.fetchone()
    actual_attempt_id = str(attempt["id"]) if attempt is not None else None
    actual_owner_generation = int(attempt["owner_generation"]) if attempt is not None else 0
    if (
        actual_attempt_id != expected_attempt_id
        or actual_owner_generation != expected_owner_generation
        or (
            attempt is not None
            and str(attempt.get("status") or "") in {"succeeded", "failed", "cancelled"}
        )
        or (
            attempt is not None
            and bool(attempt.get("lease_live"))
            and str(attempt.get("status") or "") in {"claimed", "running", "cancel_requested"}
        )
    ):
        return None

    pending_receipt_recheck = f"""
        select ({pending_receipt_guard}) as pending_terminal_receipt
        from runs where tenant_id = %s and id = %s
    """
    for update in (
        """
        select exists (
          select 1 from sandbox_leases lease
          where lease.tenant_id = %s and lease.run_id = %s and lease.status = 'active'
        ) as active_lease
        """,
        pending_receipt_recheck,
        """
        select exists (
          select 1 from run_delivery_intents delivery
          where delivery.tenant_id = %s and delivery.run_id = %s
            and delivery.state = 'claimed'
            and delivery.claim_expires_at > clock_timestamp()
        ) as delivery_inflight
        """,
    ):
        guard_cursor = await conn.execute(update, (tenant_id, run_id))
        guard = await guard_cursor.fetchone() or {}
        if any(bool(value) for value in guard.values()):
            return None

    claim_token = uuid.uuid4()
    cursor = await conn.execute(
        """
        insert into run_stale_reconciliation_claims(
          tenant_id, run_id, expected_attempt_id, expected_owner_generation,
          claim_token, claimed_by, claimed_at, claim_deadline_at
        )
        values (
          %s, %s, %s, %s, %s, %s, clock_timestamp(),
          clock_timestamp() + (%s * interval '1 second')
        )
        on conflict (tenant_id, run_id) do update
        set expected_attempt_id = excluded.expected_attempt_id,
            expected_owner_generation = excluded.expected_owner_generation,
            claim_token = excluded.claim_token,
            claimed_by = excluded.claimed_by,
            claimed_at = excluded.claimed_at,
            claim_deadline_at = excluded.claim_deadline_at
        where run_stale_reconciliation_claims.claim_deadline_at <= clock_timestamp()
        returning tenant_id, run_id, expected_attempt_id, expected_owner_generation,
                  claim_token, claimed_by, claimed_at, claim_deadline_at
        """,
        (
            tenant_id,
            run_id,
            actual_attempt_id,
            actual_owner_generation,
            claim_token,
            claimer,
            bounded_ttl,
        ),
    )
    claimed = await cursor.fetchone()
    if claimed is None:
        return None
    return {
        **dict(claimed),
        "status": str(run.get("status") or ""),
        "cancel_requested_at": run.get("cancel_requested_at"),
        "stale_before": run.get("stale_before"),
    }


async def renew_stale_run_reconciliation_claim(
    conn: AsyncConnection,
    *,
    claim: dict[str, Any],
    claim_ttl_seconds: int,
) -> bool:
    if not await _stale_run_reconciliation_claim_binding_is_current(
        conn, claim=claim, for_update=True
    ):
        return False
    cursor = await conn.execute(
        """
        update run_stale_reconciliation_claims
        set claim_deadline_at = clock_timestamp() + (%s * interval '1 second')
        where tenant_id = %s and run_id = %s and claim_token = %s
          and expected_attempt_id is not distinct from %s
          and expected_owner_generation = %s
          and claim_deadline_at > clock_timestamp()
        returning claim_token
        """,
        (
            max(int(claim_ttl_seconds), 1),
            claim["tenant_id"],
            claim["run_id"],
            claim["claim_token"],
            claim.get("expected_attempt_id"),
            int(claim["expected_owner_generation"]),
        ),
    )
    return await cursor.fetchone() is not None


async def stale_run_reconciliation_claim_is_current(
    conn: AsyncConnection,
    *,
    claim: dict[str, Any],
    for_update: bool = False,
) -> bool:
    return await _stale_run_reconciliation_claim_binding_is_current(
        conn, claim=claim, for_update=for_update
    )


async def _stale_run_reconciliation_claim_binding_is_current(
    conn: AsyncConnection,
    *,
    claim: dict[str, Any],
    for_update: bool,
) -> bool:
    """Check the claim against current Run and Attempt state in lifecycle lock order."""
    lock = " for update" if for_update else ""
    run_cursor = await conn.execute(
        "select status from runs where tenant_id = %s and id = %s" + lock,
        (claim["tenant_id"], claim["run_id"]),
    )
    run = await run_cursor.fetchone()
    if run is None or str(run.get("status") or "") != "running":
        return False

    lease_cursor = await conn.execute(
        "select status from sandbox_leases where tenant_id = %s and run_id = %s "
        "order by id asc" + (" for update" if for_update else ""),
        (claim["tenant_id"], claim["run_id"]),
    )
    leases = list(await lease_cursor.fetchall())
    pending_receipt_cursor = await conn.execute(
        f"""
        select ({_pending_terminal_receipt_sql(
            attempt_reader_active=await _attempt_settlement_reader_active(conn)
        )}) as pending_terminal_receipt
        from runs where tenant_id = %s and id = %s
        """,
        (claim["tenant_id"], claim["run_id"]),
    )
    pending_receipt = await pending_receipt_cursor.fetchone()
    if (
        any(str(lease.get("status") or "") == "active" for lease in leases)
        or pending_receipt is None
        or bool(pending_receipt.get("pending_terminal_receipt"))
    ):
        return False

    attempt_lock = " for update" if for_update else ""
    attempt_cursor = await conn.execute(
        """
        select id, owner_generation, owner_kind, owner_id, status,
               (lease_expires_at > clock_timestamp()) as lease_live
        from run_attempts
        where tenant_id = %s and run_id = %s
        order by ordinal desc
        limit 1
        """ + attempt_lock,
        (claim["tenant_id"], claim["run_id"]),
    )
    attempt = await attempt_cursor.fetchone()
    actual_attempt_id = str(attempt["id"]) if attempt is not None else None
    actual_generation = int(attempt["owner_generation"]) if attempt is not None else 0
    expected_attempt_id = claim.get("expected_attempt_id")
    expected_generation = int(claim["expected_owner_generation"])
    if actual_attempt_id != expected_attempt_id:
        return False
    if attempt is not None:
        status = str(attempt.get("status") or "")
        generation_matches = actual_generation == expected_generation
        reconciler_takeover_matches = (
            actual_generation == expected_generation + 1
            and str(attempt.get("owner_kind") or "") == "reconciler"
            and str(attempt.get("owner_id") or "") == "stale-run-maintenance"
            and status in {"expired", "cancel_requested"}
        )
        if (
            status in {"succeeded", "failed", "cancelled"}
            or bool(attempt.get("lease_live"))
            or not (generation_matches or reconciler_takeover_matches)
        ):
            return False
    elif expected_generation != 0:
        return False

    cursor = await conn.execute(
        """
        select claim_token
        from run_stale_reconciliation_claims
        where tenant_id = %s and run_id = %s and claim_token = %s
          and expected_attempt_id is not distinct from %s
          and expected_owner_generation = %s
          and claim_deadline_at > clock_timestamp()
        """ + (" for update" if for_update else ""),
        (
            claim["tenant_id"],
            claim["run_id"],
            claim["claim_token"],
            claim.get("expected_attempt_id"),
            int(claim["expected_owner_generation"]),
        ),
    )
    return await cursor.fetchone() is not None


async def release_stale_run_reconciliation_claim(
    conn: AsyncConnection, *, claim: dict[str, Any]
) -> bool:
    cursor = await conn.execute(
        """
        delete from run_stale_reconciliation_claims
        where tenant_id = %s and run_id = %s and claim_token = %s
          and expected_attempt_id is not distinct from %s
          and expected_owner_generation = %s
        returning claim_token
        """,
        (
            claim["tenant_id"],
            claim["run_id"],
            claim["claim_token"],
            claim.get("expected_attempt_id"),
            int(claim["expected_owner_generation"]),
        ),
    )
    return await cursor.fetchone() is not None


class PostgresRunLifecyclePersistence:
    """Explicit adapter; every operation uses the caller's connection."""

    stage_run_terminalization = staticmethod(stage_run_terminalization)
    load_staged_terminalization = staticmethod(load_staged_terminalization)
    clear_cancel_requested_terminalization = staticmethod(clear_cancel_requested_terminalization)
    finalize_staged_terminalization = staticmethod(finalize_staged_terminalization)
    is_cancel_requested = staticmethod(is_cancel_requested)
    classify_success_commit_block = staticmethod(classify_success_commit_block)
    list_stale_run_reconciliation_candidates = staticmethod(list_stale_run_reconciliation_candidates)
    claim_stale_run_reconciliation = staticmethod(claim_stale_run_reconciliation)
    stage_stale_run_reconciliation = staticmethod(stage_stale_run_reconciliation)
    renew_stale_run_reconciliation_claim = staticmethod(renew_stale_run_reconciliation_claim)
    stale_run_reconciliation_claim_is_current = staticmethod(stale_run_reconciliation_claim_is_current)
    release_stale_run_reconciliation_claim = staticmethod(release_stale_run_reconciliation_claim)

    async def complete_run(self, conn: AsyncConnection, *, tenant_id: str, run_id: str, result_json: dict[str, Any], observability: tuple[int | None, int, int, int, int] | None = None) -> bool:
        # Application supplies observability explicitly to preserve policy ownership.
        return await complete_run(
            conn, tenant_id=tenant_id, run_id=run_id, result_json=result_json,
            observability=observability or (None, 0, 0, 0, 0),
        )
