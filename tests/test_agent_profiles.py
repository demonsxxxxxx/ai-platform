import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.agent_apps.authority import (
    AgentProfileAuthority,
    _revision_hash,
    _revision_hash_matches,
    profile_public_projection,
    reject_profile_selector_conflicts,
)
from app.agent_apps.api import (
    AgentProfileAdminProjection as AgentAppsAdminProjection,
    safe_agent_avatar_seed,
)
from app.agent_apps.application.skill_set_pinning import pin_agent_skill_set
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
from app import repositories as repository_module
from app.main import create_app
from app.validation import MAX_SERVER_OWNED_SYSTEM_PROMPT_CHARS


def test_agent_profile_draft_accepts_extended_builtin_avatar_style():
    definition = AgentProfileDraftRequest.model_validate(
        {**profile_draft_payload("Private instruction"), "avatar_ref": "builtin:planet"}
    )

    assert definition.avatar_ref == "builtin:planet"


def test_agent_profile_models_reject_retired_fields():
    payload = profile_draft_payload("Private instruction")
    for field, value in (
        ("welcome_message", "Welcome"),
        ("capability_summary", "Summary"),
        ("recommended_tasks", ["Task"]),
        ("supported_input_types", ["text", "file"]),
        ("expected_outputs", ["Answer"]),
        ("permissions_and_data_access_notice", "Notice"),
        ("selected_skill", {"skill_id": "general-chat"}),
        ("avatar_asset_id", "file-avatar"),
        ("category", "support"),
        ("market_tag", "support"),
    ):
        with pytest.raises(ValueError):
            AgentProfileDraftRequest.model_validate({**payload, field: value})


def test_agent_profile_authority_keeps_extended_builtin_avatar_style():
    from app.agent_apps.authority import _safe_avatar_ref

    assert _safe_avatar_ref("builtin:pixel") == "builtin:pixel"
    assert _safe_avatar_ref("not-a-style") == "builtin:agent"


def test_agent_profile_avatar_seed_uses_unicode_code_points_and_rejects_c0_controls():
    seed = "\U0001f680" * 128
    definition = AgentProfileDraftRequest.model_validate(
        {**profile_draft_payload("Private instruction"), "avatar_seed": seed}
    )
    assert definition.avatar_seed == seed

    with pytest.raises(ValueError, match="control characters"):
        AgentProfileDraftRequest.model_validate(
            {**profile_draft_payload("Private instruction"), "avatar_seed": "\x1fseed"}
        )

    for historical_control in ("\x7f", "\x80", "\x85", "\x9f"):
        historical_seed = f"safe{historical_control}seed"
        definition = AgentProfileDraftRequest.model_validate(
            {**profile_draft_payload("Private instruction"), "avatar_seed": historical_seed}
        )
        assert definition.avatar_seed == historical_seed


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (" seed ", "seed"),
        ("\tseed", "agt_fallback"),
        ("safe\x1fseed", "agt_fallback"),
        ("", "agt_fallback"),
        ("x" * 129, "agt_fallback"),
        (None, "agt_fallback"),
    ],
)
def test_safe_agent_avatar_seed_has_one_projection_contract(value, expected):
    assert safe_agent_avatar_seed(value, fallback="agt_fallback") == expected


