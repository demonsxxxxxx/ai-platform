from __future__ import annotations

import asyncio
import codecs
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import io
import tempfile
import threading
import types
import zipfile

import pytest
from fastapi import HTTPException

from app.auth import AuthPrincipal
from app.files.api import (
    MultipartUploadCompleteRequest,
    MultipartUploadCreateRequest,
    MultipartUploadPart,
)
from app.files.infrastructure import postgres as upload_postgres
from app.routes import files as files_routes
from app.routes import lambchat_compat as compat_routes
from app.storage import StorageIOTimeoutError, StoredObject


def upload_principal(*, permissions: list[str] | None = None) -> AuthPrincipal:
    return AuthPrincipal(
        user_id="user-a",
        display_name="User A",
        tenant_id="default",
        roles=["user"],
        permissions=["file:upload", "file:upload:document"] if permissions is None else permissions,
    )


class FakeUploadFile:
    def __init__(self, filename: str | None, content_type: str, data: bytes):
        self.filename = filename
        self.content_type = content_type
        self._data = data
        self.read_calls: list[int] = []

    async def read(self, size: int = -1) -> bytes:
        self.read_calls.append(size)
        if size is None or size < 0:
            return self._data
        return self._data[:size]


@asynccontextmanager
async def fake_transaction():
    yield object()


async def fake_ensure_workspace(conn, *, tenant_id, workspace_id):
    return None


async def fake_ensure_user(conn, *, tenant_id, user_id, display_name):
    return None


async def fake_create_file(conn, **kwargs):
    return None


async def fake_get_file(conn, **kwargs):
    return None


async def fake_file_storage_usage(conn, **kwargs):
    return {"stored_bytes": 0, "reserved_bytes": 0, "active_uploads": 0}


def install_basic_upload_fakes(monkeypatch):
    upload_sessions: dict[str, dict[str, object]] = {}

    async def claim_direct_upload(_conn, **kwargs):
        upload_session_id = str(kwargs["upload_session_id"])
        if upload_session_id in upload_sessions:
            return False
        upload_sessions[upload_session_id] = {"id": upload_session_id, "state": "pending", **kwargs}
        return True

    async def get_upload_session(_conn, *, upload_session_id, **_kwargs):
        return upload_sessions.get(upload_session_id)

    async def complete_upload_session(_conn, *, upload_session_id):
        upload_sessions[upload_session_id]["state"] = "completed"

    async def abort_upload_session(_conn, *, upload_session_id, state="aborted"):
        upload_sessions[upload_session_id]["state"] = state

    async def expire_upload_sessions(_conn):
        return []

    async def delete_expired_upload_session(_conn, *, upload_session_id):
        if upload_sessions.get(upload_session_id, {}).get("state") == "expired":
            upload_sessions.pop(upload_session_id, None)

    monkeypatch.setattr(files_routes, "transaction", fake_transaction)
    monkeypatch.setattr(files_routes, "ensure_workspace", fake_ensure_workspace)
    monkeypatch.setattr(files_routes, "ensure_user", fake_ensure_user)
    monkeypatch.setattr(files_routes, "create_file", fake_create_file)
    monkeypatch.setattr(files_routes, "get_file", fake_get_file)
    monkeypatch.setattr(files_routes, "get_file_storage_usage", fake_file_storage_usage)
    monkeypatch.setattr(files_routes, "claim_direct_file_upload_session", claim_direct_upload)
    monkeypatch.setattr(files_routes, "get_authorized_file_upload_session", get_upload_session)
    monkeypatch.setattr(files_routes, "complete_file_upload_session", complete_upload_session)
    monkeypatch.setattr(files_routes, "abort_file_upload_session", abort_upload_session)
    monkeypatch.setattr(files_routes, "expire_file_upload_sessions", expire_upload_sessions)
    monkeypatch.setattr(files_routes, "delete_expired_file_upload_session", delete_expired_upload_session)
    monkeypatch.setattr(files_routes, "new_id", lambda prefix: "file_upload_1")
    monkeypatch.setattr(files_routes, "_direct_upload_file_id", lambda **_kwargs: "file_upload_1")
    return upload_sessions


def install_forbidden_repository_side_effects(monkeypatch):
    @asynccontextmanager
    async def forbidden_transaction():
        raise AssertionError("unsupported upload must reject before repository access")
        yield object()

    monkeypatch.setattr(files_routes, "transaction", forbidden_transaction)
    monkeypatch.setattr(files_routes, "ensure_workspace", fake_ensure_workspace)
    monkeypatch.setattr(files_routes, "ensure_user", fake_ensure_user)
    monkeypatch.setattr(files_routes, "create_file", fake_create_file)
    monkeypatch.setattr(files_routes, "new_id", lambda prefix: "file_upload_1")


def make_safe_docx_bytes() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "<?xml version='1.0' encoding='UTF-8'?><Types></Types>")
        archive.writestr("word/document.xml", "<w:document></w:document>")
        archive.writestr("_rels/.rels", "<Relationships></Relationships>")
    return buffer.getvalue()


def make_path_traversal_zip() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("../escape.txt", "escape")
    return buffer.getvalue()


def make_large_uncompressed_zip() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("big.bin", b"A" * (33 * 1024 * 1024))
    return buffer.getvalue()


