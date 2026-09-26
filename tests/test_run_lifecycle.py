from __future__ import annotations

from contextlib import asynccontextmanager
from copy import deepcopy
from typing import Any

import pytest

from app.control_plane_contracts import standard_trace_id
from app.platform.postgres.errors import RepositoryConflictError
from app.platform.public_payload import sanitize_public_payload, sanitize_public_text
from app.runs.application.lifecycle import RunLifecycleService
from app.runs.infrastructure.lifecycle_postgres import require_run_result_size


class MemoryPersistence:
    def __init__(self) -> None:
        self.status = "running"
        self.target: str | None = None
        self.result: dict[str, Any] = {}
        self.reason = ""
        self.error_code: str | None = None
        self.error_message: str | None = None

    async def stage_run_terminalization(self, _conn: object, **kwargs: Any):
        target = kwargs["target_status"]
        if self.status in {"succeeded", "failed", "cancelled"}:
            return None
        if self.target is None or (self.target == "cancel_requested" and target == "cancelled"):
            self.target = target
            self.reason = kwargs["terminal_reason"]
            self.result = kwargs.get("result_json") or {}
            self.error_code = kwargs.get("error_code")
            self.error_message = kwargs.get("error_message")
        return {"terminalization_target": self.target}

    async def load_staged_terminalization(self, _conn: object, **_kwargs: Any):
        if self.target is None and self.status not in {"succeeded", "failed", "cancelled"}:
            return None
        return {
            "status": self.status,
            "user_id": "user-a",
            "trace_id": "trace-a",
            "terminalization_target": self.target,
            "terminalization_reason": self.reason,
            "terminalization_result_json": self.result,
            "terminalization_error_code": self.error_code,
            "terminalization_error_message": self.error_message,
        }

    async def clear_cancel_requested_terminalization(self, _conn: object, **_kwargs: Any):
        if self.target != "cancel_requested":
            return None
        self.target = None
        self.reason = ""
        self.result = {}
        self.error_code = None
        self.error_message = None
        return {"status": self.status}

    async def finalize_staged_terminalization(self, _conn: object, **kwargs: Any):
        if self.status in {"succeeded", "failed", "cancelled"}:
            return {"already_terminal": True, "status": self.status}
        if kwargs["target_status"] != self.target:
            return None
        self.status = self.target
        return {
            "status": self.status,
            "artifact_count": 0,
            "latency_ms": kwargs.get("latency_ms"),
            "input_token_count": kwargs.get("input_token_count", 0),
            "output_token_count": kwargs.get("output_token_count", 0),
            "total_token_count": kwargs.get("total_token_count", 0),
            "estimated_cost_minor": kwargs.get("estimated_cost_minor", 0),
        }

    async def complete_run(self, _conn: object, **_kwargs: Any) -> bool:
        if self.status in {"succeeded", "failed", "cancelled"} or self.target is not None:
            return False
        self.status = "succeeded"
        return True

    async def classify_success_commit_block(self, _conn: object, **_kwargs: Any) -> str:
        return "cancel_requested" if self.target in {"cancel_requested", "cancelled"} else "stale_terminal_state"

    async def is_cancel_requested(self, _conn: object, **_kwargs: Any) -> bool:
        return self.target in {"cancel_requested", "cancelled"}

    async def mark_run_running(self, _conn: object, **_kwargs: Any):
        return None

    async def list_stale_run_reconciliation_candidates(self, _conn: object, **_kwargs: Any):
        return []


def service(persistence: MemoryPersistence, events: list[dict[str, Any]], audits: list[dict[str, Any]], *, fail_audit: bool = False):
    async def append_event(conn: object, **kwargs: Any) -> None:
        events.append({"_conn": conn, **kwargs})

    async def append_audit(conn: object, **kwargs: Any) -> None:
        if fail_audit:
            raise RuntimeError("audit write failed")
        audits.append({"_conn": conn, **kwargs})

    return RunLifecycleService(
        persistence=persistence,
        append_event=append_event,
        append_audit_log=append_audit,
        validate_result_size=require_run_result_size,
        sanitize_payload=sanitize_public_payload,
        sanitize_text=sanitize_public_text,
        make_trace_id=standard_trace_id,
    )


@pytest.mark.asyncio
async def test_complete_run_returns_false_when_terminal_intent_won_the_cas():
    persistence = MemoryPersistence()
    persistence.target = "cancelled"
    lifecycle = service(persistence, [], [])

    completed = await lifecycle.complete_run(
        object(), tenant_id="tenant-a", run_id="run-a", result_json={"message": "done"}
    )

    assert completed is False
    assert persistence.status == "running"


