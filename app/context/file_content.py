from __future__ import annotations

import hashlib
import io
import json
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
import tempfile
from typing import Any
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile

from docx import Document
from pypdf import PdfReader
from pypdf.generic import ArrayObject, DictionaryObject, IndirectObject

from app.context_manifest import truncate_utf8_text
from app.file_parser_contracts import (
    AttachmentParserRequirement,
    MaterializedAttachmentFact,
    XLSX_CONTENT_TYPE,
    build_attachment_preprocessing_contract,
    parse_xlsx_attachment,
)


MAX_CONTEXT_FILE_OUTPUT_BYTES = 64 * 1024
MAX_TEXT_SOURCE_BYTES = 1024 * 1024
MAX_XLSX_SOURCE_BYTES = 1024 * 1024
MAX_DOCUMENT_SOURCE_BYTES = 32 * 1024 * 1024
MAX_CONTEXT_FILE_STAGE_BYTES = 32 * 1024 * 1024
MAX_DOCX_ENTRIES = 2000
MAX_DOCX_ENTRY_BYTES = 32 * 1024 * 1024
MAX_DOCX_TOTAL_BYTES = 64 * 1024 * 1024
MAX_PDF_PAGES = 200
MAX_PDF_OBJECTS_INSPECTED = 20_000

DOCX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
PDF_CONTENT_TYPE = "application/pdf"
TEXT_CONTENT_TYPES = frozenset(
    {
        "application/json",
        "text/csv",
        "text/markdown",
        "text/plain",
    }
)
TEXT_EXTENSIONS = frozenset({".csv", ".json", ".markdown", ".md", ".txt"})


class ContextFileContentError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class ParsedContextFile:
    content: str
    content_type: str
    parser_id: str
    parser_version: str
    source_bytes: int
    truncated: bool


def _content_type(value: object) -> str:
    return str(value or "").split(";", 1)[0].strip().casefold()


def _extension(value: object) -> str:
    name = PurePosixPath(str(value or "").replace("\\", "/")).name
    return PurePosixPath(name).suffix.casefold()


def _bounded_output(value: str, *, max_output_bytes: int) -> tuple[str, bool]:
    cap = max(1, min(int(max_output_bytes), MAX_CONTEXT_FILE_OUTPUT_BYTES))
    encoded = value.encode("utf-8")
    return (
        truncate_utf8_text(value, max_bytes=cap),
        len(encoded) > cap,
    )


def _validate_identity(row: dict[str, Any], raw: bytes) -> str:
    declared_size = row.get("size_bytes")
    if declared_size is not None:
        try:
            if int(declared_size) != len(raw):
                raise ContextFileContentError("context_file_identity_mismatch")
        except (TypeError, ValueError) as exc:
            raise ContextFileContentError("context_file_identity_mismatch") from exc
    actual_sha256 = hashlib.sha256(raw).hexdigest()
    expected_sha256 = str(row.get("sha256") or "").casefold()
    if expected_sha256 and expected_sha256 != actual_sha256:
        raise ContextFileContentError("context_file_identity_mismatch")
    return actual_sha256


def _classify(row: dict[str, Any], raw: bytes) -> tuple[str, str]:
    name = row.get("original_name") or row.get("name")
    extension = _extension(name)
    declared_type = _content_type(row.get("content_type"))
    if extension in TEXT_EXTENSIONS and declared_type in TEXT_CONTENT_TYPES:
        return "text", declared_type
    if extension == ".docx" and declared_type == DOCX_CONTENT_TYPE and raw.startswith(b"PK"):
        return "docx", DOCX_CONTENT_TYPE
    if extension == ".pdf" and declared_type == PDF_CONTENT_TYPE and raw.startswith(b"%PDF-"):
        return "pdf", PDF_CONTENT_TYPE
    if extension == ".xlsx" and declared_type == XLSX_CONTENT_TYPE and raw.startswith(b"PK"):
        return "xlsx", XLSX_CONTENT_TYPE
    raise ContextFileContentError("context_file_type_unsupported")


def _parse_text(raw: bytes, *, content_type: str, max_output_bytes: int) -> ParsedContextFile:
    if len(raw) > MAX_TEXT_SOURCE_BYTES:
        raise ContextFileContentError("context_file_too_large")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ContextFileContentError("context_file_parse_failed") from exc
    if content_type == "application/json":
        try:
            json.loads(text)
        except json.JSONDecodeError as exc:
            raise ContextFileContentError("context_file_parse_failed") from exc
    content, truncated = _bounded_output(text, max_output_bytes=max_output_bytes)
    return ParsedContextFile(
        content=content,
        content_type=content_type,
        parser_id="ai-platform.text.utf8",
        parser_version="1",
        source_bytes=len(raw),
        truncated=truncated,
    )


