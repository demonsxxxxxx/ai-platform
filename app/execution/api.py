from app.execution.domain.supervision import SupervisionDomain as SupervisionDomain
from app.execution.application.recovery_operations import (
    AuthoritativeOperationStatus as AuthoritativeOperationStatus,
    RecoveryInvocationAuthority as RecoveryInvocationAuthority,
    RecoveryOperationsService as RecoveryOperationsService,
)
from app.execution.domain.recovery_operations import (
    RECOVERY_CONFIRMATION_TIMEOUT_SECONDS as RECOVERY_CONFIRMATION_TIMEOUT_SECONDS,
    RecoveryInvocation as RecoveryInvocation,
    RecoverySourceConfirmation as RecoverySourceConfirmation,
    RecoveryOperationFact as RecoveryOperationFact,
    validate_recovery_operation_facts as validate_recovery_operation_facts,
    RecoveryOperationError as RecoveryOperationError,
    RecoveryOperationScope as RecoveryOperationScope,
)
from app.execution.application.continuation_settlement import (
    ContinuationSettlementPorts as ContinuationSettlementPorts,
)
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
from app.execution.application.run_delivery_intents import (
    DeliveryAttemptResult as DeliveryAttemptResult,
    DeliveryScanResult as DeliveryScanResult,
    QueueDeliveryError as QueueDeliveryError,
    QueueDeliveryReceipt as QueueDeliveryReceipt,
    RunDeliveryIntents as RunDeliveryIntents,
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
from app.execution.application.model_selection_bridge import (
    select_chat_route_model as select_chat_route_model,
    model_selection_error_status as model_selection_error_status,
)
from app.execution.application.model_snapshot_projection import (
    group_snapshot_fields as group_snapshot_fields,
    restored_session_model_metadata as restored_session_model_metadata,
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


async def list_public_models(
    conn: Any, *, user_id: str, tenant_id: str | None = None,
    usage_scene: str = "independent", group_id: str | None = None,
) -> dict[str, object]:
    return await configured_model_control_plane().public_models(
        conn, user_id=user_id, tenant_id=tenant_id, usage_scene=usage_scene,
        group_id=group_id,
    )


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
    tenant_id: str | None = None,
    group_id: str | None = None,
    selection_mode: str | None = None,
    usage_scene: str = "independent",
) -> RunModelSelection:
    return await configured_model_control_plane().resolve_selection(
        conn,
        selection=selection,
        user_id=user_id,
        tenant_id=tenant_id,
        group_id=group_id,
        selection_mode=selection_mode,
        usage_scene=usage_scene,
    )


async def record_run_delivery_intent(
    service: RunDeliveryIntents | None,
    connection: Any,
    payload: dict[str, Any],
    *,
    source: str,
    submission_id: str | None = None,
    user_message_id: str | None = None,
) -> Any | None:
    """Persist an accepted queue message through Execution's public boundary."""

    if service is None or not service.enabled:
        return None
    return await service.record(
        connection,
        payload,
        source=source,
        submission_id=submission_id,
        user_message_id=user_message_id,
    )


async def deliver_run_delivery_intent(
    service: RunDeliveryIntents,
    *,
    tenant_id: str,
    run_id: str,
) -> DeliveryAttemptResult:
    """Deliver or read back one already accepted Run through Execution."""

    return await service.deliver_one(tenant_id=tenant_id, run_id=run_id)

__all__ = [
    "MAX_COMPOSED_EXECUTOR_SYSTEM_PROMPT_CHARS",
    "AuthoritativeOperationStatus",
    "RECOVERY_CONFIRMATION_TIMEOUT_SECONDS",
    "ContinuationSettlementPorts",
    "RecoveryInvocation",
    "RecoveryInvocationAuthority",
    "RecoveryOperationError",
    "RecoveryOperationFact",
    "RecoveryOperationScope",
    "RecoveryOperationsService",
    "RecoverySourceConfirmation",
    "validate_recovery_operation_facts",
    "DeliveryAttemptResult",
    "DeliveryScanResult",
    "QueueDeliveryError",
    "QueueDeliveryReceipt",
    "RunDeliveryIntents",
    "record_run_delivery_intent",
    "deliver_run_delivery_intent",
    "MAX_SERVER_OWNED_SYSTEM_PROMPT_CHARS",
    "assert_upstream_model_id",
    "route_office_execution_tier",
    "AUTO_COMPACT_WINDOW_DEFAULT_TOKENS",
    "AUTO_COMPACT_WINDOW_MAX_TOKENS",
    "AUTO_COMPACT_WINDOW_MIN_TOKENS",
    "legacy_input_capacity_to_auto_compact_window",
    "FollowUpSuggestionResult",
    "FollowUpSuggestionService",
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
    "RunModelSelection",
    "RunInteractionProtocol",
    "RunQuestionNeedsInput",
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
