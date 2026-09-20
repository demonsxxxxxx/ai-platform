from contextlib import asynccontextmanager

import pytest

from app.context.api import ContextFileContentError
from app.context.file_continuity import (
    compatible_reusable_file_ids,
    has_file_input_mode,
    materialize_run_context_files,
    primary_file_ids_for_run,
    snapshot_file_ids,
)


def test_compatible_reusable_file_ids_preserves_newest_order_and_rejects_type_mismatch():
    rows = [
        {"id": "file-new-pdf", "original_name": "new.pdf", "content_type": "application/pdf"},
        {"id": "file-spoofed", "original_name": "spoofed.docx", "content_type": "application/pdf"},
        {
            "id": "file-docx",
            "original_name": "source.docx",
            "content_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        },
        {"id": "file-new-pdf", "original_name": "duplicate.pdf", "content_type": "application/pdf"},
        {"id": "file-text", "original_name": "notes.txt", "content_type": "text/plain; charset=utf-8"},
        {"id": "file-markdown", "original_name": "notes.markdown", "content_type": "text/markdown"},
    ]

    assert compatible_reusable_file_ids(rows, input_modes=["pdf", "docx", "txt", "markdown"]) == [
        "file-new-pdf",
        "file-docx",
        "file-text",
        "file-markdown",
    ]


def test_has_file_input_mode_excludes_general_chat():
    assert has_file_input_mode(["docx"])
    assert has_file_input_mode(["chat", "json"])
    assert not has_file_input_mode(["chat"])


def test_primary_file_ids_prefers_requested_files_over_history():
    assert primary_file_ids_for_run(
        requested_file_ids=["file-current"],
        reusable_rows=[
            {"id": "file-prior", "original_name": "prior.docx", "content_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}
        ],
        input_modes=["docx"],
    ) == ["file-current"]


def test_primary_file_ids_selects_newest_compatible_history_with_bound():
    rows = [
        {
            "id": f"file-{index}",
            "original_name": f"source-{index}.pdf",
            "content_type": "application/pdf",
            "created_at": f"2026-08-01T00:00:{index:02d}Z",
        }
        for index in range(10)
    ]
    rows.append(
        {
            "id": "file-docx",
            "original_name": "source.docx",
            "content_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "created_at": "2026-08-02T00:00:00Z",
        }
    )

    assert primary_file_ids_for_run(
        requested_file_ids=[],
        reusable_rows=rows,
        input_modes=["pdf"],
    ) == [f"file-{index}" for index in range(9, 1, -1)]


def test_primary_file_ids_reuses_only_newest_version_of_same_basename():
    rows = [
        {
            "id": "file-new",
            "original_name": "report.docx",
            "content_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "created_at": "2026-08-02T00:00:00Z",
        },
        {
            "id": "file-old",
            "original_name": "REPORT.docx",
            "content_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "created_at": "2026-08-01T00:00:00Z",
        },
    ]

    assert primary_file_ids_for_run(
        requested_file_ids=[],
        reusable_rows=rows,
        input_modes=["docx"],
    ) == ["file-new"]


def test_snapshot_file_ids_preserves_all_current_files_and_bounds_only_history():
    current = [f"file-current-{index}" for index in range(9)]

    assert snapshot_file_ids(
        current_file_ids=current,
        historical_file_ids=[f"file-prior-{index}" for index in range(8)],
    ) == current
    assert snapshot_file_ids(
        current_file_ids=["file-current"],
        historical_file_ids=[f"file-prior-{index}" for index in range(8)],
    ) == [
        *(f"file-prior-{index}" for index in range(7)),
        "file-current",
    ]


@pytest.mark.asyncio
async def test_materialization_rejects_overlong_legacy_filename_before_storage(tmp_path):
    class Repository:
        async def get_scoped_context_file(self, _conn, **_kwargs):
            return {
                "original_name": f"{'测' * 85}.md",
                "content_type": "text/markdown",
                "size_bytes": 5,
                "storage_key": "private/file-a",
            }

    @asynccontextmanager
    async def transaction_factory():
        yield object()

    async def storage_io(*_args, **_kwargs):
        raise AssertionError("storage must not be read for an invalid filename")

    with pytest.raises(ContextFileContentError) as raised:
        await materialize_run_context_files(
            transaction_factory=transaction_factory,
            repository=Repository(),
            storage=object(),
            storage_io=storage_io,
            storage_size_limit_error=RuntimeError,
            workspace=tmp_path,
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            user_id="user-a",
            session_id="session-a",
            run_id="run-a",
            file_ids=["file-a"],
        )

    assert raised.value.code == "context_file_staging_write_failed"
    assert raised.value.phase == "staging"
    assert raised.value.attachment_index == 1
    assert not (tmp_path / "inputs").exists()
