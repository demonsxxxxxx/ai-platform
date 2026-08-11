from __future__ import annotations

from dataclasses import replace

import pytest

from app.attachments.file_capabilities import (
    AGENT_FILE_DECLARATION_EMPTY_POLICY,
    CAPABILITY_AGENT_INPUT,
    CAPABILITY_ARTIFACT_PREVIEW,
    CAPABILITY_INPUT_PREVIEW,
    CAPABILITY_REJECTED,
    CAPABILITY_SUPPORTED,
    CAPABILITY_TYPED_PARSE,
    CAPABILITY_UPLOAD,
    FILE_CAPABILITY_AGENT_BINDING_REQUIRED,
    FILE_CAPABILITY_AGENT_BINDING_UNAVAILABLE,
    FILE_CAPABILITY_AGENT_DECLARATION_EMPTY,
    FILE_CAPABILITY_AGENT_DECLARATION_INVALID,
    FILE_CAPABILITY_AGENT_NOT_AUTHORIZED,
    FILE_CAPABILITY_AGENT_REVISION_STALE,
    FILE_CAPABILITY_AGENT_SCOPE_MISMATCH,
    FILE_CAPABILITY_CONTRACT_SCOPE,
    FILE_CAPABILITY_ENFORCEMENT_STATE,
    FILE_CAPABILITY_FILES_REQUIRED,
    FILE_CAPABILITY_IDENTITY_INVALID,
    FILE_CAPABILITY_OPERATION_INVALID,
    FILE_CAPABILITY_REGISTRY,
    FILE_CAPABILITY_REGISTRY_POLICY_DIGEST,
    FILE_CAPABILITY_REGISTRY_VERSION,
    FILE_CAPABILITY_REJECTION_CODES,
    FILE_CAPABILITY_TYPE_UNSUPPORTED,
    AgentFileAuthorizationResolution,
    FileCapabilityContractError,
    FileCapabilityDecision,
    FileCapabilityProfile,
    ParserIdentity,
    VerifiedFileIdentity,
    authorization_scope_sha256,
    authorize_agent_file_capabilities,
    check_file_capabilities,
    registry_policy_digest,
)
from app.auth import AuthPrincipal
from app.file_parser_contracts import XLSX_CONTENT_TYPE


XLSX = VerifiedFileIdentity(XLSX_CONTENT_TYPE, ".xlsx")
PDF = VerifiedFileIdentity("application/pdf", ".pdf")
PROFILE_SHA256 = "a" * 64


def _principal(*, user_id: str = "user-reviewed") -> AuthPrincipal:
    return AuthPrincipal(
        user_id=user_id,
        display_name="Reviewed User",
        tenant_id="tenant-reviewed",
        department_id="department-reviewed",
        roles=["user"],
        permissions=["agent:use"],
        source="trusted-header",
        authority_source="trusted-gateway",
        authority_checked_at="2026-08-11T00:00:00+00:00",
    )


class FakeAgentAuthorizationPort:
    def __init__(self, resolution: object) -> None:
        self.resolution = resolution
        self.calls: list[dict[str, object]] = []

    async def resolve_authorized_revision(
        self,
        *,
        principal: AuthPrincipal,
        workspace_id: str,
        agent_id: str,
        expected_revision: int,
        expected_profile_sha256: str,
    ) -> object:
        self.calls.append(
            {
                "principal": principal,
                "workspace_id": workspace_id,
                "agent_id": agent_id,
                "expected_revision": expected_revision,
                "expected_profile_sha256": expected_profile_sha256,
            }
        )
        if isinstance(self.resolution, Exception):
            raise self.resolution
        return self.resolution


def _agent_resolution(
    *supported_file_types: str,
    principal: AuthPrincipal | None = None,
    workspace_id: str = "workspace-reviewed",
    agent_id: str = "agent-reviewed",
    agent_revision: int = 7,
    profile_sha256: str = PROFILE_SHA256,
    supported_input_types: tuple[str, ...] = ("text", "file"),
) -> AgentFileAuthorizationResolution:
    scoped_principal = principal or _principal()
    return AgentFileAuthorizationResolution(
        status="authorized",
        tenant_id=scoped_principal.tenant_id,
        principal_user_id=scoped_principal.user_id,
        workspace_id=workspace_id,
        authorization_scope_sha256=authorization_scope_sha256(
            scoped_principal, workspace_id
        ),
        agent_id=agent_id,
        agent_revision=agent_revision,
        profile_sha256=profile_sha256,
        supported_input_types=supported_input_types,
        supported_file_types=tuple(supported_file_types),
        selected_skill_id="qa-rag-skill",
        selected_skill_version="version-reviewed",
    )


