from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import Any

from psycopg import AsyncConnection


_USER_SUMMARY_COLUMNS = """
  u.id as user_id,
  u.display_name,
  u.status,
  u.created_at,
  coalesce(s.session_count, 0) as session_count,
  coalesce(r.run_count, 0) as run_count,
  coalesce(r.queued_run_count, 0) as queued_run_count,
  coalesce(r.running_run_count, 0) as running_run_count,
  coalesce(r.succeeded_run_count, 0) as succeeded_run_count,
  coalesce(r.failed_run_count, 0) as failed_run_count,
  coalesce(r.cancelled_run_count, 0) as cancelled_run_count,
  greatest(u.created_at, s.last_session_at, r.last_run_at) as last_activity_at
"""

_USER_ACTIVITY_CTES = """
with session_activity as (
  select
    user_id,
    count(*) as session_count,
    max(updated_at) as last_session_at
  from sessions
  where tenant_id = %s
  group by user_id
), run_activity as (
  select
    user_id,
    count(*) as run_count,
    count(*) filter (where status = 'queued') as queued_run_count,
    count(*) filter (where status = 'running') as running_run_count,
    count(*) filter (where status = 'succeeded') as succeeded_run_count,
    count(*) filter (where status = 'failed') as failed_run_count,
    count(*) filter (where status = 'cancelled') as cancelled_run_count,
    max(created_at) as last_run_at
  from runs
  where tenant_id = %s
  group by user_id
)
"""


async def _list_users(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    search: str | None,
    offset: int,
    limit: int,
) -> tuple[list[dict[str, Any]], int]:
    search_pattern = f"%{search}%" if search else None
    cursor = await conn.execute(
        _USER_ACTIVITY_CTES
        + """
select
"""
        + _USER_SUMMARY_COLUMNS
        + """
from users u
left join session_activity s on s.user_id = u.id
left join run_activity r on r.user_id = u.id
where u.tenant_id = %s
  and (
    %s::text is null
    or u.id ilike %s
    or u.display_name ilike %s
  )
order by last_activity_at desc, u.id asc
offset %s
limit %s
""",
        (
            tenant_id,
            tenant_id,
            tenant_id,
            search_pattern,
            search_pattern,
            search_pattern,
            offset,
            limit,
        ),
    )
    rows = [dict(row) for row in await cursor.fetchall()]
    count_cursor = await conn.execute(
        """
        select count(*) as total
        from users
        where tenant_id = %s
          and (
            %s::text is null
            or id ilike %s
            or display_name ilike %s
          )
        """,
        (tenant_id, search_pattern, search_pattern, search_pattern),
    )
    count_row = await count_cursor.fetchone()
    return rows, int(count_row["total"] if count_row else 0)


async def _get_user_summary(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    user_id: str,
) -> dict[str, Any] | None:
    cursor = await conn.execute(
        _USER_ACTIVITY_CTES
        + """
select
"""
        + _USER_SUMMARY_COLUMNS
        + """
from users u
left join session_activity s on s.user_id = u.id
left join run_activity r on r.user_id = u.id
where u.tenant_id = %s and u.id = %s
""",
        (tenant_id, tenant_id, tenant_id, user_id),
    )
    row = await cursor.fetchone()
    return dict(row) if row else None


async def _list_user_sessions(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    user_id: str,
    limit: int,
) -> list[dict[str, Any]]:
    cursor = await conn.execute(
        """
        select
          s.id as session_id,
          s.workspace_id,
          s.agent_id,
          s.status,
          s.purpose,
          s.created_at,
          s.updated_at,
          count(r.id) as run_count,
          count(r.id) filter (where r.status = 'failed') as failed_run_count,
          max(r.created_at) as last_run_at
        from sessions s
        left join runs r
          on r.tenant_id = s.tenant_id
         and r.session_id = s.id
         and r.user_id = s.user_id
        where s.tenant_id = %s and s.user_id = %s
        group by
          s.id, s.workspace_id, s.agent_id, s.status, s.purpose,
          s.created_at, s.updated_at
        order by greatest(s.updated_at, max(r.created_at)) desc, s.id desc
        limit %s
        """,
        (tenant_id, user_id, limit),
    )
    return [dict(row) for row in await cursor.fetchall()]


