from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app import repositories
from app.auth import AuthPrincipal, is_ai_admin
from app.capability_distribution import (
    CapabilityAccessContext,
    CapabilityAccessDecision,
    CapabilityDistributionSubject,
    resolve_capability_access,
)
from app.models import QueueRunPayload
from app.mcp import api as mcp_api
from app.required_tool_contract import RequiredCapabilityDecision
from app.skills.catalog import (
    AuthorizedSkillCatalogBinding,
    AuthorizedSkillCatalogResolution,
    RUNTIME_AUTHORIZED_SKILL_CATALOG_KEY,
    RUNTIME_AUTHORIZED_SKILL_MANIFESTS_KEY,
)
from app.control_plane_contracts import sanitize_public_text
from app.tool_policy import evaluate_tool_policy


@dataclass(frozen=True)
class WorkerCapabilityDecision:
    capability_kind: str
    capability_id: str
    decision: CapabilityAccessDecision


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
    payload: QueueRunPayload
    principal: AuthPrincipal
    decisions: tuple[WorkerCapabilityDecision, ...]
    denial: WorkerCapabilityDecision | None = None
    tool_policy_audits: tuple[WorkerToolPolicyAudit, ...] = ()
    required_tool_decision: RequiredCapabilityDecision | None = None


@dataclass(frozen=True)
class WorkerAdminBypassAudit:
    tenant_id: str
    user_id: str
    target_type: str
    target_id: str
    trace_id: str
    payload_json: dict[str, Any]


def authorized_skill_catalog_binding(
    run_identity: dict[str, str],
) -> AuthorizedSkillCatalogBinding:
    return AuthorizedSkillCatalogBinding(
        tenant_id=run_identity["tenant_id"],
        workspace_id=run_identity["workspace_id"],
        user_id=run_identity["user_id"],
        session_id=run_identity["session_id"],
        run_id=run_identity["run_id"],
        agent_id=run_identity["agent_id"],
        selected_skill_id=run_identity["skill_id"],
    )


def payload_with_authorized_skill_catalog(
    payload: QueueRunPayload,
    *,
    resolution: AuthorizedSkillCatalogResolution,
) -> QueueRunPayload:
    rebuilt_input = dict(payload.input)
    rebuilt_input.pop(RUNTIME_AUTHORIZED_SKILL_CATALOG_KEY, None)
    rebuilt_input.pop(RUNTIME_AUTHORIZED_SKILL_MANIFESTS_KEY, None)
    rebuilt_input.update(
        resolution.runtime_input_updates(pinned_manifests=payload.skill_manifests)
    )
    return payload.model_copy(update={"input": rebuilt_input})


def worker_capability_context(principal: AuthPrincipal) -> CapabilityAccessContext:
    return CapabilityAccessContext(
        tenant_id=principal.tenant_id,
        department_id=principal.department_id,
        roles=principal.roles,
        is_admin=is_ai_admin(principal),
        permissions=principal.permissions,
    )


def denied_capability_decision(
    reason: str,
    *,
    source: CapabilityAccessDecision | None = None,
) -> CapabilityAccessDecision:
    return CapabilityAccessDecision(
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
    decision: CapabilityAccessDecision,
) -> WorkerCapabilityDecision:
    return WorkerCapabilityDecision(
        capability_kind=capability_kind,
        capability_id=capability_id,
        decision=decision,
    )


def mcp_tool_lifecycle_status(tool: dict[str, Any]) -> str:
    if (
        str(tool.get("effective_status") or "disabled") == "active"
        and str(tool.get("server_status") or "disabled") == "active"
        and bool(tool.get("visible_to_user", True))
    ):
        return "active"
    return "disabled"


def mcp_capability_subject(
    tool: dict[str, Any],
    distribution: CapabilityAccessDecision,
) -> dict[str, Any] | None:
    server_id = str(tool.get("server_id") or "")
    tool_id = str(tool.get("tool_id") or "")
    allowed_tools = tool.get("allowed_tools")
    if not mcp_api.mcp_runtime_metadata_usable(tool):
        return None
    tool_identifier = allowed_tools[0]
    subject: dict[str, Any] = {
        "identity": f"mcp__{server_id}__{tool_identifier}",
        "mcp_server": server_id,
        "mcp_tool": tool_identifier,
        "public_tool_label": (sanitize_public_text(tool.get("name")) or tool_id)[:120],
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
    payload: QueueRunPayload,
    *,
    allowed_entries: list[dict[str, Any]],
    tool_policy_subjects: list[dict[str, Any]],
) -> QueueRunPayload:
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
            canonical_authorized_mcp_scope(
                step,
                allowed_tool_ids=allowed_tool_ids,
            )
            if isinstance(step, dict)
            else step
            for step in steps
        ]
    rebuilt_input["_runtime_tool_policy_subjects"] = tool_policy_subjects
    return payload.model_copy(update={"input": rebuilt_input})


