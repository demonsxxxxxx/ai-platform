from __future__ import annotations

import hashlib
import io
import json
from dataclasses import dataclass
from datetime import date, datetime, time
from pathlib import Path, PurePosixPath
from typing import Any, Literal
from zipfile import BadZipFile, ZipFile

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.context_manifest import CONTEXT_MANIFEST_SCHEMA_VERSION, utf8_token_estimate
from app.validation import assert_safe_id


ATTACHMENT_PREPROCESSING_SCHEMA_VERSION = "ai-platform.attachment-preprocessing.v1"
ATTACHMENT_CONTEXT_SCHEMA_VERSION = "ai-platform.attachment-context.v1"
XLSX_PARSER_ID = "ai-platform.xlsx.openpyxl"
XLSX_PARSER_VERSION = "1"
XLSX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

MAX_XLSX_FILE_BYTES = 1024 * 1024
MAX_XLSX_SHEETS = 16
MAX_XLSX_ROWS_PER_SHEET = 100
MAX_XLSX_COLUMNS_PER_SHEET = 32
MAX_XLSX_CELLS = 2048
MAX_XLSX_CELL_CHARS = 256
MAX_XLSX_PROMPT_CHARS = 16_000
MAX_XLSX_PROMPT_TOKENS = 24_000
MAX_XLSX_ZIP_ENTRIES = 2000
MAX_XLSX_ZIP_ENTRY_BYTES = 8 * 1024 * 1024
MAX_XLSX_ZIP_TOTAL_BYTES = 32 * 1024 * 1024
MAX_XLSX_ZIP_COMPRESSION_RATIO = 100

_SUPPORTED_XLSX_EXTENSIONS = frozenset({".xlsx"})
_UNSUPPORTED_WORKBOOK_EXTENSIONS = frozenset({".xls", ".xlsb", ".xlsm", ".ods"})
_UNSUPPORTED_WORKBOOK_CONTENT_TYPES = frozenset(
    {
        "application/vnd.ms-excel",
        "application/vnd.ms-excel.sheet.binary.macroenabled.12",
        "application/vnd.ms-excel.sheet.macroenabled.12",
        "application/vnd.oasis.opendocument.spreadsheet",
    }
)


