from dataclasses import dataclass, field
from datetime import datetime
import json
import re
from typing import Any, Iterable, Literal

from app.auth import normalize_roles


CapabilityAccessIntent = Literal["discover", "use", "manage"]
_ARCHIVED_AT_TIMESTAMP_PATTERN = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{3}Z$")
_ARCHIVED_AT_TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"
_ARCHIVED_BY_MAX_LENGTH = 255


@dataclass(slots=True)
class CapabilityAccessContext:
    """Caller attributes used by the pure capability access resolver."""

    tenant_id: str
    department_id: str
    roles: list[str] = field(default_factory=list)
    is_admin: bool = False
    permissions: list[str] = field(default_factory=list)


@dataclass(slots=True)
class CapabilityDistributionSubject:
    """Capability lifecycle state plus its authoritative distribution row."""

    capability_kind: str
    capability_id: str
    lifecycle_status: str = "active"
    distribution: dict[str, Any] | None = None
    inherited_distribution_source: str | None = None

    @property
    def visible_to_user(self) -> bool:
        return bool((self.distribution or {}).get("visible_to_user", True))

    @property
    def status(self) -> str:
        return str((self.distribution or {}).get("status") or "disabled")

    @property
    def scope_mode(self) -> str:
        return str((self.distribution or {}).get("scope_mode") or "allowlist")

    @property
    def department_ids(self) -> list[str]:
        value = (self.distribution or {}).get("department_ids")
        return [str(item) for item in value if str(item)] if isinstance(value, list) else []

    @property
    def allowed_roles(self) -> list[str]:
        value = (self.distribution or {}).get("allowed_roles")
        return [str(item) for item in value if str(item)] if isinstance(value, list) else []

    @property
    def is_archived(self) -> bool:
        """Return whether authoritative distribution metadata carries an archive marker."""

        return is_capability_distribution_archived(self.distribution)


@dataclass(slots=True)
class CapabilityAccessDecision:
    """Stable resolver output for discovery, use, management, and audit."""

    visible: bool
    usable: bool
    manageable: bool
    admin_bypass: bool
    decision_reason: str
    department_scope_ids: list[str] = field(default_factory=list)
    role_scope_ids: list[str] = field(default_factory=list)
    scope_mode: str = "allowlist"


@dataclass(frozen=True, slots=True)
class CapabilityAuthorizationDenial:
    """Immutable, allowlisted authorization denial data suitable for audit."""

    capability_kind: str
    capability_id: str
    actor_department_id: str
    actor_roles: tuple[str, ...]
    department_scope_ids: tuple[str, ...]
    role_scope_ids: tuple[str, ...]
    scope_mode: str
    decision_reason: str
    admin_bypass: bool = False

    @classmethod
    def from_decision(
        cls,
        *,
        decision: CapabilityAccessDecision,
        actor_department_id: str,
        actor_roles: Iterable[str],
        capability_kind: str,
        capability_id: str,
    ) -> "CapabilityAuthorizationDenial":
        """Freeze one resolver denial without retaining source metadata."""

        return cls(
            capability_kind=capability_kind,
            capability_id=capability_id,
            actor_department_id=actor_department_id,
            actor_roles=tuple(normalize_capability_roles(actor_roles)),
            department_scope_ids=tuple(decision.department_scope_ids),
            role_scope_ids=tuple(decision.role_scope_ids),
            scope_mode=decision.scope_mode,
            decision_reason=decision.decision_reason,
            admin_bypass=decision.admin_bypass,
        )

    def audit_payload(self) -> dict[str, Any]:
        """Return a mutable serialization of the frozen audit record."""

        return {
            "capability_kind": self.capability_kind,
            "capability_id": self.capability_id,
            "actor_department_id": self.actor_department_id,
            "actor_roles": list(self.actor_roles),
            "department_scope_ids": list(self.department_scope_ids),
            "role_scope_ids": list(self.role_scope_ids),
            "scope_mode": self.scope_mode,
            "decision_reason": self.decision_reason,
            "admin_bypass": self.admin_bypass,
        }


def normalize_capability_roles(roles: Iterable[str]) -> list[str]:
    """Canonicalize role labels for distribution comparisons."""

    return normalize_roles(roles)


def _distribution_metadata_dict(value: Any) -> dict[str, Any]:
    """Parse distribution metadata defensively without trusting malformed input."""

    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return {}
    return value if isinstance(value, dict) else {}


