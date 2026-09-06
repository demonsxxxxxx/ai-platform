from app.execution.application.adapter_run import (
    WorkerRunCancelled,
    submit_run_until_cancelled,
    time,
)
from app.execution.application.skill_invocation_evidence import (
    SkillInvocationEvidenceBinder,
)
from app.execution.application.context_file_diagnostics import (
    context_file_failure_event_fields,
    context_file_failure_event_payload,
    context_file_failure_log_extra,
    validated_context_file_diagnostic,
)
from app.execution.application.executor_reconciliation import (
    locked_run_payload_candidate,
    restored_executor_reconciliation_queue_payload,
    restored_sandbox_run_payload,
    sandbox_reconciliation_payload,
    with_locked_run_model_snapshot,
)
from app.execution.application.worker_attempt_lifecycle import (
    WorkerAttemptLifecycle,
    WorkerAttemptLifecyclePorts,
    WorkerExecutorReconciliation,
    WorkerQueueLease,
    bind_worker_attempt_lifecycle,
    fail_run_and_reconcile_worker_child,
    finalize_worker_child_parent,
    worker_child_terminal_progress,
)
from app.execution.application.worker_execution_spec import (
    prepare_worker_execution_spec,
)
from app.execution.application.claude_agent_events import (
    ClaudeAgentEventCandidate,
    ClaudeSdkAgentEventAdapter,
)
from app.execution.application.stale_terminalization import (
    stage_stale_run_reconciliation,
)
from app.execution.domain.provider_sessions import sdk_session_id_for_run
from app.execution.domain.public_projection import (
    claude_sdk_failure_code,
    claude_sdk_failure_message,
    projected_public_answer_failure_reason,
    public_answer_failure_reason,
)

from typing import Any

from app.execution.application.model_control_plane import configured_model_control_plane
from app.execution.application.provider_sessions import claude_provider_session_dispatch
from app.execution.application.model_selection import (
    RunModelSelection as RunModelSelection,
)
from app.execution.application.model_selection import (
    parse_requested_model_selection as parse_requested_model_selection,
)


async def list_public_models(conn: Any) -> dict[str, object]:
    return await configured_model_control_plane().public_models(conn)


async def resolve_chat_model_selection(
    conn: Any,
    *,
    selection: dict[str, str] | None,
) -> RunModelSelection | None:
    return await configured_model_control_plane().resolve_selection(
        conn,
        selection=selection,
    )

__all__ = [
    "ClaudeAgentEventCandidate",
    "ClaudeSdkAgentEventAdapter",
    "RunModelSelection",
    "SkillInvocationEvidenceBinder",
    "WorkerRunCancelled",
    "claude_sdk_failure_code",
    "claude_sdk_failure_message",
    "claude_provider_session_dispatch",
    "context_file_failure_event_fields",
    "context_file_failure_event_payload",
    "context_file_failure_log_extra",
    "bind_worker_attempt_lifecycle",
    "fail_run_and_reconcile_worker_child",
    "finalize_worker_child_parent",
    "restored_executor_reconciliation_queue_payload",
    "restored_sandbox_run_payload",
    "sandbox_reconciliation_payload",
    "sdk_session_id_for_run",
    "stage_stale_run_reconciliation",
    "submit_run_until_cancelled",
    "list_public_models",
    "locked_run_payload_candidate",
    "parse_requested_model_selection",
    "projected_public_answer_failure_reason",
    "public_answer_failure_reason",
    "resolve_chat_model_selection",
    "time",
    "validated_context_file_diagnostic",
    "worker_child_terminal_progress",
    "prepare_worker_execution_spec",
    "with_locked_run_model_snapshot",
    "WorkerAttemptLifecycle",
    "WorkerAttemptLifecyclePorts",
    "WorkerExecutorReconciliation",
    "WorkerQueueLease",
]
