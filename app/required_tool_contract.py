"""Authoritative required-capability declaration, replay, and evidence contract.

Callers supply user text, current run authority, and executor completion
evidence.  This module owns exact identity parsing, immutable declarations,
scope replay, builtin subject construction, and fail-closed completion.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Any

from app.skills.execution_profiles import NATIVE_COMMAND_ISOLATION
from app.tool_policy import evaluate_tool_policy

REQUIRED_CAPABILITY_DECLARATION_SCHEMA_VERSION = (
    "ai-platform.required-capability-declaration.v1"
)
REQUIRED_CAPABILITY_EVIDENCE_SCHEMA_VERSION = (
    "ai-platform.required-capability-evidence.v1"
)
REQUIRED_CAPABILITY_DECLARATION_INPUT_KEY = "_required_capability_declaration"
REQUIRED_CAPABILITY_EVIDENCE_KEY = "required_capability_evidence"
CANONICAL_REQUIRED_TOOL_IDENTITY = "Bash"
_BINDING_FIELDS = (
    "tenant_id",
    "workspace_id",
    "user_id",
    "session_id",
    "run_id",
    "attempt_id",
)
_DECLARATION_HASH_FIELDS = (
    "schema_version",
    "capability_kind",
    "canonical_identity",
    "lifecycle_phase",
    "lifecycle_status",
    "evidence_source",
    "trust_basis",
    "public_label",
    "public_status",
)
_CAPABILITY_KINDS = frozenset({"builtin", "skill", "mcp"})
_DECLARATION_LIFECYCLE_PHASES = frozenset({"selected"})
_DECLARATION_LIFECYCLE_STATUSES = frozenset({"required"})
_EVIDENCE_LIFECYCLE_PHASES = frozenset({"invocation_requested", "completed", "failed"})
_EVIDENCE_LIFECYCLE_STATUSES = frozenset({"invoking", "succeeded", "failed"})
_EVIDENCE_LIFECYCLE_PAIRS = frozenset(
    {
        ("invocation_requested", "invoking"),
        ("completed", "succeeded"),
        ("failed", "failed"),
    }
)
_SAFE_PUBLIC_LABEL = "controlled_execution_capability"
_AUTHORIZED_SUBJECT_EVIDENCE_SOURCE = "server_authorized_subject"
_AUTHORIZED_SUBJECT_TRUST_BASIS = "server_derived_authorized_subject"
SDK_HOOK_EVIDENCE_SOURCE = "claude_agent_sdk_hook"
TOOL_CALL_TRUST_BASIS = "tool_call_bound_invocation"
_EVIDENCE_TRUST_MATRIX = {
    "builtin": frozenset({("executor_private_payload", "attempt_bound_tool_invocation")}),
    "mcp": frozenset({(SDK_HOOK_EVIDENCE_SOURCE, TOOL_CALL_TRUST_BASIS)}),
    "skill": frozenset({(SDK_HOOK_EVIDENCE_SOURCE, TOOL_CALL_TRUST_BASIS)}),
}
_AFFIRMATIVE_EXECUTION = re.compile(
    r"(?:请|帮我|麻烦|立即|现在|直接|please\s+)?"
    r"(?:执行|运行|调用|使用|run|execute|invoke|use)"
    r"(?:一下|下|这个|以下|the|tool|工具|命令|command|commands|\s|[：:,])+"
    r"Bash(?![\w.-])",
)
_NEGATIVE_OR_EXPLANATORY = re.compile(
    r"(?:不要|别|禁止|无需|不用|不能|不可以|不可|别再|解释|说明|介绍|什么是|如何|怎么|"
    r"do\s+not|don't|dont|must\s+not|without|explain|describe|what\s+is|how\s+to)",
    re.IGNORECASE,
)
_QUESTION_SUFFIX = re.compile(r"(?:吗|么|呢|可以吗|能否|可否|\?|？)\s*$", re.IGNORECASE)


class RequiredToolContractError(ValueError):
    """Raised when a required-capability carrier or evidence is malformed or forged."""


@dataclass(frozen=True)
class RequiredCapabilityDeclaration:
    """Immutable selected-capability envelope; Stack A populates builtin only."""

    schema_version: str
    capability_kind: str
    canonical_identity: str
    lifecycle_phase: str
    lifecycle_status: str
    evidence_source: str
    trust_basis: str
    public_label: str
    public_status: str
    declaration_sha256: str

    @classmethod
    def from_authorized_subject(
        cls,
        *,
        capability_kind: str,
        canonical_identity: str,
    ) -> RequiredCapabilityDeclaration:
        """Create one immutable Skill or MCP declaration from server authority."""

        if capability_kind not in {"skill", "mcp"} or not canonical_identity:
            raise RequiredToolContractError("required_tool_declaration_mismatch")
        values = {
            "schema_version": REQUIRED_CAPABILITY_DECLARATION_SCHEMA_VERSION,
            "capability_kind": capability_kind,
            "canonical_identity": canonical_identity,
            "lifecycle_phase": "selected",
            "lifecycle_status": "required",
            "evidence_source": _AUTHORIZED_SUBJECT_EVIDENCE_SOURCE,
            "trust_basis": _AUTHORIZED_SUBJECT_TRUST_BASIS,
            "public_label": _SAFE_PUBLIC_LABEL,
            "public_status": "required",
        }
        return cls(
            **values,
            declaration_sha256=_declaration_digest(values),
        )

    def to_payload(self) -> dict[str, str]:
        """Return the validated carrier stored in the run input snapshot."""

        _validate_declaration(self)
        return asdict(self)

    @property
    def identity(self) -> str:
        """Expose the canonical identity to the bounded Stack A adapter."""

        return self.canonical_identity


@dataclass(frozen=True)
class RequiredCapabilityEvidence:
    """Run/attempt-bound invocation evidence, distinct from selection and auth."""

    schema_version: str
    tenant_id: str
    workspace_id: str
    user_id: str
    session_id: str
    run_id: str
    attempt_id: str
    tool_call_id: str | None
    capability_kind: str
    canonical_identity: str
    lifecycle_phase: str
    lifecycle_status: str
    evidence_source: str
    trust_basis: str
    public_label: str
    public_status: str
    declaration_sha256: str

    @classmethod
    def sdk_hook_payload(
        cls,
        *,
        declaration: RequiredCapabilityDeclaration,
        tool_call_id: str,
        lifecycle_phase: str,
    ) -> dict[str, str]:
        """Normalize one unbound private SDK-hook fact for executor binding."""

        _validate_declaration(declaration)
        if declaration.capability_kind not in {"skill", "mcp"}:
            raise RequiredToolContractError("required_tool_completion_evidence_mismatch")
        if not isinstance(tool_call_id, str) or not tool_call_id:
            raise RequiredToolContractError("required_tool_completion_evidence_mismatch")
        if lifecycle_phase not in _EVIDENCE_LIFECYCLE_PHASES:
            raise RequiredToolContractError("required_tool_completion_evidence_mismatch")
        lifecycle_status = dict(_EVIDENCE_LIFECYCLE_PAIRS)[lifecycle_phase]
        return {
            "schema_version": REQUIRED_CAPABILITY_EVIDENCE_SCHEMA_VERSION,
            "capability_kind": declaration.capability_kind,
            "canonical_identity": declaration.canonical_identity,
            "tool_call_id": tool_call_id,
            "lifecycle_phase": lifecycle_phase,
            "lifecycle_status": lifecycle_status,
            "evidence_source": SDK_HOOK_EVIDENCE_SOURCE,
            "trust_basis": TOOL_CALL_TRUST_BASIS,
            "public_label": _SAFE_PUBLIC_LABEL,
            "public_status": lifecycle_status,
            "declaration_sha256": declaration.declaration_sha256,
        }

    @classmethod
    def from_sdk_hook(
        cls,
        *,
        declaration: RequiredCapabilityDeclaration,
        binding: Mapping[str, object],
        tool_call_id: str,
        succeeded: bool | None = None,
        lifecycle_phase: str | None = None,
    ) -> RequiredCapabilityEvidence:
        """Create exact invocation evidence from one trusted SDK hook callback."""

        _validate_declaration(declaration)
        if declaration.capability_kind not in {"skill", "mcp"}:
            raise RequiredToolContractError("required_tool_completion_evidence_mismatch")
        values = {
            field: binding.get(field)
            for field in _BINDING_FIELDS
        }
        if any(not isinstance(value, str) or not value for value in values.values()):
            raise RequiredToolContractError("required_tool_completion_evidence_mismatch")
        if not isinstance(tool_call_id, str) or not tool_call_id:
            raise RequiredToolContractError("required_tool_completion_evidence_mismatch")
        if lifecycle_phase is None:
            if not isinstance(succeeded, bool):
                raise RequiredToolContractError("required_tool_completion_evidence_mismatch")
            lifecycle_phase = "completed" if succeeded else "failed"
        elif succeeded is not None or lifecycle_phase not in _EVIDENCE_LIFECYCLE_PHASES:
            raise RequiredToolContractError("required_tool_completion_evidence_mismatch")
        unbound = cls.sdk_hook_payload(
            declaration=declaration,
            tool_call_id=tool_call_id,
            lifecycle_phase=lifecycle_phase,
        )
        return cls(
            schema_version=unbound["schema_version"],
            **{field: str(values[field]) for field in _BINDING_FIELDS},
            tool_call_id=unbound["tool_call_id"],
            capability_kind=unbound["capability_kind"],
            canonical_identity=unbound["canonical_identity"],
            lifecycle_phase=unbound["lifecycle_phase"],
            lifecycle_status=unbound["lifecycle_status"],
            evidence_source=unbound["evidence_source"],
            trust_basis=unbound["trust_basis"],
            public_label=unbound["public_label"],
            public_status=unbound["public_status"],
            declaration_sha256=unbound["declaration_sha256"],
        )

    @classmethod
    def from_payload(cls, value: object) -> RequiredCapabilityEvidence:
        """Validate the exact private evidence carrier without inferring success."""

        expected_keys = {
            "schema_version",
            *_BINDING_FIELDS,
            "tool_call_id",
            "capability_kind",
            "canonical_identity",
            "lifecycle_phase",
            "lifecycle_status",
            "evidence_source",
            "trust_basis",
            "public_label",
            "public_status",
            "declaration_sha256",
        }
        if not isinstance(value, dict) or set(value) != expected_keys:
            raise RequiredToolContractError("required_tool_completion_evidence_mismatch")
        string_fields = expected_keys - {"tool_call_id"}
        if any(not isinstance(value.get(field), str) or not value.get(field) for field in string_fields):
            raise RequiredToolContractError("required_tool_completion_evidence_mismatch")
        tool_call_id = value.get("tool_call_id")
        if tool_call_id is not None and (not isinstance(tool_call_id, str) or not tool_call_id):
            raise RequiredToolContractError("required_tool_completion_evidence_mismatch")
        evidence = cls(
            schema_version=str(value.get("schema_version") or ""),
            **{field: str(value.get(field) or "") for field in _BINDING_FIELDS},
            tool_call_id=tool_call_id,
            capability_kind=str(value.get("capability_kind") or ""),
            canonical_identity=str(value.get("canonical_identity") or ""),
            lifecycle_phase=str(value.get("lifecycle_phase") or ""),
            lifecycle_status=str(value.get("lifecycle_status") or ""),
            evidence_source=str(value.get("evidence_source") or ""),
            trust_basis=str(value.get("trust_basis") or ""),
            public_label=str(value.get("public_label") or ""),
            public_status=str(value.get("public_status") or ""),
            declaration_sha256=str(value.get("declaration_sha256") or ""),
        )
        if (
            evidence.schema_version != REQUIRED_CAPABILITY_EVIDENCE_SCHEMA_VERSION
            or evidence.capability_kind not in _CAPABILITY_KINDS
            or not evidence.canonical_identity
            or evidence.lifecycle_phase not in _EVIDENCE_LIFECYCLE_PHASES
            or evidence.lifecycle_status not in _EVIDENCE_LIFECYCLE_STATUSES
            or (evidence.lifecycle_phase, evidence.lifecycle_status)
            not in _EVIDENCE_LIFECYCLE_PAIRS
            or not evidence.evidence_source
            or not evidence.trust_basis
            or evidence.public_label != _SAFE_PUBLIC_LABEL
            or evidence.public_status != evidence.lifecycle_status
            or (evidence.evidence_source, evidence.trust_basis)
            not in _EVIDENCE_TRUST_MATRIX[evidence.capability_kind]
            or (
                evidence.evidence_source != "executor_private_payload"
                and evidence.tool_call_id is None
            )
        ):
            raise RequiredToolContractError("required_tool_completion_evidence_mismatch")
        return evidence


@dataclass(frozen=True)
class RequiredCapabilityDecision:
    """One synchronous allow-or-deny decision with no approval state."""

    allowed: bool
    reason: str
    capability_kind: str
    canonical_identity: str
    lifecycle_phase: str = "authorized"
    admin_bypass: bool = False

    @property
    def identity(self) -> str:
        """Expose the canonical identity to existing worker integration code."""

        return self.canonical_identity


def _canonical_json_sha256(value: Mapping[str, object]) -> str:
    serialized = json.dumps(
        dict(value),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _declaration_digest(values: Mapping[str, object]) -> str:
    return _canonical_json_sha256({field: values.get(field) for field in _DECLARATION_HASH_FIELDS})


def _validate_declaration(declaration: RequiredCapabilityDeclaration) -> None:
    values = asdict(declaration)
    if (
        declaration.capability_kind not in _CAPABILITY_KINDS
        or declaration.lifecycle_phase not in _DECLARATION_LIFECYCLE_PHASES
        or declaration.lifecycle_status not in _DECLARATION_LIFECYCLE_STATUSES
    ):
        raise RequiredToolContractError("required_tool_declaration_mismatch")
    expected = {
        "schema_version": REQUIRED_CAPABILITY_DECLARATION_SCHEMA_VERSION,
        "capability_kind": declaration.capability_kind,
        "canonical_identity": declaration.canonical_identity,
        "lifecycle_phase": "selected",
        "lifecycle_status": "required",
        "evidence_source": _AUTHORIZED_SUBJECT_EVIDENCE_SOURCE,
        "trust_basis": _AUTHORIZED_SUBJECT_TRUST_BASIS,
        "public_label": _SAFE_PUBLIC_LABEL,
        "public_status": "required",
    }
    if declaration.capability_kind == "builtin":
        expected.update(
            {
                "canonical_identity": CANONICAL_REQUIRED_TOOL_IDENTITY,
                "evidence_source": "server_intent_parser",
                "trust_basis": "server_derived_locked_input",
            }
        )
    elif declaration.capability_kind not in {"skill", "mcp"}:
        raise RequiredToolContractError("required_tool_declaration_mismatch")
    if any(values.get(field) != value for field, value in expected.items()):
        raise RequiredToolContractError("required_tool_declaration_mismatch")
    if declaration.declaration_sha256 != _declaration_digest(values):
        raise RequiredToolContractError("required_tool_declaration_mismatch")


def parse_required_tool_declaration(message: object) -> RequiredCapabilityDeclaration | None:
    """Create a requirement only for affirmative execution plus exact ``Bash``."""

    if not isinstance(message, str):
        return None
    text = " ".join(message.split()).strip()
    if not text or _NEGATIVE_OR_EXPLANATORY.search(text) or _QUESTION_SUFFIX.search(text):
        return None
    match = _AFFIRMATIVE_EXECUTION.search(text)
    if match is None:
        return None
    values = {
        "schema_version": REQUIRED_CAPABILITY_DECLARATION_SCHEMA_VERSION,
        "capability_kind": "builtin",
        "canonical_identity": CANONICAL_REQUIRED_TOOL_IDENTITY,
        "lifecycle_phase": "selected",
        "lifecycle_status": "required",
        "evidence_source": "server_intent_parser",
        "trust_basis": "server_derived_locked_input",
        "public_label": _SAFE_PUBLIC_LABEL,
        "public_status": "required",
    }
    return RequiredCapabilityDeclaration(
        **values,
        declaration_sha256=_declaration_digest(values),
    )


def declaration_from_payload(value: object) -> RequiredCapabilityDeclaration | None:
    """Validate one stored carrier; absence preserves the normal no-requirement path."""

    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != {
        *_DECLARATION_HASH_FIELDS,
        "declaration_sha256",
    }:
        raise RequiredToolContractError("required_tool_declaration_mismatch")
    if any(not isinstance(item, str) or not item for item in value.values()):
        raise RequiredToolContractError("required_tool_declaration_mismatch")
    declaration = RequiredCapabilityDeclaration(
        schema_version=str(value.get("schema_version") or ""),
        capability_kind=str(value.get("capability_kind") or ""),
        canonical_identity=str(value.get("canonical_identity") or ""),
        lifecycle_phase=str(value.get("lifecycle_phase") or ""),
        lifecycle_status=str(value.get("lifecycle_status") or ""),
        evidence_source=str(value.get("evidence_source") or ""),
        trust_basis=str(value.get("trust_basis") or ""),
        public_label=str(value.get("public_label") or ""),
        public_status=str(value.get("public_status") or ""),
        declaration_sha256=str(value.get("declaration_sha256") or ""),
    )
    _validate_declaration(declaration)
    return declaration


def declaration_from_input(input_payload: object) -> RequiredCapabilityDeclaration | None:
    """Read the authoritative carrier from one locked run input."""

    if not isinstance(input_payload, dict):
        return None
    if REQUIRED_CAPABILITY_DECLARATION_INPUT_KEY in input_payload:
        return declaration_from_payload(input_payload.get(REQUIRED_CAPABILITY_DECLARATION_INPUT_KEY))
    return parse_required_tool_declaration(input_payload.get("message"))


def attach_required_tool_declaration(input_payload: dict[str, Any]) -> dict[str, Any]:
    """Replace caller carriers with one server-derived declaration from its message."""

    rebuilt = dict(input_payload)
    rebuilt.pop(REQUIRED_CAPABILITY_DECLARATION_INPUT_KEY, None)
    declaration = parse_required_tool_declaration(rebuilt.get("message"))
    if declaration is not None:
        rebuilt[REQUIRED_CAPABILITY_DECLARATION_INPUT_KEY] = declaration.to_payload()
    return rebuilt


_BUILTIN_CAPABILITY_PARAMETERS = {
    "Read": (["file_path", "offset", "limit", "pages"], ["file_path"]),
    "Glob": (["pattern", "path"], []),
    "LS": (["path"], []),
    "Bash": (["command", "timeout", "description"], ["command"]),
    "Write": (["file_path", "content"], ["file_path", "content"]),
    "Edit": (
        ["file_path", "old_string", "new_string", "replace_all"],
        ["file_path", "old_string", "new_string"],
    ),
    "NotebookEdit": (
        ["notebook_path", "new_source", "cell_id", "cell_type", "edit_mode"],
        ["notebook_path", "new_source"],
    ),
    "Agent": (["agent", "prompt", "description"], ["agent"]),
    "WebFetch": (["url", "prompt"], ["url"]),
    "WebSearch": (["query"], ["query"]),
    "Skill": (["skill"], ["skill"]),
}


def builtin_capability_subjects(
    *,
    payload: Any,
    run_identity: Mapping[str, str],
    skill: Mapping[str, Any],
    skill_decision: Any,
    canonical_manifest: Any,
    canonical_identities: Any,
    authorized_skill_manifests: list[dict[str, Any]] | None = None,
    authorized_skill_names: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Construct all server-owned builtin subjects for one replayed run."""

    active = str(skill.get("skill_status") or "disabled") == "active"
    distributed = skill_decision.usable
    payload_primary_manifest = next(
        (
            manifest
            for manifest in payload.skill_manifests
            if isinstance(manifest, dict)
            and str(manifest.get("skill_id") or "") == run_identity["skill_id"]
        ),
        None,
    )
    manifests_by_id = {
        str(manifest.get("skill_id") or ""): manifest
        for manifest in [payload_primary_manifest, *(authorized_skill_manifests or [])]
        if isinstance(manifest, dict) and str(manifest.get("skill_id") or "")
    }
    if authorized_skill_names is None:
        primary_manifest = manifests_by_id.get(run_identity["skill_id"])
        authorized_skill_names = (
            [run_identity["skill_id"]]
            if isinstance(primary_manifest, dict)
            and isinstance(primary_manifest.get("source"), dict)
            and primary_manifest["source"].get("kind") in {"builtin", "uploaded"}
            else []
        )
    primary_manifest = manifests_by_id.get(run_identity["skill_id"])
    primary_identities = _declared_builtin_identities(primary_manifest, canonical_identities)
    primary_profile = canonical_manifest(primary_manifest) if isinstance(primary_manifest, dict) else None
    profiles_by_identity: dict[str, list[dict[str, Any]]] = {}
    identities: set[str] = set()
    for manifest in manifests_by_id.values():
        profile = canonical_manifest(manifest)
        for identity in _declared_builtin_identities(manifest, canonical_identities):
            identities.add(identity)
            profiles_by_identity.setdefault(identity, []).append(profile)
    if authorized_skill_names:
        identities.add("Skill")
    subjects: list[dict[str, Any]] = []
    for identity in sorted(identities):
        keys, required_keys = _BUILTIN_CAPABILITY_PARAMETERS[identity]
        profiles = profiles_by_identity.get(identity, [])
        profile = (
            primary_profile
            if identity == "Skill" or identity in primary_identities
            else next(
                (
                    item
                    for item in profiles
                    if str(item.get("command_isolation") or "") == NATIVE_COMMAND_ISOLATION
                ),
                profiles[0] if profiles else None,
            )
        )
        subjects.append(
            _builtin_subject(
                identity=identity,
                active=active,
                distributed=distributed,
                allowed_parameter_keys=keys,
                required_parameter_keys=required_keys,
                allowed_skill_names=list(authorized_skill_names) if identity == "Skill" else [],
                profile=profile,
            )
        )
    try:
        declaration = declaration_from_input(getattr(payload, "input", None))
    except RequiredToolContractError:
        # The authorization replay below owns the terminal fail-closed decision.
        return subjects
    return required_builtin_capability_subjects(
        declaration=declaration,
        existing_subjects=subjects,
        active=active,
        distributed=distributed,
    )


