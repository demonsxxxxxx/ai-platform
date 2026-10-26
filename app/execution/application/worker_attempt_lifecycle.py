"""Attempt-bound worker orchestration behind explicit runtime ports."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import datetime
import re
from typing import Any, Protocol

from app.runs.api import (
    ExecutionSpec,
    ReconciliationAuthorityError,
    RunTerminalizationProgress,
    run_attempt_id_for_queue_attempt,
)


AsyncPort = Callable[..., Awaitable[Any]]


@dataclass(frozen=True, slots=True)
class WorkerQueueLease:
    """Exact Redis lease timing bound to one ordinary durable attempt."""

    queue_message_id: str
    last_heartbeat_at: datetime
    lease_expires_at: datetime

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[0-9a-f]{64}", self.queue_message_id):
            raise ValueError("run_attempt_queue_message_id_invalid")
        for value in (self.last_heartbeat_at, self.lease_expires_at):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError("run_attempt_queue_lease_timezone_required")
        if self.lease_expires_at <= self.last_heartbeat_at:
            raise ValueError("run_attempt_queue_lease_window_invalid")


class WorkerQueuePayload(Protocol):
    """Run identity and execution input required by worker orchestration."""

    tenant_id: str
    run_id: str
    input: dict[str, Any]


class WorkerBoundRunPayload(WorkerQueuePayload, Protocol):
    """Execution data bound to one durable Attempt and owner generation."""

    attempt_id: str
    owner_generation: int


@dataclass(frozen=True, slots=True)
class WorkerAttemptLifecyclePorts:
    """Concrete runtime capabilities supplied by the worker composition root."""

    lock_run: AsyncPort
    complete_run: AsyncPort
    fail_run: AsyncPort
    cancel_run: AsyncPort
    start_attempt: AsyncPort
    assert_current_attempt: AsyncPort
    request_attempt_cancel: AsyncPort
    terminalize_attempt: AsyncPort
    is_cancel_requested: AsyncPort
    classify_success_commit_block: AsyncPort
    conflict_error: Callable[[str], Exception]
    record_result_diagnostics: AsyncPort | None = None
    restore_reconciliation_attempt: AsyncPort | None = None


@dataclass(frozen=True, slots=True)
class WorkerExecutorReconciliation:
    result: Any
    lease_row: dict[str, Any]
    claim_token: str


@dataclass(frozen=True, slots=True)
class WorkerAttemptLifecycle:
    """Bind one worker execution to its queue and durable attempt authorities."""

    tenant_id: str
    run_id: str
    attempt_id: str
    queue_attempt_id: str
    worker_owner_id: str
    ports: WorkerAttemptLifecyclePorts
    queue_lease: WorkerQueueLease | None = None
    reconciliation_lease_id: str | None = None
    reconciliation_claim_token: str | None = None
    reconciliation_owner_generation: int | None = None
    reconciliation_scope: tuple[tuple[str, str], ...] = ()
    reconciliation_owner_bound: bool = False

    async def is_cancel_requested(self, conn: Any) -> bool:
        return bool(await self.ports.is_cancel_requested(
            conn, tenant_id=self.tenant_id, run_id=self.run_id,
        ))

    async def classify_success_commit_block(self, conn: Any) -> str:
        return str(await self.ports.classify_success_commit_block(
            conn, tenant_id=self.tenant_id, run_id=self.run_id,
        ))

    @classmethod
    def from_leased_attempt(
        cls,
        *,
        tenant_id: str,
        run_id: str,
        leased_attempt_id: str,
        worker_id: str | None,
        is_reconciliation: bool,
        ports: WorkerAttemptLifecyclePorts,
        queue_lease: WorkerQueueLease | None = None,
        reconciliation_lease_id: str | None = None,
        reconciliation_claim_token: str | None = None,
        reconciliation_owner_generation: int | None = None,
        reconciliation_scope: tuple[tuple[str, str], ...] = (),
    ) -> WorkerAttemptLifecycle:
        if (reconciliation_lease_id is None) is not (
            reconciliation_claim_token is None
        ):
            raise ValueError("executor_reconciliation_claim_incomplete")
        if is_reconciliation != (reconciliation_lease_id is not None):
            raise ValueError("executor_reconciliation_claim_binding_invalid")
        if is_reconciliation and queue_lease is not None:
            raise ValueError("executor_reconciliation_queue_lease_invalid")
        worker_owner_id = (
            str(worker_id or "worker-in-process").strip() or "worker-in-process"
        )
        attempt_id = (
            leased_attempt_id
            if is_reconciliation
            else run_attempt_id_for_queue_attempt(
                tenant_id=tenant_id,
                run_id=run_id,
                queue_attempt_id=leased_attempt_id,
            )
        )
        return cls(
            tenant_id=tenant_id,
            run_id=run_id,
            attempt_id=attempt_id,
            queue_attempt_id=leased_attempt_id,
            worker_owner_id=worker_owner_id,
            ports=ports,
            queue_lease=queue_lease,
            reconciliation_lease_id=reconciliation_lease_id,
            reconciliation_claim_token=reconciliation_claim_token,
            reconciliation_owner_generation=reconciliation_owner_generation,
            reconciliation_scope=reconciliation_scope,
        )

    @property
    def is_reconciliation(self) -> bool:
        return self.reconciliation_lease_id is not None

    async def restore_reconciliation_authority(
        self,
        conn: Any,
    ) -> WorkerAttemptLifecycle:
        """Restore the original queue worker owner from one durable attempt."""

        if not self.is_reconciliation:
            raise ValueError("executor_reconciliation_claim_missing")
        restored = await self._restore_reconciliation(conn)
        attempt = restored.attempt
        return replace(
            self, queue_attempt_id=str(attempt["queue_attempt_id"]),
            worker_owner_id=str(attempt["owner_id"]), reconciliation_owner_bound=True,
        )

    async def _restore_reconciliation(self, conn: Any) -> Any:
        if not self.is_reconciliation or self.ports.restore_reconciliation_attempt is None:
            raise ReconciliationAuthorityError("run_attempt_reconciliation_authority_missing")
        restored = await self.ports.restore_reconciliation_attempt(
            conn, tenant_id=self.tenant_id, run_id=self.run_id, attempt_id=self.attempt_id,
            lease_id=self.reconciliation_lease_id, claim_token=self.reconciliation_claim_token,
            expected_owner_generation=self.reconciliation_owner_generation,
            scope=dict(self.reconciliation_scope),
        )
        # Once restored, later reads must not silently adopt a replacement owner.
        if self.reconciliation_owner_bound and (
            restored.attempt["queue_attempt_id"] != self.queue_attempt_id
            or restored.attempt["owner_id"] != self.worker_owner_id
        ):
            raise ReconciliationAuthorityError("run_attempt_reconciliation_owner_stale")
        return restored

    async def restore_reconciliation_execution_spec(self, conn: Any) -> ExecutionSpec:
        """Restore only the immutable spec, never recompile current authority."""
        return (await self._restore_reconciliation(conn)).execution_spec

    async def bind_execution_spec(
        self,
        conn: Any,
        execution_spec: Any,
    ) -> dict[str, Any] | None:
        """Start an ordinary attempt; receipt recovery never binds a new spec."""

        if self.is_reconciliation:
            raise ValueError("run_attempt_reconciliation_must_restore_spec")
        lease_kwargs = (
            {
                "queue_message_id": self.queue_lease.queue_message_id,
                "last_heartbeat_at": self.queue_lease.last_heartbeat_at,
                "lease_expires_at": self.queue_lease.lease_expires_at,
            }
            if self.queue_lease is not None
            else {}
        )
        attempt = await self.ports.start_attempt(
            conn,
            tenant_id=self.tenant_id,
            run_id=self.run_id,
            queue_attempt_id=self.queue_attempt_id,
            worker_id=self.worker_owner_id,
            execution_spec=execution_spec,
            **lease_kwargs,
        )
        if str(attempt.get("id") or "") != self.attempt_id:
            raise self.ports.conflict_error(
                "run_attempt_identity_derivation_mismatch"
            )
        return attempt

    async def _lock_run(self, conn: Any) -> dict[str, Any]:
        run = await self.ports.lock_run(
            conn,
            tenant_id=self.tenant_id,
            run_id=self.run_id,
            for_update=True,
        )
        if run is None:
            raise self.ports.conflict_error("run_attempt_parent_missing")
        return run

    @staticmethod
    def _observed_terminal_progress(
        run: dict[str, Any],
        *,
        requested_status: str,
    ) -> RunTerminalizationProgress | None:
        status = str(run.get("status") or "")
        if status not in {"succeeded", "failed", "cancelled"}:
            return None
        return RunTerminalizationProgress(
            completed=status == requested_status,
            status=status,
        )

    async def _current_attempt(self, conn: Any) -> dict[str, Any] | None:
        if self.is_reconciliation:
            return (await self._restore_reconciliation(conn)).attempt
        return await self.ports.assert_current_attempt(
            conn,
            tenant_id=self.tenant_id,
            run_id=self.run_id,
            queue_attempt_id=self.queue_attempt_id,
            worker_id=self.worker_owner_id,
        )

    async def complete(
        self,
        conn: Any,
        *,
        capabilities: Any,
        result_json: dict[str, Any],
    ) -> bool:
        observed = self._observed_terminal_progress(
            await self._lock_run(conn),
            requested_status="succeeded",
        )
        if observed is not None:
            return observed.completed
        attempt = await self._current_attempt(conn)
        if self.ports.record_result_diagnostics is not None:
            result_json = await self.ports.record_result_diagnostics(
                conn,
                tenant_id=self.tenant_id,
                run_id=self.run_id,
                attempt_id=str(attempt["id"]) if attempt is not None else None,
                source="worker_executor",
                stage="terminalization",
                error_code="executor_diagnostics",
                result_json=result_json,
            )
        completed = await self.ports.complete_run(
            conn,
            capabilities=capabilities,
            tenant_id=self.tenant_id,
            run_id=self.run_id,
            result_json=result_json,
        )
        if completed and attempt is not None:
            await self.ports.terminalize_attempt(
                conn,
                tenant_id=self.tenant_id,
                run_id=self.run_id,
                attempt_id=str(attempt["id"]),
                status="succeeded",
                terminal_reason="run_succeeded",
            )
        return bool(completed)

    async def fail(
        self,
        conn: Any,
        *,
        capabilities: Any,
        error_code: str,
        error_message: str,
        result_json: dict[str, Any] | None = None,
    ) -> bool:
        observed = self._observed_terminal_progress(
            await self._lock_run(conn),
            requested_status="failed",
        )
        if observed is not None:
            return observed.completed
        attempt = await self._current_attempt(conn)
        if self.ports.record_result_diagnostics is not None:
            result_json = await self.ports.record_result_diagnostics(
                conn,
                tenant_id=self.tenant_id,
                run_id=self.run_id,
                attempt_id=str(attempt["id"]) if attempt is not None else None,
                source="worker_executor",
                stage="terminalization",
                error_code=error_code,
                result_json=result_json,
            )
        progress = await self.ports.fail_run(
            conn,
            capabilities=capabilities,
            tenant_id=self.tenant_id,
            run_id=self.run_id,
            error_code=error_code,
            error_message=error_message,
            result_json=result_json,
        )
        if progress.is_terminal("failed") and attempt is not None:
            await self.ports.terminalize_attempt(
                conn,
                tenant_id=self.tenant_id,
                run_id=self.run_id,
                attempt_id=str(attempt["id"]),
                status="failed",
                terminal_reason="run_failed",
                error_code=error_code,
            )
        return progress.is_terminal("failed")

    async def cancel(
        self,
        conn: Any,
        *,
        capabilities: Any,
        result_json: dict[str, Any] | None = None,
    ) -> bool:
        observed = self._observed_terminal_progress(
            await self._lock_run(conn),
            requested_status="cancelled",
        )
        if observed is not None:
            return observed.completed
        attempt = await self._current_attempt(conn)
        if attempt is not None:
            attempt = await self.ports.request_attempt_cancel(
                conn,
                tenant_id=self.tenant_id,
                run_id=self.run_id,
                attempt_id=str(attempt["id"]),
            )
        if (self.ports.record_result_diagnostics is not None
            and isinstance(result_json, dict) and "runtime_diagnostics" in result_json):
            result_json = await self.ports.record_result_diagnostics(
                conn, tenant_id=self.tenant_id, run_id=self.run_id,
                attempt_id=str(attempt["id"]) if attempt is not None else None,
                source="worker_executor", stage="terminalization",
                error_code=str(result_json.get("error_code") or "executor_cancelled"),
                result_json=result_json,
            )
        progress = await self.ports.cancel_run(
            conn,
            capabilities=capabilities,
            tenant_id=self.tenant_id,
            run_id=self.run_id,
            result_json=result_json,
        )
        if progress.is_terminal("cancelled") and attempt is not None:
            await self.ports.terminalize_attempt(
                conn,
                tenant_id=self.tenant_id,
                run_id=self.run_id,
                attempt_id=str(attempt["id"]),
                status="cancelled",
                terminal_reason="run_cancelled",
            )
        return progress.is_terminal("cancelled")

def bind_worker_attempt_lifecycle(
    payload: WorkerQueuePayload,
    *,
    leased_attempt_id: str,
    worker_id: str | None,
    reconciliation: WorkerExecutorReconciliation | None,
    ports: WorkerAttemptLifecyclePorts,
    queue_lease: WorkerQueueLease | None = None,
) -> WorkerAttemptLifecycle:
    scope = ()
    generation = None
    if reconciliation is not None:
        row = reconciliation.lease_row
        for field in ("tenant_id", "run_id", "workspace_id", "user_id", "session_id"):
            if row.get(field) != getattr(payload, field, None):
                raise ReconciliationAuthorityError("run_attempt_reconciliation_scope_invalid")
        if row.get("attempt_id") != leased_attempt_id:
            raise ReconciliationAuthorityError("run_attempt_reconciliation_scope_invalid")
        lease_payload = row.get("lease_payload_json")
        generation = lease_payload.get("owner_generation") if isinstance(lease_payload, dict) else None
        scope = tuple((field, getattr(payload, field, ""))
                      for field in ("workspace_id", "user_id", "session_id", "agent_id"))
    return WorkerAttemptLifecycle.from_leased_attempt(
        tenant_id=payload.tenant_id,
        run_id=payload.run_id,
        leased_attempt_id=leased_attempt_id,
        worker_id=worker_id,
        is_reconciliation=reconciliation is not None,
        ports=ports,
        queue_lease=queue_lease,
        reconciliation_lease_id=(
            str(reconciliation.lease_row["id"]) if reconciliation is not None else None
        ),
        reconciliation_claim_token=(
            reconciliation.claim_token if reconciliation is not None else None
        ),
        reconciliation_owner_generation=generation,
        reconciliation_scope=scope,
    )
