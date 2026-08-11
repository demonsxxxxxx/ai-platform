"""Canonical file identity and capability policy.

This module is deliberately a pure contract.  It does not read storage, trust a
multipart MIME value, authorize an Agent or Skill, or change route behaviour.
Callers must first obtain a byte-verified identity or an immutable
server-authorized Agent binding, then use the fail-closed checks below.

``upload`` means the reviewed product upload surface, not every byte sequence
that the storage endpoint can currently retain.  Route/UI adoption is a
separate change so that this registry can be reviewed without changing public
behaviour.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from typing import Literal, Protocol, TypeAlias

from app.attachments.classification import (
    ATTACHMENT_CLASSIFICATION_REJECTION_CODES,
    ATTACHMENT_CLASSIFIER_VERSION,
)
from app.file_parser_contracts import (
    MAX_XLSX_FILE_BYTES,
    XLSX_CONTENT_TYPE,
    XLSX_PARSER_ID,
    XLSX_PARSER_VERSION,
)
from app.validation import assert_safe_id


FILE_CAPABILITY_REGISTRY_VERSION = "ai-platform.file-capability-registry.v3"
FILE_CAPABILITY_DECISION_SCHEMA_VERSION = "ai-platform.file-capability-decision.v1"
AGENT_FILE_BINDING_SCHEMA_VERSION = "ai-platform.agent-file-binding.v1"
AGENT_FILE_DECLARATION_EMPTY_POLICY = "deny_all"
SERVER_AUTHORIZED_AGENT_REVISION = "server_authorized_agent_revision"

CAPABILITY_SUPPORTED = "supported"
CAPABILITY_REJECTED = "rejected"
CapabilityDecisionState: TypeAlias = Literal["supported", "rejected"]
AgentFileAuthorizationStatus: TypeAlias = Literal[
    "authorized", "unavailable", "unauthorized", "stale"
]

CAPABILITY_UPLOAD = "upload"
CAPABILITY_TYPED_PARSE = "typed_parse"
CAPABILITY_INPUT_PREVIEW = "input_preview"
CAPABILITY_ARTIFACT_PREVIEW = "artifact_preview"
CAPABILITY_AGENT_INPUT = "agent_input"
FileCapabilityKind: TypeAlias = Literal[
    "upload",
    "typed_parse",
    "input_preview",
    "artifact_preview",
    "agent_input",
]

FILE_CAPABILITY_IDENTITY_INVALID = "file_capability_identity_invalid"
FILE_CAPABILITY_OPERATION_INVALID = "file_capability_operation_invalid"
FILE_CAPABILITY_FILES_REQUIRED = "file_capability_files_required"
FILE_CAPABILITY_TYPE_UNSUPPORTED = "file_capability_type_unsupported"
FILE_CAPABILITY_REGISTRY_AMBIGUOUS = "file_capability_registry_ambiguous"
FILE_CAPABILITY_PARSER_AMBIGUOUS = "file_capability_parser_ambiguous"
FILE_CAPABILITY_COMBINATION_UNSUPPORTED = "file_capability_combination_unsupported"
FILE_CAPABILITY_INTENT_AMBIGUOUS = "file_capability_intent_ambiguous"
FILE_CAPABILITY_PARSER_UNAVAILABLE = "file_capability_parser_unavailable"
FILE_CAPABILITY_SIZE_EXCEEDED = "file_capability_size_exceeded"
FILE_CAPABILITY_RUNTIME_DEPENDENCY_UNAVAILABLE = (
    "file_capability_runtime_dependency_unavailable"
)
FILE_CAPABILITY_REQUIRED_ARTIFACT_INCOMPATIBLE = (
    "file_capability_required_artifact_incompatible"
)
FILE_CAPABILITY_AGENT_PROFILE_INCOMPATIBLE = (
    "file_capability_agent_profile_incompatible"
)
FILE_CAPABILITY_CALLER_SELECTION_INCOMPATIBLE = (
    "file_capability_caller_selection_incompatible"
)
FILE_CAPABILITY_SKILL_AMBIGUOUS = "file_capability_skill_ambiguous"
FILE_CAPABILITY_SKILL_UNAVAILABLE = "file_capability_skill_unavailable"
FILE_CAPABILITY_NOT_AUTHORIZED = "file_capability_not_authorized"
FILE_CAPABILITY_VERSION_STALE = "file_capability_version_stale"
FILE_CAPABILITY_WORKSPACE_SKILL_MISMATCH = "file_capability_workspace_skill_mismatch"
FILE_CAPABILITY_UPLOAD_UNSUPPORTED = "file_capability_upload_unsupported"
FILE_CAPABILITY_TYPED_PARSE_UNSUPPORTED = "file_capability_typed_parse_unsupported"
FILE_CAPABILITY_INPUT_PREVIEW_UNSUPPORTED = "file_capability_input_preview_unsupported"
FILE_CAPABILITY_ARTIFACT_PREVIEW_UNSUPPORTED = (
    "file_capability_artifact_preview_unsupported"
)
FILE_CAPABILITY_AGENT_INPUT_UNSUPPORTED = "file_capability_agent_input_unsupported"
FILE_CAPABILITY_AGENT_BINDING_REQUIRED = "file_capability_agent_binding_required"
FILE_CAPABILITY_AGENT_BINDING_INVALID = "file_capability_agent_binding_invalid"
FILE_CAPABILITY_AGENT_BINDING_UNAVAILABLE = "file_capability_agent_binding_unavailable"
FILE_CAPABILITY_AGENT_NOT_AUTHORIZED = "file_capability_agent_not_authorized"
FILE_CAPABILITY_AGENT_REVISION_STALE = "file_capability_agent_revision_stale"
FILE_CAPABILITY_AGENT_DECLARATION_INVALID = "file_capability_agent_declaration_invalid"
FILE_CAPABILITY_AGENT_DECLARATION_INCONSISTENT = (
    "file_capability_agent_declaration_inconsistent"
)
FILE_CAPABILITY_AGENT_DECLARATION_EMPTY = "file_capability_agent_declaration_empty"
FILE_CAPABILITY_AGENT_TYPE_NOT_DECLARED = "file_capability_agent_type_not_declared"

FILE_CAPABILITY_REJECTION_CODES = frozenset(
    {
        FILE_CAPABILITY_IDENTITY_INVALID,
        FILE_CAPABILITY_OPERATION_INVALID,
        FILE_CAPABILITY_FILES_REQUIRED,
        FILE_CAPABILITY_TYPE_UNSUPPORTED,
        FILE_CAPABILITY_REGISTRY_AMBIGUOUS,
        FILE_CAPABILITY_PARSER_AMBIGUOUS,
        FILE_CAPABILITY_COMBINATION_UNSUPPORTED,
        FILE_CAPABILITY_INTENT_AMBIGUOUS,
        FILE_CAPABILITY_PARSER_UNAVAILABLE,
        FILE_CAPABILITY_SIZE_EXCEEDED,
        FILE_CAPABILITY_RUNTIME_DEPENDENCY_UNAVAILABLE,
        FILE_CAPABILITY_REQUIRED_ARTIFACT_INCOMPATIBLE,
        FILE_CAPABILITY_AGENT_PROFILE_INCOMPATIBLE,
        FILE_CAPABILITY_CALLER_SELECTION_INCOMPATIBLE,
        FILE_CAPABILITY_SKILL_AMBIGUOUS,
        FILE_CAPABILITY_SKILL_UNAVAILABLE,
        FILE_CAPABILITY_NOT_AUTHORIZED,
        FILE_CAPABILITY_VERSION_STALE,
        FILE_CAPABILITY_WORKSPACE_SKILL_MISMATCH,
        FILE_CAPABILITY_UPLOAD_UNSUPPORTED,
        FILE_CAPABILITY_TYPED_PARSE_UNSUPPORTED,
        FILE_CAPABILITY_INPUT_PREVIEW_UNSUPPORTED,
        FILE_CAPABILITY_ARTIFACT_PREVIEW_UNSUPPORTED,
        FILE_CAPABILITY_AGENT_INPUT_UNSUPPORTED,
        FILE_CAPABILITY_AGENT_BINDING_REQUIRED,
        FILE_CAPABILITY_AGENT_BINDING_INVALID,
        FILE_CAPABILITY_AGENT_BINDING_UNAVAILABLE,
        FILE_CAPABILITY_AGENT_NOT_AUTHORIZED,
        FILE_CAPABILITY_AGENT_REVISION_STALE,
        FILE_CAPABILITY_AGENT_DECLARATION_INVALID,
        FILE_CAPABILITY_AGENT_DECLARATION_INCONSISTENT,
        FILE_CAPABILITY_AGENT_DECLARATION_EMPTY,
        FILE_CAPABILITY_AGENT_TYPE_NOT_DECLARED,
    }
)

RuntimeDependencyKind: TypeAlias = Literal[
    "python_runtime", "prebuilt_python", "node_npm"
]
_CAPABILITY_KINDS = frozenset(
    {
        CAPABILITY_UPLOAD,
        CAPABILITY_TYPED_PARSE,
        CAPABILITY_INPUT_PREVIEW,
        CAPABILITY_ARTIFACT_PREVIEW,
        CAPABILITY_AGENT_INPUT,
    }
)
_OPERATION_REJECTION = {
    CAPABILITY_UPLOAD: FILE_CAPABILITY_UPLOAD_UNSUPPORTED,
    CAPABILITY_TYPED_PARSE: FILE_CAPABILITY_TYPED_PARSE_UNSUPPORTED,
    CAPABILITY_INPUT_PREVIEW: FILE_CAPABILITY_INPUT_PREVIEW_UNSUPPORTED,
    CAPABILITY_ARTIFACT_PREVIEW: FILE_CAPABILITY_ARTIFACT_PREVIEW_UNSUPPORTED,
    CAPABILITY_AGENT_INPUT: FILE_CAPABILITY_AGENT_INPUT_UNSUPPORTED,
}
_MEDIA_TYPE = re.compile(r"^[a-z0-9][a-z0-9!#$&^_.+-]*/[a-z0-9][a-z0-9!#$&^_.+-]*$")
_EXTENSION = re.compile(r"^\.[a-z0-9][a-z0-9.+-]{0,31}$")
_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_HASH = re.compile(r"^[0-9a-f]{64}$")


class FileCapabilityContractError(ValueError):
    """Stable construction error for data that cannot become authoritative."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True, order=True)