def _declared_builtin_identities(manifest: object, canonical_identities: Any) -> set[str]:
    if not isinstance(manifest, dict):
        return set()
    return set(canonical_identities(manifest))


def _builtin_subject(
    *,
    identity: str,
    active: bool,
    distributed: bool,
    allowed_parameter_keys: list[str],
    required_parameter_keys: list[str],
    allowed_skill_names: list[str],
    profile: Mapping[str, Any] | None,
) -> dict[str, Any]:
    return {
        "identity": identity,
        "declared_identities": [identity],
        "registered": True,
        "declared": True,
        "active": active,
        "distributed": distributed,
        "identity_authorized": True,
        "object_authorized": True,
        "parameters_authorized": True,
        "risk_level": "low" if identity in {"Read", "Glob", "LS", "Skill"} else "high",
        "write_capable": identity not in {"Read", "Glob", "LS", "Skill"},
        "allowed_parameter_keys": list(allowed_parameter_keys),
        "required_parameter_keys": list(required_parameter_keys),
        "allowed_skill_names": list(allowed_skill_names),
        "execution_strategy": str((profile or {}).get("strategy") or "sdk_restricted"),
        "command_isolation": str((profile or {}).get("command_isolation") or "none"),
        "workspace_contract": str((profile or {}).get("workspace_contract") or ""),
    }


