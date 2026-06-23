from __future__ import annotations

import base64
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import ValidationError

from app import repositories
from app.auth import AuthPrincipal, is_ai_admin, require_principal
from app.db import transaction
from app.models import (
    MarketplaceInstallResponse,
    MarketplaceSkillFilesResponse,
    MarketplaceSkillResponse,
    MarketplaceTagsResponse,
    PublicSkillDetailResponse,
    PublicSkillFileResponse,
    PublicSkillFileMutationResponse,
    PublicSkillFileUpdateRequest,
    PublicSkillResponse,
    PublicSkillsResponse,
    PublicSkillToggleRequest,
    PublicSkillToggleResponse,
    PublishToMarketplaceRequest,
)
from app.settings import get_settings
from app.validation import assert_safe_id

router = APIRouter()

SKILL_PERMISSIONS = ("skill:read", "skill:write", "skill:delete", "skill:admin")
MARKETPLACE_PERMISSIONS = ("marketplace:read", "marketplace:publish", "marketplace:admin")
ORDERED_PUBLIC_PERMISSIONS = (
    "skill:read",
    "skill:write",
    "skill:delete",
    "skill:admin",
    "marketplace:read",
    "marketplace:publish",
    "marketplace:admin",
)


@dataclass(frozen=True)
class SkillFileProjection:
    """Decoded file projected from a skill version source snapshot."""

    path: str
    content: bytes
    size: int


def _safe_skill_name(skill_name: str) -> str:
    try:
        return assert_safe_id(skill_name, "skill_name")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _safe_file_path(file_path: str) -> str:
    normalized = file_path.replace("\\", "/").strip("/")
    if not normalized:
        raise HTTPException(status_code=400, detail="skill_file_path_required")
    path = PurePosixPath(normalized)
    if path.is_absolute() or ".." in path.parts:
        raise HTTPException(status_code=400, detail="skill_file_path_escape")
    return path.as_posix()


def _effective_permission_set(principal: AuthPrincipal) -> set[str]:
    granted = {item.strip() for item in principal.permissions if item.strip()}
    if is_ai_admin(principal):
        granted.update(ORDERED_PUBLIC_PERMISSIONS)
    if "skill:admin" in granted:
        granted.update(SKILL_PERMISSIONS)
    if "marketplace:admin" in granted:
        granted.update(MARKETPLACE_PERMISSIONS)
    return granted


def _request_model(model_type: type[Any], payload: Any) -> Any:
    try:
        return model_type.model_validate(payload)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc


def _effective_permissions(principal: AuthPrincipal) -> list[str]:
    granted = _effective_permission_set(principal)
    return [permission for permission in ORDERED_PUBLIC_PERMISSIONS if permission in granted]


def _require_permission(principal: AuthPrincipal, permission: str) -> None:
    if permission not in _effective_permission_set(principal):
        raise HTTPException(status_code=403, detail=f"missing_permission:{permission}")


def _tags_from_row(row: dict[str, Any]) -> list[str]:
    source = row.get("source") if isinstance(row.get("source"), dict) else {}
    raw_tags = source.get("tags") if isinstance(source, dict) else []
    if isinstance(raw_tags, str):
        values = raw_tags.split(",")
    elif isinstance(raw_tags, list):
        values = raw_tags
    else:
        values = []
    tags: list[str] = []
    for value in values:
        tag = str(value).strip()
        if tag and tag not in tags:
            tags.append(tag)
    return tags


def _fallback_skill_markdown(row: dict[str, Any]) -> bytes:
    skill_name = str(row.get("skill_id") or "")
    description = str(row.get("description") or "")
    return (
        f"---\nname: {skill_name}\ndescription: {description}\n---\n\n"
        f"# {skill_name}\n\n{description}\n"
    ).encode("utf-8")


def _decode_skill_file_content(encoded: str, *, invalid_detail: str) -> bytes:
    try:
        return base64.b64decode(encoded.encode("ascii"), validate=True)
    except Exception as exc:
        raise HTTPException(status_code=409, detail=invalid_detail) from exc


