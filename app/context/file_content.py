from __future__ import annotations

import codecs
import hashlib
import io
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile, ZipInfo

from app.context.api import ContextFileContentError


MAX_CONTEXT_FILE_STAGE_BYTES = 32 * 1024 * 1024
MAX_OPC_ARCHIVE_ENTRIES = 2_000
MAX_XLSX_ARCHIVE_ENTRY_BYTES = 8 * 1024 * 1024
MAX_XLSX_ARCHIVE_TOTAL_BYTES = 32 * 1024 * 1024
MAX_OPC_ARCHIVE_COMPRESSION_RATIO = 100
_FORBIDDEN_XML_DECLARATIONS = (b"<!DOCTYPE", b"<!ENTITY")
_FORBIDDEN_XML_DECLARATION_TEXT = tuple(
    token.decode("ascii") for token in _FORBIDDEN_XML_DECLARATIONS
)
_CFB_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_ACTIVE_CONTENT_MARKERS = ("macroenabled", "vbaproject", "activex", "oleobject")
_ACTIVE_RELATIONSHIP_TYPES = frozenset({"control", "oleobject", "vbaproject"})

XLSX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _content_type(value: object) -> str:
    return str(value or "").split(";", 1)[0].strip().casefold()


def _extension(value: object) -> str:
    name = PurePosixPath(str(value or "").replace("\\", "/")).name
    return PurePosixPath(name).suffix.casefold()


def _validate_identity(row: dict[str, Any], raw: bytes) -> None:
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


def _declared_as_xlsx(row: dict[str, Any]) -> bool:
    name = row.get("original_name") or row.get("name")
    return (
        _extension(name) == ".xlsx"
        or _content_type(row.get("content_type")) == XLSX_CONTENT_TYPE
    )


def _xml_multibyte_encoding(prefix: bytes) -> str | None:
    if prefix.startswith((codecs.BOM_UTF32_LE, codecs.BOM_UTF32_BE)):
        return "utf-32"
    if prefix.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return "utf-16"
    if prefix.startswith(b"\x00\x00\x00<"):
        return "utf-32-be"
    if prefix.startswith(b"<\x00\x00\x00"):
        return "utf-32-le"
    if prefix.startswith(b"\x00<"):
        return "utf-16-be"
    if prefix.startswith(b"<\x00"):
        return "utf-16-le"
    return None


def _validate_xml_declarations(payload: bytes, *, error_code: str) -> None:
    try:
        encoding = _xml_multibyte_encoding(payload)
        if encoding is not None:
            text = payload.decode(encoding, errors="strict").upper()
            if any(token in text for token in _FORBIDDEN_XML_DECLARATION_TEXT):
                raise ContextFileContentError(error_code)
            return
        probe = payload.removeprefix(codecs.BOM_UTF8).lstrip(b" \t\r\n")
        if b"\x00" in payload or (probe and not probe.startswith(b"<")):
            raise ContextFileContentError(error_code)
        if any(token in payload.upper() for token in _FORBIDDEN_XML_DECLARATIONS):
            raise ContextFileContentError(error_code)
    except (UnicodeError, ValueError) as exc:
        raise ContextFileContentError(error_code) from exc


def _validated_archive_entries(
    archive: ZipFile,
    *,
    invalid_code: str,
    entry_limit_code: str,
    encrypted_code: str,
    size_code: str,
    max_entries: int,
    max_entry_bytes: int,
    max_total_bytes: int,
) -> tuple[list[ZipInfo], int]:
    entries = archive.infolist()
    if len(entries) > max_entries:
        raise ContextFileContentError(entry_limit_code)
    total_bytes = 0
    seen: set[str] = set()
    for entry in entries:
        name = entry.filename.replace("\\", "/")
        trimmed = name[:-1] if name.endswith("/") else name
        parts = trimmed.split("/")
        windows_path = PureWindowsPath(entry.filename)
        normalized = trimmed.casefold()
        if (
            not trimmed
            or name.startswith("/")
            or windows_path.is_absolute()
            or bool(windows_path.drive)
            or normalized in seen
            or any(part in {"", ".", ".."} for part in parts)
        ):
            raise ContextFileContentError(invalid_code)
        seen.add(normalized)
        if entry.flag_bits & 0x1:
            raise ContextFileContentError(encrypted_code)
        if entry.file_size < 0 or entry.file_size > max_entry_bytes:
            raise ContextFileContentError(size_code)
        total_bytes += entry.file_size
        if total_bytes > max_total_bytes:
            raise ContextFileContentError(size_code)
        if entry.compress_size == 0:
            if entry.file_size > 0:
                raise ContextFileContentError(size_code)
        elif entry.file_size / entry.compress_size > MAX_OPC_ARCHIVE_COMPRESSION_RATIO:
            raise ContextFileContentError(size_code)
    return entries, total_bytes


