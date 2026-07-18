"""Versioned, server-owned bounded previews for authorized files.

This module is deliberately a thin seam for routes: callers provide already
authorized bytes and file metadata, and receive a public-safe DTO.  The XLSX
implementation delegates all archive and workbook interpretation to the
existing attachment parser; it never implements a second OOXML parser.
"""

from __future__ import annotations

import hashlib
import math
import multiprocessing
import os
from pathlib import Path, PurePosixPath
import tempfile
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.file_parser_contracts import (
    AttachmentParserRequirement,
    AttachmentPreprocessingError,
    ParsedAttachmentContext,
    XLSX_CONTENT_TYPE,
    parse_xlsx_attachment,
    parser_spec_for_attachment,
)


FILE_PREVIEW_SCHEMA_VERSION = "ai-platform.file-preview.v1"
XLSX_PREVIEW_TIMEOUT_SECONDS = 5.0
XLSX_PREVIEW_MEMORY_LIMIT_BYTES = 192 * 1024 * 1024
_XLSX_PREVIEW_WARNINGS = (
    "styles_not_rendered",
    "charts_not_rendered",
    "formulas_not_recalculated",
    "external_links_not_resolved",
)

PreviewFailureCode = Literal[
    "xlsx_preview_encrypted_unsupported",
    "xlsx_preview_failed",
    "xlsx_preview_file_too_large",
    "xlsx_preview_limits_exceeded",
    "xlsx_preview_macros_unsupported",
    "xlsx_preview_timeout",
    "xlsx_preview_unavailable",
    "xlsx_preview_unsupported",
]


class XlsxPreviewCell(BaseModel):
    """One bounded, display-safe cell emitted by the authoritative parser."""

    model_config = ConfigDict(extra="forbid")

    column: int = Field(ge=1)
    kind: Literal["boolean", "datetime", "formula", "number", "text"]
    value: str | int | float | bool


class XlsxPreviewRow(BaseModel):
    """A sparse worksheet row whose cell columns are one-based."""

    model_config = ConfigDict(extra="forbid")

    row: int = Field(ge=1)
    cells: list[XlsxPreviewCell] = Field(default_factory=list)


class XlsxPreviewSheet(BaseModel):
    """A bounded tabular worksheet projection, not an Office rendering model."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    rows: list[XlsxPreviewRow] = Field(default_factory=list)


class XlsxPreviewContent(BaseModel):
    """Public table data rendered by the browser without workbook parsing."""

    model_config = ConfigDict(extra="forbid")

    sheets: list[XlsxPreviewSheet] = Field(default_factory=list)
    sheet_count: int = Field(ge=0)


class FilePreviewError(BaseModel):
    """Stable public-safe failure detail; never contains parser implementation text."""

    model_config = ConfigDict(extra="forbid")

    code: PreviewFailureCode


class XlsxPreviewResponse(BaseModel):
    """Versioned DTO for the server-owned XLSX table preview."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[FILE_PREVIEW_SCHEMA_VERSION] = FILE_PREVIEW_SCHEMA_VERSION
    kind: Literal["xlsx_table"] = "xlsx_table"
    status: Literal["ready", "truncated", "failed"]
    source_sha256: str = Field(min_length=64, max_length=64)
    parser_id: str
    parser_version: str
    content: XlsxPreviewContent | None = None
    truncated: bool = False
    warnings: list[str] = Field(default_factory=list)
    error: FilePreviewError | None = None

    @model_validator(mode="after")
    def validate_status_shape(self) -> "XlsxPreviewResponse":
        if self.status == "failed":
            if self.content is not None or self.truncated or self.error is None:
                raise ValueError("failed XLSX previews require only a public error")
            return self
        if self.content is None or self.error is not None:
            raise ValueError("successful XLSX previews require content without an error")
        if self.status == "truncated" and not self.truncated:
            raise ValueError("truncated XLSX previews must declare truncation")
        if self.status == "ready" and self.truncated:
            raise ValueError("ready XLSX previews cannot declare truncation")
        return self


def is_xlsx_preview_request(*, file_name: object, content_type: object) -> bool:
    """Return whether this exact metadata is eligible for the XLSX DTO path."""

    normalized_name = PurePosixPath(str(file_name or "").replace("\\", "/")).name
    normalized_type = str(content_type or "").split(";", 1)[0].strip().casefold()
    return normalized_name.casefold().endswith(".xlsx") and normalized_type == XLSX_CONTENT_TYPE


