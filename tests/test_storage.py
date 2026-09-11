import asyncio
import os
from pathlib import Path
import threading

import pytest

import app.storage as storage_module
from app.storage import (
    ObjectStorage,
    ObjectStorageSizeLimitError,
    StorageIOBusyError,
    StorageIOTimeoutError,
    run_storage_io,
)


class _Body:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.position = 0
        self.closed = False

    def read(self, size: int) -> bytes:
        start = self.position
        self.position = min(len(self.payload), self.position + size)
        return self.payload[start : self.position]

    def close(self) -> None:
        self.closed = True


class _Client:
    def __init__(self, body: _Body) -> None:
        self.body = body

    def get_object(self, *, Bucket: str, Key: str):
        assert (Bucket, Key) == ("bucket", "private/file.xlsx")
        return {"Body": self.body}


def _storage(payload: bytes) -> tuple[ObjectStorage, _Body]:
    body = _Body(payload)
    storage = ObjectStorage.__new__(ObjectStorage)
    storage.bucket = "bucket"
    storage.client = _Client(body)
    return storage, body


def test_download_to_tempfile_writes_payload_and_closes_body(tmp_path, monkeypatch):
    storage, body = _storage(b"downloaded")
    monkeypatch.setattr("app.storage.tempfile.gettempdir", lambda: str(tmp_path))

    downloaded = storage.download_to_tempfile(storage_key="private/file.xlsx", max_bytes=10)
    try:
        assert Path(downloaded.path).read_bytes() == b"downloaded"
        assert downloaded.size_bytes == 10
        assert downloaded.sha256
        assert body.closed is True
    finally:
        os.unlink(downloaded.path)


def test_download_to_tempfile_removes_partial_file_on_limit_error(tmp_path, monkeypatch):
    storage, body = _storage(b"oversize")
    monkeypatch.setattr("app.storage.tempfile.gettempdir", lambda: str(tmp_path))

    with pytest.raises(ObjectStorageSizeLimitError):
        storage.download_to_tempfile(storage_key="private/file.xlsx", max_bytes=7)

    assert body.closed is True
    assert list(tmp_path.iterdir()) == []


def test_get_bytes_bounded_returns_streamed_payload_and_closes_body():
    storage, body = _storage(b"preview")

    assert storage.get_bytes_bounded(storage_key="private/file.xlsx", max_bytes=7) == b"preview"
    assert body.closed is True


def test_get_bytes_bounded_rejects_max_plus_one_and_closes_body():
    storage, body = _storage(b"oversize")

    with pytest.raises(ObjectStorageSizeLimitError):
        storage.get_bytes_bounded(storage_key="private/file.xlsx", max_bytes=7)

    assert body.closed is True


def test_abort_multipart_uploads_for_key_is_exact_and_paginated():
    requests: list[dict[str, object]] = []
    aborted: list[tuple[str, str]] = []

    class Client:
        def list_multipart_uploads(self, **kwargs):
            requests.append(kwargs)
            if len(requests) == 1:
                return {
                    "Uploads": [
                        {"Key": "private/claim", "UploadId": "upload-a"},
                        {"Key": "private/claim-other", "UploadId": "upload-other"},
                    ],
                    "IsTruncated": True,
                    "NextKeyMarker": "private/claim",
                    "NextUploadIdMarker": "upload-a",
                }
            return {
                "Uploads": [{"Key": "private/claim", "UploadId": "upload-b"}],
                "IsTruncated": False,
            }

        def abort_multipart_upload(self, *, Bucket, Key, UploadId):
            assert Bucket == "bucket"
            aborted.append((Key, UploadId))

    storage = ObjectStorage.__new__(ObjectStorage)
    storage.bucket = "bucket"
    storage.client = Client()
    storage.ensure_bucket = lambda: None

    assert storage.abort_multipart_uploads_for_key(storage_key="private/claim") == 2
    assert aborted == [("private/claim", "upload-a"), ("private/claim", "upload-b")]
    assert requests[1]["KeyMarker"] == "private/claim"
    assert requests[1]["UploadIdMarker"] == "upload-a"


def test_cleanup_multipart_upload_deletes_completed_object_after_no_such_upload():
    operations: list[str] = []

    class NoSuchUploadError(Exception):
        response = {"Error": {"Code": "NoSuchUpload"}}

    class Client:
        def abort_multipart_upload(self, **_kwargs):
            operations.append("abort")
            raise NoSuchUploadError

        def delete_object(self, **_kwargs):
            operations.append("delete")

    storage = ObjectStorage.__new__(ObjectStorage)
    storage.bucket = "bucket"
    storage.client = Client()

    storage.cleanup_multipart_upload(storage_key="private/file", upload_id="consumed")

    assert operations == ["abort", "delete"]


