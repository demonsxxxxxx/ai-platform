from __future__ import annotations

import hashlib
import io
from dataclasses import replace

import pytest
from openpyxl import Workbook

import app.attachments.capability_admission as capability_admission
from app.attachments.capability_admission import (
    ADMISSION_REJECTED,
    ADMISSION_REQUIRED,
    FILE_CAPABILITY_REGISTRY,
    FILE_CAPABILITY_REGISTRY_DIGEST,
    FILE_CAPABILITY_REGISTRY_VERSION,
    AgentSkillBinding,
    AuthorizedSkillPin,
    FileCapabilityAdmission,
    FileCapabilityAdmissionRequest,
    RuntimeDependencyIdentity,
    RuntimeDependencyRequirement,
    RuntimeImageInventory,
    SkillAuthorizationResolution,
    SkillSelection,
    WorkspaceSkillPin,
    admit_file_capability,
    registry_digest,
)
from app.attachments.classification import (
    AttachmentBytesForClassification,
    classify_attachment_bytes,
)
from app.attachments.file_capabilities import AgentFileAuthorizationResolution


XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
IMAGE_DIGEST = "sha256:" + "b" * 64
PROFILE_SHA256 = "c" * 64


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


class RaisingAuthorizationPort:
    async def resolve_exactly_one(self, *, logical_skill_ids, selection, binding):
        raise TimeoutError("authority unavailable")


class FakeAgentAuthorizationPort:
    def __init__(self, resolution: AgentFileAuthorizationResolution) -> None:
        self.resolution = resolution
        self.calls: list[dict[str, object]] = []

    async def resolve_authorized_revision(
        self,
        *,
        agent_id: str,
        expected_revision: int,
        expected_profile_sha256: str,
    ) -> AgentFileAuthorizationResolution:
        self.calls.append(
            {
                "agent_id": agent_id,
                "expected_revision": expected_revision,
                "expected_profile_sha256": expected_profile_sha256,
            }
        )
        return self.resolution


def _xlsx_bytes() -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Data"
    sheet["A1"] = "name"
    output = io.BytesIO()
    workbook.save(output)
    workbook.close()
    return output.getvalue()


def _attachment(file_id: str = "file-xlsx") -> AttachmentBytesForClassification:
    raw = _xlsx_bytes()
    return AttachmentBytesForClassification(
        file_id=file_id,
        raw_bytes=raw,
        source_filename="report.xlsx",
        declared_media_type="text/plain",
        expected_size_bytes=len(raw),
        expected_sha256=hashlib.sha256(raw).hexdigest(),
    )


def _pin(
    *,
    skill_id: str = "qa-rag-skill",
    version: str = "v2026-07-29",
    content_hash: str | None = None,
) -> AuthorizedSkillPin:
    return AuthorizedSkillPin(
        logical_skill_id="qa-rag-skill",
        skill_id=skill_id,
        expected_version=version,
        manifest_sha256=content_hash
        or hashlib.sha256(f"{skill_id}:{version}".encode()).hexdigest(),
        public_label="QA spreadsheet analysis",
    )


def _port(
    status: str = "authorized", pins: tuple[AuthorizedSkillPin, ...] | None = None
):
    return FakeAuthorizationPort(
        SkillAuthorizationResolution(
            status=status,
            pins=(_pin(),) if pins is None and status == "authorized" else pins or (),
        )
    )


def _agent_port(
    *,
    selected_skill_version: str = "agent-version",
    supported_input_types: tuple[str, ...] = ("text", "file"),
    supported_file_types: tuple[str, ...] = ("xlsx",),
) -> FakeAgentAuthorizationPort:
    return FakeAgentAuthorizationPort(
        AgentFileAuthorizationResolution(
            status="authorized",
            agent_id="agent-immutable",
            agent_revision=4,
            profile_sha256=PROFILE_SHA256,
            supported_input_types=supported_input_types,
            supported_file_types=supported_file_types,
            selected_skill_id="qa-rag-skill",
            selected_skill_version=selected_skill_version,
        )
    )


def _workspace_pin(pin: AuthorizedSkillPin | None = None) -> WorkspaceSkillPin:
    selected = pin or _pin()
    return WorkspaceSkillPin(
        selected.skill_id, selected.expected_version, selected.manifest_sha256
    )


