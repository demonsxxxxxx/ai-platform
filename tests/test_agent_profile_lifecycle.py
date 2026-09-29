import app.conversations.infrastructure.postgres as _owner_conversations_infrastructure_postgres
import app.platform.postgres.errors as _owner_platform_postgres_errors
import subprocess
import sys
from uuid import UUID

import pytest
from fastapi import HTTPException

from app.auth import AuthPrincipal


def test_agent_apps_package_defers_authority_until_a_public_export_is_read():
    program = """
import sys

import app.agent_apps as agent_apps

assert "app.agent_apps.authority" not in sys.modules
assert agent_apps.__all__ == [
    "AgentProfileAdmission",
    "AgentProfileAuthority",
    "conversation_identity_projection",
    "profile_acl_allows",
    "profile_public_projection",
]
try:
    agent_apps.unknown_export
except AttributeError:
    pass
else:
    raise AssertionError("unknown Agent Apps exports must fail closed")
assert "app.agent_apps.authority" not in sys.modules
exported_authority = agent_apps.AgentProfileAuthority
authority = sys.modules["app.agent_apps.authority"]
assert exported_authority is authority.AgentProfileAuthority
for name in agent_apps.__all__:
    assert getattr(agent_apps, name) is getattr(authority, name)
"""

    subprocess.run([sys.executable, "-c", program], check=True)


@pytest.fixture(autouse=True)
def available_profile_mcp(monkeypatch):
    async def available(_conn, *, tool_ids, **_kwargs):
        return [{"tool_id": item} for item in tool_ids]
    monkeypatch.setattr("app.agent_apps.authority.mcp_api.authorize_available_chat_mcp_tools", available)


def _principal(*, roles: list[str] | None = None, department_id: str = "") -> AuthPrincipal:
    return AuthPrincipal(
        user_id="user-a",
        display_name="User A",
        tenant_id="tenant-a",
        department_id=department_id,
        roles=roles or ["user"],
    )


def test_profile_acl_and_safe_projection_are_owned_by_the_agent_apps_module():
    from app.agent_apps import profile_acl_allows, profile_public_projection

    row = {
        "agent_id": "agt_support",
        "revision": 7,
        "name": "Support assistant",
        "description": "Approved support help.",
        "starter_prompts": [],
        "published_at": None,
        "avatar_ref": "builtin:assistant",
        "avatar_seed": "agt_support",
        "market_tags": [],
        "visibility": "restricted",
        "allowed_department_ids": ["研发一部"],
        "allowed_roles": ["user"],
        "allowed_user_ids": [],
        "instructions": "private instruction",
        "model_id": "private-model",
        "skill_id": "private-skill",
        "skill_version": "private-version",
        "mcp_tool_ids": ["private-tool"],
        "content_hash": "a" * 64,
    }

    assert profile_acl_allows(row, principal=_principal(department_id="研发一部")) is True
    assert profile_acl_allows(row, principal=_principal(department_id="研发二部")) is False
    invalid_visibility = {**row, "visibility": "unknown", "allowed_department_ids": [], "allowed_roles": []}
    assert profile_acl_allows(invalid_visibility, principal=_principal(department_id="support")) is False
    assert profile_public_projection(row) == {
        "agent_id": "agt_support",
        "expected_revision": 7,
        "name": "Support assistant",
        "description": "Approved support help.",
        "starter_prompts": [],
        "published_at": None,
        "avatar_ref": "builtin:assistant",
        "avatar_seed": "agt_support",
        "market_tags": [],
    }
    assert profile_public_projection({**row, "completed_tasks": 12})["completed_tasks"] == 12
    assert "completed_tasks" not in profile_public_projection(row)


@pytest.mark.asyncio
async def test_profile_department_authority_accepts_only_current_selectable_directory_ids(monkeypatch):
    from app.agent_apps import AgentProfileAuthority
    from app.department_directory import (
        DepartmentDirectoryError,
        normalize_department_directory,
        validate_profile_department_authorities,
    )
    from app.models import AgentProfileDraftRequest

    directory = normalize_department_directory(
        [
            {"value": "1", "parentId": "1", "label": "药品注册", "children": []},
            {"value": "2", "parentId": "1", "label": "Research", "children": []},
            {"value": "3", "parentId": "1", "label": "Ｒｅｓｅａｒｃｈ", "children": []},
        ]
    )

    async def fetch_directory():
        return directory

    monkeypatch.setattr("app.department_directory.fetch_department_directory", fetch_directory)
    authority = AgentProfileAuthority(
        department_authority_validator=validate_profile_department_authorities,
    )
    definition = AgentProfileDraftRequest(
        name="Support assistant",
        instructions="private instruction",
        visibility="restricted",
        allowed_department_ids=["药品注册"],
        skill_set=[{"skill_id": "general-chat"}],
        expected_draft_revision=0,
    )

    await authority._validate_profile_department_authorities(definition)
    for invalid_department_id in ("Research", "目录外部门"):
        with pytest.raises(HTTPException) as exc_info:
            await authority._validate_profile_department_authorities(
                definition.model_copy(
                    update={"allowed_department_ids": [invalid_department_id]},
                )
            )
        assert (exc_info.value.status_code, exc_info.value.detail) == (
            422,
            "agent_profile_department_authority_invalid",
        )

    async def unavailable_directory():
        raise DepartmentDirectoryError("private_upstream_detail")

    monkeypatch.setattr("app.department_directory.fetch_department_directory", unavailable_directory)
    with pytest.raises(HTTPException) as exc_info:
        await authority._validate_profile_department_authorities(definition)
    assert (exc_info.value.status_code, exc_info.value.detail) == (
        503,
        "agent_profile_department_directory_unavailable",
    )


@pytest.mark.asyncio
async def test_unpublished_profile_is_not_admitted_to_an_existing_agent_conversation(monkeypatch):
    from app.agent_apps import AgentProfileAuthority
    from app.models import SelectedAgentProfileRequest

    async def no_current_publication(*_args, **_kwargs):
        return None

    monkeypatch.setattr(
        "app.agent_apps.authority.agent_profile_repository.get_current_published_agent_profile",
        no_current_publication,
    )
    authority = AgentProfileAuthority()

    with pytest.raises(Exception) as caught:
        await authority.resolve_for_admission(
            object(),
            principal=_principal(),
            selection=SelectedAgentProfileRequest(agent_id="agt_support"),
        )

    assert getattr(caught.value, "detail", None) == "agent_profile_not_available"


def _profile_row(
    *,
    status: str = "published",
    revision: int = 7,
    content_hash: str | None = None,
) -> dict[str, object]:
    row: dict[str, object] = {
        "agent_id": "agt_support",
        "revision": revision,
        "status": status,
        "name": "Support assistant",
        "description": "Approved support help.",
        "starter_prompts": [],
        "avatar_ref": "builtin:assistant",
        "avatar_seed": "agt_support",
        "market_tags": ["support"],
        "visibility": "tenant",
        "allowed_department_ids": [],
        "allowed_roles": [],
        "allowed_user_ids": [],
        "instructions": "private instruction",
        "skill_set": [{"skill_id": "general-chat"}],
        "mcp_tool_ids": [],
        "content_hash": content_hash or "",
    }
    return _seal_profile_row(row) if content_hash is None else row


def test_admin_profile_revision_projection_preserves_market_tags():
    from app.agent_apps.authority import _admin_projection, _draft_from_row

    row = _profile_row()
    row["market_tags"] = ["客户服务"]

    assert _draft_from_row(row).market_tags == ["客户服务"]
    assert _admin_projection(row).market_tags == ["客户服务"]


def _seal_profile_row(row: dict[str, object]) -> dict[str, object]:
    from app.agent_apps.authority import _draft_from_row, _revision_hash

    row["content_hash"] = _revision_hash(_draft_from_row(row))
    return row


@pytest.mark.asyncio
async def test_profile_definition_validates_stable_mcp_reference_and_server_existence(monkeypatch):
    from app.agent_apps import AgentProfileAuthority
    from app.agent_apps.authority import _draft_from_row

    row = _profile_row()
    row["mcp_tool_ids"] = ["gateway::search"]
    observed: list[tuple[str, str]] = []
    observed_skill: dict[str, object] = {}

    async def authorize_skill(*_args, **_kwargs):
        observed_skill.update(_kwargs)
        return {"skill_id": "general-chat", "skill_version": "version-b"}

    async def resolve_skill(*_args, **_kwargs):
        return {"skill_version": "version-b"}

    async def get_server(*_args, **kwargs):
        observed.append((kwargs["tenant_id"], kwargs["name"]))
        return {"name": kwargs["name"], "status": "disabled"}

    monkeypatch.setattr(
        'app.skills.infrastructure.resolution_postgres.resolve_selected_skill',
        resolve_skill,
    )
    monkeypatch.setattr(
        'app.runs.infrastructure.capability_admission_postgres.authorize_current_selected_run_capabilities',
        authorize_skill,
    )
    monkeypatch.setattr(
        "app.agent_apps.authority.mcp_api.get_mcp_server_registry_entry",
        get_server,
    )

    skills = await AgentProfileAuthority()._validate_definition(
        object(),
        principal=_principal(),
        agent_id="agt_support",
        definition=_draft_from_row(row),
    )

    assert skills[0]["skill_id"] == "general-chat"
    assert skills[0]["skill_version"] == "version-b"
    assert "expected_version" not in observed_skill
    assert "allow_current_version" not in observed_skill
    assert observed == [("tenant-a", "gateway")]


