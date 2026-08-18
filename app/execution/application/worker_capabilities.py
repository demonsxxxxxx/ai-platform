"""Worker capability authorization planning outside the legacy worker module."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

@dataclass(frozen=True)
class WorkerCapabilityDecision:
    capability_kind: str
    capability_id: str
    decision: Any


@dataclass(frozen=True)
class WorkerToolPolicyAudit:
    tool_id: str
    allowed: bool
    reason: str
    risk_level: str
    write_capable: bool
    decision: str


@dataclass(frozen=True)
class WorkerCapabilityAuthorization:
    payload: Any
    principal: Any
    decisions: tuple[WorkerCapabilityDecision, ...]
    denial: WorkerCapabilityDecision | None = None
    tool_policy_audits: tuple[WorkerToolPolicyAudit, ...] = ()
    required_tool_decision: Any | None = None


@dataclass(frozen=True)
class WorkerCapabilityPorts:
    capability_access_context: Any
    capability_access_decision: Any
    capability_distribution_subject: Any
    evaluate_tool_policy: Any
    get_capability_distribution_row: Any
    get_mcp_tool_registry_entry: Any
    is_ai_admin: Any
    mcp_runtime_metadata_usable: Any
    repository_conflict_error: type[Exception]
    resolve_capability_access: Any
    sanitize_public_text: Any


def worker_capability_context(principal: Any, *, ports: WorkerCapabilityPorts) -> Any:
    return ports.capability_access_context(
        tenant_id=principal.tenant_id,
        department_id=principal.department_id,
        roles=principal.roles,
        is_admin=ports.is_ai_admin(principal),
        permissions=principal.permissions,
    )


def denied_capability_decision(
    reason: str,
    *,
    source: Any | None = None,
    ports: WorkerCapabilityPorts,
) -> Any:
    return ports.capability_access_decision(
        visible=False,
        usable=False,
        manageable=False,
        admin_bypass=False,
        decision_reason=reason,
        department_scope_ids=list(source.department_scope_ids) if source is not None else [],
        role_scope_ids=list(source.role_scope_ids) if source is not None else [],
        scope_mode=source.scope_mode if source is not None else "allowlist",
    )


def worker_capability_record(
    capability_kind: str,
    capability_id: str,
    decision: Any,
) -> WorkerCapabilityDecision:
    return WorkerCapabilityDecision(
        capability_kind=capability_kind,
        capability_id=capability_id,
        decision=decision,
    )


def _mcp_tool_lifecycle_status(tool: dict[str, Any]) -> str:
    if (
        str(tool.get("effective_status") or "disabled") == "active"
        and str(tool.get("server_status") or "disabled") == "active"
        and bool(tool.get("visible_to_user", True))
    ):
        return "active"
    return "disabled"


def mcp_capability_subject(
    tool: dict[str, Any],
    distribution: Any,
    *,
    ports: WorkerCapabilityPorts,
) -> dict[str, Any] | None:
    server_id = str(tool.get("server_id") or "")
    tool_id = str(tool.get("tool_id") or "")
    allowed_tools = tool.get("allowed_tools")
    if not ports.mcp_runtime_metadata_usable(tool):
        return None
    tool_identifier = allowed_tools[0]
    subject: dict[str, Any] = {
        "identity": f"mcp__{server_id}__{tool_identifier}",
        "mcp_server": server_id,
        "mcp_tool": tool_identifier,
        "public_tool_label": (ports.sanitize_public_text(tool.get("name")) or tool_id)[:120],
        "public_tool_category": "mcp",
        "registered": True,
        "declared": True,
        "active": all(
            str(tool.get(key) or "") == "active"
            for key in ("registry_status", "policy_status", "server_status")
        ),
        "distributed": distribution.usable,
        "identity_authorized": True,
        "object_authorized": True,
        "parameters_authorized": True,
        "risk_level": str(tool.get("risk_level") or "low"),
        "write_capable": bool(tool.get("write_capable")),
        "parameter_delegation": "external_mcp",
    }
    subject.update(capability_id=tool_id)
    return subject


def canonical_authorized_mcp_scope(
    container: dict[str, Any],
    *,
    allowed_tool_ids: set[str],
) -> dict[str, Any]:
    rebuilt = dict(container)
    requested: list[str] = []
    selector_present = False
    for key in ("mcp_tool_ids", "mcpToolIds"):
        if key not in container:
            continue
        selector_present = True
        for value in container[key]:
            tool_id = str(value).strip()
            if tool_id and tool_id in allowed_tool_ids and tool_id not in requested:
                requested.append(tool_id)
        rebuilt.pop(key, None)
    if selector_present:
        rebuilt["mcp_tool_ids"] = requested
    return rebuilt


def payload_with_authorized_mcp_registration(
    payload: Any,
    *,
    allowed_entries: list[dict[str, Any]],
    tool_policy_subjects: list[dict[str, Any]],
) -> Any:
    allowed_tool_ids = {
        str(entry.get("tool_id") or "").strip()
        for entry in allowed_entries
        if str(entry.get("tool_id") or "").strip()
    }
    rebuilt_input = canonical_authorized_mcp_scope(
        payload.input,
        allowed_tool_ids=allowed_tool_ids,
    )
    steps = rebuilt_input.get("multi_agent_steps")
    if isinstance(steps, list):
        rebuilt_input["multi_agent_steps"] = [
            canonical_authorized_mcp_scope(step, allowed_tool_ids=allowed_tool_ids)
            if isinstance(step, dict)
            else step
            for step in steps
        ]
    rebuilt_input["_runtime_tool_policy_subjects"] = tool_policy_subjects
    return payload.model_copy(update={"input": rebuilt_input})


async def reauthorize_mcp_capabilities(
    conn: Any,
    *,
    payload: Any,
    run_identity: dict[str, str],
    principal: Any,
    context: Any,
    decisions: list[WorkerCapabilityDecision],
    requested_tool_ids: list[str],
    tool_policy_subjects: list[dict[str, Any]],
    required_tool_decision: Any,
    ports: WorkerCapabilityPorts,
) -> WorkerCapabilityAuthorization:
    allowed_entries: list[dict[str, Any]] = []
    tool_policy_audits: list[WorkerToolPolicyAudit] = []
    for tool_id in requested_tool_ids:
        tool = await ports.get_mcp_tool_registry_entry(
            conn,
            tenant_id=run_identity["tenant_id"],
            tool_id=tool_id,
        )
        if tool is None or str(tool.get("tool_id") or "").strip() != tool_id:
            denial = worker_capability_record(
                "mcp_tool",
                tool_id,
                denied_capability_decision("distribution_missing", ports=ports),
            )
            return WorkerCapabilityAuthorization(payload, principal, tuple(decisions), denial)
        server_id = str(tool.get("server_id") or "").strip()
        if not server_id:
            denial = worker_capability_record(
                "mcp_tool",
                tool_id,
                denied_capability_decision("distribution_inheritance_missing", ports=ports),
            )
            return WorkerCapabilityAuthorization(payload, principal, tuple(decisions), denial)
        try:
            server_distribution = await ports.get_capability_distribution_row(
                conn,
                tenant_id=run_identity["tenant_id"],
                capability_kind="mcp_server",
                capability_id=server_id,
            )
        except ports.repository_conflict_error:
            denial = worker_capability_record(
                "mcp_tool",
                tool_id,
                denied_capability_decision("distribution_scope_invalid", ports=ports),
            )
            return WorkerCapabilityAuthorization(payload, principal, tuple(decisions), denial)
        distribution_decision = ports.resolve_capability_access(
            context,
            ports.capability_distribution_subject(
                capability_kind="mcp_tool",
                capability_id=tool_id,
                lifecycle_status=_mcp_tool_lifecycle_status(tool),
                distribution=server_distribution,
                inherited_distribution_source=f"mcp_server:{server_id}",
            ),
            intent="use",
        )
        tool_record = worker_capability_record("mcp_tool", tool_id, distribution_decision)
        decisions.append(tool_record)
        if not distribution_decision.usable:
            return WorkerCapabilityAuthorization(
                payload,
                principal,
                tuple(decisions),
                tool_record,
            )

        mcp_subject = mcp_capability_subject(tool, distribution_decision, ports=ports)
        if mcp_subject is None:
            denial = worker_capability_record(
                "mcp_tool",
                tool_id,
                denied_capability_decision(
                    "mcp_runtime_metadata_invalid",
                    source=distribution_decision,
                    ports=ports,
                ),
            )
            return WorkerCapabilityAuthorization(payload, principal, tuple(decisions), denial)

        tool_gate = ports.evaluate_tool_policy(
            tool={
                "requested_identity": mcp_subject["identity"],
                "declared_identities": [mcp_subject["identity"]],
                "registered": mcp_subject["registered"],
                "declared": mcp_subject["declared"],
                "active": mcp_subject["active"],
                "distributed": mcp_subject["distributed"],
                "identity_authorized": mcp_subject["identity_authorized"],
                "object_authorized": mcp_subject["object_authorized"],
                "parameters_authorized": mcp_subject["parameters_authorized"],
                "risk_level": mcp_subject["risk_level"],
                "write_capable": mcp_subject["write_capable"],
            }
        )
        tool_policy_audits.append(
            WorkerToolPolicyAudit(
                tool_id=tool_id,
                allowed=tool_gate.allowed,
                reason=tool_gate.reason,
                risk_level=tool_gate.risk_level,
                write_capable=tool_gate.write_capable,
                decision=tool_gate.outcome,
            )
        )
        if not tool_gate.allowed:
            denial = worker_capability_record(
                "mcp_tool",
                tool_id,
                denied_capability_decision(
                    tool_gate.reason,
                    source=distribution_decision,
                    ports=ports,
                ),
            )
            return WorkerCapabilityAuthorization(
                payload,
                principal,
                tuple(decisions),
                denial,
                tool_policy_audits=tuple(tool_policy_audits),
            )
        allowed_entries.append(tool)
        tool_policy_subjects.append(mcp_subject)

    if allowed_entries and payload.executor_type != "claude-agent-worker":
        denial = worker_capability_record(
            "mcp_tool",
            str(allowed_entries[0].get("tool_id") or "mcp_tool"),
            denied_capability_decision("mcp_sandbox_executor_required", ports=ports),
        )
        return WorkerCapabilityAuthorization(
            payload,
            principal,
            tuple(decisions),
            denial,
            tool_policy_audits=tuple(tool_policy_audits),
        )

    authorized_payload = payload_with_authorized_mcp_registration(
        payload,
        allowed_entries=allowed_entries,
        tool_policy_subjects=tool_policy_subjects,
    )
    return WorkerCapabilityAuthorization(
        authorized_payload,
        principal,
        tuple(decisions),
        tool_policy_audits=tuple(tool_policy_audits),
        required_tool_decision=required_tool_decision,
    )
