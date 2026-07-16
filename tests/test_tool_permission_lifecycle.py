from contextlib import asynccontextmanager

import pytest

from app import repositories
from app.tool_permission_lifecycle import (
    ToolPermissionWaitLedger,
    drain_run_tool_permission_terminalization,
    reconcile_terminalized_permission_run,
    tool_permission_budget,
)


def test_permission_budget_strictly_nests_the_full_wait_and_executor_callbacks():
    budget = tool_permission_budget(120.0)

    assert budget.permission_callback_timeout_seconds > budget.permission_wait_seconds
    assert budget.sandbox_sdk_timeout_seconds > (
        budget.normal_execution_timeout_seconds
        + budget.permission_callback_timeout_seconds
        + budget.post_authorization_execution_seconds
    )
    assert budget.outer_executor_timeout_seconds > (
        budget.sandbox_sdk_timeout_seconds
        + (2 * budget.executor_callback_timeout_seconds)
    )
    assert budget.normal_outer_executor_timeout_seconds > budget.normal_execution_timeout_seconds
    assert budget.normal_outer_executor_timeout_seconds >= (
        budget.normal_execution_timeout_seconds
        + (2 * budget.executor_callback_timeout_seconds)
    )


def test_permission_wait_ledger_consumes_one_monotonic_aggregate_allowance():
    clock = {"now": 0.0}
    budget = tool_permission_budget(120.0)
    ledger = ToolPermissionWaitLedger(budget, monotonic=lambda: clock["now"])

    first = ledger.begin_callback()
    assert first is not None
    assert first.wait_timeout_seconds == budget.aggregate_permission_wait_seconds
    assert first.transport_timeout_seconds > first.wait_timeout_seconds

    clock["now"] = 130.0
    ledger.finish_callback(first)
    second = ledger.begin_callback()
    assert second is not None
    assert second.wait_timeout_seconds == budget.aggregate_permission_wait_seconds - 130.0

    clock["now"] += second.wait_timeout_seconds
    ledger.finish_callback(second)
    assert ledger.begin_callback() is None


@pytest.mark.asyncio
async def test_drain_propagates_typed_partial_then_final_and_stops(monkeypatch):
    results = [
        repositories.ToolPermissionTerminalizationProgress(False, "failed"),
        repositories.ToolPermissionTerminalizationProgress(True, "failed", True, True),
    ]

    @asynccontextmanager
    async def tx():
        yield object()

    async def progress(_conn, **_kwargs):
        return results.pop(0)

    monkeypatch.setattr(repositories, "progress_run_tool_permission_terminalization", progress)
    result = await drain_run_tool_permission_terminalization(tenant_id="tenant-a", run_id="run-a", transaction_factory=tx)
    assert result.completed is True and result.did_transition is True and result.needs_reconcile is True
    assert results == []


@pytest.mark.asyncio
async def test_post_commit_reconcile_is_noop_unless_final_transition(monkeypatch):
    @asynccontextmanager
    async def tx():
        yield object()

    partial = repositories.ToolPermissionTerminalizationProgress(False, "failed")
    assert await reconcile_terminalized_permission_run(tenant_id="tenant-a", run_id="run-a", progress=partial, transaction_factory=tx) is None


@pytest.mark.asyncio
async def test_post_commit_reconcile_loads_durable_child_and_rolls_up_once(monkeypatch):
    """The worker/routes helper rehydrates committed child state instead of ephemeral executor data."""

    calls = []

    @asynccontextmanager
    async def tx():
        yield object()

    async def get_run(_conn, *, tenant_id, run_id, for_update):
        calls.append(("get", tenant_id, run_id, for_update))
        return {
            "id": run_id,
            "tenant_id": tenant_id,
            "status": "cancelled",
            "result_json": {"message": "任务已取消"},
            "error_code": None,
            "error_message": None,
        }

    async def reconcile(_conn, **kwargs):
        calls.append(("reconcile", kwargs["child_run_id"], kwargs["child_status"], kwargs["result_json"]))
        return {"parent_run_id": "parent-a", "status": "cancelled"}

    monkeypatch.setattr(repositories, "get_run", get_run)
    monkeypatch.setattr(repositories, "reconcile_multi_agent_child_run_terminal_state", reconcile)
    final = repositories.ToolPermissionTerminalizationProgress(True, "cancelled", True, True)

    result = await reconcile_terminalized_permission_run(
        tenant_id="tenant-a", run_id="child-a", progress=final, transaction_factory=tx
    )
    retry = await reconcile_terminalized_permission_run(
        tenant_id="tenant-a",
        run_id="child-a",
        progress=repositories.ToolPermissionTerminalizationProgress(True, "cancelled"),
        transaction_factory=tx,
    )

    assert result == {"parent_run_id": "parent-a", "status": "cancelled"}
    assert retry is None
    assert calls == [
        ("get", "tenant-a", "child-a", True),
        ("reconcile", "child-a", "cancelled", {"message": "任务已取消"}),
    ]
