"""PostgreSQL persistence for Run identity, admission, and terminal intent."""

from __future__ import annotations

import json
from typing import Any, Protocol

from psycopg import AsyncConnection

from app.platform.postgres.errors import RepositoryConflictError
from app.runs.application.cancellation import (
    CancelRequestAuthority,
    CancelRequestResult,
)
from app.runs.domain.terminalization import RunTerminalizationProgress


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


class _AppendRunEvent(Protocol):
    async def __call__(
        self,
        conn: AsyncConnection,
        *,
        tenant_id: str,
        run_id: str,
        trace_id: str | None,
        event_type: str,
        stage: str,
        message: str,
        payload: dict[str, Any],
    ) -> str: ...


class _AppendAuditLog(Protocol):
    async def __call__(
        self,
        conn: AsyncConnection,
        *,
        tenant_id: str,
        user_id: str,
        action: str,
        target_type: str,
        target_id: str,
        trace_id: str | None,
        payload_json: dict[str, Any],
    ) -> str: ...


class _ListActiveSandboxLeases(Protocol):
    async def __call__(
        self,
        conn: AsyncConnection,
        *,
        tenant_id: str,
        run_id: str,
    ) -> list[dict[str, Any]]: ...


class PostgresRunCancellationPersistence:
    def __init__(
        self,
        *,
        append_event: _AppendRunEvent,
        append_audit_log: _AppendAuditLog,
        list_active_sandbox_leases: _ListActiveSandboxLeases,
    ) -> None:
        self._append_event = append_event
        self._append_audit_log = append_audit_log
        self._list_active_sandbox_leases = list_active_sandbox_leases

    async def begin_owner_request(
        self,
        conn: AsyncConnection,
        *,
        tenant_id: str,
        run_id: str,
        owner_user_id: str,
    ) -> CancelRequestAuthority | None:
        cursor = await conn.execute(
            """
            with eligible_run as (
              select id, tenant_id, status, trace_id,
                     cancel_requested_at is null as cancel_requested_newly
              from runs
              where tenant_id = %s
                and id = %s
                and user_id = %s
                and (
                  status in ('queued', 'running')
                  or (
                    status = 'cancelled'
                    and exists (
                      select 1
                      from sandbox_leases
                      where sandbox_leases.tenant_id = runs.tenant_id
                        and sandbox_leases.run_id = runs.id
                        and sandbox_leases.status = 'active'
                    )
                  )
                )
              for update
            )
            update runs
            set
              cancel_requested_at = coalesce(cancel_requested_at, now()),
              cancel_requested_by = coalesce(cancel_requested_by, %s)
            from eligible_run
            where runs.tenant_id = eligible_run.tenant_id
              and runs.id = eligible_run.id
            returning runs.id, runs.status, eligible_run.trace_id,
                      eligible_run.cancel_requested_newly
            """,
            (tenant_id, run_id, owner_user_id, owner_user_id),
        )
        row = await cursor.fetchone()
        if not row:
            return None
        newly_requested = bool(row.get("cancel_requested_newly"))
        if newly_requested:
            await self._append_event(
                conn,
                tenant_id=tenant_id,
                run_id=run_id,
                trace_id=row.get("trace_id"),
                event_type="cancel_requested",
                stage="control",
                message="已请求取消",
                payload={
                    "visible_to_user": True,
                    "severity": "warning",
                    "requested_by": owner_user_id,
                },
            )
        target_status = "cancelled" if row["status"] == "queued" else "cancel_requested"
        await _stage_run_tool_permission_terminalization(
            conn,
            tenant_id=tenant_id,
            run_id=run_id,
            target_status=target_status,
            terminal_reason="run_cancel_requested",
        )
        return CancelRequestAuthority(
            run_id=run_id,
            prior_status=str(row["status"]),
            trace_ref=str(row.get("trace_id") or "") or None,
            target_user_id=owner_user_id,
            actor_user_id=owner_user_id,
            requested_by_role="owner",
            source="user",
            newly_requested=newly_requested,
        )

    async def begin_admin_request(
        self,
        conn: AsyncConnection,
        *,
        tenant_id: str,
        run_id: str,
        admin_user_id: str,
    ) -> CancelRequestAuthority | None:
        cursor = await conn.execute(
            """
            with eligible_run as (
              select id, tenant_id, status, user_id, trace_id,
                     cancel_requested_at is null as cancel_requested_newly
              from runs
              where tenant_id = %s
                and id = %s
                and (
                  status in ('queued', 'running')
                  or (
                    status = 'cancelled'
                    and exists (
                      select 1
                      from sandbox_leases
                      where sandbox_leases.tenant_id = runs.tenant_id
                        and sandbox_leases.run_id = runs.id
                        and sandbox_leases.status = 'active'
                    )
                  )
                )
              for update
            )
            update runs
            set
              cancel_requested_at = coalesce(cancel_requested_at, now()),
              cancel_requested_by = coalesce(cancel_requested_by, %s)
            from eligible_run
            where runs.tenant_id = eligible_run.tenant_id
              and runs.id = eligible_run.id
            returning runs.id, runs.status, eligible_run.user_id, eligible_run.trace_id,
                      eligible_run.cancel_requested_newly
            """,
            (tenant_id, run_id, admin_user_id),
        )
        row = await cursor.fetchone()
        if not row:
            return None
        newly_requested = bool(row.get("cancel_requested_newly"))
        if newly_requested:
            await self._append_event(
                conn,
                tenant_id=tenant_id,
                run_id=run_id,
                trace_id=row.get("trace_id"),
                event_type="cancel_requested",
                stage="control",
                message="管理员已请求取消",
                payload={
                    "visible_to_user": True,
                    "severity": "warning",
                    "requested_by": admin_user_id,
                    "requested_by_role": "admin",
                    "target_user_id": row.get("user_id"),
                },
            )
        target_status = "cancelled" if row["status"] == "queued" else "cancel_requested"
        await _stage_run_tool_permission_terminalization(
            conn,
            tenant_id=tenant_id,
            run_id=run_id,
            target_status=target_status,
            terminal_reason="run_cancel_requested",
        )
        return CancelRequestAuthority(
            run_id=run_id,
            prior_status=str(row["status"]),
            trace_ref=str(row.get("trace_id") or "") or None,
            target_user_id=str(row["user_id"]),
            actor_user_id=admin_user_id,
            requested_by_role="admin",
            source="system",
            newly_requested=newly_requested,
        )

    async def finish_request(
        self,
        conn: AsyncConnection,
        *,
        tenant_id: str,
        authority: CancelRequestAuthority,
        progress: RunTerminalizationProgress | None,
    ) -> CancelRequestResult:
        active_leases = await self._list_active_sandbox_leases(
            conn,
            tenant_id=tenant_id,
            run_id=authority.run_id,
        )
        status = (
            progress.status
            if progress is not None and progress.is_terminal()
            else "cancelled"
            if authority.prior_status == "cancelled"
            else "cancel_requested"
        )
        if authority.requested_by_role == "admin":
            action = "admin.run.cancel"
            payload_json = {
                "run_id": authority.run_id,
                "target_user_id": authority.target_user_id,
                "result_status": status,
            }
        else:
            action = "run.cancel"
            payload_json = {
                "run_id": authority.run_id,
                "result_status": status,
                "requested_by_role": "owner",
            }
        await self._append_audit_log(
            conn,
            tenant_id=tenant_id,
            user_id=authority.actor_user_id,
            action=action,
            target_type="run",
            target_id=authority.run_id,
            trace_id=authority.trace_ref,
            payload_json=payload_json,
        )
        return CancelRequestResult(
            run_id=authority.run_id,
            status=status,
            trace_ref=authority.trace_ref,
            active_sandbox_leases=tuple(active_leases),
            initial_terminalization_progress=progress,
        )