def _validate_docx_archive(raw: bytes) -> None:
    try:
        archive = ZipFile(io.BytesIO(raw))
    except (BadZipFile, ValueError) as exc:
        raise ContextFileContentError("context_file_parse_failed") from exc
    total_bytes = 0
    seen: set[str] = set()
    try:
        entries = archive.infolist()
        if len(entries) > MAX_DOCX_ENTRIES:
            raise ContextFileContentError("context_file_parse_failed")
        for entry in entries:
            name = entry.filename.replace("\\", "/")
            path = PurePosixPath(name)
            windows_path = PureWindowsPath(entry.filename)
            normalized = name.casefold()
            if (
                not name
                or name.startswith("/")
                or windows_path.is_absolute()
                or bool(windows_path.drive)
                or normalized in seen
                or any(part in {"", ".", ".."} for part in path.parts)
                or entry.flag_bits & 0x1
            ):
                raise ContextFileContentError("context_file_parse_failed")
            seen.add(normalized)
            if entry.file_size < 0 or entry.file_size > MAX_DOCX_ENTRY_BYTES:
                raise ContextFileContentError("context_file_parse_failed")
            total_bytes += entry.file_size
            if total_bytes > MAX_DOCX_TOTAL_BYTES:
                raise ContextFileContentError("context_file_parse_failed")
            if "vbaproject" in normalized or normalized.startswith("word/embeddings/"):
                raise ContextFileContentError("context_file_type_unsupported")
        if "[content_types].xml" not in seen or "word/document.xml" not in seen:
            raise ContextFileContentError("context_file_type_unsupported")
        content_types = archive.read("[Content_Types].xml")
        lowered_content_types = content_types.lower()
        if any(
            marker in lowered_content_types
            for marker in (b"macroenabled", b"vbaproject", b"activex", b"oleobject")
        ):
            raise ContextFileContentError("context_file_type_unsupported")
        for entry in entries:
            if not entry.filename.casefold().endswith(".rels"):
                continue
            try:
                relationship_xml = archive.read(entry)
                if b"<!doctype" in relationship_xml.lower():
                    raise ContextFileContentError("context_file_parse_failed")
                root = ElementTree.fromstring(relationship_xml)
            except ElementTree.ParseError as exc:
                raise ContextFileContentError("context_file_parse_failed") from exc
            if any(str(node.attrib.get("TargetMode") or "").casefold() == "external" for node in root):
                raise ContextFileContentError("context_file_type_unsupported")
    finally:
        archive.close()


def _parse_docx(raw: bytes, *, max_output_bytes: int) -> ParsedContextFile:
    if len(raw) > MAX_DOCUMENT_SOURCE_BYTES:
        raise ContextFileContentError("context_file_too_large")
    _validate_docx_archive(raw)
    try:
        document = Document(io.BytesIO(raw))
        lines: list[str] = []
        for block in document.iter_inner_content():
            if hasattr(block, "text") and not hasattr(block, "rows"):
                text = str(block.text or "").strip()
                if text:
                    lines.append(text)
                continue
            rows = getattr(block, "rows", ())
            for table_row in rows:
                values = [str(cell.text or "").strip() for cell in table_row.cells]
                lines.append("\t".join(values))
    except Exception as exc:
        raise ContextFileContentError("context_file_parse_failed") from exc
    content, truncated = _bounded_output("\n".join(lines), max_output_bytes=max_output_bytes)
    return ParsedContextFile(
        content=content,
        content_type=DOCX_CONTENT_TYPE,
        parser_id="ai-platform.docx.python-docx",
        parser_version="1",
        source_bytes=len(raw),
        truncated=truncated,
    )


def _pdf_has_active_content(reader: PdfReader) -> bool:
    try:
        root = reader.trailer["/Root"].get_object()
        pending: list[object] = [root, *reader.pages]
        visited_indirect: set[tuple[int, int]] = set()
        visited_direct: set[int] = set()
        inspected = 0
        while pending:
            current = pending.pop()
            inspected += 1
            if inspected > MAX_PDF_OBJECTS_INSPECTED:
                return True
            if isinstance(current, IndirectObject):
                identity = (int(current.idnum), int(current.generation))
                if identity in visited_indirect:
                    continue
                visited_indirect.add(identity)
                pending.append(current.get_object())
                continue
            if isinstance(current, DictionaryObject):
                direct_identity = id(current)
                if direct_identity in visited_direct:
                    continue
                visited_direct.add(direct_identity)
                keys = {str(key) for key in current.keys()}
                if keys & {"/AA", "/EF", "/EmbeddedFiles", "/JavaScript", "/JS", "/OpenAction"}:
                    return True
                if str(current.get("/S") or "") in {
                    "/ImportData",
                    "/JavaScript",
                    "/Launch",
                    "/SubmitForm",
                }:
                    return True
                pending.extend(current.values())
                continue
            if isinstance(current, ArrayObject):
                pending.extend(current)
    except Exception:
        return True
    return False


