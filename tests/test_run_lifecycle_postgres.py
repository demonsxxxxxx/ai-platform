from __future__ import annotations

from typing import Any

import pytest

from app.runs.infrastructure.lifecycle_postgres import (
    PostgresRunLifecyclePersistence,
    clear_cancel_requested_terminalization,
    list_stale_run_reconciliation_candidates,
    load_staged_terminalization,
    stage_run_terminalization,
)


class Cursor:
    def __init__(self, row: dict[str, Any] | None = None):
        self.row = row

    async def fetchone(self):
        return self.row

    async def fetchall(self):
        return self.row if isinstance(self.row, list) else []


class Connection:
    def __init__(self, rows: list[dict[str, Any] | None] | None = None):
        self.rows = list(rows or [])
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    async def execute(self, query: str, params: tuple[Any, ...]):
        self.calls.append((" ".join(query.split()).lower(), params))
        return Cursor(self.rows.pop(0) if self.rows else None)


@pytest.mark.asyncio
async def test_terminalization_stage_uses_ordinary_columns_and_first_intent_cas():
    conn = Connection([{"id": "run-a", "terminalization_target": "failed"}])

    row = await stage_run_terminalization(
        conn, tenant_id="tenant-a", run_id="run-a", target_status="failed",
        terminal_reason="run_failed", result_json={"message": "任务失败"},
        error_code="executor_failed", error_message="failed",
    )

    sql, params = conn.calls[0]
    assert row == {"id": "run-a", "terminalization_target": "failed"}
    assert "terminalization_target = case" in sql
    assert "terminalization_reason = case" in sql
    assert "terminalization_result_json = case" in sql
    assert "permission_terminalization" not in sql
    assert "run_tool_permission_requests" not in sql
    assert "status not in ('succeeded', 'failed', 'cancelled')" in sql
    assert params[0:4] == ("failed", "failed", "failed", "run_failed")
    assert params[-2:] == ("tenant-a", "run-a")


@pytest.mark.asyncio
async def test_success_update_is_a_scoped_terminal_and_cancellation_cas():
    conn = Connection([{"id": "run-a"}])
    persistence = PostgresRunLifecyclePersistence()

    changed = await persistence.complete_run(
        conn, tenant_id="tenant-a", run_id="run-a", result_json={"message": "done"},
        observability=(250, 11, 13, 24, 17),
    )

    sql, params = conn.calls[0]
    assert changed is True
    assert "status not in ('succeeded', 'failed', 'cancelled')" in sql
    assert "cancel_requested_at is null" in sql
    assert "terminalization_target is null" in sql
    assert "run_tool_permission_requests" not in sql
    assert params[1:6] == (250, 11, 13, 24, 17)


@pytest.mark.asyncio
async def test_terminal_update_is_scoped_and_clears_only_the_winning_staged_target():
    conn = Connection([
        {"id": "run-a", "user_id": "user-a", "trace_id": "trace-a", "status": "failed",
         "latency_ms": 250, "input_token_count": 11, "output_token_count": 13,
         "total_token_count": 24, "estimated_cost_minor": 17},
        None,  # Closing open steps does not return an artifact count.
        {"artifact_count": 2},
    ])

    finalized = await PostgresRunLifecyclePersistence().finalize_staged_terminalization(
        conn, tenant_id="tenant-a", run_id="run-a", target_status="failed",
        latency_ms=250, input_token_count=11, output_token_count=13,
        total_token_count=24, estimated_cost_minor=17,
    )

    sql, params = conn.calls[0]
    assert finalized is not None and finalized["artifact_count"] == 2
    assert "where tenant_id = %s and id = %s" in sql
    assert "terminalization_target = %s" in sql
    assert "status not in ('succeeded', 'failed', 'cancelled')" in sql
    assert "run_tool_permission_requests" not in sql
    assert params[-3:] == ("tenant-a", "run-a", "failed")
    assert any("update run_steps" in statement for statement, _ in conn.calls)
    assert any("from artifacts" in statement for statement, _ in conn.calls)


@pytest.mark.asyncio
async def test_terminalization_loader_can_observe_already_terminal_run():
    conn = Connection([{"id": "run-a", "status": "failed", "terminalization_target": None}])

    row = await load_staged_terminalization(conn, tenant_id="tenant-a", run_id="run-a")

    assert row is not None and row["status"] == "failed"
    sql, _ = conn.calls[0]
    assert "for update" in sql
    assert "status not in ('succeeded', 'failed', 'cancelled')" not in sql
    assert "terminalization_target is not null" in sql
    assert "or status in ('succeeded', 'failed', 'cancelled')" in sql


@pytest.mark.asyncio
async def test_cancel_requested_stage_cleanup_only_clears_stage_and_keeps_run_active():
    conn = Connection([{"id": "run-a", "status": "running"}])

    row = await clear_cancel_requested_terminalization(
        conn, tenant_id="tenant-a", run_id="run-a"
    )

    sql, params = conn.calls[0]
    assert row == {"id": "run-a", "status": "running"}
    assert "set terminalization_target = null" in sql
    assert "status not in ('succeeded', 'failed', 'cancelled')" in sql
    assert "set status =" not in sql
    assert params == ("tenant-a", "run-a")


@pytest.mark.asyncio
async def test_stale_candidate_scan_preserves_legacy_filter_and_bounded_skip_locked_selection():
    conn = Connection([[]])

    rows = await list_stale_run_reconciliation_candidates(
        conn, stale_after_seconds=0, cancel_requested_after_seconds=-1, limit=100
    )

    sql, params = conn.calls[0]
    assert rows == []
    assert "run_attempts" not in sql
    assert "sandbox_leases.status = 'active'" in sql
    assert "limit %s for update of runs skip locked" in sql
    assert params == (1, 1, 50)
