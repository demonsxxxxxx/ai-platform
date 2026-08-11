from __future__ import annotations

from dataclasses import replace

import pytest

from app.attachments.file_capabilities import (
    AGENT_FILE_DECLARATION_EMPTY_POLICY,
    AgentFileAuthorizationResolution,
    CAPABILITY_AGENT_INPUT,
    CAPABILITY_ARTIFACT_PREVIEW,
    CAPABILITY_INPUT_PREVIEW,
    CAPABILITY_REJECTED,
    CAPABILITY_SUPPORTED,
    CAPABILITY_TYPED_PARSE,
    CAPABILITY_UPLOAD,
    FILE_CAPABILITY_AGENT_BINDING_INVALID,
    FILE_CAPABILITY_AGENT_BINDING_REQUIRED,
    FILE_CAPABILITY_AGENT_BINDING_UNAVAILABLE,
    FILE_CAPABILITY_AGENT_DECLARATION_EMPTY,
    FILE_CAPABILITY_AGENT_DECLARATION_INCONSISTENT,
    FILE_CAPABILITY_AGENT_DECLARATION_INVALID,
    FILE_CAPABILITY_AGENT_INPUT_UNSUPPORTED,
    FILE_CAPABILITY_AGENT_NOT_AUTHORIZED,
    FILE_CAPABILITY_AGENT_REVISION_STALE,
    FILE_CAPABILITY_AGENT_TYPE_NOT_DECLARED,
    FILE_CAPABILITY_ARTIFACT_PREVIEW_UNSUPPORTED,
    FILE_CAPABILITY_FILES_REQUIRED,
    FILE_CAPABILITY_IDENTITY_INVALID,
    FILE_CAPABILITY_INPUT_PREVIEW_UNSUPPORTED,
    FILE_CAPABILITY_OPERATION_INVALID,
    FILE_CAPABILITY_REGISTRY,
    FILE_CAPABILITY_REGISTRY_DIGEST,
    FILE_CAPABILITY_REGISTRY_VERSION,
    FILE_CAPABILITY_REJECTION_CODES,
    FILE_CAPABILITY_TYPED_PARSE_UNSUPPORTED,
    FILE_CAPABILITY_UPLOAD_UNSUPPORTED,
    FileCapabilityContractError,
    FileCapabilityDecision,
    ServerAuthorizedAgentFileBinding,
    VerifiedFileIdentity,
    authorize_agent_file_capabilities,
    check_file_capabilities,
    registry_digest,
)


XLSX = VerifiedFileIdentity(
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", ".xlsx"
)
PDF = VerifiedFileIdentity("application/pdf", ".pdf")
PPTX = VerifiedFileIdentity(
    "application/vnd.openxmlformats-officedocument.presentationml.presentation", ".pptx"
)
XLS = VerifiedFileIdentity("application/vnd.ms-excel", ".xls")
TSV = VerifiedFileIdentity("text/tab-separated-values", ".tsv")
PNG = VerifiedFileIdentity("image/png", ".png")
PROFILE_SHA256 = "a" * 64


