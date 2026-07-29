from __future__ import annotations

import hashlib
import io

import pytest
from openpyxl import Workbook

from app.attachments.capability_admission import (
    ADMISSION_REJECTED,
    ADMISSION_REQUIRED,
    FILE_CAPABILITY_REGISTRY,
    FILE_CAPABILITY_REGISTRY_DIGEST,
    FILE_CAPABILITY_REGISTRY_VERSION,
    AgentSkillBinding,
    AuthorizedSkillPin,
    FileCapabilityAdmissionRequest,
    RuntimeDependencyIdentity,
    RuntimeDependencyRequirement,
    RuntimeImageInventory,
    SkillAuthorizationResolution,
    SkillSelection,
    admit_file_capability,
    registry_digest,
)
from app.attachments.classification import (
    CLASSIFICATION_CLASSIFIED,
    AttachmentBytesForClassification,
    classify_attachment_bytes,
)


XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
IMAGE_DIGEST = "sha256:" + "b" * 64


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


def _xlsx_bytes() -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Data"
    sheet["A1"] = "name"
    output = io.BytesIO()
    workbook.save(output)
    workbook.close()
    return output.getvalue()


def _identity(file_id: str = "file-xlsx"):
    raw = _xlsx_bytes()
    result = classify_attachment_bytes(
        AttachmentBytesForClassification(
            file_id=file_id,
            raw_bytes=raw,
            source_filename="report.xlsx",
            declared_media_type="text/plain",
            expected_size_bytes=len(raw),
            expected_sha256=hashlib.sha256(raw).hexdigest(),
        )
    )
    assert result.state == CLASSIFICATION_CLASSIFIED
    assert result.identity is not None
    return result.identity


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


def _inventory(
    *,
    dependencies: tuple[RuntimeDependencyIdentity, ...] | None = None,
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
            RuntimeDependencyIdentity("workspace_local", "qa-rag-skill", "v2026-07-29"),
        ),
        artifact_types=artifact_types,
        node_version=None,
        npm_source_install_allowed=False,
        public_package_registry_egress=False,
    )


def _request(
    *attachments,
    task_intent: str = "analyze",
    explicit_selection: SkillSelection | None = None,
    agent_binding: AgentSkillBinding | None = None,
    runtime_inventory: RuntimeImageInventory | None = None,
):
    return FileCapabilityAdmissionRequest(
        attachments=attachments,
        task_intent=task_intent,
        explicit_selection=explicit_selection,
        agent_binding=agent_binding,
        runtime_inventory=runtime_inventory or _inventory(),
    )


@pytest.mark.asyncio
async def test_xlsx_classification_identity_is_server_bound_to_one_pinned_authorized_skill():
    port = _port()
    identity = _identity()

    result = await admit_file_capability(_request(identity), authorization_port=port)

    assert result.state == ADMISSION_REQUIRED
    assert result.fallback_prohibited is True
    assert result.selected_skill == SkillSelection("qa-rag-skill", "v2026-07-29")
    assert result.parser_requirements[0].parser_id == "ai-platform.xlsx.openpyxl"
    assert result.parser_requirements[0].verified_media_type == XLSX_MIME
    assert result.runtime_image_digest == IMAGE_DIGEST
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
    public_payload = repr([fact.to_public_payload() for fact in result.public_progress_facts])
    assert identity.file_id not in public_payload
    assert identity.sha256 not in public_payload
    assert "qa-rag-skill" not in public_payload


@pytest.mark.asyncio
async def test_admission_rejects_a_value_not_issued_by_the_byte_classifier():
    port = _port()

    result = await admit_file_capability(_request(object()), authorization_port=port)

    assert result.state == ADMISSION_REJECTED
    assert result.rejection_code == "file_capability_metadata_untrusted"
    assert result.fallback_prohibited is True
    assert port.calls == []


@pytest.mark.asyncio
async def test_explicit_valid_selection_precedes_server_choice_and_incompatible_selection_rejects():
    valid = SkillSelection("qa-rag-skill", "caller-version")
    port = _port(pins=(_pin(version="caller-version"),))
    accepted = await admit_file_capability(
        _request(_identity(), explicit_selection=valid), authorization_port=port
    )
    incompatible = await admit_file_capability(
        _request(_identity(), explicit_selection=SkillSelection("other-skill", "v1")),
        authorization_port=_port(),
    )

    assert port.calls[0]["selection"] == valid
    assert accepted.state == ADMISSION_REQUIRED
    assert incompatible.rejection_code == "file_capability_caller_selection_incompatible"