@pytest.mark.asyncio
async def test_agent_skill_set_pinning_accepts_empty_primary_release_decision_payload():
    version = "version-a"

    async def governed_manifest_pins(*_args, **_kwargs):
        return [{"skill_id": "qa-review", "content_hash": version}]

    manifests, primary_version, primary_decision = await pin_agent_skill_set(
        [{"skill_id": "qa-review", "skill_version": version}],
        manifest_scope=object(),
        input_payload={},
        tenant_id="default",
        rollout_key="user-a",
        resolve_release_decision=lambda *_args, **_kwargs: type(
            "Decision",
            (),
            {"selected_version": version, "policy_active": False},
        )(),
        governed_manifest_pins=governed_manifest_pins,
        locked_skill_version=lambda **_kwargs: version,
        decision_payload_for_version=lambda *_args, **_kwargs: {},
        attach_snapshot_governance=lambda values, **_kwargs: values,
        pin_mcp_tool_ids=lambda values, **_kwargs: values,
        mcp_tool_ids_for_skill=lambda *_args, **_kwargs: [],
        conflict_error=RepositoryConflictError,
    )

    assert manifests == [
        {"skill_id": "qa-review", "content_hash": version, "release_decision": {}}
    ]
    assert primary_version == version
    assert primary_decision == {}


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
        "starter_prompts": ["Help with a support request"],
        "instructions": instructions,
        "skill_set": [{"skill_id": "general-chat"}],
        "mcp_tool_ids": [],
        "avatar_ref": "builtin:agent",
        "avatar_seed": "support-assistant",
        "market_tags": ["support"],
        "expected_draft_revision": expected_draft_revision,
    }


def canonical_profile_definition() -> AgentProfileDraftRequest:
    return AgentProfileDraftRequest.model_validate(
        profile_draft_payload("Private instruction")
    )


def test_profile_public_projection_never_exposes_private_execution_definition():
    projection = profile_public_projection(
        {
            "agent_id": "agt_support",
            "revision": 4,
            "name": "Support assistant",
            "description": "Helps employees with approved support requests.",
            "instructions": "Never expose this private instruction.",
            "skill_set": [{"skill_id": "support-skill"}],
            "mcp_tool_ids": ["internal-tool"],
            "avatar_ref": "builtin:agent",
            "avatar_seed": "agt_support",
            "market_tags": ["support"],
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
        "avatar_seed": "agt_support",
        "market_tags": ["support"],
        "starter_prompts": [],
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
        ("description", "Reviews approved requests."),
        ("starter_prompts", ["Review this request"]),
        ("instructions", "Updated private instructions"),
        ("skill_set", [{"skill_id": "document-review"}]),
        ("mcp_tool_ids", ["mcp:search"]),
        ("avatar_ref", "builtin:planet"),
        ("avatar_seed", "stable-expert-avatar"),
        ("market_tags", ["review"]),
        ("visibility", "restricted"),
        ("allowed_department_ids", ["department-a"]),
    ],
)
def test_every_canonical_profile_field_changes_the_immutable_revision_hash(field, value):
    definition = AgentProfileDraftRequest.model_validate(profile_draft_payload("Private instruction"))

    changed = definition.model_copy(update={field: value})

    assert _revision_hash(changed) != _revision_hash(definition)


def test_agent_profile_uses_only_the_canonical_skill_set():
    definition = AgentProfileDraftRequest.model_validate(
        {
            **profile_draft_payload("Private instruction"),
            "skill_set": [
                {"skill_id": "document-review"},
                {"skill_id": "workflow-automation"},
            ],
        }
    )

    assert [skill["skill_id"] for skill in definition.skill_set] == [
        "document-review",
        "workflow-automation",
    ]


@pytest.mark.parametrize("skill_id", ["minimax-docx", "reference-fact-extraction"])
def test_agent_profile_rejects_internal_dependency_skill_as_root(skill_id):
    with pytest.raises(ValueError, match="internal dependency"):
        AgentProfileDraftRequest.model_validate(
            {
                **profile_draft_payload("Private instruction"),
                "skill_set": [{"skill_id": skill_id}],
            }
        )


def test_agent_profile_rejects_duplicate_skill_names():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        AgentProfileDraftRequest.model_validate(
            {
                **profile_draft_payload("Private instruction"),
                "skill_set": [
                    {"skill_id": "document-review"},
                    {"skill_id": "document-review"},
                ],
            }
        )


