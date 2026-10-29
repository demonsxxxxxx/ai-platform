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


@pytest.mark.parametrize(
    ("failure_stage", "cleanup_failures"),
    [
        ("create", ()),
        ("create", ("body_close",)),
        ("read", ()),
        ("read", ("temporary_close", "body_close", "unlink")),
        ("write", ()),
        ("write", ("temporary_close", "body_close", "unlink")),
        ("flush", ()),
        ("flush", ("temporary_close", "body_close", "unlink")),
        ("result", ()),
        ("result", ("temporary_close", "body_close", "unlink")),
        ("limit", ()),
        ("limit", ("temporary_close", "body_close", "unlink")),
        ("temporary_close", ()),
        ("temporary_close", ("body_close", "unlink")),
        ("body_close", ()),
        ("body_close", ("unlink",)),
        ("cancelled", ("temporary_close", "body_close", "unlink")),
    ],
)
def test_download_to_tempfile_keeps_primary_failure_and_cleans_every_resource(
    monkeypatch, failure_stage, cleanup_failures,
):
    storage, _body = _storage(b"x")
    events = []
    primary = (
        asyncio.CancelledError("synthetic primary cancellation")
        if failure_stage == "cancelled"
        else OSError("synthetic primary failure")
    )

    def at(stage):
        events.append(stage)
        if stage == failure_stage or (stage == "read" and failure_stage == "cancelled"):
            raise primary
        if stage in cleanup_failures:
            raise OSError("synthetic private cleanup detail")

    class Body(_Body):
        def read(self, size):
            at("read")
            return super().read(size)

        def close(self):
            at("body_close")

    class Temporary:
        name = "/synthetic/download"

        def write(self, _chunk):
            at("write")

        def flush(self):
            at("flush")

        def close(self):
            at("temporary_close")

    def create(**_kwargs):
        at("create")
        return Temporary()

    def unlink(path):
        assert path == Temporary.name
        at("unlink")

    downloaded_object = storage_module.DownloadedObject

    def result(**kwargs):
        at("result")
        return downloaded_object(**kwargs)

    storage.client = _Client(Body(b"xx" if failure_stage == "limit" else b"x"))
    monkeypatch.setattr(storage_module.tempfile, "NamedTemporaryFile", create)
    monkeypatch.setattr(storage_module.os, "unlink", unlink)
    monkeypatch.setattr(storage_module, "DownloadedObject", result)

    with pytest.raises(BaseException) as caught:
        storage.download_to_tempfile(storage_key="private/file.xlsx", max_bytes=1)

    if failure_stage == "limit":
        assert isinstance(caught.value, ObjectStorageSizeLimitError)
    else:
        assert caught.value is primary
    assert events.count("body_close") == 1
    assert events.count("temporary_close") == (0 if failure_stage == "create" else 1)
    assert events.count("unlink") == (0 if failure_stage == "create" else 1)
    assert set(getattr(caught.value, "__notes__", [])) == {
        f"object_storage_download_{stage}_failed:OSError" for stage in cleanup_failures
    }


@pytest.mark.parametrize("primary_type", [OSError, asyncio.CancelledError])
@pytest.mark.parametrize("notes_mode", ["override", "cancelled", "invalid"])
@pytest.mark.parametrize("cleanup_stage", ["temporary_close", "body_close", "unlink"])
def test_download_cleanup_diagnostics_cannot_replace_primary_or_stop_cleanup(
    monkeypatch, primary_type, notes_mode, cleanup_stage,
):
    storage, _body = _storage(b"x")
    primary = primary_type("synthetic download failure")
    events = []
    if notes_mode in {"override", "cancelled"}:
        def broken_note(_note):
            if notes_mode == "cancelled":
                raise asyncio.CancelledError("synthetic diagnostic cancellation")
            raise RuntimeError("synthetic diagnostic failure")
        primary.add_note = broken_note
    else:
        primary.__notes__ = "invalid notes from upstream"

    class Body(_Body):
        def read(self, _size):
            raise primary

        def close(self):
            events.append("body_close")
            if cleanup_stage == "body_close":
                raise OSError("synthetic secondary close")

    class Temporary:
        name = "/synthetic/download"

        def close(self):
            events.append("temporary_close")
            if cleanup_stage == "temporary_close":
                raise OSError("synthetic secondary close")

    def unlink(path):
        assert path == Temporary.name
        events.append("unlink")
        if cleanup_stage == "unlink":
            raise OSError("synthetic secondary unlink")

    storage.client = _Client(Body(b"x"))
    monkeypatch.setattr(storage_module.tempfile, "NamedTemporaryFile", lambda **_kwargs: Temporary())
    monkeypatch.setattr(storage_module.os, "unlink", unlink)
    with pytest.raises(BaseException) as caught:
        storage.download_to_tempfile(storage_key="private/file.xlsx", max_bytes=1)
    assert caught.value is primary
    assert events == ["temporary_close", "body_close", "unlink"]