@pytest.mark.asyncio
async def test_agent_binding_is_immutable_and_cannot_be_broadened_by_caller_selection():
    binding = AgentSkillBinding(
        agent_id="agent-immutable",
        selected_skill=SkillSelection("qa-rag-skill", "agent-version"),
    )

    result = await admit_file_capability(
        _request(
            _identity(),
            explicit_selection=SkillSelection("qa-rag-skill", "caller-version"),
            agent_binding=binding,
        ),
        authorization_port=_port(),
    )

    assert result.rejection_code == "file_capability_agent_profile_incompatible"


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
    result = await admit_file_capability(_request(_identity()), authorization_port=_port(status))

    assert result.rejection_code == expected_code
    assert result.fallback_prohibited is True


@pytest.mark.asyncio
async def test_ambiguous_authorization_fails_closed_and_homogeneous_xlsx_files_are_supported():
    ambiguous = await admit_file_capability(
        _request(_identity()),
        authorization_port=_port(pins=(_pin(), _pin(skill_id="qa-rag-skill-2"))),
    )
    homogeneous = await admit_file_capability(
        _request(_identity("file-a"), _identity("file-b")),
        authorization_port=_port(),
    )

    assert ambiguous.rejection_code == "file_capability_skill_ambiguous"
    assert homogeneous.state == ADMISSION_REQUIRED
    assert len(homogeneous.parser_requirements) == 2


@pytest.mark.asyncio
async def test_exact_runtime_inventory_rejects_missing_prebuilt_or_workspace_dependency():
    missing_prebuilt = await admit_file_capability(
        _request(
            _identity(),
            runtime_inventory=_inventory(
                dependencies=(RuntimeDependencyIdentity("workspace_local", "qa-rag-skill", "v2026-07-29"),)
            ),
        ),
        authorization_port=_port(),
    )
    missing_workspace = await admit_file_capability(
        _request(
            _identity(),
            runtime_inventory=_inventory(
                dependencies=(RuntimeDependencyIdentity("prebuilt_python", "openpyxl", "3.1.5"),)
            ),
        ),
        authorization_port=_port(),
    )

    assert missing_prebuilt.rejection_code == "file_capability_runtime_dependency_unavailable"
    assert missing_workspace.rejection_code == "file_capability_runtime_dependency_unavailable"


def test_runtime_inventory_does_not_assume_python_or_install_unmet_node_npm_dependencies():
    inventory = _inventory()
    node_requirement = RuntimeDependencyRequirement("node_npm", "sheetjs", "0.20")
    python_requirement = RuntimeDependencyRequirement("python_runtime", "python", "3.11", require_non_root=True)

    assert inventory.missing_requirements((node_requirement,)) == (node_requirement,)
    assert inventory.missing_requirements((python_requirement,)) == ()
    assert inventory.node_version is None
    assert inventory.npm_source_install_allowed is False
    assert inventory.public_package_registry_egress is False
    inventory_with_unrunnable_node_package = _inventory(
        dependencies=(RuntimeDependencyIdentity("node_npm", "sheetjs", "0.20.3"),)
    )
    assert inventory_with_unrunnable_node_package.missing_requirements((node_requirement,)) == (
        node_requirement,
    )


@pytest.mark.asyncio
async def test_artifact_contract_gap_and_fallback_prohibition_are_terminal():
    artifact_gap = await admit_file_capability(
        _request(
            _identity(),
            task_intent="generate_artifact",
            runtime_inventory=_inventory(artifact_types=frozenset()),
        ),
        authorization_port=_port(),
    )
    not_applicable = await admit_file_capability(_request(), authorization_port=_port())

    assert artifact_gap.rejection_code == "file_capability_required_artifact_incompatible"
    assert artifact_gap.fallback_prohibited is True
    assert not_applicable.state == "not_applicable"
    assert not_applicable.fallback_prohibited is False


def test_registry_is_deterministic_and_all_non_xlsx_families_are_explicitly_disabled():
    profile_ids = {profile.profile_id for profile in FILE_CAPABILITY_REGISTRY}

    assert FILE_CAPABILITY_REGISTRY_VERSION == "ai-platform.file-capability-registry.v2"
    assert registry_digest(FILE_CAPABILITY_REGISTRY) == FILE_CAPABILITY_REGISTRY_DIGEST
    assert "tabular.xlsx" in profile_ids
    assert all(not profile.enabled for profile in FILE_CAPABILITY_REGISTRY if profile.profile_id != "tabular.xlsx")
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