def _project_files(row: dict[str, Any]) -> list[SkillFileProjection]:
    source = row.get("source") if isinstance(row.get("source"), dict) else {}
    raw_files = source.get("files") if isinstance(source, dict) else None
    projected: list[SkillFileProjection] = []
    if isinstance(raw_files, list):
        for item in raw_files:
            if not isinstance(item, dict):
                continue
            relative_path = _safe_file_path(str(item.get("relative_path") or item.get("path") or ""))
            encoded = str(item.get("content_base64") or "")
            if not encoded:
                continue
            content = _decode_skill_file_content(encoded, invalid_detail="skill_file_snapshot_invalid")
            projected.append(
                SkillFileProjection(
                    path=relative_path,
                    content=content,
                    size=int(item.get("size_bytes") or len(content)),
                )
            )

    if not projected:
        content = _fallback_skill_markdown(row)
        projected.append(SkillFileProjection(path="SKILL.md", content=content, size=len(content)))

    raw_overlays = row.get("user_file_overlays")
    overlays = raw_overlays if isinstance(raw_overlays, list) else []
    if overlays:
        by_path = {item.path: item for item in projected}
        for overlay in overlays:
            if not isinstance(overlay, dict):
                continue
            relative_path = _safe_file_path(str(overlay.get("file_path") or ""))
            status = str(overlay.get("status") or "active")
            if status == "deleted":
                by_path.pop(relative_path, None)
                continue
            if status != "active":
                continue
            content = _decode_skill_file_content(
                str(overlay.get("content_base64") or ""),
                invalid_detail="skill_file_overlay_invalid",
            )
            by_path[relative_path] = SkillFileProjection(
                path=relative_path,
                content=content,
                size=int(overlay.get("size_bytes") or len(content)),
            )
        projected = list(by_path.values())
    return sorted(projected, key=lambda item: item.path)


def _file_paths(row: dict[str, Any]) -> list[str]:
    return [item.path for item in _project_files(row)]


def _public_skill_item(row: dict[str, Any]) -> PublicSkillResponse:
    files = _file_paths(row)
    status = str(row.get("status") or "active")
    skill_name = str(row.get("skill_id") or "")
    return PublicSkillResponse(
        skill_name=skill_name,
        description=str(row.get("description") or ""),
        tags=_tags_from_row(row),
        files=files,
        enabled=status == "active",
        file_count=len(files),
        installed_from="marketplace",
        published_marketplace_name=skill_name,
        created_at=row.get("created_at"),
        updated_at=row.get("updated_at") or row.get("created_at"),
        is_published=True,
        marketplace_is_active=status == "active",
    )


def _skill_detail(row: dict[str, Any]) -> PublicSkillDetailResponse:
    item = _public_skill_item(row)
    return PublicSkillDetailResponse(
        files=item.files,
        enabled=item.enabled,
        skill_name=item.skill_name,
        description=item.description,
        tags=item.tags,
        is_published=item.is_published,
        marketplace_is_active=item.marketplace_is_active,
    )


def _marketplace_item(row: dict[str, Any], principal: AuthPrincipal) -> MarketplaceSkillResponse:
    files = _file_paths(row)
    created_by = str(row.get("created_by") or "") or None
    return MarketplaceSkillResponse(
        skill_name=str(row.get("skill_id") or ""),
        description=str(row.get("description") or ""),
        tags=_tags_from_row(row),
        version=str(row.get("version") or ""),
        created_at=row.get("created_at"),
        updated_at=row.get("updated_at") or row.get("created_at"),
        created_by=created_by,
        created_by_username=created_by,
        is_active=str(row.get("status") or "active") == "active",
        is_owner=bool(created_by and created_by == principal.user_id),
        file_count=len(files),
    )


