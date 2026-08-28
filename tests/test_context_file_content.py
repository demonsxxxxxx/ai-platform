import hashlib
import io
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile

import pytest
from docx import Document
from openpyxl import Workbook
from pypdf import PdfWriter
from pypdf.generic import ArrayObject, DictionaryObject, NameObject, NumberObject, TextStringObject

from app.context.api import ContextFileContentError
from app.context.file_content import (
    DOCX_CONTENT_TYPE,
    MAX_CONTEXT_FILE_STAGE_BYTES,
    MAX_PDF_OBJECTS_INSPECTED,
    MAX_PDF_PAGES,
    PDF_CONTENT_TYPE,
    _pdf_has_active_content,
    validate_context_file_for_stage,
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


def _zip_with_added_part(
    raw: bytes,
    name: str,
    payload: bytes,
    *,
    compression: int = ZIP_DEFLATED,
) -> bytes:
    stream = io.BytesIO(raw)
    with ZipFile(stream, "a", compression=compression) as archive:
        archive.writestr(name, payload)
    return stream.getvalue()


def _docx_with_relationship_type(
    relationship_type: str,
    *,
    target: str = "embeddings/opaque.bin",
) -> bytes:
    source = ZipFile(io.BytesIO(_docx_bytes()))
    stream = io.BytesIO()
    with source, ZipFile(stream, "w", compression=ZIP_DEFLATED) as archive:
        for entry in source.infolist():
            payload = source.read(entry)
            if entry.filename == "word/_rels/document.xml.rels":
                payload = payload.replace(
                    b"</Relationships>",
                    (
                        f'<Relationship Id="rId900" Type="{relationship_type}" '
                        f'Target="{target}" /></Relationships>'
                    ).encode(),
                )
            archive.writestr(entry, payload)
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


def _empty_password_pdf_bytes(*, page_count: int = 1, javascript: bool = False) -> bytes:
    stream = io.BytesIO()
    writer = PdfWriter()
    for _ in range(page_count):
        writer.add_blank_page(width=200, height=200)
    if javascript:
        writer.add_js("app.alert('no')")
    writer.encrypt(user_password="", owner_password="owner-password")
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


def _xlsx_bytes_with_cells(cell_count: int) -> bytes:
    stream = io.BytesIO()
    workbook = Workbook()
    sheet = workbook.active
    for row in range(1, cell_count + 1):
        sheet.cell(row=row, column=1, value=row)
    workbook.save(stream)
    return stream.getvalue()


def _xlsx_with_embedded_object(
    relationship_type: str,
    payload: bytes,
    *,
    part_name: str = "xl/embeddings/object.bin",
) -> bytes:
    source = ZipFile(io.BytesIO(_xlsx_bytes_with_cells(2)))
    stream = io.BytesIO()
    with source, ZipFile(stream, "w", compression=ZIP_DEFLATED) as archive:
        for entry in source.infolist():
            entry_payload = source.read(entry)
            if entry.filename == "xl/_rels/workbook.xml.rels":
                entry_payload = entry_payload.replace(
                    b"</Relationships>",
                    (
                        f'<Relationship Id="rId900" Type="{relationship_type}" '
                        f'Target="{part_name.removeprefix("xl/")}" /></Relationships>'
                    ).encode(),
                )
            archive.writestr(entry, entry_payload)
        archive.writestr(part_name, payload)
    return stream.getvalue()


def _passive_vsdx_bytes() -> bytes:
    stream = io.BytesIO()
    with ZipFile(stream, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr(
            "[Content_Types].xml",
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml" />'
            '<Default Extension="xml" ContentType="application/xml" />'
            '<Override PartName="/visio/document.xml" '
            'ContentType="application/vnd.ms-visio.drawing.main+xml" />'
            "</Types>",
        )
        archive.writestr(
            "_rels/.rels",
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.microsoft.com/visio/2010/relationships/document" '
            'Target="visio/document.xml" />'
            "</Relationships>",
        )
        archive.writestr(
            "visio/document.xml",
            '<VisioDocument xmlns="http://schemas.microsoft.com/office/visio/2012/main" />',
        )
    return stream.getvalue()


def _docx_with_opaque_content_bytes() -> bytes:
    source = ZipFile(io.BytesIO(_docx_bytes()))
    stream = io.BytesIO()
    with source, ZipFile(stream, "w", compression=ZIP_DEFLATED) as archive:
        for entry in source.infolist():
            payload = source.read(entry)
            if entry.filename == "[Content_Types].xml":
                payload = payload.replace(
                    b"</Types>",
                    (
                        b'<Default Extension="vsdx" '
                        b'ContentType="application/vnd.ms-visio.drawing" />'
                        b"</Types>"
                    ),
                )
            elif entry.filename == "word/_rels/document.xml.rels":
                payload = payload.replace(
                    b"</Relationships>",
                    (
                        b'<Relationship Id="rId900" '
                        b'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/package" '
                        b'Target="embeddings/process-flow.vsdx" />'
                        b'<Relationship Id="rId901" '
                        b'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/attachedTemplate" '
                        b'Target="https://example.invalid/template.dotx" TargetMode="External" />'
                        b"</Relationships>"
                    ),
                )
            archive.writestr(entry, payload)
        archive.writestr("word/embeddings/process-flow.vsdx", _passive_vsdx_bytes())
    return stream.getvalue()


def test_validate_context_file_for_stage_accepts_xlsx_above_legacy_cell_limit():
    raw = _xlsx_bytes_with_cells(2_049)

    validate_context_file_for_stage(_row("large.xlsx", XLSX_CONTENT_TYPE, raw), raw)


@pytest.mark.parametrize(
    ("name", "content_type", "raw"),
    [
        ("notes.txt", "text/plain", b"bounded text"),
        ("source.docx", DOCX_CONTENT_TYPE, _docx_bytes()),
        ("source.pdf", PDF_CONTENT_TYPE, _pdf_bytes()),
        ("book.xlsx", XLSX_CONTENT_TYPE, _xlsx_bytes_with_cells(2)),
    ],
    ids=("text", "docx", "pdf", "xlsx"),
)
def test_validate_context_file_for_stage_accepts_governed_types(name, content_type, raw):
    validate_context_file_for_stage(_row(name, content_type, raw), raw)


def test_validate_context_file_for_stage_rejects_identity_and_type_mismatch():
    raw = b"hello"
    with pytest.raises(ContextFileContentError, match="context_file_identity_mismatch"):
        validate_context_file_for_stage(
            {**_row("notes.txt", "text/plain", raw), "sha256": "0" * 64}, raw
        )
    with pytest.raises(ContextFileContentError, match="context_file_type_unsupported"):
        validate_context_file_for_stage(_row("notes.pdf", PDF_CONTENT_TYPE, raw), raw)


def test_validate_context_file_for_stage_rejects_oversize_file():
    raw = b"a" * (MAX_CONTEXT_FILE_STAGE_BYTES + 1)

    with pytest.raises(ContextFileContentError, match="context_file_too_large"):
        validate_context_file_for_stage(_row("notes.txt", "text/plain", raw), raw)


def test_validate_context_file_for_stage_accepts_docx_above_legacy_16_mib_limit():
    stream = io.BytesIO(_docx_bytes())
    with ZipFile(stream, "a", compression=ZIP_STORED) as archive:
        archive.writestr("customXml/bounded-padding.bin", b"x" * (17 * 1024 * 1024))
    raw = stream.getvalue()

    assert 16 * 1024 * 1024 < len(raw) <= MAX_CONTEXT_FILE_STAGE_BYTES
    validate_context_file_for_stage(_row("source.docx", DOCX_CONTENT_TYPE, raw), raw)


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
    ("raw", "error_code"),
    [
        (_pdf_bytes(encrypted=True), "context_file_pdf_password_required"),
        (_empty_password_pdf_bytes(), "context_file_pdf_password_required"),
        (_pdf_bytes(javascript=True), "context_file_pdf_active_content_unsupported"),
        (_pdf_with_page_action_bytes(), "context_file_pdf_active_content_unsupported"),
        (
            _empty_password_pdf_bytes(page_count=MAX_PDF_PAGES + 1),
            "context_file_pdf_password_required",
        ),
    ],
    ids=("password", "empty-password", "javascript", "page-action", "encrypted-page-limit"),
)
def test_validate_context_file_for_stage_rejects_unsafe_pdf(raw, error_code):
    with pytest.raises(ContextFileContentError, match=error_code):
        validate_context_file_for_stage(_row("source.pdf", PDF_CONTENT_TYPE, raw), raw)


def test_validate_context_file_for_stage_rejects_pdf_page_limit():
    stream = io.BytesIO()
    writer = PdfWriter()
    for _ in range(MAX_PDF_PAGES + 1):
        writer.add_blank_page(width=20, height=20)
    writer.write(stream)
    raw = stream.getvalue()

    with pytest.raises(ContextFileContentError, match="context_file_pdf_page_limit_exceeded"):
        validate_context_file_for_stage(_row("source.pdf", PDF_CONTENT_TYPE, raw), raw)


def test_validate_context_file_for_stage_rejects_invalid_xlsx_archive():
    raw = b"PK-not-a-zip"

    with pytest.raises(ContextFileContentError, match="context_file_xlsx_archive_invalid"):
        validate_context_file_for_stage(_row("source.xlsx", XLSX_CONTENT_TYPE, raw), raw)


def test_validate_context_file_for_stage_rejects_xlsx_ole_content():
    ole_relationship = (
        "http://schemas.openxmlformats.org/officeDocument/2006/relationships/oleObject"
    )
    package_relationship = (
        "http://schemas.openxmlformats.org/officeDocument/2006/relationships/package"
    )
    vba_relationship = (
        "http://schemas.microsoft.com/office/2006/relationships/vbaProject"
    )
    cases = (
        _xlsx_with_embedded_object(ole_relationship, b"opaque"),
        _xlsx_with_embedded_object(package_relationship, b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1payload"),
        _xlsx_with_embedded_object(
            package_relationship,
            b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1payload",
            part_name="xl/data/payload.bin",
        ),
        _xlsx_with_embedded_object(
            vba_relationship,
            b"opaque",
            part_name="xl/data/payload.bin",
        ),
    )

    for raw in cases:
        with pytest.raises(ContextFileContentError, match="context_file_xlsx_archive_invalid"):
            validate_context_file_for_stage(_row("source.xlsx", XLSX_CONTENT_TYPE, raw), raw)


def test_validate_context_file_for_stage_accepts_opaque_docx_content():
    raw = _docx_with_opaque_content_bytes()

    validate_context_file_for_stage(_row("source.docx", DOCX_CONTENT_TYPE, raw), raw)

    assert Document(io.BytesIO(raw)).paragraphs[0].text == "First paragraph"
    with ZipFile(io.BytesIO(raw)) as archive:
        with ZipFile(io.BytesIO(archive.read("word/embeddings/process-flow.vsdx"))) as embedded:
            assert "visio/document.xml" in embedded.namelist()


def test_validate_context_file_for_stage_rejects_docx_ole_and_activex():
    active_relationship = (
        "http://schemas.openxmlformats.org/officeDocument/2006/relationships/oleObject"
    )
    vba_relationship = (
        "http://schemas.microsoft.com/office/2006/relationships/vbaProject"
    )
    nested_stream = io.BytesIO()
    with ZipFile(nested_stream, "w", compression=ZIP_DEFLATED) as nested:
        nested.writestr("visio/media/payload.dat", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1payload")
    middle_stream = io.BytesIO()
    with ZipFile(middle_stream, "w", compression=ZIP_DEFLATED) as middle:
        middle.writestr("visio/embeddings/inner.zip", nested_stream.getvalue())
    cases = (
        _zip_with_added_part(
            _docx_bytes(),
            "word/embeddings/opaque.bin",
            b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1payload",
        ),
        _zip_with_added_part(
            _docx_bytes(),
            "word/embeddings/process-flow.vsdx",
            nested_stream.getvalue(),
        ),
        _zip_with_added_part(
            _docx_bytes(),
            "word/embeddings/nested-package.zip",
            middle_stream.getvalue(),
        ),
        _zip_with_added_part(_docx_bytes(), "word/activeX/activeX1.bin", b"opaque"),
        _zip_with_added_part(
            _docx_with_relationship_type(active_relationship),
            "word/embeddings/opaque.bin",
            b"opaque",
        ),
        _zip_with_added_part(
            _docx_with_relationship_type(
                vba_relationship,
                target="data/payload.bin",
            ),
            "word/data/payload.bin",
            b"opaque",
        ),
    )

    for raw in cases:
        with pytest.raises(ContextFileContentError, match="context_file_docx_macros_unsupported"):
            validate_context_file_for_stage(_row("source.docx", DOCX_CONTENT_TYPE, raw), raw)


def test_validate_context_file_for_stage_rejects_outer_and_embedded_zip_bombs():
    outer = _zip_with_added_part(_docx_bytes(), "customXml/repetitive.txt", b"a" * (1024 * 1024))
    embedded_stream = io.BytesIO()
    with ZipFile(embedded_stream, "w", compression=ZIP_DEFLATED) as embedded:
        embedded.writestr("visio/repetitive.xml", b"a" * (1024 * 1024))
    nested = _zip_with_added_part(
        _docx_bytes(),
        "word/embeddings/process-flow.vsdx",
        embedded_stream.getvalue(),
    )

    for raw in (outer, nested):
        with pytest.raises(ContextFileContentError, match="context_file_docx_archive_too_large"):
            validate_context_file_for_stage(_row("source.docx", DOCX_CONTENT_TYPE, raw), raw)


def test_validate_context_file_for_stage_applies_cumulative_embedded_zip_budgets(monkeypatch):
    embedded_stream = io.BytesIO()
    with ZipFile(embedded_stream, "w", compression=ZIP_DEFLATED) as embedded:
        embedded.writestr("payload.bin", b"a" * 400)
    package_relationship = (
        "http://schemas.openxmlformats.org/officeDocument/2006/relationships/package"
    )
    stream = io.BytesIO()
    with ZipFile(stream, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "<Types />")
        archive.writestr("word/document.xml", "<document />")
        archive.writestr(
            "word/_rels/document.xml.rels",
            (
                '<Relationships><Relationship Id="rId1" '
                f'Type="{package_relationship}" Target="data/second.bin" />'
                "</Relationships>"
            ),
        )
        archive.writestr("word/embeddings/first.zip", embedded_stream.getvalue())
        archive.writestr("word/data/second.bin", b"stub" + embedded_stream.getvalue())
    raw = stream.getvalue()

    monkeypatch.setattr("app.context.file_content.MAX_DOCX_ARCHIVE_TOTAL_BYTES", 1024)
    with pytest.raises(ContextFileContentError, match="context_file_docx_archive_too_large"):
        validate_context_file_for_stage(_row("source.docx", DOCX_CONTENT_TYPE, raw), raw)

    monkeypatch.setattr("app.context.file_content.MAX_DOCX_ARCHIVE_TOTAL_BYTES", 64 * 1024 * 1024)
    monkeypatch.setattr("app.context.file_content.MAX_OPC_ARCHIVE_ENTRIES", 6)
    with pytest.raises(
        ContextFileContentError,
        match="context_file_docx_archive_entry_limit_exceeded",
    ):
        validate_context_file_for_stage(_row("source.docx", DOCX_CONTENT_TYPE, raw), raw)


@pytest.mark.parametrize(
    "unsafe_name",
    ["../xl/workbook.xml", "XL/workbook.xml", "xl//workbook.xml", "xl/./workbook.xml"],
)
def test_validate_context_file_for_stage_rejects_unsafe_xlsx_paths(unsafe_name):
    raw = _zip_with_added_part(
        _xlsx_bytes_with_cells(2),
        unsafe_name,
        b'<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" />',
    )

    with pytest.raises(ContextFileContentError, match="context_file_xlsx_archive_invalid"):
        validate_context_file_for_stage(_row("source.xlsx", XLSX_CONTENT_TYPE, raw), raw)


def test_validate_context_file_for_stage_rejects_xlsx_missing_required_part():
    source = ZipFile(io.BytesIO(_xlsx_bytes_with_cells(2)))
    stream = io.BytesIO()
    with source, ZipFile(stream, "w", compression=ZIP_DEFLATED) as archive:
        for entry in source.infolist():
            if entry.filename != "xl/workbook.xml":
                archive.writestr(entry, source.read(entry))
    raw = stream.getvalue()

    with pytest.raises(ContextFileContentError, match="context_file_xlsx_archive_invalid"):
        validate_context_file_for_stage(_row("source.xlsx", XLSX_CONTENT_TYPE, raw), raw)


@pytest.mark.parametrize(
    ("entry_name", "entry_payload", "content_types", "error_code"),
    [
        (
            "../word/document.xml",
            "<document />",
            "<Types />",
            "context_file_docx_archive_structure_invalid",
        ),
        (
            "word/document.xml",
            "<document />",
            '<Types><Override ContentType="application/vnd.ms-word.document.macroEnabled.main+xml" /></Types>',
            "context_file_docx_macros_unsupported",
        ),
        (
            "word/document.xml",
            "<document />",
            '<Types><Override ContentType="application/vnd.ms-word.document.macroEnabled.main+xml" /></Types>'.encode(
                "utf-16"
            ),
            "context_file_docx_macros_unsupported",
        ),
        (
            "word/document.xml",
            "<document />",
            '<Types><Override ContentType="application/vnd.openxmlformats-officedocument.oleObject" /></Types>',
            "context_file_docx_macros_unsupported",
        ),
    ],
    ids=("zip-traversal", "macro-content-type", "utf16-macro-content-type", "ole-object"),
)
def test_validate_context_file_for_stage_rejects_unsafe_docx_packages(
    entry_name, entry_payload, content_types, error_code
):
    stream = io.BytesIO()
    with ZipFile(stream, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr(entry_name, entry_payload)
        if entry_name != "word/document.xml":
            archive.writestr("word/document.xml", "<document />")
    raw = stream.getvalue()

    with pytest.raises(ContextFileContentError, match=error_code):
        validate_context_file_for_stage(_row("source.docx", DOCX_CONTENT_TYPE, raw), raw)


@pytest.mark.parametrize("encoding", ["utf-8", "utf-16"])
def test_validate_context_file_for_stage_rejects_docx_relationship_dtd(encoding):
    stream = io.BytesIO()
    with ZipFile(stream, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "<Types />")
        archive.writestr("word/document.xml", "<document />")
        archive.writestr(
            "word/_rels/document.xml.rels",
            '<!DOCTYPE Relationships [<!ENTITY x "unsafe">]><Relationships>&x;</Relationships>'.encode(
                encoding
            ),
        )
    raw = stream.getvalue()

    with pytest.raises(ContextFileContentError, match="context_file_docx_relationship_invalid"):
        validate_context_file_for_stage(_row("source.docx", DOCX_CONTENT_TYPE, raw), raw)


def test_validate_context_file_for_stage_rejects_docx_compression_bomb_entry():
    stream = io.BytesIO()
    with ZipFile(stream, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "<Types />")
        archive.writestr("word/document.xml", b"a" * (32 * 1024 * 1024 + 1))
    raw = stream.getvalue()

    with pytest.raises(ContextFileContentError, match="context_file_docx_archive_too_large"):
        validate_context_file_for_stage(_row("source.docx", DOCX_CONTENT_TYPE, raw), raw)
