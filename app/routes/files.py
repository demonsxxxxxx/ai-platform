import codecs
import io
import re
from pathlib import PurePosixPath, PureWindowsPath
from urllib.parse import quote
import zipfile

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile

from app.artifact_preview import artifact_preview_allowed
from app.auth import AuthPrincipal, is_ai_admin, require_principal
from app.control_plane_contracts import standard_trace_id
from app.db import transaction
from app.models import UploadFileResponse
from app.repositories import (
    RepositoryNotFoundError,
    append_audit_log,
    create_file,
    ensure_user,
    ensure_workspace,
    get_admin_artifact,
    get_authorized_artifact,
    get_authorized_session,
    new_id,
)
from app.storage import ObjectStorage
from app.validation import assert_safe_id

router = APIRouter()
MAX_UPLOAD_BYTES = 50 * 1024 * 1024
SAFE_FILENAME_PATTERN = re.compile(r"[^A-Za-z0-9._() -]+")
ARTIFACT_DOWNLOAD_PERMISSION = "artifact:download"
UPLOAD_PERMISSIONS = ("file:upload", "file:upload:document")
ACTIVE_CONTENT_EXTENSIONS = frozenset({".htm", ".html", ".mht", ".mhtml", ".shtml", ".svg", ".xhtml", ".xml"})
ACTIVE_CONTENT_MIME_TYPES = frozenset(
    {
        "application/svg+xml",
        "application/xhtml+xml",
        "application/xml",
        "image/svg+xml",
        "message/rfc822",
        "multipart/related",
        "text/html",
        "text/xml",
    }
)
ZIP_CLASS_EXTENSIONS = frozenset({".docx", ".pptx", ".xlsx", ".zip"})
ZIP_CLASS_MIME_TYPES = frozenset(
    {
        "application/zip",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }
)
MAX_ZIP_ENTRY_COUNT = 2000
MAX_ZIP_SINGLE_ENTRY_BYTES = 32 * 1024 * 1024
MAX_ZIP_TOTAL_BYTES = 128 * 1024 * 1024
ACTIVE_SNIFF_BYTES = 4096


def _effective_permission_set(principal: AuthPrincipal) -> set[str]:
    granted = {item.strip() for item in principal.permissions if item.strip()}
    if is_ai_admin(principal):
        granted.update({ARTIFACT_DOWNLOAD_PERMISSION, *UPLOAD_PERMISSIONS})
    return granted


def _require_permission(principal: AuthPrincipal, permission: str) -> None:
    if permission not in _effective_permission_set(principal):
        raise HTTPException(status_code=403, detail=f"missing_permission:{permission}")


def _require_upload_permissions(principal: AuthPrincipal) -> None:
    for permission in UPLOAD_PERMISSIONS:
        _require_permission(principal, permission)


def _normalized_content_type(value: object) -> str:
    return str(value or "").split(";", 1)[0].strip().lower()


async def _read_bounded_upload(file: UploadFile) -> bytes:
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="file_too_large")
    return content


def _looks_like_active_content(content: bytes) -> bool:
    raw_sample = content[:ACTIVE_SNIFF_BYTES]
    text_samples: list[str] = []
    try:
        text_samples.append(raw_sample.decode("utf-8-sig"))
    except UnicodeDecodeError:
        pass
    if raw_sample.startswith((codecs.BOM_UTF16_BE, codecs.BOM_UTF16_LE)):
        try:
            text_samples.append(raw_sample.decode("utf-16"))
        except UnicodeDecodeError:
            pass
    if raw_sample.startswith((codecs.BOM_UTF32_BE, codecs.BOM_UTF32_LE)):
        try:
            text_samples.append(raw_sample.decode("utf-32"))
        except UnicodeDecodeError:
            pass
    for text_sample in text_samples:
        sample = text_sample.lstrip().lower()
        if sample.startswith(("<!doctype html", "<html", "<svg", "<?xml")):
            return True
        if sample.startswith("mime-version:") and "content-type:" in sample and "text/html" in sample:
            return True
    return False


def _reject_unsupported_upload() -> None:
    raise HTTPException(status_code=415, detail="unsupported_file_type")


