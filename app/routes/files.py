import asyncio
import codecs
import io
import re
from pathlib import PurePosixPath, PureWindowsPath
from urllib.parse import quote
import zipfile

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile
from fastapi.responses import JSONResponse

from app.artifact_preview import artifact_preview_allowed
from app.auth import AuthPrincipal, is_ai_admin, require_principal
from app.control_plane_contracts import standard_trace_id
from app.db import transaction
from app.file_preview_contracts import (
    XLSX_CONTENT_TYPE,
    XlsxPreviewResponse,
    acquire_xlsx_preview_lease,
    build_xlsx_preview,
    run_xlsx_preview_job,
    xlsx_preview_identity_from_metadata,
    xlsx_preview_max_bytes,
)
from app.models import (
    SessionInputFileResponse,
    SessionInputFilesResponse,
    UploadFileResponse,
)
from app.repositories import (
    RepositoryNotFoundError,
    append_audit_log,
    create_file,
    ensure_user,
    ensure_workspace,
    get_admin_artifact,
    get_authorized_artifact,
    get_authorized_run,
    get_authorized_session,
    get_scoped_context_file,
    list_authorized_session_input_files,
    new_id,
)
from app.storage import ObjectStorage, ObjectStorageSizeLimitError
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
INPUT_FILE_PREVIEW_CONTENT_TYPES = frozenset(
    {
        "application/json",
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "image/avif",
        "image/bmp",
        "image/gif",
        "image/jpeg",
        "image/png",
        "image/tiff",
        "image/webp",
        "text/csv",
        "text/markdown",
        "text/plain",
    }
)
SAFE_RESPONSE_CONTENT_TYPE_PATTERN = re.compile(
    r"^[a-z0-9][a-z0-9!#$&^_.+-]*/[a-z0-9][a-z0-9!#$&^_.+-]*$"
)


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


def _safe_response_content_type(value: object) -> str:
    normalized = _normalized_content_type(value)
    if SAFE_RESPONSE_CONTENT_TYPE_PATTERN.fullmatch(normalized):
        return normalized
    return "application/octet-stream"


def _optional_nonnegative_int(value: object) -> int | None:
    try:
        normalized = int(value) if value is not None else None
    except (TypeError, ValueError):
        return None
    return normalized if normalized is not None and normalized >= 0 else None


def _input_file_preview_allowed(file_row: dict[str, object]) -> bool:
    content_type = _normalized_content_type(file_row.get("content_type"))
    if content_type == XLSX_CONTENT_TYPE:
        return xlsx_preview_identity_from_metadata(file_row).eligible
    return content_type in INPUT_FILE_PREVIEW_CONTENT_TYPES


async def _build_xlsx_preview_response(
    *,
    storage_key: str,
    declared_size_bytes: object,
    max_bytes: int,
    file_id: str,
    file_name: str,
    content_type: str,
    headers: dict[str, str],
    expected_sha256: str | None = None,
    expected_byte_count: int | None = None,
) -> JSONResponse:
    """Read and parse one ACL-authorized XLSX under one shared preview lease."""

    declared_size = _optional_nonnegative_int(declared_size_bytes)
    if declared_size is not None and declared_size > max_bytes:
        raise HTTPException(status_code=413, detail="xlsx_preview_file_too_large")
    lease = acquire_xlsx_preview_lease()
    if lease is None:
        raise HTTPException(status_code=503, detail="xlsx_preview_busy")

    def build_preview() -> XlsxPreviewResponse:
        raw = ObjectStorage().get_bytes_bounded(
            storage_key=storage_key,
            max_bytes=max_bytes,
        )
        return build_xlsx_preview(
            raw=raw,
            file_id=file_id,
            file_name=file_name,
            content_type=content_type,
            lease=lease,
            expected_sha256=expected_sha256,
            expected_byte_count=expected_byte_count,
        )

    try:
        preview = await run_xlsx_preview_job(
            lease=lease,
            job=build_preview,
        )
    except ObjectStorageSizeLimitError as exc:
        raise HTTPException(status_code=413, detail="xlsx_preview_file_too_large") from exc
    return JSONResponse(
        content=preview.model_dump(mode="json"),
        headers=headers,
    )