@pytest.mark.parametrize(
    ("media_type", "extension"),
    [
        ("Application/PDF", ".pdf"),
        ("application/pdf; charset=binary", ".pdf"),
        (" application/pdf", ".pdf"),
        ("application/pdf", "pdf"),
        ("application/pdf", ".PDF"),
    ],
)
def test_verified_identity_rejects_noncanonical_pairs(media_type, extension):
    with pytest.raises(FileCapabilityContractError) as exc_info:
        VerifiedFileIdentity(media_type, extension)

    assert exc_info.value.code == FILE_CAPABILITY_IDENTITY_INVALID


def test_registry_is_truthfully_xlsx_only_and_explicitly_unwired():
    assert len(FILE_CAPABILITY_REGISTRY) == 1
    profile = FILE_CAPABILITY_REGISTRY[0]

    assert FILE_CAPABILITY_REGISTRY_VERSION.endswith("v4-xlsx-only")
    assert FILE_CAPABILITY_CONTRACT_SCOPE == "xlsx_only_contract"
    assert FILE_CAPABILITY_ENFORCEMENT_STATE == "not_production_wired"
    assert profile.profile_id == "tabular.xlsx"
    assert profile.identities == (XLSX,)
    assert profile.enabled is True
    assert profile.capabilities.upload is True
    assert profile.capabilities.typed_parse is True
    assert profile.capabilities.input_preview is True
    assert profile.capabilities.artifact_preview is True
    assert profile.capabilities.agent_input is True


def test_xlsx_policy_axes_are_independent_and_non_xlsx_is_unsupported():
    for capability in (
        CAPABILITY_UPLOAD,
        CAPABILITY_TYPED_PARSE,
        CAPABILITY_INPUT_PREVIEW,
        CAPABILITY_ARTIFACT_PREVIEW,
    ):
        decision = check_file_capabilities((XLSX,), capability=capability)
        assert decision.state == CAPABILITY_SUPPORTED
        assert decision.enforcement_state == "not_production_wired"

    assert (
        check_file_capabilities((PDF,), capability=CAPABILITY_UPLOAD).rejection_code
        == FILE_CAPABILITY_TYPE_UNSUPPORTED
    )
    assert (
        check_file_capabilities(
            (XLSX, PDF), capability=CAPABILITY_UPLOAD
        ).rejection_code
        == FILE_CAPABILITY_TYPE_UNSUPPORTED
    )


@pytest.mark.asyncio
async def test_agent_input_requires_acl_scoped_authority_and_exact_declaration():
    principal = _principal()
    no_binding = check_file_capabilities((XLSX,), capability=CAPABILITY_AGENT_INPUT)
    port = FakeAgentAuthorizationPort(_agent_resolution("xlsx", principal=principal))

    accepted = await authorize_agent_file_capabilities(
        (XLSX,),
        principal=principal,
        workspace_id="workspace-reviewed",
        agent_id="agent-reviewed",
        expected_revision=7,
        expected_profile_sha256=PROFILE_SHA256,
        authorization_port=port,
    )

    assert no_binding.rejection_code == FILE_CAPABILITY_AGENT_BINDING_REQUIRED
    assert accepted.state == CAPABILITY_SUPPORTED
    assert port.calls == [
        {
            "principal": principal,
            "workspace_id": "workspace-reviewed",
            "agent_id": "agent-reviewed",
            "expected_revision": 7,
            "expected_profile_sha256": PROFILE_SHA256,
        }
    ]


@pytest.mark.asyncio
async def test_empty_agent_declaration_is_deny_all_without_fallback():
    principal = _principal()
    result = await authorize_agent_file_capabilities(
        (XLSX,),
        principal=principal,
        workspace_id="workspace-reviewed",
        agent_id="agent-reviewed",
        expected_revision=7,
        expected_profile_sha256=PROFILE_SHA256,
        authorization_port=FakeAgentAuthorizationPort(
            _agent_resolution(
                principal=principal,
                supported_input_types=("text",),
            )
        ),
    )

    assert AGENT_FILE_DECLARATION_EMPTY_POLICY == "deny_all"
    assert result.state == CAPABILITY_REJECTED
    assert result.rejection_code == FILE_CAPABILITY_AGENT_DECLARATION_EMPTY
    assert result.fallback_prohibited is True