def build_xlsx_preview(
    *,
    raw: bytes,
    file_id: str,
    file_name: str,
    content_type: str,
    expected_sha256: str | None = None,
    expected_byte_count: int | None = None,
    timeout_seconds: float = XLSX_PREVIEW_TIMEOUT_SECONDS,
) -> XlsxPreviewResponse:
    """Invoke the registered XLSX parser once in a bounded child process.

    The route must complete authorization before calling this function.  The
    returned DTO intentionally carries no storage key, source path, XML, or
    parser stack trace.
    """

    source_sha256 = hashlib.sha256(raw).hexdigest()
    spec = parser_spec_for_attachment(file_name=file_name, content_type=content_type)
    if spec is None or not is_xlsx_preview_request(
        file_name=file_name,
        content_type=content_type,
    ):
        return _failed_preview(
            source_sha256=source_sha256,
            parser_id=spec.parser_id if spec else "",
            parser_version=spec.parser_version if spec else "",
            code="xlsx_preview_unsupported",
        )

    if len(raw) > spec.max_bytes:
        return _failed_preview(
            source_sha256=source_sha256,
            parser_id=spec.parser_id,
            parser_version=spec.parser_version,
            code="xlsx_preview_file_too_large",
        )

    try:
        requirement = AttachmentParserRequirement(
            file_id=file_id,
            file_name=PurePosixPath(file_name.replace("\\", "/")).name,
            extension=Path(file_name).suffix.casefold(),
            content_type=content_type,
            parser_id=spec.parser_id,
            parser_version=spec.parser_version,
            max_bytes=spec.max_bytes,
            expected_byte_count=expected_byte_count,
            expected_sha256=expected_sha256,
        )
    except (TypeError, ValueError):
        return _failed_preview(
            source_sha256=source_sha256,
            parser_id=spec.parser_id,
            parser_version=spec.parser_version,
            code="xlsx_preview_unavailable",
        )
    child_result = _invoke_isolated_xlsx_parser(
        raw=raw,
        requirement=requirement.model_dump(mode="json"),
        timeout_seconds=timeout_seconds,
    )
    if child_result["status"] != "parsed":
        return _failed_preview(
            source_sha256=source_sha256,
            parser_id=spec.parser_id,
            parser_version=spec.parser_version,
            code=child_result["code"],
        )

    try:
        parsed = ParsedAttachmentContext.model_validate(child_result["parsed"])
    except (TypeError, ValueError):
        return _failed_preview(
            source_sha256=source_sha256,
            parser_id=spec.parser_id,
            parser_version=spec.parser_version,
            code="xlsx_preview_unavailable",
        )
    if parsed.evidence.sha256 != source_sha256:
        return _failed_preview(
            source_sha256=source_sha256,
            parser_id=spec.parser_id,
            parser_version=spec.parser_version,
            code="xlsx_preview_unavailable",
        )
    return _preview_from_parsed_context(parsed)


def _invoke_isolated_xlsx_parser(
    *,
    raw: bytes,
    requirement: dict[str, Any],
    timeout_seconds: float,
) -> dict[str, Any]:
    """Run exactly one parser call and reap the child on every parent outcome."""

    context = multiprocessing.get_context("spawn")
    receive_conn, send_conn = context.Pipe(duplex=False)
    process = context.Process(
        target=_parse_xlsx_preview_child,
        args=(send_conn, raw, requirement, timeout_seconds),
        daemon=True,
    )
    started = False
    try:
        process.start()
        started = True
        send_conn.close()
        if not receive_conn.poll(timeout_seconds):
            _stop_process(process)
            return {"status": "failed", "code": "xlsx_preview_timeout"}
        try:
            result = receive_conn.recv()
        except (EOFError, OSError):
            result = {"status": "failed", "code": "xlsx_preview_unavailable"}
        process.join(timeout=0.5)
        if process.is_alive():
            _stop_process(process)
            return {"status": "failed", "code": "xlsx_preview_unavailable"}
        if not isinstance(result, dict):
            return {"status": "failed", "code": "xlsx_preview_unavailable"}
        return result
    except (OSError, ValueError):
        return {"status": "failed", "code": "xlsx_preview_unavailable"}
    finally:
        receive_conn.close()
        send_conn.close()
        if started and process.is_alive():
            _stop_process(process)
        elif started:
            process.join(timeout=0)


def _stop_process(process: multiprocessing.Process) -> None:
    """Terminate, then kill if needed, so timed-out previews never leak a child."""

    process.terminate()
    process.join(timeout=0.5)
    if process.is_alive():
        process.kill()
        process.join(timeout=0.5)


