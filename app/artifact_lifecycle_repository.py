"""Artifact ACL reads and durable object-deletion lifecycle persistence."""

from __future__ import annotations

import json
from typing import Any

from psycopg import AsyncConnection


OUTBOX_TARGET_ARTIFACT = "artifact"
OUTBOX_TARGET_FILE = "file"
FILE_DELETE_PENDING_STATES = frozenset({"pending", "processing", "failed", "dead_letter"})


class FileDeletionBlockedError(RuntimeError):
    """The owned file exists but has a canonical durable reference."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class ObjectDeletionStateError(RuntimeError):
    """Persisted target and outbox state cannot be proven consistent."""


def _file_deletion_result(
    *,
    file_row: dict[str, Any],
    outbox_row: dict[str, Any],
    created: bool,
) -> dict[str, Any]:
    file_id = str(file_row.get("id") or "")
    storage_key = str(file_row.get("storage_key") or "")
    lifecycle_state = str(file_row.get("lifecycle_state") or "")
    outbox_state = str(outbox_row.get("state") or "")
    if (
        not file_id
        or not storage_key
        or str(outbox_row.get("id") or "") != f"objdel_{file_id}"
        or str(outbox_row.get("tenant_id") or "") != str(file_row.get("tenant_id") or "")
        or str(outbox_row.get("target_type") or "") != OUTBOX_TARGET_FILE
        or outbox_row.get("artifact_id") is not None
        or str(outbox_row.get("file_id") or "") != file_id
        or str(outbox_row.get("storage_key") or "") != storage_key
    ):
        raise ObjectDeletionStateError("file_deletion_outbox_identity_mismatch")
    if lifecycle_state == "delete_pending" and outbox_state not in FILE_DELETE_PENDING_STATES:
        raise ObjectDeletionStateError("file_deletion_outbox_state_mismatch")
    if lifecycle_state == "deleted" and outbox_state != "deleted":
        raise ObjectDeletionStateError("file_deletion_outbox_state_mismatch")
    if lifecycle_state not in {"delete_pending", "deleted"}:
        raise ObjectDeletionStateError("file_deletion_lifecycle_state_mismatch")
    return {
        "file_id": file_id,
        "lifecycle_state": lifecycle_state,
        "deletion_state": outbox_state,
        "reconcile_required": bool(outbox_row.get("reconcile_required")),
        "created": created,
    }


async def _existing_file_deletion(
    conn: AsyncConnection,
    *,
    file_row: dict[str, Any],
) -> dict[str, Any]:
    cursor = await conn.execute(
        """
        select id, tenant_id, target_type, artifact_id, file_id, storage_key,
               state, attempts, lease_generation, reconcile_required
        from object_deletion_outbox
        where tenant_id = %s and target_type = 'file' and file_id = %s
        order by created_at asc, id asc
        limit 2
        """,
        (str(file_row["tenant_id"]), str(file_row["id"])),
    )
    rows = list(await cursor.fetchall())
    if len(rows) != 1:
        raise ObjectDeletionStateError("file_deletion_outbox_cardinality_mismatch")
    return _file_deletion_result(file_row=file_row, outbox_row=dict(rows[0]), created=False)


async def queue_unbound_file_for_deletion(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    workspace_id: str,
    user_id: str,
    file_id: str,
) -> dict[str, Any] | None:
    """Lock one owned file and enqueue deletion only while it remains unbound."""

    cursor = await conn.execute(
        """
        select id, tenant_id, workspace_id, user_id, session_id, run_id,
               storage_key, lifecycle_state
        from files
        where tenant_id = %s and workspace_id = %s and user_id = %s and id = %s
        for update
        """,
        (tenant_id, workspace_id, user_id, file_id),
    )
    locked = await cursor.fetchone()
    if locked is None:
        return None
    file_row = dict(locked)
    lifecycle_state = str(file_row.get("lifecycle_state") or "")
    if lifecycle_state in {"delete_pending", "deleted"}:
        return await _existing_file_deletion(conn, file_row=file_row)
    if lifecycle_state != "active":
        raise ObjectDeletionStateError("file_deletion_lifecycle_state_invalid")
    if file_row.get("session_id") is not None or file_row.get("run_id") is not None:
        raise FileDeletionBlockedError("file_session_or_run_bound")

    cursor = await conn.execute(
        """
        select
          exists (
            select 1 from runs
            where runs.tenant_id = %s
              and runs.input_json @> jsonb_build_object(
                'file_ids', jsonb_build_array(%s::text)
              )
          ) as run_input_reference,
          exists (
            select 1 from run_context_snapshots snapshots
            where snapshots.tenant_id = %s
              and snapshots.included_file_ids ? %s
          ) as context_snapshot_reference,
          exists (
            select 1 from messages
            where messages.tenant_id = %s
              and messages.metadata_json @> jsonb_build_object(
                'file_ids', jsonb_build_array(%s::text)
              )
          ) as message_reference,
          exists (
            select 1 from artifacts
            where artifacts.tenant_id = %s
              and artifacts.lifecycle_state <> 'deleted'
              and (
                artifacts.manifest_json @> jsonb_build_object(
                  'source_file_id', %s::text
                )
                or artifacts.storage_key = %s
              )
          ) as artifact_reference,
          exists (
            select 1 from object_deletion_outbox outbox
            where outbox.tenant_id = %s
              and outbox.target_type = 'artifact'
              and outbox.state <> 'deleted'
              and outbox.storage_key = %s
          ) as artifact_outbox_reference,
          exists (
            select 1 from object_deletion_outbox outbox
            where outbox.tenant_id = %s
              and outbox.target_type = 'file'
              and outbox.file_id = %s
          ) as unexpected_file_outbox
        """,
        (
            tenant_id,
            file_id,
            tenant_id,
            file_id,
            tenant_id,
            file_id,
            tenant_id,
            file_id,
            str(file_row["storage_key"]),
            tenant_id,
            str(file_row["storage_key"]),
            tenant_id,
            file_id,
        ),
    )
    references = dict(await cursor.fetchone() or {})
    if bool(references.get("unexpected_file_outbox")):
        raise ObjectDeletionStateError("file_deletion_outbox_without_tombstone")
    for key, reason in (
        ("run_input_reference", "file_run_input_referenced"),
        ("context_snapshot_reference", "file_context_snapshot_referenced"),
        ("message_reference", "file_message_referenced"),
        ("artifact_reference", "file_artifact_referenced"),
        ("artifact_outbox_reference", "file_artifact_outbox_referenced"),
    ):
        if bool(references.get(key)):
            raise FileDeletionBlockedError(reason)

    cursor = await conn.execute(
        """
        update files
        set lifecycle_state = 'delete_pending',
            delete_requested_at = coalesce(delete_requested_at, now())
        where tenant_id = %s and workspace_id = %s and user_id = %s and id = %s
          and lifecycle_state = 'active' and session_id is null and run_id is null
        returning id, tenant_id, workspace_id, user_id, session_id, run_id,
                  storage_key, lifecycle_state
        """,
        (tenant_id, workspace_id, user_id, file_id),
    )
    tombstoned = await cursor.fetchone()
    if tombstoned is None:
        raise ObjectDeletionStateError("file_deletion_tombstone_conflict")
    file_row = dict(tombstoned)
    outbox_id = f"objdel_{file_id}"
    cursor = await conn.execute(
        """
        insert into object_deletion_outbox(
          id, tenant_id, target_type, artifact_id, file_id, storage_key, state, available_at
        ) values (%s, %s, 'file', null, %s, %s, 'pending', now())
        on conflict (id) do nothing
        returning id, tenant_id, target_type, artifact_id, file_id, storage_key,
                  state, attempts, lease_generation, reconcile_required
        """,
        (outbox_id, tenant_id, file_id, str(file_row["storage_key"])),
    )
    inserted = await cursor.fetchone()
    if inserted is None:
        raise ObjectDeletionStateError("file_deletion_outbox_identity_conflict")
    return _file_deletion_result(file_row=file_row, outbox_row=dict(inserted), created=True)


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



async def claim_object_deletions(
    conn: AsyncConnection,
    *,
    limit: int = 50,
    max_attempts: int = 5,
) -> list[dict[str, Any]]:
    resolved_limit = max(1, min(int(limit), 200))
    resolved_max_attempts = max(1, min(int(max_attempts), 100))
    await conn.execute(
        """
        with invariant_violations as (
          select outbox.id
          from object_deletion_outbox outbox
          where outbox.state in ('pending', 'failed', 'processing')
            and not (
              (
                outbox.target_type = 'artifact'
                and exists (
                  select 1 from artifacts
                  where artifacts.tenant_id = outbox.tenant_id
                    and artifacts.id = outbox.artifact_id
                    and artifacts.storage_key = outbox.storage_key
                    and artifacts.lifecycle_state = 'delete_pending'
                )
              )
              or (
                outbox.target_type = 'file'
                and exists (
                  select 1 from files
                  where files.tenant_id = outbox.tenant_id
                    and files.id = outbox.file_id
                    and files.storage_key = outbox.storage_key
                    and files.lifecycle_state = 'delete_pending'
                )
              )
            )
          order by outbox.created_at asc, outbox.id asc
          limit %s
          for update of outbox skip locked
        )
        update object_deletion_outbox outbox
        set state = 'dead_letter',
            dead_letter_at = coalesce(dead_letter_at, now()),
            reconcile_required = true,
            last_error_code = 'object_delete_target_invariant',
            leased_at = null,
            updated_at = now()
        where outbox.id in (select id from invariant_violations)
        """,
        (resolved_limit,),
    )
    await conn.execute(
        """
        with exhausted as (
          select id
          from object_deletion_outbox
          where attempts >= %s
            and (
              (state in ('pending', 'failed') and available_at <= now())
              or (state = 'processing' and leased_at <= now() - interval '5 minutes')
            )
          order by available_at asc, created_at asc, id asc
          limit %s
          for update skip locked
        )
        update object_deletion_outbox
        set state = 'dead_letter',
            dead_letter_at = coalesce(dead_letter_at, now()),
            reconcile_required = true,
            leased_at = null,
            updated_at = now()
        where id in (select id from exhausted)
        """,
        (resolved_max_attempts, resolved_limit),
    )
    cursor = await conn.execute(
        """
        with candidates as (
          select id
          from object_deletion_outbox
          where (
              state in ('pending', 'failed') and available_at <= now() and attempts < %s
            ) or (
              state = 'processing'
              and leased_at <= now() - interval '5 minutes'
              and attempts < %s
            )
          order by available_at asc, created_at asc, id asc
          limit %s
          for update skip locked
        )
        update object_deletion_outbox
        set state = 'processing',
            attempts = attempts + 1,
            lease_generation = lease_generation + 1,
            leased_at = now(),
            updated_at = now()
        where id in (select id from candidates)
        returning id, tenant_id, target_type, artifact_id, file_id,
                  storage_key, attempts, lease_generation
        """,
        (resolved_max_attempts, resolved_max_attempts, resolved_limit),
    )
    return list(await cursor.fetchall())


async def complete_object_deletion(
    conn: AsyncConnection,
    *,
    outbox_id: str,
    tenant_id: str,
    lease_generation: int,
) -> bool:
    cursor = await conn.execute(
        """
        with claimed as materialized (
          select id, tenant_id, target_type, artifact_id, file_id, storage_key
          from object_deletion_outbox
          where id = %s and tenant_id = %s and state = 'processing'
            and lease_generation = %s
          for update
        ), updated_artifact as (
          update artifacts
          set lifecycle_state = 'deleted', deleted_at = coalesce(deleted_at, now())
          from claimed
          where claimed.target_type = 'artifact'
            and artifacts.tenant_id = claimed.tenant_id
            and artifacts.id = claimed.artifact_id
            and artifacts.storage_key = claimed.storage_key
            and artifacts.lifecycle_state = 'delete_pending'
          returning artifacts.id
        ), updated_file as (
          update files
          set lifecycle_state = 'deleted', deleted_at = coalesce(deleted_at, now())
          from claimed
          where claimed.target_type = 'file'
            and files.tenant_id = claimed.tenant_id
            and files.id = claimed.file_id
            and files.storage_key = claimed.storage_key
            and files.lifecycle_state = 'delete_pending'
          returning files.id
        ), updated_target as (
          select id from updated_artifact
          union all
          select id from updated_file
        )
        update object_deletion_outbox outbox
        set state = 'deleted',
            receipt_at = now(),
            dead_letter_at = null,
            reconcile_required = false,
            last_error_code = null,
            leased_at = null,
            updated_at = now()
        from claimed
        where outbox.id = claimed.id
          and exists (select 1 from updated_target)
        returning outbox.id
        """,
        (outbox_id, tenant_id, int(lease_generation)),
    )
    return await cursor.fetchone() is not None


async def fail_object_deletion(
    conn: AsyncConnection,
    *,
    outbox_id: str,
    tenant_id: str,
    lease_generation: int,
    error_code: str,
    max_attempts: int = 5,
    retry_base_seconds: int = 60,
    retry_cap_seconds: int = 3600,
) -> str | None:
    resolved_max_attempts = max(1, min(int(max_attempts), 100))
    resolved_base_seconds = max(1, min(int(retry_base_seconds), 3600))
    resolved_cap_seconds = max(resolved_base_seconds, min(int(retry_cap_seconds), 86400))
    cursor = await conn.execute(
        """
        update object_deletion_outbox
        set state = case when attempts >= %s then 'dead_letter' else 'failed' end,
            last_error_code = %s,
            available_at = case
              when attempts >= %s then available_at
              else now() + make_interval(
                secs => least(%s, %s * power(2, greatest(attempts - 1, 0)))::integer
              )
            end,
            dead_letter_at = case
              when attempts >= %s then coalesce(dead_letter_at, now())
              else null
            end,
            reconcile_required = attempts >= %s,
            leased_at = null,
            updated_at = now()
        where id = %s and tenant_id = %s and state = 'processing'
          and lease_generation = %s
        returning state
        """,
        (
            resolved_max_attempts,
            error_code[:120],
            resolved_max_attempts,
            resolved_cap_seconds,
            resolved_base_seconds,
            resolved_max_attempts,
            resolved_max_attempts,
            outbox_id,
            tenant_id,
            int(lease_generation),
        ),
    )
    row = await cursor.fetchone()
    return str(row["state"]) if row is not None else None


async def requeue_dead_letter_object_deletion(
    conn: AsyncConnection,
    *,
    outbox_id: str,
    tenant_id: str,
) -> bool:
    """Explicit operator reconciliation; physical deletion remains idempotent on retry."""

    cursor = await conn.execute(
        """
        update object_deletion_outbox outbox
        set state = 'pending',
            attempts = 0,
            available_at = now(),
            leased_at = null,
            dead_letter_at = null,
            reconcile_required = false,
            last_error_code = null,
            updated_at = now()
        where outbox.id = %s
          and outbox.tenant_id = %s
          and outbox.state = 'dead_letter'
          and (
            (
              outbox.target_type = 'artifact'
              and exists (
                select 1 from artifacts
                where artifacts.id = outbox.artifact_id
                  and artifacts.tenant_id = outbox.tenant_id
                  and artifacts.storage_key = outbox.storage_key
                  and artifacts.lifecycle_state = 'delete_pending'
              )
            )
            or (
              outbox.target_type = 'file'
              and exists (
                select 1 from files
                where files.id = outbox.file_id
                  and files.tenant_id = outbox.tenant_id
                  and files.storage_key = outbox.storage_key
                  and files.lifecycle_state = 'delete_pending'
              )
            )
          )
        returning outbox.id
        """,
        (outbox_id, tenant_id),
    )
    return await cursor.fetchone() is not None


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


async def get_data_retention_backlog(
    conn: AsyncConnection,
    *,
    retention_days: dict[str, int] | None = None,
) -> dict[str, int]:
    days = retention_days or {}
    run_event_days = max(0, int(days.get("run_events", 0)))
    context_snapshot_days = max(0, int(days.get("context_snapshots", 0)))
    audit_days = max(0, int(days.get("audit", 0)))
    message_days = max(0, int(days.get("messages", 0)))
    file_days = max(0, int(days.get("files", 0)))
    cursor = await conn.execute(
        """
        select
          (select count(*) from artifacts
           where lifecycle_state = 'active' and expires_at is not null and expires_at <= now()) as expired_artifacts,
          (select count(*) from artifacts where lifecycle_state = 'delete_pending') as artifact_delete_pending,
          (select count(*) from object_deletion_outbox where state <> 'deleted') as object_delete_backlog,
          (select count(*) from object_deletion_outbox where state = 'pending') as object_delete_pending,
          (select count(*) from object_deletion_outbox where state = 'processing') as object_delete_processing,
          (select count(*) from object_deletion_outbox where state = 'failed') as object_delete_retry_waiting,
          (select count(*) from object_deletion_outbox where state = 'dead_letter') as object_delete_dead_letter,
          (select count(*) from object_deletion_outbox where reconcile_required) as object_delete_reconcile_required,
          (select coalesce(max(attempts), 0) from object_deletion_outbox where state <> 'deleted') as object_delete_max_attempts_observed,
          (select coalesce(extract(epoch from now() - min(created_at))::bigint, 0)
           from object_deletion_outbox where state = 'dead_letter') as object_delete_oldest_dead_letter_age_seconds,
          (select count(*) from memory_records where status = 'deleted' and deleted_at is not null) as memory_soft_deleted,
          (select count(*) from run_events
           where %s > 0 and created_at <= now() - (%s * interval '1 day')) as run_events_age_eligible,
          (select count(*) from run_event_batches
           where %s > 0 and callback_received_at <= now() - (%s * interval '1 day')) as run_event_batches_age_eligible,
          (select count(*) from run_context_snapshots
           where %s > 0 and created_at <= now() - (%s * interval '1 day')) as context_snapshots_age_eligible,
          (select count(*) from audit_logs
           where %s > 0 and created_at <= now() - (%s * interval '1 day')) as audit_age_eligible,
          (select count(*) from messages
           where %s > 0 and created_at <= now() - (%s * interval '1 day')) as messages_age_eligible,
          (select count(*) from files
           where %s > 0 and created_at <= now() - (%s * interval '1 day')) as files_age_eligible
        """,
        (
            run_event_days,
            run_event_days,
            run_event_days,
            run_event_days,
            context_snapshot_days,
            context_snapshot_days,
            audit_days,
            audit_days,
            message_days,
            message_days,
            file_days,
            file_days,
        ),
    )
    row = await cursor.fetchone() or {}
    keys = (
        "expired_artifacts",
        "artifact_delete_pending",
        "object_delete_backlog",
        "object_delete_pending",
        "object_delete_processing",
        "object_delete_retry_waiting",
        "object_delete_dead_letter",
        "object_delete_reconcile_required",
        "object_delete_max_attempts_observed",
        "object_delete_oldest_dead_letter_age_seconds",
        "memory_soft_deleted",
        "run_events_age_eligible",
        "run_event_batches_age_eligible",
        "context_snapshots_age_eligible",
        "audit_age_eligible",
        "messages_age_eligible",
        "files_age_eligible",
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
