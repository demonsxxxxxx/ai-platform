import hashlib
import io
import logging
from pathlib import Path
import tempfile
import threading
import zipfile

import pytest
from openpyxl import Workbook

from app import file_preview_contracts
from app.file_parser_contracts import (
    AttachmentParserRequirement,
    MAX_XLSX_PROMPT_CHARS,
    parser_spec_for_attachment,
)
from app.file_preview_contracts import (
    _stage_xlsx_preview_bytes,
    acquire_xlsx_preview_lease,
    build_xlsx_preview,
    is_xlsx_preview_request,
    xlsx_preview_identity_from_metadata,
)


XLSX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _workbook_bytes(*, formulas: list[str] | None = None) -> bytes:
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "Checks"
    worksheet.append(["requirement", "status"])
    worksheet.append(["ACCEPT-XLSX-9472", True])
    for formula in formulas or []:
        worksheet.append([formula])
    buffer = io.BytesIO()
    workbook.save(buffer)
    workbook.close()
    return buffer.getvalue()


def _large_text_workbook_bytes() -> bytes:
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "Large text"
    for row in range(1, 101):
        worksheet.append(
            [
                hashlib.sha256(f"cell-{row}-{column}".encode()).hexdigest() * 2
                for column in range(1, 17)
            ]
        )
    buffer = io.BytesIO()
    workbook.save(buffer)
    workbook.close()
    return buffer.getvalue()


def _workbook_with_worksheet_entity_declaration() -> bytes:
    source = io.BytesIO(_workbook_bytes())
    output = io.BytesIO()
    worksheet_path = "xl/worksheets/sheet1.xml"
    declaration = b'<!DOCTYPE worksheet [<!ENTITY unsafe "blocked">]>'
    with zipfile.ZipFile(source, "r") as archive, zipfile.ZipFile(output, "w") as rewritten:
        for entry in archive.infolist():
            payload = archive.read(entry.filename)
            if entry.filename == worksheet_path:
                insertion = payload.find(b"<worksheet")
                assert insertion >= 0
                payload = payload[:insertion] + declaration + payload[insertion:]
            rewritten.writestr(entry, payload)
    return output.getvalue()


def _build_preview(**kwargs):
    lease = acquire_xlsx_preview_lease()
    assert lease is not None
    try:
        return build_xlsx_preview(lease=lease, **kwargs)
    finally:
        lease.release()


def test_xlsx_preview_uses_the_registered_parser_and_returns_only_the_presentation_dto():
    raw = _workbook_bytes()
    preview = _build_preview(
        raw=raw,
        file_id="file-preview",
        file_name="checks.xlsx",
        content_type=XLSX_CONTENT_TYPE,
        expected_sha256=hashlib.sha256(raw).hexdigest(),
        expected_byte_count=len(raw),
    )

    assert preview.status == "ready"
    assert preview.content is not None
    assert preview.content.sheets[0].name == "Checks"
    assert preview.content.sheets[0].rows[1].cells[0].value == "ACCEPT-XLSX-9472"
    assert preview.error is None
    assert "storage_key" not in preview.model_dump_json()
    assert "workbook.xml" not in preview.model_dump_json()
    assert "source_sha256" not in preview.model_dump_json()
    assert "parser_id" not in preview.model_dump_json()
    assert "parser_version" not in preview.model_dump_json()


def test_xlsx_preview_redacts_local_and_external_formula_source():
    raw = _workbook_bytes(
        formulas=["=SUM(40,2)", "='[private-book.xlsx]Sheet1'!A1"],
    )
    preview = _build_preview(
        raw=raw,
        file_id="file-formulas",
        file_name="checks.xlsx",
        content_type=XLSX_CONTENT_TYPE,
    )

    serialized = preview.model_dump_json()
    assert "SUM(40,2)" not in serialized
    assert "private-book.xlsx" not in serialized
    assert "formula" not in {cell.kind for row in preview.content.sheets[0].rows for cell in row.cells}
    assert "[formula omitted]" in serialized
    assert "formulas_omitted" in preview.warnings


def test_generated_compatibility_xlsx_is_ready_through_the_isolated_deadline(caplog):
    raw = _large_text_workbook_bytes()
    assert 60_000 <= len(raw) < 1024 * 1024
    caplog.set_level(logging.INFO, logger="app.file_preview_contracts")

    preview = _build_preview(
        raw=raw,
        file_id="file-large-text",
        file_name="large-text.xlsx",
        content_type=XLSX_CONTENT_TYPE,
    )

    assert preview.status == "ready"
    assert preview.error is None
    assert preview.content is not None
    assert len(preview.content.sheets[0].rows) == 100
    assert len(preview.model_dump_json()) > MAX_XLSX_PROMPT_CHARS
    records = [
        record
        for record in caplog.records
        if record.getMessage().startswith("xlsx_preview_isolated_parser")
    ]
    assert len(records) == 1
    assert records[0].xlsx_preview_phase == "child_send"
    assert records[0].xlsx_preview_result == "parsed"
    assert records[0].xlsx_preview_code == "none"
    assert records[0].xlsx_preview_reason == "child_message"
    assert "file-large-text" not in records[0].getMessage()
    assert "large-text.xlsx" not in records[0].getMessage()