def _filter_rows(
    rows: list[dict[str, Any]],
    *,
    query: str | None = None,
    tags: list[str] | None = None,
) -> list[dict[str, Any]]:
    normalized_query = (query or "").strip().lower()
    normalized_tags = {tag.strip().lower() for tag in tags or [] if tag.strip()}
    filtered = []
    for row in rows:
        row_tags = {tag.lower() for tag in _tags_from_row(row)}
        if normalized_query:
            haystack = " ".join(
                [
                    str(row.get("skill_id") or ""),
                    str(row.get("name") or ""),
                    str(row.get("description") or ""),
                ]
            ).lower()
            if normalized_query not in haystack:
                continue
        if normalized_tags and not normalized_tags.issubset(row_tags):
            continue
        filtered.append(row)
    return filtered


def _available_tags(rows: list[dict[str, Any]]) -> list[str]:
    tags: list[str] = []
    for row in rows:
        for tag in _tags_from_row(row):
            if tag not in tags:
                tags.append(tag)
    return sorted(tags)


async def _catalog_rows(*, tenant_id: str, include_disabled: bool) -> list[dict[str, Any]]:
    async with transaction() as conn:
        return await repositories.list_public_skill_catalog(
            conn,
            tenant_id=tenant_id,
            include_disabled=include_disabled,
        )


def _attach_user_file_overlays(
    rows: list[dict[str, Any]],
    overlays: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    by_skill: dict[str, list[dict[str, Any]]] = {}
    for overlay in overlays:
        skill_id = str(overlay.get("skill_id") or "")
        if skill_id:
            by_skill.setdefault(skill_id, []).append(dict(overlay))
    projected: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["user_file_overlays"] = by_skill.get(str(item.get("skill_id") or ""), [])
        projected.append(item)
    return projected


async def _public_catalog_rows(
    *,
    principal: AuthPrincipal,
    include_disabled: bool,
    include_file_overlay_content: bool = False,
) -> list[dict[str, Any]]:
    async with transaction() as conn:
        rows = await repositories.list_public_skill_catalog(
            conn,
            tenant_id=principal.tenant_id,
            include_disabled=include_disabled,
        )
        overlays = await repositories.list_user_skill_file_overlays(
            conn,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            skill_ids=[str(row.get("skill_id") or "") for row in rows],
            include_content=include_file_overlay_content,
        )
    return _attach_user_file_overlays(rows, overlays)


def _find_row(rows: list[dict[str, Any]], *, skill_name: str) -> dict[str, Any]:
    for row in rows:
        if str(row.get("skill_id") or "") == skill_name:
            return row
    raise HTTPException(status_code=404, detail="skill_not_found")


def _file_response(row: dict[str, Any], *, file_path: str) -> PublicSkillFileResponse:
    safe_path = _safe_file_path(file_path)
    for item in _project_files(row):
        if item.path != safe_path:
            continue
        try:
            return PublicSkillFileResponse(
                content=item.content.decode("utf-8"),
                is_binary=False,
                size=item.size,
            )
        except UnicodeDecodeError:
            return PublicSkillFileResponse(
                content=base64.b64encode(item.content).decode("ascii"),
                is_binary=True,
                mime_type="application/octet-stream",
                size=item.size,
            )
    raise HTTPException(status_code=404, detail="skill_file_not_found")


def _repository_http_exception(exc: Exception) -> HTTPException:
    if isinstance(exc, repositories.RepositoryNotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, repositories.RepositoryConflictError):
        return HTTPException(status_code=409, detail=str(exc))
    return HTTPException(status_code=500, detail="repository_error")


def _request_names(payload: Any) -> list[str]:
    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="skill_batch_request_required")
    raw_names = payload.get("names")
    if not isinstance(raw_names, list) or not raw_names:
        raise HTTPException(status_code=400, detail="skill_names_required")
    names = [_safe_skill_name(str(item)) for item in raw_names]
    if len(names) != len(set(names)):
        raise HTTPException(status_code=400, detail="duplicate_skill_names")
    return names


def _direct_marketplace_write_not_backed(skill_name: str | None = None) -> None:
    if skill_name is not None:
        _safe_skill_name(skill_name)
    raise HTTPException(status_code=409, detail="marketplace_direct_write_contract_not_backed")