@pytest.mark.asyncio
async def test_platform_upload_rejects_missing_permission_before_body_read(monkeypatch):
    install_basic_upload_fakes(monkeypatch)
    upload = FakeUploadFile("sample.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", make_safe_docx_bytes())

    with pytest.raises(HTTPException) as exc_info:
        await files_routes.upload_file(
            file=upload,
            workspace_id="default",
            session_id=None,
            principal=upload_principal(permissions=[]),
        )

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "missing_permission:file:upload"
    assert upload.read_calls == []


@pytest.mark.asyncio
async def test_compat_upload_rejects_missing_permission_before_body_read(monkeypatch):
    install_basic_upload_fakes(monkeypatch)
    upload = FakeUploadFile("sample.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", make_safe_docx_bytes())

    with pytest.raises(HTTPException) as exc_info:
        await compat_routes.upload_file(
            file=upload,
            folder="uploads",
            workspace_id="default",
            session_id=None,
            principal=upload_principal(permissions=[]),
        )

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "missing_permission:file:upload"
    assert upload.read_calls == []


@pytest.mark.asyncio
async def test_bounded_upload_accepts_the_exact_byte_limit():
    content = b"A" * files_routes.MAX_DIRECT_UPLOAD_BYTES
    upload = FakeUploadFile("exact-limit.txt", "text/plain", content)

    result = await files_routes._read_bounded_upload(upload)

    assert result == content
    assert upload.read_calls == [files_routes.MAX_DIRECT_UPLOAD_BYTES + 1]


@pytest.mark.asyncio
async def test_multipart_request_body_is_bounded_before_joining_chunks():
    class Request:
        async def stream(self):
            yield b"ab"
            yield b"cd"

    assert await files_routes._read_bounded_request_body(Request(), 4) == b"abcd"

    class OversizedRequest:
        async def stream(self):
            yield b"abc"
            yield b"de"

    with pytest.raises(HTTPException) as exc_info:
        await files_routes._read_bounded_request_body(OversizedRequest(), 4)
    assert exc_info.value.status_code == 413
    assert exc_info.value.detail == "multipart_part_too_large"


@pytest.mark.asyncio
async def test_multipart_initiation_persists_bounded_part_contract(monkeypatch):
    captured: dict[str, object] = {}

    class RecordingStorage:
        def create_multipart_upload(self, *, storage_key, content_type):
            assert captured["upload_id"] == "initializing_upload-1"
            captured["storage_key"] = storage_key
            return "s3-upload-1"

        def abort_multipart_upload(self, **kwargs):
            raise AssertionError("successful initiation must not abort")

    async def fake_expire(conn, **kwargs):
        return []

    async def fake_create_session(conn, **kwargs):
        captured.update(kwargs)

    async def fake_activate_session(conn, **kwargs):
        captured["upload_id"] = kwargs["upload_id"]
        return True

    monkeypatch.setattr(files_routes, "ObjectStorage", RecordingStorage)
    monkeypatch.setattr(files_routes, "transaction", fake_transaction)
    monkeypatch.setattr(files_routes, "expire_file_upload_sessions", fake_expire)
    monkeypatch.setattr(files_routes, "ensure_workspace", fake_ensure_workspace)
    monkeypatch.setattr(files_routes, "ensure_user", fake_ensure_user)
    monkeypatch.setattr(files_routes, "get_file_storage_usage", fake_file_storage_usage)
    monkeypatch.setattr(files_routes, "create_file_upload_session", fake_create_session)
    monkeypatch.setattr(files_routes, "activate_file_upload_session", fake_activate_session)
    monkeypatch.setattr(files_routes, "new_id", lambda prefix: f"{prefix}-1")

    response = await files_routes.initiate_multipart_upload(
        request=MultipartUploadCreateRequest(
            workspace_id="default",
            name="large.pdf",
            content_type="application/pdf",
            size_bytes=files_routes.MULTIPART_THRESHOLD_BYTES,
        ),
        principal=upload_principal(),
    )

    assert response["part_size_bytes"] == files_routes.MULTIPART_PART_BYTES
    assert len(response["parts"]) == 4
    assert captured["expected_size_bytes"] == files_routes.MULTIPART_THRESHOLD_BYTES
    assert captured["part_count"] == 4
    assert captured["upload_id"] == "s3-upload-1"


def test_abandoned_multipart_initialization_aborts_created_upload():
    abandoned = threading.Event()
    abandoned.set()
    aborted: list[tuple[str, str]] = []

    class Storage:
        def create_multipart_upload(self, *, storage_key, content_type):
            return "s3-upload"

        def abort_multipart_upload(self, *, storage_key, upload_id):
            aborted.append((storage_key, upload_id))

    upload_id = files_routes._create_multipart_upload(
        Storage(),
        storage_key="private/claim",
        content_type="application/pdf",
        abandoned=abandoned,
    )

    assert upload_id == "s3-upload"
    assert aborted == [("private/claim", "s3-upload")]


@pytest.mark.asyncio
async def test_multipart_initiation_rejects_missing_permission_before_storage(monkeypatch):
    class ForbiddenStorage:
        def __init__(self):
            raise AssertionError("unauthorized multipart initiation must not reach storage")

    monkeypatch.setattr(files_routes, "ObjectStorage", ForbiddenStorage)

    with pytest.raises(HTTPException) as exc_info:
        await files_routes.initiate_multipart_upload(
            request=MultipartUploadCreateRequest(
                name="large.txt",
                size_bytes=files_routes.MULTIPART_THRESHOLD_BYTES,
            ),
            principal=upload_principal(permissions=[]),
        )

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "missing_permission:file:upload"


