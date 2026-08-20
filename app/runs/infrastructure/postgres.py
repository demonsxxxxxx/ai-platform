"""PostgreSQL persistence for Run identity, admission, and terminal intent."""

from __future__ import annotations

import json
from typing import Any

from psycopg import AsyncConnection

from app.platform.postgres.errors import RepositoryConflictError


def _dumps_json(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False)


async def count_active_runs_for_user(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    user_id: str,
) -> int:
    cursor = await conn.execute(
        """
        select count(*) as count
        from runs
        where tenant_id = %s
          and user_id = %s
          and status in ('queued', 'running')
        """,
        (tenant_id, user_id),
    )
    row = await cursor.fetchone()
    return int(row["count"] if row else 0)


async def enforce_user_active_run_admission(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    user_id: str,
    limit: int,
) -> int:
    limit = int(limit)
    if limit <= 0:
        return 0
    await acquire_user_active_run_admission_lock(
        conn,
        tenant_id=tenant_id,
        user_id=user_id,
    )
    return await enforce_user_active_run_admission_under_lock(
        conn,
        tenant_id=tenant_id,
        user_id=user_id,
        limit=limit,
    )


async def acquire_user_active_run_admission_lock(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    user_id: str,
) -> None:
    """Acquire the transaction-scoped per-user run-admission serialization lock."""

    lock_scope = _dumps_json({"tenant_id": tenant_id, "user_id": user_id})
    await conn.execute(
        "select pg_advisory_xact_lock(hashtextextended(%s::text, 0::bigint))",
        (lock_scope,),
    )


async def enforce_user_active_run_admission_under_lock(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    user_id: str,
    limit: int,
) -> int:
    """Check the active-run limit after the caller acquired its user lock."""

    limit = int(limit)
    if limit <= 0:
        return 0
    active_count = await count_active_runs_for_user(
        conn,
        tenant_id=tenant_id,
        user_id=user_id,
    )
    if active_count >= limit:
        raise RepositoryConflictError("user_active_run_limit_exceeded")
    return active_count


async def get_active_retry_for_source_run(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    user_id: str,
    run_id: str,
) -> dict[str, Any] | None:
    cursor = await conn.execute(
        """
        select id, status
        from runs
        where tenant_id = %s
          and user_id = %s
          and copied_from_run_id = %s
          and status in ('queued', 'running')
        order by created_at desc
        limit 1
        """,
        (tenant_id, user_id, run_id),
    )
    return await cursor.fetchone()


async def get_active_resume_for_source_run(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    user_id: str,
    run_id: str,
) -> dict[str, Any] | None:
    """Return an active same-owner child run that would duplicate a resume request."""

    cursor = await conn.execute(
        """
        select id, status
        from runs
        where tenant_id = %s
          and user_id = %s
          and copied_from_run_id = %s
          and status in ('queued', 'running')
        order by created_at desc
        limit 1
        """,
        (tenant_id, user_id, run_id),
    )
    return await cursor.fetchone()


async def get_run(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    run_id: str,
    for_update: bool = False,
) -> dict[str, Any] | None:
    lock_clause = "for update of runs" if for_update else ""
    cursor = await conn.execute(
        f"""
        select runs.*,
               sessions.admitted_agent_profile_revision
                 as session_admitted_agent_profile_revision,
               sessions.admitted_agent_profile_hash
                 as session_admitted_agent_profile_hash
        from runs
        left join sessions
          on sessions.tenant_id = runs.tenant_id
         and sessions.id = runs.session_id
         and sessions.workspace_id = runs.workspace_id
         and sessions.user_id is not distinct from runs.user_id
         and sessions.agent_id = runs.agent_id
        where runs.tenant_id = %s and runs.id = %s
        {lock_clause}
        """,
        (tenant_id, run_id),
    )
    return await cursor.fetchone()


async def get_run_identity(
    conn: AsyncConnection,
    *,
    run_id: str,
    for_update: bool = False,
) -> dict[str, Any] | None:
    sql = (
        "select id, tenant_id, workspace_id, user_id, session_id, agent_id, status, "
        "context_snapshot_id from runs where id = %s"
    )
    if for_update:
        sql = f"{sql} for update"
    cursor = await conn.execute(
        sql,
        (run_id,),
    )
    return await cursor.fetchone()


async def list_stale_run_terminalization_candidates(
    conn: AsyncConnection,
    *,
    stale_after_seconds: int,
    cancel_requested_after_seconds: int | None = None,
    limit: int,
) -> list[dict[str, Any]]:
    """List ownerless stale runs without a pending executor terminal receipt."""

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
          and not exists (
            select 1 from sandbox_leases
            where sandbox_leases.tenant_id = runs.tenant_id
              and sandbox_leases.run_id = runs.id
              and sandbox_leases.executor_terminal_json is not null
              and sandbox_leases.executor_reconciliation_status <> 'finalized'
          )
        order by stale_before asc, runs.tenant_id asc, runs.id asc
        limit %s
        """,
        (bounded_cancel_staleness, bounded_staleness, bounded_limit),
    )
    return list(await cursor.fetchall())


async def _stage_run_tool_permission_terminalization(
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
    """Persist the first terminal intent while holding the owning run row first."""

    if target_status not in {"failed", "cancel_requested", "cancelled"}:
        raise ValueError("invalid_run_tool_permission_terminal_target")
    cursor = await conn.execute(
        """
        update runs
        set permission_terminalization_target = case
              when permission_terminalization_target = 'cancel_requested'
                   and %s = 'cancelled' then 'cancelled'
              else coalesce(permission_terminalization_target, %s)
            end,
            permission_terminalization_reason = case
              when permission_terminalization_target is null
                   or (permission_terminalization_target = 'cancel_requested' and %s = 'cancelled') then %s
              else permission_terminalization_reason
            end,
            permission_terminalization_result_json = case
              when permission_terminalization_target is null
                   or (permission_terminalization_target = 'cancel_requested' and %s = 'cancelled') then %s::jsonb
              else permission_terminalization_result_json
            end,
            permission_terminalization_error_code = case
              when permission_terminalization_target is null
                   or (permission_terminalization_target = 'cancel_requested' and %s = 'cancelled') then %s
              else permission_terminalization_error_code
            end,
            permission_terminalization_error_message = case
              when permission_terminalization_target is null
                   or (permission_terminalization_target = 'cancel_requested' and %s = 'cancelled') then %s
              else permission_terminalization_error_message
            end
        where tenant_id = %s
          and id = %s
          and status not in ('succeeded', 'failed', 'cancelled')
        returning id, trace_id, permission_terminalization_target,
                  permission_terminalization_reason, permission_terminalization_result_json,
                  permission_terminalization_error_code, permission_terminalization_error_message
        """,
        (
            target_status,
            target_status,
            target_status,
            terminal_reason,
            target_status,
            _dumps_json(result_json or {}),
            target_status,
            error_code,
            target_status,
            error_message,
            tenant_id,
            run_id,
        ),
    )
    return await cursor.fetchone()
