from __future__ import annotations

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

SDK_NATIVE = "sdk_native"
SDK_RESTRICTED = "sdk_restricted"
SANDBOX_FULL_LOCAL = "sandbox_full_local"

NATIVE_COMMAND_ISOLATION = "sibling-tool-sandbox-v1"
SANDBOX_BOUNDARY_COMMAND_ISOLATION = "real-sandbox-boundary-v1"
OPEN_SANDBOX_GOVERNED_COMMAND_ISOLATION = "opensandbox-workspace-v1"

_EXPLICIT_SKILL_BASH_IDENTITY = ("Bash",)
_SERVER_BUILTIN_NON_BASH_TOOL_DECLARATIONS = {
    "ctd-32s73-stability-template-fill": ("Write",),
    "minimax-docx": ("Write",),
}
_NATIVE_UPLOADED_TOOL_IDENTITIES = (
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
    # ``platform_controlled`` is retained only to decode immutable historical
    # v1 snapshots; new profiles never resolve to it.
    strategy: Literal["platform_controlled", "sdk_native", "sdk_restricted"]
    trust_basis: str
    builtin_tool_identities: list[str]
    workspace_contract: str
    command_isolation: str


class EffectiveSkillExecutionProfile(TypedDict):
    """Runtime strategy derived from one validated immutable Skill profile."""

    strategy: Literal["sandbox_full_local", "sdk_restricted"]
    trust_basis: str
    workspace_contract: str
    command_isolation: str


class SkillExecutionProfileError(ValueError):
    """Raised when a pinned execution profile differs from server authority."""

    pass


def _known_tool_identities(values: tuple[str, ...]) -> list[str]:
    return [identity for identity in values if identity in BUILTIN_TOOL_IDENTITIES]


def _builtin_execution_profile(identities: list[str]) -> SkillExecutionProfile:
    return {
        "schema_version": SKILL_EXECUTION_PROFILE_SCHEMA_VERSION,
        "strategy": SDK_NATIVE if identities else SDK_RESTRICTED,
        "trust_basis": "repository_builtin",
        "builtin_tool_identities": identities,
        "workspace_contract": SKILL_WORKSPACE_CONTRACT_VERSION,
        "command_isolation": (
            NATIVE_COMMAND_ISOLATION if "Bash" in identities else "none"
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
        return _builtin_execution_profile(identities)
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
        return _builtin_execution_profile(_known_tool_identities(legacy_declarations))
    return resolve_skill_execution_profile(
        skill_id=str(manifest.get("skill_id") or ""),
        source_kind=source_kind,
        lifecycle_status="draft",
    )


def canonical_skill_execution_profile(manifest: dict[str, Any]) -> SkillExecutionProfile:
    """Validate immutable metadata, including one historical profile for decoding."""

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
        source_kind = str(source.get("kind") or "")
        is_historical_controlled_profile = (
            str(manifest.get("skill_id") or "") == "qa-file-reviewer"
            and source_kind == "builtin"
            and normalized
            == {
                "schema_version": SKILL_EXECUTION_PROFILE_SCHEMA_VERSION,
                "strategy": "platform_controlled",
                "trust_basis": "repository_builtin",
                "builtin_tool_identities": ["Bash", "Write"],
                "workspace_contract": SKILL_WORKSPACE_CONTRACT_VERSION,
                "command_isolation": "minimal-environment-v1",
            }
        )
        # Snapshot JSON is immutable. Postgres uses this decoder when projecting
        # old run provenance; the worker's effective profile maps trusted builtin
        # metadata to the current sandbox strategy below.
        if not is_historical_controlled_profile:
            raise SkillExecutionProfileError("run_skill_snapshot_execution_profile_mismatch")
        return normalized  # type: ignore[return-value]
    return expected


def effective_skill_execution_profile(
    manifest: dict[str, Any],
) -> EffectiveSkillExecutionProfile:
    """Translate immutable v1 metadata into its governed runtime strategy."""

    persisted = canonical_skill_execution_profile(manifest)
    source = manifest.get("source") if isinstance(manifest.get("source"), dict) else {}
    source_kind = str(source.get("kind") or "")
    skill_id = str(manifest.get("skill_id") or "")
    lifecycle_status = normalize_skill_version_status(
        str(manifest.get("lifecycle_status") or "")
    )
    trusted_builtin = (
        source_kind == "builtin"
        and skill_id != LEGACY_SYNTHETIC_CHAT_SKILL_ID
        and lifecycle_status in _TRUSTED_BUILTIN_STATUSES
    )
    trusted_uploaded = (
        source_kind == "uploaded"
        and lifecycle_status in _TRUSTED_UPLOADED_STATUSES
    )
    if trusted_builtin or trusted_uploaded:
        return {
            "strategy": SANDBOX_FULL_LOCAL,
            "trust_basis": persisted["trust_basis"],
            "workspace_contract": persisted["workspace_contract"],
            "command_isolation": SANDBOX_BOUNDARY_COMMAND_ISOLATION,
        }
    return {
        "strategy": SDK_RESTRICTED,
        "trust_basis": persisted["trust_basis"],
        "workspace_contract": persisted["workspace_contract"],
        "command_isolation": "none",
    }
