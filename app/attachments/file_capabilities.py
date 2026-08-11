"""XLSX-only file capability policy contract.

This module is deliberately not a production enforcement claim.  No route,
queue, repository, or runtime adapter calls it at this revision.  It records
the one file identity that the attachment byte classifier can actually prove
today (XLSX), the five independently reviewed policy axes for that identity,
and fail-closed seams for a future server-authorized Agent adapter.

Public policy queries never turn caller metadata into authority.  Agent checks
require a canonical ``AuthPrincipal`` plus workspace scope and a server-owned
authorization port.  Empty Agent declarations mean deny all.
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
from app.auth import AuthPrincipal, normalize_roles
from app.file_parser_contracts import (
    MAX_XLSX_FILE_BYTES,
    XLSX_CONTENT_TYPE,
    XLSX_PARSER_ID,
    XLSX_PARSER_VERSION,
)
from app.validation import assert_safe_id, assert_safe_principal_user_id


FILE_CAPABILITY_REGISTRY_VERSION = "ai-platform.file-capability-registry.v4-xlsx-only"
FILE_CAPABILITY_DECISION_SCHEMA_VERSION = "ai-platform.file-capability-decision.v2"
AGENT_FILE_BINDING_SCHEMA_VERSION = "ai-platform.agent-file-binding.v2"
AGENT_FILE_DECLARATION_EMPTY_POLICY = "deny_all"
FILE_CAPABILITY_CONTRACT_SCOPE = "xlsx_only_contract"
FILE_CAPABILITY_ENFORCEMENT_STATE = "not_production_wired"
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
FILE_CAPABILITY_SKILL_SCOPE_MISMATCH = "file_capability_skill_scope_mismatch"
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
FILE_CAPABILITY_AUTHORIZATION_CONTEXT_INVALID = (
    "file_capability_authorization_context_invalid"
)
FILE_CAPABILITY_AGENT_BINDING_REQUIRED = "file_capability_agent_binding_required"
FILE_CAPABILITY_AGENT_BINDING_INVALID = "file_capability_agent_binding_invalid"
FILE_CAPABILITY_AGENT_BINDING_UNAVAILABLE = "file_capability_agent_binding_unavailable"
FILE_CAPABILITY_AGENT_NOT_AUTHORIZED = "file_capability_agent_not_authorized"
FILE_CAPABILITY_AGENT_REVISION_STALE = "file_capability_agent_revision_stale"
FILE_CAPABILITY_AGENT_SCOPE_MISMATCH = "file_capability_agent_scope_mismatch"
FILE_CAPABILITY_AGENT_DECLARATION_INVALID = "file_capability_agent_declaration_invalid"
FILE_CAPABILITY_AGENT_DECLARATION_INCONSISTENT = (
    "file_capability_agent_declaration_inconsistent"
)
FILE_CAPABILITY_AGENT_DECLARATION_EMPTY = "file_capability_agent_declaration_empty"
FILE_CAPABILITY_AGENT_TYPE_NOT_DECLARED = "file_capability_agent_type_not_declared"
FILE_CAPABILITY_RUNTIME_BINDING_REQUIRED = "file_capability_runtime_binding_required"
FILE_CAPABILITY_RUNTIME_BINDING_INVALID = "file_capability_runtime_binding_invalid"
FILE_CAPABILITY_RUNTIME_BINDING_UNAVAILABLE = (
    "file_capability_runtime_binding_unavailable"
)
FILE_CAPABILITY_RUNTIME_NOT_AUTHORIZED = "file_capability_runtime_not_authorized"
FILE_CAPABILITY_RUNTIME_SCOPE_MISMATCH = "file_capability_runtime_scope_mismatch"
FILE_CAPABILITY_RUNTIME_REVISION_STALE = "file_capability_runtime_revision_stale"

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
        FILE_CAPABILITY_SKILL_SCOPE_MISMATCH,
        FILE_CAPABILITY_NOT_AUTHORIZED,
        FILE_CAPABILITY_VERSION_STALE,
        FILE_CAPABILITY_WORKSPACE_SKILL_MISMATCH,
        FILE_CAPABILITY_UPLOAD_UNSUPPORTED,
        FILE_CAPABILITY_TYPED_PARSE_UNSUPPORTED,
        FILE_CAPABILITY_INPUT_PREVIEW_UNSUPPORTED,
        FILE_CAPABILITY_ARTIFACT_PREVIEW_UNSUPPORTED,
        FILE_CAPABILITY_AGENT_INPUT_UNSUPPORTED,
        FILE_CAPABILITY_AUTHORIZATION_CONTEXT_INVALID,
        FILE_CAPABILITY_AGENT_BINDING_REQUIRED,
        FILE_CAPABILITY_AGENT_BINDING_INVALID,
        FILE_CAPABILITY_AGENT_BINDING_UNAVAILABLE,
        FILE_CAPABILITY_AGENT_NOT_AUTHORIZED,
        FILE_CAPABILITY_AGENT_REVISION_STALE,
        FILE_CAPABILITY_AGENT_SCOPE_MISMATCH,
        FILE_CAPABILITY_AGENT_DECLARATION_INVALID,
        FILE_CAPABILITY_AGENT_DECLARATION_INCONSISTENT,
        FILE_CAPABILITY_AGENT_DECLARATION_EMPTY,
        FILE_CAPABILITY_AGENT_TYPE_NOT_DECLARED,
        FILE_CAPABILITY_RUNTIME_BINDING_REQUIRED,
        FILE_CAPABILITY_RUNTIME_BINDING_INVALID,
        FILE_CAPABILITY_RUNTIME_BINDING_UNAVAILABLE,
        FILE_CAPABILITY_RUNTIME_NOT_AUTHORIZED,
        FILE_CAPABILITY_RUNTIME_SCOPE_MISMATCH,
        FILE_CAPABILITY_RUNTIME_REVISION_STALE,
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
    """Stable construction error for facts that cannot become authoritative."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True, order=True)
