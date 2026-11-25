from contextlib import asynccontextmanager
from datetime import datetime, timezone
import hashlib
import io
from types import SimpleNamespace

import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.generic import DecodedStreamObject, NameObject

from app.context.infrastructure.attachment_cache import AttachmentCache

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
        "size_bytes": 1024,
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
async def test_ordinary_chat_reuses_only_explicit_same_session_attachment():
    conn = SessionFilePageConnection([_session_file(1), _session_file(2)])
    selection = await select_authorized_run_file_snapshot(
        conn=conn, tenant_id="tenant-a", workspace_id="workspace-a", user_id="user-a",
        session_id="session-a", requested_file_ids=["file-00001", "file-new"],
        input_modes=["chat"], preserve_agent_history=False, continue_chat_attachments=True,
        load_session_files=list_authorized_session_input_files,
    )
    assert selection.primary_file_ids == ("file-00001", "file-new")
    assert selection.reusable_primary_file_ids == ("file-00001",)
    assert len(conn.calls) == 1
    assert conn.calls[0][1][4] == ["file-00001", "file-new"]


@pytest.mark.asyncio
async def test_chat_continuation_automatically_authorizes_recent_same_session_files():
    conn = SessionFilePageConnection([_session_file(i) for i in range(10)])
    scope = dict(tenant_id="tenant-a", workspace_id="workspace-a", user_id="user-a")
    selection = await select_authorized_run_file_snapshot(
        conn=conn, **scope, session_id="session-a", requested_file_ids=[],
        input_modes=["chat"], preserve_agent_history=False, continue_chat_attachments=True,
    )

    assert selection.primary_file_ids == tuple(f"file-{i:05d}" for i in range(9, 1, -1))
    assert selection.reusable_primary_file_ids == selection.primary_file_ids
    assert not selection.file_required
    assert len(conn.calls) == 1
    statement, params = conn.calls[0]
    assert params[0:4] == ("tenant-a", "workspace-a", "user-a", "session-a")
    assert "and files.lifecycle_state = 'active'" in statement
    assert "and sessions.status = 'active'" in statement

    for session_id in (None, "session-a"):
        # No session cannot inherit files; a new/current upload takes precedence.
        new = await select_authorized_run_file_snapshot(
            conn=conn, **scope, session_id=session_id,
            requested_file_ids=["file-current"] if session_id else [],
            input_modes=["chat"], preserve_agent_history=False, continue_chat_attachments=True,
        )
        assert new.primary_file_ids == (("file-current",) if session_id else ())
        assert not new.reusable_primary_file_ids
    assert len(conn.calls) == 2  # Only targeted membership for the current upload.
    assert conn.calls[1][1][4] == ["file-current"]


@pytest.mark.asyncio
async def test_automatic_chat_history_respects_single_and_total_staging_budgets():
    mib = 1024 * 1024
    history = [
        {**_session_file(0), "size_bytes": 24 * mib},
        {**_session_file(1), "size_bytes": 120 * mib},
        {**_session_file(2), "size_bytes": 120 * mib},
        {**_session_file(3), "size_bytes": 129 * mib},
        _session_file(4, name="长" * 90 + ".pdf"),
        {**_session_file(5), "size_bytes": None},
    ]
    selected = await select_authorized_run_file_snapshot(
        conn=SessionFilePageConnection(history), tenant_id="tenant-a", workspace_id="workspace-a",
        user_id="user-a", session_id="session-a", requested_file_ids=[],
        input_modes=["chat"], preserve_agent_history=False, continue_chat_attachments=True,
    )
    assert selected.primary_file_ids == ("file-00002", "file-00001")
    assert selected.reusable_primary_file_ids == selected.primary_file_ids


