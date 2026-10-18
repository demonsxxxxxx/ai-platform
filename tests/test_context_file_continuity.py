from contextlib import asynccontextmanager
from datetime import datetime, timezone

import pytest

from app.sandbox.infrastructure.input_materialization import InputMaterializationBatch
from app.platform.storage_io_completion import StorageIOCompletion

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
            input_batch_factory=InputMaterializationBatch, storage_completion_factory=StorageIOCompletion, transaction_factory=transaction_factory,
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


def _minimal_xlsx_for_materialization(*, unsafe):
    """Synthetic OPC workbook; no office parser or external fixture is needed."""
    import io
    from zipfile import ZipFile

    stream = io.BytesIO()
    with ZipFile(stream, "w") as archive:
        archive.writestr(
            "[Content_Types].xml",
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '</Types>',
        )
        archive.writestr(
            "_rels/.rels",
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Target="xl/workbook.xml" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"/>'
            '</Relationships>',
        )
        archive.writestr(
            "xl/workbook.xml",
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheets/></workbook>',
        )
        if unsafe:
            archive.writestr("xl/vbaProject.bin", b"synthetic active content")
    return stream.getvalue()


@pytest.mark.asyncio
@pytest.mark.parametrize("storage_branch", ["tempfile", "bytes"])
@pytest.mark.parametrize("payload_kind", ["valid", "active-content", "not-zip"])
@pytest.mark.parametrize(
    ("name_fields", "content_type", "declared_xlsx"),
    [
        ({"original_name": "source.bin"},
         " application/vnd.openxmlformats-officedocument.spreadsheetml.sheet ", True),
        ({"original_name": "source.bin"},
         "\tAPPLICATION/VND.OPENXMLFORMATS-OFFICEDOCUMENT.SPREADSHEETML.SHEET ; charset=utf-8 ", True),
        ({"original_name": "source.bin"},
         "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet;charset=utf-8", True),
        ({"original_name": "source.XLSX"}, "application/octet-stream", True),
        ({"original_name": "folder\\source.XlSx"}, "text/plain", True),
        ({"original_name": "", "name": "fallback.XLSX"}, "application/octet-stream", True),
        ({"original_name": "source.bin", "name": "ignored.xlsx"}, "application/octet-stream", False),
        ({"original_name": "source.bin"}, "application/x-unknown", False),
        ({"original_name": "source.bin"}, None, False),
    ],
    ids=["mime-spaces", "mime-case-spaces-parameter", "mime-parameter", "extension-case",
         "windows-extension", "name-fallback", "original-name-priority", "opaque", "no-mime"],
)
async def test_materialization_xlsx_declaration_matches_storage_branches(
    tmp_path, monkeypatch, storage_branch, payload_kind, name_fields, content_type, declared_xlsx,
):
    import hashlib
    import io
    from pathlib import Path
    from types import SimpleNamespace

    from app.context import file_content
    from app import storage as storage_module

    raw = (b"not-a-zip" if payload_kind == "not-zip" else
           _minimal_xlsx_for_materialization(unsafe=payload_kind == "active-content"))
    row = dict(name_fields, content_type=content_type, size_bytes=len(raw),
               sha256=hashlib.sha256(raw).hexdigest(), storage_key="synthetic/source")
    requests = []
    bodies = []
    downloads = []
    scans = []

    class Repository:
        async def get_scoped_context_file(self, _conn, **scope):
            assert scope == dict(tenant_id="tenant-a", workspace_id="workspace-a", user_id="user-a",
                                 session_id="session-a", run_id="run-a", file_id="file-a")
            return row

    @asynccontextmanager
    async def transaction_factory():
        yield object()

    def get_object(**kwargs):
        requests.append(kwargs)
        body = io.BytesIO(raw)
        bodies.append(body)
        return {"Body": body}

    actual = storage_module.ObjectStorage.__new__(storage_module.ObjectStorage)
    actual.bucket = "synthetic"
    actual.client = SimpleNamespace(get_object=get_object)
    storage = actual if storage_branch == "tempfile" else SimpleNamespace(
        get_bytes_bounded=actual.get_bytes_bounded,
    )
    temporary_factory = storage_module.tempfile.NamedTemporaryFile

    def temporary_file(**kwargs):
        temporary = temporary_factory(dir=tmp_path, **kwargs)
        downloads.append(Path(temporary.name))
        return temporary

    archive_validator = file_content._validate_xlsx_archive_security

    def validate_archive(content):
        scans.append(content)
        return archive_validator(content)

    monkeypatch.setattr(storage_module.tempfile, "NamedTemporaryFile", temporary_file)
    monkeypatch.setattr(file_content, "_validate_xlsx_archive_security", validate_archive)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    arguments = dict(input_batch_factory=InputMaterializationBatch, storage_completion_factory=StorageIOCompletion, transaction_factory=transaction_factory, repository=Repository(), storage=storage,
                     storage_io=storage_module.run_storage_io,
                     storage_size_limit_error=storage_module.ObjectStorageSizeLimitError,
                     workspace=workspace, tenant_id="tenant-a", workspace_id="workspace-a",
                     user_id="user-a", session_id="session-a", run_id="run-a", file_ids=["file-a"])
    if declared_xlsx and payload_kind != "valid":
        with pytest.raises(ContextFileContentError) as raised:
            await materialize_run_context_files(**arguments)
        assert raised.value.code == "context_file_xlsx_archive_invalid"
        assert raised.value.attachment_index == 1
        assert not list(workspace.rglob("*"))
    else:
        result = await materialize_run_context_files(**arguments)
        assert len(result.materialized_file_names) == 1
        assert (workspace / "inputs" / result.materialized_file_names[0]).read_bytes() == raw
        assert result.attachment_metadata[0].size_bytes == len(raw)
        assert result.attachment_metadata[0].content_type == (content_type or "")
    assert scans == ([raw] if declared_xlsx else [])
    assert requests == [{"Bucket": "synthetic", "Key": "synthetic/source"}]
    assert bodies and all(body.closed for body in bodies)
    assert len(downloads) == (1 if storage_branch == "tempfile" else 0)
    assert all(not path.exists() for path in downloads)