@pytest.mark.parametrize(
    "raw",
    [
        pytest.param(b"not-a-zip", id="corrupt-archive"),
        pytest.param(_workbook_with_worksheet_entity_declaration(), id="xml-entity"),
    ],
)
def test_xlsx_preview_keeps_authoritative_archive_and_xml_preflight(raw):
    preview = _build_preview(
        raw=raw,
        file_id="file-unsafe",
        file_name="unsafe.xlsx",
        content_type=XLSX_CONTENT_TYPE,
    )

    assert preview.status == "failed"
    assert preview.error is not None
    assert preview.error.code == "xlsx_preview_failed"
    assert preview.content is None


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


@pytest.mark.parametrize(
    ("row", "expected_name", "has_xlsx_content_type", "eligible"),
    [
        (
            {
                "original_name": "report.xlsx",
                "storage_key": "private/opaque-object",
                "content_type": XLSX_CONTENT_TYPE,
            },
            "report.xlsx",
            True,
            True,
        ),
        (
            {
                "storage_key": "private/export.xlsx",
                "content_type": XLSX_CONTENT_TYPE,
            },
            "export.xlsx",
            True,
            True,
        ),
        (
            {
                "original_name": "report.xlsx",
                "file_name": "report.xlsm",
                "storage_key": "private/report.xlsx",
                "content_type": XLSX_CONTENT_TYPE,
            },
            None,
            True,
            False,
        ),
        (
            {
                "storage_key": "private/export.xlsm",
                "content_type": XLSX_CONTENT_TYPE,
            },
            "export.xlsm",
            True,
            False,
        ),
        (
            {
                "storage_key": "private/export.xlsx",
                "content_type": "application/vnd.ms-excel",
            },
            "export.xlsx",
            False,
            False,
        ),
    ],
)
def test_xlsx_preview_identity_uses_stored_metadata_and_fails_closed_on_mismatch(
    row,
    expected_name,
    has_xlsx_content_type,
    eligible,
):
    identity = xlsx_preview_identity_from_metadata(row)

    assert identity.file_name == expected_name
    assert identity.has_xlsx_content_type is has_xlsx_content_type
    assert identity.eligible is eligible


def test_oversized_xlsx_preview_fails_with_a_stable_public_code():
    preview = _build_preview(
        raw=b"0" * (1024 * 1024 + 1),
        file_id="file-large",
        file_name="large.xlsx",
        content_type=XLSX_CONTENT_TYPE,
    )

    assert preview.status == "failed"
    assert preview.error is not None
    assert preview.error.code == "xlsx_preview_file_too_large"
    assert preview.content is None


def test_xlsx_staging_uses_a_fixed_private_filename_and_cleans_up():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        staged = _stage_xlsx_preview_bytes(root, b"preview")

        assert staged.name == "preview.xlsx"
        assert staged.parent == root.resolve()
        assert staged.read_bytes() == b"preview"
        if file_preview_contracts.os.name != "nt":
            assert staged.stat().st_mode & 0o777 == 0o600
    assert not root.exists()


def test_xlsx_staging_uses_the_fixed_path_with_windows_semantics(monkeypatch):
    real_os = file_preview_contracts.os

    class WindowsOs:
        name = "nt"

        def __getattr__(self, attribute):
            return getattr(real_os, attribute)

    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        monkeypatch.setattr(file_preview_contracts, "os", WindowsOs())

        staged = _stage_xlsx_preview_bytes(root, b"preview")

        assert staged.name == "preview.xlsx"
        assert staged.parent == root.resolve()
        assert staged.read_bytes() == b"preview"
    assert not root.exists()