class VerifiedFileIdentity:
    """Exact normalized identity derived from trusted byte/metadata verification."""

    media_type: str
    verified_extension: str

    def __post_init__(self) -> None:
        if (
            not isinstance(self.media_type, str)
            or not isinstance(self.verified_extension, str)
            or self.media_type != self.media_type.strip().casefold()
            or self.verified_extension != self.verified_extension.strip().casefold()
            or not _MEDIA_TYPE.fullmatch(self.media_type)
            or not _EXTENSION.fullmatch(self.verified_extension)
        ):
            raise FileCapabilityContractError(FILE_CAPABILITY_IDENTITY_INVALID)


@dataclass(frozen=True, slots=True)
class FileCapabilitySupport:
    """Five independent capabilities for one or more exact file identities."""

    upload: bool
    typed_parse: bool
    input_preview: bool
    artifact_preview: bool
    agent_input: bool

    def __post_init__(self) -> None:
        if any(type(value) is not bool for value in asdict(self).values()):
            raise ValueError("file capability support values must be booleans")
        if self.agent_input and not self.typed_parse:
            raise ValueError("Agent file input requires a typed parser")

    def supports(self, capability: FileCapabilityKind) -> bool:
        return bool(getattr(self, capability))


@dataclass(frozen=True, slots=True)
class ParserIdentity:
    """Reviewed typed parser identity; never selected from caller metadata."""

    parser_id: str
    parser_version: str
    max_bytes: int
    public_label: str

    def __post_init__(self) -> None:
        if (
            not _TOKEN.fullmatch(self.parser_id)
            or not _TOKEN.fullmatch(self.parser_version)
            or type(self.max_bytes) is not int
            or self.max_bytes < 1
            or not isinstance(self.public_label, str)
            or self.public_label != self.public_label.strip()
            or not self.public_label
            or len(self.public_label.encode("utf-8")) > 160
            or any(ord(char) < 32 or ord(char) == 127 for char in self.public_label)
        ):
            raise ValueError("parser identity is invalid")