def required_builtin_capability_subjects(
    *,
    declaration: RequiredCapabilityDeclaration | None,
    existing_subjects: list[dict[str, Any]],
    active: bool,
    distributed: bool,
) -> list[dict[str, Any]]:
    """Preserve declared subjects without granting authority from a requirement."""

    if declaration is None:
        return existing_subjects
    _validate_declaration(declaration)
    del active, distributed
    return existing_subjects


def _binding_matches(left: Mapping[str, object], right: Mapping[str, object]) -> bool:
    return all(
        isinstance(left.get(field), str)
        and bool(left.get(field))
        and left.get(field) == right.get(field)
        for field in _BINDING_FIELDS
    )


def replay_required_tool_authorization(
    *,
    declaration: RequiredCapabilityDeclaration | None,
    binding: Mapping[str, object],
    current_binding: Mapping[str, object],
    current_subject: Mapping[str, Any] | None,
    is_admin: bool,
) -> RequiredCapabilityDecision:
    """Replay current authority for the exact run and attempt; admin never bypasses."""

    del is_admin
    if declaration is None:
        return RequiredCapabilityDecision(True, "required_tool_not_declared", "", "")
    _validate_declaration(declaration)
    if not _binding_matches(binding, current_binding):
        return RequiredCapabilityDecision(
            False,
            "required_tool_scope_mismatch",
            declaration.capability_kind,
            declaration.identity,
        )
    subject = dict(current_subject or {})
    policy = evaluate_tool_policy(
        tool={
            "requested_identity": declaration.identity,
            "declared_identities": subject.get("declared_identities"),
            "registered": subject.get("registered"),
            "declared": subject.get("declared"),
            "active": subject.get("active"),
            "distributed": subject.get("distributed"),
            "identity_authorized": subject.get("identity_authorized"),
            "object_authorized": subject.get("object_authorized"),
            "parameters_authorized": subject.get("parameters_authorized"),
            "risk_level": subject.get("risk_level"),
            "write_capable": subject.get("write_capable"),
        }
    )
    if not policy.allowed:
        return RequiredCapabilityDecision(
            False,
            "required_tool_not_currently_authorized",
            declaration.capability_kind,
            declaration.identity,
        )
    return RequiredCapabilityDecision(
        True,
        "required_tool_currently_authorized",
        declaration.capability_kind,
        declaration.identity,
    )