@pytest.mark.asyncio
async def test_profile_definition_preserves_repository_authorization_status(monkeypatch):
    from app.agent_apps import AgentProfileAuthority
    from app.agent_apps.authority import _draft_from_row

    async def deny_skill(*_args, **_kwargs):
        raise _owner_platform_postgres_errors.RepositoryAuthorizationError("denied")

    async def resolve_skill(*_args, **_kwargs):
        return {"skill_version": "version-a"}

    monkeypatch.setattr(
        'app.skills.infrastructure.resolution_postgres.resolve_selected_skill',
        resolve_skill,
    )
    monkeypatch.setattr(
        'app.runs.infrastructure.capability_admission_postgres.authorize_current_selected_run_capabilities',
        deny_skill,
    )

    with pytest.raises(HTTPException) as caught:
        await AgentProfileAuthority()._validate_definition(
            object(),
            principal=_principal(),
            agent_id="agt_support",
            definition=_draft_from_row(_profile_row()),
        )

    assert (caught.value.status_code, caught.value.detail) == (
        403,
        "agent_profile_capability_not_available",
    )


@pytest.mark.asyncio
async def test_mock_draft_and_publish_take_profile_lock_before_revision_or_aggregate_access(monkeypatch):
    """Record call order without claiming PostgreSQL lock-manager coverage."""

    from app.agent_apps import AgentProfileAuthority
    from app.agent_apps.authority import _draft_from_row, _revision_hash
    from app.models import AgentProfileDraftRequest

    order: list[str] = []
    revision_writes: list[dict[str, object]] = []

    async def lock_profile(*_args, **_kwargs):
        order.append("advisory_lock")

    async def ensure_user(*_args, **_kwargs):
        order.append("user")

    async def ensure_identity(*_args, **_kwargs):
        order.append("identity")

    async def append_revision(*_args, **kwargs):
        order.append("revision_append")
        revision_writes.append(kwargs)
        row = _profile_row(
            status=kwargs["status"],
            revision=kwargs["expected_previous_revision"] + 1,
            content_hash=kwargs["content_hash"],
        )
        row["skill_set"] = kwargs["skill_set"]
        return row

    async def record_draft(*_args, **_kwargs):
        order.append("aggregate_update")

    async def read_draft(*_args, **_kwargs):
        order.append("revision_read")
        row = _profile_row(status="draft", revision=7)
        row.update(
            visibility="restricted",
            allowed_department_ids=["药品注册"],
        )
        row["content_hash"] = _revision_hash(_draft_from_row(row))
        return row

    async def validation_agent(*_args, **_kwargs):
        return "agt_support"

    async def record_publication(*_args, **_kwargs):
        order.append("aggregate_update")

    async def audit(*_args, **_kwargs):
        return "aud_profile"

    async def validate_departments(values):
        assert values == ["药品注册"]
        order.append("department_validation")

    async def validate(*_args, **_kwargs):
        return ({"skill_id": "general-chat", "skill_version": "version-a"},)

    monkeypatch.setattr(
        "app.agent_apps.authority.agent_profile_repository.acquire_agent_profile_lifecycle_lock",
        lock_profile,
        raising=False,
    )
    monkeypatch.setattr('app.identity.infrastructure.postgres.ensure_submission_principal', ensure_user)
    monkeypatch.setattr("app.agent_apps.authority.agent_profile_repository.ensure_agent_profile_identity", ensure_identity)
    monkeypatch.setattr("app.agent_apps.authority.agent_profile_repository.create_agent_profile_revision", append_revision)
    monkeypatch.setattr("app.agent_apps.authority.agent_profile_repository.record_agent_profile_draft", record_draft)
    monkeypatch.setattr("app.agent_apps.authority.agent_profile_repository.get_agent_profile_revision", read_draft)
    monkeypatch.setattr(
        'app.agent_apps.infrastructure.catalog_postgres.get_tenant_profile_validation_agent',
        validation_agent,
    )
    monkeypatch.setattr("app.agent_apps.authority.agent_profile_repository.record_agent_profile_publication", record_publication)
    monkeypatch.setattr('app.identity.infrastructure.audit_postgres.append_audit_log', audit)
    authority = AgentProfileAuthority(
        department_authority_validator=validate_departments,
    )
    monkeypatch.setattr(authority, "_validate_definition", validate)
    definition = AgentProfileDraftRequest(
        name="Support assistant",
        description="Approved support help.",
        instructions="private instruction",
        visibility="restricted",
        allowed_department_ids=["药品注册"],
        skill_set=[{"skill_id": "general-chat"}],
        expected_draft_revision=7,
    )

    await authority.save_draft(
        object(),
        principal=_principal(roles=["admin"]),
        definition=definition,
        agent_id="agt_support",
    )
    assert order.index("user") < order.index("advisory_lock") < order.index("revision_append")
    assert order.index("advisory_lock") < order.index("department_validation") < order.index("revision_append")
    assert order.index("advisory_lock") < order.index("aggregate_update")
    assert revision_writes[-1]["skill_set"] == [{"skill_id": "general-chat"}]
    assert revision_writes[-1]["market_tags"] == []

    order.clear()
    await authority.publish_draft(
        object(),
        principal=_principal(roles=["admin"]),
        agent_id="agt_support",
        expected_revision=7,
    )
    assert order.index("advisory_lock") < order.index("revision_read")
    assert order.index("revision_read") < order.index("department_validation") < order.index("revision_append")
    assert order.index("user") < order.index("advisory_lock") < order.index("revision_append")
    assert order.index("advisory_lock") < order.index("aggregate_update")
    assert revision_writes[-1]["skill_set"] == [{"skill_id": "general-chat"}]
    assert revision_writes[-1]["market_tags"] == ["support"]

    order.clear()
    await authority.validate_draft(
        object(),
        principal=_principal(roles=["admin"]),
        definition=definition,
        agent_id=None,
    )
    assert order == ["user", "department_validation"]


@pytest.mark.asyncio
async def test_publish_rejects_a_tampered_draft_before_validation_or_append(monkeypatch):
    from app.agent_apps import AgentProfileAuthority
    from app.agent_apps.authority import _draft_from_row, _revision_hash

    draft = _profile_row(status="draft", revision=7)
    draft["content_hash"] = _revision_hash(_draft_from_row(draft))
    draft["instructions"] = "tampered after the immutable hash was written"
    calls: list[str] = []

    async def noop(*_args, **_kwargs):
        return None

    async def read_draft(*_args, **_kwargs):
        calls.append("read")
        return draft

    async def forbidden(*_args, **_kwargs):
        raise AssertionError("tampered draft must fail before validation, append, or audit")

    monkeypatch.setattr(
        'app.identity.infrastructure.postgres.ensure_submission_principal',
        noop,
    )
    monkeypatch.setattr(
        "app.agent_apps.authority.agent_profile_repository.acquire_agent_profile_lifecycle_lock",
        noop,
    )
    monkeypatch.setattr(
        "app.agent_apps.authority.agent_profile_repository.get_agent_profile_revision",
        read_draft,
    )
    monkeypatch.setattr(
        "app.agent_apps.authority.agent_profile_repository.create_agent_profile_revision",
        forbidden,
    )
    monkeypatch.setattr(
        'app.identity.infrastructure.audit_postgres.append_audit_log',
        forbidden,
    )
    authority = AgentProfileAuthority()
    monkeypatch.setattr(authority, "_validate_definition", forbidden)

    with pytest.raises(HTTPException) as caught:
        await authority.publish_draft(
            object(),
            principal=_principal(roles=["admin"]),
            agent_id="agt_support",
            expected_revision=7,
        )

    assert (caught.value.status_code, caught.value.detail) == (
        409,
        "agent_profile_revision_integrity_mismatch",
    )
    assert calls == ["read"]


@pytest.mark.parametrize("invalid_hash", ["", "not-a-sha256", "a" * 63])
@pytest.mark.asyncio
async def test_publish_rejects_an_unsigned_multi_skill_draft(monkeypatch, invalid_hash):
    from app.agent_apps import AgentProfileAuthority

    draft = _profile_row(status="draft", revision=7, content_hash=invalid_hash)
    draft["skill_set"] = [
        {"skill_id": "general-chat", "expected_version": "version-a"},
        {"skill_id": "qa-file-reviewer", "expected_version": "version-b"},
    ]

    async def noop(*_args, **_kwargs):
        return None

    async def read_draft(*_args, **_kwargs):
        return draft

    async def forbidden(*_args, **_kwargs):
        raise AssertionError("unsigned draft must fail before validation, append, or audit")

    monkeypatch.setattr(
        'app.identity.infrastructure.postgres.ensure_submission_principal',
        noop,
    )
    monkeypatch.setattr(
        "app.agent_apps.authority.agent_profile_repository.acquire_agent_profile_lifecycle_lock",
        noop,
    )
    monkeypatch.setattr(
        "app.agent_apps.authority.agent_profile_repository.get_agent_profile_revision",
        read_draft,
    )
    monkeypatch.setattr(
        "app.agent_apps.authority.agent_profile_repository.create_agent_profile_revision",
        forbidden,
    )
    monkeypatch.setattr(
        'app.identity.infrastructure.audit_postgres.append_audit_log',
        forbidden,
    )
    authority = AgentProfileAuthority()
    monkeypatch.setattr(authority, "_validate_definition", forbidden)

    with pytest.raises(HTTPException) as caught:
        await authority.publish_draft(
            object(),
            principal=_principal(roles=["admin"]),
            agent_id="agt_support",
            expected_revision=7,
        )

    assert (caught.value.status_code, caught.value.detail) == (
        409,
        "agent_profile_revision_integrity_mismatch",
    )

