from __future__ import annotations

import base64
import binascii
import hashlib
import io
import re
import stat
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
MAX_SKILL_PACKAGE_FILES = 1024
_SUPPORTED_SKILL_PACKAGE_COMPRESSION = frozenset({zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED})
SKILL_PACKAGE_CONTRACT_SCHEMA_VERSION = "ai-platform.skill-package-contract.v1"
SKILL_DEPENDENCY_EVIDENCE_SCHEMA_VERSION = "ai-platform.skill-dependency-evidence.v1"
SKILL_ADMIN_TRUST_REVIEW_SCHEMA_VERSION = "ai-platform.skill-admin-trust-review.v1"


@dataclass(frozen=True)
class ParsedSkillPackage:
    skill_id: str
    description: str
    content_hash: str
    files: list[dict[str, Any]]
    size_bytes: int


def _safe_zip_member_path(name: str) -> str:
    if not name or "\x00" in name:
        raise ValueError("skill_package_path_escape")
    normalized = name.replace("\\", "/")
    if normalized.startswith("/") or normalized.startswith("//") or re.match(r"^[A-Za-z]:", normalized):
        raise ValueError("skill_package_path_escape")
    normalized = normalized.rstrip("/")
    if not normalized or any(part in {"", ".", ".."} for part in normalized.split("/")):
        raise ValueError("skill_package_path_escape")
    path = PurePosixPath(normalized)
    if path.is_absolute():
        raise ValueError("skill_package_path_escape")
    return path.as_posix()


def _validate_zip_entry(info: zipfile.ZipInfo) -> None:
    if info.flag_bits & 0x1:
        raise ValueError("skill_package_encrypted_entry")
    if info.compress_type not in _SUPPORTED_SKILL_PACKAGE_COMPRESSION:
        raise ValueError("skill_package_unsupported_compression")
    if info.create_system != 3:
        return
    mode = (info.external_attr >> 16) & 0xFFFF
    file_type = stat.S_IFMT(mode)
    if file_type == 0:
        return
    if info.is_dir() and stat.S_ISDIR(mode):
        return
    if not info.is_dir() and stat.S_ISREG(mode):
        return
    raise ValueError("skill_package_non_regular_entry")


def _content_hash(files: list[tuple[str, bytes]]) -> str:
    digest = hashlib.sha256()
    for relative_path, content in files:
        path_bytes = relative_path.encode("utf-8")
        digest.update(len(path_bytes).to_bytes(8, "big"))
        digest.update(path_bytes)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def _normalized_package_files(files: list[tuple[str, bytes]]) -> list[tuple[str, bytes]]:
    """Accept either a package root or one conventional wrapped Skill directory."""

    paths = [PurePosixPath(relative_path) for relative_path, _ in files]
    skill_paths = [path for path in paths if path.name.lower() == "skill.md"]
    if len(skill_paths) > 1:
        raise ValueError("skill_package_multiple_skills_not_supported")
    if any(path.as_posix() == "SKILL.md" for path in skill_paths):
        return files

    skill_roots = {
        path.parent
        for path in skill_paths
        if path.parent != PurePosixPath(".")
    }
    if not skill_roots:
        return files

    root = next(iter(skill_roots))
    normalized: list[tuple[str, bytes]] = []
    seen: set[str] = set()
    for path, (_, data) in zip(paths, files, strict=True):
        try:
            relative = path.relative_to(root).as_posix()
        except ValueError as exc:
            raise ValueError("skill_package_mixed_root") from exc
        normalized_key = relative.casefold()
        if not relative or normalized_key in seen:
            raise ValueError("skill_package_duplicate_path")
        seen.add(normalized_key)
        normalized.append((relative, data))
    return normalized


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


