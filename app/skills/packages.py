from __future__ import annotations

import base64
import hashlib
import io
import zipfile
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

from app.skills.pinning import MAX_SKILL_SNAPSHOT_FILE_BYTES, MAX_SKILL_SNAPSHOT_TOTAL_BYTES
from app.skills.release_readiness import (
    _LICENSE_FILE_NAMES,
    _RELEASE_EVIDENCE_CATEGORIES,
    _VULNERABILITY_EVIDENCE_NAMES,
)
from app.skills.registry import parse_skill_markdown_front_matter
from app.validation import assert_safe_id

MAX_SKILL_PACKAGE_FILE_BYTES = MAX_SKILL_SNAPSHOT_FILE_BYTES
MAX_SKILL_PACKAGE_TOTAL_BYTES = MAX_SKILL_SNAPSHOT_TOTAL_BYTES
SKILL_PACKAGE_CONTRACT_SCHEMA_VERSION = "ai-platform.skill-package-contract.v1"
SKILL_DEPENDENCY_EVIDENCE_SCHEMA_VERSION = "ai-platform.skill-dependency-evidence.v1"


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


def _matching_relative_paths(relative_paths: list[str], file_names: set[str]) -> list[str]:
    return sorted(path for path in relative_paths if PurePosixPath(path).name.lower() in file_names)


def _safe_storage_key(storage_key: object) -> str:
    value = str(storage_key or "").replace("\\", "/").strip()
    if not value:
        raise ValueError("skill_package_contract_storage_key_invalid")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("skill_package_contract_storage_key_invalid")
    return path.as_posix()


def _evidence_files(parsed: ParsedSkillPackage) -> dict[str, list[str]]:
    relative_paths = [str(item.get("relative_path") or "") for item in parsed.files if isinstance(item, dict)]
    return {
        "sbom_or_signed_package": _matching_relative_paths(
            relative_paths,
            _RELEASE_EVIDENCE_CATEGORIES["sbom_or_signed_package"],
        ),
        "license_policy": _matching_relative_paths(relative_paths, _LICENSE_FILE_NAMES),
        "vulnerability_scan": _matching_relative_paths(relative_paths, _VULNERABILITY_EVIDENCE_NAMES),
    }


def build_skill_package_contract(
    parsed: ParsedSkillPackage,
    package_sha256: str,
    storage_key: str,
    *,
    uploaded_by: str,
) -> dict[str, Any]:
    """Build the immutable upload package contract persisted with a version."""

    return {
        "schema_version": SKILL_PACKAGE_CONTRACT_SCHEMA_VERSION,
        "skill_id": parsed.skill_id,
        "version": parsed.content_hash,
        "content_hash": parsed.content_hash,
        "package_sha256": str(package_sha256 or "").strip(),
        "storage_key": _safe_storage_key(storage_key),
        "uploaded_by": str(uploaded_by or ""),
        "file_count": len(parsed.files),
        "size_bytes": parsed.size_bytes,
        "evidence_files": _evidence_files(parsed),
    }


def validate_skill_package_contract(
    contract: dict[str, Any],
    *,
    skill_id: str,
    content_hash: str,
) -> dict[str, Any]:
    if contract.get("schema_version") != SKILL_PACKAGE_CONTRACT_SCHEMA_VERSION:
        raise ValueError("skill_package_contract_schema_invalid")
    if str(contract.get("skill_id") or "") != skill_id:
        raise ValueError("skill_package_contract_skill_mismatch")
    if str(contract.get("version") or "") != content_hash:
        raise ValueError("skill_package_contract_hash_mismatch")
    if str(contract.get("content_hash") or "") != content_hash:
        raise ValueError("skill_package_contract_hash_mismatch")
    if not str(contract.get("package_sha256") or "").strip():
        raise ValueError("skill_package_contract_package_sha256_required")
    contract = dict(contract)
    contract["storage_key"] = _safe_storage_key(contract.get("storage_key"))
    evidence_files = contract.get("evidence_files")
    if not isinstance(evidence_files, dict):
        raise ValueError("skill_package_contract_evidence_files_invalid")
    for key in ("sbom_or_signed_package", "license_policy", "vulnerability_scan"):
        values = evidence_files.get(key)
        if not isinstance(values, list) or any(not isinstance(item, str) or not item for item in values):
            raise ValueError("skill_package_contract_evidence_files_invalid")
    return contract


def build_skill_dependency_evidence(
    *,
    dependency_ids: list[str],
    dependency_manifests: list[dict[str, Any]],
    package_contract: dict[str, Any],
) -> dict[str, Any]:
    evidence_files = package_contract.get("evidence_files") if isinstance(package_contract.get("evidence_files"), dict) else {}
    return {
        "schema_version": SKILL_DEPENDENCY_EVIDENCE_SCHEMA_VERSION,
        "status": "review_required" if dependency_ids else "not_required",
        "dependency_count": len(dependency_ids),
        "dependency_ids": list(dependency_ids),
        "manifest_snapshot_present": bool(dependency_manifests),
        "package_evidence_present": any(bool(evidence_files.get(key)) for key in ("sbom_or_signed_package", "license_policy", "vulnerability_scan")),
        "evidence_files": {
            "sbom_or_signed_package": list(evidence_files.get("sbom_or_signed_package") or []),
            "license_policy": list(evidence_files.get("license_policy") or []),
            "vulnerability_scan": list(evidence_files.get("vulnerability_scan") or []),
        },
    }


def parse_skill_package_zip(content: bytes, *, expected_skill_id: str | None = None) -> ParsedSkillPackage:
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
    try:
        assert_safe_id(skill_id, "skill_id")
    except ValueError as exc:
        raise ValueError(str(exc)) from exc
    if expected_skill_id is not None and skill_id != expected_skill_id:
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
