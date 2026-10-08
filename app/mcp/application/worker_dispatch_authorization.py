"""MCP-owned dispatch registration after current inherited distribution checks."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class WorkerToolPolicyAudit:
    tool_id: str
    allowed: bool
    reason: str
    risk_level: str
    write_capable: bool
    decision: str


@dataclass(frozen=True)
class WorkerMcpDispatchAuthorization:
    payload: Any
    decisions: tuple[Any, ...]
    denial: Any | None = None
    tool_policy_audits: tuple[WorkerToolPolicyAudit, ...] = ()


def project_authorized_worker_mcp_payload(
    payload: Any, *, allowed_entries: list[dict[str, Any]],
    tool_policy_subjects: list[dict[str, Any]],
    authorized_registration_input: Callable[..., dict[str, Any]],
) -> Any:
    return payload.model_copy(update={
        "input": authorized_registration_input(
            payload.input, allowed_entries=allowed_entries,
            tool_policy_subjects=tool_policy_subjects,
        )
    })


class WorkerMcpDispatchService:
    def __init__(
        self, *, get_tool: Callable[..., Awaitable[Any]],
        identity_authority: Any,
        lifecycle_status: Callable[[Any], str],
        capability_subject: Callable[..., Any],
        evaluate_tool_policy: Callable[..., Any],
        sanitize_label: Callable[[str], str],
        authorized_registration_input: Callable[..., dict[str, Any]],
    ) -> None:
        self._get_tool = get_tool
        self._identity = identity_authority
        self._lifecycle_status = lifecycle_status
        self._capability_subject = capability_subject
        self._evaluate_tool_policy = evaluate_tool_policy
        self._sanitize_label = sanitize_label
        self._authorized_registration_input = authorized_registration_input

    async def authorize(
        self, conn: Any, *, payload: Any, principal: Any,
        run_identity: dict[str, str], requested_tool_ids: list[str],
        tool_policy_subjects: list[dict[str, Any]],
        prior_decisions: list[Any],
    ) -> WorkerMcpDispatchAuthorization:
        decisions = list(prior_decisions)
        allowed_entries: list[dict[str, Any]] = []
        audits: list[WorkerToolPolicyAudit] = []
        for tool_id in requested_tool_ids:
            tool = await self._get_tool(
                conn, tenant_id=run_identity["tenant_id"], tool_id=tool_id,
            )
            if tool is None or str(tool.get("tool_id") or "").strip() != tool_id:
                decisions.append(self._identity.denied("mcp_tool", tool_id, "distribution_missing"))
                continue
            server_id = str(tool.get("server_id") or "").strip()
            if not server_id:
                decisions.append(self._identity.denied("mcp_tool", tool_id, "distribution_inheritance_missing"))
                continue
            tool_record = await self._identity.mcp_tool_decision(
                conn, principal=principal, tenant_id=run_identity["tenant_id"],
                tool_id=tool_id, server_id=server_id,
                lifecycle_status=self._lifecycle_status(tool),
            )
            if tool_record.distribution_scope_conflict:
                return WorkerMcpDispatchAuthorization(payload, tuple(decisions), tool_record)
            decisions.append(tool_record)
            if not tool_record.decision.usable:
                continue
            subject = self._capability_subject(
                tool, distribution_usable=tool_record.decision.usable,
                sanitize_label=self._sanitize_label,
            )
            if subject is None:
                decisions.append(self._identity.denied(
                    "mcp_tool", tool_id, "mcp_runtime_metadata_invalid",
                    source=tool_record.decision,
                ))
                continue
            gate = self._evaluate_tool_policy(tool={
                "requested_identity": subject["identity"],
                "declared_identities": [subject["identity"]],
                "registered": subject["registered"],
                "declared": subject["declared"],
                "active": subject["active"],
                "distributed": subject["distributed"],
                "identity_authorized": subject["identity_authorized"],
                "object_authorized": subject["object_authorized"],
                "parameters_authorized": subject["parameters_authorized"],
                "risk_level": subject["risk_level"],
                "write_capable": subject["write_capable"],
            })
            audits.append(WorkerToolPolicyAudit(
                tool_id=tool_id, allowed=gate.allowed, reason=gate.reason,
                risk_level=gate.risk_level, write_capable=gate.write_capable,
                decision=gate.outcome,
            ))
            if not gate.allowed:
                decisions.append(self._identity.denied(
                    "mcp_tool", tool_id, gate.reason, source=tool_record.decision,
                ))
                continue
            allowed_entries.append(tool)
            tool_policy_subjects.append(subject)
        if allowed_entries and payload.executor_type != "claude-agent-worker":
            denial = self._identity.denied(
                "mcp_tool", str(allowed_entries[0].get("tool_id") or "mcp_tool"),
                "mcp_sandbox_executor_required",
            )
            return WorkerMcpDispatchAuthorization(
                payload, tuple(decisions), denial, tuple(audits),
            )
        return WorkerMcpDispatchAuthorization(
            project_authorized_worker_mcp_payload(
                payload, allowed_entries=allowed_entries,
                tool_policy_subjects=tool_policy_subjects,
                authorized_registration_input=self._authorized_registration_input,
            ),
            tuple(decisions), tool_policy_audits=tuple(audits),
        )
