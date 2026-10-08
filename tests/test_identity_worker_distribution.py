from types import SimpleNamespace

import pytest

from app.capability_distribution import CapabilityAccessContext, CapabilityAccessDecision
from app.identity.api import WorkerDistributionAuthority


@pytest.mark.asyncio
async def test_worker_distribution_rechecks_inherited_tool_and_non_admin_builtin_scope():
    observed = []

    async def get_distribution(conn, **kwargs):
        observed.append(("distribution", kwargs["capability_kind"], kwargs["capability_id"]))
        return {"status": "active"}

    def resolve_access(context, subject, *, intent):
        observed.append(("resolve", context.is_admin, getattr(subject, "inherited_distribution_source", None)))
        return SimpleNamespace(admin_bypass=context.is_admin, usable=True)

    authority = WorkerDistributionAuthority(
        get_distribution=get_distribution,
        context_type=CapabilityAccessContext,
        subject_type=SimpleNamespace,
        decision_type=CapabilityAccessDecision,
        resolve_access=resolve_access,
        is_admin=lambda principal: True,
        conflict_error=ValueError,
    )
    principal = SimpleNamespace(
        tenant_id="tenant", department_id="dept", roles=["member"], permissions=[],
    )
    skill, builtin_distribution = await authority.skill_decision(
        object(), principal=principal, tenant_id="tenant",
        skill_id="skill", lifecycle_status="active",
    )
    tool = await authority.mcp_tool_decision(
        object(), principal=principal, tenant_id="tenant",
        tool_id="tool", server_id="server", lifecycle_status="active",
    )
    assert skill.decision.admin_bypass is True
    assert builtin_distribution.admin_bypass is False
    assert tool.capability_id == "tool"
    assert observed == [
        ("distribution", "skill", "skill"),
        ("resolve", True, None),
        ("resolve", False, None),
        ("distribution", "mcp_server", "server"),
        ("resolve", True, "mcp_server:server"),
    ]