def test_current_profile_hash_accepts_only_the_current_canonical_shape():
    definition = AgentProfileDraftRequest.model_validate(
        profile_draft_payload("Private instruction")
    )
    row = {
        **definition.model_dump(mode="json"),
        "agent_id": "agt_support",
        "revision": 7,
    }

    assert _revision_hash_matches(row, _revision_hash(definition))
    assert not _revision_hash_matches(
        {**row, "instructions": "changed without a new immutable hash"},
        _revision_hash(definition),
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("skill_set", ["malformed-skill"]),
        ("skill_set", {"malformed": "skill-set"}),
        ("skill_set", []),
        ("skill_set", None),
        ("mcp_tool_ids", {"malformed": "tool-list"}),
        ("starter_prompts", [{}]),
        ("market_tags", [17]),
        ("allowed_department_ids", [17]),
        ("allowed_roles", [False]),
        ("allowed_user_ids", [{}]),
        ("avatar_ref", "malformed-avatar"),
        ("avatar_seed", ""),
        ("visibility", "malformed-visibility"),
    ],
)
def test_revision_integrity_rejects_malformed_canonical_fields(field, value):
    definition = canonical_profile_definition()
    row = {
        **definition.model_dump(mode="json"),
        "agent_id": "agt_support",
        "revision": 7,
        "content_hash": _revision_hash(definition),
        field: value,
    }

    with pytest.raises(HTTPException) as exc_info:
        AgentProfileAuthority._require_revision_integrity(row)

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "agent_profile_revision_integrity_mismatch"


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


def test_selected_profile_accepts_user_owned_model_selectors():
    for selector in ({"model": "legacy-model"}, {"model_id": "catalog-model"}):
        request = ChatStreamRequest(
            message="Help me",
            agent_options=selector,
        )
        reject_profile_selector_conflicts(request, active=True)


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
    async def profiles(_conn, *, principal, query):
        assert principal.tenant_id == "tenant-a"
        assert query is None
        return [
            {
                "agent_id": "agt_support",
                "expected_revision": 4,
                "name": "Support assistant",
                "description": "Approved support helper.",
                "avatar_ref": "builtin:agent",
                "avatar_seed": "agt-support",
                "market_tags": ["support"],
                "starter_prompts": [],
                "is_favorite": False,
                "published_at": None,
            }
        ]

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.agent_profiles.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.agent_profiles._authority.list_public", profiles)

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
                    "avatar_seed": "agt-support",
                    "market_tags": ["support"],
                    "starter_prompts": [],
                    "is_favorite": False,
                    "published_at": None,
                }
        ]
    }


def test_agent_profile_market_normalizes_unicode_search_before_repository_query(monkeypatch):
    observed: list[str | None] = []

    async def profiles(_conn, *, principal, query):
        assert principal.tenant_id == "tenant-a"
        observed.append(query)
        return []

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.agent_profiles.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.agent_profiles._authority.list_public", profiles)

    response = TestClient(create_app()).get(
        "/api/ai/agent-profiles",
        headers=ordinary_headers(),
        params={"query": "Ａｕｄｉｔ"},
    )

    assert response.status_code == 200
    assert response.json() == {"agent_profiles": []}
    assert observed == ["Audit"]