def _inventory(
    *,
    dependencies: tuple[RuntimeDependencyIdentity, ...] | None = None,
    workspace_skills: tuple[WorkspaceSkillPin, ...] | None = None,
    artifact_types: frozenset[str] = frozenset({"xlsx"}),
) -> RuntimeImageInventory:
    return RuntimeImageInventory(
        image_digest=IMAGE_DIGEST,
        python_version="3.11.9",
        runs_as_non_root=True,
        dependencies=dependencies
        if dependencies is not None
        else (
            RuntimeDependencyIdentity("prebuilt_python", "openpyxl", "3.1.5"),
            RuntimeDependencyIdentity("prebuilt_python", "matplotlib", "3.8.4"),
            RuntimeDependencyIdentity("prebuilt_python", "python-docx", "1.2.0"),
        ),
        workspace_skills=workspace_skills
        if workspace_skills is not None
        else (_workspace_pin(),),
        artifact_types=artifact_types,
        node_version=None,
        npm_source_install_allowed=False,
        public_package_registry_egress=False,
    )


def _request(
    *attachments: AttachmentBytesForClassification,
    task_intent: str = "analyze",
    explicit_selection: SkillSelection | None = None,
    agent_binding: AgentSkillBinding | None = None,
    runtime_inventory: RuntimeImageInventory | None = None,
) -> FileCapabilityAdmissionRequest:
    return FileCapabilityAdmissionRequest(
        attachments=attachments,
        task_intent=task_intent,
        explicit_selection=explicit_selection,
        agent_binding=agent_binding,
        runtime_inventory=runtime_inventory or _inventory(),
    )