@pytest.mark.asyncio
async def test_automatic_chat_history_keeps_distinct_same_name_files_with_stageable_paths():
    exact_limit_name = "a" * 251 + ".pdf"
    history = [
        _session_file(0, name=exact_limit_name),
        _session_file(1, name=exact_limit_name),
        _session_file(2, name="report.pdf"),
        _session_file(3, name="report.pdf"),
    ]
    selection = await select_authorized_run_file_snapshot(
        conn=SessionFilePageConnection(history), tenant_id="tenant-a", workspace_id="workspace-a",
        user_id="user-a", session_id="session-a", requested_file_ids=[],
        input_modes=["chat"], preserve_agent_history=False, continue_chat_attachments=True,
    )
    # Ordinary same-name files can both be staged. For the 255-byte basename
    # only the newest fits; the older copy would need an appended suffix.
    assert selection.primary_file_ids == ("file-00003", "file-00002", "file-00001")


@pytest.mark.asyncio
async def test_large_pdf_cache_reuses_bytes_only_after_current_snapshot_authorization(tmp_path):
    writer = PdfWriter()
    page = writer.add_blank_page(width=200, height=200)
    stream = DecodedStreamObject()
    stream.set_data(b"q Q\n" * (300 * 1024))
    page[NameObject("/Contents")] = stream
    buffer = io.BytesIO()
    writer.write(buffer)
    raw = buffer.getvalue()
    assert len(raw) > 1024 * 1024
    row = {
        **_session_file(1, name="report.pdf"),
        "size_bytes": len(raw), "storage_key": "synthetic/report",
        "sha256": hashlib.sha256(raw).hexdigest(),
    }
    scope = dict(tenant_id="tenant-a", workspace_id="workspace-a", user_id="user-a", session_id="session-a")
    first_file_ids = (row["id"],)
    continued = await select_authorized_run_file_snapshot(
        conn=SessionFilePageConnection([row]), **scope, requested_file_ids=[],
        input_modes=["chat"], preserve_agent_history=False, continue_chat_attachments=True,
    )
    snapshot_files = {
        "run-first": first_file_ids,
        "run-followup": continued.primary_file_ids,
        "run-refetch": continued.primary_file_ids,
    }
    download_count = 0

    class Repository:
        async def get_scoped_context_file(self, _conn, *, run_id, file_id, **request_scope):
            assert request_scope == scope
            return row if file_id in snapshot_files.get(run_id, ()) else None

    class Storage:
        def download_to_tempfile(self, *, storage_key, max_bytes):
            nonlocal download_count
            assert storage_key == row["storage_key"]
            assert max_bytes == len(raw)
            download_count += 1
            path = tmp_path / "download"
            path.write_bytes(raw)
            return SimpleNamespace(path=path, size_bytes=len(raw), sha256=row["sha256"])

    @asynccontextmanager
    async def transaction():
        yield None

    async def storage_io(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    cache = AttachmentCache(tmp_path / "host-cache", scope=("tenant-a", "workspace-a", "user-a", "session-a"))

    async def stage(run_id, file_ids=None):
        return await materialize_run_context_files(
            repository=Repository(), storage=Storage(), transaction_factory=transaction,
            storage_io=storage_io, storage_size_limit_error=ValueError,
            workspace=tmp_path / run_id, **scope, run_id=run_id,
            file_ids=list(snapshot_files[run_id] if file_ids is None else file_ids),
            attachment_cache=cache,
        )

    await stage("run-first")
    first = tmp_path / "run-first" / "inputs" / "report.pdf"
    assert PdfReader(first).pages[0].get_contents().get_data() == stream.get_data()
    first.write_bytes(b"mutated Attempt copy")
    assert (await stage("run-followup")).materialized_file_names == ("report.pdf",)
    assert (tmp_path / "run-followup" / "inputs" / "report.pdf").read_bytes() == raw
    assert download_count == 1
    await stage("run-not-selected", ())
    assert not (tmp_path / "run-not-selected" / "inputs").exists()
    with pytest.raises(ContextFileContentError, match="context_file_unavailable"):
        await stage("run-revoked", continued.primary_file_ids)
    assert not (tmp_path / "run-revoked" / "inputs").exists()
    assert download_count == 1

    # A same-length cache corruption is a miss, even if the cache identity matches.
    (slot,) = cache.root.iterdir()
    slot.write_bytes(slot.read_bytes()[:-1] + b"x")
    await stage("run-refetch")
    assert (tmp_path / "run-refetch" / "inputs" / "report.pdf").read_bytes() == raw
    assert download_count == 2


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