@pytest.mark.asyncio
async def test_profile_authority_provisions_and_tenant_validates_admin_fk_identity(monkeypatch):
    import app.identity.infrastructure.postgres as _repo_app_identity_infrastructure_postgres
    from app.agent_apps import AgentProfileAuthority

    calls: list[dict[str, object]] = []

    async def provision(*_args, **kwargs):
        calls.append(kwargs)
        return {"id": kwargs["user_id"], "tenant_id": kwargs["tenant_id"]}

    monkeypatch.setattr(_repo_app_identity_infrastructure_postgres, 'ensure_submission_principal', provision)
    await AgentProfileAuthority()._ensure_principal_user(  # noqa: SLF001 - focused authority contract
        object(),
        principal=_principal(roles=["admin"]),
    )
    assert calls == [
        {
            "tenant_id": "tenant-a",
            "user_id": "user-a",
            "display_name": "User A",
        }
    ]

    async def wrong_tenant(*_args, **_kwargs):
        raise _owner_platform_postgres_errors.RepositoryAuthorizationError("principal_user_scope_mismatch")

    monkeypatch.setattr(_repo_app_identity_infrastructure_postgres, 'ensure_submission_principal', wrong_tenant)
    with pytest.raises(HTTPException) as caught:
        await AgentProfileAuthority()._ensure_principal_user(  # noqa: SLF001 - focused authority contract
            object(),
            principal=_principal(roles=["admin"]),
        )
    assert (caught.value.status_code, caught.value.detail) == (403, "principal_not_authorized")


@pytest.mark.asyncio
async def test_profile_update_persists_the_submitted_canonical_definition(monkeypatch):
    from app.agent_apps import AgentProfileAuthority
    from app.models import AgentProfileDraftRequest

    captured: list[dict[str, object]] = []
    prior = _profile_row(status="draft", revision=7)
    prior.update(
        {
            "avatar_ref": "builtin:research",
            "avatar_seed": "support-custom-seed",
            "category": "research",
            "visibility": "restricted",
            "allowed_department_ids": ["research"],
            "allowed_roles": ["analyst"],
            "allowed_user_ids": ["user-special"],
        }
    )
    rows = {7: prior}

    async def noop(*_args, **_kwargs):
        return None

    async def read_prior(*_args, **kwargs):
        row = rows.get(int(kwargs["revision"]))
        if row is not None and kwargs.get("status") not in {None, row["status"]}:
            return None
        return row

    async def append_revision(*_args, **kwargs):
        captured.append(kwargs)
        revision = int(kwargs["expected_previous_revision"]) + 1
        row = _profile_row(
            status=str(kwargs["status"]),
            revision=revision,
            content_hash=str(kwargs["content_hash"]),
        )
        row.update(
            {
                field: kwargs[field]
                for field in (
                    "name",
                    "description",
                    "starter_prompts",
                    "instructions",
                    "skill_set",
                    "mcp_tool_ids",
                    "avatar_ref",
                    "avatar_seed",
                    "market_tags",
                    "visibility",
                    "allowed_department_ids",
                    "allowed_roles",
                    "allowed_user_ids",
                )
            }
        )
        rows[revision] = row
        return row

    async def audit(*_args, **_kwargs):
        return "aud_profile"

    monkeypatch.setattr("app.agent_apps.authority.agent_profile_repository.acquire_agent_profile_lifecycle_lock", noop)
    monkeypatch.setattr('app.identity.infrastructure.postgres.ensure_submission_principal', noop)
    monkeypatch.setattr("app.agent_apps.authority.agent_profile_repository.ensure_agent_profile_identity", noop)
    monkeypatch.setattr("app.agent_apps.authority.agent_profile_repository.get_agent_profile_revision", read_prior)
    monkeypatch.setattr("app.agent_apps.authority.agent_profile_repository.create_agent_profile_revision", append_revision)
    monkeypatch.setattr("app.agent_apps.authority.agent_profile_repository.record_agent_profile_draft", noop)
    monkeypatch.setattr("app.agent_apps.authority.agent_profile_repository.record_agent_profile_publication", noop)
    monkeypatch.setattr('app.identity.infrastructure.audit_postgres.append_audit_log', audit)
    authority = AgentProfileAuthority(department_authority_validator=noop)

    async def validate(*_args, **_kwargs):
        return ({"skill_id": "general-chat", "skill_version": "version-a"},)

    monkeypatch.setattr(authority, "_validate_definition", validate)

    omitted = AgentProfileDraftRequest(
        name="Updated support assistant",
        instructions="updated private instruction",
        skill_set=[{"skill_id": "general-chat"}],
        expected_draft_revision=7,
    )
    assert not {
        "avatar_ref",
        "avatar_seed",
        "market_tags",
        "visibility",
        "allowed_department_ids",
        "allowed_roles",
        "allowed_user_ids",
    }.intersection(omitted.model_fields_set)

    await authority.save_draft(
        object(),
        principal=_principal(roles=["admin"]),
        definition=omitted,
        agent_id="agt_support",
    )

    assert captured[-1]["avatar_ref"] == "builtin:agent"
    assert captured[-1]["avatar_seed"] == "agt_support"
    assert captured[-1]["market_tags"] == []
    assert captured[-1]["visibility"] == "tenant"
    assert captured[-1]["allowed_department_ids"] == []
    assert captured[-1]["allowed_roles"] == []
    assert captured[-1]["allowed_user_ids"] == []

    await authority.publish_draft(
        object(),
        principal=_principal(roles=["admin"]),
        agent_id="agt_support",
        expected_revision=8,
    )
    assert captured[-1]["status"] == "published"
    assert captured[-1]["visibility"] == "tenant"
    assert captured[-1]["allowed_department_ids"] == []
    assert captured[-1]["allowed_roles"] == []
    assert captured[-1]["allowed_user_ids"] == []

    explicit_empty = omitted.model_copy(
        update={
            "expected_draft_revision": 9,
            "avatar_seed": "support-updated-seed",
            "visibility": "restricted",
            "allowed_department_ids": [],
            "allowed_roles": [],
            "allowed_user_ids": [],
        }
    )
    explicit_empty.model_fields_set.update(
        {
            "avatar_seed",
            "visibility",
            "allowed_department_ids",
            "allowed_roles",
            "allowed_user_ids",
        }
    )
    await authority.save_draft(
        object(),
        principal=_principal(roles=["admin"]),
        definition=explicit_empty,
        agent_id="agt_support",
    )

    assert captured[-1]["visibility"] == "restricted"
    assert captured[-1]["avatar_seed"] == "support-updated-seed"
    assert captured[-1]["allowed_department_ids"] == []
    assert captured[-1]["allowed_roles"] == []
    assert captured[-1]["allowed_user_ids"] == []


@pytest.mark.asyncio
async def test_draft_preview_validates_the_submitted_canonical_definition(monkeypatch):
    from app.agent_apps import AgentProfileAuthority
    from app.models import AgentProfileDraftRequest

    prior = _profile_row(status="draft", revision=7)
    prior.update(
        {
            "avatar_ref": "builtin:research",
            "category": "research",
            "visibility": "restricted",
            "allowed_department_ids": ["research"],
            "allowed_roles": ["analyst"],
            "allowed_user_ids": ["user-special"],
        }
    )
    validated: list[AgentProfileDraftRequest] = []

    async def noop(*_args, **_kwargs):
        return None

    async def read_prior(*_args, **kwargs):
        assert kwargs["revision"] == 7
        assert kwargs["status"] == "draft"
        return prior

    async def read_aggregate(*_args, **kwargs):
        assert kwargs["for_update"] is True
        return {"latest_revision": 7}

    async def validate(*_args, **kwargs):
        validated.append(kwargs["definition"])
        return ({"skill_id": "general-chat", "skill_version": "version-a"},)

    async def audit(*_args, **_kwargs):
        return "aud_preview"

    monkeypatch.setattr('app.identity.infrastructure.postgres.ensure_submission_principal', noop)
    monkeypatch.setattr("app.agent_apps.authority.agent_profile_repository.acquire_agent_profile_lifecycle_lock", noop)
    monkeypatch.setattr("app.agent_apps.authority.agent_profile_repository.get_agent_profile_aggregate", read_aggregate)
    monkeypatch.setattr("app.agent_apps.authority.agent_profile_repository.get_agent_profile_revision", read_prior)
    monkeypatch.setattr('app.identity.infrastructure.audit_postgres.append_audit_log', audit)
    authority = AgentProfileAuthority(department_authority_validator=noop)
    monkeypatch.setattr(authority, "_validate_definition", validate)
    omitted = AgentProfileDraftRequest(
        name="Updated support assistant",
        instructions="updated private instruction",
        skill_set=[{"skill_id": "general-chat"}],
        expected_draft_revision=7,
    )

    await authority.validate_draft(
        object(),
        principal=_principal(roles=["admin"]),
        definition=omitted,
        agent_id="agt_support",
    )
    assert validated[-1].visibility == "tenant"
    assert validated[-1].allowed_department_ids == []
    assert validated[-1].allowed_roles == []
    assert validated[-1].allowed_user_ids == []

    explicit_empty = AgentProfileDraftRequest(
        name="Updated support assistant",
        instructions="updated private instruction",
        skill_set=[{"skill_id": "general-chat"}],
        visibility="restricted",
        allowed_department_ids=[],
        allowed_roles=[],
        allowed_user_ids=[],
        expected_draft_revision=7,
    )
    await authority.validate_draft(
        object(),
        principal=_principal(roles=["admin"]),
        definition=explicit_empty,
        agent_id="agt_support",
    )
    assert validated[-1].visibility == "restricted"
    assert validated[-1].allowed_department_ids == []
    assert validated[-1].allowed_roles == []
    assert validated[-1].allowed_user_ids == []


