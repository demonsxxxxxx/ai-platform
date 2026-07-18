"""Versioned, server-owned bounded previews for authorized files.

This module is deliberately a thin seam for routes: callers provide already
authorized bytes and file metadata, and receive a public-safe DTO.  The XLSX
implementation delegates all archive and workbook interpretation to the
existing attachment parser; it never implements a second OOXML parser.
"""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import hashlib
import math
import multiprocessing
import os
from pathlib import Path, PurePosixPath
import stat
import tempfile
import threading
import time
from typing import Any, Callable, Literal, Mapping

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
MAX_CONCURRENT_XLSX_PREVIEWS = 2
_XLSX_STAGING_FILENAME = "preview.xlsx"
_FORMULA_REDACTED_PLACEHOLDER = "[formula omitted]"
_PREVIEW_ADMISSION = threading.BoundedSemaphore(MAX_CONCURRENT_XLSX_PREVIEWS)
_PREVIEW_EXECUTOR = ThreadPoolExecutor(
    max_workers=MAX_CONCURRENT_XLSX_PREVIEWS,
    thread_name_prefix="xlsx-preview",
)
_XLSX_PREVIEW_WARNINGS = (
    "styles_not_rendered",
    "charts_not_rendered",
    "formulas_omitted",
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
    kind: Literal["boolean", "datetime", "number", "text"]
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


class XlsxPreviewBusyError(RuntimeError):
    """Raised when the bounded preview admission limit has been reached."""


@dataclass(frozen=True)
class XlsxPreviewIdentity:
    """One stored-metadata decision for the exact XLSX preview contract."""

    file_name: str | None
    content_type: str

    @property
    def has_xlsx_content_type(self) -> bool:
        """Return whether the stored MIME type selects the XLSX presentation path."""

        return self.content_type.split(";", 1)[0].strip().casefold() == XLSX_CONTENT_TYPE

    @property
    def eligible(self) -> bool:
        """Return whether both authoritative filename and MIME select XLSX."""

        return self.file_name is not None and is_xlsx_preview_request(
            file_name=self.file_name,
            content_type=self.content_type,
        )


class XlsxPreviewLease:
    """One route-owned permit retained until work and any child reaping finish."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._released = False
        self._release_requested = False
        self._reaper_active = False
        self._reaper_thread: threading.Thread | None = None

    def release(self) -> None:
        """Release the permit once no tracked child remains alive."""

        with self._lock:
            self._release_requested = True
            if self._released or self._reaper_active:
                return
            self._released = True
        _PREVIEW_ADMISSION.release()

    def defer_release_until(self, future: asyncio.Future[Any]) -> None:
        """Keep the permit while a cancelled request's executor work continues."""

        future.add_done_callback(lambda _future: self.release())

    def retain_until_process_exit(self, process: multiprocessing.Process) -> None:
        """Track an unreaped child and release only after its exit is confirmed."""

        with self._lock:
            if self._released or self._reaper_active:
                return
            self._reaper_active = True
            reaper = threading.Thread(
                target=self._reap_process,
                args=(process,),
                name="xlsx-preview-reaper",
                daemon=True,
            )
            self._reaper_thread = reaper
        try:
            reaper.start()
        except RuntimeError:
            # Keep the permit held rather than admitting more work with an
            # untracked child whose exit cannot yet be confirmed.
            return

    def _reap_process(self, process: multiprocessing.Process) -> None:
        try:
            while True:
                try:
                    if not process.is_alive():
                        break
                    process.join(timeout=0.1)
                except Exception:
                    # An uncertain process state must retain the permit and retry.
                    time.sleep(0.05)
            try:
                process.join(timeout=0)
                process.close()
            except Exception:
                pass
        finally:
            with self._lock:
                self._reaper_active = False
                should_release = self._release_requested and not self._released
                if should_release:
                    self._released = True
            if should_release:
                _PREVIEW_ADMISSION.release()


def acquire_xlsx_preview_lease() -> XlsxPreviewLease | None:
    """Fail closed when all preview permits are held by reads, parses, or reapers."""

    if not _PREVIEW_ADMISSION.acquire(blocking=False):
        return None
    return XlsxPreviewLease()


async def run_xlsx_preview_job(
    *,
    lease: XlsxPreviewLease,
    job: Callable[[], XlsxPreviewResponse],
) -> XlsxPreviewResponse:
    """Run one leased storage-and-parse job on the dedicated bounded executor."""

    loop = asyncio.get_running_loop()
    try:
        future = loop.run_in_executor(_PREVIEW_EXECUTOR, job)
    except Exception:
        lease.release()
        raise
    try:
        return await asyncio.shield(future)
    except asyncio.CancelledError:
        lease.defer_release_until(future)
        raise
    finally:
        if future.done():
            lease.release()


def is_xlsx_preview_request(*, file_name: object, content_type: object) -> bool:
    """Return whether this exact metadata is eligible for the XLSX DTO path."""

    normalized_name = PurePosixPath(str(file_name or "").replace("\\", "/")).name
    normalized_type = str(content_type or "").split(";", 1)[0].strip().casefold()
    return normalized_name.casefold().endswith(".xlsx") and normalized_type == XLSX_CONTENT_TYPE


def xlsx_preview_identity_from_metadata(
    row: Mapping[str, object],
) -> XlsxPreviewIdentity:
    """Resolve XLSX eligibility from stored file metadata, never a display label.

    ``original_name`` and ``file_name`` are preferred when present.  When both
    are present they must agree; a disagreement fails closed.  Artifact rows
    without either field use the persisted storage-key basename, which is also
    the filename consumed by the authorized preview route.
    """

    preferred_names: list[str] = []
    for field_name in ("original_name", "file_name"):
        name = _stored_metadata_basename(row.get(field_name))
        if name is not None:
            preferred_names.append(name)
    normalized_names = {name.casefold() for name in preferred_names}
    if len(normalized_names) > 1:
        file_name = None
    elif preferred_names:
        file_name = preferred_names[0]
    else:
        file_name = _stored_metadata_basename(row.get("storage_key"))
    return XlsxPreviewIdentity(
        file_name=file_name,
        content_type=str(row.get("content_type") or ""),
    )


def _stored_metadata_basename(value: object) -> str | None:
    """Return a non-empty basename from a stored filename-like metadata value."""

    normalized = PurePosixPath(str(value or "").replace("\\", "/")).name.strip()
    return normalized or None


def xlsx_preview_max_bytes(*, file_name: object, content_type: object) -> int:
    """Resolve the registered byte cap for an eligible XLSX presentation request."""

    spec = parser_spec_for_attachment(file_name=file_name, content_type=content_type)
    if spec is None or not is_xlsx_preview_request(
        file_name=file_name,
        content_type=content_type,
    ):
        raise ValueError("xlsx_preview_unsupported")
    return spec.max_bytes


def build_xlsx_preview(
    *,
    raw: bytes,
    file_id: str,
    file_name: str,
    content_type: str,
    lease: XlsxPreviewLease,
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
    if spec is None or not is_xlsx_preview_request(file_name=file_name, content_type=content_type):
        return _failed_preview(
            code="xlsx_preview_unsupported",
        )

    if len(raw) > spec.max_bytes:
        return _failed_preview(
            code="xlsx_preview_file_too_large",
        )

    try:
        requirement = AttachmentParserRequirement(
            file_id=file_id,
            file_name=_XLSX_STAGING_FILENAME,
            extension=".xlsx",
            content_type=content_type,
            parser_id=spec.parser_id,
            parser_version=spec.parser_version,
            max_bytes=spec.max_bytes,
            expected_byte_count=expected_byte_count,
            expected_sha256=expected_sha256,
        )
    except (TypeError, ValueError):
        return _failed_preview(
            code="xlsx_preview_unavailable",
        )
    child_result = _invoke_isolated_xlsx_parser(
        raw=raw,
        requirement=requirement.model_dump(mode="json"),
        timeout_seconds=timeout_seconds,
        lease=lease,
    )
    if child_result["status"] != "parsed":
        return _failed_preview(
            code=child_result["code"],
        )

    try:
        parsed = ParsedAttachmentContext.model_validate(child_result["parsed"])
    except (TypeError, ValueError):
        return _failed_preview(
            code="xlsx_preview_unavailable",
        )
    if (
        parsed.evidence.sha256 != source_sha256
        or parsed.evidence.parser_id != spec.parser_id
        or parsed.evidence.parser_version != spec.parser_version
    ):
        return _failed_preview(
            code="xlsx_preview_unavailable",
        )
    return _preview_from_parsed_context(parsed)


def _invoke_isolated_xlsx_parser(
    *,
    raw: bytes,
    requirement: dict[str, Any],
    timeout_seconds: float,
    lease: XlsxPreviewLease,
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
    result: dict[str, Any]
    try:
        process.start()
        started = True
        send_conn.close()
        if not receive_conn.poll(timeout_seconds):
            result = {"status": "failed", "code": "xlsx_preview_timeout"}
        else:
            try:
                result = receive_conn.recv()
            except (EOFError, OSError):
                result = {"status": "failed", "code": "xlsx_preview_unavailable"}
            process.join(timeout=0.5)
            if process.is_alive() or not isinstance(result, dict):
                result = {"status": "failed", "code": "xlsx_preview_unavailable"}
    except Exception:
        result = {"status": "failed", "code": "xlsx_preview_unavailable"}
    finally:
        _finalize_isolated_parser_process(
            process=process,
            started=started,
            lease=lease,
        )
        close_failed = not _close_isolated_parser_connections(receive_conn, send_conn)
        if close_failed:
            # The public failure is emitted only after process ownership is
            # either confirmed or retained by the reaper.
            result = {"status": "failed", "code": "xlsx_preview_unavailable"}
    return result


def _finalize_isolated_parser_process(
    *,
    process: multiprocessing.Process,
    started: bool,
    lease: XlsxPreviewLease,
) -> bool:
    """Stop or retain an isolated child before any connection close can raise."""

    if not started:
        try:
            process.close()
        except Exception:
            pass
        return True
    try:
        if process.is_alive():
            reaped = _stop_process(process)
        else:
            process.join(timeout=0)
            reaped = not process.is_alive()
    except Exception:
        reaped = False
    if not reaped:
        lease.retain_until_process_exit(process)
        return False
    try:
        process.close()
    except Exception:
        pass
    return True


def _close_isolated_parser_connections(*connections: Any) -> bool:
    """Best-effort-close both pipe endpoints without bypassing process ownership."""

    closed_without_error = True
    for connection in connections:
        try:
            connection.close()
        except Exception:
            closed_without_error = False
    return closed_without_error


def _stop_process(process: multiprocessing.Process) -> bool:
    """Terminate, then kill if needed, so timed-out previews never leak a child."""

    try:
        if process.is_alive():
            process.terminate()
        process.join(timeout=0.5)
        if process.is_alive():
            process.kill()
            process.join(timeout=0.5)
        return not process.is_alive()
    except Exception:
        return False


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
            staged_path = _stage_xlsx_preview_bytes(Path(directory), raw)
            parsed = parse_xlsx_attachment(path=staged_path, requirement=requirement)
        send_conn.send({"status": "parsed", "parsed": parsed.model_dump(mode="json")})
    except AttachmentPreprocessingError as exc:
        send_conn.send({"status": "failed", "code": _public_failure_code(exc.code)})
    except Exception:
        send_conn.send({"status": "failed", "code": "xlsx_preview_unavailable"})
    finally:
        send_conn.close()


def _stage_xlsx_preview_bytes(directory: Path, raw: bytes) -> Path:
    """Stage bytes under one fixed private filename without trusting user metadata."""

    root = directory.resolve(strict=True)
    if not root.is_dir():
        raise ValueError("xlsx_preview_staging_invalid")
    if os.name != "nt":
        root.chmod(0o700)
        if stat.S_IMODE(root.stat().st_mode) != 0o700:
            raise ValueError("xlsx_preview_staging_invalid")
    staged_path = (root / _XLSX_STAGING_FILENAME).resolve(strict=False)
    if staged_path.parent != root or staged_path.name != _XLSX_STAGING_FILENAME:
        raise ValueError("xlsx_preview_staging_invalid")
    descriptor = os.open(
        staged_path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        0o600,
    )
    try:
        with os.fdopen(descriptor, "wb") as staged_file:
            staged_file.write(raw)
    except Exception:
        try:
            staged_path.unlink(missing_ok=True)
        finally:
            raise
    if os.name != "nt":
        staged_path.chmod(0o600)
        if stat.S_IMODE(staged_path.stat().st_mode) != 0o600:
            raise ValueError("xlsx_preview_staging_invalid")
    return staged_path


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
    code: PreviewFailureCode,
) -> XlsxPreviewResponse:
    return XlsxPreviewResponse(
        status="failed",
        error=FilePreviewError(code=code),
    )


def _preview_from_parsed_context(parsed: ParsedAttachmentContext) -> XlsxPreviewResponse:
    """Adapt one parser result to the separate, browser-safe presentation DTO."""

    workbook = parsed.content.get("workbook")
    raw_sheets = workbook.get("sheets") if isinstance(workbook, dict) else None
    if not isinstance(raw_sheets, list):
        return _failed_preview(
            code="xlsx_preview_unavailable",
        )
    try:
        sheets = [
            _public_preview_sheet(sheet)
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
            code="xlsx_preview_unavailable",
        )
    truncated = parsed.evidence.truncated
    return XlsxPreviewResponse(
        status="truncated" if truncated else "ready",
        content=content,
        truncated=truncated,
        warnings=list(_XLSX_PREVIEW_WARNINGS),
    )


def _public_preview_sheet(sheet: dict[str, Any]) -> XlsxPreviewSheet:
    """Drop parser-only fields and redact formulas before they enter the UI DTO."""

    raw_rows = sheet.get("rows")
    if not isinstance(raw_rows, list):
        raise ValueError("invalid XLSX sheet payload")
    rows = [_public_preview_row(row) for row in raw_rows]
    return XlsxPreviewSheet(name=sheet.get("name"), rows=rows)


def _public_preview_row(row: object) -> XlsxPreviewRow:
    """Map one parser row to public cells without formula expressions."""

    if not isinstance(row, dict):
        raise ValueError("invalid XLSX row payload")
    raw_cells = row.get("cells")
    if not isinstance(raw_cells, list):
        raise ValueError("invalid XLSX row payload")
    cells = [_public_preview_cell(cell) for cell in raw_cells]
    return XlsxPreviewRow(row=row.get("row"), cells=cells)


def _public_preview_cell(cell: object) -> XlsxPreviewCell:
    """Replace formula source with a fixed safe placeholder for presentation."""

    if not isinstance(cell, dict):
        raise ValueError("invalid XLSX cell payload")
    kind = cell.get("kind")
    if kind == "formula":
        return XlsxPreviewCell(
            column=cell.get("column"),
            kind="text",
            value=_FORMULA_REDACTED_PLACEHOLDER,
        )
    return XlsxPreviewCell.model_validate(
        {
            "column": cell.get("column"),
            "kind": kind,
            "value": cell.get("value"),
        }
    )
