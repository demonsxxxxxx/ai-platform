from __future__ import annotations

import base64
import hashlib
import io
import zipfile
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

from app.skills.pinning import MAX_SKILL_SNAPSHOT_FILE_BYTES, MAX_SKILL_SNAPSHOT_TOTAL_BYTES
from app.skills.registry import parse_skill_markdown_front_matter

MAX_SKILL_PACKAGE_FILE_BYTES = MAX_SKILL_SNAPSHOT_FILE_BYTES
MAX_SKILL_PACKAGE_TOTAL_BYTES = MAX_SKILL_SNAPSHOT_TOTAL_BYTES


@dataclass(frozen=True)
class ParsedSkillPackage:
    skill_id: str
    description: str
    content_hash: str
    files: list[dict[str, Any]]
    size_bytes: int


def _safe_zip_member_path(name: str) -> str:
    normalized = name.replace("\\", "/").strip("/")
    if not normalized:
        raise ValueError("skill_package_path_escape")
    path = PurePosixPath(normalized)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("skill_package_path_escape")
    return path.as_posix()


def _content_hash(files: list[tuple[str, bytes]]) -> str:
    digest = hashlib.sha256()
    for relative_path, content in files:
        path_bytes = relative_path.encode("utf-8")
        digest.update(len(path_bytes).to_bytes(8, "big"))
        digest.update(path_bytes)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def parse_skill_package_zip(content: bytes, *, expected_skill_id: str) -> ParsedSkillPackage:
    if not content:
        raise ValueError("skill_package_empty")
    if len(content) > MAX_SKILL_PACKAGE_TOTAL_BYTES:
        raise ValueError("skill_package_too_large")
    try:
        archive = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile as exc:
        raise ValueError("skill_package_invalid_zip") from exc

    seen: set[str] = set()
    files: list[tuple[str, bytes]] = []
    total_bytes = 0
    with archive:
        for info in archive.infolist():
            relative_path = _safe_zip_member_path(info.filename)
            if info.is_dir():
                continue
            if relative_path in seen:
                raise ValueError("skill_package_duplicate_path")
            seen.add(relative_path)
            if info.file_size > MAX_SKILL_PACKAGE_FILE_BYTES:
                raise ValueError("skill_package_file_too_large")
            if total_bytes + info.file_size > MAX_SKILL_PACKAGE_TOTAL_BYTES:
                raise ValueError("skill_package_too_large")
            data = archive.read(info)
            if len(data) != info.file_size:
                raise ValueError("skill_package_invalid_zip")
            total_bytes += len(data)
            if len(data) > MAX_SKILL_PACKAGE_FILE_BYTES:
                raise ValueError("skill_package_file_too_large")
            if total_bytes > MAX_SKILL_PACKAGE_TOTAL_BYTES:
                raise ValueError("skill_package_too_large")
            files.append((relative_path, data))

    by_path = {relative_path: data for relative_path, data in files}
    skill_md = by_path.get("SKILL.md")
    if skill_md is None:
        raise ValueError("skill_package_skill_md_required")
    try:
        skill_md_text = skill_md.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("skill_package_invalid_utf8") from exc
    metadata = parse_skill_markdown_front_matter(skill_md_text)
    skill_id = metadata.get("name") or ""
    if skill_id != expected_skill_id:
        raise ValueError("skill_package_name_mismatch")
    description = metadata.get("description") or ""
    if not description:
        raise ValueError("skill_package_description_required")

    sorted_files = sorted(files, key=lambda item: item[0])
    return ParsedSkillPackage(
        skill_id=skill_id,
        description=description,
        content_hash=_content_hash(sorted_files),
        files=[
            {
                "relative_path": relative_path,
                "content_base64": base64.b64encode(data).decode("ascii"),
                "size_bytes": len(data),
            }
            for relative_path, data in sorted_files
        ],
        size_bytes=total_bytes,
    )