@pytest.mark.asyncio
async def test_draft_preview_rejects_a_superseded_revision_before_validation_or_audit(monkeypatch):
    from app.agent_apps import AgentProfileAuthority
    from app.models import AgentProfileDraftRequest

    order: list[str] = []

    async def ensure_user(*_args, **_kwargs):
        order.append("user")

    async def lifecycle_lock(*_args, **_kwargs):
        order.append("lifecycle_lock")

    async def aggregate(*_args, **kwargs):
        order.append("aggregate_lock")
        assert kwargs["for_update"] is True
        return {"latest_revision": 8}

    async def forbidden(*_args, **_kwargs):
        raise AssertionError("superseded preview must fail before revision validation or audit")

    monkeypatch.setattr('app.identity.infrastructure.postgres.ensure_submission_principal', ensure_user)
    monkeypatch.setattr(
        "app.agent_apps.authority.agent_profile_repository.acquire_agent_profile_lifecycle_lock",
        lifecycle_lock,
    )
    monkeypatch.setattr("app.agent_apps.authority.agent_profile_repository.get_agent_profile_aggregate", aggregate)
    monkeypatch.setattr("app.agent_apps.authority.agent_profile_repository.get_agent_profile_revision", forbidden)
    monkeypatch.setattr('app.identity.infrastructure.audit_postgres.append_audit_log', forbidden)
    authority = AgentProfileAuthority()
    monkeypatch.setattr(authority, "_validate_definition", forbidden)

    with pytest.raises(HTTPException) as caught:
        await authority.validate_draft(
            object(),
            principal=_principal(roles=["admin"]),
            definition=AgentProfileDraftRequest(
                name="Superseded draft",
                instructions="private instructions",
                skill_set=[{"skill_id": "general-chat"}],
                expected_draft_revision=7,
            ),
            agent_id="agt_support",
        )

    assert (caught.value.status_code, caught.value.detail) == (409, "agent_profile_revision_stale")
    assert order == ["user", "lifecycle_lock", "aggregate_lock"]


@pytest.mark.asyncio
async def test_public_detail_uses_the_same_acl_as_catalog(monkeypatch):
    from app.agent_apps import AgentProfileAuthority

    restricted = _profile_row()
    restricted.update({"visibility": "restricted", "allowed_department_ids": ["药品注册"]})
    _seal_profile_row(restricted)

    async def get_current(*_args, **_kwargs):
        return restricted

    async def list_current(*_args, **_kwargs):
        return [restricted]

    async def list_favorite_ids(*_args, **_kwargs):
        return set()

    monkeypatch.setattr("app.agent_apps.authority.agent_profile_repository.get_current_published_agent_profile", get_current)
    monkeypatch.setattr("app.agent_apps.authority.agent_profile_repository.list_current_published_agent_profiles", list_current)
    authority = AgentProfileAuthority(favorite_ids_loader=list_favorite_ids)

    async def validate(*_args, **_kwargs):
        return ({"skill_id": "general-chat", "skill_version": "version-a"},)

    monkeypatch.setattr(authority, "_validate_definition", validate)

    assert await authority.list_public(object(), principal=_principal(department_id="药品注册"))
    assert await authority.get_public(
        object(),
        principal=_principal(department_id="药品注册"),
        agent_id="agt_support",
    )
    assert await authority.list_public(object(), principal=_principal(department_id="药品注冊")) == []
    with pytest.raises(HTTPException) as caught:
        await authority.get_public(
            object(),
            principal=_principal(department_id="药品注冊"),
            agent_id="agt_support",
        )
    assert (caught.value.status_code, caught.value.detail) == (404, "agent_profile_not_found")


@pytest.mark.asyncio
async def test_favorite_does_not_bypass_public_profile_acl(monkeypatch):
    from app.agent_apps import AgentProfileAuthority

    restricted = _profile_row()
    restricted.update({"visibility": "restricted", "allowed_department_ids": ["药品注册"]})
    _seal_profile_row(restricted)
    writes: list[dict[str, object]] = []

    async def ensure_user(*_args, **_kwargs):
        return None

    async def get_current(*_args, **_kwargs):
        return restricted

    async def set_favorite(*_args, **kwargs):
        writes.append(kwargs)

    async def validate(*_args, **_kwargs):
        return ({"skill_id": "general-chat", "skill_version": "version-a"},)

    monkeypatch.setattr('app.identity.infrastructure.postgres.ensure_submission_principal', ensure_user)
    monkeypatch.setattr("app.agent_apps.authority.agent_profile_repository.get_current_published_agent_profile", get_current)
    authority = AgentProfileAuthority(favorite_setter=set_favorite)
    monkeypatch.setattr(authority, "_validate_definition", validate)

    with pytest.raises(HTTPException) as caught:
        await authority.set_favorite(
            object(),
            principal=_principal(department_id="药品注冊"),
            agent_id="agt_support",
            favorite=True,
        )

    assert (caught.value.status_code, caught.value.detail) == (404, "agent_profile_not_found")
    assert writes == []


@pytest.mark.asyncio
async def test_public_catalog_and_admission_reject_a_tampered_publication(monkeypatch):
    from app.agent_apps import AgentProfileAuthority
    from app.models import SelectedAgentProfileRequest

    tampered = _profile_row()
    tampered["instructions"] = "changed without advancing the immutable hash"

    async def get_current(*_args, **_kwargs):
        return tampered

    async def list_current(*_args, **_kwargs):
        return [tampered]

    async def list_favorite_ids(*_args, **_kwargs):
        return set()

    async def forbidden_validation(*_args, **_kwargs):
        raise AssertionError("integrity rejection must happen before capability validation")

    monkeypatch.setattr(
        "app.agent_apps.authority.agent_profile_repository.get_current_published_agent_profile",
        get_current,
    )
    monkeypatch.setattr(
        "app.agent_apps.authority.agent_profile_repository.list_current_published_agent_profiles",
        list_current,
    )
    authority = AgentProfileAuthority(favorite_ids_loader=list_favorite_ids)
    monkeypatch.setattr(authority, "_validate_definition", forbidden_validation)

    assert await authority.list_public(object(), principal=_principal()) == []
    with pytest.raises(HTTPException) as detail_error:
        await authority.get_public(
            object(),
            principal=_principal(),
            agent_id="agt_support",
        )
    assert (detail_error.value.status_code, detail_error.value.detail) == (
        404,
        "agent_profile_not_found",
    )
    with pytest.raises(HTTPException) as admission_error:
        await authority.resolve_for_admission(
            object(),
            principal=_principal(),
            selection=SelectedAgentProfileRequest(
                agent_id="agt_support",

            ),
        )
    assert (admission_error.value.status_code, admission_error.value.detail) == (
        409,
        "agent_profile_revision_integrity_mismatch",
    )


@pytest.mark.asyncio
async def test_bound_profile_uses_current_acl_while_executing_the_pinned_revision(monkeypatch):
    from app.agent_apps import AgentProfileAuthority

    pinned = _profile_row(revision=7)
    current = _profile_row(revision=9)
    current.update(
        {
            "visibility": "restricted",
            "allowed_department_ids": ["support"],
            "allowed_roles": [],
            "allowed_user_ids": [],
        }
    )
    _seal_profile_row(current)

    async def get_bound(*_args, **_kwargs):
        return pinned

    async def get_current(*_args, **_kwargs):
        return current

    async def forbidden_validation(*_args, **_kwargs):
        raise AssertionError("current ACL denial must happen before capability validation")

    monkeypatch.setattr("app.agent_apps.authority.agent_profile_repository.get_bound_published_agent_profile", get_bound)
    monkeypatch.setattr("app.agent_apps.authority.agent_profile_repository.get_current_published_agent_profile", get_current)
    authority = AgentProfileAuthority()
    monkeypatch.setattr(authority, "_validate_definition", forbidden_validation)

    with pytest.raises(HTTPException) as caught:
        await authority.resolve_pinned_profile_for_replay(
            object(),
            principal=_principal(department_id="finance"),
            agent_id="agt_support",
            revision=7,
            content_hash=str(pinned["content_hash"]),
        )

    assert (caught.value.status_code, caught.value.detail) == (403, "agent_profile_not_authorized")


@pytest.mark.asyncio
async def test_bound_profile_rejects_a_tampered_current_acl(monkeypatch):
    from app.agent_apps import AgentProfileAuthority

    pinned = _profile_row(revision=7)
    current = _profile_row(revision=9)
    current.update(
        visibility="restricted",
        allowed_department_ids=[],
        allowed_roles=[],
        allowed_user_ids=["other-user"],
    )
    _seal_profile_row(current)
    current["allowed_user_ids"] = ["user-a"]

    async def get_bound(*_args, **_kwargs):
        return pinned

    async def get_current(*_args, **_kwargs):
        return current

    async def forbidden_validation(*_args, **_kwargs):
        raise AssertionError("current ACL integrity must fail before capability validation")

    monkeypatch.setattr(
        "app.agent_apps.authority.agent_profile_repository.get_bound_published_agent_profile",
        get_bound,
    )
    monkeypatch.setattr(
        "app.agent_apps.authority.agent_profile_repository.get_current_published_agent_profile",
        get_current,
    )
    authority = AgentProfileAuthority()
    monkeypatch.setattr(authority, "_validate_definition", forbidden_validation)

    with pytest.raises(HTTPException) as caught:
        await authority.resolve_pinned_profile_for_replay(
            object(),
            principal=_principal(),
            agent_id="agt_support",
            revision=7,
            content_hash=str(pinned["content_hash"]),
        )
    assert (caught.value.status_code, caught.value.detail) == (
        409,
        "agent_profile_revision_integrity_mismatch",
    )
    assert await authority.resolve_bound_for_worker_dispatch(
        object(),
        principal=_principal(),
        agent_id="agt_support",
        revision=7,
        content_hash=str(pinned["content_hash"]),
    ) is None


