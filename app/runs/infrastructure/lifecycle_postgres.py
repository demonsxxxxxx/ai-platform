"""SQL-only primitives for Run lifecycle transitions."""

from __future__ import annotations

from typing import Any

from psycopg import AsyncConnection

from app.platform.postgres.errors import RepositoryConflictError
from app.platform.postgres.limits import (
    PersistenceSizeLimitError,
    RUN_RESULT_MAX_BYTES,
    ensure_json_size,
)
from app.runs.domain.terminalization import TERMINAL_RUN_STATUSES
from app.runs.infrastructure.steps_postgres import _cancel_open_run_steps, _fail_open_run_steps


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


async def mark_run_running(conn: AsyncConnection, *, tenant_id: str, run_id: str) -> dict[str, Any] | None:
    cursor = await conn.execute(
        """
        update runs set status = 'running', started_at = coalesce(started_at, now())
        from sessions
        where runs.tenant_id = %s and runs.id = %s and runs.status = 'queued'
          and sessions.id = runs.session_id and sessions.tenant_id = runs.tenant_id
          and sessions.workspace_id = runs.workspace_id and sessions.user_id = runs.user_id
          and sessions.agent_id = runs.agent_id
        returning runs.id, runs.tenant_id, runs.workspace_id, runs.user_id,
          runs.session_id, runs.agent_id, runs.execution_kind, runs.skill_id,
          runs.trace_id, runs.principal_roles, runs.principal_department_id,
          runs.auth_source, runs.admitted_agent_profile_revision,
          runs.admitted_agent_profile_hash,
          sessions.admitted_agent_profile_revision as session_admitted_agent_profile_revision,
          sessions.admitted_agent_profile_hash as session_admitted_agent_profile_hash,
          runs.input_json
        """,
        (tenant_id, run_id),
    )
    row = await cursor.fetchone()
    return dict(row) if row is not None else None


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
    if row is None or str(row.get("status") or "") in TERMINAL_RUN_STATUSES:
        return "stale_terminal_state"
    if row.get("cancel_requested_at") or str(row.get("terminalization_target") or "") in {"cancel_requested", "cancelled"}:
        return "cancel_requested"
    return "stale_terminal_state"


async def list_stale_run_reconciliation_candidates(
    conn: AsyncConnection, *, stale_after_seconds: int,
    cancel_requested_after_seconds: int | None = None, limit: int,
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


class PostgresRunLifecyclePersistence:
    """Explicit adapter; every operation uses the caller's connection."""

    stage_run_terminalization = staticmethod(stage_run_terminalization)
    load_staged_terminalization = staticmethod(load_staged_terminalization)
    clear_cancel_requested_terminalization = staticmethod(clear_cancel_requested_terminalization)
    finalize_staged_terminalization = staticmethod(finalize_staged_terminalization)
    mark_run_running = staticmethod(mark_run_running)
    is_cancel_requested = staticmethod(is_cancel_requested)
    classify_success_commit_block = staticmethod(classify_success_commit_block)
    list_stale_run_reconciliation_candidates = staticmethod(list_stale_run_reconciliation_candidates)

    async def complete_run(self, conn: AsyncConnection, *, tenant_id: str, run_id: str, result_json: dict[str, Any], observability: tuple[int | None, int, int, int, int] | None = None) -> bool:
        # Application supplies observability explicitly to preserve policy ownership.
        return await complete_run(
            conn, tenant_id=tenant_id, run_id=run_id, result_json=result_json,
            observability=observability or (None, 0, 0, 0, 0),
        )
