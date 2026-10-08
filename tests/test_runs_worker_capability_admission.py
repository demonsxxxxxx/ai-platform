from types import SimpleNamespace

import pytest

from app.runs.api import WorkerCapabilityAdmissionService, WorkerRequiredToolPorts


@pytest.mark.asyncio
async def test_worker_capability_admission_denies_missing_principal_and_revoked_pin_before_mcp():
    observed = []

    class Skills:
        async def candidate(self, conn, **kwargs):
            observed.append("skill_pin")
            return SimpleNamespace(denial_reason="skill_historical_pin_revoked")

    class Identity:
        def denied(self, kind, capability_id, reason):
            return SimpleNamespace(capability_kind=kind, capability_id=capability_id,
                                   decision=SimpleNamespace(decision_reason=reason))

    class MCP:
        async def authorize(self, conn, **kwargs):
            raise AssertionError("Denied Run cannot authorize MCP tools")

    def forbidden(*args, **kwargs):
        raise AssertionError("Denied Run cannot request tools")

    service = WorkerCapabilityAdmissionService(
        skill_authority=Skills(), identity_authority=Identity(), mcp_authority=MCP(),
        required_tools=WorkerRequiredToolPorts(
            boundary_decision=forbidden, harness_subjects=forbidden,
            skill_subjects=forbidden, sandbox_subjects=forbidden,
            authorize_required=forbidden, sandbox_provider=forbidden,
        ),
        extract_mcp_tool_ids=forbidden, skill_mcp_tool_ids=forbidden,
        selector_error=ValueError, principal_type=SimpleNamespace,
        missing_principal_reason="current_principal_authority_denied",
        harness_execution_kind="harness_chat",
    )
    payload = SimpleNamespace(execution_kind="skill")
    identity = {"tenant_id": "tenant", "user_id": "user", "skill_id": "skill"}
    missing = await service.authorize(
        object(), payload=payload, run_identity=identity, current_principal=None,
    )
    assert missing.denial.decision.decision_reason == "current_principal_authority_denied"
    assert observed == []

    revoked = await service.authorize(
        object(), payload=payload, run_identity=identity,
        current_principal=SimpleNamespace(user_id="user"),
    )
    assert revoked.denial.decision.decision_reason == "skill_historical_pin_revoked"
    assert observed == ["skill_pin"]
