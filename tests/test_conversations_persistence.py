from datetime import datetime, timezone
import pytest
from app.conversations.infrastructure import postgres as conversation_history
import app.conversations.infrastructure.postgres as _repo_owner_app_conversations_infrastructure_postgres
import app.conversations.infrastructure.session_queries_postgres as _repo_owner_app_conversations_infrastructure_session_queries_postgres
import app.persistence.chat_submissions as _repo_owner_app_persistence_chat_submissions
import app.runs.infrastructure.creation_postgres as _repo_owner_app_runs_infrastructure_creation_postgres
from tests.support.repository_fixtures import RecordingConnection, SingleRowCursor


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


def test_chat_submission_fingerprint_is_canonical_and_scope_bound():
    first = _repo_owner_app_persistence_chat_submissions.chat_submission_fingerprint(
        {
            "message": "same message",
            "workspace_id": "default",
            "file_ids": ["file-a", "file-b"],
            "input": {"b": 2, "a": 1},
        },
        tenant_id="tenant-a",
        user_id="user-a",
    )
    reordered = _repo_owner_app_persistence_chat_submissions.chat_submission_fingerprint(
        {
            "input": {"a": 1, "b": 2},
            "file_ids": ["file-a", "file-b"],
            "message": "same message",
            "workspace_id": "default",
        },
        tenant_id="tenant-a",
        user_id="user-a",
    )
    changed_scope = _repo_owner_app_persistence_chat_submissions.chat_submission_fingerprint(
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
@pytest.mark.parametrize(
    "lookup",
    [_repo_owner_app_conversations_infrastructure_session_queries_postgres.get_authorized_session, _repo_owner_app_conversations_infrastructure_postgres.get_authorized_lambchat_session],
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
    session = await _repo_owner_app_conversations_infrastructure_session_queries_postgres.get_authorized_session(
        conn,
        tenant_id="tenant-a",
        user_id="user-a",
        session_id="session-a",
        workspace_id="workspace-a",
        for_update=True,
    )
    assert session is not None
    generation = await _repo_owner_app_runs_infrastructure_creation_postgres.allocate_session_run_generation(
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

    assert await _repo_owner_app_runs_infrastructure_creation_postgres.get_authorized_run(
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
    assert await _repo_owner_app_runs_infrastructure_creation_postgres.get_authorized_run(
        conn,
        tenant_id="tenant-a",
        user_id="user-a",
        run_id="run-a",
    ) is None

    session["status"] = "active"
    session["agent_id"] = "other-agent"
    assert await _repo_owner_app_runs_infrastructure_creation_postgres.get_authorized_run(
        conn,
        tenant_id="tenant-a",
        user_id="user-a",
        run_id="run-a",
    ) is None
    assert await _repo_owner_app_runs_infrastructure_creation_postgres.get_authorized_run(
        conn,
        tenant_id="tenant-a",
        user_id="user-b",
        run_id="run-a",
    ) is None


@pytest.mark.asyncio
async def test_session_action_repositories_bind_tenant_and_active_terminal_state():
    conn = RecordingConnection()

    await _repo_owner_app_conversations_infrastructure_postgres.get_session_for_action(
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

    await _repo_owner_app_conversations_infrastructure_postgres.update_session_title(
        conn,
        tenant_id="tenant-a",
        session_id="session-a",
        title="Renamed",
        title_source="user",
    )
    rename_sql, rename_params = conn.calls[-1]
    assert "update sessions" in rename_sql
    assert "status = 'active'" in rename_sql
    assert rename_params == ("Renamed", "user", "tenant-a", "session-a")

    await _repo_owner_app_conversations_infrastructure_postgres.update_session_title(
        conn,
        tenant_id="tenant-a",
        session_id="session-a",
        title="First task",
        title_source="generated",
        expected_title_source="initial",
    )
    initialize_sql, initialize_params = conn.calls[-1]
    assert "title_source = %s" in initialize_sql
    assert initialize_params == ("First task", "generated", "tenant-a", "session-a", "initial")

    await _repo_owner_app_conversations_infrastructure_postgres.mark_session_deleted(
        conn,
        tenant_id="tenant-a",
        session_id="session-a",
    )
    delete_sql, delete_params = conn.calls[-1]
    assert "set status = 'deleted'" in delete_sql
    assert "status = 'active'" in delete_sql
    assert delete_params == ("tenant-a", "session-a")

    await _repo_owner_app_conversations_infrastructure_postgres.list_session_messages_for_fork(
        conn,
        tenant_id="tenant-a",
        session_id="session-a",
    )
    messages_sql, messages_params = conn.calls[-1]
    assert "from messages" in messages_sql
    assert "tenant_id = %s and session_id = %s" in messages_sql
    assert "order by created_at asc, id asc" in messages_sql
    assert "limit %s" in messages_sql
    assert messages_params == ("tenant-a", "session-a", 201)


@pytest.mark.asyncio
async def test_initialize_session_title_returns_none_when_user_rename_wins():
    class NoUpdateCursor:
        async def fetchone(self):
            return None

    class NoUpdateConnection:
        async def execute(self, _sql, *_params):
            return NoUpdateCursor()

    assert await _repo_owner_app_conversations_infrastructure_postgres.update_session_title(
        NoUpdateConnection(),
        tenant_id="tenant-a",
        session_id="session-a",
        title="First task",
        title_source="generated",
        expected_title_source="initial",
    ) is None


@pytest.mark.asyncio
async def test_authorized_session_runs_use_canonical_legacy_tie_break_order():
    conn = RecordingConnection()

    await _repo_owner_app_conversations_infrastructure_session_queries_postgres.list_authorized_session_runs(
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

    await _repo_owner_app_conversations_infrastructure_session_queries_postgres.list_authorized_session_runs(
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


@pytest.mark.asyncio
async def test_authorized_messages_bind_tenant_session_owner_and_stable_order():
    conn = RecordingConnection()

    await _repo_owner_app_conversations_infrastructure_postgres.list_authorized_messages(
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
    assert "limit %s" in sql
    assert params == ("tenant-a", "session-a", "user-a", 101)

    boundary = datetime(2026, 8, 9, tzinfo=timezone.utc)
    await _repo_owner_app_conversations_infrastructure_postgres.list_authorized_messages(
        conn,
        tenant_id="tenant-a",
        user_id="user-a",
        session_id="session-a",
        cursor=(boundary, "msg-a"),
        limit=50,
    )
    sql, params = conn.calls[-1]
    assert "(messages.created_at, messages.id) > (%s, %s)" in sql
    assert params == ("tenant-a", "session-a", "user-a", boundary, "msg-a", 50)


@pytest.mark.asyncio
async def test_authorized_user_messages_for_runs_minimize_and_scope_in_sql():
    query = _repo_owner_app_conversations_infrastructure_postgres.list_authorized_user_messages_for_runs
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
    query = _repo_owner_app_conversations_infrastructure_postgres.list_authorized_user_messages_for_runs
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
async def test_agent_conversation_history_query_is_principal_scoped_and_keyset_paginated():
    class Cursor:
        async def fetchall(self):
            return [{"id": "ses_older"}]

    class RecordingConnection:
        def __init__(self):
            self.calls = []

        async def execute(self, sql, params):
            self.calls.append((" ".join(sql.split()).lower(), params))
            return Cursor()

    updated_at = datetime(2026, 8, 3, 1, tzinfo=timezone.utc)
    created_at = datetime(2026, 8, 3, tzinfo=timezone.utc)
    conn = RecordingConnection()

    rows = await conversation_history.list_authorized_agent_conversations(
        conn,
        tenant_id="tenant-a",
        user_id="user-a",
        agent_id="agt_support",
        revision=7,
        cursor=(updated_at, created_at, "ses_boundary"),
        limit=21,
    )

    assert rows == [{"id": "ses_older"}]
    sql, params = conn.calls[-1]
    assert "sessions.tenant_id = %s" in sql
    assert "sessions.user_id = %s" in sql
    assert "sessions.agent_id = %s" in sql
    assert "sessions.admitted_agent_profile_revision = %s" in sql
    assert "sessions.status = 'active'" in sql
    assert "join agent_profile_revisions profile" in sql
    assert "profile.content_hash = sessions.admitted_agent_profile_hash" in sql
    assert "coalesce(legacy_first_user.title, sessions.title) as title" in sql
    assert "left join lateral" in sql
    assert "sessions.title_source = 'initial'" in sql
    assert "sessions.title = profile.name" in sql
    assert "messages.tenant_id = sessions.tenant_id" in sql
    assert "messages.session_id = sessions.id" in sql
    assert "messages.role = 'user'" in sql
    normalizer = (
        "translate( messages.content, "
        "chr(13) || chr(10) || chr(9) || chr(11) || chr(12), ' ' )"
    )
    assert sql.count(normalizer) == 2
    assert f"btrim( {normalizer} ) <> ''" in sql
    assert f"left( btrim( {normalizer} ), 32 )" in sql
    assert "e'\\r\\n\\t\\v\\f'" not in sql
    assert "order by messages.created_at asc, messages.id asc limit 1" in sql
    assert "sessions.updated_at < %s" in sql
    assert "sessions.id < %s" in sql
    assert (
        "order by sessions.updated_at desc, sessions.created_at desc, sessions.id desc"
        in sql
    )
    assert params == (
        "tenant-a",
        "user-a",
        "agt_support",
        7,
        updated_at,
        updated_at,
        created_at,
        updated_at,
        created_at,
        "ses_boundary",
        21,
    )
