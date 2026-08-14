import base64
import json
from contextlib import asynccontextmanager

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.auth import AuthPrincipal
from app.main import create_app
from app import repositories
from app import agent_conversation_repository
from app.chat_session_projection import session_response
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
async def test_ordinary_session_repository_excludes_pinned_agent_conversations():
    captured = {}

    class Cursor:
        async def fetchall(self):
            return []

    class Connection:
        async def execute(self, query, params):
            captured["query"] = query
            captured["params"] = params
            return Cursor()

    assert await repositories.list_authorized_sessions(
        Connection(), tenant_id="tenant-a", user_id="user-a"
    ) == []
    normalized = " ".join(captured["query"].split()).lower()
    assert "sessions.admitted_agent_profile_revision is null" in normalized
    assert captured["params"] == ("tenant-a", "user-a")


@pytest.mark.asyncio
async def test_agent_conversation_repository_selects_complete_pinned_public_identity():
    captured = []

    class Cursor:
        async def fetchall(self):
            return []

        async def fetchone(self):
            return None

    class Connection:
        async def execute(self, query, params):
            captured.append((query, params))
            return Cursor()

    assert await agent_conversation_repository.list_authorized_agent_conversations(
        Connection(),
        tenant_id="tenant-a",
        user_id="user-a",
        agent_id="agt_support",
        revision=7,
        cursor=None,
        limit=21,
    ) == []
    assert await repositories.get_authorized_session_projection(
        Connection(),
        tenant_id="tenant-a",
        user_id="user-a",
        session_id="ses_1",
    ) is None
    normalized = " ".join(captured[0][0].split()).lower()
    detail_normalized = " ".join(captured[1][0].split()).lower()
    for field in (
        "welcome_message",
        "starter_prompts",
        "capability_summary",
        "recommended_tasks",
        "supported_input_types",
        "expected_outputs",
        "permissions_and_data_access_notice",
        "avatar_ref",
        "category",
        "published_at",
    ):
        assert f"profile.{field} as agent_profile_{field}" in normalized
        assert f"profile.{field} as agent_profile_{field}" in detail_normalized
    assert "profile.tenant_id = sessions.tenant_id" in normalized
    assert "profile.agent_id = sessions.agent_id" in normalized
    assert "profile.revision = sessions.admitted_agent_profile_revision" in normalized
    assert "profile.content_hash = sessions.admitted_agent_profile_hash" in normalized
    assert "sessions.purpose" in normalized
    assert "sessions.purpose = 'conversation'" in normalized
    assert captured[0][1] == ("tenant-a", "user-a", "agt_support", 7, 21)
    assert captured[1][1] == ("tenant-a", "user-a", "ses_1")


@pytest.mark.asyncio
async def test_agent_conversation_repository_excludes_builder_test_sessions():
    captured = {}

    class Cursor:
        async def fetchall(self):
            return [
                {
                    "id": "ses_conversation",
                    "workspace_id": "default",
                    "agent_id": "agt_support",
                    "title": "Support",
                    "purpose": "conversation",
                }
            ]

    class Connection:
        async def execute(self, query, params):
            captured["query"] = " ".join(query.split()).lower()
            captured["params"] = params
            return Cursor()

    rows = await agent_conversation_repository.list_authorized_agent_conversations(
        Connection(),
        tenant_id="tenant-a",
        user_id="user-a",
        agent_id="agt_support",
        revision=7,
        cursor=None,
        limit=20,
    )

    assert [row["id"] for row in rows] == ["ses_conversation"]
    assert "sessions.purpose = 'conversation'" in captured["query"]
    assert "sessions.purpose" in captured["query"].split(" from sessions", 1)[0]


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
            "agent_profile_welcome_message": "Upload a policy for review.",
            "agent_profile_starter_prompts": ["Review this policy"],
            "agent_profile_capability_summary": "Reviews support policy files.",
            "agent_profile_recommended_tasks": ["Policy review"],
            "agent_profile_supported_input_types": ["text", "file"],
            "agent_profile_expected_outputs": ["Review memo"],
            "agent_profile_permissions_and_data_access_notice": "Uses authorized files only.",
            "agent_profile_avatar_ref": "builtin:assistant",
            "agent_profile_category": "support",
            "agent_profile_published_at": datetime(2026, 7, 31, tzinfo=timezone.utc),
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
    identity = response.sessions[0].agent_conversation
    assert identity is not None
    assert identity.model_dump(mode="json") == {
        "agent_id": "agt_support",
        "revision": 7,
        "name": "Support assistant",
        "description": "Approved support help.",
        "avatar_ref": "builtin:assistant",
        "category": "support",
        "welcome_message": "Upload a policy for review.",
        "starter_prompts": ["Review this policy"],
        "capability_summary": "Reviews support policy files.",
        "recommended_tasks": ["Policy review"],
        "supported_input_types": ["text", "file"],
        "expected_outputs": ["Review memo"],
        "permissions_and_data_access_notice": "Uses authorized files only.",
        "avatar_seed": "",
        "published_at": "2026-07-31T00:00:00Z",
    }


def test_agent_conversation_projection_keeps_legacy_text_only_defaults_and_no_private_fields():
    projection = session_response(
        {
            "id": "ses_legacy",
            "workspace_id": "default",
            "agent_id": "agt_support",
            "title": "Legacy support",
            "admitted_agent_profile_revision": 2,
            "agent_profile_name": "Support assistant",
            "agent_profile_description": "Approved support help.",
            "created_at": None,
            "updated_at": None,
            "instructions": "private",
            "model_id": "private-model",
            "mcp_tool_ids": ["private-tool"],
            "content_hash": "private-hash",
        }
    )

    identity = projection.agent_conversation
    assert identity is not None
    assert identity.supported_input_types == ["text"]
    assert not {
        "instructions",
        "model_id",
        "mcp_tool_ids",
        "content_hash",
    }.intersection(identity.model_dump())


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
