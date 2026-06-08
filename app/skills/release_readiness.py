from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
import re
from typing import Any

from app.skills.dependencies import (
    INTERNAL_DEPENDENCY_SKILL_IDS,
    PUBLIC_WORKBENCH_SKILL_IDS,
    skill_dependency_policy,
)
from app.skills.release_dashboard_readiness import (
    build_skill_release_dashboard_readiness,
)
from app.skills.registry import BuiltinSkillRegistry


SCHEMA_VERSION = "ai-platform.skill-release-readiness.v1"
DEPENDENCY_REVIEW_POLICY_SCHEMA_VERSION = "ai-platform.skill-dependency-review-policy.v1"
SIGNED_PACKAGE_EVIDENCE_CONTRACT_SCHEMA_VERSION = "ai-platform.skill-signed-package-evidence-contract.v1"
GATE_NAME = "G6 Skill Release / Dependency Governance"
DEPENDENCY_REVIEW_POLICY_RUNTIME_GAP = "skill_dependency_review_policy_runtime_acceptance"

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
    "package-signature.json",
    "cosign.bundle",
    "in-toto-attestation.jsonl",
    "slsa-provenance.intoto.jsonl",
}
_RELEASE_REVIEW_FILE_NAMES = {
    "ai-platform-skill-release-review.json",
    "skill-release-review.json",
}
_RELEASE_REVIEW_SCHEMA_VERSION = "ai-platform.skill-release-review.v1"
_RELEASE_EVIDENCE_CATEGORIES = {
    "sbom_or_signed_package": _SBOM_FILE_NAMES,
    "license_policy": _LICENSE_FILE_NAMES,
    "vulnerability_scan": _VULNERABILITY_EVIDENCE_NAMES,
}
_SIGNED_PACKAGE_EVIDENCE_CONTRACT = {
    "schema_version": SIGNED_PACKAGE_EVIDENCE_CONTRACT_SCHEMA_VERSION,
    "status": "contract_only_not_runtime_satisfied",
    "evidence_category": "sbom_or_signed_package",
    "required_review_manifest_schema": _RELEASE_REVIEW_SCHEMA_VERSION,
    "required_review_flag": "sbom_reviewed",
    "candidate_evidence_file_names": sorted(_SIGNED_PACKAGE_EVIDENCE_NAMES),
    "required_fields": [
        "package_artifact_ref",
        "package_digest_sha256",
        "signature_artifact_ref",
        "signer_identity",
        "signing_key_or_certificate_ref",
        "transparency_log_or_attestation_ref",
        "verification_status",
        "review_status",
    ],
    "safe_reference_policy": {
        "relative_or_artifact_refs_only": True,
        "raw_object_storage_refs_forbidden": True,
        "executor_private_runtime_payload_forbidden": True,
        "sandbox_working_directory_forbidden": True,
        "secret_like_values_forbidden": True,
    },
    "runtime_validation_gap": "skill_signed_package_evidence_runtime_validation",
    "does_not_close_g6": True,
}
_DEPENDENCY_REVIEW_POLICY = {
    "schema_version": DEPENDENCY_REVIEW_POLICY_SCHEMA_VERSION,
    "status": "contract_only_not_runtime_satisfied",
    "required_review_manifest_schema": _RELEASE_REVIEW_SCHEMA_VERSION,
    "required_review_flags": [
        "sbom_reviewed",
        "license_policy_reviewed",
        "vulnerability_reviewed",
    ],
    "required_evidence_categories": [
        "sbom_or_signed_package",
        "license_policy",
        "vulnerability_scan",
    ],
    "required_evidence_file_names": {
        category: sorted(file_names)
        for category, file_names in _RELEASE_EVIDENCE_CATEGORIES.items()
    },
    "evidence_files_must_match_skill_inventory": True,
    "rejects_placeholder_evidence_refs": True,
    "rejects_secret_like_evidence_refs": True,
    "signed_package_evidence_contract": _SIGNED_PACKAGE_EVIDENCE_CONTRACT,
    "does_not_close_g6": True,
}
_SKILL_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
_PLACEHOLDER_PATTERN = re.compile(
    r"(<[^>]+>|\$\{[^}]+\}|\b(todo|tbd|placeholder|fill-me|fill_me|replace-me|replace_me)\b)",
    re.IGNORECASE,
)
_FORBIDDEN_SKILL_ID_MARKERS = (
    "/",
    "\\",
    "..",
    ".env",
    ".claude",
    "secret",
    "token=",
    "work_dir",
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


def _validate_skill_id(skill_id: str) -> str:
    normalized = str(skill_id or "").strip()
    lowered = normalized.lower()
    if (
        not _SKILL_ID_PATTERN.fullmatch(normalized)
        or any(marker in lowered for marker in _FORBIDDEN_SKILL_ID_MARKERS)
    ):
        raise ValueError("Invalid skill_id for release review template")
    return normalized


def build_skill_release_review_template(*, skill_id: str) -> dict[str, Any]:
    """Build a pending, operator-fillable review manifest that cannot close G6 by itself."""
    normalized_skill_id = _validate_skill_id(skill_id)
    return {
        "schema_version": _RELEASE_REVIEW_SCHEMA_VERSION,
        "status": "pending",
        "skill_id": normalized_skill_id,
        "reviewer": "",
        "reviewed_at": "",
        "sbom_reviewed": False,
        "license_policy_reviewed": False,
        "vulnerability_reviewed": False,
        "does_not_close_gate_by_itself": True,
        "required_evidence": {
            "sbom_or_signed_package": sorted(_SBOM_FILE_NAMES),
            "license_policy": sorted(_LICENSE_FILE_NAMES),
            "vulnerability_scan": sorted(_VULNERABILITY_EVIDENCE_NAMES),
        },
        "evidence_files": {
            "sbom_or_signed_package": [],
            "license_policy": [],
            "vulnerability_scan": [],
        },
        "review_checklist": [
            {
                "id": "sbom_or_signed_package",
                "passed": False,
                "notes": "Confirm package provenance and SBOM evidence before setting sbom_reviewed=true. Signed-package evidence has a source contract but still needs runtime validation before it can clear this gate.",
            },
            {
                "id": "license_policy",
                "passed": False,
                "notes": "Confirm third-party license policy evidence before setting license_policy_reviewed=true.",
            },
            {
                "id": "vulnerability_scan",
                "passed": False,
                "notes": "Confirm dependency vulnerability scan evidence before setting vulnerability_reviewed=true.",
            },
        ],
        "operator_instructions": (
            "Keep status pending until real evidence files are present and reviewed. "
            "Only change status to passed after all review booleans are true."
        ),
    }


def _relative_file_names(skill_dir: Path) -> list[str]:
    names: list[str] = []
    for item in sorted(skill_dir.rglob("*")):
        if not item.is_file() or item.is_symlink():
            continue
        names.append(item.relative_to(skill_dir).as_posix())
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


def _review_evidence_file_errors(payload: dict[str, Any], relative_files: list[str]) -> list[str]:
    evidence_files = payload.get("evidence_files")
    if not isinstance(evidence_files, dict):
        return ["evidence_files_missing_or_invalid"]

    errors: list[str] = []
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
    return sorted(set(errors))


def _review_flag_errors(payload: dict[str, Any]) -> list[str]:
    flag_names = ("sbom_reviewed", "license_policy_reviewed", "vulnerability_reviewed")
    if all(payload.get(flag_name) is True for flag_name in flag_names):
        return []
    return ["review_flags_missing_or_invalid"]


def _package_evidence(relative_files: list[str]) -> dict[str, list[str]]:
    return {
        "metadata_files": _matching_files(relative_files, _PACKAGE_METADATA_FILES),
        "requirements_files": _matching_files(relative_files, _REQUIREMENTS_FILES),
        "sbom_files": _matching_files(relative_files, _SBOM_FILE_NAMES),
        "license_files": _matching_files(relative_files, _LICENSE_FILE_NAMES),
        "vulnerability_evidence_files": _matching_files(relative_files, _VULNERABILITY_EVIDENCE_NAMES),
    }


def _release_review(skill_dir: Path, relative_files: list[str], *, skill_id: str) -> dict[str, Any]:
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
        }

    invalid_files: list[str] = []
    evidence_file_errors: list[str] = []
    review_flag_errors: list[str] = []
    for relative_path in review_paths:
        try:
            payload = json.loads((skill_dir / relative_path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            invalid_files.append(_basename(relative_path))
            continue
        if not isinstance(payload, dict):
            invalid_files.append(_basename(relative_path))
            continue
        if payload.get("schema_version") != _RELEASE_REVIEW_SCHEMA_VERSION:
            invalid_files.append(_basename(relative_path))
            continue
        if payload.get("status") != "passed":
            invalid_files.append(_basename(relative_path))
            continue
        if str(payload.get("skill_id") or "") != skill_id:
            invalid_files.append(_basename(relative_path))
            continue
        current_review_flag_errors = _review_flag_errors(payload)
        current_evidence_file_errors = _review_evidence_file_errors(payload, relative_files)
        if current_review_flag_errors or current_evidence_file_errors:
            invalid_files.append(_basename(relative_path))
            evidence_file_errors.extend(current_evidence_file_errors)
            review_flag_errors.extend(current_review_flag_errors)
            continue
        return {
            "status": "passed",
            "files": review_files,
            "evidence_files_verified": True,
            "review_flag_errors": [],
            "sbom_reviewed": True,
            "license_policy_reviewed": True,
            "vulnerability_reviewed": True,
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
    }


def _skill_status(blockers: list[str]) -> str:
    return "partial_blocked" if blockers else "ready_for_verification"


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
    if not evidence["sbom_files"]:
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


def _open_gaps(skills: list[dict[str, Any]], *, inventory_present: bool) -> list[str]:
    gaps: list[str] = []
    if not inventory_present:
        gaps.append("skill_inventory_missing_or_empty")
    if any("skill_dependency_policy_blocked" in item["blockers"] for item in skills):
        gaps.append("skill_dependency_policy_blocked")
    if any(
        "signed_package_or_sbom_evidence_missing" in item["blockers"]
        or "signed_package_or_sbom_review_not_verified" in item["blockers"]
        for item in skills
    ):
        gaps.append("signed_skill_package_or_sbom_release_gate")
    if any(
        "dependency_license_policy_evidence_missing" in item["blockers"]
        or "dependency_vulnerability_evidence_missing" in item["blockers"]
        or "dependency_license_policy_review_not_verified" in item["blockers"]
        or "dependency_vulnerability_review_not_verified" in item["blockers"]
        for item in skills
    ):
        gaps.append("dependency_vulnerability_or_license_policy")
    gaps.append(DEPENDENCY_REVIEW_POLICY_RUNTIME_GAP)
    return gaps


def build_skill_release_readiness(*, skills_root: str | Path = "skills") -> dict[str, Any]:
    """Build a secret-safe, offline skill release governance evidence snapshot."""
    root = Path(skills_root)
    dashboard_readiness = build_skill_release_dashboard_readiness()
    builtin_skills = BuiltinSkillRegistry(root).list_builtin_skills()
    available_skill_ids = {skill.name for skill in builtin_skills}
    skill_items: list[dict[str, Any]] = []
    for skill in builtin_skills:
        relative_files = _relative_file_names(skill.path)
        evidence = _package_evidence(relative_files)
        release_review = _release_review(skill.path, relative_files, skill_id=skill.name)
        dependency_policy = skill_dependency_policy(skill.name, available_skill_ids)
        blockers = _skill_blockers(evidence, release_review, dependency_policy)
        skill_items.append(
            {
                "skill_id": skill.name,
                "public": skill.name in PUBLIC_WORKBENCH_SKILL_IDS,
                "internal_dependency": skill.name in INTERNAL_DEPENDENCY_SKILL_IDS,
                "content_hash": skill.version,
                "manifest": {
                    "present": True,
                    "name_matches_directory": True,
                    "description_present": bool(skill.description),
                },
                "dependency_policy": dependency_policy,
                "package_evidence": evidence,
                "release_review": release_review,
                "status": _skill_status(blockers),
                "blockers": blockers,
            }
        )

    inventory_present = bool(root.exists() and skill_items)
    open_gaps = [
        *_open_gaps(skill_items, inventory_present=inventory_present),
        *dashboard_readiness["open_gaps"],
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "gate": GATE_NAME,
        "status": "partial_blocked" if open_gaps else "ready_for_verification",
        "source": {
            "mode": "offline_repo_skill_inventory",
            "root": "skills",
            "inventory_present": inventory_present,
        },
        "summary": {
            "total_skills": len(skill_items),
            "public_workbench_skills": sum(1 for item in skill_items if item["public"]),
            "internal_dependency_skills": sum(1 for item in skill_items if item["internal_dependency"]),
            "skills_with_declared_dependencies": sum(
                1 for item in skill_items if item["dependency_policy"]["dependency_ids"]
            ),
            "skills_with_package_metadata": sum(
                1 for item in skill_items if item["package_evidence"]["metadata_files"]
            ),
            "skills_with_requirements": sum(
                1 for item in skill_items if item["package_evidence"]["requirements_files"]
            ),
            "skills_with_sbom_evidence": sum(1 for item in skill_items if item["package_evidence"]["sbom_files"]),
            "skills_with_license_evidence": sum(
                1 for item in skill_items if item["package_evidence"]["license_files"]
            ),
            "skills_with_vulnerability_evidence": sum(
                1 for item in skill_items if item["package_evidence"]["vulnerability_evidence_files"]
            ),
        },
        "skills": skill_items,
        "dependency_review_policy": deepcopy(_DEPENDENCY_REVIEW_POLICY),
        "admin_skill_release_dashboard": dashboard_readiness,
        "open_gaps": open_gaps,
        "evidence_policy": (
            "SBOM evidence plus dependency license and vulnerability review "
            "are required before closing the skill release governance gate"
        ),
    }


def render_skill_release_readiness_markdown(readiness: dict[str, Any]) -> str:
    gap_lines = "\n".join(f"- {gap}" for gap in readiness["open_gaps"]) or "- none"
    policy = readiness.get("dependency_review_policy")
    policy_lines = "- none"
    if isinstance(policy, dict):
        required_flags = ", ".join(policy.get("required_review_flags", []))
        required_categories = ", ".join(policy.get("required_evidence_categories", []))
        signed_contract = policy.get("signed_package_evidence_contract")
        signed_contract_lines = ""
        if isinstance(signed_contract, dict):
            signed_fields = ", ".join(signed_contract.get("required_fields", []))
            signed_contract_lines = (
                f"\n- Signed package evidence contract: `{signed_contract.get('schema_version')}`"
                f"\n- Signed package contract status: `{signed_contract.get('status')}`"
                f"\n- Signed package required fields: `{signed_fields}`"
                f"\n- Signed package runtime gap: `{signed_contract.get('runtime_validation_gap')}`"
                f"\n- Signed package contract does not close G6: `{signed_contract.get('does_not_close_g6')}`"
            )
        policy_lines = (
            f"- Schema: `{policy.get('schema_version')}`\n"
            f"- Status: `{policy.get('status')}`\n"
            f"- Required review manifest: `{policy.get('required_review_manifest_schema')}`\n"
            f"- Required flags: `{required_flags}`\n"
            f"- Required evidence categories: `{required_categories}`\n"
            f"- Evidence files must match Skill inventory: `{policy.get('evidence_files_must_match_skill_inventory')}`\n"
            f"- Does not close G6: `{policy.get('does_not_close_g6')}`"
            f"{signed_contract_lines}"
        )
    dashboard = readiness.get("admin_skill_release_dashboard")
    dashboard_lines = "- none"
    if isinstance(dashboard, dict):
        contract = dashboard.get("dashboard_contract")
        if isinstance(contract, dict):
            dashboard_gaps = ", ".join(dashboard.get("open_gaps", []))
            dashboard_controls = ", ".join(contract.get("required_dashboard_controls", []))
            dashboard_lines = (
                f"- Readiness schema: `{dashboard.get('schema_version')}`\n"
                f"- Status: `{dashboard.get('status')}`\n"
                f"- Dashboard contract: `{contract.get('schema_version')}`\n"
                f"- Admin only: `{contract.get('admin_only')}`\n"
                f"- Same tenant only: `{contract.get('same_tenant_only')}`\n"
                f"- Required controls: `{dashboard_controls}`\n"
                f"- Open gaps: `{dashboard_gaps}`\n"
                f"- Does not close G6: `{dashboard.get('does_not_close_g6')}`"
            )
    skill_lines = []
    for item in readiness["skills"]:
        blockers = ", ".join(item["blockers"]) if item["blockers"] else "none"
        dependencies = ", ".join(item["dependency_policy"]["dependency_ids"]) or "none"
        skill_lines.append(
            f"- `{item['skill_id']}`: status `{item['status']}`, dependencies `{dependencies}`, blockers `{blockers}`"
        )
    skills_markdown = "\n".join(skill_lines) or "- none"
    return (
        "# ai-platform Skill Release Readiness\n\n"
        f"Schema: `{readiness['schema_version']}`\n\n"
        f"Gate: `{readiness['gate']}`\n\n"
        f"Status: `{readiness['status']}`\n\n"
        "## Open Gaps\n\n"
        f"{gap_lines}\n\n"
        "## Summary\n\n"
        f"- Total skills: `{readiness['summary']['total_skills']}`\n"
        f"- Skills with package metadata: `{readiness['summary']['skills_with_package_metadata']}`\n"
        f"- Skills with requirements: `{readiness['summary']['skills_with_requirements']}`\n"
        f"- Skills with SBOM evidence: `{readiness['summary']['skills_with_sbom_evidence']}`\n"
        f"- Skills with license evidence: `{readiness['summary']['skills_with_license_evidence']}`\n"
        f"- Skills with vulnerability evidence: `{readiness['summary']['skills_with_vulnerability_evidence']}`\n\n"
        "## Dependency Review Policy\n\n"
        f"{policy_lines}\n\n"
        "## Admin Skill Release Dashboard Contract\n\n"
        f"{dashboard_lines}\n\n"
        "## Skills\n\n"
        f"{skills_markdown}\n\n"
        "## Evidence Policy\n\n"
        f"{readiness['evidence_policy']}\n"
    )


def render_skill_release_review_template_markdown(template: dict[str, Any]) -> str:
    required_lines = []
    for category, filenames in template["required_evidence"].items():
        required_lines.append(f"- `{category}`: `{', '.join(filenames)}`")
    checklist_lines = []
    for item in template["review_checklist"]:
        checklist_lines.append(f"- `{item['id']}`: passed `{item['passed']}`, notes `{item['notes']}`")
    required_markdown = "\n".join(required_lines)
    checklist_markdown = "\n".join(checklist_lines)
    return (
        "# ai-platform Skill Release Review Template\n\n"
        f"Schema: `{template['schema_version']}`\n\n"
        f"Skill: `{template['skill_id']}`\n\n"
        f"Status: `{template['status']}`\n\n"
        f"Does not close gate by itself: `{template['does_not_close_gate_by_itself']}`\n\n"
        "## Review Flags\n\n"
        f"- SBOM reviewed: `{template['sbom_reviewed']}`\n"
        f"- License policy reviewed: `{template['license_policy_reviewed']}`\n"
        f"- Vulnerability reviewed: `{template['vulnerability_reviewed']}`\n\n"
        "## Required Evidence\n\n"
        f"{required_markdown}\n\n"
        "## Checklist\n\n"
        f"{checklist_markdown}\n\n"
        "## Operator Instructions\n\n"
        f"{template['operator_instructions']}\n"
    )
