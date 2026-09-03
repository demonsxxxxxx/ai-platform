import hashlib
import os
import tempfile
from dataclasses import dataclass

import boto3
from botocore.client import Config

from app.settings import get_settings


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


class ObjectStorage:
    def __init__(self) -> None:
        settings = get_settings()
        self.bucket = settings.s3_bucket
        self.client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url,
            aws_access_key_id=settings.s3_access_key_id,
            aws_secret_access_key=settings.s3_secret_access_key,
            region_name=settings.s3_region,
            config=Config(signature_version="s3v4"),
        )

    def ensure_bucket(self) -> None:
        existing = self.client.list_buckets()
        names = {item["Name"] for item in existing.get("Buckets", [])}
        if self.bucket not in names:
            self.client.create_bucket(Bucket=self.bucket)

    def put_bytes(self, *, storage_key: str, content: bytes, content_type: str) -> StoredObject:
        self.ensure_bucket()
        self.client.put_object(
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

    def get_bytes(self, *, storage_key: str) -> bytes:
        response = self.client.get_object(Bucket=self.bucket, Key=storage_key)
        body = response["Body"]
        try:
            return body.read()
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
        self.ensure_bucket()
        response = self.client.create_multipart_upload(
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
        self.client.abort_multipart_upload(
            Bucket=self.bucket,
            Key=storage_key,
            UploadId=upload_id,
        )

    def download_to_tempfile(self, *, storage_key: str, max_bytes: int) -> DownloadedObject:
        """Download one object to disk without holding the complete object in memory."""

        if max_bytes < 0:
            raise ValueError("max_bytes must be non-negative")
        response = self.client.get_object(Bucket=self.bucket, Key=storage_key)
        body = response["Body"]
        digest = hashlib.sha256()
        size_bytes = 0
        temporary = tempfile.NamedTemporaryFile(prefix="ai-platform-upload-", delete=False)
        try:
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
        except BaseException:
            temporary.close()
            os.unlink(temporary.name)
            raise
        finally:
            body.close()
        temporary.close()
        return DownloadedObject(
            path=temporary.name,
            sha256=digest.hexdigest(),
            size_bytes=size_bytes,
        )

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
