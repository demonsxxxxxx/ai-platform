from types import SimpleNamespace

import pytest

from app.identity.api import WorkerCapabilityAuditService


@pytest.mark.asyncio
async def test_worker_capability_audits_keep_bypass_tool_and_denial_order_on_one_connection():
    writes = []
    conn = object()

    async def append_audit(observed_conn, **kwargs):
        assert observed_conn is conn
        writes.append(kwargs)

    service = WorkerCapabilityAuditService(
        append_audit=append_audit,
        distribution_audit_payload=lambda **kwargs: {"reason": kwargs["decision"].decision_reason},
    )
    run_identity = {
        "tenant_id": "tenant", "user_id": "user", "run_id": "run",
        "session_id": "session", "agent_id": "agent", "skill_id": "skill",
    }
    principal = SimpleNamespace(department_id="dept", roles=["reader"])
    decision = SimpleNamespace(admin_bypass=True, decision_reason="admin_bypass")
    record = SimpleNamespace(capability_kind="skill", capability_id="skill", decision=decision)
    authorization = SimpleNamespace(
        principal=principal, decisions=(record,),
        tool_policy_audits=(SimpleNamespace(
            tool_id="tool", allowed=False, reason="high_risk", risk_level="high",
            write_capable=True, decision="denied",
        ),),
    )

    await service.record_admission(
        conn, authorization=authorization, run_identity=run_identity, trace_id="trace",
    )
    await service.record_denial(
        conn, denial=record, principal=principal, run_identity=run_identity, trace_id="trace",
    )

    assert [write["action"] for write in writes] == [
        "capability_distribution.admin_bypass", "mcp_tool_policy_denied",
        "capability_distribution.denied",
    ]
    assert all(write["tenant_id"] == "tenant" and write["trace_id"] == "trace" for write in writes)
    assert writes[0]["payload_json"]["run_id"] == "run"
    assert writes[1]["payload_json"]["write_capable"] is True
