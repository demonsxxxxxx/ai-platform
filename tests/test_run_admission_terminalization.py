import json
from types import SimpleNamespace

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

    terminal_intents: list[tuple[str, str, str]] = []

    async def ensure_terminal_intent(_conn, *, tenant_id, run_id, status):
        terminal_intents.append((tenant_id, run_id, status))
        return SimpleNamespace(terminal_event_id="evt-terminal")

    monkeypatch.setattr(repositories, "append_event", append_event)
    monkeypatch.setattr(repositories, "append_audit_log", append_audit_log)
    monkeypatch.setattr(
        "app.streaming.redis.ensure_run_terminal_intent",
        ensure_terminal_intent,
    )
    terminal_rows: list[tuple[str, str]] = []

    class EventPersistence:
        async def append_terminal_row(self, _conn, *, tenant_id, run_id):
            terminal_rows.append((tenant_id, run_id))
            return None

    v4_capabilities = SimpleNamespace(event_persistence=EventPersistence())
    conn = DelayedPermissionDrainConnection()

    first = await terminalization.terminalize_retired_platform_multi_agent_run(
        conn,
        tenant_id="tenant-a",
        run_id="run-retired",
        v4_capabilities=v4_capabilities,
    )
    assert first.completed is False
    assert conn.reason == "retired_platform_multi_agent_control"
    assert len(conn.remaining) == 1
    assert not [event for event in events if event["event_type"] == "run_failed"]

    second = await terminalization.terminalize_retired_platform_multi_agent_run(
        conn,
        tenant_id="tenant-a",
        run_id="run-retired",
        v4_capabilities=v4_capabilities,
    )
    assert second.completed is True and second.did_transition is True
    assert len([event for event in events if event["event_type"] == "run_failed"]) == 1
    assert len([audit for audit in audits if audit["target_type"] == "run"]) == 1
    first_terminal_fact_counts = (len(events), len(audits))

    await terminalization.terminalize_retired_platform_multi_agent_run(
        conn,
        tenant_id="tenant-a",
        run_id="run-retired",
        v4_capabilities=v4_capabilities,
    )
    await terminalization.terminalize_retired_platform_multi_agent_run(
        conn,
        tenant_id="tenant-a",
        run_id="run-retired",
        v4_capabilities=v4_capabilities,
    )
    assert (len(events), len(audits)) == first_terminal_fact_counts
    assert terminal_intents == [("tenant-a", "run-retired", "failed")]
    assert terminal_rows == [("tenant-a", "run-retired")]

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


@pytest.mark.asyncio
async def test_enqueue_failure_prepares_authority_then_terminal_row_on_same_connection(monkeypatch):
    calls: list[tuple[str, object]] = []
    conn = object()

    async def mark_enqueue_failed(observed_conn, **kwargs):
        assert observed_conn is conn
        calls.append(("transition", observed_conn))
        assert kwargs == {
            "tenant_id": "tenant-a",
            "user_id": "user-a",
            "run_id": "run-a",
            "trace_id": "trace-run-a",
        }
        return terminalization.RunTerminalizationProgress(
            completed=True,
            status="failed",
            did_transition=True,
        )

    class PendingAdmissions:
        async def prepare_pending_authority_in_transaction(
            self, observed_conn, *, tenant_id, run_id, attempt_id
        ):
            assert observed_conn is conn
            assert (tenant_id, run_id, attempt_id) == (
                "tenant-a",
                "run-a",
                "enqueue_failure_run-a",
            )
            calls.append(("authority", observed_conn))
            return object()

    class EventPersistence:
        async def append_terminal_row(self, observed_conn, *, tenant_id, run_id):
            assert observed_conn is conn
            assert (tenant_id, run_id) == ("tenant-a", "run-a")
            calls.append(("terminal_row", observed_conn))
            return "row-a"

    monkeypatch.setattr(repositories, "mark_run_enqueue_failed", mark_enqueue_failed)

    progress = await terminalization.terminalize_enqueue_failure_with_v4(
        SimpleNamespace(
            pending_admissions=PendingAdmissions(),
            event_persistence=EventPersistence(),
        ),
        conn,
        tenant_id="tenant-a",
        user_id="user-a",
        run_id="run-a",
        trace_id="trace-run-a",
    )

    assert progress.did_transition is True
    assert calls == [
        ("transition", conn),
        ("authority", conn),
        ("terminal_row", conn),
    ]


@pytest.mark.asyncio
async def test_enqueue_failure_rejects_missing_v4_terminal_row(monkeypatch):
    async def mark_enqueue_failed(_conn, **_kwargs):
        return terminalization.RunTerminalizationProgress(
            completed=True,
            status="failed",
            did_transition=True,
        )

    class PendingAdmissions:
        async def prepare_pending_authority_in_transaction(self, *_args, **_kwargs):
            return object()

    class EventPersistence:
        async def append_terminal_row(self, *_args, **_kwargs):
            return None

    monkeypatch.setattr(repositories, "mark_run_enqueue_failed", mark_enqueue_failed)

    with pytest.raises(RuntimeError, match="enqueue_failure_v4_terminal_row_missing"):
        await terminalization.terminalize_enqueue_failure_with_v4(
            SimpleNamespace(
                pending_admissions=PendingAdmissions(),
                event_persistence=EventPersistence(),
            ),
            object(),
            tenant_id="tenant-a",
            user_id="user-a",
            run_id="run-a",
            trace_id="trace-run-a",
        )