class AttachmentPreprocessingError(ValueError):
    """Fail-closed attachment preprocessing error with a stable machine code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class AttachmentParserRequirement(BaseModel):
    """Server-owned preprocessing requirement for one run attachment."""

    model_config = ConfigDict(extra="forbid")

    file_id: str
    file_name: str
    extension: str
    content_type: str
    parser_id: str
    parser_version: str
    supported: bool = True
    max_bytes: int = Field(ge=1)
    expected_byte_count: int | None = Field(default=None, ge=0)
    expected_sha256: str | None = None

    @field_validator("file_id")
    @classmethod
    def validate_file_id(cls, value: str):
        return assert_safe_id(value, "file_id")

    @field_validator("expected_sha256")
    @classmethod
    def validate_expected_sha256(cls, value: str | None):
        if value is None:
            return None
        normalized = str(value).lower()
        if len(normalized) != 64 or any(character not in "0123456789abcdef" for character in normalized):
            raise ValueError("expected_sha256 must be 64 lowercase hexadecimal characters")
        return normalized


class MaterializedAttachmentFact(BaseModel):
    """Ordered server fact captured from bytes fetched for one exact file ID."""

    model_config = ConfigDict(extra="forbid")

    file_id: str
    file_name: str
    content_type: str = ""
    byte_count: int = Field(ge=0)
    sha256: str

    @field_validator("file_id")
    @classmethod
    def validate_file_id(cls, value: str):
        return assert_safe_id(value, "file_id")

    @field_validator("sha256")
    @classmethod
    def validate_sha256(cls, value: str):
        normalized = str(value or "").lower()
        if len(normalized) != 64 or any(character not in "0123456789abcdef" for character in normalized):
            raise ValueError("sha256 must be 64 lowercase hexadecimal characters")
        return normalized


class AttachmentParserEvidence(BaseModel):
    """Bounded positive evidence emitted by a platform attachment parser."""

    model_config = ConfigDict(extra="forbid")

    file_id: str
    parser_id: str
    parser_version: str
    content_type: str
    extension: str
    byte_count: int = Field(ge=0)
    sha256: str
    sheet_count: int = Field(ge=0)
    sheets_processed: int = Field(ge=0)
    cells_examined: int = Field(ge=0)
    nonempty_cells: int = Field(ge=0)
    rows_emitted: int = Field(ge=0)
    truncated: bool
    status: Literal["parsed"]

    @field_validator("file_id")
    @classmethod
    def validate_file_id(cls, value: str):
        return assert_safe_id(value, "file_id")

    @field_validator("sha256")
    @classmethod
    def validate_sha256(cls, value: str):
        normalized = str(value or "").lower()
        if len(normalized) != 64 or any(character not in "0123456789abcdef" for character in normalized):
            raise ValueError("sha256 must be 64 lowercase hexadecimal characters")
        return normalized


class ParsedAttachmentContext(BaseModel):
    """Typed, bounded attachment content forwarded separately from user text."""

    model_config = ConfigDict(extra="forbid")

    evidence: AttachmentParserEvidence
    content: dict[str, Any]


@dataclass(frozen=True)
class AttachmentParserSpec:
    """Immutable platform-owned parser registration; never loaded from a Skill."""

    parser_id: str
    parser_version: str
    extensions: frozenset[str]
    content_types: frozenset[str]
    max_bytes: int


XLSX_PARSER_SPEC = AttachmentParserSpec(
    parser_id=XLSX_PARSER_ID,
    parser_version=XLSX_PARSER_VERSION,
    extensions=_SUPPORTED_XLSX_EXTENSIONS,
    content_types=frozenset({XLSX_CONTENT_TYPE}),
    max_bytes=MAX_XLSX_FILE_BYTES,
)
ATTACHMENT_PARSER_REGISTRY = (XLSX_PARSER_SPEC,)


def _normalized_extension(file_name: object) -> str:
    safe_name = PurePosixPath(str(file_name or "").replace("\\", "/")).name
    return Path(safe_name).suffix.casefold()


def _normalized_content_type(content_type: object) -> str:
    return str(content_type or "").split(";", 1)[0].strip().casefold()


def parser_spec_for_attachment(
    *,
    file_name: object,
    content_type: object = "",
) -> AttachmentParserSpec | None:
    """Resolve a parser only from the immutable platform registry."""

    extension = _normalized_extension(file_name)
    normalized_type = _normalized_content_type(content_type)
    for spec in ATTACHMENT_PARSER_REGISTRY:
        if extension in spec.extensions or normalized_type in spec.content_types:
            return spec
    return None


def is_known_binary_workbook(*, file_name: object, content_type: object = "") -> bool:
    """Return whether a file must be staged/parsed instead of text-decoded."""

    extension = _normalized_extension(file_name)
    normalized_type = _normalized_content_type(content_type)
    return bool(
        parser_spec_for_attachment(file_name=file_name, content_type=content_type)
        or extension in _UNSUPPORTED_WORKBOOK_EXTENSIONS
        or normalized_type in _UNSUPPORTED_WORKBOOK_CONTENT_TYPES
    )


def dispatched_context_file_ids(manifest: object) -> frozenset[str]:
    """Return the immutable exact file-ID authority dispatched to the sandbox."""

    if not isinstance(manifest, dict) or manifest.get("schema_version") != CONTEXT_MANIFEST_SCHEMA_VERSION:
        return frozenset()
    rows = manifest.get("files")
    if not isinstance(rows, list):
        return frozenset()
    return frozenset(
        str(row.get("file_id") or "").strip()
        for row in rows
        if isinstance(row, dict) and str(row.get("file_id") or "").strip()
    )


def build_attachment_preprocessing_contract(
    *,
    file_ids: list[str] | None = None,
    file_names: list[str] | None = None,
    content_types: list[str] | None = None,
    attachment_facts: list[MaterializedAttachmentFact | dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build server-owned parser requirements from ordered attachment facts."""

    requirements: list[AttachmentParserRequirement] = []
    normalized_facts: list[MaterializedAttachmentFact | None]
    if attachment_facts is not None:
        try:
            normalized_facts = [
                fact
                if isinstance(fact, MaterializedAttachmentFact)
                else MaterializedAttachmentFact.model_validate(fact)
                for fact in attachment_facts
            ]
        except Exception as exc:
            raise AttachmentPreprocessingError("attachment_materialized_fact_invalid") from exc
        ordered_file_ids = [fact.file_id for fact in normalized_facts if fact is not None]
        ordered_file_names = [fact.file_name for fact in normalized_facts if fact is not None]
        ordered_content_types = [fact.content_type for fact in normalized_facts if fact is not None]
    else:
        ordered_file_ids = list(file_ids or [])
        ordered_file_names = list(file_names or [])
        ordered_content_types = list(content_types or [])
        normalized_facts = [None for _name in ordered_file_names]
    for index, file_name in enumerate(ordered_file_names):
        extension = _normalized_extension(file_name)
        declared_content_type = (
            _normalized_content_type(ordered_content_types[index])
            if index < len(ordered_content_types)
            else ""
        )
        spec = parser_spec_for_attachment(
            file_name=file_name,
            content_type=declared_content_type,
        )
        if (
            spec is None
            and extension not in _UNSUPPORTED_WORKBOOK_EXTENSIONS
            and declared_content_type not in _UNSUPPORTED_WORKBOOK_CONTENT_TYPES
        ):
            continue
        if index >= len(ordered_file_ids):
            raise AttachmentPreprocessingError("attachment_parser_file_mapping_invalid")
        fact = normalized_facts[index]
        requirement = AttachmentParserRequirement(
            file_id=ordered_file_ids[index],
            file_name=PurePosixPath(str(file_name).replace("\\", "/")).name,
            extension=extension,
            content_type=(
                declared_content_type
                or (XLSX_CONTENT_TYPE if spec is not None else "application/octet-stream")
            ),
            parser_id=spec.parser_id if spec is not None else "unsupported",
            parser_version=spec.parser_version if spec is not None else "0",
            supported=spec is not None,
            max_bytes=spec.max_bytes if spec is not None else 1,
            expected_byte_count=fact.byte_count if fact is not None else None,
            expected_sha256=fact.sha256 if fact is not None else None,
        )
        requirements.append(requirement)
    return {
        "schema_version": ATTACHMENT_PREPROCESSING_SCHEMA_VERSION,
        "requirements": [requirement.model_dump(mode="json") for requirement in requirements],
    }