@pytest.mark.asyncio
async def test_red_one_entry_classifies_bytes_then_binds_xlsx_to_one_pinned_skill():
    port = _port()

    result = await admit_file_capability(
        _request(_attachment()), authorization_port=port
    )

    assert result.state == ADMISSION_REQUIRED
    assert result.fallback_prohibited is True
    assert result.selected_skill == SkillSelection("qa-rag-skill", "v2026-07-29")
    assert result.parser_requirements[0].parser_id == "ai-platform.xlsx.openpyxl"
    assert result.parser_requirements[0].verified_media_type == XLSX_MIME
    assert result.runtime_image_digest == IMAGE_DIGEST
    assert result.workspace_skill_pin == _workspace_pin()
    assert [fact.to_public_payload() for fact in result.public_progress_facts] == [
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
    public_payload = repr(
        [fact.to_public_payload() for fact in result.public_progress_facts]
    )
    assert "file-xlsx" not in public_payload
    assert "qa-rag-skill" not in public_payload


def test_request_rejects_a_nominal_identity_before_any_admission_can_consume_it():
    public_decision = classify_attachment_bytes(_attachment())

    with pytest.raises(ValueError, match="AttachmentBytesForClassification"):
        _request(public_decision)


def test_agent_binding_cannot_self_attest_a_selected_skill():
    with pytest.raises(TypeError):
        AgentSkillBinding(  # type: ignore[call-arg]
            "agent-immutable",
            SkillSelection("qa-rag-skill", "agent-version"),
        )


def test_admission_dto_rejects_noncanonical_metadata_and_rejection_codes():
    with pytest.raises(ValueError, match="fallback invariant"):
        FileCapabilityAdmission(
            ADMISSION_REJECTED,
            True,
            "caller_supplied_rejection",
            FILE_CAPABILITY_REGISTRY_VERSION,
            FILE_CAPABILITY_REGISTRY_DIGEST,
        )
    with pytest.raises(ValueError, match="fallback invariant"):
        FileCapabilityAdmission(
            ADMISSION_REJECTED,
            True,
            "file_capability_type_unsupported",
            "caller-registry",
            FILE_CAPABILITY_REGISTRY_DIGEST,
        )


@pytest.mark.asyncio
async def test_classification_rejection_is_terminal_and_never_calls_skill_authorization():
    bad = _attachment()
    bad = AttachmentBytesForClassification(
        file_id=bad.file_id,
        raw_bytes=b"not an OOXML workbook",
        source_filename="spoofed.xlsx",
        declared_media_type=XLSX_MIME,
        expected_size_bytes=len(b"not an OOXML workbook"),
        expected_sha256=hashlib.sha256(b"not an OOXML workbook").hexdigest(),
    )
    port = _port()

    result = await admit_file_capability(_request(bad), authorization_port=port)

    assert result.state == ADMISSION_REJECTED
    assert result.rejection_code == "attachment_classification_type_unsupported"
    assert result.fallback_prohibited is True
    assert port.calls == []


@pytest.mark.asyncio
async def test_disabled_registry_profile_is_terminal_when_a_classifier_reaches_it(
    monkeypatch,
):
    disabled_xlsx = replace(FILE_CAPABILITY_REGISTRY[0], enabled=False)
    monkeypatch.setattr(
        capability_admission,
        "FILE_CAPABILITY_REGISTRY",
        (disabled_xlsx, *FILE_CAPABILITY_REGISTRY[1:]),
    )

    result = await admit_file_capability(
        _request(_attachment()), authorization_port=_port()
    )

    assert result.rejection_code == "file_capability_type_unsupported"
    assert result.fallback_prohibited is True


@pytest.mark.asyncio
async def test_explicit_selection_precedes_server_choice_and_agent_binding_cannot_broaden_it():
    valid = SkillSelection("qa-rag-skill", "caller-version")
    accepted = await admit_file_capability(
        _request(
            _attachment(),
            explicit_selection=valid,
            runtime_inventory=_inventory(
                workspace_skills=(_workspace_pin(_pin(version="caller-version")),)
            ),
        ),
        authorization_port=_port(pins=(_pin(version="caller-version"),)),
    )
    incompatible = await admit_file_capability(
        _request(_attachment(), explicit_selection=SkillSelection("other-skill", "v1")),
        authorization_port=_port(),
    )
    binding = AgentSkillBinding("agent-immutable", 4, PROFILE_SHA256)
    agent_rejected = await admit_file_capability(
        _request(_attachment(), explicit_selection=valid, agent_binding=binding),
        authorization_port=_port(),
        agent_authorization_port=_agent_port(),
    )

    assert accepted.state == ADMISSION_REQUIRED
    assert (
        incompatible.rejection_code == "file_capability_caller_selection_incompatible"
    )
    assert agent_rejected.rejection_code == "file_capability_agent_profile_incompatible"


@pytest.mark.asyncio
async def test_agent_binding_is_resolved_by_exact_revision_before_skill_admission():
    selected = _pin(version="agent-version")
    skill_port = _port(pins=(selected,))
    agent_port = _agent_port()

    result = await admit_file_capability(
        _request(
            _attachment(),
            agent_binding=AgentSkillBinding("agent-immutable", 4, PROFILE_SHA256),
            runtime_inventory=_inventory(workspace_skills=(_workspace_pin(selected),)),
        ),
        authorization_port=skill_port,
        agent_authorization_port=agent_port,
    )

    assert result.state == ADMISSION_REQUIRED
    assert result.selected_skill == SkillSelection("qa-rag-skill", "agent-version")
    assert agent_port.calls == [
        {
            "agent_id": "agent-immutable",
            "expected_revision": 4,
            "expected_profile_sha256": PROFILE_SHA256,
        }
    ]
    assert (
        skill_port.calls[0]["binding"].authority == "server_authorized_agent_revision"
    )


@pytest.mark.asyncio
async def test_agent_binding_without_authority_or_with_empty_declaration_fails_closed():
    request = _request(
        _attachment(),
        agent_binding=AgentSkillBinding("agent-immutable", 4, PROFILE_SHA256),
    )
    skill_port = _port()

    missing_port = await admit_file_capability(
        request,
        authorization_port=skill_port,
    )
    empty_declaration = await admit_file_capability(
        request,
        authorization_port=skill_port,
        agent_authorization_port=_agent_port(
            supported_input_types=("text",),
            supported_file_types=(),
        ),
    )

    assert missing_port.rejection_code == "file_capability_agent_binding_required"
    assert empty_declaration.rejection_code == "file_capability_agent_declaration_empty"
    assert skill_port.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "expected_code"),
    [
        ("unavailable", "file_capability_skill_unavailable"),
        ("unauthorized", "file_capability_not_authorized"),
        ("stale", "file_capability_version_stale"),
    ],
)
async def test_skill_resolution_failures_are_bounded(status, expected_code):
    result = await admit_file_capability(
        _request(_attachment()), authorization_port=_port(status)
    )

    assert result.rejection_code == expected_code
    assert result.fallback_prohibited is True


@pytest.mark.asyncio
async def test_skill_authority_exception_is_terminal_and_fail_closed():
    result = await admit_file_capability(
        _request(_attachment()), authorization_port=RaisingAuthorizationPort()
    )

    assert result.rejection_code == "file_capability_skill_unavailable"
    assert result.fallback_prohibited is True