def _distribution_metadata_values(distribution: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Return parsed metadata candidates without trusting either transport shape."""

    if not isinstance(distribution, dict):
        return []
    return [_distribution_metadata_dict(distribution.get(key)) for key in ("metadata_json", "metadata")]


def is_valid_archive_timestamp(value: Any) -> bool:
    """Accept only the exact UTC millisecond timestamp emitted by the archive writer."""

    if not isinstance(value, str) or _ARCHIVED_AT_TIMESTAMP_PATTERN.fullmatch(value) is None:
        return False
    try:
        datetime.strptime(value, _ARCHIVED_AT_TIMESTAMP_FORMAT)
    except ValueError:
        return False
    return True


def is_capability_distribution_archived(distribution: dict[str, Any] | None) -> bool:
    """Return whether either distribution metadata shape has a valid archive timestamp."""

    return any(is_valid_archive_timestamp(metadata.get("archived_at")) for metadata in _distribution_metadata_values(distribution))


def has_valid_capability_distribution_archive_evidence(distribution: dict[str, Any] | None) -> bool:
    """Return whether archive timestamp and actor match the bounded archive-writer evidence contract."""

    return any(
        is_valid_archive_timestamp(metadata.get("archived_at"))
        and isinstance(metadata.get("archived_by"), str)
        and len(metadata["archived_by"]) <= _ARCHIVED_BY_MAX_LENGTH
        for metadata in _distribution_metadata_values(distribution)
    )


def _decision(subject: CapabilityDistributionSubject, *, allowed: bool, reason: str, admin_bypass: bool = False) -> CapabilityAccessDecision:
    return CapabilityAccessDecision(
        visible=allowed,
        usable=allowed,
        manageable=allowed,
        admin_bypass=admin_bypass,
        decision_reason=reason,
        department_scope_ids=subject.department_ids,
        role_scope_ids=subject.allowed_roles,
        scope_mode=subject.scope_mode,
    )


def resolve_capability_access(
    context: CapabilityAccessContext,
    subject: CapabilityDistributionSubject,
    intent: CapabilityAccessIntent,
) -> CapabilityAccessDecision:
    """Resolve access from lifecycle and distribution state without side effects."""

    if subject.distribution is None:
        return _decision(subject, allowed=False, reason="distribution_missing")
    if subject.is_archived:
        return _decision(subject, allowed=False, reason="distribution_archived")
    if subject.capability_kind == "mcp_tool":
        source_kind, separator, parent_id = str(subject.inherited_distribution_source or "").strip().partition(":")
        if source_kind != "mcp_server" or not separator or not parent_id.strip():
            return _decision(subject, allowed=False, reason="distribution_inheritance_missing")
    if subject.lifecycle_status != "active":
        return _decision(subject, allowed=False, reason="lifecycle_denied")
    if context.is_admin:
        return _decision(subject, allowed=True, reason="admin_bypass", admin_bypass=True)
    if intent == "manage":
        return _decision(subject, allowed=False, reason="manage_admin_required")
    if not subject.visible_to_user:
        return _decision(subject, allowed=False, reason="distribution_hidden")
    if subject.status != "active":
        return _decision(subject, allowed=False, reason="distribution_disabled")
    if subject.department_ids and context.department_id not in subject.department_ids:
        return _decision(subject, allowed=False, reason="department_not_allowed")
    if subject.allowed_roles:
        actor_roles = set(normalize_capability_roles(context.roles))
        allowed_roles = set(normalize_capability_roles(subject.allowed_roles))
        if not actor_roles.intersection(allowed_roles):
            return _decision(subject, allowed=False, reason="role_not_allowed")
    return _decision(subject, allowed=True, reason="allowed")


def capability_distribution_audit_payload(
    *,
    decision: CapabilityAccessDecision,
    actor_department_id: str,
    actor_roles: Iterable[str],
    capability_kind: str,
    capability_id: str,
) -> dict[str, Any]:
    """Return the stable, non-sensitive audit projection of a decision."""

    return {
        "capability_kind": capability_kind,
        "capability_id": capability_id,
        "actor_department_id": actor_department_id,
        "actor_roles": normalize_capability_roles(actor_roles),
        "department_scope_ids": list(decision.department_scope_ids),
        "role_scope_ids": list(decision.role_scope_ids),
        "scope_mode": decision.scope_mode,
        "decision_reason": decision.decision_reason,
        "admin_bypass": decision.admin_bypass,
    }
