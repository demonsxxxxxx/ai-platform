import base64
import json
from contextlib import asynccontextmanager

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.auth import AuthPrincipal
from app.main import create_app
from app.routes.chat_sessions import list_sessions
from app.settings import Settings


@asynccontextmanager
async def fake_transaction():
    yield object()


def principal(**overrides):
    values = {"user_id": "user-a", "display_name": "User A", "tenant_id": "tenant-a"}
    values.update(overrides)
    return AuthPrincipal(**values)


@pytest.fixture
def chat_submission_client(monkeypatch):
    monkeypatch.setattr(
        "app.auth.get_settings",
        lambda: Settings(frontend_poc_auth_enabled=True),
    )
    with TestClient(create_app(), raise_server_exceptions=False) as client:
        yield client


_CHAT_SUBMISSION_ROUTE_PREFIXES = ("/api", "/api/ai")
_CHAT_SUBMISSION_CLIENT_HEADERS = {
    "x-ai-user-id": "user-a",
    "x-ai-tenant-id": "tenant-a",
}


@pytest.mark.asyncio
async def test_list_sessions_returns_authorized_rows(monkeypatch):
    async def fake_list_authorized_sessions(conn, *, tenant_id, user_id):
        assert user_id == "user-a"
        return [
            {
                "id": "ses_1",
                "workspace_id": "default",
                "agent_id": "document-review",
                "title": "Doc Review",
                "created_at": None,
                "updated_at": None,
            }
        ]

    monkeypatch.setattr("app.routes.chat_sessions.transaction", fake_transaction)
    monkeypatch.setattr(
        "app.routes.chat_sessions.repositories.list_authorized_sessions",
        fake_list_authorized_sessions,
    )

    response = await list_sessions(principal=principal())

    assert response.sessions[0].session_id == "ses_1"
    assert response.sessions[0].agent_id == "document-review"


@pytest.mark.asyncio
async def test_list_sessions_returns_one_agent_revision_page_with_opaque_cursor(
    monkeypatch,
):
    from datetime import datetime, timezone

    rows = [
        {
            "id": f"ses_{index}",
            "workspace_id": "default",
            "agent_id": "agt_support",
            "title": f"Support {index}",
            "admitted_agent_profile_revision": 7,
            "admitted_agent_profile_hash": "a" * 64,
            "agent_profile_name": "Support assistant",
            "agent_profile_description": "Approved support help.",
            "agent_profile_avatar_ref": "builtin:assistant",
            "agent_profile_category": "support",
            "created_at": datetime(2026, 8, index, tzinfo=timezone.utc),
            "updated_at": datetime(2026, 8, index, 1, tzinfo=timezone.utc),
        }
        for index in (3, 2, 1)
    ]

    async def fake_list_agent_conversations(
        conn, *, tenant_id, user_id, agent_id, revision, cursor, limit
    ):
        assert (tenant_id, user_id) == ("tenant-a", "user-a")
        assert (agent_id, revision, cursor, limit) == ("agt_support", 7, None, 3)
        return rows

    monkeypatch.setattr("app.routes.chat_sessions.transaction", fake_transaction)
    monkeypatch.setattr(
        "app.routes.chat_sessions.agent_conversation_repository.list_authorized_agent_conversations",
        fake_list_agent_conversations,
    )

    response = await list_sessions(
        agent_id="agt_support",
        revision=7,
        cursor=None,
        limit=2,
        principal=principal(),
    )

    assert [session.session_id for session in response.sessions] == ["ses_3", "ses_2"]
    assert response.next_cursor is not None
    padding = "=" * (-len(response.next_cursor) % 4)
    cursor_payload = json.loads(
        base64.urlsafe_b64decode(f"{response.next_cursor}{padding}").decode("utf-8")
    )
    assert cursor_payload == {
        "created_at": "2026-08-02T00:00:00+00:00",
        "session_id": "ses_2",
        "updated_at": "2026-08-02T01:00:00+00:00",
        "v": 1,
    }


@pytest.mark.asyncio
async def test_list_sessions_preserves_owned_history_without_current_publication(
    monkeypatch,
):
    calls: list[tuple[object, ...]] = []

    async def list_owned_history(
        conn, *, tenant_id, user_id, agent_id, revision, cursor, limit
    ):
        calls.append((tenant_id, user_id, agent_id, revision, cursor, limit))
        return []

    monkeypatch.setattr("app.routes.chat_sessions.transaction", fake_transaction)
    monkeypatch.setattr(
        "app.routes.chat_sessions.agent_conversation_repository.list_authorized_agent_conversations",
        list_owned_history,
    )

    response = await list_sessions(
        agent_id="agt_support",
        revision=7,
        cursor=None,
        limit=20,
        principal=principal(),
    )

    assert response.sessions == []
    assert response.next_cursor is None
    assert calls == [("tenant-a", "user-a", "agt_support", 7, None, 21)]


@pytest.mark.parametrize("prefix", _CHAT_SUBMISSION_ROUTE_PREFIXES)
def test_agent_conversation_history_contract_is_mounted_on_chat_aliases(
    monkeypatch, chat_submission_client, prefix
):
    from datetime import datetime, timezone

    async def list_page(
        _conn, *, tenant_id, user_id, agent_id, revision, cursor, limit
    ):
        assert (tenant_id, user_id, agent_id, revision, cursor, limit) == (
            "tenant-a",
            "user-a",
            "agt_support",
            7,
            None,
            2,
        )
        return [
            {
                "id": f"ses_{index}",
                "workspace_id": "default",
                "agent_id": "agt_support",
                "title": f"Support {index}",
                "admitted_agent_profile_revision": 7,
                "admitted_agent_profile_hash": "a" * 64,
                "agent_profile_name": "Support assistant",
                "agent_profile_description": "Approved support help.",
                "agent_profile_avatar_ref": "builtin:assistant",
                "agent_profile_category": "support",
                "created_at": datetime(2026, 8, index, tzinfo=timezone.utc),
                "updated_at": datetime(2026, 8, index, 1, tzinfo=timezone.utc),
            }
            for index in (2, 1)
        ]

    monkeypatch.setattr("app.routes.chat_sessions.transaction", fake_transaction)
    monkeypatch.setattr(
        "app.routes.chat_sessions.agent_conversation_repository.list_authorized_agent_conversations",
        list_page,
    )

    response = chat_submission_client.get(
        f"{prefix}/chat/sessions?agent_id=agt_support&revision=7&limit=1",
        headers=_CHAT_SUBMISSION_CLIENT_HEADERS,
    )

    assert response.status_code == 200
    body = response.json()
    assert [session["session_id"] for session in body["sessions"]] == ["ses_2"]
    assert body["sessions"][0]["agent_conversation"]["revision"] == 7
    assert isinstance(body["next_cursor"], str)


@pytest.mark.asyncio
async def test_list_sessions_rejects_incomplete_or_invalid_agent_cursor_scope():
    with pytest.raises(HTTPException) as incomplete:
        await list_sessions(
            agent_id="agt_support",
            revision=None,
            cursor=None,
            limit=20,
            principal=principal(),
        )
    assert incomplete.value.status_code == 400
    assert incomplete.value.detail == "agent_conversation_scope_incomplete"

    with pytest.raises(HTTPException) as invalid_cursor:
        await list_sessions(
            agent_id="agt_support",
            revision=7,
            cursor="not-a-cursor",
            limit=20,
            principal=principal(),
        )
    assert invalid_cursor.value.status_code == 400
    assert invalid_cursor.value.detail == "session_cursor_invalid"
