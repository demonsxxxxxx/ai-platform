from dataclasses import dataclass, replace
from functools import partial as _partial
from typing import Any

from pydantic import ValidationError

from app import repositories
from app.bootstrap.worker_attempt_lifecycle import (
    build_worker_attempt_lifecycle_ports,
    build_worker_parent_finalizer,
)
from app.agent_apps.capability_state import (
    bind_validated_controlled_skill_evidence, exact_invoked_skills, project_agent_capability_state,
)
from app.agent_apps.api import reauthorize_bound_profile_for_worker_dispatch
from app.capabilities import required_artifact_types_for_skill
from app.bootstrap.context import materialize_queued_worker_context_snapshot
from app.context.api import context_snapshot_ref_from_row
from app.context_builder import executor_context_pack_from_snapshot
from app.control_plane_contracts import (
    RUN_EXECUTION_KIND_HARNESS_CHAT,
    artifact_lineage_contract,
    artifact_manifest_contract,
    standard_trace_id,
)
from app.db import transaction
from app.execution.api import (
    WorkerAdminBypassAudit as _WorkerAdminBypassAudit,
    WorkerAttemptLifecycle,
    WorkerCapabilityAuthorization as _WorkerCapabilityAuthorization,
    WorkerExecutorReconciliation,
    WorkerQueueLease,
    WorkerRunCancelled,
    WorkerRuntimeSandboxLease as _WorkerRuntimeSandboxLease,
    AnswerPersistenceLimits, append_user_event, agent_profile_snapshot_matches_authority as _agent_profile_snapshot_matches_authority, append_worker_admin_bypass_audits as _append_worker_admin_bypass_audits, append_worker_capability_denial_evidence as _append_worker_capability_denial_evidence, append_worker_tool_policy_audits as _append_worker_tool_policy_audits, assistant_artifact_metadata, artifact_download_url as _artifact_download_url, denied_capability_decision as _denied_capability_decision, sanitize_assistant_message,
    attach_multi_agent_result_summary as _attach_multi_agent_result_summary,
    bind_worker_attempt_lifecycle,
    build_artifact_execution_owner,
    build_artifact_records,
    dependency_ids_from_manifest as _dependency_ids_from_manifest,
    create_worker_runtime_sandbox_lease as _create_worker_runtime_sandbox_lease,
    event_observability_kwargs as _event_observability_kwargs,
    executor_observability as _executor_observability,
    fail_locked_run_snapshot as _fail_locked_run_snapshot_impl,
    fail_run_and_reconcile_worker_child as _fail_run_and_reconcile_worker_child,
    fail_worker_capability_authorization as _fail_worker_capability_authorization_impl,
    fail_worker_pre_dispatch_error as _fail_worker_pre_dispatch_error_impl,
    executor_exception_failure as _executor_exception_failure,
    locked_agent_profile_identity_valid as _locked_agent_profile_identity_valid,
    locked_run_is_multi_agent_child as _locked_run_is_multi_agent_child,
    locked_run_principal as _locked_run_principal,
    locked_run_trace_id as _locked_run_trace_id,
    locked_run_payload_candidate as _locked_run_payload_candidate,
    materialize_worker_answer,
    normalize_sandbox_reported_failure as _normalize_sandbox_reported_failure,
    promote_artifact_reservations,
    public_executor_failure_message as _public_executor_failure_message,
    record_run_step_from_event as _record_run_step_from_event,
    reauthorize_mcp_capabilities as _reauthorize_mcp_capabilities_impl,
    reauthorize_worker_capabilities as _reauthorize_worker_capabilities_impl,
    release_worker_runtime_sandbox_lease as _release_worker_runtime_sandbox_lease,
    result_prefers_cancelled_after_failure as _result_prefers_cancelled_after_failure,
    sanitize_artifact_manifest as _sanitize_artifact_manifest,
    skill_manifests_for_persistence as _project_skill_manifests_for_persistence,
    skill_snapshot_from_result as _skill_snapshot_from_result,
    predispatch_failure_result as _pre_dispatch_failure_result, reconciliation_agent_profile_binding_matches as _reconciliation_agent_profile_binding_matches,
    restored_executor_reconciliation_queue_payload as _restored_executor_reconciliation_queue_payload,
    submit_run_until_cancelled as _submit_run_until_cancelled_with_owner,
    time,
    with_locked_run_model_snapshot as _with_locked_run_model_snapshot,
    worker_capability_record as _worker_capability_record,
    worker_admin_bypass_audits as _worker_admin_bypass_audits,
    worker_child_terminal_progress as _reconcile_multi_agent_child_terminal_state,
    worker_runtime_evidence as _worker_runtime_evidence,
)
from app.execution_boundary import (
    decide_worker_execution_boundary as _worker_execution_boundary_decision,
    ordinary_worker_run_uses_runtime_sandbox as _ordinary_run_uses_runtime_sandbox,
)
from app.executors.base import (
    ExecutorDispatchAccepted,
    ExecutorResult,
    RunExecutionOwner,
    RunPayload,
    project_execution_spec_to_run_payload,
)
from app.executors.registry import AdapterRegistry
from app.models import QueueRunPayload
from app.mcp import api as mcp_api
from app.persistence_limits import MESSAGE_CONTENT_MAX_BYTES, RUN_RESULT_MAX_BYTES, json_size_bytes
from app.persistence.artifacts import promote_provisional_artifact_cleanup, reserve_provisional_artifact_cleanup
from app.platform.postgres import sandbox_leases as sandbox_lease_repository
from app.principal_authority import (
    resolve_current_principal,
)
from app.queue import (
    InvalidLeasedQueueEnvelope,
    parse_leased_queue_envelope,
)
from app.runs.api import (
    RunAttemptLifecycleService,
    compile_execution_spec_for_dispatch, worker_dispatch_fence, persist_assistant_with_provider_coverage,
    load_run_model_snapshot as _load_run_model_snapshot,
)
from app.required_tool_contract import (
    RequiredCapabilityDecision,
    builtin_capability_subjects,
    required_tool_completion_for_run,
)
from app.settings import get_settings
from app.streaming.api import (
    WorkerV4Capabilities,
    admit_v4_stream,
    finalize_parent_and_publish,
    persist_worker_event,
    publish_run_event,
)
from app.streaming.worker_projection import persist_worker_failure_event
from app.skills.execution_profiles import effective_skill_execution_profile
from app.tool_permission_lifecycle import (
    cancel_run_with_v4,
    reconcile_terminalized_permission_run,
)
from app.worker_principal_authority import (
    _identity_mismatch_fields,
    _locked_run_identity,
    _payload_identity,
    _resolve_current_principal_before_dispatch,
)


_ANSWER_PERSISTENCE_LIMITS = AnswerPersistenceLimits(MESSAGE_CONTENT_MAX_BYTES, RUN_RESULT_MAX_BYTES, json_size_bytes)


_submit_run_until_cancelled = _partial(
    _submit_run_until_cancelled_with_owner,
    owner_factory=RunExecutionOwner,
)


@dataclass(frozen=True)
class WorkerOutcome:
    status: str
    run_id: str | None
    error_code: str | None = None
    error_message: str | None = None


class WorkerDirectAssistantDeltaError(RuntimeError):
    """Reject an unsupported second ingress for public assistant text."""


class _WorkerSuccessCommitBlocked(Exception):
    """Abort success-visible writes when the final run transition loses its guard."""


@dataclass(frozen=True)
class _WorkerTerminalAfterTransaction:
    outcome: WorkerOutcome
    payload: QueueRunPayload
    reconciled_parent: Any | None


