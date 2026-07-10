import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Literal


CapabilityAccessIntent = Literal["discover", "use", "manage"]


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


def normalize_capability_roles(roles: Iterable[str]) -> list[str]:
    """Canonicalize role labels for distribution comparisons."""

    normalized: list[str] = []
    for role in roles:
        candidate = re.sub(r"[\s-]+", "_", str(role).strip().lower())
        if candidate and candidate not in normalized:
            normalized.append(candidate)
    return normalized


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
    capability_kind: str,
    capability_id: str,
) -> dict[str, Any]:
    """Return the stable, non-sensitive audit projection of a decision."""

    return {
        "capability_kind": capability_kind,
        "capability_id": capability_id,
        "actor_department_id": actor_department_id,
        "department_scope_ids": list(decision.department_scope_ids),
        "role_scope_ids": list(decision.role_scope_ids),
        "scope_mode": decision.scope_mode,
        "decision_reason": decision.decision_reason,
        "admin_bypass": decision.admin_bypass,
    }