def attachment_requirements_from_contract(value: object) -> list[AttachmentParserRequirement]:
    """Validate that a runtime contract exactly matches the server registry."""

    if not isinstance(value, dict):
        return []
    if value.get("schema_version") != ATTACHMENT_PREPROCESSING_SCHEMA_VERSION:
        raise AttachmentPreprocessingError("attachment_preprocessing_contract_invalid")
    raw_requirements = value.get("requirements")
    if not isinstance(raw_requirements, list):
        raise AttachmentPreprocessingError("attachment_preprocessing_contract_invalid")
    requirements: list[AttachmentParserRequirement] = []
    seen_file_ids: set[str] = set()
    for raw in raw_requirements:
        try:
            requirement = AttachmentParserRequirement.model_validate(raw)
        except Exception as exc:
            raise AttachmentPreprocessingError("attachment_preprocessing_contract_invalid") from exc
        if requirement.file_id in seen_file_ids:
            raise AttachmentPreprocessingError("attachment_preprocessing_contract_invalid")
        seen_file_ids.add(requirement.file_id)
        rebuilt = build_attachment_preprocessing_contract(
            file_ids=[requirement.file_id],
            file_names=[requirement.file_name],
            content_types=[requirement.content_type],
        )["requirements"]
        if len(rebuilt) != 1:
            raise AttachmentPreprocessingError("attachment_preprocessing_contract_invalid")
        expected = rebuilt[0]
        actual = requirement.model_dump(mode="json")
        for key in (
            "file_id",
            "file_name",
            "extension",
            "content_type",
            "parser_id",
            "parser_version",
            "supported",
            "max_bytes",
        ):
            if actual[key] != expected[key]:
                raise AttachmentPreprocessingError("attachment_preprocessing_contract_invalid")
        if (requirement.expected_byte_count is None) != (requirement.expected_sha256 is None):
            raise AttachmentPreprocessingError("attachment_preprocessing_contract_invalid")
        requirements.append(requirement)
    return requirements


def _validate_xlsx_archive(raw: bytes) -> None:
    try:
        archive = ZipFile(io.BytesIO(raw))
    except (BadZipFile, ValueError) as exc:
        raise AttachmentPreprocessingError("xlsx_parse_failed") from exc
    total_bytes = 0
    try:
        entries = archive.infolist()
        if len(entries) > MAX_XLSX_ZIP_ENTRIES:
            raise AttachmentPreprocessingError("xlsx_archive_too_large")
        for entry in entries:
            normalized_name = entry.filename.replace("\\", "/").casefold()
            if entry.flag_bits & 0x1:
                raise AttachmentPreprocessingError("xlsx_encrypted_unsupported")
            if normalized_name.endswith("vbaproject.bin"):
                raise AttachmentPreprocessingError("xlsx_macros_unsupported")
            if entry.file_size < 0 or entry.file_size > MAX_XLSX_ZIP_ENTRY_BYTES:
                raise AttachmentPreprocessingError("xlsx_archive_too_large")
            total_bytes += entry.file_size
            if total_bytes > MAX_XLSX_ZIP_TOTAL_BYTES:
                raise AttachmentPreprocessingError("xlsx_archive_too_large")
            if entry.compress_size == 0:
                if entry.file_size > 0:
                    raise AttachmentPreprocessingError("xlsx_archive_too_large")
            elif entry.file_size / entry.compress_size > MAX_XLSX_ZIP_COMPRESSION_RATIO:
                raise AttachmentPreprocessingError("xlsx_archive_too_large")
    finally:
        archive.close()