async def _fail_run_and_reconcile_with_write(
    conn,
    *,
    payload: QueueRunPayload,
    tenant_id: str,
    run_id: str,
    error_code: str,
    error_message: str,
    result_json: dict[str, Any] | None = None,
    is_multi_agent_child: bool | None = None,
    v4_capabilities: WorkerV4Capabilities,
    attempt_lifecycle: WorkerAttemptLifecycle,
) -> tuple[bool, Any | None]:
    result_json = result_json or _pre_dispatch_failure_result(
        error_code, "worker", reason=error_code
    )
    return await _fail_run_and_reconcile_worker_child(
        conn,
        payload=payload,
        tenant_id=tenant_id,
        run_id=run_id,
        error_code=error_code,
        error_message=error_message,
        capabilities=v4_capabilities,
        reconcile_child=_reconcile_multi_agent_child_terminal_state,
        attempt_lifecycle=attempt_lifecycle,
        result_json=result_json,
        is_multi_agent_child=is_multi_agent_child,
    )


def _skill_manifests_for_persistence(
    result: ExecutorResult,
    payload: QueueRunPayload,
) -> list[dict[str, Any]]:
    return _project_skill_manifests_for_persistence(
        result,
        payload.skill_manifests,
        exact_invoked_skills=exact_invoked_skills,
    )