def test_storage_io_does_not_block_event_loop(monkeypatch):
    async def scenario() -> None:
        monkeypatch.setattr(storage_module, "_STORAGE_IO_SLOTS", asyncio.Semaphore(1))
        started = threading.Event()
        release = threading.Event()

        def blocking_call() -> str:
            started.set()
            release.wait()
            return "done"

        task = asyncio.create_task(run_storage_io(blocking_call))
        await asyncio.to_thread(started.wait)
        await asyncio.sleep(0)
        assert task.done() is False
        release.set()
        assert await task == "done"

    asyncio.run(scenario())


def test_storage_io_rejects_work_beyond_admission_limit(monkeypatch):
    async def scenario() -> None:
        monkeypatch.setattr(storage_module, "_STORAGE_IO_ADMISSIONS", asyncio.BoundedSemaphore(1))
        monkeypatch.setattr(storage_module, "_STORAGE_IO_SLOTS", asyncio.Semaphore(1))
        started = threading.Event()
        release = threading.Event()

        def blocking_call() -> str:
            started.set()
            release.wait()
            return "done"

        first = asyncio.create_task(run_storage_io(blocking_call, timeout_seconds=0.01))
        await asyncio.to_thread(started.wait)
        with pytest.raises(StorageIOTimeoutError):
            await first
        with pytest.raises(StorageIOBusyError, match="storage_io_busy"):
            await run_storage_io(lambda: "rejected")
        release.set()
        await asyncio.sleep(0.01)
        assert await run_storage_io(lambda: "done") == "done"

    asyncio.run(scenario())


def test_storage_io_timeout_holds_permit_until_thread_finishes(monkeypatch):
    async def scenario() -> None:
        monkeypatch.setattr(storage_module, "_STORAGE_IO_SLOTS", asyncio.Semaphore(1))
        started = threading.Event()
        release = threading.Event()
        abandoned = threading.Event()

        def blocking_call() -> None:
            started.set()
            release.wait()

        first = asyncio.create_task(
            run_storage_io(
                blocking_call,
                timeout_seconds=0.01,
                on_abandoned=abandoned.set,
            )
        )
        await asyncio.to_thread(started.wait)
        with pytest.raises(StorageIOTimeoutError):
            await first
        assert abandoned.is_set()
        second = asyncio.create_task(run_storage_io(lambda: "next"))
        await asyncio.sleep(0.01)
        assert second.done() is False
        release.set()
        assert await second == "next"

    asyncio.run(scenario())


def test_storage_io_returns_result_when_task_finishes_at_timeout(monkeypatch):
    real_wait_for = asyncio.wait_for

    async def scenario() -> None:
        monkeypatch.setattr(storage_module, "_STORAGE_IO_SLOTS", asyncio.Semaphore(1))
        calls = 0

        async def finish_then_timeout(awaitable, *, timeout):
            nonlocal calls
            calls += 1
            if calls == 1:
                return await real_wait_for(awaitable, timeout=timeout)
            await awaitable
            raise TimeoutError

        abandoned = threading.Event()
        monkeypatch.setattr(storage_module.asyncio, "wait_for", finish_then_timeout)

        assert await run_storage_io(
            lambda: "finished",
            timeout_seconds=0.01,
            on_abandoned=abandoned.set,
        ) == "finished"
        assert abandoned.is_set() is False

    asyncio.run(scenario())


def test_storage_io_cancellation_holds_permit_until_thread_finishes(monkeypatch):
    async def scenario() -> None:
        monkeypatch.setattr(storage_module, "_STORAGE_IO_SLOTS", asyncio.Semaphore(1))
        started = threading.Event()
        release = threading.Event()
        abandoned = threading.Event()

        def blocking_call() -> None:
            started.set()
            release.wait()

        first = asyncio.create_task(
            run_storage_io(blocking_call, on_abandoned=abandoned.set)
        )
        await asyncio.to_thread(started.wait)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        assert abandoned.is_set()
        second = asyncio.create_task(run_storage_io(lambda: "next"))
        await asyncio.sleep(0.01)
        assert second.done() is False
        release.set()
        assert await second == "next"

    asyncio.run(scenario())
