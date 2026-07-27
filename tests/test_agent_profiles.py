import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.agent_profiles import (
    profile_public_projection,
    reject_profile_selector_conflicts,
    resolve_profile_for_admission,
)
from app.auth import AuthPrincipal
from app.models import (
    AgentProfileDraftRequest,
    ChatStreamRequest,
    SelectedAgentProfileRequest,
    SelectedSkillRequest,
)
from app.repositories import RepositoryConflictError
from app.main import create_app


def auth_settings():
    return type("S", (), {"trusted_principal_secret": "test-secret", "frontend_poc_auth_enabled": False})()


@asynccontextmanager
async def fake_transaction():
    yield object()


def ordinary_headers():
    return {
        "x-ai-user-id": "user-a",
        "x-ai-user-name": "User A",
        "x-ai-tenant-id": "tenant-a",
        "x-ai-roles": "user",
        "x-ai-gateway-secret": "test-secret",
    }


def test_profile_public_projection_never_exposes_private_execution_definition():
    projection = profile_public_projection(
        {
            "agent_id": "agt_support",
            "revision": 4,
            "name": "Support assistant",
            "description": "Helps employees with approved support requests.",
            "instructions": "Never expose this private instruction.",
            "model_id": "internal-model",
            "skill_id": "support-skill",
            "skill_version": "sha256-pinned",
            "mcp_tool_ids": ["internal-tool"],
            "content_hash": "profile-hash",
            "status": "published",
        }
    )

    assert projection == {
        "agent_id": "agt_support",
        "expected_revision": 4,
        "name": "Support assistant",
        "description": "Helps employees with approved support requests.",
    }


def test_selected_profile_rejects_client_owned_capability_selectors():
    request = ChatStreamRequest(
        message="Help me",
        selected_agent_profile=SelectedAgentProfileRequest(
            agent_id="agt_support",
            expected_revision=4,
        ),
        selected_skill=SelectedSkillRequest(
            skill_id="support-skill",
            expected_version="sha256-pinned",
        ),
    )

    try:
        reject_profile_selector_conflicts(request)
    except HTTPException as exc:
        assert exc.status_code == 400
        assert exc.detail == "agent_profile_selector_conflict"
    else:
        raise AssertionError("client-owned Skill selection must be rejected")


def test_selected_profile_rejects_both_legacy_and_canonical_model_selectors():
    for selector in ({"model": "legacy-model"}, {"model_id": "catalog-model"}):
        request = ChatStreamRequest(
            message="Help me",
            selected_agent_profile=SelectedAgentProfileRequest(
                agent_id="agt_support",
                expected_revision=4,
            ),
            agent_options=selector,
        )
        with pytest.raises(HTTPException) as caught:
            reject_profile_selector_conflicts(request)
        assert caught.value.status_code == 400
        assert caught.value.detail == "agent_profile_selector_conflict"


def test_selected_profile_is_an_optimistic_revision_lock():
    request = ChatStreamRequest(
        message="Help me",
        selected_agent_profile=SelectedAgentProfileRequest(
            agent_id="agt_support",
            expected_revision=4,
        ),
    )

    assert request.selected_agent_profile.agent_id == "agt_support"
    assert request.selected_agent_profile.expected_revision == 4


def test_agent_profile_market_requires_authenticated_principal():
    response = TestClient(create_app()).get("/api/ai/agent-profiles")

    assert response.status_code == 401


def test_agent_profile_market_returns_only_safe_projection(monkeypatch):
    from app.models import AgentProfilePublicProjection

    async def profiles(_conn, *, principal):
        assert principal.tenant_id == "tenant-a"
        return [
            AgentProfilePublicProjection(
                agent_id="agt_support",
                expected_revision=4,
                name="Support assistant",
                description="Approved support helper.",
            )
        ]

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.agent_apps.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.agent_apps.list_public_profiles", profiles)

    response = TestClient(create_app()).get("/api/ai/agent-profiles", headers=ordinary_headers())

    assert response.status_code == 200
    assert response.json() == {
        "agent_profiles": [
            {
                "agent_id": "agt_support",
                "expected_revision": 4,
                "name": "Support assistant",
                "description": "Approved support helper.",
            }
        ]
    }


