"""One structured timeout budget for governed tool-permission execution."""

from dataclasses import dataclass
from typing import Any


TOOL_PERMISSION_REQUEST_TTL_SECONDS = 900.0
TOOL_PERMISSION_EXPIRY_BATCH_LIMIT = 50
_PERMISSION_TRANSPORT_MARGIN_SECONDS = 15.0
_SANDBOX_SDK_INNER_MARGIN_SECONDS = 15.0
_OUTER_EXECUTOR_MARGIN_SECONDS = 15.0
_NON_PERMISSION_CALLBACK_TIMEOUT_SECONDS = 10.0


@dataclass(frozen=True)
class ToolPermissionBudget:
    """Nested timeout policy for one sandbox-brokered SDK execution.

    Multiple brokered callbacks share one enclosing SDK deadline rather than
    each receiving another full request TTL.
    """

    request_ttl_seconds: float
    permission_wait_seconds: float
    aggregate_permission_wait_seconds: float
    permission_callback_timeout_seconds: float
    sandbox_sdk_timeout_seconds: float
    outer_executor_timeout_seconds: float
    non_permission_callback_timeout_seconds: float


def tool_permission_budget(normal_execution_timeout_seconds: float = 120.0) -> ToolPermissionBudget:
    """Build the authoritative nested timeout budget for a configured SDK run."""

    normal_execution_seconds = max(float(normal_execution_timeout_seconds), 0.0)
    permission_wait_seconds = TOOL_PERMISSION_REQUEST_TTL_SECONDS
    permission_callback_timeout_seconds = permission_wait_seconds + _PERMISSION_TRANSPORT_MARGIN_SECONDS
    sandbox_sdk_timeout_seconds = (
        normal_execution_seconds + permission_wait_seconds + _SANDBOX_SDK_INNER_MARGIN_SECONDS
    )
    outer_executor_timeout_seconds = sandbox_sdk_timeout_seconds + _OUTER_EXECUTOR_MARGIN_SECONDS
    return ToolPermissionBudget(
        request_ttl_seconds=TOOL_PERMISSION_REQUEST_TTL_SECONDS,
        permission_wait_seconds=permission_wait_seconds,
        aggregate_permission_wait_seconds=permission_wait_seconds,
        permission_callback_timeout_seconds=permission_callback_timeout_seconds,
        sandbox_sdk_timeout_seconds=sandbox_sdk_timeout_seconds,
        outer_executor_timeout_seconds=outer_executor_timeout_seconds,
        non_permission_callback_timeout_seconds=_NON_PERMISSION_CALLBACK_TIMEOUT_SECONDS,
    )


def callback_timeout_seconds(payload: dict[str, Any]) -> float:
    """Select the long transport only for the governed permission callback."""

    budget = tool_permission_budget()
    if isinstance(payload.get("tool_name"), str) and isinstance(payload.get("tool_call_id"), str):
        return budget.permission_callback_timeout_seconds
    return budget.non_permission_callback_timeout_seconds