class VerifiedFileIdentity:
    """Exact normalized identity derived from trusted byte verification."""

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
    """Five independent reviewed policy axes."""

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
            not isinstance(self.parser_id, str)
            or not _TOKEN.fullmatch(self.parser_id)
            or not isinstance(self.parser_version, str)
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
    """A prerequisite that must exist in a server-observed runtime image."""

    kind: RuntimeDependencyKind
    dependency_id: str
    minimum_version: str | None = None
    require_non_root: bool = False

    def __post_init__(self) -> None:
        if (
            not isinstance(self.kind, str)
            or self.kind not in {"python_runtime", "prebuilt_python", "node_npm"}
            or not isinstance(self.dependency_id, str)
            or not _TOKEN.fullmatch(self.dependency_id)
            or (
                self.minimum_version is not None
                and (
                    not isinstance(self.minimum_version, str)
                    or not _TOKEN.fullmatch(self.minimum_version)
                )
            )
            or type(self.require_non_root) is not bool
        ):
            raise ValueError("runtime dependency requirement is invalid")


@dataclass(frozen=True, slots=True)
class FileCapabilityProfile:
    """One reviewed row with the historical pairs-based constructor preserved."""

    profile_id: str
    category_label: str
    pairs: tuple[tuple[str, str], ...]
    enabled: bool = False
    parser: ParserIdentity | None = None
    logical_skill_id: str | None = None
    runtime_requirements: tuple[RuntimeDependencyRequirement, ...] = ()
    operations: tuple[tuple[str, tuple[str, ...]], ...] = ()
    homogeneous: bool = False
    capabilities: FileCapabilitySupport | None = None

    def __post_init__(self) -> None:
        try:
            object.__setattr__(
                self, "pairs", tuple(tuple(pair) for pair in self.pairs)
            )
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
        except (TypeError, ValueError) as exc:
            raise ValueError("file capability profile is invalid") from exc
        capabilities = self.capabilities
        if capabilities is None:
            capabilities = FileCapabilitySupport(
                upload=self.enabled,
                typed_parse=self.parser is not None,
                input_preview=False,
                artifact_preview=False,
                agent_input=False,
            )
            object.__setattr__(self, "capabilities", capabilities)
        if (
            not isinstance(self.profile_id, str)
            or not _TOKEN.fullmatch(self.profile_id)
            or not isinstance(self.category_label, str)
            or not self.category_label.strip()
            or not self.pairs
            or type(self.enabled) is not bool
            or type(self.homogeneous) is not bool
            or (self.parser is not None and not isinstance(self.parser, ParserIdentity))
            or not isinstance(capabilities, FileCapabilitySupport)
            or not all(
                isinstance(item, RuntimeDependencyRequirement)
                for item in self.runtime_requirements
            )
            or not all(
                isinstance(operation, str)
                and _TOKEN.fullmatch(operation)
                and all(
                    isinstance(artifact, str) and _TOKEN.fullmatch(artifact)
                    for artifact in artifacts
                )
                for operation, artifacts in self.operations
            )
            or len({operation for operation, _ in self.operations})
            != len(self.operations)
        ):
            raise ValueError("file capability profile is invalid")
        identities = self.identities
        if len(set(identities)) != len(identities):
            raise ValueError("file capability profile is invalid")
        if capabilities.typed_parse != (self.parser is not None):
            raise ValueError(
                "typed parse capability must have exactly one reviewed parser"
            )
        if self.enabled and (
            self.parser is None or not self.logical_skill_id or not self.operations
        ):
            raise ValueError("enabled execution profile is incomplete")
        if self.logical_skill_id is not None:
            assert_safe_id(self.logical_skill_id, "logical_skill_id")

    @property
    def identities(self) -> tuple[VerifiedFileIdentity, ...]:
        """Return canonical typed identities for the historical pair inputs."""

        try:
            return tuple(VerifiedFileIdentity(*pair) for pair in self.pairs)
        except (TypeError, ValueError) as exc:
            raise ValueError("file capability profile is invalid") from exc


