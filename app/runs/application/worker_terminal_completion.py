"""Complete an admitted Run from either an inline executor or a durable receipt."""

from dataclasses import dataclass
from typing import Any, Callable

from app.runs.application.worker_result_commit import WorkerResultCommitService
from app.runs.application.worker_execution_terminal import WorkerExecutionTerminalService


@dataclass(frozen=True)
class WorkerOutcome:
    status: str
    run_id: str | None
    error_code: str | None = None
    error_message: str | None = None


class WorkerTerminalCompletionService:
    """One terminal pipeline; publication errors never re-enter terminalization.

    A service is scoped to one admitted Run. The release marker changes only
    after its terminal transaction exits, before publication can fail.
    """

    def __init__(
        self, *, payload: Any, attempt_id: str, attempt_authority: Any,
        capabilities: Any, release_runtime_lease: Callable,
        result_commit: WorkerResultCommitService,
        execution_terminal: WorkerExecutionTerminalService,
        normalize_result: Callable, build_command: Callable,
        exception_failure: Callable, cancelled_error: type[Exception],
        publish: Callable, publish_result: Callable,
        outer_terminal_transaction: bool = False,
        terminal_fallback_allowed: bool = True,
    ) -> None:
        self._payload = payload
        self._attempt_id = attempt_id
        self._attempt = attempt_authority
        self._capabilities = capabilities
        self._release = release_runtime_lease
        self._result_commit = result_commit
        self._execution_terminal = execution_terminal
        self._normalize_result = normalize_result
        self._build_command = build_command
        self._exception_failure = exception_failure
        self._cancelled_error = cancelled_error
        self._publish = publish
        self._publish_result = publish_result
        self._outer_terminal_transaction = outer_terminal_transaction
        self._terminal_fallback_allowed = terminal_fallback_allowed
        self.runtime_lease_released = False

    def _outcome(self, terminal: Any) -> WorkerOutcome:
        return WorkerOutcome(
            terminal.status, self._payload.run_id,
            terminal.error_code, terminal.error_message,
        )

    async def cancel(self) -> WorkerOutcome:
        if self._outer_terminal_transaction or not self._terminal_fallback_allowed:
            raise self._cancelled_error()
        terminal = await self._execution_terminal.cancel(
            attempt=self._attempt, capabilities=self._capabilities,
            release_runtime_lease=self._release,
        )
        self.runtime_lease_released = terminal.status == "cancelled"
        await self._publish()
        return self._outcome(terminal)

    async def fail(self, exc: Exception) -> WorkerOutcome:
        if self._outer_terminal_transaction or not self._terminal_fallback_allowed:
            raise exc
        code, message, result = self._exception_failure(exc)
        terminal = await self._execution_terminal.fail_or_cancel(
            payload=self._payload, attempt_id=self._attempt_id,
            attempt=self._attempt, capabilities=self._capabilities,
            failure_code=code, failure_message=message, failure_result=result,
            continuation_settlement=getattr(exc, "predispatch_continuation_settlement", None),
            release_runtime_lease=self._release,
        )
        self.runtime_lease_released = terminal.status in {"failed", "cancelled"}
        await self._publish()
        return self._outcome(terminal)

    async def complete(self, result: Any, *, latency_ms: int | None) -> WorkerOutcome:
        try:
            result = self._normalize_result(result)
        except self._cancelled_error:
            return await self.cancel()
        except Exception as exc:  # noqa: BLE001 - executor result boundary.
            return await self.fail(exc)
        # Projection/persistence/publication errors propagate to the caller.
        # They are not executor failures and must not overwrite committed facts.
        command = self._build_command(result, latency_ms=latency_ms)
        terminal = await self._result_commit.commit(
            command, attempt_authority=self._attempt,
            capabilities=self._capabilities, release_runtime_lease=self._release,
        )
        self.runtime_lease_released = terminal.status in {"succeeded", "failed", "cancelled"}
        if terminal.publish_run_event and not self._outer_terminal_transaction:
            await self._publish_result(command, terminal)
        return self._outcome(terminal)
