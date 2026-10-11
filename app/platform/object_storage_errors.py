"""Typed failure classification for the shared S3-compatible storage adapter."""

from botocore.exceptions import ClientError


def is_missing_object_storage_bucket(error: BaseException) -> bool:
    """Recognize authoritative missing-bucket evidence, never message text.

    boto3 upload_file may wrap ClientError. Bound traversal so malformed or cyclic
    exception chains cannot make error handling hang.
    """
    current: BaseException | None = error
    for _ in range(8):
        if current is None:
            break
        if isinstance(current, ClientError) and current.response.get("Error", {}).get("Code") == "NoSuchBucket":
            return True
        current = current.__cause__ or current.__context__
    return False
