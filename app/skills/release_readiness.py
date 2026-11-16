from __future__ import annotations

import base64

import binascii

import json




from pathlib import Path

import re

from typing import Any




SKILL_VERSION_RELEASE_REVIEW_SCHEMA_VERSION = "ai-platform.skill-version-release-review.v1"

_EXTERNAL_EVIDENCE_PREFIX = "external-release-evidence"

_PACKAGE_METADATA_FILES = {"_meta.json", ".clawhub/origin.json"}

_REQUIREMENTS_FILES = {
    "requirements.txt",
    "requirements-dev.txt",
    "pyproject.toml",
    "poetry.lock",
    "uv.lock",
    "package.json",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
}

_SBOM_FILE_NAMES = {
    "sbom.json",
    "sbom.spdx.json",
    "bom.json",
    "cyclonedx.json",
    "cyclonedx-sbom.json",
}

_LICENSE_FILE_NAMES = {
    "license",
    "license.md",
    "license.txt",
    "licence",
    "licence.md",
    "licence.txt",
    "copying",
    "notice",
    "third-party-notices.txt",
}

_VULNERABILITY_EVIDENCE_NAMES = {
    "pip-audit.json",
    "safety-report.json",
    "npm-audit.json",
    "pnpm-audit.json",
    "osv-scanner.json",
    "vulnerability-report.json",
}

_SIGNED_PACKAGE_EVIDENCE_NAMES = {
    "ai-platform-signed-package-evidence.json",
    "signed-package-evidence.json",
}

_RELEASE_REVIEW_FILE_NAMES = {
    "ai-platform-skill-release-review.json",
    "skill-release-review.json",
}

_RELEASE_REVIEW_SCHEMA_VERSION = "ai-platform.skill-release-review.v1"

_PENDING_REVIEW_STATUSES = {"pending", "pending_review", "pending-review"}

_SIGNED_PACKAGE_REQUIRED_FIELDS = (
    "package_artifact_ref",
    "package_digest_sha256",
    "signature_artifact_ref",
    "signer_identity",
    "signing_key_or_certificate_ref",
    "transparency_log_or_attestation_ref",
    "verification_status",
    "review_status",
)

_SIGNED_PACKAGE_REFERENCE_FIELDS = {
    "package_artifact_ref",
    "signature_artifact_ref",
    "signing_key_or_certificate_ref",
    "transparency_log_or_attestation_ref",
}

_SIGNED_PACKAGE_VERIFICATION_STATUSES = {"verified", "passed"}

_SIGNED_PACKAGE_REVIEW_STATUSES = {
    "reviewed",
    "accepted",
    "approved",
}

_RELEASE_EVIDENCE_CATEGORIES = {
    "sbom_or_signed_package": _SBOM_FILE_NAMES | _SIGNED_PACKAGE_EVIDENCE_NAMES,
    "license_policy": _LICENSE_FILE_NAMES,
    "vulnerability_scan": _VULNERABILITY_EVIDENCE_NAMES,
}

_PLACEHOLDER_PATTERN = re.compile(
    r"(<[^>]+>|\$\{[^}]+\}|\b(todo|tbd|placeholder|fill-me|fill_me|replace-me|replace_me)\b)",
    re.IGNORECASE,
)

_FORBIDDEN_EVIDENCE_FILE_MARKERS = (
    ".env",
    "api_key",
    "apikey",
    "bearer",
    "callback_token",
    "client-secret",
    "client_secret",
    "database_url",
    "executor_private_payload",
    "private_payload",
    "raw_storage_key",
    "redis_url",
    "sandbox_workdir",
    "secret",
    "storage_key",
    "token",
    "work_dir",
)

_RUNTIME_BYTECODE_PATH_PATTERN = re.compile(r"(__pycache__|\.pyc\b|\.pyo\b)", re.IGNORECASE)