def _input_file_url(*, file_id: str, session_id: str, run_id: str, action: str) -> str:
    return (
        f"/api/ai/files/{quote(file_id, safe='')}/{action}"
        f"?session_id={quote(session_id, safe='')}&run_id={quote(run_id, safe='')}"
    )


def _input_file_response(
    *,
    file_row: dict[str, object],
    session_id: str,
) -> SessionInputFileResponse:
    file_id = str(file_row["id"])
    run_id = str(file_row["run_id"])
    name = str(file_row.get("original_name") or file_id)
    content_type = _safe_response_content_type(file_row.get("content_type"))
    return SessionInputFileResponse(
        file_id=file_id,
        run_id=run_id,
        name=name,
        mime_type=content_type,
        size_bytes=max(0, int(file_row.get("size_bytes") or 0)),
        preview_url=(
            _input_file_url(
                file_id=file_id,
                session_id=session_id,
                run_id=run_id,
                action="preview",
            )
            if _input_file_preview_allowed(file_row)
            else None
        ),
        download_url=_input_file_url(
            file_id=file_id,
            session_id=session_id,
            run_id=run_id,
            action="download",
        ),
        created_at=file_row.get("created_at"),
    )


async def _authorized_input_file(
    *,
    file_id: str,
    session_id: str,
    run_id: str,
    principal: AuthPrincipal,
) -> dict[str, object]:
    try:
        tenant_id = assert_safe_id(principal.tenant_id, "tenant_id")
        session_id = assert_safe_id(session_id, "session_id")
        run_id = assert_safe_id(run_id, "run_id")
        file_id = assert_safe_id(file_id, "file_id")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    async with transaction() as conn:
        session = await get_authorized_session(
            conn,
            tenant_id=tenant_id,
            user_id=principal.user_id,
            session_id=session_id,
        )
        if session is None:
            raise HTTPException(status_code=404, detail="input_file_not_found")
        workspace_id = str(session.get("workspace_id") or "")
        if not workspace_id:
            raise HTTPException(status_code=404, detail="input_file_not_found")
        file_row = await get_scoped_context_file(
            conn,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            user_id=principal.user_id,
            session_id=session_id,
            run_id=run_id,
            file_id=file_id,
        )
    if file_row is None:
        raise HTTPException(status_code=404, detail="input_file_not_found")
    return dict(file_row)


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
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    safe_name = SAFE_FILENAME_PATTERN.sub("_", file.filename or "upload.bin").strip(" .") or "upload.bin"
    content_type = file.content_type or "application/octet-stream"
    content = await _read_bounded_upload(file)
    _validate_upload_content(filename=safe_name, declared_content_type=content_type, content=content)
    try:
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

    file_id = new_id("file")
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


@router.get(
    "/chat/sessions/{session_id}/files",
    response_model=SessionInputFilesResponse,
)
async def list_session_input_files(
    session_id: str,
    principal: AuthPrincipal = Depends(require_principal),
) -> SessionInputFilesResponse:
    """Project persistent uploaded inputs for one exact owned session."""

    try:
        tenant_id = assert_safe_id(principal.tenant_id, "tenant_id")
        session_id = assert_safe_id(session_id, "session_id")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    async with transaction() as conn:
        session = await get_authorized_session(
            conn,
            tenant_id=tenant_id,
            user_id=principal.user_id,
            session_id=session_id,
        )
        if session is None:
            raise HTTPException(status_code=404, detail="session_not_found")
        workspace_id = str(session.get("workspace_id") or "")
        if not workspace_id:
            raise HTTPException(status_code=404, detail="session_not_found")
        rows = await list_authorized_session_input_files(
            conn,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            user_id=principal.user_id,
            session_id=session_id,
        )
    return SessionInputFilesResponse(
        session_id=session_id,
        files=[
            _input_file_response(file_row=dict(row), session_id=session_id)
            for row in rows
        ],
    )


