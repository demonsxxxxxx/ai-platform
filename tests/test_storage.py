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


def test_put_file_hashes_and_uploads_without_loading_the_whole_file(tmp_path):
    source = tmp_path / "profile-drive-import.bin"
    source.write_bytes(b"profile workspace preview")
    uploads: list[tuple[str, str, str, dict[str, str]]] = []

    class Client:
        def upload_file(self, filename, bucket, key, ExtraArgs):
            uploads.append((filename, bucket, key, ExtraArgs))

    storage = ObjectStorage.__new__(ObjectStorage)
    storage.bucket = "bucket"
    storage.client = Client()
    storage.ensure_bucket = lambda: None

    stored = storage.put_file(
        storage_key="private/profile-import",
        source_path=str(source),
        content_type="text/plain",
    )

    assert stored.size_bytes == len(b"profile workspace preview")
    assert stored.sha256 == "d4079665fab6d775dc20697ff67ccf7c0a8feecc2c3c78a5d9933475c663f60e"
    assert uploads == [
        (
            str(source),
            "bucket",
            "private/profile-import",
            {"ContentType": "text/plain"},
        )
    ]


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


@pytest.fixture
def bucket_client_factory(monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(storage_module, "get_settings", lambda: SimpleNamespace(
        s3_bucket="bucket", s3_endpoint_url="https://storage.invalid", s3_access_key_id="synthetic",
        s3_secret_access_key="synthetic", s3_region="synthetic",
    ))

    class Client:
        def __init__(self):
            self.lists = self.creates = 0
            self.puts = []
            self.exists = True
            self.list_error = self.create_error = self.put_error = None

        def list_buckets(self):
            self.lists += 1
            if self.list_error:
                raise self.list_error
            return {"Buckets": [{"Name": "bucket"}] if self.exists else []}

        def create_bucket(self, **kwargs):
            self.creates += 1
            assert isinstance(kwargs.get("Bucket"), str)
            if self.create_error:
                raise self.create_error
            self.exists = True

        def put_object(self, **kwargs):
            self.puts.append(kwargs)
            if self.put_error:
                raise self.put_error

    def create():
        client = Client()
        monkeypatch.setattr(storage_module.boto3, "client", lambda *_args, **_kwargs: client)
        return ObjectStorage(), client
    return create


def _put_synthetic(storage, index=0):
    return storage.put_bytes(storage_key=f"private/{index}", content=b"synthetic", content_type="text/plain")


@pytest.mark.parametrize("count", [1, 16, 128])
@pytest.mark.parametrize("exists", [True, False])
def test_bucket_confirmation_is_once_per_instance_and_preserves_uploads(bucket_client_factory, count, exists):
    import hashlib
    storage, client = bucket_client_factory()
    client.exists = exists
    for index in range(count):
        result = _put_synthetic(storage, index)
        assert result == storage_module.StoredObject(f"private/{index}", hashlib.sha256(b"synthetic").hexdigest(), 9)
        assert client.puts[index] == {"Bucket": "bucket", "Key": f"private/{index}", "Body": b"synthetic", "ContentType": "text/plain"}
    assert client.lists == 1 and client.creates == int(not exists)
    assert len(client.puts) == count


@pytest.mark.parametrize("phase,code", [("list", "AccessDenied"), ("list", "RequestTimeout"),
                                        ("create", "BucketAlreadyExists"), ("create", "BucketAlreadyOwnedByYou")])
def test_bucket_confirmation_does_not_cache_or_swallow_failure(bucket_client_factory, phase, code):
    from botocore.exceptions import ClientError
    storage, client = bucket_client_factory()
    client.exists = phase != "create"
    failure = ClientError({"Error": {"Code": code}}, phase)
    setattr(client, f"{phase}_error", failure)
    with pytest.raises(ClientError) as caught:
        _put_synthetic(storage)
    assert caught.value is failure and client.puts == []
    setattr(client, f"{phase}_error", None)
    _put_synthetic(storage)
    assert client.lists == 2 and len(client.puts) == 1


@pytest.mark.parametrize("code", ["NoSuchBucket", "AccessDenied", "RequestTimeout"])
def test_bucket_write_error_propagates_without_replay_and_missing_invalidates(bucket_client_factory, code):
    from botocore.exceptions import ClientError
    storage, client = bucket_client_factory()
    _put_synthetic(storage)
    failure = ClientError({"Error": {"Code": code}}, "PutObject")
    client.put_error = failure
    with pytest.raises(ClientError) as caught:
        _put_synthetic(storage, 1)
    assert caught.value is failure and len(client.puts) == 2
    client.put_error = None
    _put_synthetic(storage, 2)
    assert len(client.puts) == 3
    assert client.lists == (2 if code == "NoSuchBucket" else 1)


def test_bucket_confirmation_is_single_flight_within_instance(bucket_client_factory):
    from concurrent.futures import ThreadPoolExecutor
    storage, client = bucket_client_factory()
    original = client.list_buckets
    started, release = threading.Event(), threading.Event()

    def list_buckets():
        started.set()
        assert release.wait(2)
        return original()

    client.list_buckets = list_buckets
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(_put_synthetic, storage, i) for i in range(8)]
        try:
            assert started.wait(2)
        finally:
            release.set()
        assert all(future.result(timeout=2).size_bytes == 9 for future in futures)
    assert client.lists == 1 and len(client.puts) == 8


