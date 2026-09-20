import asyncio
import codecs
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import io
import re
import unicodedata
from pathlib import Path, PurePosixPath, PureWindowsPath
from threading import Event
from urllib.parse import quote
import zipfile

from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import JSONResponse

from app.files.api import (
    MAX_UPLOAD_BYTES,
    abort_file_upload_session,
    activate_file_upload_session,
    claim_direct_file_upload_session,
    complete_file_upload_session,
    create_file_upload_session,
    delete_expired_file_upload_session,
    direct_upload_storage_key,
    expire_file_upload_sessions,
    get_authorized_file_upload_session,
    get_file_storage_usage,
    is_direct_file_upload_session,
    parse_multipart_upload_complete_request,
    parse_multipart_upload_create_request,
    retry_expired_file_upload_session,
)
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
    FileDeletionResponse,
    SessionInputFileResponse,
    SessionInputFilesResponse,
)
from app.repositories import (
    FileDeletionBlockedError,
    ObjectDeletionStateError,
    RepositoryNotFoundError,
    append_audit_log,
    create_file,
    ensure_user,
    ensure_workspace,
    get_admin_artifact,
    get_authorized_artifact,
    get_authorized_run,
    get_authorized_session,
    get_file,
    get_scoped_context_file,
    list_authorized_session_input_files,
    new_id,
    queue_unbound_file_for_deletion,
)
from app.storage import (
    ObjectStorage,
    ObjectStorageSizeLimitError,
    StoredObject,
    StorageIOBusyError,
    StorageIOTimeoutError,
    run_storage_io,
)
from app.settings import get_settings
from app.validation import assert_safe_id

router = APIRouter()
MAX_DIRECT_UPLOAD_BYTES = 32 * 1024 * 1024
MULTIPART_THRESHOLD_BYTES = 32 * 1024 * 1024
MULTIPART_PART_BYTES = 8 * 1024 * 1024
MAX_UPLOAD_FILENAME_UTF8_BYTES = 255
WINDOWS_INVALID_FILENAME_CHARS = frozenset('<>:"/\\|?*')
WINDOWS_RESERVED_FILE_BASENAMES = frozenset(
    {
        "con",
        "prn",
        "aux",
        "nul",
        *(f"com{index}" for index in range(1, 10)),
        *(f"lpt{index}" for index in range(1, 10)),
    }
)
UNSAFE_FILENAME_UNICODE_CATEGORIES = frozenset({"Cc", "Cf", "Cs"})
ARTIFACT_DOWNLOAD_PERMISSION = "artifact:download"


def _direct_upload_file_id(
    *,
    tenant_id: str,
    workspace_id: str,
    user_id: str,
    session_id: str | None,
    display_name: str,
    content_type: str,
    content: bytes,
) -> str:
    identity = hashlib.sha256()
    for value in (
        tenant_id,
        workspace_id,
        user_id,
        session_id or "",
        display_name,
        content_type,
    ):
        identity.update(value.encode("utf-8"))
        identity.update(b"\0")
    identity.update(content)
    return f"file_{identity.hexdigest()[:32]}"


def _next_direct_upload_file_id(file_id: str, deletion_marker: object) -> str:
    if deletion_marker is None:
        raise ValueError("deleted_file_missing_deletion_marker")
    identity = hashlib.sha256()
    identity.update(file_id.encode("utf-8"))
    identity.update(b"\0")
    identity.update(str(deletion_marker).encode("utf-8"))
    return f"file_{identity.hexdigest()[:32]}"


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


@dataclass(frozen=True, slots=True)
class UploadFileResponse:
    file_id: str
    name: str
    sha256: str
    size_bytes: int


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


