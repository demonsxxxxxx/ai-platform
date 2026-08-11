"""Generic durable object-deletion outbox persistence."""

from __future__ import annotations

from typing import Any

from psycopg import AsyncConnection


OUTBOX_TARGET_ARTIFACT = "artifact"
OUTBOX_TARGET_FILE = "file"


class ObjectDeletionStateError(RuntimeError):
    """Persisted target and outbox state cannot be proven consistent."""


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
          where (
              (
                outbox.target_type = 'artifact'
                and outbox.state in ('pending', 'failed', 'processing')
              )
              or (
                outbox.target_type = 'file'
                and outbox.state in ('file_pending', 'file_failed', 'file_processing')
              )
            )
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
        set state = case
              when outbox.target_type = 'file' then 'file_dead_letter'
              else 'dead_letter'
            end,
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
              (
                target_type = 'artifact'
                and (
                  (state in ('pending', 'failed') and available_at <= now())
                  or (state = 'processing' and leased_at <= now() - interval '5 minutes')
                )
              )
              or (
                target_type = 'file'
                and (
                  (state in ('file_pending', 'file_failed') and available_at <= now())
                  or (
                    state = 'file_processing'
                    and leased_at <= now() - interval '5 minutes'
                  )
                )
              )
            )
          order by available_at asc, created_at asc, id asc
          limit %s
          for update skip locked
        )
        update object_deletion_outbox
        set state = case
              when target_type = 'file' then 'file_dead_letter'
              else 'dead_letter'
            end,
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
              target_type = 'artifact'
              and (
                (state in ('pending', 'failed') and available_at <= now())
                or (state = 'processing' and leased_at <= now() - interval '5 minutes')
              )
              and attempts < %s
            ) or (
              target_type = 'file'
              and (
                (state in ('file_pending', 'file_failed') and available_at <= now())
                or (
                  state = 'file_processing'
                  and leased_at <= now() - interval '5 minutes'
                )
              )
              and attempts < %s
            )
          order by available_at asc, created_at asc, id asc
          limit %s
          for update skip locked
        )
        update object_deletion_outbox
        set state = case
              when target_type = 'file' then 'file_processing'
              else 'processing'
            end,
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
          where id = %s and tenant_id = %s
            and (
              (target_type = 'artifact' and state = 'processing')
              or (target_type = 'file' and state = 'file_processing')
            )
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
        set state = case
              when claimed.target_type = 'file' then 'file_deleted'
              else 'deleted'
            end,
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
    resolved_cap_seconds = max(
        resolved_base_seconds, min(int(retry_cap_seconds), 86400)
    )
    cursor = await conn.execute(
        """
        update object_deletion_outbox
        set state = case
              when target_type = 'file' and attempts >= %s then 'file_dead_letter'
              when target_type = 'file' then 'file_failed'
              when attempts >= %s then 'dead_letter'
              else 'failed'
            end,
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
        where id = %s and tenant_id = %s
          and (
            (target_type = 'artifact' and state = 'processing')
            or (target_type = 'file' and state = 'file_processing')
          )
          and lease_generation = %s
        returning state
        """,
        (
            resolved_max_attempts,
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
        set state = case
              when outbox.target_type = 'file' then 'file_pending'
              else 'pending'
            end,
            attempts = 0,
            available_at = now(),
            leased_at = null,
            dead_letter_at = null,
            reconcile_required = false,
            last_error_code = null,
            updated_at = now()
        where outbox.id = %s
          and outbox.tenant_id = %s
          and (
            (
              outbox.target_type = 'artifact'
              and outbox.state = 'dead_letter'
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
              and outbox.state = 'file_dead_letter'
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
