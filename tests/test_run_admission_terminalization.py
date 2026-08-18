import json

import pytest

from app import repositories
from app import run_admission_terminalization as terminalization


class Cursor:
    def __init__(self, *, row=None, rows=None):
        self.row = row
        self.rows = rows or []

    async def fetchone(self):
        return self.row

    async def fetchall(self):
        return self.rows


class DelayedPermissionDrainConnection:
    def __init__(self):
        self.status = "queued"
        self.target = None
        self.reason = ""
        self.result = {}
        self.error_code = None
        self.error_message = None
        self.remaining = [f"tpr-{index}" for index in range(51)]

    async def execute(self, sql, params):
        normalized = " ".join(sql.split())
        lowered = normalized.lower()
        if "set permission_terminalization_target = case" in lowered:
            if self.status in {"succeeded", "failed", "cancelled"}:
                return Cursor()
            self.target = params[1]
            self.reason = params[3]
            self.result = json.loads(params[5])
            self.error_code = params[7]
            self.error_message = params[9]
            return Cursor(
                row={
                    "id": "run-retired",
                    "user_id": "user-a",
                    "trace_id": "trace-retired",
                    "permission_terminalization_target": self.target,
                }
            )
        if lowered.startswith("update runs") and "set latency_ms" in lowered:
            return Cursor()
        if lowered.startswith("select id, trace_id, status, permission_terminalization_target"):
            return Cursor(
                row={
                    "id": "run-retired",
                    "user_id": "user-a",
                    "trace_id": "trace-retired",
                    "status": self.status,
                    "permission_terminalization_target": self.target,
                    "permission_terminalization_reason": self.reason,
                    "permission_terminalization_result_json": self.result,
                    "permission_terminalization_error_code": self.error_code,
                    "permission_terminalization_error_message": self.error_message,
                }
            )
        if lowered.startswith("with locked_run as materialized"):
            batch, self.remaining = self.remaining[:50], self.remaining[50:]
            return Cursor(
                rows=[
                    {
                        "id": request_id,
                        "user_id": "user-a",
                        "trace_id": "trace-retired",
                        "tool_id": "Bash",
                        "tool_call_id": f"call-{request_id}",
                        "action": "execute",
                        "risk_level": "high",
                        "write_capable": True,
                        "decision": "allow_for_run",
                    }
                    for request_id in batch
                ]
            )
        if "has_unterminalized" in lowered:
            return Cursor(row={"has_unterminalized": bool(self.remaining)})
        if lowered.startswith("update runs") and "set status = 'failed'" in lowered:
            self.status = "failed"
            self.target = None
            return Cursor(row={"id": "run-retired", "status": "failed"})
        if lowered.startswith("select count(*) as artifact_count from artifacts"):
            return Cursor(row={"artifact_count": 0})
        if lowered.startswith("update run_steps"):
            return Cursor()
        raise AssertionError(normalized)


@pytest.mark.asyncio
async def test_retired_admission_terminalization_emits_one_hidden_fact_after_delayed_drain(
    monkeypatch,
):
    events: list[dict[str, object]] = []
    audits: list[dict[str, object]] = []

    async def append_event(_conn, **kwargs):
        events.append(kwargs)
        return "evt-terminal"

    async def append_audit_log(_conn, **kwargs):
        audits.append(kwargs)
        return "aud-terminal"

    async def ensure_run_terminal_intent(_conn, **_kwargs):
        return None

    monkeypatch.setattr(repositories, "append_event", append_event)
    monkeypatch.setattr(repositories, "append_audit_log", append_audit_log)
    monkeypatch.setattr(
        "app.streaming.redis.ensure_run_terminal_intent",
        ensure_run_terminal_intent,
    )
    conn = DelayedPermissionDrainConnection()

    first = await terminalization.terminalize_retired_platform_multi_agent_run(
        conn,
        tenant_id="tenant-a",
        run_id="run-retired",
    )
    assert first.completed is False
    assert conn.reason == "retired_platform_multi_agent_control"
    assert len(conn.remaining) == 1
    assert not [event for event in events if event["event_type"] == "run_failed"]

    second = await repositories.progress_run_tool_permission_terminalization(
        conn,
        tenant_id="tenant-a",
        run_id="run-retired",
    )
    assert second.completed is True and second.did_transition is True
    assert len([event for event in events if event["event_type"] == "run_failed"]) == 1
    assert len([audit for audit in audits if audit["target_type"] == "run"]) == 1
    first_terminal_fact_counts = (len(events), len(audits))

    await terminalization.terminalize_retired_platform_multi_agent_run(
        conn,
        tenant_id="tenant-a",
        run_id="run-retired",
    )
    await repositories.progress_run_tool_permission_terminalization(
        conn,
        tenant_id="tenant-a",
        run_id="run-retired",
    )
    assert (len(events), len(audits)) == first_terminal_fact_counts

    run_events = [event for event in events if event["event_type"] == "run_failed"]
    run_audits = [audit for audit in audits if audit["target_type"] == "run"]
    assert len(run_events) == len(run_audits) == 1
    assert run_events[0]["stage"] == "control"
    assert run_events[0]["visible_to_user"] is False
    assert run_events[0]["payload"]["visible_to_user"] is False
    assert run_events[0]["payload"]["error_code"] == "platform_multi_agent_not_supported"
    assert run_events[0]["payload"]["retryable"] is False
    assert run_audits[0]["action"] == "run.admission.rejected"
    assert run_audits[0]["user_id"] == "user-a"
    assert run_audits[0]["payload_json"]["reason"] == "retired_platform_multi_agent_control"
    assert run_audits[0]["payload_json"]["error_code"] == "platform_multi_agent_not_supported"
    assert run_audits[0]["payload_json"]["retryable"] is False