@dataclass(frozen=True, slots=True)
class RuntimeDependencyRequirement:
    """A prerequisite that must already exist in an immutable runtime image."""

    kind: RuntimeDependencyKind
    dependency_id: str
    minimum_version: str | None = None
    require_non_root: bool = False

    def __post_init__(self) -> None:
        if (
            self.kind not in {"python_runtime", "prebuilt_python", "node_npm"}
            or not _TOKEN.fullmatch(self.dependency_id)
            or (
                self.minimum_version is not None
                and not _TOKEN.fullmatch(self.minimum_version)
            )
            or type(self.require_non_root) is not bool
        ):
            raise ValueError("runtime dependency requirement is invalid")


@dataclass(frozen=True, slots=True)
class FileCapabilityProfile:
    """Canonical matrix row plus optional reviewed Skill-execution policy."""

    profile_id: str
    category_label: str
    identities: tuple[VerifiedFileIdentity, ...]
    capabilities: FileCapabilitySupport
    enabled: bool = False
    parser: ParserIdentity | None = None
    logical_skill_id: str | None = None
    runtime_requirements: tuple[RuntimeDependencyRequirement, ...] = ()
    operations: tuple[tuple[str, tuple[str, ...]], ...] = ()
    homogeneous: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "identities", tuple(self.identities))
        object.__setattr__(
            self, "runtime_requirements", tuple(self.runtime_requirements)
        )
        object.__setattr__(
            self,
            "operations",
            tuple(
                (operation, tuple(artifacts))
                for operation, artifacts in self.operations
            ),
        )
        if (
            not _TOKEN.fullmatch(self.profile_id)
            or not self.category_label
            or not self.identities
            or not all(
                isinstance(item, VerifiedFileIdentity) for item in self.identities
            )
            or len(set(self.identities)) != len(self.identities)
            or not isinstance(self.capabilities, FileCapabilitySupport)
            or type(self.enabled) is not bool
            or type(self.homogeneous) is not bool
            or not all(
                isinstance(item, RuntimeDependencyRequirement)
                for item in self.runtime_requirements
            )
        ):
            raise ValueError("file capability profile is invalid")
        if self.capabilities.typed_parse != (self.parser is not None):
            raise ValueError(
                "typed parse capability must have exactly one reviewed parser"
            )
        if self.enabled and (
            not self.capabilities.agent_input
            or self.parser is None
            or not self.logical_skill_id
            or not self.operations
        ):
            raise ValueError("enabled execution profile is incomplete")
        if self.logical_skill_id is not None:
            assert_safe_id(self.logical_skill_id, "logical_skill_id")

    @property
    def pairs(self) -> tuple[tuple[str, str], ...]:
        """Compatibility projection for the existing execution admission helper."""

        return tuple(
            (item.media_type, item.verified_extension) for item in self.identities
        )


_NONE = FileCapabilitySupport(False, False, False, False, False)
_UPLOAD_ONLY = FileCapabilitySupport(True, False, False, False, False)
_UPLOAD_PREVIEW = FileCapabilitySupport(True, False, True, False, False)
_UPLOAD_PREVIEW_ARTIFACT = FileCapabilitySupport(True, False, True, True, False)
_PARSED_INPUT = FileCapabilitySupport(True, True, True, False, True)
_PARSED_INPUT_ARTIFACT = FileCapabilitySupport(True, True, True, True, True)

