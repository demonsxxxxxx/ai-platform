"""Owner-scoped file-deletion admission persistence."""

from __future__ import annotations

from typing import Any

from psycopg import AsyncConnection

from app.persistence.object_deletions import (
    OUTBOX_TARGET_FILE,
    ObjectDeletionStateError,
)


FILE_DELETE_PUBLIC_STATES = {
    "file_pending": "pending",
    "file_processing": "processing",
    "file_failed": "failed",
    "file_dead_letter": "dead_letter",
    "file_deleted": "deleted",
}
FILE_DELETE_PENDING_STATES = frozenset(FILE_DELETE_PUBLIC_STATES) - {"file_deleted"}


class FileDeletionBlockedError(RuntimeError):
    """The owned file exists but has a canonical durable reference."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


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
        or str(outbox_row.get("tenant_id") or "")
        != str(file_row.get("tenant_id") or "")
        or str(outbox_row.get("target_type") or "") != OUTBOX_TARGET_FILE
        or outbox_row.get("artifact_id") is not None
        or str(outbox_row.get("file_id") or "") != file_id
        or str(outbox_row.get("storage_key") or "") != storage_key
    ):
        raise ObjectDeletionStateError("file_deletion_outbox_identity_mismatch")
    if (
        lifecycle_state == "delete_pending"
        and outbox_state not in FILE_DELETE_PENDING_STATES
    ):
        raise ObjectDeletionStateError("file_deletion_outbox_state_mismatch")
    if lifecycle_state == "deleted" and outbox_state != "file_deleted":
        raise ObjectDeletionStateError("file_deletion_outbox_state_mismatch")
    if lifecycle_state not in {"delete_pending", "deleted"}:
        raise ObjectDeletionStateError("file_deletion_lifecycle_state_mismatch")
    return {
        "file_id": file_id,
        "lifecycle_state": lifecycle_state,
        "deletion_state": FILE_DELETE_PUBLIC_STATES[outbox_state],
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
    return _file_deletion_result(
        file_row=file_row, outbox_row=dict(rows[0]), created=False
    )


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
        ) values (%s, %s, 'file', null, %s, %s, 'file_pending', now())
        on conflict (id) do nothing
        returning id, tenant_id, target_type, artifact_id, file_id, storage_key,
                  state, attempts, lease_generation, reconcile_required
        """,
        (outbox_id, tenant_id, file_id, str(file_row["storage_key"])),
    )
    inserted = await cursor.fetchone()
    if inserted is None:
        raise ObjectDeletionStateError("file_deletion_outbox_identity_conflict")
    return _file_deletion_result(
        file_row=file_row, outbox_row=dict(inserted), created=True
    )