_PERSONAL_MACHINE_PATH_PATTERN = re.compile(
    r"([A-Za-z]:[\\/](?:Users|home)[\\/][^\\/\s\"']+)|(/Users/[^/\s\"']+)|(/home/[^/\s\"']+)",
    re.IGNORECASE,
)


def _relative_external_evidence_file_names(evidence_root: Path | None, skill_id: str) -> list[str]:
    if evidence_root is None:
        return []
    skill_evidence_dir = evidence_root / skill_id
    if not skill_evidence_dir.is_dir() or skill_evidence_dir.is_symlink():
        return []
    names: list[str] = []
    for item in sorted(skill_evidence_dir.rglob("*")):
        if not item.is_file() or item.is_symlink():
            continue
        relative_path = item.relative_to(skill_evidence_dir).as_posix()
        names.append(f"{_EXTERNAL_EVIDENCE_PREFIX}/{skill_id}/{relative_path}")
    return names


def _basename(relative_path: str) -> str:
    return relative_path.replace("\\", "/").rsplit("/", 1)[-1]


def _matching_file_paths(relative_files: list[str], allowed_names: set[str]) -> list[str]:
    matches: list[str] = []
    for relative_path in relative_files:
        normalized = relative_path.replace("\\", "/").lower()
        filename = _basename(normalized)
        if normalized in allowed_names or filename in allowed_names:
            matches.append(relative_path)
    return sorted(matches)


def _matching_files(relative_files: list[str], allowed_names: set[str]) -> list[str]:
    return sorted({_basename(path) for path in _matching_file_paths(relative_files, allowed_names)})


def _normalize_relative_path(value: str) -> str:
    return value.strip().replace("\\", "/").lower()


def _evidence_ref_matches(ref: str, allowed_paths: list[str]) -> bool:
    normalized_ref = _normalize_relative_path(ref)
    allowed_normalized = {_normalize_relative_path(path) for path in allowed_paths}
    if "/" in normalized_ref:
        return normalized_ref in allowed_normalized
    return normalized_ref in {_basename(path).lower() for path in allowed_paths}


def _has_forbidden_evidence_marker(value: str) -> bool:
    normalized = _normalize_relative_path(value)
    return any(marker in normalized for marker in _FORBIDDEN_EVIDENCE_FILE_MARKERS)


def _has_placeholder_marker(value: str) -> bool:
    return bool(_PLACEHOLDER_PATTERN.search(_normalize_relative_path(value)))


def _matched_evidence_paths(ref: str, allowed_paths: list[str]) -> list[str]:
    normalized_ref = _normalize_relative_path(ref)
    if "/" in normalized_ref:
        return [path for path in allowed_paths if normalized_ref == _normalize_relative_path(path)]
    return [path for path in allowed_paths if normalized_ref == _basename(path).lower()]


def _is_signed_package_evidence_path(relative_path: str) -> bool:
    return _basename(_normalize_relative_path(relative_path)) in _SIGNED_PACKAGE_EVIDENCE_NAMES


def _skill_version_source_files(skill_version: dict[str, Any]) -> tuple[list[str], dict[str, bytes | None]]:
    source = skill_version.get("source") if isinstance(skill_version.get("source"), dict) else {}
    files = source.get("files") if isinstance(source.get("files"), list) else []
    relative_files: list[str] = []
    content_by_path: dict[str, bytes | None] = {}
    for item in files:
        if not isinstance(item, dict):
            continue
        relative_path = str(item.get("relative_path") or "").strip().replace("\\", "/").strip("/")
        if not relative_path or relative_path.startswith("/") or ".." in relative_path.split("/"):
            continue
        relative_files.append(relative_path)
        content_base64 = item.get("content_base64")
        if not isinstance(content_base64, str) or not content_base64:
            content_by_path[_normalize_relative_path(relative_path)] = None
            continue
        try:
            content_by_path[_normalize_relative_path(relative_path)] = base64.b64decode(
                content_base64.encode("ascii"),
                validate=True,
            )
        except (binascii.Error, ValueError, UnicodeEncodeError):
            content_by_path[_normalize_relative_path(relative_path)] = None
    return sorted(set(relative_files)), content_by_path