_XLSX_PARSER = ParserIdentity(
    XLSX_PARSER_ID,
    XLSX_PARSER_VERSION,
    MAX_XLSX_FILE_BYTES,
    "Spreadsheet analysis",
)
_XLSX_CAPABILITIES = FileCapabilitySupport(True, True, True, True, True)

FILE_CAPABILITY_REGISTRY = (
    FileCapabilityProfile(
        profile_id="tabular.xlsx",
        category_label="Spreadsheet files",
        pairs=((XLSX_CONTENT_TYPE, ".xlsx"),),
        enabled=True,
        parser=_XLSX_PARSER,
        logical_skill_id="qa-rag-skill",
        runtime_requirements=(
            RuntimeDependencyRequirement("python_runtime", "python", "3.11", True),
            RuntimeDependencyRequirement("prebuilt_python", "openpyxl", "3.1"),
        ),
        operations=(("analyze", ()), ("generate_artifact", ("xlsx",))),
        homogeneous=True,
        capabilities=_XLSX_CAPABILITIES,
    ),
)


def matching_file_capability_profiles(
    identity: VerifiedFileIdentity,
    *,
    registry: tuple[FileCapabilityProfile, ...] = FILE_CAPABILITY_REGISTRY,
) -> tuple[FileCapabilityProfile, ...]:
    """Return exact typed matches; this inspection helper is not authorization."""

    if not isinstance(identity, VerifiedFileIdentity):
        return ()
    return tuple(profile for profile in registry if identity in profile.identities)


