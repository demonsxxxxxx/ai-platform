"""Cross-class retention purge and backlog persistence."""

from __future__ import annotations

from typing import Any

from psycopg import AsyncConnection


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
          (select count(*) from object_deletion_outbox
           where state not in ('deleted', 'file_deleted')) as object_delete_backlog,
          (select count(*) from object_deletion_outbox
           where state in ('pending', 'file_pending')) as object_delete_pending,
          (select count(*) from object_deletion_outbox
           where state in ('processing', 'file_processing')) as object_delete_processing,
          (select count(*) from object_deletion_outbox
           where state in ('failed', 'file_failed')) as object_delete_retry_waiting,
          (select count(*) from object_deletion_outbox
           where state in ('dead_letter', 'file_dead_letter')) as object_delete_dead_letter,
          (select count(*) from object_deletion_outbox where reconcile_required) as object_delete_reconcile_required,
          (select coalesce(max(attempts), 0) from object_deletion_outbox
           where state not in ('deleted', 'file_deleted')) as object_delete_max_attempts_observed,
          (select coalesce(extract(epoch from now() - min(created_at))::bigint, 0)
           from object_deletion_outbox
           where state in ('dead_letter', 'file_dead_letter')) as object_delete_oldest_dead_letter_age_seconds,
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