_TEXT_PARSER = ParserIdentity(
    "ai-platform.text.utf8", "1", 1024 * 1024, "Text extraction"
)
_DOCX_PARSER = ParserIdentity(
    "ai-platform.docx.python-docx", "1", 32 * 1024 * 1024, "Word document extraction"
)
_PDF_PARSER = ParserIdentity(
    "ai-platform.pdf.pypdf", "1", 32 * 1024 * 1024, "PDF extraction"
)
_XLSX_PARSER = ParserIdentity(
    XLSX_PARSER_ID,
    XLSX_PARSER_VERSION,
    MAX_XLSX_FILE_BYTES,
    "Spreadsheet analysis",
)


def _identities(*pairs: tuple[str, str]) -> tuple[VerifiedFileIdentity, ...]:
    return tuple(VerifiedFileIdentity(*pair) for pair in pairs)


def _profile(
    profile_id: str,
    label: str,
    pairs: tuple[tuple[str, str], ...],
    capabilities: FileCapabilitySupport,
    *,
    parser: ParserIdentity | None = None,
) -> FileCapabilityProfile:
    return FileCapabilityProfile(
        profile_id=profile_id,
        category_label=label,
        identities=_identities(*pairs),
        capabilities=capabilities,
        parser=parser,
    )


FILE_CAPABILITY_REGISTRY = (
    FileCapabilityProfile(
        profile_id="tabular.xlsx",
        category_label="Spreadsheet files",
        identities=_identities((XLSX_CONTENT_TYPE, ".xlsx")),
        capabilities=_PARSED_INPUT_ARTIFACT,
        enabled=True,
        parser=_XLSX_PARSER,
        logical_skill_id="qa-rag-skill",
        runtime_requirements=(
            RuntimeDependencyRequirement("python_runtime", "python", "3.11", True),
            RuntimeDependencyRequirement("prebuilt_python", "openpyxl", "3.1"),
        ),
        operations=(("analyze", ()), ("generate_artifact", ("xlsx",))),
        homogeneous=True,
    ),
    _profile(
        "tabular.xls",
        "Spreadsheet files",
        (("application/vnd.ms-excel", ".xls"),),
        _UPLOAD_ONLY,
    ),
    _profile(
        "tabular.csv",
        "Tabular files",
        (("text/csv", ".csv"),),
        _PARSED_INPUT,
        parser=_TEXT_PARSER,
    ),
    _profile(
        "tabular.tsv", "Tabular files", (("text/tab-separated-values", ".tsv"),), _NONE
    ),
    _profile(
        "document.pdf",
        "Document files",
        (("application/pdf", ".pdf"),),
        _PARSED_INPUT_ARTIFACT,
        parser=_PDF_PARSER,
    ),
    _profile(
        "document.docx",
        "Document files",
        (
            (
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                ".docx",
            ),
        ),
        _PARSED_INPUT_ARTIFACT,
        parser=_DOCX_PARSER,
    ),
    _profile(
        "document.doc",
        "Document files",
        (("application/msword", ".doc"),),
        _UPLOAD_ONLY,
    ),
    _profile(
        "document.txt",
        "Document files",
        (("text/plain", ".txt"),),
        _PARSED_INPUT,
        parser=_TEXT_PARSER,
    ),
    _profile(
        "document.md",
        "Document files",
        (("text/markdown", ".md"), ("text/markdown", ".markdown")),
        _PARSED_INPUT,
        parser=_TEXT_PARSER,
    ),
    _profile("document.html", "Document files", (("text/html", ".html"),), _NONE),
    _profile(
        "presentation.pptx",
        "Presentation files",
        (
            (
                "application/vnd.openxmlformats-officedocument.presentationml.presentation",
                ".pptx",
            ),
        ),
        _UPLOAD_PREVIEW_ARTIFACT,
    ),
    _profile(
        "presentation.ppt",
        "Presentation files",
        (("application/vnd.ms-powerpoint", ".ppt"),),
        _UPLOAD_ONLY,
    ),
    _profile("image.avif", "Image files", (("image/avif", ".avif"),), _UPLOAD_PREVIEW),
    _profile("image.bmp", "Image files", (("image/bmp", ".bmp"),), _UPLOAD_PREVIEW),
    _profile("image.gif", "Image files", (("image/gif", ".gif"),), _UPLOAD_PREVIEW),
    _profile("image.png", "Image files", (("image/png", ".png"),), _UPLOAD_PREVIEW),
    _profile(
        "image.jpeg",
        "Image files",
        (("image/jpeg", ".jpeg"), ("image/jpeg", ".jpg")),
        _UPLOAD_PREVIEW,
    ),
    _profile(
        "image.tiff",
        "Image files",
        (("image/tiff", ".tiff"), ("image/tiff", ".tif")),
        _UPLOAD_PREVIEW,
    ),
    _profile("image.webp", "Image files", (("image/webp", ".webp"),), _UPLOAD_PREVIEW),
    _profile(
        "structured.json",
        "Structured data",
        (("application/json", ".json"),),
        _PARSED_INPUT,
        parser=_TEXT_PARSER,
    ),
    _profile(
        "structured.xml", "Structured data", (("application/xml", ".xml"),), _NONE
    ),
    _profile(
        "archive.reviewed", "Archive files", (("application/zip", ".zip"),), _NONE
    ),
    _profile("media.audio", "Audio files", (("audio/mpeg", ".mp3"),), _UPLOAD_ONLY),
    _profile("media.video", "Video files", (("video/mp4", ".mp4"),), _UPLOAD_ONLY),
)


