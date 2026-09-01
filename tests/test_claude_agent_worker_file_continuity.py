import hashlib
import io
from contextlib import asynccontextmanager
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from docx import Document
from pypdf import PdfWriter

from app.context.api import ContextFileContentError
from app.context.file_content import DOCX_CONTENT_TYPE, PDF_CONTENT_TYPE
from app.executors.base import RunPayload
from app.executors.claude_agent_worker import ClaudeAgentWorkerAdapter
from app.file_parser_contracts import XLSX_CONTENT_TYPE
from app.storage import ObjectStorageSizeLimitError


def _docx_bytes() -> bytes:
    stream = io.BytesIO()
    document = Document()
    document.add_paragraph("snapshot-authorized-content")
    document.save(stream)
    return stream.getvalue()


def _unsafe_pdf_bytes() -> bytes:
    stream = io.BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.add_js("app.alert('no')")
    writer.write(stream)
    return stream.getvalue()


def _docx_with_opaque_content_bytes() -> bytes:
    source = ZipFile(io.BytesIO(_docx_bytes()))
    stream = io.BytesIO()
    with source, ZipFile(stream, "w", compression=ZIP_DEFLATED) as archive:
        for entry in source.infolist():
            payload = source.read(entry)
            if entry.filename == "[Content_Types].xml":
                payload = payload.replace(
                    b"</Types>",
                    b'<Default Extension="bin" ContentType="application/octet-stream" /></Types>',
                )
            elif entry.filename == "word/_rels/document.xml.rels":
                payload = payload.replace(
                    b"</Relationships>",
                    (
                        b'<Relationship Id="rId900" '
                        b'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/package" '
                        b'Target="embeddings/opaque-package.bin" />'
                        b'<Relationship Id="rId901" '
                        b'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/attachedTemplate" '
                        b'Target="https://example.invalid/template.dotx" TargetMode="External" />'
                        b"</Relationships>"
                    ),
                )
            archive.writestr(entry, payload)
        archive.writestr("word/embeddings/opaque-package.bin", b"opaque package bytes")
    return stream.getvalue()


def payload(*, file_ids: list[str]) -> RunPayload:
    skill_version = "test-skill-version"
    return RunPayload(
        tenant_id="default",
        workspace_id="default",
        user_id="user-a",
        session_id="ses_1",
        run_id="run_1",
        attempt_id="qat-test-attempt",
        agent_id="translate",
        skill_id="baoyu-translate",
        file_ids=file_ids,
        input={},
        skill_version=skill_version,
        release_decision={
            "schema_version": "ai-platform.skill-release-decision.v1",
            "selected_version": skill_version,
        },
        skill_manifests=[
            {
                "skill_id": "baoyu-translate",
                "content_hash": skill_version,
            }
        ],
    )


