import hashlib
import io

from openpyxl import Workbook

from app.file_preview_contracts import build_xlsx_preview, is_xlsx_preview_request


XLSX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _workbook_bytes() -> bytes:
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "Checks"
    worksheet.append(["requirement", "status"])
    worksheet.append(["ACCEPT-XLSX-9472", True])
    buffer = io.BytesIO()
    workbook.save(buffer)
    workbook.close()
    return buffer.getvalue()


def test_xlsx_preview_uses_the_registered_parser_and_returns_only_the_presentation_dto():
    raw = _workbook_bytes()
    preview = build_xlsx_preview(
        raw=raw,
        file_id="file-preview",
        file_name="checks.xlsx",
        content_type=XLSX_CONTENT_TYPE,
        expected_sha256=hashlib.sha256(raw).hexdigest(),
        expected_byte_count=len(raw),
    )

    assert preview.status == "ready"
    assert preview.source_sha256 == hashlib.sha256(raw).hexdigest()
    assert preview.parser_id == "ai-platform.xlsx.openpyxl"
    assert preview.content is not None
    assert preview.content.sheets[0].name == "Checks"
    assert preview.content.sheets[0].rows[1].cells[0].value == "ACCEPT-XLSX-9472"
    assert preview.error is None
    assert "storage_key" not in preview.model_dump_json()
    assert "workbook.xml" not in preview.model_dump_json()


def test_xlsx_preview_metadata_requires_the_supported_xlsx_extension_and_mime_type():
    assert is_xlsx_preview_request(
        file_name="checks.xlsx",
        content_type=XLSX_CONTENT_TYPE,
    )
    assert not is_xlsx_preview_request(
        file_name="checks.xlsm",
        content_type=XLSX_CONTENT_TYPE,
    )
    assert not is_xlsx_preview_request(
        file_name="checks.xlsx",
        content_type="application/vnd.ms-excel",
    )


def test_oversized_xlsx_preview_fails_with_a_stable_public_code():
    preview = build_xlsx_preview(
        raw=b"0" * (1024 * 1024 + 1),
        file_id="file-large",
        file_name="large.xlsx",
        content_type=XLSX_CONTENT_TYPE,
    )

    assert preview.status == "failed"
    assert preview.error is not None
    assert preview.error.code == "xlsx_preview_file_too_large"
    assert preview.content is None