def matching_file_capability_profiles(
    identity: VerifiedFileIdentity,
    *,
    registry: tuple[FileCapabilityProfile, ...] = FILE_CAPABILITY_REGISTRY,
) -> tuple[FileCapabilityProfile, ...]:
    """Return exact pair matches; callers must reject zero or multiple matches."""

    if not isinstance(identity, VerifiedFileIdentity):
        return ()
    return tuple(profile for profile in registry if identity in profile.identities)


def _registry_descriptor(
    registry: tuple[FileCapabilityProfile, ...],
) -> dict[str, object]:
    return {
        "version": FILE_CAPABILITY_REGISTRY_VERSION,
        "classifier": ATTACHMENT_CLASSIFIER_VERSION,
        "classification_rejection_codes": sorted(
            ATTACHMENT_CLASSIFICATION_REJECTION_CODES
        ),
        "agent_empty_declaration": AGENT_FILE_DECLARATION_EMPTY_POLICY,
        "rejection_codes": sorted(FILE_CAPABILITY_REJECTION_CODES),
        "decision_contract": {
            "schema": FILE_CAPABILITY_DECISION_SCHEMA_VERSION,
            "capabilities": sorted(_CAPABILITY_KINDS),
            "operation_rejections": dict(sorted(_OPERATION_REJECTION.items())),
            "batch_semantics": "all_files_atomic",
            "fallback": "prohibited",
            "agent_binding_schema": AGENT_FILE_BINDING_SCHEMA_VERSION,
            "agent_binding_authority": SERVER_AUTHORIZED_AGENT_REVISION,
            "agent_authorization": "exact_revision_port_required",
            "agent_requirements": (
                "file_input_declared",
                "non_empty_declaration",
                "all_identities_declared",
                "typed_parse",
                "selected_skill_exact",
            ),
        },
        "profiles": [asdict(profile) for profile in registry],
    }


def registry_digest(registry: tuple[FileCapabilityProfile, ...]) -> str:
    """Return a deterministic digest over the complete reviewed matrix policy."""

    payload = json.dumps(
        _registry_descriptor(tuple(registry)),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(payload).hexdigest()


FILE_CAPABILITY_REGISTRY_DIGEST = registry_digest(FILE_CAPABILITY_REGISTRY)


@dataclass(frozen=True, slots=True)
class AgentFileAuthorizationResolution:
    """Exact Agent revision facts returned only by the server authority port."""

    status: AgentFileAuthorizationStatus
    agent_id: str | None = None
    agent_revision: int | None = None
    profile_sha256: str | None = None
    supported_input_types: tuple[str, ...] = ()
    supported_file_types: tuple[str, ...] = ()
    selected_skill_id: str | None = None
    selected_skill_version: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "supported_input_types", tuple(self.supported_input_types)
        )
        object.__setattr__(
            self, "supported_file_types", tuple(self.supported_file_types)
        )
        if self.status == "authorized":
            if self.agent_id is None:
                raise ValueError("authorized Agent file resolution is incomplete")
            assert_safe_id(self.agent_id, "agent_id")
            if (
                type(self.agent_revision) is not int
                or self.agent_revision < 0
                or not isinstance(self.profile_sha256, str)
                or not _HASH.fullmatch(self.profile_sha256)
                or self.selected_skill_id is None
                or self.selected_skill_version is None
                or not all(isinstance(item, str) for item in self.supported_input_types)
                or not all(isinstance(item, str) for item in self.supported_file_types)
            ):
                raise ValueError("authorized Agent file resolution is incomplete")
            assert_safe_id(self.selected_skill_id, "selected_skill_id")
            assert_safe_id(self.selected_skill_version, "selected_skill_version")
            return
        if self.status not in {"unavailable", "unauthorized", "stale"}:
            raise ValueError("Agent file authorization status is invalid")
        if (
            self.agent_id is not None
            or self.agent_revision is not None
            or self.profile_sha256 is not None
            or self.supported_input_types
            or self.supported_file_types
            or self.selected_skill_id is not None
            or self.selected_skill_version is not None
        ):
            raise ValueError("rejected Agent file resolution contains private facts")


class AgentFileAuthorizationPort(Protocol):
    """Server-owned exact Agent revision lookup; raw request data is not authority."""

    async def resolve_authorized_revision(
        self,
        *,
        agent_id: str,
        expected_revision: int,
        expected_profile_sha256: str,
    ) -> AgentFileAuthorizationResolution:
        """Return current authorized revision facts or one bounded status."""