@pytest.mark.asyncio
async def test_materialize_files_accepts_prior_run_file_authorized_by_current_snapshot(
    monkeypatch,
    tmp_path,
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    raw = _docx_with_opaque_content_bytes()
    display_name = "参考文件1-IP248A项目基本信息收集表.docx"

    class FakeStorage:
        def get_bytes_bounded(self, *, storage_key, max_bytes):
            assert storage_key == "files/file-prior/content"
            assert max_bytes == len(raw)
            return raw

    @asynccontextmanager
    async def fake_transaction():
        yield object()

    async def fake_get_scoped_context_file(_conn, **kwargs):
        assert kwargs == {
            "tenant_id": "default",
            "workspace_id": "default",
            "user_id": "user-a",
            "session_id": "ses_1",
            "run_id": "run_1",
            "file_id": "file-prior",
        }
        return {
            "run_id": "run-prior",
            "original_name": display_name,
            "content_type": DOCX_CONTENT_TYPE,
            "size_bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "storage_key": "files/file-prior/content",
        }

    adapter = ClaudeAgentWorkerAdapter()
    monkeypatch.setattr("app.executors.claude_agent_worker.ObjectStorage", FakeStorage)
    monkeypatch.setattr(
        "app.executors.claude_agent_worker.repositories.get_scoped_context_file",
        fake_get_scoped_context_file,
    )
    monkeypatch.setattr("app.executors.claude_agent_worker.transaction", fake_transaction)

    materialized = await adapter._materialize_files(
        payload(file_ids=["file-prior"]),
        workspace,
    )

    assert list(materialized) == [display_name]
    assert materialized.materialized_file_names == [display_name]
    assert (workspace / "inputs" / display_name).read_bytes() == raw


@pytest.mark.asyncio
async def test_materialize_files_fails_when_primary_file_is_not_snapshot_authorized(
    monkeypatch,
    tmp_path,
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    @asynccontextmanager
    async def fake_transaction():
        yield object()

    async def missing_file(*args, **kwargs):
        return None

    adapter = ClaudeAgentWorkerAdapter()
    monkeypatch.setattr(
        "app.executors.claude_agent_worker.repositories.get_scoped_context_file",
        missing_file,
    )
    monkeypatch.setattr("app.executors.claude_agent_worker.transaction", fake_transaction)

    with pytest.raises(ValueError, match="context_file_unavailable"):
        await adapter._materialize_files(payload(file_ids=["file-missing"]), workspace)


@pytest.mark.asyncio
@pytest.mark.parametrize("mismatch", ["sha256", "size_bytes"])
async def test_materialize_files_fails_when_snapshot_file_identity_mismatches(
    monkeypatch,
    tmp_path,
    mismatch,
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    raw = b"stored-content"

    class FakeStorage:
        def get_bytes_bounded(self, *, storage_key, max_bytes):
            return raw

    @asynccontextmanager
    async def fake_transaction():
        yield object()

    async def fake_get_scoped_context_file(_conn, **kwargs):
        row = {
            "original_name": "source.xlsx",
            "content_type": XLSX_CONTENT_TYPE,
            "size_bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "storage_key": "files/source.xlsx",
        }
        row[mismatch] = "0" * 64 if mismatch == "sha256" else len(raw) + 1
        return row

    adapter = ClaudeAgentWorkerAdapter()
    monkeypatch.setattr("app.executors.claude_agent_worker.ObjectStorage", FakeStorage)
    monkeypatch.setattr(
        "app.executors.claude_agent_worker.repositories.get_scoped_context_file",
        fake_get_scoped_context_file,
    )
    monkeypatch.setattr("app.executors.claude_agent_worker.transaction", fake_transaction)

    with pytest.raises(ValueError, match="context_file_identity_mismatch"):
        await adapter._materialize_files(payload(file_ids=["file-prior"]), workspace)


@pytest.mark.asyncio
async def test_materialize_files_uses_bounded_object_read_and_rejects_oversized_storage(
    monkeypatch,
    tmp_path,
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    calls = []

    class FakeStorage:
        def get_bytes_bounded(self, *, storage_key, max_bytes):
            calls.append((storage_key, max_bytes))
            raise ObjectStorageSizeLimitError("object_size_limit_exceeded")

    @asynccontextmanager
    async def fake_transaction():
        yield object()

    async def fake_get_scoped_context_file(_conn, **_kwargs):
        return {
            "original_name": "source.docx",
            "content_type": DOCX_CONTENT_TYPE,
            "size_bytes": 12,
            "storage_key": "files/source.docx",
        }

    adapter = ClaudeAgentWorkerAdapter()
    monkeypatch.setattr("app.executors.claude_agent_worker.ObjectStorage", FakeStorage)
    monkeypatch.setattr(
        "app.executors.claude_agent_worker.repositories.get_scoped_context_file",
        fake_get_scoped_context_file,
    )
    monkeypatch.setattr("app.executors.claude_agent_worker.transaction", fake_transaction)

    with pytest.raises(ValueError, match="context_file_identity_mismatch"):
        await adapter._materialize_files(payload(file_ids=["file-prior"]), workspace)

    assert calls == [("files/source.docx", 12)]
    assert list(workspace.iterdir()) == []


@pytest.mark.asyncio
async def test_materialize_files_rejects_declared_total_before_object_reads(
    monkeypatch,
    tmp_path,
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    class FakeStorage:
        def get_bytes_bounded(self, **_kwargs):
            raise AssertionError("declared total must fail before object reads")

    @asynccontextmanager
    async def fake_transaction():
        yield object()

    async def fake_get_scoped_context_file(_conn, **kwargs):
        return {
            "original_name": f"{kwargs['file_id']}.docx",
            "content_type": DOCX_CONTENT_TYPE,
            "size_bytes": 2,
            "storage_key": f"files/{kwargs['file_id']}.docx",
        }

    adapter = ClaudeAgentWorkerAdapter()
    monkeypatch.setattr("app.executors.claude_agent_worker.ObjectStorage", FakeStorage)
    monkeypatch.setattr(
        "app.context.file_continuity._MAX_CONTEXT_FILE_STAGE_TOTAL_BYTES",
        3,
    )
    monkeypatch.setattr(
        "app.executors.claude_agent_worker.repositories.get_scoped_context_file",
        fake_get_scoped_context_file,
    )
    monkeypatch.setattr("app.executors.claude_agent_worker.transaction", fake_transaction)

    with pytest.raises(ValueError, match="context_file_too_large"):
        await adapter._materialize_files(payload(file_ids=["file-a", "file-b"]), workspace)

    assert list(workspace.iterdir()) == []


@pytest.mark.asyncio
async def test_materialize_files_stages_pdf_active_content(monkeypatch, tmp_path):
    name = "active.pdf"
    raw = _unsafe_pdf_bytes()
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    class FakeStorage:
        def get_bytes_bounded(self, *, storage_key, max_bytes):
            assert max_bytes == len(raw)
            return raw

    @asynccontextmanager
    async def fake_transaction():
        yield object()

    async def fake_get_scoped_context_file(_conn, **_kwargs):
        return {
            "original_name": name,
            "content_type": PDF_CONTENT_TYPE,
            "size_bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "storage_key": f"files/{name}",
        }

    adapter = ClaudeAgentWorkerAdapter()
    monkeypatch.setattr("app.executors.claude_agent_worker.ObjectStorage", FakeStorage)
    monkeypatch.setattr(
        "app.executors.claude_agent_worker.repositories.get_scoped_context_file",
        fake_get_scoped_context_file,
    )
    monkeypatch.setattr("app.executors.claude_agent_worker.transaction", fake_transaction)

    materialized = await adapter._materialize_files(payload(file_ids=["file-active"]), workspace)

    assert list(materialized) == [name]
    assert (workspace / "inputs" / name).read_bytes() == raw


@pytest.mark.asyncio
async def test_materialize_files_reports_original_attachment_ordinal(monkeypatch, tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    contents = {
        "file-safe": _docx_bytes(),
        "file-unsafe": _unsafe_pdf_bytes(),
    }

    class FakeStorage:
        def get_bytes_bounded(self, *, storage_key, max_bytes):
            file_id = storage_key.rsplit("/", 1)[-1]
            raw = contents[file_id]
            assert max_bytes == len(raw)
            return raw

    @asynccontextmanager
    async def fake_transaction():
        yield object()

    async def fake_get_scoped_context_file(_conn, **kwargs):
        file_id = kwargs["file_id"]
        raw = contents[file_id]
        suffix = "docx" if file_id == "file-safe" else "pdf"
        content_type = DOCX_CONTENT_TYPE if suffix == "docx" else PDF_CONTENT_TYPE
        return {
            "original_name": f"{file_id}.{suffix}",
            "content_type": content_type,
            "size_bytes": len(raw),
            "sha256": (
                "0" * 64 if file_id == "file-unsafe" else hashlib.sha256(raw).hexdigest()
            ),
            "storage_key": f"files/{file_id}",
        }

    adapter = ClaudeAgentWorkerAdapter()
    monkeypatch.setattr("app.executors.claude_agent_worker.ObjectStorage", FakeStorage)
    monkeypatch.setattr(
        "app.executors.claude_agent_worker.repositories.get_scoped_context_file",
        fake_get_scoped_context_file,
    )
    monkeypatch.setattr("app.executors.claude_agent_worker.transaction", fake_transaction)

    with pytest.raises(ValueError, match="context_file_identity_mismatch") as captured:
        await adapter._materialize_files(
            payload(file_ids=["file-safe", "file-unsafe"]),
            workspace,
        )

    assert captured.value.attachment_index == 2
    assert captured.value.file_kind == "pdf"
    assert list(workspace.iterdir()) == []


@pytest.mark.asyncio
async def test_materialize_files_uses_real_scoped_repository_query_for_prior_run_file(
    monkeypatch,
    tmp_path,
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    raw = _docx_bytes()
    row = {
        "id": "file-prior",
        "run_id": "run-prior",
        "original_name": "prior.docx",
        "content_type": DOCX_CONTENT_TYPE,
        "size_bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "storage_key": "files/prior.docx",
    }

    class Cursor:
        async def fetchone(self):
            return row

    class Connection:
        sql = ""
        params = ()

        async def execute(self, sql, params):
            self.sql = " ".join(sql.split())
            self.params = params
            return Cursor()

    class FakeStorage:
        def get_bytes_bounded(self, *, storage_key, max_bytes):
            assert (storage_key, max_bytes) == ("files/prior.docx", len(raw))
            return raw

    conn = Connection()

    @asynccontextmanager
    async def real_repository_transaction():
        yield conn

    adapter = ClaudeAgentWorkerAdapter()
    monkeypatch.setattr("app.executors.claude_agent_worker.ObjectStorage", FakeStorage)
    monkeypatch.setattr(
        "app.executors.claude_agent_worker.transaction",
        real_repository_transaction,
    )

    materialized = await adapter._materialize_files(
        payload(file_ids=["file-prior"]),
        workspace,
    )

    assert list(materialized) == ["prior.docx"]
    assert "context_snapshot.included_file_ids ? files.id" in conn.sql
    assert "current_run.input_json->>'context_snapshot_id' = current_run.context_snapshot_id" in conn.sql
    assert conn.params == (
        "run_1",
        "default",
        "default",
        "user-a",
        "ses_1",
        "run_1",
        "file-prior",
    )
    assert (workspace / "inputs" / "prior.docx").read_bytes() == raw


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_point", ["mkdir", "write", "partial_write"])
async def test_materialize_files_cleans_all_written_copies_after_io_failure(
    monkeypatch,
    tmp_path,
    failure_point,
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    raw = _docx_bytes()

    class FakeStorage:
        def get_bytes_bounded(self, **_kwargs):
            return raw

    @asynccontextmanager
    async def fake_transaction():
        yield object()

    async def fake_get_scoped_context_file(_conn, **kwargs):
        return {
            "original_name": f"{kwargs['file_id']}.docx",
            "content_type": DOCX_CONTENT_TYPE,
            "size_bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "storage_key": f"files/{kwargs['file_id']}.docx",
        }

    original_write_bytes = type(workspace).write_bytes
    original_mkdir = type(workspace).mkdir

    def fail_inputs_mkdir(path, *args, **kwargs):
        if path == workspace / "inputs":
            raise OSError("simulated workspace directory failure")
        return original_mkdir(path, *args, **kwargs)

    def fail_second_canonical_write(path, content):
        if path.parent.name == "inputs" and path.name == "file-b.docx":
            if failure_point == "partial_write":
                original_write_bytes(path, b"partial")
            raise OSError("simulated workspace write failure")
        return original_write_bytes(path, content)

    adapter = ClaudeAgentWorkerAdapter()
    monkeypatch.setattr("app.executors.claude_agent_worker.ObjectStorage", FakeStorage)
    monkeypatch.setattr(
        "app.executors.claude_agent_worker.repositories.get_scoped_context_file",
        fake_get_scoped_context_file,
    )
    monkeypatch.setattr("app.executors.claude_agent_worker.transaction", fake_transaction)
    monkeypatch.setattr(
        type(workspace),
        "mkdir" if failure_point == "mkdir" else "write_bytes",
        fail_inputs_mkdir if failure_point == "mkdir" else fail_second_canonical_write,
    )

    with pytest.raises(
        ContextFileContentError,
        match="context_file_staging_write_failed",
    ) as captured:
        await adapter._materialize_files(
            payload(file_ids=["file-a", "file-b"]),
            workspace,
        )

    assert isinstance(captured.value.__cause__, OSError)
    assert list(workspace.iterdir()) == []


@pytest.mark.asyncio
async def test_materialize_files_preserves_preexisting_target_and_fails_before_object_read(
    monkeypatch,
    tmp_path,
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    inputs = workspace / "inputs"
    inputs.mkdir()
    existing = inputs / "file-a.docx"
    existing.write_bytes(b"preexisting-content")

    class FakeStorage:
        def get_bytes_bounded(self, **_kwargs):
            raise AssertionError("target collision must fail before object reads")

    @asynccontextmanager
    async def fake_transaction():
        yield object()

    async def fake_get_scoped_context_file(_conn, **_kwargs):
        return {
            "original_name": "file-a.docx",
            "content_type": DOCX_CONTENT_TYPE,
            "size_bytes": 1,
            "sha256": hashlib.sha256(b"x").hexdigest(),
            "storage_key": "files/file-a.docx",
        }

    adapter = ClaudeAgentWorkerAdapter()
    monkeypatch.setattr("app.executors.claude_agent_worker.ObjectStorage", FakeStorage)
    monkeypatch.setattr(
        "app.executors.claude_agent_worker.repositories.get_scoped_context_file",
        fake_get_scoped_context_file,
    )
    monkeypatch.setattr("app.executors.claude_agent_worker.transaction", fake_transaction)

    with pytest.raises(ValueError, match="context_file_name_conflict"):
        await adapter._materialize_files(payload(file_ids=["file-a"]), workspace)

    assert existing.read_bytes() == b"preexisting-content"
    assert list(workspace.iterdir()) == [inputs]
    assert list(inputs.iterdir()) == [existing]