@pytest.mark.asyncio
async def test_agent_conversation_admission_locks_and_pins_only_safe_identity(monkeypatch):
    from app.agent_apps import AgentProfileAuthority
    from app.models import SelectedAgentProfileRequest

    observed: dict[str, object] = {}
    profile_row = _profile_row()
    profile_row.update(
        visibility="restricted",
        allowed_department_ids=["药品注册"],
    )
    _seal_profile_row(profile_row)

    async def get_current(*_args, **kwargs):
        observed["for_update"] = kwargs.get("for_update")
        return profile_row

    async def validate(*_args, **_kwargs):
        return ({"skill_id": "general-chat", "skill_version": "version-a"},)

    async def remember_workspace(*_args, **_kwargs):
        observed["workspace"] = True

    async def remember_user(*_args, **_kwargs):
        observed["user"] = True

    async def create_session(*_args, **kwargs):
        observed["session"] = kwargs
        return "ses_profile"

    async def audit(*_args, **kwargs):
        observed["audit"] = kwargs
        return "aud_conversation"

    monkeypatch.setattr("app.agent_apps.authority.agent_profile_repository.get_current_published_agent_profile", get_current)
    monkeypatch.setattr('app.conversations.infrastructure.session_queries_postgres.ensure_workspace', remember_workspace)
    monkeypatch.setattr('app.identity.infrastructure.postgres.ensure_submission_principal', remember_user)
    monkeypatch.setattr('app.conversations.infrastructure.postgres.create_session', create_session)
    monkeypatch.setattr('app.identity.infrastructure.audit_postgres.append_audit_log', audit)
    authority = AgentProfileAuthority()
    monkeypatch.setattr(authority, "_validate_definition", validate)

    response = await authority.create_conversation(
        object(),
        principal=_principal(department_id="药品注册"),
        workspace_id="default",
        selection=SelectedAgentProfileRequest(agent_id="agt_support"),
        title="",
    )

    assert observed["for_update"] is True
    assert observed["workspace"] is True and observed["user"] is True
    assert observed["session"] == {
        "tenant_id": "tenant-a",
        "workspace_id": "default",
        "user_id": "user-a",
        "agent_id": "agt_support",
        "title": "Support assistant",
        "admitted_agent_profile_revision": 7,
        "admitted_agent_profile_hash": profile_row["content_hash"],
    }
    assert observed["audit"]["payload_json"] == {
        "revision": 7,
        "session_id": "ses_profile",
        "purpose": "conversation",
    }
    assert response.model_dump() == {
        "session_id": "ses_profile",
        "workspace_id": "default",
        "agent_id": "agt_support",
        "title": "Support assistant",
        "purpose": "conversation",
        "agent_conversation": {
            "agent_id": "agt_support",
            "revision": 7,
            "name": "Support assistant",
            "description": "Approved support help.",
            "starter_prompts": [],
            "avatar_ref": "builtin:assistant",
            "avatar_seed": "agt_support",
            "published_at": None,
        },
        "created_at": None,
        "updated_at": None,
    }
    assert "private instruction" not in str(response.model_dump())


@pytest.mark.asyncio
async def test_agent_conversation_operation_replay_returns_one_pinned_session_without_second_audit(monkeypatch):
    import app.platform.postgres.errors as _repo_app_platform_postgres_errors
    from app.agent_apps import AgentProfileAuthority
    from app.models import SelectedAgentProfileRequest

    state: dict[str, object] = {"published": True, "existing": None}
    calls: dict[str, int] = {"create": 0, "audit": 0, "admission": 0}
    operation_id = UUID("33333333-3333-4333-8333-333333333333")
    session_id = f"ses_agent_{operation_id.hex}"
    profile_row = _profile_row()

    async def get_current(*_args, **_kwargs):
        calls["admission"] += 1
        return profile_row if state["published"] else None

    async def get_session(*_args, **_kwargs):
        return state["existing"]

    async def validate(*_args, **_kwargs):
        return ({"skill_id": "general-chat", "skill_version": "version-a"},)

    async def create_session(*_args, **kwargs):
        calls["create"] += 1
        assert kwargs["session_id"] == session_id
        assert kwargs["return_created"] is True
        state["existing"] = {
            "id": session_id,
            "workspace_id": "default",
            "agent_id": "agt_support",
            "title": "Support assistant",
            "purpose": "conversation",
            "admitted_agent_profile_revision": 7,
            "admitted_agent_profile_hash": profile_row["content_hash"],
            "agent_profile_name": "Support assistant",
            "agent_profile_description": "Approved support help.",
            "agent_profile_starter_prompts": [],
            "agent_profile_avatar_ref": "builtin:assistant",
            "agent_profile_avatar_seed": "agt_support",
            "agent_profile_published_at": None,
        }
        return session_id, True

    async def audit(*_args, **_kwargs):
        calls["audit"] += 1
        return "aud_conversation"

    async def noop(*_args, **_kwargs):
        return None

    monkeypatch.setattr("app.agent_apps.authority.agent_profile_repository.get_current_published_agent_profile", get_current)
    monkeypatch.setattr('app.conversations.infrastructure.postgres.get_authorized_session_projection', get_session)
    monkeypatch.setattr('app.conversations.infrastructure.session_queries_postgres.ensure_workspace', noop)
    monkeypatch.setattr('app.identity.infrastructure.postgres.ensure_submission_principal', noop)
    monkeypatch.setattr('app.conversations.infrastructure.postgres.create_session', create_session)
    monkeypatch.setattr('app.identity.infrastructure.audit_postgres.append_audit_log', audit)
    authority = AgentProfileAuthority()
    monkeypatch.setattr(authority, "_validate_definition", validate)
    selection = SelectedAgentProfileRequest(agent_id="agt_support")

    first = await authority.create_conversation(
        object(),
        principal=_principal(),
        workspace_id="default",
        selection=selection,
        title="",
        operation_id=operation_id,
    )
    state["published"] = False
    replay = await authority.create_conversation(
        object(),
        principal=_principal(),
        workspace_id="default",
        selection=selection,
        title="",
        operation_id=operation_id,
    )

    assert first.session_id == replay.session_id == session_id
    assert replay.agent_conversation is not None
    assert replay.agent_conversation.revision == 7
    assert calls == {"create": 1, "audit": 1, "admission": 1}

    with pytest.raises(_repo_app_platform_postgres_errors.RepositoryConflictError, match="agent_conversation_operation_conflict"):
        await authority.create_conversation(
            object(),
            principal=_principal(),
            workspace_id="default",
            selection=SelectedAgentProfileRequest(agent_id="agt_other"),
            title="",
            operation_id=operation_id,
        )

    assert calls == {"create": 1, "audit": 1, "admission": 1}


@pytest.mark.parametrize(
    ("stored_title", "retry_title"),
    [
        ("Custom title", ""),
        ("Support assistant", "Custom title"),
        ("Custom title", " Custom title "),
        (" ", ""),
    ],
    ids=["custom-to-empty", "empty-to-custom", "surrounding-whitespace", "whitespace-to-empty"],
)
@pytest.mark.asyncio
async def test_agent_conversation_operation_replay_rejects_exact_title_mismatch(
    monkeypatch,
    stored_title,
    retry_title,
):
    import app.platform.postgres.errors as _repo_app_platform_postgres_errors
    from app.agent_apps import AgentProfileAuthority
    from app.models import SelectedAgentProfileRequest

    operation_id = UUID("33333333-3333-4333-8333-333333333333")
    existing = {
        "id": f"ses_agent_{operation_id.hex}",
        "workspace_id": "default",
        "agent_id": "agt_support",
        "title": stored_title,
        "purpose": "conversation",
        "admitted_agent_profile_revision": 7,
        "admitted_agent_profile_hash": "a" * 64,
        "agent_profile_name": "Support assistant",
        "agent_profile_description": "Approved support help.",
        "agent_profile_starter_prompts": [],
        "agent_profile_avatar_ref": "builtin:assistant",
        "agent_profile_avatar_seed": "agt_support",
        "agent_profile_published_at": None,
    }

    async def noop(*_args, **_kwargs):
        return None

    async def get_session(*_args, **_kwargs):
        return existing

    monkeypatch.setattr('app.conversations.infrastructure.session_queries_postgres.ensure_workspace', noop)
    monkeypatch.setattr('app.identity.infrastructure.postgres.ensure_submission_principal', noop)
    monkeypatch.setattr('app.conversations.infrastructure.postgres.get_authorized_session_projection', get_session)

    with pytest.raises(_repo_app_platform_postgres_errors.RepositoryConflictError, match="agent_conversation_operation_conflict"):
        await AgentProfileAuthority().create_conversation(
            object(),
            principal=_principal(),
            workspace_id="default",
            selection=SelectedAgentProfileRequest(agent_id="agt_support"),
            title=retry_title,
            operation_id=operation_id,
        )




