from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import urlparse

import httpx

from app.skills.packages import MAX_SKILL_PACKAGE_TOTAL_BYTES, ParsedSkillPackage, parse_skill_package_zip

GITHUB_IMPORT_TIMEOUT_SECONDS = 30.0
_GITHUB_REPO_COMPONENT_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
_GITHUB_BRANCH_RE = re.compile(r"^[A-Za-z0-9._/-]+$")


class GitHubImportError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


@dataclass(frozen=True)
class GitHubImportPackage:
    """Parsed public Skill package discovered in a GitHub archive."""

    path: str
    package: ParsedSkillPackage


def _safe_archive_path(name: str) -> str:
    normalized = name.replace("\\", "/").strip("/")
    if not normalized:
        raise GitHubImportError(400, "github_import_archive_path_escape")
    path = PurePosixPath(normalized)
    if path.is_absolute() or ".." in path.parts:
        raise GitHubImportError(400, "github_import_archive_path_escape")
    return path.as_posix()


def _safe_branch_path(branch: str) -> str:
    try:
        normalized = _safe_archive_path(branch)
    except GitHubImportError as exc:
        raise GitHubImportError(400, "github_import_branch_unsupported") from exc
    parts = PurePosixPath(normalized).parts
    if (
        normalized != branch
        or not _GITHUB_BRANCH_RE.fullmatch(normalized)
        or any(part in {".", ".."} for part in parts)
    ):
        raise GitHubImportError(400, "github_import_branch_unsupported")
    return normalized


def github_repo_archive_url(repo_url: str, branch: str) -> tuple[str, str, str]:
    parsed = urlparse(repo_url.strip())
    if parsed.scheme != "https" or parsed.netloc.lower() != "github.com":
        raise GitHubImportError(400, "github_import_repo_url_unsupported")
    parts = [part for part in parsed.path.strip("/").split("/") if part]
    if len(parts) != 2:
        raise GitHubImportError(400, "github_import_repo_url_unsupported")
    owner, repo = parts
    if not _GITHUB_REPO_COMPONENT_RE.fullmatch(owner) or not _GITHUB_REPO_COMPONENT_RE.fullmatch(repo):
        raise GitHubImportError(400, "github_import_repo_url_unsupported")
    safe_branch = _safe_branch_path(branch.strip() or "main")
    normalized_repo_url = f"https://github.com/{owner}/{repo}"
    archive_url = f"{normalized_repo_url}/archive/refs/heads/{safe_branch}.zip"
    return normalized_repo_url, archive_url, safe_branch


async def download_github_archive(url: str) -> bytes:
    try:
        async with httpx.AsyncClient(timeout=GITHUB_IMPORT_TIMEOUT_SECONDS, follow_redirects=True) as client:
            async with client.stream("GET", url) as response:
                response.raise_for_status()
                chunks: list[bytes] = []
                total_bytes = 0
                async for chunk in response.aiter_bytes():
                    total_bytes += len(chunk)
                    if total_bytes > MAX_SKILL_PACKAGE_TOTAL_BYTES:
                        raise GitHubImportError(400, "skill_package_too_large")
                    chunks.append(chunk)
                return b"".join(chunks)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            raise GitHubImportError(404, "github_import_archive_not_found") from exc
        raise GitHubImportError(502, "github_import_archive_unavailable") from exc
    except httpx.HTTPError as exc:
        raise GitHubImportError(502, "github_import_archive_unavailable") from exc


def _github_archive_member_path(name: str) -> str | None:
    path = _safe_archive_path(name)
    parts = PurePosixPath(path).parts
    if len(parts) < 2:
        return None
    return PurePosixPath(*parts[1:]).as_posix()


def discover_github_skill_packages(content: bytes) -> list[GitHubImportPackage]:
    if not content:
        raise GitHubImportError(400, "skill_package_empty")
    if len(content) > MAX_SKILL_PACKAGE_TOTAL_BYTES:
        raise GitHubImportError(400, "skill_package_too_large")
    try:
        archive = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile as exc:
        raise GitHubImportError(400, "skill_package_invalid_zip") from exc

    archive_files: dict[str, bytes] = {}
    total_bytes = 0
    with archive:
        for info in archive.infolist():
            stripped_path = _github_archive_member_path(info.filename)
            if not stripped_path or info.is_dir():
                continue
            if info.file_size > MAX_SKILL_PACKAGE_TOTAL_BYTES:
                raise GitHubImportError(400, "skill_package_file_too_large")
            total_bytes += info.file_size
            if total_bytes > MAX_SKILL_PACKAGE_TOTAL_BYTES:
                raise GitHubImportError(400, "skill_package_too_large")
            data = archive.read(info)
            if len(data) != info.file_size:
                raise GitHubImportError(400, "skill_package_invalid_zip")
            archive_files[stripped_path] = data

    packages: list[GitHubImportPackage] = []
    seen_skill_ids: set[str] = set()
    roots = _skill_roots(archive_files)
    for root in roots:
        files = _files_under_root(archive_files, root)
        try:
            package = parse_skill_package_zip(_zip_package_from_files(files))
        except ValueError:
            continue
        if package.skill_id in seen_skill_ids:
            raise GitHubImportError(400, "github_import_duplicate_skill_id")
        seen_skill_ids.add(package.skill_id)
        packages.append(GitHubImportPackage(path="" if root == "." else root, package=package))
    if not packages:
        raise GitHubImportError(400, "github_import_no_skill_packages")
    return packages


def _skill_roots(archive_files: dict[str, bytes]) -> list[str]:
    roots: list[str] = []
    for stripped_path in archive_files:
        parts = PurePosixPath(stripped_path).parts
        for index, part in enumerate(parts):
            if part == "SKILL.md":
                roots.append(PurePosixPath(*parts[:index]).as_posix() if index > 0 else ".")
                break
    return sorted(set(roots))


def _files_under_root(archive_files: dict[str, bytes], root: str) -> dict[str, bytes]:
    if root == ".":
        return dict(archive_files)
    root_parts = PurePosixPath(root).parts
    files: dict[str, bytes] = {}
    for stripped_path, data in archive_files.items():
        path_parts = PurePosixPath(stripped_path).parts
        if path_parts[: len(root_parts)] != root_parts:
            continue
        relative_parts = path_parts[len(root_parts) :]
        if relative_parts:
            files[PurePosixPath(*relative_parts).as_posix()] = data
    return files


def _zip_package_from_files(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as package:
        for relative_path, content in sorted(files.items()):
            package.writestr(relative_path, content)
    return buffer.getvalue()
