from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError


from app.bootstrap.worker_attempt_lifecycle import (
    build_worker_attempt_lifecycle_ports,
)
from app.bootstrap.worker_dispatch_binding import (
    build_worker_dispatch_binding_service,
    release_worker_runtime_lease,
)
from app.bootstrap.worker_dispatch_admission import (
    build_worker_dispatch_admission_service,
    resolve_worker_dispatch_principal,
)
from app.bootstrap import worker_execution as worker_execution_bootstrap
from app.bootstrap.worker_execution_terminal import build_worker_execution_terminal_service
from app.bootstrap.worker_result_commit import (
    build_worker_artifact_records,
    build_worker_result_commit_service,
)
from app.agent_apps.capability_state import exact_invoked_skills
from app.control_plane_contracts import standard_trace_id
from app.db import transaction
from app.execution.api import (
    WorkerExecutorReconciliation,
    WorkerQueueLease, WorkerRunCancelled,
    bind_worker_attempt_lifecycle,
    executor_exception_failure as _executor_exception_failure,
    enforce_required_artifact_types,
    project_worker_terminal_result,
    run_elapsed_ms,
    normalize_sandbox_reported_failure,
    restored_executor_reconciliation_queue_payload as _restored_executor_reconciliation_queue_payload,
    time,
    WorkerRuntimeSandboxLease as _WorkerRuntimeSandboxLease,
)
from app.executors.base import (
    ExecutorDispatchAccepted,
    ExecutorResult,
    RunPayload,
)
from app.executors.registry import AdapterRegistry
from app.models import QueueRunPayload
from app.queue import QUEUE_ATTEMPT_ID_FIELD
from app.runs.api import (
    RunAttemptLifecycleService,
    RunLifecycleService,
    WorkerResultCommitCommand,
    WorkerCapabilityAuthorization,
    InvalidLeasedQueueEnvelope,
    LeasedQueueEnvelope,
    parse_leased_queue_envelope as _parse_leased_queue_envelope,
)
from app.streaming.api import (
    WorkerV4Capabilities,
    admit_v4_stream,
    persist_worker_event,
    publish_run_event,
)
from app.validation import assert_safe_id
from app.worker_principal_authority import _payload_identity


@dataclass(frozen=True)
class WorkerOutcome:
    status: str
    run_id: str | None
    error_code: str | None = None
    error_message: str | None = None


class WorkerDirectAssistantDeltaError(RuntimeError):
    """Reject an unsupported second ingress for public assistant text."""


@dataclass(frozen=True)
class _WorkerTerminalAfterTransaction:
    outcome: WorkerOutcome
    payload: QueueRunPayload


def parse_queue_payload(raw: dict[str, Any]) -> QueueRunPayload:
    return QueueRunPayload.model_validate(raw)


def parse_leased_queue_envelope(raw: dict[str, Any]) -> LeasedQueueEnvelope:
    return _parse_leased_queue_envelope(
        raw,
        attempt_field=QUEUE_ATTEMPT_ID_FIELD,
        validate_attempt_id=assert_safe_id,
        parse_payload=parse_queue_payload,
    )


