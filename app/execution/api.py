from app.artifacts.api import (
    build_artifact_records as build_artifact_records,
    promote_artifact_reservations as promote_artifact_reservations,
)
from app.skills.api import (
    PinnedSkillMismatch as PinnedSkillMismatch,
    validate_pinned_skill_relative_path as validate_pinned_skill_relative_path,
)
from app.execution.application.worker_skill_evidence import (
    native_used_skills_from_result as native_used_skills_from_result,
    skill_manifests_for_persistence as skill_manifests_for_persistence,
    skill_snapshot_from_result as skill_snapshot_from_result,
)
from app.execution.application.worker_terminal_projection import (
    WorkerExecutorResult as WorkerExecutorResult,
    WorkerTerminalProjection as WorkerTerminalProjection,
    enforce_required_artifact_types as enforce_required_artifact_types,
    enforce_worker_required_tool_completion as enforce_worker_required_tool_completion,
    project_worker_terminal_result as project_worker_terminal_result,
    worker_assistant_metadata as worker_assistant_metadata,
)
from app.execution.domain.worker_observability import (
    event_observability_kwargs as event_observability_kwargs,
    executor_observability as executor_observability,
)
from app.execution.application.artifact_persistence import (
    build_artifact_execution_owner,
)
from app.execution.application.adapter_run import (
    WorkerRunCancelled,
    submit_run_until_cancelled,
    time,
)
from app.execution.application.pinned_skill_materialization import (
    select_execution_skill_names as select_execution_skill_names,
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
    reconciliation_agent_profile_binding_matches,
    restored_executor_reconciliation_queue_payload,
    restored_sandbox_run_payload,
    sandbox_reconciliation_payload,
    with_locked_run_model_snapshot,
)
from app.execution.application.worker_attempt_lifecycle import (
    WorkerBoundRunPayload,
    WorkerAttemptLifecycle,
    WorkerAttemptLifecyclePorts,
    WorkerExecutorReconciliation,
    WorkerQueueLease,
    bind_worker_attempt_lifecycle,
    fail_run_for_worker,
)
from app.execution.application.worker_failure_diagnostics import (
    executor_exception_failure,
    normalized_runtime_diagnostics_payload,
    normalize_sandbox_reported_failure,
    sandbox_failure_prefers_cancelled,
    predispatch_failure_result,
    public_executor_failure_message,
)
from app.execution.application.worker_runtime_sandbox_lease import (
    WorkerRuntimeSandboxLease as WorkerRuntimeSandboxLease,
    create_worker_runtime_sandbox_lease as create_worker_runtime_sandbox_lease,
    release_worker_runtime_sandbox_lease as release_worker_runtime_sandbox_lease,
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
    assistant_artifact_metadata,
    materialize_worker_answer,
    sanitize_assistant_message,
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
    sdk_failure_result_fields,
)

from typing import Any

from app.execution.application.model_control_plane import configured_model_control_plane
from app.execution.application.provider_sessions import claude_provider_session_dispatch
from app.execution.application.run_interaction import RunInteractionProtocol
from app.execution.application.model_selection import (
    RunModelSelection as RunModelSelection,
)
from app.execution.application.model_selection import (
    bind_selected_run_model as bind_selected_run_model,
    parse_requested_model_selection as parse_requested_model_selection,
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
    "WorkerExecutorResult",
    "WorkerBoundRunPayload",
    "ClaudeAgentEventCandidate",
    "ClaudeSdkAgentEventAdapter",
    "AnswerPersistenceLimits",
    "WorkerAnswerMaterialization",
    "assistant_artifact_metadata",
    "materialize_worker_answer",
    "sanitize_assistant_message",
    "select_execution_skill_names",
    "reconciliation_agent_profile_binding_matches",
    "runtime_terminal_payload",
    "RunModelSelection",
    "RunInteractionProtocol",
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
    "fail_run_for_worker",
    "executor_exception_failure",
    "executor_observability",
    "event_observability_kwargs",
    "native_used_skills_from_result",
    "skill_manifests_for_persistence",
    "skill_snapshot_from_result",
    "WorkerTerminalProjection",
    "enforce_required_artifact_types",
    "enforce_worker_required_tool_completion",
    "project_worker_terminal_result",
    "worker_assistant_metadata",
    "normalized_runtime_diagnostics_payload",
    "normalize_sandbox_reported_failure",
    "sandbox_failure_prefers_cancelled",
    "public_executor_failure_message",
    "restored_executor_reconciliation_queue_payload",
    "restored_sandbox_run_payload",
    "sandbox_reconciliation_payload",
    "sdk_failure_result_fields",
    "stage_stale_run_reconciliation",
    "submit_run_until_cancelled",
    "list_public_models",
    "bind_selected_run_model",
    "locked_run_payload_candidate",
    "parse_requested_model_selection",
    "promote_artifact_reservations",
    "predispatch_failure_result",
    "public_answer_failure_reason",
    "resolve_chat_model_selection",
    "time",
    "validated_context_file_diagnostic",
    "with_locked_run_model_snapshot",
    "WorkerAttemptLifecycle",
    "WorkerAttemptLifecyclePorts",
    "WorkerExecutorReconciliation",
    "WorkerQueueLease",
    "WorkerRuntimeSandboxLease",
    "create_worker_runtime_sandbox_lease",
    "release_worker_runtime_sandbox_lease",
]
