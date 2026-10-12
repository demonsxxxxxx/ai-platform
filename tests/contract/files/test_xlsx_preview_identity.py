import pytest

from app.files.api import (
    MAX_XLSX_FILE_BYTES,
    XLSX_CONTENT_TYPE,
    XLSX_PARSER_ID,
    XLSX_PARSER_VERSION,
    is_xlsx_preview_request,
    parser_spec_for_attachment,
    xlsx_preview_identity_from_metadata,
    xlsx_preview_max_bytes,
)


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


@pytest.mark.parametrize(
    ("file_name", "content_type", "selected", "eligible"),
    [
        ("book.xlsx", XLSX_CONTENT_TYPE, True, True),
        ("book.xlsx", "application/octet-stream", True, False),
        ("book.bin", XLSX_CONTENT_TYPE, True, False),
        ("book.xlsm", XLSX_CONTENT_TYPE, True, False),
        ("book.xlsm", "application/vnd.ms-excel", False, False),
        ("book.xlsx.exe", "application/octet-stream", False, False),
        ("", XLSX_CONTENT_TYPE, True, False),
        (None, None, False, False),
        (r"C:\private\BOOK.XLSX", f"  {XLSX_CONTENT_TYPE.upper()}; charset=utf-8  ", True, True),
    ],
)
def test_parser_selection_is_or_but_public_preview_eligibility_is_and(
    file_name, content_type, selected, eligible,
):
    spec = parser_spec_for_attachment(file_name=file_name, content_type=content_type)
    assert (spec is not None) is selected
    assert is_xlsx_preview_request(file_name=file_name, content_type=content_type) is eligible
    if spec is not None:
        assert (spec.parser_id, spec.parser_version, spec.max_bytes) == (
            XLSX_PARSER_ID, XLSX_PARSER_VERSION, MAX_XLSX_FILE_BYTES,
        )
    if eligible:
        assert xlsx_preview_max_bytes(file_name=file_name, content_type=content_type) == 1024 * 1024
    else:
        with pytest.raises(ValueError, match="^xlsx_preview_unsupported$"):
            xlsx_preview_max_bytes(file_name=file_name, content_type=content_type)


@pytest.mark.parametrize(
    ("row", "expected_name", "eligible"),
    [
        ({"original_name": r"C:\private\REPORT.XLSX", "file_name": "elsewhere/report.xlsx"}, "REPORT.XLSX", True),
        ({"original_name": " report.xlsx ", "file_name": "REPORT.XLSX"}, "report.xlsx", True),
        ({"original_name": " ", "file_name": "", "storage_key": r"private\fallback.XLSX"}, "fallback.XLSX", True),
        ({"original_name": "report.xlsm", "storage_key": "private/report.xlsx"}, "report.xlsm", False),
        ({"original_name": "report.xlsx", "file_name": "conflict.xlsx", "storage_key": "private/report.xlsx"}, None, False),
        ({"name": "display.xlsx", "storage_key": "private/opaque"}, "opaque", False),
        ({"name": "display.xlsx"}, None, False),
        ({"original_name": None, "file_name": None, "storage_key": None}, None, False),
    ],
)
def test_metadata_names_preserve_conflict_case_slashes_and_authoritative_fallback(
    row, expected_name, eligible,
):
    identity = xlsx_preview_identity_from_metadata({**row, "content_type": XLSX_CONTENT_TYPE})
    assert identity.file_name == expected_name
    assert identity.has_xlsx_content_type is True
    assert identity.eligible is eligible


def test_registered_parser_identity_and_limits_are_unchanged():
    from dataclasses import FrozenInstanceError

    from app.files.domain import xlsx_preview

    assert (XLSX_PARSER_ID, XLSX_PARSER_VERSION) == ("ai-platform.xlsx.openpyxl", "1")
    assert {
        name: getattr(xlsx_preview, name)
        for name in (
            "MAX_XLSX_FILE_BYTES", "MAX_XLSX_SHEETS", "MAX_XLSX_ROWS_PER_SHEET",
            "MAX_XLSX_COLUMNS_PER_SHEET", "MAX_XLSX_CELLS", "MAX_XLSX_CELL_CHARS",
            "MAX_XLSX_ZIP_ENTRIES", "MAX_XLSX_ZIP_ENTRY_BYTES", "MAX_XLSX_ZIP_TOTAL_BYTES",
            "MAX_XLSX_ZIP_COMPRESSION_RATIO",
        )
    } == {
        "MAX_XLSX_FILE_BYTES": 1024 * 1024,
        "MAX_XLSX_SHEETS": 16,
        "MAX_XLSX_ROWS_PER_SHEET": 100,
        "MAX_XLSX_COLUMNS_PER_SHEET": 32,
        "MAX_XLSX_CELLS": 16_000,
        "MAX_XLSX_CELL_CHARS": 256,
        "MAX_XLSX_ZIP_ENTRIES": 2000,
        "MAX_XLSX_ZIP_ENTRY_BYTES": 8 * 1024 * 1024,
        "MAX_XLSX_ZIP_TOTAL_BYTES": 32 * 1024 * 1024,
        "MAX_XLSX_ZIP_COMPRESSION_RATIO": 100,
    }
    spec = parser_spec_for_attachment(file_name="book.xlsx")
    assert spec is xlsx_preview.XLSX_PARSER_SPEC
    assert spec.extensions == frozenset({".xlsx"})
    assert spec.content_types == frozenset({XLSX_CONTENT_TYPE})
    with pytest.raises(FrozenInstanceError):
        spec.max_bytes = 2


@pytest.mark.parametrize("module_name", ["app.files.domain.xlsx_preview", "app.files.api"])
def test_identity_imports_do_not_load_parsers_or_construct_preview_resources(module_name):
    import subprocess
    import sys
    from pathlib import Path

    # A fresh interpreter proves the entire import chain; already-cached modules
    # in the pytest process must not make the resource assertion pass by accident.
    source = '''
import concurrent.futures
import importlib
import sys
import threading


def forbidden_resource(*args, **kwargs):
    raise AssertionError("pure Files identity import constructed an execution resource")


threading.BoundedSemaphore = forbidden_resource
concurrent.futures.ThreadPoolExecutor = forbidden_resource
module = importlib.import_module(sys.argv[1])
identity = module.xlsx_preview_identity_from_metadata({
    "original_name": "book.xlsx",
    "content_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
})
assert identity.eligible
assert not any(
    name in {"app.file_preview_contracts", "app.file_parser_contracts", "multiprocessing", "openpyxl"}
    or name.startswith(("app.bootstrap.", "app.files.infrastructure."))
    for name in sys.modules
)
'''
    result = subprocess.run(
        [sys.executable, "-c", source, module_name],
        cwd=Path(__file__).resolve().parents[3],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stderr