def _skill_import_not_backed() -> None:
    raise HTTPException(status_code=409, detail="skill_import_contract_not_backed")


@router.get("/skills/", response_model=PublicSkillsResponse)
async def list_skills(
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    q: str | None = Query(default=None),
    tags: list[str] = Query(default_factory=list),
    principal: AuthPrincipal = Depends(require_principal),
) -> PublicSkillsResponse:
    """List tenant-visible Skills for the authenticated frontend shell."""

    _require_permission(principal, "skill:read")
    rows = await _public_catalog_rows(principal=principal, include_disabled=True)
    filtered = _filter_rows(rows, query=q, tags=tags)
    page = filtered[skip : skip + limit]
    return PublicSkillsResponse(
        skills=[_public_skill_item(row) for row in page],
        total=len(filtered),
        skip=skip,
        limit=limit,
        available_tags=_available_tags(rows),
        effective_permissions=_effective_permissions(principal),
    )


@router.post("/skills/upload/preview")
async def preview_skill_upload(
    principal: AuthPrincipal = Depends(require_principal),
) -> dict[str, object]:
    """Expose the legacy ZIP preview contract as permission-gated but not backed."""

    _require_permission(principal, "skill:write")
    _skill_import_not_backed()


@router.post("/skills/upload")
async def upload_skills(
    principal: AuthPrincipal = Depends(require_principal),
) -> dict[str, object]:
    """Fail closed for ZIP import until durable user skill storage exists."""

    _require_permission(principal, "skill:write")
    _skill_import_not_backed()


@router.post("/skills/batch/delete")
async def batch_delete_skills(
    principal: AuthPrincipal = Depends(require_principal),
    payload: Any = Body(default=None),
) -> dict[str, object]:
    """Disable multiple tenant-visible skills through the existing availability contract."""

    _require_permission(principal, "skill:delete")
    names = _request_names(payload)
    deleted: list[str] = []
    errors: list[dict[str, str]] = []
    async with transaction() as conn:
        for skill_name in names:
            try:
                await repositories.set_public_skill_enabled(
                    conn,
                    tenant_id=principal.tenant_id,
                    skill_id=skill_name,
                    status="disabled",
                )
                await repositories.append_audit_log(
                    conn,
                    tenant_id=principal.tenant_id,
                    user_id=principal.user_id,
                    action="skill.public.batch_delete",
                    target_type="skill",
                    target_id=skill_name,
                    payload_json={"department_id": principal.department_id},
                )
                deleted.append(skill_name)
            except (repositories.RepositoryNotFoundError, repositories.RepositoryConflictError) as exc:
                errors.append({"name": skill_name, "reason": str(exc)})
    return {"deleted": deleted, "errors": errors}


@router.post("/skills/batch/toggle")
async def batch_toggle_skills(
    principal: AuthPrincipal = Depends(require_principal),
    payload: Any = Body(default=None),
) -> dict[str, object]:
    """Toggle multiple tenant-visible skills through the existing availability contract."""

    _require_permission(principal, "skill:write")
    names = _request_names(payload)
    if not isinstance(payload, dict) or not isinstance(payload.get("enabled"), bool):
        raise HTTPException(status_code=400, detail="skill_batch_enabled_required")
    enabled = bool(payload["enabled"])
    status = "active" if enabled else "disabled"
    updated: list[str] = []
    errors: list[dict[str, str]] = []
    async with transaction() as conn:
        for skill_name in names:
            try:
                await repositories.set_public_skill_enabled(
                    conn,
                    tenant_id=principal.tenant_id,
                    skill_id=skill_name,
                    status=status,
                )
                await repositories.append_audit_log(
                    conn,
                    tenant_id=principal.tenant_id,
                    user_id=principal.user_id,
                    action="skill.public.batch_toggle",
                    target_type="skill",
                    target_id=skill_name,
                    payload_json={"enabled": enabled, "department_id": principal.department_id},
                )
                updated.append(skill_name)
            except (repositories.RepositoryNotFoundError, repositories.RepositoryConflictError) as exc:
                errors.append({"name": skill_name, "reason": str(exc)})
    return {"updated": updated, "errors": errors}


