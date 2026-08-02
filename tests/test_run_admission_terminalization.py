import pytest

from app import repositories
from app import run_admission_terminalization as terminalization


@pytest.mark.asyncio
async def test_retired_platform_multi_agent_terminalization_is_non_retryable_and_idempotent(monkeypatch):
    calls: list[tuple[str, dict[str, object]]] = []
    transitions = iter((True, False))

    async def fail_run(_conn, **kwargs):
        calls.append(("fail", kwargs))
        return repositories.ToolPermissionTerminalizationProgress(
            completed=True,
            status="failed",
            did_transition=next(transitions),
        )

    async def append_event(_conn, **kwargs):
        calls.append(("event", kwargs))
        return "evt-retired-control"

    async def append_audit_log(_conn, **kwargs):
        calls.append(("audit", kwargs))
        return "aud-retired-control"

    monkeypatch.setattr(terminalization.repositories, "fail_run", fail_run)
    monkeypatch.setattr(terminalization.repositories, "append_event", append_event)
    monkeypatch.setattr(terminalization.repositories, "append_audit_log", append_audit_log)

    for _ in range(2):
        await terminalization.terminalize_retired_platform_multi_agent_run(
            object(),
            tenant_id="tenant-a",
            user_id="user-a",
            run_id="run-retired-control",
            trace_id="trace-retired-control",
        )

    fail_calls = [payload for kind, payload in calls if kind == "fail"]
    assert len(fail_calls) == 2
    assert fail_calls[0]["error_code"] == "platform_multi_agent_not_supported"
    assert fail_calls[0]["result_json"]["retryable"] is False
    event = next(payload for kind, payload in calls if kind == "event")
    assert event["event_type"] == "run_failed"
    assert event["visible_to_user"] is False
    assert event["error_code"] == "platform_multi_agent_not_supported"
    assert event["payload"]["retryable"] is False
    audit = next(payload for kind, payload in calls if kind == "audit")
    assert audit["action"] == "run.admission.rejected"
    assert audit["payload_json"] == {
        "error_code": "platform_multi_agent_not_supported",
        "reason": "retired_platform_multi_agent_control",
        "retryable": False,
    }
    assert [kind for kind, _payload in calls].count("event") == 1
    assert [kind for kind, _payload in calls].count("audit") == 1
