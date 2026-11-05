"""Runs-owned atomic commit of one Worker terminal result."""

from __future__ import annotations

from app.runs.domain.cost_observability import private_cost_observation
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, AsyncContextManager, Protocol

from app.runs.application.worker_queue_envelope import WorkerDispatchPayload

if TYPE_CHECKING:
    from app.execution.api import WorkerExecutorResult


AsyncPort = Callable[..., Awaitable[Any]]


class WorkerAttemptCommitAuthority(Protocol):
    async def is_cancel_requested(self, conn: Any) -> bool: ...
    async def complete(self, conn: Any, *, capabilities: Any, result_json: dict[str, Any]) -> bool: ...
    async def fail(
        self, conn: Any, *, capabilities: Any, error_code: str,
        error_message: str, result_json: dict[str, Any] | None = None,
    ) -> bool: ...
    async def cancel(
        self, conn: Any, *, capabilities: Any, result_json: dict[str, Any] | None = None,
    ) -> bool: ...
    async def classify_success_commit_block(self, conn: Any) -> str: ...


@dataclass(frozen=True)
class WorkerResultCommitCommand:
    payload: WorkerDispatchPayload
    result: WorkerExecutorResult
    result_payload: dict[str, Any]
    artifact_records: list[dict[str, Any]]
    skill_snapshot: dict[str, list[str]]
    terminal_event_kwargs: dict[str, Any]
    attempt_id: str
    trace_id: str
    reconciliation_lease_id: str | None = None
    reconciliation_claim_token: str | None = None


@dataclass(frozen=True)
class WorkerResultCommitOutcome:
    status: str
    error_code: str | None = None
    error_message: str | None = None
    publish_run_event: bool = True


class _WorkerResultCommitBlocked(Exception):
    """Rollback every result fact when the terminal CAS loses authority."""


_STALE = WorkerResultCommitOutcome(
    "skipped", "stale_terminal_state", "Run already reached a terminal state"
)