@pytest.mark.asyncio
async def test_multipart_initiation_accepts_small_files(monkeypatch):
    captured: dict[str, object] = {}

    class RecordingStorage:
        def create_multipart_upload(self, *, storage_key, content_type):
            return "s3-upload-small"

        def abort_multipart_upload(self, **kwargs):
            raise AssertionError("successful initiation must not abort")

    async def fake_expire(conn, **kwargs):
        return []

    async def fake_create_session(conn, **kwargs):
        captured.update(kwargs)

    async def fake_activate_session(conn, **kwargs):
        captured["upload_id"] = kwargs["upload_id"]
        return True

    monkeypatch.setattr(files_routes, "ObjectStorage", RecordingStorage)
    monkeypatch.setattr(files_routes, "transaction", fake_transaction)
    monkeypatch.setattr(files_routes, "expire_file_upload_sessions", fake_expire)
    monkeypatch.setattr(files_routes, "ensure_workspace", fake_ensure_workspace)
    monkeypatch.setattr(files_routes, "ensure_user", fake_ensure_user)
    monkeypatch.setattr(files_routes, "get_file_storage_usage", fake_file_storage_usage)
    monkeypatch.setattr(files_routes, "create_file_upload_session", fake_create_session)
    monkeypatch.setattr(files_routes, "activate_file_upload_session", fake_activate_session)
    monkeypatch.setattr(files_routes, "new_id", lambda prefix: f"{prefix}-small")

    response = await files_routes.initiate_multipart_upload(
        request=MultipartUploadCreateRequest(name="small.txt", size_bytes=1),
        principal=upload_principal(),
    )

    assert response["upload_session_id"] == "upload-small"
    assert response["parts"] == [
        {
            "part_number": 1,
            "url": "/api/ai/files/uploads/upload-small/parts/1",
        }
    ]
    assert captured["expected_size_bytes"] == 1
    assert captured["part_count"] == 1


@pytest.mark.asyncio
async def test_completed_multipart_retry_returns_original_file_without_storage(monkeypatch):
    completed = {
        "id": "upload-1",
        "tenant_id": "default",
        "workspace_id": "default",
        "user_id": "user-a",
        "file_id": "file-1",
        "storage_key": "private/file-1",
        "state": "completed",
    }

    class ForbiddenStorage:
        def __getattr__(self, name):
            raise AssertionError(f"completed retry must not call storage: {name}")

    async def fake_upload_session(conn, **kwargs):
        return completed

    async def fake_file(conn, **kwargs):
        return {
            "id": "file-1",
            "tenant_id": "default",
            "workspace_id": "default",
            "user_id": "user-a",
            "original_name": "small.txt",
            "storage_key": "private/file-1",
            "lifecycle_state": "active",
            "sha256": "abc",
            "size_bytes": 1,
        }

    monkeypatch.setattr(files_routes, "ObjectStorage", ForbiddenStorage)
    monkeypatch.setattr(files_routes, "transaction", fake_transaction)
    monkeypatch.setattr(files_routes, "get_authorized_file_upload_session", fake_upload_session)
    monkeypatch.setattr(files_routes, "get_file", fake_file)

    response = await files_routes.complete_multipart_upload(
        upload_session_id="upload-1",
        request={"parts": [{"part_number": 1, "etag": "etag-1"}]},
        principal=upload_principal(),
    )

    assert response.file_id == "file-1"
    assert response.sha256 == "abc"
    assert response.size_bytes == 1