def _validate_zip_payload(content: bytes) -> None:
    try:
        archive = zipfile.ZipFile(io.BytesIO(content))
    except (zipfile.BadZipFile, ValueError):
        _reject_unsupported_upload()
    total_uncompressed = 0
    try:
        entries = archive.infolist()
    finally:
        archive.close()
    if len(entries) > MAX_ZIP_ENTRY_COUNT:
        _reject_unsupported_upload()
    for info in entries:
        if info.flag_bits & 0x1:
            _reject_unsupported_upload()
        normalized_name = info.filename.replace("\\", "/")
        if not normalized_name:
            _reject_unsupported_upload()
        posix_path = PurePosixPath(normalized_name)
        windows_path = PureWindowsPath(info.filename)
        if normalized_name.startswith("/") or windows_path.is_absolute() or windows_path.drive:
            _reject_unsupported_upload()
        if any(part == ".." for part in posix_path.parts):
            _reject_unsupported_upload()
        if info.file_size < 0 or info.file_size > MAX_ZIP_SINGLE_ENTRY_BYTES:
            _reject_unsupported_upload()
        total_uncompressed += info.file_size
        if total_uncompressed > MAX_ZIP_TOTAL_BYTES:
            _reject_unsupported_upload()


def _validate_upload_content(*, filename: str, declared_content_type: str, content: bytes) -> None:
    suffix = PurePosixPath(filename).suffix.lower()
    normalized_content_type = _normalized_content_type(declared_content_type)
    if (
        suffix in ACTIVE_CONTENT_EXTENSIONS
        or normalized_content_type in ACTIVE_CONTENT_MIME_TYPES
        or normalized_content_type.endswith("+xml")
        or _looks_like_active_content(content)
    ):
        _reject_unsupported_upload()
    if (
        suffix in ZIP_CLASS_EXTENSIONS
        or normalized_content_type in ZIP_CLASS_MIME_TYPES
        or content.startswith((b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"))
    ):
        _validate_zip_payload(content)


@router.post("/files", response_model=UploadFileResponse)
async def upload_file(
    file: UploadFile = File(...),
    workspace_id: str = Form("default"),
    session_id: str | None = Form(None),
    principal: AuthPrincipal = Depends(require_principal),
) -> UploadFileResponse:
    _require_upload_permissions(principal)
    tenant_id = principal.tenant_id
    try:
        tenant_id = assert_safe_id(tenant_id, "tenant_id")
        workspace_id = assert_safe_id(workspace_id, "workspace_id")
        if session_id:
            session_id = assert_safe_id(session_id, "session_id")
        async with transaction() as conn:
            await ensure_workspace(conn, tenant_id=tenant_id, workspace_id=workspace_id)
            await ensure_user(
                conn,
                tenant_id=tenant_id,
                user_id=principal.user_id,
                display_name=principal.display_name,
            )
            if session_id:
                session = await get_authorized_session(
                    conn,
                    tenant_id=tenant_id,
                    user_id=principal.user_id,
                    session_id=session_id,
                )
                if session is None:
                    raise RepositoryNotFoundError("session_not_found")
    except RepositoryNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    file_id = new_id("file")
    safe_name = SAFE_FILENAME_PATTERN.sub("_", file.filename or "upload.bin").strip(" .") or "upload.bin"
    content_type = file.content_type or "application/octet-stream"
    content = await _read_bounded_upload(file)
    _validate_upload_content(filename=safe_name, declared_content_type=content_type, content=content)
    storage_key = f"tenants/{tenant_id}/workspaces/{workspace_id}/sessions/{session_id or 'unbound'}/files/{file_id}/{safe_name}"
    stored = ObjectStorage().put_bytes(
        storage_key=storage_key,
        content=content,
        content_type=content_type,
    )
    async with transaction() as conn:
        await create_file(
            conn,
            file_id=file_id,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            user_id=principal.user_id,
            session_id=session_id,
            original_name=safe_name,
            content_type=content_type,
            size_bytes=stored.size_bytes,
            storage_key=stored.storage_key,
            sha256=stored.sha256,
        )
    return UploadFileResponse(
        file_id=file_id,
        sha256=stored.sha256,
        size_bytes=stored.size_bytes,
    )


@router.get("/artifacts/{artifact_id}/download")
async def download_artifact(
    artifact_id: str,
    principal: AuthPrincipal = Depends(require_principal),
) -> Response:
    _require_permission(principal, ARTIFACT_DOWNLOAD_PERMISSION)
    tenant_id = principal.tenant_id
    try:
        tenant_id = assert_safe_id(tenant_id, "tenant_id")
        artifact_id = assert_safe_id(artifact_id, "artifact_id")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    async with transaction() as conn:
        artifact = await get_authorized_artifact(
            conn,
            tenant_id=tenant_id,
            user_id=principal.user_id,
            artifact_id=artifact_id,
        )
        if artifact is None and is_ai_admin(principal):
            artifact = await get_admin_artifact(conn, tenant_id=tenant_id, artifact_id=artifact_id)
            if artifact is not None:
                await append_audit_log(
                    conn,
                    tenant_id=tenant_id,
                    user_id=principal.user_id,
                    action="admin_artifact_downloaded",
                    target_type="artifact",
                    target_id=artifact_id,
                    trace_id=artifact.get("trace_id") or standard_trace_id(str(artifact.get("run_id") or "")),
                    payload_json={
                        "admin_user_id": principal.user_id,
                        "target_user_id": artifact.get("target_user_id"),
                        "artifact_id": artifact_id,
                        "run_id": artifact.get("run_id"),
                    },
                )
    if artifact is None:
        raise HTTPException(status_code=404, detail="artifact_not_found")
    filename = PurePosixPath(str(artifact["storage_key"])).name or f"{artifact_id}.bin"
    content = ObjectStorage().get_bytes(storage_key=artifact["storage_key"])
    return Response(
        content=content,
        media_type=artifact["content_type"] or "application/octet-stream",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}",
            "X-Artifact-Id": artifact_id,
        },
    )


