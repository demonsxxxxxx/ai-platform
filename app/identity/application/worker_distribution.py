"""Identity-owned dispatch-time Skill and inherited MCP distribution decisions."""

from dataclasses import dataclass, replace
from typing import Any, Callable


@dataclass(frozen=True)
class WorkerCapabilityDecision:
    capability_kind: str
    capability_id: str
    decision: Any
    distribution_scope_conflict: bool = False


class WorkerDistributionAuthority:
    def __init__(
        self, *, get_distribution: Callable[..., Any],
        context_type: Callable[..., Any], subject_type: Callable[..., Any],
        decision_type: Callable[..., Any], resolve_access: Callable[..., Any],
        is_admin: Callable[[Any], bool], conflict_error: type[Exception],
    ) -> None:
        self._get_distribution = get_distribution
        self._context_type = context_type
        self._subject_type = subject_type
        self._decision_type = decision_type
        self._resolve_access = resolve_access
        self._is_admin = is_admin
        self._conflict_error = conflict_error

    def record(self, kind: str, capability_id: str, decision: Any) -> WorkerCapabilityDecision:
        return WorkerCapabilityDecision(kind, capability_id, decision)

    def denied(
        self, kind: str, capability_id: str, reason: str, *,
        source: Any = None, scope_conflict: bool = False,
    ) -> WorkerCapabilityDecision:
        decision = self._decision_type(
            visible=False, usable=False, manageable=False, admin_bypass=False,
            decision_reason=reason,
            department_scope_ids=list(source.department_scope_ids) if source is not None else [],
            role_scope_ids=list(source.role_scope_ids) if source is not None else [],
            scope_mode=source.scope_mode if source is not None else "allowlist",
        )
        return WorkerCapabilityDecision(kind, capability_id, decision, scope_conflict)

    def context(self, principal: Any) -> Any:
        return self._context_type(
            tenant_id=principal.tenant_id, department_id=principal.department_id,
            roles=principal.roles, is_admin=self._is_admin(principal),
            permissions=principal.permissions,
        )

    async def skill_decision(
        self, conn: Any, *, principal: Any, tenant_id: str,
        skill_id: str, lifecycle_status: str,
    ) -> tuple[WorkerCapabilityDecision, Any | None]:
        try:
            distribution = await self._get_distribution(
                conn, tenant_id=tenant_id, capability_kind="skill", capability_id=skill_id,
            )
        except self._conflict_error:
            return self.denied("skill", skill_id, "distribution_scope_invalid"), None
        context = self.context(principal)
        subject = self._subject_type(
            capability_kind="skill", capability_id=skill_id,
            lifecycle_status=lifecycle_status, distribution=distribution,
        )
        decision = self._resolve_access(context, subject, intent="use")
        required_builtin = (
            self._resolve_access(replace(context, is_admin=False), subject, intent="use")
            if decision.admin_bypass else decision
        )
        return self.record("skill", skill_id, decision), required_builtin

    async def mcp_tool_decision(
        self, conn: Any, *, principal: Any, tenant_id: str,
        tool_id: str, server_id: str, lifecycle_status: str,
    ) -> WorkerCapabilityDecision:
        try:
            distribution = await self._get_distribution(
                conn, tenant_id=tenant_id, capability_kind="mcp_server", capability_id=server_id,
            )
        except self._conflict_error:
            return self.denied("mcp_tool", tool_id, "distribution_scope_invalid", scope_conflict=True)
        decision = self._resolve_access(
            self.context(principal),
            self._subject_type(
                capability_kind="mcp_tool", capability_id=tool_id,
                lifecycle_status=lifecycle_status, distribution=distribution,
                inherited_distribution_source=f"mcp_server:{server_id}",
            ),
            intent="use",
        )
        return self.record("mcp_tool", tool_id, decision)
