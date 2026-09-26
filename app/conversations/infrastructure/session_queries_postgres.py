"""Session queries persistence. Callers own the connection and transaction."""

from __future__ import annotations

from app.platform.postgres.errors import RepositoryNotFoundError
from psycopg import AsyncConnection
from typing import Any


async def ensure_workspace(conn: AsyncConnection, *, tenant_id: str, workspace_id: str) -> None:
    cursor = await conn.execute(
        """
        select 1
        from workspaces
        where tenant_id = %s and id = %s and status = 'active'
        """,
        (tenant_id, workspace_id),
    )
    if await cursor.fetchone() is None:
        raise RepositoryNotFoundError("workspace_not_found")


async def get_authorized_session(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    user_id: str,
    session_id: str,
    workspace_id: str | None = None,
    for_update: bool = False,
) -> dict[str, Any] | None:
    lock_clause = " for update" if for_update else ""
    workspace_filter = "and workspace_id = %s" if workspace_id else ""
    params: list[Any] = [tenant_id, session_id, user_id]
    if workspace_id:
        params.append(workspace_id)
    if for_update:
        cursor = await conn.execute(
            f"""
            select *
            from sessions
            where tenant_id = %s
              and id = %s
              and user_id = %s
              and status = 'active'
              {workspace_filter}
            {lock_clause}
            """,
            tuple(params),
        )
        return await cursor.fetchone()
    cursor = await conn.execute(
        f"""
        select sessions.*, latest_run.input_json as latest_run_input_json
        from sessions
        left join lateral (
          select runs.input_json
          from runs
          where runs.tenant_id = sessions.tenant_id
            and runs.workspace_id = sessions.workspace_id
            and runs.user_id = sessions.user_id
            and runs.session_id = sessions.id
          order by runs.session_generation desc nulls last,
                   runs.created_at desc,
                   runs.id desc
          limit 1
        ) latest_run on true
        where sessions.tenant_id = %s
          and sessions.id = %s
          and sessions.user_id = %s
          and sessions.status = 'active'
          {"and sessions.workspace_id = %s" if workspace_id else ""}
        """,
        tuple(params),
    )
    return await cursor.fetchone()


async def get_authorized_context_target_session(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    workspace_id: str,
    user_id: str,
    session_id: str,
) -> dict[str, Any] | None:
    """Load a target session only when it matches the source run owner scope."""
    cursor = await conn.execute(
        """
        select id, tenant_id, workspace_id, user_id, agent_id, status
        from sessions
        where tenant_id = %s
          and workspace_id = %s
          and user_id = %s
          and id = %s
          and status = 'active'
        """,
        (tenant_id, workspace_id, user_id, session_id),
    )
    return await cursor.fetchone()


async def list_authorized_session_runs(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    user_id: str,
    session_id: str,
    workspace_id: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    workspace_filter = "and runs.workspace_id = %s" if workspace_id else ""
    params: list[Any] = [tenant_id, user_id, session_id]
    if workspace_id:
        params.append(workspace_id)
    params.append(limit)
    cursor = await conn.execute(
        f"""
        select runs.id, runs.trace_id, runs.schema_version, runs.agent_id,
               runs.execution_kind, runs.skill_id,
               runs.status, runs.error_code, runs.error_message, runs.created_at, runs.queued_at,
               runs.started_at, runs.finished_at, runs.result_json,
               runs.session_generation, queue_admission.queue_admission_ordinal
        from runs
        left join lateral (
          select case
            when run_events.payload_json->>'queue_admission_ordinal' ~ '^[0-9]+$'
             and length(run_events.payload_json->>'queue_admission_ordinal') <= 19
             and (
               length(run_events.payload_json->>'queue_admission_ordinal') < 19
               or run_events.payload_json->>'queue_admission_ordinal' <= '9223372036854775807'
             )
            then (run_events.payload_json->>'queue_admission_ordinal')::bigint
            else null
          end as queue_admission_ordinal
          from run_events
          where run_events.tenant_id = runs.tenant_id
            and run_events.run_id = runs.id
            and run_events.event_type = 'queued'
          order by run_events.sequence desc
          limit 1
        ) queue_admission on true
        where runs.tenant_id = %s
          and runs.user_id = %s
          and runs.session_id = %s
          {workspace_filter}
        -- A non-null generation is the sole current-run authority.  Legacy
        -- unordered rows remain display-only and never outrank it.
        order by runs.session_generation desc nulls last,
                 runs.created_at desc,
                 queue_admission.queue_admission_ordinal desc nulls last,
                 runs.queued_at desc nulls last,
                 runs.id desc
        limit %s
        """,
        tuple(params),
    )
    return list(await cursor.fetchall())


async def get_latest_authorized_session_run_input(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    workspace_id: str,
    user_id: str,
    session_id: str,
) -> dict[str, Any] | None:
    """Read the latest run input for an active principal-owned session scope."""

    cursor = await conn.execute(
        """
        select runs.input_json
        from runs
        join sessions on sessions.id = runs.session_id
          and sessions.tenant_id = runs.tenant_id
          and sessions.workspace_id = runs.workspace_id
          and sessions.user_id = runs.user_id
          and sessions.agent_id = runs.agent_id
        where runs.tenant_id = %s
          and runs.workspace_id = %s
          and runs.user_id = %s
          and runs.session_id = %s
          and sessions.status = 'active'
        order by runs.session_generation desc nulls last,
                 runs.created_at desc,
                 runs.id desc
        limit 1
        """,
        (tenant_id, workspace_id, user_id, session_id),
    )
    row = await cursor.fetchone()
    if row is None:
        return None
    input_json = row.get("input_json")
    return input_json if isinstance(input_json, dict) else None
