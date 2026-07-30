import io
import zipfile

import pytest
from openpyxl import Workbook

from app.executors.base import ArtifactManifest
from app.file_parser_contracts import MAX_XLSX_FILE_BYTES
from app.skills.deliverables import (
    SkillDeliverableContractError,
    deliverable_contract_from_manifest,
    parse_skill_deliverable_contract,
    public_artifact_matches_contract,
    verified_xlsx_delivery,
)


def usable_xlsx_bytes() -> bytes:
    buffer = io.BytesIO()
    workbook = Workbook()
    workbook.active.title = "Result"
    workbook.active["A1"] = 1
    workbook.save(buffer)
    return buffer.getvalue()


def xlsx_contract(*, requires_process_evidence: bool = False) -> dict[str, object]:
    return parse_skill_deliverable_contract(
        {
            "deliverable-public-types": "xlsx",
            "deliverable-required-types": "xlsx",
            "deliverable-process-evidence": "required" if requires_process_evidence else "not_required",
        }
    )


def test_manifest_front_matter_binds_only_server_owned_xlsx_spec():
    contract = xlsx_contract(requires_process_evidence=True)

    assert contract == {
        "schema_version": "ai-platform.skill-deliverable-contract.v1",
        "allowed_public_deliverables": [
            {
                "deliverable_type": "xlsx",
                "artifact_type": "xlsx",
                "label": "Excel 文件",
                "extension": ".xlsx",
                "content_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "max_size_bytes": MAX_XLSX_FILE_BYTES,
            }
        ],
        "required_terminal_types": ["xlsx"],
        "requires_process_evidence": True,
    }


@pytest.mark.parametrize(
    "metadata,error_code",
    [
        ({"deliverable-public-types": "xlsx"}, "skill_deliverable_contract_incomplete"),
        (
            {
                "deliverable-public-types": "xlsx,pdf",
                "deliverable-required-types": "xlsx",
                "deliverable-process-evidence": "required",
            },
            "skill_deliverable_public_types_invalid",
        ),
    ],
)
def test_manifest_front_matter_rejects_incomplete_or_unregistered_contracts(metadata, error_code):
    with pytest.raises(SkillDeliverableContractError, match=error_code):
        parse_skill_deliverable_contract(metadata)


def test_pinned_manifest_rejects_contract_injected_outside_immutable_package():
    contract = xlsx_contract()
    manifest = {
        "deliverable_contract": contract,
        "source": {"package_contract": {"deliverable_contract": None}},
    }

    with pytest.raises(SkillDeliverableContractError, match="skill_deliverable_contract_pin_mismatch"):
        deliverable_contract_from_manifest(manifest)


def test_xlsx_verifier_requires_reachable_parseable_worksheet(tmp_path):
    workbook = tmp_path / "audit-result.xlsx"
    workbook.write_bytes(usable_xlsx_bytes())
    spec = xlsx_contract()["allowed_public_deliverables"][0]

    assert verified_xlsx_delivery(workbook, spec=spec) is True

    malformed = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(usable_xlsx_bytes())) as source:
        with zipfile.ZipFile(malformed, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for entry in source.infolist():
                content = b"<worksheet" if entry.filename == "xl/worksheets/sheet1.xml" else source.read(entry)
                archive.writestr(entry, content)
    workbook.write_bytes(malformed.getvalue())

    assert verified_xlsx_delivery(workbook, spec=spec) is False


def test_contract_artifact_requires_runtime_admission_origin():
    artifact = ArtifactManifest(
        artifact_type="xlsx",
        label="Excel 文件",
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        storage_key="tenants/tenant-a/runs/run-a/artifacts/1/audit-result.xlsx",
        size_bytes=1,
        manifest={
            "deliverable_type": "xlsx",
            "workspace_output": "outputs/audit-rca/delivery/audit-result.xlsx",
        },
    )

    assert public_artifact_matches_contract(xlsx_contract(), artifact) is True
    assert public_artifact_matches_contract(
        xlsx_contract(),
        ArtifactManifest(
            artifact_type="xlsx",
            label="Excel 文件",
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            storage_key="tenants/tenant-a/runs/run-a/artifacts/1/audit-result.xlsx",
            size_bytes=1,
            manifest={},
        ),
    ) is False