async def _batch_case(tmp_path, *, branch='tempfile', rows=None, bodies=None, storage=None):
    import hashlib
    import tempfile
    from app.storage import DownloadedObject, ObjectStorageSizeLimitError, run_storage_io
    bodies = bodies or {'a': b'first', 'b': b'second'}
    rows = rows or {name: dict(original_name=name + '.bin', storage_key=name, size_bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest()) for name, raw in bodies.items()}
    calls = []

    class Repository:

        async def get_scoped_context_file(self, _conn, **kwargs):
            calls.append('authorize-' + kwargs['file_id'])
            return rows[kwargs['file_id']]

    class Storage:

        def get_bytes_bounded(self, *, storage_key, max_bytes):
            calls.append('download-' + storage_key)
            return bodies[storage_key]

        def download_to_tempfile(self, *, storage_key, max_bytes):
            raw = self.get_bytes_bounded(storage_key=storage_key, max_bytes=max_bytes)
            with tempfile.NamedTemporaryFile(dir=tmp_path, delete=False) as output:
                output.write(raw)
                return DownloadedObject(output.name, hashlib.sha256(raw).hexdigest(), len(raw))
    if storage is None:
        storage = Storage()
        if branch == 'bytes':
            from types import SimpleNamespace
            storage = SimpleNamespace(get_bytes_bounded=storage.get_bytes_bounded)

    @asynccontextmanager
    async def transaction():
        yield object()
    workspace = tmp_path / 'workspace'
    workspace.mkdir(exist_ok=True)
    arguments = dict(input_batch_factory=InputMaterializationBatch, storage_completion_factory=StorageIOCompletion, transaction_factory=transaction, repository=Repository(), storage=storage, storage_io=run_storage_io, storage_size_limit_error=ObjectStorageSizeLimitError, workspace=workspace, tenant_id='tenant', workspace_id='workspace', user_id='user', session_id='session', run_id='run', file_ids=list(rows))
    return (arguments, calls)