def test_agent_profile_market_rejects_query_that_expands_past_limit_after_normalization(monkeypatch):
    called = False

    async def profiles(*_args, **_kwargs):
        nonlocal called
        called = True
        return []

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.agent_profiles.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.agent_profiles._authority.list_public", profiles)

    response = TestClient(create_app()).get(
        "/api/ai/agent-profiles",
        headers=ordinary_headers(),
        params={"query": "\ufdfa" * 10},
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "agent_profile_query_invalid"
    assert not called


def test_agent_profile_admin_wire_projects_only_the_canonical_contract(monkeypatch):
    profile = AgentProfileAdminProjection(
        agent_id="agt_support",
        revision=4,
        published_revision=4,
        status="published",
        name="Support assistant",
        description="Approved support helper.",
        starter_prompts=[],
        instructions="Keep answers concise.",
        skill_set=[{"skill_id": "general-chat"}],
        mcp_tool_ids=[],
        avatar_ref="builtin:agent",
        avatar_seed="agt-support",
        market_tags=["support"],
        visibility="tenant",
        allowed_department_ids=[],
        allowed_roles=[],
        allowed_user_ids=[],
        content_hash="a" * 64,
    )

    async def profiles(_conn, *, principal):
        assert principal.tenant_id == "tenant-a"
        return [profile]

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.agent_profiles.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.agent_profiles._authority.list_admin", profiles)
    client = TestClient(create_app())

    response = client.get(
        "/api/ai/admin/agent-profiles",
        headers=admin_headers(),
    )

    assert response.status_code == 200
    projected = response.json()["agent_profiles"][0]
    assert projected["agent_id"] == "agt_support"
    assert projected["published_revision"] == 4
    for retired_field in (
        "welcome_message",
        "capability_summary",
        "recommended_tasks",
        "supported_input_types",
        "supported_file_types",
        "expected_outputs",
        "permissions_and_data_access_notice",
        "selected_skill",
        "avatar_asset_id",
        "category",
        "market_tag",
        "model_id",
    ):
        assert retired_field not in projected
        assert retired_field not in AgentProfileAdminProjection.model_fields
        assert retired_field not in AgentProfileDraftRequest.model_fields


def test_agent_profile_admin_write_requires_admin(monkeypatch):
    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    response = TestClient(create_app()).post(
        "/api/ai/admin/agent-profiles",
        headers=ordinary_headers(),
        json=profile_draft_payload("Keep answers concise."),
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "not_ai_admin"


def test_agent_profile_admin_write_rejects_retired_fields(monkeypatch):
    saved_definitions = []

    async def save_profile(_conn, *, definition, **_kwargs):
        saved_definitions.append(definition)
        return (
            AgentAppsAdminProjection(
                {
                    "agent_id": "agt_support",
                    "revision": 1,
                    "published_revision": None,
                    "status": "draft",
                    "name": definition.name,
                    "description": definition.description,
                    "starter_prompts": definition.starter_prompts,
                    "instructions": definition.instructions,
                    "skill_set": definition.skill_set,
                    "mcp_tool_ids": definition.mcp_tool_ids,
                    "avatar_ref": definition.avatar_ref,
                    "avatar_seed": definition.avatar_seed,
                    "market_tags": definition.market_tags,
                    "visibility": definition.visibility,
                    "allowed_department_ids": definition.allowed_department_ids,
                    "allowed_roles": definition.allowed_roles,
                    "allowed_user_ids": definition.allowed_user_ids,
                    "content_hash": "a" * 64,
                    "created_at": None,
                    "published_at": None,
                }
            ),
            "audit_profile_save",
        )

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.agent_profiles.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.agent_profiles._authority.save_draft", save_profile)
    client = TestClient(create_app())
    valid_response = client.post(
        "/api/ai/admin/agent-profiles",
        headers=admin_headers(),
        json=profile_draft_payload("Keep answers concise."),
    )
    retired_response = client.post(
        "/api/ai/admin/agent-profiles",
        headers=admin_headers(),
        json={
            **profile_draft_payload("Keep answers concise."),
            "model_id": "legacy-model",
        },
    )

    assert valid_response.status_code == 200
    assert retired_response.status_code == 422
    assert len(saved_definitions) == 1
    assert not hasattr(saved_definitions[0], "model_id")


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
                published_revision=None,
                status="draft",
                name=definition.name,
                description=definition.description,
                starter_prompts=definition.starter_prompts,
                instructions=definition.instructions,
                skill_set=definition.skill_set,
                mcp_tool_ids=definition.mcp_tool_ids,
                avatar_ref=definition.avatar_ref,
                avatar_seed=definition.avatar_seed,
                market_tags=definition.market_tags,
                visibility=definition.visibility,
                allowed_department_ids=definition.allowed_department_ids,
                allowed_roles=definition.allowed_roles,
                allowed_user_ids=definition.allowed_user_ids,
                content_hash="a" * 64,
            ),
            "audit_profile_save",
        )

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.agent_profiles.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.agent_profiles._authority.save_draft", save_profile)
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
    monkeypatch.setattr("app.routes.agent_profiles._authority.save_draft", conflict)
    monkeypatch.setattr("app.routes.agent_profiles._authority.publish_draft", conflict)
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
    from app.agent_apps.infrastructure.postgres import list_latest_agent_profile_revisions

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