class FakeAgentAuthorizationPort:
    def __init__(self, resolution: object) -> None:
        self.resolution = resolution
        self.calls: list[dict[str, object]] = []

    async def resolve_authorized_revision(
        self,
        *,
        agent_id: str,
        expected_revision: int,
        expected_profile_sha256: str,
    ) -> object:
        self.calls.append(
            {
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
    agent_id: str = "agent-reviewed",
    agent_revision: int = 7,
    profile_sha256: str = PROFILE_SHA256,
    supported_input_types: tuple[str, ...] = ("text", "file"),
    selected_skill_id: str = "qa-rag-skill",
    selected_skill_version: str = "version-reviewed",
) -> AgentFileAuthorizationResolution:
    return AgentFileAuthorizationResolution(
        status="authorized",
        agent_id=agent_id,
        agent_revision=agent_revision,
        profile_sha256=profile_sha256,
        supported_input_types=supported_input_types,
        supported_file_types=tuple(supported_file_types),
        selected_skill_id=selected_skill_id,
        selected_skill_version=selected_skill_version,
    )


@pytest.mark.parametrize(
    ("media_type", "extension"),
    [
        ("Application/PDF", ".pdf"),
        ("application/pdf; charset=binary", ".pdf"),
        (" application/pdf", ".pdf"),
        ("application/pdf", "pdf"),
        ("application/pdf", ".PDF"),
        ("application", ".pdf"),
    ],
)
def test_verified_identity_rejects_noncanonical_or_unverified_pairs(
    media_type, extension
):
    with pytest.raises(FileCapabilityContractError) as exc_info:
        VerifiedFileIdentity(media_type, extension)

    assert exc_info.value.code == FILE_CAPABILITY_IDENTITY_INVALID


def test_matrix_records_each_capability_axis_independently():
    for capability in (
        CAPABILITY_UPLOAD,
        CAPABILITY_TYPED_PARSE,
        CAPABILITY_INPUT_PREVIEW,
        CAPABILITY_ARTIFACT_PREVIEW,
    ):
        assert (
            check_file_capabilities((XLSX,), capability=capability).state
            == CAPABILITY_SUPPORTED
        )

    assert (
        check_file_capabilities((PDF,), capability=CAPABILITY_TYPED_PARSE).state
        == CAPABILITY_SUPPORTED
    )
    assert (
        check_file_capabilities((PDF,), capability=CAPABILITY_ARTIFACT_PREVIEW).state
        == CAPABILITY_SUPPORTED
    )
    assert (
        check_file_capabilities((PPTX,), capability=CAPABILITY_UPLOAD).state
        == CAPABILITY_SUPPORTED
    )
    assert (
        check_file_capabilities((PPTX,), capability=CAPABILITY_INPUT_PREVIEW).state
        == CAPABILITY_SUPPORTED
    )
    assert (
        check_file_capabilities((PPTX,), capability=CAPABILITY_ARTIFACT_PREVIEW).state
        == CAPABILITY_SUPPORTED
    )

    assert (
        check_file_capabilities(
            (PPTX,), capability=CAPABILITY_TYPED_PARSE
        ).rejection_code
        == FILE_CAPABILITY_TYPED_PARSE_UNSUPPORTED
    )
    assert (
        check_file_capabilities(
            (XLS,), capability=CAPABILITY_INPUT_PREVIEW
        ).rejection_code
        == FILE_CAPABILITY_INPUT_PREVIEW_UNSUPPORTED
    )
    assert (
        check_file_capabilities(
            (XLS,), capability=CAPABILITY_ARTIFACT_PREVIEW
        ).rejection_code
        == FILE_CAPABILITY_ARTIFACT_PREVIEW_UNSUPPORTED
    )
    assert (
        check_file_capabilities((TSV,), capability=CAPABILITY_UPLOAD).rejection_code
        == FILE_CAPABILITY_UPLOAD_UNSUPPORTED
    )


@pytest.mark.asyncio
async def test_agent_file_decision_requires_one_server_authorized_revision_binding():
    no_binding = check_file_capabilities((PDF,), capability=CAPABILITY_AGENT_INPUT)
    invalid_binding = await authorize_agent_file_capabilities(
        (PDF,),
        agent_id="agent-reviewed",
        expected_revision=7,
        expected_profile_sha256=PROFILE_SHA256,
        authorization_port=FakeAgentAuthorizationPort(object()),  # type: ignore[arg-type]
    )
    port = FakeAgentAuthorizationPort(
        _agent_resolution("application/pdf", "xlsx", "document.md")
    )

    accepted = await authorize_agent_file_capabilities(
        (
            PDF,
            XLSX,
            VerifiedFileIdentity("text/markdown", ".md"),
            VerifiedFileIdentity("text/markdown", ".markdown"),
        ),
        agent_id="agent-reviewed",
        expected_revision=7,
        expected_profile_sha256=PROFILE_SHA256,
        authorization_port=port,
    )
    xlsx_accepted = await authorize_agent_file_capabilities(
        (XLSX,),
        agent_id="agent-reviewed",
        expected_revision=7,
        expected_profile_sha256=PROFILE_SHA256,
        authorization_port=port,
    )

    assert no_binding.rejection_code == FILE_CAPABILITY_AGENT_BINDING_REQUIRED
    assert invalid_binding.rejection_code == FILE_CAPABILITY_AGENT_BINDING_INVALID
    assert accepted.state == CAPABILITY_SUPPORTED
    assert xlsx_accepted.state == CAPABILITY_SUPPORTED
    assert port.calls == [
        {
            "agent_id": "agent-reviewed",
            "expected_revision": 7,
            "expected_profile_sha256": PROFILE_SHA256,
        },
        {
            "agent_id": "agent-reviewed",
            "expected_revision": 7,
            "expected_profile_sha256": PROFILE_SHA256,
        },
    ]


@pytest.mark.asyncio
async def test_empty_agent_declaration_means_deny_all_without_wildcard_fallback():
    port = FakeAgentAuthorizationPort(
        _agent_resolution(
            agent_id="agent-text-only",
            agent_revision=3,
            supported_input_types=("text",),
        )
    )

    result = await authorize_agent_file_capabilities(
        (XLSX,),
        agent_id="agent-text-only",
        expected_revision=3,
        expected_profile_sha256=PROFILE_SHA256,
        authorization_port=port,
    )

    assert AGENT_FILE_DECLARATION_EMPTY_POLICY == "deny_all"
    assert result.state == CAPABILITY_REJECTED
    assert result.rejection_code == FILE_CAPABILITY_AGENT_DECLARATION_EMPTY
    assert result.fallback_prohibited is True


@pytest.mark.parametrize(
    ("declarations", "expected_code"),
    [
        (("application/x-unreviewed",), FILE_CAPABILITY_AGENT_DECLARATION_INVALID),
        ((" PDF ",), FILE_CAPABILITY_AGENT_DECLARATION_INVALID),
        (("pdf", "application/pdf"), FILE_CAPABILITY_AGENT_DECLARATION_INVALID),
        (
            (
                "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            ),
            FILE_CAPABILITY_AGENT_INPUT_UNSUPPORTED,
        ),
    ],
)
@pytest.mark.asyncio
async def test_server_binding_rejects_unreviewed_or_non_agent_declarations(
    declarations, expected_code
):
    result = await authorize_agent_file_capabilities(
        (PDF,),
        agent_id="agent-reviewed",
        expected_revision=7,
        expected_profile_sha256=PROFILE_SHA256,
        authorization_port=FakeAgentAuthorizationPort(_agent_resolution(*declarations)),
    )

    assert result.rejection_code == expected_code


@pytest.mark.asyncio
async def test_mixed_file_decision_is_atomic_for_declaration_and_operation_support():
    xlsx_only = FakeAgentAuthorizationPort(
        _agent_resolution("xlsx", agent_id="agent-xlsx", agent_revision=2)
    )
    both = FakeAgentAuthorizationPort(
        _agent_resolution(
            "xlsx",
            "pdf",
            agent_id="agent-docs",
            agent_revision=4,
            profile_sha256="b" * 64,
        )
    )

    undeclared = await authorize_agent_file_capabilities(
        (XLSX, PDF),
        agent_id="agent-xlsx",
        expected_revision=2,
        expected_profile_sha256=PROFILE_SHA256,
        authorization_port=xlsx_only,
    )
    accepted = await authorize_agent_file_capabilities(
        (XLSX, PDF),
        agent_id="agent-docs",
        expected_revision=4,
        expected_profile_sha256="b" * 64,
        authorization_port=both,
    )
    mixed_upload = check_file_capabilities((XLSX, XLS), capability=CAPABILITY_UPLOAD)
    mixed_with_unsupported = check_file_capabilities(
        (XLSX, TSV), capability=CAPABILITY_UPLOAD
    )
    mixed_typed_parse = check_file_capabilities(
        (XLSX, PPTX), capability=CAPABILITY_TYPED_PARSE
    )
    mixed_input_preview = check_file_capabilities(
        (XLSX, XLS), capability=CAPABILITY_INPUT_PREVIEW
    )
    mixed_artifact_preview = check_file_capabilities(
        (PDF, PNG), capability=CAPABILITY_ARTIFACT_PREVIEW
    )

    assert undeclared.rejection_code == FILE_CAPABILITY_AGENT_TYPE_NOT_DECLARED
    assert accepted.state == CAPABILITY_SUPPORTED
    assert mixed_upload.state == CAPABILITY_SUPPORTED
    assert mixed_with_unsupported.rejection_code == FILE_CAPABILITY_UPLOAD_UNSUPPORTED
    assert mixed_typed_parse.rejection_code == FILE_CAPABILITY_TYPED_PARSE_UNSUPPORTED
    assert (
        mixed_input_preview.rejection_code == FILE_CAPABILITY_INPUT_PREVIEW_UNSUPPORTED
    )
    assert (
        mixed_artifact_preview.rejection_code
        == FILE_CAPABILITY_ARTIFACT_PREVIEW_UNSUPPORTED
    )


@pytest.mark.asyncio
async def test_binding_and_operation_contract_fail_closed_with_stable_codes():
    with pytest.raises(TypeError, match="Protocols cannot be instantiated"):
        ServerAuthorizedAgentFileBinding()
    port = FakeAgentAuthorizationPort(_agent_resolution("pdf"))
    invalid_binding = await authorize_agent_file_capabilities(
        (PDF,),
        agent_id="agent-reviewed",
        expected_revision=7,
        expected_profile_sha256="not-a-sha256",
        authorization_port=port,
    )

    invalid_operation = check_file_capabilities(
        (PDF,),
        capability="execute",  # type: ignore[arg-type]
    )
    empty_files = check_file_capabilities((), capability=CAPABILITY_UPLOAD)

    assert invalid_binding.rejection_code == FILE_CAPABILITY_AGENT_BINDING_INVALID
    assert port.calls == []
    assert invalid_operation.rejection_code == FILE_CAPABILITY_OPERATION_INVALID
    assert invalid_operation.rejection_code in FILE_CAPABILITY_REJECTION_CODES
    assert invalid_operation.fallback_prohibited is True
    assert empty_files.rejection_code == FILE_CAPABILITY_FILES_REQUIRED


def test_public_decision_helpers_are_pinned_to_the_canonical_registry():
    import inspect

    assert "registry" not in inspect.signature(check_file_capabilities).parameters
    assert (
        "registry"
        not in inspect.signature(authorize_agent_file_capabilities).parameters
    )


def test_decision_dto_rejects_noncanonical_registry_metadata():
    with pytest.raises(ValueError, match="decision is invalid"):
        FileCapabilityDecision(
            CAPABILITY_SUPPORTED,
            CAPABILITY_UPLOAD,
            True,
            None,
            "caller-registry",
            FILE_CAPABILITY_REGISTRY_DIGEST,
        )
    with pytest.raises(ValueError, match="decision is invalid"):
        FileCapabilityDecision(
            CAPABILITY_SUPPORTED,
            CAPABILITY_UPLOAD,
            True,
            None,
            FILE_CAPABILITY_REGISTRY_VERSION,
            "0" * 64,
        )


@pytest.mark.asyncio
async def test_server_binding_rejects_file_types_on_a_text_only_agent_revision():
    resolution = _agent_resolution(
        "pdf",
        agent_id="agent-text-only",
        agent_revision=5,
        supported_input_types=("text",),
    )
    result = await authorize_agent_file_capabilities(
        (PDF,),
        agent_id="agent-text-only",
        expected_revision=5,
        expected_profile_sha256=PROFILE_SHA256,
        authorization_port=FakeAgentAuthorizationPort(resolution),
    )

    assert result.rejection_code == FILE_CAPABILITY_AGENT_DECLARATION_INCONSISTENT


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "expected_code"),
    [
        ("unavailable", FILE_CAPABILITY_AGENT_BINDING_UNAVAILABLE),
        ("unauthorized", FILE_CAPABILITY_AGENT_NOT_AUTHORIZED),
        ("stale", FILE_CAPABILITY_AGENT_REVISION_STALE),
    ],
)
async def test_agent_authority_port_statuses_are_stable_and_fail_closed(
    status, expected_code
):
    result = await authorize_agent_file_capabilities(
        (PDF,),
        agent_id="agent-reviewed",
        expected_revision=7,
        expected_profile_sha256=PROFILE_SHA256,
        authorization_port=FakeAgentAuthorizationPort(
            AgentFileAuthorizationResolution(status=status)
        ),
    )

    assert result.rejection_code == expected_code
    assert result.fallback_prohibited is True