@router.get("/skills/{skill_name}", response_model=PublicSkillDetailResponse)
async def get_skill(
    skill_name: str,
    principal: AuthPrincipal = Depends(require_principal),
) -> PublicSkillDetailResponse:
    """Return public skill detail without exposing admin release controls."""

    _require_permission(principal, "skill:read")
    safe_skill_name = _safe_skill_name(skill_name)
    rows = await _public_catalog_rows(principal=principal, include_disabled=True)
    return _skill_detail(_find_row(rows, skill_name=safe_skill_name))


@router.get("/skills/{skill_name}/files/{file_path:path}", response_model=PublicSkillFileResponse)
async def get_skill_file(
    skill_name: str,
    file_path: str,
    principal: AuthPrincipal = Depends(require_principal),
) -> PublicSkillFileResponse:
    """Return a public skill file from the effective version snapshot."""

    _require_permission(principal, "skill:read")
    safe_skill_name = _safe_skill_name(skill_name)
    rows = await _public_catalog_rows(
        principal=principal,
        include_disabled=True,
        include_file_overlay_content=True,
    )
    return _file_response(_find_row(rows, skill_name=safe_skill_name), file_path=file_path)


@router.put("/skills/{skill_name}/files/{file_path:path}", response_model=PublicSkillFileMutationResponse)
async def update_skill_file(
    skill_name: str,
    file_path: str,
    principal: AuthPrincipal = Depends(require_principal),
    payload: Any = Body(default=None),
) -> PublicSkillFileMutationResponse:
    """Persist a tenant/user scoped public Skill file overlay."""

    _require_permission(principal, "skill:write")
    safe_skill_name = _safe_skill_name(skill_name)
    safe_file_path = _safe_file_path(file_path)
    if payload is None:
        raise HTTPException(status_code=400, detail="skill_file_content_required")
    request = _request_model(PublicSkillFileUpdateRequest, payload)
    if request.content is None:
        raise HTTPException(status_code=400, detail="skill_file_content_required")
    content = request.content.encode("utf-8")
    max_bytes = int(get_settings().public_skill_file_overlay_max_bytes)
    if max_bytes > 0 and len(content) > max_bytes:
        raise HTTPException(status_code=413, detail="skill_file_too_large")
    encoded = base64.b64encode(content).decode("ascii")
    async with transaction() as conn:
        rows = await repositories.list_public_skill_catalog(
            conn,
            tenant_id=principal.tenant_id,
            include_disabled=True,
        )
        _find_row(rows, skill_name=safe_skill_name)
        await repositories.ensure_user(
            conn,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            display_name=principal.display_name,
        )
        saved = await repositories.upsert_user_skill_file(
            conn,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            skill_id=safe_skill_name,
            file_path=safe_file_path,
            content_base64=encoded,
            size_bytes=len(content),
        )
        await repositories.append_audit_log(
            conn,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            action="skill.public.file_upsert",
            target_type="skill",
            target_id=safe_skill_name,
            payload_json={
                "file_path": safe_file_path,
                "size_bytes": int(saved.get("size_bytes") or len(content)),
                "department_id": principal.department_id,
            },
        )
    return PublicSkillFileMutationResponse(
        skill_name=safe_skill_name,
        file_path=safe_file_path,
        message="Skill file saved",
        size=int(saved.get("size_bytes") or len(content)),
    )


