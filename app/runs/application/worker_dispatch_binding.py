"""Runs-owned dispatch fence, context binding, and Attempt start transaction."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, AsyncContextManager

from app.runs.application.worker_queue_envelope import WorkerDispatchPayload

if TYPE_CHECKING:
    from app.execution.api import WorkerBoundRunPayload, WorkerRuntimeSandboxLease

from app.runs.application.worker_dispatch_admission import WorkerAdmissionOutcome


@dataclass(frozen=True)
class WorkerDispatchContextPorts:
    materialize: Callable[..., Awaitable[tuple[Any, str | None]]]
    project: Callable[..., dict[str, Any]]
    execution_pack: Callable[..., dict[str, Any]]


@dataclass(frozen=True)
class WorkerDispatchExecutionPorts:
    project_spec: Callable[..., Any]
    attach_mcp: Callable[..., Awaitable[Any]]
    mcp_runtime_error: type[ValueError]
    append_user_event: Callable[..., Awaitable[Any]]
    runtime_evidence: Callable[..., dict[str, Any]]
    uses_runtime_sandbox: Callable[..., bool]
    create_runtime_lease: Callable[..., Awaitable[Any]]
    missing_attempt_error: type[Exception]


@dataclass(frozen=True)
class WorkerDispatchBindingResult:
    payload: WorkerDispatchPayload
    run_payload: WorkerBoundRunPayload | None = None
    runtime_sandbox_lease: WorkerRuntimeSandboxLease | None = None
    outcome: WorkerAdmissionOutcome | None = None
    publish_after_commit: bool = False


class WorkerDispatchBindingService:
    def __init__(
        self, *,
        transaction_factory: Callable[[], AsyncContextManager[Any]],
        dispatch_fence: Callable[..., Awaitable[str]],
        compile_spec: Callable[..., Any],
        context: WorkerDispatchContextPorts,
        execution: WorkerDispatchExecutionPorts,
        fail_pre_dispatch: Callable[..., Awaitable[Any]],
    ) -> None:
        self._transaction_factory = transaction_factory
        self._dispatch_fence = dispatch_fence
        self._compile_spec = compile_spec
        self._context = context
        self._execution = execution
        self._fail_pre_dispatch = fail_pre_dispatch

    async def bind(
        self, *, payload: Any, run_identity: dict[str, str], locked_run: dict[str, Any],
        trace_id: str, attempt_id: str, attempt_authority: Any,
        capabilities: Any, principal: Any, worker_id: str | None,
        reconciliation: bool,
    ) -> WorkerDispatchBindingResult:
        async with self._transaction_factory() as conn:
            fence = await self._dispatch_fence(
                conn, run_identity=run_identity, locked_run=locked_run,
                context_snapshot_id=str(payload.context_snapshot_id or ""),
                reconciliation=reconciliation,
            )
            if fence == "stale":
                return WorkerDispatchBindingResult(
                    payload, outcome=WorkerAdmissionOutcome("skipped", "stale_terminal_state")
                )
            context_ref, context_error_code = (
                await self._context.materialize(
                    conn, payload=payload, run_identity=run_identity,
                    context_projector=self._context.project,
                ) if fence == "ready" else (None, None)
            )

            async def fail(error_code: str, error_message: str, stage: str, event_payload: dict[str, Any]) -> WorkerDispatchBindingResult:
                terminal = await self._fail_pre_dispatch(
                    conn, payload=payload, run_identity=run_identity,
                    v4_capabilities=capabilities, attempt_lifecycle=attempt_authority,
                    error_code=error_code, error_message=error_message,
                    event_stage=stage, event_payload=event_payload,
                )
                return WorkerDispatchBindingResult(
                    payload=terminal.payload,
                    outcome=WorkerAdmissionOutcome(
                        terminal.outcome.status, terminal.outcome.error_code,
                        terminal.outcome.error_message,
                    ),
                    publish_after_commit=True,
                )

            if context_ref is None:
                code = "worker_dispatch_fence_invalid" if fence == "invalid" else context_error_code or "context_snapshot_unavailable"
                message = (
                    "Run dispatch authority changed" if fence == "invalid"
                    else "A new conversation is required because native provider context is unavailable"
                    if context_error_code else "Run context snapshot is unavailable"
                )
                return await fail(code, message, "context", {"visible_to_user": False, "error_code": code})
            payload = payload.model_copy(update={"file_ids": context_ref["file_ids"]})
            try:
                execution_spec = self._compile_spec(
                    run_identity=run_identity, queue_payload=payload, trace_id=trace_id,
                    context_snapshot_id=str(context_ref["context_snapshot_id"]),
                    context_snapshot=context_ref["context_snapshot"],
                    context_pack={
                        **self._context.execution_pack(context_ref["context_snapshot"]),
                        "conversation_context": context_ref["conversation_context"],
                    },
                    run_model_snapshot=locked_run,
                )
                run_payload = self._execution.project_spec(execution_spec, attempt_id=attempt_id)
                run_payload = await self._execution.attach_mcp(
                    conn, principal=principal, run_payload=run_payload,
                )
            except ValueError as exc:
                mcp_error = exc if isinstance(exc, self._execution.mcp_runtime_error) else None
                code = mcp_error.code if mcp_error else "execution_spec_invalid"
                return await fail(
                    code,
                    "MCP runtime configuration is unavailable" if mcp_error else "Execution specification is invalid",
                    "authorization" if mcp_error else "worker",
                    {"visible_to_user": bool(mcp_error), "severity": "error", "error_code": code},
                )
            bound_attempt = await attempt_authority.bind_execution_spec(conn, execution_spec)
            if not reconciliation:
                if bound_attempt is None:
                    raise self._execution.missing_attempt_error("run_attempt_binding_missing")
                run_payload = replace(run_payload, owner_generation=int(bound_attempt["owner_generation"]))
            await self._execution.append_user_event(
                conn, tenant_id=run_identity["tenant_id"], run_id=run_identity["run_id"],
                event_type="worker_started", stage="worker", message="Run started",
                payload=self._execution.runtime_evidence(
                    worker_id=worker_id, executor_type=payload.executor_type,
                ),
            )
            lease = None
            if not reconciliation and not self._execution.uses_runtime_sandbox(
                payload, context_snapshot=context_ref["context_snapshot"],
            ):
                lease = await self._execution.create_runtime_lease(
                    conn, executor_type=payload.executor_type,
                    run_identity=run_identity, trace_id=trace_id,
                    attempt_id=attempt_id, owner_generation=run_payload.owner_generation,
                    worker_id=worker_id,
                )
            return WorkerDispatchBindingResult(payload, run_payload, lease)