def test_agent_profile_admin_write_requires_admin(monkeypatch):
    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    response = TestClient(create_app()).post(
        "/api/ai/admin/agent-profiles",
        headers=ordinary_headers(),
        json={
            "name": "Support assistant",
            "description": "Approved support helper.",
            "instructions": "Keep answers concise.",
            "model_id": "model-a",
            "selected_skill": {"skill_id": "general-chat", "expected_version": "version-a"},
            "mcp_tool_ids": [],
            "expected_draft_revision": 0,
        },
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "not_ai_admin"


def test_agent_profile_admin_publish_requires_admin(monkeypatch):
    monkeypatch.setattr("app.auth.get_settings", auth_settings)

    response = TestClient(create_app()).post(
        "/api/ai/admin/agent-profiles/agt_support/publish",
        headers=ordinary_headers(),
        json={"expected_revision": 4},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "not_ai_admin"


async def test_agent_profile_repository_list_is_tenant_scoped():
    from app.repositories import list_latest_agent_profile_revisions

    class Cursor:
        async def fetchall(self):
            return []

    class RecordingConnection:
        def __init__(self):
            self.calls = []

        async def execute(self, sql, params):
            self.calls.append((" ".join(sql.split()), params))
            return Cursor()

    conn = RecordingConnection()
    assert await list_latest_agent_profile_revisions(conn, tenant_id="tenant-a", status="published") == []

    sql, params = conn.calls[-1]
    assert "where agent_profile_revisions.tenant_id = %s" in sql
    assert "agents.tenant_id = agent_profile_revisions.tenant_id" in sql
    assert params == ("tenant-a", "published")


async def test_agent_profile_cross_tenant_selection_is_rejected_as_stale(monkeypatch):
    observed: dict[str, object] = {}

    async def missing_profile(_conn, **kwargs):
        observed.update(kwargs)
        return None

    monkeypatch.setattr("app.agent_profiles.repositories.get_agent_profile_revision", missing_profile)
    principal = AuthPrincipal(
        user_id="user-a",
        display_name="User A",
        tenant_id="tenant-a",
        roles=["user"],
    )

    with pytest.raises(HTTPException) as caught:
        await resolve_profile_for_admission(
            object(),
            principal=principal,
            selection=SelectedAgentProfileRequest(agent_id="agt_other_tenant", expected_revision=4),
        )

    assert caught.value.status_code == 409
    assert caught.value.detail == "agent_profile_revision_stale"
    assert observed == {
        "tenant_id": "tenant-a",
        "agent_id": "agt_other_tenant",
        "revision": 4,
        "status": "published",
    }


def test_agent_profile_schema_is_idempotent_and_legacy_rows_can_remain_unpinned():
    schema = Path("app/schema.sql").read_text(encoding="utf-8")

    assert "create table if not exists agent_profile_revisions" in schema
    assert "create index if not exists idx_agent_profile_revisions_published" in schema
    for statement in (
        "alter table sessions add column if not exists admitted_agent_profile_revision bigint;",
        "alter table sessions add column if not exists admitted_agent_profile_hash text;",
        "alter table runs add column if not exists admitted_agent_profile_revision bigint;",
        "alter table runs add column if not exists admitted_agent_profile_hash text;",
    ):
        assert statement in schema
    assert "admitted_agent_profile_revision bigint not null" not in schema
    assert "admitted_agent_profile_hash text not null" not in schema
    assert "constraint uq_agents_tenant_id unique (tenant_id, id)" in schema
    assert "fk_agent_profile_revisions_tenant_agent" in schema
    assert "fk_sessions_agent_profile_pin" in schema
    assert "fk_runs_agent_profile_pin" in schema
    assert "published_from_revision bigint" in schema
    assert "idx_agent_profile_revisions_published_from_draft" in schema


def test_legacy_run_snapshot_without_agent_profile_remains_compatible():
    from app.repositories import copied_run_execution_snapshot

    snapshot = copied_run_execution_snapshot(
        {
            "file_ids": [],
            "input": {"message": "existing run"},
            "executor_type": "claude-agent-worker",
            "skill_version": "hash-a",
            "release_decision": {"selected_version": "hash-a"},
            "skill_manifests": [{"skill_id": "general-chat", "content_hash": "hash-a"}],
            "schema_version": "ai-platform.run-payload.v1",
        }
    )

    assert "agent_profile" not in snapshot


def test_profile_copy_snapshot_preserves_private_prompt_model_and_exact_pins():
    from app.repositories import (
        admitted_agent_profile_pins_for_copy,
        copied_run_execution_snapshot,
        preserved_server_owned_execution_snapshot,
    )

    source_snapshot = copied_run_execution_snapshot(
        {
            "input": {"message": "retry only this user request"},
            "model_id": "catalog-model-a",
            "model_value": "provider-model-a",
            "agent_profile": {
                "agent_id": "agt_support",
                "revision": 4,
                "content_hash": "a" * 64,
                "instructions": "Private profile instruction",
            },
        }
    )
    source_run = {
        "agent_id": "agt_support",
        "admitted_agent_profile_revision": 4,
        "admitted_agent_profile_hash": "a" * 64,
    }

    assert admitted_agent_profile_pins_for_copy(source_run, source_snapshot) == (4, "a" * 64)
    assert preserved_server_owned_execution_snapshot(source_snapshot) == {
        "model_id": "catalog-model-a",
        "model_value": "provider-model-a",
        "agent_profile": source_snapshot["agent_profile"],
    }

    forged_run = {**source_run, "agent_id": "agt_other_tenant"}
    with pytest.raises(RepositoryConflictError, match="agent_profile_snapshot_invalid"):
        admitted_agent_profile_pins_for_copy(forged_run, source_snapshot)


def test_agent_profile_instructions_are_not_placed_in_the_user_prompt():
    from app.executors.claude_agent_sdk_runner import build_skill_prompt

    prompt = build_skill_prompt(
        skill_id="general-chat",
        user_message="User supplied question",
        file_names=[],
    )

    assert "Private profile instruction" not in prompt
    assert "User request: User supplied question" in prompt


def _draft(*, expected_draft_revision: int) -> AgentProfileDraftRequest:
    return AgentProfileDraftRequest(
        name="Support assistant",
        description="Approved support helper.",
        instructions="Private instruction",
        model_id="model-a",
        selected_skill=SelectedSkillRequest(skill_id="general-chat", expected_version="version-a"),
        mcp_tool_ids=[],
        expected_draft_revision=expected_draft_revision,
    )


async def test_profile_draft_save_requires_explicit_create_or_update_precondition():
    from app.agent_profiles import save_draft

    principal = AuthPrincipal(
        user_id="admin-a",
        display_name="Admin A",
        tenant_id="tenant-a",
        roles=["admin"],
    )
    with pytest.raises(HTTPException) as create_error:
        await save_draft(object(), principal=principal, definition=_draft(expected_draft_revision=2), agent_id=None)
    assert create_error.value.status_code == 409
    assert create_error.value.detail == "agent_profile_create_revision_invalid"

    with pytest.raises(HTTPException) as update_error:
        await save_draft(
            object(),
            principal=principal,
            definition=_draft(expected_draft_revision=0),
            agent_id="agt_support",
        )
    assert update_error.value.status_code == 409
    assert update_error.value.detail == "agent_profile_revision_stale"


async def test_profile_revision_fence_allows_one_concurrent_publish_from_the_same_draft():
    from app.repositories import create_agent_profile_revision

    class Cursor:
        def __init__(self, row=None):
            self.row = row

        async def fetchone(self):
            return self.row

    class LockedConnection:
        def __init__(self):
            self.lock = asyncio.Lock()
            self.current_revision = 4

        async def execute(self, sql, params):
            normalized = " ".join(sql.split()).lower()
            if "pg_advisory_xact_lock" in normalized:
                await self.lock.acquire()
                return Cursor()
            if "select coalesce(max(revision), 0) as current_revision" in normalized:
                return Cursor({"current_revision": self.current_revision})
            if "insert into agent_profile_revisions" in normalized:
                self.current_revision = int(params[2])
                self.lock.release()
                return Cursor(
                    {
                        "tenant_id": params[0],
                        "agent_id": params[1],
                        "revision": params[2],
                        "status": params[3],
                        "name": params[4],
                        "description": params[5],
                        "instructions": params[6],
                        "model_id": params[7],
                        "skill_id": params[8],
                        "skill_version": params[9],
                        "mcp_tool_ids": [],
                        "content_hash": params[11],
                    }
                )
            raise AssertionError(normalized)

    async def publish(conn):
        return await create_agent_profile_revision(
            conn,
            tenant_id="tenant-a",
            agent_id="agt_support",
            status="published",
            name="Support assistant",
            description="Approved support helper.",
            instructions="Private instruction",
            model_id="model-a",
            skill_id="general-chat",
            skill_version="version-a",
            mcp_tool_ids=[],
            content_hash="a" * 64,
            created_by="admin-a",
            published_by="admin-a",
            expected_previous_revision=4,
            published_from_revision=4,
        )

    conn = LockedConnection()
    outcomes = await asyncio.gather(publish(conn), publish(conn), return_exceptions=True)

    assert [outcome["revision"] for outcome in outcomes if isinstance(outcome, dict)] == [5]
    assert sum(isinstance(outcome, RepositoryConflictError) for outcome in outcomes) == 1