def test_agent_profile_schema_has_one_current_storage_contract():
    schema = Path("app/schema.sql").read_text(encoding="utf-8")

    assert "create table if not exists agent_profile_revisions" in schema
    assert "create table if not exists agent_profiles" in schema
    assert "revision_status text not null" in schema
    assert "skill_set jsonb not null" in schema
    assert "market_tags jsonb not null" in schema
    assert "fk_agent_profile_revisions_tenant_agent" in schema
    assert "fk_sessions_agent_profile_pin" in schema
    assert "fk_runs_agent_profile_pin" in schema
    assert "create trigger trg_agent_profile_legacy_insert_compatibility" not in schema
    assert "create trigger trg_agent_profile_legacy_insert_reconcile" not in schema
    for retired_column in (
        "welcome_message",
        "capability_summary",
        "recommended_tasks",
        "supported_input_types",
        "supported_file_types",
        "expected_outputs",
        "permissions_and_data_access_notice",
        "model_id",
        "skill_id",
        "skill_version",
        "avatar_style_ref",
        "avatar_asset_id",
        "category",
        "market_tag",
        "legacy_compatibility_write",
    ):
        assert f"drop column if exists {retired_column}" in schema


async def test_bound_profile_repository_uses_the_session_revision_and_hash_but_requires_live_agent():
    from app.agent_apps.infrastructure.postgres import get_bound_published_agent_profile

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
            "skill_manifests": [{"skill_id": "profile-skill", "content_hash": "version-a"}],
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
    secondary_locked_version = "c" * 64
    full_manifest = {
        "skill_id": "profile-skill",
        "version": locked_version,
        "content_hash": locked_version,
        "source": {"kind": "builtin", "asset_dir": "profile-skill"},
        "files": [
            {
                "relative_path": "SKILL.md",
                "content_base64": "c2tpbGw=",
                "size_bytes": 5,
            }
        ],
        "dependency_ids": [],
        "mcp_tool_ids": ["profile-tool"],
    }
    secondary_manifest = {
        "skill_id": "profile-skill-secondary",
        "version": secondary_locked_version,
        "content_hash": secondary_locked_version,
        "source": {"kind": "builtin", "asset_dir": "profile-skill-secondary"},
        "files": [
            {
                "relative_path": "SKILL.md",
                "content_base64": "c2tpbGw=",
                "size_bytes": 5,
            }
        ],
        "dependency_ids": [],
        "mcp_tool_ids": ["profile-tool-secondary"],
    }
    manifest_refs = repository_module.skill_manifest_refs([full_manifest, secondary_manifest])
    skill_set = [
        {"skill_id": "profile-skill", "expected_version": locked_version},
        {
            "skill_id": "profile-skill-secondary",
            "expected_version": secondary_locked_version,
        },
    ]
    source = {
        "id": "run-profile",
        "tenant_id": "tenant-a",
        "user_id": "user-a",
        "agent_id": "agt_support",
        "skill_id": "profile-skill",
        "admitted_agent_profile_revision": 4,
        "admitted_agent_profile_hash": "a" * 64,
        "input_json": {
            "input": {
                "message": "retry",
                "mcp_tool_ids": ["profile-tool", "profile-tool-secondary"],
            },
            "executor_type": "claude-agent-worker",
            "skill_version": locked_version,
            "release_decision": {"selected_version": locked_version},
            "skill_manifests": manifest_refs,
            "model_id": "model-a",
            "model_value": "provider-model-a",
                "agent_profile": {
                    "agent_id": "agt_support",
                    "revision": 4,
                    "content_hash": "a" * 64,
                    "instructions": "private profile instruction",
                    "skill_set": skill_set,
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
                "skill_version": locked_version,
                "executor_type": "claude-agent-worker",
            },
            mcp_tool_ids=("profile-tool", "profile-tool-secondary"),
            configured_mcp_tool_ids=(),
            private_execution_input={
                "agent_id": "agt_support",
                "revision": 4,
                "content_hash": "a" * 64,
                "instructions": "private profile instruction",
                "skill_set": skill_set,
            },
            public_identity=AgentConversationIdentity(
                agent_id="agt_support",
                revision=4,
                name="Support assistant",
            ),
            skills=(
                {
                    "skill_id": "profile-skill",
                    "skill_version": locked_version,
                    "executor_type": "claude-agent-worker",
                },
                {
                    "skill_id": "profile-skill-secondary",
                    "skill_version": secondary_locked_version,
                    "executor_type": "claude-agent-worker",
                },
            ),
        )

    replay_validation_calls: list[dict[str, object]] = []

    def require_replay_source_identity(**kwargs):
        replay_validation_calls.append({"source_identity": kwargs})

    async def validate_replay_skill_manifests(*_args, **kwargs):
        replay_validation_calls.append({"manifest_validation": kwargs})
        return ["profile-tool", "profile-tool-secondary"]

    async def materialize_run_skill_manifests(*_args, **kwargs):
        refs = kwargs["skill_manifest_refs"]
        if refs != manifest_refs:
            raise RepositoryConflictError("run_skill_materialization_identity_mismatch")
        return [full_manifest, secondary_manifest]

    monkeypatch.setattr("app.agent_apps.authority.repositories.get_authorized_run", get_run)
    monkeypatch.setattr(
        "app.agent_apps.authority.repositories.require_replay_source_identity",
        require_replay_source_identity,
    )
    monkeypatch.setattr(
        "app.agent_apps.authority.repositories.validate_replay_skill_manifests",
        validate_replay_skill_manifests,
    )
    monkeypatch.setattr(
        "app.agent_apps.authority.repositories.materialize_run_skill_manifests",
        materialize_run_skill_manifests,
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
    assert replay_validation_calls[1]["manifest_validation"]["skill_set"] == skill_set

    for invalid_refs in (
        [manifest_refs[0], "unexpected"],
        [manifest_refs[0], None],
        "not-a-list",
        None,
    ):
        source["input_json"]["skill_manifests"] = invalid_refs
        with pytest.raises(RepositoryConflictError, match="agent_profile_snapshot_invalid"):
            await authority.reauthorize_pinned_run_for_replay(
                object(),
                principal=principal,
                run_id="run-profile",
            )
    source["input_json"]["skill_manifests"] = manifest_refs

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
    source["input_json"]["input"]["mcp_tool_ids"] = [
        "profile-tool",
        "profile-tool-secondary",
    ]

    source["input_json"]["release_decision"] = {"selected_version": "c" * 64}
    with pytest.raises(RepositoryConflictError, match="agent_profile_snapshot_invalid"):
        await authority.reauthorize_pinned_run_for_replay(
            object(),
            principal=principal,
            run_id="run-profile",
        )


@pytest.mark.asyncio
async def test_replay_authority_accepts_legacy_required_skill_snapshot_without_canonical_skill_set(
    monkeypatch,
):
    from app.agent_apps import AgentProfileAdmission, AgentProfileAuthority
    from app.models import AgentConversationIdentity

    version = "b" * 64
    manifest = {
        "skill_id": "profile-skill",
        "version": version,
        "content_hash": version,
        "source": {"kind": "builtin", "asset_dir": "profile-skill"},
        "files": [{"relative_path": "SKILL.md", "content_base64": "c2tpbGw=", "size_bytes": 5}],
        "dependency_ids": [],
        "mcp_tool_ids": ["profile-tool"],
    }
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
            "skill_version": version,
            "release_decision": {"selected_version": version},
            "skill_manifests": repository_module.skill_manifest_refs([manifest]),
            "model_id": "model-a",
            "model_value": "provider-model-a",
            "agent_profile": {
                "agent_id": "agt_support",
                "revision": 4,
                "content_hash": "a" * 64,
                "instructions": "private profile instruction",
                "required_skill_id": "profile-skill",
                "required_skill_version": version,
            },
        },
    }
    principal = AuthPrincipal(
        user_id="user-a",
        display_name="User A",
        tenant_id="tenant-a",
        roles=["user"],
    )

    async def resolve_bound(*_args, **_kwargs):
        skill = {
            "skill_id": "profile-skill",
            "skill_version": version,
            "executor_type": "claude-agent-worker",
        }
        return AgentProfileAdmission(
            agent_id="agt_support",
            revision=4,
            content_hash="a" * 64,
            skill=skill,
            skills=(skill,),
            mcp_tool_ids=("profile-tool",),
            private_execution_input={
                "agent_id": "agt_support",
                "revision": 4,
                "content_hash": "a" * 64,
                "instructions": "private profile instruction",
                "skill_set": [{"skill_id": "profile-skill", "expected_version": version}],
            },
            public_identity=AgentConversationIdentity(
                agent_id="agt_support",
                revision=4,
                name="Support assistant",
            ),
        )

    async def get_run(*_args, **_kwargs):
        return source

    async def materialize(*_args, **_kwargs):
        return [manifest]

    async def validate(*_args, **_kwargs):
        return ["profile-tool"]

    monkeypatch.setattr("app.agent_apps.authority.repositories.get_authorized_run", get_run)
    monkeypatch.setattr("app.agent_apps.authority.repositories.materialize_run_skill_manifests", materialize)
    monkeypatch.setattr("app.agent_apps.authority.repositories.validate_replay_skill_manifests", validate)
    monkeypatch.setattr("app.agent_apps.authority.repositories.require_replay_source_identity", lambda **_kwargs: None)
    authority = AgentProfileAuthority()
    monkeypatch.setattr(authority, "resolve_bound_for_submission", resolve_bound)

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
        starter_prompts=[],
        instructions="Private instruction",
        skill_set=[{"skill_id": "general-chat"}],
        mcp_tool_ids=[],
        avatar_ref="builtin:agent",
        avatar_seed="support-assistant",
        market_tags=["support"],
        expected_draft_revision=expected_draft_revision,
    )