@pytest.mark.asyncio
async def test_worker_dispatch_reauthorizes_one_locked_profile_row(monkeypatch):
    from app.agent_apps import AgentProfileAuthority

    row = _profile_row()
    row.update(
        visibility="restricted",
        allowed_department_ids=["药品注册"],
    )
    _seal_profile_row(row)
    calls: list[tuple[str, object]] = []

    async def get_bound(*_args, **kwargs):
        calls.append(("bound", kwargs))
        return row

    async def get_current(*_args, **_kwargs):
        return row

    async def validate(_conn, **kwargs):
        calls.append(("validate", [item["skill_id"] for item in kwargs["definition"].skill_set]))
        return ({"skill_id": "general-chat", "skill_version": "version-b"},)

    monkeypatch.setattr(
        "app.agent_apps.authority.agent_profile_repository.get_bound_published_agent_profile",
        get_bound,
    )
    monkeypatch.setattr(
        "app.agent_apps.authority.agent_profile_repository.get_current_published_agent_profile",
        get_current,
    )
    authority = AgentProfileAuthority()
    monkeypatch.setattr(authority, "_validate_definition", validate)
    pinned_skill_set = [{"skill_id": "general-chat", "expected_version": "version-a"}]
    pinned_manifests = [
        {
            "skill_id": "general-chat",
            "version": "version-a",
            "content_hash": "version-a",
            "dependency_ids": [],
        }
    ]

    admission = await authority.resolve_bound_for_worker_dispatch(
        object(),
        principal=_principal(department_id="药品注册"),
        agent_id="agt_support",
        revision=7,
        content_hash=str(row["content_hash"]),
        pinned_skill_set=pinned_skill_set,
        pinned_manifests=pinned_manifests,
        pinned_executor_type="claude-agent-worker",
        execution_kind="skill",
    )

    assert admission is not None
    assert admission.private_execution_input == {
        "agent_id": "agt_support",
        "revision": 7,
        "content_hash": row["content_hash"],
        "instructions": "private instruction",
        "skill_set": [
            {"skill_id": "general-chat", "expected_version": "version-a"}
        ],
    }
    assert admission.skill["skill_version"] == "version-a"
    assert [name for name, _ in calls] == ["bound", "validate"]
    assert calls[1] == ("validate", ["general-chat"])
    assert calls[0][1]["for_update"] is True


@pytest.mark.parametrize("denial", ["withdrawn", "hash_mismatch", "acl", "capability"])
@pytest.mark.asyncio
async def test_worker_dispatch_profile_reauthorization_fails_closed(monkeypatch, denial):
    from app.agent_apps import AgentProfileAuthority
    from app.agent_apps.authority import _draft_from_row, _revision_hash

    row = _profile_row()
    row["content_hash"] = _revision_hash(_draft_from_row(row))
    current_row = row
    expected_hash = str(row["content_hash"])
    calls = {"bound": 0, "validate": 0}
    if denial == "hash_mismatch":
        row["instructions"] = "changed without a new immutable hash"
    if denial == "acl":
        current_row = _profile_row(revision=9)
        current_row.update(
            visibility="restricted",
            allowed_department_ids=["药品注册"],
            allowed_roles=[],
            allowed_user_ids=[],
        )
        _seal_profile_row(current_row)

    async def get_bound(*_args, **_kwargs):
        calls["bound"] += 1
        return None if denial == "withdrawn" else row

    async def get_current(*_args, **_kwargs):
        return current_row

    async def validate(*_args, **_kwargs):
        calls["validate"] += 1
        if denial == "capability":
            raise HTTPException(status_code=403, detail="agent_profile_capability_not_available")
        return ({"skill_id": "general-chat", "skill_version": "version-a"},)

    monkeypatch.setattr(
        "app.agent_apps.authority.agent_profile_repository.get_bound_published_agent_profile",
        get_bound,
    )
    monkeypatch.setattr(
        "app.agent_apps.authority.agent_profile_repository.get_current_published_agent_profile",
        get_current,
    )
    authority = AgentProfileAuthority()
    monkeypatch.setattr(authority, "_validate_pinned_definition", validate)

    admission = await authority.resolve_bound_for_worker_dispatch(
        object(),
        principal=_principal(department_id="药品注冊") if denial == "acl" else _principal(),
        agent_id="agt_support",
        revision=7,
        content_hash=expected_hash,
        pinned_skill_set=[{"skill_id": "general-chat", "expected_version": "version-a"}],
        pinned_manifests=[
            {
                "skill_id": "general-chat",
                "version": "version-a",
                "content_hash": "version-a",
                "dependency_ids": [],
            }
        ],
        pinned_executor_type="claude-agent-worker",
        execution_kind="skill",
    )

    assert admission is None
    assert calls["bound"] == 1
    assert calls["validate"] == (1 if denial == "capability" else 0)


@pytest.mark.asyncio
async def test_worker_harness_profile_keeps_run_pin_without_skill_manifest(monkeypatch):
    from app.agent_apps import AgentProfileAuthority

    row = _profile_row()

    async def get_bound(*_args, **_kwargs):
        return row

    async def get_current(*_args, **_kwargs):
        return row

    async def validate_current_acl(*_args, **_kwargs):
        return ({"skill_id": "general-chat", "skill_version": "version-b"},)

    monkeypatch.setattr(
        "app.agent_apps.authority.agent_profile_repository.get_bound_published_agent_profile",
        get_bound,
    )
    monkeypatch.setattr(
        "app.agent_apps.authority.agent_profile_repository.get_current_published_agent_profile",
        get_current,
    )
    authority = AgentProfileAuthority()
    monkeypatch.setattr(authority, "_validate_definition", validate_current_acl)

    admission = await authority.resolve_bound_for_worker_dispatch(
        object(),
        principal=_principal(),
        agent_id="agt_support",
        revision=7,
        content_hash=str(row["content_hash"]),
        pinned_skill_set=[{"skill_id": "general-chat", "expected_version": "version-a"}],
        pinned_manifests=[],
        pinned_executor_type="claude-agent-worker",
        execution_kind="harness_chat",
    )

    assert admission is not None
    assert admission.private_execution_input["skill_set"] == [
        {"skill_id": "general-chat", "expected_version": "version-a"}
    ]
    assert admission.skill["skill_version"] == "version-a"




@pytest.mark.asyncio
async def test_unpublish_records_an_immutable_withdrawn_revision_and_clears_admission(monkeypatch):
    from app.agent_apps import AgentProfileAuthority
    from app.agent_apps.authority import (
        _draft_from_row,
        _revision_hash,
        _revision_hash_matches,
    )

    observed: dict[str, object] = {}
    order: list[str] = []

    async def lock_profile(*_args, **_kwargs):
        order.append("advisory_lock")

    async def ensure_user(*_args, **_kwargs):
        order.append("user")

    async def aggregate(*_args, **kwargs):
        order.append("aggregate_lock")
        observed["aggregate_lock"] = kwargs.get("for_update")
        return {"lifecycle_status": "published", "published_revision": 7, "latest_revision": 8}

    async def get_revision(*_args, **kwargs):
        observed.setdefault("revision_lookups", []).append(kwargs)
        if kwargs["revision"] == 7:
            row = _profile_row(revision=7)
        else:
            row = _profile_row(status="draft", revision=8)
            row["name"] = "Unpublished authoring changes"
            row["instructions"] = "new draft instructions"
            row["avatar_seed"] = "agt-support-draft"
        row["content_hash"] = _revision_hash(_draft_from_row(row))
        return row

    async def append_revision(*_args, **kwargs):
        observed["append"] = kwargs
        row = {
            **kwargs,
            "agent_id": "agt_support",
            "revision": 9,
            "published_at": None,
            "created_at": None,
        }
        observed["appended_row"] = row
        return row

    async def record_withdrawal(*_args, **kwargs):
        observed["withdrawal"] = kwargs

    async def audit(*_args, **kwargs):
        observed["audit"] = kwargs
        return "aud_profile_withdrawn"

    monkeypatch.setattr(
        "app.agent_apps.authority.agent_profile_repository.acquire_agent_profile_lifecycle_lock",
        lock_profile,
        raising=False,
    )
    monkeypatch.setattr('app.identity.infrastructure.postgres.ensure_submission_principal', ensure_user)
    monkeypatch.setattr("app.agent_apps.authority.agent_profile_repository.get_agent_profile_aggregate", aggregate)
    monkeypatch.setattr("app.agent_apps.authority.agent_profile_repository.get_agent_profile_revision", get_revision)
    monkeypatch.setattr("app.agent_apps.authority.agent_profile_repository.create_agent_profile_revision", append_revision)
    monkeypatch.setattr("app.agent_apps.authority.agent_profile_repository.record_agent_profile_withdrawal", record_withdrawal)
    monkeypatch.setattr('app.identity.infrastructure.audit_postgres.append_audit_log', audit)

    profile, audit_id = await AgentProfileAuthority().unpublish(
        object(),
        principal=_principal(roles=["admin"]),
        agent_id="agt_support",
        expected_revision=7,
    )

    assert observed["aggregate_lock"] is True
    assert order[:3] == ["user", "advisory_lock", "aggregate_lock"]
    assert observed["append"]["status"] == "withdrawn"
    assert observed["append"]["expected_previous_revision"] == 8
    assert observed["append"]["withdrawn_from_revision"] == 7
    assert observed["append"]["name"] == "Unpublished authoring changes"
    assert observed["append"]["instructions"] == "new draft instructions"
    assert observed["append"]["avatar_seed"] == "agt-support-draft"
    assert observed["append"]["content_hash"] != "a" * 64
    assert _revision_hash_matches(
        observed["appended_row"],
        str(observed["append"]["content_hash"]),
    )
    assert observed["revision_lookups"] == [
        {
            "tenant_id": "tenant-a",
            "agent_id": "agt_support",
            "revision": 7,
            "status": "published",
        },
        {"tenant_id": "tenant-a", "agent_id": "agt_support", "revision": 8},
    ]
    assert observed["withdrawal"] == {"tenant_id": "tenant-a", "agent_id": "agt_support", "revision": 9}
    assert profile.status == "withdrawn"
    assert audit_id == "aud_profile_withdrawn"