def _parse_pdf(raw: bytes, *, max_output_bytes: int) -> ParsedContextFile:
    if len(raw) > MAX_DOCUMENT_SOURCE_BYTES:
        raise ContextFileContentError("context_file_too_large")
    try:
        reader = PdfReader(io.BytesIO(raw), strict=True)
        if reader.is_encrypted or len(reader.pages) > MAX_PDF_PAGES or _pdf_has_active_content(reader):
            raise ContextFileContentError("context_file_type_unsupported")
        lines = [str(page.extract_text() or "") for page in reader.pages]
    except ContextFileContentError:
        raise
    except Exception as exc:
        raise ContextFileContentError("context_file_parse_failed") from exc
    content, truncated = _bounded_output("\n".join(lines), max_output_bytes=max_output_bytes)
    return ParsedContextFile(
        content=content,
        content_type=PDF_CONTENT_TYPE,
        parser_id="ai-platform.pdf.pypdf",
        parser_version="1",
        source_bytes=len(raw),
        truncated=truncated,
    )


def _parse_xlsx(
    row: dict[str, Any],
    raw: bytes,
    *,
    actual_sha256: str,
    max_output_bytes: int,
) -> ParsedContextFile:
    if len(raw) > MAX_XLSX_SOURCE_BYTES:
        raise ContextFileContentError("context_file_too_large")
    file_id = str(row.get("id") or row.get("file_id") or "file-context")
    file_name = str(row.get("original_name") or row.get("name") or "context.xlsx")
    fact = MaterializedAttachmentFact(
        file_id=file_id,
        file_name=file_name,
        content_type=XLSX_CONTENT_TYPE,
        byte_count=len(raw),
        sha256=actual_sha256,
    )
    contract = build_attachment_preprocessing_contract(attachment_facts=[fact])
    requirements = contract.get("requirements") or []
    if len(requirements) != 1:
        raise ContextFileContentError("context_file_type_unsupported")
    try:
        requirement = AttachmentParserRequirement.model_validate(requirements[0])
        with tempfile.TemporaryDirectory(prefix="ai-platform-context-xlsx-") as directory:
            target = Path(directory) / PurePosixPath(file_name).name
            target.write_bytes(raw)
            parsed = parse_xlsx_attachment(path=target, requirement=requirement)
    except Exception as exc:
        raise ContextFileContentError("context_file_parse_failed") from exc
    rendered = json.dumps(parsed.content, ensure_ascii=False, separators=(",", ":"))
    content, truncated = _bounded_output(rendered, max_output_bytes=max_output_bytes)
    return ParsedContextFile(
        content=content,
        content_type=XLSX_CONTENT_TYPE,
        parser_id=requirement.parser_id,
        parser_version=requirement.parser_version,
        source_bytes=len(raw),
        truncated=truncated or parsed.evidence.truncated,
    )


def parse_context_file(
    row: dict[str, Any],
    raw: bytes,
    *,
    max_output_bytes: int = MAX_CONTEXT_FILE_OUTPUT_BYTES,
) -> ParsedContextFile:
    actual_sha256 = _validate_identity(row, raw)
    kind, content_type = _classify(row, raw)
    if kind == "text":
        return _parse_text(raw, content_type=content_type, max_output_bytes=max_output_bytes)
    if kind == "docx":
        return _parse_docx(raw, max_output_bytes=max_output_bytes)
    if kind == "pdf":
        return _parse_pdf(raw, max_output_bytes=max_output_bytes)
    if kind == "xlsx":
        return _parse_xlsx(
            row,
            raw,
            actual_sha256=actual_sha256,
            max_output_bytes=max_output_bytes,
        )
    raise ContextFileContentError("context_file_type_unsupported")


def validate_context_file_for_stage(row: dict[str, Any], raw: bytes) -> None:
    if len(raw) > MAX_CONTEXT_FILE_STAGE_BYTES:
        raise ContextFileContentError("context_file_too_large")
    parse_context_file(row, raw, max_output_bytes=1)