def _registry_descriptor(
    registry: tuple[FileCapabilityProfile, ...],
) -> dict[str, object]:
    return {
        "version": FILE_CAPABILITY_REGISTRY_VERSION,
        "scope": FILE_CAPABILITY_CONTRACT_SCOPE,
        "enforcement_state": FILE_CAPABILITY_ENFORCEMENT_STATE,
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
            "agent_authorization": "principal_workspace_acl_port_required",
            "production_wiring": "follow_up_required",
        },
        "profiles": [
            {
                "profile_id": profile.profile_id,
                "category_label": profile.category_label,
                "identities": [asdict(identity) for identity in profile.identities],
                "enabled": profile.enabled,
                "parser": asdict(profile.parser) if profile.parser else None,
                "logical_skill_id": profile.logical_skill_id,
                "runtime_requirements": [
                    asdict(requirement)
                    for requirement in profile.runtime_requirements
                ],
                "operations": profile.operations,
                "homogeneous": profile.homogeneous,
                "capabilities": asdict(profile.capabilities),
            }
            for profile in registry
        ],
    }


def registry_policy_digest(registry: tuple[FileCapabilityProfile, ...]) -> str:
    """Digest only registry policy; never per-file or per-admission integrity."""

    payload = json.dumps(
        _registry_descriptor(tuple(registry)),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def registry_digest(registry: tuple[FileCapabilityProfile, ...]) -> str:
    """Compatibility alias for the explicitly policy-only registry digest."""

    return registry_policy_digest(registry)


FILE_CAPABILITY_REGISTRY_POLICY_DIGEST = registry_policy_digest(
    FILE_CAPABILITY_REGISTRY
)
# Compatibility export. This value is policy-only, not admission integrity.
FILE_CAPABILITY_REGISTRY_DIGEST = FILE_CAPABILITY_REGISTRY_POLICY_DIGEST


def _authorization_scope_descriptor(
    principal: AuthPrincipal,
    workspace_id: str,
) -> dict[str, object]:
    try:
        if not isinstance(principal, AuthPrincipal):
            raise ValueError("principal is invalid")
        assert_safe_principal_user_id(principal.user_id)
        assert_safe_id(principal.tenant_id, "tenant_id")
        if principal.department_id:
            assert_safe_id(principal.department_id, "department_id")
        assert_safe_id(workspace_id, "workspace_id")
        if (
            type(principal.authz_policy_version) is not int
            or principal.authz_policy_version < 1
            or not all(isinstance(item, str) for item in principal.roles)
            or not all(isinstance(item, str) for item in principal.permissions)
        ):
            raise ValueError("principal is invalid")
    except (TypeError, ValueError) as exc:
        raise FileCapabilityContractError(
            FILE_CAPABILITY_AUTHORIZATION_CONTEXT_INVALID
        ) from exc
    permissions = sorted(
        {item.strip().casefold() for item in principal.permissions if item.strip()}
    )
    return {
        "tenant_id": principal.tenant_id,
        "user_id": principal.user_id,
        "department_id": principal.department_id,
        "roles": sorted(normalize_roles(principal.roles)),
        "permissions": permissions,
        "authz_policy_version": principal.authz_policy_version,
        "authority_source": principal.authority_source or principal.source,
        "workspace_id": workspace_id,
    }


def authorization_scope_sha256(
    principal: AuthPrincipal,
    workspace_id: str,
) -> str:
    """Fingerprint lookup scope; only an authority port can authorize it."""

    payload = json.dumps(
        _authorization_scope_descriptor(principal, workspace_id),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True, slots=True)
class AgentFileAuthorizationResolution:
    """Exact ACL-authorized Agent revision returned by the server port."""

    status: AgentFileAuthorizationStatus
    tenant_id: str | None = None
    principal_user_id: str | None = None
    workspace_id: str | None = None
    authorization_scope_sha256: str | None = None
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
        private_scalars = (
            self.tenant_id,
            self.principal_user_id,
            self.workspace_id,
            self.authorization_scope_sha256,
            self.agent_id,
            self.agent_revision,
            self.profile_sha256,
            self.selected_skill_id,
            self.selected_skill_version,
        )
        if self.status == "authorized":
            try:
                assert_safe_id(self.tenant_id or "", "tenant_id")
                assert_safe_principal_user_id(self.principal_user_id or "")
                assert_safe_id(self.workspace_id or "", "workspace_id")
                assert_safe_id(self.agent_id or "", "agent_id")
                assert_safe_id(self.selected_skill_id or "", "selected_skill_id")
                assert_safe_id(
                    self.selected_skill_version or "", "selected_skill_version"
                )
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    "authorized Agent file resolution is incomplete"
                ) from exc
            if (
                not isinstance(self.authorization_scope_sha256, str)
                or not _HASH.fullmatch(self.authorization_scope_sha256)
                or type(self.agent_revision) is not int
                or self.agent_revision < 0
                or not isinstance(self.profile_sha256, str)
                or not _HASH.fullmatch(self.profile_sha256)
                or not all(isinstance(item, str) for item in self.supported_input_types)
                or not all(isinstance(item, str) for item in self.supported_file_types)
            ):
                raise ValueError("authorized Agent file resolution is incomplete")
            return
        if self.status not in {"unavailable", "unauthorized", "stale"}:
            raise ValueError("Agent file authorization status is invalid")
        if (
            any(item is not None for item in private_scalars)
            or self.supported_input_types
            or self.supported_file_types
        ):
            raise ValueError("rejected Agent file resolution contains private facts")


