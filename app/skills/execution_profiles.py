from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, TypedDict

from app.control_plane_contracts import LEGACY_SYNTHETIC_CHAT_SKILL_ID
from app.skills.lifecycle import (
    SKILL_VERSION_LEGACY_ACTIVE,
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
OPEN_SANDBOX_GOVERNED_SDK_EXECUTION_PROFILE = "opensandbox_governed"
OPEN_SANDBOX_GOVERNED_COMMAND_ISOLATION = "opensandbox-workspace-v1"

_EXPLICIT_SKILL_BASH_IDENTITY = ("Bash",)
_SERVER_BUILTIN_NON_BASH_TOOL_DECLARATIONS = {
    "baoyu-translate": ("Write",),
    "ctd-32s73-stability-template-fill": ("Write",),
    "minimax-docx": ("Write",),
    "qa-file-reviewer": ("Write",),
}
_PLATFORM_CONTROLLED_SKILLS = frozenset({"baoyu-translate", "qa-file-reviewer"})
_NATIVE_UPLOADED_TOOL_IDENTITIES = (
    "Read",
    "Glob",
    "LS",
    "Bash",
    "Write",
    "Edit",
    "Grep",
)
_OPEN_SANDBOX_SDK_SKILL_FILE_TOOLS = (
    "Read",
    "Glob",
    "LS",
    "Bash",
    "Write",
    "Edit",
    "Grep",
)
_TRUSTED_UPLOADED_STATUSES = frozenset({SKILL_VERSION_REVIEWED, SKILL_VERSION_RELEASED})
_TRUSTED_BUILTIN_STATUSES = frozenset(
    {SKILL_VERSION_LEGACY_ACTIVE, SKILL_VERSION_RELEASED, SKILL_VERSION_REVIEWED}
)


class SkillExecutionProfile(TypedDict):
    """Canonical server-owned runtime authority for one pinned Skill version."""

    schema_version: str
    strategy: Literal["platform_controlled", "sdk_native", "sdk_restricted"]
    trust_basis: str
    builtin_tool_identities: list[str]
    workspace_contract: str
    command_isolation: str


@dataclass(frozen=True)
class SdkSkillToolAdmission:
    """Complete SDK tool authority for one server-selected execution profile."""

    tool_names: tuple[str, ...]
    command_isolation: str


class SkillExecutionProfileError(ValueError):
    """Raised when a pinned execution profile differs from server authority."""

    pass


def sdk_skill_tool_admission_for_execution_profile(
    *,
    execution_profile: object,
    selected_skill_id: object,
    staged_skill_ids: object,
    authorized_skill_ids: object,
) -> SdkSkillToolAdmission | None:
    """Return the exact sandbox SDK tool admission for one selected Skill."""

    if (
        execution_profile != OPEN_SANDBOX_GOVERNED_SDK_EXECUTION_PROFILE
        or not isinstance(selected_skill_id, str)
        or not selected_skill_id
        or not isinstance(staged_skill_ids, list | tuple | set | frozenset)
        or not isinstance(authorized_skill_ids, list | tuple | set | frozenset)
    ):
        return None
    staged = {item for item in staged_skill_ids if isinstance(item, str)}
    authorized = {item for item in authorized_skill_ids if isinstance(item, str)}
    if selected_skill_id not in staged or selected_skill_id not in authorized:
        return None
    return SdkSkillToolAdmission(
        tool_names=_OPEN_SANDBOX_SDK_SKILL_FILE_TOOLS,
        command_isolation=OPEN_SANDBOX_GOVERNED_COMMAND_ISOLATION,
    )


def sdk_skill_file_tools_for_execution_profile(
    *,
    execution_profile: object,
    selected_skill_id: object,
    staged_skill_ids: object,
    authorized_skill_ids: object,
) -> tuple[str, ...]:
    """Return the internal-beta SDK file tools for one exact staged Skill."""

    admission = sdk_skill_tool_admission_for_execution_profile(
        execution_profile=execution_profile,
        selected_skill_id=selected_skill_id,
        staged_skill_ids=staged_skill_ids,
        authorized_skill_ids=authorized_skill_ids,
    )
    return admission.tool_names if admission is not None else ()


def _known_tool_identities(values: tuple[str, ...]) -> list[str]:
    return [identity for identity in values if identity in BUILTIN_TOOL_IDENTITIES]


def _builtin_execution_profile(skill_id: str, identities: list[str]) -> SkillExecutionProfile:
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


def resolve_skill_execution_profile(
    *,
    skill_id: str,
    source_kind: str,
    lifecycle_status: str,
) -> SkillExecutionProfile:
    """Resolve the server-owned runtime strategy for one immutable Skill version."""

    normalized_status = normalize_skill_version_status(lifecycle_status)
    if (
        source_kind == "builtin"
        and skill_id != LEGACY_SYNTHETIC_CHAT_SKILL_ID
        and normalized_status in _TRUSTED_BUILTIN_STATUSES
    ):
        identities = _known_tool_identities(
            _EXPLICIT_SKILL_BASH_IDENTITY
            + _SERVER_BUILTIN_NON_BASH_TOOL_DECLARATIONS.get(skill_id, ())
        )
        return _builtin_execution_profile(skill_id, identities)
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
        skill_id = str(manifest.get("skill_id") or "")
        legacy_declarations = (
            _EXPLICIT_SKILL_BASH_IDENTITY
            + _SERVER_BUILTIN_NON_BASH_TOOL_DECLARATIONS.get(skill_id, ())
            if skill_id in _SERVER_BUILTIN_NON_BASH_TOOL_DECLARATIONS
            else ()
        )
        return _builtin_execution_profile(skill_id, _known_tool_identities(legacy_declarations))
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