class ServerAuthorizedAgentFileBinding(Protocol):
    """Opaque internal result derived from an authorized port resolution."""

    agent_id: str
    agent_revision: int
    profile_sha256: str
    file_input_declared: bool
    declared_identities: tuple[VerifiedFileIdentity, ...]
    selected_skill_id: str
    selected_skill_version: str
    authority: Literal["server_authorized_agent_revision"]


def _agent_binding_digest(
    *,
    agent_id: str,
    agent_revision: int,
    profile_sha256: str,
    file_input_declared: bool,
    declared_identities: tuple[VerifiedFileIdentity, ...],
    selected_skill_id: str,
    selected_skill_version: str,
) -> str:
    descriptor = {
        "schema": AGENT_FILE_BINDING_SCHEMA_VERSION,
        "authority": SERVER_AUTHORIZED_AGENT_REVISION,
        "agent_id": agent_id,
        "agent_revision": agent_revision,
        "profile_sha256": profile_sha256,
        "file_input_declared": file_input_declared,
        "declared_identities": [asdict(identity) for identity in declared_identities],
        "selected_skill_id": selected_skill_id,
        "selected_skill_version": selected_skill_version,
    }
    payload = json.dumps(
        descriptor,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True, slots=True)
class _ServerAuthorizedAgentFileBinding:
    """Private declaration pinned to one server-authorized Agent revision.

    An empty ``declared_identities`` tuple means that the Agent accepts no file
    input.  It never means every registry type and never enables a fallback.
    """

    agent_id: str
    agent_revision: int
    profile_sha256: str
    file_input_declared: bool
    declared_identities: tuple[VerifiedFileIdentity, ...]
    selected_skill_id: str
    selected_skill_version: str
    declaration_sha256: str
    authority: Literal["server_authorized_agent_revision"] = (
        SERVER_AUTHORIZED_AGENT_REVISION
    )

    def __post_init__(self) -> None:
        assert_safe_id(self.agent_id, "agent_id")
        object.__setattr__(self, "declared_identities", tuple(self.declared_identities))
        if (
            type(self.agent_revision) is not int
            or self.agent_revision < 0
            or not _HASH.fullmatch(self.profile_sha256)
            or type(self.file_input_declared) is not bool
            or self.authority != SERVER_AUTHORIZED_AGENT_REVISION
            or not _HASH.fullmatch(self.declaration_sha256)
            or not all(
                isinstance(item, VerifiedFileIdentity)
                for item in self.declared_identities
            )
            or len(set(self.declared_identities)) != len(self.declared_identities)
            or (not self.file_input_declared and bool(self.declared_identities))
        ):
            raise FileCapabilityContractError(FILE_CAPABILITY_AGENT_BINDING_INVALID)
        try:
            assert_safe_id(self.selected_skill_id, "selected_skill_id")
            assert_safe_id(self.selected_skill_version, "selected_skill_version")
        except (TypeError, ValueError) as exc:
            raise FileCapabilityContractError(
                FILE_CAPABILITY_AGENT_BINDING_INVALID
            ) from exc
        expected = _agent_binding_digest(
            agent_id=self.agent_id,
            agent_revision=self.agent_revision,
            profile_sha256=self.profile_sha256,
            file_input_declared=self.file_input_declared,
            declared_identities=self.declared_identities,
            selected_skill_id=self.selected_skill_id,
            selected_skill_version=self.selected_skill_version,
        )
        if self.declaration_sha256 != expected:
            raise FileCapabilityContractError(FILE_CAPABILITY_AGENT_BINDING_INVALID)