@pytest.mark.asyncio
async def test_completed_upload_response_rejects_deleted_file(monkeypatch):
    async def deleted_file(_conn, **_kwargs):
        return {
            "id": "file-1",
            "workspace_id": "default",
            "user_id": "user-a",
            "storage_key": "private/file-1",
            "lifecycle_state": "deleted",
            "original_name": "small.txt",
            "sha256": "abc",
            "size_bytes": 1,
        }

    monkeypatch.setattr(files_routes, "get_file", deleted_file)

    with pytest.raises(HTTPException) as exc_info:
        await files_routes._completed_upload_response(
            object(),
            {
                "tenant_id": "default",
                "workspace_id": "default",
                "user_id": "user-a",
                "file_id": "file-1",
                "storage_key": "private/file-1",
            },
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "upload_session_result_invalid"


@pytest.mark.asyncio
async def test_multipart_complete_retry_returns_file_after_commit_response_loss(monkeypatch):
    session = {
        "id": "upload-1",
        "tenant_id": "default",
        "workspace_id": "default",
        "user_id": "user-a",
        "session_id": None,
        "file_id": "file-1",
        "original_name": "retry.pdf",
        "content_type": "application/pdf",
        "expected_size_bytes": 3,
        "storage_key": "private/file-1",
        "upload_id": "s3-upload-1",
        "part_count": 1,
        "expires_at": datetime.now(timezone.utc) + timedelta(minutes=5),
        "state": "pending",
    }
    transaction_calls = 0

    class FakeStorage:
        def complete_multipart_upload(self, **_kwargs):
            return None

        def download_to_tempfile(self, **_kwargs):
            path = tempfile.NamedTemporaryFile(delete=False)
            path.write(b"pdf")
            path.close()
            return types.SimpleNamespace(
                path=path.name,
                sha256=hashlib.sha256(b"pdf").hexdigest(),
                size_bytes=3,
            )

    @asynccontextmanager
    async def uncertain_transaction():
        nonlocal transaction_calls
        transaction_calls += 1
        if transaction_calls == 3:
            raise RuntimeError("reconciliation temporarily unavailable")
        yield object()
        if transaction_calls == 2:
            raise RuntimeError("commit response lost")

    async def fake_upload_session(_conn, **_kwargs):
        return session

    async def fake_complete_session(_conn, **_kwargs):
        session["state"] = "completed"

    async def committed_file(_conn, **_kwargs):
        return {
            "id": "file-1",
            "workspace_id": "default",
            "user_id": "user-a",
            "original_name": "retry.pdf",
            "storage_key": "private/file-1",
            "lifecycle_state": "active",
            "sha256": hashlib.sha256(b"pdf").hexdigest(),
            "size_bytes": 3,
        }

    monkeypatch.setattr(files_routes, "transaction", uncertain_transaction)
    monkeypatch.setattr(files_routes, "ObjectStorage", FakeStorage)
    monkeypatch.setattr(files_routes, "get_authorized_file_upload_session", fake_upload_session)
    monkeypatch.setattr(files_routes, "get_file_storage_usage", fake_file_storage_usage)
    monkeypatch.setattr(files_routes, "create_file", fake_create_file)
    monkeypatch.setattr(files_routes, "complete_file_upload_session", fake_complete_session)
    monkeypatch.setattr(files_routes, "get_file", committed_file)
    monkeypatch.setattr(files_routes, "_validate_upload_file", lambda **_kwargs: None)

    request = MultipartUploadCompleteRequest(
        parts=(MultipartUploadPart(part_number=1, etag="etag-1"),)
    )
    with pytest.raises(RuntimeError, match="commit response lost"):
        await files_routes.complete_multipart_upload("upload-1", request, upload_principal())

    response = await files_routes.complete_multipart_upload(
        "upload-1",
        request,
        upload_principal(),
    )

    assert response.file_id == "file-1"
    assert response.sha256 == hashlib.sha256(b"pdf").hexdigest()
    assert transaction_calls == 4


@pytest.mark.asyncio
async def test_direct_upload_commit_uncertainty_preserves_confirmed_file(monkeypatch):
    upload_sessions = install_basic_upload_fakes(monkeypatch)
    transaction_calls = 0
    file_queries = 0
    deleted: list[str] = []

    @asynccontextmanager
    async def uncertain_transaction():
        nonlocal transaction_calls
        transaction_calls += 1
        yield object()
        if transaction_calls == 5:
            raise RuntimeError("commit response lost")

    class RecordingStorage:
        def put_bytes(self, *, storage_key, content, content_type):
            return StoredObject(storage_key=storage_key, sha256="abc", size_bytes=1)

        def delete_object(self, *, storage_key):
            deleted.append(storage_key)

    async def committed_file(conn, **kwargs):
        nonlocal file_queries
        file_queries += 1
        if file_queries == 1:
            return None
        return {
            "id": "file-1",
            "workspace_id": "default",
            "user_id": "user-a",
            "storage_key": (
                "tenants/default/workspaces/default/sessions/unbound/"
                "files/file-1/generations/direct_file_upload_1/content"
            ),
            "lifecycle_state": "active",
            "sha256": "abc",
            "size_bytes": 1,
            "original_name": "small.txt",
        }

    monkeypatch.setattr(files_routes, "ObjectStorage", RecordingStorage)
    monkeypatch.setattr(files_routes, "transaction", uncertain_transaction)
    monkeypatch.setattr(files_routes, "get_file", committed_file)
    monkeypatch.setattr(files_routes, "_direct_upload_file_id", lambda **_kwargs: "file-1")

    response = await files_routes.upload_file(
        file=FakeUploadFile("small.txt", "text/plain", b"x"),
        workspace_id="default",
        session_id=None,
        principal=upload_principal(),
    )

    assert response.file_id == "file-1"
    assert deleted == []
    assert transaction_calls == 6
    assert next(iter(upload_sessions.values()))["state"] == "completed"


@pytest.mark.asyncio
async def test_direct_upload_retry_waits_for_unknown_put_owner(monkeypatch):
    direct_file_id = files_routes._direct_upload_file_id
    upload_sessions = install_basic_upload_fakes(monkeypatch)
    monkeypatch.setattr(files_routes, "_direct_upload_file_id", direct_file_id)
    owner_ids = iter(("upload_owner_a", "upload_owner_b"))
    monkeypatch.setattr(
        files_routes,
        "new_id",
        lambda prefix: next(owner_ids) if prefix == "upload_owner" else f"{prefix}_1",
    )
    storage_keys: list[str] = []

    class UncertainStorage:
        def put_bytes(self, *, storage_key, content, content_type):
            storage_keys.append(storage_key)
            if len(storage_keys) == 1:
                raise StorageIOTimeoutError("storage_io_timeout")
            return StoredObject(
                storage_key=storage_key,
                sha256=hashlib.sha256(content).hexdigest(),
                size_bytes=len(content),
            )

        def delete_object(self, *, storage_key):
            raise AssertionError("an unknown put outcome must not be deleted")

    monkeypatch.setattr(files_routes, "ObjectStorage", UncertainStorage)
    upload = lambda: FakeUploadFile("small.txt", "text/plain", b"x")

    with pytest.raises(HTTPException) as first_error:
        await files_routes.upload_file(upload(), "default", None, upload_principal())
    assert first_error.value.status_code == 503
    assert first_error.value.detail == "storage_io_timeout"
    with pytest.raises(HTTPException) as retry_error:
        await files_routes.upload_file(upload(), "default", None, upload_principal())
    assert retry_error.value.status_code == 503
    assert retry_error.value.detail == "direct_upload_in_progress"
    assert len(storage_keys) == 1

    upload_sessions.clear()  # Durable expiry runs only after the retained writer has stopped.
    response = await files_routes.upload_file(upload(), "default", None, upload_principal())

    assert response.file_id.startswith("file_")
    assert len(storage_keys) == 2
    assert storage_keys[0] != storage_keys[1]


def test_abandoned_direct_put_deletes_only_its_reserved_generation():
    abandoned = threading.Event()
    abandoned.set()
    deleted: list[str] = []

    class Storage:
        def put_bytes(self, *, storage_key, content, content_type):
            return StoredObject(storage_key=storage_key, sha256="abc", size_bytes=len(content))

        def delete_object(self, *, storage_key):
            deleted.append(storage_key)

    stored = files_routes._put_direct_upload(
        Storage(),
        storage_key="files/generations/owner-a/content",
        content=b"x",
        content_type="text/plain",
        abandoned=abandoned,
    )

    assert stored.storage_key == "files/generations/owner-a/content"
    assert deleted == ["files/generations/owner-a/content"]


@pytest.mark.asyncio
async def test_concurrent_direct_upload_cannot_delete_or_overwrite_owner_object(monkeypatch):
    upload_sessions = install_basic_upload_fakes(monkeypatch)
    started = threading.Event()
    release = threading.Event()
    puts: list[str] = []
    deleted: list[str] = []

    class BlockingStorage:
        def put_bytes(self, *, storage_key, content, content_type):
            puts.append(storage_key)
            started.set()
            assert release.wait(timeout=5)
            return StoredObject(
                storage_key=storage_key,
                sha256=hashlib.sha256(content).hexdigest(),
                size_bytes=len(content),
            )

        def delete_object(self, *, storage_key):
            deleted.append(storage_key)

    monkeypatch.setattr(files_routes, "ObjectStorage", BlockingStorage)
    first = asyncio.create_task(
        files_routes.upload_file(
            FakeUploadFile("small.txt", "text/plain", b"x"),
            "default",
            None,
            upload_principal(),
        )
    )
    assert await asyncio.to_thread(started.wait, 5)

    with pytest.raises(HTTPException) as retry_error:
        await files_routes.upload_file(
            FakeUploadFile("small.txt", "text/plain", b"x"),
            "default",
            None,
            upload_principal(),
        )
    assert retry_error.value.status_code == 503
    assert retry_error.value.detail == "direct_upload_in_progress"

    release.set()
    response = await first
    assert response.file_id == "file_upload_1"
    assert len(puts) == 1
    assert deleted == []
    reservation = next(iter(upload_sessions.values()))
    assert files_routes.is_direct_file_upload_session(reservation) is True


@pytest.mark.asyncio
async def test_direct_upload_after_repeated_deletions_uses_fresh_stable_identity(monkeypatch):
    upload_sessions = install_basic_upload_fakes(monkeypatch)
    tombstones: dict[str, dict[str, object]] = {}
    replacement_id = "file_upload_1"
    for generation in range(20):
        file_id = replacement_id
        deletion_marker = f"2026-09-{generation + 1:02d} 12:00:00+00:00"
        tombstones[file_id] = {
            "id": file_id,
            "tenant_id": "default",
            "workspace_id": "default",
            "user_id": "user-a",
            "session_id": None,
            "original_name": "small.txt",
            "content_type": "text/plain",
            "storage_key": (
                "tenants/default/workspaces/default/sessions/unbound/"
                f"files/{file_id}/generations/direct_old_{generation}/content"
            ),
            "sha256": hashlib.sha256(b"x").hexdigest(),
            "size_bytes": 1,
            "lifecycle_state": "deleted",
            "delete_requested_at": deletion_marker,
        }
        replacement_id = files_routes._next_direct_upload_file_id(
            file_id,
            deletion_marker,
        )
    written: list[str] = []

    async def deleted_then_missing(_conn, *, file_id, **_kwargs):
        return tombstones.get(file_id)

    class RecordingStorage:
        def put_bytes(self, *, storage_key, content, content_type):
            written.append(storage_key)
            return StoredObject(
                storage_key=storage_key,
                sha256=hashlib.sha256(content).hexdigest(),
                size_bytes=len(content),
            )

        def delete_object(self, *, storage_key):
            raise AssertionError(f"successful replacement must not delete {storage_key}")

    monkeypatch.setattr(files_routes, "get_file", deleted_then_missing)
    monkeypatch.setattr(files_routes, "ObjectStorage", RecordingStorage)

    response = await files_routes.upload_file(
        FakeUploadFile("small.txt", "text/plain", b"x"),
        "default",
        None,
        upload_principal(),
    )

    assert response.file_id == replacement_id
    assert f"/files/{replacement_id}/generations/" in written[0]
    reservation = next(iter(upload_sessions.values()))
    assert reservation["file_id"] == replacement_id
    assert files_routes.is_direct_file_upload_session(reservation) is True


@pytest.mark.asyncio
async def test_completed_upload_abort_queues_unbound_file_for_durable_deletion(monkeypatch):
    upload_sessions = install_basic_upload_fakes(monkeypatch)
    upload_sessions["upload-1"] = {
        "id": "upload-1",
        "tenant_id": "default",
        "workspace_id": "default",
        "user_id": "user-a",
        "session_id": None,
        "file_id": "file-1",
        "storage_key": "files/file-1/content",
        "upload_id": "s3-upload-1",
        "state": "completed",
    }
    queued: list[dict[str, object]] = []

    async def queue_file(_conn, **kwargs):
        queued.append(kwargs)
        return {"created": True}

    monkeypatch.setattr(files_routes, "queue_unbound_file_for_deletion", queue_file)

    response = await files_routes.abort_multipart_upload("upload-1", upload_principal())

    assert response.status_code == 204
    assert queued == [
        {
            "tenant_id": "default",
            "workspace_id": "default",
            "user_id": "user-a",
            "file_id": "file-1",
        }
    ]
    assert upload_sessions["upload-1"]["state"] == "completed"


@pytest.mark.asyncio
async def test_direct_abort_retries_expired_cleanup_after_storage_failure(monkeypatch):
    upload_sessions = install_basic_upload_fakes(monkeypatch)
    upload_sessions["file_upload_1"] = {
        "id": "file_upload_1",
        "tenant_id": "default",
        "workspace_id": "default",
        "user_id": "user-a",
        "session_id": None,
        "file_id": "file_upload_1",
        "storage_key": (
            "tenants/default/workspaces/default/sessions/unbound/"
            "files/file_upload_1/generations/direct_owner/content"
        ),
        "upload_id": "direct_owner",
        "state": "pending",
    }
    deletes = 0

    class RetryStorage:
        def delete_object(self, *, storage_key):
            nonlocal deletes
            assert storage_key == (
                "tenants/default/workspaces/default/sessions/unbound/"
                "files/file_upload_1/generations/direct_owner/content"
            )
            deletes += 1
            if deletes == 1:
                raise RuntimeError("storage unavailable")

    monkeypatch.setattr(files_routes, "ObjectStorage", RetryStorage)

    with pytest.raises(RuntimeError, match="storage unavailable"):
        await files_routes.abort_multipart_upload("file_upload_1", upload_principal())
    assert upload_sessions["file_upload_1"]["state"] == "expired"

    response = await files_routes.abort_multipart_upload(
        "file_upload_1",
        upload_principal(),
    )

    assert response.status_code == 204
    assert deletes == 2
    assert upload_sessions == {}


@pytest.mark.asyncio
async def test_upload_cleanup_claims_already_expired_sessions():
    statements: list[str] = []

    class Cursor:
        async def fetchall(self):
            return []

    class Connection:
        async def execute(self, statement, params):
            statements.append(" ".join(statement.split()))
            assert params == (100,)
            return Cursor()

    assert await upload_postgres.expire_file_upload_sessions(Connection()) == []
    assert "where state in ('pending', 'completing', 'expired') and expires_at <= now()" in statements[0]


@pytest.mark.asyncio
async def test_upload_cleanup_retry_uses_database_time_backoff():
    statements: list[tuple[str, tuple[object, ...]]] = []

    class Connection:
        async def execute(self, statement, params):
            statements.append((statement, params))

    await upload_postgres.retry_expired_file_upload_session(
        Connection(),
        upload_session_id="upload-1",
    )

    assert "set state = 'expired', expires_at = now() + (%s * interval '1 second')" in " ".join(statements[0][0].split())
    assert statements[0][1] == (60, "upload-1")


@pytest.mark.asyncio
async def test_upload_rejects_oversize_with_bounded_read_and_no_storage_write(monkeypatch):
    install_basic_upload_fakes(monkeypatch)
    upload = FakeUploadFile("large.txt", "text/plain", b"A" * (files_routes.MAX_DIRECT_UPLOAD_BYTES + 1))

    class ForbiddenStorage:
        def put_bytes(self, *, storage_key, content, content_type):
            raise AssertionError("oversize upload must not write storage")

    monkeypatch.setattr(files_routes, "ObjectStorage", ForbiddenStorage)

    with pytest.raises(HTTPException) as exc_info:
        await files_routes.upload_file(
            file=upload,
            workspace_id="default",
            session_id=None,
            principal=upload_principal(),
        )

    assert exc_info.value.status_code == 413
    assert exc_info.value.detail == "file_too_large"
    assert upload.read_calls == [files_routes.MAX_DIRECT_UPLOAD_BYTES + 1]


@pytest.mark.parametrize(
    ("filename", "content_type", "payload"),
    [
        ("report.txt", "text/plain", b"   <!DOCTYPE html><html><body>boom</body></html>"),
        ("report.txt", "text/plain", b"\xef\xbb\xbf<!DOCTYPE html><html><body>bom</body></html>"),
        ("report.docx", "text/html", b"plain text that claims to be html"),
        ("diagram.svg", "image/svg+xml", b"<svg xmlns='http://www.w3.org/2000/svg'></svg>"),
    ],
)
@pytest.mark.asyncio
async def test_upload_rejects_active_content_by_extension_mime_or_sniff(
    monkeypatch,
    filename: str,
    content_type: str,
    payload: bytes,
):
    install_basic_upload_fakes(monkeypatch)
    upload = FakeUploadFile(filename, content_type, payload)

    class ForbiddenStorage:
        def put_bytes(self, *, storage_key, content, content_type):
            raise AssertionError("active content must not write storage")

    monkeypatch.setattr(files_routes, "ObjectStorage", ForbiddenStorage)

    with pytest.raises(HTTPException) as exc_info:
        await files_routes.upload_file(
            file=upload,
            workspace_id="default",
            session_id=None,
            principal=upload_principal(),
        )

    assert exc_info.value.status_code == 415
    assert exc_info.value.detail == "unsupported_file_type"
    assert upload.read_calls == [files_routes.MAX_DIRECT_UPLOAD_BYTES + 1]


@pytest.mark.parametrize(
    ("payload", "label"),
    [
        (codecs.BOM_UTF16_LE + "<!DOCTYPE html><html><body>utf16le</body></html>".encode("utf-16-le"), "utf16le-html"),
        (codecs.BOM_UTF16_BE + "<svg xmlns='http://www.w3.org/2000/svg'></svg>".encode("utf-16-be"), "utf16be-svg"),
        (codecs.BOM_UTF32_LE + "<?xml version='1.0'?><root>utf32le</root>".encode("utf-32-le"), "utf32le-xml"),
        (codecs.BOM_UTF32_BE + "<!DOCTYPE html><html><body>utf32be</body></html>".encode("utf-32-be"), "utf32be-html"),
    ],
)
@pytest.mark.asyncio
async def test_upload_rejects_bom_wrapped_active_content_before_repository_or_storage_side_effects(
    monkeypatch,
    payload: bytes,
    label: str,
):
    install_forbidden_repository_side_effects(monkeypatch)
    upload = FakeUploadFile(f"{label}.txt", "text/plain", payload)

    class ForbiddenStorage:
        def put_bytes(self, *, storage_key, content, content_type):
            raise AssertionError("unsupported upload must not write storage")

    monkeypatch.setattr(files_routes, "ObjectStorage", ForbiddenStorage)

    with pytest.raises(HTTPException) as exc_info:
        await files_routes.upload_file(
            file=upload,
            workspace_id="default",
            session_id=None,
            principal=upload_principal(),
        )

    assert exc_info.value.status_code == 415
    assert exc_info.value.detail == "unsupported_file_type"
    assert upload.read_calls == [files_routes.MAX_DIRECT_UPLOAD_BYTES + 1]


@pytest.mark.asyncio
async def test_upload_rejects_zip_path_traversal_without_storage_write(monkeypatch):
    install_basic_upload_fakes(monkeypatch)
    upload = FakeUploadFile("escape.zip", "application/zip", make_path_traversal_zip())

    class ForbiddenStorage:
        def put_bytes(self, *, storage_key, content, content_type):
            raise AssertionError("unsafe zip must not write storage")

    monkeypatch.setattr(files_routes, "ObjectStorage", ForbiddenStorage)

    with pytest.raises(HTTPException) as exc_info:
        await files_routes.upload_file(
            file=upload,
            workspace_id="default",
            session_id=None,
            principal=upload_principal(),
        )

    assert exc_info.value.status_code == 415
    assert exc_info.value.detail == "unsupported_file_type"


@pytest.mark.asyncio
async def test_upload_rejects_large_uncompressed_zip_without_storage_write(monkeypatch):
    install_basic_upload_fakes(monkeypatch)
    upload = FakeUploadFile("bomb.zip", "application/zip", make_large_uncompressed_zip())

    class ForbiddenStorage:
        def put_bytes(self, *, storage_key, content, content_type):
            raise AssertionError("unsafe zip must not write storage")

    monkeypatch.setattr(files_routes, "ObjectStorage", ForbiddenStorage)

    with pytest.raises(HTTPException) as exc_info:
        await files_routes.upload_file(
            file=upload,
            workspace_id="default",
            session_id=None,
            principal=upload_principal(),
        )

    assert exc_info.value.status_code == 415
    assert exc_info.value.detail == "unsupported_file_type"


@pytest.mark.asyncio
async def test_upload_rejects_malformed_zip_without_storage_write(monkeypatch):
    install_basic_upload_fakes(monkeypatch)
    upload = FakeUploadFile("broken.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", b"not-a-zip")

    class ForbiddenStorage:
        def put_bytes(self, *, storage_key, content, content_type):
            raise AssertionError("malformed OOXML must not write storage")

    monkeypatch.setattr(files_routes, "ObjectStorage", ForbiddenStorage)

    with pytest.raises(HTTPException) as exc_info:
        await files_routes.upload_file(
            file=upload,
            workspace_id="default",
            session_id=None,
            principal=upload_principal(),
        )

    assert exc_info.value.status_code == 415
    assert exc_info.value.detail == "unsupported_file_type"


@pytest.mark.asyncio
async def test_upload_accepts_safe_ooxml_and_preserves_storage_contract(monkeypatch):
    install_basic_upload_fakes(monkeypatch)
    upload = FakeUploadFile(
        "review.docx",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        make_safe_docx_bytes(),
    )
    storage_calls: list[dict[str, object]] = []

    class RecordingStorage:
        def put_bytes(self, *, storage_key, content, content_type):
            storage_calls.append(
                {
                    "storage_key": storage_key,
                    "content": content,
                    "content_type": content_type,
                }
            )
            return StoredObject(
                storage_key=storage_key,
                sha256="sha-docx",
                size_bytes=len(content),
            )

    monkeypatch.setattr(files_routes, "ObjectStorage", RecordingStorage)

    response = await files_routes.upload_file(
        file=upload,
        workspace_id="default",
        session_id=None,
        principal=upload_principal(),
    )

    assert response.file_id == "file_upload_1"
    assert response.name == "review.docx"
    assert response.sha256 == "sha-docx"
    assert response.size_bytes == len(make_safe_docx_bytes())
    assert storage_calls[0]["content_type"] == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    assert storage_calls[0]["storage_key"] == (
        "tenants/default/workspaces/default/sessions/unbound/files/file_upload_1/"
        "generations/direct_file_upload_1/content"
    )


@pytest.mark.parametrize(
    "filename",
    [
        None,
        "",
        ".",
        "..",
        "../escape.docx",
        "..\\escape.docx",
        "C:\\escape.docx",
        "report\n.docx",
        "report\u202ecod.exe",
        "\ud800.docx",
        "report.docx ",
        "report.docx.",
        "CON.txt",
        "CON .txt",
        "COM1 .log",
        "report?.docx",
        f"{'测' * 84}.docx",
    ],
)
@pytest.mark.asyncio
async def test_upload_rejects_unsafe_or_overlong_filename_before_body_or_side_effects(
    monkeypatch,
    filename,
):
    install_forbidden_repository_side_effects(monkeypatch)
    upload = FakeUploadFile(filename, "text/plain", b"content")

    class ForbiddenStorage:
        def __init__(self):
            raise AssertionError("invalid filename must reject before storage initialization")

    monkeypatch.setattr(files_routes, "ObjectStorage", ForbiddenStorage)

    with pytest.raises(HTTPException) as exc_info:
        await files_routes.upload_file(
            file=upload,
            workspace_id="default",
            session_id=None,
            principal=upload_principal(),
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "invalid_file_name"
    assert upload.read_calls == []


@pytest.mark.asyncio
async def test_upload_preserves_cjk_name_normalizes_nfc_and_decouples_storage_key(monkeypatch):
    install_basic_upload_fakes(monkeypatch)
    decomposed_name = "参考文件1-IP248A项目基本信息收集表-Re\u0301sume\u0301.docx"
    normalized_name = "参考文件1-IP248A项目基本信息收集表-Résumé.docx"
    upload = FakeUploadFile(
        decomposed_name,
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        make_safe_docx_bytes(),
    )
    stored_rows: list[dict[str, object]] = []
    storage_keys: list[str] = []

    async def record_file(_conn, **kwargs):
        stored_rows.append(kwargs)

    class RecordingStorage:
        def put_bytes(self, *, storage_key, content, content_type):
            storage_keys.append(storage_key)
            return StoredObject(
                storage_key=storage_key,
                sha256="sha-unicode",
                size_bytes=len(content),
            )

    monkeypatch.setattr(files_routes, "create_file", record_file)
    monkeypatch.setattr(files_routes, "ObjectStorage", RecordingStorage)

    response = await files_routes.upload_file(
        file=upload,
        workspace_id="default",
        session_id=None,
        principal=upload_principal(),
    )

    assert response.name == normalized_name
    assert stored_rows[0]["original_name"] == normalized_name
    assert storage_keys == [
        "tenants/default/workspaces/default/sessions/unbound/files/file_upload_1/"
        "generations/direct_file_upload_1/content"
    ]
    assert normalized_name not in storage_keys[0]


def test_upload_filename_accepts_exact_utf8_byte_limit():
    filename = f"{'测' * 83}a.docx"

    assert len(filename.encode("utf-8")) == files_routes.MAX_UPLOAD_FILENAME_UTF8_BYTES
    assert files_routes._normalize_upload_filename(filename) == filename


@pytest.mark.asyncio
async def test_compat_upload_preserves_frontend_response_contract(monkeypatch):
    install_basic_upload_fakes(monkeypatch)
    upload = FakeUploadFile(
        "参考文件1-IP248A项目基本信息收集表.docx",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        make_safe_docx_bytes(),
    )

    class RecordingStorage:
        def put_bytes(self, *, storage_key, content, content_type):
            return StoredObject(
                storage_key=storage_key,
                sha256="sha-docx",
                size_bytes=len(content),
            )

    monkeypatch.setattr(files_routes, "ObjectStorage", RecordingStorage)

    response = await compat_routes.upload_file(
        file=upload,
        folder="uploads",
        workspace_id="default",
        session_id=None,
        principal=upload_principal(),
    )

    assert response["key"] == "file_upload_1"
    assert response["file_id"] == "file_upload_1"
    assert response["url"] == "/api/ai/files/file_upload_1"
    assert response["name"] == "参考文件1-IP248A项目基本信息收集表.docx"
    assert response["type"] == "uploads"
    assert response["mimeType"] == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    assert response["mime_type"] == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    assert response["size"] == len(make_safe_docx_bytes())
    assert response["sha256"] == "sha-docx"
