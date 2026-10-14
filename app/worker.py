"""Leased queue ingress and inline executor submission."""

from typing import Any

from pydantic import ValidationError

from app.bootstrap.worker_dispatch_binding import release_worker_runtime_lease
from app.bootstrap import worker_execution as worker_execution_bootstrap
from app.bootstrap.worker_run_preparation import prepare_worker_run
from app.bootstrap.worker_terminal_completion import build_worker_terminal_completion_service
from app.db import transaction
from app.execution.api import WorkerQueueLease, WorkerRunCancelled, time
from app.executors.base import ExecutorDispatchAccepted
from app.executors.registry import AdapterRegistry
from app.models import QueueRunPayload
from app.queue import QUEUE_ATTEMPT_ID_FIELD
from app.runs.api import (
    RunAttemptLifecycleService, RunLifecycleService, WorkerOutcome as WorkerOutcome,
    InvalidLeasedQueueEnvelope, LeasedQueueEnvelope,
    parse_leased_queue_envelope as _parse_leased_queue_envelope,
)
from app.streaming.api import (
    WorkerV4Capabilities, admit_v4_stream, persist_worker_event, publish_run_event,
)
from app.validation import assert_safe_id


class WorkerDirectAssistantDeltaError(RuntimeError):
    """Reject an unsupported second ingress for public assistant text."""


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
    prepared = await prepare_worker_run(
        payload, leased_attempt_id=envelope.attempt_id, registry=registry,
        worker_id=worker_id, reconciliation=None, queue_lease=queue_lease,
        transaction_factory=transaction_factory, v4_capabilities=v4_capabilities,
        run_attempt_lifecycle=run_attempt_lifecycle, run_lifecycle=run_lifecycle,
        publish_run_event=publish_run_event,
    )
    if isinstance(prepared, WorkerOutcome):
        return prepared
    payload = prepared.payload
    run_payload = prepared.run_payload
    attempt_lifecycle = prepared.attempt_authority
    runtime_sandbox_lease = prepared.runtime_sandbox_lease
    runtime_sandbox_execution_detached = False
    completion = None

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
        if runtime_sandbox_lease is None or (completion is not None and completion.runtime_lease_released):
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
        if runtime_sandbox_lease is None or (completion is not None and completion.runtime_lease_released):
            return
        try:
            async with transaction_factory() as conn:
                await release_runtime_sandbox_lease(conn, reason="run_terminal_interrupted")
        except Exception:  # noqa: BLE001 - interruption cleanup is best effort.
            return

    try:
        completion = build_worker_terminal_completion_service(
            prepared, transaction_factory=transaction_factory, capabilities=v4_capabilities,
            reconciliation=None, release_runtime_lease=release_runtime_sandbox_lease,
            publish_run_event=publish_run_event,
        )
        try:
            if prepared.adapter is None:
                raise RuntimeError("executor_adapter_not_resolved")
            await admit_v4_stream(
                v4_capabilities, tenant_id=run_payload.tenant_id,
                run_id=run_payload.run_id, attempt_id=run_payload.attempt_id,
            )

            async def cancel_requested() -> bool:
                async with transaction_factory() as conn:
                    return await attempt_lifecycle.is_cancel_requested(conn)

            execution_owner = worker_execution_bootstrap.build_worker_execution_owner(
                run_payload, transaction_factory,
            )
            started_at = time.monotonic()
            result = await worker_execution_bootstrap.submit_worker_run_until_cancelled(
                prepared.adapter, run_payload, event_sink=event_sink,
                cancel_requested=cancel_requested, execution_owner=execution_owner,
            )
            if isinstance(result, ExecutorDispatchAccepted):
                if not result.lease_id:
                    raise ValueError("executor_dispatch_acceptance_lease_missing")
                runtime_sandbox_execution_detached = True
                return WorkerOutcome(status="running", run_id=run_payload.run_id)
            latency_ms = max(int((time.monotonic() - started_at) * 1000), 0)
        except WorkerRunCancelled:
            return await completion.cancel()
        except Exception as exc:  # noqa: BLE001 - worker executor boundary.
            return await completion.fail(exc)
        return await completion.complete(result, latency_ms=latency_ms)
    finally:
        await cleanup_runtime_sandbox_lease_after_interruption()
