"""Runs-owned locked Worker dispatch admission transaction."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, AsyncContextManager, Protocol

from app.runs.application.worker_capability_admission import WorkerCapabilityAuthorization
from app.runs.application.worker_queue_envelope import WorkerDispatchPayload

AsyncPort = Callable[..., Awaitable[Any]]


@dataclass(frozen=True)
class WorkerAdmissionOutcome:
    status: str
    error_code: str | None = None
    error_message: str | None = None


@dataclass(frozen=True)
class WorkerAuthorizedDispatchCandidate:
    payload: WorkerDispatchPayload
    locked_run: dict[str, Any]
    run_identity: dict[str, str]
    trace_id: str
    authorization: WorkerCapabilityAuthorization | None = None
    outcome: WorkerAdmissionOutcome | None = None
    publish_after_commit: bool = False


@dataclass(frozen=True)
class WorkerDispatchAdmissionResult:
    payload: WorkerDispatchPayload
    locked_run: dict[str, Any] | None
    run_identity: dict[str, str]
    trace_id: str
    attempt_authority: WorkerDispatchAttemptAuthority
    authorization: WorkerCapabilityAuthorization | None = None
    outcome: WorkerAdmissionOutcome | None = None
    publish_after_commit: bool = False


class WorkerDispatchAttemptAuthority(Protocol):
    async def restore_reconciliation_authority(self, conn: Any) -> WorkerDispatchAttemptAuthority: ...
    async def is_cancel_requested(self, conn: Any) -> bool: ...
    async def cancel(
        self, conn: Any, *, capabilities: Any, result_json: dict[str, Any] | None = None,
    ) -> bool: ...
    async def fail(
        self, conn: Any, *, capabilities: Any, error_code: str,
        error_message: str, result_json: dict[str, Any] | None = None,
    ) -> bool: ...


class WorkerDispatchAdmissionService:
    def __init__(
        self,
        *,
        transaction_factory: Callable[[], AsyncContextManager[Any]],
        lock_queued_run: AsyncPort,
        get_run: AsyncPort,
        prepare_pending_authority: AsyncPort,
        authorize_locked_candidate: AsyncPort,
        append_hidden_event: AsyncPort,
        executor_resolution_error: Callable[[str], KeyError | None],
        predispatch_failure_result: Callable[..., dict[str, Any]],
    ) -> None:
        self._transaction_factory = transaction_factory
        self._lock_queued_run = lock_queued_run
        self._get_run = get_run
        self._prepare_pending_authority = prepare_pending_authority
        self._authorize_locked_candidate = authorize_locked_candidate
        self._append_hidden_event = append_hidden_event
        self._executor_resolution_error = executor_resolution_error
        self._predispatch_failure_result = predispatch_failure_result

    async def admit(
        self,
        *,
        payload: WorkerDispatchPayload,
        run_identity: dict[str, str],
        trace_id: str,
        attempt_id: str,
        attempt_authority: WorkerDispatchAttemptAuthority,
        capabilities: Any,
        current_principal: Any,
        reconciliation_attempt_id: str | None = None,
    ) -> WorkerDispatchAdmissionResult:
        async with self._transaction_factory() as conn:
            locked = (
                await self._get_run(conn, tenant_id=payload.tenant_id, run_id=payload.run_id)
                if reconciliation_attempt_id is not None
                else await self._lock_queued_run(conn, tenant_id=payload.tenant_id, run_id=payload.run_id)
            )

            def outcome(
                status: str, error_code: str | None = None,
                error_message: str | None = None, *, publish: bool = False,
                candidate: WorkerAuthorizedDispatchCandidate | None = None,
            ) -> WorkerDispatchAdmissionResult:
                return WorkerDispatchAdmissionResult(
                    payload=candidate.payload if candidate is not None else payload,
                    locked_run=candidate.locked_run if candidate is not None else locked,
                    run_identity=candidate.run_identity if candidate is not None else run_identity,
                    trace_id=candidate.trace_id if candidate is not None else trace_id,
                    attempt_authority=attempt_authority,
                    authorization=candidate.authorization if candidate is not None else None,
                    outcome=WorkerAdmissionOutcome(status, error_code, error_message),
                    publish_after_commit=publish,
                )

            if reconciliation_attempt_id is not None and locked is not None:
                if str(locked.get("status") or "") != "running":
                    return outcome("skipped", "stale_terminal_state", "Run already reached a terminal state")
                if reconciliation_attempt_id != attempt_id:
                    return outcome("skipped", "stale_reconciliation_attempt", "Executor reconciliation attempt is stale")
                attempt_authority = await attempt_authority.restore_reconciliation_authority(conn)
            if locked is not None:
                await self._prepare_pending_authority(
                    conn, tenant_id=payload.tenant_id, run_id=payload.run_id, attempt_id=attempt_id
                )
            if not locked:
                existing = await self._get_run(conn, tenant_id=payload.tenant_id, run_id=payload.run_id)
                if existing is None:
                    return outcome("skipped", "stale_queue_payload", "Run no longer exists for leased queue payload")
                if str(existing.get("status") or "") == "queued":
                    await self._prepare_pending_authority(
                        conn, tenant_id=payload.tenant_id, run_id=payload.run_id, attempt_id=attempt_id
                    )
                    code, message = "queue_payload_identity_mismatch", "Queued run identity is invalid"
                    terminal_written = await attempt_authority.fail(
                        conn, capabilities=capabilities, error_code=code,
                        error_message=message,
                        result_json=self._predispatch_failure_result(code, "worker", reason=code),
                    )
                    if not terminal_written:
                        return outcome("skipped", "stale_terminal_state", "Run already reached a terminal state", publish=True)
                    await self._append_hidden_event(
                        conn, tenant_id=payload.tenant_id, run_id=payload.run_id,
                        event_type="error", stage="worker", message=message,
                        payload={"visible_to_user": False, "severity": "error", "reason": "scope_guard_rejected_lock"},
                    )
                    return outcome("failed", code, message, publish=True)
                await self._append_hidden_event(
                    conn, tenant_id=payload.tenant_id, run_id=payload.run_id,
                    event_type="skip", stage="worker",
                    message="Run is not queued; skipping duplicate or stale payload",
                )
                return outcome("skipped")

            candidate: WorkerAuthorizedDispatchCandidate = await self._authorize_locked_candidate(
                conn, payload=payload, locked_run=locked,
                current_principal=current_principal,
                attempt_authority=attempt_authority, capabilities=capabilities,
                attempt_id=attempt_id, trace_id=trace_id,
                reconciliation=reconciliation_attempt_id is not None,
            )
            payload = candidate.payload
            if candidate.outcome is not None:
                return outcome(
                    candidate.outcome.status, candidate.outcome.error_code,
                    candidate.outcome.error_message,
                    publish=candidate.publish_after_commit, candidate=candidate,
                )
            if await attempt_authority.is_cancel_requested(conn):
                terminal_written = await attempt_authority.cancel(
                    conn, capabilities=capabilities, result_json={"message": "任务已取消"}
                )
                if not terminal_written:
                    return outcome("skipped", "stale_terminal_state", "Run already reached a terminal state", publish=True, candidate=candidate)
                return outcome("cancelled", publish=True, candidate=candidate)
            executor_error = self._executor_resolution_error(payload.executor_type)
            if executor_error is not None:
                error_message = str(executor_error)
                terminal_written = await attempt_authority.fail(
                    conn, capabilities=capabilities,
                    error_code="unknown_executor_type", error_message=error_message,
                    result_json=self._predispatch_failure_result(
                        "unknown_executor_type", "executor_resolution", error=executor_error
                    ),
                )
                if not terminal_written:
                    return outcome("skipped", "stale_terminal_state", "Run already reached a terminal state", publish=True, candidate=candidate)
                await self._append_hidden_event(
                    conn, tenant_id=payload.tenant_id, run_id=payload.run_id,
                    event_type="error", stage="worker", message="Unknown executor type",
                    payload={"executor_type": payload.executor_type},
                )
                return outcome("failed", "unknown_executor_type", error_message, publish=True, candidate=candidate)
            return WorkerDispatchAdmissionResult(
                payload=payload, locked_run=candidate.locked_run,
                run_identity=candidate.run_identity, trace_id=candidate.trace_id,
                attempt_authority=attempt_authority, authorization=candidate.authorization,
            )
