"""Fail-closed server admission for typed file capability requests.

This module intentionally has no route, repository, staging, or executor dependency.
Its one interface accepts already verified attachment facts and delegates published
Skill authorization to an injected adapter.  A route can therefore use the result
without allowing model text or caller-declared file metadata to select a Skill.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Literal, Protocol, TypeAlias

from app.file_parser_contracts import (
    MAX_XLSX_FILE_BYTES,
    XLSX_CONTENT_TYPE,
    XLSX_PARSER_ID,
    XLSX_PARSER_VERSION,
)
from app.projection_redaction import public_skill_display_label
from app.validation import assert_safe_id


FILE_CAPABILITY_REGISTRY_VERSION = "ai-platform.file-capability-registry.v1"

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
MultiFilePolicy: TypeAlias = Literal["single", "homogeneous"]

_EXECUTION_TASK_INTENTS = frozenset(
    {"analyze", "extract", "review", "transform", "generate_artifact"}
)
_PUBLIC_FACT_KINDS = frozenset({"file_category", "parser", "skill"})
_PUBLIC_FACT_STAGES = frozenset({"admission"})
_PUBLIC_FACT_STATUSES = frozenset({"completed"})
_CONTRACT_TOKEN_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._>=-]{0,127}$")


def _safe_public_label(value: object) -> str | None:
    """Accept one existing bounded public label and nothing derived from internal IDs."""

    return public_skill_display_label(value)


def _validate_sha256(value: str, field_name: str) -> None:
    normalized = str(value).lower()
    if len(normalized) != 64 or any(character not in "0123456789abcdef" for character in normalized):
        raise ValueError(f"{field_name} must be 64 lowercase hexadecimal characters")


def _validate_contract_tokens(values: tuple[str, ...] | frozenset[str], field_name: str) -> None:
    if any(not isinstance(value, str) or not _CONTRACT_TOKEN_PATTERN.fullmatch(value) for value in values):
        raise ValueError(f"{field_name} contains an invalid contract token")


@dataclass(frozen=True, slots=True)
class FileTypePair:
    """One exact byte-classified media type and verified extension pair."""

    media_type: str
    extension: str

    def __post_init__(self) -> None:
        if (
            not self.media_type
            or self.media_type != self.media_type.casefold()
            or ";" in self.media_type
            or "/" not in self.media_type
        ):
            raise ValueError("media_type must be a normalized MIME type")
        if (
            not self.extension.startswith(".")
            or self.extension != self.extension.casefold()
            or not re.fullmatch(r"\.[a-z0-9]{1,16}", self.extension)
        ):
            raise ValueError("extension must be a normalized verified extension")


@dataclass(frozen=True, slots=True)
class ParserIdentity:
    """Private exact parser identity selected from the reviewed registry."""

    parser_id: str
    parser_version: str
    max_bytes: int
    public_label: str

    def __post_init__(self) -> None:
        assert_safe_id(self.parser_id, "parser_id")
        if not self.parser_version or self.max_bytes < 1:
            raise ValueError("parser identity is invalid")
        if _safe_public_label(self.public_label) is None:
            raise ValueError("parser public label is invalid")


@dataclass(frozen=True, slots=True)
class FileCapabilityOperation:
    """One bounded task intent and its private delivery contract."""

    task_intent: FileTaskIntent
    required_artifact_types: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.task_intent not in _EXECUTION_TASK_INTENTS:
            raise ValueError("file capability operation must require execution")
        _validate_contract_tokens(self.required_artifact_types, "required_artifact_types")


@dataclass(frozen=True, slots=True)
class FileCapabilityProfile:
    """Reviewed profile that binds exact file identity to private execution needs."""

    profile_id: str
    category: str
    public_category_label: str
    pairs: tuple[FileTypePair, ...]
    multi_file_policy: MultiFilePolicy
    enabled: bool
    parser: ParserIdentity | None
    logical_skill_ids: tuple[str, ...]
    runtime_dependency_ids: tuple[str, ...]
    operations: tuple[FileCapabilityOperation, ...]

    def __post_init__(self) -> None:
        assert_safe_id(self.profile_id, "profile_id")
        if self.multi_file_policy not in {"single", "homogeneous"}:
            raise ValueError("unknown multi-file policy")
        if not self.pairs or len(set(self.pairs)) != len(self.pairs):
            raise ValueError("profile pairs must be unique and nonempty")
        if _safe_public_label(self.public_category_label) is None:
            raise ValueError("profile public category label is invalid")
        _validate_contract_tokens(self.logical_skill_ids, "logical_skill_ids")
        _validate_contract_tokens(self.runtime_dependency_ids, "runtime_dependency_ids")
        if len({operation.task_intent for operation in self.operations}) != len(self.operations):
            raise ValueError("profile operations must have unique task intents")
        if self.enabled and (self.parser is None or len(self.logical_skill_ids) != 1 or not self.operations):
            raise ValueError("enabled profile must have one parser, one Skill, and operations")
        if not self.enabled and (self.parser is not None or self.logical_skill_ids or self.operations):
            raise ValueError("disabled profile cannot claim executable authority")

    def operation_for(self, task_intent: str) -> FileCapabilityOperation | None:
        """Return the one reviewed operation for the normalized user task intent."""

        return next((operation for operation in self.operations if operation.task_intent == task_intent), None)


@dataclass(frozen=True, slots=True)
class TrustedAttachmentFact:
    """Authoritative upload fact; caller names and declared MIME are deliberately absent."""

    file_id: str
    verified_media_type: str
    verified_extension: str
    size_bytes: int
    sha256: str
    classifier_version: str

    def __post_init__(self) -> None:
        assert_safe_id(self.file_id, "file_id")
        FileTypePair(self.verified_media_type, self.verified_extension)
        if type(self.size_bytes) is not int or self.size_bytes < 0:
            raise ValueError("size_bytes must be a non-negative integer")
        _validate_sha256(self.sha256, "sha256")
        assert_safe_id(self.classifier_version, "classifier_version")

    @property
    def type_pair(self) -> FileTypePair:
        """Return the exact verified type pair used for registry matching."""

        return FileTypePair(self.verified_media_type, self.verified_extension)


@dataclass(frozen=True, slots=True)
class SkillSelection:
    """Private exact user or Agent Skill selection optimistic lock."""

    skill_id: str
    expected_version: str

    def __post_init__(self) -> None:
        assert_safe_id(self.skill_id, "skill_id")
        assert_safe_id(self.expected_version, "expected_version")


@dataclass(frozen=True, slots=True)
class AgentSkillBinding:
    """Immutable Agent selection constraint that a file profile cannot broaden."""

    agent_id: str
    selected_skill: SkillSelection

    def __post_init__(self) -> None:
        assert_safe_id(self.agent_id, "agent_id")


@dataclass(frozen=True, slots=True)
class ExecutionCapabilityInventory:
    """Private server-owned runtime availability snapshot for this proposed run."""

    dependency_ids: frozenset[str]
    artifact_types: frozenset[str]

    def __post_init__(self) -> None:
        _validate_contract_tokens(self.dependency_ids, "dependency_ids")
        _validate_contract_tokens(self.artifact_types, "artifact_types")


@dataclass(frozen=True, slots=True)
class FileCapabilityAdmissionRequest:
    """Immutable request to admit typed attachments without route or database policy."""

    attachments: tuple[TrustedAttachmentFact, ...]
    task_intent: FileTaskIntent
    runtime_inventory: ExecutionCapabilityInventory
    explicit_selection: SkillSelection | None = None
    agent_binding: AgentSkillBinding | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "attachments", tuple(self.attachments))


@dataclass(frozen=True, slots=True)
class AuthorizedSkillPin:
    """Private exact published distribution resolved by an authorization adapter."""

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
        if label is None or label.casefold() in {
            self.logical_skill_id.casefold(),
            self.skill_id.casefold(),
        }:
            raise ValueError("public_label is not safe")


@dataclass(frozen=True, slots=True)
class SkillAuthorizationResolution:
    """Authorization adapter result with no repository or catalog implementation detail."""

    status: AuthorizationStatus
    pins: tuple[AuthorizedSkillPin, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "pins", tuple(self.pins))
        if self.status not in {"authorized", "ambiguous", "unavailable", "unauthorized", "stale"}:
            raise ValueError("authorization status is invalid")


class SkillAuthorizationPort(Protocol):
    """Seam for published, authorized, version-pinned Skill resolution."""

    async def resolve_exactly_one(
        self,
        *,
        logical_skill_ids: tuple[str, ...],
        selection: SkillSelection | None,
        binding: AgentSkillBinding | None,
    ) -> SkillAuthorizationResolution:
        """Resolve exactly one authorized published Skill without selecting it from model text."""


@dataclass(frozen=True, slots=True)
class PrivateParserRequirement:
    """Private parser work contract bound to exact stored attachment bytes."""

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
    """Strict semantic progress projection with no raw attachment or Skill identity."""

    kind: Literal["file_category", "parser", "skill"]
    stage: Literal["admission"]
    status: Literal["completed"]
    label: str
    current: int
    total: int

    def __post_init__(self) -> None:
        if self.kind not in _PUBLIC_FACT_KINDS:
            raise ValueError("public progress kind is invalid")
        if self.stage not in _PUBLIC_FACT_STAGES or self.status not in _PUBLIC_FACT_STATUSES:
            raise ValueError("public progress lifecycle is invalid")
        if _safe_public_label(self.label) is None:
            raise ValueError("public progress label is invalid")
        if type(self.current) is not int or type(self.total) is not int or self.total < 1:
            raise ValueError("public progress counts are invalid")
        if not 0 <= self.current <= self.total:
            raise ValueError("public progress counts are inconsistent")

    def to_public_payload(self) -> dict[str, object]:
        """Serialize the only public fields allowed out of admission."""

        return {
            "kind": self.kind,
            "stage": self.stage,
            "status": self.status,
            "label": self.label,
            "progress": {"current": self.current, "total": self.total},
        }


@dataclass(frozen=True, slots=True)
class FileCapabilityAdmission:
    """Terminal server decision for a typed attachment capability request."""

    state: AdmissionState
    fallback_prohibited: bool
    rejection_code: str | None
    registry_version: str
    registry_digest: str
    selected_skill: SkillSelection | None = None
    skill_pins: tuple[AuthorizedSkillPin, ...] = ()
    parser_requirements: tuple[PrivateParserRequirement, ...] = ()
    runtime_dependency_ids: tuple[str, ...] = ()
    required_artifact_types: tuple[str, ...] = ()
    public_progress_facts: tuple[PublicProgressFact, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "skill_pins", tuple(self.skill_pins))
        object.__setattr__(self, "parser_requirements", tuple(self.parser_requirements))
        object.__setattr__(self, "runtime_dependency_ids", tuple(self.runtime_dependency_ids))
        object.__setattr__(self, "required_artifact_types", tuple(self.required_artifact_types))
        object.__setattr__(self, "public_progress_facts", tuple(self.public_progress_facts))
        if self.state not in {ADMISSION_NOT_APPLICABLE, ADMISSION_REQUIRED, ADMISSION_REJECTED}:
            raise ValueError("admission state is invalid")
        if self.state == ADMISSION_NOT_APPLICABLE:
            if self.fallback_prohibited or self.rejection_code is not None:
                raise ValueError("not applicable admission cannot prohibit fallback or reject")
            return
        if not self.fallback_prohibited:
            raise ValueError("typed required or rejected admission must prohibit fallback")
        if self.state == ADMISSION_REJECTED:
            if not self.rejection_code or any(
                (
                    self.selected_skill,
                    self.skill_pins,
                    self.parser_requirements,
                    self.runtime_dependency_ids,
                    self.required_artifact_types,
                    self.public_progress_facts,
                )
            ):
                raise ValueError("rejected admission cannot include an execution selection")
        if self.state == ADMISSION_REQUIRED:
            if self.rejection_code is not None or self.selected_skill is None or len(self.skill_pins) != 1:
                raise ValueError("required admission must contain one exact selected Skill")


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
    runtime_dependency_ids: tuple[str, ...] = (),
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
        runtime_dependency_ids=runtime_dependency_ids,
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
        parser=ParserIdentity(
            parser_id=XLSX_PARSER_ID,
            parser_version=XLSX_PARSER_VERSION,
            max_bytes=MAX_XLSX_FILE_BYTES,
            public_label="Spreadsheet analysis",
        ),
        logical_skill_ids=("qa-rag-skill",),
        runtime_dependency_ids=("python.openpyxl>=3.1",),
        operations=(
            FileCapabilityOperation("analyze"),
            FileCapabilityOperation("generate_artifact", ("xlsx",)),
        ),
    ),
    _profile("tabular.xls", "tabular", "Spreadsheet files", (FileTypePair("application/vnd.ms-excel", ".xls"),)),
    _profile("tabular.csv", "tabular", "Tabular files", (FileTypePair("text/csv", ".csv"),)),
    _profile("tabular.tsv", "tabular", "Tabular files", (FileTypePair("text/tab-separated-values", ".tsv"),)),
    _profile("document.pdf", "document", "Document files", (FileTypePair("application/pdf", ".pdf"),)),
    _profile(
        "document.docx",
        "document",
        "Document files",
        (FileTypePair("application/vnd.openxmlformats-officedocument.wordprocessingml.document", ".docx"),),
    ),
    _profile("document.txt", "document", "Document files", (FileTypePair("text/plain", ".txt"),)),
    _profile("document.md", "document", "Document files", (FileTypePair("text/markdown", ".md"),)),
    _profile("document.html", "document", "Document files", (FileTypePair("text/html", ".html"),)),
    _profile(
        "presentation.pptx",
        "presentation",
        "Presentation files",
        (FileTypePair("application/vnd.openxmlformats-officedocument.presentationml.presentation", ".pptx"),),
    ),
    _profile("image.png", "image", "Image files", (FileTypePair("image/png", ".png"),)),
    _profile(
        "image.jpeg",
        "image",
        "Image files",
        (FileTypePair("image/jpeg", ".jpeg"), FileTypePair("image/jpeg", ".jpg")),
    ),
    _profile(
        "image.tiff",
        "image",
        "Image files",
        (FileTypePair("image/tiff", ".tiff"), FileTypePair("image/tiff", ".tif")),
    ),
    _profile("structured.json", "structured", "Structured data", (FileTypePair("application/json", ".json"),)),
    _profile("structured.xml", "structured", "Structured data", (FileTypePair("application/xml", ".xml"),)),
    _profile("archive.reviewed", "archive", "Archive files", (FileTypePair("application/zip", ".zip"),)),
    _profile("media.audio", "media", "Audio files", (FileTypePair("audio/mpeg", ".mp3"),)),
    _profile("media.video", "media", "Video files", (FileTypePair("video/mp4", ".mp4"),)),
)

DANGEROUS_ATTACHMENT_PAIRS = frozenset(
    {
        FileTypePair("application/x-dosexec", ".exe"),
        FileTypePair("application/x-msdownload", ".exe"),
        FileTypePair("application/x-msi", ".msi"),
        FileTypePair("application/x-sh", ".sh"),
    }
)


def registry_digest(registry: tuple[FileCapabilityProfile, ...]) -> str:
    """Return the canonical digest of every reviewed profile and danger policy field."""

    descriptor = [
        {
            "profile_id": profile.profile_id,
            "category": profile.category,
            "public_category_label": profile.public_category_label,
            "pairs": [(pair.media_type, pair.extension) for pair in profile.pairs],
            "multi_file_policy": profile.multi_file_policy,
            "enabled": profile.enabled,
            "parser": (
                None
                if profile.parser is None
                else {
                    "parser_id": profile.parser.parser_id,
                    "parser_version": profile.parser.parser_version,
                    "max_bytes": profile.parser.max_bytes,
                    "public_label": profile.parser.public_label,
                }
            ),
            "logical_skill_ids": profile.logical_skill_ids,
            "runtime_dependency_ids": profile.runtime_dependency_ids,
            "operations": [
                {
                    "task_intent": operation.task_intent,
                    "required_artifact_types": operation.required_artifact_types,
                }
                for operation in profile.operations
            ],
        }
        for profile in registry
    ]
    encoded = json.dumps(
        {
            "version": FILE_CAPABILITY_REGISTRY_VERSION,
            "profiles": descriptor,
            "dangerous_attachment_pairs": sorted(
                (pair.media_type, pair.extension) for pair in DANGEROUS_ATTACHMENT_PAIRS
            ),
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


FILE_CAPABILITY_REGISTRY_DIGEST = registry_digest(FILE_CAPABILITY_REGISTRY)


async def admit_file_capability(
    request: FileCapabilityAdmissionRequest,
    *,
    authorization_port: SkillAuthorizationPort,
) -> FileCapabilityAdmission:
    """Admit one typed attachment request or return a terminal fail-closed decision."""

    attachments = request.attachments
    if not attachments:
        return _not_applicable()
    if any(not _is_trusted_attachment_fact(attachment) for attachment in attachments):
        return _rejected("file_capability_metadata_untrusted")
    if any(attachment.type_pair in DANGEROUS_ATTACHMENT_PAIRS for attachment in attachments):
        return _rejected("file_capability_type_dangerous")

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
    if not set(profile.runtime_dependency_ids) <= request.runtime_inventory.dependency_ids:
        return _rejected("file_capability_runtime_dependency_unavailable")
    if not set(operation.required_artifact_types) <= request.runtime_inventory.artifact_types:
        return _rejected("file_capability_required_artifact_incompatible")

    selected, selection_rejection = _selection_for_profile(profile, request)
    if selection_rejection is not None:
        return _rejected(selection_rejection)
    resolution = await authorization_port.resolve_exactly_one(
        logical_skill_ids=profile.logical_skill_ids,
        selection=selected,
        binding=request.agent_binding,
    )
    authorization_rejection = _authorization_rejection(resolution)
    if authorization_rejection is not None:
        return _rejected(authorization_rejection)
    pin = resolution.pins[0]
    pin_rejection = _pin_compatibility_rejection(pin, profile, selected, request.agent_binding)
    if pin_rejection is not None:
        return _rejected(pin_rejection)

    resolved_selection = SkillSelection(pin.skill_id, pin.expected_version)
    parser_requirements = tuple(
        PrivateParserRequirement(
            file_id=attachment.file_id,
            verified_media_type=attachment.verified_media_type,
            verified_extension=attachment.verified_extension,
            expected_size_bytes=attachment.size_bytes,
            expected_sha256=attachment.sha256,
            parser_id=profile.parser.parser_id,
            parser_version=profile.parser.parser_version,
            max_bytes=profile.parser.max_bytes,
        )
        for attachment in attachments
    )
    progress = _public_progress_facts(profile, pin, len(attachments))
    return FileCapabilityAdmission(
        state=ADMISSION_REQUIRED,
        fallback_prohibited=True,
        rejection_code=None,
        registry_version=FILE_CAPABILITY_REGISTRY_VERSION,
        registry_digest=FILE_CAPABILITY_REGISTRY_DIGEST,
        selected_skill=resolved_selection,
        skill_pins=(pin,),
        parser_requirements=parser_requirements,
        runtime_dependency_ids=profile.runtime_dependency_ids,
        required_artifact_types=operation.required_artifact_types,
        public_progress_facts=progress,
    )


def _profiles_for_attachments(
    attachments: tuple[TrustedAttachmentFact, ...],
) -> tuple[tuple[FileCapabilityProfile, ...], str | None]:
    resolved: list[FileCapabilityProfile] = []
    known_pairs = {pair for profile in FILE_CAPABILITY_REGISTRY for pair in profile.pairs}
    known_media_types = {pair.media_type for pair in known_pairs}
    known_extensions = {pair.extension for pair in known_pairs}
    for attachment in attachments:
        pair = attachment.type_pair
        matches = tuple(profile for profile in FILE_CAPABILITY_REGISTRY if pair in profile.pairs)
        if len(matches) != 1:
            if matches:
                return (), "file_capability_parser_ambiguous"
            if pair.media_type in known_media_types or pair.extension in known_extensions:
                return (), "file_capability_type_mismatch"
            return (), "file_capability_type_unsupported"
        resolved.append(matches[0])
    return tuple(dict.fromkeys(resolved)), None


def _selection_for_profile(
    profile: FileCapabilityProfile,
    request: FileCapabilityAdmissionRequest,
) -> tuple[SkillSelection | None, str | None]:
    explicit = request.explicit_selection
    binding = request.agent_binding
    if binding is not None:
        if binding.selected_skill.skill_id not in profile.logical_skill_ids:
            return None, "file_capability_agent_profile_incompatible"
        if explicit is not None and explicit != binding.selected_skill:
            return None, "file_capability_agent_profile_incompatible"
        return binding.selected_skill, None
    if explicit is not None and explicit.skill_id not in profile.logical_skill_ids:
        return None, "file_capability_caller_selection_incompatible"
    return explicit, None


def _authorization_rejection(resolution: SkillAuthorizationResolution) -> str | None:
    status_codes = {
        "ambiguous": "file_capability_skill_ambiguous",
        "unavailable": "file_capability_skill_unavailable",
        "unauthorized": "file_capability_not_authorized",
        "stale": "file_capability_version_stale",
    }
    if resolution.status != "authorized":
        return status_codes.get(resolution.status, "file_capability_skill_unavailable")
    if len(resolution.pins) != 1:
        return "file_capability_skill_ambiguous"
    return None


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
        return (
            "file_capability_agent_profile_incompatible"
            if binding is not None
            else "file_capability_caller_selection_incompatible"
        )
    if pin.expected_version != selection.expected_version:
        return "file_capability_version_stale"
    return None


def _public_progress_facts(
    profile: FileCapabilityProfile,
    pin: AuthorizedSkillPin,
    file_count: int,
) -> tuple[PublicProgressFact, ...]:
    assert profile.parser is not None
    public_skill_label = _safe_public_label(pin.public_label)
    if public_skill_label is None:
        raise ValueError("authorization adapter returned an unsafe public label")
    return (
        PublicProgressFact("file_category", "admission", "completed", profile.public_category_label, file_count, file_count),
        PublicProgressFact("parser", "admission", "completed", profile.parser.public_label, file_count, file_count),
        PublicProgressFact("skill", "admission", "completed", public_skill_label, file_count, file_count),
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


def _is_trusted_attachment_fact(value: object) -> bool:
    if not isinstance(value, TrustedAttachmentFact):
        return False
    try:
        assert_safe_id(value.file_id, "file_id")
        FileTypePair(value.verified_media_type, value.verified_extension)
        _validate_sha256(value.sha256, "sha256")
        assert_safe_id(value.classifier_version, "classifier_version")
    except (TypeError, ValueError):
        return False
    return type(value.size_bytes) is int and value.size_bytes >= 0


if len({pair for profile in FILE_CAPABILITY_REGISTRY for pair in profile.pairs}) != sum(
    len(profile.pairs) for profile in FILE_CAPABILITY_REGISTRY
):
    raise RuntimeError("file capability registry has ambiguous type pairs")