def completion_decision(
    *,
    declaration: RequiredCapabilityDeclaration | None,
    authorization: RequiredCapabilityDecision,
    binding: Mapping[str, object],
    evidence: object,
) -> RequiredCapabilityDecision:
    """Permit success only with exact run/attempt-bound completion evidence."""

    if declaration is None:
        return RequiredCapabilityDecision(True, "required_tool_not_declared", "", "")
    _validate_declaration(declaration)
    if not authorization.allowed:
        return authorization
    if evidence is None:
        return RequiredCapabilityDecision(
            False,
            "required_tool_completion_evidence_missing",
            declaration.capability_kind,
            declaration.identity,
        )
    try:
        evidence_record = RequiredCapabilityEvidence.from_payload(evidence)
    except RequiredToolContractError:
        return RequiredCapabilityDecision(
            False,
            "required_tool_completion_evidence_mismatch",
            declaration.capability_kind,
            declaration.identity,
        )
    evidence_values = asdict(evidence_record)
    if (
        evidence_record.capability_kind != declaration.capability_kind
        or evidence_record.canonical_identity != declaration.canonical_identity
        or evidence_record.lifecycle_phase != "completed"
        or evidence_record.lifecycle_status != "succeeded"
        or evidence_record.declaration_sha256 != declaration.declaration_sha256
        or not _binding_matches(binding, evidence_values)
    ):
        return RequiredCapabilityDecision(
            False,
            "required_tool_completion_evidence_mismatch",
            declaration.capability_kind,
            declaration.identity,
        )
    return RequiredCapabilityDecision(
        True,
        "required_tool_completion_evidence_valid",
        declaration.capability_kind,
        declaration.identity,
    )


