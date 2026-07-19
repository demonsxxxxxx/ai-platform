from __future__ import annotations

from typing import Any, Literal, TypedDict

from app.skills.lifecycle import (
    SKILL_VERSION_RELEASED,
    SKILL_VERSION_REVIEWED,
    normalize_skill_version_status,
)
from app.tool_policy import BUILTIN_TOOL_IDENTITIES


SKILL_EXECUTION_PROFILE_SCHEMA_VERSION = "ai-platform.skill-execution-profile.v1"
SKILL_WORKSPACE_CONTRACT_VERSION = "ai-platform.skill-workspace.v1"

PLATFORM_CONTROLLED = "platform_controlled"
SDK_NATIVE = "sdk_native"
SDK_RESTRICTED = "sdk_restricted"

NATIVE_COMMAND_ISOLATION = "sibling-tool-sandbox-v1"
CONTROLLED_COMMAND_ISOLATION = "minimal-environment-v1"

_SERVER_BUILTIN_TOOL_DECLARATIONS = {
    "baoyu-translate": ("Bash", "Write"),
    "ctd-32s73-stability-template-fill": ("Bash", "Write"),
    "minimax-docx": ("Bash", "Write"),
    "qa-file-reviewer": ("Bash", "Write"),
}
_PLATFORM_CONTROLLED_SKILLS = frozenset({"baoyu-translate", "qa-file-reviewer"})
_NATIVE_UPLOADED_TOOL_IDENTITIES = (
    "Read",
    "Glob",
    "LS",
    "Bash",
    "Write",
    "Edit",
)
_TRUSTED_UPLOADED_STATUSES = frozenset({SKILL_VERSION_REVIEWED, SKILL_VERSION_RELEASED})


class SkillExecutionProfile(TypedDict):
    """Canonical server-owned runtime authority for one pinned Skill version."""

    schema_version: str
    strategy: Literal["platform_controlled", "sdk_native", "sdk_restricted"]
    trust_basis: str
    builtin_tool_identities: list[str]
    workspace_contract: str
    command_isolation: str


class SkillExecutionProfileError(ValueError):
    """Raised when a pinned execution profile differs from server authority."""

    pass


def _known_tool_identities(values: tuple[str, ...]) -> list[str]:
    return [identity for identity in values if identity in BUILTIN_TOOL_IDENTITIES]


def resolve_skill_execution_profile(
    *,
    skill_id: str,
    source_kind: str,
    lifecycle_status: str,
) -> SkillExecutionProfile:
    """Resolve the server-owned runtime strategy for one immutable Skill version."""

    normalized_status = normalize_skill_version_status(lifecycle_status)
    if source_kind == "builtin":
        identities = _known_tool_identities(_SERVER_BUILTIN_TOOL_DECLARATIONS.get(skill_id, ()))
        controlled = skill_id in _PLATFORM_CONTROLLED_SKILLS
        return {
            "schema_version": SKILL_EXECUTION_PROFILE_SCHEMA_VERSION,
            "strategy": (
                PLATFORM_CONTROLLED
                if controlled
                else SDK_NATIVE if identities else SDK_RESTRICTED
            ),
            "trust_basis": "repository_builtin",
            "builtin_tool_identities": identities,
            "workspace_contract": SKILL_WORKSPACE_CONTRACT_VERSION,
            "command_isolation": (
                CONTROLLED_COMMAND_ISOLATION
                if controlled
                else NATIVE_COMMAND_ISOLATION if "Bash" in identities else "none"
            ),
        }
    if source_kind == "uploaded" and normalized_status in _TRUSTED_UPLOADED_STATUSES:
        return {
            "schema_version": SKILL_EXECUTION_PROFILE_SCHEMA_VERSION,
            "strategy": SDK_NATIVE,
            "trust_basis": "admin_reviewed_release",
            "builtin_tool_identities": _known_tool_identities(_NATIVE_UPLOADED_TOOL_IDENTITIES),
            "workspace_contract": SKILL_WORKSPACE_CONTRACT_VERSION,
            "command_isolation": NATIVE_COMMAND_ISOLATION,
        }
    return {
        "schema_version": SKILL_EXECUTION_PROFILE_SCHEMA_VERSION,
        "strategy": SDK_RESTRICTED,
        "trust_basis": "legacy_or_unreviewed",
        "builtin_tool_identities": [],
        "workspace_contract": SKILL_WORKSPACE_CONTRACT_VERSION,
        "command_isolation": "none",
    }


def legacy_skill_execution_profile(manifest: dict[str, Any]) -> SkillExecutionProfile:
    """Preserve the tool authority of a pin created before profiles existed."""

    source = manifest.get("source") if isinstance(manifest.get("source"), dict) else {}
    source_kind = str(source.get("kind") or "")
    if source_kind == "builtin":
        return resolve_skill_execution_profile(
            skill_id=str(manifest.get("skill_id") or ""),
            source_kind=source_kind,
            lifecycle_status=SKILL_VERSION_RELEASED,
        )
    return resolve_skill_execution_profile(
        skill_id=str(manifest.get("skill_id") or ""),
        source_kind=source_kind,
        lifecycle_status="draft",
    )


def canonical_skill_execution_profile(manifest: dict[str, Any]) -> SkillExecutionProfile:
    """Validate and return the immutable server-derived execution profile."""

    raw = manifest.get("execution_profile")
    if raw is None:
        return legacy_skill_execution_profile(manifest)
    if not isinstance(raw, dict):
        raise SkillExecutionProfileError("run_skill_snapshot_execution_profile_mismatch")
    source = manifest.get("source") if isinstance(manifest.get("source"), dict) else {}
    expected = resolve_skill_execution_profile(
        skill_id=str(manifest.get("skill_id") or ""),
        source_kind=str(source.get("kind") or ""),
        lifecycle_status=str(manifest.get("lifecycle_status") or ""),
    )
    normalized = {
        "schema_version": str(raw.get("schema_version") or ""),
        "strategy": str(raw.get("strategy") or ""),
        "trust_basis": str(raw.get("trust_basis") or ""),
        "builtin_tool_identities": list(raw.get("builtin_tool_identities") or [])
        if isinstance(raw.get("builtin_tool_identities"), list)
        else [],
        "workspace_contract": str(raw.get("workspace_contract") or ""),
        "command_isolation": str(raw.get("command_isolation") or ""),
    }
    if normalized != expected:
        raise SkillExecutionProfileError("run_skill_snapshot_execution_profile_mismatch")
    return expected


def is_platform_controlled_profile(manifest: dict[str, Any]) -> bool:
    """Return whether a pinned manifest selects the controlled runner."""

    return canonical_skill_execution_profile(manifest)["strategy"] == PLATFORM_CONTROLLED
