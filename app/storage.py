import asyncio
import hashlib
import os
import tempfile
import threading
from dataclasses import dataclass
from functools import partial
from typing import Callable, TypeVar

import boto3
from botocore.client import Config

from app.settings import get_settings
from app.platform.storage_io_completion import StorageIOCompletion
from app.platform.object_storage_errors import is_missing_object_storage_bucket


@dataclass(frozen=True)
class StoredObject:
    storage_key: str
    sha256: str
    size_bytes: int


@dataclass(frozen=True)
class DownloadedObject:
    path: str
    sha256: str
    size_bytes: int


class ObjectStorageSizeLimitError(ValueError):
    """Raised when a streamed object exceeds a caller-owned byte limit."""


class StorageIOBusyError(RuntimeError):
    """Raised when bounded storage workers cannot accept more work."""


class StorageIOTimeoutError(TimeoutError):
    """Raised when a storage operation exceeds its request wait budget."""


_STORAGE_IO_CONCURRENCY = 4
_STORAGE_IO_ADMISSION_LIMIT = 8
_STORAGE_IO_ADMISSION_SECONDS = 5.0
_STORAGE_IO_TIMEOUT_SECONDS = 120.0
_STORAGE_IO_ADMISSIONS = asyncio.BoundedSemaphore(_STORAGE_IO_ADMISSION_LIMIT)
_STORAGE_IO_SLOTS = asyncio.Semaphore(_STORAGE_IO_CONCURRENCY)
_StorageResult = TypeVar("_StorageResult")


async def run_storage_io(
    operation: Callable[..., _StorageResult],
    /,
    *args: object,
    timeout_seconds: float | None = _STORAGE_IO_TIMEOUT_SECONDS,
    completion: StorageIOCompletion | None = None,
    on_abandoned: Callable[[], None] | None = None,
    **kwargs: object,
) -> _StorageResult:
    """Run blocking storage work without releasing its slot before it stops."""

    if completion is not None:
        completion.claim()
    admissions = _STORAGE_IO_ADMISSIONS
    if admissions.locked():
        raise StorageIOBusyError("storage_io_busy")
    await admissions.acquire()
    release_admission = True
    slots = _STORAGE_IO_SLOTS
    try:
        try:
            await asyncio.wait_for(
                slots.acquire(),
                timeout=_STORAGE_IO_ADMISSION_SECONDS,
            )
        except TimeoutError as exc:
            raise StorageIOBusyError("storage_io_busy") from exc

        try:
            call = partial(operation, *args, **kwargs)
            task = (completion.submit(call) if completion is not None
                    else asyncio.create_task(asyncio.to_thread(call)))
        except BaseException:
            if completion is not None and completion.submitted and not completion.finished:
                def release_failed_submission(_outcome: object) -> None:
                    slots.release()
                    admissions.release()

                release_admission = False
                completion.add_done_callback(release_failed_submission)
            else:
                slots.release()
            raise

        def physically_finished() -> bool:
            return completion.finished if completion is not None else task.done()
        try:
            return await asyncio.wait_for(asyncio.shield(task), timeout=timeout_seconds)
        except TimeoutError as exc:
            if physically_finished():
                return completion.result() if completion is not None else task.result()
            if on_abandoned is not None:
                on_abandoned()
            raise StorageIOTimeoutError("storage_io_timeout") from exc
        except BaseException:
            if not physically_finished() and on_abandoned is not None:
                on_abandoned()
            raise
        finally:
            if physically_finished():
                slots.release()
            else:
                def release_capacity(_task: object) -> None:
                    # A cancelled shield detaches its outcome reader. This owner
                    # must retrieve late failures as well as return capacity.
                    if completion is None and not task.cancelled():
                        task.exception()
                    slots.release()
                    admissions.release()

                release_admission = False
                if completion is not None:
                    completion.add_done_callback(release_capacity)
                else:
                    task.add_done_callback(release_capacity)
            # A timed-out or cancelled thread keeps its capacity until it really stops.
    finally:
        if release_admission:
            admissions.release()