@router.get("/files/{file_id}/preview")
async def preview_input_file(
    file_id: str,
    session_id: str,
    run_id: str,
    principal: AuthPrincipal = Depends(require_principal),
) -> Response:
    """Preview a passive input file authorized by one immutable run snapshot."""

    file_row = await _authorized_input_file(
        file_id=file_id,
        session_id=session_id,
        run_id=run_id,
        principal=principal,
    )
    xlsx_identity = xlsx_preview_identity_from_metadata(file_row)
    filename = str(file_row.get("original_name") or file_id)
    if not _input_file_preview_allowed(file_row):
        raise HTTPException(status_code=415, detail="input_file_preview_not_allowed")
    content_type = _safe_response_content_type(file_row.get("content_type"))
    if xlsx_identity.has_xlsx_content_type:
        if not xlsx_identity.eligible or xlsx_identity.file_name is None:
            raise HTTPException(status_code=415, detail="input_file_preview_not_allowed")
        filename = xlsx_identity.file_name
        max_bytes = xlsx_preview_max_bytes(
            file_name=filename,
            content_type=content_type,
        )
        return await _build_xlsx_preview_response(
            storage_key=str(file_row["storage_key"]),
            declared_size_bytes=file_row.get("size_bytes"),
            max_bytes=max_bytes,
            file_id=file_id,
            file_name=filename,
            content_type=content_type,
            expected_sha256=str(file_row.get("sha256") or "") or None,
            expected_byte_count=_optional_nonnegative_int(file_row.get("size_bytes")),
            headers={
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
                "X-Input-File-Id": file_id,
            },
        )
    content = ObjectStorage().get_bytes(storage_key=str(file_row["storage_key"]))
    return Response(
        content=content,
        media_type=content_type,
        headers={
            "Content-Disposition": f"inline; filename*=UTF-8''{quote(filename, safe='')}",
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "X-Input-File-Id": file_id,
        },
    )


@router.get("/files/{file_id}/download")
async def download_input_file(
    file_id: str,
    session_id: str,
    run_id: str,
    principal: AuthPrincipal = Depends(require_principal),
) -> Response:
    """Download an input file authorized by one immutable run snapshot."""

    file_row = await _authorized_input_file(
        file_id=file_id,
        session_id=session_id,
        run_id=run_id,
        principal=principal,
    )
    filename = str(file_row.get("original_name") or file_id)
    content = ObjectStorage().get_bytes(storage_key=str(file_row["storage_key"]))
    return Response(
        content=content,
        media_type=_safe_response_content_type(file_row.get("content_type")),
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename, safe='')}",
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "X-Input-File-Id": file_id,
        },
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
            admin_candidate = await get_admin_artifact(
                conn,
                tenant_id=tenant_id,
                artifact_id=artifact_id,
            )
            target_user_id = str(admin_candidate.get("target_user_id") or "") if admin_candidate else ""
            target_run_id = str(admin_candidate.get("run_id") or "") if admin_candidate else ""
            if target_user_id and target_run_id:
                active_run = await get_authorized_run(
                    conn,
                    tenant_id=tenant_id,
                    user_id=target_user_id,
                    run_id=target_run_id,
                )
                if active_run is not None:
                    artifact = admin_candidate
                    admin_preview = True
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
    content_type = _safe_response_content_type(artifact.get("content_type"))
    xlsx_identity = xlsx_preview_identity_from_metadata(artifact)
    if xlsx_identity.has_xlsx_content_type:
        if not xlsx_identity.eligible or xlsx_identity.file_name is None:
            raise HTTPException(status_code=415, detail="artifact_preview_not_allowed")
        filename = xlsx_identity.file_name
        max_bytes = xlsx_preview_max_bytes(
            file_name=filename,
            content_type=content_type,
        )
        return await _build_xlsx_preview_response(
            storage_key=str(artifact["storage_key"]),
            declared_size_bytes=artifact.get("size_bytes"),
            max_bytes=max_bytes,
            file_id=artifact_id,
            file_name=filename,
            content_type=content_type,
            expected_byte_count=_optional_nonnegative_int(artifact.get("size_bytes")),
            headers={
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
                "X-Artifact-Id": artifact_id,
            },
        )
    content = ObjectStorage().get_bytes(storage_key=artifact["storage_key"])
    return Response(
        content=content,
        media_type=content_type,
        headers={
            "Content-Disposition": f"inline; filename*=UTF-8''{quote(filename)}",
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "X-Artifact-Id": artifact_id,
        },
    )