def _read_skill_version_release_file(
    relative_path: str,
    *,
    source_files: dict[str, bytes | None],
    evidence_root: Path | None,
    skill_id: str,
) -> bytes:
    normalized = relative_path.replace("\\", "/")
    external_prefix = f"{_EXTERNAL_EVIDENCE_PREFIX}/{skill_id}/"
    if evidence_root is not None and normalized.startswith(external_prefix):
        suffix = normalized[len(external_prefix) :]
        if not suffix or suffix.startswith("/") or ".." in suffix.split("/"):
            raise OSError("invalid evidence path")
        return (evidence_root / skill_id / suffix).read_bytes()
    content = source_files.get(_normalize_relative_path(normalized))
    if content is None:
        raise OSError("source file content unavailable")
    return content


def _is_safe_signed_package_reference(value: str) -> bool:
    normalized = value.strip().replace("\\", "/")
    lowered = normalized.lower()
    if not normalized:
        return False
    if _has_placeholder_marker(normalized) or _has_forbidden_evidence_marker(normalized):
        return False
    if re.match(r"^[A-Za-z][A-Za-z0-9+.-]*://", normalized):
        if not lowered.startswith("artifact://"):
            return False
        artifact_ref = normalized[len("artifact://") :]
        if not artifact_ref or artifact_ref.startswith("/") or ":" in artifact_ref:
            return False
        if not re.fullmatch(r"[A-Za-z0-9._/-]+", artifact_ref):
            return False
        return not any(part in {"", ".", ".."} for part in artifact_ref.split("/"))
    if normalized.startswith("/") or normalized.startswith("//"):
        return False
    if re.match(r"^[A-Za-z]:[\\/]", value):
        return False
    if ":" in normalized:
        return False
    return ".." not in {part for part in normalized.split("/") if part}


def _signed_package_evidence_payload_errors(payload: Any) -> list[str]:
    if not isinstance(payload, dict):
        return ["signed_package_evidence_invalid_json"]

    errors: list[str] = []
    for field in _SIGNED_PACKAGE_REQUIRED_FIELDS:
        value = payload.get(field)
        if not isinstance(value, str) or not value.strip():
            errors.append("signed_package_evidence_missing_required_fields")
            continue
        if _has_placeholder_marker(value):
            errors.append("signed_package_evidence_placeholder_value")
        if _has_forbidden_evidence_marker(value):
            errors.append("signed_package_evidence_forbidden_reference")

    digest = payload.get("package_digest_sha256")
    if not isinstance(digest, str) or not re.fullmatch(r"[A-Fa-f0-9]{64}", digest.strip()):
        errors.append("signed_package_evidence_invalid_digest")

    verification_status = str(payload.get("verification_status") or "").strip().lower()
    if verification_status not in _SIGNED_PACKAGE_VERIFICATION_STATUSES:
        errors.append("signed_package_evidence_invalid_verification_status")

    review_status = str(payload.get("review_status") or "").strip().lower()
    if review_status not in _SIGNED_PACKAGE_REVIEW_STATUSES:
        errors.append("signed_package_evidence_invalid_review_status")

    for field in _SIGNED_PACKAGE_REFERENCE_FIELDS:
        value = payload.get(field)
        if not isinstance(value, str) or not _is_safe_signed_package_reference(value):
            errors.append("signed_package_evidence_forbidden_reference")
    return sorted(set(errors))


