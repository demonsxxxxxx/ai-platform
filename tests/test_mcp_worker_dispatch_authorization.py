from types import SimpleNamespace

import pytest

from app.identity.api import WorkerCapabilityDecision
from app.mcp.api import WorkerMcpDispatchService


@pytest.mark.asyncio
async def test_worker_mcp_scope_conflict_stops_before_tool_policy_or_registration():
    calls = []

    async def get_tool(conn, **kwargs):
        calls.append("tool")
        return {"tool_id": "tool", "server_id": "server"}

    class Identity:
        async def mcp_tool_decision(self, conn, **kwargs):
            calls.append("distribution")
            return WorkerCapabilityDecision(
                "mcp_tool", "tool",
                SimpleNamespace(usable=False, decision_reason="distribution_scope_invalid"),
                distribution_scope_conflict=True,
            )

        def denied(self, *args, **kwargs):
            raise AssertionError("Scope conflict already supplied the denial")

    def forbidden(*args, **kwargs):
        raise AssertionError("Invalid distribution cannot register or evaluate a tool")

    service = WorkerMcpDispatchService(
        get_tool=get_tool, identity_authority=Identity(),
        lifecycle_status=lambda tool: "active", capability_subject=forbidden,
        evaluate_tool_policy=forbidden, sanitize_label=lambda label: label,
        authorized_registration_input=forbidden,
    )
    decision = await service.authorize(
        object(), payload=SimpleNamespace(input={}, executor_type="claude-agent-worker"),
        principal=object(), run_identity={"tenant_id": "tenant"},
        requested_tool_ids=["tool"], tool_policy_subjects=[], prior_decisions=[],
    )
    assert decision.denial.decision.decision_reason == "distribution_scope_invalid"
    assert decision.decisions == ()
    assert calls == ["tool", "distribution"]