async def process_run_payload(
    raw: dict[str, Any],
    registry: AdapterRegistry | None = None,
    *,
    worker_id: str | None = None,
    reconciliation: WorkerExecutorReconciliation | None = None,
    queue_lease: WorkerQueueLease | None = None,
    transaction_factory: Any | None = None,
    v4_capabilities: WorkerV4Capabilities,
    run_attempt_lifecycle: RunAttemptLifecycleService,
    run_lifecycle: RunLifecycleService,
) -> WorkerOutcome:
    transaction_factory = transaction_factory if transaction_factory is not None else transaction
    try:
        envelope = parse_leased_queue_envelope(raw)
    except InvalidLeasedQueueEnvelope as exc:
        return WorkerOutcome(
            status="dead_letter",
            run_id=None,
            error_code="invalid_queue_attempt",
            error_message=str(exc),
        )
    except ValidationError as exc:
        return WorkerOutcome(
            status="dead_letter",
            run_id=None,
            error_code="invalid_queue_payload",
            error_message=str(exc),
        )
    payload = envelope.payload
    if v4_capabilities is None:
        raise RuntimeError("worker_v4_capabilities_unavailable")
    attempt_lifecycle = bind_worker_attempt_lifecycle(
        payload,
        leased_attempt_id=envelope.attempt_id,
        worker_id=worker_id,
        reconciliation=reconciliation,
        ports=build_worker_attempt_lifecycle_ports(run_attempt_lifecycle, run_lifecycle),
        queue_lease=queue_lease,
    )
    attempt_id = attempt_lifecycle.attempt_id
    trace_id = standard_trace_id(payload.run_id)

    adapter_registry = registry if registry is not None else AdapterRegistry()
    adapter = None

    def resolve_executor_error(executor_type: str) -> KeyError | None:
        nonlocal adapter
        try:
            if executor_type in {"ragflow", "runtime211"}:
                raise KeyError(f"Unknown executor type: {executor_type}")
            adapter = adapter_registry.get(executor_type)
        except KeyError as exc:
            return exc
        return None

    run_identity = _payload_identity(payload)
    runtime_sandbox_lease: _WorkerRuntimeSandboxLease | None = None
    runtime_sandbox_lease_released = False
    runtime_sandbox_execution_detached = False

    terminal_after_transaction: _WorkerTerminalAfterTransaction | None = None
    capability_authorization: WorkerCapabilityAuthorization | None = None
    try:
        current_principal = await resolve_worker_dispatch_principal(
            payload, transaction_factory=transaction_factory,
        )
        admission = await build_worker_dispatch_admission_service(
            transaction_factory,
            run_attempt_lifecycle=run_attempt_lifecycle,
            capabilities=v4_capabilities,
            executor_resolution_error=resolve_executor_error,
        ).admit(
            payload=payload, run_identity=run_identity, trace_id=trace_id,
            attempt_id=attempt_id, attempt_authority=attempt_lifecycle,
            capabilities=v4_capabilities, current_principal=current_principal,
            reconciliation_attempt_id=(
                str(reconciliation.lease_row.get("attempt_id") or "")
                if reconciliation is not None else None
            ),
        )
        payload = admission.payload
        locked = admission.locked_run
        run_identity = admission.run_identity
        trace_id = admission.trace_id
        attempt_lifecycle = admission.attempt_authority
        capability_authorization = admission.authorization
        if admission.outcome is not None:
            early_outcome = WorkerOutcome(
                admission.outcome.status, payload.run_id,
                admission.outcome.error_code, admission.outcome.error_message,
            )
            if admission.publish_after_commit:
                terminal_after_transaction = _WorkerTerminalAfterTransaction(early_outcome, payload)
            return early_outcome
        if capability_authorization is None:
            raise RuntimeError("worker_capability_authorization_missing")
        binding = await build_worker_dispatch_binding_service(
            transaction_factory,
        ).bind(
            payload=payload, run_identity=run_identity, locked_run=locked,
            trace_id=trace_id, attempt_id=attempt_id,
            attempt_authority=attempt_lifecycle, capabilities=v4_capabilities,
            principal=capability_authorization.principal,
            worker_id=worker_id, reconciliation=reconciliation is not None,
        )
        payload = binding.payload
        if binding.outcome is not None:
            early_outcome = WorkerOutcome(
                binding.outcome.status, payload.run_id,
                binding.outcome.error_code, binding.outcome.error_message,
            )
            if binding.publish_after_commit:
                terminal_after_transaction = _WorkerTerminalAfterTransaction(early_outcome, payload)
            return early_outcome
        run_payload = binding.run_payload
        if run_payload is None:
            raise RuntimeError("worker_dispatch_payload_missing")
        runtime_sandbox_lease = binding.runtime_sandbox_lease
    finally:
        if terminal_after_transaction is not None:
            await admit_v4_stream(
                v4_capabilities,
                tenant_id=terminal_after_transaction.payload.tenant_id,
                run_id=terminal_after_transaction.payload.run_id,
                attempt_id=attempt_id,
            )
            await publish_run_event(
                v4_capabilities,
                tenant_id=terminal_after_transaction.payload.tenant_id,
                run_id=terminal_after_transaction.payload.run_id,
            )

    async def event_sink(
        *,
        event_type: str,
        stage: str,
        message: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        if event_type == "assistant_delta":
            raise WorkerDirectAssistantDeltaError
        if await persist_worker_event(
            v4_capabilities,
            run_payload=run_payload,
            persist_event=True,
            event_type=event_type,
            stage=stage,
            message=message,
            payload=payload,
        ):
            raise WorkerRunCancelled

    async def release_runtime_sandbox_lease(conn, *, reason: str) -> None:
        if reconciliation is not None:
            return
        if runtime_sandbox_lease is None or runtime_sandbox_lease_released:
            return
        await release_worker_runtime_lease(
            conn, runtime_sandbox_lease, reason=reason,
        )
        # This write is provisional until the owning terminal transaction exits.

    async def cleanup_runtime_sandbox_lease_after_interruption() -> None:
        # Binding returns only SDK placeholder leases; detached provider leases
        # and reconciliation claims retain their Sandbox-owned cleanup authority.
        if runtime_sandbox_execution_detached:
            return
        if runtime_sandbox_lease is None or runtime_sandbox_lease_released:
            return
        try:
            async with transaction_factory() as conn:
                await release_runtime_sandbox_lease(conn, reason="run_terminal_interrupted")
        except Exception:  # noqa: BLE001 - interruption cleanup is best effort.
            return

    try:
        try:
            if adapter is None:
                raise RuntimeError("executor_adapter_not_resolved")

            if reconciliation is not None:
                result: ExecutorResult | ExecutorDispatchAccepted = reconciliation.result
            else:
                await admit_v4_stream(
                    v4_capabilities,
                    tenant_id=run_payload.tenant_id,
                    run_id=run_payload.run_id,
                    attempt_id=run_payload.attempt_id,
                )

                async def cancel_requested() -> bool:
                    async with transaction_factory() as conn:
                        return await attempt_lifecycle.is_cancel_requested(conn)

                execution_owner = worker_execution_bootstrap.build_worker_execution_owner(
                    run_payload, transaction_factory,
                )
                started_at = time.monotonic()
                result = await worker_execution_bootstrap.submit_worker_run_until_cancelled(
                    adapter,
                    run_payload,
                    event_sink=event_sink,
                    cancel_requested=cancel_requested,
                    execution_owner=execution_owner,
                )
            if isinstance(result, ExecutorDispatchAccepted):
                if not result.lease_id:
                    raise ValueError("executor_dispatch_acceptance_lease_missing")
                runtime_sandbox_execution_detached = True
                return WorkerOutcome(status="running", run_id=run_payload.run_id)
            latency_ms = run_elapsed_ms(locked.get("started_at")) if reconciliation is not None else max(int((time.monotonic() - started_at) * 1000), 0)
            result.validate()
            result = normalize_sandbox_reported_failure(result)
            if capability_authorization is None:
                raise RuntimeError("worker_capability_authorization_missing")
            result = worker_execution_bootstrap.enforce_worker_required_tool_completion(
                result, payload=payload, run_identity=run_identity,
                attempt_id=attempt_id,
                required_tool_decision=capability_authorization.required_tool_decision,
            )
        except WorkerRunCancelled:
            cancelled = await build_worker_execution_terminal_service(
                transaction_factory, capabilities=v4_capabilities,
            ).cancel(
                attempt=attempt_lifecycle, capabilities=v4_capabilities,
                release_runtime_lease=release_runtime_sandbox_lease,
            )
            runtime_sandbox_lease_released = cancelled.status == "cancelled"
            await publish_run_event(v4_capabilities, tenant_id=payload.tenant_id, run_id=payload.run_id)
            return WorkerOutcome(cancelled.status, payload.run_id, cancelled.error_code, cancelled.error_message)
        except Exception as exc:  # noqa: BLE001 - worker boundary terminalizes all failures.
            failure_code, failure_message, failure_result = _executor_exception_failure(exc)
            terminal = await build_worker_execution_terminal_service(
                transaction_factory, capabilities=v4_capabilities,
            ).fail_or_cancel(
                payload=payload, attempt_id=attempt_id,
                attempt=attempt_lifecycle, capabilities=v4_capabilities,
                failure_code=failure_code, failure_message=failure_message,
                failure_result=failure_result,
                release_runtime_lease=release_runtime_sandbox_lease,
            )
            runtime_sandbox_lease_released = terminal.status in {"failed", "cancelled"}
            await publish_run_event(v4_capabilities, tenant_id=payload.tenant_id, run_id=payload.run_id)
            return WorkerOutcome(terminal.status, payload.run_id, terminal.error_code, terminal.error_message)

        artifact_records = build_worker_artifact_records(
            result, reconciliation=reconciliation is not None,
        )
        terminal_projection = project_worker_terminal_result(
            result, artifact_records, trace_id=trace_id, latency_ms=latency_ms,
            invoked_skill_ids=exact_invoked_skills,
        )
        result_payload = terminal_projection.result_payload
        skill_snapshot = terminal_projection.skill_snapshot
        terminal_event_kwargs = terminal_projection.terminal_event_kwargs
        result, artifact_records, result_payload = enforce_required_artifact_types(
            result, artifact_records, result_payload
        )
        result_commit = build_worker_result_commit_service(transaction_factory)
        commit_command = WorkerResultCommitCommand(
            payload=payload,
            result=result,
            result_payload=result_payload,
            artifact_records=artifact_records,
            skill_snapshot=skill_snapshot,
            terminal_event_kwargs=terminal_event_kwargs,
            attempt_id=attempt_id,
            trace_id=trace_id,
            reconciliation_lease_id=(
                str(reconciliation.lease_row["id"]) if reconciliation is not None else None
            ),
            reconciliation_claim_token=(reconciliation.claim_token if reconciliation is not None else None),
        )
        committed = await result_commit.commit(
            commit_command,
            attempt_authority=attempt_lifecycle,
            capabilities=v4_capabilities,
            release_runtime_lease=release_runtime_sandbox_lease,
        )
        runtime_sandbox_lease_released = committed.status in {"succeeded", "failed", "cancelled"}
        if committed.publish_run_event:
            await publish_run_event(v4_capabilities, tenant_id=payload.tenant_id, run_id=payload.run_id)
        return WorkerOutcome(committed.status, payload.run_id, committed.error_code, committed.error_message)
    finally:
        await cleanup_runtime_sandbox_lease_after_interruption()


async def reconcile_executor_terminal_result(
    *,
    lease_row: dict[str, Any],
    result: ExecutorResult,
    registry: AdapterRegistry | None = None,
    worker_id: str | None = None,
    claim_token: str,
    transaction_factory: Any | None = None,
    v4_capabilities: WorkerV4Capabilities,
    run_attempt_lifecycle: RunAttemptLifecycleService,
    run_lifecycle: RunLifecycleService,
) -> WorkerOutcome:
    queue_payload, attempt_id = _restored_executor_reconciliation_queue_payload(
        lease_row.get("executor_reconciliation_context_json"),
        result=result.result,
        run_payload_factory=RunPayload,
        queue_payload_factory=QueueRunPayload,
    )
    return await process_run_payload(
        {
            **queue_payload.model_dump(mode="json"),
            "_queue_attempt_id": attempt_id,
        },
        registry,
        worker_id=worker_id,
        reconciliation=WorkerExecutorReconciliation(result, lease_row, claim_token),
        transaction_factory=transaction_factory,
        v4_capabilities=v4_capabilities,
        run_attempt_lifecycle=run_attempt_lifecycle,
        run_lifecycle=run_lifecycle,
    )