@pytest.mark.asyncio
async def test_non_xlsx_agent_declaration_and_scope_mismatch_fail_closed():
    principal = _principal()
    invalid_declaration = await authorize_agent_file_capabilities(
        (XLSX,),
        principal=principal,
        workspace_id="workspace-reviewed",
        agent_id="agent-reviewed",
        expected_revision=7,
        expected_profile_sha256=PROFILE_SHA256,
        authorization_port=FakeAgentAuthorizationPort(
            _agent_resolution("pdf", principal=principal)
        ),
    )
    wrong_scope = replace(
        _agent_resolution("xlsx", principal=principal),
        workspace_id="workspace-other",
    )
    scope_mismatch = await authorize_agent_file_capabilities(
        (XLSX,),
        principal=principal,
        workspace_id="workspace-reviewed",
        agent_id="agent-reviewed",
        expected_revision=7,
        expected_profile_sha256=PROFILE_SHA256,
        authorization_port=FakeAgentAuthorizationPort(wrong_scope),
    )

    assert (
        invalid_declaration.rejection_code
        == FILE_CAPABILITY_AGENT_DECLARATION_INVALID
    )
    assert scope_mismatch.rejection_code == FILE_CAPABILITY_AGENT_SCOPE_MISMATCH


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("resolution", "expected_code"),
    [
        (TimeoutError(), FILE_CAPABILITY_AGENT_BINDING_UNAVAILABLE),
        (
            AgentFileAuthorizationResolution(status="unavailable"),
            FILE_CAPABILITY_AGENT_BINDING_UNAVAILABLE,
        ),
        (
            AgentFileAuthorizationResolution(status="unauthorized"),
            FILE_CAPABILITY_AGENT_NOT_AUTHORIZED,
        ),
        (
            AgentFileAuthorizationResolution(status="stale"),
            FILE_CAPABILITY_AGENT_REVISION_STALE,
        ),
    ],
)
async def test_agent_authority_failures_have_stable_codes(resolution, expected_code):
    result = await authorize_agent_file_capabilities(
        (XLSX,),
        principal=_principal(),
        workspace_id="workspace-reviewed",
        agent_id="agent-reviewed",
        expected_revision=7,
        expected_profile_sha256=PROFILE_SHA256,
        authorization_port=FakeAgentAuthorizationPort(resolution),
    )

    assert result.rejection_code == expected_code
    assert result.fallback_prohibited is True


def test_policy_helpers_are_canonical_and_legacy_pairs_constructor_still_works():
    import inspect

    legacy = FileCapabilityProfile(
        "legacy.xlsx",
        "Legacy spreadsheet",
        ((XLSX_CONTENT_TYPE, ".xlsx"),),
        True,
        ParserIdentity("legacy.parser", "1", 1024, "Legacy parser"),
        "legacy-skill",
        (),
        (("analyze", ()),),
        True,
    )
    changed = (replace(FILE_CAPABILITY_REGISTRY[0], homogeneous=False),)

    assert legacy.pairs == ((XLSX_CONTENT_TYPE, ".xlsx"),)
    assert legacy.identities == (XLSX,)
    assert "registry" not in inspect.signature(check_file_capabilities).parameters
    assert "registry" not in inspect.signature(
        authorize_agent_file_capabilities
    ).parameters
    assert (
        registry_policy_digest(FILE_CAPABILITY_REGISTRY)
        == FILE_CAPABILITY_REGISTRY_POLICY_DIGEST
    )
    assert (
        registry_policy_digest(changed)
        != FILE_CAPABILITY_REGISTRY_POLICY_DIGEST
    )


def test_profile_rejects_a_non_parser_object_at_construction():
    with pytest.raises(ValueError, match="file capability profile is invalid"):
        replace(FILE_CAPABILITY_REGISTRY[0], parser=object())


def test_policy_decision_metadata_is_not_an_admission_integrity_claim():
    decision = check_file_capabilities((XLSX,), capability=CAPABILITY_UPLOAD)

    assert decision.registry_policy_digest == FILE_CAPABILITY_REGISTRY_POLICY_DIGEST
    assert decision.registry_digest == decision.registry_policy_digest
    assert decision.enforcement_state == FILE_CAPABILITY_ENFORCEMENT_STATE
    with pytest.raises(ValueError, match="decision is invalid"):
        FileCapabilityDecision(
            CAPABILITY_SUPPORTED,
            CAPABILITY_UPLOAD,
            True,
            None,
            FILE_CAPABILITY_REGISTRY_VERSION,
            "0" * 64,
        )


def test_invalid_operation_and_empty_batch_are_stable_fail_closed_decisions():
    invalid = check_file_capabilities(
        (XLSX,), capability="execute"  # type: ignore[arg-type]
    )
    empty = check_file_capabilities((), capability=CAPABILITY_UPLOAD)

    assert invalid.rejection_code == FILE_CAPABILITY_OPERATION_INVALID
    assert invalid.rejection_code in FILE_CAPABILITY_REJECTION_CODES
    assert empty.rejection_code == FILE_CAPABILITY_FILES_REQUIRED
