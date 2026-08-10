from __future__ import annotations

import pytest

from app import artifact_lifecycle_repository
from app.persistence import RepositoryNotFoundError


class OneRowCursor:
    def __init__(self, row):
        self.row = row

    async def fetchone(self):
        return self.row


class FileDeletionConnection:
    def __init__(self, *, file_row, queued_row=None, existing_outbox=None):
        self.file_row = file_row
        self.queued_row = queued_row
        self.existing_outbox = existing_outbox
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    async def execute(self, sql, params):
        normalized = " ".join(sql.split())
        self.calls.append((normalized, params))
        if normalized.startswith("select files.*"):
            return OneRowCursor(self.file_row)
        if normalized.startswith("select id, tenant_id, file_id, state"):
            return OneRowCursor(self.existing_outbox)
        if normalized.startswith("with tombstoned as"):
            return OneRowCursor(self.queued_row)
        raise AssertionError(f"unexpected SQL: {normalized}")


def file_row(**updates):
    return {
        "id": "file-a",
        "tenant_id": "tenant-a",
        "workspace_id": "workspace-a",
        "user_id": "user-a",
        "session_id": None,
        "run_id": None,
        "storage_key": "tenants/tenant-a/unbound/file-a",
        "lifecycle_state": "active",
        "snapshot_referenced": False,
        "artifact_referenced": False,
        **updates,
    }


@pytest.mark.asyncio
async def test_unbound_file_delete_is_owner_scoped_reference_safe_and_idempotently_queued():
    queued = {
        "id": "objdel_file-a",
        "tenant_id": "tenant-a",
        "file_id": "file-a",
        "state": "pending",
        "attempts": 0,
    }
    first = FileDeletionConnection(file_row=file_row(), queued_row=queued)

    result = await artifact_lifecycle_repository.queue_unbound_file_for_deletion(
        first,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        file_id="file-a",
    )

    assert result == queued
    lock_sql, lock_params = first.calls[0]
    assert "snapshots.included_file_ids ? files.id" in lock_sql
    assert "artifacts.manifest_json->>'source_file_id' = files.id" in lock_sql
    assert "for update of files" in lock_sql
    assert lock_params == ("file-a", "tenant-a", "workspace-a", "user-a")
    queue_sql, queue_params = first.calls[1]
    assert "set lifecycle_state = 'delete_pending'" in queue_sql
    assert "insert into object_deletion_outbox" in queue_sql
    assert "artifact_id, file_id" in queue_sql
    assert queue_params == ("file-a", "tenant-a", "workspace-a", "user-a")

    replay = FileDeletionConnection(
        file_row=file_row(lifecycle_state="delete_pending"),
        existing_outbox=queued,
    )
    assert await artifact_lifecycle_repository.queue_unbound_file_for_deletion(
        replay,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        file_id="file-a",
    ) == queued
    assert len(replay.calls) == 2
    assert all("update files" not in sql for sql, _params in replay.calls)


@pytest.mark.asyncio
async def test_unbound_file_delete_hides_foreign_scope_and_rejects_every_binding():
    missing = FileDeletionConnection(file_row=None)
    with pytest.raises(RepositoryNotFoundError, match="file_not_found"):
        await artifact_lifecycle_repository.queue_unbound_file_for_deletion(
            missing,
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            user_id="user-a",
            file_id="file-foreign",
        )

    for update in (
        {"session_id": "session-a"},
        {"run_id": "run-a"},
        {"snapshot_referenced": True},
        {"artifact_referenced": True},
    ):
        bound = FileDeletionConnection(file_row=file_row(**update))
        with pytest.raises(
            artifact_lifecycle_repository.FileDeletionConflictError,
            match="file_already_bound",
        ):
            await artifact_lifecycle_repository.queue_unbound_file_for_deletion(
                bound,
                tenant_id="tenant-a",
                workspace_id="workspace-a",
                user_id="user-a",
                file_id="file-a",
            )
        assert len(bound.calls) == 1


@pytest.mark.asyncio
async def test_file_object_deletion_receipt_marks_only_the_exact_file_target_deleted():
    class ReceiptConnection:
        def __init__(self):
            self.calls: list[tuple[str, tuple[object, ...]]] = []

        async def execute(self, sql, params):
            normalized = " ".join(sql.split())
            self.calls.append((normalized, params))
            return OneRowCursor(
                {"artifact_id": None, "file_id": "file-a"}
                if len(self.calls) == 1
                else None
            )

    conn = ReceiptConnection()
    assert await artifact_lifecycle_repository.complete_object_deletion(
        conn,
        outbox_id="objdel_file-a",
        tenant_id="tenant-a",
        artifact_id="file-a",
    ) is True
    assert "artifact_id = %s or file_id = %s" in conn.calls[0][0]
    assert conn.calls[0][1] == ("objdel_file-a", "tenant-a", "file-a", "file-a")
    assert "update files" in conn.calls[1][0]
    assert conn.calls[1][1] == ("tenant-a", "file-a")