@pytest.mark.parametrize("read_fails", [False, True])
def test_download_to_tempfile_body_close_failure_removes_real_temporary_file(
    tmp_path, monkeypatch, read_fails,
):
    storage, _body = _storage(b"downloaded")
    read_error = RuntimeError("synthetic read failed")
    close_error = OSError("synthetic close failed")
    body_closes = []
    temporary_files = []
    create = storage_module.tempfile.NamedTemporaryFile

    class Body(_Body):
        def read(self, size):
            if read_fails:
                raise read_error
            return super().read(size)

        def close(self):
            body_closes.append(True)
            raise close_error

    def record_temporary(**kwargs):
        temporary = create(dir=tmp_path, **kwargs)
        temporary_files.append(temporary)
        return temporary

    storage.client = _Client(Body(b"downloaded"))
    monkeypatch.setattr(storage_module.tempfile, "NamedTemporaryFile", record_temporary)
    with pytest.raises(Exception) as caught:
        storage.download_to_tempfile(storage_key="private/file.xlsx", max_bytes=10)

    assert caught.value is (read_error if read_fails else close_error)
    assert len(body_closes) == 1
    assert len(temporary_files) == 1
    assert temporary_files[0].closed
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
        artifact_factory=lambda **kwargs: kwargs, storage_factory=lambda: storage,
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


def test_storage_completion_survives_direct_waiter_cancel_and_preserves_context(monkeypatch):
    import contextvars
    from app.platform.storage_io_completion import StorageIOCompletion

    async def scenario():
        value = contextvars.ContextVar("storage-test", default="missing")
        value.set("scoped")
        started, release = threading.Event(), threading.Event()
        completion = StorageIOCompletion()
        completion.claim()

        def operation():
            started.set()
            assert release.wait(5)
            return value.get()

        waiter = completion.submit(operation)
        try:
            assert await asyncio.to_thread(started.wait, 5)
            waiter.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiter
            assert completion.submitted and not completion.finished
        finally:
            release.set()
            await completion.settle()
        assert completion.result() == "scoped"
        with pytest.raises(ValueError, match="already_used"):
            completion.claim()

    asyncio.run(scenario())


def test_storage_completion_preserves_operation_timeout_identity(monkeypatch):
    from app.platform.storage_io_completion import StorageIOCompletion

    async def scenario():
        monkeypatch.setattr(storage_module, "_STORAGE_IO_SLOTS", asyncio.Semaphore(1))
        completion = StorageIOCompletion()
        failure = TimeoutError("operation timeout")

        def operation():
            raise failure

        with pytest.raises(TimeoutError) as caught:
            await run_storage_io(operation, completion=completion)
        assert caught.value is failure
        await completion.settle()
        assert storage_module._STORAGE_IO_SLOTS._value == 1

    asyncio.run(scenario())


def test_storage_completion_admission_failure_has_nothing_to_settle(monkeypatch):
    from app.platform.storage_io_completion import StorageIOCompletion

    async def scenario():
        monkeypatch.setattr(storage_module, "_STORAGE_IO_ADMISSIONS", asyncio.BoundedSemaphore(0))
        completion = StorageIOCompletion()
        with pytest.raises(StorageIOBusyError):
            await run_storage_io(lambda: pytest.fail("not admitted"), completion=completion)
        assert not completion.submitted
        await completion.settle()

    asyncio.run(scenario())


