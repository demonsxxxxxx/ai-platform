"""Pure marketplace catalog projections and source-snapshot normalization."""

from __future__ import annotations

import base64
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

from fastapi import HTTPException

from app.models import (
    MarketplaceSkillResponse,
    PublicSkillDetailResponse,
    PublicSkillResponse,
)


@dataclass(frozen=True)
class SkillFileProjection:
    """Decoded file projected from a skill version source snapshot."""

    path: str
    content: bytes
    size: int


def normalize_skill_file_path(file_path: str) -> str:
    """Return a normalized relative skill file path or preserve the route error contract."""

    normalized = file_path.replace("\\", "/").strip("/")
    if not normalized:
        raise HTTPException(status_code=400, detail="skill_file_path_required")
    path = PurePosixPath(normalized)
    if path.is_absolute() or ".." in path.parts:
        raise HTTPException(status_code=400, detail="skill_file_path_escape")
    return path.as_posix()


def marketplace_tags(row: dict[str, Any]) -> list[str]:
    """Normalize ordered unique marketplace tags from a catalog row."""

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


def project_skill_files(row: dict[str, Any]) -> list[SkillFileProjection]:
    """Project source files and active user overlays into sorted readable files."""

    source = row.get("source") if isinstance(row.get("source"), dict) else {}
    raw_files = source.get("files") if isinstance(source, dict) else None
    projected: list[SkillFileProjection] = []
    if isinstance(raw_files, list):
        for item in raw_files:
            if not isinstance(item, dict):
                continue
            relative_path = normalize_skill_file_path(
                str(item.get("relative_path") or item.get("path") or "")
            )
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
            relative_path = normalize_skill_file_path(str(overlay.get("file_path") or ""))
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


def project_public_skill(
    row: dict[str, Any],
    *,
    include_file_metadata: bool,
) -> PublicSkillResponse:
    """Project a public skill catalog item without source files by default."""

    files = project_skill_file_paths(row) if include_file_metadata else []
    status = str(row.get("status") or "active")
    skill_name = str(row.get("skill_id") or "")
    input_modes = [str(mode) for mode in row.get("input_modes") or []]
    return PublicSkillResponse(
        skill_name=skill_name,
        expected_version=str(row.get("expected_version") or ""),
        input_modes=input_modes,
        requires_file="docx" in input_modes,
        description=str(row.get("description") or ""),
        tags=marketplace_tags(row),
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


def project_public_skill_detail(row: dict[str, Any]) -> PublicSkillDetailResponse:
    """Project the public detail view with its authorized file metadata."""

    item = project_public_skill(row, include_file_metadata=True)
    return PublicSkillDetailResponse(
        files=item.files,
        enabled=item.enabled,
        skill_name=item.skill_name,
        expected_version=item.expected_version,
        input_modes=item.input_modes,
        requires_file=item.requires_file,
        description=item.description,
        tags=item.tags,
        is_published=item.is_published,
        marketplace_is_active=item.marketplace_is_active,
    )


def project_marketplace_skill(
    row: dict[str, Any],
    *,
    include_file_metadata: bool,
    viewer_user_id: str,
) -> MarketplaceSkillResponse:
    """Project a marketplace item after the route has determined viewer access."""

    files = project_skill_file_paths(row) if include_file_metadata else []
    created_by = str(row.get("created_by") or "") or None
    return MarketplaceSkillResponse(
        skill_name=str(row.get("skill_id") or ""),
        description=str(row.get("description") or ""),
        tags=marketplace_tags(row),
        version=str(row.get("version") or ""),
        created_at=row.get("created_at"),
        updated_at=row.get("updated_at") or row.get("created_at"),
        created_by=created_by,
        created_by_username=created_by,
        is_active=str(row.get("status") or "active") == "active",
        is_owner=bool(created_by and created_by == viewer_user_id),
        file_count=len(files),
    )


def filter_marketplace_rows(
    rows: list[dict[str, Any]],
    *,
    query: str | None = None,
    tags: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Filter rows by a case-insensitive catalog query and required tags."""

    normalized_query = (query or "").strip().lower()
    normalized_tags = {tag.strip().lower() for tag in tags or [] if tag.strip()}
    filtered = []
    for row in rows:
        row_tags = {tag.lower() for tag in marketplace_tags(row)}
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


def available_marketplace_tags(rows: list[dict[str, Any]]) -> list[str]:
    """Return all unique catalog tags in the existing lexical order."""

    tags: list[str] = []
    for row in rows:
        for tag in marketplace_tags(row):
            if tag not in tags:
                tags.append(tag)
    return sorted(tags)


def attach_user_file_overlays(
    rows: list[dict[str, Any]],
    overlays: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return copied catalog rows with only their matching user overlays attached."""

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


def project_skill_file_paths(row: dict[str, Any]) -> list[str]:
    """Return the projected skill file paths in their stable lexical order."""

    return [item.path for item in project_skill_files(row)]


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
