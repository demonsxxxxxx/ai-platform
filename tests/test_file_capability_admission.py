from __future__ import annotations

import hashlib

import pytest

from app.skills.file_capability_admission import (
    ADMISSION_REJECTED,
    ADMISSION_REQUIRED,
    DANGEROUS_ATTACHMENT_PAIRS,
    FILE_CAPABILITY_REGISTRY,
    FILE_CAPABILITY_REGISTRY_DIGEST,
    FILE_CAPABILITY_REGISTRY_VERSION,
    AgentSkillBinding,
    AuthorizedSkillPin,
    ExecutionCapabilityInventory,
    FileCapabilityAdmissionRequest,
    SkillAuthorizationResolution,
    SkillSelection,
    TrustedAttachmentFact,
    admit_file_capability,
    registry_digest,
)


XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
XLSX_SHA256 = "a" * 64


class FakeAuthorizationPort:
    def __init__(self, resolution: SkillAuthorizationResolution) -> None:
        self.resolution = resolution
        self.calls: list[dict[str, object]] = []

    async def resolve_exactly_one(self, *, logical_skill_ids, selection, binding):
        self.calls.append(
            {
                "logical_skill_ids": logical_skill_ids,
                "selection": selection,
                "binding": binding,
            }
        )
        return self.resolution


def _pin(*, skill_id: str = "qa-rag-skill", version: str = "v2026-07-29") -> AuthorizedSkillPin:
    return AuthorizedSkillPin(
        logical_skill_id="qa-rag-skill",
        skill_id=skill_id,
        expected_version=version,
        manifest_sha256=hashlib.sha256(f"{skill_id}:{version}".encode()).hexdigest(),
        public_label="QA spreadsheet analysis",
    )


def _port(status: str = "authorized", pins: tuple[AuthorizedSkillPin, ...] | None = None):
    return FakeAuthorizationPort(
        SkillAuthorizationResolution(
            status=status,
            pins=(_pin(),) if pins is None and status == "authorized" else pins or (),
        )
    )


def _xlsx(*, media_type: str = XLSX_MIME, extension: str = ".xlsx", file_id: str = "file-xlsx"):
    return TrustedAttachmentFact(
        file_id=file_id,
        verified_media_type=media_type,
        verified_extension=extension,
        size_bytes=123,
        sha256=XLSX_SHA256,
        classifier_version="upload-classifier-v1",
    )


def _request(
    *attachments: TrustedAttachmentFact,
    task_intent: str = "analyze",
    explicit_selection: SkillSelection | None = None,
    agent_binding: AgentSkillBinding | None = None,
    runtime_dependencies: frozenset[str] = frozenset({"python.openpyxl>=3.1"}),
    artifact_types: frozenset[str] = frozenset({"xlsx"}),
):
    return FileCapabilityAdmissionRequest(
        attachments=attachments,
        task_intent=task_intent,
        explicit_selection=explicit_selection,
        agent_binding=agent_binding,
        runtime_inventory=ExecutionCapabilityInventory(
            dependency_ids=runtime_dependencies,
            artifact_types=artifact_types,
        ),
    )


@pytest.mark.asyncio
async def test_red_xlsx_request_is_server_bound_to_one_pinned_authorized_skill():
    port = _port()

    result = await admit_file_capability(_request(_xlsx()), authorization_port=port)

    assert result.state == ADMISSION_REQUIRED
    assert result.fallback_prohibited is True
    assert result.rejection_code is None
    assert result.selected_skill == SkillSelection("qa-rag-skill", "v2026-07-29")
    assert result.parser_requirements[0].parser_id == "ai-platform.xlsx.openpyxl"
    assert result.parser_requirements[0].parser_version == "1"
    assert result.skill_pins == (_pin(),)
    assert port.calls[0]["logical_skill_ids"] == ("qa-rag-skill",)
    public_payloads = [fact.to_public_payload() for fact in result.public_progress_facts]
    assert public_payloads == [
        {
            "kind": "file_category",
            "stage": "admission",
            "status": "completed",
            "label": "Spreadsheet files",
            "progress": {"current": 1, "total": 1},
        },
        {
            "kind": "parser",
            "stage": "admission",
            "status": "completed",
            "label": "Spreadsheet analysis",
            "progress": {"current": 1, "total": 1},
        },
        {
            "kind": "skill",
            "stage": "admission",
            "status": "completed",
            "label": "QA spreadsheet analysis",
            "progress": {"current": 1, "total": 1},
        },
    ]
    assert "file-xlsx" not in repr(public_payloads)
    assert XLSX_SHA256 not in repr(public_payloads)
    assert "qa-rag-skill" not in repr(public_payloads)


