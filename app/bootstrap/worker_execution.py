"""Bind Worker execution ownership to durable Artifact cleanup."""

from functools import partial
from typing import Any

from app.execution.application.worker_dispatch import execute_worker_run as classify_worker_dispatch
from app.execution.api import (
    build_artifact_execution_owner,
    enforce_worker_required_tool_completion as project_required_tool_completion,
    submit_run_until_cancelled,
)
from app.executors.base import ExecutorDispatchAccepted, RunExecutionOwner
from app.persistence.artifacts import reserve_provisional_artifact_cleanup
from app.runs.api import WorkerOutcome
from app.sandbox.api import SandboxDispatchOutcomeUnknown
from app.required_tool_contract import RequiredCapabilityDecision, required_tool_completion_for_run


submit_worker_run_until_cancelled = partial(
    submit_run_until_cancelled, owner_factory=RunExecutionOwner,
)


async def execute_worker_run(adapter, run_payload, **kwargs):
    return await classify_worker_dispatch(
        adapter, run_payload, **kwargs,
        submit=submit_worker_run_until_cancelled,
        accepted_type=ExecutorDispatchAccepted,
        uncertain_dispatch_type=SandboxDispatchOutcomeUnknown,
        running_outcome=WorkerOutcome,
    )


def enforce_worker_required_tool_completion(
    result: Any, *, payload: Any, run_identity: dict[str, str],
    attempt_id: str, required_tool_decision: Any | None,
) -> Any:
    return project_required_tool_completion(
        result, payload=payload, run_identity=run_identity,
        attempt_id=attempt_id,
        required_tool_decision=required_tool_decision or RequiredCapabilityDecision(
            True, "required_tool_not_declared", "", "",
        ),
        check_completion=required_tool_completion_for_run,
    )


def build_worker_execution_owner(run_payload: Any, transaction_factory: Any) -> RunExecutionOwner:
    return build_artifact_execution_owner(
        run_payload, transaction_factory,
        reserve_provisional_artifact_cleanup, RunExecutionOwner,
    )
