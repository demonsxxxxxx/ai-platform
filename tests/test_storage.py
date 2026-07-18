import pytest

from app.storage import ObjectStorage, ObjectStorageSizeLimitError


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


def test_get_bytes_bounded_returns_streamed_payload_and_closes_body():
    storage, body = _storage(b"preview")

    assert storage.get_bytes_bounded(storage_key="private/file.xlsx", max_bytes=7) == b"preview"
    assert body.closed is True


def test_get_bytes_bounded_rejects_max_plus_one_and_closes_body():
    storage, body = _storage(b"oversize")

    with pytest.raises(ObjectStorageSizeLimitError):
        storage.get_bytes_bounded(storage_key="private/file.xlsx", max_bytes=7)

    assert body.closed is True
