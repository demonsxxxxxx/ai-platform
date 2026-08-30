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
        "welcome_message": "",
        "starter_prompts": [],
        "capability_summary": "",
        "recommended_tasks": [],
        "supported_input_types": ["text"],
        "expected_outputs": [],
        "permissions_and_data_access_notice": "",
        "published_at": None,
        "avatar_ref": "builtin:assistant",
        "avatar_seed": "agt_support",
        "category": "support",
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
        "welcome_message": "",
        "starter_prompts": [],
        "capability_summary": "",
        "recommended_tasks": [],
        "supported_input_types": ["text", "file"],
        "expected_outputs": [],
        "permissions_and_data_access_notice": "",
        "published_at": None,
        "avatar_ref": "builtin:assistant",
        "avatar_seed": "agt_support",
        "category": "support",
    }


@pytest.mark.asyncio
async def test_profile_department_authority_accepts_only_current_selectable_directory_ids(monkeypatch):
    from app.agent_apps.authority import _validate_profile_department_authorities
    from app.department_directory import DepartmentDirectoryError, normalize_department_directory
    from app.models import AgentProfileDraftRequest, SelectedSkillRequest

    directory = normalize_department_directory(
        [
            {"value": "1", "parentId": "1", "label": "药品注册", "children": []},
            {"value": "2", "parentId": "1", "label": "Research", "children": []},
            {"value": "3", "parentId": "1", "label": "Ｒｅｓｅａｒｃｈ", "children": []},
        ]
    )

    async def fetch_directory():
        return directory

    monkeypatch.setattr("app.agent_apps.authority.fetch_department_directory", fetch_directory)
    definition = AgentProfileDraftRequest(
        name="Support assistant",
        description="Approved support help.",
        instructions="private instruction",
        visibility="restricted",
        allowed_department_ids=["药品注册"],
        selected_skill=SelectedSkillRequest(
            skill_id="general-chat",
            expected_version="version-a",
        ),
        expected_draft_revision=0,
    )

    await _validate_profile_department_authorities(definition)
    for invalid_department_id in ("Research", "目录外部门"):
        with pytest.raises(HTTPException) as exc_info:
            await _validate_profile_department_authorities(
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

    monkeypatch.setattr("app.agent_apps.authority.fetch_department_directory", unavailable_directory)
    with pytest.raises(HTTPException) as exc_info:
        await _validate_profile_department_authorities(definition)
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
        "app.agent_apps.authority.repositories.get_current_published_agent_profile",
        no_current_publication,
    )
    authority = AgentProfileAuthority()

    with pytest.raises(Exception) as caught:
        await authority.resolve_for_admission(
            object(),
            principal=_principal(),
            selection=SelectedAgentProfileRequest(agent_id="agt_support", expected_revision=7),
        )

    assert getattr(caught.value, "detail", None) == "agent_profile_not_available"


def _profile_row(
    *,
    status: str = "published",
    revision: int = 7,
    content_hash: str | None = None,
) -> dict[str, object]:
    from app.agent_apps.authority import _ROLLING_LEGACY_SUPPORTED_FILE_TYPES

    row: dict[str, object] = {
        "agent_id": "agt_support",
        "revision": revision,
        "status": status,
        "name": "Support assistant",
        "description": "Approved support help.",
        "welcome_message": "",
        "starter_prompts": [],
        "capability_summary": "",
        "recommended_tasks": [],
        "supported_input_types": ["text", "file"],
        "legacy_supported_file_types": list(_ROLLING_LEGACY_SUPPORTED_FILE_TYPES),
        "expected_outputs": [],
        "permissions_and_data_access_notice": "",
        "avatar_ref": "builtin:assistant",
        "avatar_asset_id": None,
        "avatar_seed": "agt_support",
        "category": "support",
        "visibility": "tenant",
        "allowed_department_ids": [],
        "allowed_roles": [],
        "allowed_user_ids": [],
        "instructions": "private instruction",
        "model_id": "model-a",
        "skill_id": "general-chat",
        "skill_version": "version-a",
        "skill_set": [
            {
                "skill_id": "general-chat",
                "expected_version": "version-a",
            }
        ],
        "mcp_tool_ids": [],
        "content_hash": content_hash or "",
    }
    return _seal_profile_row(row) if content_hash is None else row


def _seal_profile_row(row: dict[str, object]) -> dict[str, object]:
    from app.agent_apps.authority import _draft_from_row, _revision_hash

    row["content_hash"] = _revision_hash(_draft_from_row(row))
    return row