@router.delete("/skills/{skill_name}/files/{file_path:path}", response_model=PublicSkillFileMutationResponse)
async def delete_skill_file(
    skill_name: str,
    file_path: str,
    principal: AuthPrincipal = Depends(require_principal),
) -> PublicSkillFileMutationResponse:
    """Persist a tenant/user scoped public Skill file deletion tombstone."""

    _require_permission(principal, "skill:delete")
    safe_skill_name = _safe_skill_name(skill_name)
    safe_file_path = _safe_file_path(file_path)
    async with transaction() as conn:
        rows = await repositories.list_public_skill_catalog(
            conn,
            tenant_id=principal.tenant_id,
            include_disabled=True,
        )
        _find_row(rows, skill_name=safe_skill_name)
        await repositories.ensure_user(
            conn,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            display_name=principal.display_name,
        )
        await repositories.delete_user_skill_file(
            conn,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            skill_id=safe_skill_name,
            file_path=safe_file_path,
        )
        await repositories.append_audit_log(
            conn,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            action="skill.public.file_delete",
            target_type="skill",
            target_id=safe_skill_name,
            payload_json={"file_path": safe_file_path, "department_id": principal.department_id},
        )
    return PublicSkillFileMutationResponse(
        skill_name=safe_skill_name,
        file_path=safe_file_path,
        message="Skill file deleted",
        size=None,
    )


@router.patch("/skills/{skill_name}/toggle", response_model=PublicSkillToggleResponse)
async def toggle_skill(
    skill_name: str,
    principal: AuthPrincipal = Depends(require_principal),
    payload: Any = Body(default=None),
) -> PublicSkillToggleResponse:
    """Enable or disable tenant availability for a public skill."""

    _require_permission(principal, "skill:write")
    request = _request_model(PublicSkillToggleRequest, payload or {})
    safe_skill_name = _safe_skill_name(skill_name)
    enabled = True if request.enabled is None else request.enabled
    status = "active" if enabled else "disabled"
    try:
        async with transaction() as conn:
            await repositories.set_public_skill_enabled(
                conn,
                tenant_id=principal.tenant_id,
                skill_id=safe_skill_name,
                status=status,
            )
            await repositories.append_audit_log(
                conn,
                tenant_id=principal.tenant_id,
                user_id=principal.user_id,
                action="skill.public.toggle",
                target_type="skill",
                target_id=safe_skill_name,
                payload_json={"enabled": enabled, "department_id": principal.department_id},
            )
    except (repositories.RepositoryNotFoundError, repositories.RepositoryConflictError) as exc:
        raise _repository_http_exception(exc) from exc
    return PublicSkillToggleResponse(
        skill_name=safe_skill_name,
        enabled=enabled,
        message=f"Skill {'enabled' if enabled else 'disabled'}",
    )


@router.delete("/skills/{skill_name}")
async def delete_skill(
    skill_name: str,
    principal: AuthPrincipal = Depends(require_principal),
) -> dict[str, str]:
    """Map public skill uninstall to tenant availability disablement."""

    _require_permission(principal, "skill:delete")
    safe_skill_name = _safe_skill_name(skill_name)
    try:
        async with transaction() as conn:
            await repositories.set_public_skill_enabled(
                conn,
                tenant_id=principal.tenant_id,
                skill_id=safe_skill_name,
                status="disabled",
            )
            await repositories.append_audit_log(
                conn,
                tenant_id=principal.tenant_id,
                user_id=principal.user_id,
                action="skill.public.delete",
                target_type="skill",
                target_id=safe_skill_name,
                payload_json={"department_id": principal.department_id},
            )
    except (repositories.RepositoryNotFoundError, repositories.RepositoryConflictError) as exc:
        raise _repository_http_exception(exc) from exc
    return {"message": "Skill removed"}


@router.post("/skills/{skill_name}/publish", response_model=MarketplaceSkillResponse)
async def publish_skill(
    skill_name: str,
    principal: AuthPrincipal = Depends(require_principal),
    payload: Any = Body(default=None),
) -> MarketplaceSkillResponse:
    """Expose a user-facing publish contract without invoking admin release APIs."""

    _require_permission(principal, "marketplace:publish")
    request = _request_model(PublishToMarketplaceRequest, payload or {})
    safe_skill_name = _safe_skill_name(skill_name)
    rows = await _catalog_rows(tenant_id=principal.tenant_id, include_disabled=True)
    row = dict(_find_row(rows, skill_name=safe_skill_name))
    if request.description:
        row["description"] = request.description
    if request.version:
        row["version"] = request.version
    if request.tags:
        source = dict(row.get("source") if isinstance(row.get("source"), dict) else {})
        source["tags"] = request.tags
        row["source"] = source
    async with transaction() as conn:
        await repositories.append_audit_log(
            conn,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            action="skill.public.publish_requested",
            target_type="skill",
            target_id=safe_skill_name,
            payload_json={
                "marketplace_skill_name": request.skill_name or safe_skill_name,
                "version": request.version,
                "department_id": principal.department_id,
            },
        )
    return _marketplace_item(row, principal)