@pytest.mark.asyncio
@pytest.mark.parametrize('branch', ['tempfile', 'bytes'])
@pytest.mark.parametrize('failure', ['digest', 'unsafe-xlsx', 'size', 'key'])
async def test_input_batch_rejects_late_file_before_any_publication(tmp_path, monkeypatch, branch, failure):
    import hashlib
    from app.context.api import ContextFileContentError
    from app.sandbox.infrastructure import input_materialization as owner
    bodies = {'a': b'first', 'b': b'second'}
    rows = {name: dict(original_name=name + '.bin', storage_key=name, size_bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest()) for name, raw in bodies.items()}
    if failure == 'digest':
        rows['b']['sha256'] = '0' * 64
    if failure == 'unsafe-xlsx':
        rows['b']['original_name'] = 'b.xlsx'
    if failure == 'size':
        rows['b']['size_bytes'] = 128 * 1024 * 1024 + 1
    if failure == 'key':
        rows['b']['storage_key'] = ''
    args, calls = await _batch_case(tmp_path, branch=branch, rows=rows, bodies=bodies)

    def forbidden_publish(*_args):
        raise AssertionError('no file may be published before the final validation')
    monkeypatch.setattr(owner.InputMaterializationBatch, 'publish', forbidden_publish)
    with pytest.raises(ContextFileContentError) as failure_info:
        await materialize_run_context_files(**args)
    assert failure_info.value.attachment_index == 2
    assert not (args['workspace'] / 'inputs').exists()
    assert not list(tmp_path.glob('.input-stage-*'))
    assert list(tmp_path.iterdir()) == [args['workspace']]
    if failure in {'size', 'key'}:
        assert calls == ['authorize-a', 'authorize-b']

@pytest.mark.asyncio
@pytest.mark.parametrize('branch', ['tempfile', 'bytes'])
async def test_input_batch_validates_all_then_preserves_bound_directory_inode(tmp_path, monkeypatch, branch):
    import os
    from app.sandbox.infrastructure import input_materialization as owner
    args, calls = await _batch_case(tmp_path, branch=branch)
    inputs = args['workspace'] / 'inputs'
    inputs.mkdir()
    (inputs / 'old.bin').write_bytes(b'old')
    descriptor = os.open(inputs, os.O_RDONLY | os.O_DIRECTORY)
    original_publish = owner.InputMaterializationBatch.publish

    def publish(batch, names):
        assert calls == ['authorize-a', 'authorize-b', 'download-a', 'download-b']
        assert sorted(os.listdir(descriptor)) == ['old.bin']
        original_publish(batch, names)
    monkeypatch.setattr(owner.InputMaterializationBatch, 'publish', publish)
    try:
        result = await materialize_run_context_files(**args)
        assert result.materialized_file_names == ('a.bin', 'b.bin')
        assert os.fstat(descriptor).st_ino == inputs.stat().st_ino
        assert sorted(os.listdir(descriptor)) == ['a.bin', 'b.bin', 'old.bin']
        assert (inputs / 'old.bin').read_bytes() == b'old'
    finally:
        os.close(descriptor)
    assert not list(tmp_path.glob('.input-stage-*'))