def _normalize_upload_filename(value: str | None) -> str:
    normalized = unicodedata.normalize("NFC", value or "")
    windows_path = PureWindowsPath(normalized)
    posix_path = PurePosixPath(normalized)
    reserved_basename = normalized.split(".", 1)[0].rstrip(" ").casefold()
    invalid = (
        not normalized
        or normalized in {".", ".."}
        or normalized.endswith((" ", "."))
        or any(
            unicodedata.category(character) in UNSAFE_FILENAME_UNICODE_CATEGORIES
            for character in normalized
        )
        or len(normalized.encode("utf-8")) > MAX_UPLOAD_FILENAME_UTF8_BYTES
        or any(character in WINDOWS_INVALID_FILENAME_CHARS for character in normalized)
        or windows_path.drive != ""
        or windows_path.is_absolute()
        or posix_path.is_absolute()
        or len(windows_path.parts) != 1
        or len(posix_path.parts) != 1
        or reserved_basename in WINDOWS_RESERVED_FILE_BASENAMES
    )
    if invalid:
        raise HTTPException(status_code=400, detail="invalid_file_name")
    return normalized


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


async def _run_storage(operation, /, *args, **kwargs):
    try:
        return await run_storage_io(operation, *args, **kwargs)
    except (StorageIOBusyError, StorageIOTimeoutError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def _put_direct_upload(
    storage: ObjectStorage,
    *,
    storage_key: str,
    content: bytes,
    content_type: str,
    abandoned: Event,
) -> StoredObject:
    stored = storage.put_bytes(
        storage_key=storage_key,
        content=content,
        content_type=content_type,
    )
    if abandoned.is_set():
        storage.delete_object(storage_key=storage_key)
    return stored


def _create_multipart_upload(
    storage: ObjectStorage,
    *,
    storage_key: str,
    content_type: str,
    abandoned: Event,
) -> str:
    upload_id = storage.create_multipart_upload(
        storage_key=storage_key,
        content_type=content_type,
    )
    if abandoned.is_set():
        storage.abort_multipart_upload(storage_key=storage_key, upload_id=upload_id)
    return upload_id


async def _completed_upload_response(conn, row: dict[str, object]) -> UploadFileResponse:
    file_row = await get_file(
        conn,
        tenant_id=str(row["tenant_id"]),
        file_id=str(row["file_id"]),
    )
    if (
        file_row is None
        or str(file_row.get("lifecycle_state") or "") != "active"
        or str(file_row.get("user_id") or "") != str(row["user_id"])
        or str(file_row.get("workspace_id") or "") != str(row["workspace_id"])
        or str(file_row.get("storage_key") or "") != str(row["storage_key"])
    ):
        raise HTTPException(status_code=409, detail="upload_session_result_invalid")
    return UploadFileResponse(
        file_id=str(file_row["id"]),
        name=str(file_row["original_name"]),
        sha256=str(file_row["sha256"]),
        size_bytes=int(file_row["size_bytes"]),
    )


async def _cleanup_expired_upload_sessions(storage: ObjectStorage) -> None:
    async with transaction() as conn:
        expired_sessions = await expire_file_upload_sessions(conn)
    for expired in expired_sessions:
        upload_id = str(expired["upload_id"])
        storage_key = str(expired["storage_key"])
        initialization_unknown = upload_id == f"initializing_{expired['id']}"
        try:
            if is_direct_file_upload_session(expired):
                await _run_storage(storage.delete_object, storage_key=storage_key)
            elif initialization_unknown:
                await _run_storage(
                    storage.abort_multipart_uploads_for_key,
                    storage_key=storage_key,
                )
            else:
                await _run_storage(
                    storage.cleanup_multipart_upload,
                    storage_key=storage_key,
                    upload_id=upload_id,
                )
        except asyncio.CancelledError:
            async with transaction() as conn:
                await retry_expired_file_upload_session(
                    conn,
                    upload_session_id=str(expired["id"]),
                )
            raise
        except Exception:
            async with transaction() as conn:
                await retry_expired_file_upload_session(
                    conn,
                    upload_session_id=str(expired["id"]),
                )
        else:
            async with transaction() as conn:
                if initialization_unknown:
                    # ponytail: retain a small tombstone until ObjectStorage offers idempotent upload creation.
                    await retry_expired_file_upload_session(
                        conn,
                        upload_session_id=str(expired["id"]),
                        delay_seconds=86_400,
                    )
                else:
                    await delete_expired_file_upload_session(
                        conn,
                        upload_session_id=str(expired["id"]),
                    )


async def _read_bounded_request_body(request: Request, max_bytes: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(status_code=413, detail="multipart_part_too_large")
        chunks.append(chunk)
    return b"".join(chunks)


async def _read_bounded_upload(file: UploadFile) -> bytes:
    content = await file.read(MAX_DIRECT_UPLOAD_BYTES + 1)
    if len(content) > MAX_DIRECT_UPLOAD_BYTES:
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


def _validate_zip_payload(content: bytes | Path) -> None:
    try:
        archive = zipfile.ZipFile(io.BytesIO(content) if isinstance(content, bytes) else content)
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


def _validate_upload_file(*, filename: str, declared_content_type: str, path: Path) -> None:
    with path.open("rb") as stream:
        sample = stream.read(ACTIVE_SNIFF_BYTES)
    suffix = PurePosixPath(filename).suffix.lower()
    normalized_content_type = _normalized_content_type(declared_content_type)
    if (
        suffix in ACTIVE_CONTENT_EXTENSIONS
        or normalized_content_type in ACTIVE_CONTENT_MIME_TYPES
        or normalized_content_type.endswith("+xml")
        or _looks_like_active_content(sample)
    ):
        _reject_unsupported_upload()
    if (
        suffix in ZIP_CLASS_EXTENSIONS
        or normalized_content_type in ZIP_CLASS_MIME_TYPES
        or sample.startswith((b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"))
    ):
        _validate_zip_payload(path)


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

    display_name = _normalize_upload_filename(file.filename)
    content_type = file.content_type or "application/octet-stream"
    content = await _read_bounded_upload(file)
    await _run_storage(
        _validate_upload_content,
        filename=display_name,
        declared_content_type=content_type,
        content=content,
    )
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

    file_id = _direct_upload_file_id(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        user_id=principal.user_id,
        session_id=session_id,
        display_name=display_name,
        content_type=content_type,
        content=content,
    )
    content_sha256 = hashlib.sha256(content).hexdigest()
    # ponytail: linear only in same-content deletion churn; persist a generation if that becomes hot.
    seen_file_ids: set[str] = set()
    while file_id not in seen_file_ids:
        seen_file_ids.add(file_id)
        storage_key_prefix = (
            f"tenants/{tenant_id}/workspaces/{workspace_id}/"
            f"sessions/{session_id or 'unbound'}/files/{file_id}/generations/"
        )
        async with transaction() as conn:
            committed = await get_file(conn, tenant_id=tenant_id, file_id=file_id)
        if committed is None:
            break
        if (
            str(committed.get("user_id") or "") != principal.user_id
            or str(committed.get("workspace_id") or "") != workspace_id
            or committed.get("session_id") != session_id
            or str(committed.get("original_name") or "") != display_name
            or str(committed.get("content_type") or "") != content_type
            or not str(committed.get("storage_key") or "").startswith(storage_key_prefix)
            or not str(committed.get("storage_key") or "").endswith("/content")
            or str(committed.get("sha256") or "").casefold() != content_sha256
            or int(committed.get("size_bytes") or -1) != len(content)
        ):
            raise HTTPException(status_code=409, detail="file_upload_identity_conflict")
        lifecycle_state = str(committed.get("lifecycle_state") or "")
        if lifecycle_state == "active":
            return UploadFileResponse(
                file_id=file_id,
                name=display_name,
                sha256=content_sha256,
                size_bytes=len(content),
            )
        if lifecycle_state not in {"delete_pending", "deleted"}:
            raise HTTPException(status_code=409, detail="file_upload_identity_conflict")
        try:
            file_id = _next_direct_upload_file_id(
                file_id,
                committed.get("delete_requested_at") or committed.get("deleted_at"),
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail="file_upload_identity_conflict") from exc
    else:
        raise HTTPException(status_code=409, detail="file_upload_identity_conflict")

    storage = ObjectStorage()
    await _cleanup_expired_upload_sessions(storage)
    identity_suffix = file_id.removeprefix("file_")
    upload_session_id = f"upload_direct_{identity_suffix}"
    upload_id = ""
    storage_key = ""
    claimed = False
    async with transaction() as conn:
        reservation = await get_authorized_file_upload_session(
            conn,
            upload_session_id=upload_session_id,
            tenant_id=tenant_id,
            user_id=principal.user_id,
        )
        if reservation is None:
            upload_id = f"direct_{new_id('upload_owner')}"
            storage_key = direct_upload_storage_key(
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                session_id=session_id,
                file_id=file_id,
                upload_id=upload_id,
            )
            usage = await get_file_storage_usage(
                conn,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                user_id=principal.user_id,
            )
            settings = get_settings()
            if usage["active_uploads"] >= settings.file_upload_max_active_sessions:
                raise HTTPException(status_code=429, detail="upload_session_limit_exceeded")
            if (
                usage["stored_bytes"] + usage["reserved_bytes"] + len(content)
                > settings.file_storage_quota_bytes
            ):
                raise HTTPException(status_code=413, detail="file_storage_quota_exceeded")
            claimed = await claim_direct_file_upload_session(
                conn,
                upload_session_id=upload_session_id,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                user_id=principal.user_id,
                session_id=session_id,
                file_id=file_id,
                original_name=display_name,
                content_type=content_type,
                expected_size_bytes=len(content),
                storage_key=storage_key,
                upload_id=upload_id,
            )
            if not claimed:
                reservation = await get_authorized_file_upload_session(
                    conn,
                    upload_session_id=upload_session_id,
                    tenant_id=tenant_id,
                    user_id=principal.user_id,
                )
    if reservation is not None:
        reserved_upload_id = str(reservation.get("upload_id") or "")
        reserved_storage_key = str(reservation.get("storage_key") or "")
        expected_reserved_key = direct_upload_storage_key(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            session_id=session_id,
            file_id=file_id,
            upload_id=reserved_upload_id,
        )
        if (
            str(reservation.get("workspace_id") or "") != workspace_id
            or reservation.get("session_id") != session_id
            or str(reservation.get("file_id") or "") != file_id
            or str(reservation.get("original_name") or "") != display_name
            or str(reservation.get("content_type") or "") != content_type
            or int(reservation.get("expected_size_bytes") or -1) != len(content)
            or not reserved_upload_id.startswith("direct_")
            or reserved_storage_key != expected_reserved_key
        ):
            raise HTTPException(status_code=409, detail="file_upload_identity_conflict")
        upload_id = reserved_upload_id
        storage_key = reserved_storage_key
        if reservation.get("state") == "completed":
            async with transaction() as conn:
                return await _completed_upload_response(conn, reservation)
        raise HTTPException(status_code=503, detail="direct_upload_in_progress")
    if not claimed:
        raise HTTPException(status_code=409, detail="file_upload_identity_conflict")

    try:
        abandoned_put = Event()
        stored = await run_storage_io(
            _put_direct_upload,
            storage,
            storage_key=storage_key,
            content=content,
            content_type=content_type,
            abandoned=abandoned_put,
            on_abandoned=abandoned_put.set,
        )
    except StorageIOBusyError as exc:
        async with transaction() as conn:
            await abort_file_upload_session(conn, upload_session_id=upload_session_id, state="expired")
            await delete_expired_file_upload_session(conn, upload_session_id=upload_session_id)
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except StorageIOTimeoutError as exc:
        # The durable reservation prevents a retry from racing the retained writer.
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    try:
        async with transaction() as conn:
            current = await get_authorized_file_upload_session(
                conn,
                upload_session_id=upload_session_id,
                tenant_id=tenant_id,
                user_id=principal.user_id,
                for_update=True,
            )
            if current is None or current.get("state") != "pending":
                raise RepositoryNotFoundError("direct_upload_reservation_lost")
            await create_file(
                conn,
                file_id=file_id,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                user_id=principal.user_id,
                session_id=session_id,
                original_name=display_name,
                content_type=content_type,
                size_bytes=stored.size_bytes,
                storage_key=stored.storage_key,
                sha256=stored.sha256,
            )
            await complete_file_upload_session(conn, upload_session_id=upload_session_id)
    except Exception as original_error:
        try:
            async with transaction() as conn:
                current = await get_authorized_file_upload_session(
                    conn,
                    upload_session_id=upload_session_id,
                    tenant_id=tenant_id,
                    user_id=principal.user_id,
                )
                if current is not None and current.get("state") == "completed":
                    return await _completed_upload_response(conn, current)
        except Exception:
            raise original_error
        async with transaction() as conn:
            await abort_file_upload_session(
                conn,
                upload_session_id=upload_session_id,
                state="expired",
            )
        try:
            await _run_storage(storage.delete_object, storage_key=stored.storage_key)
        except Exception:
            raise original_error
        async with transaction() as conn:
            await delete_expired_file_upload_session(conn, upload_session_id=upload_session_id)
        raise original_error
    return UploadFileResponse(
        file_id=file_id,
        name=display_name,
        sha256=stored.sha256,
        size_bytes=stored.size_bytes,
    )


@router.post("/files/uploads")
async def initiate_multipart_upload(
    request: object = Body(...),
    principal: AuthPrincipal = Depends(require_principal),
) -> dict[str, object]:
    _require_upload_permissions(principal)
    try:
        request = parse_multipart_upload_create_request(request)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if request.size_bytes <= 0:
        raise HTTPException(status_code=400, detail="empty_file_not_supported")
    try:
        tenant_id = assert_safe_id(principal.tenant_id, "tenant_id")
        workspace_id = assert_safe_id(request.workspace_id, "workspace_id")
        session_id = assert_safe_id(request.session_id, "session_id") if request.session_id else None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    display_name = _normalize_upload_filename(request.name)
    content_type = _normalized_content_type(request.content_type) or "application/octet-stream"
    file_id = new_id("file")
    upload_session_id = new_id("upload")
    initializing_upload_id = f"initializing_{upload_session_id}"
    storage_key = f"tenants/{tenant_id}/workspaces/{workspace_id}/sessions/{session_id or 'unbound'}/files/{file_id}/content"
    part_count = (request.size_bytes + MULTIPART_PART_BYTES - 1) // MULTIPART_PART_BYTES
    storage = ObjectStorage()
    await _cleanup_expired_upload_sessions(storage)
    try:
        async with transaction() as conn:
            await ensure_workspace(conn, tenant_id=tenant_id, workspace_id=workspace_id)
            await ensure_user(
                conn,
                tenant_id=tenant_id,
                user_id=principal.user_id,
                display_name=principal.display_name,
            )
            if session_id and await get_authorized_session(
                conn,
                tenant_id=tenant_id,
                user_id=principal.user_id,
                session_id=session_id,
            ) is None:
                raise RepositoryNotFoundError("session_not_found")
            usage = await get_file_storage_usage(
                conn,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                user_id=principal.user_id,
            )
            settings = get_settings()
            if usage["active_uploads"] >= settings.file_upload_max_active_sessions:
                raise HTTPException(status_code=429, detail="upload_session_limit_exceeded")
            if (
                usage["stored_bytes"]
                + usage["reserved_bytes"]
                + request.size_bytes
                > settings.file_storage_quota_bytes
            ):
                raise HTTPException(status_code=413, detail="file_storage_quota_exceeded")
            await create_file_upload_session(
                conn,
                upload_session_id=upload_session_id,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                user_id=principal.user_id,
                session_id=session_id,
                file_id=file_id,
                original_name=display_name,
                content_type=content_type,
                expected_size_bytes=request.size_bytes,
                part_size_bytes=MULTIPART_PART_BYTES,
                part_count=part_count,
                storage_key=storage_key,
                upload_id=initializing_upload_id,
            )
    except RepositoryNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    abandoned = Event()
    try:
        upload_id = await run_storage_io(
            _create_multipart_upload,
            storage,
            storage_key=storage_key,
            content_type=content_type,
            abandoned=abandoned,
            on_abandoned=abandoned.set,
        )
    except StorageIOBusyError as exc:
        async with transaction() as conn:
            await abort_file_upload_session(
                conn,
                upload_session_id=upload_session_id,
                state="expired",
            )
        await _cleanup_expired_upload_sessions(storage)
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except StorageIOTimeoutError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except asyncio.CancelledError:
        raise
    except Exception:
        async with transaction() as conn:
            await abort_file_upload_session(
                conn,
                upload_session_id=upload_session_id,
                state="expired",
            )
        await _cleanup_expired_upload_sessions(storage)
        raise

    try:
        async with transaction() as conn:
            activated = await activate_file_upload_session(
                conn,
                upload_session_id=upload_session_id,
                expected_upload_id=initializing_upload_id,
                upload_id=upload_id,
            )
            if not activated:
                raise RuntimeError("upload_session_activation_conflict")
    except Exception as original_error:
        try:
            async with transaction() as conn:
                current = await get_authorized_file_upload_session(
                    conn,
                    upload_session_id=upload_session_id,
                    tenant_id=tenant_id,
                    user_id=principal.user_id,
                )
        except Exception:
            raise original_error
        if current is None or str(current.get("upload_id") or "") != upload_id:
            raise original_error

    return {
        "upload_session_id": upload_session_id,
        "name": display_name,
        "size_bytes": request.size_bytes,
        "part_size_bytes": MULTIPART_PART_BYTES,
        "parts": [
            {
                "part_number": part_number,
                "url": f"/api/ai/files/uploads/{upload_session_id}/parts/{part_number}",
            }
            for part_number in range(1, part_count + 1)
        ],
    }


@router.put("/files/uploads/{upload_session_id}/parts/{part_number}")
async def upload_multipart_part(
    upload_session_id: str,
    part_number: int,
    request: Request,
    principal: AuthPrincipal = Depends(require_principal),
) -> dict[str, str]:
    _require_upload_permissions(principal)
    try:
        upload_session_id = assert_safe_id(upload_session_id, "upload_session_id")
        tenant_id = assert_safe_id(principal.tenant_id, "tenant_id")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    async with transaction() as conn:
        row = await get_authorized_file_upload_session(
            conn,
            upload_session_id=upload_session_id,
            tenant_id=tenant_id,
            user_id=principal.user_id,
        )
    if row is None:
        raise HTTPException(status_code=404, detail="upload_session_not_found")
    if row["state"] != "pending":
        raise HTTPException(status_code=409, detail="upload_session_not_pending")
    if row["expires_at"] <= datetime.now(timezone.utc):
        raise HTTPException(status_code=410, detail="upload_session_expired")
    if part_number < 1 or part_number > int(row["part_count"]):
        raise HTTPException(status_code=400, detail="invalid_multipart_part")
    expected_size = min(
        int(row["part_size_bytes"]),
        int(row["expected_size_bytes"]) - (part_number - 1) * int(row["part_size_bytes"]),
    )
    content = await _read_bounded_request_body(request, expected_size)
    if len(content) != expected_size:
        raise HTTPException(status_code=400, detail="multipart_part_size_mismatch")
    try:
        etag = await _run_storage(
            ObjectStorage().upload_multipart_part,
            storage_key=row["storage_key"],
            upload_id=row["upload_id"],
            part_number=part_number,
            content=content,
        )
    except Exception as exc:
        raise HTTPException(status_code=503, detail="multipart_part_upload_failed") from exc
    return {"etag": etag}


@router.post("/files/uploads/{upload_session_id}/complete", response_model=UploadFileResponse)
async def complete_multipart_upload(
    upload_session_id: str,
    request: object = Body(...),
    principal: AuthPrincipal = Depends(require_principal),
) -> UploadFileResponse:
    try:
        request = parse_multipart_upload_complete_request(request)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    _require_upload_permissions(principal)
    try:
        upload_session_id = assert_safe_id(upload_session_id, "upload_session_id")
        tenant_id = assert_safe_id(principal.tenant_id, "tenant_id")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    part_numbers = [part.part_number for part in request.parts]
    if part_numbers != sorted(set(part_numbers)):
        raise HTTPException(status_code=400, detail="invalid_multipart_parts")
    storage = ObjectStorage()
    async with transaction() as conn:
        row = await get_authorized_file_upload_session(
            conn,
            upload_session_id=upload_session_id,
            tenant_id=tenant_id,
            user_id=principal.user_id,
        )
        if row is not None and row["state"] == "completed":
            return await _completed_upload_response(conn, row)
    if row is None:
        raise HTTPException(status_code=404, detail="upload_session_not_found")
    if row["state"] not in {"pending", "completing"}:
        raise HTTPException(status_code=409, detail="upload_session_not_pending")
    if row["expires_at"] <= datetime.now(timezone.utc):
        await _run_storage(
            storage.cleanup_multipart_upload,
            storage_key=row["storage_key"],
            upload_id=row["upload_id"],
        )
        async with transaction() as conn:
            await abort_file_upload_session(conn, upload_session_id=upload_session_id, state="expired")
        raise HTTPException(status_code=410, detail="upload_session_expired")
    expected_parts = int(row["part_count"])
    if part_numbers != list(range(1, expected_parts + 1)):
        raise HTTPException(status_code=400, detail="invalid_multipart_parts")
    parts = [{"ETag": part.etag, "PartNumber": part.part_number} for part in request.parts]
    completion_error: Exception | None = None
    try:
        await _run_storage(
            storage.complete_multipart_upload,
            storage_key=row["storage_key"],
            upload_id=row["upload_id"],
            parts=parts,
        )
    except Exception as exc:
        completion_error = exc
    try:
        downloaded = await _run_storage(
            storage.download_to_tempfile,
            storage_key=row["storage_key"],
            max_bytes=MAX_UPLOAD_BYTES,
        )
    except ObjectStorageSizeLimitError as exc:
        await _run_storage(storage.delete_object, storage_key=row["storage_key"])
        async with transaction() as conn:
            await abort_file_upload_session(conn, upload_session_id=upload_session_id)
        raise HTTPException(status_code=413, detail="file_too_large") from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail="multipart_completion_unresolved") from (
            completion_error or exc
        )

    temporary_path = Path(downloaded.path)
    try:
        if downloaded.size_bytes != int(row["expected_size_bytes"]):
            raise HTTPException(status_code=400, detail="multipart_size_mismatch")
        await _run_storage(
            _validate_upload_file,
            filename=row["original_name"],
            declared_content_type=row["content_type"],
            path=temporary_path,
        )
    except HTTPException:
        await _run_storage(storage.delete_object, storage_key=row["storage_key"])
        async with transaction() as conn:
            await abort_file_upload_session(conn, upload_session_id=upload_session_id)
        raise
    finally:
        await _run_storage(temporary_path.unlink, missing_ok=True)

    try:
        async with transaction() as conn:
            current = await get_authorized_file_upload_session(
                conn,
                upload_session_id=upload_session_id,
                tenant_id=tenant_id,
                user_id=principal.user_id,
                for_update=True,
            )
            if current is None:
                raise RepositoryNotFoundError("upload_session_not_found")
            if current["state"] == "completed":
                return await _completed_upload_response(conn, current)
            if current["state"] not in {"pending", "completing"}:
                raise RepositoryNotFoundError("upload_session_not_pending")
            await create_file(
                conn,
                file_id=current["file_id"],
                tenant_id=tenant_id,
                workspace_id=current["workspace_id"],
                user_id=principal.user_id,
                session_id=current["session_id"],
                original_name=current["original_name"],
                content_type=current["content_type"],
                size_bytes=downloaded.size_bytes,
                storage_key=current["storage_key"],
                sha256=downloaded.sha256,
            )
            await complete_file_upload_session(conn, upload_session_id=upload_session_id)
    except RepositoryNotFoundError as exc:
        await _run_storage(storage.delete_object, storage_key=row["storage_key"])
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as original_error:
        try:
            async with transaction() as conn:
                current = await get_authorized_file_upload_session(
                    conn,
                    upload_session_id=upload_session_id,
                    tenant_id=tenant_id,
                    user_id=principal.user_id,
                )
                if current is not None and current["state"] == "completed":
                    return await _completed_upload_response(conn, current)
        except Exception:
            raise original_error
        raise HTTPException(status_code=503, detail="multipart_completion_unresolved") from original_error
    return UploadFileResponse(
        file_id=str(row["file_id"]),
        name=str(row["original_name"]),
        sha256=downloaded.sha256,
        size_bytes=downloaded.size_bytes,
    )


@router.post("/files/uploads/{upload_session_id}/abort", status_code=204)
async def abort_multipart_upload(
    upload_session_id: str,
    principal: AuthPrincipal = Depends(require_principal),
) -> Response:
    _require_upload_permissions(principal)
    try:
        upload_session_id = assert_safe_id(upload_session_id, "upload_session_id")
        tenant_id = assert_safe_id(principal.tenant_id, "tenant_id")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    async with transaction() as conn:
        row = await get_authorized_file_upload_session(
            conn,
            upload_session_id=upload_session_id,
            tenant_id=tenant_id,
            user_id=principal.user_id,
            for_update=True,
        )
        if row is None:
            raise HTTPException(status_code=404, detail="upload_session_not_found")
        if row["state"] in {"pending", "completing"}:
            await abort_file_upload_session(
                conn,
                upload_session_id=upload_session_id,
                state="expired",
            )
        elif row["state"] == "completed":
            try:
                queued = await queue_unbound_file_for_deletion(
                    conn,
                    tenant_id=tenant_id,
                    workspace_id=str(row["workspace_id"]),
                    user_id=principal.user_id,
                    file_id=str(row["file_id"]),
                )
            except FileDeletionBlockedError:
                queued = True
            if queued is None:
                raise HTTPException(status_code=409, detail="upload_session_result_invalid")
    if row["state"] in {"pending", "completing", "expired"}:
        storage = ObjectStorage()
        if is_direct_file_upload_session(row):
            await _run_storage(storage.delete_object, storage_key=row["storage_key"])
        else:
            await _run_storage(
                storage.cleanup_multipart_upload,
                storage_key=row["storage_key"],
                upload_id=row["upload_id"],
            )
        async with transaction() as conn:
            await delete_expired_file_upload_session(
                conn,
                upload_session_id=upload_session_id,
            )
    return Response(status_code=204)


@router.delete("/files/{file_id}", response_model=FileDeletionResponse)
async def delete_unbound_file(
    file_id: str,
    workspace_id: str = "default",
    principal: AuthPrincipal = Depends(require_principal),
) -> FileDeletionResponse:
    """Queue one exact owned and still-unbound file for durable deletion."""

    try:
        tenant_id = assert_safe_id(principal.tenant_id, "tenant_id")
        workspace_id = assert_safe_id(workspace_id, "workspace_id")
        file_id = assert_safe_id(file_id, "file_id")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        async with transaction() as conn:
            result = await queue_unbound_file_for_deletion(
                conn,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                user_id=principal.user_id,
                file_id=file_id,
            )
            if result is None:
                raise HTTPException(status_code=404, detail="file_not_found")
            if bool(result.get("created")):
                await append_audit_log(
                    conn,
                    tenant_id=tenant_id,
                    user_id=principal.user_id,
                    action="file.deletion_requested",
                    target_type="file",
                    target_id=file_id,
                    trace_id=standard_trace_id(file_id),
                    payload_json={
                        "workspace_id": workspace_id,
                        "lifecycle_state": result["lifecycle_state"],
                        "deletion_state": result["deletion_state"],
                        "source": "user_request",
                    },
                )
    except FileDeletionBlockedError as exc:
        raise HTTPException(status_code=409, detail="file_deletion_blocked") from exc
    except ObjectDeletionStateError as exc:
        raise HTTPException(status_code=409, detail="file_deletion_state_conflict") from exc
    return FileDeletionResponse(
        file_id=str(result["file_id"]),
        lifecycle_state=str(result["lifecycle_state"]),
        deletion_state=str(result["deletion_state"]),
        reconcile_required=bool(result["reconcile_required"]),
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
    content = await _run_storage(
        ObjectStorage().get_bytes,
        storage_key=str(file_row["storage_key"]),
    )
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
    content = await _run_storage(
        ObjectStorage().get_bytes,
        storage_key=str(file_row["storage_key"]),
    )
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
    content = await _run_storage(
        ObjectStorage().get_bytes,
        storage_key=artifact["storage_key"],
    )
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
    content = await _run_storage(
        ObjectStorage().get_bytes,
        storage_key=artifact["storage_key"],
    )
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