@router.post("/github/preview")
async def preview_github_skills(
    principal: AuthPrincipal = Depends(require_principal),
    payload: Any = Body(default=None),
) -> dict[str, object]:
    """Fail closed for GitHub import preview until backend import is product-backed."""

    _require_permission(principal, "skill:write")
    _skill_import_not_backed()


@router.post("/github/install")
async def install_github_skills(
    principal: AuthPrincipal = Depends(require_principal),
    payload: Any = Body(default=None),
) -> dict[str, object]:
    """Fail closed for GitHub import install until backend import is product-backed."""

    _require_permission(principal, "skill:write")
    _skill_import_not_backed()


@router.get("/marketplace/", response_model=list[MarketplaceSkillResponse])
async def list_marketplace(
    tags: str | None = Query(default=None),
    search: str | None = Query(default=None),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    principal: AuthPrincipal = Depends(require_principal),
) -> list[MarketplaceSkillResponse]:
    """List active marketplace skills for authenticated users."""

    _require_permission(principal, "marketplace:read")
    rows = await _catalog_rows(tenant_id=principal.tenant_id, include_disabled=True)
    tag_values = [tag.strip() for tag in (tags or "").split(",") if tag.strip()]
    filtered = _filter_rows(rows, query=search, tags=tag_values)
    return [_marketplace_item(row, principal) for row in filtered[skip : skip + limit]]


@router.post("/marketplace/")
async def create_marketplace_skill(
    principal: AuthPrincipal = Depends(require_principal),
    payload: Any = Body(default=None),
) -> dict[str, object]:
    """Fail closed for direct marketplace lifecycle writes."""

    _require_permission(principal, "marketplace:admin")
    _direct_marketplace_write_not_backed()


@router.get("/marketplace/tags", response_model=MarketplaceTagsResponse)
async def list_marketplace_tags(
    principal: AuthPrincipal = Depends(require_principal),
) -> MarketplaceTagsResponse:
    """Return marketplace tags for frontend filters."""

    _require_permission(principal, "marketplace:read")
    rows = await _catalog_rows(tenant_id=principal.tenant_id, include_disabled=True)
    return MarketplaceTagsResponse(tags=_available_tags(rows))


@router.get("/marketplace/{skill_name}", response_model=MarketplaceSkillResponse)
async def get_marketplace_skill(
    skill_name: str,
    principal: AuthPrincipal = Depends(require_principal),
) -> MarketplaceSkillResponse:
    """Return marketplace skill detail for preview and install prompts."""

    _require_permission(principal, "marketplace:read")
    safe_skill_name = _safe_skill_name(skill_name)
    rows = await _catalog_rows(tenant_id=principal.tenant_id, include_disabled=True)
    return _marketplace_item(_find_row(rows, skill_name=safe_skill_name), principal)


@router.put("/marketplace/{skill_name}")
async def update_marketplace_skill_direct(
    skill_name: str,
    principal: AuthPrincipal = Depends(require_principal),
    payload: Any = Body(default=None),
) -> dict[str, object]:
    """Fail closed for direct marketplace edit writes."""

    _require_permission(principal, "marketplace:admin")
    _direct_marketplace_write_not_backed(skill_name)


@router.patch("/marketplace/{skill_name}/activate")
async def activate_marketplace_skill_direct(
    skill_name: str,
    principal: AuthPrincipal = Depends(require_principal),
    payload: Any = Body(default=None),
) -> dict[str, object]:
    """Fail closed for direct marketplace activation writes."""

    _require_permission(principal, "marketplace:admin")
    _direct_marketplace_write_not_backed(skill_name)