class ObjectStorage:
    def __init__(self) -> None:
        settings = get_settings()
        self.bucket = settings.s3_bucket
        self._bucket_ready = False
        self._bucket_ready_lock = threading.Lock()
        self.client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url,
            aws_access_key_id=settings.s3_access_key_id,
            aws_secret_access_key=settings.s3_secret_access_key,
            region_name=settings.s3_region,
            config=Config(signature_version="s3v4"),
        )

    def ensure_bucket(self) -> None:
        # This client/configuration lives for one storage instance. Cache only a
        # successful confirmation, never permission errors or creation conflicts.
        with self._bucket_ready_lock:
            if self._bucket_ready:
                return
            existing = self.client.list_buckets()
            names = {item["Name"] for item in existing.get("Buckets", [])}
            if self.bucket not in names:
                self.client.create_bucket(Bucket=self.bucket)
            self._bucket_ready = True

    def _bucket_operation(
        self, operation: Callable[..., _StorageResult], /,
        *args: object, **kwargs: object,
    ) -> _StorageResult:
        self.ensure_bucket()
        try:
            return operation(*args, **kwargs)
        except Exception as exc:
            self._invalidate_missing_bucket(exc)
            raise

    def _invalidate_missing_bucket(self, exc: Exception) -> None:
        if is_missing_object_storage_bucket(exc):
            with self._bucket_ready_lock:
                self._bucket_ready = False

    def put_bytes(self, *, storage_key: str, content: bytes, content_type: str) -> StoredObject:
        self._bucket_operation(
            self.client.put_object,
            Bucket=self.bucket,
            Key=storage_key,
            Body=content,
            ContentType=content_type,
        )
        return StoredObject(
            storage_key=storage_key,
            sha256=hashlib.sha256(content).hexdigest(),
            size_bytes=len(content),
        )

    def put_file(self, *, storage_key: str, source_path: str, content_type: str) -> StoredObject:
        """Upload one local file without holding its complete content in memory."""

        digest = hashlib.sha256()
        size_bytes = 0
        with open(source_path, "rb") as source:
            while chunk := source.read(1024 * 1024):
                digest.update(chunk)
                size_bytes += len(chunk)
        self._bucket_operation(
            self.client.upload_file,
            source_path,
            self.bucket,
            storage_key,
            ExtraArgs={"ContentType": content_type},
        )
        return StoredObject(
            storage_key=storage_key,
            sha256=digest.hexdigest(),
            size_bytes=size_bytes,
        )

    def get_bytes(self, *, storage_key: str) -> bytes:
        response = self.client.get_object(Bucket=self.bucket, Key=storage_key)
        body = response["Body"]
        try:
            return body.read()
        finally:
            body.close()

    def get_bytes_prefix(self, *, storage_key: str, max_bytes: int) -> bytes:
        """Read at most max_bytes without rejecting a larger object; always close."""

        if max_bytes < 0:
            raise ValueError("max_bytes must be non-negative")
        if max_bytes == 0:
            return b""
        response = self.client.get_object(Bucket=self.bucket, Key=storage_key)
        body = response["Body"]
        chunks: list[bytes] = []
        remaining = max_bytes
        try:
            while remaining:
                chunk = body.read(min(64 * 1024, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            return b"".join(chunks)
        finally:
            body.close()

    def get_bytes_bounded(self, *, storage_key: str, max_bytes: int) -> bytes:
        """Read one object in bounded chunks and always close its response body."""

        if max_bytes < 0:
            raise ValueError("max_bytes must be non-negative")
        response = self.client.get_object(Bucket=self.bucket, Key=storage_key)
        body = response["Body"]
        chunks: list[bytes] = []
        byte_count = 0
        try:
            while True:
                chunk = body.read(min(64 * 1024, max_bytes - byte_count + 1))
                if not chunk:
                    return b"".join(chunks)
                byte_count += len(chunk)
                if byte_count > max_bytes:
                    raise ObjectStorageSizeLimitError("object_size_limit_exceeded")
                chunks.append(chunk)
        finally:
            body.close()

    def create_multipart_upload(self, *, storage_key: str, content_type: str) -> str:
        response = self._bucket_operation(
            self.client.create_multipart_upload,
            Bucket=self.bucket,
            Key=storage_key,
            ContentType=content_type,
        )
        return str(response["UploadId"])

    def upload_multipart_part(
        self,
        *,
        storage_key: str,
        upload_id: str,
        part_number: int,
        content: bytes,
    ) -> str:
        response = self.client.upload_part(
            Bucket=self.bucket,
            Key=storage_key,
            UploadId=upload_id,
            PartNumber=part_number,
            Body=content,
        )
        return str(response["ETag"])

    def complete_multipart_upload(
        self,
        *,
        storage_key: str,
        upload_id: str,
        parts: list[dict[str, object]],
    ) -> None:
        self.client.complete_multipart_upload(
            Bucket=self.bucket,
            Key=storage_key,
            UploadId=upload_id,
            MultipartUpload={"Parts": parts},
        )

    def abort_multipart_upload(self, *, storage_key: str, upload_id: str) -> None:
        try:
            self.client.abort_multipart_upload(
                Bucket=self.bucket,
                Key=storage_key,
                UploadId=upload_id,
            )
        except Exception as exc:
            self._invalidate_missing_bucket(exc)
            raise

    def cleanup_multipart_upload(self, *, storage_key: str, upload_id: str) -> None:
        abort_error: Exception | None = None
        try:
            self.abort_multipart_upload(storage_key=storage_key, upload_id=upload_id)
        except Exception as exc:
            response = getattr(exc, "response", {})
            error = response.get("Error", {}) if isinstance(response, dict) else {}
            if error.get("Code") != "NoSuchUpload":
                abort_error = exc
        delete_error: Exception | None = None
        try:
            self.delete_object(storage_key=storage_key)
        except Exception as exc:
            delete_error = exc
        if abort_error is not None:
            raise abort_error
        if delete_error is not None:
            raise delete_error

    def abort_multipart_uploads_for_key(self, *, storage_key: str) -> int:
        request: dict[str, object] = {"Bucket": self.bucket, "Prefix": storage_key}
        aborted = 0
        while True:
            response = self._bucket_operation(self.client.list_multipart_uploads, **request)
            for upload in response.get("Uploads") or []:
                if str(upload.get("Key") or "") != storage_key:
                    continue
                upload_id = str(upload.get("UploadId") or "")
                if not upload_id:
                    continue
                self.abort_multipart_upload(storage_key=storage_key, upload_id=upload_id)
                aborted += 1
            if not response.get("IsTruncated"):
                return aborted
            request["KeyMarker"] = str(response["NextKeyMarker"])
            request["UploadIdMarker"] = str(response["NextUploadIdMarker"])

    def download_to_tempfile(self, *, storage_key: str, max_bytes: int) -> DownloadedObject:
        """Download one object to disk without holding the complete object in memory."""

        if max_bytes < 0:
            raise ValueError("max_bytes must be non-negative")
        digest = hashlib.sha256()
        size_bytes = 0
        temporary = None
        downloaded = None
        failure: BaseException | None = None
        response = self.client.get_object(Bucket=self.bucket, Key=storage_key)
        body = response["Body"]
        try:
            temporary = tempfile.NamedTemporaryFile(prefix="ai-platform-upload-", delete=False)
            while True:
                chunk = body.read(min(64 * 1024, max_bytes - size_bytes + 1))
                if not chunk:
                    break
                size_bytes += len(chunk)
                if size_bytes > max_bytes:
                    raise ObjectStorageSizeLimitError("object_size_limit_exceeded")
                digest.update(chunk)
                temporary.write(chunk)
            temporary.flush()
            downloaded = DownloadedObject(
                path=temporary.name,
                sha256=digest.hexdigest(),
                size_bytes=size_bytes,
            )
        except BaseException as exc:
            failure = exc

        # Until every required close succeeds, this method still owns the path.
        # Each resource gets its own cleanup attempt, even after another fails.
        if temporary is not None:
            try:
                temporary.close()
            except BaseException as exc:
                if failure is None:
                    failure = exc
                else:
                    try:
                        failure.add_note(f"object_storage_download_temporary_close_failed:{type(exc).__name__}")
                    except BaseException:
                        # Optional diagnostics cannot replace the selected primary failure.
                        pass
        try:
            body.close()
        except BaseException as exc:
            if failure is None:
                failure = exc
            else:
                try:
                    failure.add_note(f"object_storage_download_body_close_failed:{type(exc).__name__}")
                except BaseException:
                    # Optional diagnostics cannot replace the selected primary failure.
                    pass
        if failure is not None:
            if temporary is not None:
                try:
                    os.unlink(temporary.name)
                except BaseException as exc:
                    try:
                        failure.add_note(f"object_storage_download_unlink_failed:{type(exc).__name__}")
                    except BaseException:
                        # Optional diagnostics cannot replace the selected primary failure.
                        pass
            raise failure

        assert downloaded is not None
        return downloaded

    def delete_object(self, *, storage_key: str) -> None:
        """Idempotently delete one object; PostgreSQL owns durable receipts."""

        self.client.delete_object(Bucket=self.bucket, Key=storage_key)

    def presigned_get_url(self, *, storage_key: str, expires_in_seconds: int = 300) -> str:
        return self.client.generate_presigned_url(
            ClientMethod="get_object",
            Params={
                "Bucket": self.bucket,
                "Key": storage_key,
            },
            ExpiresIn=expires_in_seconds,
        )