def _source_json_from_skill_manifest(
    item: dict[str, Any],
    *,
    release_decision: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return repositories.run_skill_snapshot_source_json(item, release_decision=release_decision)


def _payload_from_locked_run(
    locked_run: object,
    *,
    run_identity: dict[str, str],
) -> QueueRunPayload | None:
    candidate = _locked_run_payload_candidate(
        locked_run,
        run_identity=run_identity,
        harness_execution_kind=RUN_EXECUTION_KIND_HARNESS_CHAT,
    )
    if candidate is None:
        return None
    try:
        return QueueRunPayload.model_validate(candidate)
    except ValidationError:
        return None


_builtin_capability_subjects = _partial(
    builtin_capability_subjects,
    canonical_manifest=effective_skill_execution_profile,
    canonical_identities=repositories.canonical_builtin_tool_identities,
)


async def _fail_worker_pre_dispatch_error(
    conn,
    *,
    payload: QueueRunPayload,
    run_identity: dict[str, str],
    error_code: str,
    error_message: str,
    event_stage: str,
    event_payload: dict[str, Any],
    v4_capabilities: WorkerV4Capabilities,
    attempt_lifecycle: WorkerAttemptLifecycle,
    is_multi_agent_child: bool | None = None,
) -> _WorkerTerminalAfterTransaction:
    return await _fail_worker_pre_dispatch_error_impl(
        conn,
        payload=payload,
        run_identity=run_identity,
        error_code=error_code,
        error_message=error_message,
        event_stage=event_stage,
        event_payload=event_payload,
        v4_capabilities=v4_capabilities,
        attempt_lifecycle=attempt_lifecycle,
        is_multi_agent_child=is_multi_agent_child,
        fail_run_and_reconcile=_fail_run_and_reconcile_with_write,
        append_event=repositories.append_event,
        outcome_factory=WorkerOutcome,
        terminal_factory=_WorkerTerminalAfterTransaction,
    )


async def _fail_locked_run_snapshot(
    conn,
    *,
    payload: QueueRunPayload,
    locked_run: object,
    run_identity: dict[str, str],
    trace_id: str,
    v4_capabilities: WorkerV4Capabilities,
    attempt_lifecycle: WorkerAttemptLifecycle,
) -> _WorkerTerminalAfterTransaction:
    return await _fail_locked_run_snapshot_impl(
        conn,
        payload=payload,
        locked_run=locked_run,
        run_identity=run_identity,
        trace_id=trace_id,
        v4_capabilities=v4_capabilities,
        attempt_lifecycle=attempt_lifecycle,
        fail_run_and_reconcile=_fail_run_and_reconcile_with_write,
        append_denial_evidence=_append_worker_capability_denial_evidence,
        locked_run_principal=_locked_run_principal,
        locked_run_is_multi_agent_child=_locked_run_is_multi_agent_child,
        capability_record=_worker_capability_record,
        denied_capability_decision=_denied_capability_decision,
        outcome_factory=WorkerOutcome,
        terminal_factory=_WorkerTerminalAfterTransaction,
    )


async def _fail_worker_capability_authorization(
    conn,
    *,
    payload: QueueRunPayload,
    authorization: _WorkerCapabilityAuthorization,
    run_identity: dict[str, str],
    trace_id: str,
    v4_capabilities: WorkerV4Capabilities,
    attempt_lifecycle: WorkerAttemptLifecycle,
    policy: str = "capability_distribution",
) -> _WorkerTerminalAfterTransaction:
    return await _fail_worker_capability_authorization_impl(
        conn,
        payload=payload,
        authorization=authorization,
        run_identity=run_identity,
        trace_id=trace_id,
        v4_capabilities=v4_capabilities,
        attempt_lifecycle=attempt_lifecycle,
        fail_run_and_reconcile=_fail_run_and_reconcile_with_write,
        append_denial_evidence=_append_worker_capability_denial_evidence,
        outcome_factory=WorkerOutcome,
        terminal_factory=_WorkerTerminalAfterTransaction,
        policy=policy,
    )


async def process_run_payload(
    raw: dict[str, Any],
    registry: AdapterRegistry | None = None,
    *,
    worker_id: str | None = None,
    reconciliation: WorkerExecutorReconciliation | None = None,
    queue_lease: WorkerQueueLease | None = None,
    transaction_factory: Any | None = None,
    v4_capabilities: WorkerV4Capabilities,
    run_attempt_lifecycle: RunAttemptLifecycleService,
) -> WorkerOutcome:
    transaction_factory = transaction_factory if transaction_factory is not None else transaction
    try:
        envelope = parse_leased_queue_envelope(raw)
    except InvalidLeasedQueueEnvelope as exc:
        return WorkerOutcome(
            status="dead_letter",
            run_id=None,
            error_code="invalid_queue_attempt",
            error_message=str(exc),
        )
    except ValidationError as exc:
        return WorkerOutcome(
            status="dead_letter",
            run_id=None,
            error_code="invalid_queue_payload",
            error_message=str(exc),
        )
    payload = envelope.payload
    if v4_capabilities is None:
        raise RuntimeError("worker_v4_capabilities_unavailable")
    attempt_lifecycle = bind_worker_attempt_lifecycle(
        payload,
        leased_attempt_id=envelope.attempt_id,
        worker_id=worker_id,
        reconciliation=reconciliation,
        ports=build_worker_attempt_lifecycle_ports(run_attempt_lifecycle),
        queue_lease=queue_lease,
    )
    attempt_id = attempt_lifecycle.attempt_id
    finalize_multi_agent_parent = build_worker_parent_finalizer(
        run_attempt_lifecycle,
        reconcile_terminalized_run=reconcile_terminalized_permission_run,
    )
    trace_id = standard_trace_id(payload.run_id)

    adapter_registry = registry if registry is not None else AdapterRegistry()
    adapter = None
    run_identity = _payload_identity(payload)
    runtime_sandbox_lease: _WorkerRuntimeSandboxLease | None = None
    runtime_sandbox_lease_released = False
    runtime_sandbox_execution_detached = False

    terminal_after_transaction: _WorkerTerminalAfterTransaction | None = None
    capability_authorization: _WorkerCapabilityAuthorization | None = None
    admin_bypass_audits: tuple[_WorkerAdminBypassAudit, ...] = ()
    try:
        current_principal = await _resolve_current_principal_before_dispatch(
            payload,
            transaction_factory=transaction_factory,
            run_loader=repositories.get_run,
            principal_resolver=resolve_current_principal,
        )
        async with transaction_factory() as conn:
            locked = (
                await repositories.get_run(
                    conn,
                    tenant_id=payload.tenant_id,
                    run_id=payload.run_id,
                )
                if reconciliation is not None
                else await run_attempt_lifecycle.lock_queued_run(
                    conn,
                    tenant_id=payload.tenant_id,
                    run_id=payload.run_id,
                )
            )
            if reconciliation is not None and locked is not None:
                if str(locked.get("status") or "") != "running":
                    return WorkerOutcome(
                        "skipped",
                        payload.run_id,
                        "stale_terminal_state",
                        "Run already reached a terminal state",
                    )
                if str(reconciliation.lease_row.get("attempt_id") or "") != attempt_id:
                    return WorkerOutcome(
                        "skipped",
                        payload.run_id,
                        "stale_reconciliation_attempt",
                        "Executor reconciliation attempt is stale",
                    )
                attempt_lifecycle = (
                    await attempt_lifecycle.restore_reconciliation_authority(conn)
                )
            if locked is not None:
                await v4_capabilities.pending_admissions.prepare_pending_authority_in_transaction(
                    conn,
                    tenant_id=payload.tenant_id,
                    run_id=payload.run_id,
                    attempt_id=attempt_id,
                )
            if not locked:
                existing_run = await repositories.get_run(conn, tenant_id=payload.tenant_id, run_id=payload.run_id)
                if existing_run is None:
                    return WorkerOutcome(
                        "skipped",
                        payload.run_id,
                        "stale_queue_payload",
                        "Run no longer exists for leased queue payload",
                    )
                if str(existing_run.get("status") or "") == "queued":
                    await v4_capabilities.pending_admissions.prepare_pending_authority_in_transaction(
                        conn,
                        tenant_id=payload.tenant_id,
                        run_id=payload.run_id,
                        attempt_id=attempt_id,
                    )
                    error_code = "queue_payload_identity_mismatch"
                    error_message = "Queued run identity is invalid"
                    terminal_written, reconciled_parent = await _fail_run_and_reconcile_with_write(
                        conn,
                        v4_capabilities=v4_capabilities,
                        payload=payload,
                        tenant_id=payload.tenant_id,
                        run_id=payload.run_id,
                        error_code=error_code,
                        error_message=error_message,
                        attempt_lifecycle=attempt_lifecycle,
                    )
                    if not terminal_written:
                        terminal_after_transaction = _WorkerTerminalAfterTransaction(
                            WorkerOutcome(
                                "skipped",
                                payload.run_id,
                                "stale_terminal_state",
                                "Run already reached a terminal state",
                            ),
                            payload,
                            None,
                        )
                        return terminal_after_transaction.outcome
                    await repositories.append_event(
                        conn,
                        tenant_id=payload.tenant_id,
                        run_id=payload.run_id,
                        event_type="error",
                        stage="worker",
                        message=error_message,
                        payload={"visible_to_user": False, "severity": "error", "reason": "scope_guard_rejected_lock"},
                    )
                    terminal_after_transaction = _WorkerTerminalAfterTransaction(
                        WorkerOutcome("failed", payload.run_id, error_code, error_message),
                        payload,
                        reconciled_parent,
                    )
                    return terminal_after_transaction.outcome
                await repositories.append_event(
                    conn,
                    tenant_id=payload.tenant_id,
                    run_id=payload.run_id,
                    event_type="skip",
                    stage="worker",
                    message="Run is not queued; skipping duplicate or stale payload",
                )
                return WorkerOutcome("skipped", payload.run_id)
            run_identity = _locked_run_identity(payload, locked)
            mismatch_fields = _identity_mismatch_fields(payload, run_identity)
            if mismatch_fields:
                terminal_after_transaction = await _fail_worker_pre_dispatch_error(
                    conn,
                    payload=payload,
                    run_identity=run_identity,
                    v4_capabilities=v4_capabilities, attempt_lifecycle=attempt_lifecycle,
                    error_code="queue_payload_identity_mismatch",
                    error_message="Queue payload identity does not match run record",
                    event_stage="worker",
                    event_payload={
                        "visible_to_user": False,
                        "severity": "error",
                        "mismatch_fields": mismatch_fields,
                    },
                )
                return terminal_after_transaction.outcome
            locked = await _with_locked_run_model_snapshot(
                locked,
                conn=conn,
                run_identity=run_identity,
                load_run_model_snapshot=_load_run_model_snapshot,
            )
            trace_id = _locked_run_trace_id(payload, locked)
            locked_payload = _payload_from_locked_run(
                locked,
                run_identity=run_identity,
            )
            if locked_payload is None:
                terminal_after_transaction = await _fail_locked_run_snapshot(
                    conn,
                    payload=payload,
                    locked_run=locked,
                    run_identity=run_identity,
                    trace_id=trace_id,
                    v4_capabilities=v4_capabilities, attempt_lifecycle=attempt_lifecycle,
                )
                return terminal_after_transaction.outcome
            if not _locked_agent_profile_identity_valid(locked_payload.agent_profile or {}, locked) or (
                reconciliation is not None
                and not _reconciliation_agent_profile_binding_matches(payload.input, locked_payload.agent_profile or {})):
                terminal_after_transaction = await _fail_locked_run_snapshot(
                    conn,
                    payload=locked_payload,
                    locked_run=locked,
                    run_identity=run_identity,
                    trace_id=trace_id,
                    v4_capabilities=v4_capabilities, attempt_lifecycle=attempt_lifecycle,
                )
                return terminal_after_transaction.outcome
            if locked_payload.agent_profile and current_principal is not None:
                pinned_revision = int(locked_payload.agent_profile["revision"])
                pinned_hash = str(locked_payload.agent_profile["content_hash"])
                profile_admission = await reauthorize_bound_profile_for_worker_dispatch(
                    conn,
                    principal=current_principal,
                    agent_id=run_identity["agent_id"],
                    revision=pinned_revision,
                    content_hash=pinned_hash,
                )
                profile_denial_reason = None
                if profile_admission is None:
                    profile_denial_reason = "profile_not_authorized"
                elif not _agent_profile_snapshot_matches_authority(
                    locked_payload,
                    profile_admission,
                ):
                    profile_denial_reason = "profile_snapshot_invalid"
                if profile_denial_reason is not None:
                    profile_denial = _worker_capability_record(
                        "agent_profile",
                        run_identity["agent_id"],
                        _denied_capability_decision(profile_denial_reason),
                    )
                    terminal_after_transaction = await _fail_worker_capability_authorization(
                        conn,
                        payload=locked_payload,
                        authorization=_WorkerCapabilityAuthorization(
                            locked_payload,
                            current_principal,
                            (),
                            profile_denial,
                        ),
                        run_identity=run_identity,
                        trace_id=trace_id,
                        v4_capabilities=v4_capabilities, attempt_lifecycle=attempt_lifecycle,
                        policy="agent_profile_authority",
                    )
                    return terminal_after_transaction.outcome
            payload = locked_payload
            try:
                materialized_skill_manifests = await repositories.materialize_run_skill_manifests(
                    conn,
                    tenant_id=run_identity["tenant_id"],
                    run_id=run_identity["run_id"],
                    skill_manifest_refs=payload.skill_manifests,
                )
            except repositories.RepositoryConflictError:
                terminal_after_transaction = await _fail_locked_run_snapshot(
                    conn,
                    payload=payload,
                    locked_run=locked,
                    run_identity=run_identity,
                    trace_id=trace_id,
                    v4_capabilities=v4_capabilities, attempt_lifecycle=attempt_lifecycle,
                )
                return terminal_after_transaction.outcome
            payload = payload.model_copy(
                update={"skill_manifests": materialized_skill_manifests}
            )
            capability_authorization = await _reauthorize_worker_capabilities_impl(
                conn,
                payload=payload,
                run_identity=run_identity,
                attempt_id=attempt_id,
                current_principal=current_principal,
                builtin_capability_subjects=_builtin_capability_subjects,
                execution_boundary_decider=_worker_execution_boundary_decision,
                mcp_reauthorizer=_partial(
                    _reauthorize_mcp_capabilities_impl,
                    mcp_api_module=mcp_api,
                ),
                sandbox_provider=get_settings().sandbox_container_provider,
            )
            admin_bypass_audits = _worker_admin_bypass_audits(
                authorization=capability_authorization,
                run_identity=run_identity,
                trace_id=trace_id,
            )
            await _append_worker_admin_bypass_audits(conn, audits=admin_bypass_audits)
            await _append_worker_tool_policy_audits(
                conn,
                authorization=capability_authorization,
                run_identity=run_identity,
                trace_id=trace_id,
            )
            if capability_authorization.denial is not None:
                terminal_after_transaction = await _fail_worker_capability_authorization(
                    conn,
                    payload=payload,
                    authorization=capability_authorization,
                    run_identity=run_identity,
                    trace_id=trace_id,
                    v4_capabilities=v4_capabilities, attempt_lifecycle=attempt_lifecycle,
                )
                return terminal_after_transaction.outcome
            payload = capability_authorization.payload
            if await repositories.is_cancel_requested(conn, tenant_id=run_identity["tenant_id"], run_id=run_identity["run_id"]):
                cancel_result = {"message": "任务已取消"}
                terminal_written = await cancel_run_with_v4(
                    conn,
                    capabilities=v4_capabilities,
                    tenant_id=run_identity["tenant_id"],
                    run_id=run_identity["run_id"],
                    result_json=cancel_result,
                )
                if not terminal_written:
                    terminal_after_transaction = _WorkerTerminalAfterTransaction(
                        WorkerOutcome(
                            "skipped",
                            run_identity["run_id"],
                            "stale_terminal_state",
                            "Run already reached a terminal state",
                        ),
                        payload,
                        None,
                    )
                    return terminal_after_transaction.outcome
                reconciled_parent = await _reconcile_multi_agent_child_terminal_state(
                    conn,
                    payload=payload,
                    child_status="cancelled",
                    result_json=cancel_result,
                )
                terminal_after_transaction = _WorkerTerminalAfterTransaction(
                    WorkerOutcome("cancelled", run_identity["run_id"]),
                    payload,
                    reconciled_parent,
                )
                return terminal_after_transaction.outcome
            try:
                if payload.executor_type in {"ragflow", "runtime211"}:
                    raise KeyError(f"Unknown executor type: {payload.executor_type}")
                adapter = adapter_registry.get(payload.executor_type)
            except KeyError as exc:
                terminal_written, reconciled_parent = await _fail_run_and_reconcile_with_write(
                    conn,
                    v4_capabilities=v4_capabilities,
                    payload=payload,
                    tenant_id=payload.tenant_id,
                    run_id=payload.run_id,
                    error_code="unknown_executor_type",
                    error_message=str(exc),
                    result_json=_pre_dispatch_failure_result(
                        "unknown_executor_type", "executor_resolution", error=exc
                    ),
                    attempt_lifecycle=attempt_lifecycle,
                )
                if not terminal_written:
                    terminal_after_transaction = _WorkerTerminalAfterTransaction(
                        WorkerOutcome(
                            "skipped",
                            payload.run_id,
                            "stale_terminal_state",
                            "Run already reached a terminal state",
                        ),
                        payload,
                        None,
                    )
                    return terminal_after_transaction.outcome
                await repositories.append_event(
                    conn,
                    tenant_id=payload.tenant_id,
                    run_id=payload.run_id,
                    event_type="error",
                    stage="worker",
                    message="Unknown executor type",
                    payload={"executor_type": payload.executor_type},
                )
                terminal_after_transaction = _WorkerTerminalAfterTransaction(
                    WorkerOutcome("failed", payload.run_id, "unknown_executor_type", str(exc)),
                    payload,
                    reconciled_parent,
                )
                return terminal_after_transaction.outcome
        async with transaction_factory() as conn:
            fence = await worker_dispatch_fence(
                conn, run_identity=run_identity, locked_run=locked,
                context_snapshot_id=str(payload.context_snapshot_id or ""),
                reconciliation=reconciliation is not None,
            )
            if fence == "stale":
                return WorkerOutcome("skipped", payload.run_id, "stale_terminal_state")
            context_ref, context_error_code = (
                await materialize_queued_worker_context_snapshot(
                    conn, payload=payload, run_identity=run_identity,
                    context_projector=context_snapshot_ref_from_row,
                ) if fence == "ready" else (None, None)
            )
            if context_ref is None:
                error_code = "worker_dispatch_fence_invalid" if fence == "invalid" else context_error_code or "context_snapshot_unavailable"
                error_message = (
                    "Run dispatch authority changed" if fence == "invalid"
                    else "A new conversation is required because native provider context is unavailable"
                    if context_error_code else "Run context snapshot is unavailable"
                )
                terminal_after_transaction = await _fail_worker_pre_dispatch_error(
                    conn,
                    payload=payload,
                    run_identity=run_identity,
                    v4_capabilities=v4_capabilities, attempt_lifecycle=attempt_lifecycle,
                    error_code=error_code,
                    error_message=error_message,
                    event_stage="context",
                    event_payload={"visible_to_user": False, "error_code": error_code},
                )
                return terminal_after_transaction.outcome
            payload = payload.model_copy(update={"file_ids": context_ref["file_ids"]})
            try:
                execution_spec = compile_execution_spec_for_dispatch(
                    run_identity=run_identity,
                    queue_payload=payload,
                    trace_id=trace_id,
                    context_snapshot_id=str(context_ref["context_snapshot_id"]),
                    context_snapshot=context_ref["context_snapshot"],
                    context_pack={**executor_context_pack_from_snapshot(context_ref["context_snapshot"]),
                                  "conversation_context": context_ref["conversation_context"]},
                    run_model_snapshot=locked,
                )
                run_payload = project_execution_spec_to_run_payload(
                    execution_spec, attempt_id=attempt_id
                )
                run_payload = await mcp_api.attach_mcp_server_configs(
                    conn, principal=capability_authorization.principal, run_payload=run_payload
                )
            except ValueError as exc:
                mcp_error = exc if isinstance(exc, mcp_api.McpRuntimeContextError) else None
                error_code = mcp_error.code if mcp_error else "execution_spec_invalid"
                terminal_after_transaction = await _fail_worker_pre_dispatch_error(
                    conn,
                    payload=payload,
                    run_identity=run_identity,
                    v4_capabilities=v4_capabilities, attempt_lifecycle=attempt_lifecycle,
                    error_code=error_code,
                    error_message="MCP runtime configuration is unavailable" if mcp_error else "Execution specification is invalid",
                    event_stage="authorization" if mcp_error else "worker",
                    event_payload={
                        "visible_to_user": bool(mcp_error),
                        "severity": "error",
                        "error_code": error_code,
                    },
                    is_multi_agent_child=_locked_run_is_multi_agent_child(locked),
                )
                return terminal_after_transaction.outcome
            await attempt_lifecycle.bind_execution_spec(conn, execution_spec)
            await append_user_event(
                conn,
                tenant_id=run_identity["tenant_id"],
                run_id=run_identity["run_id"],
                event_type="worker_started",
                stage="worker",
                message="Run started",
                payload=_worker_runtime_evidence(
                    worker_id=worker_id,
                    executor_type=payload.executor_type,
                ),
            )
            if reconciliation is None and not _ordinary_run_uses_runtime_sandbox(
                payload,
                context_snapshot=context_ref["context_snapshot"],
            ):
                runtime_sandbox_lease = await _create_worker_runtime_sandbox_lease(
                    conn,
                    payload=payload,
                    run_identity=run_identity,
                    trace_id=trace_id,
                    attempt_id=attempt_id,
                    worker_id=worker_id,
                )
    finally:
        if terminal_after_transaction is not None:
            await admit_v4_stream(
                v4_capabilities,
                tenant_id=terminal_after_transaction.payload.tenant_id,
                run_id=terminal_after_transaction.payload.run_id,
                attempt_id=attempt_id,
            )
            await finalize_multi_agent_parent(
                transaction_factory,
                terminal_after_transaction.payload,
                terminal_after_transaction.reconciled_parent,
            )
            await publish_run_event(
                v4_capabilities,
                tenant_id=terminal_after_transaction.payload.tenant_id,
                run_id=terminal_after_transaction.payload.run_id,
            )

    async def event_sink(
        *,
        event_type: str,
        stage: str,
        message: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        if event_type == "assistant_delta":
            raise WorkerDirectAssistantDeltaError
        if await persist_worker_event(
            v4_capabilities,
            run_payload=run_payload,
            persist_event=True,
            event_type=event_type,
            stage=stage,
            message=message,
            payload=payload,
            record_run_step=_record_run_step_from_event,
        ):
            raise WorkerRunCancelled

    async def release_runtime_sandbox_lease(conn, *, reason: str) -> None:
        nonlocal runtime_sandbox_lease_released
        if reconciliation is not None:
            return
        if runtime_sandbox_lease is None or runtime_sandbox_lease_released:
            return
        await _release_worker_runtime_sandbox_lease(conn, runtime_sandbox_lease, reason=reason)
        runtime_sandbox_lease_released = True

    async def cleanup_runtime_sandbox_lease_after_interruption() -> None:
        if runtime_sandbox_execution_detached:
            return
        if runtime_sandbox_lease is None or runtime_sandbox_lease_released:
            return
        try:
            async with transaction_factory() as conn:
                await release_runtime_sandbox_lease(conn, reason="run_terminal_interrupted")
        except Exception:  # noqa: BLE001 - interruption cleanup is best effort.
            return

    try:
        if adapter is None:
            raise RuntimeError("executor_adapter_not_resolved")

        if reconciliation is not None:
            started_at = time.monotonic()
            result: ExecutorResult | ExecutorDispatchAccepted = reconciliation.result
        else:
            await admit_v4_stream(
                v4_capabilities,
                tenant_id=run_payload.tenant_id,
                run_id=run_payload.run_id,
                attempt_id=run_payload.attempt_id,
            )

            async def cancel_requested() -> bool:
                async with transaction_factory() as conn:
                    return await repositories.is_cancel_requested(
                        conn,
                        tenant_id=run_payload.tenant_id,
                        run_id=run_payload.run_id,
                    )

            execution_owner = build_artifact_execution_owner(
                run_payload, transaction_factory, reserve_provisional_artifact_cleanup, RunExecutionOwner
            )
            started_at = time.monotonic()
            result = await _submit_run_until_cancelled(
                adapter,
                run_payload,
                event_sink=event_sink,
                cancel_requested=cancel_requested,
                execution_owner=execution_owner,
            )
        if isinstance(result, ExecutorDispatchAccepted):
            if not result.lease_id:
                raise ValueError("executor_dispatch_acceptance_lease_missing")
            runtime_sandbox_execution_detached = True
            return WorkerOutcome(status="running", run_id=run_payload.run_id)
        latency_ms = max(int((time.monotonic() - started_at) * 1000), 0)
        result.validate()
        result = _normalize_sandbox_reported_failure(result)
        result = replace(result, executor_payload=bind_validated_controlled_skill_evidence(payload, result, attempt_id, adapter))
        if capability_authorization is None:
            raise RuntimeError("worker_capability_authorization_missing")
        required_tool_decision = capability_authorization.required_tool_decision or RequiredCapabilityDecision(
            True,
            "required_tool_not_declared",
            "",
            "",
        )
        required_completion = required_tool_completion_for_run(
            payload=payload,
            run_identity=run_identity,
            attempt_id=attempt_id,
            authorization=required_tool_decision,
            executor_payload=result.executor_payload,
        )
        if result.status == "succeeded" and not required_completion.allowed:
            result = replace(
                result,
                status="failed",
                artifacts=[],
                result={
                    **result.result,
                    "message": "Required execution capability evidence is unavailable.",
                    "error_code": required_completion.reason,
                },
            )
    except WorkerRunCancelled:
        reconciled_parent = None
        cancelled_outcome = WorkerOutcome(
            "skipped",
            payload.run_id,
            "stale_terminal_state",
            "Run already reached a terminal state",
        )
        async with transaction_factory() as conn:
            cancel_result = {"message": "任务已取消"}
            terminal_written = await attempt_lifecycle.cancel(
                conn,
                capabilities=v4_capabilities,
                result_json=cancel_result,
            )
            if terminal_written:
                reconciled_parent = await _reconcile_multi_agent_child_terminal_state(
                    conn,
                    payload=payload,
                    child_status="cancelled",
                    result_json=cancel_result,
                )
                await release_runtime_sandbox_lease(conn, reason="run_cancelled")
                cancelled_outcome = WorkerOutcome("cancelled", payload.run_id)
        if cancelled_outcome.status == "skipped":
            progress = await attempt_lifecycle.drain(
                capabilities=v4_capabilities,
                transaction_factory=transaction_factory,
            )
            if progress is not None and progress.is_terminal("cancelled"):
                async with transaction_factory() as conn:
                    reconciled_parent = await _reconcile_multi_agent_child_terminal_state(
                        conn,
                        payload=payload,
                        child_status="cancelled",
                        result_json=cancel_result,
                    )
                    await release_runtime_sandbox_lease(conn, reason="run_cancelled")
                cancelled_outcome = WorkerOutcome("cancelled", payload.run_id)
        await finalize_parent_and_publish(
            transaction_factory,
            v4_capabilities,
            finalize_multi_agent_parent,
            payload,
            reconciled_parent,
        )
        return cancelled_outcome
    except Exception as exc:  # noqa: BLE001 - worker boundary terminalizes all failures.
        reconciled_parent = None
        failure_code, failure_message, failure_result = _executor_exception_failure(exc)
        outcome_after_exception = WorkerOutcome(
            "failed", payload.run_id, failure_code, failure_message
        )
        async with transaction_factory() as conn:
            await v4_capabilities.pending_admissions.prepare_pending_authority_in_transaction(
                conn,
                tenant_id=payload.tenant_id,
                run_id=payload.run_id,
                attempt_id=attempt_id,
            )
            if await repositories.is_cancel_requested(conn, tenant_id=payload.tenant_id, run_id=payload.run_id):
                cancel_result = {"message": "任务已取消"}
                terminal_written = await attempt_lifecycle.cancel(
                    conn,
                    capabilities=v4_capabilities,
                    result_json=cancel_result,
                )
                if not terminal_written:
                    outcome_after_exception = WorkerOutcome(
                        "skipped",
                        payload.run_id,
                        "stale_terminal_state",
                        "Run already reached a terminal state",
                    )
                else:
                    reconciled_parent = await _reconcile_multi_agent_child_terminal_state(
                        conn,
                        payload=payload,
                        child_status="cancelled",
                        result_json=cancel_result,
                    )
                    await release_runtime_sandbox_lease(conn, reason="run_cancelled")
                    outcome_after_exception = WorkerOutcome("cancelled", payload.run_id)
            else:
                terminal_written, reconciled_parent = await _fail_run_and_reconcile_with_write(
                    conn,
                    v4_capabilities=v4_capabilities,
                    payload=payload,
                    tenant_id=payload.tenant_id,
                    run_id=payload.run_id,
                    error_code=failure_code,
                    error_message=failure_message,
                    result_json=failure_result,
                    attempt_lifecycle=attempt_lifecycle,
                )
                if not terminal_written:
                    outcome_after_exception = WorkerOutcome(
                        "skipped",
                        payload.run_id,
                        "stale_terminal_state",
                        "Run already reached a terminal state",
                    )
                else:
                    await repositories.append_event(
                        conn,
                        tenant_id=payload.tenant_id,
                        run_id=payload.run_id,
                        event_type="error",
                        stage="executor",
                        message="Executor failed",
                        payload={
                            "error": failure_message,
                            "executor_type": payload.executor_type,
                            "visible_to_user": False,
                        },
                    )
                    await release_runtime_sandbox_lease(conn, reason="run_failed")
        if outcome_after_exception.status == "skipped":
            progress = await attempt_lifecycle.drain(
                capabilities=v4_capabilities,
                transaction_factory=transaction_factory,
                error_code=failure_code,
            )
            if progress is not None and progress.is_terminal():
                final_status = str(progress.status)
                async with transaction_factory() as conn:
                    await release_runtime_sandbox_lease(
                        conn,
                        reason=(
                            "run_cancelled" if final_status == "cancelled" else "run_failed"
                        ),
                    )
                outcome_after_exception = WorkerOutcome(
                    final_status,
                    payload.run_id,
                    failure_code if final_status == "failed" else None,
                    failure_message if final_status == "failed" else None,
                )
        await finalize_parent_and_publish(
            transaction_factory,
            v4_capabilities,
            finalize_multi_agent_parent,
            payload,
            reconciled_parent,
        )
        return outcome_after_exception

    observability = _executor_observability(result.executor_payload, latency_ms=latency_ms)
    event_observability_kwargs = _event_observability_kwargs(observability, result.executor_payload)
    terminal_event_kwargs = {"trace_id": trace_id, **event_observability_kwargs} if event_observability_kwargs else {}

    artifact_records = build_artifact_records(
        result.artifacts, reconciliation is not None, repositories.new_id, _artifact_download_url
    )
    skill_snapshot = _skill_snapshot_from_result(
        result,
        exact_invoked_skills=exact_invoked_skills,
    )
    required_agent_skill_id = None
    agent_capability_state = (
        project_agent_capability_state(
            required_skill_id=required_agent_skill_id or "",
            executor_payload={**result.result, **result.executor_payload},
            run_succeeded=result.status == "succeeded",
            durable_artifact_count=0,
        )
        if required_agent_skill_id is not None
        else None
    )
    public_result = {
        key: value
        for key, value in (result.result | ({"runtime_diagnostics": result.executor_payload["runtime_diagnostics"]} if "runtime_diagnostics" in result.executor_payload else {})).items()
        if key not in {"skill_manifests", "used_skills", "used_skills_source", "inferred_used_skills"}
    }
    if required_agent_skill_id is None and (
        "used_skills" in result.result or "used_skills" in result.executor_payload
    ):
        public_result["used_skills"] = skill_snapshot["used_skills"]
    result_payload = {
        **public_result,
        **observability,
        "message": sanitize_assistant_message(str(result.result.get("message") or "")),
        "artifacts": [
            {
                "id": item["id"],
                "artifact_type": item["artifact_type"],
                "label": item["label"],
                "content_type": item["content_type"],
                "size_bytes": item["size_bytes"],
                "download_url": item["download_url"],
            }
            for item in artifact_records
        ],
        "executor": {
            "schema_version": result.schema_version,
            "adapter_version": result.adapter_version,
            "executor_type": result.executor_type,
            "executor_version": result.executor_version,
            "capabilities": result.capabilities,
        },
    }
    if skill_snapshot and required_agent_skill_id is None:
        result_payload["skills"] = skill_snapshot
    if agent_capability_state is not None:
        result_payload["capability_state"] = agent_capability_state.public_projection()
    assistant_message_for_persistence: str | None = None
    assistant_message_metadata: dict[str, Any] = {}
    reconciled_parent = None
    try:
        async with transaction_factory() as conn:
            locked_run = await repositories.get_run(
                conn,
                tenant_id=payload.tenant_id,
                run_id=payload.run_id,
                for_update=True,
            )
            if locked_run is None or str(locked_run.get("status") or "") in {"succeeded", "failed", "cancelled"}:
                raise _WorkerSuccessCommitBlocked()
            if reconciliation is not None and not await sandbox_lease_repository.is_sandbox_executor_reconciliation_claim_current(
                conn,
                lease_id=str(reconciliation.lease_row["id"]),
                claim_token=reconciliation.claim_token,
            ):
                raise RuntimeError("executor_reconciliation_claim_lost")
            pending_permission_blocks_success = (
                result.status == "succeeded"
                and await repositories.has_pending_tool_permission_requests(
                    conn,
                    tenant_id=payload.tenant_id,
                    run_id=payload.run_id,
                )
            )
            # The selected platform Skill owns this contract.  Preserve an
            # adapter's additional declared requirements, but never let an
            # executor omit the capability requirement on resume or retry.
            required_artifact_types = set(required_artifact_types_for_skill(payload.skill_id)) | {
                str(value)
                for value in result.executor_payload.get("required_artifact_types", [])
                if isinstance(value, str) and value
            }
            produced_artifact_types = {artifact.artifact_type for artifact in result.artifacts}
            missing_required_artifact_types = required_artifact_types - produced_artifact_types
            missing_required_artifact = result.status == "succeeded" and bool(missing_required_artifact_types)
            if pending_permission_blocks_success or missing_required_artifact:
                error_code = (
                    "tool_permission_pending"
                    if pending_permission_blocks_success
                    else "required_artifact_missing"
                )
                error_message = (
                    "A pending tool-permission request blocks successful completion."
                    if pending_permission_blocks_success
                    else "The file-required Skill did not produce every required artifact type."
                )
                result = replace(
                    result,
                    status="failed",
                    artifacts=[],
                    result={
                        **result.result,
                        "message": error_message,
                        "error_code": error_code,
                        "missing_required_artifact_types": sorted(missing_required_artifact_types),
                    },
                )
                artifact_records = []
                result_payload = {
                    **result_payload,
                    "message": error_message,
                    "error_code": error_code,
                    "artifacts": [],
                }
            answer_receipt = result.executor_payload.get("answer_receipt")
            if result.status == "succeeded" and answer_receipt is not None:
                materialized = await materialize_worker_answer(v4_capabilities, conn, result=result, result_payload=result_payload, artifact_records=artifact_records, tenant_id=payload.tenant_id, run_id=payload.run_id, attempt_id=attempt_id, answer_receipt=answer_receipt, limits=_ANSWER_PERSISTENCE_LIMITS)
                result = materialized.result
                result_payload = materialized.result_payload
                artifact_records = materialized.artifact_records
                assistant_message_for_persistence = materialized.assistant_message_for_persistence
                assistant_message_metadata = materialized.assistant_message_metadata
            cancel_requested = await repositories.is_cancel_requested(conn, tenant_id=payload.tenant_id, run_id=payload.run_id)
            if result.status == "succeeded" and cancel_requested:
                result_payload = {
                    **result_payload,
                    "cancel_status": "cancel_requested_but_completed",
                }
            if agent_capability_state is not None:
                agent_capability_state = project_agent_capability_state(
                    required_skill_id=required_agent_skill_id or "",
                    executor_payload={**result.result, **result.executor_payload},
                    run_succeeded=result.status == "succeeded",
                    durable_artifact_count=0,
                )
                result_payload["capability_state"] = agent_capability_state.public_projection()
                semantic_events = (
                    (
                        "capability_staged",
                        agent_capability_state.staged,
                        "Agent capability loaded",
                        "staged",
                    ),
                    (
                        "capability_sdk_registered",
                        agent_capability_state.sdk_registered,
                        "Agent capability registered",
                        "sdk_registered",
                    ),
                    (
                        "capability_actually_invoked",
                        agent_capability_state.actually_invoked,
                        "Agent capability invoked",
                        "actually_invoked",
                    ),
                    (
                        "capability_completed",
                        agent_capability_state.completed,
                        "Agent capability completed",
                        "completed",
                    ),
                )
                for event_type, present, message, public_state in semantic_events:
                    if not present:
                        continue
                    await append_user_event(
                        conn,
                        tenant_id=payload.tenant_id,
                        run_id=payload.run_id,
                        event_type=event_type,
                        stage="capability",
                        message=message,
                        payload={"capability_state": public_state},
                    )
                if agent_capability_state.optional_not_invoked_count:
                    await append_user_event(
                        conn,
                        tenant_id=payload.tenant_id,
                        run_id=payload.run_id,
                        event_type="capability_optional_not_invoked",
                        stage="capability",
                        message="Optional Agent capabilities were not invoked",
                        payload={
                            "capability_state": "optional_not_invoked",
                            "count": agent_capability_state.optional_not_invoked_count,
                        },
                    )
            await promote_artifact_reservations(
                conn, artifact_records, payload, promote_provisional_artifact_cleanup
            )
            for artifact in artifact_records:
                manifest_json = artifact_manifest_contract(
                    artifact_type=artifact["artifact_type"],
                    manifest=_sanitize_artifact_manifest(artifact["manifest_json"]),
                )
                lineage = artifact_lineage_contract(manifest_json, source_run_id=payload.run_id)
                await repositories.create_artifact(
                    conn,
                    artifact_id=artifact["id"],
                    tenant_id=payload.tenant_id,
                    run_id=payload.run_id,
                    artifact_type=artifact["artifact_type"],
                    label=artifact["label"],
                    content_type=artifact["content_type"],
                    storage_key=artifact["storage_key"],
                    size_bytes=artifact["size_bytes"],
                    trace_id=trace_id,
                    manifest_json=manifest_json,
                )
                await append_user_event(
                    conn,
                    tenant_id=payload.tenant_id,
                    run_id=payload.run_id,
                    event_type="artifact_ready",
                    stage="artifact",
                    message="Artifact is ready",
                    payload={
                        "artifact_id": artifact["id"],
                        "artifact_type": artifact["artifact_type"],
                        "download_url": artifact["download_url"],
                        "lineage": lineage,
                    },
                )
            if agent_capability_state is not None:
                agent_capability_state = project_agent_capability_state(
                    required_skill_id=required_agent_skill_id or "",
                    executor_payload={**result.result, **result.executor_payload},
                    run_succeeded=result.status == "succeeded",
                    durable_artifact_count=len(artifact_records),
                )
                result_payload["capability_state"] = agent_capability_state.public_projection()
            for item in _skill_manifests_for_persistence(result, payload):
                skill_id = str(item.get("skill_id") or "").strip()
                if not skill_id:
                    continue
                await repositories.upsert_run_skill_snapshot(
                    conn,
                    tenant_id=payload.tenant_id,
                    run_id=payload.run_id,
                    skill_id=skill_id,
                    skill_version=str(item.get("version") or item.get("skill_version") or ""),
                    content_hash=str(item.get("content_hash") or item.get("version") or ""),
                    source_json=_source_json_from_skill_manifest(
                        item,
                        release_decision=payload.release_decision,
                    ),
                    dependency_ids=_dependency_ids_from_manifest(item),
                    allowed=bool(item.get("allowed")),
                    staged=bool(item.get("staged")),
                    used=bool(item.get("used")),
                    used_skills_source=str(item.get("used_skills_source") or "").strip(),
                )
            if result.status == "succeeded":
                await _attach_multi_agent_result_summary(
                    conn,
                    tenant_id=payload.tenant_id,
                    run_id=payload.run_id,
                    result_capabilities=result.capabilities,
                    result_payload=result_payload,
                )
                await persist_assistant_with_provider_coverage(
                    conn, append_message=repositories.append_message,
                    tenant_id=payload.tenant_id, session_id=payload.session_id,
                    run_id=payload.run_id, attempt_id=attempt_id,
                    executor_type=payload.executor_type,
                    content=(assistant_message_for_persistence
                             if assistant_message_for_persistence is not None
                             else str(result_payload.get("message") or "")),
                    metadata_json={
                        **assistant_artifact_metadata(artifact_records),
                        "executor_type": result.executor_type,
                        "adapter_version": result.adapter_version,
                        **assistant_message_metadata,
                        **(
                            {"capability_state": agent_capability_state.public_projection()}
                            if agent_capability_state is not None
                            else {"skills": skill_snapshot}
                        ),
                    },
                    provider_final_sequence=result.executor_payload.get("provider_session_final_sequence"),
                )

                await append_user_event(
                    conn,
                    tenant_id=payload.tenant_id,
                    run_id=payload.run_id,
                    event_type="assistant_message_created",
                    stage="message",
                    message="Assistant response is ready",
                    payload={
                        "artifact_count": len(result.artifacts),
                        **(
                            {"capability_state": agent_capability_state.public_projection()}
                            if agent_capability_state is not None
                            else {"skills": skill_snapshot}
                        ),
                    },
                )
                if cancel_requested:
                    await append_user_event(
                        conn,
                        tenant_id=payload.tenant_id,
                        run_id=payload.run_id,
                        event_type="cancel_requested_but_completed",
                        stage="control",
                        message="取消请求已记录，但任务已完成",
                        payload={"severity": "warning"},
                    )
                terminal_written = await attempt_lifecycle.complete(
                    conn,
                    capabilities=v4_capabilities,
                    result_json=result_payload,
                )
                if not terminal_written:
                    raise _WorkerSuccessCommitBlocked()
                else:
                    reconciled_parent = await _reconcile_multi_agent_child_terminal_state(
                        conn,
                        payload=payload,
                        child_status="succeeded",
                        result_json=result_payload,
                    )
                    await append_user_event(
                        conn,
                        tenant_id=payload.tenant_id,
                        run_id=payload.run_id,
                        event_type="run_succeeded",
                        stage="worker",
                        message="Run succeeded",
                        payload={
                            "artifact_count": len(result.artifacts),
                            **(
                                {"capability_state": agent_capability_state.public_projection()}
                                if agent_capability_state is not None
                                else {"skills": skill_snapshot}
                            ),
                        },
                        **terminal_event_kwargs,
                    )
                    await repositories.append_event(
                        conn,
                        tenant_id=payload.tenant_id,
                        run_id=payload.run_id,
                        event_type="status",
                        stage="worker",
                        message="Run succeeded",
                        payload={"artifact_count": len(result.artifacts), "visible_to_user": False},
                    )
                    await release_runtime_sandbox_lease(conn, reason="run_succeeded")
                    terminal_outcome = WorkerOutcome("succeeded", payload.run_id)
            else:
                reported_error_code = str(result.result.get("error_code") or "executor_reported_failure")
                reported_error_message = _public_executor_failure_message(result)
                await _attach_multi_agent_result_summary(
                    conn,
                    tenant_id=payload.tenant_id,
                    run_id=payload.run_id,
                    result_capabilities=result.capabilities,
                    result_payload=result_payload,
                )
                if cancel_requested and _result_prefers_cancelled_after_failure(result):
                    cancel_result = {"message": "任务已取消"}
                    terminal_written = await attempt_lifecycle.cancel(
                        conn,
                        capabilities=v4_capabilities,
                        result_json=cancel_result,
                    )
                    if not terminal_written:
                        terminal_outcome = WorkerOutcome(
                            "skipped",
                            payload.run_id,
                            "stale_terminal_state",
                            "Run already reached a terminal state",
                        )
                    else:
                        reconciled_parent = await _reconcile_multi_agent_child_terminal_state(
                            conn,
                            payload=payload,
                            child_status="cancelled",
                            result_json=cancel_result,
                        )
                        await release_runtime_sandbox_lease(conn, reason="run_cancelled")
                        terminal_outcome = WorkerOutcome("cancelled", payload.run_id)
                else:
                    terminal_written, reconciled_parent = await _fail_run_and_reconcile_with_write(
                        conn,
                        v4_capabilities=v4_capabilities,
                        payload=payload,
                        tenant_id=payload.tenant_id,
                        run_id=payload.run_id,
                        error_code=reported_error_code,
                        error_message=reported_error_message,
                        result_json=result_payload,
                        attempt_lifecycle=attempt_lifecycle,
                    )
                    if not terminal_written:
                        terminal_outcome = WorkerOutcome(
                            "skipped",
                            payload.run_id,
                            "stale_terminal_state",
                            "Run already reached a terminal state",
                        )
                    else:
                        await persist_worker_failure_event(
                            conn,
                            tenant_id=payload.tenant_id,
                            run_id=payload.run_id,
                            result=result,
                            attempt_id=attempt_id,
                            trace_id=trace_id,
                            error_code=reported_error_code,
                        )
                        await release_runtime_sandbox_lease(conn, reason="run_failed")
                        terminal_outcome = WorkerOutcome("failed", payload.run_id, reported_error_code, reported_error_message)
    except _WorkerSuccessCommitBlocked:
        async with transaction_factory() as conn:
            blocked_reason = await repositories.classify_success_commit_block(
                conn,
                tenant_id=payload.tenant_id,
                run_id=payload.run_id,
            )
            if blocked_reason == "cancel_requested":
                cancel_result = {"message": "任务已取消"}
                terminal_written = await attempt_lifecycle.cancel(
                    conn,
                    capabilities=v4_capabilities,
                    result_json=cancel_result,
                )
                if terminal_written:
                    reconciled_parent = await _reconcile_multi_agent_child_terminal_state(
                        conn,
                        payload=payload,
                        child_status="cancelled",
                        result_json=cancel_result,
                    )
                    await release_runtime_sandbox_lease(conn, reason="run_cancelled")
                    terminal_outcome = WorkerOutcome("cancelled", payload.run_id)
                else:
                    terminal_outcome = WorkerOutcome(
                        "skipped",
                        payload.run_id,
                        "stale_terminal_state",
                        "Run already reached a terminal state",
                    )
            elif blocked_reason == "tool_permission_pending":
                blocked_result_payload = {
                    **result_payload,
                    "message": "A pending tool-permission request blocked successful completion.",
                    "error_code": "tool_permission_pending",
                    "artifacts": [],
                }
                terminal_written, reconciled_parent = await _fail_run_and_reconcile_with_write(
                    conn,
                    v4_capabilities=v4_capabilities,
                    payload=payload,
                    tenant_id=payload.tenant_id,
                    run_id=payload.run_id,
                    error_code="tool_permission_pending",
                    error_message="A pending tool-permission request blocked successful completion.",
                    result_json=blocked_result_payload,
                    attempt_lifecycle=attempt_lifecycle,
                )
                if not terminal_written:
                    terminal_outcome = WorkerOutcome(
                        "skipped",
                        payload.run_id,
                        "stale_terminal_state",
                        "Run already reached a terminal state",
                    )
                else:
                    await repositories.append_event(
                        conn,
                        tenant_id=payload.tenant_id,
                        run_id=payload.run_id,
                        event_type="error",
                        stage="worker",
                        message="Run failed",
                        payload={"artifact_count": 0, "visible_to_user": False},
                    )
                    await release_runtime_sandbox_lease(conn, reason="run_failed")
                    terminal_outcome = WorkerOutcome(
                        "failed",
                        payload.run_id,
                        "tool_permission_pending",
                        "A pending tool-permission request blocked successful completion.",
                    )
            else:
                terminal_outcome = WorkerOutcome(
                    "skipped",
                    payload.run_id,
                    "stale_terminal_state",
                    "Run already reached a terminal state",
                )
    finally:
        await cleanup_runtime_sandbox_lease_after_interruption()
    if terminal_outcome.status == "skipped":
        terminalization_progress = await attempt_lifecycle.drain(
            capabilities=v4_capabilities,
            transaction_factory=transaction_factory,
            error_code=terminal_outcome.error_code,
        )
        if (
            terminalization_progress is not None
            and terminalization_progress.get("did_transition")
            and terminalization_progress.get("needs_reconcile")
        ):
            await reconcile_terminalized_permission_run(
                tenant_id=payload.tenant_id,
                run_id=payload.run_id,
                progress=terminalization_progress,
                transaction_factory=transaction_factory,
                attempt_lifecycle=run_attempt_lifecycle,
            )
        if terminalization_progress and terminalization_progress.get("completed") is True:
            final_status = str(terminalization_progress.get("status") or "")
            if final_status in {"failed", "cancelled"}:
                terminal_outcome = WorkerOutcome(
                    final_status,
                    payload.run_id,
                    terminal_outcome.error_code if final_status == "failed" else None,
                    terminal_outcome.error_message if final_status == "failed" else None,
                )
    await finalize_parent_and_publish(
        transaction_factory,
        v4_capabilities,
        finalize_multi_agent_parent,
        payload,
        reconciled_parent,
    )
    return terminal_outcome


async def reconcile_executor_terminal_result(
    *,
    lease_row: dict[str, Any],
    result: ExecutorResult,
    registry: AdapterRegistry | None = None,
    worker_id: str | None = None,
    claim_token: str,
    transaction_factory: Any | None = None,
    v4_capabilities: WorkerV4Capabilities,
    run_attempt_lifecycle: RunAttemptLifecycleService,
) -> WorkerOutcome:
    queue_payload, attempt_id = _restored_executor_reconciliation_queue_payload(
        lease_row.get("executor_reconciliation_context_json"),
        result=result.result,
        run_payload_factory=RunPayload,
        queue_payload_factory=QueueRunPayload,
    )
    return await process_run_payload(
        {
            **queue_payload.model_dump(mode="json"),
            "_queue_attempt_id": attempt_id,
        },
        registry,
        worker_id=worker_id,
        reconciliation=WorkerExecutorReconciliation(result, lease_row, claim_token),
        transaction_factory=transaction_factory,
        v4_capabilities=v4_capabilities,
        run_attempt_lifecycle=run_attempt_lifecycle,
    )