def _validate_office_relationship_xml(
    payload: bytes,
    *,
    invalid_code: str,
    active_code: str,
) -> None:
    _validate_xml_declarations(payload, error_code=invalid_code)
    try:
        root = ElementTree.fromstring(payload)
    except ElementTree.ParseError as exc:
        raise ContextFileContentError(invalid_code) from exc
    for relationship in root:
        relationship_type = str(relationship.attrib.get("Type") or "").rsplit("/", 1)[-1]
        if relationship_type.casefold() in _ACTIVE_RELATIONSHIP_TYPES:
            raise ContextFileContentError(active_code)


def _validate_content_types_xml(
    payload: bytes,
    *,
    invalid_code: str,
    active_code: str,
) -> None:
    _validate_xml_declarations(payload, error_code=invalid_code)
    try:
        root = ElementTree.fromstring(payload)
    except ElementTree.ParseError as exc:
        raise ContextFileContentError(invalid_code) from exc
    for node in root:
        content_type = str(node.attrib.get("ContentType") or "").casefold()
        if any(marker in content_type for marker in _ACTIVE_CONTENT_MARKERS):
            raise ContextFileContentError(active_code)


def _validate_xlsx_xml_entry(archive: ZipFile, entry: Any) -> None:
    normalized_name = str(entry.filename).replace("\\", "/").casefold()
    if not normalized_name.endswith((".xml", ".rels")):
        return
    try:
        payload = archive.read(entry)
    except (BadZipFile, OSError, RuntimeError, ValueError) as exc:
        raise ContextFileContentError("context_file_xlsx_archive_invalid") from exc
    if normalized_name.endswith(".rels"):
        _validate_office_relationship_xml(
            payload,
            invalid_code="context_file_xlsx_archive_invalid",
            active_code="context_file_xlsx_archive_invalid",
        )
    else:
        _validate_xml_declarations(payload, error_code="context_file_xlsx_archive_invalid")


def _validate_xlsx_archive_security(raw: bytes) -> None:
    try:
        archive = ZipFile(io.BytesIO(raw))
    except (BadZipFile, ValueError) as exc:
        raise ContextFileContentError("context_file_xlsx_archive_invalid") from exc
    try:
        entries, _ = _validated_archive_entries(
            archive,
            invalid_code="context_file_xlsx_archive_invalid",
            entry_limit_code="context_file_xlsx_archive_invalid",
            encrypted_code="context_file_xlsx_archive_invalid",
            size_code="context_file_xlsx_archive_invalid",
            max_entries=MAX_OPC_ARCHIVE_ENTRIES,
            max_entry_bytes=MAX_XLSX_ARCHIVE_ENTRY_BYTES,
            max_total_bytes=MAX_XLSX_ARCHIVE_TOTAL_BYTES,
        )
        seen = {entry.filename.replace("\\", "/").casefold() for entry in entries}
        for entry in entries:
            normalized_name = entry.filename.replace("\\", "/").casefold()
            if normalized_name.endswith("vbaproject.bin") or normalized_name.startswith(
                "xl/activex/"
            ):
                raise ContextFileContentError("context_file_xlsx_archive_invalid")
            with archive.open(entry) as payload:
                if payload.read(len(_CFB_MAGIC)) == _CFB_MAGIC:
                    raise ContextFileContentError("context_file_xlsx_archive_invalid")
            _validate_xlsx_xml_entry(archive, entry)
        if not {"[content_types].xml", "_rels/.rels", "xl/workbook.xml"}.issubset(seen):
            raise ContextFileContentError("context_file_xlsx_archive_invalid")
        _validate_content_types_xml(
            archive.read("[Content_Types].xml"),
            invalid_code="context_file_xlsx_archive_invalid",
            active_code="context_file_xlsx_archive_invalid",
        )
    except ContextFileContentError:
        raise
    except (BadZipFile, KeyError, OSError, RuntimeError, ValueError) as exc:
        raise ContextFileContentError("context_file_xlsx_archive_invalid") from exc
    finally:
        archive.close()


def validate_context_file_for_stage(row: dict[str, Any], raw: bytes) -> None:
    if len(raw) > MAX_CONTEXT_FILE_STAGE_BYTES:
        raise ContextFileContentError("context_file_too_large")
    _validate_identity(row, raw)
    if _declared_as_xlsx(row):
        _validate_xlsx_archive_security(raw)