@router.get("/artifacts/{artifact_id}/preview")
async def preview_artifact(
    artifact_id: str,
    principal: AuthPrincipal = Depends(require_principal),
) -> Response:
    _require_permission(principal, ARTIFACT_DOWNLOAD_PERMISSION)
    tenant_id = principal.tenant_id
    try:
        tenant_id = assert_safe_id(tenant_id, "tenant_id")
        artifact_id = assert_safe_id(artifact_id, "artifact_id")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    async with transaction() as conn:
        artifact = await get_authorized_artifact(
            conn,
            tenant_id=tenant_id,
            user_id=principal.user_id,
            artifact_id=artifact_id,
        )
        admin_preview = False
        if artifact is None and is_ai_admin(principal):
            artifact = await get_admin_artifact(conn, tenant_id=tenant_id, artifact_id=artifact_id)
            admin_preview = artifact is not None
        if artifact is None:
            raise HTTPException(status_code=404, detail="artifact_not_found")
        if not artifact_preview_allowed(artifact.get("content_type")):
            raise HTTPException(status_code=415, detail="artifact_preview_not_allowed")
        if admin_preview:
            await append_audit_log(
                conn,
                tenant_id=tenant_id,
                user_id=principal.user_id,
                action="admin_artifact_previewed",
                target_type="artifact",
                target_id=artifact_id,
                trace_id=artifact.get("trace_id") or standard_trace_id(str(artifact.get("run_id") or "")),
                payload_json={
                    "admin_user_id": principal.user_id,
                    "target_user_id": artifact.get("target_user_id"),
                    "artifact_id": artifact_id,
                    "run_id": artifact.get("run_id"),
                },
            )
    filename = PurePosixPath(str(artifact["storage_key"])).name or f"{artifact_id}.bin"
    content = ObjectStorage().get_bytes(storage_key=artifact["storage_key"])
    return Response(
        content=content,
        media_type=artifact["content_type"] or "application/octet-stream",
        headers={
            "Content-Disposition": f"inline; filename*=UTF-8''{quote(filename)}",
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "X-Artifact-Id": artifact_id,
        },
    )