@pytest.mark.asyncio
async def test_oversized_result_keeps_repository_conflict_contract():
    lifecycle = service(MemoryPersistence(), [], [])

    with pytest.raises(RepositoryConflictError, match="run_result_too_large"):
        await lifecycle.complete_run(
            object(), tenant_id="tenant-a", run_id="run-a", result_json={"large": "x" * (256 * 1024)}
        )


@pytest.mark.asyncio
async def test_fail_and_cancel_keep_first_final_intent_and_emit_one_fact_pair():
    persistence = MemoryPersistence()
    events: list[dict[str, Any]] = []
    audits: list[dict[str, Any]] = []
    lifecycle = service(persistence, events, audits)
    conn = object()

    failed = await lifecycle.fail_run(
        conn, tenant_id="tenant-a", run_id="run-a", error_code="executor_failed",
        error_message="worker failed", result_json={"message": "failed"},
    )
    late_cancel = await lifecycle.cancel_run(
        conn, tenant_id="tenant-a", run_id="run-a", result_json={"message": "cancelled"}
    )

    assert failed.is_terminal("failed") and failed.did_transition
    assert not late_cancel.completed and late_cancel.status is None
    assert persistence.status == "failed"
    assert len(events) == len(audits) == 1
    assert events[0]["event_type"] == "run_failed"
    assert audits[0]["action"] == "run.failed"
    assert audits[0]["user_id"] is None
    assert events[0]["_conn"] is audits[0]["_conn"] is conn


@pytest.mark.asyncio
async def test_cancel_requested_stage_is_cleared_without_terminal_facts():
    persistence = MemoryPersistence()
    persistence.target = "cancel_requested"
    events: list[dict[str, Any]] = []
    audits: list[dict[str, Any]] = []
    lifecycle = service(persistence, events, audits)

    progress = await lifecycle.progress_run_terminalization(
        object(), tenant_id="tenant-a", run_id="run-a"
    )

    assert progress is not None and not progress.completed
    assert progress.status == "running"
    assert persistence.status == "running"
    assert persistence.target is None
    assert events == audits == []


@pytest.mark.asyncio
async def test_existing_terminal_run_is_reported_without_duplicate_event_or_audit():
    persistence = MemoryPersistence()
    persistence.status = "failed"
    events: list[dict[str, Any]] = []
    audits: list[dict[str, Any]] = []
    lifecycle = service(persistence, events, audits)

    progress = await lifecycle.progress_run_terminalization(
        object(), tenant_id="tenant-a", run_id="run-a"
    )

    assert progress is not None and progress.completed
    assert progress.status == "failed"
    assert progress.did_transition is False
    assert events == audits == []


@pytest.mark.asyncio
async def test_enqueue_failure_audit_uses_stable_trace_fallback():
    persistence = MemoryPersistence()
    events: list[dict[str, Any]] = []
    audits: list[dict[str, Any]] = []
    lifecycle = service(persistence, events, audits)

    await lifecycle.mark_run_enqueue_failed(
        object(), tenant_id="tenant-a", user_id="user-a", run_id="run-a"
    )

    queue_audit = next(item for item in audits if item["action"] == "run.queue.enqueue_failed")
    assert queue_audit["user_id"] == "user-a"
    assert queue_audit["trace_id"] == standard_trace_id("run-a")


@pytest.mark.asyncio
async def test_event_and_audit_failure_roll_back_terminal_state_as_one_transaction():
    persistence = MemoryPersistence()
    events: list[dict[str, Any]] = []
    audits: list[dict[str, Any]] = []
    lifecycle = service(persistence, events, audits, fail_audit=True)
    committed = False

    @asynccontextmanager
    async def transaction():
        nonlocal committed
        before = deepcopy(persistence.__dict__)
        try:
            yield object()
        except Exception:
            persistence.__dict__.clear()
            persistence.__dict__.update(before)
            events.clear()
            audits.clear()
            raise
        else:
            committed = True

    with pytest.raises(RuntimeError, match="audit write failed"):
        async with transaction() as conn:
            await lifecycle.cancel_run(
                conn, tenant_id="tenant-a", run_id="run-a", result_json={"message": "cancelled"}
            )

    assert committed is False
    assert persistence.status == "running"
    assert persistence.target is None
    assert events == audits == []