@pytest.mark.asyncio
async def test_mock_draft_and_publish_take_profile_lock_before_revision_or_aggregate_access(monkeypatch):
    """Record call order without claiming PostgreSQL lock-manager coverage."""

    from app.agent_apps import AgentProfileAuthority
    from app.agent_apps.authority import _draft_from_row, _revision_hash
    from app.models import AgentProfileDraftRequest, SelectedSkillRequest

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
        return _profile_row(
            status=kwargs["status"],
            revision=kwargs["expected_previous_revision"] + 1,
            content_hash=kwargs["content_hash"],
        )

    async def record_draft(*_args, **_kwargs):
        order.append("aggregate_update")

    async def read_draft(*_args, **_kwargs):
        order.append("revision_read")
        row = _profile_row(status="draft", revision=7)
        row["content_hash"] = _revision_hash(_draft_from_row(row))
        return row

    async def validation_agent(*_args, **_kwargs):
        return "agt_support"

    async def record_publication(*_args, **_kwargs):
        order.append("aggregate_update")

    async def audit(*_args, **_kwargs):
        return "aud_profile"

    async def validate_departments(*_args, **_kwargs):
        order.append("department_validation")

    async def validate(*_args, **_kwargs):
        return ({"skill_id": "general-chat", "skill_version": "version-a"},)

    monkeypatch.setattr(
        "app.agent_apps.authority.repositories.acquire_agent_profile_lifecycle_lock",
        lock_profile,
        raising=False,
    )
    monkeypatch.setattr("app.agent_apps.authority.repositories.ensure_submission_principal", ensure_user)
    monkeypatch.setattr("app.agent_apps.authority.repositories.ensure_agent_profile_identity", ensure_identity)
    monkeypatch.setattr("app.agent_apps.authority.repositories.create_agent_profile_revision", append_revision)
    monkeypatch.setattr("app.agent_apps.authority.repositories.record_agent_profile_draft", record_draft)
    monkeypatch.setattr("app.agent_apps.authority.repositories.get_agent_profile_revision", read_draft)
    monkeypatch.setattr(
        "app.agent_apps.authority.repositories.get_tenant_profile_validation_agent",
        validation_agent,
    )
    monkeypatch.setattr("app.agent_apps.authority.repositories.record_agent_profile_publication", record_publication)
    monkeypatch.setattr("app.agent_apps.authority.repositories.append_audit_log", audit)
    monkeypatch.setattr(
        "app.agent_apps.authority._validate_profile_department_authorities",
        validate_departments,
    )
    authority = AgentProfileAuthority()
    monkeypatch.setattr(authority, "_validate_definition", validate)
    definition = AgentProfileDraftRequest(
        name="Support assistant",
        description="Approved support help.",
        instructions="private instruction",
        selected_skill=SelectedSkillRequest(skill_id="general-chat", expected_version="version-a"),
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
    assert revision_writes[-1]["supported_input_types"] == ["text", "file"]
    assert revision_writes[-1]["legacy_supported_file_types"] == [
        "application/*",
        "audio/*",
        "chemical/*",
        "font/*",
        "image/*",
        "message/*",
        "model/*",
        "multipart/*",
        "text/*",
        "video/*",
    ]
    assert revision_writes[-1]["legacy_model_id"] == "platform-selected"
    assert "model_id" not in revision_writes[-1]

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
    assert revision_writes[-1]["supported_input_types"] == ["text", "file"]
    assert revision_writes[-1]["legacy_supported_file_types"] == [
        "application/*",
        "audio/*",
        "chemical/*",
        "font/*",
        "image/*",
        "message/*",
        "model/*",
        "multipart/*",
        "text/*",
        "video/*",
    ]
    assert revision_writes[-1]["legacy_model_id"] == "platform-selected"
    assert "model_id" not in revision_writes[-1]

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
        "app.agent_apps.authority.repositories.ensure_submission_principal",
        noop,
    )
    monkeypatch.setattr(
        "app.agent_apps.authority.repositories.acquire_agent_profile_lifecycle_lock",
        noop,
    )
    monkeypatch.setattr(
        "app.agent_apps.authority.repositories.get_agent_profile_revision",
        read_draft,
    )
    monkeypatch.setattr(
        "app.agent_apps.authority.repositories.create_agent_profile_revision",
        forbidden,
    )
    monkeypatch.setattr(
        "app.agent_apps.authority.repositories.append_audit_log",
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
        "app.agent_apps.authority.repositories.ensure_submission_principal",
        noop,
    )
    monkeypatch.setattr(
        "app.agent_apps.authority.repositories.acquire_agent_profile_lifecycle_lock",
        noop,
    )
    monkeypatch.setattr(
        "app.agent_apps.authority.repositories.get_agent_profile_revision",
        read_draft,
    )
    monkeypatch.setattr(
        "app.agent_apps.authority.repositories.create_agent_profile_revision",
        forbidden,
    )
    monkeypatch.setattr(
        "app.agent_apps.authority.repositories.append_audit_log",
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
    from app import repositories
    from app.agent_apps import AgentProfileAuthority

    calls: list[dict[str, object]] = []

    async def provision(*_args, **kwargs):
        calls.append(kwargs)
        return {"id": kwargs["user_id"], "tenant_id": kwargs["tenant_id"]}

    monkeypatch.setattr(repositories, "ensure_submission_principal", provision)
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
        raise repositories.RepositoryAuthorizationError("principal_user_scope_mismatch")

    monkeypatch.setattr(repositories, "ensure_submission_principal", wrong_tenant)
    with pytest.raises(HTTPException) as caught:
        await AgentProfileAuthority()._ensure_principal_user(  # noqa: SLF001 - focused authority contract
            object(),
            principal=_principal(roles=["admin"]),
        )
    assert (caught.value.status_code, caught.value.detail) == (403, "principal_not_authorized")


@pytest.mark.asyncio
async def test_profile_update_preserves_omitted_acl_metadata_but_honors_explicit_empty(monkeypatch):
    from app.agent_apps import AgentProfileAuthority
    from app.models import AgentProfileDraftRequest, SelectedSkillRequest

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
                    "instructions",
                    "skill_id",
                    "skill_version",
                    "mcp_tool_ids",
                    "avatar_ref",
                    "avatar_seed",
                    "category",
                    "visibility",
                    "allowed_department_ids",
                    "allowed_roles",
                    "allowed_user_ids",
                )
            }
            | {"model_id": kwargs["legacy_model_id"]}
        )
        rows[revision] = row
        return row

    async def audit(*_args, **_kwargs):
        return "aud_profile"

    monkeypatch.setattr("app.agent_apps.authority.repositories.acquire_agent_profile_lifecycle_lock", noop)
    monkeypatch.setattr("app.agent_apps.authority.repositories.ensure_submission_principal", noop)
    monkeypatch.setattr("app.agent_apps.authority.repositories.ensure_agent_profile_identity", noop)
    monkeypatch.setattr("app.agent_apps.authority.repositories.get_agent_profile_revision", read_prior)
    monkeypatch.setattr("app.agent_apps.authority.repositories.create_agent_profile_revision", append_revision)
    monkeypatch.setattr("app.agent_apps.authority.repositories.record_agent_profile_draft", noop)
    monkeypatch.setattr("app.agent_apps.authority.repositories.record_agent_profile_publication", noop)
    monkeypatch.setattr("app.agent_apps.authority.repositories.append_audit_log", audit)
    monkeypatch.setattr("app.agent_apps.authority._validate_profile_department_authorities", noop)
    authority = AgentProfileAuthority()

    async def validate(*_args, **_kwargs):
        return ({"skill_id": "general-chat", "skill_version": "version-a"},)

    monkeypatch.setattr(authority, "_validate_definition", validate)

    omitted = AgentProfileDraftRequest(
        name="Updated support assistant",
        instructions="updated private instruction",
        selected_skill=SelectedSkillRequest(skill_id="general-chat", expected_version="version-a"),
        expected_draft_revision=7,
    )
    assert not {
        "avatar_ref",
        "avatar_seed",
        "category",
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

    assert captured[-1]["avatar_ref"] == "builtin:research"
    assert captured[-1]["avatar_seed"] == "support-custom-seed"
    assert captured[-1]["category"] == "research"
    assert captured[-1]["visibility"] == "restricted"
    assert captured[-1]["allowed_department_ids"] == ["research"]
    assert captured[-1]["allowed_roles"] == ["analyst"]
    assert captured[-1]["allowed_user_ids"] == ["user-special"]

    await authority.publish_draft(
        object(),
        principal=_principal(roles=["admin"]),
        agent_id="agt_support",
        expected_revision=8,
    )
    assert captured[-1]["status"] == "published"
    assert captured[-1]["visibility"] == "restricted"
    assert captured[-1]["allowed_department_ids"] == ["research"]
    assert captured[-1]["allowed_roles"] == ["analyst"]
    assert captured[-1]["allowed_user_ids"] == ["user-special"]

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
async def test_draft_preview_uses_presence_aware_effective_existing_definition(monkeypatch):
    from app.agent_apps import AgentProfileAuthority
    from app.models import AgentProfileDraftRequest, SelectedSkillRequest

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

    monkeypatch.setattr("app.agent_apps.authority.repositories.ensure_submission_principal", noop)
    monkeypatch.setattr("app.agent_apps.authority.repositories.acquire_agent_profile_lifecycle_lock", noop)
    monkeypatch.setattr("app.agent_apps.authority.repositories.get_agent_profile_aggregate", read_aggregate)
    monkeypatch.setattr("app.agent_apps.authority.repositories.get_agent_profile_revision", read_prior)
    monkeypatch.setattr("app.agent_apps.authority.repositories.append_audit_log", audit)
    monkeypatch.setattr("app.agent_apps.authority._validate_profile_department_authorities", noop)
    authority = AgentProfileAuthority()
    monkeypatch.setattr(authority, "_validate_definition", validate)
    omitted = AgentProfileDraftRequest(
        name="Updated support assistant",
        instructions="updated private instruction",
        selected_skill=SelectedSkillRequest(skill_id="general-chat", expected_version="version-a"),
        expected_draft_revision=7,
    )

    await authority.validate_draft(
        object(),
        principal=_principal(roles=["admin"]),
        definition=omitted,
        agent_id="agt_support",
    )
    assert validated[-1].visibility == "restricted"
    assert validated[-1].allowed_department_ids == ["research"]
    assert validated[-1].allowed_roles == ["analyst"]
    assert validated[-1].allowed_user_ids == ["user-special"]

    explicit_empty = AgentProfileDraftRequest(
        name="Updated support assistant",
        instructions="updated private instruction",
        selected_skill=SelectedSkillRequest(skill_id="general-chat", expected_version="version-a"),
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
    from app.models import AgentProfileDraftRequest, SelectedSkillRequest

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

    monkeypatch.setattr("app.agent_apps.authority.repositories.ensure_submission_principal", ensure_user)
    monkeypatch.setattr(
        "app.agent_apps.authority.repositories.acquire_agent_profile_lifecycle_lock",
        lifecycle_lock,
    )
    monkeypatch.setattr("app.agent_apps.authority.repositories.get_agent_profile_aggregate", aggregate)
    monkeypatch.setattr("app.agent_apps.authority.repositories.get_agent_profile_revision", forbidden)
    monkeypatch.setattr("app.agent_apps.authority.repositories.append_audit_log", forbidden)
    authority = AgentProfileAuthority()
    monkeypatch.setattr(authority, "_validate_definition", forbidden)

    with pytest.raises(HTTPException) as caught:
        await authority.validate_draft(
            object(),
            principal=_principal(roles=["admin"]),
            definition=AgentProfileDraftRequest(
                name="Superseded draft",
                instructions="private instructions",
                selected_skill=SelectedSkillRequest(
                    skill_id="general-chat",
                    expected_version="version-a",
                ),
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

    monkeypatch.setattr("app.agent_apps.authority.repositories.get_current_published_agent_profile", get_current)
    monkeypatch.setattr("app.agent_apps.authority.repositories.list_current_published_agent_profiles", list_current)
    authority = AgentProfileAuthority()

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
async def test_public_catalog_and_admission_reject_a_tampered_publication(monkeypatch):
    from app.agent_apps import AgentProfileAuthority
    from app.models import SelectedAgentProfileRequest

    tampered = _profile_row()
    tampered["instructions"] = "changed without advancing the immutable hash"

    async def get_current(*_args, **_kwargs):
        return tampered

    async def list_current(*_args, **_kwargs):
        return [tampered]

    async def forbidden_validation(*_args, **_kwargs):
        raise AssertionError("integrity rejection must happen before capability validation")

    monkeypatch.setattr(
        "app.agent_apps.authority.repositories.get_current_published_agent_profile",
        get_current,
    )
    monkeypatch.setattr(
        "app.agent_apps.authority.repositories.list_current_published_agent_profiles",
        list_current,
    )
    authority = AgentProfileAuthority()
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
                expected_revision=7,
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

    monkeypatch.setattr("app.agent_apps.authority.repositories.get_bound_published_agent_profile", get_bound)
    monkeypatch.setattr("app.agent_apps.authority.repositories.get_current_published_agent_profile", get_current)
    authority = AgentProfileAuthority()
    monkeypatch.setattr(authority, "_validate_definition", forbidden_validation)

    with pytest.raises(HTTPException) as caught:
        await authority.resolve_bound_for_submission(
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
        "app.agent_apps.authority.repositories.get_bound_published_agent_profile",
        get_bound,
    )
    monkeypatch.setattr(
        "app.agent_apps.authority.repositories.get_current_published_agent_profile",
        get_current,
    )
    authority = AgentProfileAuthority()
    monkeypatch.setattr(authority, "_validate_definition", forbidden_validation)

    with pytest.raises(HTTPException) as caught:
        await authority.resolve_bound_for_submission(
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

    monkeypatch.setattr("app.agent_apps.authority.repositories.get_current_published_agent_profile", get_current)
    monkeypatch.setattr("app.agent_apps.authority.repositories.ensure_workspace", remember_workspace)
    monkeypatch.setattr("app.agent_apps.authority.repositories.ensure_submission_principal", remember_user)
    monkeypatch.setattr("app.agent_apps.authority.repositories.create_session", create_session)
    monkeypatch.setattr("app.agent_apps.authority.repositories.append_audit_log", audit)
    authority = AgentProfileAuthority()
    monkeypatch.setattr(authority, "_validate_definition", validate)

    response = await authority.create_conversation(
        object(),
        principal=_principal(department_id="药品注册"),
        workspace_id="default",
        selection=SelectedAgentProfileRequest(agent_id="agt_support", expected_revision=7),
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
            "welcome_message": "",
            "starter_prompts": [],
            "capability_summary": "",
            "recommended_tasks": [],
            "supported_input_types": ["text", "file"],
            "expected_outputs": [],
            "permissions_and_data_access_notice": "",
            "published_at": None,
            "avatar_ref": "builtin:assistant",
            "avatar_seed": "agt_support",
            "category": "support",
        },
        "created_at": None,
        "updated_at": None,
    }
    assert "private instruction" not in str(response.model_dump())


@pytest.mark.asyncio
async def test_agent_conversation_operation_replay_returns_one_pinned_session_without_second_audit(monkeypatch):
    from app import repositories
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
            "agent_profile_welcome_message": "",
            "agent_profile_starter_prompts": [],
            "agent_profile_capability_summary": "",
            "agent_profile_recommended_tasks": [],
            "agent_profile_supported_input_types": ["text"],
            "agent_profile_expected_outputs": [],
            "agent_profile_permissions_and_data_access_notice": "",
            "agent_profile_avatar_ref": "builtin:assistant",
            "agent_profile_category": "support",
            "agent_profile_published_at": None,
        }
        return session_id, True

    async def audit(*_args, **_kwargs):
        calls["audit"] += 1
        return "aud_conversation"

    async def noop(*_args, **_kwargs):
        return None

    monkeypatch.setattr("app.agent_apps.authority.repositories.get_current_published_agent_profile", get_current)
    monkeypatch.setattr("app.agent_apps.authority.repositories.get_authorized_session_projection", get_session)
    monkeypatch.setattr("app.agent_apps.authority.repositories.ensure_workspace", noop)
    monkeypatch.setattr("app.agent_apps.authority.repositories.ensure_submission_principal", noop)
    monkeypatch.setattr("app.agent_apps.authority.repositories.create_session", create_session)
    monkeypatch.setattr("app.agent_apps.authority.repositories.append_audit_log", audit)
    authority = AgentProfileAuthority()
    monkeypatch.setattr(authority, "_validate_definition", validate)
    selection = SelectedAgentProfileRequest(agent_id="agt_support", expected_revision=7)

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

    with pytest.raises(repositories.RepositoryConflictError, match="agent_conversation_operation_conflict"):
        await authority.create_conversation(
            object(),
            principal=_principal(),
            workspace_id="default",
            selection=SelectedAgentProfileRequest(agent_id="agt_support", expected_revision=8),
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
    from app import repositories
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
        "agent_profile_welcome_message": "",
        "agent_profile_starter_prompts": [],
        "agent_profile_capability_summary": "",
        "agent_profile_recommended_tasks": [],
        "agent_profile_supported_input_types": ["text"],
        "agent_profile_expected_outputs": [],
        "agent_profile_permissions_and_data_access_notice": "",
        "agent_profile_avatar_ref": "builtin:assistant",
        "agent_profile_category": "support",
        "agent_profile_published_at": None,
    }

    async def noop(*_args, **_kwargs):
        return None

    async def get_session(*_args, **_kwargs):
        return existing

    monkeypatch.setattr("app.agent_apps.authority.repositories.ensure_workspace", noop)
    monkeypatch.setattr("app.agent_apps.authority.repositories.ensure_submission_principal", noop)
    monkeypatch.setattr("app.agent_apps.authority.repositories.get_authorized_session_projection", get_session)

    with pytest.raises(repositories.RepositoryConflictError, match="agent_conversation_operation_conflict"):
        await AgentProfileAuthority().create_conversation(
            object(),
            principal=_principal(),
            workspace_id="default",
            selection=SelectedAgentProfileRequest(agent_id="agt_support", expected_revision=7),
            title=retry_title,
            operation_id=operation_id,
        )


@pytest.mark.asyncio
async def test_revision_bound_conversations_stay_on_their_publication_until_unpublish(monkeypatch):
    """This in-memory mirror proves policy; the PostgreSQL test proves storage locking."""

    from app.agent_apps import AgentProfileAuthority
    from app.models import SelectedAgentProfileRequest

    revision_7 = _profile_row(revision=7)
    revision_9 = _profile_row(revision=9)
    revision_9["instructions"] = "updated private instruction"
    _seal_profile_row(revision_9)
    publications = {7: revision_7, 9: revision_9}
    hash_7 = str(revision_7["content_hash"])
    hash_9 = str(revision_9["content_hash"])
    state = {"current_revision": 7, "lifecycle_status": "published"}
    observed: list[tuple[str, int, str | None, bool | None]] = []
    created_sessions: list[dict[str, object]] = []

    async def get_current(*_args, **kwargs):
        revision = kwargs.get("expected_revision")
        observed.append(("current", revision, None, kwargs.get("for_update")))
        effective_revision = state["current_revision"] if revision is None else revision
        if (
            state["lifecycle_status"] != "published"
            or effective_revision != state["current_revision"]
        ):
            return None
        return publications[effective_revision]

    async def get_bound(*_args, **kwargs):
        revision = kwargs["revision"]
        content_hash = kwargs["content_hash"]
        observed.append(("bound", revision, content_hash, kwargs.get("for_update")))
        row = publications.get(revision)
        if (
            state["lifecycle_status"] != "published"
            or row is None
            or row["content_hash"] != content_hash
        ):
            return None
        return row

    async def validate(*_args, **_kwargs):
        return ({"skill_id": "general-chat", "skill_version": "version-a"},)

    async def noop(*_args, **_kwargs):
        return None

    async def create_session(*_args, **kwargs):
        created_sessions.append(kwargs)
        return f"ses_{len(created_sessions)}"

    async def audit(*_args, **_kwargs):
        return "aud_conversation"

    monkeypatch.setattr("app.agent_apps.authority.repositories.get_current_published_agent_profile", get_current)
    monkeypatch.setattr(
        "app.agent_apps.authority.repositories.get_bound_published_agent_profile",
        get_bound,
        raising=False,
    )
    monkeypatch.setattr("app.agent_apps.authority.repositories.ensure_workspace", noop)
    monkeypatch.setattr("app.agent_apps.authority.repositories.ensure_submission_principal", noop)
    monkeypatch.setattr("app.agent_apps.authority.repositories.create_session", create_session)
    monkeypatch.setattr("app.agent_apps.authority.repositories.append_audit_log", audit)
    authority = AgentProfileAuthority()
    monkeypatch.setattr(authority, "_validate_definition", validate)

    first = await authority.create_conversation(
        object(),
        principal=_principal(),
        workspace_id="default",
        selection=SelectedAgentProfileRequest(agent_id="agt_support", expected_revision=7),
        title="",
    )
    # Publishing N+1 changes the current aggregate pointer but not the existing pin.
    state["current_revision"] = 9
    existing = await authority.resolve_bound_for_submission(
        object(),
        principal=_principal(),
        agent_id="agt_support",
        revision=7,
        content_hash=hash_7,
    )
    second = await authority.create_conversation(
        object(),
        principal=_principal(),
        workspace_id="default",
        selection=SelectedAgentProfileRequest(agent_id="agt_support", expected_revision=9),
        title="",
    )

    assert (first.agent_conversation.revision, existing.revision, second.agent_conversation.revision) == (7, 7, 9)
    assert existing.content_hash == hash_7
    assert [
        (session["admitted_agent_profile_revision"], session["admitted_agent_profile_hash"])
        for session in created_sessions
    ] == [(7, hash_7), (9, hash_9)]
    assert observed[-2:] == [
        ("current", None, None, None),
        ("current", 9, None, True),
    ]

    with pytest.raises(HTTPException, match="agent_profile_not_available"):
        await authority.resolve_for_admission(
            object(),
            principal=_principal(),
            selection=SelectedAgentProfileRequest(agent_id="agt_support", expected_revision=7),
        )
    with pytest.raises(HTTPException, match="agent_profile_not_available"):
        await authority.resolve_bound_for_submission(
            object(),
            principal=_principal(),
            agent_id="agt_support",
            revision=7,
            content_hash="forged-hash",
        )

    state["lifecycle_status"] = "withdrawn"
    for revision, content_hash in ((7, hash_7), (9, hash_9)):
        with pytest.raises(HTTPException, match="agent_profile_not_available"):
            await authority.resolve_bound_for_submission(
                object(),
                principal=_principal(),
                agent_id="agt_support",
                revision=revision,
                content_hash=content_hash,
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

    async def validate(*_args, **kwargs):
        calls.append(("validate", kwargs["definition"]))
        return ({"skill_id": "general-chat", "skill_version": "version-a"},)

    monkeypatch.setattr(
        "app.agent_apps.authority.repositories.get_bound_published_agent_profile",
        get_bound,
    )
    monkeypatch.setattr(
        "app.agent_apps.authority.repositories.get_current_published_agent_profile",
        get_current,
    )
    authority = AgentProfileAuthority()
    monkeypatch.setattr(authority, "_validate_definition", validate)

    admission = await authority.resolve_bound_for_worker_dispatch(
        object(),
        principal=_principal(department_id="药品注册"),
        agent_id="agt_support",
        revision=7,
        content_hash=str(row["content_hash"]),
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
    assert [name for name, _ in calls] == ["bound", "validate"]
    assert calls[0][1]["for_update"] is True


@pytest.mark.asyncio
async def test_worker_dispatch_accepts_only_the_exact_legacy_one_skill_hash(monkeypatch):
    from app.agent_apps import AgentProfileAuthority
    from app.agent_apps.authority import _draft_from_row, _legacy_revision_hash

    row = _profile_row()
    row["avatar_seed"] = ""
    row["legacy_supported_file_types"] = []
    row["content_hash"] = _legacy_revision_hash(_draft_from_row(row))

    async def get_bound(*_args, **_kwargs):
        return row

    async def get_current(*_args, **_kwargs):
        return row

    async def validate(*_args, **_kwargs):
        return ({"skill_id": "general-chat", "skill_version": "version-a"},)

    monkeypatch.setattr(
        "app.agent_apps.authority.repositories.get_bound_published_agent_profile",
        get_bound,
    )
    monkeypatch.setattr(
        "app.agent_apps.authority.repositories.get_current_published_agent_profile",
        get_current,
    )
    authority = AgentProfileAuthority()
    monkeypatch.setattr(authority, "_validate_definition", validate)

    admission = await authority.resolve_bound_for_worker_dispatch(
        object(),
        principal=_principal(),
        agent_id="agt_support",
        revision=7,
        content_hash=str(row["content_hash"]),
    )
    assert admission is not None

    row["instructions"] = "tampered"
    assert await authority.resolve_bound_for_worker_dispatch(
        object(),
        principal=_principal(),
        agent_id="agt_support",
        revision=7,
        content_hash=str(row["content_hash"]),
    ) is None


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
        "app.agent_apps.authority.repositories.get_bound_published_agent_profile",
        get_bound,
    )
    monkeypatch.setattr(
        "app.agent_apps.authority.repositories.get_current_published_agent_profile",
        get_current,
    )
    authority = AgentProfileAuthority()
    monkeypatch.setattr(authority, "_validate_definition", validate)

    admission = await authority.resolve_bound_for_worker_dispatch(
        object(),
        principal=_principal(department_id="药品注冊") if denial == "acl" else _principal(),
        agent_id="agt_support",
        revision=7,
        content_hash=expected_hash,
    )

    assert admission is None
    assert calls["bound"] == 1
    assert calls["validate"] == (1 if denial == "capability" else 0)


@pytest.mark.asyncio
async def test_chat_route_uses_immutable_session_pin_and_rejects_revision_override(monkeypatch):
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from app import repositories
    from app.agent_apps import AgentProfileAdmission, AgentProfileAuthority
    from app.execution.api import RunModelSelection
    from app.main import create_app
    from app.models import AgentConversationIdentity, ChatStreamRequest, SelectedAgentProfileRequest
    from app.routes.chat import chat_stream as route_chat_stream

    test_stream_request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                run_stream_runtime=SimpleNamespace(worker_capabilities=object())
            )
        )
    )

    async def chat_stream(*args, **kwargs):
        kwargs.setdefault("http_request", test_stream_request)
        return await route_chat_stream(*args, **kwargs)

    create_app()

    @asynccontextmanager
    async def transaction():
        yield object()

    bound_calls = []
    harness_calls = []
    noop = AsyncMock(return_value=None)

    async def owned_session(*_args, **_kwargs):
        return {
            "id": "session-profile",
            "workspace_id": "workspace-owned",
            "agent_id": "agt_support",
            "admitted_agent_profile_revision": 7,
            "admitted_agent_profile_hash": "a" * 64,
        }

    async def bound_profile(*_args, **kwargs):
        admission = AgentProfileAdmission(
            agent_id="agt_support",
            revision=7,
            content_hash="a" * 64,
            skill={"skill_id": "general-chat", "skill_version": "version-a"},
            mcp_tool_ids=(),
            private_execution_input={
                "agent_id": "agt_support",
                "revision": 7,
                "content_hash": "a" * 64,
                "instructions": "private",
                "skill_set": [
                    {"skill_id": "general-chat", "expected_version": "version-a"}
                ],
            },
            public_identity=AgentConversationIdentity(
                agent_id="agt_support",
                revision=7,
                name="Support assistant",
                description="Approved support help.",
            ),
        )
        AgentProfileAuthority.reject_profile_selector_conflicts(
            kwargs["submitted_request"],
            active=True,
            query_agent_id=kwargs["query_agent_id"],
            admission=admission,
        )
        bound_calls.append(kwargs)
        return admission

    async def claim_submission(*_args, **kwargs):
        return (
            {
                "request_fingerprint_sha256": kwargs["request_fingerprint_sha256"],
                "state": "queued",
                "outcome_json": {
                    "session_id": "session-profile",
                    "run_id": "run-profile",
                    "status": "queued",
                    "submission_id": kwargs["submission_id"],
                },
            },
            False,
        )

    async def harness_agent(*_args, **kwargs):
        harness_calls.append(kwargs)
        return {"id": "agt_support", "agent_type": "chat"}

    async def lock_profile_skills(*_args, **_kwargs):
        return (
            [
                {
                    "skill_id": "general-chat",
                    "version": "version-a",
                    "content_hash": "version-a",
                    "source": {"kind": "builtin", "asset_dir": "general-chat"},
                    "files": [
                        {
                            "relative_path": "SKILL.md",
                            "content_base64": "c2tpbGw=",
                            "size_bytes": 5,
                        }
                    ],
                    "dependency_ids": [],
                    "mcp_tool_ids": [],
                }
            ],
            "version-a",
            {
                "schema_version": "ai-platform.skill-release-decision.v1",
                "policy_active": False,
                "selected_version": "version-a",
                "selected_track": "manifest_pin",
            },
        )

    monkeypatch.setattr("app.routes.chat.transaction", transaction)
    monkeypatch.setattr(
        "app.execution.infrastructure.model_management.resolve_run_model",
        AsyncMock(
            return_value=RunModelSelection(
                model_id="model-a",
                model_value="model-a",
                connection_revision=None,
            )
        ),
    )
    monkeypatch.setattr(repositories, "get_chat_submission", AsyncMock(return_value=None))
    monkeypatch.setattr(repositories, "ensure_submission_principal", noop)
    monkeypatch.setattr(repositories, "get_authorized_session", owned_session)
    monkeypatch.setattr(repositories, "acquire_user_active_run_admission_lock", noop)
    monkeypatch.setattr(repositories, "get_latest_authorized_session_run_input", noop)
    monkeypatch.setattr(repositories, "get_agent", harness_agent)
    monkeypatch.setattr(repositories, "enforce_user_active_run_admission_under_lock", noop)
    monkeypatch.setattr(repositories, "ensure_workspace_belongs_to_tenant", noop)
    monkeypatch.setattr(
        repositories,
        "list_authorized_session_input_files",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(repositories, "authorize_files_for_run", noop)
    monkeypatch.setattr(repositories, "claim_chat_submission", claim_submission)
    monkeypatch.setattr("app.routes.chat.pin_agent_skill_set", lock_profile_skills)
    monkeypatch.setattr("app.routes.chat.resolve_bound_profile_for_submission", bound_profile)
    monkeypatch.setattr(
        "app.routes.chat.resolve_profile_for_admission",
        AsyncMock(side_effect=AssertionError("a continuation must not resolve the current publication")),
    )

    response = await chat_stream(
        ChatStreamRequest(
            message="continue on revision seven",
            session_id="session-profile",
            submission_id="7ea93033-30f5-40ea-8a33-2f3c6e7b21c4",
        ),
        principal=_principal(),
    )

    assert response.run_id == "run-profile"
    assert harness_calls == [{"tenant_id": "tenant-a", "agent_id": "agt_support"}]
    assert bound_calls[0]["principal"] == _principal()
    assert (bound_calls[0]["agent_id"], bound_calls[0]["revision"], bound_calls[0]["content_hash"]) == (
        "agt_support", 7, "a" * 64
    )

    with pytest.raises(HTTPException) as caught:
        await chat_stream(
            ChatStreamRequest(
                message="try to move this session",
                session_id="session-profile",
                submission_id="854b63f1-89f8-46cb-bc76-bc25891ba717",
                selected_agent_profile=SelectedAgentProfileRequest(
                    agent_id="agt_support",
                    expected_revision=9,
                ),
            ),
            principal=_principal(),
        )
    assert caught.value.status_code == 409
    assert caught.value.detail == {
        "code": "agent_profile_session_mismatch",
        "submission_disposition": "rejected_before_persist",
    }
    assert len(bound_calls) == 1

    with pytest.raises(HTTPException) as selector_error:
        await chat_stream(
            ChatStreamRequest(
                message="try to override the pinned Skill",
                session_id="session-profile",
                submission_id="e76e042e-744b-4612-9c8a-a7700f35904c",
                selected_skill={"skill_id": "other-skill", "expected_version": "version-b"},
            ),
            principal=_principal(),
        )
    assert selector_error.value.status_code == 400
    assert selector_error.value.detail == {
        "code": "agent_profile_selector_conflict",
        "submission_disposition": "rejected_before_persist",
    }
    assert len(bound_calls) == 1


@pytest.mark.asyncio
async def test_unpublish_records_an_immutable_withdrawn_revision_and_clears_admission(monkeypatch):
    from app.agent_apps import AgentProfileAuthority
    from app.agent_apps.authority import (
        _draft_from_row,
        _pre_avatar_seed_skill_set_revision_hash,
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
            row["avatar_seed"] = ""
        if row["avatar_seed"]:
            row["content_hash"] = _revision_hash(_draft_from_row(row))
        else:
            row["content_hash"] = _pre_avatar_seed_skill_set_revision_hash(
                _draft_from_row(row),
                legacy_supported_input_types=row["supported_input_types"],
                legacy_supported_file_types=row["legacy_supported_file_types"],
            )
        return row

    async def append_revision(*_args, **kwargs):
        observed["append"] = kwargs
        row = {
            **kwargs,
            "model_id": kwargs["legacy_model_id"],
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
        "app.agent_apps.authority.repositories.acquire_agent_profile_lifecycle_lock",
        lock_profile,
        raising=False,
    )
    monkeypatch.setattr("app.agent_apps.authority.repositories.ensure_submission_principal", ensure_user)
    monkeypatch.setattr("app.agent_apps.authority.repositories.get_agent_profile_aggregate", aggregate)
    monkeypatch.setattr("app.agent_apps.authority.repositories.get_agent_profile_revision", get_revision)
    monkeypatch.setattr("app.agent_apps.authority.repositories.create_agent_profile_revision", append_revision)
    monkeypatch.setattr("app.agent_apps.authority.repositories.record_agent_profile_withdrawal", record_withdrawal)
    monkeypatch.setattr("app.agent_apps.authority.repositories.append_audit_log", audit)

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
    assert observed["append"]["avatar_seed"] == ""
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


def test_profile_bound_continuation_rejects_client_execution_overrides():
    from app.agent_apps.authority import reject_profile_selector_conflicts
    from app.models import ChatStreamRequest, SelectedSkillRequest

    request = ChatStreamRequest(
        message="continue",
        selected_skill=SelectedSkillRequest(skill_id="general-chat", expected_version="version-a"),
    )

    with pytest.raises(HTTPException) as caught:
        reject_profile_selector_conflicts(request, active=True)
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
        profile_row["mcp_tool_ids"] = ["profile-tool"]
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
        "app.agent_apps.authority.repositories.get_current_published_agent_profile",
        get_current,
    )
    monkeypatch.setattr(
        "app.agent_apps.authority.repositories.get_bound_published_agent_profile",
        get_bound,
    )
    authority = AgentProfileAuthority()
    monkeypatch.setattr(authority, "_validate_definition", validate)
    request_payload = {
        "message": "continue with the published Agent",
        "agent_options": {
            "enable_thinking": "off",
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
        request_payload["selected_agent_profile"] = {
            "agent_id": "agt_support",
            "expected_revision": 7,
        }
        query_agent_id = "general-agent"
    request = ChatStreamRequest.model_validate(request_payload)

    if bound:
        admission = await authority.resolve_bound_for_submission(
            object(),
            principal=_principal(),
            agent_id="agt_support",
            revision=7,
            content_hash=str(profile_row["content_hash"]),
            submitted_request=request,
            query_agent_id=query_agent_id,
        )
    else:
        admission = await authority.resolve_for_admission(
            object(),
            principal=_principal(),
            selection=SelectedAgentProfileRequest(
                agent_id="agt_support",
                expected_revision=7,
            ),
            submitted_request=request,
            query_agent_id=query_agent_id,
        )

    assert admission.agent_id == "agt_support"
    assert admission.revision == 7
    assert observed == (
        [("bound", True), ("current", None)]
        if bound
        else [("current", True)]
    )


@pytest.mark.asyncio
async def test_profile_authority_rejects_nonempty_client_mcp_selector_even_when_configured(
    monkeypatch,
):
    from app.agent_apps import AgentProfileAuthority
    from app.models import ChatStreamRequest, SelectedAgentProfileRequest

    profile_row = _profile_row()
    profile_row["mcp_tool_ids"] = ["profile-tool"]
    _seal_profile_row(profile_row)

    async def get_current(*_args, **_kwargs):
        return profile_row

    async def validate(*_args, **_kwargs):
        return ({"skill_id": "general-chat", "skill_version": "version-a"},)

    monkeypatch.setattr(
        "app.agent_apps.authority.repositories.get_current_published_agent_profile",
        get_current,
    )
    authority = AgentProfileAuthority()
    monkeypatch.setattr(authority, "_validate_definition", validate)
    request = ChatStreamRequest.model_validate(
        {
            "message": "attempt to override the published expert",
            "selected_agent_profile": {
                "agent_id": "agt_support",
                "expected_revision": 7,
            },
            "selected_mcp_tool_ids": ["profile-tool"],
            "submission_id": "8eb026d4-2839-44db-83dd-5196ed80d9e8",
        }
    )

    with pytest.raises(HTTPException) as caught:
        await authority.resolve_for_admission(
            object(),
            principal=_principal(),
            selection=SelectedAgentProfileRequest(
                agent_id="agt_support",
                expected_revision=7,
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
    profile_row["skill_id"] = "skill-a"
    profile_row["skill_version"] = "version-a"
    profile_row["skill_set"] = [
        {"skill_id": "skill-a", "expected_version": "version-a"},
        {"skill_id": "skill-b", "expected_version": "version-b"},
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
        "app.agent_apps.authority.repositories.get_current_published_agent_profile",
        get_current,
    )
    authority = AgentProfileAuthority()
    monkeypatch.setattr(authority, "_validate_definition", validate)
    request = ChatStreamRequest.model_validate(
        {
            "message": "use the published expert",
            "selected_agent_profile": {
                "agent_id": "agt_support",
                "expected_revision": 7,
            },
            "selected_mcp_tool_ids": [],
            "submission_id": "7ea93033-30f5-40ea-8a33-2f3c6e7b21c4",
        }
    )

    admission = await authority.resolve_for_admission(
        object(),
        principal=_principal(),
        selection=SelectedAgentProfileRequest(
            agent_id="agt_support",
            expected_revision=7,
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
        ({"selected_mcp_tool_ids": ["other-tool"]}, None),
        ({"agent_options": {"temperature": 0.2}}, None),
        ({"agent_options": {"enable_thinking": "high"}}, None),
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
                "selectedAgentProfile": {
                    "agent_id": "agt_support",
                    "expected_revision": 7,
                }
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
        "nondefault-thinking-option",
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
        "app.agent_apps.authority.repositories.get_current_published_agent_profile",
        get_current,
    )
    monkeypatch.setattr(
        "app.agent_apps.authority.repositories.get_bound_published_agent_profile",
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
            await authority.resolve_bound_for_submission(
                object(),
                principal=_principal(),
                agent_id="agt_support",
                revision=7,
                content_hash=str(profile_row["content_hash"]),
                submitted_request=request,
                query_agent_id=query_agent_id,
            )
        else:
            await authority.resolve_for_admission(
                object(),
                principal=_principal(),
                selection=SelectedAgentProfileRequest(
                    agent_id="agt_support",
                    expected_revision=7,
                ),
                submitted_request=request,
                query_agent_id=query_agent_id,
            )

    assert (caught.value.status_code, caught.value.detail) == (400, "agent_profile_selector_conflict")
    assert storage_reads == (["bound", "current"] if bound else ["current"])


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
            "agent_profile_category": "support",
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
        "welcome_message": "",
        "starter_prompts": [],
        "capability_summary": "",
        "recommended_tasks": [],
        "supported_input_types": ["text", "file"],
        "expected_outputs": [],
        "permissions_and_data_access_notice": "",
        "published_at": None,
        "avatar_ref": "builtin:assistant",
        "avatar_seed": "agt_support",
        "category": "support",
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
        agent_profiles.repositories,
        "get_authorized_session_projection",
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
        ),
        http_request=http_request,
        principal=principal,
    )

    assert result is expected_response
    assert observed["http_request"] is http_request
    assert observed["agent_id"] == "agent-a"
    assert observed["principal"] is principal
    assert observed["request"].session_id == "session-a"
