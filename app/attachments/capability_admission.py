"""Fail-closed admission for byte-classified attachment capabilities.

This deep module has one decision interface. It accepts only identities issued by
``classification``, delegates published Skill authorization to an injected adapter,
and describes the exact immutable runtime image required to execute the profile.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from typing import Literal, Protocol, TypeAlias

from app.attachments.classification import (
    ATTACHMENT_CLASSIFIER_VERSION,
    AttachmentByteIdentity,
    is_classified_attachment_identity,
)
from app.file_parser_contracts import (
    MAX_XLSX_FILE_BYTES,
    XLSX_CONTENT_TYPE,
    XLSX_PARSER_ID,
    XLSX_PARSER_VERSION,
)
from app.projection_redaction import public_skill_display_label
from app.validation import assert_safe_id


FILE_CAPABILITY_REGISTRY_VERSION = "ai-platform.file-capability-registry.v2"
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
RuntimeDependencyKind: TypeAlias = Literal[
    "python_runtime", "prebuilt_python", "workspace_local", "node_npm"
]
MultiFilePolicy: TypeAlias = Literal["single", "homogeneous"]

_EXECUTION_TASK_INTENTS = frozenset(
    {"analyze", "extract", "review", "transform", "generate_artifact"}
)
_CONTRACT_TOKEN_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._>=-]{0,127}$")
_IMAGE_DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
_PUBLIC_FACT_KINDS = frozenset({"file_category", "parser", "skill"})


def _safe_public_label(value: object) -> str | None:
    return public_skill_display_label(value)


def _validate_contract_tokens(values: tuple[str, ...] | frozenset[str], field_name: str) -> None:
    if any(not isinstance(value, str) or not _CONTRACT_TOKEN_PATTERN.fullmatch(value) for value in values):
        raise ValueError(f"{field_name} contains an invalid contract token")


def _validate_sha256(value: str, field_name: str) -> None:
    normalized = str(value).casefold()
    if len(normalized) != 64 or any(character not in "0123456789abcdef" for character in normalized):
        raise ValueError(f"{field_name} must be 64 lowercase hexadecimal characters")


def _version_satisfies(actual: str, minimum: str | None) -> bool:
    if minimum is None:
        return bool(actual)
    try:
        actual_parts = tuple(int(part) for part in actual.split("."))
        minimum_parts = tuple(int(part) for part in minimum.split("."))
    except ValueError:
        return actual == minimum
    length = max(len(actual_parts), len(minimum_parts))
    return actual_parts + (0,) * (length - len(actual_parts)) >= minimum_parts + (0,) * (
        length - len(minimum_parts)
    )


@dataclass(frozen=True, slots=True)
class FileTypePair:
    """One exact byte-classified media type and compatible verified extension."""

    media_type: str
    extension: str

    def __post_init__(self) -> None:
        if (
            not self.media_type
            or self.media_type != self.media_type.casefold()
            or ";" in self.media_type
            or "/" not in self.media_type
        ):
            raise ValueError("media_type must be normalized")
        if not re.fullmatch(r"\.[a-z0-9]{1,16}", self.extension):
            raise ValueError("extension must be normalized")


@dataclass(frozen=True, slots=True)
class ParserIdentity:
    """Private parser identity selected only from the reviewed profile registry."""

    parser_id: str
    parser_version: str
    max_bytes: int
    public_label: str

    def __post_init__(self) -> None:
        assert_safe_id(self.parser_id, "parser_id")
        if not self.parser_version or self.max_bytes < 1 or _safe_public_label(self.public_label) is None:
            raise ValueError("parser identity is invalid")


@dataclass(frozen=True, slots=True)
class RuntimeDependencyRequirement:
    """Exact image or workspace-local prerequisite; source installation never satisfies it."""

    kind: RuntimeDependencyKind
    dependency_id: str
    minimum_version: str | None = None
    require_non_root: bool = False

    def __post_init__(self) -> None:
        if self.kind not in {"python_runtime", "prebuilt_python", "workspace_local", "node_npm"}:
            raise ValueError("runtime dependency kind is invalid")
        assert_safe_id(self.dependency_id, "dependency_id")
        if self.minimum_version is not None and not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", self.minimum_version):
            raise ValueError("minimum_version is invalid")


@dataclass(frozen=True, slots=True)
class RuntimeDependencyIdentity:
    """One dependency proved present in the exact immutable runtime image or workspace."""

    kind: Literal["prebuilt_python", "workspace_local", "node_npm"]
    dependency_id: str
    version: str

    def __post_init__(self) -> None:
        if self.kind not in {"prebuilt_python", "workspace_local", "node_npm"}:
            raise ValueError("runtime dependency identity kind is invalid")
        assert_safe_id(self.dependency_id, "dependency_id")
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", self.version):
            raise ValueError("runtime dependency version is invalid")


@dataclass(frozen=True, slots=True)
class RuntimeImageInventory:
    """Private exact runtime image evidence; no dependency installer is implied or invoked."""

    image_digest: str
    python_version: str
    runs_as_non_root: bool
    dependencies: tuple[RuntimeDependencyIdentity, ...]
    artifact_types: frozenset[str]
    node_version: str | None
    npm_source_install_allowed: bool
    public_package_registry_egress: bool

    def __post_init__(self) -> None:
        object.__setattr__(self, "dependencies", tuple(self.dependencies))
        if not _IMAGE_DIGEST_PATTERN.fullmatch(self.image_digest):
            raise ValueError("image_digest must be an exact sha256 digest")
        if not re.fullmatch(r"[0-9]+(?:\.[0-9]+){1,3}", self.python_version):
            raise ValueError("python_version is invalid")
        if type(self.runs_as_non_root) is not bool:
            raise ValueError("runs_as_non_root is invalid")
        _validate_contract_tokens(self.artifact_types, "artifact_types")
        if self.node_version is not None and not re.fullmatch(r"[0-9]+(?:\.[0-9]+){1,3}", self.node_version):
            raise ValueError("node_version is invalid")
        if type(self.npm_source_install_allowed) is not bool or type(self.public_package_registry_egress) is not bool:
            raise ValueError("runtime install policy is invalid")

    def missing_requirements(
        self, requirements: tuple[RuntimeDependencyRequirement, ...]
    ) -> tuple[RuntimeDependencyRequirement, ...]:
        """Return requirements absent from this exact inventory without attempting installation."""

        missing: list[RuntimeDependencyRequirement] = []
        for requirement in requirements:
            if requirement.kind == "python_runtime":
                if (
                    requirement.dependency_id != "python"
                    or not _version_satisfies(self.python_version, requirement.minimum_version)
                    or requirement.require_non_root and not self.runs_as_non_root
                ):
                    missing.append(requirement)
                continue
            if requirement.kind == "node_npm" and self.node_version is None:
                missing.append(requirement)
                continue
            matching = [
                dependency
                for dependency in self.dependencies
                if dependency.kind == requirement.kind and dependency.dependency_id == requirement.dependency_id
            ]
            if not any(_version_satisfies(dependency.version, requirement.minimum_version) for dependency in matching):
                missing.append(requirement)
        return tuple(missing)


@dataclass(frozen=True, slots=True)
class FileCapabilityOperation:
    """One bounded task intent and its private artifact contract."""

    task_intent: FileTaskIntent
    required_artifact_types: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.task_intent not in _EXECUTION_TASK_INTENTS:
            raise ValueError("operation must require execution")
        _validate_contract_tokens(self.required_artifact_types, "required_artifact_types")


@dataclass(frozen=True, slots=True)
class FileCapabilityProfile:
    """Reviewed byte identity, parser, Skill, runtime, and artifact policy for one file family."""

    profile_id: str
    category: str
    public_category_label: str
    pairs: tuple[FileTypePair, ...]
    multi_file_policy: MultiFilePolicy
    enabled: bool
    parser: ParserIdentity | None
    logical_skill_ids: tuple[str, ...]
    runtime_requirements: tuple[RuntimeDependencyRequirement, ...]
    operations: tuple[FileCapabilityOperation, ...]

    def __post_init__(self) -> None:
        assert_safe_id(self.profile_id, "profile_id")
        if self.multi_file_policy not in {"single", "homogeneous"} or not self.pairs:
            raise ValueError("profile file policy is invalid")
        if len(set(self.pairs)) != len(self.pairs) or _safe_public_label(self.public_category_label) is None:
            raise ValueError("profile identities are invalid")
        _validate_contract_tokens(self.logical_skill_ids, "logical_skill_ids")
        if len({operation.task_intent for operation in self.operations}) != len(self.operations):
            raise ValueError("profile operations must be unique")
        if self.enabled and (self.parser is None or len(self.logical_skill_ids) != 1 or not self.operations):
            raise ValueError("enabled profile is incomplete")
        if not self.enabled and (
            self.parser is not None or self.logical_skill_ids or self.runtime_requirements or self.operations
        ):
            raise ValueError("disabled profile cannot claim execution authority")

    def operation_for(self, task_intent: str) -> FileCapabilityOperation | None:
        """Return the exact reviewed operation for one normalized task intent."""

        return next((operation for operation in self.operations if operation.task_intent == task_intent), None)


@dataclass(frozen=True, slots=True)
class SkillSelection:
    """Private exact caller or immutable Agent optimistic-lock selection."""

    skill_id: str
    expected_version: str

    def __post_init__(self) -> None:
        assert_safe_id(self.skill_id, "skill_id")
        assert_safe_id(self.expected_version, "expected_version")


@dataclass(frozen=True, slots=True)
class AgentSkillBinding:
    """Immutable Agent profile constraint that a file profile cannot broaden."""

    agent_id: str
    selected_skill: SkillSelection

    def __post_init__(self) -> None:
        assert_safe_id(self.agent_id, "agent_id")


@dataclass(frozen=True, slots=True)
class FileCapabilityAdmissionRequest:
    """Immutable admission request formed only after byte classification and runtime inventory capture."""

    attachments: tuple[AttachmentByteIdentity, ...]
    task_intent: FileTaskIntent
    runtime_inventory: RuntimeImageInventory
    explicit_selection: SkillSelection | None = None
    agent_binding: AgentSkillBinding | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "attachments", tuple(self.attachments))


@dataclass(frozen=True, slots=True)
class AuthorizedSkillPin:
    """Private exact published Skill distribution returned by the authorization adapter."""

    logical_skill_id: str
    skill_id: str
    expected_version: str
    manifest_sha256: str
    public_label: str

    def __post_init__(self) -> None:
        assert_safe_id(self.logical_skill_id, "logical_skill_id")
        assert_safe_id(self.skill_id, "skill_id")
        assert_safe_id(self.expected_version, "expected_version")
        _validate_sha256(self.manifest_sha256, "manifest_sha256")
        label = _safe_public_label(self.public_label)
        if label is None or label.casefold() in {self.logical_skill_id.casefold(), self.skill_id.casefold()}:
            raise ValueError("public_label is not safe")


@dataclass(frozen=True, slots=True)
class SkillAuthorizationResolution:
    """Narrow adapter result for exact authorized published Skill resolution."""

    status: AuthorizationStatus
    pins: tuple[AuthorizedSkillPin, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "pins", tuple(self.pins))
        if self.status not in {"authorized", "ambiguous", "unavailable", "unauthorized", "stale"}:
            raise ValueError("authorization status is invalid")


class SkillAuthorizationPort(Protocol):
    """Seam for repository-owned authorization and manifest pinning policy."""

    async def resolve_exactly_one(
        self,
        *,
        logical_skill_ids: tuple[str, ...],
        selection: SkillSelection | None,
        binding: AgentSkillBinding | None,
    ) -> SkillAuthorizationResolution:
        """Return exactly one current authorized pin without using model text as authority."""


@dataclass(frozen=True, slots=True)
class PrivateParserRequirement:
    """Private parser contract bound to the exact classified bytes, never to caller metadata."""

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
    """Strict semantic progress fact without raw file, hash, parser, or Skill identities."""

    kind: Literal["file_category", "parser", "skill"]
    stage: Literal["admission"]
    status: Literal["completed"]
    label: str
    current: int
    total: int

    def __post_init__(self) -> None:
        if self.kind not in _PUBLIC_FACT_KINDS or self.stage != "admission" or self.status != "completed":
            raise ValueError("public progress lifecycle is invalid")
        if _safe_public_label(self.label) is None or type(self.current) is not int or type(self.total) is not int:
            raise ValueError("public progress fact is invalid")
        if self.total < 1 or not 0 <= self.current <= self.total:
            raise ValueError("public progress counts are inconsistent")

    def to_public_payload(self) -> dict[str, object]:
        """Serialize the only public fields admission can emit."""

        return {
            "kind": self.kind,
            "stage": self.stage,
            "status": self.status,
            "label": self.label,
            "progress": {"current": self.current, "total": self.total},
        }


@dataclass(frozen=True, slots=True)
class FileCapabilityAdmission:
    """Terminal typed-file decision with fail-closed fallback and private execution contracts."""

    state: AdmissionState
    fallback_prohibited: bool
    rejection_code: str | None
    registry_version: str
    registry_digest: str
    selected_skill: SkillSelection | None = None
    skill_pins: tuple[AuthorizedSkillPin, ...] = ()
    parser_requirements: tuple[PrivateParserRequirement, ...] = ()
    runtime_image_digest: str | None = None
    runtime_requirements: tuple[RuntimeDependencyRequirement, ...] = ()
    required_artifact_types: tuple[str, ...] = ()
    public_progress_facts: tuple[PublicProgressFact, ...] = ()

    def __post_init__(self) -> None:
        for field_name in (
            "skill_pins",
            "parser_requirements",
            "runtime_requirements",
            "required_artifact_types",
            "public_progress_facts",
        ):
            object.__setattr__(self, field_name, tuple(getattr(self, field_name)))
        if self.state not in {ADMISSION_NOT_APPLICABLE, ADMISSION_REQUIRED, ADMISSION_REJECTED}:
            raise ValueError("admission state is invalid")
        private_values = (
            self.selected_skill,
            self.skill_pins,
            self.parser_requirements,
            self.runtime_image_digest,
            self.runtime_requirements,
            self.required_artifact_types,
            self.public_progress_facts,
        )
        if self.state == ADMISSION_NOT_APPLICABLE:
            if self.fallback_prohibited or self.rejection_code is not None or any(private_values):
                raise ValueError("not applicable admission is invalid")
        elif self.state == ADMISSION_REJECTED:
            if not self.fallback_prohibited or not self.rejection_code or any(private_values):
                raise ValueError("rejected admission is invalid")
        elif (
            not self.fallback_prohibited
            or self.rejection_code is not None
            or self.selected_skill is None
            or len(self.skill_pins) != 1
            or not self.parser_requirements
            or self.runtime_image_digest is None
        ):
            raise ValueError("required admission is incomplete")


def _profile(
    profile_id: str,
    category: str,
    label: str,
    pairs: tuple[FileTypePair, ...],
    *,
    policy: MultiFilePolicy = "single",
    enabled: bool = False,
    parser: ParserIdentity | None = None,
    logical_skill_ids: tuple[str, ...] = (),
    runtime_requirements: tuple[RuntimeDependencyRequirement, ...] = (),
    operations: tuple[FileCapabilityOperation, ...] = (),
) -> FileCapabilityProfile:
    return FileCapabilityProfile(
        profile_id=profile_id,
        category=category,
        public_category_label=label,
        pairs=pairs,
        multi_file_policy=policy,
        enabled=enabled,
        parser=parser,
        logical_skill_ids=logical_skill_ids,
        runtime_requirements=runtime_requirements,
        operations=operations,
    )


FILE_CAPABILITY_REGISTRY = (
    _profile(
        "tabular.xlsx",
        "tabular",
        "Spreadsheet files",
        (FileTypePair(XLSX_CONTENT_TYPE, ".xlsx"),),
        policy="homogeneous",
        enabled=True,
        parser=ParserIdentity(XLSX_PARSER_ID, XLSX_PARSER_VERSION, MAX_XLSX_FILE_BYTES, "Spreadsheet analysis"),
        logical_skill_ids=("qa-rag-skill",),
        runtime_requirements=(
            RuntimeDependencyRequirement("python_runtime", "python", "3.11", require_non_root=True),
            RuntimeDependencyRequirement("prebuilt_python", "openpyxl", "3.1"),
            RuntimeDependencyRequirement("workspace_local", "qa-rag-skill"),
        ),
        operations=(FileCapabilityOperation("analyze"), FileCapabilityOperation("generate_artifact", ("xlsx",))),
    ),
    _profile("tabular.xls", "tabular", "Spreadsheet files", (FileTypePair("application/vnd.ms-excel", ".xls"),)),
    _profile("tabular.csv", "tabular", "Tabular files", (FileTypePair("text/csv", ".csv"),)),
    _profile("tabular.tsv", "tabular", "Tabular files", (FileTypePair("text/tab-separated-values", ".tsv"),)),
    _profile("document.pdf", "document", "Document files", (FileTypePair("application/pdf", ".pdf"),)),
    _profile("document.docx", "document", "Document files", (FileTypePair("application/vnd.openxmlformats-officedocument.wordprocessingml.document", ".docx"),)),
    _profile("document.txt", "document", "Document files", (FileTypePair("text/plain", ".txt"),)),
    _profile("document.md", "document", "Document files", (FileTypePair("text/markdown", ".md"),)),
    _profile("document.html", "document", "Document files", (FileTypePair("text/html", ".html"),)),
    _profile("presentation.pptx", "presentation", "Presentation files", (FileTypePair("application/vnd.openxmlformats-officedocument.presentationml.presentation", ".pptx"),)),
    _profile("image.png", "image", "Image files", (FileTypePair("image/png", ".png"),)),
    _profile("image.jpeg", "image", "Image files", (FileTypePair("image/jpeg", ".jpeg"), FileTypePair("image/jpeg", ".jpg"))),
    _profile("image.tiff", "image", "Image files", (FileTypePair("image/tiff", ".tiff"), FileTypePair("image/tiff", ".tif"))),
    _profile("structured.json", "structured", "Structured data", (FileTypePair("application/json", ".json"),)),
    _profile("structured.xml", "structured", "Structured data", (FileTypePair("application/xml", ".xml"),)),
    _profile("archive.reviewed", "archive", "Archive files", (FileTypePair("application/zip", ".zip"),)),
    _profile("media.audio", "media", "Audio files", (FileTypePair("audio/mpeg", ".mp3"),)),
    _profile("media.video", "media", "Video files", (FileTypePair("video/mp4", ".mp4"),)),
)


def registry_digest(registry: tuple[FileCapabilityProfile, ...]) -> str:
    """Return a deterministic digest of every reviewed selection and runtime policy field."""

    descriptor = [
        {
            "profile_id": profile.profile_id,
            "category": profile.category,
            "public_category_label": profile.public_category_label,
            "pairs": [(pair.media_type, pair.extension) for pair in profile.pairs],
            "multi_file_policy": profile.multi_file_policy,
            "enabled": profile.enabled,
            "parser": None if profile.parser is None else asdict(profile.parser),
            "logical_skill_ids": profile.logical_skill_ids,
            "runtime_requirements": [asdict(requirement) for requirement in profile.runtime_requirements],
            "operations": [asdict(operation) for operation in profile.operations],
        }
        for profile in registry
    ]
    encoded = json.dumps(
        {"version": FILE_CAPABILITY_REGISTRY_VERSION, "classifier": ATTACHMENT_CLASSIFIER_VERSION, "profiles": descriptor},
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


FILE_CAPABILITY_REGISTRY_DIGEST = registry_digest(FILE_CAPABILITY_REGISTRY)


async def admit_file_capability(
    request: FileCapabilityAdmissionRequest,
    *,
    authorization_port: SkillAuthorizationPort,
) -> FileCapabilityAdmission:
    """Return the one terminal decision for classified attachments and exact runtime evidence."""

    attachments = request.attachments
    if not attachments:
        return _not_applicable()
    if any(not is_classified_attachment_identity(attachment) for attachment in attachments):
        return _rejected("file_capability_metadata_untrusted")
    profiles, identity_code = _profiles_for_attachments(attachments)
    if identity_code is not None:
        return _rejected(identity_code)
    if len(profiles) != 1:
        return _rejected("file_capability_combination_unsupported")
    profile = profiles[0]
    if not profile.enabled:
        return _rejected("file_capability_type_unsupported")
    if len(attachments) > 1 and profile.multi_file_policy == "single":
        return _rejected("file_capability_combination_unsupported")
    if request.task_intent == "non_execution":
        return _not_applicable()
    operation = profile.operation_for(request.task_intent)
    if operation is None:
        return _rejected("file_capability_intent_ambiguous")
    if profile.parser is None:
        return _rejected("file_capability_parser_unavailable")
    if any(attachment.size_bytes > profile.parser.max_bytes for attachment in attachments):
        return _rejected("file_capability_size_exceeded")
    if request.runtime_inventory.missing_requirements(profile.runtime_requirements):
        return _rejected("file_capability_runtime_dependency_unavailable")
    if not set(operation.required_artifact_types) <= request.runtime_inventory.artifact_types:
        return _rejected("file_capability_required_artifact_incompatible")
    selection, selection_rejection = _selection_for_profile(profile, request)
    if selection_rejection is not None:
        return _rejected(selection_rejection)
    resolution = await authorization_port.resolve_exactly_one(
        logical_skill_ids=profile.logical_skill_ids,
        selection=selection,
        binding=request.agent_binding,
    )
    authorization_rejection = _authorization_rejection(resolution)
    if authorization_rejection is not None:
        return _rejected(authorization_rejection)
    pin = resolution.pins[0]
    pin_rejection = _pin_compatibility_rejection(pin, profile, selection, request.agent_binding)
    if pin_rejection is not None:
        return _rejected(pin_rejection)
    return FileCapabilityAdmission(
        state=ADMISSION_REQUIRED,
        fallback_prohibited=True,
        rejection_code=None,
        registry_version=FILE_CAPABILITY_REGISTRY_VERSION,
        registry_digest=FILE_CAPABILITY_REGISTRY_DIGEST,
        selected_skill=SkillSelection(pin.skill_id, pin.expected_version),
        skill_pins=(pin,),
        parser_requirements=tuple(
            PrivateParserRequirement(
                file_id=attachment.file_id,
                verified_media_type=attachment.media_type,
                verified_extension=attachment.verified_extension,
                expected_size_bytes=attachment.size_bytes,
                expected_sha256=attachment.sha256,
                parser_id=profile.parser.parser_id,
                parser_version=profile.parser.parser_version,
                max_bytes=profile.parser.max_bytes,
            )
            for attachment in attachments
        ),
        runtime_image_digest=request.runtime_inventory.image_digest,
        runtime_requirements=profile.runtime_requirements,
        required_artifact_types=operation.required_artifact_types,
        public_progress_facts=_public_progress_facts(profile, pin, len(attachments)),
    )


def _profiles_for_attachments(
    attachments: tuple[AttachmentByteIdentity, ...],
) -> tuple[tuple[FileCapabilityProfile, ...], str | None]:
    resolved: list[FileCapabilityProfile] = []
    for attachment in attachments:
        pair = FileTypePair(attachment.media_type, attachment.verified_extension)
        matches = tuple(profile for profile in FILE_CAPABILITY_REGISTRY if pair in profile.pairs)
        if len(matches) != 1:
            return (), "file_capability_parser_ambiguous" if matches else "file_capability_type_unsupported"
        resolved.append(matches[0])
    return tuple(dict.fromkeys(resolved)), None


def _selection_for_profile(
    profile: FileCapabilityProfile, request: FileCapabilityAdmissionRequest
) -> tuple[SkillSelection | None, str | None]:
    if request.agent_binding is not None:
        bound = request.agent_binding.selected_skill
        if bound.skill_id not in profile.logical_skill_ids or (
            request.explicit_selection is not None and request.explicit_selection != bound
        ):
            return None, "file_capability_agent_profile_incompatible"
        return bound, None
    if request.explicit_selection is not None and request.explicit_selection.skill_id not in profile.logical_skill_ids:
        return None, "file_capability_caller_selection_incompatible"
    return request.explicit_selection, None


def _authorization_rejection(resolution: SkillAuthorizationResolution) -> str | None:
    if resolution.status != "authorized":
        return {
            "ambiguous": "file_capability_skill_ambiguous",
            "unavailable": "file_capability_skill_unavailable",
            "unauthorized": "file_capability_not_authorized",
            "stale": "file_capability_version_stale",
        }.get(resolution.status, "file_capability_skill_unavailable")
    return None if len(resolution.pins) == 1 else "file_capability_skill_ambiguous"


def _pin_compatibility_rejection(
    pin: AuthorizedSkillPin,
    profile: FileCapabilityProfile,
    selection: SkillSelection | None,
    binding: AgentSkillBinding | None,
) -> str | None:
    if pin.logical_skill_id not in profile.logical_skill_ids:
        return "file_capability_skill_unavailable"
    if selection is None:
        return None
    if pin.skill_id != selection.skill_id:
        return "file_capability_agent_profile_incompatible" if binding else "file_capability_caller_selection_incompatible"
    return None if pin.expected_version == selection.expected_version else "file_capability_version_stale"


def _public_progress_facts(
    profile: FileCapabilityProfile, pin: AuthorizedSkillPin, file_count: int
) -> tuple[PublicProgressFact, ...]:
    assert profile.parser is not None
    label = _safe_public_label(pin.public_label)
    if label is None:
        raise ValueError("authorization adapter returned an unsafe public label")
    return (
        PublicProgressFact("file_category", "admission", "completed", profile.public_category_label, file_count, file_count),
        PublicProgressFact("parser", "admission", "completed", profile.parser.public_label, file_count, file_count),
        PublicProgressFact("skill", "admission", "completed", label, file_count, file_count),
    )


def _not_applicable() -> FileCapabilityAdmission:
    return FileCapabilityAdmission(
        state=ADMISSION_NOT_APPLICABLE,
        fallback_prohibited=False,
        rejection_code=None,
        registry_version=FILE_CAPABILITY_REGISTRY_VERSION,
        registry_digest=FILE_CAPABILITY_REGISTRY_DIGEST,
    )


def _rejected(code: str) -> FileCapabilityAdmission:
    return FileCapabilityAdmission(
        state=ADMISSION_REJECTED,
        fallback_prohibited=True,
        rejection_code=code,
        registry_version=FILE_CAPABILITY_REGISTRY_VERSION,
        registry_digest=FILE_CAPABILITY_REGISTRY_DIGEST,
    )


if len({pair for profile in FILE_CAPABILITY_REGISTRY for pair in profile.pairs}) != sum(
    len(profile.pairs) for profile in FILE_CAPABILITY_REGISTRY
):
    raise RuntimeError("file capability registry has ambiguous type pairs")