@pytest.mark.asyncio
async def test_exact_verified_mime_extension_mismatch_rejects_without_authorization_call():
    port = _port()

    result = await admit_file_capability(
        _request(_xlsx(media_type="text/plain")), authorization_port=port
    )

    assert result.state == ADMISSION_REJECTED
    assert result.rejection_code == "file_capability_type_mismatch"
    assert result.fallback_prohibited is True
    assert port.calls == []


@pytest.mark.asyncio
async def test_untrusted_attachment_object_rejects_without_authorization_call():
    port = _port()
    request = _request()
    object.__setattr__(request, "attachments", ("caller-declared-metadata",))

    result = await admit_file_capability(request, authorization_port=port)

    assert result.rejection_code == "file_capability_metadata_untrusted"
    assert result.fallback_prohibited is True
    assert port.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("attachment", "expected_code"),
    [
        (
            TrustedAttachmentFact(
                file_id="file-dangerous",
                verified_media_type="application/x-msdownload",
                verified_extension=".exe",
                size_bytes=1,
                sha256=XLSX_SHA256,
                classifier_version="upload-classifier-v1",
            ),
            "file_capability_type_dangerous",
        ),
        (
            TrustedAttachmentFact(
                file_id="file-unknown",
                verified_media_type="application/octet-stream",
                verified_extension=".bin",
                size_bytes=1,
                sha256=XLSX_SHA256,
                classifier_version="upload-classifier-v1",
            ),
            "file_capability_type_unsupported",
        ),
        (
            TrustedAttachmentFact(
                file_id="file-csv",
                verified_media_type="text/csv",
                verified_extension=".csv",
                size_bytes=1,
                sha256=XLSX_SHA256,
                classifier_version="upload-classifier-v1",
            ),
            "file_capability_type_unsupported",
        ),
    ],
)
async def test_dangerous_unknown_and_disabled_profiles_fail_closed(attachment, expected_code):
    result = await admit_file_capability(_request(attachment), authorization_port=_port())

    assert result.state == ADMISSION_REJECTED
    assert result.rejection_code == expected_code
    assert result.fallback_prohibited is True


@pytest.mark.asyncio
async def test_mixed_file_profiles_and_ambiguous_intent_fail_closed():
    port = _port()
    mixed = await admit_file_capability(
        _request(
            _xlsx(),
            TrustedAttachmentFact(
                file_id="file-docx",
                verified_media_type=(
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                ),
                verified_extension=".docx",
                size_bytes=1,
                sha256=XLSX_SHA256,
                classifier_version="upload-classifier-v1",
            ),
        ),
        authorization_port=port,
    )
    ambiguous = await admit_file_capability(
        _request(_xlsx(), task_intent="unspecified"), authorization_port=port
    )

    assert mixed.rejection_code == "file_capability_combination_unsupported"
    assert ambiguous.rejection_code == "file_capability_intent_ambiguous"
    assert port.calls == []


@pytest.mark.asyncio
async def test_explicit_valid_selection_precedes_server_choice_and_incompatible_selection_rejects():
    valid = SkillSelection("qa-rag-skill", "caller-version")
    port = _port(pins=(_pin(version="caller-version"),))
    result = await admit_file_capability(
        _request(_xlsx(), explicit_selection=valid), authorization_port=port
    )
    incompatible = await admit_file_capability(
        _request(_xlsx(), explicit_selection=SkillSelection("other-skill", "v1")),
        authorization_port=_port(),
    )

    assert port.calls[0]["selection"] == valid
    assert result.state == ADMISSION_REQUIRED
    assert incompatible.rejection_code == "file_capability_caller_selection_incompatible"


