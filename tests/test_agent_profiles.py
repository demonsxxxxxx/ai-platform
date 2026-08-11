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
from app.agent_apps.authority import _revision_hash
from app.auth import AuthPrincipal
from app.models import (
    AgentProfileAdminProjection,
    AgentProfileDraftRequest,
    ChatSessionResponse,
    ChatStreamRequest,
    SelectedAgentProfileRequest,
    SelectedSkillRequest,
)
from app.repositories import RepositoryConflictError, RepositoryNotFoundError
from app.main import create_app
from app.validation import MAX_SERVER_OWNED_SYSTEM_PROMPT_CHARS


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


def admin_headers():
    return {**ordinary_headers(), "x-ai-roles": "admin"}


def profile_draft_payload(instructions: str, *, expected_draft_revision: int = 0) -> dict[str, object]:
    return {
        "name": "Support assistant",
        "description": "Approved support helper.",
        "instructions": instructions,
        "model_id": "model-a",
        "selected_skill": {"skill_id": "general-chat", "expected_version": "version-a"},
        "mcp_tool_ids": [],
        "expected_draft_revision": expected_draft_revision,
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
        "avatar_ref": "builtin:agent",
        "category": "general",
        "welcome_message": "",
        "starter_prompts": [],
        "capability_summary": "",
        "recommended_tasks": [],
        "supported_input_types": ["text"],
        "supported_file_types": [],
        "expected_outputs": [],
        "permissions_and_data_access_notice": "",
        "published_at": None,
    }
    assert not {
        "instructions",
        "model_id",
        "skill_id",
        "skill_version",
        "mcp_tool_ids",
        "content_hash",
    }.intersection(projection)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("welcome_message", "Welcome to the expert workspace."),
        ("starter_prompts", ["Review this request"]),
        ("capability_summary", "Reviews approved requests."),
        ("recommended_tasks", ["Policy review"]),
        ("supported_input_types", ["text", "file"]),
        ("supported_file_types", ["application/pdf"]),
        ("expected_outputs", ["Review memo"]),
        ("permissions_and_data_access_notice", "Uses tenant-authorized files only."),
        ("avatar_asset_id", "file-avatar-a"),
    ],
)
def test_every_enterprise_profile_field_changes_the_immutable_revision_hash(field, value):
    definition = AgentProfileDraftRequest.model_validate(profile_draft_payload("Private instruction"))

    changed = definition.model_copy(update={field: value})

    assert _revision_hash(changed) != _revision_hash(definition)


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