def test_xlsx_child_uses_stdlib_xml_and_reports_memory_failure_without_source_data(
    monkeypatch,
):
    spec = parser_spec_for_attachment(
        file_name="preview.xlsx",
        content_type=XLSX_CONTENT_TYPE,
    )
    assert spec is not None
    requirement = AttachmentParserRequirement(
        file_id="sanitized-fixture",
        file_name="preview.xlsx",
        extension=".xlsx",
        content_type=XLSX_CONTENT_TYPE,
        parser_id=spec.parser_id,
        parser_version=spec.parser_version,
        max_bytes=spec.max_bytes,
    )
    sent: list[dict[str, object]] = []

    class Connection:
        def send(self, result):
            sent.append(result)

        def close(self):
            return None

    def fail_with_memory_error(**kwargs):
        raise MemoryError

    inherited_thread_counts = {
        "OPENBLAS_NUM_THREADS": "32",
        "OMP_NUM_THREADS": "16",
        "MKL_NUM_THREADS": "8",
        "NUMEXPR_NUM_THREADS": "4",
    }
    configured_environments: list[dict[str, str | None]] = []

    def observe_resource_limit_application(_timeout_seconds):
        configured_environments.append(
            {
                name: file_preview_contracts.os.environ.get(name)
                for name in ("OPENPYXL_LXML", *inherited_thread_counts)
            }
        )

    monkeypatch.setenv("OPENPYXL_LXML", "True")
    for name, value in inherited_thread_counts.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(
        file_preview_contracts,
        "_apply_child_resource_limits",
        observe_resource_limit_application,
    )
    monkeypatch.setattr(
        file_preview_contracts,
        "parse_xlsx_preview_attachment",
        fail_with_memory_error,
    )

    file_preview_contracts._parse_xlsx_preview_child(
        Connection(),
        b"sanitized",
        requirement.model_dump(mode="json"),
        5.0,
    )

    assert file_preview_contracts.os.environ["OPENPYXL_LXML"] == "False"
    assert configured_environments == [
        {
            "OPENPYXL_LXML": "False",
            "OPENBLAS_NUM_THREADS": "1",
            "OMP_NUM_THREADS": "1",
            "MKL_NUM_THREADS": "1",
            "NUMEXPR_NUM_THREADS": "1",
        }
    ]
    assert sent == [
        {
            "status": "failed",
            "code": "xlsx_preview_unavailable",
            "diagnostic": {"phase": "child_parse", "reason": "memory_limit"},
        }
    ]
    assert "sanitized-fixture" not in str(sent)


@pytest.mark.parametrize(
    "malformed_child_result",
    [
        {
            "status": "failed",
            "code": [],
            "diagnostic": {"phase": [], "reason": {}},
        },
        {
            "status": "failed",
            "code": {},
            "diagnostic": {"phase": {}, "reason": []},
        },
    ],
    ids=("list-values", "dict-values"),
)
def test_malformed_child_allowlist_values_fail_closed_without_leaking(
    malformed_child_result,
    caplog,
):
    caplog.set_level(logging.WARNING, logger="app.file_preview_contracts")

    file_preview_contracts._log_isolated_xlsx_result(malformed_child_result)
    public_result = file_preview_contracts._strip_isolated_xlsx_diagnostic(
        malformed_child_result
    )

    assert public_result == {
        "status": "failed",
        "code": "xlsx_preview_unavailable",
    }
    records = [
        record
        for record in caplog.records
        if record.getMessage().startswith("xlsx_preview_isolated_parser")
    ]
    assert len(records) == 1
    assert records[0].xlsx_preview_phase == "parent_receive"
    assert records[0].xlsx_preview_result == "failed"
    assert records[0].xlsx_preview_code == "none"
    assert records[0].xlsx_preview_reason == "invalid_child_result"
    assert records[0].getMessage() == (
        "xlsx_preview_isolated_parser phase=parent_receive result=failed "
        "code=none reason=invalid_child_result"
    )


def test_xlsx_preview_admission_fails_fast_without_queue(monkeypatch):
    admission = threading.BoundedSemaphore(1)
    monkeypatch.setattr(file_preview_contracts, "_PREVIEW_ADMISSION", admission)

    lease = acquire_xlsx_preview_lease()
    assert lease is not None
    assert acquire_xlsx_preview_lease() is None
    lease.release()


