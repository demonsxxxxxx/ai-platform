import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import uuid

import psycopg
from psycopg import sql as psycopg_sql
from psycopg.rows import dict_row
import pytest

from app import repositories
from app.models import QueueRunPayload
from app.repositories import (
    RepositoryConflictError,
    RepositoryNotFoundError,
    append_audit_log,
    append_event,
    cancel_run,
    complete_run,
    count_active_runs_for_user,
    create_artifact,
    create_context_snapshot,
    create_sandbox_lease,
    create_tool_permission_request,
    create_run,
    admin_delete_memory_record,
    delete_memory_record,
    enforce_user_active_run_admission,
    expire_tool_permission_request,
    fail_run,
    get_admin_run_detail,
    get_context_snapshot_for_worker,
    get_exact_tool_permission_decision,
    get_authorized_context_target_session,
    get_latest_authorized_executor_context_snapshot,
    list_context_share_snapshots_for_target_session,
    get_latest_tool_permission_decision,
    get_tool_permission_request_by_id,
    get_tool_permission_request_by_id_for_tenant,
    get_tool_permission_request_for_tenant,
    get_run_identity,
    list_multi_agent_dispatch_candidate_run_ids,
    list_tool_permission_inbox,
    list_tool_permission_inbox_for_tenant,
    list_run_events,
    list_run_artifacts,
    list_scoped_context_messages,
    mark_multi_agent_dispatch_enqueue_failed,
    mark_multi_agent_dispatch_parent_awaiting_dispatch,
    get_scoped_context_file,
    get_scoped_context_artifact,
    list_scoped_context_memory_records,
    renew_sandbox_lease,
    upsert_run_step,
)


def test_chat_submission_fingerprint_is_canonical_and_scope_bound():
    first = repositories.chat_submission_fingerprint(
        {
            "message": "same message",
            "workspace_id": "default",
            "file_ids": ["file-a", "file-b"],
            "input": {"b": 2, "a": 1},
        },
        tenant_id="tenant-a",
        user_id="user-a",
    )
    reordered = repositories.chat_submission_fingerprint(
        {
            "input": {"a": 1, "b": 2},
            "file_ids": ["file-a", "file-b"],
            "message": "same message",
            "workspace_id": "default",
        },
        tenant_id="tenant-a",
        user_id="user-a",
    )
    changed_scope = repositories.chat_submission_fingerprint(
        {
            "input": {"a": 1, "b": 2},
            "file_ids": ["file-a", "file-b"],
            "message": "same message",
            "workspace_id": "default",
        },
        tenant_id="tenant-b",
        user_id="user-a",
    )

    assert first == reordered
    assert first != changed_scope
    assert len(first) == 64


@pytest.mark.asyncio
async def test_list_stale_run_candidates_requires_progress_staleness_and_no_active_sandbox_lease():
    conn = SingleRowConnection(None)

    await repositories.list_stale_run_reconciliation_candidates(
        conn,
        stale_after_seconds=900,
        limit=25,
    )

    assert "runs.status in ('queued', 'running')" in conn.sql
    assert "greatest( coalesce(latest_event.created_at" in conn.sql
    assert "<= clock_timestamp() - (%s * interval '1 second')" in conn.sql
    assert "not exists ( select 1 from sandbox_leases" in conn.sql
    assert "sandbox_leases.status = 'active'" in conn.sql
    assert "for update of runs skip locked" not in conn.sql
    assert conn.params == (900, 900, 25)


@pytest.mark.asyncio
async def test_list_cancel_requested_orphans_bypasses_general_staleness_but_keeps_live_owner_fences():
    conn = SingleRowConnection(None)

    await repositories.list_stale_run_reconciliation_candidates(
        conn,
        stale_after_seconds=900,
        cancel_requested_after_seconds=5,
        limit=25,
    )

    assert "cancel_requested_at <= clock_timestamp() - (%s * interval '1 second')" in conn.sql
    assert "greatest( coalesce(latest_event.created_at" in conn.sql
    assert "not exists ( select 1 from sandbox_leases" in conn.sql
    assert conn.params == (5, 900, 25)


@pytest.mark.asyncio
async def test_stage_stale_cancel_requested_run_uses_scoped_cas_and_existing_cancel_contract(monkeypatch):
    calls = []

    class Connection:
        async def execute(self, sql, params):
            normalized = " ".join(sql.split())
            calls.append((normalized, params))
            if normalized.startswith("update runs set permission_terminalization_target"):
                return SingleRowCursor(
                    {
                        "id": "run-a",
                        "trace_id": "trace-a",
                        "permission_terminalization_target": "cancelled",
                    }
                )
            return SingleRowCursor(None)

    async def append_event(_conn, **kwargs):
        calls.append(("event", kwargs))

    async def append_audit_log(_conn, **kwargs):
        calls.append(("audit", kwargs))

    monkeypatch.setattr(repositories, "append_event", append_event)
    monkeypatch.setattr(repositories, "append_audit_log", append_audit_log)

    row = await repositories.stage_stale_run_reconciliation(
        Connection(),
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        run_id="run-a",
        expected_status="running",
        stale_before="2026-07-21T11:00:00Z",
        cancel_requested_before="2026-07-21T11:07:58Z",
        terminal_status="cancelled",
        error_code=None,
        error_message=None,
    )

    assert row == {"id": "run-a", "trace_id": "trace-a", "permission_terminalization_target": "cancelled"}
    update_sql, update_params = calls[0]
    assert "workspace_id = %s" in update_sql
    assert "user_id is not distinct from %s" in update_sql
    assert "status = %s" in update_sql
    assert "cancel_requested_at is not null" in update_sql
    assert "not exists ( select 1 from sandbox_leases" in update_sql
    assert "greatest( coalesce((select max(created_at)" in update_sql
    assert update_params[4:9] == ("tenant-a", "workspace-a", "user-a", "run-a", "running")
    assert update_params[-3:] == (
        "2026-07-21T11:07:58Z",
        "cancelled",
        "2026-07-21T11:00:00Z",
    )
    assert calls[1][0] == "event"
    assert calls[1][1]["event_type"] == "stale_run_reconciled"
    assert calls[1][1]["payload"]["result_status"] == "cancelled"
    assert calls[2][0] == "audit"
    assert calls[2][1]["action"] == "run.stale.reconcile"


@pytest.mark.asyncio
async def test_stage_stale_running_run_fails_explicitly_and_cas_loss_emits_nothing(monkeypatch):
    calls = []

    class Connection:
        def __init__(self, row):
            self.row = row

        async def execute(self, sql, params):
            calls.append(("sql", " ".join(sql.split()), params))
            return SingleRowCursor(self.row)

    async def append_event(_conn, **kwargs):
        calls.append(("event", kwargs))

    async def append_audit_log(_conn, **kwargs):
        calls.append(("audit", kwargs))

    monkeypatch.setattr(repositories, "append_event", append_event)
    monkeypatch.setattr(repositories, "append_audit_log", append_audit_log)

    failed = await repositories.stage_stale_run_reconciliation(
        Connection(
            {"id": "run-failed", "trace_id": "trace-failed", "permission_terminalization_target": "failed"}
        ),
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        run_id="run-failed",
        expected_status="running",
        stale_before="2026-07-21T11:00:00Z",
        cancel_requested_before=None,
        terminal_status="failed",
        error_code="stale_run_interrupted",
        error_message="Run interrupted because no live execution owner remains.",
    )

    assert failed is not None
    assert any(call[0] == "event" and call[1]["payload"]["error_code"] == "stale_run_interrupted" for call in calls)
    assert any(call[0] == "audit" and call[1]["payload_json"]["result_status"] == "failed" for call in calls)

    calls.clear()
    lost = await repositories.stage_stale_run_reconciliation(
        Connection(None),
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        run_id="run-lost",
        expected_status="running",
        stale_before="2026-07-21T11:00:00Z",
        cancel_requested_before=None,
        terminal_status="failed",
        error_code="stale_run_interrupted",
        error_message="Run interrupted because no live execution owner remains.",
    )

    assert lost is None
    assert [call[0] for call in calls] == ["sql"]


class FakeCursor:
    async def fetchone(self):
        return {"count": 2, "id": "step-a"}

    async def fetchall(self):
        return []


class FakeConnection:
    def __init__(self):
        self.sql = ""
        self.params = None

    async def execute(self, sql, params):
        self.sql = sql
        self.params = params
        return FakeCursor()


class RecordingConnection:
    def __init__(self):
        self.calls = []

    async def execute(self, sql, params):
        normalized = " ".join(sql.split())
        self.calls.append((normalized, params))
        if normalized.startswith("update sessions set next_run_generation"):
            return SingleRowCursor({"next_run_generation": 1})
        if "select clock_timestamp() as authority_now" in normalized:
            return SingleRowCursor({"authority_now": datetime(2026, 7, 16, tzinfo=timezone.utc)})
        return FakeCursor()


class SingleRowCursor:
    def __init__(self, row):
        self.row = row

    async def fetchone(self):
        return self.row

    async def fetchall(self):
        return [self.row] if self.row is not None else []


class SingleRowConnection:
    def __init__(self, row):
        self.row = row
        self.sql = ""
        self.params = None

    async def execute(self, sql, params):
        self.sql = " ".join(sql.split())
        self.params = params
        return SingleRowCursor(self.row)


class TwoSnapshotFileMembershipConnection:
    """Model S1 as persisted authority even though S2 is created later."""

    def __init__(self, *, selected_member: bool, later_member: bool):
        self.selected_snapshot_id = "ctx-s1"
        self.snapshots = [
            {"id": "ctx-s1", "included_file_ids": ["file-a"] if selected_member else []},
            {"id": "ctx-s2", "included_file_ids": ["file-a"] if later_member else []},
        ]
        self.calls: list[tuple[str, tuple]] = []

    async def execute(self, sql, params):
        normalized = " ".join(sql.split())
        self.calls.append((normalized, params))
        is_projection = len(params) == 4
        exact_snapshot_join = (
            "authorized_snapshot.id = runs.context_snapshot_id"
            if is_projection
            else "context_snapshot.id = current_run.context_snapshot_id"
        )
        uses_selected_snapshot = exact_snapshot_join in normalized
        snapshot = self.snapshots[0] if uses_selected_snapshot else self.snapshots[-1]
        authorized = "file-a" in snapshot["included_file_ids"]
        row = (
            {
                "id": "file-a",
                "run_id": "run-a",
                "original_name": "source.txt",
                "content_type": "text/plain",
                "size_bytes": 10,
                "storage_key": "tenants/private/source.txt",
            }
            if authorized
            else None
        )
        return SingleRowCursor(row)


class _SessionTableConnection:
    def __init__(self, rows):
        self.rows = rows
        self.sql = ""
        self.params = None

    async def execute(self, sql, params):
        self.sql = " ".join(sql.split())
        self.params = params
        tenant_id, session_id, user_id = params
        row = next(
            (
                candidate
                for candidate in self.rows
                if candidate["tenant_id"] == tenant_id
                and candidate["id"] == session_id
                and candidate["user_id"] == user_id
                and ("status = 'active'" not in self.sql or candidate["status"] == "active")
            ),
            None,
        )
        return SingleRowCursor(row)


class _RowsCursor:
    def __init__(self, rows):
        self.rows = rows

    async def fetchall(self):
        return self.rows


class _RunSessionTableConnection:
    def __init__(self, *, run, session):
        self.run = run
        self.session = session
        self.calls = []

    async def execute(self, sql, params):
        normalized = " ".join(sql.split())
        self.calls.append((normalized, params))
        tenant_id, run_id, user_id = params
        run = self.run
        row = run if (
            run["tenant_id"] == tenant_id
            and run["id"] == run_id
            and run["user_id"] == user_id
        ) else None
        if row is not None and "join sessions" in normalized:
            session = self.session
            predicates = [session["id"] == run["session_id"]]
            if "sessions.tenant_id = runs.tenant_id" in normalized:
                predicates.append(session["tenant_id"] == run["tenant_id"])
            if "sessions.workspace_id = runs.workspace_id" in normalized:
                predicates.append(session["workspace_id"] == run["workspace_id"])
            if "sessions.user_id = runs.user_id" in normalized:
                predicates.append(session["user_id"] == run["user_id"])
            if "sessions.agent_id = runs.agent_id" in normalized:
                predicates.append(session["agent_id"] == run["agent_id"])
            if "sessions.status = 'active'" in normalized:
                predicates.append(session["status"] == "active")
            if not all(predicates):
                row = None
        return SingleRowCursor(row)


class _RevealedArtifactTableConnection:
    def __init__(self, *, artifact, run, session):
        self.artifact = artifact
        self.run = run
        self.session = session
        self.calls = []

    async def execute(self, sql, params):
        normalized = " ".join(sql.split())
        self.calls.append((normalized, params))
        tenant_id, user_id = params[:2]
        artifact = self.artifact
        run = self.run
        visible = (
            artifact["tenant_id"] == tenant_id
            and run["id"] == artifact["run_id"]
            and run["tenant_id"] == artifact["tenant_id"]
            and run["user_id"] == user_id
        )
        session = self.session
        session_matches = session["id"] == run["session_id"]
        if "sessions.tenant_id = runs.tenant_id" in normalized:
            session_matches = session_matches and session["tenant_id"] == run["tenant_id"]
        if "sessions.workspace_id = runs.workspace_id" in normalized:
            session_matches = session_matches and session["workspace_id"] == run["workspace_id"]
        if "sessions.user_id = runs.user_id" in normalized:
            session_matches = session_matches and session["user_id"] == run["user_id"]
        if "sessions.agent_id = runs.agent_id" in normalized:
            session_matches = session_matches and session["agent_id"] == run["agent_id"]
        if "sessions.status = 'active'" in normalized:
            session_matches = session_matches and session["status"] == "active"
        if "left join sessions" not in normalized:
            visible = visible and session_matches
        elif "sessions.status = 'active'" in normalized:
            visible = visible and session_matches
        if not visible:
            return _RowsCursor([])
        if "group by runs.session_id" in normalized:
            return _RowsCursor(
                [
                    {
                        "session_id": run["session_id"],
                        "session_name": session["title"],
                        "file_count": 1,
                        "updated_at": artifact["created_at"],
                    }
                ]
            )
        return _RowsCursor(
            [
                {
                    **artifact,
                    "run_id": run["id"],
                    "session_id": run["session_id"],
                    "workspace_id": run["workspace_id"],
                    "user_id": run["user_id"],
                    "session_name": session["title"],
                }
            ]
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "lookup",
    [repositories.get_authorized_session, repositories.get_authorized_lambchat_session],
)
async def test_owner_session_lookups_are_active_only_and_keep_principal_scope(lookup):
    active = {
        "id": "session-active",
        "tenant_id": "tenant-a",
        "workspace_id": "workspace-a",
        "user_id": "user-a",
        "agent_id": "general-agent",
        "status": "active",
    }
    deleted = {**active, "id": "session-deleted", "status": "deleted"}
    conn = _SessionTableConnection([active, deleted])

    assert await lookup(
        conn,
        tenant_id="tenant-a",
        user_id="user-a",
        session_id="session-active",
    ) == active
    assert "status = 'active'" in conn.sql
    assert conn.params == ("tenant-a", "session-active", "user-a")

    assert await lookup(
        conn,
        tenant_id="tenant-a",
        user_id="user-a",
        session_id="session-deleted",
    ) is None
    assert "status = 'active'" in conn.sql

    assert await lookup(
        conn,
        tenant_id="tenant-a",
        user_id="user-b",
        session_id="session-active",
    ) is None
    assert conn.params == ("tenant-a", "session-active", "user-b")


@pytest.mark.asyncio
async def test_selectorless_continuation_lock_precedes_same_session_generation_allocation():
    class LinearizedConnection:
        def __init__(self):
            self.calls = []

        async def execute(self, sql, params):
            normalized = " ".join(sql.split())
            self.calls.append((normalized, params))
            if normalized.startswith("select * from sessions"):
                return SingleRowCursor(
                    {
                        "id": "session-a",
                        "tenant_id": "tenant-a",
                        "workspace_id": "workspace-a",
                        "user_id": "user-a",
                        "agent_id": "general-agent",
                        "status": "active",
                    }
                )
            if normalized.startswith("update sessions set next_run_generation"):
                return SingleRowCursor({"next_run_generation": 1})
            raise AssertionError(f"unexpected SQL: {normalized}")

    conn = LinearizedConnection()
    session = await repositories.get_authorized_session(
        conn,
        tenant_id="tenant-a",
        user_id="user-a",
        session_id="session-a",
        workspace_id="workspace-a",
        for_update=True,
    )
    assert session is not None
    generation = await repositories.allocate_session_run_generation(
        conn,
        tenant_id="tenant-a",
        workspace_id=str(session["workspace_id"]),
        user_id="user-a",
        session_id="session-a",
        agent_id=str(session["agent_id"]),
    )

    assert generation == 1
    lock_sql, lock_params = conn.calls[0]
    assert lock_sql.endswith("for update")
    assert lock_params == ("tenant-a", "session-a", "user-a", "workspace-a")
    generation_sql, generation_params = conn.calls[1]
    assert generation_sql.startswith("update sessions set next_run_generation = next_run_generation + 1")
    assert generation_params == ("tenant-a", "workspace-a", "user-a", "session-a", "general-agent")


@pytest.mark.asyncio
async def test_owner_run_lookup_closes_on_session_delete_and_locks_only_the_run_row():
    run = {
        "id": "run-a",
        "tenant_id": "tenant-a",
        "workspace_id": "workspace-a",
        "user_id": "user-a",
        "session_id": "session-a",
        "agent_id": "general-agent",
        "status": "running",
    }
    session = {
        "id": "session-a",
        "tenant_id": "tenant-a",
        "workspace_id": "workspace-a",
        "user_id": "user-a",
        "agent_id": "general-agent",
        "status": "active",
    }
    conn = _RunSessionTableConnection(run=run, session=session)

    assert await repositories.get_authorized_run(
        conn,
        tenant_id="tenant-a",
        user_id="user-a",
        run_id="run-a",
        for_update=True,
    ) == run
    active_sql, active_params = conn.calls[-1]
    assert active_sql.startswith("select runs.* from runs join sessions")
    assert "sessions.tenant_id = runs.tenant_id" in active_sql
    assert "sessions.workspace_id = runs.workspace_id" in active_sql
    assert "sessions.user_id = runs.user_id" in active_sql
    assert "sessions.agent_id = runs.agent_id" in active_sql
    assert "sessions.status = 'active'" in active_sql
    assert active_sql.endswith("for update of runs")
    assert active_params == ("tenant-a", "run-a", "user-a")

    session["status"] = "deleted"
    assert await repositories.get_authorized_run(
        conn,
        tenant_id="tenant-a",
        user_id="user-a",
        run_id="run-a",
    ) is None

    session["status"] = "active"
    session["agent_id"] = "other-agent"
    assert await repositories.get_authorized_run(
        conn,
        tenant_id="tenant-a",
        user_id="user-a",
        run_id="run-a",
    ) is None
    assert await repositories.get_authorized_run(
        conn,
        tenant_id="tenant-a",
        user_id="user-b",
        run_id="run-a",
    ) is None


@pytest.mark.asyncio
async def test_session_action_repositories_bind_tenant_and_active_terminal_state():
    conn = RecordingConnection()

    await repositories.get_session_for_action(
        conn,
        tenant_id="tenant-a",
        session_id="session-a",
    )
    get_sql, get_params = conn.calls[-1]
    assert "from sessions" in get_sql
    assert "tenant_id = %s and id = %s" in get_sql
    assert "for update" in get_sql
    assert "status = 'active'" not in get_sql
    assert get_params == ("tenant-a", "session-a")

    await repositories.update_session_title(
        conn,
        tenant_id="tenant-a",
        session_id="session-a",
        title="Renamed",
    )
    rename_sql, rename_params = conn.calls[-1]
    assert "update sessions" in rename_sql
    assert "status = 'active'" in rename_sql
    assert rename_params == ("Renamed", "tenant-a", "session-a")

    await repositories.mark_session_deleted(
        conn,
        tenant_id="tenant-a",
        session_id="session-a",
    )
    delete_sql, delete_params = conn.calls[-1]
    assert "set status = 'deleted'" in delete_sql
    assert "status = 'active'" in delete_sql
    assert delete_params == ("tenant-a", "session-a")

    await repositories.list_session_messages_for_fork(
        conn,
        tenant_id="tenant-a",
        session_id="session-a",
    )
    messages_sql, messages_params = conn.calls[-1]
    assert "from messages" in messages_sql
    assert "tenant_id = %s and session_id = %s" in messages_sql
    assert "order by created_at asc, id asc" in messages_sql
    assert messages_params == ("tenant-a", "session-a")


@pytest.mark.asyncio
async def test_authorized_artifact_requires_an_active_exact_scope_owning_session():
    artifact = {
        "id": "artifact-a",
        "run_id": "run-a",
        "storage_key": "tenants/tenant-a/runs/run-a/artifact-a.txt",
    }
    conn = SingleRowConnection(artifact)

    row = await repositories.get_authorized_artifact(
        conn,
        tenant_id="tenant-a",
        user_id="user-a",
        artifact_id="artifact-a",
    )

    assert row == artifact
    assert "join sessions on sessions.id = runs.session_id" in conn.sql
    assert "sessions.tenant_id = runs.tenant_id" in conn.sql
    assert "sessions.workspace_id = runs.workspace_id" in conn.sql
    assert "sessions.user_id = runs.user_id" in conn.sql
    assert "sessions.agent_id = runs.agent_id" in conn.sql
    assert "runs.user_id = %s" in conn.sql
    assert "sessions.status = 'active'" in conn.sql
    assert conn.params == ("tenant-a", "artifact-a", "user-a")


@pytest.mark.asyncio
async def test_revealed_artifact_rows_disappear_after_exact_owning_session_is_deleted():
    artifact = {
        "id": "artifact-a",
        "tenant_id": "tenant-a",
        "run_id": "run-a",
        "storage_key": "tenants/tenant-a/runs/run-a/artifact-a.txt",
        "label": "Artifact A",
        "content_type": "text/plain",
        "size_bytes": 10,
        "artifact_type": "document",
        "created_at": "2026-07-18T00:00:00Z",
        "trace_id": "trace-a",
    }
    run = {
        "id": "run-a",
        "tenant_id": "tenant-a",
        "workspace_id": "workspace-a",
        "user_id": "user-a",
        "session_id": "session-a",
        "agent_id": "general-agent",
    }
    session = {
        "id": "session-a",
        "tenant_id": "tenant-a",
        "workspace_id": "workspace-a",
        "user_id": "user-a",
        "agent_id": "general-agent",
        "title": "Session A",
        "status": "active",
    }
    conn = _RevealedArtifactTableConnection(artifact=artifact, run=run, session=session)

    assert [row["id"] for row in await repositories.list_revealed_artifacts(
        conn,
        tenant_id="tenant-a",
        user_id="user-a",
    )] == ["artifact-a"]
    assert [row["session_id"] for row in await repositories.list_revealed_artifact_sessions(
        conn,
        tenant_id="tenant-a",
        user_id="user-a",
    )] == ["session-a"]
    for sql, params in conn.calls:
        assert "join sessions on sessions.id = runs.session_id" in sql
        assert "left join sessions" not in sql
        assert "sessions.tenant_id = runs.tenant_id" in sql
        assert "sessions.workspace_id = runs.workspace_id" in sql
        assert "sessions.user_id = runs.user_id" in sql
        assert "sessions.agent_id = runs.agent_id" in sql
        assert "sessions.status = 'active'" in sql
        assert params == ("tenant-a", "user-a")

    session["status"] = "deleted"
    assert await repositories.list_revealed_artifacts(
        conn,
        tenant_id="tenant-a",
        user_id="user-a",
    ) == []
    assert await repositories.list_revealed_artifact_sessions(
        conn,
        tenant_id="tenant-a",
        user_id="user-a",
    ) == []

    session["status"] = "active"
    session["workspace_id"] = "other-workspace"
    assert await repositories.list_revealed_artifacts(
        conn,
        tenant_id="tenant-a",
        user_id="user-a",
    ) == []


@pytest.mark.asyncio
async def test_authorized_session_runs_use_canonical_legacy_tie_break_order():
    conn = RecordingConnection()

    await repositories.list_authorized_session_runs(
        conn,
        tenant_id="tenant-a",
        user_id="user-a",
        session_id="session-a",
        limit=50,
    )

    sql, params = conn.calls[-1]
    assert "queue_admission_ordinal" in sql
    assert "event_type = 'queued'" in sql
    assert "payload_json->>'queue_admission_ordinal'" in sql
    assert "~ '^[0-9]+$'" in sql
    assert "length(run_events.payload_json->>'queue_admission_ordinal') <= 19" in sql
    assert "<= '9223372036854775807'" in sql
    assert "case when" in sql
    created_at_order = sql.index("runs.created_at desc")
    ordinal_order = sql.index("queue_admission.queue_admission_ordinal desc nulls last")
    queued_at_order = sql.index("runs.queued_at desc nulls last")
    id_order = sql.index("runs.id desc")
    assert created_at_order < ordinal_order < queued_at_order < id_order
    assert params == ("tenant-a", "user-a", "session-a", 50)


@pytest.mark.asyncio
async def test_authorized_session_runs_can_bind_one_workspace_for_continuation_inheritance():
    conn = RecordingConnection()

    await repositories.list_authorized_session_runs(
        conn,
        tenant_id="tenant-a",
        user_id="user-a",
        session_id="session-a",
        workspace_id="workspace-a",
        limit=1,
    )

    sql, params = conn.calls[-1]
    assert "runs.workspace_id = %s" in sql
    assert params == ("tenant-a", "user-a", "session-a", "workspace-a", 1)


@pytest.mark.parametrize(
    "raw,expected_valid",
    [
        ("0", True),
        ("9223372036854775807", True),
        ("9223372036854775808", False),
        ("999999999999999999999999", False),
        ("-1", False),
        ("not-a-number", False),
    ],
)
def test_queue_admission_ordinal_bigint_guard_boundaries(raw, expected_valid):
    valid = (
        raw.isdigit()
        and len(raw) <= 19
        and (len(raw) < 19 or raw <= "9223372036854775807")
    )
    assert valid is expected_valid


@pytest.mark.asyncio
async def test_authorized_messages_bind_tenant_session_owner_and_stable_order():
    conn = RecordingConnection()

    await repositories.list_authorized_messages(
        conn,
        tenant_id="tenant-a",
        user_id="user-a",
        session_id="session-a",
    )

    sql, params = conn.calls[-1]
    assert "join sessions" in sql
    assert "messages.tenant_id = %s" in sql
    assert "messages.session_id = %s" in sql
    assert "sessions.user_id = %s" in sql
    assert "order by messages.created_at asc, messages.id asc" in sql
    assert params == ("tenant-a", "session-a", "user-a")


@pytest.mark.asyncio
async def test_authorized_user_messages_for_runs_minimize_and_scope_in_sql():
    query = getattr(repositories, "list_authorized_user_messages_for_runs", None)
    assert callable(query), "dedicated authorized run-message projection is missing"
    conn = RecordingConnection()

    rows = await query(
        conn,
        tenant_id="tenant-a",
        user_id="user-a",
        session_id="session-a",
        run_ids=["run-b", "run-a", "run-b", ""],
    )

    assert rows == []
    sql, params = conn.calls[-1]
    select_clause = sql.split("from messages", 1)[0]
    assert "messages.id" in select_clause
    assert "messages.run_id" in select_clause
    assert "messages.content" in select_clause
    assert "messages.created_at" in select_clause
    assert "messages.metadata_json" in select_clause
    assert "role" not in select_clause
    assert "join sessions" in sql
    assert "messages.tenant_id = %s" in sql
    assert "messages.session_id = %s" in sql
    assert "sessions.user_id = %s" in sql
    assert "messages.role = 'user'" in sql
    assert "messages.run_id = any(%s::text[])" in sql
    assert params == ("tenant-a", "session-a", "user-a", ["run-b", "run-a"])


@pytest.mark.asyncio
async def test_authorized_user_messages_for_runs_empty_target_is_query_free():
    query = getattr(repositories, "list_authorized_user_messages_for_runs", None)
    assert callable(query), "dedicated authorized run-message projection is missing"
    conn = RecordingConnection()

    rows = await query(
        conn,
        tenant_id="tenant-a",
        user_id="user-a",
        session_id="session-a",
        run_ids=[],
    )

    assert rows == []
    assert conn.calls == []


@pytest.mark.asyncio
async def test_capability_distribution_backfill_marks_completion_and_never_recreates_after_rerun():
    backfill = getattr(repositories, "ensure_tenant_capability_distribution_backfill", None)
    assert callable(backfill), "ensure_tenant_capability_distribution_backfill missing"

    class Cursor:
        def __init__(self, row=None):
            self.row = row

        async def fetchone(self):
            return self.row

        async def fetchall(self):
            return []

    class Connection:
        def __init__(self):
            self.calls = []
            self.completed = False

        async def execute(self, sql, params=()):
            compact = " ".join(sql.split())
            self.calls.append((compact, params))
            if compact.startswith("select completed_at from tenant_capability_distribution_backfills"):
                return Cursor({"completed_at": "now" if self.completed else None})
            if compact.startswith("update tenant_capability_distribution_backfills set completed_at = now()"):
                self.completed = True
            return Cursor()

    conn = Connection()
    await backfill(conn, tenant_id="tenant-a")
    await backfill(conn, tenant_id="tenant-a")

    assert len(conn.calls) == 7
    initial_check, marker_insert, marker_lock, skill_call, mcp_call, completion_update, rerun_check = conn.calls
    skill_sql, skill_params = skill_call
    mcp_sql, mcp_params = mcp_call
    assert "for update" not in initial_check[0]
    assert initial_check[1] == ("tenant-a",)
    assert "insert into tenant_capability_distribution_backfills" in marker_insert[0]
    assert marker_insert[1] == ("tenant-a",)
    assert "for update" in marker_lock[0]
    assert marker_lock[1] == ("tenant-a",)
    assert completion_update[1] == ("tenant-a",)
    assert rerun_check == initial_check
    assert "from tenant_workbench_skills" in skill_sql
    assert "from skills" in skill_sql
    assert skill_sql.count("skills.status = 'active'") == 2
    assert "on conflict (tenant_id, capability_kind, capability_id) do nothing" in skill_sql
    assert "do update" not in skill_sql
    assert "from mcp_servers" in mcp_sql
    assert "department_ids" in mcp_sql
    assert "allowed_roles" in mcp_sql
    assert "jsonb_typeof(mcp_servers.allowed_roles) is distinct from 'array'" in mcp_sql
    assert "jsonb_array_elements" in mcp_sql
    assert "jsonb_typeof(role_value) is distinct from 'string'" in mcp_sql
    assert "select distinct lower(btrim(role_value #>> '{}'))" in mcp_sql
    assert "unnest(mcp_servers.department_ids)" in mcp_sql
    assert "department_validation.scope_valid" in mcp_sql
    assert "not role_validation.scope_valid or not department_validation.scope_valid" in mcp_sql
    assert "else 'disabled'" in mcp_sql
    assert "on conflict (tenant_id, capability_kind, capability_id) do nothing" in mcp_sql
    assert "do update" not in mcp_sql
    assert skill_sql.count("%s") == len(skill_params)
    assert mcp_sql.count("%s") == len(mcp_params)
    assert skill_params == (
        "tenant-a",
        "tenant-a",
        "tenant-a",
        "tenant-a",
        sorted(repositories.PUBLIC_WORKBENCH_SKILL_IDS),
    )
    assert sum("from tenant_workbench_skills" in sql for sql, _ in conn.calls) == 1
    assert sum("from mcp_servers" in sql for sql, _ in conn.calls) == 1


@pytest.mark.asyncio
async def test_capability_distribution_backfill_lock_recheck_observes_concurrent_completion():
    class Cursor:
        def __init__(self, row=None):
            self.row = row

        async def fetchone(self):
            return self.row

    class Connection:
        def __init__(self):
            self.calls = []

        async def execute(self, sql, params=()):
            compact = " ".join(sql.split())
            self.calls.append((compact, params))
            if compact.startswith("select completed_at from tenant_capability_distribution_backfills"):
                if "for update" in compact:
                    return Cursor({"completed_at": "completed-by-concurrent-transaction"})
                return Cursor({"completed_at": None})
            return Cursor()

    conn = Connection()
    await repositories.ensure_tenant_capability_distribution_backfill(conn, tenant_id="tenant-a")

    assert len(conn.calls) == 3
    initial_check, marker_insert, locked_recheck = conn.calls
    assert "for update" not in initial_check[0]
    assert "insert into tenant_capability_distribution_backfills" in marker_insert[0]
    assert "for update" in locked_recheck[0]
    assert not any("from tenant_workbench_skills" in sql for sql, _ in conn.calls)
    assert not any("from mcp_servers" in sql for sql, _ in conn.calls)
    assert not any("set completed_at = now()" in sql for sql, _ in conn.calls)


@pytest.mark.asyncio
async def test_resolve_agent_skill_uses_global_skill_lifecycle_and_canonical_backing_tool():
    conn = SingleRowConnection(
        {
            "agent_id": "sop-assistant",
            "agent_status": "active",
            "default_skill_id": "ragflow-knowledge-search",
            "skill_id": "ragflow-knowledge-search",
            "skill_status": "active",
            "skill_version": "0.1.0",
            "skill_version_status": "active",
            "executor_type": "ragflow",
            "backing_mcp_tool_id": "ragflow-knowledge-search",
            "input_modes": ["chat"],
        }
    )

    row = await repositories.resolve_agent_skill(
        conn,
        tenant_id="tenant-a",
        agent_id="sop-assistant",
        skill_id="ragflow-knowledge-search",
    )

    assert row["backing_mcp_tool_id"] == "ragflow-knowledge-search"
    assert "skills.status as skill_status" in conn.sql
    assert "tenant_workbench_skills" not in conn.sql


@pytest.mark.asyncio
async def test_resolve_selected_skill_allows_active_non_default_skill():
    conn = SingleRowConnection(
        {
            "agent_id": "general-agent",
            "agent_status": "active",
            "default_skill_id": "general-chat",
            "skill_id": "department-review",
            "skill_status": "active",
            "skill_version": "hash-review-v1",
            "skill_content_hash": "hash-review-v1",
            "skill_version_status": "active",
            "release_policy_version": "hash-review-v1",
            "release_policy_previous_version": None,
            "release_policy_rollout_percent": 100,
            "executor_type": "claude-agent-worker",
            "backing_mcp_tool_id": None,
            "input_modes": ["docx"],
        }
    )

    row = await repositories.resolve_selected_skill(
        conn,
        tenant_id="tenant-a",
        agent_id="general-agent",
        skill_id="department-review",
    )

    assert row["skill_id"] == "department-review"
    assert row["default_skill_id"] == "general-chat"


@pytest.mark.asyncio
async def test_resolve_selected_skill_rejects_non_materializable_version_identity():
    conn = SingleRowConnection(
        {
            "agent_id": "general-agent",
            "agent_status": "active",
            "default_skill_id": "general-chat",
            "skill_id": "department-review",
            "skill_status": "active",
            "skill_version": "hash-review-v1",
            "skill_content_hash": "different-content-hash",
            "skill_version_status": "active",
            "release_policy_version": "hash-review-v1",
            "release_policy_previous_version": None,
            "release_policy_rollout_percent": 100,
            "executor_type": "claude-agent-worker",
            "backing_mcp_tool_id": None,
            "input_modes": ["docx"],
        }
    )

    with pytest.raises(RepositoryConflictError, match="skill_version_not_materializable"):
        await repositories.resolve_selected_skill(
            conn,
            tenant_id="tenant-a",
            agent_id="general-agent",
            skill_id="department-review",
        )


@pytest.mark.asyncio
async def test_authorize_selected_run_capabilities_returns_stable_stale_conflict_without_current_version(
    monkeypatch,
):
    async def resolve_selected(conn, *, tenant_id, agent_id, skill_id):
        return {
            "agent_id": agent_id,
            "agent_status": "active",
            "skill_id": skill_id,
            "skill_status": "active",
            "skill_version": "hash-v2",
            "skill_content_hash": "hash-v2",
            "skill_version_status": "active",
            "release_policy_version": "hash-v2",
            "release_policy_previous_version": None,
            "release_policy_rollout_percent": 100,
            "executor_type": "claude-agent-worker",
            "input_modes": [],
        }

    async def distribution(conn, **kwargs):
        return {
            "status": "active",
            "visible_to_user": True,
            "scope_mode": "allowlist",
            "department_ids": ["qa"],
            "allowed_roles": ["reviewer"],
        }

    async def exact_version(conn, *, skill_id, version):
        return {"skill_id": skill_id, "version": version, "content_hash": version, "status": "active"}

    monkeypatch.setattr(repositories, "resolve_selected_skill", resolve_selected, raising=False)
    monkeypatch.setattr(repositories, "get_capability_distribution_row", distribution)
    monkeypatch.setattr(repositories, "get_effective_skill_version_for_policy", exact_version)

    with pytest.raises(RepositoryConflictError) as exc_info:
        await repositories.authorize_selected_run_capabilities(
            object(),
            tenant_id="tenant-a",
            agent_id="general-agent",
            skill_id="department-review",
            expected_version="hash-v1",
            rollout_key="user-a",
            normalized_input={},
            principal_department_id="qa",
            principal_roles=["reviewer"],
            is_admin=False,
            permissions=[],
        )

    assert str(exc_info.value) == "skill_selection_stale"
    assert "hash-v2" not in str(exc_info.value)


@pytest.mark.asyncio
async def test_authorize_selected_run_capabilities_rejects_version_content_hash_invariant(
    monkeypatch,
):
    async def resolve_selected(conn, *, tenant_id, agent_id, skill_id):
        return {
            "agent_id": agent_id,
            "agent_status": "active",
            "skill_id": skill_id,
            "skill_status": "active",
            "skill_version": "hash-v1",
            "skill_content_hash": "different-hash",
            "skill_version_status": "active",
            "release_policy_version": None,
            "release_policy_previous_version": None,
            "release_policy_rollout_percent": 100,
            "executor_type": "claude-agent-worker",
            "input_modes": [],
        }

    async def distribution(conn, **kwargs):
        return {
            "status": "active",
            "visible_to_user": True,
            "scope_mode": "allowlist",
            "department_ids": [],
            "allowed_roles": [],
        }

    monkeypatch.setattr(repositories, "resolve_selected_skill", resolve_selected, raising=False)
    monkeypatch.setattr(repositories, "get_capability_distribution_row", distribution)

    with pytest.raises(repositories.RepositoryAuthorizationError, match="capability_not_authorized"):
        await repositories.authorize_selected_run_capabilities(
            object(),
            tenant_id="tenant-a",
            agent_id="general-agent",
            skill_id="department-review",
            expected_version="hash-v1",
            rollout_key="user-a",
            normalized_input={},
            principal_department_id="qa",
            principal_roles=["reviewer"],
            is_admin=False,
            permissions=[],
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("historical_status", ["active", "released", "deprecated"])
async def test_authorize_replay_run_capabilities_keeps_exact_v1_after_current_v2(
    monkeypatch,
    historical_status,
):
    async def resolve_selected(conn, *, tenant_id, agent_id, skill_id):
        return {
            "agent_id": agent_id,
            "agent_status": "active",
            "skill_id": skill_id,
            "skill_status": "active",
            "skill_version": "hash-v2",
            "skill_content_hash": "hash-v2",
            "skill_version_status": "active",
            "release_policy_version": "hash-v2",
            "release_policy_previous_version": "hash-v1",
            "release_policy_rollout_percent": 100,
            "executor_type": "claude-agent-worker",
            "input_modes": [],
        }

    async def distribution(conn, **kwargs):
        return {"status": "active", "visible_to_user": True, "department_ids": [], "allowed_roles": []}

    async def historical_version(conn, *, skill_id, version):
        assert version == "hash-v1"
        return {"skill_id": skill_id, "version": "hash-v1", "content_hash": "hash-v1", "status": historical_status}

    monkeypatch.setattr(repositories, "resolve_selected_skill", resolve_selected, raising=False)
    monkeypatch.setattr(repositories, "get_capability_distribution_row", distribution)
    monkeypatch.setattr(repositories, "get_skill_version", historical_version)

    skill = await repositories.authorize_replay_run_capabilities(
        object(),
        tenant_id="tenant-a",
        agent_id="general-agent",
        skill_id="department-review",
        pinned_version="hash-v1",
        pinned_executor_type="claude-agent-worker",
        skill_manifests=[
            {
                "skill_id": "department-review",
                "version": "hash-v1",
                "content_hash": "hash-v1",
                "source": {"kind": "uploaded"},
                "files": [{"relative_path": "SKILL.md", "content_base64": "c2tpbGw=", "size_bytes": 5}],
                "dependency_ids": [],
                "mcp_tool_ids": [],
            }
        ],
        normalized_input={},
        principal_department_id="qa",
        principal_roles=["reviewer"],
        is_admin=False,
        permissions=[],
    )

    assert skill["skill_version"] == "hash-v2"


@pytest.mark.asyncio
@pytest.mark.parametrize("historical_status", ["disabled", "security_revoked"])
async def test_authorize_replay_run_capabilities_blocks_revoked_historical_pin(
    monkeypatch,
    historical_status,
):
    async def resolve_selected(conn, *, tenant_id, agent_id, skill_id):
        return {
            "agent_id": agent_id,
            "agent_status": "active",
            "skill_id": skill_id,
            "skill_status": "active",
            "skill_version": "hash-v2",
            "skill_content_hash": "hash-v2",
            "skill_version_status": "active",
            "release_policy_version": "hash-v2",
            "release_policy_previous_version": None,
            "release_policy_rollout_percent": 100,
            "executor_type": "claude-agent-worker",
            "input_modes": [],
        }

    async def distribution(conn, **kwargs):
        return {"status": "active", "visible_to_user": True, "department_ids": [], "allowed_roles": []}

    async def historical_version(conn, *, skill_id, version):
        return {"skill_id": skill_id, "version": version, "content_hash": version, "status": historical_status}

    monkeypatch.setattr(repositories, "resolve_selected_skill", resolve_selected, raising=False)
    monkeypatch.setattr(repositories, "get_capability_distribution_row", distribution)
    monkeypatch.setattr(repositories, "get_skill_version", historical_version)

    with pytest.raises(repositories.RepositoryAuthorizationError, match="capability_not_authorized"):
        await repositories.authorize_replay_run_capabilities(
            object(),
            tenant_id="tenant-a",
            agent_id="general-agent",
            skill_id="department-review",
            pinned_version="hash-v1",
            pinned_executor_type="claude-agent-worker",
            skill_manifests=[
                {
                    "skill_id": "department-review",
                    "version": "hash-v1",
                    "content_hash": "hash-v1",
                    "source": {"kind": "uploaded"},
                    "files": [{"relative_path": "SKILL.md", "content_base64": "c2tpbGw=", "size_bytes": 5}],
                    "dependency_ids": [],
                    "mcp_tool_ids": [],
                }
            ],
            normalized_input={},
            principal_department_id="qa",
            principal_roles=["reviewer"],
            is_admin=False,
            permissions=[],
        )


@pytest.mark.asyncio
async def test_authorize_replay_run_capabilities_reauthorizes_pinned_historical_mcp(monkeypatch):
    calls = []

    async def shared_authorizer(conn, **kwargs):
        calls.append(kwargs["normalized_input"])
        return {
            "skill_id": kwargs["skill_id"],
            "executor_type": "claude-agent-worker",
            "skill_version": "hash-v2",
        }

    async def historical_version(conn, *, skill_id, version):
        return {
            "skill_id": skill_id,
            "version": version,
            "content_hash": version,
            "status": "active",
        }

    monkeypatch.setattr(repositories, "_authorize_run_capabilities", shared_authorizer)
    monkeypatch.setattr(repositories, "get_skill_version", historical_version)

    await repositories.authorize_replay_run_capabilities(
        object(),
        tenant_id="tenant-a",
        agent_id="general-agent",
        skill_id="knowledge-v1",
        pinned_version="hash-v1",
        pinned_executor_type="ragflow",
        skill_manifests=[
            {
                "skill_id": "knowledge-v1",
                "version": "hash-v1",
                "content_hash": "hash-v1",
                "source": {"kind": "uploaded"},
                "files": [{"relative_path": "SKILL.md", "content_base64": "c2tpbGw=", "size_bytes": 5}],
                "dependency_ids": [],
                "mcp_tool_ids": ["historical-search"],
            }
        ],
        normalized_input={"message": "search"},
        principal_department_id="qa",
        principal_roles=["reviewer"],
        is_admin=False,
        permissions=[],
    )

    assert calls == [{"message": "search", "mcp_tool_ids": ["historical-search"]}]


def test_run_skill_snapshot_source_recomputes_file_and_release_identity():
    manifest = {
        "skill_id": "department-review",
        "version": "hash-v1",
        "content_hash": "hash-v1",
        "source": {"kind": "uploaded", "storage_key": "private/package.zip"},
        "files": [{"relative_path": "SKILL.md", "content_base64": "c2tpbGw=", "size_bytes": 5}],
        "dependency_ids": [],
        "mcp_tool_ids": [],
        "mcp_tool_ids": [],
        "snapshot_governance": {"selected_files": [{"sha256": "caller-controlled"}]},
    }

    locked = repositories.run_skill_snapshot_source_json(
        manifest,
        release_decision={"selected_version": "hash-v1", "selected_track": "current"},
    )
    changed_file = repositories.run_skill_snapshot_source_json(
        {
            **manifest,
            "files": [{"relative_path": "SKILL.md", "content_base64": "ZHJpZnQ=", "size_bytes": 5}],
        },
        release_decision={"selected_version": "hash-v1", "selected_track": "current"},
    )
    changed_release = repositories.run_skill_snapshot_source_json(
        manifest,
        release_decision={"selected_version": "hash-v1", "selected_track": "previous"},
    )

    assert locked["snapshot_governance"]["selected_files"][0]["sha256"] != "caller-controlled"
    assert locked != changed_file
    assert locked["release_decision_sha256"] != changed_release["release_decision_sha256"]
    assert "files" not in locked
    assert "storage_key" not in locked


@pytest.mark.asyncio
async def test_copy_run_as_new_task_rejects_source_snapshot_mismatch_before_writes(monkeypatch):
    source_manifest = {
        "skill_id": "department-review",
        "version": "hash-v1",
        "content_hash": "hash-v1",
        "source": {"kind": "uploaded"},
        "files": [{"relative_path": "SKILL.md", "content_base64": "c2tpbGw=", "size_bytes": 5}],
        "dependency_ids": [],
        "mcp_tool_ids": [],
    }

    async def source_run(conn, **kwargs):
        return {
            "id": "run-source",
            "workspace_id": "default",
            "session_id": "ses-source",
            "agent_id": "general-agent",
            "skill_id": "department-review",
            "principal_roles": ["reviewer"],
            "principal_department_id": "qa",
            "input_json": {
                "input": {"message": "review"},
                "executor_type": "claude-agent-worker",
                "skill_version": "hash-v1",
                "release_decision": {"selected_version": "hash-v1", "selected_track": "current"},
                "skill_manifests": [source_manifest],
            },
        }

    async def mismatch(*args, **kwargs):
        raise RepositoryConflictError("run_skill_snapshot_identity_mismatch")

    async def forbidden_replay(*args, **kwargs):
        raise AssertionError("source snapshot mismatch must deny before replay authorization or writes")

    monkeypatch.setattr(repositories, "get_authorized_run", source_run)
    monkeypatch.setattr(repositories, "validate_run_skill_snapshots_for_dispatch", mismatch)
    monkeypatch.setattr(repositories, "authorize_replay_run_capabilities", forbidden_replay)

    with pytest.raises(RepositoryConflictError, match="run_skill_snapshot_identity_mismatch"):
        await repositories.copy_run_as_new_task(
            object(),
            tenant_id="tenant-a",
            user_id="user-a",
            run_id="run-source",
        )


@pytest.mark.asyncio
async def test_copy_run_as_new_task_reauthorizes_but_persists_source_v1_provenance(monkeypatch):
    source_manifest = {
        "skill_id": "department-review",
        "version": "hash-v1",
        "content_hash": "hash-v1",
        "source": {"kind": "uploaded"},
        "files": [{"relative_path": "SKILL.md", "content_base64": "c2tpbGw=", "size_bytes": 5}],
        "dependency_ids": [],
        "mcp_tool_ids": [],
        "allowed": True,
        "staged": False,
        "used": False,
    }
    source_release = {
        "schema_version": "ai-platform.skill-release-decision.v1",
        "policy_active": True,
        "selected_version": "hash-v1",
        "selected_track": "current",
    }
    source = {
        "id": "run-source",
        "tenant_id": "tenant-a",
        "workspace_id": "workspace-a",
        "session_id": "session-a",
        "user_id": "user-a",
        "agent_id": "general-agent",
        "skill_id": "department-review",
        "principal_roles": ["reviewer"],
        "principal_department_id": "qa",
        "auth_source": "session-token",
        "input_json": {
            "input": {"message": "retry"},
            "file_ids": [],
            "executor_type": "claude-agent-worker",
            "skill_version": "hash-v1",
            "release_decision": source_release,
            "skill_manifests": [source_manifest],
        },
    }
    calls = {}

    async def get_source(*args, **kwargs):
        return source

    async def authorize_replay(conn, **kwargs):
        calls["authorize"] = kwargs
        return {"executor_type": "claude-agent-worker", "skill_version": "hash-v2"}

    async def validate_source(conn, **kwargs):
        calls["source_snapshot"] = kwargs

    async def no_completed(*args, **kwargs):
        return {}, {}

    async def no_write(*args, **kwargs):
        return None

    async def insert_snapshots(conn, **kwargs):
        calls["snapshots"] = kwargs

    monkeypatch.setattr(repositories, "get_authorized_run", get_source)
    monkeypatch.setattr(repositories, "authorize_replay_run_capabilities", authorize_replay)
    monkeypatch.setattr(repositories, "validate_run_skill_snapshots_for_dispatch", validate_source)
    monkeypatch.setattr(repositories, "_completed_steps_for_resume", no_completed)
    monkeypatch.setattr(repositories, "append_event", no_write)
    monkeypatch.setattr(repositories, "append_message", no_write)
    monkeypatch.setattr(repositories, "insert_run_skill_snapshots_at_creation", insert_snapshots)

    copied = await repositories.copy_run_as_new_task(
        RecordingConnection(),
        tenant_id="tenant-a",
        user_id="user-a",
        run_id="run-source",
    )

    assert calls["authorize"]["pinned_version"] == "hash-v1"
    assert calls["authorize"]["pinned_executor_type"] == "claude-agent-worker"
    assert calls["authorize"]["skill_manifests"] == [source_manifest]
    assert calls["source_snapshot"]["run_id"] == "run-source"
    assert calls["source_snapshot"]["release_decision"] == source_release
    assert calls["snapshots"]["skill_manifests"] == [source_manifest]
    assert copied["skill_version"] == "hash-v1"
    assert copied["release_decision"] == source_release
    assert copied["skill_manifests"] == [source_manifest]


@pytest.mark.asyncio
async def test_capability_distribution_list_and_get_normalize_array_and_json_projections():
    list_rows = getattr(repositories, "list_capability_distribution_rows", None)
    get_row = getattr(repositories, "get_capability_distribution_row", None)
    assert callable(list_rows), "list_capability_distribution_rows missing"
    assert callable(get_row), "get_capability_distribution_row missing"

    row = {
        "id": "capdist_a",
        "tenant_id": "tenant-a",
        "capability_kind": "mcp_server",
        "capability_id": "qa-mcp",
        "status": "active",
        "visible_to_user": True,
        "scope_mode": "allowlist",
        "department_ids": ("qa",),
        "allowed_roles": '["qa_operator"]',
        "metadata_json": '{"legacy_source":"mcp_servers"}',
        "updated_by": "admin-a",
        "created_at": None,
        "updated_at": None,
    }

    class Cursor:
        async def fetchone(self):
            return row

        async def fetchall(self):
            return [row]

    class Connection:
        async def execute(self, sql, params=()):
            return Cursor()

    conn = Connection()
    listed = await list_rows(conn, tenant_id="tenant-a", capability_kind="mcp_server", include_disabled=False)
    fetched = await get_row(conn, tenant_id="tenant-a", capability_kind="mcp_server", capability_id="qa-mcp")

    assert listed == [fetched]
    assert fetched["department_ids"] == ["qa"]
    assert fetched["allowed_roles"] == ["qa_operator"]
    assert fetched["metadata_json"] == {"legacy_source": "mcp_servers"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "allowed_roles",
    ['{"unexpected":"object"}', '[1,"qa"]', '[""]', '["   "]'],
)
async def test_capability_distribution_projection_rejects_malformed_allowed_roles(allowed_roles):
    row = {
        "id": "capdist-malformed",
        "tenant_id": "tenant-a",
        "capability_kind": "mcp_server",
        "capability_id": "unsafe-mcp",
        "status": "active",
        "visible_to_user": True,
        "scope_mode": "allowlist",
        "department_ids": ("QA",),
        "allowed_roles": allowed_roles,
        "metadata_json": '{}',
        "updated_by": "admin-a",
        "created_at": None,
        "updated_at": None,
    }

    class Cursor:
        async def fetchone(self):
            return row

    class Connection:
        async def execute(self, sql, params=()):
            return Cursor()

    with pytest.raises(repositories.RepositoryConflictError, match="capability_distribution_scope_invalid"):
        await repositories.get_capability_distribution_row(
            Connection(),
            tenant_id="tenant-a",
            capability_kind="mcp_server",
            capability_id="unsafe-mcp",
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("department_ids", [("QA", ""), ("QA", "   "), ("QA", None)])
async def test_capability_distribution_projection_rejects_malformed_department_ids(department_ids):
    row = {
        "id": "capdist-malformed-department",
        "tenant_id": "tenant-a",
        "capability_kind": "mcp_server",
        "capability_id": "unsafe-mcp",
        "status": "active",
        "visible_to_user": True,
        "scope_mode": "allowlist",
        "department_ids": department_ids,
        "allowed_roles": [],
        "metadata_json": {},
        "updated_by": "admin-a",
        "created_at": None,
        "updated_at": None,
    }

    class Cursor:
        async def fetchone(self):
            return row

    class Connection:
        async def execute(self, sql, params=()):
            return Cursor()

    with pytest.raises(repositories.RepositoryConflictError, match="capability_distribution_scope_invalid"):
        await repositories.get_capability_distribution_row(
            Connection(),
            tenant_id="tenant-a",
            capability_kind="mcp_server",
            capability_id="unsafe-mcp",
        )


@pytest.mark.asyncio
async def test_principal_agent_projection_filters_exact_scope_and_audits_admin_bypass(monkeypatch):
    rows = [
        {
            "id": "general-agent",
            "default_skill_id": "general-chat",
            "status": "active",
        },
        {
            "id": "qa-word-review",
            "default_skill_id": "qa-file-reviewer",
            "status": "active",
        },
        {
            "id": "baoyu-translate",
            "default_skill_id": "baoyu-translate",
            "status": "active",
        },
    ]
    distributions = [
        {
            "capability_kind": "skill",
            "capability_id": "general-chat",
            "status": "active",
            "visible_to_user": True,
            "scope_mode": "allowlist",
            "department_ids": ["QA"],
            "allowed_roles": ["qa-operator"],
        },
        {
            "capability_kind": "skill",
            "capability_id": "qa-file-reviewer",
            "status": "active",
            "visible_to_user": True,
            "scope_mode": "allowlist",
            "department_ids": ["qa"],
            "allowed_roles": ["qa-operator"],
        },
        {
            "capability_kind": "skill",
            "capability_id": "baoyu-translate",
            "status": "disabled",
            "visible_to_user": False,
            "scope_mode": "allowlist",
            "department_ids": ["translation"],
            "allowed_roles": ["translator"],
        },
    ]
    audits = []

    async def fake_list_agents(conn, *, tenant_id):
        assert tenant_id == "tenant-a"
        return rows

    async def fake_list_distributions(conn, *, tenant_id, capability_kind, include_disabled):
        assert (tenant_id, capability_kind, include_disabled) == ("tenant-a", "skill", True)
        return distributions

    async def fake_append_audit(conn, **kwargs):
        audits.append(kwargs)
        return f"audit-{len(audits)}"

    monkeypatch.setattr(repositories, "list_lambchat_agents", fake_list_agents)
    monkeypatch.setattr(repositories, "list_capability_distribution_rows", fake_list_distributions)
    monkeypatch.setattr(repositories, "append_audit_log", fake_append_audit)

    authorized = await repositories.list_principal_lambchat_agents(
        object(),
        tenant_id="tenant-a",
        actor_user_id="qa-user",
        department_id="QA",
        roles=[" QA-OPERATOR "],
        is_admin=False,
        permissions=["chat:read"],
    )
    admin_rows = await repositories.list_principal_lambchat_agents(
        object(),
        tenant_id="tenant-a",
        actor_user_id="admin-a",
        department_id="platform",
        roles=["admin"],
        is_admin=True,
        permissions=["chat:read"],
    )

    assert [row["id"] for row in authorized] == ["general-agent"]
    assert [row["id"] for row in admin_rows] == [
        "general-agent",
        "qa-word-review",
        "baoyu-translate",
    ]
    assert len(audits) == 3
    assert {audit["target_id"] for audit in audits} == {
        "general-chat",
        "qa-file-reviewer",
        "baoyu-translate",
    }
    for audit in audits:
        assert audit["action"] == "capability_distribution.admin_bypass"
        assert audit["target_type"] == "skill"
        assert audit["payload_json"]["admin_bypass"] is True
        assert audit["payload_json"]["decision_reason"] == "admin_bypass"


@pytest.mark.asyncio
@pytest.mark.parametrize("is_admin", [False, True])
async def test_principal_agent_projection_hides_archived_default_skill_for_every_principal(monkeypatch, is_admin):
    async def fake_list_agents(conn, *, tenant_id):
        return [
            {"id": "general-agent", "default_skill_id": "general-chat", "status": "active"},
            {"id": "qa-word-review", "default_skill_id": "qa-file-reviewer", "status": "active"},
        ]

    async def fake_list_distributions(conn, **kwargs):
        return [
            {
                "capability_kind": "skill",
                "capability_id": "general-chat",
                "status": "active",
                "visible_to_user": True,
                "scope_mode": "allowlist",
                "department_ids": [],
                "allowed_roles": [],
                "metadata_json": {},
            },
            {
                "capability_kind": "skill",
                "capability_id": "qa-file-reviewer",
                "status": "disabled",
                "visible_to_user": False,
                "scope_mode": "allowlist",
                "department_ids": [],
                "allowed_roles": [],
                "metadata": {"archived_at": "2026-07-15T00:00:00.000Z"},
            },
        ]

    audits = []

    async def fake_append_audit(conn, **kwargs):
        audits.append(kwargs)
        return "audit"

    monkeypatch.setattr(repositories, "list_lambchat_agents", fake_list_agents)
    monkeypatch.setattr(repositories, "list_capability_distribution_rows", fake_list_distributions)
    monkeypatch.setattr(repositories, "append_audit_log", fake_append_audit)

    rows = await repositories.list_principal_lambchat_agents(
        object(),
        tenant_id="tenant-a",
        actor_user_id="admin-a" if is_admin else "user-a",
        department_id="platform" if is_admin else "qa",
        roles=["admin"] if is_admin else ["qa_operator"],
        is_admin=is_admin,
        permissions=["chat:read"],
    )

    assert [row["id"] for row in rows] == ["general-agent"]
    assert [audit["target_id"] for audit in audits] == (["general-chat"] if is_admin else [])


@pytest.mark.asyncio
@pytest.mark.parametrize("previous_status", ["draft", "reviewed", "disabled", "deprecated"])
async def test_principal_agent_projection_hides_non_runnable_rollout_selected_previous_version(
    monkeypatch,
    previous_status,
):
    async def fake_list_agents(conn, *, tenant_id):
        return [
            {
                "id": "qa-word-review",
                "default_skill_id": "qa-file-reviewer",
                "status": "active",
                "skill_version": "hash-new",
                "skill_version_status": "released",
                "release_policy_version": "hash-new",
                "release_policy_previous_version": "hash-old",
                "release_policy_rollout_percent": 0,
                "release_policy_previous_version_status": previous_status,
            }
        ]

    async def fake_list_distributions(conn, **kwargs):
        return [
            {
                "capability_kind": "skill",
                "capability_id": "qa-file-reviewer",
                "status": "active",
                "visible_to_user": True,
                "scope_mode": "allowlist",
                "department_ids": [],
                "allowed_roles": [],
            }
        ]

    monkeypatch.setattr(repositories, "list_lambchat_agents", fake_list_agents)
    monkeypatch.setattr(repositories, "list_capability_distribution_rows", fake_list_distributions)

    rows = await repositories.list_principal_lambchat_agents(
        object(),
        tenant_id="tenant-a",
        actor_user_id="previous-track-user",
        department_id="QA",
        roles=["user"],
        is_admin=False,
        permissions=["chat:read"],
    )

    assert rows == []


@pytest.mark.asyncio
async def test_principal_agent_projection_projects_runnable_rollout_selected_previous_version(monkeypatch):
    async def fake_list_agents(conn, *, tenant_id):
        return [
            {
                "id": "qa-word-review",
                "default_skill_id": "qa-file-reviewer",
                "status": "active",
                "skill_version": "hash-new",
                "skill_version_status": "released",
                "release_policy_version": "hash-new",
                "release_policy_previous_version": "hash-old",
                "release_policy_rollout_percent": 0,
                "release_policy_previous_version_status": "released",
            }
        ]

    async def fake_list_distributions(conn, **kwargs):
        return [
            {
                "capability_kind": "skill",
                "capability_id": "qa-file-reviewer",
                "status": "active",
                "visible_to_user": True,
                "scope_mode": "allowlist",
                "department_ids": [],
                "allowed_roles": [],
            }
        ]

    monkeypatch.setattr(repositories, "list_lambchat_agents", fake_list_agents)
    monkeypatch.setattr(repositories, "list_capability_distribution_rows", fake_list_distributions)

    rows = await repositories.list_principal_lambchat_agents(
        object(),
        tenant_id="tenant-a",
        actor_user_id="previous-track-user",
        department_id="QA",
        roles=["user"],
        is_admin=False,
        permissions=["chat:read"],
    )

    assert rows[0]["skill_version"] == "hash-old"
    assert rows[0]["skill_version_status"] == "released"
    assert "release_policy_previous_version_status" not in rows[0]


@pytest.mark.asyncio
async def test_capability_distribution_upsert_and_toggle_raise_controlled_not_found_errors():
    upsert = getattr(repositories, "upsert_capability_distribution_row", None)
    toggle = getattr(repositories, "toggle_capability_distribution_row", None)
    assert callable(upsert), "upsert_capability_distribution_row missing"
    assert callable(toggle), "toggle_capability_distribution_row missing"

    class MissingCursor:
        async def fetchone(self):
            return None

    class Connection:
        async def execute(self, sql, params=()):
            return MissingCursor()

    conn = Connection()
    kwargs = {
        "tenant_id": "tenant-a",
        "capability_kind": "skill",
        "capability_id": "qa-file-reviewer",
    }
    with pytest.raises(RepositoryNotFoundError, match="capability_distribution_not_found"):
        await upsert(
            conn,
            **kwargs,
            status="active",
            visible_to_user=True,
            scope_mode="allowlist",
            department_ids=["qa"],
            allowed_roles=["qa_operator"],
            metadata_json={},
            updated_by="admin-a",
        )
    with pytest.raises(RepositoryNotFoundError, match="capability_distribution_not_found"):
        await toggle(conn, **kwargs, enabled=False, updated_by="admin-a")


@pytest.mark.asyncio
async def test_archive_capability_distribution_is_tenant_scoped_and_idempotent(monkeypatch):
    async def no_backfill(conn, *, tenant_id):
        return None

    class Cursor:
        def __init__(self, row):
            self.row = row

        async def fetchone(self):
            return self.row

    class Connection:
        def __init__(self):
            self.calls = []
            self.rows = {
                ("tenant-a", "skill", "qa-file-reviewer"): {
                    "id": "capdist-a",
                    "tenant_id": "tenant-a",
                    "capability_kind": "skill",
                    "capability_id": "qa-file-reviewer",
                    "status": "active",
                    "visible_to_user": True,
                    "scope_mode": "allowlist",
                    "department_ids": [],
                    "allowed_roles": [],
                    "metadata_json": {},
                    "updated_by": None,
                },
                ("tenant-b", "skill", "qa-file-reviewer"): {
                    "id": "capdist-b",
                    "tenant_id": "tenant-b",
                    "capability_kind": "skill",
                    "capability_id": "qa-file-reviewer",
                    "status": "active",
                    "visible_to_user": True,
                    "scope_mode": "allowlist",
                    "department_ids": [],
                    "allowed_roles": [],
                    "metadata_json": {},
                    "updated_by": None,
                },
            }

        async def execute(self, sql, params=()):
            compact = " ".join(sql.split())
            self.calls.append((compact, params))
            if "pg_advisory_xact_lock" in compact:
                return Cursor(None)
            if compact.startswith("select metadata_json"):
                tenant_id, capability_kind, capability_id = params
                row = self.rows.get((tenant_id, capability_kind, capability_id))
                return Cursor({"metadata_json": row["metadata_json"]} if row is not None else None)
            assert compact.startswith("update tenant_capability_distributions")
            preserve_existing_evidence, _, archived_by, updated_by, tenant_id, capability_kind, capability_id = params
            row = self.rows.get((tenant_id, capability_kind, capability_id))
            if row is None:
                return Cursor(None)
            metadata_json = row["metadata_json"]
            if not preserve_existing_evidence:
                metadata_json["archived_at"] = "2026-07-15T00:00:00.000Z"
                metadata_json["archived_by"] = archived_by[:255]
            row["status"] = "disabled"
            row["visible_to_user"] = False
            row["updated_by"] = updated_by
            return Cursor(dict(row))

    monkeypatch.setattr(repositories, "ensure_tenant_capability_distribution_backfill", no_backfill)
    conn = Connection()

    first = await repositories.archive_capability_distribution_row(
        conn,
        tenant_id="tenant-a",
        capability_kind="skill",
        capability_id="qa-file-reviewer",
        archived_by="admin-a",
    )
    second = await repositories.archive_capability_distribution_row(
        conn,
        tenant_id="tenant-a",
        capability_kind="skill",
        capability_id="qa-file-reviewer",
        archived_by="admin-b",
    )

    assert first["status"] == second["status"] == "disabled"
    assert first["visible_to_user"] is second["visible_to_user"] is False
    assert second["metadata_json"] == {
        "archived_at": "2026-07-15T00:00:00.000Z",
        "archived_by": "admin-a",
    }
    assert conn.rows[("tenant-b", "skill", "qa-file-reviewer")]["status"] == "active"
    assert conn.rows[("tenant-b", "skill", "qa-file-reviewer")]["metadata_json"] == {}
    assert all("delete from" not in sql for sql, _ in conn.calls)
    assert all(
        forbidden not in " ".join(sql for sql, _ in conn.calls)
        for forbidden in ("skill_versions", "package_objects", "run_skill_snapshots")
    )


@pytest.mark.parametrize(
    "archive_marker",
    [None, "", "invalid", "2026-02-30T00:00:00.000Z", [], {}, False],
)
def test_repository_archive_predicate_matches_strict_shared_timestamp_semantics(archive_marker):
    assert repositories.is_capability_distribution_archived(
        {"metadata_json": {"archived_at": archive_marker}}
    ) is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("existing_metadata", "preserve_evidence"),
    [
        (
            {"archived_at": "2026-07-15T00:00:00.000Z", "archived_by": "admin-a"},
            True,
        ),
        (
            {"archived_at": "invalid", "archived_by": ["admin-a"]},
            False,
        ),
        (
            {"archived_at": "2026-07-15T00:00:00.000Z", "archived_by": ["admin-a"]},
            False,
        ),
    ],
)
async def test_archive_distribution_preserves_only_valid_first_evidence(
    monkeypatch,
    existing_metadata,
    preserve_evidence,
):
    async def no_backfill(conn, *, tenant_id):
        return None

    class Cursor:
        def __init__(self, row):
            self.row = row

        async def fetchone(self):
            return self.row

    class Connection:
        async def execute(self, sql, params=()):
            compact = " ".join(sql.split())
            if "pg_advisory_xact_lock" in compact:
                return Cursor(None)
            if compact.startswith("select metadata_json"):
                return Cursor({"metadata_json": existing_metadata})
            assert compact.startswith("update tenant_capability_distributions")
            assert "case when %s::boolean" in compact
            assert params[0:2] == (preserve_evidence, preserve_evidence)
            metadata_json = (
                existing_metadata
                if preserve_evidence
                else {"archived_at": "2026-07-15T01:02:03.004Z", "archived_by": "admin-b"}
            )
            return Cursor(
                {
                    "id": "capdist-a",
                    "tenant_id": "tenant-a",
                    "capability_kind": "skill",
                    "capability_id": "qa-file-reviewer",
                    "status": "disabled",
                    "visible_to_user": False,
                    "scope_mode": "allowlist",
                    "department_ids": [],
                    "allowed_roles": [],
                    "metadata_json": metadata_json,
                    "updated_by": "admin-b",
                }
            )

    monkeypatch.setattr(repositories, "ensure_tenant_capability_distribution_backfill", no_backfill)
    archived = await repositories.archive_capability_distribution_row(
        Connection(),
        tenant_id="tenant-a",
        capability_kind="skill",
        capability_id="qa-file-reviewer",
        archived_by="admin-b",
    )

    assert archived["metadata_json"]["archived_at"] != "invalid"
    assert archived["metadata_json"]["archived_by"] == ("admin-a" if preserve_evidence else "admin-b")


@pytest.mark.asyncio
async def test_invalid_archive_marker_does_not_block_distribution_status_update(monkeypatch):
    async def no_backfill(conn, *, tenant_id):
        return None

    class Cursor:
        def __init__(self, row):
            self.row = row

        async def fetchone(self):
            return self.row

    class Connection:
        async def execute(self, sql, params=()):
            compact = " ".join(sql.split())
            if compact.startswith("select metadata_json"):
                return Cursor({"metadata_json": {"archived_at": "invalid"}})
            assert "metadata_json ? 'archived_at'" not in compact
            return Cursor(
                {
                    "id": "capdist-a",
                    "tenant_id": "tenant-a",
                    "capability_kind": "skill",
                    "capability_id": "qa-file-reviewer",
                    "status": "active",
                    "visible_to_user": True,
                    "scope_mode": "allowlist",
                    "department_ids": [],
                    "allowed_roles": [],
                    "metadata_json": {"archived_at": "invalid"},
                }
            )

    monkeypatch.setattr(repositories, "ensure_tenant_capability_distribution_backfill", no_backfill)
    row = await repositories.toggle_capability_distribution_row(
        Connection(),
        tenant_id="tenant-a",
        capability_kind="skill",
        capability_id="qa-file-reviewer",
        enabled=True,
        updated_by="admin-a",
    )

    assert row["status"] == "active"


@pytest.mark.asyncio
@pytest.mark.parametrize("actor", [None, "", "   ", [], "x" * 256])
async def test_archive_distribution_rejects_invalid_actor_before_database_write(monkeypatch, actor):
    async def fail_backfill(*args, **kwargs):
        raise AssertionError("invalid archive actor must fail before database access")

    monkeypatch.setattr(repositories, "ensure_tenant_capability_distribution_backfill", fail_backfill)

    with pytest.raises(RepositoryConflictError, match="capability_distribution_archive_actor_invalid"):
        await repositories.archive_capability_distribution_row(
            object(),
            tenant_id="tenant-a",
            capability_kind="skill",
            capability_id="qa-file-reviewer",
            archived_by=actor,
        )


@pytest.mark.asyncio
async def test_batch_lifecycle_locks_use_canonical_order_without_duplicates(monkeypatch):
    class Cursor:
        async def fetchone(self):
            return None

    class Connection:
        def __init__(self):
            self.calls = []

        async def execute(self, sql, params):
            self.calls.append((" ".join(sql.split()), params))
            events.append(("lock", params[0]))
            return Cursor()

    conn = Connection()
    events = []

    async def completed_backfill(conn, *, tenant_id):
        events.append(("ensure", tenant_id))

    monkeypatch.setattr(repositories, "ensure_tenant_capability_distribution_backfill", completed_backfill)
    await repositories.acquire_capability_distribution_lifecycle_locks(
        conn,
        tenant_id="tenant-a",
        capability_kind="skill",
        capability_ids=["skill-b", "skill-a", "skill-b"],
    )

    assert events == [
        ("ensure", "tenant-a"),
        ("lock", '{"capability_id":"skill-a","capability_kind":"skill","tenant_id":"tenant-a"}'),
        ("lock", '{"capability_id":"skill-b","capability_kind":"skill","tenant_id":"tenant-a"}'),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["archive", "upsert", "toggle", "set_status"])
async def test_capability_distribution_lifecycle_lock_precedes_row_lock_and_write(monkeypatch, operation):
    async def no_backfill(conn, *, tenant_id):
        return None

    class Cursor:
        def __init__(self, row):
            self.row = row

        async def fetchone(self):
            return self.row

    class Connection:
        def __init__(self):
            self.calls = []

        async def execute(self, sql, params=()):
            compact = " ".join(sql.split())
            self.calls.append((compact, params))
            if "pg_advisory_xact_lock" in compact:
                return Cursor(None)
            if compact.startswith("select metadata_json"):
                return Cursor(
                    {"metadata_json": {}}
                    if operation in {"archive", "toggle"}
                    else None
                )
            return Cursor(
                {
                    "id": "capdist-a",
                    "tenant_id": "tenant-a",
                    "capability_kind": "skill",
                    "capability_id": "qa-file-reviewer",
                    "status": "disabled" if operation == "archive" else "active",
                    "visible_to_user": operation != "archive",
                    "scope_mode": "allowlist",
                    "department_ids": [],
                    "allowed_roles": [],
                    "metadata_json": {},
                }
            )

    monkeypatch.setattr(repositories, "ensure_tenant_capability_distribution_backfill", no_backfill)
    conn = Connection()
    kwargs = {
        "tenant_id": "tenant-a",
        "capability_kind": "skill",
        "capability_id": "qa-file-reviewer",
    }

    if operation == "archive":
        await repositories.archive_capability_distribution_row(conn, **kwargs, archived_by="admin-a")
    elif operation == "upsert":
        await repositories.upsert_capability_distribution_row(
            conn,
            **kwargs,
            status="active",
            visible_to_user=True,
            scope_mode="allowlist",
            department_ids=[],
            allowed_roles=[],
            metadata_json={},
            updated_by="admin-a",
        )
    elif operation == "toggle":
        await repositories.toggle_capability_distribution_row(conn, **kwargs, enabled=True, updated_by="admin-a")
    else:
        await repositories.set_capability_distribution_status(conn, **kwargs, status="active", updated_by="admin-a")

    assert "pg_advisory_xact_lock" in conn.calls[0][0]
    assert conn.calls[0][1] == ('{"capability_id":"qa-file-reviewer","capability_kind":"skill","tenant_id":"tenant-a"}',)
    assert conn.calls[1][0].startswith("select metadata_json")
    assert conn.calls[1][0].endswith("for update")
    assert conn.calls[2][0].startswith(("insert into tenant_capability_distributions", "update tenant_capability_distributions"))


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["toggle", "set_status", "upsert"])
async def test_archived_capability_distribution_rejects_reactivation(monkeypatch, operation):
    async def no_backfill(conn, *, tenant_id):
        return None

    class Cursor:
        def __init__(self, row):
            self.row = row

        async def fetchone(self):
            return self.row

    class Connection:
        def __init__(self):
            self.calls = []

        async def execute(self, sql, params=()):
            compact = " ".join(sql.split())
            self.calls.append((compact, params))
            if compact.startswith("select metadata_json"):
                return Cursor({"metadata_json": {"archived_at": "2026-07-15T00:00:00.000Z"}})
            return Cursor(None)

    monkeypatch.setattr(repositories, "ensure_tenant_capability_distribution_backfill", no_backfill)
    conn = Connection()
    kwargs = {
        "tenant_id": "tenant-a",
        "capability_kind": "skill",
        "capability_id": "qa-file-reviewer",
    }

    with pytest.raises(RepositoryConflictError, match="capability_distribution_archived"):
        if operation == "toggle":
            await repositories.toggle_capability_distribution_row(conn, **kwargs, enabled=True, updated_by="admin-a")
        elif operation == "set_status":
            await repositories.set_capability_distribution_status(conn, **kwargs, status="active", updated_by="admin-a")
        else:
            await repositories.upsert_capability_distribution_row(
                conn,
                **kwargs,
                status="active",
                visible_to_user=True,
                scope_mode="allowlist",
                department_ids=[],
                allowed_roles=[],
                metadata_json={},
                updated_by="admin-a",
            )

    assert any(sql.startswith("select metadata_json") and sql.endswith("for update") for sql, _ in conn.calls)
    assert not any("metadata_json ? 'archived_at'" in sql for sql, _ in conn.calls)


@pytest.mark.asyncio
async def test_list_public_skill_catalog_hides_archived_but_keeps_disabled_distribution(monkeypatch):
    async def no_backfill(conn, *, tenant_id):
        return None

    def row(skill_id, *, status, metadata_json):
        return {
            "skill_id": skill_id,
            "name": skill_id,
            "version": f"{skill_id}-hash",
            "expected_version": f"{skill_id}-hash",
            "description": skill_id,
            "input_modes": [],
            "lifecycle_status": "active",
            "status": status,
            "visible_to_user": status == "active",
            "department_ids": [],
            "allowed_roles": [],
            "distribution_metadata_json": metadata_json,
            "version_status": "active",
            "source_json": {"kind": "builtin", "files": []},
            "dependency_ids": [],
            "created_by": "admin-a",
            "created_at": None,
            "updated_at": None,
        }

    class Cursor:
        async def fetchall(self):
            return [
                row("archived-skill", status="disabled", metadata_json={"archived_at": "2026-07-15T00:00:00.000Z"}),
                row("disabled-skill", status="disabled", metadata_json={}),
            ]

    class Connection:
        async def execute(self, sql, params):
            return Cursor()

    monkeypatch.setattr(repositories, "ensure_tenant_capability_distribution_backfill", no_backfill)
    rows = await repositories.list_public_skill_catalog(
        Connection(),
        tenant_id="tenant-a",
        include_disabled=True,
    )

    assert [row["skill_id"] for row in rows] == ["disabled-skill"]


@pytest.mark.asyncio
async def test_authorize_selected_run_capabilities_fails_closed_for_archived_distribution(monkeypatch):
    async def resolve_selected(conn, *, tenant_id, agent_id, skill_id):
        return {
            "agent_id": agent_id,
            "skill_id": skill_id,
            "skill_status": "active",
            "skill_version": "hash-v1",
            "skill_content_hash": "hash-v1",
            "skill_version_status": "active",
            "release_policy_version": None,
            "release_policy_previous_version": None,
            "release_policy_rollout_percent": 100,
            "executor_type": "claude-agent-worker",
            "input_modes": [],
        }

    async def archived_distribution(conn, **kwargs):
        return {
            "status": "disabled",
            "visible_to_user": False,
            "scope_mode": "allowlist",
            "department_ids": [],
            "allowed_roles": [],
            "metadata_json": {"archived_at": "2026-07-15T00:00:00.000Z"},
        }

    monkeypatch.setattr(repositories, "resolve_selected_skill", resolve_selected)
    monkeypatch.setattr(repositories, "get_capability_distribution_row", archived_distribution)

    with pytest.raises(repositories.RepositoryAuthorizationError, match="capability_not_authorized"):
        await repositories.authorize_selected_run_capabilities(
            object(),
            tenant_id="tenant-a",
            agent_id="general-agent",
            skill_id="qa-file-reviewer",
            expected_version="hash-v1",
            rollout_key="admin-a",
            normalized_input={},
            principal_department_id="platform",
            principal_roles=["admin"],
            is_admin=True,
            permissions=["skill:write"],
        )


@pytest.mark.asyncio
async def test_capability_distribution_authorization_allows_same_department_skill_and_mcp_tool(monkeypatch):
    calls = []

    async def fake_resolve_agent_skill(conn, *, tenant_id, agent_id, skill_id):
        calls.append(("skill", tenant_id, agent_id, skill_id))
        return {"skill_id": skill_id, "skill_status": "active", "executor_type": "claude-agent-worker"}

    async def fake_get_distribution(conn, *, tenant_id, capability_kind, capability_id):
        calls.append(("distribution", capability_kind, capability_id))
        return {
            "status": "active",
            "visible_to_user": True,
            "scope_mode": "allowlist",
            "department_ids": ["qa"],
            "allowed_roles": ["qa-operator"],
        }

    async def fake_get_tool(conn, *, tenant_id, tool_id):
        calls.append(("tool", tenant_id, tool_id))
        return {
            "tool_id": tool_id,
            "server_id": "qa-mcp",
            "effective_status": "active",
            "server_status": "active",
            "visible_to_user": True,
        }

    monkeypatch.setattr(repositories, "resolve_agent_skill", fake_resolve_agent_skill)
    monkeypatch.setattr(repositories, "get_capability_distribution_row", fake_get_distribution)
    monkeypatch.setattr(repositories, "get_mcp_tool_registry_entry", fake_get_tool)

    skill = await repositories.authorize_run_capabilities(
        object(),
        tenant_id="tenant-a",
        agent_id="general-agent",
        skill_id="general-chat",
        normalized_input={"mcp_tool_ids": ["qa-search"]},
        principal_department_id="qa",
        principal_roles=[" QA-Operator "],
        is_admin=False,
        permissions=[],
    )

    assert skill["skill_id"] == "general-chat"
    assert calls == [
        ("skill", "tenant-a", "general-agent", "general-chat"),
        ("distribution", "skill", "general-chat"),
        ("tool", "tenant-a", "qa-search"),
        ("distribution", "mcp_server", "qa-mcp"),
    ]


@pytest.mark.asyncio
async def test_direct_ragflow_authorization_derives_canonical_backing_tool_without_explicit_selector(monkeypatch):
    calls = []

    async def fake_resolve_agent_skill(conn, *, tenant_id, agent_id, skill_id):
        return {
            "skill_id": skill_id,
            "skill_status": "active",
            "executor_type": "ragflow",
            "backing_mcp_tool_id": "tenant-search",
        }

    async def fake_get_distribution(conn, *, tenant_id, capability_kind, capability_id):
        calls.append(("distribution", capability_kind, capability_id))
        return {
            "status": "active",
            "visible_to_user": True,
            "scope_mode": "allowlist",
            "department_ids": ["qa"],
            "allowed_roles": ["qa_operator"],
        }

    async def fake_get_tool(conn, *, tenant_id, tool_id):
        calls.append(("tool", tool_id))
        return {
            "tool_id": tool_id,
            "server_id": "tenant-search-server",
            "effective_status": "active",
            "server_status": "active",
            "visible_to_user": True,
        }

    monkeypatch.setattr(repositories, "resolve_agent_skill", fake_resolve_agent_skill)
    monkeypatch.setattr(repositories, "get_capability_distribution_row", fake_get_distribution)
    monkeypatch.setattr(repositories, "get_mcp_tool_registry_entry", fake_get_tool)

    await repositories.authorize_run_capabilities(
        object(),
        tenant_id="tenant-a",
        agent_id="sop-assistant",
        skill_id="knowledge-skill",
        normalized_input={},
        principal_department_id="qa",
        principal_roles=["qa_operator"],
        is_admin=False,
        permissions=[],
    )

    assert calls == [
        ("distribution", "skill", "knowledge-skill"),
        ("tool", "tenant-search"),
        ("distribution", "mcp_server", "tenant-search-server"),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "denial",
    ["backing_missing", "tool_missing", "hidden", "disabled", "department", "role", "parent_disabled"],
)
async def test_direct_ragflow_authorization_fails_closed_for_current_parent_and_tool_state(monkeypatch, denial):
    async def fake_resolve_agent_skill(conn, *, tenant_id, agent_id, skill_id):
        return {
            "skill_id": skill_id,
            "skill_status": "active",
            "executor_type": "ragflow",
            "backing_mcp_tool_id": None if denial == "backing_missing" else "tenant-search",
        }

    async def fake_get_distribution(conn, *, tenant_id, capability_kind, capability_id):
        row = {
            "status": "active",
            "visible_to_user": True,
            "scope_mode": "allowlist",
            "department_ids": ["qa"],
            "allowed_roles": ["qa_operator"],
        }
        if capability_kind == "mcp_server":
            if denial == "hidden":
                row["visible_to_user"] = False
            elif denial == "disabled":
                row["status"] = "disabled"
            elif denial == "department":
                row["department_ids"] = ["finance"]
            elif denial == "role":
                row["allowed_roles"] = ["reviewer"]
        return row

    async def fake_get_tool(conn, *, tenant_id, tool_id):
        if denial == "tool_missing":
            return None
        return {
            "tool_id": tool_id,
            "server_id": "tenant-search-server",
            "effective_status": "active",
            "server_status": "disabled" if denial == "parent_disabled" else "active",
            "visible_to_user": True,
        }

    monkeypatch.setattr(repositories, "resolve_agent_skill", fake_resolve_agent_skill)
    monkeypatch.setattr(repositories, "get_capability_distribution_row", fake_get_distribution)
    monkeypatch.setattr(repositories, "get_mcp_tool_registry_entry", fake_get_tool)

    with pytest.raises(repositories.RepositoryAuthorizationError, match="capability_not_authorized"):
        await repositories.authorize_run_capabilities(
            object(),
            tenant_id="tenant-a",
            agent_id="sop-assistant",
            skill_id="knowledge-skill",
            normalized_input={},
            principal_department_id="qa",
            principal_roles=["qa_operator"],
            is_admin=False,
            permissions=[],
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("denial", ["missing", "hidden", "disabled", "department", "role"])
async def test_capability_distribution_authorization_denies_skill_before_enqueue(monkeypatch, denial):
    async def fake_resolve_agent_skill(conn, *, tenant_id, agent_id, skill_id):
        if denial == "missing":
            raise RepositoryNotFoundError("agent_or_skill_not_found")
        return {"skill_id": skill_id, "skill_status": "active", "executor_type": "claude-agent-worker"}

    async def fake_get_distribution(conn, *, tenant_id, capability_kind, capability_id):
        row = {
            "status": "active",
            "visible_to_user": True,
            "scope_mode": "allowlist",
            "department_ids": ["qa"],
            "allowed_roles": ["qa_operator"],
        }
        if denial == "hidden":
            row["visible_to_user"] = False
        elif denial == "disabled":
            row["status"] = "disabled"
        elif denial == "department":
            row["department_ids"] = ["finance"]
        elif denial == "role":
            row["allowed_roles"] = ["reviewer"]
        return row

    monkeypatch.setattr(repositories, "resolve_agent_skill", fake_resolve_agent_skill)
    monkeypatch.setattr(repositories, "get_capability_distribution_row", fake_get_distribution)

    with pytest.raises(repositories.RepositoryAuthorizationError, match="capability_not_authorized"):
        await repositories.authorize_run_capabilities(
            object(),
            tenant_id="tenant-a",
            agent_id="general-agent",
            skill_id="general-chat",
            normalized_input={},
            principal_department_id="qa",
            principal_roles=["qa_operator"],
            is_admin=False,
            permissions=[],
        )


@pytest.mark.asyncio
async def test_capability_distribution_denial_carries_sanitized_immutable_audit_record(monkeypatch):
    async def fake_resolve_agent_skill(conn, *, tenant_id, agent_id, skill_id):
        return {
            "skill_id": skill_id,
            "skill_status": "active",
            "executor_type": "claude-agent-worker",
        }

    async def fake_get_distribution(conn, *, tenant_id, capability_kind, capability_id):
        return {
            "status": "active",
            "visible_to_user": True,
            "scope_mode": "allowlist",
            "department_ids": ["finance"],
            "allowed_roles": ["reviewer"],
            "metadata_json": {"secret": "must-not-be-audited"},
        }

    monkeypatch.setattr(repositories, "resolve_agent_skill", fake_resolve_agent_skill)
    monkeypatch.setattr(repositories, "get_capability_distribution_row", fake_get_distribution)

    with pytest.raises(repositories.RepositoryAuthorizationError) as exc_info:
        await repositories.authorize_run_capabilities(
            object(),
            tenant_id="tenant-a",
            agent_id="general-agent",
            skill_id="general-chat",
            normalized_input={},
            principal_department_id="QA",
            principal_roles=[" QA-Operator ", "qa-operator"],
            is_admin=False,
            permissions=[],
        )

    denial = exc_info.value.denial
    assert denial is not None
    assert denial.audit_payload() == {
        "capability_kind": "skill",
        "capability_id": "general-chat",
        "actor_department_id": "QA",
        "actor_roles": ["qa-operator"],
        "department_scope_ids": ["finance"],
        "role_scope_ids": ["reviewer"],
        "scope_mode": "allowlist",
        "decision_reason": "department_not_allowed",
        "admin_bypass": False,
    }
    with pytest.raises((AttributeError, TypeError)):
        denial.actor_roles += ("admin",)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "selector_state",
    [
        "agent_inactive",
        "skill_inactive",
        "skill_version_not_released",
        "executor_type_not_allowed",
        "agent_skill_mismatch",
        "mcp_tool_disabled",
    ],
)
async def test_capability_distribution_authorization_hides_pre_authorization_selector_state(
    monkeypatch,
    selector_state,
):
    async def fake_resolve_agent_skill(conn, *, tenant_id, agent_id, skill_id):
        raise repositories.RepositoryConflictError(selector_state)

    monkeypatch.setattr(repositories, "resolve_agent_skill", fake_resolve_agent_skill)

    with pytest.raises(repositories.RepositoryAuthorizationError, match="capability_not_authorized"):
        await repositories.authorize_run_capabilities(
            object(),
            tenant_id="tenant-a",
            agent_id="general-agent",
            skill_id="general-chat",
            normalized_input={},
            principal_department_id="QA",
            principal_roles=["qa-operator"],
            is_admin=False,
            permissions=[],
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("denial", ["missing", "disabled", "server_disabled", "department"])
async def test_capability_distribution_authorization_denies_explicit_mcp_tool_before_enqueue(monkeypatch, denial):
    async def fake_resolve_agent_skill(conn, *, tenant_id, agent_id, skill_id):
        return {"skill_id": skill_id, "skill_status": "active", "executor_type": "claude-agent-worker"}

    async def fake_get_distribution(conn, *, tenant_id, capability_kind, capability_id):
        if capability_kind == "skill":
            return {
                "status": "active",
                "visible_to_user": True,
                "scope_mode": "allowlist",
                "department_ids": ["qa"],
                "allowed_roles": [],
            }
        return {
            "status": "active",
            "visible_to_user": True,
            "scope_mode": "allowlist",
            "department_ids": ["finance"] if denial == "department" else ["qa"],
            "allowed_roles": [],
        }

    async def fake_get_tool(conn, *, tenant_id, tool_id):
        if denial == "missing":
            return None
        return {
            "tool_id": tool_id,
            "server_id": "qa-mcp",
            "effective_status": "disabled" if denial == "disabled" else "active",
            "server_status": "disabled" if denial == "server_disabled" else "active",
            "visible_to_user": True,
        }

    monkeypatch.setattr(repositories, "resolve_agent_skill", fake_resolve_agent_skill)
    monkeypatch.setattr(repositories, "get_capability_distribution_row", fake_get_distribution)
    monkeypatch.setattr(repositories, "get_mcp_tool_registry_entry", fake_get_tool)

    with pytest.raises(repositories.RepositoryAuthorizationError, match="capability_not_authorized"):
        await repositories.authorize_run_capabilities(
            object(),
            tenant_id="tenant-a",
            agent_id="general-agent",
            skill_id="general-chat",
            normalized_input={"mcp_tool_ids": ["qa-search"]},
            principal_department_id="qa",
            principal_roles=["qa_operator"],
            is_admin=False,
            permissions=[],
        )


def test_extract_run_mcp_tool_ids_covers_top_level_aliases_and_canonical_multi_agent_steps():
    extracted = repositories.extract_run_mcp_tool_ids(
        {
            "mcp_tool_ids": ["tool-a", "tool-shared"],
            "mcpToolIds": ["tool-b", "tool-shared"],
            "multi_agent_steps": [
                {"step_key": "inspect", "mcp_tool_ids": ["tool-c"]},
                {"stepKey": "execute", "mcpToolIds": ["tool-d"]},
            ],
            "metadata": {"mcp_tool_ids": ["unrelated-tool"]},
            "message": "do not parse mcp_tool_ids=unrelated-string",
        }
    )

    assert extracted == ["tool-a", "tool-shared", "tool-b", "tool-c", "tool-d"]


@pytest.mark.parametrize("redact_public", [False, True])
def test_normalize_run_input_preserves_top_level_and_step_mcp_tool_scopes(redact_public):
    normalized = repositories.normalize_run_input_for_enqueue(
        {
            "message": "run scoped tools",
            "mcpToolIds": ["tool-global"],
            "multi_agent_steps": [
                {"step_key": "plan", "mcp_tool_ids": ["tool-plan"]},
                {"step_key": "code", "mcpToolIds": ["tool-code"]},
            ],
        },
        redact_public=redact_public,
    )

    assert normalized["mcp_tool_ids"] == ["tool-global"]
    assert normalized["multi_agent_steps"][0]["mcp_tool_ids"] == ["tool-plan"]
    assert normalized["multi_agent_steps"][1]["mcp_tool_ids"] == ["tool-code"]
    if redact_public:
        assert "mcpToolIds" not in normalized
        assert "mcpToolIds" not in normalized["multi_agent_steps"][1]
    else:
        assert normalized["mcpToolIds"] == ["tool-global"]
        assert normalized["multi_agent_steps"][1]["mcpToolIds"] == ["tool-code"]
    assert repositories.extract_run_mcp_tool_ids(normalized) == ["tool-global", "tool-plan", "tool-code"]

    step_only = repositories.normalize_run_input_for_enqueue(
        {
            "multi_agent_steps": [
                {"step_key": "plan", "mcpToolIds": ["tool-plan"]},
                {"step_key": "code", "mcp_tool_ids": ["tool-code"]},
            ]
        },
        redact_public=redact_public,
    )
    assert "mcp_tool_ids" not in step_only
    assert "mcpToolIds" not in step_only


@pytest.mark.parametrize(
    ("selected_step_key", "selected_tool_id", "sibling_tool_id"),
    [
        ("plan", "tool-plan", "tool-code"),
        ("code", "tool-code", "tool-plan"),
    ],
)
def test_multi_agent_child_and_queue_input_exclude_sibling_step_tools(
    selected_step_key,
    selected_tool_id,
    sibling_tool_id,
):
    child_input = repositories._multi_agent_dispatch_child_execution_input(
        {
            "message": "run scoped tools",
            "mcpToolIds": ["tool-global"],
            "multi_agent_steps": [
                {"step_key": "plan", "mcp_tool_ids": ["tool-plan"]},
                {"step_key": "code", "mcpToolIds": ["tool-code"]},
            ],
        },
        parent_run_id="run-parent",
        dispatch_id=f"dispatch-{selected_step_key}",
        step={
            "id": f"step-{selected_step_key}",
            "step_key": selected_step_key,
            "role": f"{selected_step_key}-role",
            "title": selected_step_key.title(),
        },
        depends_on=[],
        resume_payload={},
    )
    queue_payload = QueueRunPayload(
        tenant_id="tenant-a",
        workspace_id="default",
        user_id="user-a",
        session_id="session-a",
        run_id=f"run-child-{selected_step_key}",
        agent_id="general-agent",
        skill_id="general-chat",
        input=child_input,
        executor_type="claude-agent-worker",
        skill_version="hash-primary",
        release_decision={
            "schema_version": "ai-platform.skill-release-decision.v1",
            "policy_active": False,
            "selected_version": "hash-primary",
            "selected_track": "manifest_pin",
        },
        skill_manifests=[{"skill_id": "general-chat", "content_hash": "hash-primary"}],
    )

    assert child_input["mcp_tool_ids"] == ["tool-global"]
    assert child_input["multi_agent_steps"][0]["mcp_tool_ids"] == [selected_tool_id]
    assert sibling_tool_id not in json.dumps(child_input)
    assert queue_payload.input == child_input
    assert sibling_tool_id not in json.dumps(queue_payload.input)


@pytest.mark.parametrize(
    "payload",
    [
        {"mcp_tool_ids": "tool-a"},
        {"mcpToolIds": {"tool": "tool-a"}},
        {"multi_agent_steps": [{"step_key": "inspect", "mcp_tool_ids": "tool-a"}]},
        {"multi_agent_steps": [{"step_key": "inspect", "mcpToolIds": 7}]},
        {"multi_agent_steps": [{"step_key": "inspect", "mcp_tool_ids": ["tool-a", None]}]},
    ],
)
def test_extract_run_mcp_tool_ids_rejects_invalid_typed_forms_fail_closed(payload):
    with pytest.raises(repositories.RepositoryAuthorizationError, match="capability_not_authorized"):
        repositories.extract_run_mcp_tool_ids(payload)


@pytest.mark.asyncio
async def test_capability_distribution_skill_revocation_after_original_run_denies_requeue(monkeypatch):
    async def fake_resolve_agent_skill(conn, *, tenant_id, agent_id, skill_id):
        return {"skill_id": skill_id, "skill_status": "active", "executor_type": "claude-agent-worker"}

    async def revoked_skill_distribution(conn, *, tenant_id, capability_kind, capability_id):
        assert (capability_kind, capability_id) == ("skill", "general-chat")
        return {
            "status": "disabled",
            "visible_to_user": True,
            "scope_mode": "allowlist",
            "department_ids": ["qa"],
            "allowed_roles": ["qa_operator"],
        }

    async def fail_tool_lookup(*args, **kwargs):
        raise AssertionError("revoked Skill must deny before MCP lookup")

    monkeypatch.setattr(repositories, "resolve_agent_skill", fake_resolve_agent_skill)
    monkeypatch.setattr(repositories, "get_capability_distribution_row", revoked_skill_distribution)
    monkeypatch.setattr(repositories, "get_mcp_tool_registry_entry", fail_tool_lookup)

    with pytest.raises(repositories.RepositoryAuthorizationError, match="capability_not_authorized"):
        await repositories.authorize_run_capabilities(
            object(),
            tenant_id="tenant-a",
            agent_id="general-agent",
            skill_id="general-chat",
            normalized_input={"mcp_tool_ids": ["tool-a"]},
            principal_department_id="qa",
            principal_roles=["qa_operator"],
            is_admin=False,
            permissions=[],
        )


@pytest.mark.asyncio
async def test_capability_distribution_nested_mcp_revocation_after_original_run_denies_requeue(monkeypatch):
    calls = []

    async def fake_resolve_agent_skill(conn, *, tenant_id, agent_id, skill_id):
        return {"skill_id": skill_id, "skill_status": "active", "executor_type": "claude-agent-worker"}

    async def fake_get_distribution(conn, *, tenant_id, capability_kind, capability_id):
        calls.append((capability_kind, capability_id))
        return {
            "status": "active" if capability_kind == "skill" else "disabled",
            "visible_to_user": True,
            "scope_mode": "allowlist",
            "department_ids": ["qa"],
            "allowed_roles": ["qa_operator"],
        }

    async def fake_get_tool(conn, *, tenant_id, tool_id):
        calls.append(("tool", tool_id))
        return {
            "tool_id": tool_id,
            "server_id": "qa-mcp",
            "effective_status": "active",
            "server_status": "active",
            "visible_to_user": True,
        }

    monkeypatch.setattr(repositories, "resolve_agent_skill", fake_resolve_agent_skill)
    monkeypatch.setattr(repositories, "get_capability_distribution_row", fake_get_distribution)
    monkeypatch.setattr(repositories, "get_mcp_tool_registry_entry", fake_get_tool)

    with pytest.raises(repositories.RepositoryAuthorizationError, match="capability_not_authorized"):
        await repositories.authorize_run_capabilities(
            object(),
            tenant_id="tenant-a",
            agent_id="general-agent",
            skill_id="general-chat",
            normalized_input={
                "multi_agent_steps": [
                    {"step_key": "inspect", "mcpToolIds": ["revoked-tool"]},
                ]
            },
            principal_department_id="qa",
            principal_roles=["qa_operator"],
            is_admin=False,
            permissions=[],
        )

    assert calls == [
        ("skill", "general-chat"),
        ("tool", "revoked-tool"),
        ("mcp_server", "qa-mcp"),
    ]


@pytest.mark.asyncio
async def test_create_context_snapshot_preserves_private_context_manifest_refs_without_storage_keys():
    conn = SingleRowConnection({"id": "ctx-row"})

    await create_context_snapshot(
        conn,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        session_id="session-a",
        run_id="run-a",
        trace_id="trace-a",
        context_kind="executor",
        included_message_ids=["msg-a"],
        included_file_ids=["file-a"],
        included_artifact_ids=[],
        included_memory_record_ids=[],
        redaction_summary_json={},
        payload_json={
            "context_manifest": {
                "schema_version": "ai-platform.context-manifest.v1",
                "recent_messages": [{"message_id": "msg-a", "requires_retrieval": True}],
                "files": [
                    {
                        "file_id": "file-a",
                        "storage_key": "tenants/tenant-a/private/source.docx",
                        "requires_retrieval": True,
                    }
                ],
                "raw_storage_key": "tenants/tenant-a/private/raw.docx",
            }
        },
    )

    persisted_payload = json.loads(conn.params[-1])
    assert persisted_payload["context_manifest"]["recent_messages"] == [
        {"message_id": "msg-a", "requires_retrieval": True}
    ]
    assert persisted_payload["context_manifest"]["files"] == [
        {"file_id": "file-a", "requires_retrieval": True}
    ]
    serialized = json.dumps(persisted_payload)
    assert "storage_key" not in serialized
    assert "tenants/tenant-a/private" not in serialized


@pytest.mark.asyncio
async def test_list_scoped_context_messages_filters_full_scope_and_limits_rows():
    conn = SingleRowConnection(
        {
            "id": "msg-a",
            "session_id": "session-a",
            "run_id": "run-a",
            "role": "user",
            "content": "hello",
            "metadata_json": {},
            "created_at": "now",
        }
    )

    rows = await list_scoped_context_messages(
        conn,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        session_id="session-a",
        run_id="run-a",
        limit=25,
        offset=5,
    )

    assert rows[0]["id"] == "msg-a"
    assert "join sessions" in conn.sql
    assert "join runs source_runs" in conn.sql
    assert "join runs current_run" in conn.sql
    assert "context_snapshot.id = current_run.context_snapshot_id" in conn.sql
    assert "context_snapshot.included_message_ids ? messages.id" in conn.sql
    assert "source_runs.session_id = current_run.session_id" in conn.sql
    assert conn.params == ("run-a", "tenant-a", "workspace-a", "user-a", "session-a", "run-a", 25, 5)


@pytest.mark.asyncio
async def test_scoped_context_file_and_artifact_queries_bind_full_scope():
    file_conn = SingleRowConnection(
        {
            "id": "file-a",
            "original_name": "source.txt",
            "content_type": "text/plain",
            "size_bytes": 10,
            "storage_key": "tenants/private/source.txt",
            "sha256": "hash",
        }
    )
    artifact_conn = SingleRowConnection(
        {
            "id": "artifact-a",
            "artifact_type": "report_txt",
            "label": "report.txt",
            "content_type": "text/plain",
            "size_bytes": 10,
            "storage_key": "tenants/private/report.txt",
        }
    )

    file_row = await get_scoped_context_file(
        file_conn,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        session_id="session-a",
        run_id="run-a",
        file_id="file-a",
    )
    artifact_row = await get_scoped_context_artifact(
        artifact_conn,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        session_id="session-a",
        run_id="run-a",
        artifact_id="artifact-a",
    )

    assert file_row["id"] == "file-a"
    assert "join runs source_run" in file_conn.sql
    assert "join runs current_run" in file_conn.sql
    assert "context_snapshot.id = current_run.context_snapshot_id" in file_conn.sql
    assert "join lateral" not in file_conn.sql
    assert "sessions.status = 'active'" in file_conn.sql
    assert "context_snapshot.included_file_ids ? files.id" in file_conn.sql
    assert "source_run.session_id = current_run.session_id" in file_conn.sql
    assert file_conn.params == ("run-a", "tenant-a", "workspace-a", "user-a", "session-a", "run-a", "file-a")
    assert artifact_row["id"] == "artifact-a"
    assert "join runs source_run" in artifact_conn.sql
    assert "join runs current_run" in artifact_conn.sql
    assert "context_snapshot.included_artifact_ids ? artifacts.id" in artifact_conn.sql
    assert "source_run.session_id = current_run.session_id" in artifact_conn.sql
    assert "artifacts.expires_at is null or artifacts.expires_at > now()" in artifact_conn.sql
    assert artifact_conn.params == ("run-a", "tenant-a", "workspace-a", "user-a", "session-a", "run-a", "artifact-a")


@pytest.mark.parametrize(
    ("selected_member", "later_member", "expected_authorized"),
    [
        (True, False, True),
        (False, True, False),
    ],
)
@pytest.mark.asyncio
async def test_input_file_list_and_read_use_persisted_s1_after_later_s2(
    selected_member,
    later_member,
    expected_authorized,
):
    conn = TwoSnapshotFileMembershipConnection(
        selected_member=selected_member,
        later_member=later_member,
    )

    file_row = await get_scoped_context_file(
        conn,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        session_id="session-a",
        run_id="run-a",
        file_id="file-a",
    )
    projected_rows = await repositories.list_authorized_session_input_files(
        conn,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        session_id="session-a",
    )

    assert conn.selected_snapshot_id == "ctx-s1"
    assert conn.snapshots[-1]["id"] == "ctx-s2"
    assert (file_row is not None) is expected_authorized
    assert bool(projected_rows) is expected_authorized
    read_sql, projection_sql = (call[0] for call in conn.calls)
    assert "context_snapshot.id = current_run.context_snapshot_id" in read_sql
    assert "authorized_snapshot.id = runs.context_snapshot_id" in projection_sql
    assert "join lateral" not in read_sql
    assert "join lateral" not in projection_sql


@pytest.mark.asyncio
async def test_session_context_candidates_bind_owner_scope_and_latest_successful_artifact_run():
    conn = RecordingConnection()

    await repositories.list_session_context_messages(
        conn,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        session_id="session-a",
        run_id="run-current",
        limit=8,
    )
    messages_sql, messages_params = conn.calls[-1]
    assert "sessions.status = 'active'" in messages_sql
    assert "runs.workspace_id = sessions.workspace_id" in messages_sql
    assert "runs.user_id = sessions.user_id" in messages_sql
    assert "runs.session_generation <" in messages_sql
    assert "order by runs.session_generation desc" in messages_sql
    assert "order by session_generation asc" in messages_sql
    assert messages_params == (
        "tenant-a", "workspace-a", "user-a", "session-a", "run-current",
        "tenant-a", "session-a", "workspace-a", "user-a", 8,
    )

    await repositories.count_session_context_messages(
        conn,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        session_id="session-a",
        run_id="run-current",
    )
    count_sql, count_params = conn.calls[-1]
    assert "count(*) as context_message_count" in count_sql
    assert "messages.content" not in count_sql
    assert "order by" not in count_sql
    assert "limit" not in count_sql
    assert count_params == (
        "tenant-a", "workspace-a", "user-a", "session-a", "run-current",
        "tenant-a", "session-a", "workspace-a", "user-a",
    )

    await repositories.list_session_context_files(
        conn,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        session_id="session-a",
        run_id="run-current",
        limit=8,
    )
    files_sql, files_params = conn.calls[-1]
    assert "sessions.status = 'active'" in files_sql
    assert "runs.session_id = files.session_id" in files_sql
    assert "runs.session_generation <" in files_sql
    assert "order by runs.session_generation desc" in files_sql
    assert "order by session_generation asc" in files_sql
    assert files_params == (
        "tenant-a", "workspace-a", "user-a", "session-a", "run-current",
        "tenant-a", "workspace-a", "user-a", "session-a", 8,
    )

    await repositories.list_authorized_session_input_files(
        conn,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        session_id="session-a",
    )
    projection_sql, projection_params = conn.calls[-1]
    assert "join sessions" in projection_sql
    assert "join runs" in projection_sql
    assert "join lateral" not in projection_sql
    assert "authorized_snapshot.id = runs.context_snapshot_id" in projection_sql
    assert "authorized_snapshot.included_file_ids ? files.id" in projection_sql
    assert "sessions.status = 'active'" in projection_sql
    assert "sessions.workspace_id = files.workspace_id" in projection_sql
    assert "sessions.user_id = files.user_id" in projection_sql
    assert "runs.session_id = files.session_id" in projection_sql
    assert projection_params == ("tenant-a", "workspace-a", "user-a", "session-a")

    await repositories.list_session_context_artifacts(
        conn,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        session_id="session-a",
        exclude_run_id="run-current",
        limit=8,
    )
    artifacts_sql, artifacts_params = conn.calls[-1]
    assert "with latest_source_run" in artifacts_sql
    assert "runs.status = 'succeeded'" in artifacts_sql
    assert "runs.id <> %s" in artifacts_sql
    assert "artifacts.expires_at is null or artifacts.expires_at > now()" in artifacts_sql
    assert artifacts_params == (
        "tenant-a",
        "workspace-a",
        "user-a",
        "session-a",
        "run-current",
        "tenant-a",
        "workspace-a",
        "user-a",
        "session-a",
        "run-current",
        "tenant-a",
        8,
    )


@pytest.mark.asyncio
async def test_list_scoped_context_memory_records_excludes_deleted_and_binds_session_scope():
    conn = SingleRowConnection(
        {
            "id": "mem-a",
            "record_type": "preference",
            "content": "prefer concise answers",
            "metadata_json": {},
            "status": "active",
            "deleted_at": None,
            "created_at": "now",
        }
    )

    rows = await list_scoped_context_memory_records(
        conn,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        agent_id="general-agent",
        session_id="session-a",
        query="prefer",
        limit=10,
    )

    assert rows[0]["id"] == "mem-a"
    assert "status = 'active'" in conn.sql
    assert "deleted_at is null" in conn.sql
    assert "session_id = %s" in conn.sql
    assert conn.params == (
        "tenant-a",
        "workspace-a",
        "user-a",
        "general-agent",
        "session-a",
        "%prefer%",
        "%prefer%",
        10,
    )


@pytest.mark.asyncio
async def test_tenant_exists_checks_tenant_identity():
    class ExistingTenantCursor:
        async def fetchone(self):
            return {"exists": 1}

    class MissingTenantCursor:
        async def fetchone(self):
            return None

    class TenantConnection:
        def __init__(self, cursor):
            self.cursor = cursor
            self.sql = ""
            self.params = None

        async def execute(self, sql, params):
            self.sql = " ".join(sql.split())
            self.params = params
            return self.cursor

    existing_conn = TenantConnection(ExistingTenantCursor())
    missing_conn = TenantConnection(MissingTenantCursor())

    assert await repositories.tenant_exists(existing_conn, tenant_id="tenant-a") is True
    assert "from tenants where id = %s" in existing_conn.sql
    assert existing_conn.params == ("tenant-a",)
    assert await repositories.tenant_exists(missing_conn, tenant_id="tenant-b") is False
    assert missing_conn.params == ("tenant-b",)


@pytest.mark.asyncio
async def test_count_active_runs_for_user_counts_queued_and_running_only():
    conn = FakeConnection()

    count = await count_active_runs_for_user(conn, tenant_id="tenant-a", user_id="user-a")

    assert count == 2
    assert "status in ('queued', 'running')" in conn.sql
    assert conn.params == ("tenant-a", "user-a")


@pytest.mark.asyncio
async def test_list_public_skill_catalog_projects_public_source_without_internal_dependencies(monkeypatch):
    async def no_backfill(conn, *, tenant_id):
        return None

    monkeypatch.setattr(repositories, "ensure_tenant_capability_distribution_backfill", no_backfill)
    class CatalogCursor:
        async def fetchall(self):
            return [
                {
                    "skill_id": "qa-file-reviewer",
                    "name": "QA Word Review",
                    "version": "hash-a",
                    "expected_version": "hash-a",
                    "description": "Review Word documents.",
                    "input_modes": ["docx"],
                    "status": "active",
                    "visible_to_user": True,
                    "source_json": {
                        "kind": "builtin",
                        "tags": ["document"],
                        "files": [{"relative_path": "SKILL.md", "content_base64": "IyBRQQ=="}],
                    },
                    "dependency_ids": ["minimax-docx"],
                    "created_by": "dev-admin",
                    "created_at": None,
                    "updated_at": None,
                    "release_policy_version": "hash-a",
                    "release_policy_previous_version": "hash-old",
                    "release_policy_rollout_percent": 100,
                    "release_policy_previous_version_status": "released",
                    "release_policy_previous_content_hash": "hash-old",
                }
            ]

    class CatalogConnection:
        def __init__(self):
            self.sql = ""
            self.params = None

        async def execute(self, sql, params):
            self.sql = sql
            self.params = params
            return CatalogCursor()

    conn = CatalogConnection()

    rows = await repositories.list_public_skill_catalog(
        conn,
        tenant_id="default",
        include_disabled=True,
        rollout_key="current-track-user",
    )

    assert rows[0]["source"]["tags"] == ["document"]
    assert rows[0]["source"]["files"][0]["relative_path"] == "SKILL.md"
    assert rows[0]["dependency_ids"] == ["minimax-docx"]
    assert rows[0]["expected_version"] == "hash-a"
    assert rows[0]["input_modes"] == ["docx"]
    assert "skill_versions.content_hash as expected_version" in conn.sql
    assert "skills.input_modes" in conn.sql
    assert "tenant_capability_distributions.capability_id is not null" in conn.sql
    assert "tenant_workbench_skills" not in conn.sql
    assert "skills.status = 'active'" in conn.sql
    assert conn.params[0:2] == ("default", "default")
    assert "qa-file-reviewer" in conn.params[2]
    assert "minimax-docx" not in conn.params[2]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("version", "content_hash"),
    [
        (None, "hash-a"),
        ("", "hash-a"),
        ("hash-a", None),
        ("hash-a", ""),
        ("hash-a", "hash-other"),
    ],
)
async def test_list_public_skill_catalog_hides_non_materializable_current_versions(
    monkeypatch,
    version,
    content_hash,
):
    async def no_backfill(conn, *, tenant_id):
        return None

    class CatalogCursor:
        async def fetchall(self):
            return [
                {
                    "skill_id": "qa-file-reviewer",
                    "name": "QA Word Review",
                    "version": version,
                    "expected_version": content_hash,
                    "description": "Review Word documents.",
                    "input_modes": ["docx"],
                    "status": "active",
                    "visible_to_user": True,
                    "version_status": "released",
                    "source_json": {"kind": "builtin", "files": []},
                    "dependency_ids": [],
                    "created_by": "dev-admin",
                    "created_at": None,
                    "updated_at": None,
                }
            ]

    class CatalogConnection:
        async def execute(self, sql, params):
            return CatalogCursor()

    monkeypatch.setattr(repositories, "ensure_tenant_capability_distribution_backfill", no_backfill)

    rows = await repositories.list_public_skill_catalog(
        CatalogConnection(),
        tenant_id="default",
        include_disabled=True,
    )

    assert rows == []


@pytest.mark.asyncio
async def test_list_workbench_skills_projects_distribution_without_legacy_status_or_visibility(monkeypatch):
    async def no_backfill(conn, *, tenant_id):
        return None

    monkeypatch.setattr(repositories, "ensure_tenant_capability_distribution_backfill", no_backfill)

    class Cursor:
        async def fetchall(self):
            return [
                {
                    "skill_id": "general-chat",
                    "name": "General Chat",
                    "version": "0.1.0",
                    "description": "Chat",
                    "input_modes": ["chat"],
                    "output_modes": ["answer"],
                    "executor_type": "claude-agent-worker",
                    "lifecycle_status": "active",
                    "status": "disabled",
                    "visible_to_user": False,
                }
            ]

    class Connection:
        async def execute(self, sql, params):
            self.sql = " ".join(sql.split())
            self.params = params
            return Cursor()

    conn = Connection()
    rows = await repositories.list_workbench_skills(conn, tenant_id="tenant-a", include_disabled=True)

    assert rows[0]["status"] == "disabled"
    assert "tenant_capability_distributions" in conn.sql
    assert "tenant_workbench_skills" not in conn.sql
    assert "skills.status as lifecycle_status" in conn.sql


@pytest.mark.asyncio
async def test_list_workbench_capabilities_uses_global_lifecycle_and_distribution_authority(monkeypatch):
    async def no_backfill(conn, *, tenant_id):
        assert tenant_id == "tenant-a"

    class Cursor:
        async def fetchall(self):
            return []

    class Connection:
        def __init__(self):
            self.sql = ""
            self.params = None

        async def execute(self, sql, params=()):
            self.sql = " ".join(sql.split())
            self.params = params
            return Cursor()

    monkeypatch.setattr(repositories, "ensure_tenant_capability_distribution_backfill", no_backfill)
    conn = Connection()

    rows = await repositories.list_workbench_capabilities(conn, tenant_id="tenant-a")

    assert rows == []
    assert "tenant_workbench_skills" not in conn.sql
    assert "join tenant_capability_distributions" in conn.sql
    assert "skills.status" in conn.sql
    assert "tenant_capability_distributions.status" in conn.sql
    assert "tenant_capability_distributions.visible_to_user" in conn.sql
    assert conn.params == ("tenant-a", "tenant-a")


@pytest.mark.asyncio
async def test_list_public_skill_catalog_hides_unreleased_selected_versions_by_default(monkeypatch):
    async def no_backfill(conn, *, tenant_id):
        return None

    monkeypatch.setattr(repositories, "ensure_tenant_capability_distribution_backfill", no_backfill)
    def catalog_row(skill_id: str, version_status: str) -> dict[str, object]:
        version = f"{skill_id}-version"
        return {
            "skill_id": skill_id,
            "name": skill_id,
            "version": version,
            "expected_version": version,
            "description": f"{skill_id} description",
            "input_modes": ["chat"],
            "status": "active",
            "visible_to_user": True,
            "version_status": version_status,
            "source_json": {"kind": "builtin", "files": []},
            "dependency_ids": [],
            "created_by": "dev-admin",
            "created_at": None,
            "updated_at": None,
        }

    class CatalogCursor:
        async def fetchall(self):
            return [
                catalog_row("general-chat", "active"),
                catalog_row("qa-file-reviewer", "released"),
                catalog_row("baoyu-translate", "draft"),
                catalog_row("ragflow-knowledge-search", "reviewed"),
                catalog_row("ctd-32s73-stability-template-fill", "disabled"),
                catalog_row("custom-deprecated-skill", "deprecated"),
            ]

    class CatalogConnection:
        def __init__(self):
            self.sql = ""
            self.params = None

        async def execute(self, sql, params):
            self.sql = " ".join(sql.split())
            self.params = params
            return CatalogCursor()

    conn = CatalogConnection()

    rows = await repositories.list_public_skill_catalog(
        conn,
        tenant_id="default",
        include_disabled=False,
    )

    assert [row["skill_id"] for row in rows] == ["general-chat", "qa-file-reviewer"]
    assert "coalesce(skill_versions.status, 'active') as version_status" in conn.sql
    assert "previous_skill_versions.status as release_policy_previous_version_status" in conn.sql


@pytest.mark.asyncio
@pytest.mark.parametrize("previous_status", ["draft", "reviewed", "disabled", "deprecated"])
async def test_public_skill_catalog_hides_non_runnable_rollout_selected_previous_version(
    monkeypatch,
    previous_status,
):
    async def no_backfill(conn, *, tenant_id):
        return None

    class CatalogCursor:
        async def fetchall(self):
            return [
                {
                    "skill_id": "qa-file-reviewer",
                    "name": "QA Word Review",
                    "version": "hash-new",
                    "expected_version": "hash-new",
                    "description": "New description",
                    "input_modes": ["docx"],
                    "lifecycle_status": "active",
                    "status": "active",
                    "visible_to_user": True,
                    "version_status": "released",
                    "source_json": {"kind": "builtin", "tags": ["new"]},
                    "dependency_ids": ["new-dependency"],
                    "created_by": "admin-new",
                    "created_at": None,
                    "updated_at": None,
                    "release_policy_version": "hash-new",
                    "release_policy_previous_version": "hash-old",
                    "release_policy_previous_content_hash": "hash-old",
                    "release_policy_rollout_percent": 0,
                    "release_policy_previous_version_status": previous_status,
                    "release_policy_previous_description": "Old description",
                    "release_policy_previous_source_json": {"kind": "builtin", "tags": ["old"]},
                    "release_policy_previous_dependency_ids": ["old-dependency"],
                    "release_policy_previous_created_by": "admin-old",
                    "release_policy_previous_created_at": None,
                }
            ]

    class CatalogConnection:
        def __init__(self):
            self.sql = ""

        async def execute(self, sql, params):
            self.sql = " ".join(sql.split())
            return CatalogCursor()

    monkeypatch.setattr(repositories, "ensure_tenant_capability_distribution_backfill", no_backfill)

    rows = await repositories.list_public_skill_catalog(
        CatalogConnection(),
        tenant_id="default",
        include_disabled=False,
        rollout_key="previous-track-user",
    )

    assert rows == []


@pytest.mark.asyncio
@pytest.mark.parametrize("previous_content_hash", ["hash-old", None, "", "hash-other"])
async def test_public_skill_catalog_projects_only_materializable_rollout_selected_previous_version(
    monkeypatch,
    previous_content_hash,
):
    async def no_backfill(conn, *, tenant_id):
        return None

    class CatalogCursor:
        async def fetchall(self):
            return [
                {
                    "skill_id": "qa-file-reviewer",
                    "name": "QA Word Review",
                    "version": "hash-new",
                    "expected_version": "hash-new",
                    "description": "New description",
                    "input_modes": ["docx"],
                    "lifecycle_status": "active",
                    "status": "active",
                    "visible_to_user": True,
                    "version_status": "released",
                    "source_json": {"kind": "builtin", "tags": ["new"]},
                    "dependency_ids": ["new-dependency"],
                    "created_by": "admin-new",
                    "created_at": None,
                    "updated_at": None,
                    "release_policy_version": "hash-new",
                    "release_policy_previous_version": "hash-old",
                    "release_policy_previous_content_hash": previous_content_hash,
                    "release_policy_rollout_percent": 0,
                    "release_policy_previous_version_status": "released",
                    "release_policy_previous_description": "Old description",
                    "release_policy_previous_source_json": {"kind": "builtin", "tags": ["old"]},
                    "release_policy_previous_dependency_ids": ["old-dependency"],
                    "release_policy_previous_created_by": "admin-old",
                    "release_policy_previous_created_at": None,
                }
            ]

    class CatalogConnection:
        async def execute(self, sql, params):
            return CatalogCursor()

    monkeypatch.setattr(repositories, "ensure_tenant_capability_distribution_backfill", no_backfill)

    rows = await repositories.list_public_skill_catalog(
        CatalogConnection(),
        tenant_id="default",
        include_disabled=False,
        rollout_key="previous-track-user",
    )

    if previous_content_hash != "hash-old":
        assert rows == []
        return

    assert rows[0]["version"] == "hash-old"
    assert rows[0]["expected_version"] == "hash-old"
    assert rows[0]["input_modes"] == ["docx"]
    assert rows[0]["version_status"] == "released"
    assert rows[0]["description"] == "Old description"
    assert rows[0]["source"]["tags"] == ["old"]
    assert rows[0]["dependency_ids"] == ["old-dependency"]
    assert rows[0]["created_by"] == "admin-old"
    assert not any(key.startswith("release_policy_") for key in rows[0])


@pytest.mark.asyncio
async def test_enforce_user_active_run_admission_locks_before_counting():
    class CountCursor:
        async def fetchone(self):
            return {"count": 2}

    class EmptyCursor:
        async def fetchone(self):
            return None

    class AdmissionConnection:
        def __init__(self):
            self.calls = []

        async def execute(self, sql, params):
            normalized = " ".join(sql.split())
            self.calls.append((normalized, params))
            if "count(*) as count" in normalized:
                return CountCursor()
            return EmptyCursor()

    conn = AdmissionConnection()

    observed = await enforce_user_active_run_admission(
        conn,
        tenant_id="tenant-a",
        user_id="user-a",
        limit=3,
    )

    assert observed == 2
    assert "pg_advisory_xact_lock" in conn.calls[0][0]
    assert conn.calls[0][1] == ('{"tenant_id": "tenant-a", "user_id": "user-a"}',)
    assert "status in ('queued', 'running')" in conn.calls[1][0]
    assert conn.calls[1][1] == ("tenant-a", "user-a")


@pytest.mark.asyncio
async def test_enforce_user_active_run_admission_rejects_at_limit():
    class CountCursor:
        async def fetchone(self):
            return {"count": 3}

    class AdmissionConnection:
        async def execute(self, sql, params):
            return CountCursor()

    with pytest.raises(RepositoryConflictError, match="user_active_run_limit_exceeded"):
        await enforce_user_active_run_admission(
            AdmissionConnection(),
            tenant_id="tenant-a",
            user_id="user-a",
            limit=3,
        )


@pytest.mark.asyncio
async def test_enforce_user_active_run_admission_skips_disabled_limit():
    class AdmissionConnection:
        async def execute(self, sql, params):
            raise AssertionError("disabled admission must not lock or count")

    observed = await enforce_user_active_run_admission(
        AdmissionConnection(),
        tenant_id="tenant-a",
        user_id="user-a",
        limit=0,
    )

    assert observed == 0


@pytest.mark.asyncio
async def test_run_control_operation_lock_scope_precedes_any_mapping_query():
    conn = RecordingConnection()

    await repositories.acquire_run_control_operation_lock(
        conn,
        tenant_id="tenant-a",
        user_id="user-a",
        source_run_id="run-source",
        action="retry",
        operation_id="7ea93033-30f5-40ea-8a33-2f3c6e7b21c4",
    )
    await repositories.get_run_control_operation(
        conn,
        tenant_id="tenant-a",
        user_id="user-a",
        source_run_id="run-source",
        action="retry",
        operation_id="7ea93033-30f5-40ea-8a33-2f3c6e7b21c4",
    )

    assert "pg_advisory_xact_lock" in conn.calls[0][0]
    assert conn.calls[0][1] == (
        '{"scope": "run_control_operation", "tenant_id": "tenant-a", "user_id": "user-a", '
        '"source_run_id": "run-source", "action": "retry", '
        '"operation_id": "7ea93033-30f5-40ea-8a33-2f3c6e7b21c4"}',
    )
    mapping_sql, mapping_params = conn.calls[1]
    assert "run_control_operation_committed" in mapping_sql
    assert "source.user_id = %s" in mapping_sql
    assert "child.user_id = %s" in mapping_sql
    assert "child.copied_from_run_id = source.id" in mapping_sql
    assert "child.workspace_id" in mapping_sql
    assert "child.agent_id" in mapping_sql
    assert "child.skill_id" in mapping_sql
    assert "child.input_json" in mapping_sql
    assert mapping_params.count("tenant-a") >= 1
    assert mapping_params.count("user-a") == 2
    assert "run-source" in mapping_params
    assert "retry" in mapping_params
    assert "7ea93033-30f5-40ea-8a33-2f3c6e7b21c4" in mapping_params


@pytest.mark.asyncio
async def test_record_run_control_operation_persists_only_safe_exact_lineage(monkeypatch):
    recorded: list[dict[str, object]] = []

    async def append_event(_conn, **kwargs):
        recorded.append(kwargs)
        return "evt-operation"

    monkeypatch.setattr(repositories, "append_event", append_event)

    event_id = await repositories.record_run_control_operation(
        object(),
        tenant_id="tenant-a",
        source_run_id="run-source",
        child_run_id="run-child",
        action="resume",
        operation_id="7ea93033-30f5-40ea-8a33-2f3c6e7b21c4",
        trace_id="trace-source",
    )

    assert event_id == "evt-operation"
    assert recorded == [
        {
            "tenant_id": "tenant-a",
            "run_id": "run-source",
            "trace_id": "trace-source",
            "event_type": "run_control_operation_committed",
            "stage": "control",
            "message": "Run control operation committed",
            "visible_to_user": False,
            "payload": {
                "visible_to_user": False,
                "source_run_id": "run-source",
                "child_run_id": "run-child",
                "action": "resume",
                "operation_id": "7ea93033-30f5-40ea-8a33-2f3c6e7b21c4",
            },
        }
    ]


def _run_control_postgres_dsn() -> str:
    dsn = os.getenv("AI_PLATFORM_S0A_SCHEMA_TEST_DSN", "").strip()
    if not dsn:
        pytest.skip("AI_PLATFORM_S0A_SCHEMA_TEST_DSN is not configured")
    return dsn


@pytest.mark.asyncio
async def test_run_control_operation_interleavings_are_exactly_once_in_postgres():
    """Exercise operation-lock creation, GET linearization and scoped resolution on PostgreSQL."""

    dsn = _run_control_postgres_dsn()
    schema_name = f"run_control_operation_{uuid.uuid4().hex}"
    schema_sql = Path("app/schema.sql").read_text(encoding="utf-8")
    observer = await psycopg.AsyncConnection.connect(dsn, autocommit=True, row_factory=dict_row)
    first: psycopg.AsyncConnection | None = None
    second: psycopg.AsyncConnection | None = None
    tasks: list[asyncio.Task] = []
    operation_id = "7ea93033-30f5-40ea-8a33-2f3c6e7b21c4"

    async def set_search_path(conn: psycopg.AsyncConnection) -> None:
        await conn.execute(psycopg_sql.SQL("set search_path to {}").format(psycopg_sql.Identifier(schema_name)))
        await conn.commit()

    async def backend_pid(conn: psycopg.AsyncConnection) -> int:
        cursor = await conn.execute("select pg_backend_pid() as pid")
        return int((await cursor.fetchone())["pid"])

    async def wait_until_blocked(*, waiter_pid: int, blocker_pid: int) -> None:
        for _ in range(300):
            cursor = await observer.execute(
                "select %s = any(pg_blocking_pids(%s)) as is_blocked",
                (blocker_pid, waiter_pid),
            )
            if (await cursor.fetchone())["is_blocked"]:
                return
            await asyncio.sleep(0)
        raise AssertionError("operation resolver never blocked on the in-flight mutation")

    async def create_or_resolve(conn: psycopg.AsyncConnection, child_run_id: str):
        await repositories.acquire_run_control_operation_lock(
            conn,
            tenant_id="tenant-a",
            user_id="user-a",
            source_run_id="run-source",
            action="retry",
            operation_id=operation_id,
        )
        existing = await repositories.get_run_control_operation(
            conn,
            tenant_id="tenant-a",
            user_id="user-a",
            source_run_id="run-source",
            action="retry",
            operation_id=operation_id,
        )
        if existing is not None:
            return existing, False
        await conn.execute(
            "select id from runs where tenant_id = %s and id = %s for update",
            ("tenant-a", "run-source"),
        )
        await conn.execute(
            """
            insert into runs(
              id, tenant_id, workspace_id, session_id, user_id, agent_id, skill_id,
              status, copied_from_run_id, session_generation
            ) values (%s, %s, %s, %s, %s, %s, %s, 'queued', %s, %s)
            """,
            (
                child_run_id,
                "tenant-a",
                "workspace-a",
                "session-a",
                "user-a",
                "agent-a",
                "skill-a",
                "run-source",
                2,
            ),
        )
        await repositories.record_run_control_operation(
            conn,
            tenant_id="tenant-a",
            source_run_id="run-source",
            child_run_id=child_run_id,
            action="retry",
            operation_id=operation_id,
            trace_id="trace-source",
        )
        return {"run_id": child_run_id}, True

    try:
        await observer.execute(psycopg_sql.SQL("create schema {}").format(psycopg_sql.Identifier(schema_name)))
        await observer.execute(psycopg_sql.SQL("set search_path to {}").format(psycopg_sql.Identifier(schema_name)))
        await observer.execute(schema_sql)
        await observer.execute("insert into tenants(id, name) values ('tenant-a', 'Tenant A')")
        await observer.execute(
            "insert into workspaces(id, tenant_id, name) values ('workspace-a', 'tenant-a', 'Workspace A')"
        )
        await observer.execute(
            "insert into users(id, tenant_id, display_name) values ('user-a', 'tenant-a', 'User A'), "
            "('user-b', 'tenant-a', 'User B')"
        )
        await observer.execute(
            "insert into skills(id, name, version, executor_type) values ('skill-a', 'Skill A', '1', 'worker')"
        )
        await observer.execute(
            "insert into agents(id, tenant_id, name, agent_type, default_skill_id) "
            "values ('agent-a', 'tenant-a', 'Agent A', 'assistant', 'skill-a')"
        )
        await observer.execute(
            "insert into sessions(id, tenant_id, workspace_id, user_id, agent_id, next_run_generation) "
            "values ('session-a', 'tenant-a', 'workspace-a', 'user-a', 'agent-a', 1)"
        )
        await observer.execute(
            """
            insert into runs(
              id, tenant_id, workspace_id, session_id, user_id, agent_id, skill_id,
              trace_id, status, session_generation
            ) values ('run-source', 'tenant-a', 'workspace-a', 'session-a', 'user-a',
                      'agent-a', 'skill-a', 'trace-source', 'failed', 1)
            """
        )
        first = await psycopg.AsyncConnection.connect(dsn, row_factory=dict_row)
        second = await psycopg.AsyncConnection.connect(dsn, row_factory=dict_row)
        await set_search_path(first)
        await set_search_path(second)
        first_pid = await backend_pid(first)
        second_pid = await backend_pid(second)

        first_result = await create_or_resolve(first, "run-child-first")
        assert first_result == ({"run_id": "run-child-first"}, True)
        second_task = asyncio.create_task(create_or_resolve(second, "run-child-second"))
        tasks.append(second_task)
        await wait_until_blocked(waiter_pid=second_pid, blocker_pid=first_pid)
        await first.commit()
        second_result = await asyncio.wait_for(second_task, timeout=5)
        await second.commit()

        assert second_result[1] is False
        assert second_result[0]["run_id"] == "run-child-first"
        count_cursor = await observer.execute(
            "select count(*) as count from runs where copied_from_run_id = 'run-source'"
        )
        assert int((await count_cursor.fetchone())["count"]) == 1
        assert await repositories.get_run_control_operation(
            observer,
            tenant_id="tenant-a",
            user_id="user-b",
            source_run_id="run-source",
            action="retry",
            operation_id=operation_id,
        ) is None
        assert await repositories.get_run_control_operation(
            observer,
            tenant_id="tenant-a",
            user_id="user-a",
            source_run_id="run-source",
            action="resume",
            operation_id=operation_id,
        ) is None

        absent_operation_id = "d9428888-122b-4f2e-86f3-df16c79c7358"
        await repositories.acquire_run_control_operation_lock(
            first,
            tenant_id="tenant-a",
            user_id="user-a",
            source_run_id="run-source",
            action="resume",
            operation_id=absent_operation_id,
        )

        async def resolve_absence_after_lock():
            await repositories.acquire_run_control_operation_lock(
                second,
                tenant_id="tenant-a",
                user_id="user-a",
                source_run_id="run-source",
                action="resume",
                operation_id=absent_operation_id,
            )
            return await repositories.get_run_control_operation(
                second,
                tenant_id="tenant-a",
                user_id="user-a",
                source_run_id="run-source",
                action="resume",
                operation_id=absent_operation_id,
            )

        absence_task = asyncio.create_task(resolve_absence_after_lock())
        tasks.append(absence_task)
        await wait_until_blocked(waiter_pid=second_pid, blocker_pid=first_pid)
        await first.rollback()
        assert await asyncio.wait_for(absence_task, timeout=5) is None
        await second.commit()
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        if first is not None:
            await first.close()
        if second is not None:
            await second.close()
        await observer.execute(psycopg_sql.SQL("drop schema if exists {} cascade").format(psycopg_sql.Identifier(schema_name)))
        await observer.close()


@pytest.mark.asyncio
async def test_cancel_run_closes_non_terminal_run_steps():
    class RecordingConnection:
        def __init__(self):
            self.calls = []

        async def execute(self, sql, params):
            normalized = " ".join(sql.split())
            self.calls.append((normalized, params))
            if "set permission_terminalization_target" in normalized:
                return SingleRowCursor(
                    {
                        "id": "run-a",
                        "trace_id": "trace-a",
                        "permission_terminalization_target": "cancelled",
                    }
                )
            if normalized.startswith("select id, trace_id, status, permission_terminalization_target"):
                return SingleRowCursor(
                    {
                        "id": "run-a",
                        "permission_terminalization_target": "cancelled",
                        "permission_terminalization_reason": "run_cancelled",
                    }
                )
            if "has_unterminalized" in normalized:
                return SingleRowCursor({"has_unterminalized": False})
            if "set status = 'cancelled'" in normalized:
                return SingleRowCursor({"id": "run-a", "status": "cancelled"})
            return FakeCursor()

    conn = RecordingConnection()

    result = await cancel_run(
        conn,
        tenant_id="tenant-a",
        run_id="run-a",
        result_json={"message": "cancelled"},
    )

    assert result.completed is True
    assert result.did_transition is True
    assert result.status == "cancelled"
    assert "set permission_terminalization_target" in conn.calls[0][0]
    assert conn.calls[0][1][0:4] == ("cancelled", "cancelled", "cancelled", "run_cancelled")
    assert conn.calls[0][1][-2:] == ("tenant-a", "run-a")
    assert conn.calls[1][0].startswith("select id, trace_id, status, permission_terminalization_target")
    assert conn.calls[2][0].startswith("with locked_run as")
    assert conn.calls[2][1] == ("tenant-a", "run-a", "tenant-a", "run-a", None, None, 50, "cancelled", "run_cancelled")
    assert "has_unterminalized" in conn.calls[3][0]
    assert "set status = 'cancelled'" in conn.calls[4][0]
    step_updates = [call for call in conn.calls if call[0].startswith("update run_steps")]
    assert len(step_updates) == 1
    assert "status in ('pending', 'running')" in step_updates[0][0]
    assert step_updates[0][1] == ("tenant-a", "run-a")


@pytest.mark.asyncio
async def test_fail_run_closes_non_terminal_run_steps_without_leaving_stale_progress():
    class RecordingConnection:
        def __init__(self):
            self.calls = []

        async def execute(self, sql, params):
            normalized = " ".join(sql.split())
            self.calls.append((normalized, params))
            if "set permission_terminalization_target" in normalized:
                return SingleRowCursor(
                    {
                        "id": "run-a",
                        "trace_id": "trace-a",
                        "permission_terminalization_target": "failed",
                    }
                )
            if normalized.startswith("select id, trace_id, status, permission_terminalization_target"):
                return SingleRowCursor(
                    {
                        "id": "run-a",
                        "permission_terminalization_target": "failed",
                        "permission_terminalization_reason": "run_failed",
                        "permission_terminalization_result_json": {"message": "failed"},
                        "permission_terminalization_error_code": "executor_failure",
                        "permission_terminalization_error_message": "boom",
                    }
                )
            if "has_unterminalized" in normalized:
                return SingleRowCursor({"has_unterminalized": False})
            if "set status = 'failed'" in normalized:
                return SingleRowCursor({"id": "run-a", "status": "failed"})
            return FakeCursor()

    conn = RecordingConnection()

    result = await fail_run(
        conn,
        tenant_id="tenant-a",
        run_id="run-a",
        error_code="executor_failure",
        error_message="boom",
        result_json={"message": "failed"},
    )

    assert result.completed is True
    assert result.did_transition is True
    assert result.status == "failed"
    assert "set permission_terminalization_target" in conn.calls[0][0]
    assert conn.calls[0][1][0:4] == ("failed", "failed", "failed", "run_failed")
    assert conn.calls[0][1][-2:] == ("tenant-a", "run-a")
    assert "set latency_ms" in conn.calls[1][0]
    assert conn.calls[2][0].startswith("select id, trace_id, status, permission_terminalization_target")
    assert conn.calls[3][0].startswith("with locked_run as")
    assert conn.calls[3][1] == ("tenant-a", "run-a", "tenant-a", "run-a", None, None, 50, "failed", "run_failed")
    assert "has_unterminalized" in conn.calls[4][0]
    assert "set status = 'failed'" in conn.calls[5][0]
    step_updates = [call for call in conn.calls if call[0].startswith("update run_steps")]
    assert len(step_updates) == 1
    assert "case when status = 'running' then 'failed' else 'cancelled' end" in step_updates[0][0]
    assert "status in ('pending', 'running')" in step_updates[0][0]
    assert step_updates[0][1] == ("tenant-a", "run-a")


@pytest.mark.asyncio
async def test_expired_permission_request_emits_tenant_run_scoped_terminal_audit(monkeypatch):
    calls = []

    class ExpiredRequestConnection:
        async def execute(self, sql, params):
            calls.append(("sql", " ".join(sql.split()), params))
            return SingleRowCursor(
                {
                    "id": "tpr-a",
                    "user_id": "user-a",
                    "run_id": "run-a",
                    "trace_id": "trace-a",
                    "tool_id": "Bash",
                    "tool_call_id": "call-a",
                    "action": "execute",
                    "risk_level": "high",
                    "write_capable": True,
                }
            )

    async def fake_append_event(conn, **kwargs):
        calls.append(("event", kwargs))
        return "evt-a"

    async def fake_append_audit_log(conn, **kwargs):
        calls.append(("audit", kwargs))
        return "aud-a"

    monkeypatch.setattr("app.repositories.append_event", fake_append_event)
    monkeypatch.setattr("app.repositories.append_audit_log", fake_append_audit_log)

    row = await expire_tool_permission_request(
        ExpiredRequestConnection(),
        tenant_id="tenant-a",
        user_id="user-a",
        run_id="run-a",
        request_id="tpr-a",
    )

    assert row["id"] == "tpr-a"
    assert "status = 'expired'" in calls[0][1]
    assert calls[0][2] == (
        "tenant-a", "run-a", "run-a", "user-a", "user-a", "tpr-a", "tpr-a", 1,
        "tenant-a", "user-a", "user-a", "run-a", "run-a", "tpr-a", "tpr-a", 1,
    )
    events = [entry[1] for entry in calls if entry[0] == "event"]
    audits = [entry[1] for entry in calls if entry[0] == "audit"]
    assert len(events) == 1
    assert events[0]["tenant_id"] == "tenant-a"
    assert events[0]["run_id"] == "run-a"
    assert events[0]["event_type"] == "tool_permission_terminalized"
    assert events[0]["payload"]["permission_request_id"] == "tpr-a"
    assert events[0]["payload"]["status"] == "expired"
    assert events[0]["payload"]["tool_call_id"] == "call-a"
    assert len(audits) == 1
    assert audits[0]["tenant_id"] == "tenant-a"
    assert audits[0]["user_id"] is None
    assert audits[0]["target_id"] == "tpr-a"
    assert audits[0]["payload_json"]["run_id"] == "run-a"
    assert audits[0]["payload_json"]["request_user_id"] == "user-a"


@pytest.mark.asyncio
async def test_terminal_run_writes_do_not_overwrite_existing_terminal_status():
    class TerminalRecordingConnection(RecordingConnection):
        async def execute(self, sql, params):
            normalized = " ".join(sql.split())
            if "set permission_terminalization_target = case" in normalized:
                self.calls.append((normalized, params))
                return SingleRowCursor({"permission_terminalization_target": params[0]})
            return await super().execute(sql, params)

    conn = TerminalRecordingConnection()

    await complete_run(conn, tenant_id="tenant-a", run_id="run-a", result_json={"message": "done"})
    await fail_run(conn, tenant_id="tenant-a", run_id="run-b", error_code="executor_failure", error_message="boom")
    await cancel_run(conn, tenant_id="tenant-a", run_id="run-c", result_json={"message": "cancelled"})

    update_runs_sql = [sql for sql, _params in conn.calls if "update runs" in sql]
    assert len(update_runs_sql) == 4
    completion_lock_index, completion_lock_sql = next(
        (index, sql)
        for index, (sql, _params) in enumerate(conn.calls)
        if sql.startswith("select id from runs") and "for update" in sql
    )
    completion_index, completion_sql = next(
        (index, sql)
        for index, (sql, _params) in enumerate(conn.calls)
        if "update runs" in sql and "status = 'succeeded'" in sql
    )
    assert completion_lock_index < completion_index
    assert "status not in ('succeeded', 'failed', 'cancelled')" in completion_lock_sql
    assert "cancel_requested_at is null" in completion_lock_sql
    assert "permission_terminalization_target is null" in completion_lock_sql
    assert "where runs.tenant_id = %s and runs.id = %s" in completion_sql

    staged_terminal_updates = [sql for sql in update_runs_sql if "permission_terminalization_target = case" in sql]
    assert len(staged_terminal_updates) == 2
    assert all("status not in ('succeeded', 'failed', 'cancelled')" in sql for sql in staged_terminal_updates)
    metric_staging_updates = [sql for sql in update_runs_sql if "set latency_ms" in sql]
    assert len(metric_staging_updates) == 1
    assert "status not in ('succeeded', 'failed', 'cancelled')" in metric_staging_updates[0]
    assert all("status = 'failed'" not in sql and "status = 'cancelled'" not in sql for sql in metric_staging_updates)


@pytest.mark.asyncio
async def test_record_sandbox_runtime_cleanup_outcome_writes_event_and_audit(monkeypatch):
    calls = []

    async def fake_append_event(conn, **kwargs):
        calls.append(("event", kwargs))
        return "evt-cleanup"

    async def fake_append_audit_log(conn, **kwargs):
        calls.append(("audit", kwargs))
        return "aud-cleanup"

    monkeypatch.setattr("app.repositories.append_event", fake_append_event)
    monkeypatch.setattr("app.repositories.append_audit_log", fake_append_audit_log)

    await repositories.record_sandbox_runtime_cleanup_outcome(
        object(),
        tenant_id="tenant-a",
        run_id="run-a",
        trace_id="trace-a",
        user_id="user-a",
        requested_by_role="owner",
        reason="cancel_requested",
        status="failed",
        lease_ids=["lease-a"],
        failures=[{"container_id": "lease-a", "message": "runtime handle missing"}],
    )

    assert calls == [
        (
            "event",
            {
                "tenant_id": "tenant-a",
                "run_id": "run-a",
                "trace_id": "trace-a",
                "event_type": "sandbox_runtime_cleanup_failed",
                "stage": "sandbox",
                "message": "Sandbox runtime cleanup failed",
                "payload": {
                    "visible_to_user": False,
                    "reason": "cancel_requested",
                    "status": "failed",
                    "lease_ids": ["lease-a"],
                    "failure_count": 1,
                    "requested_by_role": "owner",
                    "failures": [{"container_id": "lease-a", "message": "runtime handle missing"}],
                },
            },
        ),
        (
            "audit",
            {
                "tenant_id": "tenant-a",
                "user_id": "user-a",
                "action": "sandbox.runtime.cleanup.failed",
                "target_type": "run",
                "target_id": "run-a",
                "trace_id": "trace-a",
                "payload_json": {
                    "run_id": "run-a",
                    "reason": "cancel_requested",
                    "status": "failed",
                    "lease_ids": ["lease-a"],
                    "failures": [{"container_id": "lease-a", "message": "runtime handle missing"}],
                    "requested_by_role": "owner",
                },
            },
        ),
    ]


@pytest.mark.asyncio
async def test_create_run_persists_g2_contract_fields():
    conn = RecordingConnection()

    run_id = await create_run(
        conn,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        session_id="session-a",
        user_id="user-a",
        agent_id="general-agent",
        skill_id="general-chat",
        input_json={},
    )

    sql, params = conn.calls[-1]
    assert run_id.startswith("run_")
    assert "trace_id" in sql
    assert "schema_version" in sql
    assert "executor_schema_version" in sql
    assert "principal_roles" in sql
    assert any(str(item).startswith("trace_") for item in params)
    assert "ai-platform.run.v1" in params
    assert "ai-platform.executor-result.v1" in params


@pytest.mark.asyncio
async def test_create_run_binds_normalized_auth_snapshot():
    conn = RecordingConnection()

    await create_run(
        conn,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        session_id="session-a",
        user_id="user-a",
        agent_id="general-agent",
        skill_id="general-chat",
        input_json={},
        principal_roles=[" QA-Operator ", "qa operator", "User"],
        principal_department_id="qa",
        auth_source="trusted-header",
    )

    sql, params = conn.calls[-1]
    assert "principal_roles, principal_department_id, auth_source" in sql
    assert json.dumps(["qa-operator", "qa operator", "user"], ensure_ascii=False) in params
    assert "qa" in params
    assert "trusted-header" in params


@pytest.mark.asyncio
async def test_session_generation_allocator_serializes_allocation_at_the_session_row():
    class GenerationConnection:
        def __init__(self):
            self.calls = []
            self.next_generation = 0

        async def execute(self, sql, params):
            normalized = " ".join(sql.split())
            self.calls.append((normalized, params))
            self.next_generation += 1
            return SingleRowCursor({"next_run_generation": self.next_generation})

    conn = GenerationConnection()
    first = await repositories.allocate_session_run_generation(
        conn,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        session_id="session-a",
        agent_id="general-agent",
    )
    second = await repositories.allocate_session_run_generation(
        conn,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        session_id="session-a",
        agent_id="general-agent",
    )

    assert (first, second) == (1, 2)
    sql, params = conn.calls[0]
    assert sql.startswith("update sessions set next_run_generation = next_run_generation + 1")
    assert "user_id is not distinct from %s" in sql
    assert "returning next_run_generation" in sql
    assert params == ("tenant-a", "workspace-a", "user-a", "session-a", "general-agent")


@pytest.mark.asyncio
async def test_context_snapshot_binding_rejects_a_mismatched_public_reference_before_sql():
    class NoQueryConnection:
        async def execute(self, *_args, **_kwargs):
            raise AssertionError("mismatched snapshot reference must not execute SQL")

    with pytest.raises(RepositoryConflictError, match="context_snapshot_binding_invalid"):
        await repositories.update_run_context_snapshot_ref(
            NoQueryConnection(),
            tenant_id="tenant-a",
            run_id="run-a",
            context_snapshot_id="ctx-a",
            context_snapshot={"context_snapshot_id": "ctx-other"},
        )


@pytest.mark.asyncio
async def test_context_snapshot_binding_requires_same_scope_executor_and_allows_only_exact_repeat():
    conn = SingleRowConnection({"context_snapshot_id": "ctx-a"})

    await repositories.update_run_context_snapshot_ref(
        conn,
        tenant_id="tenant-a",
        run_id="run-a",
        context_snapshot_id="ctx-a",
        context_snapshot={"context_snapshot_id": "ctx-a", "source": "chat_stream"},
    )

    assert "context_kind = 'executor'" in conn.sql
    assert "context_snapshot_id is null" in conn.sql
    assert "context_snapshot_id = %s" in conn.sql
    assert "input_json->>'context_snapshot_id' = context_snapshot_id" in conn.sql
    assert conn.params[-2:] == ("ctx-a", "ctx-a")


@pytest.mark.asyncio
async def test_context_snapshot_binding_fails_closed_when_the_database_rejects_scope_or_rebinding():
    conn = SingleRowConnection(None)

    with pytest.raises(RepositoryConflictError, match="context_snapshot_binding_invalid"):
        await repositories.update_run_context_snapshot_ref(
            conn,
            tenant_id="tenant-a",
            run_id="run-a",
            context_snapshot_id="ctx-other",
            context_snapshot={"context_snapshot_id": "ctx-other", "source": "chat_stream"},
        )


@pytest.mark.asyncio
async def test_create_session_validates_workspace_tenant_before_insert(monkeypatch):
    calls = []

    async def ensure_workspace_belongs_to_tenant(conn, *, tenant_id, workspace_id):
        calls.append(("ensure_workspace", tenant_id, workspace_id, len(conn.calls)))

    monkeypatch.setattr(
        repositories,
        "ensure_workspace_belongs_to_tenant",
        ensure_workspace_belongs_to_tenant,
        raising=False,
    )
    conn = RecordingConnection()

    await repositories.create_session(
        conn,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        agent_id="general-agent",
        title="General",
    )

    assert calls == [("ensure_workspace", "tenant-a", "workspace-a", 0)]
    assert conn.calls[-1][0].startswith("insert into sessions")


@pytest.mark.asyncio
async def test_create_run_validates_workspace_tenant_before_insert(monkeypatch):
    calls = []

    async def ensure_workspace_belongs_to_tenant(conn, *, tenant_id, workspace_id):
        calls.append(("ensure_workspace", tenant_id, workspace_id, len(conn.calls)))

    monkeypatch.setattr(
        repositories,
        "ensure_workspace_belongs_to_tenant",
        ensure_workspace_belongs_to_tenant,
        raising=False,
    )
    conn = RecordingConnection()

    await repositories.create_run(
        conn,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        session_id="session-a",
        user_id="user-a",
        agent_id="general-agent",
        skill_id="general-chat",
        input_json={},
    )

    assert calls == [("ensure_workspace", "tenant-a", "workspace-a", 0)]
    assert conn.calls[-1][0].startswith("insert into runs")


@pytest.mark.asyncio
async def test_ensure_workspace_belongs_to_tenant_raises_for_missing_workspace():
    ensure_workspace = getattr(repositories, "ensure_workspace_belongs_to_tenant", None)
    assert callable(ensure_workspace), "ensure_workspace_belongs_to_tenant missing"

    class EmptyCursor:
        async def fetchone(self):
            return None

    class WorkspaceConnection:
        def __init__(self):
            self.calls = []

        async def execute(self, sql, params):
            self.calls.append((" ".join(sql.split()), params))
            return EmptyCursor()

    conn = WorkspaceConnection()

    with pytest.raises(RepositoryNotFoundError, match="workspace_not_found"):
        await ensure_workspace(
            conn,
            tenant_id="tenant-a",
            workspace_id="workspace-b",
        )

    assert conn.calls[0][1] == ("tenant-a", "workspace-b")


@pytest.mark.asyncio
async def test_update_run_auth_snapshot_normalizes_roles_and_scopes_update():
    conn = RecordingConnection()

    await repositories.update_run_auth_snapshot(
        conn,
        tenant_id="tenant-a",
        run_id="run-a",
        principal_roles=[" QA-Operator ", "qa operator", "User"],
        principal_department_id="qa",
        auth_source="trusted-header",
    )

    sql, params = conn.calls[-1]
    assert "update runs" in sql
    assert "principal_roles = %s::jsonb" in sql
    assert "principal_department_id = %s" in sql
    assert "auth_source = %s" in sql
    assert params == (
        json.dumps(["qa-operator", "qa operator", "user"], ensure_ascii=False),
        "qa",
        "trusted-header",
        "tenant-a",
        "run-a",
    )


@pytest.mark.asyncio
async def test_locked_run_query_projects_complete_auth_snapshot():
    conn = RecordingConnection()

    await repositories.mark_run_running(conn, tenant_id="tenant-a", run_id="run-a")

    sql, _params = conn.calls[0]
    assert "runs.principal_roles" in sql
    assert "runs.principal_department_id" in sql
    assert "runs.auth_source" in sql


@pytest.mark.asyncio
async def test_create_run_rejects_session_scope_mismatch_before_insert_returns():
    class EmptyCursor:
        async def fetchone(self):
            return None

    class RunConnection:
        def __init__(self):
            self.calls = []

        async def execute(self, sql, params):
            normalized = " ".join(sql.split())
            self.calls.append((normalized, params))
            if normalized.startswith("select id, tenant_id, status from workspaces"):
                return SingleRowCursor({"id": "workspace-a", "tenant_id": "tenant-a", "status": "active"})
            return EmptyCursor()

    conn = RunConnection()

    with pytest.raises(RepositoryNotFoundError, match="session_not_found"):
        await create_run(
            conn,
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            session_id="session-cross-scope",
            user_id="user-a",
            agent_id="general-agent",
            skill_id="general-chat",
            input_json={},
        )

    sql, params = conn.calls[-1]
    assert sql.startswith("update sessions set next_run_generation")
    assert "tenant_id = %s" in sql
    assert "workspace_id = %s" in sql
    assert "user_id is not distinct from %s" in sql
    assert "id = %s" in sql
    assert "agent_id = %s" in sql
    assert "returning next_run_generation" in sql
    assert "session-cross-scope" in params


@pytest.mark.asyncio
async def test_mark_run_running_requires_run_session_scope_to_match():
    conn = RecordingConnection()

    await repositories.mark_run_running(conn, tenant_id="tenant-a", run_id="run-a")

    sql, params = conn.calls[0]
    assert "update runs" in sql
    assert "from sessions" in sql
    assert "sessions.id = runs.session_id" in sql
    assert "sessions.tenant_id = runs.tenant_id" in sql
    assert "sessions.workspace_id = runs.workspace_id" in sql
    assert "sessions.user_id = runs.user_id" in sql
    assert "sessions.agent_id = runs.agent_id" in sql
    assert params == ("tenant-a", "run-a")


@pytest.mark.asyncio
async def test_append_event_persists_standard_envelope_columns():
    conn = RecordingConnection()

    await append_event(
        conn,
        tenant_id="tenant-a",
        run_id="run-a",
        trace_id="trace_a",
        event_type="run_failed",
        stage="worker",
        message="Run failed",
        payload={"severity": "error", "visible_to_user": False, "error_code": "executor_failure"},
        latency_ms=12,
    )

    sql, params = conn.calls[0]
    assert "trace_id" in sql
    assert "schema_version" in sql
    assert "sequence" in sql
    assert "coalesce(max(sequence), 0) + 1" in sql
    assert "severity" in sql
    assert "visible_to_user" in sql
    assert "error_code" in sql
    assert "latency_ms" in sql
    assert "trace_a" in params
    assert "ai-platform.event-envelope.v1" in params
    assert "error" in params
    assert False in params
    assert "executor_failure" in params
    assert 12 in params


@pytest.mark.asyncio
async def test_list_run_events_supports_sequence_cursor_and_limit():
    conn = RecordingConnection()

    await list_run_events(conn, tenant_id="tenant-a", run_id="run-a", after_sequence=7, limit=20)

    sql, params = conn.calls[0]
    assert "sequence > %s" in sql
    assert "order by sequence asc, created_at asc" in sql
    assert "limit %s" in sql
    assert params == ("tenant-a", "run-a", 7, 20)


@pytest.mark.asyncio
async def test_create_artifact_persists_manifest_version_and_trace_id():
    conn = RecordingConnection()

    await create_artifact(
        conn,
        artifact_id="art-a",
        tenant_id="tenant-a",
        run_id="run-a",
        trace_id="trace_a",
        artifact_type="reviewed_docx",
        label="批注 Word",
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        storage_key="tenants/tenant-a/runs/run-a/artifacts/1/reviewed.docx",
        size_bytes=10,
        manifest_json={},
    )

    sql, params = conn.calls[0]
    assert "manifest_version" in sql
    assert "trace_id" in sql
    assert "ai-platform.artifact-manifest.v1" in params
    assert "trace_a" in params


@pytest.mark.asyncio
async def test_create_context_snapshot_persists_scope_and_context_contract():
    conn = RecordingConnection()

    snapshot = await create_context_snapshot(
        conn,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        session_id="session-a",
        run_id="run-a",
        trace_id="trace-a",
        context_kind="executor",
        included_message_ids=["msg-a"],
        included_file_ids=["file-a"],
        included_artifact_ids=["art-a"],
        included_memory_record_ids=["mem-a"],
        redaction_summary_json={"secrets": 0},
        payload_json={"window": "current"},
    )

    sql, params = conn.calls[0]
    assert snapshot["id"].startswith("ctx_")
    assert "run_context_snapshots" in sql
    assert "ai-platform.context-snapshot.v1" in params
    assert any("\"msg-a\"" in str(item) for item in params)
    assert any("\"mem-a\"" in str(item) for item in params)
    assert "eligible_members" in sql
    assert "jsonb_array_elements_text" in sql
    assert "eligible_message_count = jsonb_array_length(message_ids)" in sql
    assert "eligible_file_count = jsonb_array_length(file_ids)" in sql
    assert "eligible_artifact_count = jsonb_array_length(artifact_ids)" in sql
    assert "eligible_memory_record_count = jsonb_array_length(memory_record_ids)" in sql
    assert "runs.workspace_id = %s" not in sql
    assert "runs.session_id = %s" not in sql
    assert "workspace-a" not in params
    assert "session-a" not in params
    assert "message_run.id = scoped_run.run_id" not in sql
    assert "file_run.id = scoped_run.run_id" not in sql
    assert "artifact_run.id = scoped_run.run_id" not in sql
    assert "join runs message_run" in sql
    assert "join runs file_run" in sql
    assert "messages.run_id is null" not in sql
    assert "files.run_id is null" not in sql
    assert "artifacts.expires_at > statement_timestamp()" in sql
    assert "memory_records.expires_at > statement_timestamp()" in sql


@pytest.mark.asyncio
async def test_create_context_snapshot_returns_scope_derived_by_the_atomic_statement():
    conn = SingleRowConnection(
        {
            "id": "ctx-from-statement",
            "tenant_id": "tenant-a",
            "workspace_id": "workspace-canonical",
            "user_id": "user-a",
            "session_id": "session-canonical",
            "run_id": "run-a",
            "trace_id": "trace-canonical",
        }
    )

    snapshot = await create_context_snapshot(
        conn,
        tenant_id="tenant-a",
        workspace_id="workspace-untrusted",
        user_id="user-a",
        session_id="session-untrusted",
        run_id="run-a",
        trace_id="trace-untrusted",
        context_kind="executor",
        included_message_ids=[],
        included_file_ids=[],
        included_artifact_ids=[],
        included_memory_record_ids=[],
        redaction_summary_json={},
        payload_json={},
    )

    assert snapshot["id"].startswith("ctx_")
    assert snapshot["workspace_id"] == "workspace-canonical"
    assert snapshot["session_id"] == "session-canonical"
    assert snapshot["trace_id"] == "trace-canonical"
    assert "workspace-untrusted" not in conn.params
    assert "session-untrusted" not in conn.params
    assert "trace-untrusted" not in conn.params


@pytest.mark.asyncio
async def test_create_context_snapshot_sanitizes_payload_and_summary_before_insert():
    conn = RecordingConnection()

    snapshot = await create_context_snapshot(
        conn,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        session_id="session-a",
        run_id="run-a",
        trace_id="trace-a",
        context_kind="executor",
        included_message_ids=[],
        included_file_ids=[],
        included_artifact_ids=[],
        included_memory_record_ids=[],
        redaction_summary_json={
            "source": "internal",
            "client_secret": "client-secret-context",
            "note": "authorization: Bearer context-bearer",
        },
        payload_json={
            "window": "current",
            "api_key": "sk-context-secret",
            "runtime_path": "/var/lib/ai-platform/run-a",
            "nested": {"email": "alice@example.com", "safe": "kept"},
        },
    )

    _sql, params = conn.calls[0]
    inserted_summary = json.loads(params[-2])
    inserted_payload = json.loads(params[-1])
    assert inserted_summary == {"source": "internal", "note": "authorization=[redacted-secret]"}
    assert inserted_payload == {
        "window": "current",
        "nested": {"email": "[redacted-email]", "safe": "kept"},
    }
    assert snapshot["redaction_summary_json"] == inserted_summary
    assert snapshot["payload_json"] == inserted_payload
    serialized = json.dumps(snapshot, ensure_ascii=False) + str(params)
    assert "client-secret-context" not in serialized
    assert "context-bearer" not in serialized
    assert "sk-context-secret" not in serialized
    assert "/var/lib/ai-platform" not in serialized
    assert "alice@example.com" not in serialized


@pytest.mark.asyncio
async def test_create_context_snapshot_rejects_unverified_members_without_insert_returning():
    class EmptyCursor:
        async def fetchone(self):
            return None

    class SnapshotConnection:
        def __init__(self):
            self.calls = []

        async def execute(self, sql, params):
            self.calls.append((" ".join(sql.split()), params))
            return EmptyCursor()

    conn = SnapshotConnection()

    with pytest.raises(RepositoryConflictError, match="context_snapshot_material_invalid"):
        await create_context_snapshot(
            conn,
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            user_id="user-a",
            session_id="session-a",
            run_id="run-cross-scope",
            trace_id="trace-a",
            context_kind="executor",
            included_message_ids=[],
            included_file_ids=[],
            included_artifact_ids=[],
            included_memory_record_ids=[],
            redaction_summary_json={},
            payload_json={},
        )

    sql, params = conn.calls[0]
    assert "insert into run_context_snapshots" in sql
    assert "from runs" in sql
    assert "join sessions" in sql
    assert "runs.tenant_id = %s" in sql
    assert "runs.user_id = %s" in sql
    assert "runs.id = %s" in sql
    assert "sessions.id = runs.session_id" in sql
    assert "messages.session_id = scoped_run.session_id" in sql
    assert "file_run.session_id = scoped_run.session_id" in sql
    assert "artifact_run.session_id = scoped_run.session_id" in sql
    assert "memory_records.session_id = scoped_run.session_id" in sql
    assert "memory_records.status = 'active'" in sql
    assert "memory_records.deleted_at is null" in sql
    assert "artifacts.expires_at is null or artifacts.expires_at > statement_timestamp()" in sql
    assert "returning id" in sql
    assert "run-cross-scope" in params


@pytest.mark.asyncio
async def test_create_context_snapshot_rejects_duplicate_or_oversized_members_before_sql():
    class NoQueryConnection:
        async def execute(self, *_args, **_kwargs):
            raise AssertionError("invalid context snapshot members must not execute SQL")

    common = {
        "tenant_id": "tenant-a",
        "workspace_id": "workspace-a",
        "user_id": "user-a",
        "session_id": "session-a",
        "run_id": "run-a",
        "trace_id": "trace-a",
        "context_kind": "executor",
        "included_file_ids": [],
        "included_artifact_ids": [],
        "included_memory_record_ids": [],
        "redaction_summary_json": {},
        "payload_json": {},
    }

    with pytest.raises(RepositoryConflictError, match="context_snapshot_material_invalid"):
        await create_context_snapshot(
            NoQueryConnection(),
            included_message_ids=["msg-a", "msg-a"],
            **common,
        )
    with pytest.raises(RepositoryConflictError, match="context_snapshot_material_invalid"):
        await create_context_snapshot(
            NoQueryConnection(),
            included_message_ids=[f"msg-{index}" for index in range(129)],
            **common,
        )


@pytest.mark.asyncio
async def test_get_context_snapshot_for_worker_scopes_by_full_run_identity():
    class SnapshotCursor:
        async def fetchone(self):
            return {"id": "ctx-a", "payload_json": {"source": "db"}}

    class SnapshotConnection:
        def __init__(self):
            self.sql = ""
            self.params = None

        async def execute(self, sql, params):
            self.sql = " ".join(sql.split())
            self.params = params
            return SnapshotCursor()

    conn = SnapshotConnection()

    row = await get_context_snapshot_for_worker(
        conn,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        session_id="session-a",
        run_id="run-a",
        context_snapshot_id="ctx-a",
    )

    assert row == {"id": "ctx-a", "payload_json": {"source": "db"}}
    assert "from run_context_snapshots" in conn.sql
    assert "tenant_id = %s" in conn.sql
    assert "workspace_id = %s" in conn.sql
    assert "user_id = %s" in conn.sql
    assert "session_id = %s" in conn.sql
    assert "run_id = %s" in conn.sql
    assert "id = %s" in conn.sql
    assert "join runs on runs.context_snapshot_id = run_context_snapshots.id" in conn.sql
    assert "runs.input_json->>'context_snapshot_id' = runs.context_snapshot_id" in conn.sql
    assert "runs.input_json->'context_snapshot'->>'context_snapshot_id' = runs.context_snapshot_id" in conn.sql
    assert conn.params == ("tenant-a", "workspace-a", "user-a", "session-a", "run-a", "ctx-a")


@pytest.mark.asyncio
async def test_get_authorized_context_target_session_scopes_by_tenant_workspace_user_and_session():
    class SessionCursor:
        async def fetchone(self):
            return {
                "id": "session-target",
                "tenant_id": "tenant-a",
                "workspace_id": "workspace-a",
                "user_id": "user-a",
                "agent_id": "general-agent",
                "status": "active",
            }

    class SessionConnection:
        def __init__(self):
            self.sql = ""
            self.params = None

        async def execute(self, sql, params):
            self.sql = " ".join(sql.split())
            self.params = params
            return SessionCursor()

    conn = SessionConnection()

    row = await get_authorized_context_target_session(
        conn,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        session_id="session-target",
    )

    assert row["id"] == "session-target"
    assert "from sessions" in conn.sql
    assert "tenant_id = %s" in conn.sql
    assert "workspace_id = %s" in conn.sql
    assert "user_id = %s" in conn.sql
    assert "id = %s" in conn.sql
    assert "status = 'active'" in conn.sql
    assert conn.params == ("tenant-a", "workspace-a", "user-a", "session-target")


@pytest.mark.asyncio
async def test_list_context_share_snapshots_for_target_session_filters_public_payload_target_binding():
    class ShareCursor:
        async def fetchall(self):
            return [
                {
                    "id": "ctx-share",
                    "payload_json": {
                        "share_fork_context": {
                            "target_session_id": "session-target",
                            "redaction_state": "public_redacted",
                        }
                    },
                }
            ]

    class ShareConnection:
        def __init__(self):
            self.sql = ""
            self.params = None

        async def execute(self, sql, params):
            self.sql = " ".join(sql.split())
            self.params = params
            return ShareCursor()

    conn = ShareConnection()

    rows = await list_context_share_snapshots_for_target_session(
        conn,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        target_session_id="session-target",
    )

    assert rows[0]["id"] == "ctx-share"
    assert "from run_context_snapshots" in conn.sql
    assert "context_kind = 'share_fork'" in conn.sql
    assert "payload_json->'share_fork_context'->>'target_session_id' = %s" in conn.sql
    assert "tenant_id = %s" in conn.sql
    assert "workspace_id = %s" in conn.sql
    assert "user_id = %s" in conn.sql
    assert conn.params == ("tenant-a", "workspace-a", "user-a", "session-target")


@pytest.mark.asyncio
async def test_executor_context_compatibility_lookup_uses_physical_run_binding():
    class ExecutorSnapshotCursor:
        async def fetchone(self):
            return {"id": "ctx-executor", "context_kind": "executor"}

    class ExecutorSnapshotConnection:
        def __init__(self):
            self.sql = ""
            self.params = None

        async def execute(self, sql, params):
            self.sql = " ".join(sql.split())
            self.params = params
            return ExecutorSnapshotCursor()

    conn = ExecutorSnapshotConnection()

    row = await get_latest_authorized_executor_context_snapshot(
        conn,
        tenant_id="tenant-a",
        user_id="user-a",
        run_id="run-source",
    )

    assert row["id"] == "ctx-executor"
    assert "from runs" in conn.sql
    assert "context_snapshot.id = runs.context_snapshot_id" in conn.sql
    assert "runs.tenant_id = %s" in conn.sql
    assert "runs.user_id = %s" in conn.sql
    assert "runs.id = %s" in conn.sql
    assert "context_snapshot.context_kind = 'executor'" in conn.sql
    assert "order by" not in conn.sql
    assert conn.params == ("tenant-a", "user-a", "run-source")


@pytest.mark.asyncio
async def test_get_effective_memory_policy_defaults_to_session_only_memory_when_no_policy():
    class EmptyPolicyCursor:
        async def fetchone(self):
            return None

    class PolicyConnection:
        def __init__(self):
            self.calls = []

        async def execute(self, sql, params):
            self.calls.append((" ".join(sql.split()), params))
            return EmptyPolicyCursor()

    conn = PolicyConnection()

    policy = await repositories.get_effective_memory_policy(
        conn,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        agent_id="general-agent",
    )

    sql, params = conn.calls[0]
    assert "from memory_policies" in sql
    assert "agent_id = %s or agent_id is null" in sql
    assert params == ("tenant-a", "workspace-a", "user-a", "general-agent", "general-agent")
    assert policy == {
        "tenant_id": "tenant-a",
        "workspace_id": "workspace-a",
        "user_id": "user-a",
        "agent_id": "general-agent",
        "memory_enabled": True,
        "long_term_memory_enabled": False,
        "retention_days": 90,
        "redaction_mode": "standard",
        "source": "default",
        "reason": "",
        "updated_by": "",
        "updated_at": None,
    }


@pytest.mark.asyncio
async def test_get_effective_memory_policy_clamps_legacy_long_term_memory_enabled_rows():
    class LegacyPolicyCursor:
        async def fetchone(self):
            return {
                "id": "mempol-legacy",
                "tenant_id": "tenant-a",
                "workspace_id": "workspace-a",
                "user_id": "user-a",
                "agent_id": "general-agent",
                "memory_enabled": True,
                "long_term_memory_enabled": True,
                "retention_days": 90,
                "redaction_mode": "standard",
                "reason": "legacy dirty row",
                "updated_by": "legacy-admin",
                "updated_at": "2026-06-02T12:00:00Z",
            }

    class PolicyConnection:
        async def execute(self, sql, params):
            return LegacyPolicyCursor()

    policy = await repositories.get_effective_memory_policy(
        PolicyConnection(),
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        agent_id="general-agent",
    )

    assert policy["source"] == "stored"
    assert policy["memory_enabled"] is True
    assert policy["long_term_memory_enabled"] is False
    assert policy["redaction_mode"] == "standard"


@pytest.mark.asyncio
async def test_get_effective_memory_policy_treats_invalid_stored_redaction_mode_as_strict():
    class DirtyPolicyCursor:
        async def fetchone(self):
            return {
                "id": "mempol-dirty",
                "tenant_id": "tenant-a",
                "workspace_id": "workspace-a",
                "user_id": "user-a",
                "agent_id": "general-agent",
                "memory_enabled": True,
                "long_term_memory_enabled": False,
                "retention_days": 90,
                "redaction_mode": "off",
                "reason": "manual dirty row",
                "updated_by": "legacy-admin",
                "updated_at": "2026-06-02T12:00:00Z",
            }

    class PolicyConnection:
        async def execute(self, sql, params):
            return DirtyPolicyCursor()

    policy = await repositories.get_effective_memory_policy(
        PolicyConnection(),
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        agent_id="general-agent",
    )

    assert policy["redaction_mode"] == "strict"


@pytest.mark.asyncio
async def test_get_effective_memory_policy_treats_blank_stored_redaction_mode_as_strict():
    class DirtyPolicyCursor:
        async def fetchone(self):
            return {
                "id": "mempol-dirty",
                "tenant_id": "tenant-a",
                "workspace_id": "workspace-a",
                "user_id": "user-a",
                "agent_id": "general-agent",
                "memory_enabled": True,
                "long_term_memory_enabled": False,
                "retention_days": 90,
                "redaction_mode": "",
                "reason": "manual dirty row",
                "updated_by": "legacy-admin",
                "updated_at": "2026-06-02T12:00:00Z",
            }

    class PolicyConnection:
        async def execute(self, sql, params):
            return DirtyPolicyCursor()

    policy = await repositories.get_effective_memory_policy(
        PolicyConnection(),
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        agent_id="general-agent",
    )

    assert policy["redaction_mode"] == "strict"


@pytest.mark.asyncio
async def test_set_memory_policy_upserts_deterministic_scope_without_secret_reason_leak():
    class UpsertCursor:
        async def fetchone(self):
            return {
                "id": "mempol_test",
                "tenant_id": "tenant-a",
                "workspace_id": "workspace-a",
                "user_id": "user-a",
                "agent_id": "general-agent",
                "memory_enabled": False,
                "long_term_memory_enabled": False,
                "retention_days": 30,
                "redaction_mode": "strict",
                "reason": "user opt-out [redacted-secret]",
                "updated_by": "admin-a",
                "updated_at": "2026-06-02T12:00:00Z",
            }

    class PolicyConnection:
        def __init__(self):
            self.calls = []

        async def execute(self, sql, params):
            self.calls.append((" ".join(sql.split()), params))
            return UpsertCursor()

    conn = PolicyConnection()

    policy = await repositories.set_memory_policy(
        conn,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        agent_id="general-agent",
        memory_enabled=False,
        long_term_memory_enabled=False,
        retention_days=30,
        redaction_mode="strict",
        reason="user opt-out [redacted-secret]",
        updated_by="admin-a",
    )

    sql, params = conn.calls[0]
    assert "insert into memory_policies" in sql
    assert "on conflict (id) do update" in sql
    assert "token=hidden" not in str(params)
    assert params[1:11] == (
        "tenant-a",
        "workspace-a",
        "user-a",
        "general-agent",
        False,
        False,
        30,
        "strict",
        "user opt-out [redacted-secret]",
        "admin-a",
    )
    assert policy["memory_enabled"] is False
    assert policy["long_term_memory_enabled"] is False
    assert policy["redaction_mode"] == "strict"
    assert policy["source"] == "stored"


@pytest.mark.asyncio
async def test_set_memory_policy_rejects_long_term_enable_at_repository_boundary():
    class FailConnection:
        async def execute(self, sql, params):
            raise AssertionError("repository must reject long-term memory before SQL")

    with pytest.raises(RepositoryConflictError, match="long_term_memory_not_available"):
        await repositories.set_memory_policy(
            FailConnection(),
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            user_id="user-a",
            agent_id="general-agent",
            memory_enabled=True,
            long_term_memory_enabled=True,
            retention_days=90,
            redaction_mode="standard",
            reason="enable",
            updated_by="admin-a",
        )


@pytest.mark.asyncio
async def test_set_memory_policy_rejects_invalid_redaction_mode_before_sql():
    class FailConnection:
        async def execute(self, sql, params):
            raise AssertionError("repository must reject invalid redaction mode before SQL")

    with pytest.raises(RepositoryConflictError, match="memory_redaction_mode_invalid"):
        await repositories.set_memory_policy(
            FailConnection(),
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            user_id="user-a",
            agent_id="general-agent",
            memory_enabled=True,
            long_term_memory_enabled=False,
            retention_days=90,
            redaction_mode="off",
            reason="invalid",
            updated_by="admin-a",
        )


@pytest.mark.asyncio
async def test_set_memory_policy_rejects_blank_redaction_mode_before_sql():
    class FailConnection:
        async def execute(self, sql, params):
            raise AssertionError("repository must reject blank redaction mode before SQL")

    with pytest.raises(RepositoryConflictError, match="memory_redaction_mode_invalid"):
        await repositories.set_memory_policy(
            FailConnection(),
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            user_id="user-a",
            agent_id="general-agent",
            memory_enabled=True,
            long_term_memory_enabled=False,
            retention_days=90,
            redaction_mode="",
            reason="invalid",
            updated_by="admin-a",
        )


@pytest.mark.asyncio
async def test_list_admin_memory_policies_scopes_filters_clamps_and_closes_long_term():
    class PolicyCursor:
        async def fetchall(self):
            return [
                {
                    "id": "mempol-user-a",
                    "tenant_id": "tenant-a",
                    "workspace_id": "workspace-a",
                    "user_id": "user-a",
                    "agent_id": "general-agent",
                    "memory_enabled": False,
                    "long_term_memory_enabled": True,
                    "retention_days": 30,
                    "redaction_mode": "strict",
                    "reason": "user opt-out",
                    "updated_by": "user-a",
                    "updated_at": "2026-06-05T00:00:00Z",
                }
            ]

    class PolicyConnection:
        def __init__(self):
            self.calls = []

        async def execute(self, sql, params):
            self.calls.append((" ".join(sql.split()), params))
            return PolicyCursor()

    conn = PolicyConnection()

    rows = await repositories.list_admin_memory_policies(
        conn,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        agent_id="general-agent",
        limit=999,
    )

    sql, params = conn.calls[0]
    assert "from memory_policies" in sql
    assert "tenant_id = %s" in sql
    assert "workspace_id = %s" in sql
    assert "(%s::text is null or user_id = %s)" in sql
    assert "(%s::text is null or agent_id = %s)" in sql
    assert params == ("tenant-a", "workspace-a", "user-a", "user-a", "general-agent", "general-agent", 500)
    assert rows == [
        {
            "tenant_id": "tenant-a",
            "workspace_id": "workspace-a",
            "user_id": "user-a",
            "agent_id": "general-agent",
            "memory_enabled": False,
            "long_term_memory_enabled": False,
            "retention_days": 30,
            "redaction_mode": "strict",
            "source": "stored",
            "reason": "user opt-out",
            "updated_by": "user-a",
            "updated_at": "2026-06-05T00:00:00Z",
        }
    ]


@pytest.mark.asyncio
async def test_ensure_mcp_tool_active_applies_tenant_tool_policy_fail_closed():
    class ToolCursor:
        async def fetchone(self):
            return {
                "id": "ragflow-knowledge-search",
                "server_id": "ragflow",
                "registry_status": "active",
                "policy_status": "disabled",
                "registry_write_capable": False,
                "policy_write_capable": False,
                "registry_risk_level": "low",
                "policy_risk_level": "low",
                "registry_visible_to_user": True,
                "policy_visible_to_user": True,
            }

    class ToolConnection:
        def __init__(self):
            self.calls = []

        async def execute(self, sql, params):
            self.calls.append((" ".join(sql.split()), params))
            return ToolCursor()

    conn = ToolConnection()

    with pytest.raises(RepositoryConflictError, match="mcp_tool_disabled"):
        await repositories.ensure_mcp_tool_active(
            conn,
            tenant_id="tenant-a",
            tool_id="ragflow-knowledge-search",
        )

    sql, params = conn.calls[0]
    assert "left join tool_policies" in sql
    assert "tool_policies.tenant_id = %s" in sql
    assert params == ("tenant-a", "ragflow-knowledge-search")


@pytest.mark.asyncio
async def test_ensure_mcp_tool_active_requires_tenant_tool_policy_row():
    class ToolCursor:
        async def fetchone(self):
            return {
                "id": "ragflow-knowledge-search",
                "server_id": "ragflow",
                "registry_status": "active",
                "policy_status": None,
                "registry_write_capable": False,
                "policy_write_capable": None,
                "registry_risk_level": "low",
                "policy_risk_level": None,
                "registry_visible_to_user": True,
                "policy_visible_to_user": None,
            }

    class ToolConnection:
        async def execute(self, sql, params):
            return ToolCursor()

    with pytest.raises(RepositoryConflictError, match="mcp_tool_disabled"):
        await repositories.ensure_mcp_tool_active(
            ToolConnection(),
            tenant_id="tenant-a",
            tool_id="ragflow-knowledge-search",
        )


@pytest.mark.asyncio
async def test_ensure_mcp_tool_active_cannot_lower_registry_write_or_risk():
    class ToolCursor:
        async def fetchone(self):
            return {
                "id": "dangerous-writer",
                "server_id": "business",
                "registry_status": "active",
                "policy_status": "active",
                "registry_write_capable": True,
                "policy_write_capable": False,
                "registry_risk_level": "high",
                "policy_risk_level": "low",
                "registry_visible_to_user": True,
                "policy_visible_to_user": True,
            }

    class ToolConnection:
        async def execute(self, sql, params):
            return ToolCursor()

    row = await repositories.ensure_mcp_tool_active(
        ToolConnection(),
        tenant_id="tenant-a",
        tool_id="dangerous-writer",
    )

    assert row["id"] == "dangerous-writer"
    assert row["status"] == "active"
    assert row["write_capable"] is True
    assert row["risk_level"] == "high"
    assert row["visible_to_user"] is True


@pytest.mark.asyncio
async def test_list_admin_tool_policies_returns_missing_tenant_policy_as_disabled_inventory():
    class ToolPolicyCursor:
        async def fetchall(self):
            return [
                {
                    "tenant_id": "tenant-a",
                    "tool_id": "ragflow-knowledge-search",
                    "server_id": "ragflow",
                    "name": "RAGFlow",
                    "description": "Read-only search",
                    "registry_status": "active",
                    "policy_status": None,
                    "registry_write_capable": False,
                    "policy_write_capable": None,
                    "registry_risk_level": "low",
                    "policy_risk_level": None,
                    "registry_visible_to_user": True,
                    "policy_visible_to_user": None,
                    "reason": None,
                    "updated_by": None,
                    "updated_at": None,
                    "endpoint": "https://internal.example",
                    "auth_mode": "api-key",
                }
            ]

    class ToolPolicyConnection:
        def __init__(self):
            self.calls = []

        async def execute(self, sql, params):
            self.calls.append((" ".join(sql.split()), params))
            return ToolPolicyCursor()

    conn = ToolPolicyConnection()

    rows = await repositories.list_admin_tool_policies(
        conn,
        tenant_id="tenant-a",
        include_disabled=True,
        limit=999,
    )

    sql, params = conn.calls[0]
    assert "from mcp_tools" in sql
    assert "left join tool_policies" in sql
    assert "tool_policies.tenant_id = %s" in sql
    assert params == ("tenant-a", True, 500)
    assert "endpoint" not in rows[0]
    assert "auth_mode" not in rows[0]
    assert rows == [
        {
            "tenant_id": "tenant-a",
            "tool_id": "ragflow-knowledge-search",
            "id": "ragflow-knowledge-search",
            "server_id": "ragflow",
            "name": "RAGFlow",
            "description": "Read-only search",
            "registry_status": "active",
            "policy_status": "disabled",
            "effective_status": "disabled",
            "status": "disabled",
            "registry_write_capable": False,
            "policy_write_capable": False,
            "write_capable": False,
            "registry_risk_level": "low",
            "policy_risk_level": "low",
            "risk_level": "low",
            "registry_visible_to_user": True,
            "policy_visible_to_user": False,
            "visible_to_user": False,
            "source": "registry",
            "reason": "",
            "updated_by": None,
            "updated_at": None,
        }
    ]


@pytest.mark.asyncio
async def test_list_admin_tool_policies_filters_hidden_when_disabled_excluded():
    class ToolPolicyCursor:
        async def fetchall(self):
            return []

    class ToolPolicyConnection:
        def __init__(self):
            self.calls = []

        async def execute(self, sql, params):
            self.calls.append((" ".join(sql.split()), params))
            return ToolPolicyCursor()

    conn = ToolPolicyConnection()

    rows = await repositories.list_admin_tool_policies(
        conn,
        tenant_id="tenant-a",
        include_disabled=False,
        limit=50,
    )

    assert rows == []
    sql, params = conn.calls[0]
    assert "coalesce(mcp_tools.visible_to_user, false) = true" in sql
    assert "tool_policies.status = 'active'" in sql
    assert "tool_policies.visible_to_user = true" in sql
    assert params == ("tenant-a", False, 50)


@pytest.mark.asyncio
async def test_upsert_admin_tool_policy_writes_tenant_policy_and_returns_effective_row():
    class ToolPolicyCursor:
        async def fetchone(self):
            return {
                "tenant_id": "tenant-a",
                "tool_id": "ragflow-knowledge-search",
                "server_id": "ragflow",
                "name": "RAGFlow",
                "description": "Read-only search",
                "registry_status": "active",
                "policy_status": "active",
                "registry_write_capable": False,
                "policy_write_capable": True,
                "registry_risk_level": "low",
                "policy_risk_level": "high",
                "registry_visible_to_user": True,
                "policy_visible_to_user": True,
                "reason": "controlled write",
                "updated_by": "tool-admin",
                "updated_at": "2026-06-05T00:00:00Z",
            }

    class ToolPolicyConnection:
        def __init__(self):
            self.calls = []

        async def execute(self, sql, params):
            self.calls.append((" ".join(sql.split()), params))
            return ToolPolicyCursor()

    conn = ToolPolicyConnection()

    row = await repositories.upsert_admin_tool_policy(
        conn,
        tenant_id="tenant-a",
        tool_id="ragflow-knowledge-search",
        status="active",
        risk_level="high",
        write_capable=True,
        visible_to_user=True,
        reason="controlled write",
        updated_by="tool-admin",
    )

    sql, params = conn.calls[0]
    assert "insert into tool_policies" in sql
    assert "on conflict (tenant_id, tool_id) do update" in sql
    assert params == (
        "tenant-a",
        "active",
        True,
        "high",
        True,
        "controlled write",
        "tool-admin",
        "ragflow-knowledge-search",
    )
    assert row["source"] == "tenant"
    assert row["effective_status"] == "active"
    assert row["write_capable"] is True
    assert row["risk_level"] == "high"


@pytest.mark.asyncio
async def test_upsert_admin_tool_policy_raises_for_missing_tool():
    class MissingCursor:
        async def fetchone(self):
            return None

    class MissingConnection:
        async def execute(self, sql, params):
            return MissingCursor()

    with pytest.raises(RepositoryNotFoundError, match="mcp_tool_not_found"):
        await repositories.upsert_admin_tool_policy(
            MissingConnection(),
            tenant_id="tenant-a",
            tool_id="missing-tool",
            status="disabled",
            risk_level="low",
            write_capable=False,
            visible_to_user=False,
            reason="missing",
            updated_by="tool-admin",
        )


@pytest.mark.asyncio
async def test_list_mcp_server_registry_filters_by_tenant_department_and_redacts_private_fields():
    class RegistryCursor:
        async def fetchall(self):
            return [
                {
                    "tenant_id": "tenant-a",
                    "name": "qa-mcp",
                    "transport": "streamable_http",
                    "endpoint_redacted": "https://mcp.example/sse",
                    "status": "active",
                    "is_system": False,
                    "allowed_roles": ["qa"],
                    "role_quotas_json": {"qa": {"daily_limit": 3}},
                    "department_ids": ["qa"],
                    "credential_state": "configured",
                    "credential_metadata_json": {"header_names": ["Authorization"]},
                    "credential_fingerprint": "secret-fingerprint",
                    "created_at": "2026-06-23T00:00:00Z",
                    "updated_at": "2026-06-23T00:00:00Z",
                }
            ]

    class RegistryConnection:
        def __init__(self):
            self.calls = []

        async def execute(self, sql, params):
            self.calls.append((" ".join(sql.split()), params))
            return RegistryCursor()

    conn = RegistryConnection()

    rows = await repositories.list_mcp_server_registry(
        conn,
        tenant_id="tenant-a",
        department_id="qa",
        include_disabled=False,
    )

    sql, params = conn.calls[0]
    assert "from mcp_servers" in sql
    assert "tenant_id = %s" in sql
    assert "%s = any(department_ids)" in sql
    assert "credential_fingerprint" not in rows[0]
    assert "credential_metadata_json" not in rows[0]
    assert rows == [
        {
            "tenant_id": "tenant-a",
            "name": "qa-mcp",
            "transport": "streamable_http",
            "endpoint_redacted": "https://mcp.example/sse",
            "status": "active",
            "is_system": False,
            "allowed_roles": ["qa"],
            "role_quotas": {"qa": {"daily_limit": 3}},
            "department_ids": ["qa"],
            "credential_state": "configured",
            "credential_metadata": {"header_names": ["Authorization"]},
            "created_at": "2026-06-23T00:00:00Z",
            "updated_at": "2026-06-23T00:00:00Z",
        }
    ]
    assert params == ("tenant-a", "qa", False)


@pytest.mark.asyncio
async def test_upsert_mcp_server_registry_persists_only_redacted_endpoint_and_credential_fingerprint():
    class RegistryCursor:
        async def fetchone(self):
            return {
                "tenant_id": "tenant-a",
                "name": "qa-mcp",
                "transport": "streamable_http",
                "endpoint_redacted": "https://mcp.example/sse",
                "status": "active",
                "is_system": False,
                "allowed_roles": ["qa"],
                "role_quotas_json": {},
                "department_ids": ["qa"],
                "credential_state": "configured",
                "credential_metadata_json": {"header_names": ["Authorization"]},
                "created_at": "2026-06-23T00:00:00Z",
                "updated_at": "2026-06-23T00:00:00Z",
            }

    class RegistryConnection:
        def __init__(self):
            self.calls = []

        async def execute(self, sql, params):
            self.calls.append((" ".join(sql.split()), params))
            return RegistryCursor()

    conn = RegistryConnection()

    row = await repositories.upsert_mcp_server_registry(
        conn,
        tenant_id="tenant-a",
        name="qa-mcp",
        transport="streamable_http",
        enabled=True,
        is_system=False,
        endpoint_redacted="https://mcp.example/sse",
        allowed_roles=["qa"],
        role_quotas={},
        department_ids=["qa"],
        credential_state="configured",
        credential_metadata={"header_names": ["Authorization"]},
        credential_fingerprint="credential-sha",
        updated_by="admin-a",
    )

    sql, params = conn.calls[0]
    assert "insert into mcp_servers" in sql
    assert "existing.is_system <> %s" in sql
    assert "where mcp_servers.is_system = excluded.is_system returning *" in sql
    assert "credential_fingerprint" in sql
    assert "credential-sha" in params
    assert params[:3] == ("tenant-a", "qa-mcp", False)
    assert "raw-secret" not in str(params)
    assert row["name"] == "qa-mcp"
    assert row["credential_state"] == "configured"
    assert "credential_fingerprint" not in row


@pytest.mark.asyncio
async def test_list_mcp_server_registry_names_excludes_deleted_registry_overrides():
    class RegistryNamesCursor:
        async def fetchall(self):
            return [{"name": "ragflow"}, {"name": "custom"}]

    class RegistryNamesConnection:
        def __init__(self):
            self.calls = []

        async def execute(self, sql, params):
            self.calls.append((" ".join(sql.split()), params))
            return RegistryNamesCursor()

    conn = RegistryNamesConnection()

    names = await repositories.list_mcp_server_registry_names(conn, tenant_id="tenant-a")

    assert names == ["ragflow", "custom"]
    sql, params = conn.calls[0]
    assert "status <> 'deleted'" in sql
    assert params == ("tenant-a",)


@pytest.mark.asyncio
async def test_get_mcp_tool_registry_entry_scopes_tool_through_parent_server_tenant():
    class RegistryCursor:
        async def fetchone(self):
            return {
                "tool_id": "qa-search",
                "server_id": "qa-mcp",
                "name": "QA Search",
                "description": "Search QA records.",
                "registry_status": "active",
                "server_status": "active",
                "registry_write_capable": False,
                "registry_risk_level": "low",
                "registry_visible_to_user": True,
                "policy_status": "active",
                "policy_write_capable": False,
                "policy_risk_level": "low",
                "policy_visible_to_user": True,
            }

    class RegistryConnection:
        def __init__(self):
            self.calls = []

        async def execute(self, sql, params):
            self.calls.append((" ".join(sql.split()), params))
            return RegistryCursor()

    conn = RegistryConnection()

    row = await repositories.get_mcp_tool_registry_entry(
        conn,
        tenant_id="tenant-a",
        tool_id="qa-search",
    )

    sql, params = conn.calls[0]
    assert "join mcp_servers" in sql
    assert "mcp_servers.tenant_id = %s" in sql
    assert "mcp_servers.name = mcp_tools.server_id" in sql
    assert "mcp_tools.id = %s" in sql
    assert sql.count("%s") == len(params)
    assert params == ("tenant-a", "qa-search")
    assert row is not None
    assert {
        key: row[key]
        for key in (
            "tool_id",
            "server_id",
            "name",
            "description",
            "registry_status",
            "server_status",
            "write_capable",
            "risk_level",
            "visible_to_user",
            "effective_status",
            "source",
        )
    } == {
        "tool_id": "qa-search",
        "server_id": "qa-mcp",
        "name": "QA Search",
        "description": "Search QA records.",
        "registry_status": "active",
        "server_status": "active",
        "write_capable": False,
        "risk_level": "low",
        "visible_to_user": True,
        "effective_status": "active",
        "source": "tenant",
    }


@pytest.mark.asyncio
async def test_record_mcp_server_credential_keeps_hash_not_secret_material():
    class CredentialConnection:
        def __init__(self):
            self.calls = []

        async def execute(self, sql, params):
            self.calls.append((" ".join(sql.split()), params))
            return FakeCursor()

    conn = CredentialConnection()

    await repositories.record_mcp_server_credential(
        conn,
        tenant_id="tenant-a",
        server_name="qa-mcp",
        credential_fingerprint="credential-sha",
        metadata={"header_names": ["Authorization"]},
        updated_by="admin-a",
    )

    sql, params = conn.calls[0]
    assert "insert into mcp_server_credentials" in sql
    assert "credential_fingerprint" in sql
    assert params == (
        "tenant-a",
        "qa-mcp",
        "credential-sha",
        json.dumps({"header_names": ["Authorization"]}, ensure_ascii=False),
        "admin-a",
    )
    assert "raw-secret" not in str(params)


@pytest.mark.asyncio
async def test_create_memory_record_sets_expires_at_from_retention_days():
    class MemoryCursor:
        async def fetchone(self):
            return {
                "id": "mem-retention",
                "tenant_id": "tenant-a",
                "workspace_id": "workspace-a",
                "user_id": "user-a",
                "agent_id": "general-agent",
                "session_id": "session-a",
                "record_type": "session_summary",
                "content": "Retain for policy duration.",
                "metadata_json": {"source": "test"},
                "status": "active",
                "expires_at": "2026-07-03T12:00:00Z",
                "deleted_at": None,
                "created_at": "2026-06-03T12:00:00Z",
                "updated_at": "2026-06-03T12:00:00Z",
            }

    class MemoryConnection:
        def __init__(self):
            self.calls = []

        async def execute(self, sql, params):
            self.calls.append((" ".join(sql.split()), params))
            return MemoryCursor()

    conn = MemoryConnection()

    row = await repositories.create_memory_record(
        conn,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        agent_id="general-agent",
        session_id="session-a",
        record_type="session_summary",
        content="Retain for policy duration.",
        metadata_json={"source": "test"},
        retention_days=30,
    )

    sql, params = conn.calls[0]
    assert "insert into memory_records" in sql
    assert "expires_at" in sql
    assert "now() + (%s * interval '1 day')" in sql
    assert params[9] == 30
    assert row["expires_at"] == "2026-07-03T12:00:00Z"
    assert row["status"] == "active"


@pytest.mark.asyncio
async def test_create_memory_record_redacts_secret_like_content_and_metadata_before_insert():
    class MemoryCursor:
        async def fetchone(self):
            return {
                "id": "mem-redacted",
                "tenant_id": "tenant-a",
                "workspace_id": "workspace-a",
                "user_id": "user-a",
                "agent_id": "general-agent",
                "session_id": "session-a",
                "record_type": "session_summary",
                "content": "safe",
                "metadata_json": {"source": "test"},
                "status": "active",
                "expires_at": "2026-07-03T12:00:00Z",
                "deleted_at": None,
                "created_at": "2026-06-03T12:00:00Z",
                "updated_at": "2026-06-03T12:00:00Z",
            }

    class MemoryConnection:
        def __init__(self):
            self.calls = []

        async def execute(self, sql, params):
            self.calls.append((" ".join(sql.split()), params))
            return MemoryCursor()

    conn = MemoryConnection()

    await repositories.create_memory_record(
        conn,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        agent_id="general-agent",
        session_id="session-a",
        record_type="session_summary",
        content=(
            "User api_key=sk-live-123 password: hidden-password email alice@example.com "
            "authorization: Bearer sk-live-bearer {\"api_key\":\"sk-live-json\"} "
            "client_secret=client-secret-text openai_api_key=sk-openai-text id_token=id-token-text "
            "{\"client_secret\":\"client-secret-json\",\"openai_api_key\":\"sk-openai-json\",\"id_token\":\"id-token-json\"}"
        ),
        metadata_json={
            "source": "test",
            "api_key": "sk-live-456",
            "client_secret": "client-secret-value",
            "openai_api_key": "sk-openai-value",
            "id_token": "id-token-value",
            "nested": {
                "token": "hidden-token",
                "note": "password=hidden-password-2 authorization: Bearer nested-bearer-token",
                "json": (
                    "{\"api_key\":\"sk-live-json-meta\",\"client_secret\":\"client-secret-json-meta\","
                    "\"openai_api_key\":\"sk-openai-json-meta\",\"id_token\":\"id-token-json-meta\"}"
                ),
            },
        },
        retention_days=30,
    )

    _, params = conn.calls[0]
    inserted_content = params[7]
    inserted_metadata = json.loads(params[8])
    serialized = f"{inserted_content} {inserted_metadata}"
    assert "sk-live-123" not in serialized
    assert "sk-live-456" not in serialized
    assert "sk-live-bearer" not in serialized
    assert "sk-live-json" not in serialized
    assert "client-secret-value" not in serialized
    assert "sk-openai-value" not in serialized
    assert "id-token-value" not in serialized
    assert "client-secret-text" not in serialized
    assert "sk-openai-text" not in serialized
    assert "id-token-text" not in serialized
    assert "client-secret-json" not in serialized
    assert "sk-openai-json" not in serialized
    assert "id-token-json" not in serialized
    assert "hidden-password" not in serialized
    assert "hidden-token" not in serialized
    assert "nested-bearer-token" not in serialized
    assert "sk-live-json-meta" not in serialized
    assert "client-secret-json-meta" not in serialized
    assert "sk-openai-json-meta" not in serialized
    assert "id-token-json-meta" not in serialized
    assert "alice@example.com" not in serialized
    assert "authorization=[redacted-secret] [redacted-secret]" not in serialized
    assert "[redacted-secret]" in serialized
    assert "[redacted-email]" in serialized
    assert inserted_metadata["source"] == "test"
    assert inserted_metadata["client_secret"] == "[redacted-secret]"
    assert inserted_metadata["openai_api_key"] == "[redacted-secret]"
    assert inserted_metadata["id_token"] == "[redacted-secret]"


@pytest.mark.asyncio
async def test_create_memory_record_strict_mode_redacts_raw_provider_and_jwt_tokens_before_insert():
    raw_openai = "sk-strict1234567890abcdef"
    raw_github = "ghp_strict1234567890abcdef"
    raw_jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJzdHJpY3QifQ.signature1234567890"

    class MemoryCursor:
        async def fetchone(self):
            return {
                "id": "mem-strict-redacted",
                "tenant_id": "tenant-a",
                "workspace_id": "workspace-a",
                "user_id": "user-a",
                "agent_id": "general-agent",
                "session_id": "session-a",
                "record_type": "session_summary",
                "content": "safe",
                "metadata_json": {"source": "test"},
                "status": "active",
                "expires_at": "2026-07-03T12:00:00Z",
                "deleted_at": None,
                "created_at": "2026-06-03T12:00:00Z",
                "updated_at": "2026-06-03T12:00:00Z",
            }

    class MemoryConnection:
        def __init__(self):
            self.calls = []

        async def execute(self, sql, params):
            self.calls.append((" ".join(sql.split()), params))
            return MemoryCursor()

    conn = MemoryConnection()

    await repositories.create_memory_record(
        conn,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        agent_id="general-agent",
        session_id="session-a",
        record_type="session_summary",
        content=f"raw provider markers {raw_openai} {raw_github} {raw_jwt}",
        metadata_json={
            "source": "test",
            "note": f"raw provider markers {raw_openai} {raw_github}",
            "nested": {"jwt": raw_jwt},
        },
        retention_days=30,
        redaction_mode="strict",
    )

    _, params = conn.calls[0]
    inserted_content = params[7]
    inserted_metadata = json.loads(params[8])
    serialized = f"{inserted_content} {inserted_metadata}"
    assert raw_openai not in serialized
    assert raw_github not in serialized
    assert raw_jwt not in serialized
    assert serialized.count("[redacted-secret]") >= 3


@pytest.mark.asyncio
async def test_create_memory_record_rejects_missing_session_id_before_insert():
    class MemoryConnection:
        def __init__(self):
            self.calls = []

        async def execute(self, sql, params):
            self.calls.append((" ".join(sql.split()), params))
            raise AssertionError("memory write must fail before SQL when session_id is missing")

    conn = MemoryConnection()

    with pytest.raises(RepositoryConflictError, match="memory_session_id_required"):
        await repositories.create_memory_record(
            conn,
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            user_id="user-a",
            agent_id="general-agent",
            session_id=None,
            record_type="session_summary",
            content="unsafe cross-session memory",
            metadata_json={},
        )

    assert conn.calls == []


@pytest.mark.asyncio
async def test_list_memory_records_rejects_missing_session_id_before_query():
    class MemoryConnection:
        def __init__(self):
            self.calls = []

        async def execute(self, sql, params):
            self.calls.append((" ".join(sql.split()), params))
            raise AssertionError("memory list must fail before SQL when session_id is missing")

    conn = MemoryConnection()

    with pytest.raises(RepositoryConflictError, match="memory_session_id_required"):
        await repositories.list_memory_records(
            conn,
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            user_id="user-a",
            agent_id="general-agent",
            session_id=None,
        )

    assert conn.calls == []


@pytest.mark.asyncio
async def test_list_memory_records_exports_only_active_unexpired_session_memory():
    class MemoryCursor:
        async def fetchall(self):
            return [
                {
                    "id": "mem-active",
                    "tenant_id": "tenant-a",
                    "workspace_id": "workspace-a",
                    "user_id": "user-a",
                    "agent_id": "general-agent",
                    "session_id": "session-a",
                    "record_type": "session_summary",
                    "content": "safe summary",
                    "metadata_json": {"source": "test"},
                    "status": "active",
                    "expires_at": "2026-07-03T12:00:00Z",
                    "deleted_at": None,
                    "created_at": "2026-06-03T12:00:00Z",
                    "updated_at": "2026-06-03T12:00:00Z",
                }
            ]

    class MemoryConnection:
        def __init__(self):
            self.calls = []

        async def execute(self, sql, params):
            normalized = " ".join(sql.split()).lower()
            self.calls.append((normalized, params))
            return MemoryCursor()

    conn = MemoryConnection()

    rows = await repositories.list_memory_records(
        conn,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        agent_id="general-agent",
        session_id="session-a",
        limit=50,
    )

    sql, params = conn.calls[0]
    assert "from memory_records" in sql
    assert "tenant_id = %s" in sql
    assert "workspace_id = %s" in sql
    assert "user_id = %s" in sql
    assert "status = 'active'" in sql
    assert "deleted_at is null" in sql
    assert "expires_at is null or expires_at > now()" in sql
    assert "session_id = %s" in sql
    assert params == (
        "tenant-a",
        "workspace-a",
        "user-a",
        "general-agent",
        "general-agent",
        "session-a",
        "session-a",
        50,
    )
    assert rows[0]["id"] == "mem-active"


@pytest.mark.asyncio
async def test_list_admin_memory_records_operator_export_does_not_select_content_or_metadata():
    class MemoryCursor:
        async def fetchall(self):
            return [
                {
                    "id": "mem-active",
                    "tenant_id": "tenant-a",
                    "workspace_id": "workspace-a",
                    "user_id": "user-a",
                    "agent_id": "general-agent",
                    "session_id": "session-a",
                    "record_type": "session_summary",
                    "status": "active",
                    "expires_at": "2026-07-03T12:00:00Z",
                    "deleted_at": None,
                    "created_at": "2026-06-03T12:00:00Z",
                    "updated_at": "2026-06-03T12:00:00Z",
                }
            ]

    class MemoryConnection:
        def __init__(self):
            self.calls = []

        async def execute(self, sql, params):
            normalized = " ".join(sql.split()).lower()
            self.calls.append((normalized, params))
            return MemoryCursor()

    conn = MemoryConnection()

    rows = await repositories.list_admin_memory_records(
        conn,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        status="active",
        limit=25,
    )

    sql, params = conn.calls[0]
    selected = sql.split(" from memory_records", 1)[0]
    assert "content" not in selected
    assert "metadata_json" not in selected
    assert "tenant_id = %s" in sql
    assert "workspace_id = %s" in sql
    assert "user_id = %s" in sql
    assert "%s = 'all' or status = %s" in sql
    assert params == ("tenant-a", "workspace-a", "user-a", "user-a", "active", "active", 25)
    assert "content" not in rows[0]
    assert "metadata_json" not in rows[0]


@pytest.mark.asyncio
async def test_create_memory_record_rejects_missing_agent_id_before_insert():
    class MemoryConnection:
        def __init__(self):
            self.calls = []

        async def execute(self, sql, params):
            self.calls.append((" ".join(sql.split()), params))
            raise AssertionError("memory write must fail before SQL when agent_id is missing")

    conn = MemoryConnection()

    with pytest.raises(RepositoryConflictError, match="memory_agent_id_required"):
        await repositories.create_memory_record(
            conn,
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            user_id="user-a",
            agent_id=None,
            session_id="session-a",
            record_type="session_summary",
            content="unsafe cross-agent memory",
            metadata_json={},
        )

    assert conn.calls == []


@pytest.mark.asyncio
async def test_create_memory_record_rejects_session_scope_mismatch_before_insert_returns():
    class EmptyCursor:
        async def fetchone(self):
            return None

    class MemoryConnection:
        def __init__(self):
            self.calls = []

        async def execute(self, sql, params):
            self.calls.append((" ".join(sql.split()), params))
            return EmptyCursor()

    conn = MemoryConnection()

    with pytest.raises(RepositoryNotFoundError, match="session_not_found"):
        await repositories.create_memory_record(
            conn,
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            user_id="user-a",
            agent_id="general-agent",
            session_id="session-cross-scope",
            record_type="session_summary",
            content="unsafe cross-session memory",
            metadata_json={},
        )

    sql, params = conn.calls[0]
    assert "from sessions" in sql
    assert "sessions.tenant_id = %s" in sql
    assert "sessions.workspace_id = %s" in sql
    assert "sessions.user_id = %s" in sql
    assert "sessions.id = %s" in sql
    assert "sessions.agent_id = %s" in sql
    assert "session-cross-scope" in params


@pytest.mark.asyncio
async def test_get_user_scopes_by_tenant_and_user_id():
    class UserCursor:
        async def fetchone(self):
            return {"id": "user-a", "tenant_id": "tenant-a", "display_name": "User A", "status": "active"}

    class UserConnection:
        def __init__(self):
            self.calls = []

        async def execute(self, sql, params):
            self.calls.append((" ".join(sql.split()), params))
            return UserCursor()

    conn = UserConnection()

    user = await repositories.get_user(conn, tenant_id="tenant-a", user_id="user-a")

    sql, params = conn.calls[0]
    assert "from users" in sql
    assert "tenant_id = %s" in sql
    assert "id = %s" in sql
    assert params == ("tenant-a", "user-a")
    assert user["id"] == "user-a"


@pytest.mark.asyncio
async def test_get_agent_scopes_by_tenant_and_agent_id():
    class AgentCursor:
        async def fetchone(self):
            return {"id": "general-agent", "tenant_id": "tenant-a", "status": "active"}

    class AgentConnection:
        def __init__(self):
            self.calls = []

        async def execute(self, sql, params):
            self.calls.append((" ".join(sql.split()), params))
            return AgentCursor()

    conn = AgentConnection()

    agent = await repositories.get_agent(conn, tenant_id="tenant-a", agent_id="general-agent")

    sql, params = conn.calls[0]
    assert "from agents" in sql
    assert "tenant_id = %s" in sql
    assert "id = %s" in sql
    assert params == ("tenant-a", "general-agent")
    assert agent["id"] == "general-agent"


@pytest.mark.asyncio
async def test_delete_memory_record_soft_deletes_with_user_workspace_session_scope():
    class MemoryCursor:
        async def fetchone(self):
            return {
                "id": "mem-a",
                "tenant_id": "tenant-a",
                "workspace_id": "workspace-a",
                "user_id": "user-a",
                "agent_id": "general-agent",
                "session_id": "session-a",
                "record_type": "session_summary",
                "content": "User prefers concise answers.",
                "metadata_json": {},
                "status": "deleted",
                "expires_at": None,
                "deleted_at": "2026-06-02T12:00:00Z",
                "created_at": "2026-06-02T11:00:00Z",
                "updated_at": "2026-06-02T12:00:00Z",
            }

    class MemoryConnection:
        def __init__(self):
            self.calls = []

        async def execute(self, sql, params):
            self.calls.append((" ".join(sql.split()), params))
            return MemoryCursor()

    conn = MemoryConnection()

    row = await delete_memory_record(
        conn,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        agent_id="general-agent",
        session_id="session-a",
        record_id="mem-a",
    )

    sql, params = conn.calls[0]
    assert row["status"] == "deleted"
    assert "update memory_records" in sql
    assert "status = 'deleted'" in sql
    assert "deleted_at = now()" in sql
    assert "tenant_id = %s" in sql
    assert "workspace_id = %s" in sql
    assert "user_id = %s" in sql
    assert "agent_id = %s" in sql
    assert "session_id = %s" in sql
    assert "status = 'active'" in sql
    assert "deleted_at is null" in sql
    assert "content" not in sql
    assert "metadata_json" not in sql
    assert params == ("tenant-a", "workspace-a", "user-a", "general-agent", "session-a", "mem-a")


@pytest.mark.asyncio
async def test_admin_delete_memory_record_soft_deletes_with_tenant_workspace_scope():
    class MemoryCursor:
        async def fetchone(self):
            return {
                "id": "mem-b",
                "tenant_id": "tenant-a",
                "workspace_id": "workspace-a",
                "user_id": "user-b",
                "agent_id": "general-agent",
                "session_id": "session-b",
                "record_type": "user_preference",
                "content": "Use short answers.",
                "metadata_json": {},
                "status": "deleted",
                "expires_at": None,
                "deleted_at": "2026-06-02T12:00:00Z",
                "created_at": "2026-06-02T11:00:00Z",
                "updated_at": "2026-06-02T12:00:00Z",
            }

    class MemoryConnection:
        def __init__(self):
            self.calls = []

        async def execute(self, sql, params):
            self.calls.append((" ".join(sql.split()), params))
            return MemoryCursor()

    conn = MemoryConnection()

    row = await admin_delete_memory_record(
        conn,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        record_id="mem-b",
    )

    sql, params = conn.calls[0]
    assert row["user_id"] == "user-b"
    assert row["status"] == "deleted"
    assert "update memory_records" in sql
    assert "tenant_id = %s" in sql
    assert "workspace_id = %s" in sql
    assert "user_id = %s" not in sql
    assert "status = 'active'" in sql
    assert "content" not in sql
    assert "metadata_json" not in sql
    assert params == ("tenant-a", "workspace-a", "mem-b")


@pytest.mark.asyncio
async def test_cleanup_expired_memory_records_soft_deletes_only_expired_active_rows():
    class CleanupCursor:
        async def fetchall(self):
            return [
                {
                    "id": "mem-expired",
                    "tenant_id": "tenant-a",
                    "workspace_id": "workspace-a",
                    "user_id": "user-a",
                    "agent_id": "general-agent",
                    "session_id": "session-a",
                    "record_type": "session_summary",
                    "status": "deleted",
                    "expires_at": "2026-06-01T12:00:00Z",
                    "deleted_at": "2026-06-03T12:00:00Z",
                    "created_at": "2026-05-31T12:00:00Z",
                    "updated_at": "2026-06-03T12:00:00Z",
                }
            ]

    class MemoryConnection:
        def __init__(self):
            self.calls = []

        async def execute(self, sql, params):
            self.calls.append((" ".join(sql.split()), params))
            return CleanupCursor()

    conn = MemoryConnection()

    rows = await repositories.cleanup_expired_memory_records(
        conn,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        limit=25,
    )

    sql, params = conn.calls[0]
    assert rows[0]["id"] == "mem-expired"
    assert rows[0]["status"] == "deleted"
    assert "update memory_records" in sql
    assert "status = 'deleted'" in sql
    assert "expires_at is not null" in sql
    assert "expires_at <= now()" in sql
    assert "deleted_at is null" in sql
    assert "workspace_id = %s" in sql
    assert "%s::text is null" not in sql
    assert "for update skip locked" in sql
    assert "content" not in sql
    assert "metadata_json" not in sql
    assert params == ("tenant-a", "workspace-a", 25)


@pytest.mark.asyncio
async def test_cleanup_expired_memory_records_across_scopes_rejects_non_positive_limit():
    class MemoryConnection:
        async def execute(self, *_args, **_kwargs):
            raise AssertionError("invalid limit must fail before SQL execution")

    with pytest.raises(repositories.RepositoryConflictError, match="memory_cleanup_limit_invalid"):
        await repositories.cleanup_expired_memory_records_across_scopes(MemoryConnection(), limit=0)


@pytest.mark.asyncio
async def test_cleanup_expired_memory_records_across_scopes_prioritizes_one_row_per_scope():
    class EmptyCursor:
        async def fetchone(self):
            return None

    class ScopeCursor:
        async def fetchall(self):
            return [
                {"tenant_id": "tenant-a", "workspace_id": "workspace-a"},
                {"tenant_id": "tenant-b", "workspace_id": "workspace-b"},
                {"tenant_id": "tenant-c", "workspace_id": "workspace-c"},
                {"tenant_id": "tenant-d", "workspace_id": "workspace-d"},
            ]

    class CleanupCursor:
        async def fetchall(self):
            return []

    class MemoryConnection:
        def __init__(self):
            self.calls = []

        async def execute(self, sql, params):
            normalized = " ".join(sql.split())
            self.calls.append((normalized, params))
            if "from worker_maintenance_cursors" in normalized:
                return EmptyCursor()
            if "group by tenant_id, workspace_id" in normalized:
                return ScopeCursor()
            return CleanupCursor()

    conn = MemoryConnection()

    await repositories.cleanup_expired_memory_records_across_scopes(conn, limit=5)

    cleanup_sql, cleanup_params = conn.calls[3]
    assert "row_number() over" in cleanup_sql
    assert "order by locked_rows.expires_at asc, locked_rows.created_at asc, locked_rows.id asc" in cleanup_sql
    assert "case when selected.scope_rank = 1 then 0 else 1 end" in cleanup_sql
    assert cleanup_params == (
        ["tenant-a", "tenant-b", "tenant-c", "tenant-d"],
        ["workspace-a", "workspace-b", "workspace-c", "workspace-d"],
        2,
        5,
    )


@pytest.mark.asyncio
async def test_cleanup_expired_memory_records_across_scopes_uses_bounded_scope_cursor():
    class CursorCursor:
        async def fetchone(self):
            return {"tenant_id": "tenant-a", "workspace_id": "workspace-a"}

    class ScopeCursor:
        async def fetchall(self):
            return [
                {"tenant_id": "tenant-b", "workspace_id": "workspace-b"},
                {"tenant_id": "tenant-c", "workspace_id": "workspace-c"},
            ]

    class UpdateCursor:
        async def fetchall(self):
            return [
                {
                    "id": "mem-b",
                    "tenant_id": "tenant-b",
                    "workspace_id": "workspace-b",
                    "user_id": "user-b",
                    "agent_id": "general-agent",
                    "session_id": "session-b",
                    "record_type": "session_summary",
                    "status": "deleted",
                    "expires_at": "2026-06-01T12:00:00Z",
                    "deleted_at": "2026-06-03T12:00:00Z",
                    "created_at": "2026-05-31T12:00:00Z",
                    "updated_at": "2026-06-03T12:00:00Z",
                },
                {
                    "id": "mem-c",
                    "tenant_id": "tenant-c",
                    "workspace_id": "workspace-c",
                    "user_id": "user-c",
                    "agent_id": "general-agent",
                    "session_id": "session-c",
                    "record_type": "session_summary",
                    "status": "deleted",
                    "expires_at": "2026-06-01T12:05:00Z",
                    "deleted_at": "2026-06-03T12:00:00Z",
                    "created_at": "2026-05-31T12:05:00Z",
                    "updated_at": "2026-06-03T12:00:00Z",
                },
            ]

    class MemoryConnection:
        def __init__(self):
            self.calls = []

        async def execute(self, sql, params):
            normalized = " ".join(sql.split())
            self.calls.append((normalized, params))
            if "from worker_maintenance_cursors" in normalized:
                return CursorCursor()
            if "group by tenant_id, workspace_id" in normalized:
                return ScopeCursor()
            return UpdateCursor()

    conn = MemoryConnection()

    rows = await repositories.cleanup_expired_memory_records_across_scopes(conn, limit=2)

    assert [row["tenant_id"] for row in rows] == ["tenant-b", "tenant-c"]
    cursor_sql, cursor_params = conn.calls[0]
    scope_sql, scope_params = conn.calls[1]
    cursor_update_sql, cursor_update_params = conn.calls[2]
    cleanup_sql, cleanup_params = conn.calls[3]
    assert "from worker_maintenance_cursors" in cursor_sql
    assert "for update" in cursor_sql
    assert cursor_params == ("memory_retention_cleanup",)
    assert "group by tenant_id, workspace_id" in scope_sql
    assert "(tenant_id, workspace_id) > (%s, %s)" in scope_sql
    assert scope_params == ("tenant-a", "tenant-a", "workspace-a", None, None, None, 2)
    assert "insert into worker_maintenance_cursors" in cursor_update_sql
    assert "on conflict (cursor_key) do update" in cursor_update_sql
    assert cursor_update_params == ("memory_retention_cleanup", "tenant-c", "workspace-c")
    assert "unnest(%s::text[], %s::text[])" in cleanup_sql
    assert "cross join lateral" in cleanup_sql
    assert "memory_records.tenant_id = scope.tenant_id" in cleanup_sql
    assert "memory_records.workspace_id = scope.workspace_id" in cleanup_sql
    assert "for update skip locked" in cleanup_sql
    assert "content" not in cleanup_sql
    assert "metadata_json" not in cleanup_sql
    assert cleanup_params == (["tenant-b", "tenant-c"], ["workspace-b", "workspace-c"], 1, 2)


@pytest.mark.asyncio
async def test_cleanup_expired_memory_records_across_scopes_soft_deletes_bounded_rows():
    class EmptyCursor:
        async def fetchone(self):
            return None

    class ScopeCursor:
        async def fetchall(self):
            return [{"tenant_id": "tenant-a", "workspace_id": "workspace-a"}]

    class CleanupCursor:
        async def fetchall(self):
            return [
                {
                    "id": "mem-expired",
                    "tenant_id": "tenant-a",
                    "workspace_id": "workspace-a",
                    "user_id": "user-a",
                    "agent_id": "general-agent",
                    "session_id": "session-a",
                    "record_type": "session_summary",
                    "status": "deleted",
                    "expires_at": "2026-06-01T12:00:00Z",
                    "deleted_at": "2026-06-03T12:00:00Z",
                    "created_at": "2026-05-31T12:00:00Z",
                    "updated_at": "2026-06-03T12:00:00Z",
                }
            ]

    class MemoryConnection:
        def __init__(self):
            self.calls = []

        async def execute(self, sql, params):
            normalized = " ".join(sql.split())
            self.calls.append((normalized, params))
            if "from worker_maintenance_cursors" in normalized:
                return EmptyCursor()
            if "group by tenant_id, workspace_id" in normalized:
                return ScopeCursor()
            return CleanupCursor()

    conn = MemoryConnection()

    rows = await repositories.cleanup_expired_memory_records_across_scopes(conn, limit=25)

    sql, params = conn.calls[3]
    assert rows[0]["tenant_id"] == "tenant-a"
    assert rows[0]["workspace_id"] == "workspace-a"
    assert "update memory_records" in sql
    assert "status = 'deleted'" in sql
    assert "expires_at is not null" in sql
    assert "expires_at <= now()" in sql
    assert "deleted_at is null" in sql
    assert "tenant_id = %s" not in sql
    assert "workspace_id = %s" not in sql
    assert "cross join lateral" in sql
    assert "case when selected.scope_rank = 1 then 0 else 1 end" in sql
    assert "selected.expires_at asc, selected.created_at asc" in sql
    assert "for update skip locked" in sql
    assert "content" not in sql
    assert "metadata_json" not in sql
    assert params == (["tenant-a"], ["workspace-a"], 25, 25)


@pytest.mark.asyncio
async def test_list_admin_memory_records_projects_operator_fields_without_content_or_metadata():
    class MemoryCursor:
        async def fetchall(self):
            return [
                {
                    "id": "mem-ops",
                    "tenant_id": "tenant-a",
                    "workspace_id": "workspace-a",
                    "user_id": "user-b",
                    "agent_id": "general-agent",
                    "session_id": "session-b",
                    "record_type": "session_summary",
                    "status": "active",
                    "expires_at": "2026-07-03T12:00:00Z",
                    "deleted_at": None,
                    "created_at": "2026-06-03T12:00:00Z",
                    "updated_at": "2026-06-03T12:30:00Z",
                }
            ]

    class MemoryConnection:
        def __init__(self):
            self.calls = []

        async def execute(self, sql, params):
            self.calls.append((" ".join(sql.split()), params))
            return MemoryCursor()

    conn = MemoryConnection()

    rows = await repositories.list_admin_memory_records(
        conn,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-b",
        status="active",
        limit=25,
    )

    sql, params = conn.calls[0]
    selected_columns = sql.lower().split("from memory_records", 1)[0]
    assert "content" not in selected_columns
    assert "metadata" not in selected_columns
    assert "where tenant_id = %s" in sql
    assert "and workspace_id = %s" in sql
    assert "(%s::text is null or user_id = %s)" in sql
    assert "(%s = 'all' or status = %s)" in sql
    assert params == ("tenant-a", "workspace-a", "user-b", "user-b", "active", "active", 25)
    assert rows[0]["id"] == "mem-ops"


@pytest.mark.asyncio
async def test_create_tool_permission_request_persists_pending_snapshot():
    conn = RecordingConnection()

    row = await create_tool_permission_request(
        conn,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        session_id="session-a",
        run_id="run-a",
        trace_id="trace-a",
        tool_id="ragflow-knowledge-search",
        tool_call_id="call-a",
        action="execute",
        risk_level="low",
        write_capable=False,
        reason="read-only search",
        request_payload_json={"query": "sop"},
    )

    sql, params = conn.calls[0]
    assert row["id"].startswith("tpr_")
    assert "run_tool_permission_requests" in sql
    assert "ragflow-knowledge-search" in params
    assert "call-a" in params
    assert False in params


@pytest.mark.asyncio
async def test_create_tool_permission_request_persists_caller_absolute_expiry_after_run_lock_wait():
    """The locked insert receives the original absolute expiry, never a post-lock extension."""

    conn = RecordingConnection()
    absolute_expiry = datetime(2030, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    await create_tool_permission_request(
        conn,
        tenant_id="tenant-a", workspace_id="workspace-a", user_id="user-a", session_id="session-a",
        run_id="run-a", trace_id="trace-a", tool_id="Bash", tool_call_id="call-absolute",
        action="execute", risk_level="high", write_capable=True, reason="waited on run lock",
        request_payload_json={}, expires_in_seconds=900, absolute_expires_at=absolute_expiry,
    )

    sql, params = conn.calls[0]
    assert "coalesce(%s::timestamptz, clock_timestamp() + (%s * interval '1 second'))" in sql
    assert params[-2] == absolute_expiry
    assert params[-1] == 900.0


@pytest.mark.asyncio
async def test_decide_tool_permission_request_preserves_the_request_deadline():
    class DecisionConnection:
        def __init__(self):
            self.calls = []

        async def execute(self, sql, params):
            normalized = " ".join(sql.split())
            self.calls.append((normalized, params))
            if "set status = 'expired'" in normalized:
                return SingleRowCursor(None)
            return SingleRowCursor({"id": "tpr-a"})

    conn = DecisionConnection()

    row = await repositories.decide_tool_permission_request(
        conn,
        tenant_id="tenant-a",
        user_id="user-a",
        run_id="run-a",
        request_id="tpr-a",
        decision="allow_once",
        reason="approved once",
        decision_payload_json={"source": "card"},
        expires_in_seconds=900,
    )

    sql, params = conn.calls[-1]
    assert row["id"] == "tpr-a"
    assert "with executable_run as" in sql
    assert "for update" in sql
    assert "cancel_requested_at is null" in sql
    assert "executable_run.id = permission_request.run_id" in sql
    assert "update run_tool_permission_requests" in sql
    assert "expires_at = permission_request.expires_at" in sql
    assert "now() + (%s * interval '1 second')" not in sql
    assert "permission_request.expires_at > clock_timestamp()" in sql
    assert "decision_payload_json = %s::jsonb" in sql
    assert params == (
        "tenant-a",
        "run-a",
        "allow_once",
        "approved once",
        '{"source": "card"}',
        "tenant-a",
        "user-a",
        "run-a",
        "tpr-a",
    )


@pytest.mark.asyncio
async def test_permission_authority_queries_use_current_clock_after_the_run_lock():
    conn = RecordingConnection()

    await repositories.get_exact_tool_permission_decision(
        conn,
        tenant_id="tenant-a",
        user_id="user-a",
        run_id="run-a",
        tool_id="claude-sdk:Bash",
        tool_call_id="call-a",
        request_payload_json={"command_sha256": "a" * 64},
    )
    await repositories.consume_tool_permission_decision(
        conn,
        tenant_id="tenant-a",
        user_id="user-a",
        run_id="run-a",
        request_id="tpr-a",
    )

    authority_sql = [sql for sql, _params in conn.calls]
    assert len(authority_sql) == 2
    assert all("for update" in sql for sql in authority_sql)
    assert all("expires_at > clock_timestamp()" in sql for sql in authority_sql)


@pytest.mark.asyncio
async def test_decide_tool_permission_request_terminalizes_at_or_beyond_expiry(monkeypatch):
    calls = []

    class ExpiredDecisionConnection:
        async def execute(self, sql, params):
            calls.append(("sql", " ".join(sql.split()), params))
            return SingleRowCursor(
                {
                    "id": "tpr-expired",
                    "run_id": "run-a",
                    "user_id": "user-a",
                    "trace_id": "trace-a",
                    "tool_id": "Bash",
                    "tool_call_id": "call-expired",
                }
            )

    async def append_event(conn, **kwargs):
        calls.append(("event", kwargs))

    async def append_audit_log(conn, **kwargs):
        calls.append(("audit", kwargs))

    monkeypatch.setattr("app.repositories.append_event", append_event)
    monkeypatch.setattr("app.repositories.append_audit_log", append_audit_log)

    result = await repositories.decide_tool_permission_request(
        ExpiredDecisionConnection(),
        tenant_id="tenant-a",
        user_id="user-a",
        run_id="run-a",
        request_id="tpr-expired",
        decision="allow_once",
        reason="too late",
        decision_payload_json={},
    )

    assert result is None
    assert "expires_at <= clock_timestamp()" in calls[0][1]
    assert calls[0][2] == (
        "tenant-a",
        "run-a",
        "run-a",
        "user-a",
        "user-a",
        "tpr-expired",
        "tpr-expired",
        1,
        "tenant-a",
        "user-a",
        "user-a",
        "run-a",
        "run-a",
        "tpr-expired",
        "tpr-expired",
        1,
    )
    assert calls[1][1]["payload"]["status"] == "expired"
    assert not any("set status = 'decided'" in call[1] for call in calls if call[0] == "sql")


@pytest.mark.asyncio
async def test_decision_loses_a_barrier_synchronized_cancel_race(monkeypatch):
    class DecisionCancelRaceConnection:
        def __init__(self):
            self.decision_ready = asyncio.Event()
            self.cancel_terminalized = asyncio.Event()
            self.terminalizations = []

        async def execute(self, sql, params):
            normalized = " ".join(sql.split())
            if "set status = 'expired'" in normalized:
                return SingleRowCursor(None)
            if "with executable_run as" in normalized:
                self.decision_ready.set()
                await self.cancel_terminalized.wait()
                return SingleRowCursor(None)
            if normalized.startswith("with eligible_run as"):
                await self.decision_ready.wait()
                return SingleRowCursor({"id": "run-a", "status": "running", "trace_id": "trace-a", "cancel_requested_newly": True})
            if normalized.startswith("update runs") and "coalesce(permission_terminalization_target" in normalized:
                return SingleRowCursor({"id": "run-a", "permission_terminalization_target": "cancel_requested"})
            if normalized.startswith("select id, trace_id, status, permission_terminalization_target"):
                return SingleRowCursor(
                    {
                        "id": "run-a",
                        "permission_terminalization_target": "cancel_requested",
                        "permission_terminalization_reason": "run_cancel_requested",
                    }
                )
            if normalized.startswith("with locked_run as"):
                self.terminalizations.append(params)
                self.cancel_terminalized.set()
                return FakeCursor()
            if "has_unterminalized" in normalized:
                return SingleRowCursor({"has_unterminalized": False})
            if normalized.startswith("update runs") and "permission_terminalization_target = null" in normalized:
                return SingleRowCursor({"id": "run-a", "status": "running"})
            raise AssertionError(normalized)

    async def no_active_leases(conn, *, tenant_id, run_id):
        return []

    async def no_op_event_or_audit(*args, **kwargs):
        return None

    monkeypatch.setattr(repositories, "list_active_sandbox_leases_for_run", no_active_leases)
    monkeypatch.setattr(repositories, "append_event", no_op_event_or_audit)
    monkeypatch.setattr(repositories, "append_audit_log", no_op_event_or_audit)
    conn = DecisionCancelRaceConnection()

    decision_task = asyncio.create_task(
        repositories.decide_tool_permission_request(
            conn,
            tenant_id="tenant-a",
            user_id="user-a",
            run_id="run-a",
            request_id="tpr-a",
            decision="allow_once",
            reason="approve",
            decision_payload_json={},
        )
    )
    cancel_task = asyncio.create_task(
        repositories.request_run_cancel(conn, tenant_id="tenant-a", user_id="user-a", run_id="run-a")
    )
    decision, cancellation = await asyncio.gather(decision_task, cancel_task)

    assert decision is None
    assert cancellation["run_id"] == "run-a"
    assert cancellation["status"] == "cancel_requested"
    assert "_permission_terminalization_progress" not in cancellation
    assert conn.terminalizations == [
        ("tenant-a", "run-a", "tenant-a", "run-a", None, None, 50, "cancelled", "run_cancel_requested")
    ]


@pytest.mark.asyncio
async def test_request_creation_loses_a_barrier_synchronized_cancel_race(monkeypatch):
    class RequestCancelRaceConnection:
        def __init__(self):
            self.request_ready = asyncio.Event()
            self.cancel_terminalized = asyncio.Event()
            self.terminalizations = []

        async def execute(self, sql, params):
            normalized = " ".join(sql.split())
            if "with eligible_run as" in normalized and "insert into run_tool_permission_requests" in normalized:
                self.request_ready.set()
                await self.cancel_terminalized.wait()
                return SingleRowCursor(None)
            if normalized.startswith("with eligible_run as"):
                await self.request_ready.wait()
                return SingleRowCursor({"id": "run-a", "status": "running", "trace_id": "trace-a", "cancel_requested_newly": True})
            if normalized.startswith("update runs") and "coalesce(permission_terminalization_target" in normalized:
                return SingleRowCursor({"id": "run-a", "permission_terminalization_target": "cancel_requested"})
            if normalized.startswith("select id, trace_id, status, permission_terminalization_target"):
                return SingleRowCursor(
                    {
                        "id": "run-a",
                        "permission_terminalization_target": "cancel_requested",
                        "permission_terminalization_reason": "run_cancel_requested",
                    }
                )
            if normalized.startswith("with locked_run as"):
                self.terminalizations.append(params)
                self.cancel_terminalized.set()
                return FakeCursor()
            if "has_unterminalized" in normalized:
                return SingleRowCursor({"has_unterminalized": False})
            if normalized.startswith("update runs") and "permission_terminalization_target = null" in normalized:
                return SingleRowCursor({"id": "run-a", "status": "running"})
            raise AssertionError(normalized)

    async def no_active_leases(conn, *, tenant_id, run_id):
        return []

    async def no_op_event_or_audit(*args, **kwargs):
        return None

    monkeypatch.setattr(repositories, "list_active_sandbox_leases_for_run", no_active_leases)
    monkeypatch.setattr(repositories, "append_event", no_op_event_or_audit)
    monkeypatch.setattr(repositories, "append_audit_log", no_op_event_or_audit)
    conn = RequestCancelRaceConnection()

    request_task = asyncio.create_task(
        repositories.create_tool_permission_request(
            conn,
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            user_id="user-a",
            session_id="session-a",
            run_id="run-a",
            trace_id="trace-a",
            tool_id="Bash",
            tool_call_id="call-a",
            action="execute",
            risk_level="high",
            write_capable=True,
            reason="write requested",
            request_payload_json={},
        )
    )
    cancel_task = asyncio.create_task(
        repositories.request_run_cancel(conn, tenant_id="tenant-a", user_id="user-a", run_id="run-a")
    )
    cancellation = await cancel_task
    with pytest.raises(RepositoryConflictError, match="tool_permission_run_not_open"):
        await request_task

    assert cancellation["run_id"] == "run-a"
    assert cancellation["status"] == "cancel_requested"
    assert "_permission_terminalization_progress" not in cancellation
    assert conn.terminalizations == [
        ("tenant-a", "run-a", "tenant-a", "run-a", None, None, 50, "cancelled", "run_cancel_requested")
    ]


@pytest.mark.asyncio
async def test_queued_cancel_orders_one_cancel_request_before_the_finalizer_terminal_event(monkeypatch):
    """Queued cancellation has one owner for each public lifecycle fact, including retries."""

    events = []

    class Connection:
        def __init__(self):
            self.attempt = 0

        async def execute(self, sql, _params):
            normalized = " ".join(sql.split())
            if normalized.startswith("with eligible_run as"):
                self.attempt += 1
                return SingleRowCursor(
                    {
                        "id": "run-a",
                        "status": "queued",
                        "trace_id": "trace-a",
                        "cancel_requested_newly": self.attempt == 1,
                    }
                )
            raise AssertionError(normalized)

    async def stage(_conn, **_kwargs):
        return {"id": "run-a"}

    async def progress(_conn, **_kwargs):
        if len(events) == 1:
            await repositories.append_event(
                _conn,
                tenant_id="tenant-a",
                run_id="run-a",
                event_type="run_cancelled",
                stage="control",
                message="任务已取消",
                payload={"visible_to_user": True},
            )
            return repositories.ToolPermissionTerminalizationProgress(True, "cancelled", True, True)
        return repositories.ToolPermissionTerminalizationProgress(True, "cancelled")

    async def record_event(_conn, **kwargs):
        events.append(kwargs["event_type"])
        return f"evt-{len(events)}"

    async def no_leases(*_args, **_kwargs):
        return []

    async def no_audit(*_args, **_kwargs):
        return None

    monkeypatch.setattr(repositories, "_stage_run_tool_permission_terminalization", stage)
    monkeypatch.setattr(repositories, "progress_run_tool_permission_terminalization", progress)
    monkeypatch.setattr(repositories, "append_event", record_event)
    monkeypatch.setattr(repositories, "append_audit_log", no_audit)
    monkeypatch.setattr(repositories, "list_active_sandbox_leases_for_run", no_leases)
    conn = Connection()

    first = await repositories.request_run_cancel(conn, tenant_id="tenant-a", user_id="user-a", run_id="run-a")
    second = await repositories.request_run_cancel(conn, tenant_id="tenant-a", user_id="user-a", run_id="run-a")

    assert first["status"] == second["status"] == "cancelled"
    assert events == ["cancel_requested", "run_cancelled"]


@pytest.mark.asyncio
async def test_list_tool_permission_inbox_filters_current_user_and_status():
    conn = RecordingConnection()

    rows = await list_tool_permission_inbox(
        conn,
        tenant_id="tenant-a",
        user_id="user-a",
        status="pending",
        limit=25,
    )

    sql, params = conn.calls[-1]
    assert "from run_tool_permission_requests" in sql
    assert "where permission_request.tenant_id = %s and permission_request.user_id = %s" in sql
    assert "runs.status as run_status" in sql
    assert "(%s = 'all' or permission_request.status = %s)" in sql
    assert "expires_at > clock_timestamp()" in sql
    assert "order by permission_request.created_at desc, permission_request.id desc" in sql
    assert params == ("tenant-a", "user-a", "pending", "pending", 25)
    assert "set status = 'expired'" in conn.calls[0][0]
    assert rows == []


@pytest.mark.asyncio
async def test_list_tool_permission_inbox_for_tenant_excludes_admin_user_filter():
    conn = RecordingConnection()

    rows = await list_tool_permission_inbox_for_tenant(
        conn,
        tenant_id="tenant-a",
        status="pending",
        limit=25,
    )

    sql, params = conn.calls[-1]
    assert "from run_tool_permission_requests" in sql
    assert "where permission_request.tenant_id = %s" in sql
    assert "permission_request.user_id =" not in sql
    assert "runs.status as run_status" in sql
    assert "(%s = 'all' or permission_request.status = %s)" in sql
    assert "expires_at > clock_timestamp()" in sql
    assert params == ("tenant-a", "pending", "pending", 25)
    assert "set status = 'expired'" in conn.calls[0][0]
    assert "order by permission_request.expires_at asc, permission_request.id asc" in conn.calls[0][0]
    assert "for update skip locked" in conn.calls[0][0]
    assert conn.calls[0][1][-1] == 50
    assert rows == []


@pytest.mark.asyncio
async def test_tenant_permission_inbox_expiry_is_bounded_and_makes_batch_progress(monkeypatch):
    calls = []

    class BatchCursor:
        async def fetchall(self):
            return [
                {
                    "id": "tpr-a",
                    "tenant_id": "tenant-a",
                    "run_id": "run-a",
                    "user_id": "user-a",
                    "trace_id": "trace-a",
                    "tool_id": "Bash",
                    "tool_call_id": "call-a",
                    "action": "execute",
                    "risk_level": "high",
                    "write_capable": True,
                },
                {
                    "id": "tpr-b",
                    "tenant_id": "tenant-a",
                    "run_id": "run-b",
                    "user_id": "user-b",
                    "trace_id": "trace-b",
                    "tool_id": "Bash",
                    "tool_call_id": "call-b",
                    "action": "execute",
                    "risk_level": "high",
                    "write_capable": True,
                },
            ]

    class BatchConnection:
        async def execute(self, sql, params):
            calls.append((" ".join(sql.split()), params))
            return BatchCursor()

    async def append_event(conn, **kwargs):
        calls.append(("event", kwargs))

    async def append_audit_log(conn, **kwargs):
        calls.append(("audit", kwargs))

    monkeypatch.setattr(repositories, "append_event", append_event)
    monkeypatch.setattr(repositories, "append_audit_log", append_audit_log)

    rows = await repositories.expire_pending_tool_permission_requests(
        BatchConnection(),
        tenant_id="tenant-a",
        limit=10_000,
    )

    assert [row["id"] for row in rows] == ["tpr-a", "tpr-b"]
    expiry_sql, expiry_params = calls[0]
    assert expiry_sql.startswith("with locked_runs as materialized")
    assert "), expired_requests as" in expiry_sql
    assert "from runs" in expiry_sql
    assert "runs.permission_terminalization_target is null" in expiry_sql
    assert "runs.tenant_id = %s" in expiry_sql
    assert "permission_request.tenant_id = %s" in expiry_sql
    assert "candidate.expires_at is null or candidate.expires_at <= clock_timestamp()" in expiry_sql
    assert "permission_request.expires_at is null or permission_request.expires_at <= clock_timestamp()" in expiry_sql
    assert "expires_at = coalesce(permission_request.expires_at, clock_timestamp())" in expiry_sql
    assert "order by permission_request.expires_at asc, permission_request.id asc" in expiry_sql
    assert "limit %s" in expiry_sql
    assert "for update skip locked" in expiry_sql
    assert "for update of permission_request skip locked" in expiry_sql
    assert expiry_params[0] == "tenant-a"
    assert expiry_params[8] == "tenant-a"
    assert expiry_params[7] == 50
    assert expiry_params[-1] == 50
    assert [entry[1]["target_id"] for entry in calls if entry[0] == "audit"] == ["tpr-a", "tpr-b"]
    assert [entry[1]["run_id"] for entry in calls if entry[0] == "event"] == ["run-a", "run-b"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("request_count", "batch_size", "target_status"),
    [
        (2, 1, "failed"),
        (51, 50, "failed"),
        (51, 50, "cancelled"),
    ],
    ids=["existing-one-plus-one", "failed-51", "cancelled-51"],
)
async def test_terminalization_progresses_in_bounded_crash_retry_batches_without_duplicate_facts(
    monkeypatch,
    request_count,
    batch_size,
    target_status,
):
    events = []
    audits = []

    class RowsCursor:
        def __init__(self, rows):
            self.rows = rows

        async def fetchall(self):
            return self.rows

    class ProgressConnection:
        def __init__(self, request_count=2, target_status="failed", batch_size=50):
            self.batch = 0
            self.sql = []
            self.remaining_request_ids = (
                ["tpr-a", "tpr-b"]
                if request_count == 2
                else [f"tpr-{index}" for index in range(request_count)]
            )
            self.target_status = target_status
            self.batch_size = batch_size
            self.finalized = False
            self.run_status = "running"
            self.permission_terminalization_target = target_status
            self.closed_steps = []

        async def execute(self, sql, params):
            normalized = " ".join(sql.split())
            normalized_lower = normalized.lower()
            self.sql.append((normalized, params))
            if normalized_lower.startswith("select id, trace_id, status, permission_terminalization_target"):
                return SingleRowCursor(
                    {
                        "id": "run-a",
                        "trace_id": "trace-a",
                        "status": self.run_status,
                        "permission_terminalization_target": self.permission_terminalization_target,
                        "permission_terminalization_reason": f"run_{self.target_status}",
                        "permission_terminalization_result_json": {"message": "failed"},
                        "permission_terminalization_error_code": "executor_failure",
                        "permission_terminalization_error_message": "failed",
                        "latency_ms": 17,
                        "input_token_count": 3,
                        "output_token_count": 5,
                        "total_token_count": 8,
                        "estimated_cost_minor": 11,
                    }
                )
            if normalized_lower.startswith("with locked_run as"):
                self.batch += 1
                batch_ids = self.remaining_request_ids[:self.batch_size]
                self.remaining_request_ids = self.remaining_request_ids[self.batch_size:]
                return RowsCursor([
                        {
                            "id": request_id,
                            "user_id": "user-a",
                            "trace_id": "trace-a",
                            "tool_id": "Bash",
                            "tool_call_id": f"call-{request_id}",
                            "action": "execute",
                            "risk_level": "high",
                            "write_capable": True,
                            "decision": "allow_for_run",
                        } for request_id in batch_ids
                ])
            if "has_unterminalized" in normalized_lower:
                return SingleRowCursor({"has_unterminalized": bool(self.remaining_request_ids)})
            if normalized_lower.startswith("update runs") and "set status" in normalized_lower:
                self.finalized = True
                self.run_status = self.target_status
                self.permission_terminalization_target = None
                return SingleRowCursor({"id": "run-a", "status": self.target_status})
            if normalized_lower.startswith("select count(*) as artifact_count from artifacts"):
                return SingleRowCursor({"artifact_count": 2})
            if normalized_lower.startswith("update run_steps"):
                self.closed_steps.append(self.target_status)
                return FakeCursor()
            raise AssertionError(normalized)

    async def append_event(conn, **kwargs):
        events.append(kwargs)

    async def append_audit_log(conn, **kwargs):
        audits.append(kwargs)

    monkeypatch.setattr(repositories, "append_event", append_event)
    monkeypatch.setattr(repositories, "append_audit_log", append_audit_log)
    conn = ProgressConnection(
        request_count=request_count,
        target_status=target_status,
        batch_size=batch_size,
    )

    first = await repositories.progress_run_tool_permission_terminalization(
        conn,
        tenant_id="tenant-a",
        run_id="run-a",
    )

    request_events = [event for event in events if event["event_type"] == "tool_permission_terminalized"]
    run_events = [event for event in events if event["event_type"] == f"run_{target_status}"]
    request_audits = [audit for audit in audits if audit["target_type"] == "tool_permission_request"]
    run_audits = [audit for audit in audits if audit["target_type"] == "run"]

    assert first.completed is False
    assert first.status == target_status
    assert first.did_transition is False and first.needs_reconcile is False
    assert len(request_events) == batch_size
    assert len({event["payload"]["permission_request_id"] for event in request_events}) == batch_size
    assert len(request_audits) == batch_size
    assert len(run_events) == len(run_audits) == 0
    assert len(conn.remaining_request_ids) == request_count - batch_size
    assert conn.run_status == "running"
    assert conn.permission_terminalization_target == target_status
    assert conn.closed_steps == []

    second = await repositories.progress_run_tool_permission_terminalization(
        conn,
        tenant_id="tenant-a",
        run_id="run-a",
    )

    request_events = [event for event in events if event["event_type"] == "tool_permission_terminalized"]
    run_events = [event for event in events if event["event_type"] == f"run_{target_status}"]
    request_audits = [audit for audit in audits if audit["target_type"] == "tool_permission_request"]
    run_audits = [audit for audit in audits if audit["target_type"] == "run"]

    assert second.completed is True
    assert second.status == target_status
    assert second.did_transition is True and second.needs_reconcile is True
    assert len(request_events) == request_count
    request_ids = [event["payload"]["permission_request_id"] for event in request_events]
    assert len(set(request_ids)) == request_count
    assert len(request_audits) == request_count
    assert len({audit["target_id"] for audit in request_audits}) == request_count
    assert len(run_events) == len(run_audits) == 1
    assert run_events[0]["payload"]["artifact_count"] == 2
    assert run_events[0]["payload"]["result"] == {"message": "failed"}
    if target_status == "failed":
        assert run_events[0]["payload"]["error_code"] == "executor_failure"
        assert run_events[0]["payload"]["error_message"] == "failed"
    assert run_events[0]["latency_ms"] == 17
    assert run_events[0]["input_token_count"] == 3
    assert run_events[0]["output_token_count"] == 5
    assert run_events[0]["total_token_count"] == 8
    assert run_events[0]["estimated_cost_minor"] == 11
    assert conn.remaining_request_ids == []
    assert conn.run_status == target_status
    assert conn.permission_terminalization_target is None
    assert conn.closed_steps == [target_status]

    before_retry_facts = (len(events), len(audits))
    retry = await repositories.progress_run_tool_permission_terminalization(
        conn,
        tenant_id="tenant-a",
        run_id="run-a",
    )
    assert retry.completed is True and retry.status == target_status
    assert retry.did_transition is False and retry.needs_reconcile is False
    assert (len(events), len(audits)) == before_retry_facts
    batch_sql = [sql for sql, _ in conn.sql if sql.startswith("with locked_run as")]
    assert len(batch_sql) == 3
    assert all("limit %s" in sql and "for update of permission_request skip locked" in sql for sql in batch_sql)


@pytest.mark.asyncio
async def test_soft_cancel_51_row_drain_upgrades_to_one_cancelled_terminal_result(monkeypatch):
    """The route's soft 50-row intent is upgraded by the worker's final cancelled write."""

    events = []
    audits = []

    class RowsCursor:
        def __init__(self, rows):
            self.rows = rows

        async def fetchall(self):
            return self.rows

    class SoftCancelConnection:
        def __init__(self):
            self.target = None
            self.run_status = "running"
            self.remaining_request_ids = [f"tpr-{index}" for index in range(51)]
            self.closed_steps = []

        async def execute(self, sql, params):
            normalized = " ".join(sql.split())
            lowered = normalized.lower()
            if "set permission_terminalization_target = case" in lowered:
                assert "permission_terminalization_target = 'cancel_requested'" in lowered
                requested = params[0]
                if self.target is None or (self.target == "cancel_requested" and requested == "cancelled"):
                    self.target = requested
                return SingleRowCursor({"id": "run-a", "trace_id": "trace-a", "permission_terminalization_target": self.target})
            if lowered.startswith("select id, trace_id, status, permission_terminalization_target"):
                return SingleRowCursor(
                    {
                        "id": "run-a",
                        "trace_id": "trace-a",
                        "status": self.run_status,
                        "permission_terminalization_target": self.target,
                        "permission_terminalization_reason": "run_cancel_requested",
                        "permission_terminalization_result_json": {"message": "任务已取消"},
                        "permission_terminalization_error_code": None,
                        "permission_terminalization_error_message": None,
                        "latency_ms": 0,
                        "input_token_count": 0,
                        "output_token_count": 0,
                        "total_token_count": 0,
                        "estimated_cost_minor": 0,
                    }
                )
            if lowered.startswith("with locked_run as"):
                batch = self.remaining_request_ids[:50]
                self.remaining_request_ids = self.remaining_request_ids[50:]
                return RowsCursor(
                    [
                        {
                            "id": request_id,
                            "user_id": "user-a",
                            "trace_id": "trace-a",
                            "tool_id": "Bash",
                            "tool_call_id": f"call-{request_id}",
                            "action": "execute",
                            "risk_level": "high",
                            "write_capable": True,
                            "decision": "allow_for_run",
                        }
                        for request_id in batch
                    ]
                )
            if "has_unterminalized" in lowered:
                return SingleRowCursor({"has_unterminalized": bool(self.remaining_request_ids)})
            if lowered.startswith("update runs") and "set status = 'cancelled'" in lowered:
                self.target = None
                self.run_status = "cancelled"
                return SingleRowCursor({"id": "run-a", "status": "cancelled"})
            if lowered.startswith("select count(*) as artifact_count from artifacts"):
                return SingleRowCursor({"artifact_count": 0})
            if lowered.startswith("update run_steps"):
                self.closed_steps.append("cancelled")
                return FakeCursor()
            raise AssertionError(normalized)

    async def append_event(_conn, **kwargs):
        events.append(kwargs)

    async def append_audit(_conn, **kwargs):
        audits.append(kwargs)

    monkeypatch.setattr(repositories, "append_event", append_event)
    monkeypatch.setattr(repositories, "append_audit_log", append_audit)
    conn = SoftCancelConnection()

    staged_soft = await repositories._stage_run_tool_permission_terminalization(
        conn,
        tenant_id="tenant-a",
        run_id="run-a",
        target_status="cancel_requested",
        terminal_reason="run_cancel_requested",
    )
    first = await repositories.progress_run_tool_permission_terminalization(
        conn,
        tenant_id="tenant-a",
        run_id="run-a",
    )
    final = await repositories.cancel_run(
        conn,
        tenant_id="tenant-a",
        run_id="run-a",
        result_json={"message": "任务已取消"},
    )

    assert staged_soft["permission_terminalization_target"] == "cancel_requested"
    assert first.completed is False and first.status == "cancel_requested"
    assert bool(first) is False
    assert final.completed is True and final.status == "cancelled"
    assert bool(final) is True
    assert conn.run_status == "cancelled"
    assert conn.closed_steps == ["cancelled"]
    assert [event["event_type"] for event in events].count("run_cancelled") == 1
    assert [audit["action"] for audit in audits].count("run.cancelled") == 1


@pytest.mark.asyncio
async def test_terminal_intent_merge_upgrades_only_soft_cancel_and_preserves_first_final_target():
    """Conflicting final intents retain their first durable target while a soft cancel can become cancelled."""

    class IntentConnection:
        def __init__(self):
            self.target = None

        async def execute(self, sql, params):
            normalized = " ".join(sql.split()).lower()
            assert "set permission_terminalization_target = case" in normalized
            assert "when permission_terminalization_target = 'cancel_requested'" in normalized
            requested = params[0]
            if self.target is None or (self.target == "cancel_requested" and requested == "cancelled"):
                self.target = requested
            return SingleRowCursor({"id": "run-a", "permission_terminalization_target": self.target})

    conn = IntentConnection()
    soft = await repositories._stage_run_tool_permission_terminalization(
        conn, tenant_id="tenant-a", run_id="run-a", target_status="cancel_requested", terminal_reason="route"
    )
    upgraded = await repositories._stage_run_tool_permission_terminalization(
        conn, tenant_id="tenant-a", run_id="run-a", target_status="cancelled", terminal_reason="worker"
    )
    conflict = await repositories._stage_run_tool_permission_terminalization(
        conn, tenant_id="tenant-a", run_id="run-a", target_status="failed", terminal_reason="late_failure"
    )

    assert soft["permission_terminalization_target"] == "cancel_requested"
    assert upgraded["permission_terminalization_target"] == "cancelled"
    assert conflict["permission_terminalization_target"] == "cancelled"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("operation", "first_target"),
    [("cancel", "failed"), ("fail", "cancelled")],
)
async def test_conflicting_final_intent_returns_actual_status_without_claiming_completion(monkeypatch, operation, first_target):
    """A later final writer cannot claim its result when the run already has the opposite durable target."""

    async def stage(*_args, **_kwargs):
        return {"permission_terminalization_target": first_target}

    async def progress(*_args, **_kwargs):
        raise AssertionError("conflicting target must not drain or mutate the existing final intent")

    monkeypatch.setattr(repositories, "_stage_run_tool_permission_terminalization", stage)
    monkeypatch.setattr(repositories, "progress_run_tool_permission_terminalization", progress)

    if operation == "cancel":
        result = await repositories.cancel_run(
            object(), tenant_id="tenant-a", run_id="run-a", result_json={"message": "cancelled"}
        )
    else:
        result = await repositories.fail_run(
            object(),
            tenant_id="tenant-a",
            run_id="run-a",
            error_code="executor_failure",
            error_message="boom",
        )

    assert result.completed is False
    assert result.status == first_target
    assert bool(result) is False


@pytest.mark.asyncio
@pytest.mark.parametrize("admin", [False, True], ids=["owner", "admin"])
async def test_cancel_request_response_reports_actual_conflicting_terminal_status(monkeypatch, admin):
    """Owner and admin cancellation responses expose a concurrent final failure rather than a soft intent."""

    class Connection:
        async def execute(self, sql, _params):
            normalized = " ".join(sql.split())
            assert normalized.startswith("with eligible_run as")
            row = {
                "id": "run-a",
                "status": "running",
                "trace_id": "trace-a",
                "cancel_requested_newly": False,
            }
            if admin:
                row["user_id"] = "owner-a"
            return SingleRowCursor(row)

    async def stage(*_args, **_kwargs):
        return {"permission_terminalization_target": "failed"}

    async def progress(*_args, **_kwargs):
        return repositories.ToolPermissionTerminalizationProgress(True, "failed", True, True)

    async def no_leases(*_args, **_kwargs):
        return []

    async def no_audit(*_args, **_kwargs):
        return None

    monkeypatch.setattr(repositories, "_stage_run_tool_permission_terminalization", stage)
    monkeypatch.setattr(repositories, "progress_run_tool_permission_terminalization", progress)
    monkeypatch.setattr(repositories, "list_active_sandbox_leases_for_run", no_leases)
    monkeypatch.setattr(repositories, "append_audit_log", no_audit)

    if admin:
        result = await repositories.request_admin_run_cancel(
            Connection(), tenant_id="tenant-a", admin_user_id="admin-a", run_id="run-a"
        )
    else:
        result = await repositories.request_run_cancel(
            Connection(), tenant_id="tenant-a", user_id="owner-a", run_id="run-a"
        )

    assert result == {
        "run_id": "run-a",
        "status": "failed",
        "_permission_terminalization_progress": repositories.ToolPermissionTerminalizationProgress(
            True, "failed", True, True
        ),
    }


@pytest.mark.asyncio
async def test_terminalization_maintenance_lists_only_bounded_durable_or_legacy_run_work_items():
    class Cursor:
        async def fetchall(self):
            return [{"tenant_id": "tenant-a", "run_id": "run-a"}]

    class Connection:
        def __init__(self):
            self.calls = []

        async def execute(self, sql, params):
            self.calls.append((" ".join(sql.split()), params))
            return Cursor()

    conn = Connection()
    rows = await repositories.list_runs_requiring_tool_permission_terminalization(conn, limit=10_000)

    assert rows == [{"tenant_id": "tenant-a", "run_id": "run-a"}]
    sql, params = conn.calls[0]
    assert "runs.permission_terminalization_target is not null" in sql
    assert "runs.status in ('succeeded', 'failed', 'cancelled')" in sql
    assert "permission_request.status in ('pending', 'decided')" in sql
    assert "permission_request.expires_at is null or permission_request.expires_at <= clock_timestamp()" in sql
    assert "limit %s" in sql
    assert "for update skip locked" in sql
    assert params == (50,)


@pytest.mark.asyncio
async def test_terminalization_maintenance_lists_bounded_durable_handed_off_child_recovery_work():
    class Cursor:
        async def fetchall(self):
            return [{"tenant_id": "tenant-a", "run_id": "child-a", "status": "cancelled"}]

    class Connection:
        def __init__(self):
            self.calls = []

        async def execute(self, sql, params):
            self.calls.append((" ".join(sql.split()), params))
            return Cursor()

    conn = Connection()
    rows = await repositories.list_multi_agent_terminal_children_requiring_reconciliation(conn, limit=10_000)

    assert rows == [{"tenant_id": "tenant-a", "run_id": "child-a", "status": "cancelled"}]
    sql, params = conn.calls[0]
    assert "child.copied_from_run_id is not null" in sql
    assert "parent_step.payload_json->>'dispatch_state' = 'handed_off'" in sql
    assert "parent_step.payload_json->>'dispatch_child_run_id' = child.id" in sql
    assert "child.status in ('succeeded', 'failed', 'cancelled')" in sql
    assert "limit %s" in sql and "for update of child, parent_step skip locked" in sql
    assert params == (50,)


@pytest.mark.asyncio
async def test_multi_agent_recovery_queries_order_only_by_authoritative_runs_columns():
    """Recovery ordering is schema-bound: `runs` has no `updated_at` column to hide a PostgreSQL failure."""

    schema = Path("app/schema.sql").read_text(encoding="utf-8")
    runs_definition = schema.split("create table if not exists runs (", 1)[1].split(");", 1)[0]
    runs_columns = {
        line.strip().split(maxsplit=1)[0].rstrip(",")
        for line in runs_definition.splitlines()
        if line.strip() and not line.strip().startswith(("primary key", "foreign key", "check", "constraint"))
    }
    assert {"id", "tenant_id", "started_at", "finished_at", "created_at"}.issubset(runs_columns)
    assert "updated_at" not in runs_columns

    class Cursor:
        def __init__(self, rows):
            self.rows = rows

        async def fetchall(self):
            return self.rows

    class SchemaBoundConnection:
        async def execute(self, sql, params):
            normalized = " ".join(sql.split())
            assert "updated_at" not in normalized
            if "from runs child" in normalized:
                assert (
                    "order by coalesce(child.finished_at, child.started_at, child.created_at) asc, "
                    "child.tenant_id asc, child.id asc"
                ) in normalized
                assert params == (50,)
                return Cursor([{"tenant_id": "tenant-a", "run_id": "child-a", "status": "failed"}])
            assert "from runs parent" in normalized
            assert (
                "order by coalesce(parent.finished_at, parent.started_at, parent.created_at) asc, "
                "parent.tenant_id asc, parent.id asc"
            ) in normalized
            assert params == (50,)
            return Cursor([{"tenant_id": "tenant-a", "run_id": "parent-a"}])

    conn = SchemaBoundConnection()
    child_rows = await repositories.list_multi_agent_terminal_children_requiring_reconciliation(conn, limit=10_000)
    parent_rows = await repositories.list_multi_agent_parent_runs_requiring_finalization(conn, limit=10_000)

    assert child_rows == [{"tenant_id": "tenant-a", "run_id": "child-a", "status": "failed"}]
    assert parent_rows == [{"tenant_id": "tenant-a", "run_id": "parent-a"}]


@pytest.mark.asyncio
async def test_terminalization_maintenance_lists_ready_parent_rollup_recovery_work():
    class Cursor:
        async def fetchall(self):
            return [{"tenant_id": "tenant-a", "run_id": "parent-a"}]

    class Connection:
        def __init__(self):
            self.calls = []

        async def execute(self, sql, params):
            self.calls.append((" ".join(sql.split()), params))
            return Cursor()

    conn = Connection()
    rows = await repositories.list_multi_agent_parent_runs_requiring_finalization(conn, limit=10_000)

    assert rows == [{"tenant_id": "tenant-a", "run_id": "parent-a"}]
    sql, params = conn.calls[0]
    assert "parent.status in ('running', 'succeeded', 'failed', 'cancelled')" in sql
    assert "parent.status = 'queued' and parent.cancel_requested_at is not null" in sql
    assert "parent_step.status not in ('succeeded', 'failed', 'cancelled')" in sql
    assert "parent_event.event_type = 'multi_agent_parent_finalized'" in sql
    assert "parent_audit.action = 'run.multi_agent.parent.finalize'" in sql
    assert "for update of parent skip locked" in sql
    assert params == (50,)


def test_terminalization_progress_soft_cancel_intent_is_not_truthy_completion():
    """A recorded cancellation request is not evidence that a run reached cancelled."""

    progress = repositories.ToolPermissionTerminalizationProgress(
        completed=True,
        status="cancel_requested",
    )

    assert bool(progress) is False


@pytest.mark.asyncio
async def test_terminal_parent_missing_rollup_facts_are_written_after_generic_terminalization(monkeypatch):
    """A parent already terminalized by a bounded drain still gets its one parent fact."""

    events = []
    audits = []

    class RowsCursor:
        async def fetchall(self):
            return []

    class Cursor:
        async def fetchone(self):
            return {
                "id": "parent-a",
                "tenant_id": "tenant-a",
                "copied_from_run_id": None,
                "trace_id": "trace-parent-a",
                "status": "failed",
                "cancel_requested_at": None,
                "input_json": {
                    "input": {
                        "execution_mode": "multi_agent",
                        "multi_agent_steps": [{"step_key": f"child-{index}"} for index in range(51)],
                    }
                },
            }

    class ParentRecoveryConnection:
        def __init__(self):
            self.has_event = False
            self.has_audit = False

        async def execute(self, sql, _params):
            normalized = " ".join(sql.split()).lower()
            if normalized.startswith("select id, tenant_id, copied_from_run_id"):
                return Cursor()
            if normalized.startswith("select child.id, child.status"):
                return RowsCursor()
            if "has_parent_finalized_event" in normalized:
                return SingleRowCursor(
                    {"has_parent_finalized_event": self.has_event, "has_parent_finalized_audit": self.has_audit}
                )
            raise AssertionError(normalized)

    async def terminal_steps(*_args, **_kwargs):
        return [
            {"id": f"step-{index}", "step_key": f"child-{index}", "status": "failed", "payload_json": {}}
            for index in range(51)
        ]

    async def append_event(_conn, **kwargs):
        events.append(kwargs)
        conn.has_event = True
        return "evt-parent-a"

    async def append_audit(_conn, **kwargs):
        audits.append(kwargs)
        conn.has_audit = True
        return "aud-parent-a"

    monkeypatch.setattr(repositories, "list_run_steps", terminal_steps)
    monkeypatch.setattr(repositories, "append_event", append_event)
    monkeypatch.setattr(repositories, "append_audit_log", append_audit)

    conn = ParentRecoveryConnection()
    finalized = await repositories.finalize_multi_agent_parent_run_if_ready(
        conn,
        tenant_id="tenant-a",
        parent_run_id="parent-a",
    )
    retry = await repositories.finalize_multi_agent_parent_run_if_ready(
        conn,
        tenant_id="tenant-a",
        parent_run_id="parent-a",
    )

    assert finalized is not None
    assert finalized["status"] == "failed"
    assert finalized["counts"]["failed"] == 51
    assert retry is None
    assert [event["event_type"] for event in events] == ["multi_agent_parent_finalized"]
    assert [audit["action"] for audit in audits] == ["run.multi_agent.parent.finalize"]


@pytest.mark.asyncio
async def test_terminalization_maintenance_progresses_legacy_null_expiry_without_memory_cleanup(monkeypatch):
    class Cursor:
        async def fetchone(self):
            return {
                "id": "run-a",
                "trace_id": "trace-a",
                "status": "running",
                "permission_terminalization_target": None,
            }

    class Connection:
        async def execute(self, sql, params):
            assert "from runs" in sql
            assert params == ("tenant-a", "run-a")
            return Cursor()

    expired_calls = []

    async def expire_pending(conn, **kwargs):
        expired_calls.append(kwargs)
        return [{"id": "tpr-null-expiry", "status": "expired"}]

    monkeypatch.setattr(repositories, "expire_pending_tool_permission_requests", expire_pending)

    result = await repositories.progress_run_tool_permission_terminalization(
        Connection(),
        tenant_id="tenant-a",
        run_id="run-a",
    )

    assert result.completed is False and result.status == "running" and result.did_transition is False
    assert expired_calls == [{"tenant_id": "tenant-a", "run_id": "run-a"}]


@pytest.mark.asyncio
async def test_get_tool_permission_request_by_id_scopes_to_user_without_run():
    conn = RecordingConnection()

    row = await get_tool_permission_request_by_id(
        conn,
        tenant_id="tenant-a",
        user_id="user-a",
        request_id="tpr-a",
    )

    sql, params = conn.calls[0]
    assert "from run_tool_permission_requests" in sql
    assert "where tenant_id = %s and user_id = %s and id = %s" in sql
    assert "run_id =" not in sql
    assert params == ("tenant-a", "user-a", "tpr-a")
    assert row["id"] == "step-a"


@pytest.mark.asyncio
async def test_admin_tool_permission_lookup_scopes_to_tenant_run_and_request_not_admin_user():
    conn = RecordingConnection()

    row = await get_tool_permission_request_for_tenant(
        conn,
        tenant_id="tenant-a",
        run_id="run-a",
        request_id="tpr-a",
    )

    sql, params = conn.calls[0]
    assert "from run_tool_permission_requests" in sql
    assert "where permission_request.tenant_id = %s and permission_request.run_id = %s and permission_request.id = %s" in sql
    assert "runs.status as run_status" in sql
    assert "user_id =" not in sql
    assert params == ("tenant-a", "run-a", "tpr-a")
    assert row["id"] == "step-a"


@pytest.mark.asyncio
async def test_admin_tool_permission_inbox_lookup_scopes_to_tenant_and_request_not_admin_user():
    conn = RecordingConnection()

    row = await get_tool_permission_request_by_id_for_tenant(
        conn,
        tenant_id="tenant-a",
        request_id="tpr-a",
    )

    sql, params = conn.calls[0]
    assert "from run_tool_permission_requests" in sql
    assert "where permission_request.tenant_id = %s and permission_request.id = %s" in sql
    assert "runs.status as run_status" in sql
    assert "user_id =" not in sql
    assert params == ("tenant-a", "tpr-a")
    assert row["id"] == "step-a"


@pytest.mark.asyncio
async def test_get_exact_tool_permission_decision_requires_exact_call_or_fingerprint():
    conn = RecordingConnection()

    row = await get_exact_tool_permission_decision(
        conn,
        tenant_id="tenant-a",
        user_id="user-a",
        run_id="run-a",
        tool_id="ragflow-knowledge-search",
        action="execute",
    )

    assert row is None
    assert conn.calls == []


@pytest.mark.asyncio
async def test_get_exact_tool_permission_decision_filters_tool_call_or_fingerprint():
    conn = RecordingConnection()

    await get_exact_tool_permission_decision(
        conn,
        tenant_id="tenant-a",
        user_id="user-a",
        run_id="run-a",
        tool_id="claude-sdk:Bash",
        action="execute",
        tool_call_id="tool-current",
        request_payload_json={"command_sha256": "a" * 64},
    )

    sql, params = conn.calls[0]
    assert "decision in ('allow_once', 'deny')" in sql
    assert "tool_call_id = %s" in sql
    assert "decision = 'allow_for_run'" in sql
    assert "request_payload_json ->> %s = %s" in sql
    assert "with executable_run as" in sql
    assert "cancel_requested_at is null" in sql
    assert "for update" in sql
    assert params == (
        "tenant-a",
        "run-a",
        "tenant-a",
        "user-a",
        "run-a",
        "claude-sdk:Bash",
        "execute",
        "tool-current",
        "command_sha256",
        "a" * 64,
    )


@pytest.mark.asyncio
async def test_legacy_latest_tool_permission_decision_wrapper_uses_exact_lookup_shape():
    conn = RecordingConnection()

    await get_latest_tool_permission_decision(
        conn,
        tenant_id="tenant-a",
        user_id="user-a",
        run_id="run-a",
        tool_id="ragflow-knowledge-search",
        action="execute",
        tool_call_id="mcp-current",
        request_payload_json={"input_sha256": "b" * 64},
    )

    sql, params = conn.calls[0]
    assert "decision in ('allow_once', 'deny')" in sql
    assert "decision = 'allow_for_run'" in sql
    assert params == (
        "tenant-a",
        "run-a",
        "tenant-a",
        "user-a",
        "run-a",
        "ragflow-knowledge-search",
        "execute",
        "mcp-current",
        "input_sha256",
        "b" * 64,
    )


@pytest.mark.asyncio
async def test_consume_tool_permission_decision_marks_only_decided_allow_once_consumed():
    conn = RecordingConnection()
    consume = getattr(repositories, "consume_tool_permission_decision", None)
    assert consume is not None, "repository must expose consume_tool_permission_decision"

    row = await consume(
        conn,
        tenant_id="tenant-a",
        user_id="user-a",
        run_id="run-a",
        request_id="tpr-a",
    )

    sql, params = conn.calls[0]
    assert row["id"] == "step-a"
    assert "update run_tool_permission_requests" in sql
    assert "set status = 'consumed'" in sql
    assert "with executable_run as" in sql
    assert "cancel_requested_at is null" in sql
    assert "permission_request.tenant_id = %s" in sql
    assert "permission_request.user_id = %s" in sql
    assert "permission_request.run_id = %s" in sql
    assert "permission_request.id = %s" in sql
    assert "permission_request.decision = 'allow_once'" in sql
    assert "permission_request.status = 'decided'" in sql
    assert "permission_request.expires_at > clock_timestamp()" in sql
    assert "returning permission_request.*" in sql
    assert params == ("tenant-a", "run-a", "tenant-a", "user-a", "run-a", "tpr-a")


@pytest.mark.asyncio
async def test_permission_grant_lookup_locks_an_executable_run_before_reuse():
    conn = RecordingConnection()

    await get_exact_tool_permission_decision(
        conn,
        tenant_id="tenant-a",
        user_id="user-a",
        run_id="run-a",
        tool_id="claude-sdk:Bash",
        tool_call_id="call-a",
    )

    sql, params = conn.calls[0]
    assert "with executable_run as" in sql
    assert "status = 'running'" in sql
    assert "cancel_requested_at is null" in sql
    assert "for update" in sql
    assert "join executable_run on executable_run.id = permission_request.run_id" in sql
    assert params[:5] == ("tenant-a", "run-a", "tenant-a", "user-a", "run-a")


@pytest.mark.asyncio
async def test_allow_once_consumption_loses_a_barrier_synchronized_cancel_race(monkeypatch):
    class ConsumeCancelRaceConnection:
        def __init__(self):
            self.consume_ready = asyncio.Event()
            self.cancel_terminalized = asyncio.Event()
            self.terminalizations = []

        async def execute(self, sql, params):
            normalized = " ".join(sql.split())
            if "set status = 'consumed'" in normalized:
                self.consume_ready.set()
                await self.cancel_terminalized.wait()
                return SingleRowCursor(None)
            if normalized.startswith("with eligible_run as"):
                await self.consume_ready.wait()
                return SingleRowCursor({"id": "run-a", "status": "running", "trace_id": "trace-a", "cancel_requested_newly": True})
            if normalized.startswith("update runs") and "coalesce(permission_terminalization_target" in normalized:
                return SingleRowCursor({"id": "run-a", "permission_terminalization_target": "cancel_requested"})
            if normalized.startswith("select id, trace_id, status, permission_terminalization_target"):
                return SingleRowCursor(
                    {
                        "id": "run-a",
                        "permission_terminalization_target": "cancel_requested",
                        "permission_terminalization_reason": "run_cancel_requested",
                    }
                )
            if normalized.startswith("with locked_run as"):
                self.terminalizations.append(params)
                self.cancel_terminalized.set()
                return FakeCursor()
            if "has_unterminalized" in normalized:
                return SingleRowCursor({"has_unterminalized": False})
            if normalized.startswith("update runs") and "permission_terminalization_target = null" in normalized:
                return SingleRowCursor({"id": "run-a", "status": "running"})
            raise AssertionError(normalized)

    async def no_active_leases(conn, *, tenant_id, run_id):
        return []

    async def no_op_event_or_audit(*args, **kwargs):
        return None

    monkeypatch.setattr(repositories, "list_active_sandbox_leases_for_run", no_active_leases)
    monkeypatch.setattr(repositories, "append_event", no_op_event_or_audit)
    monkeypatch.setattr(repositories, "append_audit_log", no_op_event_or_audit)
    conn = ConsumeCancelRaceConnection()

    consume_task = asyncio.create_task(
        repositories.consume_tool_permission_decision(
            conn,
            tenant_id="tenant-a",
            user_id="user-a",
            run_id="run-a",
            request_id="tpr-a",
        )
    )
    cancel_task = asyncio.create_task(
        repositories.request_run_cancel(conn, tenant_id="tenant-a", user_id="user-a", run_id="run-a")
    )
    consumed, cancellation = await asyncio.gather(consume_task, cancel_task)

    assert consumed is None
    assert cancellation["run_id"] == "run-a"
    assert cancellation["status"] == "cancel_requested"
    assert "_permission_terminalization_progress" not in cancellation
    assert len(conn.terminalizations) == 1
    assert conn.terminalizations[0][-2:] == ("cancelled", "run_cancel_requested")


@pytest.mark.asyncio
async def test_allow_for_run_lookup_loses_a_barrier_synchronized_cancel_race(monkeypatch):
    class ReuseCancelRaceConnection:
        def __init__(self):
            self.lookup_ready = asyncio.Event()
            self.cancel_terminalized = asyncio.Event()
            self.terminalizations = []

        async def execute(self, sql, params):
            normalized = " ".join(sql.split())
            if normalized.startswith("with executable_run as") and "select permission_request.*" in normalized:
                self.lookup_ready.set()
                await self.cancel_terminalized.wait()
                return SingleRowCursor(None)
            if normalized.startswith("with eligible_run as"):
                await self.lookup_ready.wait()
                return SingleRowCursor({"id": "run-a", "status": "running", "trace_id": "trace-a", "cancel_requested_newly": True})
            if normalized.startswith("update runs") and "coalesce(permission_terminalization_target" in normalized:
                return SingleRowCursor({"id": "run-a", "permission_terminalization_target": "cancel_requested"})
            if normalized.startswith("select id, trace_id, status, permission_terminalization_target"):
                return SingleRowCursor(
                    {
                        "id": "run-a",
                        "permission_terminalization_target": "cancel_requested",
                        "permission_terminalization_reason": "run_cancel_requested",
                    }
                )
            if normalized.startswith("with locked_run as"):
                self.terminalizations.append(params)
                self.cancel_terminalized.set()
                return FakeCursor()
            if "has_unterminalized" in normalized:
                return SingleRowCursor({"has_unterminalized": False})
            if normalized.startswith("update runs") and "permission_terminalization_target = null" in normalized:
                return SingleRowCursor({"id": "run-a", "status": "running"})
            raise AssertionError(normalized)

    async def no_active_leases(conn, *, tenant_id, run_id):
        return []

    async def no_op_event_or_audit(*args, **kwargs):
        return None

    monkeypatch.setattr(repositories, "list_active_sandbox_leases_for_run", no_active_leases)
    monkeypatch.setattr(repositories, "append_event", no_op_event_or_audit)
    monkeypatch.setattr(repositories, "append_audit_log", no_op_event_or_audit)
    conn = ReuseCancelRaceConnection()

    reuse_task = asyncio.create_task(
        repositories.get_exact_tool_permission_decision(
            conn,
            tenant_id="tenant-a",
            user_id="user-a",
            run_id="run-a",
            tool_id="claude-sdk:Bash",
            tool_call_id="call-a",
            request_payload_json={"command_sha256": "a" * 64},
        )
    )
    cancel_task = asyncio.create_task(
        repositories.request_run_cancel(conn, tenant_id="tenant-a", user_id="user-a", run_id="run-a")
    )
    reusable_grant, cancellation = await asyncio.gather(reuse_task, cancel_task)

    assert reusable_grant is None
    assert cancellation["run_id"] == "run-a"
    assert cancellation["status"] == "cancel_requested"
    assert "_permission_terminalization_progress" not in cancellation
    assert conn.terminalizations == [
        ("tenant-a", "run-a", "tenant-a", "run-a", None, None, 50, "cancelled", "run_cancel_requested")
    ]


@pytest.mark.asyncio
async def test_terminalization_revokes_decided_authority_and_preserves_its_audit_value(monkeypatch):
    calls = []

    class DecidedGrantConnection:
        async def execute(self, sql, params):
            calls.append(("sql", " ".join(sql.split()), params))
            return SingleRowCursor(
                {
                    "id": "tpr-allow-for-run",
                    "user_id": "user-a",
                    "trace_id": "trace-a",
                    "tool_id": "Bash",
                    "tool_call_id": "call-a",
                    "action": "execute",
                    "risk_level": "high",
                    "write_capable": True,
                    "decision": "allow_for_run",
                }
            )

    async def append_event(conn, **kwargs):
        calls.append(("event", kwargs))

    async def append_audit_log(conn, **kwargs):
        calls.append(("audit", kwargs))

    monkeypatch.setattr(repositories, "append_event", append_event)
    monkeypatch.setattr(repositories, "append_audit_log", append_audit_log)

    rows = await repositories.terminalize_pending_tool_permission_requests(
        DecidedGrantConnection(),
        tenant_id="tenant-a",
        run_id="run-a",
        terminal_status="cancelled",
        terminal_reason="run_cancel_requested",
    )

    assert rows[0]["decision"] == "allow_for_run"
    assert "status in ('pending', 'decided')" in calls[0][1]
    audit = next(entry[1] for entry in calls if entry[0] == "audit")
    assert audit["payload_json"]["decision"] == "allow_for_run"


@pytest.mark.asyncio
async def test_create_and_renew_sandbox_lease_persists_ttl_contract():
    conn = RecordingConnection()

    await create_sandbox_lease(
        conn,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        session_id="session-a",
        run_id="run-a",
        trace_id="trace-a",
        sandbox_mode="ephemeral",
        provider="fake",
        browser_enabled=False,
        ttl_seconds=600,
        resource_limits_json={"max_seconds": 60},
        user_visible_payload_json={"workspace": "/workspace"},
        lease_payload_json={"purpose": "test"},
    )
    await renew_sandbox_lease(
        conn,
        tenant_id="tenant-a",
        user_id="user-a",
        run_id="run-a",
        lease_id="lease-a",
        ttl_seconds=900,
    )

    create_sql, create_params = conn.calls[0]
    renew_sql, renew_params = conn.calls[1]
    assert "sandbox_leases" in create_sql
    assert "now() + (%s * interval '1 second')" in create_sql
    assert 600 in create_params
    assert "status = 'active'" in renew_sql
    assert "(expires_at is null or expires_at > now())" in renew_sql
    assert renew_params == (900, "tenant-a", "user-a", "run-a", "lease-a")


@pytest.mark.asyncio
async def test_cleanup_expired_sandbox_leases_releases_expired_non_runtime_leases_and_emits_events(monkeypatch):
    from app.repositories import cleanup_expired_sandbox_leases

    calls = []

    class ExpiredLeaseCursor:
        async def fetchall(self):
            return [
                {
                    "id": "lease-expired",
                    "tenant_id": "tenant-a",
                    "run_id": "run-a",
                    "trace_id": "trace-lease",
                    "release_reason": "expired",
                }
            ]

    class FakeConnection:
        async def execute(self, sql, params):
            normalized = " ".join(sql.split())
            calls.append((normalized, params))
            if normalized.startswith("update sandbox_leases"):
                return ExpiredLeaseCursor()
            raise AssertionError(f"unexpected sql: {normalized}")

    async def fake_append_event(conn, **kwargs):
        calls.append(("event", kwargs))
        return "evt-a"

    monkeypatch.setattr("app.repositories.append_event", fake_append_event)

    cleaned = await cleanup_expired_sandbox_leases(
        FakeConnection(),
        tenant_id="tenant-a",
        reason="expired",
    )

    assert [item["id"] for item in cleaned] == ["lease-expired"]
    update_sql, update_params = calls[0]
    assert "status = 'released'" in update_sql
    assert "status = 'active'" in update_sql
    assert "expires_at <= now()" in update_sql
    assert "provider not in ('fake', 'docker', 'opensandbox')" in update_sql
    assert update_params == ("expired", "tenant-a", "tenant-a")
    assert calls[1] == (
        "event",
        {
            "tenant_id": "tenant-a",
            "run_id": "run-a",
            "trace_id": "trace-lease",
            "event_type": "sandbox_lease_released",
            "stage": "sandbox",
            "message": "已释放过期 Sandbox 租约",
            "payload": {
                "visible_to_user": True,
                "lease_id": "lease-expired",
                "reason": "expired",
            },
        },
    )


@pytest.mark.asyncio
async def test_cleanup_expired_sandbox_leases_global_scope_emits_events_for_each_tenant(monkeypatch):
    from app.repositories import cleanup_expired_sandbox_leases

    calls = []

    class ExpiredLeaseCursor:
        async def fetchall(self):
            return [
                {
                    "id": "lease-a",
                    "tenant_id": "tenant-a",
                    "run_id": "run-a",
                    "trace_id": "trace-a",
                    "release_reason": "expired",
                },
                {
                    "id": "lease-b",
                    "tenant_id": "tenant-b",
                    "run_id": "run-b",
                    "trace_id": "trace-b",
                    "release_reason": "expired",
                },
            ]

    class FakeConnection:
        async def execute(self, sql, params):
            normalized = " ".join(sql.split())
            calls.append((normalized, params))
            if normalized.startswith("update sandbox_leases"):
                return ExpiredLeaseCursor()
            raise AssertionError(f"unexpected sql: {normalized}")

    async def fake_append_event(conn, **kwargs):
        calls.append(("event", kwargs))
        return f"evt-{kwargs['tenant_id']}"

    monkeypatch.setattr("app.repositories.append_event", fake_append_event)

    cleaned = await cleanup_expired_sandbox_leases(FakeConnection())

    assert [item["id"] for item in cleaned] == ["lease-a", "lease-b"]
    update_sql, update_params = calls[0]
    assert "where (%s::text is null or tenant_id = %s)" in update_sql
    assert update_params == ("expired", None, None)
    assert [call[1]["tenant_id"] for call in calls[1:]] == ["tenant-a", "tenant-b"]
    assert [call[1]["payload"]["lease_id"] for call in calls[1:]] == ["lease-a", "lease-b"]


@pytest.mark.asyncio
async def test_cleanup_expired_multi_agent_dispatch_claims_reclaims_steps_and_writes_audit(monkeypatch):
    calls = []

    class ExpiredClaimCursor:
        async def fetchall(self):
            return [
                {
                    "id": "step-code",
                    "tenant_id": "default",
                    "run_id": "run-ready",
                    "trace_id": "trace-ready",
                    "step_key": "code",
                    "payload_json": {
                        "dispatch_id": "dispatch-code",
                        "dispatch_state": "claimed",
                        "dispatch_lease_expires_at": "2000-01-01T00:00:00+00:00",
                    },
                }
            ]

    class UpdateCursor:
        async def fetchall(self):
            return []

    class FakeConnection:
        async def execute(self, sql, params):
            normalized = " ".join(sql.split())
            calls.append((normalized, params))
            if normalized.startswith("select rs.id"):
                return ExpiredClaimCursor()
            if normalized.startswith("update run_steps"):
                return UpdateCursor()
            raise AssertionError(f"unexpected sql: {normalized}")

    async def fake_append_audit_log(conn, **kwargs):
        calls.append(("audit", kwargs))
        return "aud-expired"

    monkeypatch.setattr("app.repositories.append_audit_log", fake_append_audit_log)

    cleaned = await repositories.cleanup_expired_multi_agent_dispatch_claims(
        FakeConnection(),
        tenant_id="default",
        cleaned_by="admin-a",
        limit=25,
    )

    assert cleaned == [
        {
            "step_id": "step-code",
            "run_id": "run-ready",
            "step_key": "code",
            "dispatch_id": "dispatch-code",
            "status": "pending",
        }
    ]
    select_sql, select_params = calls[0]
    assert "payload_json->>'dispatch_state' = 'claimed'" in select_sql
    assert "payload_json->>'dispatch_lease_expires_at'" in select_sql
    assert "::timestamptz" not in select_sql
    assert "r.status in" not in select_sql
    assert "skip locked" in select_sql
    assert select_params[0] == "default"
    assert select_params[-1] == 25
    update_sql, update_params = calls[1]
    assert "status = 'pending'" in update_sql
    assert "started_at = null" in update_sql
    assert "finished_at = null" in update_sql
    assert "dispatch_state" in update_sql
    assert update_params[1] == "default"
    assert update_params[2] == "step-code"
    assert calls[2] == (
        "audit",
        {
            "tenant_id": "default",
            "user_id": "admin-a",
            "action": "run.multi_agent.dispatch.expire",
            "target_type": "run_step",
            "target_id": "step-code",
            "trace_id": "trace-ready",
            "payload_json": {
                "run_id": "run-ready",
                "step_key": "code",
                "dispatch_id": "dispatch-code",
                "result_status": "expired",
            },
        },
    )


@pytest.mark.asyncio
async def test_cleanup_expired_multi_agent_dispatch_claims_skips_malformed_timestamp_without_audit(monkeypatch):
    calls = []

    class ExpiredClaimCursor:
        async def fetchall(self):
            return [
                {
                    "id": "step-code",
                    "tenant_id": "default",
                    "run_id": "run-ready",
                    "trace_id": "trace-ready",
                    "step_key": "code",
                    "payload_json": {
                        "dispatch_id": "dispatch-code",
                        "dispatch_state": "claimed",
                        "dispatch_lease_expires_at": "2026-99-99Tbad",
                    },
                }
            ]

    class UpdateCursor:
        async def fetchall(self):
            return []

    class FakeConnection:
        async def execute(self, sql, params):
            normalized = " ".join(sql.split())
            calls.append((normalized, params))
            if normalized.startswith("select rs.id"):
                return ExpiredClaimCursor()
            if normalized.startswith("update run_steps"):
                return UpdateCursor()
            raise AssertionError(f"unexpected sql: {normalized}")

    async def fail_append_audit_log(*args, **kwargs):
        raise AssertionError("malformed lease timestamps must not write audit rows")

    monkeypatch.setattr("app.repositories.append_audit_log", fail_append_audit_log)

    cleaned = await repositories.cleanup_expired_multi_agent_dispatch_claims(
        FakeConnection(),
        tenant_id="default",
        cleaned_by="admin-a",
        limit=25,
    )

    assert cleaned == []
    assert len(calls) == 1
    select_sql, select_params = calls[0]
    assert "::timestamptz" not in select_sql
    assert "skip locked" in select_sql
    assert select_params[0] == "default"
    assert select_params[-1] == 25


@pytest.mark.asyncio
async def test_cleanup_expired_multi_agent_dispatch_claims_scans_past_unreclaimable_candidates(monkeypatch):
    calls = []

    class CandidateCursor:
        def __init__(self, rows):
            self.rows = rows

        async def fetchall(self):
            return self.rows

    class UpdateCursor:
        async def fetchall(self):
            return []

    class FakeConnection:
        async def execute(self, sql, params):
            normalized = " ".join(sql.split())
            calls.append((normalized, params))
            if normalized.startswith("select rs.id"):
                seen_ids = set(params[1])
                first_batch = [
                    {
                        "id": "step-malformed",
                        "tenant_id": "default",
                        "run_id": "run-ready",
                        "trace_id": "trace-ready",
                        "step_key": "malformed",
                        "payload_json": {
                            "dispatch_id": "dispatch-malformed",
                            "dispatch_state": "claimed",
                            "dispatch_lease_expires_at": "2026-99-99Tbad",
                        },
                    },
                    {
                        "id": "step-future",
                        "tenant_id": "default",
                        "run_id": "run-ready",
                        "trace_id": "trace-ready",
                        "step_key": "future",
                        "payload_json": {
                            "dispatch_id": "dispatch-future",
                            "dispatch_state": "claimed",
                            "dispatch_lease_expires_at": "2999-01-01T00:00:00+00:00",
                        },
                    },
                ]
                second_batch = [
                    {
                        "id": "step-expired",
                        "tenant_id": "default",
                        "run_id": "run-ready",
                        "trace_id": "trace-ready",
                        "step_key": "expired",
                        "payload_json": {
                            "dispatch_id": "dispatch-expired",
                            "dispatch_state": "claimed",
                            "dispatch_lease_expires_at": "2000-01-01T00:00:00+00:00",
                        },
                    }
                ]
                rows = [row for row in first_batch + second_batch if row["id"] not in seen_ids]
                return CandidateCursor(rows[: params[-1]])
            if normalized.startswith("update run_steps"):
                return UpdateCursor()
            raise AssertionError(f"unexpected sql: {normalized}")

    async def fake_append_audit_log(conn, **kwargs):
        calls.append(("audit", kwargs))
        return "aud-expired"

    monkeypatch.setattr("app.repositories.append_audit_log", fake_append_audit_log)

    cleaned = await repositories.cleanup_expired_multi_agent_dispatch_claims(
        FakeConnection(),
        tenant_id="default",
        cleaned_by="admin-a",
        limit=2,
    )

    assert cleaned == [
        {
            "step_id": "step-expired",
            "run_id": "run-ready",
            "step_key": "expired",
            "dispatch_id": "dispatch-expired",
            "status": "pending",
        }
    ]
    select_calls = [call for call in calls if call[0].startswith("select rs.id")]
    assert len(select_calls) == 2
    assert select_calls[0][1][1] == []
    assert select_calls[1][1][1] == ["step-malformed", "step-future"]


@pytest.mark.asyncio
async def test_list_expired_active_sandbox_leases_preserves_runtime_stop_targets():
    from app.repositories import list_expired_active_sandbox_leases

    class ExpiredLeaseCursor:
        async def fetchall(self):
            return [
                {
                    "id": "lease-docker",
                    "tenant_id": "tenant-a",
                    "run_id": "run-a",
                    "provider": "docker",
                    "status": "active",
                }
            ]

    class FakeConnection:
        def __init__(self):
            self.calls = []

        async def execute(self, sql, params):
            normalized = " ".join(sql.split())
            self.calls.append((normalized, params))
            return ExpiredLeaseCursor()

    conn = FakeConnection()

    rows = await list_expired_active_sandbox_leases(conn, tenant_id="tenant-a", limit=25)

    assert [row["id"] for row in rows] == ["lease-docker"]
    select_sql, select_params = conn.calls[0]
    assert select_sql.startswith("select * from sandbox_leases")
    assert "status = 'active'" in select_sql
    assert "expires_at <= now()" in select_sql
    assert "provider not in" not in select_sql
    assert select_params == ("tenant-a", "tenant-a", 25)


@pytest.mark.asyncio
async def test_release_stopped_sandbox_leases_releases_by_stopped_ids_and_emits_expired_events(monkeypatch):
    from app import repositories

    calls = []

    class LeaseCursor:
        async def fetchall(self):
            return [
                {
                    "id": "lease-a",
                    "tenant_id": "tenant-a",
                    "run_id": "run-a",
                    "trace_id": "trace-a",
                }
            ]

    class FakeConnection:
        async def execute(self, sql, params):
            normalized = " ".join(sql.split())
            calls.append((normalized, params))
            if normalized.startswith("update sandbox_leases"):
                return LeaseCursor()
            raise AssertionError(f"unexpected sql: {normalized}")

    async def fake_append_event(conn, **kwargs):
        calls.append(("event", kwargs))
        return "evt-a"

    monkeypatch.setattr("app.repositories.append_event", fake_append_event)

    released = await repositories.release_stopped_sandbox_leases(
        FakeConnection(),
        tenant_id="tenant-a",
        reason="expired",
        lease_ids=["lease-a"],
    )

    assert [lease["id"] for lease in released] == ["lease-a"]
    update_sql, update_params = calls[0]
    assert "id = any(%s)" in update_sql
    assert "run_id = %s" not in update_sql
    assert update_params == ("expired", "tenant-a", ["lease-a"])
    assert calls[1] == (
        "event",
        {
            "tenant_id": "tenant-a",
            "run_id": "run-a",
            "trace_id": "trace-a",
            "event_type": "sandbox_lease_released",
            "stage": "sandbox",
            "message": "已释放过期 Sandbox 租约",
            "payload": {
                "visible_to_user": True,
                "lease_id": "lease-a",
                "reason": "expired",
            },
        },
    )


@pytest.mark.asyncio
async def test_get_run_identity_can_lock_row_for_callback_race_window():
    conn = RecordingConnection()

    await get_run_identity(conn, run_id="run-a", for_update=True)

    sql, params = conn.calls[0]
    assert sql.endswith("for update")
    assert params == ("run-a",)


@pytest.mark.asyncio
async def test_get_authorized_run_can_lock_row_for_retry_race_window():
    conn = RecordingConnection()

    await repositories.get_authorized_run(
        conn,
        tenant_id="tenant-a",
        user_id="user-a",
        run_id="run-a",
        for_update=True,
    )

    sql, params = conn.calls[0]
    assert sql.endswith("for update of runs")
    assert "join sessions on sessions.id = runs.session_id" in sql
    assert "sessions.status = 'active'" in sql
    assert params == ("tenant-a", "run-a", "user-a")


@pytest.mark.asyncio
async def test_list_multi_agent_dispatch_candidate_runs_filters_running_top_level_multi_agent():
    class CandidateCursor:
        async def fetchall(self):
            return [{"id": "run-a"}, {"id": "run-b"}]

    class CandidateConnection:
        def __init__(self):
            self.calls = []

        async def execute(self, sql, params):
            self.calls.append((" ".join(sql.split()), params))
            return CandidateCursor()

    conn = CandidateConnection()

    result = await list_multi_agent_dispatch_candidate_run_ids(conn, tenant_id="tenant-a", limit=25)

    assert result == ["run-a", "run-b"]
    sql, params = conn.calls[0]
    assert "where tenant_id = %s" in sql
    assert "status = 'running'" in sql
    assert "copied_from_run_id is null" in sql
    assert "input_json#>>'{input,execution_mode}' = 'multi_agent'" in sql
    assert "input_json->>'execution_mode' = 'multi_agent'" in sql
    assert "input_json#>>'{multi_agent_dispatch,orchestration_state}' = 'awaiting_dispatch'" in sql
    assert "input_json#>>'{input,multi_agent_dispatch,orchestration_state}'" not in sql
    assert "updated_at" not in sql
    assert "order by queued_at asc" in sql
    assert "created_at asc" in sql
    assert "id asc" in sql
    assert "limit %s" in sql
    assert params == ("tenant-a", 25)


@pytest.mark.asyncio
async def test_mark_multi_agent_dispatch_parent_awaiting_dispatch_sets_server_owned_marker():
    conn = RecordingConnection()

    await mark_multi_agent_dispatch_parent_awaiting_dispatch(
        conn,
        tenant_id="tenant-a",
        run_id="run-a",
        worker_id="worker-a",
    )

    sql, params = conn.calls[0]
    assert "update runs" in sql
    assert "multi_agent_dispatch" in sql
    assert "updated_at" not in sql
    assert "where tenant_id = %s" in sql
    assert "id = %s" in sql
    payload = json.loads(params[0])
    assert payload["orchestration_state"] == "awaiting_dispatch"
    assert payload["source"] == "worker"
    assert payload["worker_id"] == "worker-a"
    assert params[1:3] == ("tenant-a", "run-a")


@pytest.mark.asyncio
async def test_mark_multi_agent_dispatch_parent_awaiting_dispatch_uses_top_level_server_marker_only():
    conn = RecordingConnection()

    await mark_multi_agent_dispatch_parent_awaiting_dispatch(
        conn,
        tenant_id="tenant-a",
        run_id="run-a",
        worker_id="worker-a",
    )

    sql, params = conn.calls[0]
    assert "'{multi_agent_dispatch}'" in sql
    assert "'{input,multi_agent_dispatch}'" not in sql
    assert sql.count("%s") == len(params)


@pytest.mark.asyncio
async def test_mark_multi_agent_dispatch_enqueue_failed_resets_parent_step_and_stages_child_terminalization():
    class Cursor:
        def __init__(self, row):
            self.row = row

        async def fetchone(self):
            return self.row

    class Connection:
        def __init__(self):
            self.calls = []

        async def execute(self, sql, params):
            normalized = " ".join(sql.split())
            self.calls.append((normalized, params))
            if "update run_steps" in normalized:
                return Cursor({"id": "step-code", "step_key": "code"})
            if "update runs" in normalized:
                return Cursor({"id": "run-child"})
            return Cursor({"id": "evt-or-audit"})

    conn = Connection()

    result = await mark_multi_agent_dispatch_enqueue_failed(
        conn,
        tenant_id="tenant-a",
        parent_run_id="run-parent",
        parent_step_id="step-code",
        dispatch_id="dispatch-code",
        child_run_id="run-child",
        reason="redis down",
        triggered_by="system:multi-agent-dispatcher",
    )

    assert result["parent_step_id"] == "step-code"
    assert result["child_run_id"] == "run-child"
    step_sql, step_params = conn.calls[0]
    child_sql, child_params = conn.calls[1]
    assert "update run_steps" in step_sql
    assert "status = 'pending'" in step_sql
    assert "- 'dispatch_state'" in step_sql
    assert "- 'dispatch_child_run_id'" in step_sql
    assert "payload_json->>'dispatch_state' = 'handed_off'" in step_sql
    assert step_params == ("tenant-a", "run-parent", "step-code", "dispatch-code", "run-child")
    assert child_sql.startswith("select id from runs")
    assert "copied_from_run_id = %s" in child_sql
    assert "for update" in child_sql
    assert child_params == ("tenant-a", "run-child", "run-parent")
    assert any("set permission_terminalization_target" in sql for sql, _params in conn.calls)
    assert not any("set status = 'failed'" in sql for sql, _params in conn.calls)


@pytest.mark.asyncio
async def test_upsert_run_step_merges_existing_payload_on_conflict():
    conn = RecordingConnection()

    await upsert_run_step(
        conn,
        tenant_id="tenant-a",
        run_id="run-a",
        step_key="code",
        step_kind="agent",
        status="succeeded",
        title="coding agent reused checkpoint",
        role="coding",
        sequence=1,
        payload_json={"checkpoint_reused": True, "output": "code output"},
    )

    sql, _params = conn.calls[0]
    assert "payload_json = run_steps.payload_json || excluded.payload_json" in sql


@pytest.mark.asyncio
async def test_admin_run_detail_rejects_missing_run_contract(monkeypatch):
    async def fake_get_run(conn, *, tenant_id, run_id):
        return {
            "id": run_id,
            "session_id": "ses-a",
            "user_id": "user-a",
            "workspace_id": "default",
            "status": "succeeded",
            "agent_id": "general-agent",
            "skill_id": "general-chat",
            "created_at": None,
            "input_json": {},
            "result_json": {},
        }

    monkeypatch.setattr(repositories, "get_run", fake_get_run)

    with pytest.raises(RepositoryConflictError, match="invalid_run_contract"):
        await repositories.get_admin_run_detail(FakeConnection(), tenant_id="tenant-a", run_id="run-a")


@pytest.mark.asyncio
async def test_admin_run_detail_rejects_missing_artifact_manifest_schema(monkeypatch):
    async def fake_get_run(conn, *, tenant_id, run_id):
        return {
            "id": run_id,
            "session_id": "ses-a",
            "user_id": "user-a",
            "workspace_id": "default",
            "status": "succeeded",
            "agent_id": "general-agent",
            "skill_id": "general-chat",
            "created_at": None,
            "input_json": {},
            "result_json": {},
            "schema_version": "ai-platform.run.v1",
            "executor_schema_version": "ai-platform.executor-result.v1",
        }

    async def fake_empty_list(*args, **kwargs):
        return []

    async def fake_list_run_artifacts(conn, *, tenant_id, run_id):
        return [
            {
                "id": "art-a",
                "trace_id": "trace-a",
                "artifact_type": "reviewed_docx",
                "label": "Reviewed",
                "content_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                "size_bytes": 10,
                "manifest_version": None,
                "manifest_json": {},
                "created_at": None,
            }
        ]

    monkeypatch.setattr(repositories, "get_run", fake_get_run)
    monkeypatch.setattr(repositories, "list_run_events", fake_empty_list)
    monkeypatch.setattr(repositories, "list_run_steps", fake_empty_list)
    monkeypatch.setattr(repositories, "list_run_artifacts", fake_list_run_artifacts)
    monkeypatch.setattr(repositories, "list_run_skill_snapshots", fake_empty_list)

    with pytest.raises(RepositoryConflictError, match="invalid_artifact_manifest_schema_version"):
        await repositories.get_admin_run_detail(FakeConnection(), tenant_id="tenant-a", run_id="run-a")


@pytest.mark.asyncio
async def test_admin_run_detail_rejects_missing_audit_schema(monkeypatch):
    async def fake_get_run(conn, *, tenant_id, run_id):
        return {
            "id": run_id,
            "session_id": "ses-a",
            "user_id": "user-a",
            "workspace_id": "default",
            "status": "succeeded",
            "agent_id": "general-agent",
            "skill_id": "general-chat",
            "created_at": None,
            "input_json": {},
            "result_json": {},
            "schema_version": "ai-platform.run.v1",
            "executor_schema_version": "ai-platform.executor-result.v1",
        }

    async def fake_empty_list(*args, **kwargs):
        return []

    class AuditCursor:
        async def fetchall(self):
            return [
                {
                    "id": "aud-a",
                    "user_id": "admin-a",
                    "action": "admin_run_viewed",
                    "target_type": "run",
                    "target_id": "run-a",
                    "trace_id": "trace-a",
                    "schema_version": None,
                    "payload_json": {"run_id": "run-a"},
                    "created_at": None,
                }
            ]

    class EmptyListCursor:
        async def fetchall(self):
            return []

    class AuditConnection:
        async def execute(self, sql, params):
            if "from sandbox_leases" in " ".join(sql.split()):
                return EmptyListCursor()
            return AuditCursor()

    monkeypatch.setattr(repositories, "get_run", fake_get_run)
    monkeypatch.setattr(repositories, "list_run_events", fake_empty_list)
    monkeypatch.setattr(repositories, "list_run_steps", fake_empty_list)
    monkeypatch.setattr(repositories, "list_run_artifacts", fake_empty_list)
    monkeypatch.setattr(repositories, "list_run_skill_snapshots", fake_empty_list)

    with pytest.raises(RepositoryConflictError, match="invalid_audit_event_schema_version"):
        await repositories.get_admin_run_detail(AuditConnection(), tenant_id="tenant-a", run_id="run-a")


@pytest.mark.asyncio
async def test_append_audit_log_accepts_trace_context():
    conn = RecordingConnection()

    await append_audit_log(
        conn,
        tenant_id="tenant-a",
        user_id="admin-a",
        action="admin_artifact_downloaded",
        target_type="artifact",
        target_id="art-a",
        trace_id="trace_a",
        payload_json={"run_id": "run-a"},
    )

    sql, params = conn.calls[0]
    assert "trace_id" in sql
    assert "schema_version" in sql
    assert "trace_a" in params
    assert "ai-platform.audit-event.v1" in params


@pytest.mark.asyncio
async def test_list_admin_tool_policy_history_uses_bounded_tenant_scoped_audit_query():
    class HistoryCursor:
        async def fetchall(self):
            return [{"id": "aud-policy", "target_id": "ragflow-knowledge-search"}]

    class HistoryConnection:
        def __init__(self):
            self.calls = []

        async def execute(self, sql, params):
            self.calls.append((" ".join(sql.split()), params))
            return HistoryCursor()

    conn = HistoryConnection()

    rows = await repositories.list_admin_tool_policy_history(
        conn,
        tenant_id="tenant-a",
        tool_id="ragflow-knowledge-search",
        limit=25,
    )

    assert rows == [{"id": "aud-policy", "target_id": "ragflow-knowledge-search"}]
    sql, params = conn.calls[0]
    assert "from audit_logs" in sql
    assert "tenant_id = %s" in sql
    assert "target_type = %s" in sql
    assert "action = %s" in sql
    assert "target_id = %s" in sql
    assert "limit %s" in sql.lower()
    assert params == (
        "tenant-a",
        "tool_policy",
        "admin.tool_policy.updated",
        "ragflow-knowledge-search",
        25,
    )


@pytest.mark.asyncio
async def test_list_admin_tool_policy_history_clamps_limit_for_direct_callers():
    class HistoryCursor:
        async def fetchall(self):
            return []

    class HistoryConnection:
        def __init__(self):
            self.calls = []

        async def execute(self, sql, params):
            self.calls.append((" ".join(sql.split()), params))
            return HistoryCursor()

    conn = HistoryConnection()

    await repositories.list_admin_tool_policy_history(
        conn,
        tenant_id="tenant-a",
        tool_id=None,
        limit=9999,
    )
    await repositories.list_admin_tool_policy_history(
        conn,
        tenant_id="tenant-a",
        tool_id=None,
        limit=-5,
    )
    await repositories.list_admin_tool_policy_history(
        conn,
        tenant_id="tenant-a",
        tool_id=None,
        limit=0,
    )

    assert conn.calls[0][1] == ("tenant-a", "tool_policy", "admin.tool_policy.updated", 500)
    assert conn.calls[1][1] == ("tenant-a", "tool_policy", "admin.tool_policy.updated", 1)
    assert conn.calls[2][1] == ("tenant-a", "tool_policy", "admin.tool_policy.updated", 1)


@pytest.mark.asyncio
async def test_list_role_governance_audit_history_uses_bounded_tenant_scoped_query():
    class RoleGovernanceHistoryCursor:
        async def fetchall(self):
            return [{"id": "aud-role", "target_id": "skill_developer"}]

    class RoleGovernanceHistoryConnection:
        def __init__(self):
            self.calls = []

        async def execute(self, sql, params):
            self.calls.append((" ".join(sql.split()), params))
            return RoleGovernanceHistoryCursor()

    conn = RoleGovernanceHistoryConnection()

    rows = await repositories.list_role_governance_audit_history(
        conn,
        tenant_id="tenant-a",
        user_id="ordinary",
        limit=10,
    )

    assert rows == [{"id": "aud-role", "target_id": "skill_developer"}]
    sql, params = conn.calls[0]
    assert "from audit_logs" in sql
    assert "tenant_id = %s" in sql
    assert "action = any(%s)" in sql
    assert "user_id = %s or payload_json->>'requester_id' = %s" in sql
    assert "order by created_at desc, id desc" in sql
    assert "limit %s" in sql.lower()
    assert params == (
        "tenant-a",
        [
            "role_governance.request.created",
            "role_governance.approval.approve_requested",
            "role_governance.approval.reject_requested",
            "role_governance.rollback.requested",
        ],
        "ordinary",
        "ordinary",
        10,
    )


@pytest.mark.asyncio
async def test_list_role_governance_audit_history_clamps_limit_for_direct_callers():
    class RoleGovernanceHistoryCursor:
        async def fetchall(self):
            return []

    class RoleGovernanceHistoryConnection:
        def __init__(self):
            self.calls = []

        async def execute(self, sql, params):
            self.calls.append((" ".join(sql.split()), params))
            return RoleGovernanceHistoryCursor()

    conn = RoleGovernanceHistoryConnection()

    await repositories.list_role_governance_audit_history(conn, tenant_id="tenant-a", limit=9999)
    await repositories.list_role_governance_audit_history(conn, tenant_id="tenant-a", limit=-5)
    await repositories.list_role_governance_audit_history(conn, tenant_id="tenant-a", limit=0)

    assert conn.calls[0][1][-1] == 100
    assert conn.calls[1][1][-1] == 1
    assert conn.calls[2][1][-1] == 1


@pytest.mark.asyncio
async def test_resolve_agent_skill_uses_tenant_stable_release_policy():
    class ResolveCursor:
        async def fetchone(self):
            return {
                "agent_id": "qa-word-review",
                "agent_status": "active",
                "default_skill_id": "qa-file-reviewer",
                "skill_id": "qa-file-reviewer",
                "skill_status": "active",
                "skill_version": "hash-release",
                "release_policy_version": "hash-release",
                "release_policy_previous_version": "hash-previous",
                "release_policy_rollout_percent": 50,
                "executor_type": "claude-agent-worker",
                "mcp_tool_status": None,
                "input_modes": ["docx"],
            }

    class ResolveConnection:
        def __init__(self):
            self.sql = ""
            self.params = None

        async def execute(self, sql, params):
            self.sql = " ".join(sql.split())
            self.params = params
            return ResolveCursor()

    conn = ResolveConnection()

    row = await repositories.resolve_agent_skill(
        conn,
        tenant_id="default",
        agent_id="qa-word-review",
        skill_id="qa-file-reviewer",
    )

    assert "left join skill_release_policies" in conn.sql
    assert "coalesce(skill_release_policies.current_version, skills.version) as skill_version" in conn.sql
    assert "skill_release_policies.current_version as release_policy_version" in conn.sql
    assert "skill_release_policies.previous_version as release_policy_previous_version" in conn.sql
    assert "skill_release_policies.rollout_percent as release_policy_rollout_percent" in conn.sql
    assert "skill_release_policies.channel = 'stable'" in conn.sql
    assert conn.params == ("qa-file-reviewer", "default", "qa-word-review")
    assert row["skill_version"] == "hash-release"
    assert row["release_policy_version"] == "hash-release"
    assert row["release_policy_previous_version"] == "hash-previous"
    assert row["release_policy_rollout_percent"] == 50


@pytest.mark.asyncio
@pytest.mark.parametrize("version_status", ["draft", "reviewed", "disabled", "deprecated"])
async def test_resolve_agent_skill_rejects_unreleased_policy_version(version_status):
    class ResolveCursor:
        async def fetchone(self):
            return {
                "agent_id": "qa-word-review",
                "agent_status": "active",
                "default_skill_id": "qa-file-reviewer",
                "skill_id": "qa-file-reviewer",
                "skill_status": "active",
                "skill_version": "hash-release",
                "skill_version_status": version_status,
                "release_policy_version": "hash-release",
                "release_policy_previous_version": None,
                "release_policy_rollout_percent": 100,
                "executor_type": "claude-agent-worker",
                "mcp_tool_status": None,
                "input_modes": ["docx"],
            }

    class ResolveConnection:
        def __init__(self):
            self.sql = ""
            self.params = None

        async def execute(self, sql, params):
            self.sql = " ".join(sql.split())
            self.params = params
            return ResolveCursor()

    conn = ResolveConnection()

    with pytest.raises(RepositoryConflictError, match="skill_version_not_released"):
        await repositories.resolve_agent_skill(
            conn,
            tenant_id="default",
            agent_id="qa-word-review",
            skill_id="qa-file-reviewer",
        )

    assert "coalesce(skill_versions.status, 'active') as skill_version_status" in conn.sql


@pytest.mark.asyncio
async def test_resolve_agent_skill_rejects_embedded_poco_executor_fact_source():
    class ResolveCursor:
        async def fetchone(self):
            return {
                "agent_id": "general-agent",
                "agent_status": "active",
                "default_skill_id": "general-chat",
                "skill_id": "general-chat",
                "skill_status": "active",
                "skill_version": "0.1.0",
                "release_policy_version": None,
                "executor_type": "embedded-poco-kernel",
                "mcp_tool_status": None,
                "input_modes": ["chat"],
            }

    class ResolveConnection:
        async def execute(self, sql, params):
            return ResolveCursor()

    with pytest.raises(repositories.RepositoryConflictError, match="executor_type_not_allowed"):
        await repositories.resolve_agent_skill(
            ResolveConnection(),
            tenant_id="default",
            agent_id="general-agent",
            skill_id="general-chat",
        )


@pytest.mark.asyncio
async def test_upsert_run_skill_snapshot_is_tenant_and_run_scoped():
    conn = RecordingConnection()

    await repositories.upsert_run_skill_snapshot(
        conn,
        tenant_id="default",
        run_id="run-a",
        skill_id="qa-file-reviewer",
        skill_version="hash-a",
        content_hash="hash-a",
        source_json={"kind": "builtin"},
        dependency_ids=["minimax-docx"],
        allowed=True,
        staged=True,
        used=True,
        used_skills_source="executor_hook",
        inferred_used=False,
    )

    sql, params = conn.calls[0]
    assert "insert into run_skill_snapshots" in sql
    assert "on conflict (tenant_id, run_id, skill_id)" in sql
    assert params[0].startswith("rss_")
    assert params[1:5] == ("default", "run-a", "qa-file-reviewer", "hash-a")
    assert any('"kind": "builtin"' in str(item) for item in params)
    assert any("minimax-docx" in str(item) for item in params)
    assert "used_skills_source" in sql
    assert "inferred_used" in sql
    assert "executor_hook" in params
    assert False in params


@pytest.mark.asyncio
async def test_upsert_run_skill_snapshot_preserves_immutable_provenance_identity():
    conn = RecordingConnection()

    await repositories.upsert_run_skill_snapshot(
        conn,
        tenant_id="tenant-a",
        run_id="run-a",
        skill_id="department-review",
        skill_version="hash-v1",
        content_hash="hash-v1",
        source_json={"kind": "uploaded", "snapshot_governance": {"schema_version": "v1"}},
        dependency_ids=["dependency-a"],
        allowed=True,
        staged=True,
        used=False,
    )

    sql, _params = conn.calls[0]
    update_clause = sql.split("do update set", 1)[1].split("where", 1)[0]
    assert "skill_version" not in update_clause
    assert "content_hash" not in update_clause
    assert "source_json" not in update_clause
    assert "dependency_ids" not in update_clause
    assert "run_skill_snapshots.skill_version = excluded.skill_version" in sql
    assert "run_skill_snapshots.content_hash = excluded.content_hash" in sql
    assert "run_skill_snapshots.source_json = excluded.source_json" in sql
    assert "run_skill_snapshots.dependency_ids = excluded.dependency_ids" in sql
    assert "returning id" in sql


@pytest.mark.asyncio
async def test_upsert_run_skill_snapshot_fails_closed_on_immutable_identity_mismatch():
    class ConflictCursor:
        async def fetchone(self):
            return None

    class ConflictConnection(RecordingConnection):
        async def execute(self, sql, params):
            self.calls.append((" ".join(sql.split()), params))
            return ConflictCursor()

    with pytest.raises(RepositoryConflictError, match="run_skill_snapshot_identity_mismatch"):
        await repositories.upsert_run_skill_snapshot(
            ConflictConnection(),
            tenant_id="tenant-a",
            run_id="run-a",
            skill_id="department-review",
            skill_version="hash-v2",
            content_hash="hash-v2",
            source_json={"kind": "uploaded"},
            dependency_ids=[],
            allowed=True,
            staged=True,
            used=False,
        )


@pytest.mark.asyncio
async def test_insert_run_skill_snapshots_at_creation_is_insert_only_and_exact():
    conn = RecordingConnection()
    manifests = [
        {
            "skill_id": "department-review",
            "version": "hash-v1",
            "content_hash": "hash-v1",
            "source": {"kind": "uploaded", "storage_key": "must-not-persist"},
            "files": [{"relative_path": "SKILL.md", "content_base64": "c2tpbGw=", "size_bytes": 5}],
            "dependency_ids": ["dependency-a"],
            "mcp_tool_ids": [],
            "snapshot_governance": {
                "schema_version": "ai-platform.skill-pinned-snapshot-governance.v1",
                "selected_files": [{"relative_path": "SKILL.md", "size_bytes": 5, "sha256": "hash-file"}],
            },
            "allowed": True,
            "staged": False,
            "used": False,
        }
    ]

    await repositories.insert_run_skill_snapshots_at_creation(
        conn,
        tenant_id="tenant-a",
        run_id="run-a",
        skill_manifests=manifests,
        release_decision={"selected_version": "hash-v1", "selected_track": "current"},
    )

    sql, params = conn.calls[0]
    assert "insert into run_skill_snapshots" in sql
    assert "on conflict (tenant_id, run_id, skill_id) do nothing" in sql
    assert "returning id" in sql
    assert params[1:6] == ("tenant-a", "run-a", "department-review", "hash-v1", "hash-v1")
    serialized_params = str(params)
    assert "dependency-a" in serialized_params
    assert "snapshot_governance" in serialized_params
    assert "content_base64" not in serialized_params
    assert "storage_key" not in serialized_params


@pytest.mark.asyncio
async def test_insert_run_skill_snapshots_allows_dependency_manifest_without_execution_mcp_pin():
    conn = RecordingConnection()
    primary = {
        "skill_id": "department-review",
        "version": "hash-v1",
        "content_hash": "hash-v1",
        "source": {"kind": "uploaded"},
        "files": [{"relative_path": "SKILL.md", "content_base64": "c2tpbGw=", "size_bytes": 5}],
        "dependency_ids": ["document-helper"],
        "mcp_tool_ids": [],
    }
    dependency = {
        "skill_id": "document-helper",
        "version": "hash-helper",
        "content_hash": "hash-helper",
        "source": {"kind": "builtin", "asset_dir": "document-helper"},
        "files": [{"relative_path": "SKILL.md", "content_base64": "c2tpbGw=", "size_bytes": 5}],
        "dependency_ids": [],
    }

    await repositories.insert_run_skill_snapshots_at_creation(
        conn,
        tenant_id="tenant-a",
        run_id="run-a",
        skill_manifests=[primary, dependency],
        release_decision={"selected_version": "hash-v1", "selected_track": "current"},
    )

    assert len(conn.calls) == 2
    dependency_source = json.loads(conn.calls[1][1][6])
    assert dependency_source["mcp_tool_ids"] == []


@pytest.mark.asyncio
async def test_insert_run_skill_snapshots_at_creation_rejects_non_materializable_identity():
    with pytest.raises(RepositoryConflictError, match="run_skill_snapshot_identity_mismatch"):
        await repositories.insert_run_skill_snapshots_at_creation(
            RecordingConnection(),
            tenant_id="tenant-a",
            run_id="run-a",
            skill_manifests=[
                {
                    "skill_id": "department-review",
                    "version": "hash-v1",
                    "content_hash": "different-hash",
                    "source": {"kind": "uploaded"},
                    "files": [{"relative_path": "SKILL.md", "content_base64": "c2tpbGw=", "size_bytes": 5}],
                    "dependency_ids": [],
                    "mcp_tool_ids": [],
                }
            ],
            release_decision={"selected_version": "hash-v1", "selected_track": "current"},
        )


@pytest.mark.asyncio
async def test_authorize_files_for_run_locks_and_validates_without_writing():
    class FileCursor:
        async def fetchone(self):
            return {
                "id": "file-a",
                "tenant_id": "tenant-a",
                "workspace_id": "workspace-a",
                "user_id": "user-a",
                "session_id": None,
                "run_id": None,
            }

    class FileConnection(RecordingConnection):
        async def execute(self, sql, params):
            self.calls.append((" ".join(sql.split()), params))
            return FileCursor()

    conn = FileConnection()
    await repositories.authorize_files_for_run(
        conn,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        session_id="session-a",
        run_id="run-a",
        file_ids=["file-a"],
    )

    assert len(conn.calls) == 1
    sql, params = conn.calls[0]
    assert "select id, tenant_id, workspace_id, user_id, session_id, run_id" in sql
    assert "for update" in sql
    assert "update files" not in sql
    assert params == ("file-a",)


@pytest.mark.asyncio
async def test_list_run_skill_snapshots_projects_persisted_telemetry():
    class SnapshotCursor:
        async def fetchall(self):
            return [
                {
                    "skill_id": "qa-file-reviewer",
                    "skill_version": "hash-a",
                    "content_hash": "hash-a",
                    "source_json": {
                        "kind": "builtin",
                        "version": "hash-a",
                        "snapshot_governance": {
                            "schema_version": "ai-platform.skill-pinned-snapshot-governance.v1",
                            "snapshot_source": "platform_release_lock",
                            "release_lock": {
                                "mode": "manifest_pin",
                                "release_decision": {"selected_version": "hash-a"},
                                "selected_version": "hash-a",
                                "track": "manifest_pin",
                                "rollout": 100,
                            },
                            "manifest": {
                                "digest": "hash-a",
                                "source_kind": "builtin",
                                "selected_file_count": 1,
                                "content_hash": "hash-a",
                            },
                            "selected_files": [
                                {
                                    "relative_path": "SKILL.md",
                                    "size_bytes": 5,
                                    "sha256": "9c53c074d7ac6a2728b638ac1f376c5fa9eb8f71603017c3ea638c2fd40548df",
                                    "content_base64": "c2tpbGw=",
                                }
                            ],
                            "dependency_evidence": {
                                "status": "review_required",
                                "ref": "skill_dependency_policy",
                                "dependency_count": 1,
                            },
                            "does_not_close_b4_or_211": True,
                            "storage_key": "tenants/default/private/package.zip",
                        },
                    },
                    "dependency_ids": ["minimax-docx"],
                    "allowed": True,
                    "staged": True,
                    "used": False,
                    "used_skills_source": "inferred",
                    "inferred_used": True,
                    "created_at": None,
                }
            ]

    class SnapshotConnection:
        async def execute(self, sql, params):
            assert "used_skills_source" in sql
            assert "inferred_used" in sql
            assert params == ("default", "run-a")
            return SnapshotCursor()

    snapshots = await repositories.list_run_skill_snapshots(
        SnapshotConnection(),
        tenant_id="default",
        run_id="run-a",
    )

    assert snapshots == [
        {
            "skill_id": "qa-file-reviewer",
            "skill_version": "hash-a",
            "content_hash": "hash-a",
            "source": {
                "kind": "builtin",
                "snapshot_governance": {
                    "schema_version": "ai-platform.skill-pinned-snapshot-governance.v1",
                    "snapshot_source": "platform_release_lock",
                    "release_lock": {"mode": "manifest_pin"},
                    "manifest": {
                        "source_kind": "builtin",
                        "selected_file_count": 1,
                    },
                    "selected_files": [
                        {
                            "relative_path": "SKILL.md",
                            "size_bytes": 5,
                            "sha256": "9c53c074d7ac6a2728b638ac1f376c5fa9eb8f71603017c3ea638c2fd40548df",
                        }
                    ],
                    "dependency_evidence": {
                        "status": "review_required",
                        "ref": "skill_dependency_policy",
                        "dependency_count": 1,
                    },
                    "does_not_close_b4_or_211": True,
                },
            },
            "dependency_ids": ["minimax-docx"],
            "allowed": True,
            "staged": True,
            "used": False,
            "created_at": None,
            "usage": {
                "used_skills_source": "inferred",
                "inferred_used": True,
                "inferred_used_skills": ["qa-file-reviewer"],
            },
        }
    ]
    serialized = json.dumps(snapshots, ensure_ascii=False)
    assert snapshots[0]["skill_version"] == "hash-a"
    assert snapshots[0]["content_hash"] == "hash-a"
    assert "content_base64" not in serialized
    assert "storage_key" not in serialized
    assert "hash-a" not in json.dumps(snapshots[0]["source"], ensure_ascii=False)
    assert "version" not in snapshots[0]["source"]
    assert "track" not in serialized
    assert "rollout" not in serialized


@pytest.mark.asyncio
async def test_update_run_input_execution_snapshot_atomically_replaces_canonical_fields():
    conn = RecordingConnection()
    execution_snapshot = repositories.copied_run_execution_snapshot(
        {
            "tenant_id": "must-not-project",
            "file_ids": ["file-a"],
            "input": {"message": "review"},
            "executor_type": "claude-agent-worker",
            "skill_version": "hash-current",
            "release_decision": {
                "schema_version": "ai-platform.skill-release-decision.v1",
                "policy_active": False,
                "selected_version": "hash-current",
                "selected_track": "manifest_pin",
            },
            "skill_manifests": [
                {
                    "skill_id": "qa-file-reviewer",
                    "content_hash": "hash-current",
                    "source": {"kind": "builtin", "asset_dir": "qa-file-reviewer"},
                }
            ],
            "context_snapshot_id": "ctx-current",
            "context_snapshot": {"context_snapshot_id": "ctx-current", "source": "copy_run"},
            "model_id": "model-catalog-a",
            "model_value": "provider-model-a",
            "schema_version": "ai-platform.run-payload.v1",
        }
    )

    await repositories.update_run_input_execution_snapshot(
        conn,
        tenant_id="default",
        run_id="run-a",
        execution_snapshot=execution_snapshot,
    )

    sql, params = conn.calls[0]
    assert "update runs" in sql
    assert "coalesce(input_json, '{}'::jsonb) || %s::jsonb" in sql
    assert "tenant_id = %s and id = %s" in sql
    assert params == (
        json.dumps(execution_snapshot, ensure_ascii=False),
        "default",
        "run-a",
        json.dumps(execution_snapshot, ensure_ascii=False),
        json.dumps(execution_snapshot, ensure_ascii=False),
        json.dumps(execution_snapshot, ensure_ascii=False),
        json.dumps(execution_snapshot, ensure_ascii=False),
    )


@pytest.mark.asyncio
async def test_update_run_input_execution_snapshot_explicitly_replaces_null_and_empty_values():
    conn = RecordingConnection()
    execution_snapshot = repositories.copied_run_execution_snapshot(
        {
            "input": {},
            "executor_type": "claude-agent-worker",
            "skill_version": None,
            "release_decision": {},
            "skill_manifests": [],
            "context_snapshot_id": None,
            "context_snapshot": {},
            "model_id": None,
            "model_value": None,
        }
    )

    await repositories.update_run_input_execution_snapshot(
        conn,
        tenant_id="tenant-a",
        run_id="run-empty",
        execution_snapshot=execution_snapshot,
    )

    assert len(conn.calls) == 1
    _, params = conn.calls[0]
    assert params == (
        json.dumps(execution_snapshot, ensure_ascii=False),
        "tenant-a",
        "run-empty",
        json.dumps(execution_snapshot, ensure_ascii=False),
        json.dumps(execution_snapshot, ensure_ascii=False),
        json.dumps(execution_snapshot, ensure_ascii=False),
        json.dumps(execution_snapshot, ensure_ascii=False),
    )


def test_copied_run_execution_snapshot_audits_all_queue_non_identity_fields():
    snapshot = repositories.copied_run_execution_snapshot(
        {
            "tenant_id": "must-not-project",
            "run_id": "must-not-project",
            "file_ids": ["file-a"],
            "input": {"message": "copy"},
            "executor_type": "claude-agent-worker",
            "skill_version": "hash-a",
            "release_decision": {"selected_version": "hash-a"},
            "skill_manifests": [{"skill_id": "general-chat", "content_hash": "hash-a"}],
            "context_snapshot_id": "ctx-a",
            "context_snapshot": {"context_snapshot_id": "ctx-a"},
            "model_id": "model-catalog-a",
            "model_value": "provider-model-a",
            "schema_version": "ai-platform.run-payload.v1",
            "unrelated": "preserve-outside-projection",
        }
    )

    assert snapshot == {
        "file_ids": ["file-a"],
        "input": {"message": "copy"},
        "executor_type": "claude-agent-worker",
        "skill_version": "hash-a",
        "release_decision": {"selected_version": "hash-a"},
        "skill_manifests": [{"skill_id": "general-chat", "content_hash": "hash-a"}],
        "context_snapshot_id": "ctx-a",
        "context_snapshot": {"context_snapshot_id": "ctx-a"},
        "model_id": "model-catalog-a",
        "model_value": "provider-model-a",
        "schema_version": "ai-platform.run-payload.v1",
    }


@pytest.mark.asyncio
async def test_upsert_skill_version_records_immutable_catalog_version():
    conn = RecordingConnection()

    await repositories.upsert_skill_version(
        conn,
        skill_id="qa-file-reviewer",
        version="hash-a",
        content_hash="hash-a",
        description="QA review",
        source_json={"kind": "builtin"},
        dependency_ids=["minimax-docx"],
        status="active",
        created_by="admin-a",
    )

    sql, params = conn.calls[0]
    assert "insert into skill_versions" in sql
    assert "on conflict (skill_id, version)" in sql
    assert "do nothing" in sql
    assert "returning skill_id" in sql
    assert "do update set" not in sql
    assert "content_hash = excluded.content_hash" not in sql
    assert params[0].startswith("skv_")
    assert params[1:4] == ("qa-file-reviewer", "hash-a", "hash-a")
    assert '"kind": "builtin"' in str(params)
    assert "minimax-docx" in str(params)


@pytest.mark.asyncio
async def test_upsert_skill_version_reports_conflict_when_insert_skipped():
    class ConflictCursor:
        async def fetchone(self):
            return None

    class ConflictConnection(RecordingConnection):
        async def execute(self, sql, params):
            self.calls.append((" ".join(sql.split()), params))
            return ConflictCursor()

    conn = ConflictConnection()

    inserted = await repositories.upsert_skill_version(
        conn,
        skill_id="qa-file-reviewer",
        version="hash-a",
        content_hash="hash-a",
        description="QA review",
        source_json={"kind": "uploaded"},
        dependency_ids=["minimax-docx"],
        status="draft",
        created_by="admin-a",
    )

    assert inserted is False


@pytest.mark.asyncio
async def test_create_skill_catalog_is_insert_only_and_reports_conflict():
    class ConflictCursor:
        async def fetchone(self):
            return None

    class ConflictConnection(RecordingConnection):
        async def execute(self, sql, params):
            self.calls.append((" ".join(sql.split()), params))
            return ConflictCursor()

    conn = ConflictConnection()

    with pytest.raises(RepositoryConflictError) as exc_info:
        await repositories.create_skill_catalog(
            conn,
            skill_id="new-research-skill",
            name="New Research Skill",
            version="hash-new",
            description="Summarize research briefs.",
            input_modes=["chat"],
            output_modes=["answer"],
            executor_type="claude-agent-worker",
            status="active",
        )

    sql, params = conn.calls[0]
    assert str(exc_info.value) == "skill_catalog_already_exists"
    assert "insert into skills" in sql
    assert "on conflict (id) do nothing" in sql
    assert "do update" not in sql
    assert "returning id" in sql
    assert params[0:5] == (
        "new-research-skill",
        "New Research Skill",
        "hash-new",
        "Summarize research briefs.",
        '["chat"]',
    )
    assert params[5:8] == ('["answer"]', "claude-agent-worker", "active")


@pytest.mark.asyncio
async def test_update_skill_catalog_version_updates_current_skill_pointer():
    conn = RecordingConnection()

    await repositories.update_skill_catalog_version(
        conn,
        skill_id="qa-file-reviewer",
        version="hash-current",
        description="Current QA review",
    )

    sql, params = conn.calls[0]
    assert "update skills" in sql
    assert "set version = %s" in sql
    assert "where id = %s" in sql
    assert params == ("hash-current", "Current QA review", "qa-file-reviewer")


@pytest.mark.asyncio
async def test_user_skill_file_overlay_repository_contracts():
    class OverlayCursor:
        def __init__(self, *, row=None, rows=None):
            self.row = row or {
                "skill_id": "qa-file-reviewer",
                "file_path": "SKILL.md",
                "content_base64": "dXBkYXRlZA==",
                "size_bytes": 7,
                "status": "active",
            }
            self.rows = rows or [self.row]

        async def fetchone(self):
            return self.row

        async def fetchall(self):
            return self.rows

    class OverlayConnection:
        def __init__(self):
            self.calls = []

        async def execute(self, sql, params):
            compact = " ".join(sql.split())
            self.calls.append((compact, params))
            return OverlayCursor()

    conn = OverlayConnection()

    overlays = await repositories.list_user_skill_file_overlays(
        conn,
        tenant_id="default",
        user_id="ordinary",
        skill_ids=["qa-file-reviewer"],
        include_content=True,
    )
    upserted = await repositories.upsert_user_skill_file(
        conn,
        tenant_id="default",
        user_id="ordinary",
        skill_id="qa-file-reviewer",
        file_path="SKILL.md",
        content_base64="dXBkYXRlZA==",
        size_bytes=7,
    )
    deleted = await repositories.delete_user_skill_file(
        conn,
        tenant_id="default",
        user_id="ordinary",
        skill_id="qa-file-reviewer",
        file_path="SKILL.md",
    )

    list_sql, list_params = conn.calls[0]
    assert "from user_skill_files" in list_sql
    assert "content_base64" in list_sql
    assert "skill_id = any(%s)" in list_sql
    assert "status in ('active', 'deleted')" in list_sql
    assert list_params == ("default", "ordinary", ["qa-file-reviewer"])
    assert overlays[0]["file_path"] == "SKILL.md"

    upsert_sql, upsert_params = conn.calls[1]
    assert "insert into user_skill_files" in upsert_sql
    assert "on conflict (tenant_id, user_id, skill_id, file_path)" in upsert_sql
    assert "status = 'active'" in upsert_sql
    assert upsert_params[0].startswith("usf_")
    assert upsert_params[1:7] == (
        "default",
        "ordinary",
        "qa-file-reviewer",
        "SKILL.md",
        "dXBkYXRlZA==",
        7,
    )
    assert upserted["status"] == "active"

    delete_sql, delete_params = conn.calls[2]
    assert "insert into user_skill_files" in delete_sql
    assert "status = 'deleted'" in delete_sql
    assert "content_base64 = ''" in delete_sql
    assert delete_params[0].startswith("usf_")
    assert delete_params[1:5] == ("default", "ordinary", "qa-file-reviewer", "SKILL.md")
    assert deleted["file_path"] == "SKILL.md"


@pytest.mark.asyncio
async def test_user_skill_file_overlay_list_can_omit_content_for_catalog_projection():
    class OverlayCursor:
        async def fetchall(self):
            return [
                {
                    "skill_id": "qa-file-reviewer",
                    "file_path": "SKILL.md",
                    "content_base64": "",
                    "size_bytes": 7,
                    "status": "active",
                }
            ]

    class OverlayConnection:
        def __init__(self):
            self.sql = ""
            self.params = None

        async def execute(self, sql, params):
            self.sql = " ".join(sql.split())
            self.params = params
            return OverlayCursor()

    conn = OverlayConnection()

    overlays = await repositories.list_user_skill_file_overlays(
        conn,
        tenant_id="default",
        user_id="ordinary",
        skill_ids=["qa-file-reviewer"],
    )

    assert "'' as content_base64" in conn.sql
    assert "content_base64," not in conn.sql
    assert conn.params == ("default", "ordinary", ["qa-file-reviewer"])
    assert overlays[0]["content_base64"] == ""


@pytest.mark.asyncio
async def test_backfill_builtin_skill_version_snapshot_only_updates_incomplete_builtin_rows():
    conn = RecordingConnection()

    await repositories.backfill_builtin_skill_version_snapshot(
        conn,
        skill_id="qa-file-reviewer",
        version="hash-current",
        source_json={
            "kind": "builtin",
            "asset_dir": "qa-file-reviewer",
            "version": "hash-current",
            "files": [{"relative_path": "SKILL.md", "content_base64": "c2tpbGw=", "size_bytes": 5}],
            "dependency_manifests": [
                {
                    "skill_id": "minimax-docx",
                    "version": "hash-dep",
                    "content_hash": "hash-dep",
                    "source": {"kind": "builtin", "asset_dir": "minimax-docx"},
                    "files": [{"relative_path": "SKILL.md", "content_base64": "ZGVw", "size_bytes": 3}],
                }
            ],
        },
        dependency_ids=["minimax-docx"],
        description="Current QA review",
    )

    sql, params = conn.calls[0]
    assert "update skill_versions" in sql
    assert "source_json->>'kind' = 'builtin'" in sql
    assert "not (source_json ? 'files')" in sql
    assert "source_json->'files' is distinct from (%s::jsonb->'files')" in sql
    assert "dependency_ids <> %s::jsonb" in sql
    assert "source_json->'dependency_manifests' is distinct from (%s::jsonb->'dependency_manifests')" in sql
    assert params[3:] == (
        "qa-file-reviewer",
        "hash-current",
        params[0],
        params[1],
        params[0],
    )
    assert '"files"' in params[0]
    assert '"dependency_manifests"' in params[0]
    assert "minimax-docx" in params[1]


@pytest.mark.asyncio
async def test_list_skill_versions_projects_source_and_dependencies():
    class VersionCursor:
        async def fetchall(self):
            return [
                {
                    "skill_id": "qa-file-reviewer",
                    "version": "hash-a",
                    "content_hash": "hash-a",
                    "description": "QA review",
                    "source_json": {"kind": "builtin"},
                    "dependency_ids": ["minimax-docx"],
                    "status": "active",
                    "created_by": "admin-a",
                    "created_at": None,
                }
            ]

    class VersionConnection:
        def __init__(self):
            self.sql = ""
            self.params = None

        async def execute(self, sql, params):
            self.sql = " ".join(sql.split())
            self.params = params
            return VersionCursor()

    conn = VersionConnection()

    versions = await repositories.list_skill_versions(conn, skill_id="qa-file-reviewer")

    assert "from skill_versions" in conn.sql
    assert conn.params == ("qa-file-reviewer",)
    assert versions == [
        {
            "skill_id": "qa-file-reviewer",
            "version": "hash-a",
            "content_hash": "hash-a",
            "description": "QA review",
            "source": {"kind": "builtin"},
            "dependency_ids": ["minimax-docx"],
            "status": "active",
            "created_by": "admin-a",
            "created_at": None,
        }
    ]


@pytest.mark.asyncio
async def test_get_effective_skill_version_for_policy_returns_uploaded_source():
    class VersionCursor:
        async def fetchone(self):
            return {
                "skill_id": "qa-file-reviewer",
                "version": "hash-uploaded",
                "content_hash": "hash-uploaded",
                "description": "QA review",
                "source_json": {"kind": "uploaded", "files": [{"relative_path": "SKILL.md"}]},
                "dependency_ids": [],
                "status": "active",
                "created_by": "admin-a",
                "created_at": None,
            }

    class VersionConnection:
        def __init__(self):
            self.sql = ""
            self.params = None

        async def execute(self, sql, params):
            self.sql = " ".join(sql.split())
            self.params = params
            return VersionCursor()

    conn = VersionConnection()

    version = await repositories.get_effective_skill_version_for_policy(
        conn,
        skill_id="qa-file-reviewer",
        version="hash-uploaded",
    )

    assert "from skill_versions" in conn.sql
    assert conn.params == ("qa-file-reviewer", "hash-uploaded")
    assert version["source"]["kind"] == "uploaded"
    assert version["version"] == "hash-uploaded"


@pytest.mark.asyncio
async def test_get_skill_projects_status_for_upload_preflight():
    class SkillCursor:
        async def fetchone(self):
            return {"skill_id": "qa-file-reviewer", "version": "0.1.0", "status": "active"}

    class SkillConnection:
        def __init__(self):
            self.sql = ""
            self.params = None

        async def execute(self, sql, params):
            self.sql = " ".join(sql.split())
            self.params = params
            return SkillCursor()

    conn = SkillConnection()

    row = await repositories.get_skill(conn, skill_id="qa-file-reviewer")

    assert "from skills" in conn.sql
    assert "version" in conn.sql
    assert conn.params == ("qa-file-reviewer",)
    assert row == {"skill_id": "qa-file-reviewer", "version": "0.1.0", "status": "active"}


@pytest.mark.asyncio
async def test_list_skill_ids_returns_all_catalog_ids_for_dependency_policy():
    class SkillCursor:
        async def fetchall(self):
            return [{"id": "qa-file-reviewer"}, {"id": "minimax-docx"}]

    class SkillConnection:
        def __init__(self):
            self.sql = ""
            self.params = None

        async def execute(self, sql, params=()):
            self.sql = " ".join(sql.split())
            self.params = params
            return SkillCursor()

    conn = SkillConnection()

    skill_ids = await repositories.list_skill_ids(conn)

    assert "from skills" in conn.sql
    assert conn.params == ()
    assert skill_ids == ["qa-file-reviewer", "minimax-docx"]


@pytest.mark.asyncio
async def test_get_skill_release_policy_projects_current_version():
    class ReleaseCursor:
        async def fetchone(self):
            return {
                "skill_id": "qa-file-reviewer",
                "channel": "stable",
                "current_version": "hash-b",
                "previous_version": "hash-a",
                "rollout_percent": 100,
                "status": "active",
                "promoted_by": "dev-admin",
                "promoted_at": None,
            }

    class ReleaseConnection:
        def __init__(self):
            self.sql = ""
            self.params = None

        async def execute(self, sql, params):
            self.sql = " ".join(sql.split())
            self.params = params
            return ReleaseCursor()

    conn = ReleaseConnection()

    policy = await repositories.get_skill_release_policy(
        conn,
        tenant_id="default",
        skill_id="qa-file-reviewer",
    )

    assert "status = 'active'" in conn.sql
    assert policy == {
        "skill_id": "qa-file-reviewer",
        "channel": "stable",
        "current_version": "hash-b",
        "previous_version": "hash-a",
        "rollout_percent": 100,
        "status": "active",
        "promoted_by": "dev-admin",
        "promoted_at": None,
    }


@pytest.mark.asyncio
async def test_set_skill_release_policy_is_tenant_scoped_and_preserves_previous_version():
    conn = RecordingConnection()

    await repositories.set_skill_release_policy(
        conn,
        tenant_id="default",
        skill_id="qa-file-reviewer",
        version="hash-b",
        previous_version="hash-a",
        promoted_by="dev-admin",
        channel="stable",
        rollout_percent=100,
    )

    sql, params = conn.calls[0]
    assert "insert into skill_release_policies" in sql
    assert "on conflict (tenant_id, skill_id, channel)" in sql
    assert "current_version = excluded.current_version" in sql
    assert params[0].startswith("skr_")
    assert params[1:6] == ("default", "qa-file-reviewer", "stable", "hash-b", "hash-a")
    assert params[6:9] == (100, "active", "dev-admin")


@pytest.mark.asyncio
async def test_diff_skill_versions_reports_manifest_and_dependency_changes():
    class DiffCursor:
        def __init__(self, row):
            self.row = row

        async def fetchone(self):
            return self.row

    class DiffConnection:
        async def execute(self, sql, params):
            assert "from skill_versions" in " ".join(sql.split())
            version = params[1]
            rows = {
                "hash-a": {
                    "skill_id": "qa-file-reviewer",
                    "version": "hash-a",
                    "content_hash": "hash-a",
                    "description": "old QA",
                    "source_json": {"kind": "builtin", "asset_dir": "qa-file-reviewer"},
                    "dependency_ids": ["minimax-docx"],
                    "status": "active",
                    "created_by": "admin-a",
                    "created_at": None,
                },
                "hash-b": {
                    "skill_id": "qa-file-reviewer",
                    "version": "hash-b",
                    "content_hash": "hash-b",
                    "description": "new QA",
                    "source_json": {"kind": "builtin", "asset_dir": "qa-file-reviewer-v2"},
                    "dependency_ids": ["minimax-docx", "term-checker"],
                    "status": "active",
                    "created_by": "admin-a",
                    "created_at": None,
                },
            }
            return DiffCursor(rows.get(version))

    diff = await repositories.diff_skill_versions(
        DiffConnection(),
        skill_id="qa-file-reviewer",
        from_version="hash-a",
        to_version="hash-b",
    )

    assert diff == {
        "skill_id": "qa-file-reviewer",
        "from_version": "hash-a",
        "to_version": "hash-b",
        "content_hash_changed": True,
        "description_changed": True,
        "source_changed": True,
        "dependency_added": ["term-checker"],
        "dependency_removed": [],
    }


@pytest.mark.asyncio
async def test_diff_skill_versions_raises_when_version_missing():
    class MissingCursor:
        async def fetchone(self):
            return None

    class MissingConnection:
        async def execute(self, sql, params):
            return MissingCursor()

    with pytest.raises(repositories.RepositoryNotFoundError, match="skill_version_not_found"):
        await repositories.diff_skill_versions(
            MissingConnection(),
            skill_id="qa-file-reviewer",
            from_version="hash-a",
            to_version="hash-b",
        )


@pytest.mark.asyncio
async def test_admin_skill_detail_projects_versions_and_recent_snapshots(monkeypatch):
    async def no_backfill(conn, *, tenant_id):
        assert tenant_id == "tenant-a"

    monkeypatch.setattr(repositories, "ensure_tenant_capability_distribution_backfill", no_backfill)

    class DetailCursor:
        def __init__(self, *, one=None, many=None):
            self.one = one
            self.many = many or []

        async def fetchone(self):
            return self.one

        async def fetchall(self):
            return self.many

    class SkillDetailConnection:
        async def execute(self, sql, params):
            compact = " ".join(sql.split())
            if "from skills" in compact:
                assert "tenant_workbench_skills" not in compact
                assert "join tenant_capability_distributions" in compact
                assert "skills.status as lifecycle_status" in compact
                assert params == ("tenant-a", "qa-file-reviewer")
                return DetailCursor(
                    one={
                        "skill_id": "qa-file-reviewer",
                        "name": "qa-file-reviewer",
                        "version": "0.1.0",
                        "description": "QA review",
                        "input_modes": ["docx"],
                        "output_modes": ["reviewed_docx"],
                        "executor_type": "claude_agent",
                        "status": "active",
                        "visible_to_user": True,
                    }
                )
            if "from skill_versions" in compact:
                assert params == ("qa-file-reviewer",)
                return DetailCursor(
                    many=[
                        {
                            "skill_id": "qa-file-reviewer",
                            "version": "hash-a",
                            "content_hash": "hash-a",
                            "description": "QA review",
                            "source_json": {"kind": "builtin"},
                            "dependency_ids": ["minimax-docx"],
                            "status": "active",
                            "created_by": "admin-a",
                            "created_at": None,
                        }
                    ]
                )
            if "from skill_release_policies" in compact:
                assert params == ("tenant-a", "qa-file-reviewer", "stable")
                return DetailCursor(
                    one={
                        "skill_id": "qa-file-reviewer",
                        "channel": "stable",
                        "current_version": "hash-a",
                        "previous_version": "0.1.0",
                        "rollout_percent": 100,
                        "status": "active",
                        "promoted_by": "admin-a",
                        "promoted_at": None,
                    }
                )
            if "from run_skill_snapshots" in compact:
                assert params == ("tenant-a", "qa-file-reviewer")
                return DetailCursor(
                    many=[
                        {
                            "run_id": "run-a",
                            "skill_id": "qa-file-reviewer",
                            "skill_version": "hash-a",
                            "content_hash": "hash-a",
                                "source_json": {
                                    "kind": "builtin",
                                    "version": "hash-a",
                                    "snapshot_governance": {
                                    "schema_version": "ai-platform.skill-pinned-snapshot-governance.v1",
                                    "snapshot_source": "platform_release_lock",
                                    "release_lock": {
                                        "mode": "manifest_pin",
                                        "release_decision": {"selected_version": "hash-a"},
                                        "selected_version": "hash-a",
                                        "track": "manifest_pin",
                                        "rollout": 100,
                                    },
                                    "manifest": {
                                        "digest": "hash-a",
                                        "source_kind": "builtin",
                                        "selected_file_count": 1,
                                        "content_hash": "hash-a",
                                    },
                                    "selected_files": [
                                        {
                                            "relative_path": "SKILL.md",
                                            "size_bytes": 5,
                                            "sha256": "9c53c074d7ac6a2728b638ac1f376c5fa9eb8f71603017c3ea638c2fd40548df",
                                            "content_base64": "c2tpbGw=",
                                        }
                                    ],
                                    "dependency_evidence": {
                                        "status": "review_required",
                                        "ref": "skill_dependency_policy",
                                        "dependency_count": 1,
                                    },
                                    "does_not_close_b4_or_211": True,
                                    "storage_key": "tenants/default/private/package.zip",
                                },
                            },
                            "dependency_ids": ["minimax-docx"],
                            "allowed": True,
                            "staged": True,
                            "used": True,
                            "created_at": None,
                        }
                    ]
                )
            raise AssertionError(compact)

    detail = await repositories.get_admin_skill_detail(
        SkillDetailConnection(),
        tenant_id="tenant-a",
        skill_id="qa-file-reviewer",
    )

    assert detail["skill"]["skill_id"] == "qa-file-reviewer"
    assert detail["versions"][0]["content_hash"] == "hash-a"
    assert detail["versions"][0]["source"] == {"kind": "builtin"}
    assert detail["versions"][0]["dependency_ids"] == ["minimax-docx"]
    assert detail["release_policy"]["current_version"] == "hash-a"
    assert detail["release_policy"]["previous_version"] == "0.1.0"
    assert detail["recent_snapshots"][0]["run_id"] == "run-a"
    assert detail["recent_snapshots"][0]["dependency_ids"] == ["minimax-docx"]
    assert detail["recent_snapshots"][0]["source"] == {
        "kind": "builtin",
        "snapshot_governance": {
            "schema_version": "ai-platform.skill-pinned-snapshot-governance.v1",
            "snapshot_source": "platform_release_lock",
            "release_lock": {"mode": "manifest_pin"},
            "manifest": {
                "source_kind": "builtin",
                "selected_file_count": 1,
            },
            "selected_files": [
                {
                    "relative_path": "SKILL.md",
                    "size_bytes": 5,
                    "sha256": "9c53c074d7ac6a2728b638ac1f376c5fa9eb8f71603017c3ea638c2fd40548df",
                }
            ],
            "dependency_evidence": {
                "status": "review_required",
                "ref": "skill_dependency_policy",
                "dependency_count": 1,
            },
            "does_not_close_b4_or_211": True,
        },
    }
    serialized_snapshots = json.dumps(detail["recent_snapshots"], ensure_ascii=False)
    assert "skill_version" not in serialized_snapshots
    assert "content_hash" not in serialized_snapshots
    assert "content_base64" not in serialized_snapshots
    assert "storage_key" not in serialized_snapshots
    assert "hash-a" not in serialized_snapshots
    assert "version" not in detail["recent_snapshots"][0]["source"]
    assert "track" not in serialized_snapshots
    assert "rollout" not in serialized_snapshots


@pytest.mark.asyncio
async def test_list_admin_skill_summaries_excludes_package_source(monkeypatch):
    async def no_backfill(_conn, *, tenant_id):
        assert tenant_id == "tenant-a"

    monkeypatch.setattr(repositories, "ensure_tenant_capability_distribution_backfill", no_backfill)

    class SummaryCursor:
        async def fetchall(self):
            return [
                {
                    "skill_id": "native-demo",
                    "name": "native-demo",
                    "description": "Native demo",
                    "lifecycle_status": "active",
                    "distribution_status": "disabled",
                    "visible_to_user": False,
                    "latest_version": "hash-a",
                    "latest_version_status": "draft",
                    "current_version": None,
                    "rollout_percent": None,
                }
            ]

    class SummaryConnection:
        async def execute(self, sql, params):
            compact = " ".join(sql.split())
            assert params == ("tenant-a", "tenant-a")
            assert "source_json" not in compact
            assert "storage_key" not in compact
            assert "left join lateral" in compact
            return SummaryCursor()

    rows = await repositories.list_admin_skill_summaries(
        SummaryConnection(),
        tenant_id="tenant-a",
    )

    assert rows == [
        {
            "skill_id": "native-demo",
            "name": "native-demo",
            "description": "Native demo",
            "lifecycle_status": "active",
            "distribution_status": "disabled",
            "visible_to_user": False,
            "latest_version": "hash-a",
            "latest_version_status": "draft",
            "current_version": None,
            "rollout_percent": None,
        }
    ]


@pytest.mark.asyncio
async def test_set_workbench_skill_status_rejects_internal_dependency_skill():
    conn = RecordingConnection()

    with pytest.raises(repositories.RepositoryNotFoundError, match="workbench_skill_not_found"):
        await repositories.set_workbench_skill_status(
            conn,
            tenant_id="default",
            skill_id="minimax-docx",
            status="active",
        )

    assert conn.calls == []


@pytest.mark.asyncio
async def test_set_uploaded_workbench_skill_status_creates_authoritative_distribution(monkeypatch):
    async def no_backfill(conn, *, tenant_id):
        return None

    monkeypatch.setattr(repositories, "ensure_tenant_capability_distribution_backfill", no_backfill)

    class UploadedSkillConnection:
        def __init__(self):
            self.calls = []

        async def execute(self, sql, params):
            compact = " ".join(sql.split())
            self.calls.append((compact, params))
            if compact.startswith("insert into tenant_capability_distributions"):
                return SingleRowCursor(
                    {
                        "id": "capdist-new",
                        "tenant_id": "default",
                        "capability_kind": "skill",
                        "capability_id": "new-research-skill",
                        "status": "active",
                        "visible_to_user": True,
                        "scope_mode": "allowlist",
                        "department_ids": [],
                        "allowed_roles": [],
                        "metadata_json": {},
                    }
                )
            if compact.startswith("select skills.id as skill_id"):
                return SingleRowCursor(
                    {
                        "skill_id": "new-research-skill",
                        "name": "new-research-skill",
                        "version": "hash-new",
                        "description": "Summarize research briefs.",
                        "input_modes": ["chat"],
                        "output_modes": ["answer"],
                        "executor_type": "claude-agent-worker",
                        "status": "active",
                        "visible_to_user": True,
                    }
                )
            return FakeCursor()

    conn = UploadedSkillConnection()

    row = await repositories.set_uploaded_workbench_skill_status(
        conn,
        tenant_id="default",
        skill_id="new-research-skill",
        status="active",
    )

    assert row["skill_id"] == "new-research-skill"
    assert "pg_advisory_xact_lock" in conn.calls[0][0]
    assert "select metadata_json" in conn.calls[1][0]
    assert "insert into tenant_capability_distributions" in conn.calls[2][0]
    assert "tenant_workbench_skills" not in " ".join(sql for sql, _ in conn.calls)
    assert conn.calls[2][1][1:5] == ("default", "skill", "new-research-skill", "active")
    assert conn.calls[3][1] == ("default", "new-research-skill")


@pytest.mark.asyncio
async def test_set_public_skill_enabled_updates_existing_authoritative_distribution(monkeypatch):
    async def existing_distribution(conn, *, tenant_id, capability_kind, capability_id):
        return {
            "tenant_id": tenant_id,
            "capability_kind": capability_kind,
            "capability_id": capability_id,
            "status": "disabled",
            "visible_to_user": True,
            "scope_mode": "allowlist",
            "department_ids": ["qa"],
            "allowed_roles": ["qa_operator"],
            "metadata_json": {"source": "shared"},
        }

    async def no_backfill(conn, *, tenant_id):
        return None

    monkeypatch.setattr(repositories, "get_capability_distribution_row", existing_distribution)
    monkeypatch.setattr(repositories, "ensure_tenant_capability_distribution_backfill", no_backfill)

    class UploadedSkillConnection:
        def __init__(self):
            self.calls = []

        async def execute(self, sql, params):
            compact = " ".join(sql.split())
            self.calls.append((compact, params))
            if compact.startswith("insert into tenant_capability_distributions"):
                return SingleRowCursor(
                    {
                        "id": "capdist-existing",
                        "tenant_id": "default",
                        "capability_kind": "skill",
                        "capability_id": "new-research-skill",
                        "status": "active",
                        "visible_to_user": True,
                        "scope_mode": "allowlist",
                        "department_ids": ["qa"],
                        "allowed_roles": ["qa_operator"],
                        "metadata_json": {"source": "shared"},
                    }
                )
            if compact.startswith("select skills.id as skill_id"):
                return SingleRowCursor(
                    {
                        "skill_id": "new-research-skill",
                        "name": "new-research-skill",
                        "version": "hash-new",
                        "description": "Summarize research briefs.",
                        "input_modes": ["chat"],
                        "output_modes": ["answer"],
                        "executor_type": "claude-agent-worker",
                        "status": "active",
                        "visible_to_user": True,
                    }
                )
            return FakeCursor()

    conn = UploadedSkillConnection()

    row = await repositories.set_public_skill_enabled(
        conn,
        tenant_id="default",
        skill_id="new-research-skill",
        status="active",
    )

    assert row["skill_id"] == "new-research-skill"
    assert "pg_advisory_xact_lock" in conn.calls[0][0]
    assert "select metadata_json" in conn.calls[1][0]
    assert "insert into tenant_capability_distributions" in conn.calls[2][0]
    assert "tenant_workbench_skills" not in " ".join(sql for sql, _ in conn.calls)


@pytest.mark.asyncio
async def test_set_public_skill_enabled_rejects_non_public_skill_without_distribution(monkeypatch):
    async def missing_distribution(conn, *, tenant_id, capability_kind, capability_id):
        return None

    monkeypatch.setattr(repositories, "get_capability_distribution_row", missing_distribution)

    class MissingUploadedSkillConnection:
        def __init__(self):
            self.calls = []

        async def execute(self, sql, params):
            compact = " ".join(sql.split())
            self.calls.append((compact, params))
            return SingleRowCursor(None)

    conn = MissingUploadedSkillConnection()

    with pytest.raises(RepositoryNotFoundError, match="workbench_skill_not_found"):
        await repositories.set_public_skill_enabled(
            conn,
            tenant_id="default",
            skill_id="minimax-docx",
            status="active",
        )

    assert conn.calls == []


@pytest.mark.asyncio
async def test_list_run_artifacts_returns_manifest_contract_columns():
    conn = RecordingConnection()

    await list_run_artifacts(conn, tenant_id="tenant-a", run_id="run-a")

    sql, _ = conn.calls[0]
    assert "trace_id" in sql
    assert "manifest_version" in sql


@pytest.mark.asyncio
async def test_admin_run_detail_projects_g2_trace_event_artifact_and_audit_contracts():
    class DetailCursor:
        def __init__(self, *, one=None, many=None):
            self.one = one
            self.many = many or []

        async def fetchone(self):
            return self.one

        async def fetchall(self):
            return self.many

    class DetailConnection:
        async def execute(self, sql, params):
            compact = " ".join(sql.split())
            if "from runs where tenant_id" in compact:
                return DetailCursor(
                    one={
                        "id": "run-a",
                        "trace_id": "trace_run_a",
                        "schema_version": "ai-platform.run.v1",
                        "executor_schema_version": "ai-platform.executor-result.v1",
                        "session_id": "ses-a",
                        "user_id": "user-a",
                        "workspace_id": "default",
                        "status": "succeeded",
                        "agent_id": "qa-word-review",
                        "skill_id": "qa-file-reviewer",
                        "created_at": None,
                        "queued_at": None,
                        "started_at": None,
                        "finished_at": None,
                        "cancel_requested_at": None,
                        "cancel_requested_by": None,
                        "input_json": {},
                        "result_json": {},
                        "error_code": None,
                        "error_message": None,
                    }
                )
            if "from run_events" in compact:
                return DetailCursor(
                    many=[
                        {
                            "id": "evt-a",
                            "trace_id": "trace_run_a",
                            "schema_version": "ai-platform.event-envelope.v1",
                            "event_type": "run_succeeded",
                            "stage": "worker",
                            "message": "Run succeeded",
                            "severity": "info",
                            "visible_to_user": True,
                            "error_code": None,
                            "latency_ms": 12,
                            "input_token_count": 1,
                            "output_token_count": 2,
                            "total_token_count": 3,
                            "estimated_cost_minor": 4,
                            "payload_json": {"message": "done"},
                            "created_at": None,
                        },
                        {
                            "id": "evt-skill-b",
                            "trace_id": "trace_run_a",
                            "schema_version": "ai-platform.event-envelope.v1",
                            "event_type": "skill_used",
                            "stage": "skills",
                            "message": "Platform Skill used: qa-file-reviewer",
                            "severity": "info",
                            "visible_to_user": False,
                            "error_code": None,
                            "latency_ms": None,
                            "input_token_count": 0,
                            "output_token_count": 0,
                            "total_token_count": 0,
                            "estimated_cost_minor": 0,
                            "payload_json": {
                                "skill_id": "qa-file-reviewer",
                                "used_skills_source": "executor_hook",
                                "source": "claude_agent_sdk_hook",
                                "tool_use_id": "tool-use-b",
                            },
                            "created_at": None,
                        },
                        {
                            "id": "evt-skill-visible",
                            "trace_id": "trace_run_a",
                            "schema_version": "ai-platform.event-envelope.v1",
                            "event_type": "skill_used",
                            "stage": "skills",
                            "message": "Visible skill label",
                            "severity": "info",
                            "visible_to_user": True,
                            "error_code": None,
                            "latency_ms": None,
                            "input_token_count": 0,
                            "output_token_count": 0,
                            "total_token_count": 0,
                            "estimated_cost_minor": 0,
                            "payload_json": {
                                "skill_id": "qa-file-reviewer",
                                "used_skills_source": "visible_projection",
                                "source": "visible_event",
                                "tool_use_id": "visible-tool",
                            },
                            "created_at": None,
                        },
                        {
                            "id": "evt-skill-a",
                            "trace_id": "trace_run_a",
                            "schema_version": "ai-platform.event-envelope.v1",
                            "event_type": "skill_used",
                            "stage": "skills",
                            "message": "Platform Skill used: qa-file-reviewer",
                            "severity": "info",
                            "visible_to_user": False,
                            "error_code": None,
                            "latency_ms": None,
                            "input_token_count": 0,
                            "output_token_count": 0,
                            "total_token_count": 0,
                            "estimated_cost_minor": 0,
                            "payload_json": {
                                "skill_id": "qa-file-reviewer",
                                "used_skills_source": "executor_hook",
                                "source": "claude_agent_sdk_hook",
                                "tool_use_id": "tool-use-a",
                            },
                            "created_at": None,
                        }
                    ]
                )
            if "from run_steps" in compact:
                return DetailCursor(many=[])
            if "from artifacts" in compact:
                return DetailCursor(
                    many=[
                        {
                            "id": "art-a",
                            "trace_id": "trace_run_a",
                            "manifest_version": "ai-platform.artifact-manifest.v1",
                            "artifact_type": "reviewed_docx",
                            "label": "审核 Word",
                            "content_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                            "storage_key": "tenants/default/private.docx",
                            "size_bytes": 10,
                            "manifest_json": {"storage_key": "tenants/default/private.docx", "source_file_id": "file-a"},
                            "created_at": None,
                        }
                    ]
                )
            if "from run_skill_snapshots" in compact:
                return DetailCursor(
                    many=[
                        {
                            "skill_id": "qa-file-reviewer",
                            "skill_version": "hash-a",
                            "content_hash": "hash-a",
                            "source_json": {"kind": "builtin"},
                            "dependency_ids": ["minimax-docx"],
                            "allowed": True,
                            "staged": True,
                            "used": True,
                            "created_at": None,
                        }
                    ]
                )
            if "from sandbox_leases" in compact:
                return DetailCursor(
                    many=[
                        {
                            "id": "lease-a",
                            "tenant_id": "tenant-a",
                            "workspace_id": "default",
                            "user_id": "user-a",
                            "session_id": "ses-a",
                            "run_id": "run-a",
                            "trace_id": "trace_lease_a",
                            "sandbox_mode": "ephemeral",
                            "provider": "fake",
                            "status": "released",
                            "browser_enabled": False,
                            "resource_limits_json": {"cpu": 1, "token": "secret-limit"},
                            "user_visible_payload_json": {
                                "workspace_fingerprint": "tenant-a:default:ses-a:run-a",
                                "runtime_private_payload": {"cwd": "/var/lib/ai-platform/run-a"},
                            },
                            "lease_payload_json": {
                                "source": "foundation_runtime_lifecycle_probe",
                                "container_id": "exec-run-a",
                                "container_name": "executor-exec-run-a",
                                "executor_url": "http://executor.internal",
                                "workspace_host_path": "/var/lib/ai-platform/run-a",
                                "workspace_container_path": "/workspace",
                                "labels": {"runtime": "private"},
                                "client_secret": "lease-secret",
                            },
                            "runtime_container_id": "exec-run-a",
                            "runtime_container_name": "executor-exec-run-a",
                            "runtime_executor_url": "http://executor.internal",
                            "runtime_workspace_container_path": "/workspace",
                            "runtime_handle_verified_at": "2026-07-11T00:00:00Z",
                            "heartbeat_at": None,
                            "expires_at": None,
                            "released_at": None,
                            "release_reason": "completed",
                            "created_at": None,
                        }
                    ]
                )
            if "from audit_logs" in compact:
                return DetailCursor(
                    many=[
                        {
                            "id": "aud-a",
                            "trace_id": "trace_run_a",
                            "schema_version": "ai-platform.audit-event.v1",
                            "user_id": "admin-a",
                            "action": "admin_artifact_downloaded",
                            "target_type": "artifact",
                            "target_id": "art-a",
                            "payload_json": {"run_id": "run-a"},
                            "created_at": None,
                        }
                    ]
                )
            raise AssertionError(compact)

    detail = await get_admin_run_detail(DetailConnection(), tenant_id="tenant-a", run_id="run-a")

    assert detail["run"]["trace_id"] == "trace_run_a"
    assert detail["run"]["contract_version"] == "ai-platform.run.v1"
    assert detail["run"]["executor_schema_version"] == "ai-platform.executor-result.v1"
    assert detail["events"][0]["schema_version"] == "ai-platform.event-envelope.v1"
    assert detail["events"][0]["trace_id"] == "trace_run_a"
    assert detail["events"][0]["token_counts"] == {"input": 1, "output": 2, "total": 3}
    assert detail["artifacts"][0]["trace_id"] == "trace_run_a"
    assert detail["artifacts"][0]["manifest"]["schema_version"] == "ai-platform.artifact-manifest.v1"
    assert "storage_key" not in str(detail["artifacts"][0]["manifest"])
    assert detail["sandbox_leases"][0]["lease_id"] == "lease-a"
    assert detail["sandbox_leases"][0]["lease_payload"] == {"source": "foundation_runtime_lifecycle_probe"}
    assert "resource_limits" in detail["sandbox_leases"][0]
    serialized_leases = json.dumps(detail["sandbox_leases"], ensure_ascii=False, default=str)
    assert "lease-secret" not in serialized_leases
    assert "secret-limit" not in serialized_leases
    assert "/var/lib/ai-platform" not in serialized_leases
    assert "exec-run-a" not in serialized_leases
    assert "executor.internal" not in serialized_leases
    assert "runtime_handle_verified_at" not in serialized_leases
    assert detail["skill_snapshots"] == [
        {
            "skill_id": "qa-file-reviewer",
            "skill_version": "hash-a",
            "content_hash": "hash-a",
            "source": {"kind": "builtin"},
            "dependency_ids": ["minimax-docx"],
            "allowed": True,
            "staged": True,
            "used": True,
            "usage": {
                "event_source": "claude_agent_sdk_hook",
                "event_count": 2,
                "tool_use_ids": ["tool-use-a", "tool-use-b"],
            },
            "created_at": None,
        }
    ]
    serialized_skill_snapshots = json.dumps(detail["skill_snapshots"], ensure_ascii=False, default=str)
    assert '"skill_version": "hash-a"' in serialized_skill_snapshots
    assert '"content_hash": "hash-a"' in serialized_skill_snapshots
    assert detail["audit"][0]["schema_version"] == "ai-platform.audit-event.v1"
    assert detail["audit"][0]["trace_id"] == "trace_run_a"


@pytest.mark.asyncio
async def test_admin_run_detail_sanitizes_secret_and_runtime_payloads(monkeypatch):
    async def fake_get_run(conn, *, tenant_id, run_id):
        return {
            "id": run_id,
            "session_id": "ses-a",
            "user_id": "user-a",
            "workspace_id": "default",
            "status": "failed",
            "agent_id": "qa-word-review",
            "skill_id": "qa-file-reviewer",
            "created_at": None,
            "queued_at": None,
            "started_at": None,
            "finished_at": None,
            "cancel_requested_at": None,
            "cancel_requested_by": None,
            "input_json": {
                "input": {"message": "审核", "api_key": "sk-admin-input"},
                "workerPath": "/var/lib/ai-platform/run-a/worker.py",
                "skill_ids": ["qa-file-reviewer"],
            },
            "result_json": {
                "message": "failed client_secret=admin-result-secret",
                "runtime_private_payload": {"cwd": "/var/lib/ai-platform/run-a"},
                "used_skills": ["qa-file-reviewer"],
            },
            "error_code": "executor_failure token=admin-detail-code-token",
            "error_message": "failed token=admin-error-token /var/lib/ai-platform/run-a/out.log",
            "trace_id": "trace_run_a",
            "schema_version": "ai-platform.run.v1",
            "executor_schema_version": "ai-platform.executor-result.v1",
        }

    async def fake_list_run_events(conn, *, tenant_id, run_id, **kwargs):
        return [
            {
                "id": "evt-a",
                "trace_id": "trace_run_a",
                "schema_version": "ai-platform.event-envelope.v1",
                "sequence": 1,
                "event_type": "error",
                "stage": "worker",
                "message": "failed token=admin-event-token",
                "severity": "error",
                "visible_to_user": True,
                "error_code": "executor_failure",
                "latency_ms": None,
                "input_token_count": 0,
                "output_token_count": 0,
                "total_token_count": 0,
                "estimated_cost_minor": 0,
                "payload_json": {
                    "runtime_private_payload": {"cwd": "/var/lib/ai-platform/run-a"},
                    "summary": "client_secret=admin-event-secret",
                },
                "created_at": None,
            }
        ]

    async def fake_list_run_steps(conn, *, tenant_id, run_id):
        return [
            {
                "id": "step-a",
                "run_id": run_id,
                "step_key": "review",
                "step_kind": "agent",
                "status": "failed",
                "title": "Review token=admin-title-token",
                "role": "reviewer client_secret=admin-role-secret",
                "sequence": 1,
                "payload_json": {
                    "skill_ids": ["qa-file-reviewer"],
                    "runtime_private_payload": {"cwd": "/var/lib/ai-platform/run-a"},
                    "note": "client_secret=admin-step-secret",
                },
                "started_at": None,
                "finished_at": None,
                "created_at": None,
                "updated_at": None,
            }
        ]

    async def fake_empty_list(*args, **kwargs):
        return []

    class AuditCursor:
        async def fetchall(self):
            return [
                {
                    "id": "aud-a",
                    "trace_id": "trace_run_a",
                    "schema_version": "ai-platform.audit-event.v1",
                    "user_id": "admin-a",
                    "action": "admin_run_viewed",
                    "target_type": "run",
                    "target_id": "run-a",
                    "payload_json": {
                        "run_id": "run-a",
                        "client_secret": "admin-audit-secret",
                        "runtime_private_payload": {"cwd": "/var/lib/ai-platform/run-a"},
                    },
                    "created_at": None,
                }
            ]

    class EmptyListCursor:
        async def fetchall(self):
            return []

    class AuditConnection:
        async def execute(self, sql, params):
            if "from sandbox_leases" in " ".join(sql.split()):
                return EmptyListCursor()
            return AuditCursor()

    monkeypatch.setattr(repositories, "get_run", fake_get_run)
    monkeypatch.setattr(repositories, "list_run_events", fake_list_run_events)
    monkeypatch.setattr(repositories, "list_run_steps", fake_list_run_steps)
    monkeypatch.setattr(repositories, "list_run_artifacts", fake_empty_list)
    monkeypatch.setattr(repositories, "list_run_skill_snapshots", fake_empty_list)

    detail = await get_admin_run_detail(AuditConnection(), tenant_id="tenant-a", run_id="run-a")

    assert detail["run"]["input"]["skill_ids"] == ["qa-file-reviewer"]
    assert detail["run"]["result"]["used_skills"] == ["qa-file-reviewer"]
    assert detail["steps"][0]["payload"]["skill_ids"] == ["qa-file-reviewer"]
    serialized = json.dumps(detail, ensure_ascii=False, default=str)
    assert "sk-admin-input" not in serialized
    assert "admin-result-secret" not in serialized
    assert "admin-detail-code-token" not in serialized
    assert "admin-error-token" not in serialized
    assert "admin-event-token" not in serialized
    assert "admin-event-secret" not in serialized
    assert "admin-title-token" not in serialized
    assert "admin-role-secret" not in serialized
    assert "admin-step-secret" not in serialized
    assert "admin-audit-secret" not in serialized
    assert "/var/lib/ai-platform" not in serialized
    assert "runtime_private_payload" not in serialized


@pytest.mark.asyncio
async def test_admin_run_detail_sanitizes_dirty_skill_snapshot_source_and_usage(monkeypatch):
    async def fake_get_run(conn, *, tenant_id, run_id):
        return {
            "id": run_id,
            "session_id": "ses-a",
            "user_id": "user-a",
            "workspace_id": "default",
            "status": "succeeded",
            "agent_id": "qa-word-review",
            "skill_id": "qa-file-reviewer",
            "created_at": None,
            "input_json": {},
            "result_json": {},
            "error_code": None,
            "error_message": None,
            "trace_id": "trace_run_a",
            "schema_version": "ai-platform.run.v1",
            "executor_schema_version": "ai-platform.executor-result.v1",
        }

    async def fake_list_run_events(conn, *, tenant_id, run_id, **kwargs):
        return [
            {
                "id": "evt-skill-a",
                "trace_id": "trace_run_a",
                "schema_version": "ai-platform.event-envelope.v1",
                "sequence": 1,
                "event_type": "skill_used",
                "stage": "skills",
                "message": "hidden",
                "severity": "info",
                "visible_to_user": False,
                "error_code": None,
                "latency_ms": None,
                "input_token_count": 0,
                "output_token_count": 0,
                "total_token_count": 0,
                "estimated_cost_minor": 0,
                "payload_json": {
                    "skill_id": "qa-file-reviewer",
                    "source": "claude_agent_sdk_hook token=skill-event-token",
                    "tool_use_id": "tool-use-a token=skill-tool-token",
                },
                "created_at": None,
            }
        ]

    async def fake_list_run_skill_snapshots(conn, *, tenant_id, run_id):
        return [
            {
                "skill_id": "qa-file-reviewer",
                "skill_version": "hash-a",
                "content_hash": "hash-a",
                "source": {
                    "kind": "builtin",
                    "client_secret": "skill-source-secret",
                    "local_path": "/var/lib/ai-platform/skills/qa-file-reviewer",
                    "nested": {"safe": "kept", "token": "nested-token"},
                },
                "dependency_ids": ["minimax-docx"],
                "allowed": True,
                "staged": True,
                "used": True,
                "usage": {"used_skills_source": "claude_agent_sdk_hook token=skill-persisted-token"},
                "created_at": None,
            }
        ]

    async def fake_empty_list(*args, **kwargs):
        return []

    class EmptyAuditConnection:
        async def execute(self, sql, params):
            class Cursor:
                async def fetchall(self):
                    return []

            return Cursor()

    monkeypatch.setattr(repositories, "get_run", fake_get_run)
    monkeypatch.setattr(repositories, "list_run_events", fake_list_run_events)
    monkeypatch.setattr(repositories, "list_run_steps", fake_empty_list)
    monkeypatch.setattr(repositories, "list_run_artifacts", fake_empty_list)
    monkeypatch.setattr(repositories, "list_run_skill_snapshots", fake_list_run_skill_snapshots)

    detail = await get_admin_run_detail(EmptyAuditConnection(), tenant_id="tenant-a", run_id="run-a")

    snapshot = detail["skill_snapshots"][0]
    assert snapshot["skill_id"] == "qa-file-reviewer"
    assert snapshot["source"] == {"kind": "builtin", "nested": {"safe": "kept"}}
    assert snapshot["usage"]["used_skills_source"] == "claude_agent_sdk_hook token=[redacted-secret]"
    assert snapshot["usage"]["event_source"] == "claude_agent_sdk_hook token=[redacted-secret]"
    assert snapshot["usage"]["tool_use_ids"] == ["tool-use-a token=[redacted-secret]"]
    serialized = json.dumps(detail, ensure_ascii=False, default=str)
    assert "skill-source-secret" not in serialized
    assert "nested-token" not in serialized
    assert "skill-persisted-token" not in serialized
    assert "skill-event-token" not in serialized
    assert "skill-tool-token" not in serialized
    assert "/var/lib/ai-platform" not in serialized


@pytest.mark.asyncio
async def test_complete_run_persists_g2_observability_columns_from_result_json():
    conn = RecordingConnection()

    await complete_run(
        conn,
        tenant_id="tenant-a",
        run_id="run-a",
        result_json={
            "message": "done",
            "latency_ms": 250,
            "token_counts": {"input": 11, "output": 13, "total": 24},
            "cost": {"estimated_cost_minor": 17},
        },
    )

    sql, params = next(
        (sql, params)
        for sql, params in conn.calls
        if sql.startswith("update runs") and "set status = 'succeeded'" in sql
    )
    assert "latency_ms" in sql
    assert "input_token_count" in sql
    assert "output_token_count" in sql
    assert "total_token_count" in sql
    assert "estimated_cost_minor" in sql
    assert 250 in params
    assert 11 in params
    assert 13 in params
    assert 24 in params
    assert 17 in params


@pytest.mark.asyncio
async def test_complete_run_consumes_valid_allow_for_run_before_its_final_pending_guard():
    authority_now = datetime(2026, 7, 16, tzinfo=timezone.utc)

    class Cursor:
        def __init__(self, row=None, rows=None):
            self.row = row
            self.rows = rows or []

        async def fetchone(self):
            return self.row

        async def fetchall(self):
            return self.rows

    class Connection:
        def __init__(self):
            self.calls = []

        async def execute(self, sql, params):
            normalized = " ".join(sql.split())
            self.calls.append((normalized, params))
            if normalized.startswith("select id from runs"):
                return Cursor({"id": "run-a"})
            if "select clock_timestamp() as authority_now" in normalized:
                return Cursor({"authority_now": authority_now})
            if normalized.startswith("select id, status, decision, expires_at from run_tool_permission_requests"):
                return Cursor(
                    rows=[
                        {
                            "id": "tpr-a",
                            "status": "decided",
                            "decision": "allow_for_run",
                            "expires_at": authority_now + timedelta(seconds=1),
                        }
                    ]
                )
            if normalized.startswith("update runs"):
                return Cursor({"id": "run-a"})
            if normalized.startswith("update run_tool_permission_requests"):
                return Cursor(rows=[{"id": "tpr-a"}])
            raise AssertionError(normalized)

    conn = Connection()

    await complete_run(conn, tenant_id="tenant-a", run_id="run-a", result_json={"message": "done"})

    lock_sql, _params = conn.calls[0]
    authority_sql, _params = conn.calls[1]
    grant_lock_sql, _params = conn.calls[2]
    completion_sql, _params = conn.calls[3]
    consume_sql, _params = conn.calls[4]
    assert "for update" in lock_sql
    assert "clock_timestamp() as authority_now" in authority_sql
    assert "for update" in grant_lock_sql
    assert "update runs" in completion_sql
    assert "update run_tool_permission_requests" in consume_sql
    assert "id = any(%s::text[])" in consume_sql
    assert "decision = 'allow_for_run'" in consume_sql


@pytest.mark.asyncio
async def test_complete_run_permission_blocker_returns_before_any_run_or_grant_mutation():
    authority_now = datetime(2026, 7, 16, tzinfo=timezone.utc)

    class Cursor:
        def __init__(self, row=None, rows=None):
            self.row = row
            self.rows = rows or []

        async def fetchone(self):
            return self.row

        async def fetchall(self):
            return self.rows

    class Connection:
        def __init__(self):
            self.calls = []

        async def execute(self, sql, params):
            normalized = " ".join(sql.split())
            self.calls.append((normalized, params))
            if normalized.startswith("select id from runs"):
                return Cursor({"id": "run-a"})
            if "select clock_timestamp() as authority_now" in normalized:
                return Cursor({"authority_now": authority_now})
            if normalized.startswith("select id, status, decision, expires_at from run_tool_permission_requests"):
                return Cursor(rows=[{"id": "tpr-pending", "status": "pending", "decision": None, "expires_at": None}])
            raise AssertionError(normalized)

    conn = Connection()
    assert await complete_run(conn, tenant_id="tenant-a", run_id="run-a", result_json={}) is False
    assert len(conn.calls) == 3
    assert "for update" in conn.calls[0][0]


@pytest.mark.asyncio
async def test_complete_run_uses_one_locked_db_time_and_consumes_exact_valid_run_grants():
    authority_now = datetime(2026, 7, 16, tzinfo=timezone.utc)

    class Cursor:
        def __init__(self, row=None, rows=None):
            self.row = row
            self.rows = rows or []

        async def fetchone(self):
            return self.row

        async def fetchall(self):
            return self.rows

    class Connection:
        def __init__(self):
            self.calls = []

        async def execute(self, sql, params):
            normalized = " ".join(sql.split())
            self.calls.append((normalized, params))
            if normalized.startswith("select id from runs") and "for update" in normalized:
                return Cursor({"id": "run-a"})
            if "select clock_timestamp() as authority_now" in normalized:
                return Cursor({"authority_now": authority_now})
            if normalized.startswith("select id, status, decision, expires_at from run_tool_permission_requests"):
                # This row expires after the fixed authority time but before
                # the later mutation statements: it must remain valid.
                return Cursor(
                    rows=[
                        {
                            "id": "tpr-valid",
                            "status": "decided",
                            "decision": "allow_for_run",
                            "expires_at": authority_now + timedelta(microseconds=1),
                        }
                    ]
                )
            if normalized.startswith("update runs") and "set status = 'succeeded'" in normalized:
                return Cursor({"id": "run-a"})
            if normalized.startswith("update run_tool_permission_requests"):
                return Cursor(rows=[{"id": "tpr-valid"}])
            raise AssertionError(normalized)

    conn = Connection()

    assert await complete_run(conn, tenant_id="tenant-a", run_id="run-a", result_json={"message": "done"}) is True
    assert "for update" in conn.calls[0][0]
    assert "clock_timestamp() as authority_now" in conn.calls[1][0]
    assert "for update" in conn.calls[2][0]
    assert conn.calls[3][0].startswith("update runs")
    consume_sql, consume_params = conn.calls[4]
    assert "id = any(%s::text[])" in consume_sql
    assert consume_params[-1] == ["tpr-valid"]
    assert all("expires_at > clock_timestamp()" not in sql for sql, _params in conn.calls[2:])


@pytest.mark.asyncio
async def test_complete_run_raises_before_commit_when_exact_grant_consumption_is_partial():
    authority_now = datetime(2026, 7, 16, tzinfo=timezone.utc)
    committed = []

    class Cursor:
        def __init__(self, row=None, rows=None):
            self.row = row
            self.rows = rows or []

        async def fetchone(self):
            return self.row

        async def fetchall(self):
            return self.rows

    class Connection:
        def __init__(self):
            self.pending = []

        async def execute(self, sql, params):
            normalized = " ".join(sql.split())
            if normalized.startswith("select id from runs") and "for update" in normalized:
                return Cursor({"id": "run-a"})
            if "select clock_timestamp() as authority_now" in normalized:
                return Cursor({"authority_now": authority_now})
            if normalized.startswith("select id, status, decision, expires_at from run_tool_permission_requests"):
                return Cursor(
                    rows=[
                        {"id": "tpr-a", "status": "decided", "decision": "allow_for_run", "expires_at": authority_now + timedelta(microseconds=1)},
                        {"id": "tpr-b", "status": "decided", "decision": "allow_for_run", "expires_at": authority_now + timedelta(microseconds=1)},
                    ]
                )
            if normalized.startswith("update runs") and "set status = 'succeeded'" in normalized:
                self.pending.append("run_succeeded")
                return Cursor({"id": "run-a"})
            if normalized.startswith("update run_tool_permission_requests"):
                self.pending.append("grant_consumed")
                return Cursor(rows=[{"id": "tpr-a"}])
            raise AssertionError(normalized)

    @asynccontextmanager
    async def transaction():
        conn = Connection()
        try:
            yield conn
        except Exception:
            raise
        else:
            committed.extend(conn.pending)

    with pytest.raises(RepositoryConflictError, match="allow_for_run_consumption_mismatch"):
        async with transaction() as conn:
            await complete_run(conn, tenant_id="tenant-a", run_id="run-a", result_json={})
    assert committed == []


@pytest.mark.asyncio
async def test_get_admin_runtime_run_summary_counts_statuses_and_redacts_failures():
    class SummaryCursor:
        def __init__(self, row=None, rows=None):
            self.row = row
            self.rows = rows or []

        async def fetchone(self):
            return self.row

        async def fetchall(self):
            return self.rows

    class SummaryConnection:
        def __init__(self):
            self.calls = []

        async def execute(self, sql, params):
            compact = " ".join(sql.lower().split())
            self.calls.append((compact, params))
            if "group by status" in compact:
                return SummaryCursor(
                    rows=[
                        {"status": "queued", "count": 2},
                        {"status": "running", "count": 1},
                        {"status": "failed", "count": 1},
                    ]
                )
            if "from runs" in compact and "error_code" in compact:
                return SummaryCursor(
                    rows=[
                        {
                            "id": "run-failed",
                            "user_id": "user-a",
                            "agent_id": "qa-word-review",
                            "skill_id": "qa-file-reviewer",
                            "error_code": "executor_failure token=run-code-token",
                            "error_message": "failed token=run-message-token /var/lib/ai-platform/x",
                            "created_at": None,
                        }
                    ]
                )
            raise AssertionError(compact)

    summary = await repositories.get_admin_runtime_run_summary(
        SummaryConnection(),
        tenant_id="tenant-a",
        limit=5,
    )

    assert summary["total"] == 4
    assert summary["active"] == 3
    assert summary["terminal"] == 1
    assert summary["by_status"] == {"queued": 2, "running": 1, "failed": 1}
    assert "skill_id" not in summary["recent_failures"][0]
    assert summary["recent_failures"][0]["error_code"] == "executor_failure token=[redacted-secret]"
    assert summary["recent_failures"][0]["error_message"] == ""
    serialized = json.dumps(summary, ensure_ascii=False, default=str)
    assert "qa-file-reviewer" not in serialized
    assert "run-code-token" not in serialized
    assert "run-message-token" not in serialized
    assert "/var/lib/ai-platform" not in serialized


@pytest.mark.asyncio
async def test_get_admin_runtime_admission_summary_counts_same_tenant_active_users():
    class SummaryCursor:
        def __init__(self, row=None, rows=None):
            self.row = row
            self.rows = rows or []

        async def fetchone(self):
            return self.row

        async def fetchall(self):
            return self.rows

    class SummaryConnection:
        def __init__(self):
            self.calls = []

        async def execute(self, sql, params):
            normalized = " ".join(sql.split())
            self.calls.append((normalized, params))
            assert "where tenant_id = %s" in normalized
            assert "status in ('queued', 'running')" in normalized
            assert "input_json" not in normalized
            assert "skill_id" not in normalized
            if "sum(active)" in normalized:
                return SummaryCursor(row={"active_runs": 9, "active_users": 3, "saturated_users": 2})
            return SummaryCursor(
                rows=[
                    {"user_id": "user-a", "active": 3},
                    {"user_id": "user-b", "active": 2},
                ]
            )

    conn = SummaryConnection()

    summary = await repositories.get_admin_runtime_admission_summary(
        conn,
        tenant_id="tenant-a",
        limit=3,
        top_user_limit=2,
    )

    assert summary == {
        "policy_active": True,
        "max_active_runs_per_user": 3,
        "active_runs": 9,
        "active_users": 3,
        "saturated_users": 2,
        "top_users": [
            {"user_id": "user-a", "active": 3, "saturated": True},
            {"user_id": "user-b", "active": 2, "saturated": False},
        ],
    }
    assert conn.calls[0][1] == ("tenant-a", 3, 3)
    assert conn.calls[1][1] == ("tenant-a", 2)


@pytest.mark.asyncio
async def test_get_admin_runtime_admission_summary_disables_saturation_when_limit_off():
    class SummaryCursor:
        def __init__(self, row=None, rows=None):
            self.row = row
            self.rows = rows or []

        async def fetchone(self):
            return self.row

        async def fetchall(self):
            return self.rows

    class SummaryConnection:
        async def execute(self, sql, params):
            normalized = " ".join(sql.split())
            if "sum(active)" in normalized:
                return SummaryCursor(row={"active_runs": 7, "active_users": 1, "saturated_users": 0})
            return SummaryCursor(rows=[{"user_id": "user-a", "active": 7}])

    summary = await repositories.get_admin_runtime_admission_summary(
        SummaryConnection(),
        tenant_id="tenant-a",
        limit=0,
        top_user_limit=10,
    )

    assert summary["policy_active"] is False
    assert summary["max_active_runs_per_user"] == 0
    assert summary["active_runs"] == 7
    assert summary["active_users"] == 1
    assert summary["saturated_users"] == 0
    assert summary["top_users"] == [{"user_id": "user-a", "active": 7, "saturated": False}]


@pytest.mark.asyncio
async def test_get_admin_runtime_observability_summary_coerces_nulls_to_defaults():
    class SummaryCursor:
        def __init__(self, row):
            self.row = row

        async def fetchone(self):
            return self.row

    class SummaryConnection:
        def __init__(self):
            self.calls = []

        async def execute(self, sql, params):
            self.calls.append((" ".join(sql.split()), params))
            assert "tenant_id = %s" in sql
            assert all(param == "tenant-a" for param in params)
            return SummaryCursor(
                {
                    "event_count": None,
                    "artifact_count": None,
                    "error_count": None,
                    "error_types": None,
                    "avg_latency_ms": None,
                    "max_latency_ms": None,
                    "p50_latency_ms": None,
                    "p95_latency_ms": None,
                    "p99_latency_ms": None,
                    "input_token_count": None,
                    "output_token_count": None,
                    "total_token_count": None,
                    "estimated_cost_minor": None,
                }
            )

    summary = await repositories.get_admin_runtime_observability_summary(
        SummaryConnection(),
        tenant_id="tenant-a",
    )

    assert summary == {
        "event_count": 0,
        "artifact_count": 0,
        "error_count": 0,
        "error_types": {},
        "error_categories": {},
        "latency_ms": {"avg": None, "max": None, "p50": None, "p95": None, "p99": None},
        "token_counts": {"input": 0, "output": 0, "total": 0},
        "estimated_cost_minor": 0,
    }


@pytest.mark.asyncio
async def test_get_admin_runtime_observability_summary_uses_run_totals_for_terminal_token_cost():
    class SummaryCursor:
        def __init__(self, row):
            self.row = row

        async def fetchone(self):
            return self.row

    class SummaryConnection:
        async def execute(self, sql, params):
            compact = " ".join(sql.lower().split())
            assert all(param == "tenant-a" for param in params)
            assert len(params) == 5
            assert compact.count("tenant_id = %s") == 5
            assert "+ event_summary.event_input_token_count" not in compact
            assert "+ event_summary.event_output_token_count" not in compact
            assert "+ event_summary.event_total_token_count" not in compact
            assert "+ event_summary.event_estimated_cost_minor" not in compact
            assert "percentile_cont(0.5)" in compact
            assert "percentile_cont(0.95)" in compact
            assert "percentile_cont(0.99)" in compact
            return SummaryCursor(
                {
                    "event_count": 2,
                    "artifact_count": 1,
                    "error_count": 2,
                    "error_types": {"executor_failure": 2},
                    "avg_latency_ms": 250,
                    "max_latency_ms": 300,
                    "p50_latency_ms": 240,
                    "p95_latency_ms": 295,
                    "p99_latency_ms": 299,
                    "input_token_count": 10,
                    "output_token_count": 20,
                    "total_token_count": 30,
                    "estimated_cost_minor": 7,
                }
            )

    summary = await repositories.get_admin_runtime_observability_summary(
        SummaryConnection(),
        tenant_id="tenant-a",
    )

    assert summary["event_count"] == 2
    assert summary["error_count"] == 2
    assert summary["error_types"] == {"executor_failure": 2}
    assert summary["error_categories"] == {"executor": 2}
    assert summary["latency_ms"] == {"avg": 250, "max": 300, "p50": 240, "p95": 295, "p99": 299}
    assert summary["token_counts"] == {"input": 10, "output": 20, "total": 30}
    assert summary["estimated_cost_minor"] == 7