@pytest.mark.asyncio
@pytest.mark.parametrize("lifecycle_status", ["draft", "withdrawn"])
async def test_retire_deactivates_only_a_non_published_profile_identity(
    monkeypatch,
    lifecycle_status,
):
    from app.agent_apps import AgentProfileAuthority

    observed: list[tuple[str, object]] = []

    async def ensure_user(*_args, **_kwargs):
        observed.append(("user", None))

    async def lock_profile(*_args, **kwargs):
        observed.append(("lock", kwargs))

    async def aggregate(*_args, **kwargs):
        observed.append(("aggregate", kwargs))
        return {
            "tenant_id": "tenant-a",
            "agent_id": "agt_support",
            "lifecycle_status": lifecycle_status,
            "latest_revision": 9,
            "published_revision": None,
        }

    async def retire_identity(*_args, **kwargs):
        observed.append(("retire", kwargs))

    async def audit(*_args, **kwargs):
        observed.append(("audit", kwargs))
        return "aud_profile_retired"

    monkeypatch.setattr('app.identity.infrastructure.postgres.ensure_submission_principal', ensure_user)
    monkeypatch.setattr(
        "app.agent_apps.authority.agent_profile_repository.acquire_agent_profile_lifecycle_lock",
        lock_profile,
    )
    monkeypatch.setattr(
        "app.agent_apps.authority.agent_profile_repository.get_agent_profile_aggregate",
        aggregate,
    )
    monkeypatch.setattr(
        "app.agent_apps.authority.agent_profile_repository.retire_agent_profile_identity",
        retire_identity,
    )
    monkeypatch.setattr('app.identity.infrastructure.audit_postgres.append_audit_log', audit)

    audit_id = await AgentProfileAuthority().retire(
        object(),
        principal=_principal(roles=["admin"]),
        agent_id="agt_support",
        expected_revision=9,
    )

    assert audit_id == "aud_profile_retired"
    assert [name for name, _ in observed] == ["user", "lock", "aggregate", "retire", "audit"]
    assert observed[3][1] == {"tenant_id": "tenant-a", "agent_id": "agt_support"}
    assert observed[4][1]["action"] == "agent_profile.retired"
    assert observed[4][1]["payload_json"] == {
        "revision": 9,
        "previous_status": lifecycle_status,
    }


