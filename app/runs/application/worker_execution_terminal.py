"""Runs-owned cancellation and executor-exception terminal transactions."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, AsyncContextManager


@dataclass(frozen=True)
class WorkerExecutionTerminalOutcome:
    status: str
    error_code: str | None = None
    error_message: str | None = None


class WorkerExecutionTerminalService:
    def __init__(
        self, *,
        transaction_factory: Callable[[], AsyncContextManager[Any]],
        prepare_pending_authority: Callable[..., Awaitable[Any]],
        append_event: Callable[..., Awaitable[Any]],
        fail_run: Callable[..., Awaitable[bool]],
    ) -> None:
        self._transaction_factory = transaction_factory
        self._prepare_pending_authority = prepare_pending_authority
        self._append_event = append_event
        self._fail_run = fail_run

    async def cancel(
        self, *, attempt: Any, capabilities: Any,
        release_runtime_lease: Callable[..., Awaitable[None]],
    ) -> WorkerExecutionTerminalOutcome:
        async with self._transaction_factory() as conn:
            terminal_written = await attempt.cancel(
                conn, capabilities=capabilities, result_json={"message": "任务已取消"},
            )
            if not terminal_written:
                return WorkerExecutionTerminalOutcome(
                    "skipped", "stale_terminal_state", "Run already reached a terminal state",
                )
            await release_runtime_lease(conn, reason="run_cancelled")
        return WorkerExecutionTerminalOutcome("cancelled")

    async def fail_or_cancel(
        self, *, payload: Any, attempt_id: str,
        attempt: Any, capabilities: Any,
        failure_code: str, failure_message: str, failure_result: dict[str, Any],
        release_runtime_lease: Callable[..., Awaitable[None]],
    ) -> WorkerExecutionTerminalOutcome:
        async with self._transaction_factory() as conn:
            await self._prepare_pending_authority(
                conn, tenant_id=payload.tenant_id, run_id=payload.run_id,
                attempt_id=attempt_id,
            )
            if await attempt.is_cancel_requested(conn):
                terminal_written = await attempt.cancel(
                    conn, capabilities=capabilities, result_json={"message": "任务已取消"},
                )
                if not terminal_written:
                    return WorkerExecutionTerminalOutcome(
                        "skipped", "stale_terminal_state", "Run already reached a terminal state",
                    )
                await release_runtime_lease(conn, reason="run_cancelled")
                return WorkerExecutionTerminalOutcome("cancelled")
            terminal_written = await self._fail_run(
                conn, payload=payload, tenant_id=payload.tenant_id,
                run_id=payload.run_id, error_code=failure_code,
                error_message=failure_message, result_json=failure_result,
                capabilities=capabilities, attempt_lifecycle=attempt,
            )
            if not terminal_written:
                return WorkerExecutionTerminalOutcome(
                    "skipped", "stale_terminal_state", "Run already reached a terminal state",
                )
            await self._append_event(
                conn, tenant_id=payload.tenant_id, run_id=payload.run_id,
                event_type="error", stage="executor", message="Executor failed",
                payload={
                    "error": failure_message,
                    "executor_type": payload.executor_type,
                    "visible_to_user": False,
                },
            )
            await release_runtime_lease(conn, reason="run_failed")
        return WorkerExecutionTerminalOutcome("failed", failure_code, failure_message)
