from types import SimpleNamespace

import pytest

from app import run_admission_terminalization as terminalization
from app.runs.api import RunLifecycleService


def lifecycle_for_enqueue_failure(conn, calls, *, already_terminal=False):
    class Persistence:
        async def stage_run_terminalization(self, observed_conn, **kwargs):
            assert observed_conn is conn
            calls.append(("stage_terminalization", observed_conn))
            assert kwargs == {
                "tenant_id": "tenant-a",
                "run_id": "run-a",
                "target_status": "failed",
                "terminal_reason": "run_failed",
                "result_json": {
                    "message": "Queue admission failed; retry this run.",
                    "retryable": True,
                },
                "error_code": "queue_enqueue_failed",
                "error_message": "Queue admission failed; retry this run.",
            }
            return {"terminalization_target": "failed"}

        async def load_staged_terminalization(self, observed_conn, **kwargs):
            assert observed_conn is conn
            calls.append(("load_terminalization", observed_conn))
            assert kwargs == {"tenant_id": "tenant-a", "run_id": "run-a"}
            return {
                "status": "failed" if already_terminal else "queued",
                "trace_id": "trace-run-a",
                "terminalization_target": "failed",
                "terminalization_reason": "run_failed",
                "terminalization_result_json": {
                    "message": "Queue admission failed; retry this run.",
                    "retryable": True,
                },
                "terminalization_error_code": "queue_enqueue_failed",
                "terminalization_error_message": "Queue admission failed; retry this run.",
            }

        async def finalize_staged_terminalization(self, observed_conn, **kwargs):
            assert observed_conn is conn
            calls.append(("finalize_terminalization", observed_conn))
            assert kwargs["tenant_id"] == "tenant-a"
            assert kwargs["run_id"] == "run-a"
            assert kwargs["target_status"] == "failed"
            return {
                "already_terminal": False,
                "artifact_count": 0,
                "latency_ms": None,
                "input_token_count": 0,
                "output_token_count": 0,
                "total_token_count": 0,
                "estimated_cost_minor": 0,
            }

    async def append_event(observed_conn, **kwargs):
        assert observed_conn is conn
        calls.append((f"event:{kwargs['event_type']}", observed_conn))

    async def append_audit_log(observed_conn, **kwargs):
        assert observed_conn is conn
        calls.append((f"audit:{kwargs['action']}", observed_conn))

    return RunLifecycleService(
        persistence=Persistence(),
        append_event=append_event,
        append_audit_log=append_audit_log,
        validate_result_size=lambda _result: None,
        sanitize_payload=lambda payload: payload,
        sanitize_text=lambda value: str(value or ""),
        make_trace_id=lambda run_id: f"trace-{run_id}",
    )


def v4_capabilities(calls, *, terminal_row="row-a"):
    class PendingAdmissions:
        async def prepare_pending_authority_in_transaction(
            self, conn, *, tenant_id, run_id, attempt_id
        ):
            calls.append(("authority", conn))
            assert (tenant_id, run_id, attempt_id) == (
                "tenant-a",
                "run-a",
                "enqueue_failure_run-a",
            )
            return object()

    class EventPersistence:
        async def append_terminal_row(self, conn, *, tenant_id, run_id):
            calls.append(("terminal_row", conn))
            assert (tenant_id, run_id) == ("tenant-a", "run-a")
            return terminal_row

    return SimpleNamespace(
        pending_admissions=PendingAdmissions(),
        event_persistence=EventPersistence(),
    )


class Diagnostics:
    def __init__(self, calls, conn):
        self.calls = calls
        self.conn = conn

    async def capture_failure_result(self, observed_conn, **kwargs):
        assert observed_conn is self.conn
        assert kwargs["attempt_id"] is None
        assert kwargs["source"] == "run_admission"
        assert kwargs["stage"] == "queue_enqueue"
        assert kwargs["error_code"] == "queue_enqueue_failed"
        assert kwargs["result_json"]["runtime_diagnostics"]["sdk"][
            "exception_type"
        ] == "RuntimeError"
        self.calls.append(("diagnostics", observed_conn))


@pytest.mark.asyncio
async def test_enqueue_failure_transitions_before_persisting_v4_terminal_row():
    calls = []
    conn = object()
    lifecycle = lifecycle_for_enqueue_failure(conn, calls)

    progress = await terminalization.terminalize_enqueue_failure_with_v4(
        v4_capabilities(calls),
        conn,
        lifecycle=lifecycle,
        tenant_id="tenant-a",
        user_id="user-a",
        run_id="run-a",
        trace_id="trace-run-a",
        diagnostic_error=RuntimeError("queue payload invalid"),
        run_diagnostics=Diagnostics(calls, conn),
    )

    assert progress.completed is True
    assert progress.status == "failed"
    assert progress.did_transition is True
    assert calls == [
        ("authority", conn),
        ("stage_terminalization", conn),
        ("load_terminalization", conn),
        ("finalize_terminalization", conn),
        ("event:run_failed", conn),
        ("audit:run.failed", conn),
        ("event:queue_enqueue_failed", conn),
        ("audit:run.queue.enqueue_failed", conn),
        ("diagnostics", conn),
        ("terminal_row", conn),
    ]


@pytest.mark.asyncio
async def test_enqueue_failure_rejects_missing_v4_terminal_row():
    calls = []
    conn = object()

    with pytest.raises(RuntimeError, match="enqueue_failure_v4_terminal_row_missing"):
        await terminalization.terminalize_enqueue_failure_with_v4(
            v4_capabilities(calls, terminal_row=None),
            conn,
            lifecycle=lifecycle_for_enqueue_failure(conn, calls),
            tenant_id="tenant-a",
            user_id="user-a",
            run_id="run-a",
            trace_id="trace-run-a",
        )

    assert ("finalize_terminalization", conn) in calls
    assert calls[-1] == ("terminal_row", conn)


@pytest.mark.asyncio
async def test_enqueue_failure_rejects_a_non_winning_transition():
    calls = []
    conn = object()

    with pytest.raises(RuntimeError, match="enqueue_failure_terminal_transition_missing"):
        await terminalization.terminalize_enqueue_failure_with_v4(
            v4_capabilities(calls),
            conn,
            lifecycle=lifecycle_for_enqueue_failure(
                conn, calls, already_terminal=True
            ),
            tenant_id="tenant-a",
            user_id="user-a",
            run_id="run-a",
            trace_id="trace-run-a",
        )

    assert calls == [
        ("authority", conn),
        ("stage_terminalization", conn),
        ("load_terminalization", conn),
    ]
