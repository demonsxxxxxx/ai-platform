from __future__ import annotations

from collections import defaultdict
from pathlib import PurePosixPath
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from app import repositories
from app.auth import AuthPrincipal, is_ai_admin, require_principal
from app.artifact_preview import artifact_preview_allowed, artifact_preview_url
from app.control_plane_contracts import standard_trace_id
from app.db import transaction
from app.file_preview_contracts import xlsx_preview_identity_from_metadata
from app.models import (
    RevealedFileGroupedListResponse,
    RevealedFileItemResponse,
    RevealedFileListResponse,
    RevealedFileSessionGroupResponse,
    RevealedFileSessionResponse,
)
from app.validation import assert_safe_id

router = APIRouter()

ARTIFACT_READ = "artifact:download"
REVEALED_FILE_STATS_KEYS = ("total", "image", "video", "document", "code", "project", "other")


def _require_permission(principal: AuthPrincipal, permission: str) -> None:
    granted = {item.strip() for item in principal.permissions if item.strip()}
    if permission in granted or is_ai_admin(principal):
        return
    raise HTTPException(status_code=403, detail=f"missing_permission:{permission}")


def _safe_optional_id(value: str | None, field_name: str) -> str | None:
    if value is None or not str(value).strip():
        return None
    try:
        return assert_safe_id(str(value), field_name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _file_type_for(row: dict[str, Any]) -> str:
    content_type = str(row.get("content_type") or "").lower()
    artifact_type = str(row.get("artifact_type") or "").lower()
    suffix = PurePosixPath(str(row.get("storage_key") or "")).suffix.lower()
    if artifact_type in {"project", "reveal_project"}:
        return "project"
    if content_type.startswith("image/") or suffix in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}:
        return "image"
    if content_type.startswith("video/") or suffix in {".mp4", ".webm", ".mov"}:
        return "video"
    if suffix in {".py", ".ts", ".tsx", ".js", ".jsx", ".json", ".yaml", ".yml", ".md", ".css", ".html"}:
        return "code"
    if (
        content_type in {"application/pdf", "application/msword"}
        or content_type.startswith("text/")
        or suffix in {".pdf", ".doc", ".docx", ".txt", ".rtf", ".xlsx", ".pptx"}
    ):
        return "document"
    return "other"


def _file_name_for(row: dict[str, Any]) -> str:
    label = str(row.get("label") or "").strip()
    if label:
        return label
    storage_name = PurePosixPath(str(row.get("storage_key") or "")).name
    return storage_name or str(row.get("id") or "artifact")


def _revealed_item(row: dict[str, Any]) -> RevealedFileItemResponse:
    artifact_id = str(row.get("id") or "")
    run_id = str(row.get("run_id") or "")
    session_id = str(row.get("session_id") or "")
    file_type = _file_type_for(row)
    source = "reveal_project" if file_type == "project" else "reveal_file"
    file_name = _file_name_for(row)
    content_type = str(row.get("content_type") or "")
    xlsx_identity = xlsx_preview_identity_from_metadata(row)
    preview_allowed = artifact_preview_allowed(content_type) and (
        not xlsx_identity.has_xlsx_content_type or xlsx_identity.eligible
    )
    preview_url = artifact_preview_url(artifact_id) if artifact_id and preview_allowed else None
    download_url = f"/api/ai/artifacts/{artifact_id}/download" if artifact_id else None
    return RevealedFileItemResponse(
        id=artifact_id,
        file_key=artifact_id,
        file_name=file_name,
        file_type=file_type,  # type: ignore[arg-type]
        mime_type=content_type or None,
        file_size=int(row.get("size_bytes") or 0),
        preview_url=preview_url,
        download_url=download_url,
        url=preview_url,
        session_id=session_id,
        session_name=row.get("session_name"),
        trace_id=str(row.get("trace_id") or standard_trace_id(run_id or artifact_id)),
        project_id=row.get("workspace_id"),
        user_id=str(row.get("user_id") or ""),
        source=source,  # type: ignore[arg-type]
        description=str(row.get("artifact_type") or "") or None,
        original_path=_file_name_for(row) or None,
        created_at=row.get("created_at"),
        is_favorite=False,
        card_preview={
            "kind": "project" if file_type == "project" else ("document" if file_type == "document" else "fallback"),
            "title": _file_name_for(row),
            "subtitle": str(row.get("artifact_type") or "") or None,
        },
        project_meta=None,
    )


def _filter_revealed_items(
    rows: list[dict[str, Any]],
    *,
    file_type: str | None,
    favorites_only: bool,
) -> list[RevealedFileItemResponse]:
    items = [_revealed_item(row) for row in rows]
    if file_type:
        items = [item for item in items if item.file_type == file_type]
    if favorites_only:
        items = [item for item in items if item.is_favorite]
    return items


