from types import SimpleNamespace

import pytest

from app.runs.api import WorkerEarlyFailureService


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["pre_dispatch_error", "invalid_snapshot", "capability_denial"])
async def test_worker_early_failure_uses_attempt_cas_and_does_not_emit_when_stale(route):
    calls = []
    conn = object()

    class Attempt:
        async def fail(self, observed_conn, **kwargs):
            assert observed_conn is conn
            assert kwargs["capabilities"] is capabilities
            calls.append(("attempt_fail", kwargs["error_code"]))
            return False

    async def append_event(*args, **kwargs):
        raise AssertionError("Stale terminal CAS must not append event")

    async def record_denial(*args, **kwargs):
        raise AssertionError("Stale terminal CAS must not append denial audit")

    service = WorkerEarlyFailureService(
        fail_result=lambda *args, **kwargs: {"reason": "safe"},
        append_event=append_event,
        record_denial_audit=record_denial,
        locked_principal=lambda *args: object(),
        denied_capability=lambda *args: SimpleNamespace(
            capability_kind="skill", capability_id="skill",
            decision=SimpleNamespace(decision_reason="locked_snapshot_invalid"),
        ),
    )
    payload = object()
    capabilities = object()
    common = {
        "payload": payload,
        "run_identity": {"tenant_id": "tenant", "run_id": "run", "skill_id": "skill"},
        "v4_capabilities": capabilities,
        "attempt_lifecycle": Attempt(),
    }
    if route == "pre_dispatch_error":
        outcome = await service.pre_dispatch_error(
            conn, **common,
            error_code="early_failure", error_message="safe",
            event_stage="worker", event_payload={"visible_to_user": False},
        )
    elif route == "invalid_snapshot":
        outcome = await service.invalid_snapshot(
            conn, **common, locked_run={}, trace_id="trace",
        )
    else:
        outcome = await service.capability_denial(
            conn, **common, trace_id="trace",
            authorization=SimpleNamespace(
                denial=SimpleNamespace(
                    capability_kind="skill", capability_id="skill",
                    decision=SimpleNamespace(decision_reason="revoked"),
                ),
                principal=object(),
            ),
        )
    assert outcome.outcome.status == "skipped"
    assert outcome.payload is payload
    assert len(calls) == 1
    assert calls[0][0] == "attempt_fail"
