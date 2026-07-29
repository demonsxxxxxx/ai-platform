"""Server-owned capability admission for bounded attachment bytes.

``admit_file_capability`` is the only admission entry point.  It classifies
bytes, validates their stored identity, selects a reviewed profile, resolves an
authorized immutable Skill, and verifies the exact prebuilt image/workspace
facts.  It never trusts caller MIME, names, parser facts, or Skill text.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from typing import Literal, Protocol, TypeAlias

from app.attachments.classification import (
    ATTACHMENT_CLASSIFIER_VERSION,
    AttachmentBytesForClassification,
    _ClassifiedAttachment,
    _classify_attachment,
)
from app.file_parser_contracts import MAX_XLSX_FILE_BYTES, XLSX_CONTENT_TYPE, XLSX_PARSER_ID, XLSX_PARSER_VERSION
from app.projection_redaction import public_skill_display_label
from app.validation import assert_safe_id


FILE_CAPABILITY_REGISTRY_VERSION = "ai-platform.file-capability-registry.v2"
ADMISSION_NOT_APPLICABLE = "not_applicable"
ADMISSION_REQUIRED = "required"
ADMISSION_REJECTED = "rejected"
AdmissionState: TypeAlias = Literal["not_applicable", "required", "rejected"]
FileTaskIntent: TypeAlias = Literal["analyze", "extract", "review", "transform", "generate_artifact", "non_execution", "unspecified"]
AuthorizationStatus: TypeAlias = Literal["authorized", "ambiguous", "unavailable", "unauthorized", "stale"]
RuntimeDependencyKind: TypeAlias = Literal["python_runtime", "prebuilt_python", "node_npm"]
_EXECUTION_INTENTS = frozenset({"analyze", "extract", "review", "transform", "generate_artifact"})
_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_HASH = re.compile(r"^[0-9a-f]{64}$")


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
    return current + (0,) * (width - len(current)) >= required + (0,) * (width - len(required))


@dataclass(frozen=True, slots=True)
class ParserIdentity:
    """Private parser requirement selected only from the reviewed registry."""

    parser_id: str
    parser_version: str
    max_bytes: int
    public_label: str


@dataclass(frozen=True, slots=True)
class RuntimeDependencyRequirement:
    """A prerequisite that must already exist in the immutable image."""

    kind: RuntimeDependencyKind
    dependency_id: str
    minimum_version: str | None = None
    require_non_root: bool = False

    def __post_init__(self) -> None:
        if self.kind not in {"python_runtime", "prebuilt_python", "node_npm"} or not _TOKEN.fullmatch(self.dependency_id):
            raise ValueError("runtime dependency requirement is invalid")


@dataclass(frozen=True, slots=True)
class RuntimeDependencyIdentity:
    """A dependency proven already present in the selected runtime image."""

    kind: Literal["prebuilt_python", "node_npm"]
    dependency_id: str
    version: str

    def __post_init__(self) -> None:
        if self.kind not in {"prebuilt_python", "node_npm"} or not _TOKEN.fullmatch(self.dependency_id):
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
        if not _DIGEST.fullmatch(self.image_digest) or not re.fullmatch(r"[0-9]+(?:\.[0-9]+){1,3}", self.python_version):
            raise ValueError("runtime image inventory is invalid")
        if not all(isinstance(item, RuntimeDependencyIdentity) for item in self.dependencies) or not all(isinstance(item, WorkspaceSkillPin) for item in self.workspace_skills):
            raise ValueError("runtime inventory facts are invalid")
        if any(not isinstance(item, str) or not _TOKEN.fullmatch(item) for item in self.artifact_types):
            raise ValueError("runtime artifact types are invalid")
        if type(self.runs_as_non_root) is not bool or type(self.npm_source_install_allowed) is not bool or type(self.public_package_registry_egress) is not bool:
            raise ValueError("runtime execution policy is invalid")

    def missing_requirements(self, requirements: tuple[RuntimeDependencyRequirement, ...]) -> tuple[RuntimeDependencyRequirement, ...]:
        """Return unmet requirements without attempting package installation."""

        missing: list[RuntimeDependencyRequirement] = []
        for requirement in requirements:
            if requirement.kind == "python_runtime":
                present = requirement.dependency_id == "python" and _version_satisfies(self.python_version, requirement.minimum_version) and (not requirement.require_non_root or self.runs_as_non_root)
            else:
                present = requirement.kind != "node_npm" or self.node_version is not None
                present = present and any(dependency.kind == requirement.kind and dependency.dependency_id == requirement.dependency_id and _version_satisfies(dependency.version, requirement.minimum_version) for dependency in self.dependencies)
            if not present:
                missing.append(requirement)
        return tuple(missing)

    def has_workspace_skill(self, pin: WorkspaceSkillPin) -> bool:
        """Require id, version, and content hash equality for a staged Skill."""

        return pin in self.workspace_skills


@dataclass(frozen=True, slots=True)
class FileCapabilityProfile:
    """Reviewed type, parser, Skill, runtime, artifact, and multi-file policy."""

    profile_id: str
    category_label: str
    pairs: tuple[tuple[str, str], ...]
    enabled: bool = False
    parser: ParserIdentity | None = None
    logical_skill_id: str | None = None
    runtime_requirements: tuple[RuntimeDependencyRequirement, ...] = ()
    operations: tuple[tuple[str, tuple[str, ...]], ...] = ()
    homogeneous: bool = False


def _disabled(profile_id: str, label: str, *pairs: tuple[str, str]) -> FileCapabilityProfile:
    return FileCapabilityProfile(profile_id, label, pairs)


FILE_CAPABILITY_REGISTRY = (
    FileCapabilityProfile(
        "tabular.xlsx", "Spreadsheet files", ((XLSX_CONTENT_TYPE, ".xlsx"),), True,
        ParserIdentity(XLSX_PARSER_ID, XLSX_PARSER_VERSION, MAX_XLSX_FILE_BYTES, "Spreadsheet analysis"),
        "qa-rag-skill",
        (RuntimeDependencyRequirement("python_runtime", "python", "3.11", True), RuntimeDependencyRequirement("prebuilt_python", "openpyxl", "3.1")),
        (("analyze", ()), ("generate_artifact", ("xlsx",))), True,
    ),
    _disabled("tabular.xls", "Spreadsheet files", ("application/vnd.ms-excel", ".xls")),
    _disabled("tabular.csv", "Tabular files", ("text/csv", ".csv")),
    _disabled("tabular.tsv", "Tabular files", ("text/tab-separated-values", ".tsv")),
    _disabled("document.pdf", "Document files", ("application/pdf", ".pdf")),
    _disabled("document.docx", "Document files", ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", ".docx")),
    _disabled("document.txt", "Document files", ("text/plain", ".txt")),
    _disabled("document.md", "Document files", ("text/markdown", ".md")),
    _disabled("document.html", "Document files", ("text/html", ".html")),
    _disabled("presentation.pptx", "Presentation files", ("application/vnd.openxmlformats-officedocument.presentationml.presentation", ".pptx")),
    _disabled("image.png", "Image files", ("image/png", ".png")),
    _disabled("image.jpeg", "Image files", ("image/jpeg", ".jpeg"), ("image/jpeg", ".jpg")),
    _disabled("image.tiff", "Image files", ("image/tiff", ".tiff"), ("image/tiff", ".tif")),
    _disabled("structured.json", "Structured data", ("application/json", ".json")),
    _disabled("structured.xml", "Structured data", ("application/xml", ".xml")),
    _disabled("archive.reviewed", "Archive files", ("application/zip", ".zip")),
    _disabled("media.audio", "Audio files", ("audio/mpeg", ".mp3")),
    _disabled("media.video", "Video files", ("video/mp4", ".mp4")),
)


def registry_digest(registry: tuple[FileCapabilityProfile, ...]) -> str:
    """Return a deterministic digest of the reviewed profile policy."""

    descriptor = {"version": FILE_CAPABILITY_REGISTRY_VERSION, "classifier": ATTACHMENT_CLASSIFIER_VERSION, "profiles": [asdict(profile) for profile in registry]}
    return hashlib.sha256(json.dumps(descriptor, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


FILE_CAPABILITY_REGISTRY_DIGEST = registry_digest(FILE_CAPABILITY_REGISTRY)


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
    """Immutable Agent constraint that file admission cannot broaden."""

    agent_id: str
    selected_skill: SkillSelection

    def __post_init__(self) -> None:
        assert_safe_id(self.agent_id, "agent_id")


@dataclass(frozen=True, slots=True)
class AuthorizedSkillPin:
    """One exact authorized published Skill distribution from the authorization port."""

    logical_skill_id: str
    skill_id: str
    expected_version: str
    manifest_sha256: str
    public_label: str

    def __post_init__(self) -> None:
        for value, field_name in ((self.logical_skill_id, "logical_skill_id"), (self.skill_id, "skill_id"), (self.expected_version, "expected_version")):
            assert_safe_id(value, field_name)
        label = _valid_label(self.public_label)
        if not _HASH.fullmatch(self.manifest_sha256) or label is None or label.casefold() in {self.logical_skill_id.casefold(), self.skill_id.casefold()}:
            raise ValueError("authorized Skill pin is invalid")

    def workspace_pin(self) -> WorkspaceSkillPin:
        """Return the exact workspace distribution this authorized pin requires."""

        return WorkspaceSkillPin(self.skill_id, self.expected_version, self.manifest_sha256)


@dataclass(frozen=True, slots=True)
class SkillAuthorizationResolution:
    """Narrow authoritative result for exact published Skill authorization."""

    status: AuthorizationStatus
    pins: tuple[AuthorizedSkillPin, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "pins", tuple(self.pins))
        if self.status not in {"authorized", "ambiguous", "unavailable", "unauthorized", "stale"}:
            raise ValueError("authorization status is invalid")


class SkillAuthorizationPort(Protocol):
    """Repository-owned authorization and published-distribution resolution seam."""

    async def resolve_exactly_one(self, *, logical_skill_ids: tuple[str, ...], selection: SkillSelection | None, binding: AgentSkillBinding | None) -> SkillAuthorizationResolution:
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
        if not all(isinstance(item, AttachmentBytesForClassification) for item in self.attachments):
            raise ValueError("attachments must be AttachmentBytesForClassification")
        if not isinstance(self.runtime_inventory, RuntimeImageInventory):
            raise ValueError("runtime_inventory must be RuntimeImageInventory")
        if self.task_intent not in _EXECUTION_INTENTS | {"non_execution", "unspecified"}:
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
        if self.kind not in {"file_category", "parser", "skill"} or self.stage != "admission" or self.status != "completed":
            raise ValueError("public progress fact is invalid")
        if _valid_label(self.label) is None or type(self.current) is not int or type(self.total) is not int or self.total < 1 or not 0 <= self.current <= self.total:
            raise ValueError("public progress progress is invalid")

    def to_public_payload(self) -> dict[str, object]:
        """Serialize only the safe public progress contract."""

        return {"kind": self.kind, "stage": self.stage, "status": self.status, "label": self.label, "progress": {"current": self.current, "total": self.total}}


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
        for name in ("skill_pins", "parser_requirements", "runtime_requirements", "required_artifact_types", "public_progress_facts"):
            object.__setattr__(self, name, tuple(getattr(self, name)))
        private = (self.selected_skill, self.skill_pins, self.workspace_skill_pin, self.parser_requirements, self.runtime_image_digest, self.runtime_requirements, self.required_artifact_types, self.public_progress_facts)
        if self.state == ADMISSION_NOT_APPLICABLE and not self.fallback_prohibited and self.rejection_code is None and not any(private):
            return
        if self.state == ADMISSION_REJECTED and self.fallback_prohibited and self.rejection_code and not any(private):
            return
        if self.state == ADMISSION_REQUIRED and self.fallback_prohibited and self.rejection_code is None and self.selected_skill and len(self.skill_pins) == 1 and self.workspace_skill_pin and self.parser_requirements and self.runtime_image_digest:
            return
        raise ValueError("admission state violates its fallback invariant")


async def admit_file_capability(request: FileCapabilityAdmissionRequest, *, authorization_port: SkillAuthorizationPort) -> FileCapabilityAdmission:
    """Classify bounded bytes and return the one fail-closed file-capability decision."""

    if not request.attachments:
        return _not_applicable()
    attachments: list[_ClassifiedAttachment] = []
    for source in request.attachments:
        attachment, classification = _classify_attachment(source)
        if attachment is None:
            return _rejected(classification.rejection_code or "attachment_classification_type_unsupported")
        attachments.append(attachment)
    profile, rejection = _profile_for(attachments)
    if rejection:
        return _rejected(rejection)
    assert profile is not None
    if not profile.enabled:
        return _rejected("file_capability_type_unsupported")
    if len(attachments) > 1 and not profile.homogeneous:
        return _rejected("file_capability_combination_unsupported")
    if request.task_intent == "non_execution":
        return _not_applicable()
    artifacts = dict(profile.operations).get(request.task_intent)
    if artifacts is None:
        return _rejected("file_capability_intent_ambiguous")
    if profile.parser is None or any(item.size_bytes > profile.parser.max_bytes for item in attachments):
        return _rejected("file_capability_parser_unavailable" if profile.parser is None else "file_capability_size_exceeded")
    if request.runtime_inventory.missing_requirements(profile.runtime_requirements):
        return _rejected("file_capability_runtime_dependency_unavailable")
    if not set(artifacts) <= request.runtime_inventory.artifact_types:
        return _rejected("file_capability_required_artifact_incompatible")
    selection, rejection = _selection(profile, request)
    if rejection:
        return _rejected(rejection)
    resolution = await authorization_port.resolve_exactly_one(logical_skill_ids=(profile.logical_skill_id or "",), selection=selection, binding=request.agent_binding)
    rejection = _authorization_rejection(resolution)
    if rejection:
        return _rejected(rejection)
    pin = resolution.pins[0]
    rejection = _pin_rejection(pin, profile, selection, request.agent_binding)
    if rejection:
        return _rejected(rejection)
    workspace_pin = pin.workspace_pin()
    if not request.runtime_inventory.has_workspace_skill(workspace_pin):
        return _rejected("file_capability_workspace_skill_mismatch")
    return FileCapabilityAdmission(
        ADMISSION_REQUIRED, True, None, FILE_CAPABILITY_REGISTRY_VERSION, FILE_CAPABILITY_REGISTRY_DIGEST,
        SkillSelection(pin.skill_id, pin.expected_version), (pin,), workspace_pin,
        tuple(PrivateParserRequirement(item.file_id, item.media_type, item.verified_extension, item.size_bytes, item.sha256, profile.parser.parser_id, profile.parser.parser_version, profile.parser.max_bytes) for item in attachments),
        request.runtime_inventory.image_digest, profile.runtime_requirements, artifacts,
        _public_facts(profile, pin, len(attachments)),
    )


def _profile_for(attachments: list[_ClassifiedAttachment]) -> tuple[FileCapabilityProfile | None, str | None]:
    profiles: list[FileCapabilityProfile] = []
    for attachment in attachments:
        matches = [profile for profile in FILE_CAPABILITY_REGISTRY if (attachment.media_type, attachment.verified_extension) in profile.pairs]
        if len(matches) != 1:
            return None, "file_capability_parser_ambiguous" if matches else "file_capability_type_unsupported"
        profiles.append(matches[0])
    return (profiles[0], None) if all(profile == profiles[0] for profile in profiles) else (None, "file_capability_combination_unsupported")


def _selection(profile: FileCapabilityProfile, request: FileCapabilityAdmissionRequest) -> tuple[SkillSelection | None, str | None]:
    if request.agent_binding:
        bound = request.agent_binding.selected_skill
        if bound.skill_id != profile.logical_skill_id or request.explicit_selection not in {None, bound}:
            return None, "file_capability_agent_profile_incompatible"
        return bound, None
    if request.explicit_selection and request.explicit_selection.skill_id != profile.logical_skill_id:
        return None, "file_capability_caller_selection_incompatible"
    return request.explicit_selection, None


def _authorization_rejection(resolution: SkillAuthorizationResolution) -> str | None:
    if resolution.status == "authorized":
        return None if len(resolution.pins) == 1 else "file_capability_skill_ambiguous"
    return {"ambiguous": "file_capability_skill_ambiguous", "unavailable": "file_capability_skill_unavailable", "unauthorized": "file_capability_not_authorized", "stale": "file_capability_version_stale"}.get(resolution.status, "file_capability_skill_unavailable")


def _pin_rejection(pin: AuthorizedSkillPin, profile: FileCapabilityProfile, selection: SkillSelection | None, binding: AgentSkillBinding | None) -> str | None:
    if pin.logical_skill_id != profile.logical_skill_id:
        return "file_capability_skill_unavailable"
    if selection and (pin.skill_id != selection.skill_id or pin.expected_version != selection.expected_version):
        return "file_capability_agent_profile_incompatible" if binding else "file_capability_caller_selection_incompatible"
    return None


def _public_facts(profile: FileCapabilityProfile, pin: AuthorizedSkillPin, count: int) -> tuple[PublicProgressFact, ...]:
    assert profile.parser is not None
    skill_label = _valid_label(pin.public_label)
    if skill_label is None:
        raise ValueError("authorization adapter returned an unsafe public label")
    return (
        PublicProgressFact("file_category", "admission", "completed", profile.category_label, count, count),
        PublicProgressFact("parser", "admission", "completed", profile.parser.public_label, count, count),
        PublicProgressFact("skill", "admission", "completed", skill_label, count, count),
    )


def _not_applicable() -> FileCapabilityAdmission:
    return FileCapabilityAdmission(ADMISSION_NOT_APPLICABLE, False, None, FILE_CAPABILITY_REGISTRY_VERSION, FILE_CAPABILITY_REGISTRY_DIGEST)


def _rejected(code: str) -> FileCapabilityAdmission:
    return FileCapabilityAdmission(ADMISSION_REJECTED, True, code, FILE_CAPABILITY_REGISTRY_VERSION, FILE_CAPABILITY_REGISTRY_DIGEST)


if len({pair for profile in FILE_CAPABILITY_REGISTRY for pair in profile.pairs}) != sum(len(profile.pairs) for profile in FILE_CAPABILITY_REGISTRY):
    raise RuntimeError("file capability registry has ambiguous type pairs")