def _signed_package_evidence_content_errors(content: bytes) -> list[str]:
    try:
        payload = json.loads(content.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return ["signed_package_evidence_invalid_json"]
    return _signed_package_evidence_payload_errors(payload)


def _json_has_pending_review_marker(value: Any) -> bool:
    if isinstance(value, dict):
        property_name = str(value.get("name") or "").strip().lower()
        property_value = str(value.get("value") or "").strip().lower()
        if (
            property_name == "ai-platform.evidence_status"
            and property_value in _PENDING_REVIEW_STATUSES
        ):
            return True
        for key, item in value.items():
            normalized_key = str(key).strip().lower()
            if (
                normalized_key in {"status", "review_status", "evidence_status"}
                and isinstance(item, str)
                and item.strip().lower() in _PENDING_REVIEW_STATUSES
            ):
                return True
            if normalized_key == "review_required" and item is True:
                return True
            if _json_has_pending_review_marker(item):
                return True
        return False
    if isinstance(value, list):
        return any(_json_has_pending_review_marker(item) for item in value)
    return False


def _reviewed_evidence_content_errors(relative_path: str, content: bytes, *, category: str) -> list[str]:
    error_name = f"{category}_evidence_not_reviewed"
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return [f"{category}_evidence_file_unreadable"]

    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        payload = None
    if _json_has_pending_review_marker(payload):
        return [error_name]

    normalized_text = text.lower()
    if "status: pending_review" in normalized_text or "status: pending-review" in normalized_text:
        return [error_name]
    if "operator action:" in normalized_text and "_reviewed=true" in normalized_text:
        return [error_name]

    errors: list[str] = []
    basename = _basename(relative_path).lower()
    if category == "sbom_or_signed_package" and basename in _SBOM_FILE_NAMES:
        if _RUNTIME_BYTECODE_PATH_PATTERN.search(text):
            errors.append(f"{category}_evidence_file_runtime_bytecode_content")
    if _PERSONAL_MACHINE_PATH_PATTERN.search(text):
        errors.append(f"{category}_evidence_file_personal_machine_path_content")
    return sorted(set(errors))


def _review_evidence_file_errors_from_content(
    payload: dict[str, Any],
    relative_files: list[str],
    *,
    read_content,
) -> list[str]:
    evidence_files = payload.get("evidence_files")
    if not isinstance(evidence_files, dict):
        return ["evidence_files_missing_or_invalid"]

    errors: list[str] = []
    requires_reviewed_evidence = payload.get("status") == "passed" or any(
        payload.get(flag_name) is True
        for flag_name in ("sbom_reviewed", "license_policy_reviewed", "vulnerability_reviewed")
    )
    for category, allowed_names in _RELEASE_EVIDENCE_CATEGORIES.items():
        category_refs = evidence_files.get(category)
        if not isinstance(category_refs, list) or not category_refs:
            errors.append(f"{category}_evidence_files_missing")
            continue
        allowed_paths = _matching_file_paths(relative_files, allowed_names)
        for item in category_refs:
            if not isinstance(item, str) or not item.strip():
                errors.append(f"{category}_evidence_file_invalid")
                continue
            normalized = _normalize_relative_path(item)
            if _has_placeholder_marker(normalized):
                errors.append(f"{category}_evidence_file_placeholder")
                continue
            if _has_forbidden_evidence_marker(item):
                errors.append(f"{category}_evidence_file_forbidden_marker")
                continue
            if not _evidence_ref_matches(item, allowed_paths):
                errors.append(f"{category}_evidence_file_unmatched")
                continue
            matched_paths = _matched_evidence_paths(item, allowed_paths)
            if any(_has_placeholder_marker(path) for path in matched_paths):
                errors.append(f"{category}_evidence_file_placeholder_actual_path")
                continue
            if any(_has_forbidden_evidence_marker(path) for path in matched_paths):
                errors.append(f"{category}_evidence_file_forbidden_actual_path")
                continue
            for path in matched_paths:
                try:
                    content = read_content(path)
                except OSError:
                    errors.append(f"{category}_evidence_file_unreadable")
                    continue
                if category == "sbom_or_signed_package" and _is_signed_package_evidence_path(path):
                    errors.extend(_signed_package_evidence_content_errors(content))
                elif requires_reviewed_evidence:
                    errors.extend(
                        _reviewed_evidence_content_errors(
                            path,
                            content,
                            category=category,
                        )
                    )
    return sorted(set(errors))


def _valid_signed_package_evidence_paths_from_content(
    relative_files: list[str],
    *,
    read_content,
) -> list[str]:
    paths = _matching_file_paths(relative_files, _SIGNED_PACKAGE_EVIDENCE_NAMES)
    valid_paths: list[str] = []
    for path in paths:
        try:
            content = read_content(path)
        except OSError:
            continue
        if not _signed_package_evidence_content_errors(content):
            valid_paths.append(path)
    return valid_paths


def _package_evidence_from_content(
    relative_files: list[str],
    *,
    read_content,
) -> dict[str, list[str]]:
    signed_package_paths = _valid_signed_package_evidence_paths_from_content(
        relative_files,
        read_content=read_content,
    )
    return {
        "metadata_files": _matching_files(relative_files, _PACKAGE_METADATA_FILES),
        "requirements_files": _matching_files(relative_files, _REQUIREMENTS_FILES),
        "sbom_files": _matching_files(relative_files, _SBOM_FILE_NAMES),
        "signed_package_evidence_files": sorted({_basename(path) for path in signed_package_paths}),
        "license_files": _matching_files(relative_files, _LICENSE_FILE_NAMES),
        "vulnerability_evidence_files": _matching_files(relative_files, _VULNERABILITY_EVIDENCE_NAMES),
    }


def _release_review_from_content(
    relative_files: list[str],
    *,
    skill_id: str,
    content_hash: str,
    read_content,
) -> dict[str, Any]:
    review_paths = _matching_file_paths(relative_files, _RELEASE_REVIEW_FILE_NAMES)
    review_files = sorted({_basename(path) for path in review_paths})
    if not review_paths:
        return {
            "status": "missing",
            "files": [],
            "evidence_files_verified": False,
            "sbom_reviewed": False,
            "license_policy_reviewed": False,
            "vulnerability_reviewed": False,
            "content_hash_verified": False,
        }

    invalid_files: list[str] = []
    pending_files: list[str] = []
    evidence_file_errors: list[str] = []
    review_flag_errors: list[str] = []
    for relative_path in review_paths:
        try:
            payload = json.loads(read_content(relative_path).decode("utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            invalid_files.append(_basename(relative_path))
            continue
        if not isinstance(payload, dict):
            invalid_files.append(_basename(relative_path))
            continue
        if payload.get("schema_version") != _RELEASE_REVIEW_SCHEMA_VERSION:
            invalid_files.append(_basename(relative_path))
            continue
        if str(payload.get("skill_id") or "") != skill_id:
            invalid_files.append(_basename(relative_path))
            continue
        current_review_flag_errors = _review_flag_errors(payload)
        if str(payload.get("skill_content_hash") or "") != content_hash:
            current_review_flag_errors.append("review_content_hash_mismatch")
        current_evidence_file_errors = _review_evidence_file_errors_from_content(
            payload,
            relative_files,
            read_content=read_content,
        )
        if current_review_flag_errors or current_evidence_file_errors:
            if payload.get("status") == "passed" or current_evidence_file_errors:
                invalid_files.append(_basename(relative_path))
                evidence_file_errors.extend(current_evidence_file_errors)
                review_flag_errors.extend(current_review_flag_errors)
            else:
                pending_files.append(_basename(relative_path))
                review_flag_errors.extend(current_review_flag_errors)
            continue
        if payload.get("status") != "passed":
            pending_files.append(_basename(relative_path))
            continue
        return {
            "status": "passed",
            "files": review_files,
            "evidence_files_verified": True,
            "review_flag_errors": [],
            "sbom_reviewed": True,
            "license_policy_reviewed": True,
            "vulnerability_reviewed": True,
            "content_hash_verified": True,
        }

    if pending_files and not invalid_files and not evidence_file_errors:
        return {
            "status": "pending_review",
            "files": review_files,
            "invalid_files": [],
            "pending_files": sorted(set(pending_files)),
            "evidence_files_verified": True,
            "evidence_file_errors": [],
            "review_flag_errors": sorted(set(review_flag_errors)),
            "sbom_reviewed": False,
            "license_policy_reviewed": False,
            "vulnerability_reviewed": False,
            "content_hash_verified": False,
        }

    return {
        "status": "invalid_or_incomplete",
        "files": review_files,
        "invalid_files": sorted(set(invalid_files)),
        "evidence_files_verified": False,
        "evidence_file_errors": sorted(set(evidence_file_errors)),
        "review_flag_errors": sorted(set(review_flag_errors)),
        "sbom_reviewed": False,
        "license_policy_reviewed": False,
        "vulnerability_reviewed": False,
        "content_hash_verified": False,
    }


def _review_flag_errors(payload: dict[str, Any]) -> list[str]:
    flag_names = ("sbom_reviewed", "license_policy_reviewed", "vulnerability_reviewed")
    if all(payload.get(flag_name) is True for flag_name in flag_names):
        return []
    return ["review_flags_missing_or_invalid"]


def _skill_blockers(
    evidence: dict[str, list[str]],
    release_review: dict[str, Any],
    dependency_policy: dict[str, object],
) -> list[str]:
    blockers: list[str] = []
    dependency_details = dependency_policy.get("dependency_details", [])
    if isinstance(dependency_details, list) and any(
        isinstance(item, dict) and item.get("status") != "allowed" for item in dependency_details
    ):
        blockers.append("skill_dependency_policy_blocked")
    if not evidence["sbom_files"] and not evidence["signed_package_evidence_files"]:
        blockers.append("signed_package_or_sbom_evidence_missing")
    elif not release_review.get("sbom_reviewed"):
        blockers.append("signed_package_or_sbom_review_not_verified")
    if not evidence["license_files"]:
        blockers.append("dependency_license_policy_evidence_missing")
    elif not release_review.get("license_policy_reviewed"):
        blockers.append("dependency_license_policy_review_not_verified")
    if not evidence["vulnerability_evidence_files"]:
        blockers.append("dependency_vulnerability_evidence_missing")
    elif not release_review.get("vulnerability_reviewed"):
        blockers.append("dependency_vulnerability_review_not_verified")
    return blockers


def build_skill_version_release_review(
    skill_version: dict[str, Any],
    *,
    skill_release_evidence_root: str | Path | None = "docs/release-evidence/skill-release",
) -> dict[str, Any]:
    """Build a fail-closed release-review verdict for one immutable Skill version."""
    skill_id = str(skill_version.get("skill_id") or "").strip()
    version = str(skill_version.get("version") or "").strip()
    content_hash = str(skill_version.get("content_hash") or version).strip()
    source_relative_files, source_files = _skill_version_source_files(skill_version)
    evidence_root = Path(skill_release_evidence_root) if skill_release_evidence_root is not None else None
    relative_files = [
        *source_relative_files,
        *_relative_external_evidence_file_names(evidence_root, skill_id),
    ]

    def read_content(relative_path: str) -> bytes:
        return _read_skill_version_release_file(
            relative_path,
            source_files=source_files,
            evidence_root=evidence_root,
            skill_id=skill_id,
        )

    evidence = _package_evidence_from_content(relative_files, read_content=read_content)
    release_review = _release_review_from_content(
        relative_files,
        skill_id=skill_id,
        content_hash=content_hash,
        read_content=read_content,
    )
    blockers = _skill_blockers(evidence, release_review, {"dependency_details": []})
    return {
        "schema_version": SKILL_VERSION_RELEASE_REVIEW_SCHEMA_VERSION,
        "status": "blocked" if blockers else "passed",
        "skill_id": skill_id,
        "version": version,
        "content_hash": content_hash,
        "package_evidence": evidence,
        "release_review": release_review,
        "blockers": blockers,
        "does_not_close_g6": True,
    }