@pytest.mark.asyncio
async def test_agent_authority_port_exception_is_stable_and_fail_closed():
    result = await authorize_agent_file_capabilities(
        (PDF,),
        agent_id="agent-reviewed",
        expected_revision=7,
        expected_profile_sha256=PROFILE_SHA256,
        authorization_port=FakeAgentAuthorizationPort(TimeoutError()),
    )

    assert result.rejection_code == FILE_CAPABILITY_AGENT_BINDING_UNAVAILABLE
    assert result.fallback_prohibited is True


@pytest.mark.asyncio
async def test_authorized_port_result_must_match_the_exact_requested_revision():
    result = await authorize_agent_file_capabilities(
        (PDF,),
        agent_id="agent-reviewed",
        expected_revision=7,
        expected_profile_sha256=PROFILE_SHA256,
        authorization_port=FakeAgentAuthorizationPort(
            _agent_resolution("pdf", profile_sha256="b" * 64)
        ),
    )

    assert result.rejection_code == FILE_CAPABILITY_AGENT_REVISION_STALE


def test_registry_version_digest_and_profile_identities_are_deterministic():
    identities = [
        identity
        for profile in FILE_CAPABILITY_REGISTRY
        for identity in profile.identities
    ]
    profile_by_id = {
        profile.profile_id: profile for profile in FILE_CAPABILITY_REGISTRY
    }
    changed_registry = (
        FILE_CAPABILITY_REGISTRY[0],
        replace(
            FILE_CAPABILITY_REGISTRY[1],
            capabilities=replace(
                FILE_CAPABILITY_REGISTRY[1].capabilities, upload=False
            ),
        ),
        *FILE_CAPABILITY_REGISTRY[2:],
    )

    assert FILE_CAPABILITY_REGISTRY_VERSION == "ai-platform.file-capability-registry.v3"
    assert registry_digest(FILE_CAPABILITY_REGISTRY) == FILE_CAPABILITY_REGISTRY_DIGEST
    assert registry_digest(changed_registry) != FILE_CAPABILITY_REGISTRY_DIGEST
    assert len(identities) == len(set(identities))
    assert profile_by_id["tabular.xlsx"].enabled is True
    assert profile_by_id["document.pdf"].capabilities.agent_input is True
    assert profile_by_id["presentation.pptx"].capabilities.typed_parse is False
    assert all(
        not profile.enabled
        for profile in FILE_CAPABILITY_REGISTRY
        if profile.profile_id != "tabular.xlsx"
    )


def test_preview_media_type_projections_match_current_route_contracts():
    from app.artifact_preview import ARTIFACT_PREVIEW_ALLOWED_CONTENT_TYPES
    from app.routes.files import INPUT_FILE_PREVIEW_CONTENT_TYPES

    input_preview_media_types = {
        identity.media_type
        for profile in FILE_CAPABILITY_REGISTRY
        if profile.capabilities.input_preview
        for identity in profile.identities
    }
    artifact_preview_media_types = {
        identity.media_type
        for profile in FILE_CAPABILITY_REGISTRY
        if profile.capabilities.artifact_preview
        for identity in profile.identities
    }

    assert input_preview_media_types == INPUT_FILE_PREVIEW_CONTENT_TYPES
    assert artifact_preview_media_types == ARTIFACT_PREVIEW_ALLOWED_CONTENT_TYPES