@pytest.mark.asyncio
async def test_agent_binding_is_immutable_and_cannot_be_broadened_by_caller_selection():
    agent_binding = AgentSkillBinding(
        agent_id="agent-immutable",
        selected_skill=SkillSelection("qa-rag-skill", "agent-version"),
    )
    result = await admit_file_capability(
        _request(
            _xlsx(),
            explicit_selection=SkillSelection("qa-rag-skill", "caller-version"),
            agent_binding=agent_binding,
        ),
        authorization_port=_port(),
    )

    assert result.rejection_code == "file_capability_agent_profile_incompatible"
    assert result.fallback_prohibited is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "expected_code"),
    [
        ("unavailable", "file_capability_skill_unavailable"),
        ("unauthorized", "file_capability_not_authorized"),
        ("stale", "file_capability_version_stale"),
    ],
)
async def test_skill_authorization_failures_map_to_stable_rejections(status, expected_code):
    result = await admit_file_capability(_request(_xlsx()), authorization_port=_port(status))

    assert result.state == ADMISSION_REJECTED
    assert result.rejection_code == expected_code
    assert result.fallback_prohibited is True


@pytest.mark.asyncio
async def test_ambiguous_authorization_resolution_fails_closed():
    result = await admit_file_capability(
        _request(_xlsx()),
        authorization_port=_port(pins=(_pin(), _pin(skill_id="qa-rag-skill-2"))),
    )

    assert result.rejection_code == "file_capability_skill_ambiguous"


@pytest.mark.asyncio
async def test_dependency_and_artifact_contract_gaps_reject_before_skill_resolution():
    dependency_gap = await admit_file_capability(
        _request(_xlsx(), runtime_dependencies=frozenset()), authorization_port=_port()
    )
    artifact_gap = await admit_file_capability(
        _request(
            _xlsx(),
            task_intent="generate_artifact",
            artifact_types=frozenset(),
        ),
        authorization_port=_port(),
    )

    assert dependency_gap.rejection_code == "file_capability_runtime_dependency_unavailable"
    assert artifact_gap.rejection_code == "file_capability_required_artifact_incompatible"


@pytest.mark.asyncio
async def test_not_applicable_is_the_only_state_that_allows_generic_fallback():
    result = await admit_file_capability(_request(), authorization_port=_port())

    assert result.state == "not_applicable"
    assert result.fallback_prohibited is False


def test_registry_is_reviewed_deterministic_and_represents_declared_file_families():
    profile_ids = {profile.profile_id for profile in FILE_CAPABILITY_REGISTRY}

    assert FILE_CAPABILITY_REGISTRY_VERSION == "ai-platform.file-capability-registry.v1"
    assert registry_digest(FILE_CAPABILITY_REGISTRY) == FILE_CAPABILITY_REGISTRY_DIGEST
    assert len(FILE_CAPABILITY_REGISTRY_DIGEST) == 64
    assert "tabular.xlsx" in profile_ids
    assert {
        "tabular.xls",
        "tabular.csv",
        "tabular.tsv",
        "document.pdf",
        "document.docx",
        "document.txt",
        "document.md",
        "document.html",
        "presentation.pptx",
        "image.png",
        "image.jpeg",
        "image.tiff",
        "structured.json",
        "structured.xml",
        "archive.reviewed",
        "media.audio",
        "media.video",
    } <= profile_ids
    assert DANGEROUS_ATTACHMENT_PAIRS


def test_authorization_pin_rejects_an_internal_skill_id_as_a_public_label():
    with pytest.raises(ValueError, match="public_label is not safe"):
        AuthorizedSkillPin(
            logical_skill_id="qa-rag-skill",
            skill_id="qa-rag-skill",
            expected_version="v1",
            manifest_sha256=XLSX_SHA256,
            public_label="qa-rag-skill",
        )
