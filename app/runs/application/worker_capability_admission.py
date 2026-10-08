"""Run/Attempt-bound Worker capability admission across owning contexts."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class WorkerCapabilityAuthorization:
    payload: Any
    principal: Any
    decisions: tuple[Any, ...]
    denial: Any | None = None
    tool_policy_audits: tuple[Any, ...] = ()
    required_tool_decision: Any | None = None


@dataclass(frozen=True)
class WorkerRequiredToolPorts:
    boundary_decision: Callable[..., Any]
    harness_subjects: Callable[..., list[dict[str, Any]]]
    skill_subjects: Callable[..., list[dict[str, Any]]]
    sandbox_subjects: Callable[..., list[dict[str, Any]]]
    authorize_required: Callable[..., Any]
    sandbox_provider: Callable[[], str]


class WorkerCapabilityAdmissionService:
    def __init__(
        self, *, skill_authority: Any, identity_authority: Any,
        mcp_authority: Any, required_tools: WorkerRequiredToolPorts,
        extract_mcp_tool_ids: Callable[..., list[str]],
        skill_mcp_tool_ids: Callable[..., list[str]],
        selector_error: type[Exception],
        principal_type: Callable[..., Any],
        missing_principal_reason: str, harness_execution_kind: str,
    ) -> None:
        self._skills = skill_authority
        self._identity = identity_authority
        self._mcp = mcp_authority
        self._required_tools = required_tools
        self._extract_mcp_tool_ids = extract_mcp_tool_ids
        self._skill_mcp_tool_ids = skill_mcp_tool_ids
        self._selector_error = selector_error
        self._principal_type = principal_type
        self._missing_principal_reason = missing_principal_reason
        self._harness_execution_kind = harness_execution_kind

    async def _authorize_mcp(
        self, conn: Any, *, payload: Any, principal: Any,
        run_identity: dict[str, str], decisions: list[Any],
        requested_tool_ids: list[str], tool_policy_subjects: list[dict[str, Any]],
        required_tool_decision: Any,
    ) -> WorkerCapabilityAuthorization:
        authorized = await self._mcp.authorize(
            conn, payload=payload, principal=principal, run_identity=run_identity,
            requested_tool_ids=requested_tool_ids,
            tool_policy_subjects=tool_policy_subjects, prior_decisions=decisions,
        )
        return WorkerCapabilityAuthorization(
            authorized.payload, principal, authorized.decisions,
            authorized.denial, authorized.tool_policy_audits,
            required_tool_decision if authorized.denial is None else None,
        )

    async def authorize(
        self, conn: Any, *, payload: Any, run_identity: dict[str, str],
        attempt_id: str = "", current_principal: Any = None,
    ) -> WorkerCapabilityAuthorization:
        decisions: list[Any] = []
        principal = current_principal
        if principal is None:
            principal = self._principal_type(
                user_id=run_identity["user_id"], display_name=run_identity["user_id"],
                tenant_id=run_identity["tenant_id"], roles=[], permissions=[],
                source="company-user-info-current",
            )
            return WorkerCapabilityAuthorization(
                payload, principal, (),
                self._identity.denied(
                    "principal_authority", "current_principal", self._missing_principal_reason,
                ),
            )
        if payload.execution_kind == self._harness_execution_kind:
            try:
                requested_tool_ids = self._extract_mcp_tool_ids(payload.input)
            except self._selector_error:
                return WorkerCapabilityAuthorization(
                    payload, principal, (),
                    self._identity.denied("mcp_tool", "mcp_tool_ids", "invalid_capability_selector"),
                )
            tool_policy_subjects = self._required_tools.harness_subjects(
                decision=self._required_tools.boundary_decision(payload),
                sandbox_provider=self._required_tools.sandbox_provider(),
            )
            required = self._required_tools.authorize_required(
                payload=payload, run_identity=run_identity,
                attempt_id=attempt_id or "missing-attempt", subjects=tool_policy_subjects,
                admin_bypass=False, admin_non_bypass_authorized=False,
            )
            if not required.allowed:
                return WorkerCapabilityAuthorization(
                    payload, principal, (),
                    self._identity.denied(
                        "builtin_tool", required.identity or "required_tool", required.reason,
                    ),
                    required_tool_decision=required,
                )
            return await self._authorize_mcp(
                conn, payload=payload, principal=principal, run_identity=run_identity,
                decisions=decisions, requested_tool_ids=requested_tool_ids,
                tool_policy_subjects=tool_policy_subjects, required_tool_decision=required,
            )

        candidate = await self._skills.candidate(
            conn, payload=payload, run_identity=run_identity,
        )
        if candidate.denial_reason is not None:
            return WorkerCapabilityAuthorization(
                payload, principal, (),
                self._identity.denied("skill", run_identity["skill_id"], candidate.denial_reason),
            )
        skill_record, builtin_distribution = await self._identity.skill_decision(
            conn, principal=principal, tenant_id=run_identity["tenant_id"],
            skill_id=run_identity["skill_id"], lifecycle_status=candidate.lifecycle_status,
        )
        if builtin_distribution is None:
            return WorkerCapabilityAuthorization(payload, principal, (), skill_record)
        decisions.append(skill_record)
        if not skill_record.decision.usable:
            return WorkerCapabilityAuthorization(payload, principal, tuple(decisions), skill_record)
        catalog = await self._skills.catalog(
            conn, payload=payload, run_identity=run_identity, principal=principal,
            profile_skill_set=candidate.profile_skill_set,
        )
        if catalog.denial_reason is not None:
            return WorkerCapabilityAuthorization(
                payload, principal, tuple(decisions),
                self._identity.denied("skill", run_identity["skill_id"], catalog.denial_reason),
            )
        payload = catalog.payload
        try:
            if payload.agent_profile:
                requested_tool_ids = self._extract_mcp_tool_ids(payload.input)
            else:
                requested_tool_ids = self._skill_mcp_tool_ids(candidate.skill, payload.input)
                for tool_id in candidate.pinned_mcp_tool_ids:
                    if tool_id not in requested_tool_ids:
                        requested_tool_ids.append(tool_id)
        except self._selector_error:
            return WorkerCapabilityAuthorization(
                payload, principal, tuple(decisions),
                self._identity.denied("mcp_tool", "mcp_tool_ids", "invalid_capability_selector"),
            )
        tool_policy_subjects = self._required_tools.skill_subjects(
            payload=payload, run_identity=run_identity, skill=candidate.skill,
            skill_decision=builtin_distribution,
            authorized_skill_manifests=catalog.manifests,
            authorized_skill_names=catalog.skill_names,
        )
        tool_policy_subjects = self._required_tools.sandbox_subjects(
            tool_policy_subjects,
            decision=self._required_tools.boundary_decision(payload),
            sandbox_provider=self._required_tools.sandbox_provider(),
        )
        required = self._required_tools.authorize_required(
            payload=payload, run_identity=run_identity,
            attempt_id=attempt_id or "missing-attempt", subjects=tool_policy_subjects,
            admin_bypass=skill_record.decision.admin_bypass,
            admin_non_bypass_authorized=(
                skill_record.decision.admin_bypass and builtin_distribution.usable
            ),
        )
        if not required.allowed:
            return WorkerCapabilityAuthorization(
                payload, principal, tuple(decisions),
                self._identity.denied(
                    "builtin_tool", required.identity or "required_tool", required.reason,
                ),
                required_tool_decision=required,
            )
        return await self._authorize_mcp(
            conn, payload=payload, principal=principal, run_identity=run_identity,
            decisions=decisions, requested_tool_ids=requested_tool_ids,
            tool_policy_subjects=tool_policy_subjects, required_tool_decision=required,
        )