def test_selected_profile_rejects_client_owned_definition_hash():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        SelectedAgentProfileRequest.model_validate(
            {
                "agent_id": "agt_support",
                "expected_revision": 4,
                "content_hash": "a" * 64,
            }
        )


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
    monkeypatch.setattr("app.routes.agent_profiles.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.agent_profiles.list_public_profiles", profiles)

    response = TestClient(create_app()).get("/api/ai/agent-profiles", headers=ordinary_headers())

    assert response.status_code == 200
    assert response.json() == {
        "agent_profiles": [
            {
                "agent_id": "agt_support",
                "expected_revision": 4,
                "name": "Support assistant",
                "description": "Approved support helper.",
                    "avatar_ref": "builtin:agent",
                    "category": "general",
                    "welcome_message": "",
                    "starter_prompts": [],
                    "capability_summary": "",
                    "recommended_tasks": [],
                    "supported_input_types": ["text"],
                    "supported_file_types": [],
                    "expected_outputs": [],
                    "permissions_and_data_access_notice": "",
                    "published_at": None,
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


def test_profile_instruction_length_is_rejected_by_the_admin_api_before_runtime(monkeypatch):
    saved_instruction_lengths = []

    async def save_profile(_conn, *, definition, **_kwargs):
        saved_instruction_lengths.append(len(definition.instructions))
        return (
            AgentProfileAdminProjection(
                agent_id="agt_support",
                revision=1,
                status="draft",
                name=definition.name,
                description=definition.description,
                instructions=definition.instructions,
                model_id=definition.model_id,
                selected_skill=definition.selected_skill,
                mcp_tool_ids=definition.mcp_tool_ids,
                content_hash="a" * 64,
            ),
            "audit_profile_save",
        )

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.agent_profiles.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.agent_profiles.save_draft", save_profile)
    client = TestClient(create_app())
    max_length_instructions = "界" * MAX_SERVER_OWNED_SYSTEM_PROMPT_CHARS

    accepted = client.post(
        "/api/ai/admin/agent-profiles",
        headers=admin_headers(),
        json=profile_draft_payload(max_length_instructions),
    )
    rejected = client.post(
        "/api/ai/admin/agent-profiles",
        headers=admin_headers(),
        json=profile_draft_payload(max_length_instructions + "界"),
    )

    assert accepted.status_code == 200
    assert len(max_length_instructions.encode("utf-8")) > MAX_SERVER_OWNED_SYSTEM_PROMPT_CHARS
    assert rejected.status_code == 422
    assert saved_instruction_lengths == [MAX_SERVER_OWNED_SYSTEM_PROMPT_CHARS]


def test_agent_profile_mutation_routes_map_repository_conflicts_to_one_safe_stale_code(monkeypatch):
    async def conflict(*_args, **_kwargs):
        raise RepositoryConflictError("database constraint detail must not be public")

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.agent_profiles.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.agent_profiles.save_draft", conflict)
    monkeypatch.setattr("app.routes.agent_profiles.publish_draft", conflict)
    client = TestClient(create_app())

    responses = [
        client.post(
            "/api/ai/admin/agent-profiles",
            headers=admin_headers(),
            json=profile_draft_payload("Keep answers concise."),
        ),
        client.put(
            "/api/ai/admin/agent-profiles/agt_support",
            headers=admin_headers(),
            json=profile_draft_payload("Keep answers concise.", expected_draft_revision=4),
        ),
        client.post(
            "/api/ai/admin/agent-profiles/agt_support/publish",
            headers=admin_headers(),
            json={"expected_revision": 4},
        ),
    ]

    assert [(response.status_code, response.json()) for response in responses] == [
        (409, {"detail": "agent_profile_revision_stale"}),
        (409, {"detail": "agent_profile_revision_stale"}),
        (409, {"detail": "agent_profile_revision_stale"}),
    ]


@pytest.mark.parametrize(
    ("error", "expected_status", "expected_detail"),
    [
        (RepositoryNotFoundError("workspace_not_found"), 404, "workspace_not_found"),
        (RepositoryConflictError("raw database conflict"), 409, "agent_profile_not_available"),
    ],
)
def test_agent_conversation_creation_maps_repository_failures_to_safe_4xx(
    monkeypatch,
    error,
    expected_status,
    expected_detail,
):
    async def fail(*_args, **_kwargs):
        raise error

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.agent_profiles.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.agent_profiles._authority.create_conversation", fail)

    response = TestClient(create_app(), raise_server_exceptions=False).post(
        "/api/ai/agent-conversations",
        headers=ordinary_headers(),
        json={
            "workspace_id": "workspace-a",
            "selected_agent_profile": {
                "agent_id": "agt_support",
                "expected_revision": 4,
            },
            "operation_id": "11111111-1111-4111-8111-111111111111",
        },
    )

    assert (response.status_code, response.json()["detail"]) == (
        expected_status,
        expected_detail,
    )


def test_agent_conversation_creation_binds_one_stable_operation_identity(monkeypatch):
    observed: list[dict[str, object]] = []

    async def create_conversation(_conn, **kwargs):
        observed.append(kwargs)
        return ChatSessionResponse(
            session_id="ses_agent_33333333333343338333333333333333",
            workspace_id="workspace-a",
            agent_id="agt_support",
            title="Support assistant",
            purpose="conversation",
            agent_conversation=None,
        )

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.agent_profiles.transaction", fake_transaction)
    monkeypatch.setattr(
        "app.routes.agent_profiles._authority.create_conversation",
        create_conversation,
    )
    body = {
        "workspace_id": "workspace-a",
        "selected_agent_profile": {
            "agent_id": "agt_support",
            "expected_revision": 4,
        },
        "operation_id": "33333333-3333-4333-8333-333333333333",
    }
    client = TestClient(create_app())

    first = client.post("/api/ai/agent-conversations", headers=ordinary_headers(), json=body)
    second = client.post("/api/ai/agent-conversations", headers=ordinary_headers(), json=body)

    assert first.status_code == second.status_code == 200
    assert [call["operation_id"].hex for call in observed] == [
        "33333333333343338333333333333333",
        "33333333333343338333333333333333",
    ]


@pytest.mark.parametrize(
    "operation_id",
    [
        "00000000-0000-0000-0000-000000000000",
        "6ba7b810-9dad-11d1-80b4-00c04fd430c8",
        "3d813cbb-47fb-32ba-91df-831e1593ac29",
        "21f7f8de-8051-5b89-8680-0195ef798b6a",
        "not-a-uuid",
    ],
    ids=["nil", "v1", "v3", "v5", "malformed"],
)
def test_agent_conversation_creation_rejects_non_v4_operation_identity(monkeypatch, operation_id):
    async def must_not_create(*_args, **_kwargs):
        raise AssertionError("invalid operation identity reached conversation authority")

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.agent_profiles.transaction", fake_transaction)
    monkeypatch.setattr(
        "app.routes.agent_profiles._authority.create_conversation",
        must_not_create,
    )

    response = TestClient(create_app(), raise_server_exceptions=False).post(
        "/api/ai/agent-conversations",
        headers=ordinary_headers(),
        json={
            "workspace_id": "workspace-a",
            "selected_agent_profile": {
                "agent_id": "agt_support",
                "expected_revision": 4,
            },
            "operation_id": operation_id,
        },
    )

    assert response.status_code == 422


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


async def test_agent_profile_compatibility_admission_delegates_to_authority(monkeypatch):
    observed: dict[str, object] = {}
    sentinel = object()

    async def resolve_from_authority(conn, *, principal, selection):
        observed.update({"conn": conn, "principal": principal, "selection": selection})
        return sentinel

    monkeypatch.setattr(
        "app.agent_profiles._authority.resolve_for_admission",
        resolve_from_authority,
    )
    principal = AuthPrincipal(
        user_id="user-a",
        display_name="User A",
        tenant_id="tenant-a",
        roles=["user"],
    )
    conn = object()
    selection = SelectedAgentProfileRequest(agent_id="agt_other_tenant", expected_revision=4)

    result = await resolve_profile_for_admission(
        conn,
        principal=principal,
        selection=selection,
    )

    assert result is sentinel
    assert observed == {
        "conn": conn,
        "principal": principal,
        "selection": selection,
    }


def test_agent_profile_schema_is_idempotent_and_legacy_rows_can_remain_unpinned():
    schema = Path("app/schema.sql").read_text(encoding="utf-8")
    normalized_schema = " ".join(schema.split())

    assert "create table if not exists agent_profile_revisions" in schema
    assert "create index idx_agent_profile_revisions_published" in schema
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
    assert "create table if not exists agent_profiles" in schema
    assert "lifecycle_status text not null check (lifecycle_status in ('draft', 'published', 'withdrawn'))" in schema
    assert "published_revision bigint" in schema
    assert "published_status text" in schema
    assert "revision_status text" in schema
    assert "status text not null check (status in ('draft', 'published'))" in schema
    assert "agent_profile_legacy_insert_compatibility" in schema
    assert "agent_profile_legacy_insert_reconcile" in schema
    assert "uq_agent_profile_revision_publication" in schema
    assert "fk_agent_profiles_current_publication" in schema
    assert "published_revision, published_hash, published_status" in schema
    assert "revision, content_hash, revision_status" in schema
    assert "on conflict (tenant_id, agent_id) do nothing;" not in schema
    legacy_visibility_repair = "set visibility = 'tenant'"
    visibility_repair = "set visibility = 'restricted'"
    visibility_check = "constraint chk_agent_profile_revisions_visibility"
    assert legacy_visibility_repair in schema
    assert visibility_repair in schema
    assert visibility_check in schema
    assert schema.index(legacy_visibility_repair) < schema.index(visibility_repair)
    assert schema.index(visibility_repair) < schema.rindex(visibility_check)
    assert "withdrawn_from_revision bigint" in schema
    assert "profiles.published_status is distinct from 'published'" in normalized_schema
    assert "where row( agent_profiles.latest_revision" in normalized_schema
    assert "is distinct from row( greatest(agent_profiles.latest_revision" in normalized_schema
    assert "and revisions.status is distinct from desired.desired_status" in normalized_schema


async def test_bound_profile_repository_uses_the_session_revision_and_hash_but_requires_live_agent():
    from app.repositories import get_bound_published_agent_profile

    class Cursor:
        async def fetchone(self):
            return None

    class RecordingConnection:
        def __init__(self):
            self.calls = []

        async def execute(self, sql, params):
            self.calls.append((" ".join(sql.split()).lower(), params))
            return Cursor()

    conn = RecordingConnection()
    assert (
        await get_bound_published_agent_profile(
            conn,
            tenant_id="tenant-a",
            agent_id="agt_support",
            revision=4,
            content_hash="a" * 64,
            for_update=True,
        )
        is None
    )

    sql, params = conn.calls[-1]
    assert "agent_profiles.lifecycle_status = 'published'" in sql
    assert "agent_profile_revisions.revision = %s" in sql
    assert "agent_profile_revisions.content_hash = %s" in sql
    assert "agent_profile_revisions.revision_status = 'published'" in sql
    assert "current_revision.visibility as current_visibility" in sql
    assert "agent_profiles.published_revision = %s" not in sql
    assert "for update of agent_profiles" in sql
    assert params == ("tenant-a", "agt_support", 4, "a" * 64)


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


@pytest.mark.asyncio
async def test_replay_authority_revalidates_exact_profile_snapshot_and_leaves_generic_runs_unchanged(monkeypatch):
    from app.agent_apps import AgentProfileAdmission, AgentProfileAuthority
    from app.models import AgentConversationIdentity

    principal = AuthPrincipal(
        user_id="user-a",
        display_name="User A",
        tenant_id="tenant-a",
        roles=["user"],
    )
    source = {
        "id": "run-profile",
        "agent_id": "agt_support",
        "skill_id": "profile-skill",
        "admitted_agent_profile_revision": 4,
        "admitted_agent_profile_hash": "a" * 64,
        "input_json": {
            "input": {"message": "retry", "mcp_tool_ids": ["profile-tool"]},
            "executor_type": "claude-agent-worker",
            "skill_version": "version-a",
            "release_decision": {"selected_version": "version-a"},
                "skill_manifests": [
                    {
                        "skill_id": "profile-skill",
                        "version": "version-a",
                        "content_hash": "version-a",
                    }
                ],
            "model_id": "model-a",
            "model_value": "provider-model-a",
            "agent_profile": {
                "agent_id": "agt_support",
                "revision": 4,
                "content_hash": "a" * 64,
                "instructions": "private profile instruction",
            },
        },
    }
    rows = {"run-profile": source, "run-generic": {**source, "id": "run-generic"}}
    rows["run-generic"].update(
        {
            "agent_id": "general-agent",
            "skill_id": "general-chat",
            "admitted_agent_profile_revision": None,
            "admitted_agent_profile_hash": None,
            "input_json": {"input": {"message": "generic"}},
        }
    )
    bound_calls: list[dict[str, object]] = []

    async def get_run(*_args, **kwargs):
        return rows.get(kwargs["run_id"])

    async def resolve_bound(*_args, **kwargs):
        bound_calls.append(kwargs)
        return AgentProfileAdmission(
            agent_id="agt_support",
            revision=4,
            content_hash="a" * 64,
            skill={
                "skill_id": "profile-skill",
                "skill_version": "version-a",
                "executor_type": "claude-agent-worker",
            },
            model={"id": "model-a", "value": "provider-model-a"},
            mcp_tool_ids=("profile-tool",),
            private_execution_input={
                "agent_id": "agt_support",
                "revision": 4,
                "content_hash": "a" * 64,
                "instructions": "private profile instruction",
            },
            public_identity=AgentConversationIdentity(
                agent_id="agt_support",
                revision=4,
                name="Support assistant",
            ),
        )

    monkeypatch.setattr("app.agent_apps.authority.repositories.get_authorized_run", get_run)
    async def validate_replay_skill_manifests(*_args, **_kwargs):
        return ["profile-tool"]

    monkeypatch.setattr(
        "app.agent_apps.authority.repositories.validate_replay_skill_manifests",
        validate_replay_skill_manifests,
    )
    authority = AgentProfileAuthority()
    monkeypatch.setattr(authority, "resolve_bound_for_submission", resolve_bound)

    await authority.reauthorize_pinned_run_for_replay(
        object(),
        principal=principal,
        run_id="run-profile",
    )
    await authority.reauthorize_pinned_run_for_replay(
        object(),
        principal=principal,
        run_id="run-generic",
    )

    assert [(call["agent_id"], call["revision"], call["content_hash"]) for call in bound_calls] == [
        ("agt_support", 4, "a" * 64)
    ]


@pytest.mark.asyncio
async def test_replay_authority_accepts_governed_manifest_lock_but_rejects_lock_drift(monkeypatch):
    from app.agent_apps import AgentProfileAdmission, AgentProfileAuthority
    from app.models import AgentConversationIdentity

    principal = AuthPrincipal(
        user_id="user-a",
        display_name="User A",
        tenant_id="tenant-a",
        roles=["user"],
    )
    locked_version = "b" * 64
    source = {
        "id": "run-profile",
        "tenant_id": "tenant-a",
        "user_id": "user-a",
        "agent_id": "agt_support",
        "skill_id": "profile-skill",
        "admitted_agent_profile_revision": 4,
        "admitted_agent_profile_hash": "a" * 64,
        "input_json": {
            "input": {"message": "retry", "mcp_tool_ids": ["profile-tool"]},
            "executor_type": "claude-agent-worker",
            "skill_version": locked_version,
            "release_decision": {"selected_version": locked_version},
            "skill_manifests": [
                {
                    "skill_id": "profile-skill",
                    "version": locked_version,
                    "content_hash": locked_version,
                }
            ],
            "model_id": "model-a",
            "model_value": "provider-model-a",
                "agent_profile": {
                    "agent_id": "agt_support",
                    "revision": 4,
                    "content_hash": "a" * 64,
                    "instructions": "private profile instruction",
                },
        },
    }

    async def get_run(*_args, **_kwargs):
        return source

    async def resolve_bound(*_args, **_kwargs):
        return AgentProfileAdmission(
            agent_id="agt_support",
            revision=4,
            content_hash="a" * 64,
            skill={
                "skill_id": "profile-skill",
                "skill_version": "version-a",
                "executor_type": "claude-agent-worker",
            },
            model={"id": "model-a", "value": "provider-model-a"},
            mcp_tool_ids=("profile-tool",),
                private_execution_input={
                    "agent_id": "agt_support",
                    "revision": 4,
                    "content_hash": "a" * 64,
                    "instructions": "private profile instruction",
                },
            public_identity=AgentConversationIdentity(
                agent_id="agt_support",
                revision=4,
                name="Support assistant",
            ),
        )

    replay_validation_calls: list[dict[str, object]] = []

    def require_replay_source_identity(**kwargs):
        replay_validation_calls.append({"source_identity": kwargs})

    async def validate_replay_skill_manifests(*_args, **kwargs):
        replay_validation_calls.append({"manifest_validation": kwargs})
        return ["profile-tool"]

    monkeypatch.setattr("app.agent_apps.authority.repositories.get_authorized_run", get_run)
    monkeypatch.setattr(
        "app.agent_apps.authority.repositories.require_replay_source_identity",
        require_replay_source_identity,
    )
    monkeypatch.setattr(
        "app.agent_apps.authority.repositories.validate_replay_skill_manifests",
        validate_replay_skill_manifests,
    )
    authority = AgentProfileAuthority()
    monkeypatch.setattr(authority, "resolve_bound_for_submission", resolve_bound)

    await authority.reauthorize_pinned_run_for_replay(
        object(),
        principal=principal,
        run_id="run-profile",
    )
    assert len(replay_validation_calls) == 2
    assert replay_validation_calls[0]["source_identity"]["pinned_version"] == locked_version
    assert replay_validation_calls[1]["manifest_validation"]["pinned_version"] == locked_version

    async def reject_malformed_manifest(*_args, **_kwargs):
        raise RepositoryConflictError("run_skill_snapshot_identity_mismatch")

    monkeypatch.setattr(
        "app.agent_apps.authority.repositories.validate_replay_skill_manifests",
        reject_malformed_manifest,
    )
    with pytest.raises(RepositoryConflictError, match="agent_profile_snapshot_invalid"):
        await authority.reauthorize_pinned_run_for_replay(
            object(),
            principal=principal,
            run_id="run-profile",
        )

    monkeypatch.setattr(
        "app.agent_apps.authority.repositories.validate_replay_skill_manifests",
        validate_replay_skill_manifests,
    )

    source["input_json"]["input"]["mcp_tool_ids"] = "profile-tool"
    with pytest.raises(RepositoryConflictError, match="agent_profile_snapshot_invalid"):
        await authority.reauthorize_pinned_run_for_replay(
            object(),
            principal=principal,
            run_id="run-profile",
        )
    source["input_json"]["input"]["mcp_tool_ids"] = ["profile-tool"]

    source["input_json"]["release_decision"] = {"selected_version": "c" * 64}
    with pytest.raises(RepositoryConflictError, match="agent_profile_snapshot_invalid"):
        await authority.reauthorize_pinned_run_for_replay(
            object(),
            principal=principal,
            run_id="run-profile",
        )


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


async def test_mock_profile_revision_fence_allows_one_concurrent_publish_from_the_same_draft():
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
