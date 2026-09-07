"""Artifact retention selection and ACL-scoped read persistence."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from psycopg import AsyncConnection


async def reserve_provisional_artifact_cleanup(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    run_id: str,
    storage_key: str,
) -> str:
    """Own an object key durably before a reconciliation writer can mutate it."""

    artifact_id = f"art_cleanup_{hashlib.sha256(storage_key.encode('utf-8')).hexdigest()[:32]}"
    outbox_id = f"objdel_{artifact_id}"
    await conn.execute(
        """
        insert into artifacts(
          id, tenant_id, run_id, artifact_type, label, content_type, storage_key,
          size_bytes, manifest_json, lifecycle_state, delete_requested_at
        )
        values (
          %s, %s, null, 'reconciliation_provisional', 'Pending artifact cleanup',
          'application/octet-stream', %s, 0,
          jsonb_build_object(
            'provisional_reconciliation_cleanup', true,
            'expected_run_id', %s::text
          ),
          'delete_pending', now()
        )
        on conflict (id) do nothing
        """,
        (artifact_id, tenant_id, storage_key, run_id),
    )
    artifact = await (
        await conn.execute(
            """
            select id
            from artifacts
            where id = %s and tenant_id = %s and run_id is null
              and storage_key = %s
              and lifecycle_state = 'delete_pending'
              and manifest_json @> jsonb_build_object(
                'provisional_reconciliation_cleanup', true,
                'expected_run_id', %s::text
              )
            for update
            """,
            (artifact_id, tenant_id, storage_key, run_id),
        )
    ).fetchone()
    if artifact is None:
        raise RuntimeError("provisional_artifact_cleanup_conflict")
    await conn.execute(
        """
        insert into object_deletion_outbox(
          id, tenant_id, target_type, artifact_id, file_id, storage_key,
          state, available_at
        )
        values (%s, %s, 'artifact', %s, null, %s, 'pending', now() + interval '15 minutes')
        on conflict (tenant_id, artifact_id) do update
        set state = 'pending',
            attempts = 0,
            available_at = now() + interval '15 minutes',
            leased_at = null,
            receipt_at = null,
            dead_letter_at = null,
            reconcile_required = false,
            last_error_code = null,
            updated_at = now()
        where object_deletion_outbox.id = excluded.id
          and object_deletion_outbox.storage_key = excluded.storage_key
          and object_deletion_outbox.state in ('pending', 'failed', 'dead_letter', 'deleted')
        """,
        (outbox_id, tenant_id, artifact_id, storage_key),
    )
    receipt = await (
        await conn.execute(
            """
            select id
            from object_deletion_outbox
            where id = %s and tenant_id = %s and target_type = 'artifact'
              and artifact_id = %s and storage_key = %s and state = 'pending'
            for update
            """,
            (outbox_id, tenant_id, artifact_id, storage_key),
        )
    ).fetchone()
    if receipt is None:
        raise RuntimeError("provisional_artifact_cleanup_receipt_conflict")
    return artifact_id


async def promote_provisional_artifact_cleanup(
    conn: AsyncConnection,
    *,
    artifact_id: str,
    tenant_id: str,
    run_id: str,
    storage_key: str,
) -> bool:
    """Retire provisional cleanup only inside the final artifact transaction."""

    receipt = await (
        await conn.execute(
            """
            select outbox.id
            from object_deletion_outbox outbox
            join artifacts
              on artifacts.id = outbox.artifact_id
             and artifacts.tenant_id = outbox.tenant_id
             and artifacts.storage_key = outbox.storage_key
            where outbox.id = %s and outbox.tenant_id = %s
              and outbox.target_type = 'artifact' and outbox.state = 'pending'
              and artifacts.run_id is null and artifacts.storage_key = %s
              and artifacts.lifecycle_state = 'delete_pending'
              and artifacts.manifest_json @> jsonb_build_object(
                'provisional_reconciliation_cleanup', true,
                'expected_run_id', %s::text
              )
            for update of outbox, artifacts
            """,
            (f"objdel_{artifact_id}", tenant_id, storage_key, run_id),
        )
    ).fetchone()
    if receipt is None:
        return False
    await conn.execute(
        "delete from object_deletion_outbox where id = %s and tenant_id = %s",
        (str(receipt["id"]), tenant_id),
    )
    cursor = await conn.execute(
        """
        delete from artifacts
        where id = %s and tenant_id = %s and run_id is null and storage_key = %s
          and lifecycle_state = 'delete_pending'
          and manifest_json @> jsonb_build_object(
            'provisional_reconciliation_cleanup', true,
            'expected_run_id', %s::text
          )
        returning id
        """,
        (artifact_id, tenant_id, storage_key, run_id),
    )
    return await cursor.fetchone() is not None


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
              delete_requested_at = coalesce(delete_requested_at, now()),
              manifest_json = artifacts.manifest_json || jsonb_build_object(
                'retention_artifact_cleanup', true,
                'deletion_owner_run_id', artifacts.run_id
              ),
              run_id = null
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
          id, tenant_id, target_type, artifact_id, file_id, storage_key, state, available_at
        )
        select 'objdel_' || id, tenant_id, 'artifact', id, null, storage_key, 'pending', now()
        from tombstoned
        on conflict (tenant_id, artifact_id) do update
        set storage_key = excluded.storage_key,
            state = case
              when object_deletion_outbox.state = 'deleted' then 'deleted'
              else object_deletion_outbox.state
            end,
            available_at = case
              when object_deletion_outbox.state = 'pending' then now()
              else object_deletion_outbox.available_at
            end,
            updated_at = now()
        returning id, tenant_id, target_type, artifact_id, file_id,
                  state, attempts, lease_generation, created_at
        """,
        (json.dumps(candidate_ids),),
    )
    return list(await cursor.fetchall())


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

    order_column = (
        "artifacts.created_at"
        if sort_by not in {"file_name", "file_size"}
        else {
            "file_name": "artifacts.label",
            "file_size": "artifacts.size_bytes",
        }[sort_by]
    )
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


async def list_revealed_session_artifacts(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    user_id: str,
    session_id: str,
) -> list[dict[str, Any]]:
    """Return an exact session's active artifacts in one deterministic query."""

    cursor = await conn.execute(
        """
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
        where artifacts.tenant_id = %s
          and artifacts.lifecycle_state = 'active'
          and (artifacts.expires_at is null or artifacts.expires_at > now())
          and runs.user_id = %s
          and runs.session_id = %s
          and sessions.status = 'active'
        order by artifacts.created_at desc, artifacts.id desc
        """,
        (tenant_id, user_id, session_id),
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