@router.delete("/marketplace/{skill_name}")
async def delete_marketplace_skill_direct(
    skill_name: str,
    principal: AuthPrincipal = Depends(require_principal),
) -> dict[str, object]:
    """Fail closed for direct marketplace delete writes."""

    _require_permission(principal, "marketplace:admin")
    _direct_marketplace_write_not_backed(skill_name)


@router.get("/marketplace/{skill_name}/files", response_model=MarketplaceSkillFilesResponse)
async def list_marketplace_files(
    skill_name: str,
    principal: AuthPrincipal = Depends(require_principal),
) -> MarketplaceSkillFilesResponse:
    """Return marketplace skill file paths for preview."""

    _require_permission(principal, "marketplace:read")
    safe_skill_name = _safe_skill_name(skill_name)
    rows = await _catalog_rows(tenant_id=principal.tenant_id, include_disabled=True)
    return MarketplaceSkillFilesResponse(files=_file_paths(_find_row(rows, skill_name=safe_skill_name)))


@router.get("/marketplace/{skill_name}/files/{file_path:path}", response_model=PublicSkillFileResponse)
async def get_marketplace_file(
    skill_name: str,
    file_path: str,
    principal: AuthPrincipal = Depends(require_principal),
) -> PublicSkillFileResponse:
    """Return marketplace skill file content for preview."""

    _require_permission(principal, "marketplace:read")
    safe_skill_name = _safe_skill_name(skill_name)
    rows = await _catalog_rows(tenant_id=principal.tenant_id, include_disabled=True)
    return _file_response(_find_row(rows, skill_name=safe_skill_name), file_path=file_path)


async def _install_or_update_marketplace_skill(
    *,
    skill_name: str,
    principal: AuthPrincipal,
    message: str,
    audit_action: str,
) -> MarketplaceInstallResponse:
    _require_permission(principal, "marketplace:read")
    _require_permission(principal, "skill:write")
    safe_skill_name = _safe_skill_name(skill_name)
    rows = await _catalog_rows(tenant_id=principal.tenant_id, include_disabled=True)
    row = _find_row(rows, skill_name=safe_skill_name)
    try:
        async with transaction() as conn:
            await repositories.set_public_skill_enabled(
                conn,
                tenant_id=principal.tenant_id,
                skill_id=safe_skill_name,
                status="active",
            )
            await repositories.append_audit_log(
                conn,
                tenant_id=principal.tenant_id,
                user_id=principal.user_id,
                action=audit_action,
                target_type="skill",
                target_id=safe_skill_name,
                payload_json={"department_id": principal.department_id},
            )
    except (repositories.RepositoryNotFoundError, repositories.RepositoryConflictError) as exc:
        raise _repository_http_exception(exc) from exc
    return MarketplaceInstallResponse(
        message=message,
        skill_name=safe_skill_name,
        file_count=len(_file_paths(row)),
    )


@router.post("/marketplace/{skill_name}/install", response_model=MarketplaceInstallResponse)
async def install_marketplace_skill(
    skill_name: str,
    principal: AuthPrincipal = Depends(require_principal),
) -> MarketplaceInstallResponse:
    """Install a marketplace skill by enabling tenant availability."""

    return await _install_or_update_marketplace_skill(
        skill_name=skill_name,
        principal=principal,
        message="Skill installed",
        audit_action="marketplace.skill.installed",
    )


@router.post("/marketplace/{skill_name}/update", response_model=MarketplaceInstallResponse)
async def update_marketplace_skill(
    skill_name: str,
    principal: AuthPrincipal = Depends(require_principal),
) -> MarketplaceInstallResponse:
    """Update an installed skill to the effective marketplace version."""

    return await _install_or_update_marketplace_skill(
        skill_name=skill_name,
        principal=principal,
        message="Skill updated",
        audit_action="marketplace.skill.updated",
    )
