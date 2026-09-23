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
from app.execution.application.file_workflow import (
    allowed_skill_names,
    completed_step_checkpoint_payload,
    file_skill_steps,
    is_file_skill_execution_step,
    non_execution_step_output,
    pinned_skill_manifests,
    resume_checkpoint_lineage,
    resume_completed_step_checkpoints,
    resume_completed_step_outputs,
    resume_copied_from_run_id,
    string_list,
)
from app.execution.application.pinned_skill_materialization import (
    PinnedSkillMismatch as PinnedSkillMismatch,
    validate_pinned_skill_relative_path as validate_pinned_skill_relative_path,
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
from app.execution.application.worker_result_projection import (
    artifact_download_url,
    dependency_ids_from_manifest,
    event_observability_kwargs,
    executor_observability,
    int_payload_value,
    sanitize_artifact_manifest,
    skill_manifests_for_persistence,
    skill_snapshot_from_result,
    worker_runtime_evidence,
)
from app.execution.application.worker_step_projection import (
    multi_agent_result_summary,
    step_key_from_event,
)
from app.execution.application.worker_terminalization import (
    fail_locked_run_snapshot,
    fail_worker_capability_authorization,
    fail_worker_pre_dispatch_error,
)
from app.execution.application.worker_capability_projection import (
    append_worker_admin_bypass_audits,
    append_worker_capability_denial_evidence,
    append_worker_tool_policy_audits,
    authorized_skill_catalog_binding,
    canonical_authorized_mcp_scope,
    denied_capability_decision,
    mcp_capability_subject,
    mcp_tool_lifecycle_status,
    payload_with_authorized_mcp_registration,
    payload_with_authorized_skill_catalog,
    reauthorize_mcp_capabilities,
    worker_capability_context,
    worker_capability_record,
    worker_capability_audit_payload,
    worker_admin_bypass_audits,
    WorkerAdminBypassAudit,
    WorkerCapabilityAuthorization,
    WorkerCapabilityDecision,
    WorkerToolPolicyAudit,
)
from app.execution.application.worker_authorization import reauthorize_worker_capabilities
from app.execution.application.worker_runtime_sandbox_lease import (
    WorkerRuntimeSandboxLease,
    create_worker_runtime_sandbox_lease,
    release_worker_runtime_sandbox_lease,
)
from app.execution.application.worker_dispatch_identity import (
    agent_profile_snapshot_matches_authority,
    locked_agent_profile_identity_valid,
    locked_run_is_multi_agent_child,
    locked_run_principal,
    locked_run_trace_id,
)
from app.execution.application.worker_events import (
    append_user_event,
    attach_multi_agent_result_summary,
    record_run_step_from_event,
)
from app.execution.application.worker_attempt_lifecycle import (
    WorkerAttemptLifecycle,
    WorkerAttemptLifecyclePorts,
    WorkerExecutorReconciliation,
    WorkerQueueLease,
    bind_worker_attempt_lifecycle,
    fail_run_and_reconcile_worker_child,
    finalize_worker_child_parent,  # noqa: F401 - public facade
    worker_child_terminal_progress,
)
from app.execution.application.worker_failure_diagnostics import (
    executor_exception_failure,
    normalize_sandbox_reported_failure,
    normalized_runtime_diagnostics_payload,
    predispatch_failure_result,
    public_executor_failure_message,
    result_prefers_cancelled_after_failure,
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
    "ClaudeAgentEventCandidate",
    "ClaudeSdkAgentEventAdapter",
    "AnswerPersistenceLimits",
    "WorkerAnswerMaterialization",
    "assistant_artifact_metadata",
    "materialize_worker_answer",
    "sanitize_assistant_message",
    "reconciliation_agent_profile_binding_matches",
    "runtime_terminal_payload",
    "RunModelSelection",
    "SkillInvocationEvidenceBinder",
    "WorkerRunCancelled",
    "agent_profile_snapshot_matches_authority",
    "allowed_skill_names",
    "append_user_event",
    "append_worker_admin_bypass_audits",
    "append_worker_capability_denial_evidence",
    "append_worker_tool_policy_audits",
    "artifact_content_type",
    "artifact_label",
    "artifact_download_url",
    "artifact_type",
    "authorized_skill_catalog_binding",
    "build_artifact_execution_owner",
    "build_artifact_records",
    "canonical_authorized_mcp_scope",
    "claude_sdk_failure_code",
    "claude_sdk_failure_message",
    "claude_provider_session_dispatch",
    "context_file_failure_event_fields",
    "context_file_failure_event_payload",
    "context_file_failure_log_extra",
    "denied_capability_decision",
    "create_worker_runtime_sandbox_lease",
    "dependency_ids_from_manifest",
    "collect_workspace_artifacts",
    "completed_step_checkpoint_payload",
    "attach_multi_agent_result_summary",
    "bind_worker_attempt_lifecycle",
    "fail_run_and_reconcile_worker_child",
    "finalize_worker_child_parent",
    "executor_exception_failure",
    "normalize_sandbox_reported_failure",
    "event_observability_kwargs",
    "executor_observability",
    "file_skill_steps",
    "fail_locked_run_snapshot",
    "fail_worker_capability_authorization",
    "fail_worker_pre_dispatch_error",
    "normalized_runtime_diagnostics_payload",
    "non_execution_step_output",
    "public_executor_failure_message",
    "result_prefers_cancelled_after_failure",
    "pinned_skill_manifests",
    "reauthorize_mcp_capabilities",
    "reauthorize_worker_capabilities",
    "record_run_step_from_event",
    "release_worker_runtime_sandbox_lease",
    "restored_executor_reconciliation_queue_payload",
    "restored_sandbox_run_payload",
    "sandbox_reconciliation_payload",
    "sdk_failure_result_fields",
    "sanitize_artifact_manifest",
    "skill_manifests_for_persistence",
    "skill_snapshot_from_result",
    "stage_stale_run_reconciliation",
    "submit_run_until_cancelled",
    "string_list",
    "step_key_from_event",
    "list_public_models",
    "int_payload_value",
    "mcp_capability_subject",
    "mcp_tool_lifecycle_status",
    "multi_agent_result_summary",
    "is_file_skill_execution_step",
    "bind_selected_run_model",
    "locked_agent_profile_identity_valid",
    "locked_run_payload_candidate",
    "locked_run_is_multi_agent_child",
    "locked_run_principal",
    "locked_run_trace_id",
    "parse_requested_model_selection",
    "promote_artifact_reservations",
    "payload_with_authorized_mcp_registration",
    "payload_with_authorized_skill_catalog",
    "predispatch_failure_result",
    "public_answer_failure_reason",
    "resolve_chat_model_selection",
    "resume_checkpoint_lineage",
    "resume_completed_step_checkpoints",
    "resume_completed_step_outputs",
    "resume_copied_from_run_id",
    "time",
    "validated_context_file_diagnostic",
    "worker_admin_bypass_audits",
    "worker_capability_audit_payload",
    "worker_capability_context",
    "worker_capability_record",
    "worker_child_terminal_progress",
    "WorkerAdminBypassAudit",
    "WorkerCapabilityAuthorization",
    "WorkerCapabilityDecision",
    "WorkerToolPolicyAudit",
    "with_locked_run_model_snapshot",
    "worker_runtime_evidence",
    "WorkerAttemptLifecycle",
    "WorkerAttemptLifecyclePorts",
    "WorkerExecutorReconciliation",
    "WorkerQueueLease",
    "WorkerRuntimeSandboxLease",
]