def _parse_xlsx_preview_child(
    send_conn: Any,
    raw: bytes,
    requirement_payload: dict[str, Any],
    timeout_seconds: float,
) -> None:
    """Child entrypoint: constrain resources, stage private bytes, parse once."""

    try:
        _apply_child_resource_limits(timeout_seconds)
        requirement = AttachmentParserRequirement.model_validate(requirement_payload)
        with tempfile.TemporaryDirectory(prefix="ai-platform-xlsx-preview-") as directory:
            staged_path = Path(directory) / requirement.file_name
            staged_path.write_bytes(raw)
            parsed = parse_xlsx_attachment(path=staged_path, requirement=requirement)
        send_conn.send({"status": "parsed", "parsed": parsed.model_dump(mode="json")})
    except AttachmentPreprocessingError as exc:
        send_conn.send({"status": "failed", "code": _public_failure_code(exc.code)})
    except Exception:
        send_conn.send({"status": "failed", "code": "xlsx_preview_unavailable"})
    finally:
        send_conn.close()


def _apply_child_resource_limits(timeout_seconds: float) -> None:
    """Apply POSIX memory/CPU caps when available; wall time is parent-enforced."""

    if os.name == "nt":
        return
    try:
        import resource

        resource.setrlimit(
            resource.RLIMIT_AS,
            (XLSX_PREVIEW_MEMORY_LIMIT_BYTES, XLSX_PREVIEW_MEMORY_LIMIT_BYTES),
        )
        cpu_seconds = max(1, math.ceil(timeout_seconds))
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds + 1))
    except (ImportError, OSError, ValueError):
        # The parent deadline and child reaping remain mandatory when a host
        # cannot expose POSIX limits (for example on a development workstation).
        return


def _public_failure_code(value: str) -> PreviewFailureCode:
    """Collapse parser internals into the stable error vocabulary exposed to UI."""

    if value == "xlsx_encrypted_unsupported":
        return "xlsx_preview_encrypted_unsupported"
    if value == "xlsx_macros_unsupported":
        return "xlsx_preview_macros_unsupported"
    if value in {
        "attachment_parser_file_too_large",
        "xlsx_archive_too_large",
        "xlsx_cell_limit_exceeded",
        "attachment_parser_prompt_too_large",
    }:
        return "xlsx_preview_limits_exceeded"
    if value == "attachment_parser_unsupported":
        return "xlsx_preview_unsupported"
    return "xlsx_preview_failed"


def _failed_preview(
    *,
    source_sha256: str,
    parser_id: str,
    parser_version: str,
    code: PreviewFailureCode,
) -> XlsxPreviewResponse:
    return XlsxPreviewResponse(
        status="failed",
        source_sha256=source_sha256,
        parser_id=parser_id,
        parser_version=parser_version,
        error=FilePreviewError(code=code),
    )


def _preview_from_parsed_context(parsed: ParsedAttachmentContext) -> XlsxPreviewResponse:
    """Adapt one parser result to the separate, browser-safe presentation DTO."""

    workbook = parsed.content.get("workbook")
    raw_sheets = workbook.get("sheets") if isinstance(workbook, dict) else None
    if not isinstance(raw_sheets, list):
        return _failed_preview(
            source_sha256=parsed.evidence.sha256,
            parser_id=parsed.evidence.parser_id,
            parser_version=parsed.evidence.parser_version,
            code="xlsx_preview_unavailable",
        )
    try:
        sheets = [
            XlsxPreviewSheet.model_validate(
                {
                    "name": sheet.get("name"),
                    "rows": sheet.get("rows"),
                }
            )
            for sheet in raw_sheets
            if isinstance(sheet, dict)
        ]
        if len(sheets) != len(raw_sheets):
            raise ValueError("invalid XLSX sheet payload")
        content = XlsxPreviewContent(
            sheets=sheets,
            sheet_count=parsed.evidence.sheet_count,
        )
    except (TypeError, ValueError):
        return _failed_preview(
            source_sha256=parsed.evidence.sha256,
            parser_id=parsed.evidence.parser_id,
            parser_version=parsed.evidence.parser_version,
            code="xlsx_preview_unavailable",
        )
    truncated = parsed.evidence.truncated
    return XlsxPreviewResponse(
        status="truncated" if truncated else "ready",
        source_sha256=parsed.evidence.sha256,
        parser_id=parsed.evidence.parser_id,
        parser_version=parsed.evidence.parser_version,
        content=content,
        truncated=truncated,
        warnings=list(_XLSX_PREVIEW_WARNINGS),
    )