def test_timeout_reaps_and_closes_the_child_process(monkeypatch, caplog):
    calls: list[str] = []
    caplog.set_level(logging.WARNING, logger="app.file_preview_contracts")

    class Connection:
        def poll(self, timeout):
            calls.append(f"poll:{timeout}")
            return False

        def close(self):
            calls.append("connection.close")

    class Process:
        def __init__(self):
            self.alive = False

        def start(self):
            self.alive = True
            calls.append("start")

        def is_alive(self):
            return self.alive

        def terminate(self):
            self.alive = False
            calls.append("terminate")

        def kill(self):
            self.alive = False
            calls.append("kill")

        def join(self, timeout=0):
            calls.append(f"join:{timeout}")

        def close(self):
            calls.append("process.close")

    class Context:
        def Pipe(self, *, duplex):
            assert duplex is False
            return Connection(), Connection()

        def Process(self, **kwargs):
            assert kwargs["daemon"] is True
            return Process()

    monkeypatch.setattr(file_preview_contracts.multiprocessing, "get_context", lambda _: Context())
    lease = acquire_xlsx_preview_lease()
    assert lease is not None
    result = file_preview_contracts._invoke_isolated_xlsx_parser(
        raw=b"safe",
        requirement={},
        timeout_seconds=0.01,
        lease=lease,
    )
    lease.release()

    assert result == {"status": "failed", "code": "xlsx_preview_timeout"}
    assert "terminate" in calls
    assert "join:0.5" in calls
    assert "process.close" in calls
    records = [
        record
        for record in caplog.records
        if record.getMessage().startswith("xlsx_preview_isolated_parser")
    ]
    assert len(records) == 1
    assert records[0].xlsx_preview_phase == "parent_wait"
    assert records[0].xlsx_preview_result == "failed"
    assert records[0].xlsx_preview_code == "xlsx_preview_timeout"
    assert records[0].xlsx_preview_reason == "deadline"
    assert "safe" not in records[0].getMessage()


def test_unreaped_child_retains_the_permit_until_the_reaper_confirms_exit(monkeypatch):
    admission = threading.BoundedSemaphore(1)
    monkeypatch.setattr(file_preview_contracts, "_PREVIEW_ADMISSION", admission)
    allow_exit = threading.Event()
    calls: list[str] = []

    class Connection:
        def poll(self, timeout):
            return False

        def close(self):
            return None

    class Process:
        alive = False

        def start(self):
            self.alive = True

        def is_alive(self):
            return self.alive

        def terminate(self):
            calls.append("terminate")

        def kill(self):
            calls.append("kill")

        def join(self, timeout=0):
            allow_exit.wait(min(timeout, 0.01))

        def close(self):
            calls.append("close")

    process = Process()

    class Context:
        def Pipe(self, *, duplex):
            assert duplex is False
            return Connection(), Connection()

        def Process(self, **kwargs):
            return process

    monkeypatch.setattr(file_preview_contracts.multiprocessing, "get_context", lambda _: Context())
    lease = acquire_xlsx_preview_lease()
    assert lease is not None

    result = file_preview_contracts._invoke_isolated_xlsx_parser(
        raw=b"safe",
        requirement={},
        timeout_seconds=0.01,
        lease=lease,
    )
    lease.release()

    assert result == {"status": "failed", "code": "xlsx_preview_timeout"}
    assert acquire_xlsx_preview_lease() is None
    assert "terminate" in calls and "kill" in calls

    process.alive = False
    assert lease._reaper_thread is not None
    lease._reaper_thread.join(timeout=1)
    recovered = acquire_xlsx_preview_lease()
    assert recovered is not None
    recovered.release()
    assert "close" in calls


def test_connection_close_error_still_reaps_stubborn_child_before_releasing_lease(monkeypatch):
    admission = threading.BoundedSemaphore(1)
    monkeypatch.setattr(file_preview_contracts, "_PREVIEW_ADMISSION", admission)
    calls: list[str] = []

    class Connection:
        def close(self):
            calls.append("connection.close")
            raise RuntimeError("close failed")

        def poll(self, timeout):
            raise AssertionError("send close error must stop the request path")

    class Process:
        alive = False

        def start(self):
            self.alive = True
            calls.append("start")

        def is_alive(self):
            return self.alive

        def terminate(self):
            calls.append("terminate")

        def kill(self):
            calls.append("kill")

        def join(self, timeout=0):
            calls.append(f"join:{timeout}")

        def close(self):
            calls.append("process.close")

    process = Process()

    class Context:
        def Pipe(self, *, duplex):
            assert duplex is False
            return Connection(), Connection()

        def Process(self, **kwargs):
            return process

    monkeypatch.setattr(file_preview_contracts.multiprocessing, "get_context", lambda _: Context())
    lease = acquire_xlsx_preview_lease()
    assert lease is not None

    result = file_preview_contracts._invoke_isolated_xlsx_parser(
        raw=b"safe",
        requirement={},
        timeout_seconds=0.01,
        lease=lease,
    )
    lease.release()

    assert result == {"status": "failed", "code": "xlsx_preview_unavailable"}
    assert "terminate" in calls and "kill" in calls
    assert lease._reaper_thread is not None
    assert acquire_xlsx_preview_lease() is None

    process.alive = False
    lease._reaper_thread.join(timeout=1)
    recovered = acquire_xlsx_preview_lease()
    assert recovered is not None
    recovered.release()
