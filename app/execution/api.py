from app.execution.application.artifact_persistence import (
    build_artifact_execution_owner,
    build_artifact_records,
    promote_artifact_reservations,
)
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
from app.execution.application.worker_failure_diagnostics import (
    executor_exception_failure,
    predispatch_failure_result,
)
from app.execution.application.claude_agent_events import (
    ClaudeAgentEventCandidate,
    ClaudeSdkAgentEventAdapter,
    runtime_terminal_payload,
)
from app.execution.application.stale_terminalization import (
    stage_stale_run_reconciliation,
)
from app.execution.application.worker_answer_persistence import (
    AnswerPersistenceLimits,
    WorkerAnswerMaterialization,
    append_artifact_links,
    materialize_worker_answer,
)
from app.execution.application.artifact_storage import (
    artifact_content_type,
    artifact_label,
    artifact_type,
    collect_workspace_artifacts,
)
from app.execution.domain.public_projection import (
    claude_sdk_failure_code,
    claude_sdk_failure_message,
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


async def count_checkpoint_input_for_run(*, run_id: str, source_text: str) -> int:
    return await configured_model_control_plane().count_checkpoint_input_for_run(
        run_id=run_id, source_text=source_text,
    )


async def summarize_context_for_run(*, run_id: str, source_text: str) -> dict[str, Any]:
    return await configured_model_control_plane().summarize_context_for_run(
        run_id=run_id, source_text=source_text,
    )


async def list_public_models(conn: Any) -> dict[str, object]:
    return await configured_model_control_plane().public_models(conn)


async def resolve_chat_model_selection(
    conn: Any,
    *,
    selection: dict[str, str] | None,
) -> RunModelSelection:
    return await configured_model_control_plane().resolve_selection(
        conn,
        selection=selection,
    )

__all__ = [
    "ClaudeAgentEventCandidate",
    "ClaudeSdkAgentEventAdapter",
    "AnswerPersistenceLimits",
    "WorkerAnswerMaterialization",
    "append_artifact_links",
    "materialize_worker_answer",
    "runtime_terminal_payload",
    "RunModelSelection",
    "SkillInvocationEvidenceBinder",
    "WorkerRunCancelled",
    "artifact_content_type",
    "artifact_label",
    "artifact_type",
    "build_artifact_execution_owner",
    "build_artifact_records",
    "claude_sdk_failure_code",
    "claude_sdk_failure_message",
    "claude_provider_session_dispatch",
    "context_file_failure_event_fields",
    "context_file_failure_event_payload",
    "context_file_failure_log_extra",
    "collect_workspace_artifacts",
    "bind_worker_attempt_lifecycle",
    "fail_run_and_reconcile_worker_child",
    "executor_exception_failure",
    "finalize_worker_child_parent",
    "restored_executor_reconciliation_queue_payload",
    "restored_sandbox_run_payload",
    "sandbox_reconciliation_payload",
    "stage_stale_run_reconciliation",
    "submit_run_until_cancelled",
    "list_public_models",
    "count_checkpoint_input_for_run",
    "summarize_context_for_run",
    "locked_run_payload_candidate",
    "parse_requested_model_selection",
    "promote_artifact_reservations",
    "predispatch_failure_result",
    "public_answer_failure_reason",
    "resolve_chat_model_selection",
    "time",
    "validated_context_file_diagnostic",
    "worker_child_terminal_progress",
    "with_locked_run_model_snapshot",
    "WorkerAttemptLifecycle",
    "WorkerAttemptLifecyclePorts",
    "WorkerExecutorReconciliation",
    "WorkerQueueLease",
]
