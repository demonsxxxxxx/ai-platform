"""Unwired XLSX admission contract with explicit authority seams.

No production caller invokes this module at this revision.  The contract is a
reviewable integration target: bytes are classified as XLSX, Agent ACL facts
come from an ACL-aware port, Skill facts come from an authorization port, and
runtime/workspace facts come from a server-observation port.  Request DTOs carry
only lookup scope and untrusted locators; they cannot attest runtime inventory,
Agent declarations, selected Skills, or image/workspace state.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from typing import Literal, Protocol, TypeAlias

from app.attachments.classification import (
    ATTACHMENT_CLASSIFICATION_REJECTION_CODES,
    AttachmentBytesForClassification,
    _ClassifiedAttachment,
    _classify_attachment,
)
from app.attachments.file_capabilities import (
    CAPABILITY_AGENT_INPUT,
    FILE_CAPABILITY_AGENT_BINDING_REQUIRED,
    FILE_CAPABILITY_AGENT_PROFILE_INCOMPATIBLE,
    FILE_CAPABILITY_CALLER_SELECTION_INCOMPATIBLE,
    FILE_CAPABILITY_COMBINATION_UNSUPPORTED,
    FILE_CAPABILITY_CONTRACT_SCOPE,
    FILE_CAPABILITY_ENFORCEMENT_STATE,
    FILE_CAPABILITY_INTENT_AMBIGUOUS,
    FILE_CAPABILITY_NOT_AUTHORIZED,
    FILE_CAPABILITY_PARSER_AMBIGUOUS,
    FILE_CAPABILITY_PARSER_UNAVAILABLE,
    FILE_CAPABILITY_REGISTRY,
    FILE_CAPABILITY_REGISTRY_POLICY_DIGEST,
    FILE_CAPABILITY_REGISTRY_VERSION,
    FILE_CAPABILITY_REJECTION_CODES,
    FILE_CAPABILITY_REQUIRED_ARTIFACT_INCOMPATIBLE,
    FILE_CAPABILITY_RUNTIME_BINDING_INVALID,
    FILE_CAPABILITY_RUNTIME_BINDING_UNAVAILABLE,
    FILE_CAPABILITY_RUNTIME_DEPENDENCY_UNAVAILABLE,
    FILE_CAPABILITY_RUNTIME_NOT_AUTHORIZED,
    FILE_CAPABILITY_RUNTIME_REVISION_STALE,
    FILE_CAPABILITY_RUNTIME_SCOPE_MISMATCH,
    FILE_CAPABILITY_SIZE_EXCEEDED,
    FILE_CAPABILITY_SKILL_AMBIGUOUS,
    FILE_CAPABILITY_SKILL_SCOPE_MISMATCH,
    FILE_CAPABILITY_SKILL_UNAVAILABLE,
    FILE_CAPABILITY_TYPE_UNSUPPORTED,
    FILE_CAPABILITY_VERSION_STALE,
    FILE_CAPABILITY_WORKSPACE_SKILL_MISMATCH,
    AgentFileAuthorizationPort,
    FileCapabilityProfile,
    ParserIdentity as ParserIdentity,
    RuntimeDependencyKind as RuntimeDependencyKind,
    RuntimeDependencyRequirement,
    ServerAuthorizedAgentFileBinding,
    VerifiedFileIdentity,
    _check_file_capabilities,
    _resolve_authorized_agent_file_binding,
    authorization_scope_sha256,
    matching_file_capability_profiles,
    registry_policy_digest as _registry_policy_digest,
)
from app.auth import AuthPrincipal
from app.projection_redaction import public_skill_display_label
from app.validation import assert_safe_id, assert_safe_principal_user_id


ADMISSION_NOT_APPLICABLE = "not_applicable"
ADMISSION_REQUIRED = "required"
ADMISSION_REJECTED = "rejected"
AdmissionState: TypeAlias = Literal["not_applicable", "required", "rejected"]
FileTaskIntent: TypeAlias = Literal[
    "analyze",
    "extract",
    "review",
    "transform",
    "generate_artifact",
    "non_execution",
    "unspecified",
]
AuthorizationStatus: TypeAlias = Literal[
    "authorized", "ambiguous", "unavailable", "unauthorized", "stale"
]
RuntimeAuthorizationStatus: TypeAlias = Literal[
    "authorized", "unavailable", "unauthorized", "stale"
]
RUNTIME_OBSERVATION_SCHEMA_VERSION = "ai-platform.runtime-observation.v1"
SKILL_AUTHORIZATION_SCHEMA_VERSION = "ai-platform.skill-authorization.v1"
ADMISSION_FINGERPRINT_SCHEMA_VERSION = "ai-platform.file-admission-fingerprint.v1"
SERVER_RUNTIME_OBSERVATION = "server_runtime_observation"
SERVER_RUNTIME_OBSERVATION_SOURCES = frozenset(
    {"runtime_lease_projection", "executor_probe"}
)
SERVER_SKILL_AUTHORIZATION_SOURCES = frozenset(
    {"published_skill_authority", "agent_skill_authority"}
)
_EXECUTION_INTENTS = frozenset(
    {"analyze", "extract", "review", "transform", "generate_artifact"}
)
_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_HASH = re.compile(r"^[0-9a-f]{64}$")
FILE_CAPABILITY_ADMISSION_REJECTION_CODES = frozenset(
    FILE_CAPABILITY_REJECTION_CODES | ATTACHMENT_CLASSIFICATION_REJECTION_CODES
)


def registry_policy_digest(registry: tuple[FileCapabilityProfile, ...]) -> str:
    """Return the policy-only registry digest."""

    return _registry_policy_digest(registry)


def registry_digest(registry: tuple[FileCapabilityProfile, ...]) -> str:
    """Compatibility alias; this is policy-only, not admission integrity."""

    return registry_policy_digest(registry)


def _valid_label(value: object) -> str | None:
    return public_skill_display_label(value)


def _version_satisfies(actual: str, minimum: str | None) -> bool:
    if minimum is None:
        return bool(actual)
    try:
        current = tuple(int(part) for part in actual.split("."))
        required = tuple(int(part) for part in minimum.split("."))
    except ValueError:
        return actual == minimum
    width = max(len(current), len(required))
    return current + (0,) * (width - len(current)) >= required + (0,) * (
        width - len(required)
    )


@dataclass(frozen=True, slots=True)
class RuntimeDependencyIdentity:
    """One dependency observed by a trusted runtime adapter."""

    kind: Literal["prebuilt_python", "node_npm"]
    dependency_id: str
    version: str

    def __post_init__(self) -> None:
        if (
            not isinstance(self.kind, str)
            or self.kind not in {"prebuilt_python", "node_npm"}
            or not isinstance(self.dependency_id, str)
            or not _TOKEN.fullmatch(self.dependency_id)
            or not isinstance(self.version, str)
            or not _TOKEN.fullmatch(self.version)
        ):
            raise ValueError("runtime dependency identity is invalid")


@dataclass(frozen=True, slots=True)
class WorkspaceSkillPin:
    """Exact immutable Skill distribution expected in one workspace."""

    skill_id: str
    expected_version: str
    content_hash: str

    def __post_init__(self) -> None:
        assert_safe_id(self.skill_id, "skill_id")
        assert_safe_id(self.expected_version, "expected_version")
        if not isinstance(self.content_hash, str) or not _HASH.fullmatch(
            self.content_hash
        ):
            raise ValueError("workspace Skill content_hash is invalid")


@dataclass(frozen=True, slots=True)
class RuntimeImageInventory:
    """Runtime facts accepted only inside an authorized port resolution."""

    image_digest: str
    python_version: str
    runs_as_non_root: bool
    dependencies: tuple[RuntimeDependencyIdentity, ...]
    workspace_skills: tuple[WorkspaceSkillPin, ...]
    artifact_types: frozenset[str]
    node_version: str | None
    npm_source_install_allowed: bool
    public_package_registry_egress: bool

    def __post_init__(self) -> None:
        object.__setattr__(self, "dependencies", tuple(self.dependencies))
        object.__setattr__(self, "workspace_skills", tuple(self.workspace_skills))
        object.__setattr__(self, "artifact_types", frozenset(self.artifact_types))
        if (
            not isinstance(self.image_digest, str)
            or not _DIGEST.fullmatch(self.image_digest)
            or not isinstance(self.python_version, str)
            or not re.fullmatch(r"[0-9]+(?:\.[0-9]+){1,3}", self.python_version)
            or (
                self.node_version is not None
                and (
                    not isinstance(self.node_version, str)
                    or not re.fullmatch(
                        r"[0-9]+(?:\.[0-9]+){1,3}", self.node_version
                    )
                )
            )
        ):
            raise ValueError("runtime image inventory is invalid")
        if not all(
            isinstance(item, RuntimeDependencyIdentity) for item in self.dependencies
        ) or not all(
            isinstance(item, WorkspaceSkillPin) for item in self.workspace_skills
        ):
            raise ValueError("runtime inventory facts are invalid")
        if any(
            not isinstance(item, str) or not _TOKEN.fullmatch(item)
            for item in self.artifact_types
        ):
            raise ValueError("runtime artifact types are invalid")
        if (
            type(self.runs_as_non_root) is not bool
            or type(self.npm_source_install_allowed) is not bool
            or type(self.public_package_registry_egress) is not bool
        ):
            raise ValueError("runtime execution policy is invalid")

    def missing_requirements(
        self, requirements: tuple[RuntimeDependencyRequirement, ...]
    ) -> tuple[RuntimeDependencyRequirement, ...]:
        missing: list[RuntimeDependencyRequirement] = []
        for requirement in requirements:
            if requirement.kind == "python_runtime":
                present = (
                    requirement.dependency_id == "python"
                    and _version_satisfies(
                        self.python_version, requirement.minimum_version
                    )
                    and (not requirement.require_non_root or self.runs_as_non_root)
                )
            else:
                present = (
                    requirement.kind != "node_npm" or self.node_version is not None
                )
                present = present and any(
                    dependency.kind == requirement.kind
                    and dependency.dependency_id == requirement.dependency_id
                    and _version_satisfies(
                        dependency.version, requirement.minimum_version
                    )
                    for dependency in self.dependencies
                )
            if not present:
                missing.append(requirement)
        return tuple(missing)

    def has_workspace_skill(self, pin: WorkspaceSkillPin) -> bool:
        return pin in self.workspace_skills


@dataclass(frozen=True, slots=True)
class RuntimeInventoryResolution:
    """Scoped runtime observation returned only by a server authority port."""

    status: RuntimeAuthorizationStatus
    tenant_id: str | None = None
    principal_user_id: str | None = None
    workspace_id: str | None = None
    run_id: str | None = None
    attempt_id: str | None = None
    authorization_scope_sha256: str | None = None
    authority_source: str | None = None
    inventory: RuntimeImageInventory | None = None

    def __post_init__(self) -> None:
        private = (
            self.tenant_id,
            self.principal_user_id,
            self.workspace_id,
            self.run_id,
            self.attempt_id,
            self.authorization_scope_sha256,
            self.authority_source,
            self.inventory,
        )
        if self.status == "authorized":
            try:
                assert_safe_id(self.tenant_id or "", "tenant_id")
                assert_safe_principal_user_id(self.principal_user_id or "")
                assert_safe_id(self.workspace_id or "", "workspace_id")
                assert_safe_id(self.run_id or "", "run_id")
                assert_safe_id(self.attempt_id or "", "attempt_id")
            except (TypeError, ValueError) as exc:
                raise ValueError("runtime resolution is incomplete") from exc
            if (
                not isinstance(self.authorization_scope_sha256, str)
                or not _HASH.fullmatch(self.authorization_scope_sha256)
                or self.authority_source not in SERVER_RUNTIME_OBSERVATION_SOURCES
                or not isinstance(self.inventory, RuntimeImageInventory)
            ):
                raise ValueError("runtime resolution is incomplete")
            return
        if self.status not in {"unavailable", "unauthorized", "stale"}:
            raise ValueError("runtime authorization status is invalid")
        if any(item is not None for item in private):
            raise ValueError("rejected runtime resolution contains private facts")


class RuntimeInventoryPort(Protocol):
    """Server runtime lease/probe authority seam for a future adapter."""

    async def resolve_authorized_inventory(
        self,
        *,
        principal: AuthPrincipal,
        workspace_id: str,
        run_id: str,
        attempt_id: str,
        expected_workspace_skill: WorkspaceSkillPin,
    ) -> RuntimeInventoryResolution:
        """Authorize scope and observe runtime/workspace facts server-side."""


@dataclass(frozen=True, slots=True)
class _ServerAuthorizedRuntimeBinding:
    tenant_id: str
    principal_user_id: str
    workspace_id: str
    run_id: str
    attempt_id: str
    authorization_scope_sha256: str
    authority_source: str
    expected_workspace_skill: WorkspaceSkillPin
    inventory: RuntimeImageInventory
    observation_sha256: str
    authority: Literal["server_runtime_observation"] = SERVER_RUNTIME_OBSERVATION

    def __post_init__(self) -> None:
        if (
            self.authority != SERVER_RUNTIME_OBSERVATION
            or not isinstance(self.expected_workspace_skill, WorkspaceSkillPin)
            or not isinstance(self.inventory, RuntimeImageInventory)
            or not isinstance(self.observation_sha256, str)
            or not _HASH.fullmatch(self.observation_sha256)
        ):
            raise ValueError("server runtime binding is invalid")
        resolution = RuntimeInventoryResolution(
            status="authorized",
            tenant_id=self.tenant_id,
            principal_user_id=self.principal_user_id,
            workspace_id=self.workspace_id,
            run_id=self.run_id,
            attempt_id=self.attempt_id,
            authorization_scope_sha256=self.authorization_scope_sha256,
            authority_source=self.authority_source,
            inventory=self.inventory,
        )
        if self.observation_sha256 != _runtime_observation_sha256(
            resolution, self.expected_workspace_skill
        ):
            raise ValueError("server runtime binding is invalid")


def _runtime_inventory_descriptor(inventory: RuntimeImageInventory) -> dict[str, object]:
    return {
        "image_digest": inventory.image_digest,
        "python_version": inventory.python_version,
        "runs_as_non_root": inventory.runs_as_non_root,
        "dependencies": [asdict(item) for item in inventory.dependencies],
        "workspace_skills": [asdict(item) for item in inventory.workspace_skills],
        "artifact_types": sorted(inventory.artifact_types),
        "node_version": inventory.node_version,
        "npm_source_install_allowed": inventory.npm_source_install_allowed,
        "public_package_registry_egress": inventory.public_package_registry_egress,
    }


def _runtime_observation_sha256(
    resolution: RuntimeInventoryResolution,
    expected_workspace_skill: WorkspaceSkillPin,
) -> str:
    assert resolution.inventory is not None
    descriptor = {
        "schema": RUNTIME_OBSERVATION_SCHEMA_VERSION,
        "authority": SERVER_RUNTIME_OBSERVATION,
        "tenant_id": resolution.tenant_id,
        "principal_user_id": resolution.principal_user_id,
        "workspace_id": resolution.workspace_id,
        "run_id": resolution.run_id,
        "attempt_id": resolution.attempt_id,
        "authorization_scope_sha256": resolution.authorization_scope_sha256,
        "authority_source": resolution.authority_source,
        "expected_workspace_skill": asdict(expected_workspace_skill),
        "inventory": _runtime_inventory_descriptor(resolution.inventory),
    }
    payload = json.dumps(
        descriptor,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(payload).hexdigest()


async def _resolve_authorized_runtime_binding(
    *,
    principal: AuthPrincipal,
    workspace_id: str,
    run_id: str,
    attempt_id: str,
    expected_workspace_skill: WorkspaceSkillPin,
    authorization_port: RuntimeInventoryPort,
) -> tuple[_ServerAuthorizedRuntimeBinding | None, str | None]:
    try:
        scope_sha256 = authorization_scope_sha256(principal, workspace_id)
        assert_safe_id(run_id, "run_id")
        assert_safe_id(attempt_id, "attempt_id")
    except (TypeError, ValueError):
        return None, FILE_CAPABILITY_RUNTIME_BINDING_INVALID
    try:
        resolution = await authorization_port.resolve_authorized_inventory(
            principal=principal,
            workspace_id=workspace_id,
            run_id=run_id,
            attempt_id=attempt_id,
            expected_workspace_skill=expected_workspace_skill,
        )
    except Exception:
        return None, FILE_CAPABILITY_RUNTIME_BINDING_UNAVAILABLE
    if not isinstance(resolution, RuntimeInventoryResolution):
        return None, FILE_CAPABILITY_RUNTIME_BINDING_INVALID
    if resolution.status != "authorized":
        return None, {
            "unavailable": FILE_CAPABILITY_RUNTIME_BINDING_UNAVAILABLE,
            "unauthorized": FILE_CAPABILITY_RUNTIME_NOT_AUTHORIZED,
            "stale": FILE_CAPABILITY_RUNTIME_REVISION_STALE,
        }[resolution.status]
    if (
        resolution.tenant_id != principal.tenant_id
        or resolution.principal_user_id != principal.user_id
        or resolution.workspace_id != workspace_id
        or resolution.run_id != run_id
        or resolution.attempt_id != attempt_id
        or resolution.authorization_scope_sha256 != scope_sha256
    ):
        return None, FILE_CAPABILITY_RUNTIME_SCOPE_MISMATCH
    assert resolution.inventory is not None
    return (
        _ServerAuthorizedRuntimeBinding(
            tenant_id=principal.tenant_id,
            principal_user_id=principal.user_id,
            workspace_id=workspace_id,
            run_id=run_id,
            attempt_id=attempt_id,
            authorization_scope_sha256=scope_sha256,
            authority_source=resolution.authority_source or "",
            expected_workspace_skill=expected_workspace_skill,
            inventory=resolution.inventory,
            observation_sha256=_runtime_observation_sha256(
                resolution, expected_workspace_skill
            ),
        ),
        None,
    )


@dataclass(frozen=True, slots=True)
class SkillSelection:
    skill_id: str
    expected_version: str

    def __post_init__(self) -> None:
        assert_safe_id(self.skill_id, "skill_id")
        assert_safe_id(self.expected_version, "expected_version")


@dataclass(frozen=True, slots=True)
class AgentSkillBinding:
    """Untrusted Agent revision locator; never an authorization fact."""

    agent_id: str
    expected_revision: int
    expected_profile_sha256: str

    def __post_init__(self) -> None:
        assert_safe_id(self.agent_id, "agent_id")
        if (
            type(self.expected_revision) is not int
            or self.expected_revision < 0
            or not isinstance(self.expected_profile_sha256, str)
            or not _HASH.fullmatch(self.expected_profile_sha256)
        ):
            raise ValueError("Agent revision locator is invalid")


@dataclass(frozen=True, slots=True)
class AuthorizedSkillPin:
    logical_skill_id: str
    skill_id: str
    expected_version: str
    manifest_sha256: str
    public_label: str

    def __post_init__(self) -> None:
        for value, field_name in (
            (self.logical_skill_id, "logical_skill_id"),
            (self.skill_id, "skill_id"),
            (self.expected_version, "expected_version"),
        ):
            assert_safe_id(value, field_name)
        label = _valid_label(self.public_label)
        if (
            not isinstance(self.manifest_sha256, str)
            or not _HASH.fullmatch(self.manifest_sha256)
            or label is None
            or label.casefold()
            in {self.logical_skill_id.casefold(), self.skill_id.casefold()}
        ):
            raise ValueError("authorized Skill pin is invalid")

    def workspace_pin(self) -> WorkspaceSkillPin:
        return WorkspaceSkillPin(
            self.skill_id, self.expected_version, self.manifest_sha256
        )


@dataclass(frozen=True, slots=True)
class SkillAuthorizationResolution:
    status: AuthorizationStatus
    pins: tuple[AuthorizedSkillPin, ...] = ()
    tenant_id: str | None = None
    principal_user_id: str | None = None
    workspace_id: str | None = None
    authorization_scope_sha256: str | None = None
    authority_source: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "pins", tuple(self.pins))
        if not all(isinstance(pin, AuthorizedSkillPin) for pin in self.pins):
            raise ValueError("authorization resolution is invalid")
        private_scalars = (
            self.tenant_id,
            self.principal_user_id,
            self.workspace_id,
            self.authorization_scope_sha256,
            self.authority_source,
        )
        if self.status == "authorized":
            try:
                assert_safe_id(self.tenant_id or "", "tenant_id")
                assert_safe_principal_user_id(self.principal_user_id or "")
                assert_safe_id(self.workspace_id or "", "workspace_id")
            except (TypeError, ValueError) as exc:
                raise ValueError("authorized Skill resolution is incomplete") from exc
            if (
                not isinstance(self.authorization_scope_sha256, str)
                or not _HASH.fullmatch(self.authorization_scope_sha256)
                or self.authority_source not in SERVER_SKILL_AUTHORIZATION_SOURCES
            ):
                raise ValueError("authorized Skill resolution is incomplete")
            return
        if self.status not in {
            "ambiguous",
            "unavailable",
            "unauthorized",
            "stale",
        }:
            raise ValueError("authorization resolution is invalid")
        if self.pins or any(item is not None for item in private_scalars):
            raise ValueError("rejected Skill resolution contains private facts")


def _skill_authorization_sha256(
    resolution: SkillAuthorizationResolution,
) -> str:
    if resolution.status != "authorized":
        raise ValueError("Skill authorization proof requires an authorized resolution")
    descriptor = {
        "schema": SKILL_AUTHORIZATION_SCHEMA_VERSION,
        "tenant_id": resolution.tenant_id,
        "principal_user_id": resolution.principal_user_id,
        "workspace_id": resolution.workspace_id,
        "authorization_scope_sha256": resolution.authorization_scope_sha256,
        "authority_source": resolution.authority_source,
        "pins": [asdict(pin) for pin in resolution.pins],
    }
    payload = json.dumps(
        descriptor,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(payload).hexdigest()


class SkillAuthorizationPort(Protocol):
    """Principal/workspace-aware immutable Skill authority seam."""

    async def resolve_exactly_one(
        self,
        *,
        principal: AuthPrincipal,
        workspace_id: str,
        logical_skill_ids: tuple[str, ...],
        selection: SkillSelection | None,
        binding: ServerAuthorizedAgentFileBinding | None,
    ) -> SkillAuthorizationResolution:
        """Authorize exactly one immutable Skill in the current scope."""


@dataclass(frozen=True, slots=True)
class FileCapabilityAdmissionRequest:
    """Bytes plus lookup scope; contains no runtime or Agent authority facts."""

    attachments: tuple[AttachmentBytesForClassification, ...]
    task_intent: FileTaskIntent
    principal: AuthPrincipal
    workspace_id: str
    run_id: str
    attempt_id: str
    explicit_selection: SkillSelection | None = None
    agent_binding: AgentSkillBinding | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "attachments", tuple(self.attachments))
        if not all(
            isinstance(item, AttachmentBytesForClassification)
            for item in self.attachments
        ):
            raise ValueError("attachments must be AttachmentBytesForClassification")
        try:
            authorization_scope_sha256(self.principal, self.workspace_id)
            assert_safe_id(self.run_id, "run_id")
            assert_safe_id(self.attempt_id, "attempt_id")
        except (TypeError, ValueError) as exc:
            raise ValueError("admission lookup scope is invalid") from exc
        if self.explicit_selection is not None and not isinstance(
            self.explicit_selection, SkillSelection
        ):
            raise ValueError("explicit_selection must be SkillSelection")
        if self.agent_binding is not None and not isinstance(
            self.agent_binding, AgentSkillBinding
        ):
            raise ValueError("agent_binding must be AgentSkillBinding")
        if self.task_intent not in _EXECUTION_INTENTS | {
            "non_execution",
            "unspecified",
        }:
            raise ValueError("task_intent is invalid")


@dataclass(frozen=True, slots=True)
class PrivateParserRequirement:
    file_id: str
    verified_media_type: str
    verified_extension: str
    expected_size_bytes: int
    expected_sha256: str
    parser_id: str
    parser_version: str
    max_bytes: int

    def __post_init__(self) -> None:
        try:
            assert_safe_id(self.file_id, "file_id")
        except (TypeError, ValueError) as exc:
            raise ValueError("private parser requirement is invalid") from exc
        if (
            self.verified_media_type != self.verified_media_type.strip().casefold()
            or not self.verified_media_type
            or self.verified_extension != self.verified_extension.strip().casefold()
            or not self.verified_extension.startswith(".")
            or type(self.expected_size_bytes) is not int
            or self.expected_size_bytes < 0
            or not isinstance(self.expected_sha256, str)
            or not _HASH.fullmatch(self.expected_sha256)
            or not isinstance(self.parser_id, str)
            or not _TOKEN.fullmatch(self.parser_id)
            or not isinstance(self.parser_version, str)
            or not _TOKEN.fullmatch(self.parser_version)
            or type(self.max_bytes) is not int
            or self.max_bytes < 1
        ):
            raise ValueError("private parser requirement is invalid")


@dataclass(frozen=True, slots=True)
class PublicProgressFact:
    kind: Literal["file_category", "parser", "skill"]
    stage: Literal["admission"]
    status: Literal["completed"]
    label: str
    current: int
    total: int

    def __post_init__(self) -> None:
        if (
            self.kind not in {"file_category", "parser", "skill"}
            or self.stage != "admission"
            or self.status != "completed"
            or _valid_label(self.label) is None
            or type(self.current) is not int
            or type(self.total) is not int
            or self.total < 1
            or not 0 <= self.current <= self.total
        ):
            raise ValueError("public progress fact is invalid")

    def to_public_payload(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "stage": self.stage,
            "status": self.status,
            "label": self.label,
            "progress": {"current": self.current, "total": self.total},
        }


@dataclass(frozen=True, slots=True)
class FileCapabilityAdmission:
    """Unwired contract result with policy digest and per-admission fingerprint."""

    state: AdmissionState
    fallback_prohibited: bool
    rejection_code: str | None
    registry_version: str
    registry_policy_digest: str
    selected_skill: SkillSelection | None = None
    skill_pins: tuple[AuthorizedSkillPin, ...] = ()
    workspace_skill_pin: WorkspaceSkillPin | None = None
    parser_requirements: tuple[PrivateParserRequirement, ...] = ()
    runtime_image_digest: str | None = None
    runtime_requirements: tuple[RuntimeDependencyRequirement, ...] = ()
    required_artifact_types: tuple[str, ...] = ()
    public_progress_facts: tuple[PublicProgressFact, ...] = ()
    admission_fingerprint: str | None = None
    contract_scope: str = FILE_CAPABILITY_CONTRACT_SCOPE
    enforcement_state: str = FILE_CAPABILITY_ENFORCEMENT_STATE

    def __post_init__(self) -> None:
        for name in (
            "skill_pins",
            "parser_requirements",
            "runtime_requirements",
            "required_artifact_types",
            "public_progress_facts",
        ):
            object.__setattr__(self, name, tuple(getattr(self, name)))
        private = (
            self.selected_skill,
            self.skill_pins,
            self.workspace_skill_pin,
            self.parser_requirements,
            self.runtime_image_digest,
            self.runtime_requirements,
            self.required_artifact_types,
            self.public_progress_facts,
            self.admission_fingerprint,
        )
        if (
            self.registry_version != FILE_CAPABILITY_REGISTRY_VERSION
            or self.registry_policy_digest
            != FILE_CAPABILITY_REGISTRY_POLICY_DIGEST
            or self.contract_scope != FILE_CAPABILITY_CONTRACT_SCOPE
            or self.enforcement_state != FILE_CAPABILITY_ENFORCEMENT_STATE
        ):
            raise ValueError("admission state violates its invariant")
        if (
            self.state == ADMISSION_NOT_APPLICABLE
            and self.fallback_prohibited is False
            and self.rejection_code is None
            and not any(private)
        ):
            return
        if (
            self.state == ADMISSION_REJECTED
            and self.fallback_prohibited is True
            and self.rejection_code in FILE_CAPABILITY_ADMISSION_REJECTION_CODES
            and not any(private)
        ):
            return
        if (
            self.state == ADMISSION_REQUIRED
            and self.fallback_prohibited is True
            and self.rejection_code is None
            and isinstance(self.selected_skill, SkillSelection)
            and len(self.skill_pins) == 1
            and isinstance(self.skill_pins[0], AuthorizedSkillPin)
            and self.selected_skill
            == SkillSelection(
                self.skill_pins[0].skill_id,
                self.skill_pins[0].expected_version,
            )
            and isinstance(self.workspace_skill_pin, WorkspaceSkillPin)
            and self.workspace_skill_pin == self.skill_pins[0].workspace_pin()
            and self.parser_requirements
            and all(
                isinstance(item, PrivateParserRequirement)
                for item in self.parser_requirements
            )
            and isinstance(self.runtime_image_digest, str)
            and _DIGEST.fullmatch(self.runtime_image_digest)
            and all(
                isinstance(item, RuntimeDependencyRequirement)
                for item in self.runtime_requirements
            )
            and all(
                isinstance(item, str) and _TOKEN.fullmatch(item)
                for item in self.required_artifact_types
            )
            and all(
                isinstance(item, PublicProgressFact)
                for item in self.public_progress_facts
            )
            and isinstance(self.admission_fingerprint, str)
            and _DIGEST.fullmatch(self.admission_fingerprint)
        ):
            return
        raise ValueError("admission state violates its invariant")

    @property
    def registry_digest(self) -> str:
        """Compatibility projection; explicitly the policy-only digest."""

        return self.registry_policy_digest


def _admission_fingerprint(
    *,
    attachments: tuple[_ClassifiedAttachment, ...],
    task_intent: FileTaskIntent,
    agent_binding: ServerAuthorizedAgentFileBinding | None,
    skill_pin: AuthorizedSkillPin,
    skill_authorization_sha256: str,
    runtime_binding: _ServerAuthorizedRuntimeBinding,
    required_artifact_types: tuple[str, ...],
) -> str:
    descriptor = {
        "schema": ADMISSION_FINGERPRINT_SCHEMA_VERSION,
        "registry_policy_digest": FILE_CAPABILITY_REGISTRY_POLICY_DIGEST,
        "contract_scope": FILE_CAPABILITY_CONTRACT_SCOPE,
        "task_intent": task_intent,
        "attachments": [
            {
                "file_id": item.file_id,
                "media_type": item.media_type,
                "verified_extension": item.verified_extension,
                "size_bytes": item.size_bytes,
                "sha256": item.sha256,
                "classifier_version": item.classifier_version,
            }
            for item in attachments
        ],
        "agent_declaration_sha256": (
            agent_binding.declaration_sha256 if agent_binding else None
        ),
        "skill_pin": asdict(skill_pin),
        "skill_authorization_sha256": skill_authorization_sha256,
        "runtime_observation_sha256": runtime_binding.observation_sha256,
        "required_artifact_types": required_artifact_types,
    }
    payload = json.dumps(
        descriptor,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return "sha256:" + hashlib.sha256(payload).hexdigest()


async def admit_file_capability(
    request: FileCapabilityAdmissionRequest,
    *,
    authorization_port: SkillAuthorizationPort,
    runtime_authorization_port: RuntimeInventoryPort,
    agent_authorization_port: AgentFileAuthorizationPort | None = None,
) -> FileCapabilityAdmission:
    """Evaluate the unwired XLSX integration contract with server authority ports."""

    if not request.attachments:
        return _not_applicable()
    attachments: list[_ClassifiedAttachment] = []
    for source in request.attachments:
        attachment, classification = _classify_attachment(source)
        if attachment is None:
            return _rejected(
                classification.rejection_code
                or "attachment_classification_type_unsupported"
            )
        attachments.append(attachment)
    profile, rejection = _profile_for(attachments)
    if rejection:
        return _rejected(rejection)
    assert profile is not None
    if not profile.enabled:
        return _rejected(FILE_CAPABILITY_TYPE_UNSUPPORTED)
    if len(attachments) > 1 and not profile.homogeneous:
        return _rejected(FILE_CAPABILITY_COMBINATION_UNSUPPORTED)
    if request.task_intent == "non_execution":
        return _not_applicable()
    artifacts = dict(profile.operations).get(request.task_intent)
    if artifacts is None:
        return _rejected(FILE_CAPABILITY_INTENT_AMBIGUOUS)
    if profile.parser is None or any(
        item.size_bytes > profile.parser.max_bytes for item in attachments
    ):
        return _rejected(
            FILE_CAPABILITY_PARSER_UNAVAILABLE
            if profile.parser is None
            else FILE_CAPABILITY_SIZE_EXCEEDED
        )
    authorized_agent_binding: ServerAuthorizedAgentFileBinding | None = None
    if request.agent_binding is not None:
        if agent_authorization_port is None:
            return _rejected(FILE_CAPABILITY_AGENT_BINDING_REQUIRED)
        authorized_agent_binding, rejection = (
            await _resolve_authorized_agent_file_binding(
                principal=request.principal,
                workspace_id=request.workspace_id,
                agent_id=request.agent_binding.agent_id,
                expected_revision=request.agent_binding.expected_revision,
                expected_profile_sha256=(
                    request.agent_binding.expected_profile_sha256
                ),
                authorization_port=agent_authorization_port,
            )
        )
        if rejection:
            return _rejected(rejection)
        assert authorized_agent_binding is not None
        agent_decision = _check_file_capabilities(
            tuple(
                VerifiedFileIdentity(item.media_type, item.verified_extension)
                for item in attachments
            ),
            capability=CAPABILITY_AGENT_INPUT,
            agent_binding=authorized_agent_binding,
        )
        if agent_decision.rejection_code:
            return _rejected(agent_decision.rejection_code)
    selection, rejection = _selection(profile, request, authorized_agent_binding)
    if rejection:
        return _rejected(rejection)
    try:
        resolution = await authorization_port.resolve_exactly_one(
            principal=request.principal,
            workspace_id=request.workspace_id,
            logical_skill_ids=(profile.logical_skill_id or "",),
            selection=selection,
            binding=authorized_agent_binding,
        )
    except Exception:
        return _rejected(FILE_CAPABILITY_SKILL_UNAVAILABLE)
    if not isinstance(resolution, SkillAuthorizationResolution):
        return _rejected(FILE_CAPABILITY_SKILL_UNAVAILABLE)
    rejection = _authorization_rejection(resolution)
    if rejection:
        return _rejected(rejection)
    expected_scope_sha256 = authorization_scope_sha256(
        request.principal, request.workspace_id
    )
    if (
        resolution.tenant_id != request.principal.tenant_id
        or resolution.principal_user_id != request.principal.user_id
        or resolution.workspace_id != request.workspace_id
        or resolution.authorization_scope_sha256 != expected_scope_sha256
    ):
        return _rejected(FILE_CAPABILITY_SKILL_SCOPE_MISMATCH)
    pin = resolution.pins[0]
    rejection = _pin_rejection(pin, profile, selection, authorized_agent_binding)
    if rejection:
        return _rejected(rejection)
    workspace_pin = pin.workspace_pin()
    runtime_binding, rejection = await _resolve_authorized_runtime_binding(
        principal=request.principal,
        workspace_id=request.workspace_id,
        run_id=request.run_id,
        attempt_id=request.attempt_id,
        expected_workspace_skill=workspace_pin,
        authorization_port=runtime_authorization_port,
    )
    if rejection:
        return _rejected(rejection)
    assert runtime_binding is not None
    inventory = runtime_binding.inventory
    if inventory.missing_requirements(profile.runtime_requirements):
        return _rejected(FILE_CAPABILITY_RUNTIME_DEPENDENCY_UNAVAILABLE)
    if not set(artifacts) <= inventory.artifact_types:
        return _rejected(FILE_CAPABILITY_REQUIRED_ARTIFACT_INCOMPATIBLE)
    if not inventory.has_workspace_skill(workspace_pin):
        return _rejected(FILE_CAPABILITY_WORKSPACE_SKILL_MISMATCH)
    classified = tuple(attachments)
    return FileCapabilityAdmission(
        ADMISSION_REQUIRED,
        True,
        None,
        FILE_CAPABILITY_REGISTRY_VERSION,
        FILE_CAPABILITY_REGISTRY_POLICY_DIGEST,
        SkillSelection(pin.skill_id, pin.expected_version),
        (pin,),
        workspace_pin,
        tuple(
            PrivateParserRequirement(
                item.file_id,
                item.media_type,
                item.verified_extension,
                item.size_bytes,
                item.sha256,
                profile.parser.parser_id,
                profile.parser.parser_version,
                profile.parser.max_bytes,
            )
            for item in classified
        ),
        inventory.image_digest,
        profile.runtime_requirements,
        artifacts,
        _public_facts(profile, pin, len(classified)),
        _admission_fingerprint(
            attachments=classified,
            task_intent=request.task_intent,
            agent_binding=authorized_agent_binding,
            skill_pin=pin,
            skill_authorization_sha256=_skill_authorization_sha256(resolution),
            runtime_binding=runtime_binding,
            required_artifact_types=artifacts,
        ),
    )


def _profile_for(
    attachments: list[_ClassifiedAttachment],
) -> tuple[FileCapabilityProfile | None, str | None]:
    profiles: list[FileCapabilityProfile] = []
    for attachment in attachments:
        matches = matching_file_capability_profiles(
            VerifiedFileIdentity(
                attachment.media_type, attachment.verified_extension
            ),
            registry=FILE_CAPABILITY_REGISTRY,
        )
        if len(matches) != 1:
            return (
                None,
                FILE_CAPABILITY_PARSER_AMBIGUOUS
                if matches
                else FILE_CAPABILITY_TYPE_UNSUPPORTED,
            )
        profiles.append(matches[0])
    return (
        (profiles[0], None)
        if all(profile == profiles[0] for profile in profiles)
        else (None, FILE_CAPABILITY_COMBINATION_UNSUPPORTED)
    )


def _selection(
    profile: FileCapabilityProfile,
    request: FileCapabilityAdmissionRequest,
    binding: ServerAuthorizedAgentFileBinding | None,
) -> tuple[SkillSelection | None, str | None]:
    if binding:
        bound = SkillSelection(
            binding.selected_skill_id, binding.selected_skill_version
        )
        if (
            bound.skill_id != profile.logical_skill_id
            or request.explicit_selection not in {None, bound}
        ):
            return None, FILE_CAPABILITY_AGENT_PROFILE_INCOMPATIBLE
        return bound, None
    if (
        request.explicit_selection
        and request.explicit_selection.skill_id != profile.logical_skill_id
    ):
        return None, FILE_CAPABILITY_CALLER_SELECTION_INCOMPATIBLE
    return request.explicit_selection, None


def _authorization_rejection(
    resolution: SkillAuthorizationResolution,
) -> str | None:
    if resolution.status == "authorized":
        return None if len(resolution.pins) == 1 else FILE_CAPABILITY_SKILL_AMBIGUOUS
    return {
        "ambiguous": FILE_CAPABILITY_SKILL_AMBIGUOUS,
        "unavailable": FILE_CAPABILITY_SKILL_UNAVAILABLE,
        "unauthorized": FILE_CAPABILITY_NOT_AUTHORIZED,
        "stale": FILE_CAPABILITY_VERSION_STALE,
    }.get(resolution.status, FILE_CAPABILITY_SKILL_UNAVAILABLE)


def _pin_rejection(
    pin: AuthorizedSkillPin,
    profile: FileCapabilityProfile,
    selection: SkillSelection | None,
    binding: ServerAuthorizedAgentFileBinding | None,
) -> str | None:
    if pin.logical_skill_id != profile.logical_skill_id:
        return FILE_CAPABILITY_SKILL_UNAVAILABLE
    if selection and (
        pin.skill_id != selection.skill_id
        or pin.expected_version != selection.expected_version
    ):
        return (
            FILE_CAPABILITY_AGENT_PROFILE_INCOMPATIBLE
            if binding
            else FILE_CAPABILITY_CALLER_SELECTION_INCOMPATIBLE
        )
    return None


def _public_facts(
    profile: FileCapabilityProfile, pin: AuthorizedSkillPin, count: int
) -> tuple[PublicProgressFact, ...]:
    assert profile.parser is not None
    skill_label = _valid_label(pin.public_label)
    if skill_label is None:
        raise ValueError("authorization adapter returned an unsafe public label")
    return (
        PublicProgressFact(
            "file_category",
            "admission",
            "completed",
            profile.category_label,
            count,
            count,
        ),
        PublicProgressFact(
            "parser",
            "admission",
            "completed",
            profile.parser.public_label,
            count,
            count,
        ),
        PublicProgressFact(
            "skill", "admission", "completed", skill_label, count, count
        ),
    )


def _not_applicable() -> FileCapabilityAdmission:
    return FileCapabilityAdmission(
        ADMISSION_NOT_APPLICABLE,
        False,
        None,
        FILE_CAPABILITY_REGISTRY_VERSION,
        FILE_CAPABILITY_REGISTRY_POLICY_DIGEST,
    )


def _rejected(code: str) -> FileCapabilityAdmission:
    return FileCapabilityAdmission(
        ADMISSION_REJECTED,
        True,
        code,
        FILE_CAPABILITY_REGISTRY_VERSION,
        FILE_CAPABILITY_REGISTRY_POLICY_DIGEST,
    )