def _binding_from_authorized_resolution(
    resolution: AgentFileAuthorizationResolution,
) -> _ServerAuthorizedAgentFileBinding:
    """Resolve one port-authorized revision to an opaque exact binding.

    Exact MIME, profile id, and unique bare/dotted extension declarations are
    accepted only when they resolve to reviewed Agent-input rows.
    """

    if resolution.status != "authorized":
        raise FileCapabilityContractError(FILE_CAPABILITY_AGENT_BINDING_INVALID)
    assert resolution.agent_id is not None
    assert resolution.agent_revision is not None
    assert resolution.profile_sha256 is not None
    assert resolution.selected_skill_id is not None
    assert resolution.selected_skill_version is not None
    supported_input_types = resolution.supported_input_types
    supported_file_types = resolution.supported_file_types
    if (
        not all(
            isinstance(item, str) and item in {"text", "file"}
            for item in supported_input_types
        )
        or len(set(supported_input_types)) != len(supported_input_types)
        or not isinstance(supported_file_types, tuple)
        or not all(isinstance(item, str) for item in supported_file_types)
    ):
        raise FileCapabilityContractError(FILE_CAPABILITY_AGENT_DECLARATION_INVALID)
    file_input_declared = "file" in supported_input_types
    if not file_input_declared and supported_file_types:
        raise FileCapabilityContractError(
            FILE_CAPABILITY_AGENT_DECLARATION_INCONSISTENT
        )
    identities: list[VerifiedFileIdentity] = []
    for raw_declaration in supported_file_types:
        declaration = raw_declaration.strip().casefold()
        if not declaration or declaration != raw_declaration:
            raise FileCapabilityContractError(FILE_CAPABILITY_AGENT_DECLARATION_INVALID)
        profiles = tuple(
            profile
            for profile in FILE_CAPABILITY_REGISTRY
            if profile.profile_id == declaration
        )
        matches = tuple(
            identity for profile in profiles for identity in profile.identities
        )
        if not matches and "/" in declaration:
            matches = tuple(
                identity
                for profile in FILE_CAPABILITY_REGISTRY
                for identity in profile.identities
                if identity.media_type == declaration
            )
        if not matches and "/" not in declaration:
            extension = (
                declaration if declaration.startswith(".") else f".{declaration}"
            )
            matches = tuple(
                identity
                for profile in FILE_CAPABILITY_REGISTRY
                for identity in profile.identities
                if identity.verified_extension == extension
            )
        if not matches:
            raise FileCapabilityContractError(FILE_CAPABILITY_AGENT_DECLARATION_INVALID)
        if any(identity in identities for identity in matches):
            raise FileCapabilityContractError(FILE_CAPABILITY_AGENT_DECLARATION_INVALID)
        for identity in matches:
            profiles = matching_file_capability_profiles(identity)
            if len(profiles) != 1 or not profiles[0].capabilities.agent_input:
                raise FileCapabilityContractError(
                    FILE_CAPABILITY_AGENT_INPUT_UNSUPPORTED
                )
            identities.append(identity)
    declared_identities = tuple(identities)
    declaration_sha256 = _agent_binding_digest(
        agent_id=resolution.agent_id,
        agent_revision=resolution.agent_revision,
        profile_sha256=resolution.profile_sha256,
        file_input_declared=file_input_declared,
        declared_identities=declared_identities,
        selected_skill_id=resolution.selected_skill_id,
        selected_skill_version=resolution.selected_skill_version,
    )
    return _ServerAuthorizedAgentFileBinding(
        agent_id=resolution.agent_id,
        agent_revision=resolution.agent_revision,
        profile_sha256=resolution.profile_sha256,
        file_input_declared=file_input_declared,
        declared_identities=declared_identities,
        selected_skill_id=resolution.selected_skill_id,
        selected_skill_version=resolution.selected_skill_version,
        declaration_sha256=declaration_sha256,
    )


@dataclass(frozen=True, slots=True)
class FileCapabilityDecision:
    """Atomic all-files decision with stable fail-closed metadata."""

    state: CapabilityDecisionState
    capability: str
    fallback_prohibited: bool
    rejection_code: str | None
    registry_version: str
    registry_digest: str

    def __post_init__(self) -> None:
        if (
            self.registry_version != FILE_CAPABILITY_REGISTRY_VERSION
            or self.registry_digest != FILE_CAPABILITY_REGISTRY_DIGEST
            or not isinstance(self.capability, str)
            or type(self.fallback_prohibited) is not bool
        ):
            raise ValueError("file capability decision is invalid")
        invalid_operation = (
            self.capability not in _CAPABILITY_KINDS
            and self.state == CAPABILITY_REJECTED
            and self.rejection_code == FILE_CAPABILITY_OPERATION_INVALID
        )
        if self.fallback_prohibited is not True:
            raise ValueError("file capability decision is invalid")
        if invalid_operation:
            return
        if self.capability not in _CAPABILITY_KINDS:
            raise ValueError("file capability decision is invalid")
        if self.state == CAPABILITY_SUPPORTED and self.rejection_code is None:
            return
        if (
            self.state == CAPABILITY_REJECTED
            and self.rejection_code in FILE_CAPABILITY_REJECTION_CODES
        ):
            return
        raise ValueError("file capability decision is invalid")


def check_file_capabilities(
    identities: tuple[VerifiedFileIdentity, ...],
    *,
    capability: FileCapabilityKind,
) -> FileCapabilityDecision:
    """Check non-Agent axes; Agent input must use the authorization-port helper."""

    return _check_file_capabilities(
        identities,
        capability=capability,
        agent_binding=None,
    )


