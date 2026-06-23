from __future__ import annotations

import base64
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
_GITHUB_API_ACCEPT = "application/vnd.github+json"


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
    archive_url = f"https://codeload.github.com/{owner}/{repo}/zip/refs/heads/{safe_branch}"
    return normalized_repo_url, archive_url, safe_branch


def github_repo_components(repo_url: str, branch: str) -> tuple[str, str, str, str]:
    normalized_repo_url, _, safe_branch = github_repo_archive_url(repo_url, branch)
    parsed = urlparse(normalized_repo_url)
    owner, repo = parsed.path.strip("/").split("/", maxsplit=1)
    return normalized_repo_url, owner, repo, safe_branch


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


async def download_github_archive_from_api(repo_url: str, branch: str) -> bytes:
    """Download public Skill package files through GitHub's REST API fallback."""

    normalized_repo_url, owner, repo, safe_branch = github_repo_components(repo_url, branch)
    del normalized_repo_url
    api_base = f"https://api.github.com/repos/{owner}/{repo}"
    headers = {"Accept": _GITHUB_API_ACCEPT, "X-GitHub-Api-Version": "2022-11-28"}
    try:
        async with httpx.AsyncClient(timeout=GITHUB_IMPORT_TIMEOUT_SECONDS, follow_redirects=True) as client:
            ref_response = await client.get(f"{api_base}/git/ref/heads/{safe_branch}", headers=headers)
            ref_response.raise_for_status()
            ref_json = ref_response.json()
            commit_sha = _github_json_path(ref_json, "object", "sha")
            tree_response = await client.get(
                f"{api_base}/git/trees/{commit_sha}",
                headers=headers,
                params={"recursive": "1"},
            )
            tree_response.raise_for_status()
            tree_json = tree_response.json()
            if bool(tree_json.get("truncated")):
                raise GitHubImportError(400, "github_import_tree_truncated")
            return await _download_github_tree_files(client, api_base, headers, tree_json)
    except GitHubImportError:
        raise
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            raise GitHubImportError(404, "github_import_archive_not_found") from exc
        raise GitHubImportError(502, "github_import_archive_unavailable") from exc
    except (ValueError, httpx.HTTPError) as exc:
        raise GitHubImportError(502, "github_import_archive_unavailable") from exc


def _github_json_path(payload: dict[str, Any], *path: str) -> str:
    current: Any = payload
    for key in path:
        if not isinstance(current, dict):
            raise GitHubImportError(502, "github_import_archive_unavailable")
        current = current.get(key)
    if not isinstance(current, str) or not current:
        raise GitHubImportError(502, "github_import_archive_unavailable")
    return current


async def _download_github_tree_files(
    client: httpx.AsyncClient,
    api_base: str,
    headers: dict[str, str],
    tree_json: dict[str, Any],
) -> bytes:
    tree = tree_json.get("tree")
    if not isinstance(tree, list):
        raise GitHubImportError(502, "github_import_archive_unavailable")
    package_roots = _github_tree_skill_roots(tree)
    buffer = io.BytesIO()
    total_bytes = 0
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for item in tree:
            if not isinstance(item, dict) or item.get("type") != "blob":
                continue
            path = _safe_archive_path(str(item.get("path") or ""))
            if not _github_tree_path_in_roots(path, package_roots):
                continue
            size = int(item.get("size") or 0)
            if size > MAX_SKILL_PACKAGE_TOTAL_BYTES:
                raise GitHubImportError(400, "skill_package_file_too_large")
            total_bytes += size
            if total_bytes > MAX_SKILL_PACKAGE_TOTAL_BYTES:
                raise GitHubImportError(400, "skill_package_too_large")
            blob_sha = str(item.get("sha") or "")
            if not blob_sha:
                raise GitHubImportError(502, "github_import_archive_unavailable")
            blob_response = await client.get(f"{api_base}/git/blobs/{blob_sha}", headers=headers)
            blob_response.raise_for_status()
            archive.writestr(f"repo-api/{path}", _decode_github_blob(blob_response.json()))
    return buffer.getvalue()


def _github_tree_skill_roots(tree: list[Any]) -> set[str]:
    roots: set[str] = set()
    for item in tree:
        if not isinstance(item, dict) or item.get("type") != "blob":
            continue
        path = _safe_archive_path(str(item.get("path") or ""))
        parts = PurePosixPath(path).parts
        if parts and parts[-1] == "SKILL.md":
            roots.add(PurePosixPath(*parts[:-1]).as_posix() if len(parts) > 1 else ".")
    return roots


def _github_tree_path_in_roots(path: str, roots: set[str]) -> bool:
    if not roots:
        return False
    if "." in roots:
        return True
    path_parts = PurePosixPath(path).parts
    for root in roots:
        root_parts = PurePosixPath(root).parts
        if path_parts[: len(root_parts)] == root_parts:
            return True
    return False


def _decode_github_blob(payload: dict[str, Any]) -> bytes:
    encoding = payload.get("encoding")
    content = payload.get("content")
    if encoding != "base64" or not isinstance(content, str):
        raise GitHubImportError(502, "github_import_archive_unavailable")
    normalized_content = "".join(content.split())
    try:
        return base64.b64decode(normalized_content, validate=True)
    except ValueError as exc:
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