async def test_profile_draft_save_requires_explicit_create_or_update_precondition():
    authority = AgentProfileAuthority()

    principal = AuthPrincipal(
        user_id="admin-a",
        display_name="Admin A",
        tenant_id="tenant-a",
        roles=["admin"],
    )
    with pytest.raises(HTTPException) as create_error:
        await authority.save_draft(object(), principal=principal, definition=_draft(expected_draft_revision=2), agent_id=None)
    assert create_error.value.status_code == 409
    assert create_error.value.detail == "agent_profile_create_revision_invalid"

    with pytest.raises(HTTPException) as update_error:
        await authority.save_draft(
            object(),
            principal=principal,
            definition=_draft(expected_draft_revision=0),
            agent_id="agt_support",
        )
    assert update_error.value.status_code == 409
    assert update_error.value.detail == "agent_profile_revision_stale"


async def test_mock_profile_revision_fence_allows_one_concurrent_publish_from_the_same_draft():
    from app.agent_apps.infrastructure.postgres import create_agent_profile_revision

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
                        "starter_prompts": [],
                        "instructions": params[7],
                        "skill_set": [{"skill_id": "general-chat"}],
                        "mcp_tool_ids": [],
                        "content_hash": params[10],
                        "avatar_ref": params[11],
                        "avatar_seed": params[12],
                        "market_tags": [],
                        "visibility": params[14],
                        "allowed_department_ids": [],
                        "allowed_roles": [],
                        "allowed_user_ids": [],
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
            starter_prompts=[],
            instructions="Private instruction",
            skill_set=[{"skill_id": "general-chat"}],
            mcp_tool_ids=[],
            avatar_ref="builtin:agent",
            avatar_seed="support-assistant",
            market_tags=["support"],
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
