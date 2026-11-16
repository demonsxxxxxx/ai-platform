"""Compose the shared admission and binding graph, without queue consumption.

Callers must initialize worker runtime services in this process before entry;
this module never relies on a queue slot having run, nor configures global ports.
"""

from dataclasses import dataclass
from typing import Any

from app.bootstrap.worker_attempt_lifecycle import build_worker_attempt_lifecycle_ports
from app.bootstrap.worker_dispatch_admission import (
    build_worker_dispatch_admission_service, resolve_worker_dispatch_principal,
)
from app.bootstrap.worker_dispatch_binding import build_worker_dispatch_binding_service
from app.control_plane_contracts import standard_trace_id
from app.execution.api import bind_worker_attempt_lifecycle
from app.executors.registry import AdapterRegistry
from app.models import QueueRunPayload
from app.runs.api import WorkerOutcome, WorkerCapabilityAuthorization
from app.streaming.api import admit_v4_stream
from app.worker_principal_authority import _payload_identity


@dataclass(frozen=True)
class _WorkerTerminalAfterTransaction:
    outcome: WorkerOutcome
    payload: QueueRunPayload


@dataclass(frozen=True)
class PreparedWorkerRun:
    payload: Any
    run_payload: Any
    locked_run: dict[str, Any]
    run_identity: dict[str, str]
    trace_id: str
    attempt_authority: Any
    authorization: WorkerCapabilityAuthorization
    adapter: Any
    runtime_sandbox_lease: Any


async def prepare_worker_run(
    payload: QueueRunPayload, *, leased_attempt_id: str,
    registry: AdapterRegistry | None, worker_id: str | None,
    reconciliation: Any, queue_lease: Any, transaction_factory: Any,
    v4_capabilities: Any, run_attempt_lifecycle: Any, run_lifecycle: Any,
    publish_run_event: Any, outer_terminal_transaction: bool = False,
) -> PreparedWorkerRun | WorkerOutcome:
    if v4_capabilities is None:
        raise RuntimeError("worker_v4_capabilities_unavailable")
    attempt_lifecycle = bind_worker_attempt_lifecycle(
        payload,
        leased_attempt_id=leased_attempt_id,
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
        return PreparedWorkerRun(
            payload, run_payload, locked, run_identity, trace_id,
            attempt_lifecycle, capability_authorization, adapter,
            binding.runtime_sandbox_lease,
        )
    finally:
        if terminal_after_transaction is not None and not outer_terminal_transaction:
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