def _bounded_cell_payload(cell: Any) -> tuple[dict[str, Any] | None, bool]:
    value = getattr(cell, "value", None)
    if value is None:
        return None, False
    if isinstance(value, (datetime, date, time)):
        text = value.isoformat()
        kind = "datetime"
    elif isinstance(value, bool):
        return {"column": int(cell.column), "kind": "boolean", "value": value}, False
    elif isinstance(value, (int, float)):
        return {"column": int(cell.column), "kind": "number", "value": value}, False
    else:
        text = str(value)
        kind = "formula" if getattr(cell, "data_type", "") == "f" or text.startswith("=") else "text"
    truncated = len(text) > MAX_XLSX_CELL_CHARS
    return {
        "column": int(cell.column),
        "kind": kind,
        "value": text[:MAX_XLSX_CELL_CHARS],
    }, truncated


def _prompt_content_within_caps(content: dict[str, Any]) -> bool:
    rendered = json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return len(rendered) <= MAX_XLSX_PROMPT_CHARS and utf8_token_estimate(rendered) <= MAX_XLSX_PROMPT_TOKENS


def parse_xlsx_attachment(
    *,
    path: Path,
    requirement: AttachmentParserRequirement,
) -> ParsedAttachmentContext:
    """Parse one broker-staged XLSX with deterministic, bounded read-only rules."""

    spec = parser_spec_for_attachment(
        file_name=requirement.file_name,
        content_type=requirement.content_type,
    )
    if not requirement.supported or spec is None:
        raise AttachmentPreprocessingError("attachment_parser_unsupported")
    if (
        requirement.parser_id != spec.parser_id
        or requirement.parser_version != spec.parser_version
        or requirement.max_bytes != spec.max_bytes
        or (
            requirement.extension not in spec.extensions
            and _normalized_content_type(requirement.content_type) not in spec.content_types
        )
    ):
        raise AttachmentPreprocessingError("attachment_preprocessing_contract_invalid")
    if path.name != requirement.file_name or path.suffix.casefold() != requirement.extension:
        raise AttachmentPreprocessingError("attachment_parser_staged_file_mismatch")
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise AttachmentPreprocessingError("attachment_parser_staged_file_invalid") from exc
    if len(raw) > requirement.max_bytes:
        raise AttachmentPreprocessingError("attachment_parser_file_too_large")
    actual_sha256 = hashlib.sha256(raw).hexdigest()
    if requirement.expected_byte_count is not None and len(raw) != requirement.expected_byte_count:
        raise AttachmentPreprocessingError("attachment_parser_staged_file_mismatch")
    if requirement.expected_sha256 is not None and actual_sha256 != requirement.expected_sha256:
        raise AttachmentPreprocessingError("attachment_parser_staged_file_mismatch")
    _validate_xlsx_archive(raw)

    try:
        from openpyxl import load_workbook

        workbook = load_workbook(
            io.BytesIO(raw),
            read_only=True,
            data_only=False,
            keep_links=False,
        )
    except Exception as exc:
        raise AttachmentPreprocessingError("xlsx_parse_failed") from exc

    content: dict[str, Any] = {
        "schema_version": ATTACHMENT_CONTEXT_SCHEMA_VERSION,
        "file_id": requirement.file_id,
        "workbook": {"sheets": []},
    }
    cells_examined = 0
    nonempty_cells = 0
    rows_emitted = 0
    sheets_processed = 0
    truncated = False
    stop_all = False
    try:
        sheet_names = list(workbook.sheetnames)
        if len(sheet_names) > MAX_XLSX_SHEETS:
            truncated = True
        for sheet_name in sheet_names[:MAX_XLSX_SHEETS]:
            worksheet = workbook[sheet_name]
            max_row = max(0, int(worksheet.max_row or 0))
            max_column = max(0, int(worksheet.max_column or 0))
            if len(str(sheet_name)) > MAX_XLSX_CELL_CHARS:
                truncated = True
            if max_row > MAX_XLSX_ROWS_PER_SHEET or max_column > MAX_XLSX_COLUMNS_PER_SHEET:
                truncated = True
            sheet_payload: dict[str, Any] = {
                "name": str(sheet_name)[:MAX_XLSX_CELL_CHARS],
                "max_row": max_row,
                "max_column": max_column,
                "rows": [],
            }
            content["workbook"]["sheets"].append(sheet_payload)
            sheets_processed += 1
            if not _prompt_content_within_caps(content):
                content["workbook"]["sheets"].pop()
                sheets_processed -= 1
                truncated = True
                break
            if max_row == 0 or max_column == 0:
                continue
            for row_index, row in enumerate(
                worksheet.iter_rows(
                    min_row=1,
                    max_row=min(max_row, MAX_XLSX_ROWS_PER_SHEET),
                    max_col=min(max_column, MAX_XLSX_COLUMNS_PER_SHEET),
                ),
                start=1,
            ):
                row_cells: list[dict[str, Any]] = []
                for cell in row:
                    if cells_examined >= MAX_XLSX_CELLS:
                        truncated = True
                        stop_all = True
                        break
                    cells_examined += 1
                    cell_payload, cell_truncated = _bounded_cell_payload(cell)
                    truncated = truncated or cell_truncated
                    if cell_payload is not None:
                        nonempty_cells += 1
                        row_cells.append(cell_payload)
                if row_cells:
                    row_payload = {"row": row_index, "cells": row_cells}
                    sheet_payload["rows"].append(row_payload)
                    if not _prompt_content_within_caps(content):
                        sheet_payload["rows"].pop()
                        truncated = True
                        stop_all = True
                        break
                    rows_emitted += 1
                if stop_all:
                    break
            if stop_all:
                break
    except Exception as exc:
        raise AttachmentPreprocessingError("xlsx_parse_failed") from exc
    finally:
        workbook.close()

    content["workbook"]["sheet_count"] = len(sheet_names)
    content["workbook"]["truncated"] = truncated
    if not _prompt_content_within_caps(content):
        raise AttachmentPreprocessingError("attachment_parser_prompt_too_large")
    evidence = AttachmentParserEvidence(
        file_id=requirement.file_id,
        parser_id=spec.parser_id,
        parser_version=spec.parser_version,
        content_type=requirement.content_type,
        extension=requirement.extension,
        byte_count=len(raw),
        sha256=actual_sha256,
        sheet_count=len(sheet_names),
        sheets_processed=sheets_processed,
        cells_examined=cells_examined,
        nonempty_cells=nonempty_cells,
        rows_emitted=rows_emitted,
        truncated=truncated,
        status="parsed",
    )
    return ParsedAttachmentContext(evidence=evidence, content=content)