def test_storage_completion_pending_admission_cancel_restores_capacity(monkeypatch):
    from app.platform.storage_io_completion import StorageIOCompletion

    async def scenario():
        slots = asyncio.Semaphore(0)
        admissions = asyncio.BoundedSemaphore(1)
        monkeypatch.setattr(storage_module, "_STORAGE_IO_SLOTS", slots)
        monkeypatch.setattr(storage_module, "_STORAGE_IO_ADMISSIONS", admissions)
        completion = StorageIOCompletion()
        task = asyncio.create_task(run_storage_io(lambda: None, completion=completion))
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await completion.settle()
        assert not completion.submitted
        assert admissions._value == 1 and slots._value == 0

    asyncio.run(scenario())


def test_storage_completion_waiter_creation_failure_keeps_physical_capacity(monkeypatch):
    import app.platform.storage_io_completion as completion_module

    async def scenario():
        slots, admissions = asyncio.Semaphore(1), asyncio.BoundedSemaphore(1)
        monkeypatch.setattr(storage_module, "_STORAGE_IO_SLOTS", slots)
        monkeypatch.setattr(storage_module, "_STORAGE_IO_ADMISSIONS", admissions)
        started, release = threading.Event(), threading.Event()
        completion = completion_module.StorageIOCompletion()
        failure = RuntimeError("task factory unavailable")

        def operation():
            started.set()
            assert release.wait(5)

        def fail_task(_coroutine):
            raise failure

        try:
            with monkeypatch.context() as patch:
                patch.setattr(completion_module.asyncio, "create_task", fail_task)
                with pytest.raises(RuntimeError) as caught:
                    await run_storage_io(operation, completion=completion)
                assert caught.value is failure
            assert completion.submitted and not completion.finished
            assert slots._value == 0 and admissions._value == 0
        finally:
            release.set()
            await completion.settle()
        assert slots._value == 1 and admissions._value == 1

    asyncio.run(scenario())


