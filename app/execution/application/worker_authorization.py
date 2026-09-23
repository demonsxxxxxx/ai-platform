from __future__ import annotations

from dataclasses import replace
from typing import Any, Awaitable, Callable

from app import repositories
from app.auth import AuthPrincipal
from app.capability_distribution import (
    CapabilityDistributionSubject,
    resolve_capability_access,
)
from app.capability_distribution import CapabilityAccessContext
from app.control_plane_contracts import (
    LEGACY_SYNTHETIC_CHAT_SKILL_ID,
    RUN_EXECUTION_KIND_HARNESS_CHAT,
)
from app.models import QueueRunPayload
from app.principal_authority import CURRENT_PRINCIPAL_DENIAL_REASON
from app.required_tool_contract import (
    required_tool_authorization_for_run,
    with_boundary_sandbox_local_tool_subjects,
    with_harness_local_tool_subjects,
)
from app.settings import get_settings
from app.skills.catalog import (
    AuthorizedSkillCatalogError,
    AuthorizedSkillCatalogResolution,
    resolve_authorized_skill_catalog,
)
from app.execution.application.worker_capability_projection import (
    WorkerCapabilityAuthorization,
    WorkerCapabilityDecision,
    denied_capability_decision,
    payload_with_authorized_skill_catalog,
    worker_capability_context,
    worker_capability_record,
    reauthorize_mcp_capabilities,
    authorized_skill_catalog_binding,
)


