from contextlib import asynccontextmanager

import pytest
from fastapi import HTTPException

from app import repositories
from app.auth import AuthPrincipal
from app.routes import files as files_routes


class ScriptedCursor:
    def __init__(self, value):
        self.value = value

    async def fetchone(self):
        if isinstance(self.value, list):
            return self.value[0] if self.value else None
        return self.value

    async def fetchall(self):
        if self.value is None:
            return []
        return self.value if isinstance(self.value, list) else [self.value]


class ScriptedConnection:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    async def execute(self, statement, params=None):
        self.calls.append((" ".join(str(statement).split()), params))
        if not self.responses:
            raise AssertionError("unexpected SQL call")
        return ScriptedCursor(self.responses.pop(0))


def file_row(*, lifecycle_state="active", storage_key="private/file-a"):
    return {
        "id": "file-a",
        "tenant_id": "tenant-a",
        "workspace_id": "workspace-a",
        "user_id": "user-a",
        "session_id": None,
        "run_id": None,
        "storage_key": storage_key,
        "lifecycle_state": lifecycle_state,
    }


def outbox_row(*, state="pending", storage_key="private/file-a"):
    return {
        "id": "objdel_file-a",
        "tenant_id": "tenant-a",
        "target_type": "file",
        "artifact_id": None,
        "file_id": "file-a",
        "storage_key": storage_key,
        "state": state,
        "attempts": 0,
        "lease_generation": 0,
        "reconcile_required": state == "dead_letter",
    }


@pytest.mark.asyncio
async def test_queue_owned_unbound_file_tombstones_and_enqueues_one_exact_intent():
    conn = ScriptedConnection(
        file_row(),
        {
            "run_input_reference": False,
            "context_snapshot_reference": False,
            "message_reference": False,
            "artifact_reference": False,
            "artifact_outbox_reference": False,
            "unexpected_file_outbox": False,
        },
        file_row(lifecycle_state="delete_pending"),
        outbox_row(),
    )

    result = await repositories.queue_unbound_file_for_deletion(
        conn,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        file_id="file-a",
    )

    assert result == {
        "file_id": "file-a",
        "lifecycle_state": "delete_pending",
        "deletion_state": "pending",
        "reconcile_required": False,
        "created": True,
    }
    lock_sql, lock_params = conn.calls[0]
    assert "tenant_id = %s and workspace_id = %s and user_id = %s and id = %s" in lock_sql
    assert "for update" in lock_sql
    assert lock_params == ("tenant-a", "workspace-a", "user-a", "file-a")
    assert "runs.input_json @> jsonb_build_object" in conn.calls[1][0]
    assert "'file_ids', jsonb_build_array(%s::text)" in conn.calls[1][0]
    assert "snapshots.included_file_ids ? %s" in conn.calls[1][0]
    assert "messages.metadata_json @> jsonb_build_object" in conn.calls[1][0]
    assert "artifacts.manifest_json @> jsonb_build_object" in conn.calls[1][0]
    assert "'source_file_id', %s::text" in conn.calls[1][0]
    assert "lifecycle_state = 'delete_pending'" in conn.calls[2][0]
    assert "on conflict (id) do nothing" in conn.calls[3][0]
    assert "'file', null" in conn.calls[3][0]


@pytest.mark.asyncio
async def test_duplicate_delete_returns_the_existing_intent_without_requeue():
    conn = ScriptedConnection(
        file_row(lifecycle_state="delete_pending"),
        [outbox_row(state="processing")],
    )

    result = await repositories.queue_unbound_file_for_deletion(
        conn,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        file_id="file-a",
    )

    assert result["created"] is False
    assert result["deletion_state"] == "processing"
    assert len(conn.calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("reference_key", "reason"),
    [
        ("run_input_reference", "file_run_input_referenced"),
        ("context_snapshot_reference", "file_context_snapshot_referenced"),
        ("message_reference", "file_message_referenced"),
        ("artifact_reference", "file_artifact_referenced"),
        ("artifact_outbox_reference", "file_artifact_outbox_referenced"),
    ],
)
async def test_canonical_durable_references_block_file_deletion(reference_key, reason):
    references = {
        "run_input_reference": False,
        "context_snapshot_reference": False,
        "message_reference": False,
        "artifact_reference": False,
        "artifact_outbox_reference": False,
        "unexpected_file_outbox": False,
    }
    references[reference_key] = True
    conn = ScriptedConnection(file_row(), references)

    with pytest.raises(repositories.FileDeletionBlockedError, match=reason):
        await repositories.queue_unbound_file_for_deletion(
            conn,
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            user_id="user-a",
            file_id="file-a",
        )

    assert len(conn.calls) == 2


@pytest.mark.asyncio
async def test_cross_scope_delete_is_indistinguishable_from_missing_file():
    conn = ScriptedConnection(None)

    result = await repositories.queue_unbound_file_for_deletion(
        conn,
        tenant_id="tenant-b",
        workspace_id="workspace-b",
        user_id="user-b",
        file_id="file-a",
    )

    assert result is None
    assert len(conn.calls) == 1


@pytest.mark.asyncio
async def test_idempotent_replay_rejects_wrong_storage_identity():
    conn = ScriptedConnection(
        file_row(lifecycle_state="delete_pending"),
        [outbox_row(storage_key="private/wrong")],
    )

    with pytest.raises(repositories.ObjectDeletionStateError, match="identity_mismatch"):
        await repositories.queue_unbound_file_for_deletion(
            conn,
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            user_id="user-a",
            file_id="file-a",
        )


