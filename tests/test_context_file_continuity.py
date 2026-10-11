from contextlib import asynccontextmanager
from datetime import datetime, timezone

import pytest

from app.context.api import ContextFileContentError
from app.context.file_continuity import (
    compatible_reusable_file_ids,
    has_file_input_mode,
    list_authorized_session_input_files,
    materialize_run_context_files,
    primary_file_ids_for_run,
    select_authorized_run_file_snapshot,
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


class SessionFilePageConnection:
    """Evaluate the selection query's scope-independent keyset over owned rows."""

    def __init__(self, rows):
        self.rows = rows
        self.calls = []
        self.page_sizes = []

    async def execute(self, sql, params):
        sql = " ".join(sql.split())
        self.calls.append((sql, params))
        assert params[:4] == ("tenant-a", "workspace-a", "user-a", "session-a")
        assert "authorized_snapshot.id = runs.context_snapshot_id" in sql
        assert "authorized_snapshot.included_file_ids ? files.id" in sql
        assert "order by files.created_at desc, files.id desc limit %s" in sql
        assert "(files.created_at, files.id) < (%s::timestamptz, %s::text)" in sql
        only_ids, repeated_only_ids, excluded, before, repeated_before, before_id, page_limit = params[4:]
        assert only_ids == repeated_only_ids and before == repeated_before
        assert page_limit == 128
        page = sorted(
            (
                row for row in self.rows
                if (only_ids is None or row["id"] in only_ids)
                and row["id"] not in excluded
                and (before is None or (row["created_at"], row["id"]) < (before, before_id))
            ),
            key=lambda row: (row["created_at"], row["id"]),
            reverse=True,
        )[:page_limit]
        self.page_sizes.append(len(page))

        class Cursor:
            async def fetchall(self):
                return page

        return Cursor()


def _session_file(index, *, name=None, content_type="application/pdf", created_at=None):
    return {
        "id": f"file-{index:05d}",
        "original_name": name or f"report-{index}.pdf",
        "content_type": content_type,
        "created_at": created_at or datetime(2026, 8, 1, tzinfo=timezone.utc),
    }


async def _selected_session_files(conn, **kwargs):
    return await list_authorized_session_input_files(
        conn, tenant_id="tenant-a", workspace_id="workspace-a", user_id="user-a",
        session_id="session-a", **kwargs,
    )


@pytest.mark.asyncio
async def test_session_file_selection_stops_after_one_bounded_page_for_large_history():
    conn = SessionFilePageConnection([_session_file(index) for index in range(10000)])

    rows = await _selected_session_files(conn)

    assert [row["id"] for row in rows] == [f"file-{index:05d}" for index in range(9992, 10000)]
    assert conn.page_sizes == [128]
    assert len(conn.calls) == 1


@pytest.mark.asyncio
async def test_session_file_selection_fills_slots_across_tied_keyset_pages_and_casefold_duplicates():
    history = [
        _session_file(
            index,
            name=("irrelevant.txt" if index >= 300 else
                  ("straße.PDF" if index % 2 else "STRASSE.pdf") if index >= 160 else None),
            content_type="text/plain" if index >= 300 else "application/pdf",
        )
        for index in range(360)
    ]
    excluded = ["file-00159"]
    conn = SessionFilePageConnection(history)

    rows = await _selected_session_files(conn, input_modes=["pdf"], excluded_file_ids=excluded)

    expected = primary_file_ids_for_run(
        requested_file_ids=[], reusable_rows=[row for row in history if row["id"] not in excluded],
        input_modes=["pdf"],
    )
    assert [row["id"] for row in reversed(rows)] == expected
    assert expected == ["file-00299", *[f"file-{index:05d}" for index in range(158, 151, -1)]]
    assert conn.page_sizes == [128, 128]
    assert conn.calls[1][1][7:] == (
        history[232]["created_at"], history[232]["created_at"], "file-00232", 128,
    )
    assert len({row["id"] for row in rows}) == 8


@pytest.mark.asyncio
async def test_session_file_selection_orders_timestamp_before_id():
    conn = SessionFilePageConnection([
        _session_file(99),
        _session_file(1, created_at=datetime(2026, 8, 2, tzinfo=timezone.utc)),
        _session_file(2, created_at=datetime(2026, 8, 2, tzinfo=timezone.utc)),
    ])

    rows = await _selected_session_files(conn, limit=2)

    assert [row["id"] for row in rows] == ["file-00001", "file-00002"]


@pytest.mark.asyncio
async def test_session_file_keyset_does_not_restart_when_newer_rows_appear_between_pages():
    history = [
        _session_file(index, name="duplicate.pdf" if index >= 10 else None)
        for index in range(140)
    ]

    class GrowingConnection(SessionFilePageConnection):
        async def execute(self, sql, params):
            if self.calls:
                self.rows.append(_session_file(9999))
            return await super().execute(sql, params)

    conn = GrowingConnection(history)
    rows = await _selected_session_files(conn, input_modes=["pdf"])

    assert [row["id"] for row in reversed(rows)] == [
        "file-00139", *[f"file-{index:05d}" for index in range(9, 2, -1)],
    ]
    assert len(conn.calls) == 2
    assert all(row["id"] != "file-09999" for row in rows)


@pytest.mark.asyncio
@pytest.mark.parametrize("kwargs", [{"limit": 0}, {"file_ids": []}, {"input_modes": []}])
async def test_session_file_selection_skips_database_when_selection_cannot_add_files(kwargs):
    conn = SessionFilePageConnection([_session_file(1)])

    assert await _selected_session_files(conn, **kwargs) == []
    assert conn.calls == []


@pytest.mark.asyncio
async def test_authorized_agent_selection_keeps_current_priority_and_reusable_membership():
    history = [_session_file(index, name="same.pdf") for index in range(1000)]
    conn = SessionFilePageConnection(history)

    selection = await select_authorized_run_file_snapshot(
        conn=conn, tenant_id="tenant-a", workspace_id="workspace-a", user_id="user-a",
        session_id="session-a", requested_file_ids=["file-00000", "file-current", "file-current"],
        input_modes=["chat"], preserve_agent_history=True,
        load_session_files=list_authorized_session_input_files,
    )

    newest = [f"file-{index:05d}" for index in range(999, 993, -1)]
    assert selection.primary_file_ids == (*newest, "file-00000", "file-current")
    assert selection.reusable_primary_file_ids == (*newest, "file-00000")
    assert conn.page_sizes == [1, 128]
    assert conn.calls[0][1][4] == ["file-00000", "file-current"]
    assert conn.calls[1][1][6] == ["file-00000", "file-current"]
    # Agent history never applied the Skill-specific basename collapse.
    assert len(selection.primary_file_ids) == 8


@pytest.mark.asyncio
async def test_authorized_agent_selection_with_full_current_set_reads_only_targeted_membership():
    current = [f"file-{index:05d}" for index in range(9)]
    conn = SessionFilePageConnection([_session_file(index) for index in range(10000)])

    selection = await select_authorized_run_file_snapshot(
        conn=conn, tenant_id="tenant-a", workspace_id="workspace-a", user_id="user-a",
        session_id="session-a", requested_file_ids=current,
        input_modes=["chat"], preserve_agent_history=True,
        load_session_files=list_authorized_session_input_files,
    )

    assert selection.primary_file_ids == tuple(current)
    assert selection.reusable_primary_file_ids == tuple(current)
    assert len(conn.calls) == 1
    assert conn.calls[0][1][4] == current
    assert conn.page_sizes == [9]


@pytest.mark.asyncio
async def test_authorized_skill_selection_with_current_file_never_reselects_same_name_history():
    conn = SessionFilePageConnection([_session_file(1, name="current.pdf")])

    selection = await select_authorized_run_file_snapshot(
        conn=conn, tenant_id="tenant-a", workspace_id="workspace-a", user_id="user-a",
        session_id="session-a", requested_file_ids=["file-current"],
        input_modes=["pdf"], preserve_agent_history=False,
        load_session_files=list_authorized_session_input_files,
    )

    assert selection.primary_file_ids == ("file-current",)
    assert selection.reusable_primary_file_ids == ()
    assert conn.calls == []


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