@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['second-link', 'racing-target', 'replaced-first', 'cleanup-failure'])
async def test_input_batch_rolls_back_only_owned_links(tmp_path, monkeypatch, caplog, failure):
    import errno
    import os
    from app.context.api import ContextFileContentError
    from app.sandbox.infrastructure import input_materialization as owner
    args, _ = await _batch_case(tmp_path)
    inputs = args['workspace'] / 'inputs'
    inputs.mkdir()
    (inputs / 'old.bin').write_bytes(b'old')
    original_link, original_unlink = (os.link, os.unlink)

    def link(source, target, **kwargs):
        if source == 'b.bin':
            if failure == 'racing-target':
                (inputs / 'b.bin').write_bytes(b'foreign')
                return original_link(source, target, **kwargs)
            if failure == 'replaced-first':
                (inputs / 'a.bin').unlink()
                (inputs / 'a.bin').write_bytes(b'foreign')
            raise OSError(errno.ENOSPC, 'synthetic publish failure')
        return original_link(source, target, **kwargs)

    def unlink(path, **kwargs):
        if failure == 'cleanup-failure' and path == 'a.bin' and (kwargs.get('dir_fd') is not None):
            raise PermissionError('synthetic rollback failure')
        return original_unlink(path, **kwargs)
    monkeypatch.setattr(owner.os, 'link', link)
    monkeypatch.setattr(owner.os, 'supports_dir_fd', os.supports_dir_fd | {link})
    monkeypatch.setattr(owner.os, 'unlink', unlink)
    with pytest.raises(ContextFileContentError) as error:
        await materialize_run_context_files(**args)
    assert (inputs / 'old.bin').read_bytes() == b'old'
    if failure == 'racing-target':
        assert error.value.code == 'context_file_name_conflict'
        assert error.value.attachment_index == 2
        assert (inputs / 'b.bin').read_bytes() == b'foreign'
        assert not (inputs / 'a.bin').exists()
    elif failure == 'replaced-first':
        assert (inputs / 'a.bin').read_bytes() == b'foreign'
    elif failure == 'cleanup-failure':
        assert error.value.__cause__.errno == errno.ENOSPC
        assert 'input_materialization_rollback_failed' in caplog.text
    else:
        assert sorted((p.name for p in inputs.iterdir())) == ['old.bin']

@pytest.mark.asyncio
@pytest.mark.parametrize('blocked', ['download', 'copy', 'validation'])
async def test_input_batch_settles_late_io_before_repeated_cancel_cleanup(tmp_path, monkeypatch, blocked):
    import asyncio
    import threading
    from app.context import file_continuity as materializer
    from app.sandbox.infrastructure import input_materialization as owner
    args, _ = await _batch_case(tmp_path, branch='bytes' if blocked == 'validation' else 'tempfile')
    entered, release = (threading.Event(), threading.Event())
    if blocked == 'download':
        original = args['storage'].download_to_tempfile

        def operation(**kwargs):
            entered.set()
            assert release.wait(5)
            return original(**kwargs)
        monkeypatch.setattr(args['storage'], 'download_to_tempfile', operation)
    elif blocked == 'copy':
        original = owner.InputMaterializationBatch.copy_file

        def operation(*arguments):
            entered.set()
            assert release.wait(5)
            return original(*arguments)
        monkeypatch.setattr(owner.InputMaterializationBatch, 'copy_file', operation)
    else:
        original = materializer.validate_context_file_for_stage

        def operation(*arguments):
            entered.set()
            assert release.wait(5)
            return original(*arguments)
        monkeypatch.setattr(materializer, 'validate_context_file_for_stage', operation)
    task = asyncio.create_task(materialize_run_context_files(**args))
    try:
        for _ in range(1000):
            if entered.is_set():
                break
            await asyncio.sleep(0.001)
        assert entered.is_set()
        task.cancel()
        await asyncio.sleep(0.01)
        task.cancel()
        await asyncio.sleep(0.01)
        assert not task.done()
        assert not (args['workspace'] / 'inputs').exists()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert list(tmp_path.iterdir()) == [args['workspace']]
        assert list(args['workspace'].iterdir()) == []
    finally:
        release.set()
        if not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['inputs', 'workspace', 'ancestor'])