@pytest.mark.asyncio
async def test_ambiguous_resolution_homogeneous_files_and_artifact_contract():
    ambiguous = await admit_file_capability(
        _request(_attachment()),
        authorization_port=_port(pins=(_pin(), _pin(skill_id="qa-rag-skill-2"))),
    )
    homogeneous = await admit_file_capability(
        _request(_attachment("file-a"), _attachment("file-b")),
        authorization_port=_port(),
    )
    artifact_gap = await admit_file_capability(
        _request(
            _attachment(),
            task_intent="generate_artifact",
            runtime_inventory=_inventory(artifact_types=frozenset()),
        ),
        authorization_port=_port(),
    )

    assert ambiguous.rejection_code == "file_capability_skill_ambiguous"
    assert homogeneous.state == ADMISSION_REQUIRED
    assert len(homogeneous.parser_requirements) == 2
    assert (
        artifact_gap.rejection_code == "file_capability_required_artifact_incompatible"
    )


@pytest.mark.asyncio
async def test_workspace_skill_must_match_the_selected_id_version_and_content_hash():
    selected = _pin(version="v2026-07-30", content_hash="a" * 64)
    runtime_old = WorkspaceSkillPin("qa-rag-skill", "v2026-07-29", "b" * 64)
    result = await admit_file_capability(
        _request(
            _attachment(),
            explicit_selection=SkillSelection(
                selected.skill_id, selected.expected_version
            ),
            runtime_inventory=_inventory(workspace_skills=(runtime_old,)),
        ),
        authorization_port=_port(pins=(selected,)),
    )

    assert result.rejection_code == "file_capability_workspace_skill_mismatch"
    assert result.fallback_prohibited is True


def test_runtime_inventory_uses_exact_prebuilt_image_facts_and_never_installs_node_dependencies():
    inventory = _inventory()
    node = RuntimeDependencyRequirement("node_npm", "sheetjs", "0.20")
    python = RuntimeDependencyRequirement(
        "python_runtime", "python", "3.11", require_non_root=True
    )

    assert inventory.missing_requirements((node,)) == (node,)
    assert inventory.missing_requirements((python,)) == ()
    assert inventory.node_version is None
    assert inventory.npm_source_install_allowed is False
    assert inventory.public_package_registry_egress is False
    no_openpyxl = _inventory(dependencies=())
    assert no_openpyxl.missing_requirements(
        (RuntimeDependencyRequirement("prebuilt_python", "openpyxl", "3.1"),)
    )
    with pytest.raises(ValueError, match="dependency identity"):
        RuntimeDependencyIdentity("prebuilt_python", "openpyxl", 1)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="runtime image inventory"):
        replace(inventory, node_version=20)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_not_applicable_is_only_for_no_typed_files_and_rejections_prohibit_fallback():
    not_applicable = await admit_file_capability(_request(), authorization_port=_port())
    unsupported = AttachmentBytesForClassification(
        file_id="file-unknown",
        raw_bytes=b"unknown",
        source_filename="unknown.bin",
        declared_media_type="application/octet-stream",
        expected_size_bytes=7,
        expected_sha256=hashlib.sha256(b"unknown").hexdigest(),
    )
    rejected = await admit_file_capability(
        _request(unsupported), authorization_port=_port()
    )

    assert not_applicable.state == "not_applicable"
    assert not_applicable.fallback_prohibited is False
    assert rejected.rejection_code == "attachment_classification_type_unsupported"
    assert rejected.fallback_prohibited is True


def test_registry_is_deterministic_and_specialized_execution_remains_xlsx_only():
    from app.attachments.capability_admission import ParserIdentity
    from app.attachments.capability_admission import RuntimeDependencyKind
    from app.attachments.file_capabilities import (
        ParserIdentity as CanonicalParserIdentity,
    )
    from app.attachments.file_capabilities import (
        RuntimeDependencyKind as CanonicalRuntimeDependencyKind,
    )

    profile_ids = {profile.profile_id for profile in FILE_CAPABILITY_REGISTRY}

    assert FILE_CAPABILITY_REGISTRY_VERSION == "ai-platform.file-capability-registry.v3"
    assert registry_digest(FILE_CAPABILITY_REGISTRY) == FILE_CAPABILITY_REGISTRY_DIGEST
    assert ParserIdentity is CanonicalParserIdentity
    assert RuntimeDependencyKind is CanonicalRuntimeDependencyKind
    assert "tabular.xlsx" in profile_ids
    assert all(
        not profile.enabled
        for profile in FILE_CAPABILITY_REGISTRY
        if profile.profile_id != "tabular.xlsx"
    )
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