def _check_file_capabilities(
    identities: tuple[VerifiedFileIdentity, ...],
    *,
    capability: FileCapabilityKind,
    agent_binding: _ServerAuthorizedAgentFileBinding | None,
) -> FileCapabilityDecision:
    """Require every file in one atomic decision after any authority lookup."""

    digest = FILE_CAPABILITY_REGISTRY_DIGEST
    if capability not in _CAPABILITY_KINDS:
        return _rejected(capability, FILE_CAPABILITY_OPERATION_INVALID, digest)
    if not isinstance(identities, tuple) or not all(
        isinstance(item, VerifiedFileIdentity) for item in identities
    ):
        return _rejected(capability, FILE_CAPABILITY_IDENTITY_INVALID, digest)
    if not identities:
        return _rejected(capability, FILE_CAPABILITY_FILES_REQUIRED, digest)

    profiles: list[FileCapabilityProfile] = []
    for identity in identities:
        matches = matching_file_capability_profiles(identity)
        if not matches:
            return _rejected(capability, FILE_CAPABILITY_TYPE_UNSUPPORTED, digest)
        if len(matches) != 1:
            return _rejected(capability, FILE_CAPABILITY_REGISTRY_AMBIGUOUS, digest)
        profiles.append(matches[0])

    if capability == CAPABILITY_AGENT_INPUT:
        if agent_binding is None:
            return _rejected(capability, FILE_CAPABILITY_AGENT_BINDING_REQUIRED, digest)
        if not isinstance(agent_binding, _ServerAuthorizedAgentFileBinding):
            return _rejected(capability, FILE_CAPABILITY_AGENT_BINDING_INVALID, digest)
        expected_binding_digest = _agent_binding_digest(
            agent_id=agent_binding.agent_id,
            agent_revision=agent_binding.agent_revision,
            profile_sha256=agent_binding.profile_sha256,
            file_input_declared=agent_binding.file_input_declared,
            declared_identities=agent_binding.declared_identities,
            selected_skill_id=agent_binding.selected_skill_id,
            selected_skill_version=agent_binding.selected_skill_version,
        )
        if agent_binding.declaration_sha256 != expected_binding_digest:
            return _rejected(capability, FILE_CAPABILITY_AGENT_BINDING_INVALID, digest)
        if (
            not agent_binding.file_input_declared
            or not agent_binding.declared_identities
        ):
            return _rejected(
                capability, FILE_CAPABILITY_AGENT_DECLARATION_EMPTY, digest
            )
        if any(
            identity not in agent_binding.declared_identities for identity in identities
        ):
            return _rejected(
                capability, FILE_CAPABILITY_AGENT_TYPE_NOT_DECLARED, digest
            )

    for profile in profiles:
        if not profile.capabilities.supports(capability):
            return _rejected(capability, _OPERATION_REJECTION[capability], digest)
    return FileCapabilityDecision(
        CAPABILITY_SUPPORTED,
        capability,
        True,
        None,
        FILE_CAPABILITY_REGISTRY_VERSION,
        digest,
    )


async def authorize_agent_file_capabilities(
    identities: tuple[VerifiedFileIdentity, ...],
    *,
    agent_id: str,
    expected_revision: int,
    expected_profile_sha256: str,
    authorization_port: AgentFileAuthorizationPort,
) -> FileCapabilityDecision:
    """Resolve one exact authorized Agent revision, then check all file identities."""

    binding, rejection = await _resolve_authorized_agent_file_binding(
        agent_id=agent_id,
        expected_revision=expected_revision,
        expected_profile_sha256=expected_profile_sha256,
        authorization_port=authorization_port,
    )
    if rejection:
        return _rejected(
            CAPABILITY_AGENT_INPUT,
            rejection,
            FILE_CAPABILITY_REGISTRY_DIGEST,
        )
    assert binding is not None
    return _check_file_capabilities(
        identities,
        capability=CAPABILITY_AGENT_INPUT,
        agent_binding=binding,
    )


async def _resolve_authorized_agent_file_binding(
    *,
    agent_id: str,
    expected_revision: int,
    expected_profile_sha256: str,
    authorization_port: AgentFileAuthorizationPort,
) -> tuple[_ServerAuthorizedAgentFileBinding | None, str | None]:
    """Return an opaque binding only after one exact authority-port lookup."""

    try:
        assert_safe_id(agent_id, "agent_id")
    except (TypeError, ValueError):
        return None, FILE_CAPABILITY_AGENT_BINDING_INVALID
    if (
        type(expected_revision) is not int
        or expected_revision < 0
        or not isinstance(expected_profile_sha256, str)
        or not _HASH.fullmatch(expected_profile_sha256)
    ):
        return None, FILE_CAPABILITY_AGENT_BINDING_INVALID
    try:
        resolution = await authorization_port.resolve_authorized_revision(
            agent_id=agent_id,
            expected_revision=expected_revision,
            expected_profile_sha256=expected_profile_sha256,
        )
    except Exception:
        return None, FILE_CAPABILITY_AGENT_BINDING_UNAVAILABLE
    if not isinstance(resolution, AgentFileAuthorizationResolution):
        return None, FILE_CAPABILITY_AGENT_BINDING_INVALID
    if resolution.status != "authorized":
        code = {
            "unavailable": FILE_CAPABILITY_AGENT_BINDING_UNAVAILABLE,
            "unauthorized": FILE_CAPABILITY_AGENT_NOT_AUTHORIZED,
            "stale": FILE_CAPABILITY_AGENT_REVISION_STALE,
        }[resolution.status]
        return None, code
    if (
        resolution.agent_id != agent_id
        or resolution.agent_revision != expected_revision
        or resolution.profile_sha256 != expected_profile_sha256
    ):
        return None, FILE_CAPABILITY_AGENT_REVISION_STALE
    try:
        binding = _binding_from_authorized_resolution(resolution)
    except FileCapabilityContractError as exc:
        return None, exc.code
    return binding, None


def _rejected(
    capability: str,
    code: str,
    digest: str,
) -> FileCapabilityDecision:
    return FileCapabilityDecision(
        CAPABILITY_REJECTED,
        capability,
        True,
        code,
        FILE_CAPABILITY_REGISTRY_VERSION,
        digest,
    )


def _validate_registry(registry: tuple[FileCapabilityProfile, ...]) -> None:
    profile_ids = [profile.profile_id for profile in registry]
    identities = [identity for profile in registry for identity in profile.identities]
    if len(profile_ids) != len(set(profile_ids)) or len(identities) != len(
        set(identities)
    ):
        raise RuntimeError("file capability registry has ambiguous identities")


_validate_registry(FILE_CAPABILITY_REGISTRY)
