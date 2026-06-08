from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.skills.dependencies import (
    INTERNAL_DEPENDENCY_SKILL_IDS,
    PUBLIC_WORKBENCH_SKILL_IDS,
    skill_dependency_policy,
)
from app.skills.registry import BuiltinSkillRegistry


SCHEMA_VERSION = "ai-platform.skill-release-readiness.v1"
GATE_NAME = "G6 Skill Release / Dependency Governance"

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
_RELEASE_REVIEW_FILE_NAMES = {
    "ai-platform-skill-release-review.json",
    "skill-release-review.json",
}
_RELEASE_REVIEW_SCHEMA_VERSION = "ai-platform.skill-release-review.v1"


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
            "sbom_reviewed": False,
            "license_policy_reviewed": False,
            "vulnerability_reviewed": False,
        }

    invalid_files: list[str] = []
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
        return {
            "status": "passed",
            "files": review_files,
            "sbom_reviewed": bool(payload.get("sbom_reviewed")),
            "license_policy_reviewed": bool(payload.get("license_policy_reviewed")),
            "vulnerability_reviewed": bool(payload.get("vulnerability_reviewed")),
        }

    return {
        "status": "invalid_or_incomplete",
        "files": review_files,
        "invalid_files": sorted(set(invalid_files)),
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
    return gaps


def build_skill_release_readiness(*, skills_root: str | Path = "skills") -> dict[str, Any]:
    """Build a secret-safe, offline skill release governance evidence snapshot."""
    root = Path(skills_root)
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
    open_gaps = _open_gaps(skill_items, inventory_present=inventory_present)
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
        "open_gaps": open_gaps,
        "evidence_policy": (
            "signed package or SBOM evidence plus dependency license and vulnerability review "
            "are required before closing the skill release governance gate"
        ),
    }


def render_skill_release_readiness_markdown(readiness: dict[str, Any]) -> str:
    gap_lines = "\n".join(f"- {gap}" for gap in readiness["open_gaps"]) or "- none"
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
        "## Skills\n\n"
        f"{skills_markdown}\n\n"
        "## Evidence Policy\n\n"
        f"{readiness['evidence_policy']}\n"
    )