async def reauthorize_worker_capabilities(
    conn,
    *,
    payload: QueueRunPayload,
    run_identity: dict[str, str],
    attempt_id: str = "",
    current_principal: AuthPrincipal | None = None,
    builtin_capability_subjects: Callable[..., list[dict[str, Any]]],
    execution_boundary_decider: Callable[[QueueRunPayload], Any],
    mcp_reauthorizer: Callable[..., Awaitable[WorkerCapabilityAuthorization]] = reauthorize_mcp_capabilities,
    sandbox_provider: str | None = None,
) -> WorkerCapabilityAuthorization:
    decisions: list[WorkerCapabilityDecision] = []
    principal = current_principal
    if principal is None:
        principal = AuthPrincipal(
            user_id=run_identity["user_id"],
            display_name=run_identity["user_id"],
            tenant_id=run_identity["tenant_id"],
            roles=[],
            permissions=[],
            source="company-user-info-current",
        )
        denial = worker_capability_record(
            "principal_authority",
            "current_principal",
            denied_capability_decision(CURRENT_PRINCIPAL_DENIAL_REASON),
        )
        return WorkerCapabilityAuthorization(payload, principal, tuple(decisions), denial)
    context: CapabilityAccessContext = worker_capability_context(principal)
    sandbox_provider = sandbox_provider or get_settings().sandbox_container_provider

    if payload.execution_kind == RUN_EXECUTION_KIND_HARNESS_CHAT:
        try:
            requested_tool_ids = repositories.extract_run_mcp_tool_ids(payload.input)
        except repositories.RepositoryAuthorizationError:
            denial = worker_capability_record(
                "mcp_tool",
                "mcp_tool_ids",
                denied_capability_decision("invalid_capability_selector"),
            )
            return WorkerCapabilityAuthorization(payload, principal, tuple(decisions), denial)
        tool_policy_subjects = with_harness_local_tool_subjects(
            decision=execution_boundary_decider(payload),
            sandbox_provider=sandbox_provider,
        )
        required_tool_decision = required_tool_authorization_for_run(
            payload=payload,
            run_identity=run_identity,
            attempt_id=attempt_id or "missing-attempt",
            subjects=tool_policy_subjects,
            admin_bypass=False,
            admin_non_bypass_authorized=False,
        )
        if not required_tool_decision.allowed:
            denial = worker_capability_record(
                "builtin_tool",
                required_tool_decision.identity or "required_tool",
                denied_capability_decision(required_tool_decision.reason),
            )
            return WorkerCapabilityAuthorization(
                payload,
                principal,
                tuple(decisions),
                denial,
                required_tool_decision=required_tool_decision,
            )
        return await mcp_reauthorizer(
            conn,
            payload=payload,
            run_identity=run_identity,
            principal=principal,
            context=context,
            decisions=decisions,
            requested_tool_ids=requested_tool_ids,
            tool_policy_subjects=tool_policy_subjects,
            required_tool_decision=required_tool_decision,
        )

    try:
        await repositories.validate_run_skill_snapshots_for_dispatch(
            conn,
            tenant_id=run_identity["tenant_id"],
            run_id=run_identity["run_id"],
            skill_manifests=payload.skill_manifests,
            release_decision=payload.release_decision,
        )
    except repositories.RepositoryConflictError:
        denial = worker_capability_record(
            "skill",
            run_identity["skill_id"],
            denied_capability_decision("skill_snapshot_identity_mismatch"),
        )
        return WorkerCapabilityAuthorization(payload, principal, tuple(decisions), denial)

    profile_skill_set = (
        payload.agent_profile.get("skill_set")
        if isinstance(payload.agent_profile, dict)
        else None
    )
    try:
        pinned_mcp_tool_ids = await repositories.validate_replay_skill_manifests(
            conn,
            skill_id=run_identity["skill_id"],
            pinned_version=str(payload.skill_version or ""),
            pinned_executor_type=payload.executor_type,
            skill_manifests=payload.skill_manifests,
            skill_set=profile_skill_set if isinstance(profile_skill_set, list) else None,
        )
    except (repositories.RepositoryAuthorizationError, repositories.RepositoryConflictError):
        denial = worker_capability_record(
            "skill",
            run_identity["skill_id"],
            denied_capability_decision("skill_historical_pin_revoked"),
        )
        return WorkerCapabilityAuthorization(payload, principal, tuple(decisions), denial)

    skill: dict[str, Any] = {}
    skill_lifecycle_status = "disabled"
    try:
        skill = await repositories.resolve_selected_skill(
            conn,
            tenant_id=run_identity["tenant_id"],
            agent_id=run_identity["agent_id"],
            skill_id=run_identity["skill_id"],
        )
        skill_lifecycle_status = str(skill.get("skill_status") or "disabled")
    except (repositories.RepositoryNotFoundError, repositories.RepositoryConflictError):
        pass
    try:
        skill_distribution = await repositories.get_capability_distribution_row(
            conn,
            tenant_id=run_identity["tenant_id"],
            capability_kind="skill",
            capability_id=run_identity["skill_id"],
        )
    except repositories.RepositoryConflictError:
        denial = worker_capability_record(
            "skill",
            run_identity["skill_id"],
            denied_capability_decision("distribution_scope_invalid"),
        )
        return WorkerCapabilityAuthorization(payload, principal, tuple(decisions), denial)

    skill_subject = CapabilityDistributionSubject(
        capability_kind="skill",
        capability_id=run_identity["skill_id"],
        lifecycle_status=skill_lifecycle_status,
        distribution=skill_distribution,
    )
    skill_decision = resolve_capability_access(context, skill_subject, intent="use")
    skill_record = worker_capability_record("skill", run_identity["skill_id"], skill_decision)
    decisions.append(skill_record)
    if not skill_decision.usable:
        return WorkerCapabilityAuthorization(payload, principal, tuple(decisions), skill_record)
    required_builtin_distribution = (
        resolve_capability_access(replace(context, is_admin=False), skill_subject, intent="use")
        if skill_decision.admin_bypass
        else skill_decision
    )

    authorized_skill_catalog: AuthorizedSkillCatalogResolution | None = None
    if payload.executor_type == "claude-agent-worker":
        try:
            authorized_skill_catalog = await resolve_authorized_skill_catalog(
                conn,
                binding=authorized_skill_catalog_binding(run_identity),
                department_id=principal.department_id,
                roles=principal.roles,
                permissions=principal.permissions,
                pinned_manifests=payload.skill_manifests,
                skill_set=profile_skill_set if isinstance(profile_skill_set, list) else None,
            )
        except (AuthorizedSkillCatalogError, repositories.RepositoryConflictError):
            denial = worker_capability_record(
                "skill",
                run_identity["skill_id"],
                denied_capability_decision("authorized_skill_catalog_unavailable"),
            )
            return WorkerCapabilityAuthorization(payload, principal, tuple(decisions), denial)
        selected_catalog_entry = authorized_skill_catalog.snapshot.entry(run_identity["skill_id"])
        if run_identity["skill_id"] != LEGACY_SYNTHETIC_CHAT_SKILL_ID and (
            selected_catalog_entry is None or not selected_catalog_entry.available
        ):
            denial = worker_capability_record(
                "skill",
                run_identity["skill_id"],
                denied_capability_decision("selected_skill_catalog_unavailable"),
            )
            return WorkerCapabilityAuthorization(payload, principal, tuple(decisions), denial)
        payload = payload_with_authorized_skill_catalog(
            payload,
            resolution=authorized_skill_catalog,
        )

    try:
        requested_tool_ids = repositories.run_mcp_tool_ids_for_skill(skill, payload.input)
        for tool_id in pinned_mcp_tool_ids or []:
            if tool_id not in requested_tool_ids:
                requested_tool_ids.append(tool_id)
    except repositories.RepositoryAuthorizationError:
        denial = worker_capability_record(
            "mcp_tool",
            "mcp_tool_ids",
            denied_capability_decision("invalid_capability_selector"),
        )
        return WorkerCapabilityAuthorization(payload, principal, tuple(decisions), denial)

    tool_policy_subjects = builtin_capability_subjects(
        payload=payload,
        run_identity=run_identity,
        skill=skill,
        skill_decision=required_builtin_distribution,
        authorized_skill_manifests=(
            authorized_skill_catalog.manifests if authorized_skill_catalog is not None else []
        ),
        authorized_skill_names=(
            list(authorized_skill_catalog.snapshot.materialized_skill_ids)
            if authorized_skill_catalog is not None
            else [run_identity["skill_id"]]
        ),
    )
    tool_policy_subjects = with_boundary_sandbox_local_tool_subjects(
        tool_policy_subjects,
        decision=execution_boundary_decider(payload),
        sandbox_provider=sandbox_provider,
    )
    required_tool_decision = required_tool_authorization_for_run(
        payload=payload,
        run_identity=run_identity,
        attempt_id=attempt_id or "missing-attempt",
        subjects=tool_policy_subjects,
        admin_bypass=skill_decision.admin_bypass,
        admin_non_bypass_authorized=(
            skill_decision.admin_bypass and required_builtin_distribution.usable
        ),
    )
    if not required_tool_decision.allowed:
        denial = worker_capability_record(
            "builtin_tool",
            required_tool_decision.identity or "required_tool",
            denied_capability_decision(required_tool_decision.reason),
        )
        return WorkerCapabilityAuthorization(
            payload,
            principal,
            tuple(decisions),
            denial,
            required_tool_decision=required_tool_decision,
        )
    return await mcp_reauthorizer(
        conn,
        payload=payload,
        run_identity=run_identity,
        principal=principal,
        context=context,
        decisions=decisions,
        requested_tool_ids=requested_tool_ids,
        tool_policy_subjects=tool_policy_subjects,
        required_tool_decision=required_tool_decision,
    )