@pytest.mark.asyncio
async def test_retire_requires_unpublish_before_deactivation(monkeypatch):
    from app.agent_apps import AgentProfileAuthority

    async def no_user_write(*_args, **_kwargs):
        return None

    async def no_lock(*_args, **_kwargs):
        return None

    async def published_aggregate(*_args, **_kwargs):
        return {
            "lifecycle_status": "published",
            "latest_revision": 7,
            "published_revision": 7,
        }

    async def forbidden_retire(*_args, **_kwargs):
        raise AssertionError("published profile must not be retired")

    monkeypatch.setattr(
        'app.identity.infrastructure.postgres.ensure_submission_principal',
        no_user_write,
    )
    monkeypatch.setattr(
        "app.agent_apps.authority.agent_profile_repository.acquire_agent_profile_lifecycle_lock",
        no_lock,
    )
    monkeypatch.setattr(
        "app.agent_apps.authority.agent_profile_repository.get_agent_profile_aggregate",
        published_aggregate,
    )
    monkeypatch.setattr(
        "app.agent_apps.authority.agent_profile_repository.retire_agent_profile_identity",
        forbidden_retire,
    )

    with pytest.raises(HTTPException) as exc_info:
        await AgentProfileAuthority().retire(
            object(),
            principal=_principal(roles=["admin"]),
            agent_id="agt_support",
            expected_revision=7,
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "agent_profile_must_be_unpublished"


def test_profile_bound_continuation_rejects_client_execution_overrides():
    from app.agent_apps.authority import AgentProfileAuthority
    from app.models import ChatStreamRequest, SelectedSkillRequest

    request = ChatStreamRequest(
        message="continue",
        selected_skill=SelectedSkillRequest(skill_id="general-chat", expected_version="version-a"),
    )

    with pytest.raises(HTTPException) as caught:
        AgentProfileAuthority.reject_profile_selector_conflicts(request, active=True)
    assert (caught.value.status_code, caught.value.detail) == (400, "agent_profile_selector_conflict")


@pytest.mark.parametrize("bound", [False, True], ids=["marketplace-first-submit", "restored-continuation"])
@pytest.mark.asyncio
async def test_profile_authority_accepts_the_exact_canonical_frontend_transport_shape(
    monkeypatch,
    bound,
):
    """Mirror buildSubmitChatBody/buildSubmitChatUrl after JSON serialization."""

    from app.agent_apps import AgentProfileAuthority
    from app.models import ChatStreamRequest, SelectedAgentProfileRequest

    observed: list[tuple[str, bool | None]] = []
    profile_row = _profile_row()
    if bound:
        profile_row["mcp_tool_ids"] = ["gateway::profile-tool"]
        _seal_profile_row(profile_row)

    async def get_current(*_args, **kwargs):
        observed.append(("current", kwargs.get("for_update")))
        return profile_row

    async def get_bound(*_args, **kwargs):
        observed.append(("bound", kwargs.get("for_update")))
        return profile_row

    async def validate(*_args, **_kwargs):
        return ({"skill_id": "general-chat", "skill_version": "version-a"},)

    monkeypatch.setattr(
        "app.agent_apps.authority.agent_profile_repository.get_current_published_agent_profile",
        get_current,
    )
    monkeypatch.setattr(
        "app.agent_apps.authority.agent_profile_repository.get_bound_published_agent_profile",
        get_bound,
    )
    authority = AgentProfileAuthority()
    monkeypatch.setattr(authority, "_validate_definition", validate)
    request_payload = {
        "message": "continue with the published Agent",
        "agent_options": {
            "enable_thinking": "high",
            "model": "user-model-b",
            "model_id": "user-model-b",
        },
        "disabled_skills": [],
        "selected_mcp_tool_ids": [],
        "submission_id": "7ea93033-30f5-40ea-8a33-2f3c6e7b21c4",
        "user_timezone": "Asia/Shanghai",
    }
    if bound:
        request_payload["session_id"] = "ses_profile"
        query_agent_id = "agt_support"
    else:
        request_payload["selected_agent_profile"] = {"agent_id": "agt_support"}
        query_agent_id = "general-agent"
    request = ChatStreamRequest.model_validate(request_payload)

    if bound:
        admission = await authority.resolve_for_admission(
                object(),
                principal=_principal(),
                selection=SelectedAgentProfileRequest(agent_id="agt_support"),
            submitted_request=request,
            query_agent_id=query_agent_id,
        )
    else:
        admission = await authority.resolve_for_admission(
            object(),
            principal=_principal(),
            selection=SelectedAgentProfileRequest(
                agent_id="agt_support",

            ),
            submitted_request=request,
            query_agent_id=query_agent_id,
        )

    assert admission.agent_id == "agt_support"
    assert admission.revision == 7
    assert observed == [("current", True)]


@pytest.mark.asyncio
async def test_profile_authority_rejects_nonempty_client_mcp_selector_even_when_configured(
    monkeypatch,
):
    from app.agent_apps import AgentProfileAuthority
    from app.models import ChatStreamRequest, SelectedAgentProfileRequest

    profile_row = _profile_row()
    profile_row["mcp_tool_ids"] = ["gateway::profile-tool"]
    _seal_profile_row(profile_row)

    async def get_current(*_args, **_kwargs):
        return profile_row

    async def validate(*_args, **_kwargs):
        return ({"skill_id": "general-chat", "skill_version": "version-a"},)

    monkeypatch.setattr(
        "app.agent_apps.authority.agent_profile_repository.get_current_published_agent_profile",
        get_current,
    )
    authority = AgentProfileAuthority()
    monkeypatch.setattr(authority, "_validate_definition", validate)
    request = ChatStreamRequest.model_validate(
        {
            "message": "attempt to override the published expert",
            "selected_agent_profile": {"agent_id": "agt_support"},
            "selected_mcp_tool_ids": ["gateway::profile-tool"],
            "submission_id": "8eb026d4-2839-44db-83dd-5196ed80d9e8",
        }
    )

    with pytest.raises(HTTPException) as caught:
        await authority.resolve_for_admission(
            object(),
            principal=_principal(),
            selection=SelectedAgentProfileRequest(
                agent_id="agt_support",

            ),
            submitted_request=request,
            query_agent_id="general-agent",
        )

    assert (caught.value.status_code, caught.value.detail) == (
        400,
        "agent_profile_selector_conflict",
    )


@pytest.mark.asyncio
async def test_profile_admission_adds_authorized_skill_backing_mcp_without_client_redeclaration(
    monkeypatch,
):
    from app.agent_apps import AgentProfileAuthority
    from app.models import ChatStreamRequest, SelectedAgentProfileRequest

    profile_row = _profile_row()
    profile_row["skill_set"] = [
        {"skill_id": "skill-a"},
        {"skill_id": "skill-b"},
    ]
    _seal_profile_row(profile_row)

    async def get_current(*_args, **_kwargs):
        return profile_row

    async def validate(*_args, **_kwargs):
        return (
            {
                "skill_id": "skill-a",
                "skill_version": "version-a",
                "executor_type": "claude-agent-worker",
                "backing_mcp_tool_id": "skill-a-search",
            },
            {
                "skill_id": "skill-b",
                "skill_version": "version-b",
                "executor_type": "claude-agent-worker",
                "backing_mcp_tool_id": "skill-b-search",
            },
        )

    monkeypatch.setattr(
        "app.agent_apps.authority.agent_profile_repository.get_current_published_agent_profile",
        get_current,
    )
    authority = AgentProfileAuthority()
    monkeypatch.setattr(authority, "_validate_definition", validate)
    request = ChatStreamRequest.model_validate(
        {
            "message": "use the published expert",
            "selected_agent_profile": {"agent_id": "agt_support"},
            "selected_mcp_tool_ids": [],
            "submission_id": "7ea93033-30f5-40ea-8a33-2f3c6e7b21c4",
        }
    )

    admission = await authority.resolve_for_admission(
        object(),
        principal=_principal(),
        selection=SelectedAgentProfileRequest(
            agent_id="agt_support",

        ),
        submitted_request=request,
        query_agent_id="general-agent",
    )

    assert admission.configured_mcp_tool_ids == ()
    assert admission.mcp_tool_ids == ("skill-a-search", "skill-b-search")


@pytest.mark.parametrize("bound", [False, True], ids=["new", "continued"])
@pytest.mark.parametrize(
    ("request_payload", "query_agent_id"),
    [
        ({"agent_id": "general-agent"}, None),
        ({"skill_id": "general-chat"}, None),
        ({"disabled_skills": ["other-skill"]}, None),
        ({"enabled_skills": ["other-skill"]}, None),
        ({"disabled_mcp_tools": ["other-tool"]}, None),
        ({"selected_mcp_tool_ids": ["gateway::other-tool"]}, None),
        ({"agent_options": {"temperature": 0.2}}, None),
        (
            {
                "selected_skill": {
                    "skill_id": "general-chat",
                    "expected_version": "version-a",
                }
            },
            None,
        ),
        ({"confirmed_capability_id": "general_chat"}, None),
        ({"input": {"multi_agent_steps": [{"skillIds": ["other-skill"]}]}}, None),
        ({"input": {"multi_agent_steps": [{"tools": [{"mcpToolIds": ["other-tool"]}]}]}}, None),
        ({"input": {"multiAgentSteps": [{"mcpServerIds": ["other-server"]}]}}, None),
        (
            {
                "selectedAgentProfile": {"agent_id": "agt_support"}
            },
            None,
        ),
        ({"agentProfile": {"contentHash": "a" * 64}}, None),
        ({"instructions": "replace the published Prompt"}, None),
        ({"input": {"prompt": "replace the published Prompt"}}, None),
        ({"revision": 7}, None),
        ({}, "agt_other"),
    ],
    ids=[
        "top-level-agent",
        "raw-skill-selector",
        "nonempty-disabled-skill-selector",
        "enabled-skill-selector",
        "disabled-mcp-selector",
        "selected-mcp-selector",
        "unsupported-agent-option",
        "selected-skill-selector",
        "confirmed-capability",
        "nested-step-skill-alias",
        "nested-tool-mcp-alias",
        "nested-mcp-server-alias",
        "selected-profile-alias",
        "private-profile-hash-alias",
        "raw-instructions",
        "nested-prompt",
        "raw-revision",
        "query-agent",
    ],
)
@pytest.mark.asyncio
async def test_profile_authority_rejects_incompatible_client_selectors_after_profile_resolution(
    monkeypatch,
    bound,
    request_payload,
    query_agent_id,
):
    from app.agent_apps import AgentProfileAuthority
    from app.models import ChatStreamRequest, SelectedAgentProfileRequest

    storage_reads: list[str] = []
    profile_row = _profile_row()

    async def get_current(*_args, **_kwargs):
        storage_reads.append("current")
        return profile_row

    async def get_bound(*_args, **_kwargs):
        storage_reads.append("bound")
        return profile_row

    async def validate(*_args, **_kwargs):
        return ({"skill_id": "general-chat", "skill_version": "version-a"},)

    monkeypatch.setattr(
        "app.agent_apps.authority.agent_profile_repository.get_current_published_agent_profile",
        get_current,
    )
    monkeypatch.setattr(
        "app.agent_apps.authority.agent_profile_repository.get_bound_published_agent_profile",
        get_bound,
    )
    request = ChatStreamRequest.model_validate(
        {
            "message": "do not broaden the published definition",
            **request_payload,
        }
    )
    authority = AgentProfileAuthority()
    monkeypatch.setattr(authority, "_validate_definition", validate)

    with pytest.raises(HTTPException) as caught:
        if bound:
            await authority.resolve_for_admission(
                object(),
                principal=_principal(),
                selection=SelectedAgentProfileRequest(agent_id="agt_support"),
                submitted_request=request,
                query_agent_id=query_agent_id,
            )
        else:
            await authority.resolve_for_admission(
                object(),
                principal=_principal(),
                selection=SelectedAgentProfileRequest(
                    agent_id="agt_support",

                ),
                submitted_request=request,
                query_agent_id=query_agent_id,
            )

    assert (caught.value.status_code, caught.value.detail) == (400, "agent_profile_selector_conflict")
    assert storage_reads == ["current"]


def test_session_recovery_projects_only_safe_agent_conversation_identity():
    from app.chat_session_projection import session_response

    response = session_response(
        {
            "id": "ses_profile",
            "workspace_id": "default",
            "agent_id": "agt_support",
            "title": "Support thread",
            "admitted_agent_profile_revision": 7,
            "admitted_agent_profile_hash": "a" * 64,
            "agent_profile_name": "Support assistant",
            "agent_profile_description": "Approved support help.",
            "agent_profile_avatar_ref": "builtin:assistant",
            "agent_profile_avatar_seed": "agt_support",
            "instructions": "must never be projected",
            "model_id": "private-model",
            "skill_id": "private-skill",
            "mcp_tool_ids": ["private-tool"],
        }
    ).model_dump()

    assert response["agent_conversation"] == {
        "agent_id": "agt_support",
        "revision": 7,
        "name": "Support assistant",
        "description": "Approved support help.",
        "starter_prompts": [],
        "avatar_ref": "builtin:assistant",
        "avatar_seed": "agt_support",
        "published_at": None,
    }
    serialized = str(response)
    for forbidden in ("must never be projected", "private-model", "private-skill", "private-tool", "a" * 64):
        assert forbidden not in serialized


@pytest.mark.asyncio
async def test_dedicated_agent_run_forwards_http_request_to_chat_composition(monkeypatch):
    from contextlib import asynccontextmanager

    from app.models import AgentAppRunRequest
    from app.routes import agent_profiles

    connection = object()
    http_request = object()
    expected_response = object()
    observed: dict[str, object] = {}

    @asynccontextmanager
    async def transaction():
        yield connection

    async def get_session(observed_connection, **kwargs):
        assert observed_connection is connection
        assert kwargs == {
            "tenant_id": "tenant-a",
            "user_id": "user-a",
            "session_id": "session-a",
        }
        return {"workspace_id": "workspace-a", "agent_id": "agent-a"}

    async def chat_stream(request, observed_http_request, *, agent_id, principal):
        observed.update(
            request=request,
            http_request=observed_http_request,
            agent_id=agent_id,
            principal=principal,
        )
        return expected_response

    monkeypatch.setattr(agent_profiles, "transaction", transaction)
    monkeypatch.setattr(
        _owner_conversations_infrastructure_postgres,
        'get_authorized_session_projection',
        get_session,
    )
    monkeypatch.setattr("app.routes.chat.chat_stream", chat_stream)

    principal = _principal()
    result = await agent_profiles._submit_dedicated_agent_run(
        agent_id="agent-a",
        session_id="session-a",
        request=AgentAppRunRequest(
            message="hello",
            submission_id=UUID("12345678-1234-5678-1234-567812345678"),
            thinking_effort="high",
        ),
        http_request=http_request,
        principal=principal,
    )

    assert result is expected_response
    assert observed["http_request"] is http_request
    assert observed["agent_id"] == "agent-a"
    assert observed["principal"] is principal
    assert observed["request"].session_id == "session-a"
    assert observed["request"].agent_options == {"enable_thinking": "high"}

@pytest.mark.asyncio
async def test_new_turn_uses_current_publication_and_accepted_run_replays_its_snapshot(monkeypatch):
    from app.agent_apps import AgentProfileAuthority
    from app.models import SelectedAgentProfileRequest
    old, current = _profile_row(revision=7), _profile_row(revision=9)
    current["instructions"] = "current instructions"
    _seal_profile_row(current)
    state = {"current": old}
    async def get_current(*_args, **_kwargs):
        return state["current"]
    async def get_bound(*_args, **kwargs):
        assert kwargs["revision"] == 7 and kwargs["content_hash"] == old["content_hash"]
        return old
    async def validate(*_args, **_kwargs):
        return ({"skill_id": "general-chat", "skill_version": "version-a"},)
    monkeypatch.setattr("app.agent_apps.authority.agent_profile_repository.get_current_published_agent_profile", get_current)
    monkeypatch.setattr("app.agent_apps.authority.agent_profile_repository.get_bound_published_agent_profile", get_bound)
    authority = AgentProfileAuthority()
    monkeypatch.setattr(authority, "_validate_definition", validate)
    selected = SelectedAgentProfileRequest(agent_id="agt_support")
    first = await authority.resolve_for_admission(object(), principal=_principal(), selection=selected)
    state["current"] = current
    next_turn = await authority.resolve_for_admission(object(), principal=_principal(), selection=selected)
    replay = await authority.resolve_pinned_profile_for_replay(object(), principal=_principal(), agent_id="agt_support", revision=first.revision, content_hash=first.content_hash)
    assert (first.revision, next_turn.revision, replay.revision) == (7, 9, 7)
    assert next_turn.private_execution_input["instructions"] == "current instructions"
    assert replay.private_execution_input == first.private_execution_input
    state["current"] = None
    with pytest.raises(HTTPException):
        await authority.resolve_for_admission(object(), principal=_principal(), selection=selected)