async def _list_user_runs(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    user_id: str,
    limit: int,
) -> list[dict[str, Any]]:
    cursor = await conn.execute(
        """
        select
          id as run_id,
          session_id,
          workspace_id,
          status,
          agent_id,
          execution_kind,
          skill_id,
          error_code,
          error_message,
          created_at,
          queued_at,
          started_at,
          finished_at
        from runs
        where tenant_id = %s and user_id = %s
        order by created_at desc, id desc
        limit %s
        """,
        (tenant_id, user_id, limit),
    )
    return [dict(row) for row in await cursor.fetchall()]


async def _list_user_audit(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    user_id: str,
    limit: int,
) -> list[dict[str, Any]]:
    cursor = await conn.execute(
        """
        select
          a.id as audit_id,
          a.user_id as actor_user_id,
          a.action,
          a.target_type,
          a.target_id,
          a.trace_id,
          case
            when a.target_type = 'run' and exists (
              select 1 from runs owned_run
              where owned_run.tenant_id = a.tenant_id
                and owned_run.user_id = %s
                and owned_run.id = a.target_id
            ) then a.target_id
            else (
              select traced_run.id from runs traced_run
              where traced_run.tenant_id = a.tenant_id
                and traced_run.user_id = %s
                and a.trace_id is not null
                and a.trace_id <> ''
                and traced_run.trace_id = a.trace_id
              order by traced_run.created_at desc, traced_run.id desc
              limit 1
            )
          end as run_id,
          case
            when a.target_type = 'session' and exists (
              select 1 from sessions owned_session
              where owned_session.tenant_id = a.tenant_id
                and owned_session.user_id = %s
                and owned_session.id = a.target_id
            ) then a.target_id
            else null
          end as session_id,
          a.created_at
        from audit_logs a
        where a.tenant_id = %s
          and (
            a.user_id = %s
            or (a.target_type = 'user' and a.target_id = %s)
            or (
              a.target_type = 'run'
              and exists (
                select 1 from runs owned_run
                where owned_run.tenant_id = a.tenant_id
                  and owned_run.user_id = %s
                  and owned_run.id = a.target_id
              )
            )
            or (
              a.target_type = 'session'
              and exists (
                select 1 from sessions owned_session
                where owned_session.tenant_id = a.tenant_id
                  and owned_session.user_id = %s
                  and owned_session.id = a.target_id
              )
            )
            or (
              a.trace_id is not null
              and a.trace_id <> ''
              and exists (
                select 1 from runs traced_run
                where traced_run.tenant_id = a.tenant_id
                  and traced_run.user_id = %s
                  and traced_run.trace_id = a.trace_id
              )
            )
          )
        order by a.created_at desc, a.id desc
        limit %s
        """,
        (
            user_id,
            user_id,
            user_id,
            tenant_id,
            user_id,
            user_id,
            user_id,
            user_id,
            user_id,
            limit,
        ),
    )
    return [dict(row) for row in await cursor.fetchall()]


class PostgresAdminUserDiagnosticsStore:
    """Read-only, tenant-bound cross-domain projection for administrator support."""

    def __init__(
        self,
        transaction_factory: Callable[
            [], AbstractAsyncContextManager[AsyncConnection]
        ],
    ) -> None:
        self._transaction_factory = transaction_factory

    async def list_users(
        self,
        *,
        tenant_id: str,
        search: str | None,
        offset: int,
        limit: int,
    ) -> tuple[list[dict[str, Any]], int]:
        async with self._transaction_factory() as conn:
            return await _list_users(
                conn,
                tenant_id=tenant_id,
                search=search,
                offset=offset,
                limit=limit,
            )

    async def get_diagnostics(
        self,
        *,
        tenant_id: str,
        user_id: str,
        session_limit: int,
        run_limit: int,
        audit_limit: int,
    ) -> dict[str, Any] | None:
        async with self._transaction_factory() as conn:
            user = await _get_user_summary(
                conn,
                tenant_id=tenant_id,
                user_id=user_id,
            )
            if user is None:
                return None
            sessions = await _list_user_sessions(
                conn,
                tenant_id=tenant_id,
                user_id=user_id,
                limit=session_limit,
            )
            runs = await _list_user_runs(
                conn,
                tenant_id=tenant_id,
                user_id=user_id,
                limit=run_limit,
            )
            audit = await _list_user_audit(
                conn,
                tenant_id=tenant_id,
                user_id=user_id,
                limit=audit_limit,
            )
            return {
                "user": user,
                "sessions": sessions,
                "runs": runs,
                "audit": audit,
            }


__all__ = ["PostgresAdminUserDiagnosticsStore"]
