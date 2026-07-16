from app.tool_permission_lifecycle import ToolPermissionWaitLedger, tool_permission_budget


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