def selected_capability_completion_decision(
    *,
    declarations: list[RequiredCapabilityDeclaration],
    binding: Mapping[str, object],
    evidence: object,
) -> RequiredCapabilityDecision:
    """Validate one exact invoking-to-completed sequence for every selection."""

    if not declarations:
        return RequiredCapabilityDecision(True, "required_capability_not_selected", "", "")
    try:
        for declaration in declarations:
            _validate_declaration(declaration)
        if not isinstance(evidence, list):
            raise RequiredToolContractError("required_tool_completion_evidence_mismatch")
        records = [RequiredCapabilityEvidence.from_payload(item) for item in evidence]
    except RequiredToolContractError:
        return RequiredCapabilityDecision(
            False,
            "required_tool_completion_evidence_mismatch",
            "",
            "",
        )
    declaration_identities = [
        (declaration.capability_kind, declaration.canonical_identity)
        for declaration in declarations
    ]
    evidence_identities = {
        (record.capability_kind, record.canonical_identity)
        for record in records
    }
    if not records:
        declaration = declarations[0]
        return RequiredCapabilityDecision(
            False,
            "required_tool_completion_evidence_missing",
            declaration.capability_kind,
            declaration.identity,
        )
    if (
        len(set(declaration_identities)) != len(declaration_identities)
        or evidence_identities != set(declaration_identities)
    ):
        return RequiredCapabilityDecision(
            False,
            "required_tool_completion_evidence_mismatch",
            "",
            "",
        )
    for declaration in declarations:
        matching = [
            (index, record)
            for index, record in enumerate(records)
            if record.capability_kind == declaration.capability_kind
            and record.canonical_identity == declaration.canonical_identity
        ]
        if not matching:
            return RequiredCapabilityDecision(
                False,
                "required_tool_completion_evidence_missing",
                declaration.capability_kind,
                declaration.identity,
            )
        invoking = [item for item in matching if item[1].lifecycle_phase == "invocation_requested"]
        completed = [item for item in matching if item[1].lifecycle_phase == "completed"]
        failed = [item for item in matching if item[1].lifecycle_phase == "failed"]
        if (
            len(invoking) != 1
            or len(completed) != 1
            or failed
            or invoking[0][0] >= completed[0][0]
            or invoking[0][1].tool_call_id != completed[0][1].tool_call_id
            or any(
                record.declaration_sha256 != declaration.declaration_sha256
                or not _binding_matches(binding, asdict(record))
                for _, record in matching
            )
        ):
            return RequiredCapabilityDecision(
                False,
                "required_tool_completion_evidence_mismatch",
                declaration.capability_kind,
                declaration.identity,
            )
    return RequiredCapabilityDecision(
        True,
        "required_tool_completion_evidence_valid",
        "",
        "",
    )