async def test_input_batch_refuses_symlink_directory_ownership(tmp_path, kind):
    from app.context.api import ContextFileContentError
    args, _ = await _batch_case(tmp_path)
    outside = tmp_path / 'outside'
    outside.mkdir()
    if kind == 'inputs':
        (args['workspace'] / 'inputs').symlink_to(outside, target_is_directory=True)
    elif kind == 'workspace':
        args['workspace'].rmdir()
        args['workspace'].symlink_to(outside, target_is_directory=True)
    else:
        link = tmp_path / 'alias'
        link.symlink_to(tmp_path, target_is_directory=True)
        args['workspace'] = link / 'workspace'
    with pytest.raises(ContextFileContentError, match='context_file_staging_write_failed'):
        await materialize_run_context_files(**args)
    assert list(outside.iterdir()) == []
    assert not list(tmp_path.glob('.input-stage-*'))


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["cross-device", "unsupported-dirfd", "workspace-replaced"])
async def test_input_batch_fails_closed_without_unsafe_publication_fallback(tmp_path, monkeypatch, kind):
    import errno
    import os
    from app.sandbox.infrastructure import input_materialization as owner

    arguments, _ = await _batch_case(tmp_path)
    if kind == "unsupported-dirfd":
        monkeypatch.setattr(owner.os, "supports_dir_fd", set())
    elif kind == "cross-device":
        def link(*args, **kwargs):
            raise OSError(errno.EXDEV, "synthetic separate filesystem")
        monkeypatch.setattr(owner.os, "link", link)
        monkeypatch.setattr(owner.os, "supports_dir_fd", os.supports_dir_fd | {link})
    else:
        original = owner.InputMaterializationBatch.publish
        def publish(batch, names):
            workspace = arguments["workspace"]
            workspace.rename(tmp_path / "old-workspace")
            workspace.mkdir()
            original(batch, names)
        monkeypatch.setattr(owner.InputMaterializationBatch, "publish", publish)
    with pytest.raises(ContextFileContentError, match="context_file_staging_write_failed"):
        await materialize_run_context_files(**arguments)
    assert not (arguments["workspace"] / "inputs").exists()
    assert not list(tmp_path.glob(".input-stage-*"))


@pytest.mark.asyncio
async def test_input_batch_joins_timed_out_download_before_cleanup(tmp_path, monkeypatch):
    import asyncio
    import threading
    from app.storage import run_storage_io, StorageIOTimeoutError

    arguments, _ = await _batch_case(tmp_path)
    entered, release = threading.Event(), threading.Event()
    download = arguments["storage"].download_to_tempfile
    timed_out = asyncio.Event()
    def blocked_download(**kwargs):
        entered.set()
        assert release.wait(5)
        return download(**kwargs)
    monkeypatch.setattr(arguments["storage"], "download_to_tempfile", blocked_download)
    async def bounded_io(operation, *args, **kwargs):
        try:
            return await run_storage_io(operation, *args, timeout_seconds=.01, **kwargs)
        except StorageIOTimeoutError:
            timed_out.set()
            raise
    arguments["storage_io"] = bounded_io
    task = asyncio.create_task(materialize_run_context_files(**arguments))
    try:
        await asyncio.wait_for(timed_out.wait(), 2)
        assert entered.is_set() and not task.done()
        assert not (arguments["workspace"] / "inputs").exists()
        release.set()
        with pytest.raises(ContextFileContentError) as error:
            await task
        assert error.value.code == "context_file_storage_unavailable"
        assert isinstance(error.value.__cause__, StorageIOTimeoutError)
        assert list(tmp_path.iterdir()) == [arguments["workspace"]]
    finally:
        release.set()
        if not task.done():
            try:
                await task
            except ContextFileContentError:
                pass


@pytest.mark.asyncio
async def test_input_batch_preserves_cause_when_inputs_appears_before_publish(tmp_path, monkeypatch):
    import errno
    from app.sandbox.infrastructure import input_materialization as owner

    arguments, _ = await _batch_case(tmp_path)
    inputs = arguments["workspace"] / "inputs"
    original_publish = owner.InputMaterializationBatch.publish
    created_inode = []
    def competing_creation(batch, names):
        assert batch.inputs_fd == -1
        inputs.mkdir()
        (inputs / "foreign.bin").write_bytes(b"foreign")
        created_inode.append(inputs.stat().st_ino)
        original_publish(batch, names)
    monkeypatch.setattr(owner.InputMaterializationBatch, "publish", competing_creation)
    with pytest.raises(ContextFileContentError) as error:
        await materialize_run_context_files(**arguments)
    assert error.value.code == "context_file_staging_write_failed"
    assert error.value.attachment_index is None
    assert isinstance(error.value.__cause__, FileExistsError)
    assert error.value.__cause__.errno == errno.EEXIST
    assert inputs.stat().st_ino == created_inode[0]
    assert [path.name for path in inputs.iterdir()] == ["foreign.bin"]
    assert (inputs / "foreign.bin").read_bytes() == b"foreign"
    assert not list(tmp_path.glob(".input-stage-*"))