@pytest.mark.asyncio
async def test_active_file_with_preexisting_outbox_fails_before_tombstone():
    conn = ScriptedConnection(
        file_row(),
        {
            "run_input_reference": False,
            "context_snapshot_reference": False,
            "message_reference": False,
            "artifact_reference": False,
            "artifact_outbox_reference": False,
            "unexpected_file_outbox": True,
        },
    )

    with pytest.raises(repositories.ObjectDeletionStateError, match="without_tombstone"):
        await repositories.queue_unbound_file_for_deletion(
            conn,
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            user_id="user-a",
            file_id="file-a",
        )
    assert len(conn.calls) == 2


@pytest.mark.asyncio
async def test_claim_and_receipt_queries_bind_a_monotonic_lease_generation():
    conn = ScriptedConnection(None, None, [])
    assert await repositories.claim_object_deletions(conn, limit=7, max_attempts=4) == []
    assert "object_delete_target_invariant" in conn.calls[0][0]
    assert "limit %s" in conn.calls[0][0] and "skip locked" in conn.calls[0][0]
    assert conn.calls[0][1] == (7,)
    assert "limit %s" in conn.calls[1][0] and "skip locked" in conn.calls[1][0]
    assert conn.calls[1][1] == (4, 7)
    assert "lease_generation = lease_generation + 1" in conn.calls[2][0]
    assert "target_type, artifact_id, file_id" in conn.calls[2][0]

    completion = ScriptedConnection({"id": "out-a"})
    assert await repositories.complete_object_deletion(
        completion,
        outbox_id="out-a",
        tenant_id="tenant-a",
        lease_generation=9,
    )
    complete_sql, complete_params = completion.calls[0]
    assert "and lease_generation = %s" in complete_sql
    assert "updated_artifact" in complete_sql and "updated_file" in complete_sql
    assert "exists (select 1 from updated_target)" in complete_sql
    assert complete_params == ("out-a", "tenant-a", 9)

    failure = ScriptedConnection({"state": "failed"})
    assert await repositories.fail_object_deletion(
        failure,
        outbox_id="out-a",
        tenant_id="tenant-a",
        lease_generation=9,
        error_code="safe_error",
    ) == "failed"
    assert failure.calls[0][1][-3:] == ("out-a", "tenant-a", 9)


def principal() -> AuthPrincipal:
    return AuthPrincipal(
        user_id="user-a",
        display_name="User A",
        tenant_id="tenant-a",
        roles=["user"],
        permissions=[],
    )


def test_canonical_file_delete_route_has_a_typed_public_response():
    route = next(
        item
        for item in files_routes.router.routes
        if getattr(item, "path", None) == "/files/{file_id}"
        and "DELETE" in getattr(item, "methods", set())
    )

    assert route.response_model is files_routes.FileDeletionResponse


@pytest.mark.asyncio
async def test_delete_route_uses_principal_scope_and_audits_only_new_intent(monkeypatch):
    calls = []

    @asynccontextmanager
    async def transaction():
        yield object()

    async def queue(_conn, **kwargs):
        calls.append(("queue", kwargs))
        return {
            "file_id": "file-a",
            "lifecycle_state": "delete_pending",
            "deletion_state": "pending",
            "reconcile_required": False,
            "created": True,
        }

    async def audit(_conn, **kwargs):
        calls.append(("audit", kwargs))

    monkeypatch.setattr(files_routes, "transaction", transaction)
    monkeypatch.setattr(files_routes, "queue_unbound_file_for_deletion", queue)
    monkeypatch.setattr(files_routes, "append_audit_log", audit)

    response = await files_routes.delete_unbound_file(
        file_id="file-a",
        workspace_id="workspace-a",
        principal=principal(),
    )

    assert response.model_dump() == {
        "file_id": "file-a",
        "lifecycle_state": "delete_pending",
        "deletion_state": "pending",
        "reconcile_required": False,
    }
    assert calls[0] == (
        "queue",
        {
            "tenant_id": "tenant-a",
            "workspace_id": "workspace-a",
            "user_id": "user-a",
            "file_id": "file-a",
        },
    )
    assert calls[1][0] == "audit"
    assert "storage" not in str(calls[1])


@pytest.mark.asyncio
async def test_delete_route_hides_cross_scope_and_reference_details(monkeypatch):
    @asynccontextmanager
    async def transaction():
        yield object()

    async def missing(_conn, **_kwargs):
        return None

    monkeypatch.setattr(files_routes, "transaction", transaction)
    monkeypatch.setattr(files_routes, "queue_unbound_file_for_deletion", missing)
    with pytest.raises(HTTPException) as missing_error:
        await files_routes.delete_unbound_file(
            file_id="file-a",
            workspace_id="workspace-a",
            principal=principal(),
        )
    assert (missing_error.value.status_code, missing_error.value.detail) == (404, "file_not_found")

    async def blocked(_conn, **_kwargs):
        raise repositories.FileDeletionBlockedError("private_reference_detail")

    monkeypatch.setattr(files_routes, "queue_unbound_file_for_deletion", blocked)
    with pytest.raises(HTTPException) as blocked_error:
        await files_routes.delete_unbound_file(
            file_id="file-a",
            workspace_id="workspace-a",
            principal=principal(),
        )
    assert (blocked_error.value.status_code, blocked_error.value.detail) == (
        409,
        "file_deletion_blocked",
    )
