"""Attempt-bound worker orchestration behind explicit runtime ports."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import datetime
import re
from typing import Any, Protocol

from app.runs.api import (
    ExecutionSpec,
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
    """Run identity and child-dispatch fields required by worker orchestration."""

    tenant_id: str
    run_id: str
    input: dict[str, Any]


@dataclass(frozen=True, slots=True)
class WorkerAttemptLifecyclePorts:
    """Concrete runtime capabilities supplied by the worker composition root."""

    lock_run: AsyncPort
    complete_run: AsyncPort
    fail_run: AsyncPort
    cancel_run: AsyncPort
    drain_terminalization: AsyncPort
    is_reconciliation_claim_current: AsyncPort
    get_attempt: AsyncPort
    get_attempt_for_queue_attempt: AsyncPort
    start_attempt: AsyncPort
    assert_current_attempt: AsyncPort
    request_attempt_cancel: AsyncPort
    terminalize_attempt: AsyncPort
    conflict_error: Callable[[str], Exception]


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
        attempt = await self.ports.get_attempt(
            conn,
            tenant_id=self.tenant_id,
            run_id=self.run_id,
            attempt_id=self.attempt_id,
            for_update=True,
        )
        if attempt is None:
            return self
        queue_attempt_id = str(attempt.get("queue_attempt_id") or "")
        worker_owner_id = str(attempt.get("owner_id") or "")
        if not queue_attempt_id:
            raise self.ports.conflict_error("run_attempt_queue_binding_missing")
        if (
            str(attempt.get("owner_kind") or "") != "queue_worker"
            or not worker_owner_id
        ):
            raise self.ports.conflict_error(
                "run_attempt_reconciliation_owner_invalid"
            )
        return replace(
            self,
            queue_attempt_id=queue_attempt_id,
            worker_owner_id=worker_owner_id,
        )

    async def restore_reconciliation_execution_spec(
        self,
        conn: Any,
    ) -> ExecutionSpec:
        """Restore the immutable execution spec bound to this attempt."""

        if not self.is_reconciliation:
            raise ValueError("executor_reconciliation_claim_missing")
        attempt = await self.ports.get_attempt_for_queue_attempt(
            conn,
            tenant_id=self.tenant_id,
            run_id=self.run_id,
            queue_attempt_id=self.queue_attempt_id,
            for_update=True,
        )
        if attempt is None:
            raise self.ports.conflict_error(
                "run_attempt_reconciliation_attempt_missing"
            )
        canonical_json = attempt.get("execution_spec_canonical_json")
        spec_sha256 = attempt.get("execution_spec_sha256")
        if not isinstance(canonical_json, str) or not isinstance(spec_sha256, str):
            raise self.ports.conflict_error("run_attempt_execution_spec_missing")
        try:
            return ExecutionSpec.from_canonical_json(
                canonical_json.encode("utf-8"),
                expected_sha256=spec_sha256,
            )
        except ValueError as exc:
            raise self.ports.conflict_error(
                "run_attempt_execution_spec_invalid"
            ) from exc

    async def bind_execution_spec(
        self,
        conn: Any,
        execution_spec: Any,
    ) -> dict[str, Any] | None:
        """Create an ordinary attempt or verify a restored reconciliation attempt."""

        if not self.is_reconciliation:
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
        attempt = await self.ports.get_attempt_for_queue_attempt(
            conn,
            tenant_id=self.tenant_id,
            run_id=self.run_id,
            queue_attempt_id=self.queue_attempt_id,
            for_update=True,
        )
        if attempt is not None and (
            str(attempt.get("status") or "") != "running"
            or str(attempt.get("execution_spec_sha256") or "")
            != execution_spec.spec_sha256
        ):
            raise self.ports.conflict_error("run_attempt_reconciliation_conflict")
        return attempt

    async def _require_reconciliation_claim(self, conn: Any) -> None:
        if not self.is_reconciliation:
            return
        if not await self.ports.is_reconciliation_claim_current(
            conn,
            lease_id=self.reconciliation_lease_id,
            claim_token=self.reconciliation_claim_token,
        ):
            raise RuntimeError("executor_reconciliation_claim_lost")

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
        await self._require_reconciliation_claim(conn)
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
    ) -> RunTerminalizationProgress:
        observed = self._observed_terminal_progress(
            await self._lock_run(conn),
            requested_status="failed",
        )
        if observed is not None:
            return observed
        attempt = await self._current_attempt(conn)
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
        return progress

    async def cancel(
        self,
        conn: Any,
        *,
        capabilities: Any,
        result_json: dict[str, Any] | None = None,
    ) -> RunTerminalizationProgress:
        observed = self._observed_terminal_progress(
            await self._lock_run(conn),
            requested_status="cancelled",
        )
        if observed is not None:
            return observed
        attempt = await self._current_attempt(conn)
        if attempt is not None:
            attempt = await self.ports.request_attempt_cancel(
                conn,
                tenant_id=self.tenant_id,
                run_id=self.run_id,
                attempt_id=str(attempt["id"]),
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
        return progress

    async def drain(
        self,
        *,
        capabilities: Any,
        transaction_factory: Callable[[], Any],
        error_code: str | None = None,
        max_batches: int = 4,
    ) -> RunTerminalizationProgress | None:
        return await self.ports.drain_terminalization(
            tenant_id=self.tenant_id,
            run_id=self.run_id,
            capabilities=capabilities,
            transaction_factory=transaction_factory,
            max_batches=max_batches,
            attempt_id=self.attempt_id,
            attempt_error_code=error_code,
        )


def bind_worker_attempt_lifecycle(
    payload: WorkerQueuePayload,
    *,
    leased_attempt_id: str,
    worker_id: str | None,
    reconciliation: WorkerExecutorReconciliation | None,
    ports: WorkerAttemptLifecyclePorts,
    queue_lease: WorkerQueueLease | None = None,
) -> WorkerAttemptLifecycle:
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
    )


async def worker_child_terminal_progress(
    conn: Any,
    *,
    payload: WorkerQueuePayload,
    child_status: str,
    result_json: dict[str, Any] | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
    is_multi_agent_child: bool | None = None,
) -> RunTerminalizationProgress | None:
    """Carry one committed child transition to the post-commit lifecycle seam."""

    del conn, result_json, error_code, error_message
    child_dispatch = isinstance(payload.input.get("multi_agent_dispatch"), dict)
    if child_status not in {"succeeded", "failed", "cancelled"} or not (
        child_dispatch if is_multi_agent_child is None else is_multi_agent_child
    ):
        return None
    return RunTerminalizationProgress(
        completed=True,
        status=child_status,
        did_transition=True,
        needs_reconcile=True,
    )


async def finalize_worker_child_parent(
    transaction_factory: Callable[[], Any],
    payload: WorkerQueuePayload,
    reconciled: Any | None,
    *,
    reconcile_terminalized_run: AsyncPort,
) -> Any | None:
    """Invoke the shared post-commit parent finalizer for one child transition."""

    if not isinstance(reconciled, RunTerminalizationProgress):
        return None
    return await reconcile_terminalized_run(
        tenant_id=payload.tenant_id,
        run_id=payload.run_id,
        progress=reconciled,
        transaction_factory=transaction_factory,
    )


async def fail_run_and_reconcile_worker_child(
    conn: Any,
    *,
    payload: WorkerQueuePayload,
    tenant_id: str,
    run_id: str,
    error_code: str,
    error_message: str,
    capabilities: Any,
    fail_run: AsyncPort,
    reconcile_child: AsyncPort,
    attempt_lifecycle: WorkerAttemptLifecycle | None = None,
    result_json: dict[str, Any] | None = None,
    is_multi_agent_child: bool | None = None,
) -> tuple[bool, Any | None]:
    """Fail one Run and project a child terminal fact when this is a child."""

    if attempt_lifecycle is not None:
        terminal_written = await attempt_lifecycle.fail(
            conn,
            capabilities=capabilities,
            error_code=error_code,
            error_message=error_message,
            result_json=result_json,
        )
    else:
        terminal_written = await fail_run(
            conn,
            capabilities=capabilities,
            tenant_id=tenant_id,
            run_id=run_id,
            error_code=error_code,
            error_message=error_message,
            result_json=result_json,
        )
    if not terminal_written:
        return False, None
    if tenant_id == payload.tenant_id and run_id == payload.run_id:
        return True, await reconcile_child(
            conn,
            payload=payload,
            child_status="failed",
            result_json=result_json,
            error_code=error_code,
            error_message=error_message,
            is_multi_agent_child=is_multi_agent_child,
        )
    return True, None
