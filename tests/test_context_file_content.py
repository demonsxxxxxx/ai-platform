import hashlib
import io
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile

import pytest
from docx import Document
from openpyxl import Workbook
from pypdf import PdfWriter
from pypdf.generic import ArrayObject, DictionaryObject, NameObject, NumberObject, TextStringObject

from app.context.file_content import (
    ContextFileContentError,
    DOCX_CONTENT_TYPE,
    MAX_CONTEXT_FILE_STAGE_BYTES,
    MAX_DOCUMENT_SOURCE_BYTES,
    MAX_PDF_OBJECTS_INSPECTED,
    MAX_PDF_PAGES,
    MAX_TEXT_SOURCE_BYTES,
    PDF_CONTENT_TYPE,
    _pdf_has_active_content,
    parse_context_file,
)
from app.file_parser_contracts import XLSX_CONTENT_TYPE


def _row(name: str, content_type: str, raw: bytes) -> dict[str, object]:
    return {
        "id": "file-a",
        "original_name": name,
        "content_type": content_type,
        "size_bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }


def _docx_bytes() -> bytes:
    stream = io.BytesIO()
    document = Document()
    document.add_paragraph("First paragraph")
    table = document.add_table(rows=1, cols=2)
    table.cell(0, 0).text = "A"
    table.cell(0, 1).text = "B"
    document.save(stream)
    return stream.getvalue()


def _pdf_bytes(*, encrypted: bool = False, javascript: bool = False) -> bytes:
    stream = io.BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    if encrypted:
        writer.encrypt("secret")
    if javascript:
        writer.add_js("app.alert('no')")
    writer.write(stream)
    return stream.getvalue()


def _pdf_with_page_action_bytes() -> bytes:
    stream = io.BytesIO()
    writer = PdfWriter()
    page = writer.add_blank_page(width=200, height=200)
    page[NameObject("/AA")] = DictionaryObject(
        {
            NameObject("/O"): DictionaryObject(
                {
                    NameObject("/S"): NameObject("/JavaScript"),
                    NameObject("/JS"): TextStringObject("app.alert('no')"),
                }
            )
        }
    )
    writer.write(stream)
    return stream.getvalue()


def _xlsx_bytes() -> bytes:
    stream = io.BytesIO()
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Data"
    sheet.append(["name", "value"])
    sheet.append(["alpha", 1])
    workbook.save(stream)
    return stream.getvalue()


@pytest.mark.parametrize(
    ("name", "content_type", "raw", "parser_id"),
    [
        ("notes.txt", "text/plain", b"bounded text", "ai-platform.text.utf8"),
        ("notes.markdown", "text/markdown", b"# Heading", "ai-platform.text.utf8"),
        ("source.docx", DOCX_CONTENT_TYPE, _docx_bytes(), "ai-platform.docx.python-docx"),
        ("source.pdf", PDF_CONTENT_TYPE, _pdf_bytes(), "ai-platform.pdf.pypdf"),
        ("book.xlsx", XLSX_CONTENT_TYPE, _xlsx_bytes(), "ai-platform.xlsx.openpyxl"),
    ],
    ids=("text", "markdown", "docx", "pdf", "xlsx"),
)
def test_parse_context_file_supports_governed_types(name, content_type, raw, parser_id):
    parsed = parse_context_file(_row(name, content_type, raw), raw)

    assert parsed.parser_id == parser_id
    assert parsed.source_bytes == len(raw)
    if name.endswith(".docx"):
        assert "First paragraph" in parsed.content
        assert "A\tB" in parsed.content
    if name.endswith(".xlsx"):
        assert "alpha" in parsed.content


def test_parse_context_file_rejects_identity_and_type_mismatch():
    raw = b"hello"
    with pytest.raises(ContextFileContentError, match="context_file_identity_mismatch"):
        parse_context_file({**_row("notes.txt", "text/plain", raw), "sha256": "0" * 64}, raw)
    with pytest.raises(ContextFileContentError, match="context_file_type_unsupported"):
        parse_context_file(_row("notes.pdf", PDF_CONTENT_TYPE, raw), raw)


def test_parse_context_file_rejects_oversize_text():
    raw = b"a" * (MAX_TEXT_SOURCE_BYTES + 1)
    with pytest.raises(ContextFileContentError, match="context_file_too_large"):
        parse_context_file(_row("notes.txt", "text/plain", raw), raw)


def test_parse_context_file_accepts_docx_above_legacy_16_mib_limit():
    stream = io.BytesIO(_docx_bytes())
    with ZipFile(stream, "a", compression=ZIP_STORED) as archive:
        archive.writestr("customXml/bounded-padding.bin", b"x" * (17 * 1024 * 1024))
    raw = stream.getvalue()

    assert 16 * 1024 * 1024 < len(raw) <= MAX_DOCUMENT_SOURCE_BYTES
    assert MAX_DOCUMENT_SOURCE_BYTES == MAX_CONTEXT_FILE_STAGE_BYTES == 32 * 1024 * 1024
    parsed = parse_context_file(_row("source.docx", DOCX_CONTENT_TYPE, raw), raw)

    assert parsed.parser_id == "ai-platform.docx.python-docx"
    assert parsed.source_bytes == len(raw)


@pytest.mark.parametrize(
    ("name", "content_type", "prefix"),
    [
        ("source.docx", DOCX_CONTENT_TYPE, b"PK"),
        ("source.pdf", PDF_CONTENT_TYPE, b"%PDF-"),
    ],
    ids=("docx", "pdf"),
)
def test_parse_context_file_rejects_oversize_documents(name, content_type, prefix):
    raw = prefix + b"0" * (MAX_DOCUMENT_SOURCE_BYTES + 1 - len(prefix))

    with pytest.raises(ContextFileContentError, match="context_file_too_large"):
        parse_context_file(_row(name, content_type, raw), raw)


def test_parse_context_file_rejects_pdf_page_limit():
    stream = io.BytesIO()
    writer = PdfWriter()
    for _ in range(MAX_PDF_PAGES + 1):
        writer.add_blank_page(width=20, height=20)
    writer.write(stream)
    raw = stream.getvalue()

    with pytest.raises(ContextFileContentError, match="context_file_type_unsupported"):
        parse_context_file(_row("source.pdf", PDF_CONTENT_TYPE, raw), raw)


def test_pdf_active_content_walk_fails_closed_at_object_limit():
    root = DictionaryObject(
        {
            NameObject("/Items"): ArrayObject(
                NumberObject(index) for index in range(MAX_PDF_OBJECTS_INSPECTED + 1)
            )
        }
    )
    reader = type("Reader", (), {"trailer": {"/Root": root}, "pages": []})()

    assert _pdf_has_active_content(reader) is True


@pytest.mark.parametrize(
    "raw",
    [_pdf_bytes(encrypted=True), _pdf_bytes(javascript=True), _pdf_with_page_action_bytes()],
    ids=("encrypted", "javascript", "page-action"),
)
def test_parse_context_file_rejects_unsafe_pdf(raw):
    with pytest.raises(ContextFileContentError, match="context_file_type_unsupported"):
        parse_context_file(_row("source.pdf", PDF_CONTENT_TYPE, raw), raw)


def test_parse_context_file_rejects_docx_external_relationship():
    stream = io.BytesIO()
    with ZipFile(stream, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "<Types />")
        archive.writestr("word/document.xml", "<document />")
        archive.writestr(
            "word/_rels/document.xml.rels",
            '<Relationships><Relationship TargetMode="External" Target="https://example.test" /></Relationships>',
        )
    raw = stream.getvalue()

    with pytest.raises(ContextFileContentError, match="context_file_type_unsupported"):
        parse_context_file(_row("source.docx", DOCX_CONTENT_TYPE, raw), raw)


@pytest.mark.parametrize(
    ("entry_name", "content_types", "error_code"),
    [
        ("../word/document.xml", "<Types />", "context_file_parse_failed"),
        (
            "word/document.xml",
            '<Types><Override ContentType="application/vnd.ms-word.document.macroEnabled.main+xml" /></Types>',
            "context_file_type_unsupported",
        ),
    ],
    ids=("zip-traversal", "macro-content-type"),
)
def test_parse_context_file_rejects_unsafe_docx_packages(entry_name, content_types, error_code):
    stream = io.BytesIO()
    with ZipFile(stream, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr(entry_name, "<document />")
        if entry_name != "word/document.xml":
            archive.writestr("word/document.xml", "<document />")
    raw = stream.getvalue()

    with pytest.raises(ContextFileContentError, match=error_code):
        parse_context_file(_row("source.docx", DOCX_CONTENT_TYPE, raw), raw)


def test_parse_context_file_rejects_docx_compression_bomb_entry():
    stream = io.BytesIO()
    with ZipFile(stream, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "<Types />")
        archive.writestr("word/document.xml", b"a" * (32 * 1024 * 1024 + 1))
    raw = stream.getvalue()

    with pytest.raises(ContextFileContentError, match="context_file_parse_failed"):
        parse_context_file(_row("source.docx", DOCX_CONTENT_TYPE, raw), raw)