def test_storage_completion_queued_job_settles_after_repeated_cancel(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import app.sandbox.infrastructure.host_bind_snapshot as snapshot_owner
    from app.platform.storage_io_completion import StorageIOCompletion

    async def scenario():
        loop = asyncio.get_running_loop()
        loop.set_default_executor(ThreadPoolExecutor(max_workers=1))
        blocked, release = threading.Event(), threading.Event()
        ran = threading.Event()
        physical_error = RuntimeError("physical failure after cancelled caller")
        first_error = []
        completion = StorageIOCompletion()

        def occupy():
            blocked.set()
            assert release.wait(5)

        def snapshot():
            ran.set()
            raise physical_error

        async def tracked_io(*args, **kwargs):
            try:
                return await run_storage_io(*args, **kwargs)
            except asyncio.CancelledError as error:
                first_error.append(error)
                raise

        monkeypatch.setattr(snapshot_owner, "snapshot_host_bind_delivery_files", snapshot)
        blocker = loop.run_in_executor(None, occupy)
        while not blocked.is_set():
            await asyncio.sleep(0)
        owner = asyncio.create_task(snapshot_owner.snapshot_with_physical_owner(
            storage_io=tracked_io, completion_factory=lambda: completion,
        ))
        try:
            while not completion.submitted:
                await asyncio.sleep(0)
            assert not ran.is_set()
            owner.cancel("original")
            await asyncio.sleep(0)
            owner.cancel("repeated")
            await asyncio.sleep(0)
            assert not owner.done() and not completion.finished
        finally:
            release.set()
            await blocker
            with pytest.raises(asyncio.CancelledError) as caught:
                await owner
        assert ran.is_set() and completion.finished
        assert caught.value is first_error[0]
        with pytest.raises(RuntimeError) as late:
            completion.result()
        assert late.value is physical_error

    asyncio.run(scenario())


@pytest.mark.parametrize("with_completion", [False, True])
@pytest.mark.parametrize("mode", ["normal", "timeout", "cancel"])
@pytest.mark.parametrize("fails", [False, True])
def test_storage_late_outcome(monkeypatch, with_completion, mode, fails):
    from app.platform.storage_io_completion import StorageIOCompletion

    async def scenario():
        loop = asyncio.get_running_loop()
        original_factory = loop.get_task_factory()
        workers = []
        reads = []
        contexts = []
        loop.set_exception_handler(lambda _loop, context: contexts.append(context))

        class RecordingTask(asyncio.Task):
            def exception(self):
                result = super().exception()
                reads.append((self, result))
                return result

        def factory(loop, coro, **kwargs):
            task = RecordingTask(coro, loop=loop, **kwargs)
            if coro.__qualname__ in {"to_thread", "StorageIOCompletion.submit.<locals>.wait"}:
                workers.append(task)
            return task

        async def until(predicate):
            async with asyncio.timeout(3):
                while not predicate():
                    await asyncio.sleep(0.001)

        slots = asyncio.Semaphore(1)
        admissions = asyncio.BoundedSemaphore(1)
        monkeypatch.setattr(storage_module, "_STORAGE_IO_SLOTS", slots)
        monkeypatch.setattr(storage_module, "_STORAGE_IO_ADMISSIONS", admissions)
        started = threading.Event()
        release = threading.Event()
        ended = threading.Event()
        abandoned = []
        error = RuntimeError("synthetic-storage-failure")
        completion = StorageIOCompletion() if with_completion else None

        def operation():
            started.set()
            try:
                assert release.wait(3), "probe did not release worker"
                if fails:
                    raise error
                return "stored"
            finally:
                ended.set()

        loop.set_task_factory(factory)
        caller = asyncio.create_task(storage_module.run_storage_io(
            operation,
            timeout_seconds=0.05 if mode == "timeout" else None,
            completion=completion,
            on_abandoned=lambda: abandoned.append(True),
        ))
        try:
            await until(started.is_set)
            assert len(workers) == 1
            worker = workers[0]
            if mode == "normal":
                release.set()
                if fails:
                    with pytest.raises(RuntimeError) as caught:
                        await caller
                    assert caught.value is error
                else:
                    assert await caller == "stored"
                assert not abandoned
            else:
                if mode == "cancel":
                    caller.cancel("original-cancel")
                    with pytest.raises(asyncio.CancelledError) as caught:
                        await caller
                    assert caught.value.args == ("original-cancel",)
                    assert not caller.cancel("repeat-cancel")
                else:
                    with pytest.raises(storage_module.StorageIOTimeoutError):
                        await caller
                assert abandoned == [True]
                assert not worker.done()
                assert not ended.is_set()
                assert slots.locked() and admissions.locked()
                with pytest.raises(storage_module.StorageIOBusyError):
                    await storage_module.run_storage_io(lambda: "unexpected")
                release.set()
            await until(lambda: worker.done() and slots._value == admissions._value == 1)
            await asyncio.sleep(0)
            assert ended.is_set()
            if fails:
                assert any(task is worker and result is error for task, result in reads)
            assert not contexts
        finally:
            release.set()
            if completion is not None:
                await completion.settle()
            await until(lambda: all(task.done() for task in workers))
            loop.set_task_factory(original_factory)

    asyncio.run(scenario())


@pytest.mark.parametrize("fails", [False, True])
def test_storage_loop_shutdown_handles_cancelled_worker_waiter(monkeypatch, fails):
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    contexts = []

    def operation():
        started.set()
        try:
            assert release.wait(3), "shutdown did not release worker"
            if fails:
                raise RuntimeError("synthetic-shutdown-failure")
            return "stored"
        finally:
            finished.set()

    async def scenario():
        loop = asyncio.get_running_loop()
        loop.set_exception_handler(lambda _loop, context: contexts.append(context))
        monkeypatch.setattr(storage_module, "_STORAGE_IO_SLOTS", asyncio.Semaphore(1))
        monkeypatch.setattr(storage_module, "_STORAGE_IO_ADMISSIONS", asyncio.BoundedSemaphore(1))
        caller = asyncio.create_task(storage_module.run_storage_io(operation, timeout_seconds=None))
        async with asyncio.timeout(3):
            while not started.is_set():
                await asyncio.sleep(0.001)
        caller.cancel("original")
        with pytest.raises(asyncio.CancelledError):
            await caller
        assert storage_module._STORAGE_IO_SLOTS.locked()
        assert storage_module._STORAGE_IO_ADMISSIONS.locked()
        workers = [task for task in asyncio.all_tasks()
                   if task.get_coro().__qualname__ == "to_thread"]
        assert len(workers) == 1
        # asyncio.run cancels remaining Tasks before joining its thread pool.
        # Release physical work at that boundary so shutdown also completes.
        workers[0].add_done_callback(lambda _task: release.set())

    try:
        asyncio.run(scenario())
    finally:
        release.set()
    assert finished.is_set()
    assert not contexts
    assert storage_module._STORAGE_IO_SLOTS._value == 1
    assert storage_module._STORAGE_IO_ADMISSIONS._value == 1
