import re
from pathlib import PurePosixPath
from urllib.parse import quote

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


@router.post("/files", response_model=UploadFileResponse)
async def upload_file(
    file: UploadFile = File(...),
    workspace_id: str = Form("default"),
    session_id: str | None = Form(None),
    principal: AuthPrincipal = Depends(require_principal),
) -> UploadFileResponse:
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

    content = await file.read()
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="file_too_large")
    file_id = new_id("file")
    safe_name = SAFE_FILENAME_PATTERN.sub("_", file.filename or "upload.bin").strip(" .") or "upload.bin"
    storage_key = f"tenants/{tenant_id}/workspaces/{workspace_id}/sessions/{session_id or 'unbound'}/files/{file_id}/{safe_name}"
    stored = ObjectStorage().put_bytes(
        storage_key=storage_key,
        content=content,
        content_type=file.content_type or "application/octet-stream",
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
            content_type=file.content_type or "application/octet-stream",
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
