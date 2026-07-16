"""One structured timeout budget for governed tool-permission execution."""

from dataclasses import dataclass
from time import monotonic as system_monotonic
from typing import Any, Callable


TOOL_PERMISSION_REQUEST_TTL_SECONDS = 900.0
TOOL_PERMISSION_EXPIRY_BATCH_LIMIT = 50
_PERMISSION_TRANSPORT_MARGIN_SECONDS = 15.0
_POST_AUTHORIZATION_EXECUTION_SECONDS = 15.0
_SANDBOX_SDK_INNER_MARGIN_SECONDS = 15.0
_OUTER_EXECUTOR_MARGIN_SECONDS = 15.0
_NON_PERMISSION_CALLBACK_TIMEOUT_SECONDS = 10.0


@dataclass(frozen=True)
class ToolPermissionBudget:
    """Nested timeout policy for one sandbox-brokered SDK execution.

    Multiple brokered callbacks share one enclosing SDK deadline rather than
    each receiving another full request TTL.
    """

    normal_execution_timeout_seconds: float
    request_ttl_seconds: float
    permission_wait_seconds: float
    aggregate_permission_wait_seconds: float
    permission_callback_timeout_seconds: float
    post_authorization_execution_seconds: float
    sandbox_sdk_timeout_seconds: float
    executor_callback_timeout_seconds: float
    normal_outer_executor_timeout_seconds: float
    outer_executor_timeout_seconds: float
    non_permission_callback_timeout_seconds: float


@dataclass(frozen=True)
class PermissionCallbackWindow:
    """One bounded slice of the shared permission wait allowance."""

    started_at: float
    wait_timeout_seconds: float
    transport_timeout_seconds: float


class ToolPermissionWaitLedger:
    """Measure all broker waits against one monotonic allowance for an SDK run."""

    def __init__(self, budget: ToolPermissionBudget, *, monotonic=system_monotonic) -> None:
        self._budget = budget
        self._monotonic = monotonic
        self._consumed_seconds = 0.0

    @property
    def remaining_wait_seconds(self) -> float:
        """Return the still-authoritative aggregate wait without adding a new TTL."""

        return max(self._budget.aggregate_permission_wait_seconds - self._consumed_seconds, 0.0)

    def begin_callback(self) -> PermissionCallbackWindow | None:
        """Reserve the current remainder for one callback, or fail closed when empty."""

        remaining = self.remaining_wait_seconds
        if remaining <= 0:
            return None
        return PermissionCallbackWindow(
            started_at=float(self._monotonic()),
            wait_timeout_seconds=remaining,
            transport_timeout_seconds=remaining + _PERMISSION_TRANSPORT_MARGIN_SECONDS,
        )

    def finish_callback(self, window: PermissionCallbackWindow) -> None:
        """Charge elapsed callback time once, bounded by the shared allowance."""

        elapsed = max(float(self._monotonic()) - window.started_at, 0.0)
        self._consumed_seconds = min(
            self._budget.aggregate_permission_wait_seconds,
            self._consumed_seconds + elapsed,
        )


def tool_permission_budget(normal_execution_timeout_seconds: float = 120.0) -> ToolPermissionBudget:
    """Build the authoritative nested timeout budget for a configured SDK run."""

    normal_execution_seconds = max(float(normal_execution_timeout_seconds), 0.0)
    permission_wait_seconds = TOOL_PERMISSION_REQUEST_TTL_SECONDS
    permission_callback_timeout_seconds = permission_wait_seconds + _PERMISSION_TRANSPORT_MARGIN_SECONDS
    sandbox_sdk_timeout_seconds = (
        normal_execution_seconds
        + permission_callback_timeout_seconds
        + _POST_AUTHORIZATION_EXECUTION_SECONDS
        + _SANDBOX_SDK_INNER_MARGIN_SECONDS
    )
    executor_callback_timeout_seconds = _NON_PERMISSION_CALLBACK_TIMEOUT_SECONDS
    # The ordinary sandbox POST contains the initial callback, runner, final
    # observation callback, and response hand-off.  Keep this separate from
    # governed nesting so non-governed runs retain their runner policy while
    # the transport has room to report truthful observations.
    normal_outer_executor_timeout_seconds = (
        normal_execution_seconds
        + (2 * executor_callback_timeout_seconds)
        + _OUTER_EXECUTOR_MARGIN_SECONDS
    )
    outer_executor_timeout_seconds = (
        sandbox_sdk_timeout_seconds
        + (2 * executor_callback_timeout_seconds)
        + _OUTER_EXECUTOR_MARGIN_SECONDS
    )
    return ToolPermissionBudget(
        normal_execution_timeout_seconds=normal_execution_seconds,
        request_ttl_seconds=TOOL_PERMISSION_REQUEST_TTL_SECONDS,
        permission_wait_seconds=permission_wait_seconds,
        aggregate_permission_wait_seconds=permission_wait_seconds,
        permission_callback_timeout_seconds=permission_callback_timeout_seconds,
        post_authorization_execution_seconds=_POST_AUTHORIZATION_EXECUTION_SECONDS,
        sandbox_sdk_timeout_seconds=sandbox_sdk_timeout_seconds,
        executor_callback_timeout_seconds=executor_callback_timeout_seconds,
        normal_outer_executor_timeout_seconds=normal_outer_executor_timeout_seconds,
        outer_executor_timeout_seconds=outer_executor_timeout_seconds,
        non_permission_callback_timeout_seconds=_NON_PERMISSION_CALLBACK_TIMEOUT_SECONDS,
    )


def callback_timeout_seconds(payload: dict[str, Any]) -> float:
    """Select the long transport only for the governed permission callback."""

    budget = tool_permission_budget()
    if isinstance(payload.get("tool_name"), str) and isinstance(payload.get("tool_call_id"), str):
        requested_wait = payload.get("permission_wait_seconds")
        if isinstance(requested_wait, int | float) and not isinstance(requested_wait, bool):
            return max(min(float(requested_wait), budget.aggregate_permission_wait_seconds), 0.0) + (
                budget.permission_callback_timeout_seconds - budget.permission_wait_seconds
            )
        return budget.permission_callback_timeout_seconds
    return budget.non_permission_callback_timeout_seconds


async def drain_run_tool_permission_terminalization(
    *,
    tenant_id: str,
    run_id: str,
    transaction_factory: Callable[[], Any],
    max_batches: int = 4,
) -> dict[str, Any] | None:
    """Commit a bounded number of durable terminalization batches for one exact run."""

    from app import repositories

    result: dict[str, Any] | None = None
    for _ in range(max(1, int(max_batches))):
        async with transaction_factory() as conn:
            result = await repositories.progress_run_tool_permission_terminalization(
                conn,
                tenant_id=tenant_id,
                run_id=run_id,
            )
        if result is None or result.get("completed") is True or result.get("status") is None:
            return result
    return result
