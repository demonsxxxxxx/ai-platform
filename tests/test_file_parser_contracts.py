import hashlib
from pathlib import Path

import pytest
from openpyxl import Workbook

from app.executors.claude_agent_sdk_runner import _attachment_context_prompt_section
from app.file_parser_contracts import (
    MAX_XLSX_CELL_CHARS,
    MAX_XLSX_FILE_BYTES,
    MAX_XLSX_ROWS_PER_SHEET,
    AttachmentPreprocessingError,
    attachment_requirements_from_contract,
    build_attachment_preprocessing_contract,
    is_known_binary_workbook,
    parse_xlsx_attachment,
    validate_required_parser_evidence,
)


def _requirement(file_name: str = "book.xlsx"):
    contract = build_attachment_preprocessing_contract(
        file_ids=["file-a"],
        file_names=[file_name],
    )
    return attachment_requirements_from_contract(contract)[0]


def _write_workbook(path: Path, *, long: bool = False) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Data"
    sheet["A1"] = "name"
    sheet["B1"] = "value"
    sheet["A2"] = "alpha"
    sheet["B2"] = "=1+2"
    if long:
        sheet["C2"] = "界" * (MAX_XLSX_CELL_CHARS + 10)
        for row in range(3, MAX_XLSX_ROWS_PER_SHEET + 3):
            sheet.cell(row=row, column=1, value=f"row-{row}")
    workbook.save(path)
    workbook.close()


def test_xlsx_parser_emits_bounded_typed_content_and_positive_evidence(tmp_path):
    path = tmp_path / "book.xlsx"
    _write_workbook(path)

    parsed = parse_xlsx_attachment(path=path, requirement=_requirement())

    evidence = parsed.evidence
    assert evidence.status == "parsed"
    assert evidence.file_id == "file-a"
    assert evidence.parser_id == "ai-platform.xlsx.openpyxl"
    assert evidence.byte_count == path.stat().st_size
    assert evidence.sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
    assert evidence.sheet_count == 1
    assert evidence.cells_examined >= 4
    formula = parsed.content["workbook"]["sheets"][0]["rows"][1]["cells"][1]
    assert formula == {"column": 2, "kind": "formula", "value": "=1+2"}

    prompt_section = _attachment_context_prompt_section([parsed])
    assert "Platform-preprocessed attachments" in prompt_section
    assert '"kind":"formula"' in prompt_section


def test_xlsx_parser_reports_deterministic_truncation(tmp_path):
    path = tmp_path / "book.xlsx"
    _write_workbook(path, long=True)

    parsed = parse_xlsx_attachment(path=path, requirement=_requirement())

    assert parsed.evidence.truncated is True
    assert parsed.content["workbook"]["truncated"] is True
    first_data_row = parsed.content["workbook"]["sheets"][0]["rows"][1]
    assert len(first_data_row["cells"][2]["value"]) == MAX_XLSX_CELL_CHARS


@pytest.mark.parametrize(
    ("payload", "expected_code"),
    [
        (b"not-a-workbook", "xlsx_parse_failed"),
        (b"x" * (MAX_XLSX_FILE_BYTES + 1), "attachment_parser_file_too_large"),
    ],
    ids=["malformed", "oversized"],
)
def test_xlsx_parser_fails_truthfully_for_malformed_or_oversized_input(tmp_path, payload, expected_code):
    path = tmp_path / "book.xlsx"
    path.write_bytes(payload)

    with pytest.raises(AttachmentPreprocessingError, match=expected_code):
        parse_xlsx_attachment(path=path, requirement=_requirement())


def test_platform_registry_marks_legacy_workbook_unsupported():
    contract = build_attachment_preprocessing_contract(
        file_ids=["file-a"],
        file_names=["legacy.xls"],
    )
    requirement = attachment_requirements_from_contract(contract)[0]

    assert requirement.supported is False
    assert requirement.parser_id == "unsupported"
    assert is_known_binary_workbook(file_name="legacy.xls") is True


def test_platform_registry_uses_server_content_type_when_extension_is_generic(tmp_path):
    path = tmp_path / "attachment.bin"
    _write_workbook(path)
    contract = build_attachment_preprocessing_contract(
        file_ids=["file-a"],
        file_names=["attachment.bin"],
        content_types=["application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"],
    )
    requirement = attachment_requirements_from_contract(contract)[0]

    parsed = parse_xlsx_attachment(path=path, requirement=requirement)

    assert requirement.extension == ".bin"
    assert requirement.content_type.endswith("spreadsheetml.sheet")
    assert parsed.evidence.status == "parsed"


def test_parser_rejects_brokered_bytes_that_do_not_match_worker_materialization(tmp_path):
    path = tmp_path / "book.xlsx"
    _write_workbook(path)
    contract = build_attachment_preprocessing_contract(
        file_ids=["file-a"],
        file_names=["book.xlsx"],
        workspace=tmp_path,
    )
    requirement = attachment_requirements_from_contract(contract)[0]
    assert requirement.expected_byte_count == path.stat().st_size
    assert requirement.expected_sha256 == hashlib.sha256(path.read_bytes()).hexdigest()

    path.write_bytes(b"tampered broker bytes")
    with pytest.raises(AttachmentPreprocessingError, match="attachment_parser_staged_file_mismatch"):
        parse_xlsx_attachment(path=path, requirement=requirement)


def test_worker_evidence_validation_rejects_mismatch_and_accepts_exact_record(tmp_path):
    path = tmp_path / "book.xlsx"
    _write_workbook(path)
    requirement = _requirement()
    parsed = parse_xlsx_attachment(path=path, requirement=requirement)
    evidence = parsed.evidence.model_dump(mode="json")

    assert validate_required_parser_evidence(
        requirements=[requirement],
        evidence=[evidence],
    ) == (True, "")

    evidence["parser_version"] = "999"
    assert validate_required_parser_evidence(
        requirements=[requirement],
        evidence=[evidence],
    ) == (False, "attachment_parser_evidence_mismatch")
