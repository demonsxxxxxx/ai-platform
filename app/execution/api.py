from app.execution.domain.token_metrics import (
    mapped_token_count as mapped_token_count,
    sum_token_counts as sum_token_counts,
    token_count as token_count,
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
    run_elapsed_ms as run_elapsed_ms,
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
    public_tool_operation_id as public_tool_operation_id,
)
from app.execution.domain.model_text_checkpoint import ModelTextCheckpoint as ModelTextCheckpoint
from app.execution.application.stale_terminalization import (
    stage_stale_run_reconciliation,
)
from app.execution.application.sandbox_terminal_projection import (
    project_sandbox_terminal_result as project_sandbox_terminal_result,
    sandbox_terminal_disposition as sandbox_terminal_disposition,
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

from app.execution.domain.model_catalog import (
    AUTO_COMPACT_WINDOW_DEFAULT_TOKENS as AUTO_COMPACT_WINDOW_DEFAULT_TOKENS,
    AUTO_COMPACT_WINDOW_MAX_TOKENS as AUTO_COMPACT_WINDOW_MAX_TOKENS,
    AUTO_COMPACT_WINDOW_MIN_TOKENS as AUTO_COMPACT_WINDOW_MIN_TOKENS,
)
from app.execution.domain.model_catalog import (
    legacy_input_capacity_to_auto_compact_window as legacy_input_capacity_to_auto_compact_window,
)

from typing import Any

from app.execution.application.follow_up_completion import (
    FollowUpSuggestionResult as FollowUpSuggestionResult,
    FollowUpSuggestionService as FollowUpSuggestionService,
    configured_follow_up_suggestions as configured_follow_up_suggestions,
)
from app.execution.application.model_control_plane import configured_model_control_plane
from app.execution.application.provider_sessions import claude_provider_session_dispatch
from app.execution.application.run_interaction import RunInteractionProtocol, RunQuestionNeedsInput
from app.execution.application.model_selection import (
    RunModelSelection as RunModelSelection,
)
from app.execution.application.model_selection import (
    bind_selected_run_model as bind_selected_run_model,
    parse_requested_model_selection as parse_requested_model_selection,
)
from app.execution.domain.office_execution_tier import (
    route_office_execution_tier as route_office_execution_tier,
)
from app.execution.domain.input_limits import (
    MAX_COMPOSED_EXECUTOR_SYSTEM_PROMPT_CHARS as MAX_COMPOSED_EXECUTOR_SYSTEM_PROMPT_CHARS,
    MAX_SERVER_OWNED_SYSTEM_PROMPT_CHARS as MAX_SERVER_OWNED_SYSTEM_PROMPT_CHARS,
    assert_upstream_model_id as assert_upstream_model_id,
)


async def list_public_models(conn: Any, *, user_id: str) -> dict[str, object]:
    return await configured_model_control_plane().public_models(conn, user_id=user_id)


async def model_access_excludes_user(
    conn: Any, *, model_id: str, user_id: str
) -> bool:
    """Return whether the current model user list excludes this user."""

    return await configured_model_control_plane().model_access_excludes_user(
        conn, model_id=model_id, user_id=user_id
    )


async def resolve_chat_model_selection(
    conn: Any,
    *,
    selection: dict[str, str] | None,
    user_id: str,
) -> RunModelSelection:
    return await configured_model_control_plane().resolve_selection(
        conn,
        selection=selection,
        user_id=user_id,
    )

__all__ = [
    "MAX_COMPOSED_EXECUTOR_SYSTEM_PROMPT_CHARS",
    "MAX_SERVER_OWNED_SYSTEM_PROMPT_CHARS",
    "assert_upstream_model_id",
    "route_office_execution_tier",
    "AUTO_COMPACT_WINDOW_DEFAULT_TOKENS",
    "AUTO_COMPACT_WINDOW_MAX_TOKENS",
    "AUTO_COMPACT_WINDOW_MIN_TOKENS",
    "legacy_input_capacity_to_auto_compact_window",
    "FollowUpSuggestionResult",
    "FollowUpSuggestionService",
    "mapped_token_count",
    "sum_token_counts",
    "token_count",
    "ModelTextCheckpoint",
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
    "sandbox_terminal_disposition",
    "project_sandbox_terminal_result",
    "RunModelSelection",
    "RunInteractionProtocol",
    "RunQuestionNeedsInput",
    "SkillInvocationEvidenceBinder",
    "WorkerRunCancelled",
    "artifact_content_type",
    "artifact_label",
    "artifact_type",
    "build_artifact_execution_owner",
    "claude_sdk_failure_code",
    "claude_sdk_failure_message",
    "claude_provider_session_dispatch",
    "context_file_failure_event_fields",
    "context_file_failure_event_payload",
    "context_file_failure_log_extra",
    "collect_workspace_artifacts",
    "bind_worker_attempt_lifecycle",
    "executor_exception_failure",
    "executor_observability",
    "run_elapsed_ms",
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
    "configured_follow_up_suggestions",
    "list_public_models",
    "model_access_excludes_user",
    "bind_selected_run_model",
    "locked_run_payload_candidate",
    "parse_requested_model_selection",
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
