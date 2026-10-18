from contextlib import asynccontextmanager

import pytest

from app.sandbox.infrastructure.input_materialization import InputMaterializationBatch
from app.platform.storage_io_completion import StorageIOCompletion

from app.context.file_continuity import materialize_run_context_files


@pytest.mark.asyncio
async def test_materialize_run_context_files_disambiguates_duplicate_names(tmp_path):
    rows = {
        "file-a": {
            "original_name": "book.txt",
            "content_type": "text/plain",
            "size_bytes": 5,
            "storage_key": "files/a",
        },
        "file-b": {
            "original_name": "book.txt",
            "content_type": "text/plain",
            "size_bytes": 6,
            "storage_key": "files/b",
        },
    }
    contents = {"files/a": b"first", "files/b": b"second"}

    class Repository:
        async def get_scoped_context_file(self, _conn, **kwargs):
            return rows[kwargs["file_id"]]

    class Storage:
        def get_bytes_bounded(self, *, storage_key, max_bytes):
            content = contents[storage_key]
            assert max_bytes == len(content)
            return content

    @asynccontextmanager
    async def transaction_factory():
        yield object()

    from app.storage import run_storage_io as storage_io

    result = await materialize_run_context_files(
        input_batch_factory=InputMaterializationBatch, storage_completion_factory=StorageIOCompletion, transaction_factory=transaction_factory,
        repository=Repository(),
        storage=Storage(),
        storage_io=storage_io,
        storage_size_limit_error=RuntimeError,
        workspace=tmp_path,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        session_id="session-a",
        run_id="run-a",
        file_ids=["file-a", "file-b"],
    )

    assert result.file_names == ("book.txt", "book (2).txt")
    assert result.materialized_file_names == ("book.txt", "book (2).txt")
    assert (tmp_path / "inputs" / "book.txt").read_bytes() == b"first"
    assert (tmp_path / "inputs" / "book (2).txt").read_bytes() == b"second"
