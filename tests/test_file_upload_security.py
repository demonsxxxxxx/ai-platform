from __future__ import annotations

from contextlib import asynccontextmanager
import io
import zipfile

import pytest
from fastapi import HTTPException

from app.auth import AuthPrincipal
from app.routes import files as files_routes
from app.routes import lambchat_compat as compat_routes
from app.storage import StoredObject


def upload_principal(*, permissions: list[str] | None = None) -> AuthPrincipal:
    return AuthPrincipal(
        user_id="user-a",
        display_name="User A",
        tenant_id="default",
        roles=["user"],
        permissions=["file:upload", "file:upload:document"] if permissions is None else permissions,
    )


class FakeUploadFile:
    def __init__(self, filename: str, content_type: str, data: bytes):
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


def install_basic_upload_fakes(monkeypatch):
    monkeypatch.setattr(files_routes, "transaction", fake_transaction)
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
async def test_upload_rejects_oversize_with_bounded_read_and_no_storage_write(monkeypatch):
    install_basic_upload_fakes(monkeypatch)
    upload = FakeUploadFile("large.txt", "text/plain", b"A" * (files_routes.MAX_UPLOAD_BYTES + 1))

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
    assert upload.read_calls == [files_routes.MAX_UPLOAD_BYTES + 1]


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
    assert upload.read_calls == [files_routes.MAX_UPLOAD_BYTES + 1]


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
    assert response.sha256 == "sha-docx"
    assert response.size_bytes == len(make_safe_docx_bytes())
    assert storage_calls[0]["content_type"] == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    assert storage_calls[0]["storage_key"] == "tenants/default/workspaces/default/sessions/unbound/files/file_upload_1/review.docx"


@pytest.mark.asyncio
async def test_compat_upload_preserves_frontend_response_contract(monkeypatch):
    install_basic_upload_fakes(monkeypatch)
    upload = FakeUploadFile(
        "review.docx",
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
    assert response["name"] == "review.docx"
    assert response["type"] == "uploads"
    assert response["mimeType"] == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    assert response["mime_type"] == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    assert response["size"] == len(make_safe_docx_bytes())
    assert response["sha256"] == "sha-docx"
