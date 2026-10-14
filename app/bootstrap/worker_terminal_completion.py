"""Bind Execution projections and the single Runs terminal completion use case."""

from typing import Any

from app.agent_apps.capability_state import exact_invoked_skills
from app.bootstrap import worker_execution as worker_execution_bootstrap
from app.bootstrap import worker_result_commit as result_commit_bootstrap
from app.bootstrap.worker_execution_terminal import build_worker_execution_terminal_service
from app.execution.api import (
    WorkerRunCancelled, executor_exception_failure,
    enforce_required_artifact_types, normalize_sandbox_reported_failure,
    project_worker_terminal_result,
)
from app.runs.api import WorkerResultCommitCommand, WorkerTerminalCompletionService


def build_worker_terminal_completion_service(
    prepared: Any, *, transaction_factory: Any, capabilities: Any,
    reconciliation: Any, release_runtime_lease: Any, publish_run_event: Any,
) -> WorkerTerminalCompletionService:
    payload = prepared.payload
    attempt_id = prepared.attempt_authority.attempt_id
    trace_id = prepared.trace_id

    def normalize_result(result):
        result.validate()
        result = normalize_sandbox_reported_failure(result)
        return worker_execution_bootstrap.enforce_worker_required_tool_completion(
            result, payload=payload, run_identity=prepared.run_identity,
            attempt_id=attempt_id,
            required_tool_decision=prepared.authorization.required_tool_decision,
        )

    def build_command(result, *, latency_ms):
        artifact_records = result_commit_bootstrap.build_worker_artifact_records(
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
        return WorkerResultCommitCommand(
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

    async def publish():
        await publish_run_event(capabilities, tenant_id=payload.tenant_id, run_id=payload.run_id)

    async def publish_result(command, terminal):
        await result_commit_bootstrap.publish_worker_result_event(
            publish_run_event, capabilities, command, terminal,
        )

    return WorkerTerminalCompletionService(
        payload=payload, attempt_id=attempt_id,
        attempt_authority=prepared.attempt_authority, capabilities=capabilities,
        release_runtime_lease=release_runtime_lease,
        result_commit=result_commit_bootstrap.build_worker_result_commit_service(transaction_factory),
        execution_terminal=build_worker_execution_terminal_service(transaction_factory, capabilities=capabilities),
        normalize_result=normalize_result, build_command=build_command,
        exception_failure=executor_exception_failure, cancelled_error=WorkerRunCancelled,
        publish=publish, publish_result=publish_result,
    )
