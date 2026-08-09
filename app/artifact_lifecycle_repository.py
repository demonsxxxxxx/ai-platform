"""Artifact ACL reads and bounded physical-deletion persistence."""

from __future__ import annotations

import json
from typing import Any

from psycopg import AsyncConnection


async def queue_expired_artifacts_for_deletion(
    conn: AsyncConnection,
    *,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Lock a safe batch, then recheck references in a fresh statement snapshot."""

    resolved_limit = max(1, min(int(limit), 200))
    cursor = await conn.execute(
        """
        select artifacts.id, artifacts.tenant_id, artifacts.storage_key
        from artifacts
        join runs on runs.id = artifacts.run_id and runs.tenant_id = artifacts.tenant_id
        join sessions on sessions.id = runs.session_id and sessions.tenant_id = runs.tenant_id
        where artifacts.lifecycle_state = 'active'
          and artifacts.expires_at is not null
          and artifacts.expires_at <= now()
          and runs.status not in ('queued', 'running')
          and sessions.status <> 'active'
          and not exists (
            select 1 from run_context_snapshots snapshots
            where snapshots.tenant_id = artifacts.tenant_id
              and snapshots.included_artifact_ids ? artifacts.id
          )
          and not exists (
            select 1 from audit_logs audit
            where audit.tenant_id = artifacts.tenant_id
              and audit.target_id = artifacts.id
          )
        order by artifacts.expires_at asc, artifacts.created_at asc, artifacts.id asc
        limit %s
        for update of artifacts skip locked
        """,
        (resolved_limit,),
    )
    candidates = list(await cursor.fetchall())
    if not candidates:
        return []

    candidate_ids = [str(item["id"]) for item in candidates]
    cursor = await conn.execute(
        """
        with requested as (
          select jsonb_array_elements_text(%s::jsonb) as id
        ), tombstoned as (
          update artifacts
          set lifecycle_state = 'delete_pending',
              delete_requested_at = coalesce(delete_requested_at, now())
          from requested
          where artifacts.id = requested.id
            and artifacts.lifecycle_state = 'active'
            and artifacts.expires_at is not null
            and artifacts.expires_at <= now()
            and exists (
              select 1
              from runs
              join sessions on sessions.id = runs.session_id and sessions.tenant_id = runs.tenant_id
              where runs.id = artifacts.run_id
                and runs.tenant_id = artifacts.tenant_id
                and runs.status not in ('queued', 'running')
                and sessions.status <> 'active'
            )
            and not exists (
              select 1 from run_context_snapshots snapshots
              where snapshots.tenant_id = artifacts.tenant_id
                and snapshots.included_artifact_ids ? artifacts.id
            )
            and not exists (
              select 1 from audit_logs audit
              where audit.tenant_id = artifacts.tenant_id
                and audit.target_id = artifacts.id
            )
          returning artifacts.id, artifacts.tenant_id, artifacts.storage_key
        )
        insert into object_deletion_outbox(
          id, tenant_id, artifact_id, storage_key, state, available_at
        )
        select 'objdel_' || id, tenant_id, id, storage_key, 'pending', now()
        from tombstoned
        on conflict (tenant_id, artifact_id) do update
        set storage_key = excluded.storage_key,
            state = case
              when object_deletion_outbox.state = 'deleted' then 'deleted'
              else 'pending'
            end,
            available_at = now(),
            updated_at = now()
        returning id, tenant_id, artifact_id, state, attempts, created_at
        """,
        (json.dumps(candidate_ids),),
    )
    return list(await cursor.fetchall())



async def claim_object_deletions(
    conn: AsyncConnection,
    *,
    limit: int = 50,
) -> list[dict[str, Any]]:
    resolved_limit = max(1, min(int(limit), 200))
    cursor = await conn.execute(
        """
        with candidates as (
          select id
          from object_deletion_outbox
          where (
              state in ('pending', 'failed') and available_at <= now()
            ) or (
              state = 'processing' and leased_at <= now() - interval '5 minutes'
            )
          order by available_at asc, created_at asc, id asc
          limit %s
          for update skip locked
        )
        update object_deletion_outbox
        set state = 'processing',
            attempts = attempts + 1,
            leased_at = now(),
            updated_at = now()
        where id in (select id from candidates)
        returning id, tenant_id, artifact_id, storage_key, attempts
        """,
        (resolved_limit,),
    )
    return list(await cursor.fetchall())


async def complete_object_deletion(
    conn: AsyncConnection,
    *,
    outbox_id: str,
    tenant_id: str,
    artifact_id: str,
) -> bool:
    cursor = await conn.execute(
        """
        update object_deletion_outbox
        set state = 'deleted', receipt_at = now(), last_error_code = null, updated_at = now()
        where id = %s and tenant_id = %s and artifact_id = %s and state = 'processing'
        returning id
        """,
        (outbox_id, tenant_id, artifact_id),
    )
    row = await cursor.fetchone()
    if row is None:
        return False
    await conn.execute(
        """
        update artifacts
        set lifecycle_state = 'deleted', deleted_at = coalesce(deleted_at, now())
        where tenant_id = %s and id = %s and lifecycle_state = 'delete_pending'
        """,
        (tenant_id, artifact_id),
    )
    return True


async def fail_object_deletion(
    conn: AsyncConnection,
    *,
    outbox_id: str,
    error_code: str,
) -> None:
    await conn.execute(
        """
        update object_deletion_outbox
        set state = 'failed',
            last_error_code = %s,
            available_at = now() + interval '1 minute',
            leased_at = null,
            updated_at = now()
        where id = %s and state = 'processing'
        """,
        (error_code[:120], outbox_id),
    )


async def purge_deleted_memory_records(
    conn: AsyncConnection,
    *,
    grace_days: int,
    limit: int = 50,
) -> list[dict[str, Any]]:
    resolved_grace = max(1, min(int(grace_days), 3650))
    resolved_limit = max(1, min(int(limit), 200))
    cursor = await conn.execute(
        """
        with candidates as (
          select memory_records.id
          from memory_records
          where memory_records.status = 'deleted'
            and memory_records.deleted_at is not null
            and memory_records.deleted_at <= now() - (%s * interval '1 day')
            and not exists (
              select 1 from sessions
              where sessions.tenant_id = memory_records.tenant_id
                and sessions.id = memory_records.session_id
                and sessions.status = 'active'
            )
            and not exists (
              select 1 from run_context_snapshots snapshots
              where snapshots.tenant_id = memory_records.tenant_id
                and snapshots.included_memory_record_ids ? memory_records.id
            )
            and not exists (
              select 1 from audit_logs audit
              where audit.tenant_id = memory_records.tenant_id
                and audit.target_id = memory_records.id
            )
          order by memory_records.deleted_at asc, memory_records.id asc
          limit %s
          for update of memory_records skip locked
        )
        delete from memory_records
        where id in (select id from candidates)
        returning id, tenant_id, workspace_id, user_id, deleted_at
        """,
        (resolved_grace, resolved_limit),
    )
    return list(await cursor.fetchall())


async def get_data_retention_backlog(conn: AsyncConnection) -> dict[str, int]:
    cursor = await conn.execute(
        """
        select
          (select count(*) from artifacts
           where lifecycle_state = 'active' and expires_at is not null and expires_at <= now()) as expired_artifacts,
          (select count(*) from artifacts where lifecycle_state = 'delete_pending') as artifact_delete_pending,
          (select count(*) from object_deletion_outbox where state <> 'deleted') as object_delete_backlog,
          (select count(*) from memory_records where status = 'deleted' and deleted_at is not null) as memory_soft_deleted
        """
    )
    row = await cursor.fetchone() or {}
    keys = (
        "expired_artifacts",
        "artifact_delete_pending",
        "object_delete_backlog",
        "memory_soft_deleted",
    )
    return {key: int(row.get(key) or 0) for key in keys}


async def get_artifact(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    artifact_id: str,
) -> dict[str, Any] | None:
    cursor = await conn.execute(
        """
        select artifacts.*
        from artifacts
        join runs on runs.id = artifacts.run_id and runs.tenant_id = artifacts.tenant_id
        where artifacts.tenant_id = %s and artifacts.id = %s
          and artifacts.lifecycle_state = 'active'
          and (artifacts.expires_at is null or artifacts.expires_at > now())
        """,
        (tenant_id, artifact_id),
    )
    return await cursor.fetchone()


async def get_authorized_artifact(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    user_id: str,
    artifact_id: str,
) -> dict[str, Any] | None:
    cursor = await conn.execute(
        """
        select artifacts.*
        from artifacts
        join runs on runs.id = artifacts.run_id and runs.tenant_id = artifacts.tenant_id
        join sessions on sessions.id = runs.session_id
          and sessions.tenant_id = runs.tenant_id
          and sessions.workspace_id = runs.workspace_id
          and sessions.user_id = runs.user_id
          and sessions.agent_id = runs.agent_id
        where artifacts.tenant_id = %s
          and artifacts.id = %s
          and runs.user_id = %s
          and sessions.status = 'active'
          and artifacts.lifecycle_state = 'active'
          and (artifacts.expires_at is null or artifacts.expires_at > now())
        """,
        (tenant_id, artifact_id, user_id),
    )
    return await cursor.fetchone()


async def get_admin_artifact(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    artifact_id: str,
) -> dict[str, Any] | None:
    cursor = await conn.execute(
        """
        select artifacts.*, runs.id as run_id, runs.user_id as target_user_id
        from artifacts
        join runs on runs.id = artifacts.run_id and runs.tenant_id = artifacts.tenant_id
        where artifacts.tenant_id = %s
          and artifacts.id = %s
          and artifacts.lifecycle_state = 'active'
          and (artifacts.expires_at is null or artifacts.expires_at > now())
        """,
        (tenant_id, artifact_id),
    )
    return await cursor.fetchone()


async def list_revealed_artifacts(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    user_id: str,
    session_id: str | None = None,
    project_id: str | None = None,
    search: str | None = None,
    sort_by: str = "created_at",
    sort_order: str = "desc",
) -> list[dict[str, Any]]:
    """Return ACL-scoped artifacts for the public revealed-files projection."""

    order_column = "artifacts.created_at" if sort_by not in {"file_name", "file_size"} else {
        "file_name": "artifacts.label",
        "file_size": "artifacts.size_bytes",
    }[sort_by]
    order_direction = "asc" if str(sort_order).lower() == "asc" else "desc"
    filters = [
        "artifacts.tenant_id = %s",
        "artifacts.lifecycle_state = 'active'",
        "(artifacts.expires_at is null or artifacts.expires_at > now())",
        "runs.user_id = %s",
        "sessions.status = 'active'",
    ]
    params: list[Any] = [tenant_id, user_id]
    if session_id:
        filters.append("runs.session_id = %s")
        params.append(session_id)
    if project_id:
        filters.append("runs.workspace_id = %s")
        params.append(project_id)
    if search:
        filters.append("(artifacts.label ilike %s or artifacts.storage_key ilike %s)")
        like = f"%{search}%"
        params.extend([like, like])
    cursor = await conn.execute(
        f"""
        select
          artifacts.id, artifacts.storage_key, artifacts.label,
          artifacts.content_type, artifacts.size_bytes, artifacts.artifact_type,
          artifacts.created_at, artifacts.trace_id, runs.id as run_id,
          runs.session_id, runs.workspace_id, runs.user_id,
          sessions.title as session_name
        from artifacts
        join runs on runs.id = artifacts.run_id and runs.tenant_id = artifacts.tenant_id
        join sessions on sessions.id = runs.session_id
          and sessions.tenant_id = runs.tenant_id
          and sessions.workspace_id = runs.workspace_id
          and sessions.user_id = runs.user_id
          and sessions.agent_id = runs.agent_id
        where {" and ".join(filters)}
        order by {order_column} {order_direction}, artifacts.created_at desc
        limit 500
        """,
        tuple(params),
    )
    return list(await cursor.fetchall())


async def list_revealed_artifact_sessions(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    user_id: str,
    project_id: str | None = None,
    search: str | None = None,
) -> list[dict[str, Any]]:
    """Return session summaries for ACL-scoped revealed artifact rows."""

    filters = [
        "artifacts.tenant_id = %s",
        "artifacts.lifecycle_state = 'active'",
        "(artifacts.expires_at is null or artifacts.expires_at > now())",
        "runs.user_id = %s",
        "sessions.status = 'active'",
    ]
    params: list[Any] = [tenant_id, user_id]
    if project_id:
        filters.append("runs.workspace_id = %s")
        params.append(project_id)
    if search:
        filters.append("(artifacts.label ilike %s or artifacts.storage_key ilike %s)")
        like = f"%{search}%"
        params.extend([like, like])
    cursor = await conn.execute(
        f"""
        select
          runs.session_id,
          max(sessions.title) as session_name,
          count(*) as file_count,
          max(artifacts.created_at) as updated_at
        from artifacts
        join runs on runs.id = artifacts.run_id and runs.tenant_id = artifacts.tenant_id
        join sessions on sessions.id = runs.session_id
          and sessions.tenant_id = runs.tenant_id
          and sessions.workspace_id = runs.workspace_id
          and sessions.user_id = runs.user_id
          and sessions.agent_id = runs.agent_id
        where {" and ".join(filters)}
        group by runs.session_id
        order by updated_at desc
        limit 200
        """,
        tuple(params),
    )
    return list(await cursor.fetchall())
