"""Compose Run artifact authorization with existing storage and Office preview readers."""
from pathlib import PurePosixPath
from urllib.parse import quote

from botocore.exceptions import ClientError
from fastapi import HTTPException, Response

from app.artifacts.api import AdminRunArtifactAccess
from app.artifacts.infrastructure.admin_run_access_postgres import get_admin_run_artifact
from app.auth import is_ai_admin, require_principal
from app.db import transaction
from app.identity.infrastructure.audit_postgres import append_audit_log
from app.routes import files
from app.runs.transport.admin_artifacts import build_admin_run_artifact_router
from app.storage import ObjectStorage


async def _read_content(*, artifact, action):
    filename = PurePosixPath(str(artifact.get("label") or artifact["id"])).name
    content_type = files._safe_response_content_type(artifact.get("content_type"))
    headers = {"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff", "X-Artifact-Id": artifact["id"]}
    try:
        if action == "preview":
            identity = files.xlsx_preview_identity_from_metadata(artifact)
            if identity.has_xlsx_content_type:
                if not identity.eligible or identity.file_name is None:
                    raise HTTPException(status_code=415, detail="artifact_preview_not_allowed")
                return await files._build_xlsx_preview_response(
                    storage_key=artifact["storage_key"], declared_size_bytes=artifact.get("size_bytes"),
                    max_bytes=files.xlsx_preview_max_bytes(file_name=identity.file_name, content_type=content_type),
                    file_id=artifact["id"], file_name=identity.file_name, content_type=content_type,
                    expected_byte_count=files._optional_nonnegative_int(artifact.get("size_bytes")), headers=headers,
                )
        content = await files._run_storage(ObjectStorage().get_bytes, storage_key=artifact["storage_key"])
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in {"NoSuchKey", "404", "NotFound"}:
            raise HTTPException(status_code=404, detail="artifact_not_found") from exc
        raise HTTPException(status_code=503, detail="artifact_storage_unavailable") from exc
    disposition = "inline" if action == "preview" else "attachment"
    headers["Content-Disposition"] = f"{disposition}; filename*=UTF-8''{quote(filename, safe='')}"
    return Response(content=content, media_type=content_type, headers=headers)


def build_admin_run_artifacts_router():
    return build_admin_run_artifact_router(
        access=AdminRunArtifactAccess(transaction=transaction, read_binding=get_admin_run_artifact, audit=append_audit_log),
        principal_dependency=require_principal, is_admin=is_ai_admin, read_content=_read_content,
    )