class AgentFileAuthorizationPort(Protocol):
    """ACL-aware exact Agent revision authority seam for a future adapter."""

    async def resolve_authorized_revision(
        self,
        *,
        principal: AuthPrincipal,
        workspace_id: str,
        agent_id: str,
        expected_revision: int,
        expected_profile_sha256: str,
    ) -> AgentFileAuthorizationResolution:
        """Recheck tenant/user/department/role/workspace ACL and revision."""


class ServerAuthorizedAgentFileBinding(Protocol):
    """Opaque result that can only be produced after the ACL-aware port call."""

    tenant_id: str
    principal_user_id: str
    workspace_id: str
    authorization_scope_sha256: str
    agent_id: str
    agent_revision: int
    profile_sha256: str
    file_input_declared: bool
    declared_identities: tuple[VerifiedFileIdentity, ...]
    selected_skill_id: str
    selected_skill_version: str
    declaration_sha256: str
    authority: Literal["server_authorized_agent_revision"]


def _agent_binding_digest(
    *,
    resolution: AgentFileAuthorizationResolution,
    file_input_declared: bool,
    declared_identities: tuple[VerifiedFileIdentity, ...],
) -> str:
    descriptor = {
        "schema": AGENT_FILE_BINDING_SCHEMA_VERSION,
        "authority": SERVER_AUTHORIZED_AGENT_REVISION,
        "tenant_id": resolution.tenant_id,
        "principal_user_id": resolution.principal_user_id,
        "workspace_id": resolution.workspace_id,
        "authorization_scope_sha256": resolution.authorization_scope_sha256,
        "agent_id": resolution.agent_id,
        "agent_revision": resolution.agent_revision,
        "profile_sha256": resolution.profile_sha256,
        "file_input_declared": file_input_declared,
        "declared_identities": [asdict(identity) for identity in declared_identities],
        "selected_skill_id": resolution.selected_skill_id,
        "selected_skill_version": resolution.selected_skill_version,
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
    tenant_id: str
    principal_user_id: str
    workspace_id: str
    authorization_scope_sha256: str
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
        if (
            self.authority != SERVER_AUTHORIZED_AGENT_REVISION
            or type(self.file_input_declared) is not bool
            or not all(
                isinstance(item, VerifiedFileIdentity)
                for item in self.declared_identities
            )
            or not isinstance(self.declaration_sha256, str)
            or not _HASH.fullmatch(self.declaration_sha256)
        ):
            raise ValueError("server-authorized Agent file binding is invalid")
        resolution = AgentFileAuthorizationResolution(
            status="authorized",
            tenant_id=self.tenant_id,
            principal_user_id=self.principal_user_id,
            workspace_id=self.workspace_id,
            authorization_scope_sha256=self.authorization_scope_sha256,
            agent_id=self.agent_id,
            agent_revision=self.agent_revision,
            profile_sha256=self.profile_sha256,
            supported_input_types=("file",) if self.file_input_declared else (),
            selected_skill_id=self.selected_skill_id,
            selected_skill_version=self.selected_skill_version,
        )
        if self.declaration_sha256 != _agent_binding_digest(
            resolution=resolution,
            file_input_declared=self.file_input_declared,
            declared_identities=self.declared_identities,
        ):
            raise ValueError("server-authorized Agent file binding is invalid")


def _binding_from_authorized_resolution(
    resolution: AgentFileAuthorizationResolution,
) -> _ServerAuthorizedAgentFileBinding:
    if resolution.status != "authorized":
        raise FileCapabilityContractError(FILE_CAPABILITY_AGENT_BINDING_INVALID)
    supported_input_types = resolution.supported_input_types
    supported_file_types = resolution.supported_file_types
    if (
        not all(item in {"text", "file"} for item in supported_input_types)
        or len(set(supported_input_types)) != len(supported_input_types)
        or len(set(supported_file_types)) != len(supported_file_types)
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
            raise FileCapabilityContractError(
                FILE_CAPABILITY_AGENT_DECLARATION_INVALID
            )
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
        if not matches or any(identity in identities for identity in matches):
            raise FileCapabilityContractError(
                FILE_CAPABILITY_AGENT_DECLARATION_INVALID
            )
        for identity in matches:
            profiles = matching_file_capability_profiles(identity)
            if (
                len(profiles) != 1
                or not profiles[0].enabled
                or not profiles[0].capabilities.supports(CAPABILITY_AGENT_INPUT)
            ):
                raise FileCapabilityContractError(
                    FILE_CAPABILITY_AGENT_INPUT_UNSUPPORTED
                )
            identities.append(identity)
    declared_identities = tuple(identities)
    declaration_sha256 = _agent_binding_digest(
        resolution=resolution,
        file_input_declared=file_input_declared,
        declared_identities=declared_identities,
    )
    return _ServerAuthorizedAgentFileBinding(
        tenant_id=resolution.tenant_id or "",
        principal_user_id=resolution.principal_user_id or "",
        workspace_id=resolution.workspace_id or "",
        authorization_scope_sha256=resolution.authorization_scope_sha256 or "",
        agent_id=resolution.agent_id or "",
        agent_revision=resolution.agent_revision or 0,
        profile_sha256=resolution.profile_sha256 or "",
        file_input_declared=file_input_declared,
        declared_identities=declared_identities,
        selected_skill_id=resolution.selected_skill_id or "",
        selected_skill_version=resolution.selected_skill_version or "",
        declaration_sha256=declaration_sha256,
    )


@dataclass(frozen=True, slots=True)
class FileCapabilityDecision:
    """Policy decision only; enforcement remains explicitly unwired."""

    state: CapabilityDecisionState
    capability: str
    fallback_prohibited: bool
    rejection_code: str | None
    registry_version: str
    registry_policy_digest: str
    contract_scope: str = FILE_CAPABILITY_CONTRACT_SCOPE
    enforcement_state: str = FILE_CAPABILITY_ENFORCEMENT_STATE

    def __post_init__(self) -> None:
        if (
            self.registry_version != FILE_CAPABILITY_REGISTRY_VERSION
            or self.registry_policy_digest
            != FILE_CAPABILITY_REGISTRY_POLICY_DIGEST
            or self.contract_scope != FILE_CAPABILITY_CONTRACT_SCOPE
            or self.enforcement_state != FILE_CAPABILITY_ENFORCEMENT_STATE
            or not isinstance(self.capability, str)
            or self.fallback_prohibited is not True
        ):
            raise ValueError("file capability decision is invalid")
        invalid_operation = (
            self.capability not in _CAPABILITY_KINDS
            and self.state == CAPABILITY_REJECTED
            and self.rejection_code == FILE_CAPABILITY_OPERATION_INVALID
        )
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

    @property
    def registry_digest(self) -> str:
        """Compatibility projection; explicitly the policy-only digest."""

        return self.registry_policy_digest


def check_file_capabilities(
    identities: tuple[VerifiedFileIdentity, ...],
    *,
    capability: FileCapabilityKind,
) -> FileCapabilityDecision:
    """Query XLSX policy only; this is not production admission authority."""

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
    digest = FILE_CAPABILITY_REGISTRY_POLICY_DIGEST
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
        if not matches[0].enabled:
            return _rejected(capability, FILE_CAPABILITY_TYPE_UNSUPPORTED, digest)
        profiles.append(matches[0])
    if capability == CAPABILITY_AGENT_INPUT:
        if agent_binding is None:
            return _rejected(
                capability, FILE_CAPABILITY_AGENT_BINDING_REQUIRED, digest
            )
        if not isinstance(agent_binding, _ServerAuthorizedAgentFileBinding):
            return _rejected(
                capability, FILE_CAPABILITY_AGENT_BINDING_INVALID, digest
            )
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
    principal: AuthPrincipal,
    workspace_id: str,
    agent_id: str,
    expected_revision: int,
    expected_profile_sha256: str,
    authorization_port: AgentFileAuthorizationPort,
) -> FileCapabilityDecision:
    """Resolve ACL-aware Agent authority, then query the unwired XLSX policy."""

    binding, rejection = await _resolve_authorized_agent_file_binding(
        principal=principal,
        workspace_id=workspace_id,
        agent_id=agent_id,
        expected_revision=expected_revision,
        expected_profile_sha256=expected_profile_sha256,
        authorization_port=authorization_port,
    )
    if rejection:
        return _rejected(
            CAPABILITY_AGENT_INPUT,
            rejection,
            FILE_CAPABILITY_REGISTRY_POLICY_DIGEST,
        )
    assert binding is not None
    return _check_file_capabilities(
        identities,
        capability=CAPABILITY_AGENT_INPUT,
        agent_binding=binding,
    )


async def _resolve_authorized_agent_file_binding(
    *,
    principal: AuthPrincipal,
    workspace_id: str,
    agent_id: str,
    expected_revision: int,
    expected_profile_sha256: str,
    authorization_port: AgentFileAuthorizationPort,
) -> tuple[_ServerAuthorizedAgentFileBinding | None, str | None]:
    try:
        scope_sha256 = authorization_scope_sha256(principal, workspace_id)
        assert_safe_id(agent_id, "agent_id")
    except (TypeError, ValueError, FileCapabilityContractError):
        return None, FILE_CAPABILITY_AUTHORIZATION_CONTEXT_INVALID
    if (
        type(expected_revision) is not int
        or expected_revision < 0
        or not isinstance(expected_profile_sha256, str)
        or not _HASH.fullmatch(expected_profile_sha256)
    ):
        return None, FILE_CAPABILITY_AGENT_BINDING_INVALID
    try:
        resolution = await authorization_port.resolve_authorized_revision(
            principal=principal,
            workspace_id=workspace_id,
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
        resolution.tenant_id != principal.tenant_id
        or resolution.principal_user_id != principal.user_id
        or resolution.workspace_id != workspace_id
        or resolution.authorization_scope_sha256 != scope_sha256
    ):
        return None, FILE_CAPABILITY_AGENT_SCOPE_MISMATCH
    if (
        resolution.agent_id != agent_id
        or resolution.agent_revision != expected_revision
        or resolution.profile_sha256 != expected_profile_sha256
    ):
        return None, FILE_CAPABILITY_AGENT_REVISION_STALE
    try:
        return _binding_from_authorized_resolution(resolution), None
    except FileCapabilityContractError as exc:
        return None, exc.code


def _rejected(capability: str, code: str, digest: str) -> FileCapabilityDecision:
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