def validate_skill_package_snapshot(
    files: object,
    *,
    skill_id: str,
    content_hash: str,
) -> dict[str, int]:
    """Validate immutable uploaded file bytes without requiring platform metadata."""

    if not isinstance(files, list) or not files or len(files) > MAX_SKILL_PACKAGE_FILES:
        raise ValueError("skill_package_snapshot_files_invalid")
    decoded: list[tuple[str, bytes]] = []
    seen: set[str] = set()
    total_bytes = 0
    for item in files:
        if not isinstance(item, dict):
            raise ValueError("skill_package_snapshot_files_invalid")
        relative_path = _safe_zip_member_path(str(item.get("relative_path") or ""))
        normalized_key = relative_path.casefold()
        if normalized_key in seen:
            raise ValueError("skill_package_duplicate_path")
        seen.add(normalized_key)
        encoded = item.get("content_base64")
        if not isinstance(encoded, str):
            raise ValueError("skill_package_snapshot_files_invalid")
        try:
            content = base64.b64decode(encoded.encode("ascii"), validate=True)
        except (binascii.Error, UnicodeEncodeError, ValueError) as exc:
            raise ValueError("skill_package_snapshot_files_invalid") from exc
        try:
            declared_size = int(item.get("size_bytes"))
        except (TypeError, ValueError) as exc:
            raise ValueError("skill_package_snapshot_files_invalid") from exc
        if declared_size != len(content) or len(content) > MAX_SKILL_PACKAGE_FILE_BYTES:
            raise ValueError("skill_package_snapshot_files_invalid")
        total_bytes += len(content)
        if total_bytes > MAX_SKILL_PACKAGE_TOTAL_BYTES:
            raise ValueError("skill_package_snapshot_files_invalid")
        decoded.append((relative_path, content))

    sorted_files = sorted(decoded, key=lambda item: item[0])
    if _content_hash(sorted_files) != content_hash:
        raise ValueError("skill_package_snapshot_hash_mismatch")
    by_path = {relative_path: content for relative_path, content in sorted_files}
    skill_md = by_path.get("SKILL.md")
    if skill_md is None:
        raise ValueError("skill_package_skill_md_required")
    try:
        metadata = parse_skill_markdown_front_matter(skill_md.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise ValueError("skill_package_invalid_utf8") from exc
    if metadata.get("name") != skill_id or not metadata.get("description"):
        raise ValueError("skill_package_manifest_mismatch")
    return {"file_count": len(sorted_files), "size_bytes": total_bytes}


def build_uploaded_skill_admin_trust_review(skill_version: dict[str, Any]) -> dict[str, Any]:
    """Build the server-owned trust verdict used by the explicit admin review action."""

    skill_id = str(skill_version.get("skill_id") or "")
    version = str(skill_version.get("version") or "")
    content_hash = str(skill_version.get("content_hash") or "")
    source = skill_version.get("source") if isinstance(skill_version.get("source"), dict) else {}
    blockers: list[str] = []
    summary: dict[str, int] = {"file_count": 0, "size_bytes": 0}
    contract: dict[str, Any] = {}
    try:
        assert_safe_id(skill_id, "skill_id")
        if not version or version != content_hash or source.get("kind") != "uploaded":
            raise ValueError("skill_package_version_identity_invalid")
        raw_contract = source.get("package_contract")
        if not isinstance(raw_contract, dict):
            raise ValueError("skill_package_contract_required")
        contract = validate_skill_package_contract(
            raw_contract,
            skill_id=skill_id,
            content_hash=content_hash,
        )
        summary = validate_skill_package_snapshot(
            source.get("files"),
            skill_id=skill_id,
            content_hash=content_hash,
        )
        if (
            str(source.get("storage_key") or "") != str(contract.get("storage_key") or "")
            or str(source.get("package_sha256") or "") != str(contract.get("package_sha256") or "")
            or summary["file_count"] != int(contract.get("file_count") or -1)
            or summary["size_bytes"] != int(contract.get("size_bytes") or -1)
            or not str(contract.get("uploaded_by") or "")
        ):
            raise ValueError("skill_package_contract_snapshot_mismatch")
    except (TypeError, ValueError):
        blockers.append("uploaded_skill_package_trust_not_verified")
    return {
        "schema_version": SKILL_ADMIN_TRUST_REVIEW_SCHEMA_VERSION,
        "status": "blocked" if blockers else "passed",
        "skill_id": skill_id,
        "version": version,
        "content_hash": content_hash,
        "trust_basis": "admin_reviewed_immutable_upload",
        "package_contract_valid": not blockers,
        "file_count": summary["file_count"],
        "size_bytes": summary["size_bytes"],
        "blockers": blockers,
    }


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
        entries = archive.infolist()
        if len(entries) > MAX_SKILL_PACKAGE_FILES:
            raise ValueError("skill_package_too_many_files")
        for info in entries:
            _validate_zip_entry(info)
            relative_path = _safe_zip_member_path(info.filename)
            if info.is_dir():
                continue
            normalized_key = relative_path.casefold()
            if normalized_key in seen:
                raise ValueError("skill_package_duplicate_path")
            seen.add(normalized_key)
            if info.file_size > MAX_SKILL_PACKAGE_FILE_BYTES:
                raise ValueError("skill_package_file_too_large")
            if total_bytes + info.file_size > MAX_SKILL_PACKAGE_TOTAL_BYTES:
                raise ValueError("skill_package_too_large")
            try:
                data = archive.read(info)
            except (RuntimeError, OSError, NotImplementedError, zipfile.BadZipFile) as exc:
                raise ValueError("skill_package_invalid_zip") from exc
            if len(data) != info.file_size:
                raise ValueError("skill_package_invalid_zip")
            total_bytes += len(data)
            if len(data) > MAX_SKILL_PACKAGE_FILE_BYTES:
                raise ValueError("skill_package_file_too_large")
            if total_bytes > MAX_SKILL_PACKAGE_TOTAL_BYTES:
                raise ValueError("skill_package_too_large")
            files.append((relative_path, data))

    files = _normalized_package_files(files)
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
