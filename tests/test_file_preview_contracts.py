import hashlib
import io
from pathlib import Path
import tempfile
import threading

import pytest
from openpyxl import Workbook

from app import file_preview_contracts
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


def test_xlsx_preview_admission_fails_fast_without_queue(monkeypatch):
    admission = threading.BoundedSemaphore(1)
    monkeypatch.setattr(file_preview_contracts, "_PREVIEW_ADMISSION", admission)

    lease = acquire_xlsx_preview_lease()
    assert lease is not None
    assert acquire_xlsx_preview_lease() is None
    lease.release()


def test_timeout_reaps_and_closes_the_child_process(monkeypatch):
    calls: list[str] = []

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
