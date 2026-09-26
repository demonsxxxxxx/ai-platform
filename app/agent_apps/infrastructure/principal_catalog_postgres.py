"""Principal catalog persistence. Callers own the connection and transaction."""

from __future__ import annotations

from app.agent_apps.infrastructure.catalog_postgres import list_lambchat_agents
from app.auth import normalize_roles
from app.capability_distribution import CapabilityAccessContext
from app.capability_distribution import CapabilityDistributionSubject
from app.capability_distribution import capability_distribution_audit_payload
from app.capability_distribution import resolve_capability_access
from app.control_plane_contracts import standard_trace_id
from app.identity.infrastructure.audit_postgres import append_audit_log
from app.identity.infrastructure.capability_distributions_postgres import list_capability_distribution_rows
from app.skills.infrastructure.versions_postgres import _principal_skill_release_decision
from app.skills.lifecycle import is_user_runnable_status
from psycopg import AsyncConnection
from typing import Any


async def list_principal_lambchat_agents(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    actor_user_id: str,
    department_id: str,
    roles: list[str] | None,
    is_admin: bool,
    permissions: list[str] | None,
) -> list[dict[str, Any]]:
    """Return canonical Agent rows discoverable by the principal."""

    rows = await list_lambchat_agents(conn, tenant_id=tenant_id)
    distributions = await list_capability_distribution_rows(
        conn,
        tenant_id=tenant_id,
        capability_kind="skill",
        include_disabled=True,
    )
    distribution_by_skill = {
        str(distribution.get("capability_id") or ""): distribution
        for distribution in distributions
    }
    context = CapabilityAccessContext(
        tenant_id=tenant_id,
        department_id=str(department_id or ""),
        roles=normalize_roles(roles or []),
        is_admin=bool(is_admin),
        permissions=[str(item) for item in permissions or [] if str(item)],
    )
    authorized_rows: list[dict[str, Any]] = []
    for row in rows:
        projected = dict(row)
        skill_id = str(projected.get("default_skill_id") or "")
        if not skill_id:
            if str(projected.get("agent_type") or "") != "chat":
                continue
            projected["skill_version"] = None
            projected["skill_version_status"] = None
            projected["input_modes"] = ["chat"]
            projected["output_modes"] = ["answer"]
            for field in (
                "release_policy_version",
                "release_policy_previous_version",
                "release_policy_rollout_percent",
                "release_policy_previous_version_status",
            ):
                projected.pop(field, None)
            authorized_rows.append(projected)
            continue
        release_decision = _principal_skill_release_decision(
            projected,
            tenant_id=tenant_id,
            skill_id=skill_id,
            rollout_key=actor_user_id,
            fallback_version_field="skill_version",
        )
        selected_version_status = (
            projected.get("release_policy_previous_version_status")
            if release_decision.selected_track == "previous"
            else projected.get("skill_version_status", "active")
        )
        projected["skill_version"] = release_decision.selected_version
        projected["skill_version_status"] = selected_version_status
        for field in (
            "release_policy_version",
            "release_policy_previous_version",
            "release_policy_rollout_percent",
            "release_policy_previous_version_status",
        ):
            projected.pop(field, None)
        lifecycle_status = str(projected.get("status") or "disabled")
        if not is_user_runnable_status(selected_version_status):
            lifecycle_status = "disabled"
        decision = resolve_capability_access(
            context,
            CapabilityDistributionSubject(
                capability_kind="skill",
                capability_id=skill_id,
                lifecycle_status=lifecycle_status,
                distribution=distribution_by_skill.get(skill_id),
            ),
            intent="discover",
        )
        if not decision.visible:
            continue
        if decision.admin_bypass:
            await append_audit_log(
                conn,
                tenant_id=tenant_id,
                user_id=actor_user_id,
                action="capability_distribution.admin_bypass",
                target_type="skill",
                target_id=skill_id,
                trace_id=standard_trace_id(skill_id),
                payload_json=capability_distribution_audit_payload(
                    decision=decision,
                    actor_department_id=context.department_id,
                    actor_roles=context.roles,
                    capability_kind="skill",
                    capability_id=skill_id,
                ),
            )
        authorized_rows.append(projected)
    return authorized_rows