def test_bucket_confirmation_does_not_cross_instance_configuration(bucket_client_factory):
    first, first_client = bucket_client_factory()
    second, second_client = bucket_client_factory()
    second.bucket = "different-bucket"
    first.ensure_bucket()
    second.ensure_bucket()
    assert first_client.lists == second_client.lists == 1
    assert first_client.creates == 0 and second_client.creates == 1


def test_wrapped_upload_file_missing_bucket_invalidates_without_replay(bucket_client_factory, tmp_path):
    from botocore.exceptions import ClientError
    storage, client = bucket_client_factory()
    source = tmp_path / "synthetic.txt"
    source.write_bytes(b"synthetic")
    calls = []

    def upload_file(*_args, **_kwargs):
        calls.append(1)
        try:
            raise ClientError({"Error": {"Code": "NoSuchBucket"}}, "PutObject")
        except ClientError as cause:
            raise RuntimeError("synthetic wrapper") from cause

    client.upload_file = upload_file
    with pytest.raises(RuntimeError, match="synthetic wrapper"):
        storage.put_file(storage_key="private/file", source_path=str(source), content_type="text/plain")
    assert len(calls) == 1
    _put_synthetic(storage)
    assert client.lists == 2


def test_artifact_batch_uses_one_confirmation_and_preserves_reservation_order(bucket_client_factory, tmp_path):
    from app.execution.api import collect_workspace_artifacts
    storage, client = bucket_client_factory()
    output = tmp_path / "output"
    output.mkdir()
    for name in ["a.txt", "b.txt"]:
        (output / name).write_bytes(b"synthetic")
    operations = []
    original_put = client.put_object

    def put(**kwargs):
        operations.append(("put", kwargs["Key"]))
        original_put(**kwargs)

    def reserve(key):
        operations.append(("reserve", key))
        return "synthetic-receipt"

    client.put_object = put
    artifacts = collect_workspace_artifacts(
        tenant_id="synthetic", workspace_id="synthetic", session_id="synthetic", run_id="synthetic",
        source_executor="synthetic", workspace=tmp_path, response_files=["output/a.txt", "output/b.txt"],
        required_artifact_types=(), artifact_factory=lambda **kwargs: kwargs, storage_factory=lambda: storage,
        ensure_inside=lambda root, path, _message: path.relative_to(root), reserve_storage=reserve,
    )
    assert [item[0] for item in operations] == ["reserve", "put", "reserve", "put"]
    assert operations[0][1] == operations[1][1] and operations[2][1] == operations[3][1]
    assert client.lists == 1 and len(client.puts) == 2
    assert [artifact["manifest"]["delivery_position"] for artifact in artifacts] == [0, 1]
    assert all(artifact["provisional_cleanup_id"] == "synthetic-receipt" for artifact in artifacts)


def test_multipart_operations_reuse_bucket_confirmation_not_upload_state(bucket_client_factory):
    storage, client = bucket_client_factory()
    initialized = []
    listed = []

    def initialize(**kwargs):
        initialized.append(kwargs["Key"])
        return {"UploadId": f"upload-{len(initialized)}"}

    def list_uploads(**kwargs):
        listed.append(kwargs)
        return {"Uploads": [], "IsTruncated": False}

    client.create_multipart_upload, client.list_multipart_uploads = initialize, list_uploads
    assert storage.create_multipart_upload(storage_key="a", content_type="text/plain") == "upload-1"
    assert storage.create_multipart_upload(storage_key="b", content_type="text/plain") == "upload-2"
    assert storage.abort_multipart_uploads_for_key(storage_key="a") == 0
    assert initialized == ["a", "b"] and listed == [{"Bucket": "bucket", "Prefix": "a"}]
    assert client.lists == 1


def test_multipart_cleanup_abort_missing_bucket_invalidates_without_replay(bucket_client_factory):
    from botocore.exceptions import ClientError
    storage, client = bucket_client_factory()
    aborts = []
    failure = ClientError({"Error": {"Code": "NoSuchBucket"}}, "AbortMultipartUpload")
    client.list_multipart_uploads = lambda **kwargs: {"Uploads": [{"Key": "a", "UploadId": "u"}]}

    def abort(**kwargs):
        aborts.append(kwargs)
        raise failure

    client.abort_multipart_upload = abort
    with pytest.raises(ClientError) as caught:
        storage.abort_multipart_uploads_for_key(storage_key="a")
    assert caught.value is failure and len(aborts) == 1
    _put_synthetic(storage)
    assert client.lists == 2
