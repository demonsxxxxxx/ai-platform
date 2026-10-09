"""Identity-owned Worker capability distribution and tool-policy audit writes."""

from collections.abc import Awaitable, Callable
from typing import Any


class WorkerCapabilityAuditService:
    def __init__(
        self, *, append_audit: Callable[..., Awaitable[Any]],
        distribution_audit_payload: Callable[..., dict[str, Any]],
    ) -> None:
        self._append_audit = append_audit
        self._distribution_audit_payload = distribution_audit_payload

    def _audit_payload(
        self, record: Any, *, principal: Any, run_identity: dict[str, str],
    ) -> dict[str, Any]:
        return {
            **self._distribution_audit_payload(
                decision=record.decision,
                actor_department_id=principal.department_id,
                actor_roles=principal.roles,
                capability_kind=record.capability_kind,
                capability_id=record.capability_id,
            ),
            "run_id": run_identity["run_id"],
            "session_id": run_identity["session_id"],
            "agent_id": run_identity["agent_id"],
            "skill_id": run_identity["skill_id"],
        }

    async def record_admission(
        self, conn: Any, *, authorization: Any, run_identity: dict[str, str], trace_id: str,
    ) -> None:
        for record in authorization.decisions:
            if not record.decision.admin_bypass:
                continue
            await self._append_audit(
                conn, tenant_id=run_identity["tenant_id"], user_id=run_identity["user_id"],
                action="capability_distribution.admin_bypass",
                target_type=record.capability_kind, target_id=record.capability_id,
                trace_id=trace_id,
                payload_json=self._audit_payload(
                    record, principal=authorization.principal, run_identity=run_identity,
                ),
            )
        for audit in authorization.tool_policy_audits:
            await self._append_audit(
                conn, tenant_id=run_identity["tenant_id"], user_id=run_identity["user_id"],
                action="mcp_tool_policy_allowed" if audit.allowed else "mcp_tool_policy_denied",
                target_type="mcp_tool", target_id=audit.tool_id, trace_id=trace_id,
                payload_json={
                    "run_id": run_identity["run_id"],
                    "session_id": run_identity["session_id"],
                    "agent_id": run_identity["agent_id"],
                    "skill_id": run_identity["skill_id"],
                    "reason": audit.reason,
                    "risk_level": audit.risk_level,
                    "write_capable": audit.write_capable,
                    "outcome": audit.decision,
                },
            )

    async def record_denial(
        self, conn: Any, *, denial: Any, principal: Any,
        run_identity: dict[str, str], trace_id: str,
    ) -> None:
        await self._append_audit(
            conn, tenant_id=run_identity["tenant_id"], user_id=run_identity["user_id"],
            action="capability_distribution.denied",
            target_type=denial.capability_kind, target_id=denial.capability_id,
            trace_id=trace_id,
            payload_json=self._audit_payload(
                denial, principal=principal, run_identity=run_identity,
            ),
        )