def completion_evidence_from_executor_payload(
    executor_payload: object,
) -> dict[str, Any] | None:
    """Return only the exact private executor evidence carrier when present."""

    if not isinstance(executor_payload, dict):
        return None
    evidence = executor_payload.get(REQUIRED_CAPABILITY_EVIDENCE_KEY)
    return dict(evidence) if isinstance(evidence, dict) else None


def required_tool_authorization_for_run(
    *,
    payload: Any,
    run_identity: Mapping[str, object],
    attempt_id: str,
    subjects: list[dict[str, Any]],
    admin_bypass: bool,
    admin_non_bypass_authorized: bool = False,
) -> RequiredCapabilityDecision:
    """Replay one locked payload against the current subject set."""

    try:
        declaration = declaration_from_input(payload.input)
    except RequiredToolContractError:
        return RequiredCapabilityDecision(
            False,
            "required_tool_declaration_mismatch",
            "",
            "",
        )
    if declaration is None:
        return RequiredCapabilityDecision(True, "required_tool_not_declared", "", "")
    binding = {
        field: attempt_id if field == "attempt_id" else getattr(payload, field, "")
        for field in _BINDING_FIELDS
    }
    current_binding = {
        field: attempt_id if field == "attempt_id" else run_identity.get(field)
        for field in _BINDING_FIELDS
    }
    subject = next(
        (item for item in subjects if item.get("identity") == declaration.identity),
        None,
    )
    decision = replay_required_tool_authorization(
        declaration=declaration,
        binding=binding,
        current_binding=current_binding,
        current_subject=subject,
        is_admin=False,
    )
    if (
        admin_bypass
        and decision.allowed
        and decision.identity
        and admin_non_bypass_authorized is not True
    ):
        return RequiredCapabilityDecision(
            False,
            "required_tool_admin_bypass_forbidden",
            declaration.capability_kind,
            declaration.identity,
        )
    return decision


def required_tool_completion_for_run(
    *,
    payload: Any,
    run_identity: Mapping[str, object],
    attempt_id: str,
    authorization: RequiredCapabilityDecision,
    executor_payload: object,
) -> RequiredCapabilityDecision:
    """Validate private completion evidence for the same locked run and attempt."""

    try:
        declaration = declaration_from_input(payload.input)
    except RequiredToolContractError:
        return RequiredCapabilityDecision(
            False,
            "required_tool_declaration_mismatch",
            "",
            "",
        )
    binding = {
        field: attempt_id if field == "attempt_id" else run_identity.get(field)
        for field in _BINDING_FIELDS
    }
    return completion_decision(
        declaration=declaration,
        authorization=authorization,
        binding=binding,
        evidence=completion_evidence_from_executor_payload(executor_payload),
    )


def public_required_tool_detail(status: str) -> dict[str, str]:
    """Return a fixed public detail without tool identity or executor payload."""

    if status == "unavailable":
        return {
            "status": "unavailable",
            "detail_code": "required_capability_unavailable",
            "message": "任务所需执行能力当前不可用。请调整请求或联系管理员。",
        }
    return {
        "status": "required",
        "detail_code": "required_capability_required",
        "message": "此任务需要受控执行能力。",
    }
