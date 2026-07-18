import hashlib
from dataclasses import dataclass

import boto3
from botocore.client import Config

from app.settings import get_settings


@dataclass(frozen=True)
class StoredObject:
    storage_key: str
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

    def presigned_get_url(self, *, storage_key: str, expires_in_seconds: int = 300) -> str:
        return self.client.generate_presigned_url(
            ClientMethod="get_object",
            Params={
                "Bucket": self.bucket,
                "Key": storage_key,
            },
            ExpiresIn=expires_in_seconds,
        )