async def _revealed_items(
    principal: AuthPrincipal,
    *,
    session_id: str | None = None,
    project_id: str | None = None,
    search: str | None = None,
    sort_by: str = "created_at",
    sort_order: str = "desc",
    file_type: str | None = None,
    favorites_only: bool = False,
) -> list[RevealedFileItemResponse]:
    async with transaction() as conn:
        rows = await repositories.list_revealed_artifacts(
            conn,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            session_id=session_id,
            project_id=project_id,
            search=search,
            sort_by=sort_by,
            sort_order=sort_order,
        )
    return _filter_revealed_items(rows, file_type=file_type, favorites_only=favorites_only)


def _page(items: list[Any], *, page: int, page_size: int) -> list[Any]:
    start = (page - 1) * page_size
    return items[start : start + page_size]


@router.get("/files/revealed", response_model=RevealedFileListResponse)
async def list_revealed_files(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    file_type: str | None = Query(default=None),
    session_id: str | None = Query(default=None),
    project_id: str | None = Query(default=None),
    search: str | None = Query(default=None),
    sort_by: str = Query(default="created_at"),
    sort_order: str = Query(default="desc"),
    favorites_only: bool = Query(default=False),
    principal: AuthPrincipal = Depends(require_principal),
) -> RevealedFileListResponse:
    """Return ACL-scoped revealed artifacts as file workbench items."""

    _require_permission(principal, ARTIFACT_READ)
    safe_session_id = _safe_optional_id(session_id, "session_id")
    safe_project_id = _safe_optional_id(project_id, "project_id")
    items = await _revealed_items(
        principal,
        session_id=safe_session_id,
        project_id=safe_project_id,
        search=search,
        sort_by=sort_by,
        sort_order=sort_order,
        file_type=file_type,
        favorites_only=favorites_only,
    )
    return RevealedFileListResponse(items=_page(items, page=page, page_size=page_size), total=len(items), page=page, page_size=page_size)


@router.get("/files/revealed/grouped", response_model=RevealedFileGroupedListResponse)
async def list_revealed_files_grouped(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    file_type: str | None = Query(default=None),
    project_id: str | None = Query(default=None),
    search: str | None = Query(default=None),
    sort_by: str = Query(default="created_at"),
    sort_order: str = Query(default="desc"),
    favorites_only: bool = Query(default=False),
    principal: AuthPrincipal = Depends(require_principal),
) -> RevealedFileGroupedListResponse:
    """Return ACL-scoped revealed artifacts grouped by session."""

    _require_permission(principal, ARTIFACT_READ)
    safe_project_id = _safe_optional_id(project_id, "project_id")
    items = await _revealed_items(
        principal,
        project_id=safe_project_id,
        search=search,
        sort_by=sort_by,
        sort_order=sort_order,
        file_type=file_type,
        favorites_only=favorites_only,
    )
    grouped: dict[str, list[RevealedFileItemResponse]] = defaultdict(list)
    for item in items:
        grouped[item.session_id].append(item)
    groups = [
        RevealedFileSessionGroupResponse(
            session_id=session_id,
            session_name=session_items[0].session_name,
            file_count=len(session_items),
            files=session_items,
        )
        for session_id, session_items in grouped.items()
    ]
    return RevealedFileGroupedListResponse(
        sessions=_page(groups, page=page, page_size=page_size),
        total_sessions=len(groups),
        page=page,
        page_size=page_size,
    )


@router.get("/files/revealed/stats")
async def get_revealed_file_stats(
    project_id: str | None = Query(default=None),
    search: str | None = Query(default=None),
    principal: AuthPrincipal = Depends(require_principal),
) -> dict[str, int]:
    """Return type counts for ACL-scoped revealed artifacts."""

    _require_permission(principal, ARTIFACT_READ)
    safe_project_id = _safe_optional_id(project_id, "project_id")
    items = await _revealed_items(principal, project_id=safe_project_id, search=search)
    stats = {key: 0 for key in REVEALED_FILE_STATS_KEYS}
    stats["total"] = len(items)
    for item in items:
        stats[item.file_type] += 1
    return stats


@router.get("/files/revealed/sessions", response_model=list[RevealedFileSessionResponse])
async def list_revealed_file_sessions(
    project_id: str | None = Query(default=None),
    search: str | None = Query(default=None),
    principal: AuthPrincipal = Depends(require_principal),
) -> list[RevealedFileSessionResponse]:
    """Return session filters for ACL-scoped revealed artifacts."""

    _require_permission(principal, ARTIFACT_READ)
    safe_project_id = _safe_optional_id(project_id, "project_id")
    async with transaction() as conn:
        rows = await repositories.list_revealed_artifact_sessions(
            conn,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            project_id=safe_project_id,
            search=search,
        )
    return [
        RevealedFileSessionResponse(
            session_id=str(row.get("session_id") or ""),
            session_name=row.get("session_name"),
            file_count=int(row.get("file_count") or 0),
        )
        for row in rows
    ]


@router.patch("/files/revealed/{file_id}/favorite")
async def toggle_revealed_file_favorite(
    file_id: str,
    principal: AuthPrincipal = Depends(require_principal),
) -> dict[str, bool]:
    """Accept the frontend favorite toggle without persisting unsupported state."""

    _require_permission(principal, ARTIFACT_READ)
    _safe_optional_id(file_id, "file_id")
    return {"is_favorite": False}