def validate_required_parser_evidence(
    *,
    requirements: list[AttachmentParserRequirement],
    evidence: object,
) -> tuple[bool, str]:
    """Require one exact positive evidence record for every supported workbook."""

    if any(not requirement.supported for requirement in requirements):
        return False, "attachment_parser_unsupported"
    if not requirements:
        return True, ""
    if not isinstance(evidence, list):
        return False, "attachment_parser_evidence_missing"
    parsed_by_file: dict[str, AttachmentParserEvidence] = {}
    for raw in evidence:
        try:
            item = AttachmentParserEvidence.model_validate(raw)
        except Exception:
            return False, "attachment_parser_evidence_invalid"
        if item.file_id in parsed_by_file:
            return False, "attachment_parser_evidence_invalid"
        parsed_by_file[item.file_id] = item
    for requirement in requirements:
        item = parsed_by_file.get(requirement.file_id)
        if item is None:
            return False, "attachment_parser_evidence_missing"
        if (
            item.parser_id != requirement.parser_id
            or item.parser_version != requirement.parser_version
            or item.content_type != requirement.content_type
            or item.extension != requirement.extension
            or item.byte_count > requirement.max_bytes
            or (
                requirement.expected_byte_count is not None
                and item.byte_count != requirement.expected_byte_count
            )
            or (
                requirement.expected_sha256 is not None
                and item.sha256 != requirement.expected_sha256
            )
            or item.sheets_processed > item.sheet_count
            or item.nonempty_cells > item.cells_examined
        ):
            return False, "attachment_parser_evidence_mismatch"
    return True, ""
