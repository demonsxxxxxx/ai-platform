"""Server-owned capability admission for bounded attachment bytes.

``admit_file_capability`` is the only admission entry point.  It classifies
bytes, validates their stored identity, selects a reviewed profile, resolves an
authorized immutable Skill, and verifies the exact prebuilt image/workspace
facts.  It never trusts caller MIME, names, parser facts, or Skill text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal, Protocol, TypeAlias

from app.attachments.classification import (
    ATTACHMENT_CLASSIFICATION_REJECTION_CODES,
    AttachmentBytesForClassification,
    _ClassifiedAttachment,
    _classify_attachment,
)
from app.attachments.file_capabilities import (
    CAPABILITY_AGENT_INPUT,
    FILE_CAPABILITY_REGISTRY,
    FILE_CAPABILITY_REGISTRY_DIGEST,
    FILE_CAPABILITY_REGISTRY_VERSION,
    FILE_CAPABILITY_AGENT_BINDING_REQUIRED,
    FILE_CAPABILITY_AGENT_PROFILE_INCOMPATIBLE,
    FILE_CAPABILITY_CALLER_SELECTION_INCOMPATIBLE,
    FILE_CAPABILITY_COMBINATION_UNSUPPORTED,
    FILE_CAPABILITY_INTENT_AMBIGUOUS,
    FILE_CAPABILITY_NOT_AUTHORIZED,
    FILE_CAPABILITY_PARSER_AMBIGUOUS,
    FILE_CAPABILITY_PARSER_UNAVAILABLE,
    FILE_CAPABILITY_REQUIRED_ARTIFACT_INCOMPATIBLE,
    FILE_CAPABILITY_REJECTION_CODES,
    FILE_CAPABILITY_RUNTIME_DEPENDENCY_UNAVAILABLE,
    FILE_CAPABILITY_SIZE_EXCEEDED,
    FILE_CAPABILITY_SKILL_AMBIGUOUS,
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
    matching_file_capability_profiles,
    registry_digest as _registry_digest,
)
from app.projection_redaction import public_skill_display_label
from app.validation import assert_safe_id


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
_EXECUTION_INTENTS = frozenset(
    {"analyze", "extract", "review", "transform", "generate_artifact"}
)
_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_HASH = re.compile(r"^[0-9a-f]{64}$")
FILE_CAPABILITY_ADMISSION_REJECTION_CODES = frozenset(
    FILE_CAPABILITY_REJECTION_CODES | ATTACHMENT_CLASSIFICATION_REJECTION_CODES
)


def registry_digest(registry: tuple[FileCapabilityProfile, ...]) -> str:
    """Compatibility export for callers of the original admission module."""

    return _registry_digest(registry)


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
    """A dependency proven already present in the selected runtime image."""

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
    """Exact immutable Skill distribution staged in the workspace, not a logical name."""

    skill_id: str
    expected_version: str
    content_hash: str

    def __post_init__(self) -> None:
        assert_safe_id(self.skill_id, "skill_id")
        assert_safe_id(self.expected_version, "expected_version")
        if not _HASH.fullmatch(self.content_hash):
            raise ValueError("workspace Skill content_hash is invalid")


@dataclass(frozen=True, slots=True)
class RuntimeImageInventory:
    """Exact runtime facts; this contract performs no install or network activity."""

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
        """Return unmet requirements without attempting package installation."""

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
        """Require id, version, and content hash equality for a staged Skill."""

        return pin in self.workspace_skills


@dataclass(frozen=True, slots=True)
class SkillSelection:
    """An exact caller choice or immutable Agent-bound selection."""

    skill_id: str
    expected_version: str

    def __post_init__(self) -> None:
        assert_safe_id(self.skill_id, "skill_id")
        assert_safe_id(self.expected_version, "expected_version")


@dataclass(frozen=True, slots=True)
class AgentSkillBinding:
    """Untrusted exact-revision locator; never an authorization fact by itself.

    The historical symbol is retained for import compatibility, but its shape
    intentionally cannot self-attest the Agent's selected Skill or file types.
    ``admit_file_capability`` must resolve it through ``AgentFileAuthorizationPort``.
    """

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
    """One exact authorized published Skill distribution from the authorization port."""

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
            not _HASH.fullmatch(self.manifest_sha256)
            or label is None
            or label.casefold()
            in {self.logical_skill_id.casefold(), self.skill_id.casefold()}
        ):
            raise ValueError("authorized Skill pin is invalid")

    def workspace_pin(self) -> WorkspaceSkillPin:
        """Return the exact workspace distribution this authorized pin requires."""

        return WorkspaceSkillPin(
            self.skill_id, self.expected_version, self.manifest_sha256
        )


@dataclass(frozen=True, slots=True)
class SkillAuthorizationResolution:
    """Narrow authoritative result for exact published Skill authorization."""

    status: AuthorizationStatus
    pins: tuple[AuthorizedSkillPin, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "pins", tuple(self.pins))
        if self.status not in {
            "authorized",
            "ambiguous",
            "unavailable",
            "unauthorized",
            "stale",
        }:
            raise ValueError("authorization status is invalid")
        if not all(isinstance(pin, AuthorizedSkillPin) for pin in self.pins):
            raise ValueError("authorization pins are invalid")


class SkillAuthorizationPort(Protocol):
    """Repository-owned authorization and published-distribution resolution seam."""

    async def resolve_exactly_one(
        self,
        *,
        logical_skill_ids: tuple[str, ...],
        selection: SkillSelection | None,
        binding: ServerAuthorizedAgentFileBinding | None,
    ) -> SkillAuthorizationResolution:
        """Resolve exactly one authorized immutable distribution without model authority."""


@dataclass(frozen=True, slots=True)
class FileCapabilityAdmissionRequest:
    """Server-owned raw byte, selection, and runtime facts for one atomic admission."""

    attachments: tuple[AttachmentBytesForClassification, ...]
    task_intent: FileTaskIntent
    runtime_inventory: RuntimeImageInventory
    explicit_selection: SkillSelection | None = None
    agent_binding: AgentSkillBinding | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "attachments", tuple(self.attachments))
        if not all(
            isinstance(item, AttachmentBytesForClassification)
            for item in self.attachments
        ):
            raise ValueError("attachments must be AttachmentBytesForClassification")
        if not isinstance(self.runtime_inventory, RuntimeImageInventory):
            raise ValueError("runtime_inventory must be RuntimeImageInventory")
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
    """Private parser facts exact to bytes classified by this admission call."""

    file_id: str
    verified_media_type: str
    verified_extension: str
    expected_size_bytes: int
    expected_sha256: str
    parser_id: str
    parser_version: str
    max_bytes: int

    def __post_init__(self) -> None:
        assert_safe_id(self.file_id, "file_id")
        VerifiedFileIdentity(self.verified_media_type, self.verified_extension)
        if (
            type(self.expected_size_bytes) is not int
            or self.expected_size_bytes < 0
            or not isinstance(self.expected_sha256, str)
            or not _HASH.fullmatch(self.expected_sha256)
            or not _TOKEN.fullmatch(self.parser_id)
            or not _TOKEN.fullmatch(self.parser_version)
            or type(self.max_bytes) is not int
            or self.max_bytes < 1
            or self.expected_size_bytes > self.max_bytes
        ):
            raise ValueError("private parser requirement is invalid")


@dataclass(frozen=True, slots=True)
class PublicProgressFact:
    """Strictly allowlisted semantic progress without private identifiers or content."""

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
        ):
            raise ValueError("public progress fact is invalid")
        if (
            _valid_label(self.label) is None
            or type(self.current) is not int
            or type(self.total) is not int
            or self.total < 1
            or not 0 <= self.current <= self.total
        ):
            raise ValueError("public progress progress is invalid")

    def to_public_payload(self) -> dict[str, object]:
        """Serialize only the safe public progress contract."""

        return {
            "kind": self.kind,
            "stage": self.stage,
            "status": self.status,
            "label": self.label,
            "progress": {"current": self.current, "total": self.total},
        }


@dataclass(frozen=True, slots=True)
class FileCapabilityAdmission:
    """Terminal decision with private execution requirements and a fallback invariant."""

    state: AdmissionState
    fallback_prohibited: bool
    rejection_code: str | None
    registry_version: str
    registry_digest: str
    selected_skill: SkillSelection | None = None
    skill_pins: tuple[AuthorizedSkillPin, ...] = ()
    workspace_skill_pin: WorkspaceSkillPin | None = None
    parser_requirements: tuple[PrivateParserRequirement, ...] = ()
    runtime_image_digest: str | None = None
    runtime_requirements: tuple[RuntimeDependencyRequirement, ...] = ()
    required_artifact_types: tuple[str, ...] = ()
    public_progress_facts: tuple[PublicProgressFact, ...] = ()

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
        )
        fields_valid = (
            self.registry_version == FILE_CAPABILITY_REGISTRY_VERSION
            and self.registry_digest == FILE_CAPABILITY_REGISTRY_DIGEST
            and self.state
            in {ADMISSION_NOT_APPLICABLE, ADMISSION_REQUIRED, ADMISSION_REJECTED}
            and type(self.fallback_prohibited) is bool
            and (
                self.selected_skill is None
                or isinstance(self.selected_skill, SkillSelection)
            )
            and all(isinstance(item, AuthorizedSkillPin) for item in self.skill_pins)
            and (
                self.workspace_skill_pin is None
                or isinstance(self.workspace_skill_pin, WorkspaceSkillPin)
            )
            and all(
                isinstance(item, PrivateParserRequirement)
                for item in self.parser_requirements
            )
            and (
                self.runtime_image_digest is None
                or (
                    isinstance(self.runtime_image_digest, str)
                    and _DIGEST.fullmatch(self.runtime_image_digest)
                )
            )
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
            and len({item.file_id for item in self.parser_requirements})
            == len(self.parser_requirements)
            and len(set(self.required_artifact_types))
            == len(self.required_artifact_types)
        )
        if not fields_valid:
            raise ValueError("admission state violates its fallback invariant")
        if (
            self.state == ADMISSION_NOT_APPLICABLE
            and not self.fallback_prohibited
            and self.rejection_code is None
            and not any(private)
        ):
            return
        if (
            self.state == ADMISSION_REJECTED
            and self.fallback_prohibited
            and self.rejection_code in FILE_CAPABILITY_ADMISSION_REJECTION_CODES
            and not any(private)
        ):
            return
        if (
            self.state == ADMISSION_REQUIRED
            and self.fallback_prohibited
            and self.rejection_code is None
            and self.selected_skill
            and len(self.skill_pins) == 1
            and self.workspace_skill_pin
            and self.parser_requirements
            and self.runtime_image_digest
            and self.skill_pins[0].skill_id == self.selected_skill.skill_id
            and self.skill_pins[0].expected_version
            == self.selected_skill.expected_version
            and self.workspace_skill_pin == self.skill_pins[0].workspace_pin()
        ):
            return
        raise ValueError("admission state violates its fallback invariant")


async def admit_file_capability(
    request: FileCapabilityAdmissionRequest,
    *,
    authorization_port: SkillAuthorizationPort,
    agent_authorization_port: AgentFileAuthorizationPort | None = None,
) -> FileCapabilityAdmission:
    """Classify bounded bytes and return the one fail-closed file-capability decision."""

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
    if request.runtime_inventory.missing_requirements(profile.runtime_requirements):
        return _rejected(FILE_CAPABILITY_RUNTIME_DEPENDENCY_UNAVAILABLE)
    if not set(artifacts) <= request.runtime_inventory.artifact_types:
        return _rejected(FILE_CAPABILITY_REQUIRED_ARTIFACT_INCOMPATIBLE)
    authorized_agent_binding: ServerAuthorizedAgentFileBinding | None = None
    if request.agent_binding is not None:
        if agent_authorization_port is None:
            return _rejected(FILE_CAPABILITY_AGENT_BINDING_REQUIRED)
        (
            authorized_agent_binding,
            rejection,
        ) = await _resolve_authorized_agent_file_binding(
            agent_id=request.agent_binding.agent_id,
            expected_revision=request.agent_binding.expected_revision,
            expected_profile_sha256=request.agent_binding.expected_profile_sha256,
            authorization_port=agent_authorization_port,
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
    pin = resolution.pins[0]
    rejection = _pin_rejection(pin, profile, selection, authorized_agent_binding)
    if rejection:
        return _rejected(rejection)
    workspace_pin = pin.workspace_pin()
    if not request.runtime_inventory.has_workspace_skill(workspace_pin):
        return _rejected(FILE_CAPABILITY_WORKSPACE_SKILL_MISMATCH)
    return FileCapabilityAdmission(
        ADMISSION_REQUIRED,
        True,
        None,
        FILE_CAPABILITY_REGISTRY_VERSION,
        FILE_CAPABILITY_REGISTRY_DIGEST,
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
            for item in attachments
        ),
        request.runtime_inventory.image_digest,
        profile.runtime_requirements,
        artifacts,
        _public_facts(profile, pin, len(attachments)),
    )


def _profile_for(
    attachments: list[_ClassifiedAttachment],
) -> tuple[FileCapabilityProfile | None, str | None]:
    profiles: list[FileCapabilityProfile] = []
    for attachment in attachments:
        identity = VerifiedFileIdentity(
            attachment.media_type, attachment.verified_extension
        )
        matches = matching_file_capability_profiles(
            identity, registry=FILE_CAPABILITY_REGISTRY
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
            binding.selected_skill_id,
            binding.selected_skill_version,
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


def _authorization_rejection(resolution: SkillAuthorizationResolution) -> str | None:
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
        FILE_CAPABILITY_REGISTRY_DIGEST,
    )


def _rejected(code: str) -> FileCapabilityAdmission:
    return FileCapabilityAdmission(
        ADMISSION_REJECTED,
        True,
        code,
        FILE_CAPABILITY_REGISTRY_VERSION,
        FILE_CAPABILITY_REGISTRY_DIGEST,
    )
