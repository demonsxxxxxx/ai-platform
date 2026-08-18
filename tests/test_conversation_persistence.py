from datetime import datetime, timezone

import pytest

from app.conversations.infrastructure import postgres as conversation_persistence
from app.persistence import RepositoryNotFoundError


class Cursor:
    def __init__(self, *, one=None, many=None):
        self.one = one
        self.many = many or []

    async def fetchone(self):
        return self.one

    async def fetchall(self):
        return self.many


class RecordingConnection:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    async def execute(self, sql, params):
        self.calls.append((" ".join(sql.split()), params))
        return next(self.responses)


@pytest.mark.asyncio
async def test_create_session_keeps_workspace_guard_before_the_idempotent_insert():
    conn = RecordingConnection([Cursor(one=None)])

    with pytest.raises(RepositoryNotFoundError, match="workspace_not_found"):
        await conversation_persistence.create_session(
            conn,
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            user_id="user-a",
            agent_id="general-agent",
            title="General",
            session_id="session-a",
        )

    assert len(conn.calls) == 1
    assert "from workspaces" in conn.calls[0][0]
    assert conn.calls[0][1] == ("tenant-a", "workspace-a")


@pytest.mark.asyncio
async def test_message_history_keeps_principal_cursor_scope_and_limit_bound():
    cursor_time = datetime(2026, 8, 13, tzinfo=timezone.utc)
    conn = RecordingConnection([Cursor(many=[])])

    rows = await conversation_persistence.list_authorized_messages(
        conn,
        tenant_id="tenant-a",
        user_id="user-a",
        session_id="session-a",
        cursor=(cursor_time, "msg-a"),
        limit=999,
    )

    assert rows == []
    sql, params = conn.calls[0]
    assert "sessions.user_id = %s" in sql
    assert "(messages.created_at, messages.id) > (%s, %s)" in sql
    assert "order by messages.created_at asc, messages.id asc" in sql
    assert params == (
        "tenant-a",
        "session-a",
        "user-a",
        cursor_time,
        "msg-a",
        201,
    )


@pytest.mark.asyncio
async def test_agent_history_keeps_revision_hash_binding_and_descending_keyset():
    updated_at = datetime(2026, 8, 13, 2, tzinfo=timezone.utc)
    created_at = datetime(2026, 8, 13, 1, tzinfo=timezone.utc)
    conn = RecordingConnection([Cursor(many=[])])

    rows = await conversation_persistence.list_authorized_agent_conversations(
        conn,
        tenant_id="tenant-a",
        user_id="user-a",
        agent_id="agent-a",
        revision=7,
        cursor=(updated_at, created_at, "session-a"),
        limit=21,
    )

    assert rows == []
    sql, params = conn.calls[0]
    assert "profile.content_hash = sessions.admitted_agent_profile_hash" in sql
    assert "sessions.admitted_agent_profile_revision = %s" in sql
    assert "sessions.purpose = 'conversation'" in sql
    assert "order by sessions.updated_at desc, sessions.created_at desc, sessions.id desc" in sql
    assert params == (
        "tenant-a",
        "user-a",
        "agent-a",
        7,
        updated_at,
        updated_at,
        created_at,
        updated_at,
        created_at,
        "session-a",
        21,
    )