class WorkerResultCommitService:
    def __init__(
        self,
        *,
        transaction_factory: Callable[[], AsyncContextManager[Any]],
        lock_run: AsyncPort,
        reconciliation_claim_current: AsyncPort,
        materialize_answer: AsyncPort,
        persist_artifacts: AsyncPort,
        persist_skill_snapshots: AsyncPort,
        persist_assistant: AsyncPort,
        append_user_event: AsyncPort,
        append_hidden_event: AsyncPort,
        persist_failure_event: AsyncPort,
        public_failure_message: Callable[[Any], str],
        prefers_cancelled: Callable[[Any], bool],
        settle_continuation: AsyncPort | None = None,
        release_continuation_lease: AsyncPort | None = None,
        verification_scope: Callable[..., Any] | None = None,
    ) -> None:
        self._transaction_factory = transaction_factory
        self._lock_run = lock_run
        self._reconciliation_claim_current = reconciliation_claim_current
        self._materialize_answer = materialize_answer
        self._persist_artifacts = persist_artifacts
        self._persist_skill_snapshots = persist_skill_snapshots
        self._persist_assistant = persist_assistant
        self._append_user_event = append_user_event
        self._append_hidden_event = append_hidden_event
        self._persist_failure_event = persist_failure_event
        self._public_failure_message = public_failure_message
        self._prefers_cancelled = prefers_cancelled
        self._settle_continuation = settle_continuation
        self._release_continuation_lease = release_continuation_lease
        self._verification_scope = verification_scope

    async def _settle_provider(
        self, conn: Any, command: WorkerResultCommitCommand, *, status: str,
    ) -> None:
        settlement = command.result.executor_payload.get("continuation_settlement")
        if settlement is None:
            return
        if self._settle_continuation is None:
            raise RuntimeError("continuation_settlement_ports_unconfigured")
        if not isinstance(settlement, dict):
            raise ValueError("continuation_settlement_invalid")
        if "runtime_lease" in settlement and self._release_continuation_lease is None:
            raise RuntimeError("continuation_lease_release_unconfigured")
        await self._settle_continuation(
            conn, tenant_id=command.payload.tenant_id, run_id=command.payload.run_id,
            attempt_id=command.attempt_id,
            settlement={**settlement, "sdk_settlement": {
                **settlement.get("sdk_settlement", {}), "status": status,
            }},
        )

    async def _release_leases(
        self, conn: Any, command: WorkerResultCommitCommand, *, reason: str,
        release_runtime_lease: AsyncPort,
    ) -> None:
        settlement = command.result.executor_payload.get("continuation_settlement")
        if settlement is not None and self._release_continuation_lease is not None:
            await self._release_continuation_lease(conn, settlement=settlement, reason=reason)
        await release_runtime_lease(conn, reason=reason)

    async def commit(self, command: WorkerResultCommitCommand, *, attempt_authority: WorkerAttemptCommitAuthority,
                     capabilities: Any, release_runtime_lease: AsyncPort) -> WorkerResultCommitOutcome:
        if self._verification_scope is not None and command.result.executor_payload.get("continuation_settlement") is not None:
            async with self._verification_scope(tenant_id=command.payload.tenant_id, run_id=command.payload.run_id):
                return await self._commit(command, attempt_authority=attempt_authority,
                                          capabilities=capabilities, release_runtime_lease=release_runtime_lease)
        return await self._commit(command, attempt_authority=attempt_authority,
                                  capabilities=capabilities, release_runtime_lease=release_runtime_lease)

    async def _commit(
        self,
        command: WorkerResultCommitCommand,
        *,
        attempt_authority: WorkerAttemptCommitAuthority,
        capabilities: Any,
        release_runtime_lease: AsyncPort,
    ) -> WorkerResultCommitOutcome:
        payload = command.payload
        result = command.result
        result_payload = command.result_payload
        artifact_records = command.artifact_records
        assistant_message_for_persistence: str | None = None
        assistant_message_metadata: dict[str, Any] = {}
        try:
            async with self._transaction_factory() as conn:
                locked_run = await self._lock_run(
                    conn, tenant_id=payload.tenant_id, run_id=payload.run_id, for_update=True
                )
                if locked_run is None or str(locked_run.get("status") or "") in {
                    "succeeded", "failed", "cancelled"
                }:
                    raise _WorkerResultCommitBlocked()
                if command.reconciliation_lease_id is not None and not await self._reconciliation_claim_current(
                    conn,
                    lease_id=command.reconciliation_lease_id,
                    claim_token=command.reconciliation_claim_token,
                ):
                    raise RuntimeError("executor_reconciliation_claim_lost")
                # The Run is locked: include every success-only public field before
                # answer materialization chooses between inline text and its receipt.
                cancel_requested = await attempt_authority.is_cancel_requested(conn)
                if result.status == "succeeded" and cancel_requested:
                    result_payload = {**result_payload, "cancel_status": "cancel_requested_but_completed"}
                answer_receipt = result.executor_payload.get("answer_receipt")
                if result.status == "succeeded" and answer_receipt is not None:
                    materialized = await self._materialize_answer(
                        capabilities, conn, result=result, result_payload=result_payload,
                        artifact_records=artifact_records, tenant_id=payload.tenant_id,
                        run_id=payload.run_id, attempt_id=command.attempt_id,
                        answer_receipt=answer_receipt,
                    )
                    result = materialized.result
                    result_payload = materialized.result_payload
                    artifact_records = materialized.artifact_records
                    assistant_message_for_persistence = materialized.assistant_message_for_persistence
                    assistant_message_metadata = materialized.assistant_message_metadata
                    if result.status != "succeeded" and cancel_requested:
                        # Receipt rejection is a failure, not a completed cancellation.
                        result_payload = {key: value for key, value in result_payload.items() if key != "cancel_status"}
                await self._persist_artifacts(
                    conn, artifact_records, payload, trace_id=command.trace_id
                )
                await self._persist_skill_snapshots(conn, result, payload)
                if result.status == "succeeded":
                    await self._persist_assistant(
                        conn, result, payload, artifact_records, command.skill_snapshot,
                        assistant_message_for_persistence, assistant_message_metadata,
                        command.attempt_id, result_payload,
                    )
                    # The empty assistant row remains a provider-coverage
                    # anchor; only an actual public response is ready to read.
                    if str(assistant_message_for_persistence or result_payload.get("message") or "").strip() or artifact_records:
                        await self._append_user_event(
                            conn, tenant_id=payload.tenant_id, run_id=payload.run_id,
                            event_type="assistant_message_created", stage="message",
                            message="Assistant response is ready",
                            payload={
                                "artifact_count": len(result.artifacts),
                                "skills": command.skill_snapshot,
                            },
                        )
                    if cancel_requested:
                        await self._append_user_event(
                            conn, tenant_id=payload.tenant_id, run_id=payload.run_id,
                            event_type="cancel_requested_but_completed", stage="control",
                            message="取消请求已记录，但任务已完成",
                            payload={"severity": "warning"},
                        )
                    await self._settle_provider(conn, command, status="succeeded")
                    terminal_written = await attempt_authority.complete(
                        conn, capabilities=capabilities, result_json=result_payload
                    )
                    if not terminal_written:
                        raise _WorkerResultCommitBlocked()
                    await self._append_user_event(
                        conn, tenant_id=payload.tenant_id, run_id=payload.run_id,
                        event_type="run_succeeded", stage="worker", message="Run succeeded",
                        payload={
                            "artifact_count": len(result.artifacts),
                            "skills": command.skill_snapshot,
                            **({"cost": result_payload["cost"]} if "cost" in result_payload else {}),
                            **private_cost_observation(result_payload),
                        },
                        **command.terminal_event_kwargs,
                    )
                    await self._append_hidden_event(
                        conn, tenant_id=payload.tenant_id, run_id=payload.run_id,
                        event_type="status", stage="worker", message="Run succeeded",
                        payload={"artifact_count": len(result.artifacts), "visible_to_user": False},
                    )
                    await self._release_leases(conn, command, reason="run_succeeded", release_runtime_lease=release_runtime_lease)
                    return WorkerResultCommitOutcome("succeeded")

                error_code = str(result.result.get("error_code") or "executor_reported_failure")
                error_message = self._public_failure_message(result)
                settlement = result.executor_payload.get("continuation_settlement")
                stopped_failed_cancel = (
                    result.status == "failed"
                    and result.executor_payload.get("runtime_terminal_status") == "failed"
                    and command.reconciliation_lease_id is not None
                    and command.reconciliation_claim_token is not None
                    and locked_run.get("terminalization_target") == "cancel_requested"
                    and isinstance(settlement, dict)
                    and settlement.get("stop_evidence") in {"provider_stopped", "provider_not_found"}
                    and isinstance(settlement.get("workspace_receipt"), dict)
                    and settlement["workspace_receipt"].get("state") == "saved"
                )
                if cancel_requested and (self._prefers_cancelled(result) or stopped_failed_cancel):
                    await self._settle_provider(conn, command, status="cancelled")
                    cancelled_result = {
                        "message": "任务已取消",
                        **({key: result_payload[key] for key in ("latency_ms", "token_counts", "cost") if key in result_payload}
                           if private_cost_observation(result_payload) else {}),
                        **private_cost_observation(result_payload),
                    }
                    if stopped_failed_cancel:
                        # Cancellation settles lifecycle ownership, not the executor's failure facts.
                        cancelled_result = {
                            **result_payload,
                            "runtime_private_payload": {
                                **(result_payload.get("runtime_private_payload") or {}),
                                "sdk_settlement": {key: value for key, value in settlement.get("sdk_settlement", {}).items()
                                    if key in {"error_code", "terminal_reason", "owner_generation", "input_accepted",
                                               "mirror_complete", "received_result", "ack_unknown", "external_effect_unknown"}},
                            },
                        }
                    terminal_written = await attempt_authority.cancel(
                        conn, capabilities=capabilities, result_json=cancelled_result,
                    )
                    if not terminal_written:
                        raise _WorkerResultCommitBlocked()
                    await self._release_leases(conn, command, reason="run_cancelled", release_runtime_lease=release_runtime_lease)
                    return WorkerResultCommitOutcome("cancelled")
                await self._settle_provider(conn, command, status="failed")
                terminal_written = await attempt_authority.fail(
                    conn, capabilities=capabilities, error_code=error_code,
                    error_message=error_message, result_json=result_payload,
                )
                if not terminal_written:
                    raise _WorkerResultCommitBlocked()
                await self._persist_failure_event(
                    conn, tenant_id=payload.tenant_id, run_id=payload.run_id,
                    result=result, attempt_id=command.attempt_id,
                    trace_id=command.trace_id, error_code=error_code,
                )
                await self._release_leases(conn, command, reason="run_failed", release_runtime_lease=release_runtime_lease)
                return WorkerResultCommitOutcome("failed", error_code, error_message)
        except _WorkerResultCommitBlocked:
            if result.status != "succeeded":
                return _STALE
            async with self._transaction_factory() as conn:
                blocked_reason = await attempt_authority.classify_success_commit_block(conn)
                if blocked_reason == "cancel_requested":
                    await self._settle_provider(conn, command, status="cancelled")
                    terminal_written = await attempt_authority.cancel(
                        conn, capabilities=capabilities, result_json={
                            "message": "任务已取消",
                            **({key: result_payload[key] for key in ("latency_ms", "token_counts", "cost") if key in result_payload}
                               if private_cost_observation(result_payload) else {}),
                            **private_cost_observation(result_payload),
                        }
                    )
                    if terminal_written:
                        await self._release_leases(conn, command, reason="run_cancelled", release_runtime_lease=release_runtime_lease)
                        return WorkerResultCommitOutcome("cancelled")
                return _STALE