async def reauthorize_mcp_capabilities(
    conn,
    *,
    payload: QueueRunPayload,
    run_identity: dict[str, str],
    principal: AuthPrincipal,
    context: CapabilityAccessContext,
    decisions: list[WorkerCapabilityDecision],
    requested_tool_ids: list[str],
    tool_policy_subjects: list[dict[str, Any]],
    required_tool_decision: RequiredCapabilityDecision,
    mcp_api_module: Any = mcp_api,
) -> WorkerCapabilityAuthorization:
    allowed_entries: list[dict[str, Any]] = []
    tool_policy_audits: list[WorkerToolPolicyAudit] = []
    for tool_id in requested_tool_ids:
        tool = await mcp_api_module.get_mcp_tool_registry_entry(
            conn,
            tenant_id=run_identity["tenant_id"],
            tool_id=tool_id,
        )
        if tool is None or str(tool.get("tool_id") or "").strip() != tool_id:
            denial = worker_capability_record(
                "mcp_tool", tool_id, denied_capability_decision("distribution_missing")
            )
            return WorkerCapabilityAuthorization(payload, principal, tuple(decisions), denial)
        server_id = str(tool.get("server_id") or "").strip()
        if not server_id:
            denial = worker_capability_record(
                "mcp_tool",
                tool_id,
                denied_capability_decision("distribution_inheritance_missing"),
            )
            return WorkerCapabilityAuthorization(payload, principal, tuple(decisions), denial)
        try:
            server_distribution = await repositories.get_capability_distribution_row(
                conn,
                tenant_id=run_identity["tenant_id"],
                capability_kind="mcp_server",
                capability_id=server_id,
            )
        except repositories.RepositoryConflictError:
            denial = worker_capability_record(
                "mcp_tool",
                tool_id,
                denied_capability_decision("distribution_scope_invalid"),
            )
            return WorkerCapabilityAuthorization(payload, principal, tuple(decisions), denial)
        distribution_decision = resolve_capability_access(
            context,
            CapabilityDistributionSubject(
                capability_kind="mcp_tool",
                capability_id=tool_id,
                lifecycle_status=mcp_tool_lifecycle_status(tool),
                distribution=server_distribution,
                inherited_distribution_source=f"mcp_server:{server_id}",
            ),
            intent="use",
        )
        tool_record = worker_capability_record("mcp_tool", tool_id, distribution_decision)
        decisions.append(tool_record)
        if not distribution_decision.usable:
            return WorkerCapabilityAuthorization(payload, principal, tuple(decisions), tool_record)

        mcp_subject = mcp_capability_subject(tool, distribution_decision)
        if mcp_subject is None:
            denial = worker_capability_record(
                "mcp_tool",
                tool_id,
                denied_capability_decision(
                    "mcp_runtime_metadata_invalid", source=distribution_decision
                ),
            )
            return WorkerCapabilityAuthorization(payload, principal, tuple(decisions), denial)

        tool_gate = evaluate_tool_policy(
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
                    tool_gate.reason, source=distribution_decision
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
            denied_capability_decision("mcp_sandbox_executor_required"),
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
def worker_capability_audit_payload(
    record: WorkerCapabilityDecision,
    *,
    principal: AuthPrincipal,
    run_identity: dict[str, str],
) -> dict[str, Any]:
    from app.capability_distribution import capability_distribution_audit_payload

    return {
        **capability_distribution_audit_payload(
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


def worker_admin_bypass_audits(
    *,
    authorization: WorkerCapabilityAuthorization,
    run_identity: dict[str, str],
    trace_id: str,
) -> tuple[WorkerAdminBypassAudit, ...]:
    audits: list[WorkerAdminBypassAudit] = []
    for record in authorization.decisions:
        if not record.decision.admin_bypass:
            continue
        audits.append(
            WorkerAdminBypassAudit(
                tenant_id=run_identity["tenant_id"],
                user_id=run_identity["user_id"],
                target_type=record.capability_kind,
                target_id=record.capability_id,
                trace_id=trace_id,
                payload_json=worker_capability_audit_payload(
                    record,
                    principal=authorization.principal,
                    run_identity=run_identity,
                ),
            )
        )
    return tuple(audits)


async def append_worker_admin_bypass_audits(
    conn,
    *,
    audits: tuple[WorkerAdminBypassAudit, ...],
) -> None:
    for audit in audits:
        await repositories.append_audit_log(
            conn,
            tenant_id=audit.tenant_id,
            user_id=audit.user_id,
            action="capability_distribution.admin_bypass",
            target_type=audit.target_type,
            target_id=audit.target_id,
            trace_id=audit.trace_id,
            payload_json=audit.payload_json,
        )


async def append_worker_tool_policy_audits(
    conn,
    *,
    authorization: WorkerCapabilityAuthorization,
    run_identity: dict[str, str],
    trace_id: str,
) -> None:
    for audit in authorization.tool_policy_audits:
        await repositories.append_audit_log(
            conn,
            tenant_id=run_identity["tenant_id"],
            user_id=run_identity["user_id"],
            action="mcp_tool_policy_allowed" if audit.allowed else "mcp_tool_policy_denied",
            target_type="mcp_tool",
            target_id=audit.tool_id,
            trace_id=trace_id,
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


async def append_worker_capability_denial_evidence(
    conn,
    *,
    denial: WorkerCapabilityDecision,
    principal: AuthPrincipal,
    run_identity: dict[str, str],
    trace_id: str,
    policy: str,
    error_message: str,
) -> None:
    await repositories.append_event(
        conn,
        tenant_id=run_identity["tenant_id"],
        run_id=run_identity["run_id"],
        event_type="capability_not_authorized",
        stage="authorization",
        message=error_message,
        payload={
            "capability_kind": denial.capability_kind,
            "capability_id": denial.capability_id,
            "policy": policy,
            "reason": denial.decision.decision_reason,
            "visible_to_user": True,
            "severity": "error",
        },
    )
    await repositories.append_audit_log(
        conn,
        tenant_id=run_identity["tenant_id"],
        user_id=run_identity["user_id"],
        action="capability_distribution.denied",
        target_type=denial.capability_kind,
        target_id=denial.capability_id,
        trace_id=trace_id,
        payload_json=worker_capability_audit_payload(
            denial,
            principal=principal,
            run_identity=run_identity,
        ),
    )
