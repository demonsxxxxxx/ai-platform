"""Transport timeouts for executor requests."""

from __future__ import annotations

from dataclasses import dataclass


EXECUTOR_CALLBACK_TIMEOUT_SECONDS = 10.0
EXECUTOR_TRANSPORT_MARGIN_SECONDS = 15.0


@dataclass(frozen=True)
class ExecutorTimeoutBudget:
    execution_timeout_seconds: float
    callback_timeout_seconds: float
    outer_timeout_seconds: float


def executor_timeout_budget(execution_timeout_seconds: float = 120.0) -> ExecutorTimeoutBudget:
    execution_seconds = max(float(execution_timeout_seconds), 0.0)
    callback_seconds = EXECUTOR_CALLBACK_TIMEOUT_SECONDS
    return ExecutorTimeoutBudget(
        execution_timeout_seconds=execution_seconds,
        callback_timeout_seconds=callback_seconds,
        outer_timeout_seconds=(
            execution_seconds
            + (2 * callback_seconds)
            + EXECUTOR_TRANSPORT_MARGIN_SECONDS
        ),
    )
