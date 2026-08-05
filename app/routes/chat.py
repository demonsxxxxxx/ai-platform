import hashlib
import logging
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.exception_handlers import (
    http_exception_handler,
    request_validation_exception_handler,
)
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from starlette.responses import PlainTextResponse

from app import repositories
from app.agent_profiles import (
    reauthorize_pinned_run_for_replay,
    resolve_bound_profile_for_submission,
    resolve_profile_for_admission,
)
from app.auth import AuthPrincipal, is_ai_admin, require_principal
from app.capability_distribution import (
    CapabilityAccessDecision,
    CapabilityAuthorizationDenial,
)
from app.context_builder import record_initial_context_snapshot
from app.context.file_continuity import has_file_input_mode, primary_file_ids_for_run
from app.control_plane_contracts import sanitize_public_text, standard_trace_id
from app.db import transaction
from app.intent_router import (
    FileSummary,
    classify_execution_polarity,
    fallback_to_general_chat,
    route_intent,
)
from app.model_catalog import resolve_model_selection
from app.models import (
    CapabilitySuggestionResponse,
    AgentConversationIdentity,
    ChatMessageResponse,
    ChatMessagesResponse,
    ChatSessionRequest,
    ChatSessionResponse,
    ChatSessionsResponse,
    ChatStreamRequest,
    ChatStreamResponse,
    ChatSubmissionPreLedgerAbsenceResponse,
    ChatSubmissionResponse,
    IntentDecisionResponse,
    QueueRunPayload,
    SelectedAgentProfileRequest,
    SelectedSkillRequest,
)
from app.product_events import initial_run_event_specs, intent_event_specs
from app.projection_redaction import (
    capability_id_from_skill,
    default_skill_id_for_public_agent,
    internal_agent_id_for_request,
    public_agent_id_for_projection,
    public_skill_display_label,
    sanitize_user_control_input,
)
from app.queue import (
    QueueAdmissionMetadata,
    QueueAdmissionRejected,
    enqueue_run,
    enqueue_run_with_metadata,
    get_queue_insight,
    read_queue_admission,
)
from app.queue_payload_validation import queue_payload_invalid_detail
from app.repositories import RepositoryConflictError, RepositoryNotFoundError
from app.required_tool_contract import (
    attach_required_tool_declaration,
    declaration_from_input,
    public_required_tool_detail,
)
from app.run_admission_policy import (
    PLATFORM_MULTI_AGENT_NOT_SUPPORTED,
    contains_persisted_platform_multi_agent_control,
    contains_platform_multi_agent_control,
)
from app.run_admission_terminalization import terminalize_retired_platform_multi_agent_run
from app.settings import get_settings
from app.skills.lifecycle import is_user_runnable_status
from app.skills.pinning import (
    SkillVersionMaterializationError,
    attach_skill_snapshot_governance,
    build_skill_manifest_pins,
    build_skill_version_policy_manifest_pins,
    governed_locked_skill_version,
)
from app.skills.registry import BuiltinSkillRegistry
from app.skills.release_policy import (
    release_decision_payload_for_locked_version,
    resolve_rollout_skill_decision,
)
from app.validation import assert_safe_principal_user_id

router = APIRouter()
logger = logging.getLogger(__name__)
_MISSING = object()
_ORIGINAL_ENQUEUE_RUN = enqueue_run
_CHAT_SUBMISSION_RESOLUTION_CACHE_CONTROL = "private, no-store"
_PRELEDGER_RECOVERY_REJECTION_CODE = "chat_submission_retired_before_ledger"
_REQUIRED_CAPABILITY_UNAVAILABLE_CODE = "required_capability_unavailable"
_SAFE_SUBMISSION_DETAIL_CODES = frozenset({_REQUIRED_CAPABILITY_UNAVAILABLE_CODE})


class _ChatSubmissionNoStoreRoute(APIRoute):
    """Make every resolver response non-cacheable without widening router scope."""

    def get_route_handler(self):  # type: ignore[override]
        original_handler = super().get_route_handler()

        async def no_store_handler(request: Request) -> Response:
            try:
                response = await original_handler(request)
            except HTTPException as exc:
                response = await http_exception_handler(request, exc)
            except RequestValidationError as exc:
                response = await request_validation_exception_handler(request, exc)
            except Exception:
                logger.exception("chat submission resolver failed unexpectedly")
                response = PlainTextResponse("Internal Server Error", status_code=500)
            response.headers["Cache-Control"] = _CHAT_SUBMISSION_RESOLUTION_CACHE_CONTROL
            return response

        return no_store_handler


def _chat_submission_http_error(*, status_code: int, code: str) -> HTTPException:
    """Return the sole server-controlled pre-persistence rejection signal."""

    detail = {
        "code": code,
        "submission_disposition": "rejected_before_persist",
    }
    if code == _REQUIRED_CAPABILITY_UNAVAILABLE_CODE:
        detail.update(public_required_tool_detail("unavailable"))
    return HTTPException(
        status_code=status_code,
        detail=detail,
    )


def _submission_code(detail: object, fallback: str = "chat_submission_rejected") -> str:
    if isinstance(detail, dict) and isinstance(detail.get("code"), str):
        return str(detail["code"])
    if isinstance(detail, dict) and detail.get("detail_code") in _SAFE_SUBMISSION_DETAIL_CODES:
        return str(detail["detail_code"])
    if isinstance(detail, str) and detail:
        return detail
    return fallback


def _canonical_pre_persistence_rejection_fingerprint(
    *,
    request: ChatStreamRequest,
    principal: AuthPrincipal,
    query_agent_id: str | None,
    code: str,
) -> str:
    """Hash the complete rejected request through the authoritative ledger contract."""

    return repositories.chat_submission_fingerprint(
        {
            "request": request.model_dump(mode="json", exclude={"submission_id"}),
            "query_agent_id": query_agent_id,
            "rejection_code": code,
        },
        tenant_id=principal.tenant_id,
        user_id=principal.user_id,
    )


def _chat_stream_response_from_submission(row: dict[str, Any]) -> ChatStreamResponse:
    state = str(row.get("state") or "")
    if state == "admission_rejected":
        raise HTTPException(
            status_code=409,
            detail=str(row.get("rejection_code") or PLATFORM_MULTI_AGENT_NOT_SUPPORTED),
        )
    if state == "rejected_before_persist":
        code = str(row.get("rejection_code") or "chat_submission_rejected")
        raise _chat_submission_http_error(
            status_code=403 if code == _REQUIRED_CAPABILITY_UNAVAILABLE_CODE else 409,
            code=code,
        )
    if state == "enqueue_failed":
        raise HTTPException(status_code=503, detail="queue_enqueue_failed")
    outcome = row.get("outcome_json")
    if isinstance(outcome, dict) and outcome:
        return ChatStreamResponse.model_validate(outcome)
    if state == "accepted_pending_enqueue" and row.get("session_id") and row.get("run_id"):
        return ChatStreamResponse(
            session_id=str(row["session_id"]),
            run_id=str(row["run_id"]),
            status="accepted_pending_enqueue",
            submission_id=str(row["submission_id"]),
        )
    raise HTTPException(status_code=409, detail="chat_submission_unresolved")


def _chat_submission_resolution(row: dict[str, Any]) -> ChatSubmissionResponse:
    if str(row.get("state") or "") == "admission_rejected":
        raise HTTPException(
            status_code=409,
            detail=str(row.get("rejection_code") or PLATFORM_MULTI_AGENT_NOT_SUPPORTED),
        )
    outcome = row.get("outcome_json")
    return ChatSubmissionResponse(
        submission_id=str(row["submission_id"]),
        state=str(row.get("state") or "accepted_pending_enqueue"),
        submission_disposition=(
            "rejected_before_persist"
            if row.get("submission_disposition") == "rejected_before_persist"
            else None
        ),
        rejection_code=str(row["rejection_code"]) if row.get("rejection_code") else None,
        outcome=ChatStreamResponse.model_validate(outcome) if isinstance(outcome, dict) and outcome else None,
    )


def _require_chat_submission_admitted(resolution: ChatSubmissionResponse) -> ChatSubmissionResponse:
    if resolution.state == "admission_rejected":
        raise HTTPException(
            status_code=409,
            detail=resolution.rejection_code or PLATFORM_MULTI_AGENT_NOT_SUPPORTED,
        )
    return resolution


async def _resolve_chat_submission(
    *,
    principal: AuthPrincipal,
    submission_id: str,
) -> ChatSubmissionResponse | None:
    """Read one principal-scoped durable ledger row without changing it."""

    async with transaction() as conn:
        submission = await repositories.get_chat_submission(
            conn,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            submission_id=submission_id,
        )
    if submission is None:
        return None
    return _chat_submission_resolution(submission)


def _preledger_recovery_fingerprint(principal: AuthPrincipal) -> str:
    """Return the reserved principal-scoped fingerprint for a recovery tombstone."""

    return repositories.chat_submission_fingerprint(
        {
            "submission_protocol": "chat_submission_resolution.v2",
            "recovery": "retire_absent_before_ledger",
        },
        tenant_id=principal.tenant_id,
        user_id=principal.user_id,
    )


def _is_preledger_recovery_tombstone(
    row: dict[str, Any],
    *,
    principal: AuthPrincipal,
) -> bool:
    """Recognize only the reserved durable record created by recovery POST."""

    return (
        str(row.get("state") or "") == "rejected_before_persist"
        and row.get("submission_disposition") == "rejected_before_persist"
        and row.get("rejection_code") == _PRELEDGER_RECOVERY_REJECTION_CODE
        and row.get("request_fingerprint_sha256")
        == _preledger_recovery_fingerprint(principal)
    )


async def _recover_preledger_chat_submission(
    *,
    principal: AuthPrincipal,
    submission_id: str,
) -> ChatSubmissionResponse | ChatSubmissionPreLedgerAbsenceResponse:
    """Atomically resolve a row or retire an absent key before a late POST can win."""

    recovery_fingerprint = _preledger_recovery_fingerprint(principal)
    async with transaction() as conn:
        await repositories.ensure_submission_principal(
            conn,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            display_name=principal.display_name,
        )
        row, created = await repositories.claim_chat_submission(
            conn,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            submission_id=submission_id,
            workspace_id=None,
            request_fingerprint_sha256=recovery_fingerprint,
        )
        if created:
            await repositories.finalize_chat_submission(
                conn,
                tenant_id=principal.tenant_id,
                user_id=principal.user_id,
                submission_id=submission_id,
                state="rejected_before_persist",
                submission_disposition="rejected_before_persist",
                rejection_code=_PRELEDGER_RECOVERY_REJECTION_CODE,
            )
            return ChatSubmissionPreLedgerAbsenceResponse(submission_id=submission_id)
        if _is_preledger_recovery_tombstone(row, principal=principal):
            return ChatSubmissionPreLedgerAbsenceResponse(submission_id=submission_id)
        return _chat_submission_resolution(row)


async def _persist_pre_persistence_rejection(
    *,
    request: ChatStreamRequest,
    principal: AuthPrincipal,
    submission_id: str | None,
    query_agent_id: str | None,
    workspace_id: str | None,
    session_id: str | None,
    code: str,
) -> None:
    """Record a deterministic rejection after the mutation transaction rolled back."""

    if submission_id is None:
        return
    request_fingerprint = _canonical_pre_persistence_rejection_fingerprint(
        request=request,
        principal=principal,
        query_agent_id=query_agent_id,
        code=code,
    )
    async with transaction() as conn:
        await repositories.ensure_submission_principal(
            conn,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            display_name=principal.display_name,
        )
        effective_workspace_id = workspace_id
        if session_id:
            continuation_session = await repositories.get_authorized_session(
                conn,
                tenant_id=principal.tenant_id,
                user_id=principal.user_id,
                session_id=session_id,
            )
            saved_workspace_id = continuation_session.get("workspace_id") if continuation_session else None
            if isinstance(saved_workspace_id, str) and saved_workspace_id:
                effective_workspace_id = saved_workspace_id
        row, created = await repositories.claim_chat_submission(
            conn,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            submission_id=submission_id,
            workspace_id=effective_workspace_id,
            request_fingerprint_sha256=request_fingerprint,
        )
        if not created and _is_preledger_recovery_tombstone(row, principal=principal):
            raise _chat_submission_http_error(
                status_code=409,
                code=_PRELEDGER_RECOVERY_REJECTION_CODE,
            )
        if not created and row.get("request_fingerprint_sha256") != request_fingerprint:
            raise HTTPException(status_code=409, detail="submission_payload_mismatch")
        if not created and row.get("state") == "rejected_before_persist":
            if (
                code == _REQUIRED_CAPABILITY_UNAVAILABLE_CODE
                and row.get("rejection_code") == code
            ):
                return
            raise _chat_submission_http_error(
                status_code=409,
                code=str(row.get("rejection_code") or "chat_submission_rejected"),
            )
        if created or row.get("state") == "resolving":
            await repositories.finalize_chat_submission(
                conn,
                tenant_id=principal.tenant_id,
                user_id=principal.user_id,
                submission_id=submission_id,
                state="rejected_before_persist",
                workspace_id=effective_workspace_id,
                submission_disposition="rejected_before_persist",
                rejection_code=code,
            )


async def _load_existing_chat_submission(
    *,
    principal: AuthPrincipal,
    submission_id: str | None,
    request_fingerprint: str | None,
) -> ChatStreamResponse | None:
    if submission_id is None or request_fingerprint is None:
        return None
    async with transaction() as conn:
        existing = await repositories.get_chat_submission(
            conn,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            submission_id=submission_id,
        )
    if existing is None:
        return None
    if _is_preledger_recovery_tombstone(existing, principal=principal):
        return _chat_stream_response_from_submission(existing)
    if existing.get("request_fingerprint_sha256") != request_fingerprint:
        raise HTTPException(status_code=409, detail="submission_payload_mismatch")
    return _chat_stream_response_from_submission(existing)


async def _admit_chat_submission(
    *,
    principal: AuthPrincipal,
    submission_id: str,
) -> ChatSubmissionResponse:
    """Admit one already-persisted run without replaying chat creation work."""

    profile_bound = False
    profile_resolution: ChatSubmissionResponse | None = None
    profile_enqueue_error: Exception | None = None
    async with transaction() as conn:
        submission = await repositories.get_chat_submission(
            conn,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            submission_id=submission_id,
            for_update=True,
        )
        if submission is None:
            raise HTTPException(status_code=404, detail="chat_submission_not_found")
        if str(submission.get("state")) in {
            "admission_rejected",
            "rejected_before_persist",
            "enqueue_failed",
            "needs_confirmation",
        }:
            return _chat_submission_resolution(submission)
        run_id = str(submission.get("run_id") or "")
        if not run_id:
            return _chat_submission_resolution(submission)
        run = await repositories.get_authorized_run(
            conn,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            run_id=run_id,
            for_update=True,
        )
        if run is None:
            raise HTTPException(status_code=404, detail="run_not_found")
        execution_snapshot: dict[str, Any] | None = None
        if str(run.get("status") or "") == "queued":
            execution_snapshot = repositories.copied_run_execution_snapshot(run.get("input_json"))
        retired_control_rejected = (
            str(run.get("error_code") or "") == PLATFORM_MULTI_AGENT_NOT_SUPPORTED
            or (
                execution_snapshot is not None
                and contains_persisted_platform_multi_agent_control(run.get("input_json"))
            )
        )
        if retired_control_rejected:
            if str(run.get("error_code") or "") != PLATFORM_MULTI_AGENT_NOT_SUPPORTED:
                await terminalize_retired_platform_multi_agent_run(
                    conn,
                    tenant_id=principal.tenant_id,
                    run_id=run_id,
                )
            await repositories.finalize_chat_submission(
                conn,
                tenant_id=principal.tenant_id,
                user_id=principal.user_id,
                submission_id=submission_id,
                state="admission_rejected",
                rejection_code=PLATFORM_MULTI_AGENT_NOT_SUPPORTED,
            )
            return ChatSubmissionResponse(
                submission_id=submission_id,
                state="admission_rejected",
                rejection_code=PLATFORM_MULTI_AGENT_NOT_SUPPORTED,
            )
        if str(run.get("status") or "") != "queued":
            if str(run.get("error_code") or "") == "queue_enqueue_failed":
                if str(submission.get("state")) != "enqueue_failed":
                    await repositories.finalize_chat_submission(
                        conn,
                        tenant_id=principal.tenant_id,
                        user_id=principal.user_id,
                        submission_id=submission_id,
                        state="enqueue_failed",
                        rejection_code="queue_enqueue_failed",
                    )
                    submission["state"] = "enqueue_failed"
                    submission["rejection_code"] = "queue_enqueue_failed"
                return _chat_submission_resolution(submission)
            if str(submission.get("state")) != "queued":
                outcome = _chat_stream_response_from_submission(submission)
                queued_outcome = outcome.model_copy(update={"status": "queued"})
                await repositories.finalize_chat_submission(
                    conn,
                    tenant_id=principal.tenant_id,
                    user_id=principal.user_id,
                    submission_id=submission_id,
                    state="queued",
                    outcome_json=queued_outcome.model_dump(mode="json"),
                )
                submission["state"] = "queued"
                submission["outcome_json"] = queued_outcome.model_dump(mode="json")
            return _chat_submission_resolution(submission)
        if execution_snapshot is None:
            raise HTTPException(status_code=409, detail="chat_submission_not_admitted")
        queue_payload = _validate_queue_payload_for_enqueue(
            {
                "tenant_id": principal.tenant_id,
                "workspace_id": str(run["workspace_id"]),
                "user_id": principal.user_id,
                "session_id": str(run["session_id"]),
                "run_id": run_id,
                "agent_id": str(run["agent_id"]),
                "skill_id": str(run["skill_id"]),
                **execution_snapshot,
            }
        )
        profile_revision, _profile_hash = repositories.admitted_agent_profile_pins_for_copy(
            run,
            execution_snapshot,
        )
        profile_bound = profile_revision is not None
        if profile_bound:
            # Run creation committed before this fresh authority transaction.
            # Keep its run/profile locks through Redis admission so workers can
            # see the run but lifecycle writers cannot overtake admission.
            await reauthorize_pinned_run_for_replay(
                conn,
                principal=principal,
                run_id=run_id,
            )
            queue_admission, profile_enqueue_error = await _attempt_chat_queue_admission(
                queue_payload,
                check_existing=True,
            )
            if queue_admission is None:
                if profile_enqueue_error is not None and _is_definitive_chat_queue_rejection(
                    profile_enqueue_error
                ):
                    await repositories.mark_run_enqueue_failed(
                        conn,
                        tenant_id=principal.tenant_id,
                        user_id=principal.user_id,
                        run_id=run_id,
                        trace_id=str(run.get("trace_id") or standard_trace_id(run_id)),
                    )
                    await repositories.finalize_chat_submission(
                        conn,
                        tenant_id=principal.tenant_id,
                        user_id=principal.user_id,
                        submission_id=submission_id,
                        state="enqueue_failed",
                        rejection_code="queue_enqueue_failed",
                    )
                    submission["state"] = "enqueue_failed"
                    submission["rejection_code"] = "queue_enqueue_failed"
                profile_resolution = _chat_submission_resolution(submission)
            else:
                prior_outcome = _chat_stream_response_from_submission(submission)
                queued_outcome = prior_outcome.model_copy(
                    update={
                        "status": "queued",
                        "queue_position": int(queue_admission.queue_position) or None,
                        "submission_id": submission_id,
                    }
                )
                if str(submission.get("state")) != "queued":
                    await _persist_chat_queue_success(
                        conn,
                        principal=principal,
                        run_id=run_id,
                        queue_admission=queue_admission,
                        outcome=queued_outcome,
                        submission_id=submission_id,
                    )
                    submission["state"] = "queued"
                    submission["outcome_json"] = queued_outcome.model_dump(mode="json")
                profile_resolution = _chat_submission_resolution(submission)

    if profile_bound:
        if profile_enqueue_error is not None and _is_definitive_chat_queue_rejection(
            profile_enqueue_error
        ):
            raise HTTPException(status_code=503, detail="queue_enqueue_failed") from profile_enqueue_error
        if profile_resolution is None:
            raise HTTPException(status_code=409, detail="chat_submission_not_admitted")
        return profile_resolution

    queue_admission, enqueue_error = await _attempt_chat_queue_admission(
        queue_payload,
        check_existing=True,
    )
    if enqueue_error is not None and queue_admission is None:
        exc = enqueue_error
        async with transaction() as conn:
            current_submission = await repositories.get_chat_submission(
                conn,
                tenant_id=principal.tenant_id,
                user_id=principal.user_id,
                submission_id=submission_id,
                for_update=True,
            )
            if current_submission is None:
                raise HTTPException(status_code=404, detail="chat_submission_not_found")
            # Never replace a concurrent success (or a previously settled
            # terminal result) with a local enqueue conclusion.
            if str(current_submission.get("state")) != "accepted_pending_enqueue":
                return _chat_submission_resolution(current_submission)
            if not _is_definitive_chat_queue_rejection(exc):
                # The outcome remains unknown.  The durable submission is
                # recoverable through retry-admission and immutable Redis
                # idempotency, without this request posting again.
                return _chat_submission_resolution(current_submission)
            current_run = await repositories.get_authorized_run(
                conn,
                tenant_id=principal.tenant_id,
                user_id=principal.user_id,
                run_id=run_id,
                for_update=True,
            )
            if current_run is None:
                raise HTTPException(status_code=404, detail="run_not_found")
            if str(current_run.get("status") or "") != "queued":
                return _chat_submission_resolution(current_submission)
            # Only the queue module's deterministic pre-admission rejection
            # can produce enqueue_failed.  This transaction is distinct from
            # planning and commits before the HTTP error.
            await repositories.mark_run_enqueue_failed(
                conn,
                tenant_id=principal.tenant_id,
                user_id=principal.user_id,
                run_id=run_id,
                trace_id=str(current_run.get("trace_id") or standard_trace_id(run_id)),
            )
            await repositories.finalize_chat_submission(
                conn,
                tenant_id=principal.tenant_id,
                user_id=principal.user_id,
                submission_id=submission_id,
                state="enqueue_failed",
                rejection_code="queue_enqueue_failed",
            )
        raise HTTPException(status_code=503, detail="queue_enqueue_failed") from exc

    # Record the queue identity in a fresh transaction as well.  This can be
    # retried from the durable submission if an acknowledgement write fails.
    async with transaction() as conn:
        submission = await repositories.get_chat_submission(
            conn,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            submission_id=submission_id,
            for_update=True,
        )
        if submission is None:
            raise HTTPException(status_code=404, detail="chat_submission_not_found")
        if str(submission.get("state")) in {
            "admission_rejected",
            "rejected_before_persist",
            "enqueue_failed",
            "needs_confirmation",
        }:
            return _chat_submission_resolution(submission)
        prior_outcome = _chat_stream_response_from_submission(submission)
        queued_outcome = prior_outcome.model_copy(
            update={
                "status": "queued",
                "queue_position": int(queue_admission.queue_position) or None,
                "submission_id": submission_id,
            }
        )
        if str(submission.get("state")) != "queued":
            await repositories.append_event(
                conn,
                tenant_id=principal.tenant_id,
                run_id=run_id,
                event_type="queued",
                stage="queue",
                message="任务队列接纳完成",
                payload={
                    "visible_to_user": False,
                    "source": "admin_runtime_queue",
                    "queue_position": int(queue_admission.queue_position) or None,
                    "queue_admission_ordinal": int(queue_admission.queue_admission_ordinal) or None,
                    "queue_probe_source": str(queue_admission.source),
                },
            )
            await repositories.finalize_chat_submission(
                conn,
                tenant_id=principal.tenant_id,
                user_id=principal.user_id,
                submission_id=submission_id,
                state="queued",
                outcome_json=queued_outcome.model_dump(mode="json"),
                queue_position=int(queue_admission.queue_position) or None,
                queue_admission_ordinal=int(queue_admission.queue_admission_ordinal) or None,
                queue_message_id=queue_admission.message_id,
            )
            submission["state"] = "queued"
            submission["outcome_json"] = queued_outcome.model_dump(mode="json")
        return _chat_submission_resolution(submission)


async def _audit_capability_denial(
    principal: AuthPrincipal,
    error: repositories.RepositoryAuthorizationError,
    *,
    source: str,
) -> None:
    if error.denial is None:
        return
    audit_error = error
    denial = error.denial
    if denial.capability_kind == "mcp_tool":
        digest = hashlib.sha256()
        digest.update(b"ai-platform.chat-mcp-denial-audit.v1\x00")
        digest.update(denial.capability_id.encode("utf-8"))
        public_capability_id = f"mcp_tool_sha256:{digest.hexdigest()}"
        audit_error = repositories.RepositoryAuthorizationError(
            str(error),
            denial=CapabilityAuthorizationDenial(
                capability_kind=denial.capability_kind,
                capability_id=public_capability_id,
                actor_department_id=denial.actor_department_id,
                actor_roles=denial.actor_roles,
                department_scope_ids=denial.department_scope_ids,
                role_scope_ids=denial.role_scope_ids,
                scope_mode=denial.scope_mode,
                decision_reason=denial.decision_reason,
                admin_bypass=denial.admin_bypass,
            ),
        )
    async with transaction() as conn:
        await repositories.append_capability_authorization_denial_audit(
            conn,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            error=audit_error,
            source=source,
        )


def _skill_manifest_pins(skill_id: str, input_payload: dict[str, Any]) -> list[dict[str, Any]]:
    settings = get_settings()
    try:
        return build_skill_manifest_pins(
            skill_id=skill_id,
            input_payload=input_payload,
            builtin_skills=BuiltinSkillRegistry(settings.platform_skills_root).list_builtin_skills(),
        )
    except ValueError as exc:
        raise SkillVersionMaterializationError("skill_version_not_materializable") from exc


def _available_builtin_skill_ids_for_policy() -> set[str]:
    settings = get_settings()
    try:
        return {skill.name for skill in BuiltinSkillRegistry(settings.platform_skills_root).list_builtin_skills()}
    except ValueError as exc:
        raise SkillVersionMaterializationError("skill_version_not_materializable") from exc


async def _governed_skill_manifest_pins(
    conn,
    *,
    skill_id: str,
    input_payload: dict[str, Any],
    release_policy_version: object | None,
) -> list[dict[str, Any]]:
    policy_version = str(release_policy_version or "")
    if policy_version:
        version = await repositories.get_effective_skill_version_for_policy(
            conn,
            skill_id=skill_id,
            version=policy_version,
        )
        if version is None:
            raise SkillVersionMaterializationError("skill_version_not_materializable")
        if not is_user_runnable_status(version.get("status")):
            raise SkillVersionMaterializationError("skill_version_not_materializable")
        return build_skill_version_policy_manifest_pins(
            version,
            available_skill_ids=_available_builtin_skill_ids_for_policy(),
        )
    return _skill_manifest_pins(skill_id, input_payload)


def _release_decision_event_payload(release_decision: dict[str, Any], *, skill_id: str) -> dict[str, Any]:
    return {
        **release_decision,
        "skill_id": skill_id,
        "skill_version": release_decision.get("selected_version"),
        "visible_to_user": False,
    }


def _validate_queue_payload_for_enqueue(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        return QueueRunPayload.model_validate(payload).model_dump(mode="json")
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=queue_payload_invalid_detail(exc)) from exc


async def _enqueue_chat_run(queue_payload: dict[str, Any]):
    if enqueue_run is not _ORIGINAL_ENQUEUE_RUN:
        queue_position = await enqueue_run(queue_payload)
        return QueueAdmissionMetadata(
            queue_position=int(queue_position),
            queue_admission_ordinal=int(queue_position),
            message_id="",
        )
    return await enqueue_run_with_metadata(queue_payload)


async def _attempt_chat_queue_admission(
    queue_payload: dict[str, Any],
    *,
    check_existing: bool,
) -> tuple[QueueAdmissionMetadata | None, Exception | None]:
    """Perform one deterministic enqueue and boundedly reconcile an ambiguous write."""

    try:
        if check_existing:
            existing = await read_queue_admission(queue_payload)
            if existing is not None:
                return existing, None
        return await _enqueue_chat_run(queue_payload), None
    except Exception as exc:  # noqa: BLE001 - preserve an unknown external queue outcome
        if not isinstance(exc, QueueAdmissionRejected):
            try:
                existing = await read_queue_admission(queue_payload)
            except Exception:  # noqa: BLE001 - bounded best-effort reconciliation only
                existing = None
            if existing is not None:
                return existing, None
        return None, exc


def _is_definitive_chat_queue_rejection(error: Exception) -> bool:
    """Return true only for a locally invalid immutable queue payload."""

    return isinstance(error, QueueAdmissionRejected) and str(error) == "queue_payload_invalid"


async def _persist_chat_queue_success(
    conn,
    *,
    principal: AuthPrincipal,
    run_id: str,
    queue_admission: QueueAdmissionMetadata,
    outcome: ChatStreamResponse,
    submission_id: str | None,
) -> None:
    """Record one queue admission after the durable run is committed."""

    queue_position = int(queue_admission.queue_position)
    queue_ordinal = int(queue_admission.queue_admission_ordinal)
    await repositories.append_event(
        conn,
        tenant_id=principal.tenant_id,
        run_id=run_id,
        event_type="queued",
        stage="queue",
        message="任务队列接纳完成",
        payload={
            "visible_to_user": False,
            "source": "admin_runtime_queue",
            "queue_position": queue_position or None,
            "queue_admission_ordinal": queue_ordinal or None,
            "queue_probe_source": str(queue_admission.source),
        },
    )
    if submission_id is not None:
        await repositories.finalize_chat_submission(
            conn,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            submission_id=submission_id,
            state="queued",
            outcome_json=outcome.model_dump(mode="json"),
            queue_position=queue_position or None,
            queue_admission_ordinal=queue_ordinal or None,
            queue_message_id=queue_admission.message_id,
        )

def _strip_server_owned_control_metadata(input_payload: object, *, redact_public: bool = False) -> dict[str, Any]:
    return repositories.normalize_run_input_for_enqueue(input_payload, redact_public=redact_public)


def _file_ids_from_request(request: ChatStreamRequest) -> list[str]:
    if request.file_ids:
        return request.file_ids
    file_ids: list[str] = []
    for attachment in request.attachments:
        value = attachment.get("file_id") or attachment.get("key") or attachment.get("id")
        if isinstance(value, str) and value.startswith("file_"):
            file_ids.append(value)
    return file_ids


def _has_legacy_client_mcp_selector(value: object) -> bool:
    """Reject client-owned MCP selectors; Chat accepts the structured field only."""

    if not isinstance(value, dict):
        return False
    return "mcp_tool_ids" in value or "mcpToolIds" in value


def _requested_model_selection(request: ChatStreamRequest) -> dict[str, str] | None:
    agent_options = request.agent_options if isinstance(request.agent_options, dict) else {}
    raw_model_id = agent_options.get("model_id")
    if raw_model_id is None:
        return None
    try:
        return resolve_model_selection(str(raw_model_id), get_settings())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="model_id_not_available") from exc


def _file_ids_for_intent_lookup(request: ChatStreamRequest) -> list[str]:
    file_ids: list[str] = []
    for value in request.file_ids:
        if value not in file_ids:
            file_ids.append(value)
    for attachment in request.attachments:
        value = attachment.get("file_id") or attachment.get("key") or attachment.get("id")
        if isinstance(value, str) and value.startswith("file_") and value not in file_ids:
            file_ids.append(value)
    return file_ids


def _row_value(row: dict[str, Any], key: str, default: Any = None) -> Any:
    if hasattr(row, "get"):
        return row.get(key, default)
    try:
        return row[key]
    except (KeyError, TypeError):
        return default


def _file_row_matches_request_scope(
    row: dict[str, Any],
    request: ChatStreamRequest,
    principal: AuthPrincipal,
    *,
    workspace_id: str,
) -> bool:
    tenant_id = _row_value(row, "tenant_id", _MISSING)
    if tenant_id != principal.tenant_id:
        return False
    row_workspace_id = _row_value(row, "workspace_id", _MISSING)
    if row_workspace_id != workspace_id:
        return False
    user_id = _row_value(row, "user_id", _MISSING)
    if user_id != principal.user_id:
        return False
    session_id = _row_value(row, "session_id", _MISSING)
    if session_id is _MISSING:
        return False
    if session_id and session_id != request.session_id:
        return False
    run_id = _row_value(row, "run_id", _MISSING)
    return not (run_id is _MISSING or run_id)


def _file_summaries_from_request(request: ChatStreamRequest) -> list[FileSummary]:
    summaries: list[FileSummary] = []
    for attachment in request.attachments:
        value = attachment.get("file_id") or attachment.get("key") or attachment.get("id") or ""
        summaries.append(
            FileSummary(
                file_id=str(value),
                name=str(attachment.get("name") or attachment.get("filename") or ""),
                content_type=str(attachment.get("mimeType") or attachment.get("mime_type") or ""),
            )
        )
    return summaries


def _merge_file_summary(existing: FileSummary, incoming: FileSummary) -> FileSummary:
    return FileSummary(
        file_id=existing.file_id or incoming.file_id,
        name=existing.name or incoming.name,
        content_type=existing.content_type or incoming.content_type,
    )


def _merge_file_summaries(summaries: list[FileSummary], incoming: FileSummary) -> list[FileSummary]:
    if not incoming.file_id:
        return [*summaries, incoming]
    merged: list[FileSummary] = []
    replaced = False
    for item in summaries:
        if item.file_id == incoming.file_id:
            merged.append(_merge_file_summary(item, incoming))
            replaced = True
        else:
            merged.append(item)
    if not replaced:
        merged.append(incoming)
    return merged


def _file_summary_from_row(file_id: str, row: dict[str, Any]) -> FileSummary:
    return FileSummary(
        file_id=str(_row_value(row, "id") or file_id),
        name=str(_row_value(row, "original_name") or _row_value(row, "name") or ""),
        content_type=str(_row_value(row, "content_type") or _row_value(row, "mime_type") or ""),
    )


async def _file_summaries_for_intent(
    conn,
    request: ChatStreamRequest,
    principal: AuthPrincipal,
    *,
    workspace_id: str,
) -> list[FileSummary]:
    summaries = _file_summaries_from_request(request)
    for file_id in _file_ids_for_intent_lookup(request):
        existing = next((item for item in summaries if item.file_id == file_id), None)
        if existing and (existing.name or existing.content_type):
            continue
        row = await repositories.get_file(conn, tenant_id=principal.tenant_id, file_id=file_id)
        if not row or not _file_row_matches_request_scope(
            row, request, principal, workspace_id=workspace_id
        ):
            continue
        summaries = _merge_file_summaries(summaries, _file_summary_from_row(file_id, row))
    return summaries


def _intent_response(payload: dict[str, object], principal: AuthPrincipal) -> IntentDecisionResponse:
    response_payload = dict(payload)
    if not is_ai_admin(principal):
        response_payload["agent_id"] = public_agent_id_for_projection(
            response_payload.get("agent_id"),
            response_payload.get("skill_id"),
        )
        response_payload["skill_id"] = None
    return IntentDecisionResponse.model_validate(response_payload)


def _normalized_query_agent_id(agent_id: str | None) -> str | None:
    return agent_id if isinstance(agent_id, str) and agent_id else None


def _normalize_request_selector(
    agent_id: str,
    skill_id: str | None,
    *,
    allow_raw_skill_agent_id: bool = True,
) -> tuple[str, str | None]:
    if not allow_raw_skill_agent_id and capability_id_from_skill(agent_id):
        return "general-agent", None
    internal_agent_id = internal_agent_id_for_request(agent_id) or agent_id
    return internal_agent_id, skill_id or default_skill_id_for_public_agent(agent_id)


def _explicit_intent_payload(agent_id: str, skill_id: str | None) -> dict[str, object] | None:
    if not skill_id and agent_id == "general-agent":
        return None
    if skill_id == "qa-file-reviewer" or agent_id in {"qa-word-review", "document-review"}:
        return {
            "status": "selected",
            "intent": "document_review",
            "confidence": 1.0,
            "reason": "请求指定了文档审核能力",
            "selected_capability": "document_review",
            "agent_id": agent_id,
            "skill_id": skill_id or "qa-file-reviewer",
            "confirmed_by_user": True,
            "suggestions": [],
        }
    if skill_id == "baoyu-translate" or agent_id == "baoyu-translate":
        return {
            "status": "selected",
            "intent": "document_translation",
            "confidence": 1.0,
            "reason": "请求指定了文档翻译能力",
            "selected_capability": "document_translation",
            "agent_id": agent_id,
            "skill_id": skill_id or "baoyu-translate",
            "confirmed_by_user": True,
            "suggestions": [],
        }
    if skill_id == "ragflow-knowledge-search" or agent_id == "sop-assistant":
        return {
            "status": "selected",
            "intent": "knowledge_answer",
            "confidence": 1.0,
            "reason": "请求指定了知识库问答能力",
            "selected_capability": "knowledge_answer",
            "agent_id": agent_id,
            "skill_id": skill_id or "ragflow-knowledge-search",
            "confirmed_by_user": True,
            "suggestions": [],
        }
    return {
        "status": "selected",
        "intent": "general_chat",
        "confidence": 1.0,
        "reason": "请求指定了通用聊天能力",
        "selected_capability": "general_chat",
        "agent_id": agent_id,
        "skill_id": skill_id or "general-chat",
        "confirmed_by_user": True,
        "suggestions": [],
    }


def _session_response(row: dict[str, object]) -> ChatSessionResponse:
    raw_agent_id = str(row["agent_id"])
    profile_revision = row.get("admitted_agent_profile_revision")
    profile_name = row.get("agent_profile_name")
    agent_conversation = None
    if isinstance(profile_revision, int) and profile_revision > 0 and isinstance(profile_name, str) and profile_name:
        avatar_ref = str(row.get("agent_profile_avatar_ref") or "")
        category = str(row.get("agent_profile_category") or "")
        agent_conversation = AgentConversationIdentity(
            agent_id=raw_agent_id,
            revision=profile_revision,
            name=profile_name,
            description=str(row.get("agent_profile_description") or ""),
            avatar_ref=avatar_ref if avatar_ref in {"builtin:agent", "builtin:assistant", "builtin:document", "builtin:research"} else "builtin:agent",
            category=category if category in {"general", "support", "writing", "research", "operations"} else "general",
        )
    return ChatSessionResponse(
        session_id=str(row["id"]),
        workspace_id=str(row["workspace_id"]),
        agent_id=public_agent_id_for_projection(raw_agent_id) or raw_agent_id,
        title=str(row.get("title") or ""),
        agent_conversation=agent_conversation,
        created_at=row.get("created_at"),
        updated_at=row.get("updated_at"),
    )


def _message_metadata(row: dict[str, object], principal: AuthPrincipal) -> dict[str, Any]:
    metadata = row.get("metadata_json") or {}
    if not isinstance(metadata, dict):
        return {}
    if is_ai_admin(principal):
        return metadata
    redacted = sanitize_user_control_input(metadata)
    return redacted if isinstance(redacted, dict) else {}


def _message_content(row: dict[str, object], principal: AuthPrincipal) -> str:
    content = str(row["content"])
    if is_ai_admin(principal):
        return content
    return sanitize_public_text(content)


async def enforce_user_active_run_limit(conn, *, tenant_id: str, user_id: str) -> None:
    limit = int(get_settings().max_active_runs_per_user)
    await repositories.enforce_user_active_run_admission_under_lock(
        conn,
        tenant_id=tenant_id,
        user_id=user_id,
        limit=limit,
    )


@router.get("/chat/sessions", response_model=ChatSessionsResponse, response_model_exclude_none=True)
async def list_sessions(principal: AuthPrincipal = Depends(require_principal)) -> ChatSessionsResponse:  # noqa: B008
    async with transaction() as conn:
        rows = await repositories.list_authorized_sessions(conn, tenant_id=principal.tenant_id, user_id=principal.user_id)
    return ChatSessionsResponse(sessions=[_session_response(row) for row in rows])


@router.get("/chat/sessions/{session_id}", response_model=ChatSessionResponse, response_model_exclude_none=True)
async def get_session(
    session_id: str,
    principal: AuthPrincipal = Depends(require_principal),  # noqa: B008
) -> ChatSessionResponse:
    """Recover one owned Session with only its safe Agent Conversation identity."""

    async with transaction() as conn:
        row = await repositories.get_authorized_session_projection(
            conn,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            session_id=session_id,
        )
    if row is None:
        raise HTTPException(status_code=404, detail="session_not_found")
    return _session_response(row)


@router.post("/chat/sessions", response_model=ChatSessionResponse, response_model_exclude_none=True)
async def create_chat_session(
    request: ChatSessionRequest,
    principal: AuthPrincipal = Depends(require_principal),  # noqa: B008
) -> ChatSessionResponse:
    async with transaction() as conn:
        await repositories.ensure_workspace(conn, tenant_id=principal.tenant_id, workspace_id=request.workspace_id)
        await repositories.ensure_user(conn, tenant_id=principal.tenant_id, user_id=principal.user_id, display_name=principal.display_name)
        resolved_agent_id = internal_agent_id_for_request(request.agent_id) or request.agent_id
        session_id = await repositories.create_session(
            conn,
            tenant_id=principal.tenant_id,
            workspace_id=request.workspace_id,
            user_id=principal.user_id,
            agent_id=resolved_agent_id,
            title=request.title or request.agent_id,
        )
        rows = await repositories.list_authorized_sessions(conn, tenant_id=principal.tenant_id, user_id=principal.user_id)
    row = next(item for item in rows if item["id"] == session_id)
    return _session_response(row)


@router.get("/chat/sessions/{session_id}/messages", response_model=ChatMessagesResponse)
async def list_messages(
    session_id: str,
    principal: AuthPrincipal = Depends(require_principal),  # noqa: B008
) -> ChatMessagesResponse:
    async with transaction() as conn:
        session = await repositories.get_authorized_session(
            conn,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            session_id=session_id,
        )
        if session is None:
            raise HTTPException(status_code=404, detail="session_not_found")
        rows = await repositories.list_authorized_messages(
            conn,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            session_id=session_id,
        )
    return ChatMessagesResponse(
        messages=[
            ChatMessageResponse(
                message_id=str(row["id"]),
                session_id=str(row["session_id"]),
                run_id=row.get("run_id"),
                role=str(row["role"]),
                content=_message_content(row, principal),
                metadata=_message_metadata(row, principal),
                created_at=row.get("created_at"),
            )
            for row in rows
        ]
    )


@router.post("/chat/stream", response_model=ChatStreamResponse)
async def chat_stream(
    request: ChatStreamRequest,
    agent_id: str | None = Query(None),
    principal: AuthPrincipal = Depends(require_principal),  # noqa: B008
) -> ChatStreamResponse:
    try:
        assert_safe_principal_user_id(principal.user_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid_principal_user_id") from exc
    query_agent_id = _normalized_query_agent_id(agent_id)
    submission_id = str(request.submission_id) if request.submission_id is not None else None
    request_fingerprint = None
    existing_submission_row = None
    if submission_id is not None:
        request_fingerprint = repositories.chat_submission_fingerprint(
            {
                "request": request.model_dump(mode="json", exclude={"submission_id"}),
                "query_agent_id": query_agent_id,
            },
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
        )
        async with transaction() as conn:
            existing_submission_row = await repositories.get_chat_submission(
                conn, tenant_id=principal.tenant_id, user_id=principal.user_id, submission_id=submission_id
            )
        if existing_submission_row:
            if _is_preledger_recovery_tombstone(
                existing_submission_row,
                principal=principal,
            ):
                return _chat_stream_response_from_submission(existing_submission_row)
            fingerprint_matches = existing_submission_row.get("request_fingerprint_sha256") == (
                _canonical_pre_persistence_rejection_fingerprint(
                    request=request,
                    principal=principal,
                    query_agent_id=query_agent_id,
                    code=str(existing_submission_row.get("rejection_code") or "chat_submission_rejected"),
                )
                if existing_submission_row.get("state") == "rejected_before_persist"
                else request_fingerprint
            )
            if request.session_id is None or request.selected_mcp_tool_ids is not None:
                if not fingerprint_matches:
                    raise HTTPException(status_code=409, detail="submission_payload_mismatch")
                return _chat_stream_response_from_submission(existing_submission_row)
            if fingerprint_matches:
                return _chat_stream_response_from_submission(existing_submission_row)
    if contains_platform_multi_agent_control(request.input):
        code = PLATFORM_MULTI_AGENT_NOT_SUPPORTED
        await _persist_pre_persistence_rejection(
            principal=principal,
            submission_id=submission_id,
            request=request,
            query_agent_id=query_agent_id,
            workspace_id=request.workspace_id,
            session_id=request.session_id,
            code=code,
        )
        if submission_id is not None:
            raise _chat_submission_http_error(status_code=400, code=code)
        raise HTTPException(status_code=400, detail=code)
    execution_polarity = classify_execution_polarity(request.message)
    selected_agent_profile = request.selected_agent_profile
    allowed = execution_polarity != "non_execution" or selected_agent_profile is not None
    explicit_skill_selection = request.selected_skill is not None
    skill_selector_allowed = allowed or explicit_skill_selection
    requested_agent_id = request.agent_id or query_agent_id or "general-agent"
    if selected_agent_profile is not None:
        requested_agent_id = selected_agent_profile.agent_id
    if skill_selector_allowed and request.skill_id and not is_ai_admin(principal):
        await _persist_pre_persistence_rejection(
            principal=principal,
            submission_id=submission_id,
            request=request,
            query_agent_id=query_agent_id,
            workspace_id=request.workspace_id,
            session_id=request.session_id,
            code="raw_skill_selector_forbidden",
        )
        if submission_id is not None:
            raise _chat_submission_http_error(status_code=403, code="raw_skill_selector_forbidden")
        raise HTTPException(status_code=403, detail="raw_skill_selector_forbidden")
    requested_skill_id = request.skill_id if skill_selector_allowed and is_ai_admin(principal) else None
    if skill_selector_allowed and request.selected_skill is not None and request.skill_id:
        await _persist_pre_persistence_rejection(
            principal=principal,
            submission_id=submission_id,
            request=request,
            query_agent_id=query_agent_id,
            workspace_id=request.workspace_id,
            session_id=request.session_id,
            code="skill_selector_conflict",
        )
        if submission_id is not None:
            raise _chat_submission_http_error(status_code=400, code="skill_selector_conflict")
        raise HTTPException(status_code=400, detail="skill_selector_conflict")
    requested_agent_id, requested_skill_id = _normalize_request_selector(
        requested_agent_id,
        requested_skill_id,
        allow_raw_skill_agent_id=is_ai_admin(principal),
    )
    try:
        requested_model_selection = _requested_model_selection(request)
    except HTTPException as exc:
        code = _submission_code(exc.detail)
        await _persist_pre_persistence_rejection(
            principal=principal,
            submission_id=submission_id,
            request=request,
            query_agent_id=query_agent_id,
            workspace_id=request.workspace_id,
            session_id=request.session_id,
            code=code,
        )
        if submission_id is not None:
            raise _chat_submission_http_error(status_code=exc.status_code, code=code) from exc
        raise
    requested_model_id = requested_model_selection["id"] if requested_model_selection is not None else None
    requested_model_value = requested_model_selection["value"] if requested_model_selection is not None else None
    requested_file_ids = _file_ids_from_request(request)
    if allowed and _has_legacy_client_mcp_selector(request.input):
        code = "selected_mcp_tool_ids_required"
        await _persist_pre_persistence_rejection(
            principal=principal,
            submission_id=submission_id,
            request=request,
            query_agent_id=query_agent_id,
            workspace_id=request.workspace_id,
            session_id=request.session_id,
            code=code,
        )
        if submission_id is not None:
            raise _chat_submission_http_error(status_code=400, code=code)
        raise HTTPException(status_code=400, detail=code)
    try:
        run_input = _strip_server_owned_control_metadata(
            {**request.input, "message": request.message},
            redact_public=not is_ai_admin(principal),
        )
        run_input = attach_required_tool_declaration(run_input)
        required_tool_declaration = declaration_from_input(run_input)
    except repositories.RepositoryAuthorizationError as exc:
        await _audit_capability_denial(principal, exc, source="chat_stream")
        denial = getattr(exc, "denial", None)
        error_code = (
            "mcp_tool_not_available"
            if denial is not None and denial.capability_kind == "mcp_tool"
            else "capability_not_authorized"
        )
        await _persist_pre_persistence_rejection(
            principal=principal,
            submission_id=submission_id,
            request=request,
            query_agent_id=query_agent_id,
            workspace_id=request.workspace_id,
            session_id=request.session_id,
            code=error_code,
        )
        if submission_id is not None:
            raise _chat_submission_http_error(status_code=403, code=error_code) from exc
        raise HTTPException(status_code=403, detail=error_code) from exc
    selected_skill_for_execution = request.selected_skill if skill_selector_allowed else None
    selected_mcp_tool_ids_for_execution = request.selected_mcp_tool_ids if allowed else None
    if not allowed and selected_skill_for_execution is None:
        requested_agent_id, requested_skill_id = "general-agent", None
    if selected_skill_for_execution is not None:
        requested_skill_id = selected_skill_for_execution.skill_id
    if selected_mcp_tool_ids_for_execution is not None:
        run_input["mcp_tool_ids"] = list(selected_mcp_tool_ids_for_execution)
        try:
            async with transaction() as conn:
                await repositories.authorize_selected_chat_mcp_tools(
                    conn,
                    tenant_id=principal.tenant_id,
                    tool_ids=list(selected_mcp_tool_ids_for_execution),
                    principal_department_id=principal.department_id,
                    principal_roles=principal.roles,
                    is_admin=is_ai_admin(principal),
                    permissions=principal.permissions,
                )
        except repositories.RepositoryAuthorizationError as exc:
            await _audit_capability_denial(principal, exc, source="chat_stream")
            await _persist_pre_persistence_rejection(
                principal=principal,
                submission_id=submission_id,
                request=request,
                query_agent_id=query_agent_id,
                workspace_id=request.workspace_id,
                session_id=request.session_id,
                code="mcp_tool_not_available",
            )
            if submission_id is not None:
                raise _chat_submission_http_error(
                    status_code=403,
                    code="mcp_tool_not_available",
                ) from exc
            raise HTTPException(status_code=403, detail="mcp_tool_not_available") from exc
    pending_submission_response: ChatStreamResponse | None = None
    locked_skill_label: str | None = None
    effective_workspace_id = request.workspace_id
    inherited_mcp_selection = False
    admitted_agent_profile = None
    try:
        async with transaction() as conn:
            # Global submission order: user advisory -> session row -> Agent
            # profile aggregate. Every path takes this once before admission.
            await repositories.acquire_user_active_run_admission_lock(
                conn,
                tenant_id=principal.tenant_id,
                user_id=principal.user_id,
            )
            await repositories.ensure_submission_principal(
                conn,
                tenant_id=principal.tenant_id,
                user_id=principal.user_id,
                display_name=principal.display_name,
            )
            continuation_session = None
            continuation_prior_runs: list[dict[str, Any]] = []
            continuation_latest_input_json: dict[str, Any] | None = None
            preserve_continuation_skill = bool(
                request.session_id
                and request.selected_skill is None
                and request.skill_id is None
                and selected_agent_profile is None
            )
            if request.session_id:
                continuation_session = await repositories.get_authorized_session(
                    conn,
                    tenant_id=principal.tenant_id,
                    user_id=principal.user_id,
                    session_id=request.session_id,
                )
                if continuation_session is None:
                    raise HTTPException(status_code=404, detail="session_not_found")
                continuation_workspace_id = continuation_session.get("workspace_id")
                if not isinstance(continuation_workspace_id, str) or not continuation_workspace_id:
                    raise HTTPException(status_code=404, detail="session_not_found")
                # The persisted session owns its workspace as well as its
                # agent.  ``default`` remains the legacy/omitted request value;
                # an explicit non-default workspace must agree before routing.
                if (
                    request.workspace_id != "default"
                    and request.workspace_id != continuation_workspace_id
                ):
                    raise HTTPException(status_code=409, detail="session_workspace_mismatch")
                effective_workspace_id = continuation_workspace_id
                # A loaded session owns its execution agent. A stale client
                # selection may not defer ownership validation until write-time
                # or switch the session to another agent.
                requested_agent_id = str(continuation_session["agent_id"])

            if not allowed and selected_skill_for_execution is None:
                preserve_continuation_skill = False
                requested_agent_id, requested_skill_id = "general-agent", None

            requires_locked_continuation = bool(
                request.session_id
                and (
                    preserve_continuation_skill
                    or request.selected_mcp_tool_ids is None
                )
            )
            if requires_locked_continuation:
                # Bind inherited selection and later generation to the session
                # row only after the transaction-wide user lock above.
                locked_continuation_session = await repositories.get_authorized_session(
                    conn,
                    tenant_id=principal.tenant_id,
                    user_id=principal.user_id,
                    session_id=request.session_id,
                    workspace_id=effective_workspace_id,
                    for_update=True,
                )
                if locked_continuation_session is None:
                    raise HTTPException(status_code=404, detail="session_not_found")
                continuation_session = locked_continuation_session
                requested_agent_id = (
                    str(continuation_session["agent_id"])
                    if allowed or selected_skill_for_execution is not None
                    else "general-agent"
                )
                if (
                    request.selected_mcp_tool_ids is None and allowed
                ):
                    continuation_latest_input_json = (
                        await repositories.get_latest_authorized_session_run_input(
                            conn,
                            tenant_id=principal.tenant_id,
                            workspace_id=effective_workspace_id,
                            user_id=principal.user_id,
                            session_id=request.session_id,
                        )
                    )

            session_profile_revision = (
                continuation_session.get("admitted_agent_profile_revision")
                if isinstance(continuation_session, dict)
                else None
            )
            session_profile_hash = (
                continuation_session.get("admitted_agent_profile_hash")
                if isinstance(continuation_session, dict)
                else None
            )
            if request.session_id and isinstance(session_profile_revision, int) and session_profile_revision > 0:
                session_profile_agent_id = str(continuation_session.get("agent_id") or "")
                if not isinstance(session_profile_hash, str) or not session_profile_hash:
                    raise HTTPException(status_code=409, detail="agent_profile_session_mismatch")
                if (
                    selected_agent_profile is not None
                    and (
                        selected_agent_profile.agent_id != session_profile_agent_id
                        or selected_agent_profile.expected_revision != session_profile_revision
                    )
                ):
                    raise HTTPException(status_code=409, detail="agent_profile_session_mismatch")
                selected_agent_profile = SelectedAgentProfileRequest(
                    agent_id=session_profile_agent_id,
                    expected_revision=session_profile_revision,
                )
            elif request.session_id and selected_agent_profile is not None:
                raise HTTPException(status_code=409, detail="agent_profile_session_mismatch")

            if selected_agent_profile is not None:
                if request.session_id and isinstance(session_profile_revision, int):
                    admitted_agent_profile = await resolve_bound_profile_for_submission(
                        conn,
                        principal=principal,
                        agent_id=selected_agent_profile.agent_id,
                        revision=selected_agent_profile.expected_revision,
                        content_hash=session_profile_hash,
                        submitted_request=request,
                        query_agent_id=query_agent_id,
                    )
                else:
                    admitted_agent_profile = await resolve_profile_for_admission(
                        conn,
                        principal=principal,
                        selection=selected_agent_profile,
                        submitted_request=request,
                        query_agent_id=query_agent_id,
                    )
                requested_agent_id = admitted_agent_profile.agent_id
                requested_skill_id = str(admitted_agent_profile.skill["skill_id"])
                selected_skill_for_execution = SelectedSkillRequest(
                    skill_id=requested_skill_id,
                    expected_version=str(admitted_agent_profile.skill["skill_version"]),
                )
                selected_mcp_tool_ids_for_execution = list(admitted_agent_profile.mcp_tool_ids)
                requested_model_id = admitted_agent_profile.model["id"]
                requested_model_value = admitted_agent_profile.model["value"]
                run_input["mcp_tool_ids"] = list(admitted_agent_profile.mcp_tool_ids)

            if (
                request.session_id
                and request.selected_mcp_tool_ids is None
                and selected_agent_profile is None
                and allowed
            ):
                prior_input = (
                    continuation_latest_input_json.get("input")
                    if isinstance(continuation_latest_input_json, dict)
                    else None
                )
                if isinstance(prior_input, dict) and "mcp_tool_ids" in prior_input:
                    run_input["mcp_tool_ids"] = repositories.extract_run_mcp_tool_ids(prior_input)
                    inherited_mcp_selection = True

            if inherited_mcp_selection:
                await repositories.authorize_selected_chat_mcp_tools(
                    conn,
                    tenant_id=principal.tenant_id,
                    tool_ids=list(run_input.get("mcp_tool_ids") or []),
                    principal_department_id=principal.department_id,
                    principal_roles=principal.roles,
                    is_admin=is_ai_admin(principal),
                    permissions=principal.permissions,
                )

            fingerprint_request = request.model_dump(
                mode="json",
                exclude={"submission_id"},
            )
            if request.selected_mcp_tool_ids is None and allowed and "mcp_tool_ids" in run_input:
                fingerprint_request["selected_mcp_tool_ids"] = list(
                    run_input.get("mcp_tool_ids") or []
                )
            resolved_request_fingerprint = repositories.chat_submission_fingerprint(
                {
                    "request": fingerprint_request,
                    "query_agent_id": query_agent_id,
                },
                tenant_id=principal.tenant_id,
                user_id=principal.user_id,
            )
            if submission_id is not None:
                request_fingerprint = resolved_request_fingerprint

            if submission_id is not None and request_fingerprint is not None:
                claimed_submission, created_submission = await repositories.claim_chat_submission(
                    conn,
                    tenant_id=principal.tenant_id,
                    user_id=principal.user_id,
                    submission_id=submission_id,
                    workspace_id=effective_workspace_id,
                    request_fingerprint_sha256=request_fingerprint,
                )
                if not created_submission:
                    if _is_preledger_recovery_tombstone(
                        claimed_submission,
                        principal=principal,
                    ):
                        return _chat_stream_response_from_submission(claimed_submission)
                    claimed_fingerprint = request_fingerprint
                    if claimed_submission.get("state") == "rejected_before_persist":
                        claimed_fingerprint = _canonical_pre_persistence_rejection_fingerprint(
                            request=request,
                            principal=principal,
                            query_agent_id=query_agent_id,
                            code=str(
                                claimed_submission.get("rejection_code")
                                or "chat_submission_rejected"
                            ),
                        )
                    if claimed_submission.get("request_fingerprint_sha256") != claimed_fingerprint:
                        raise HTTPException(status_code=409, detail="submission_payload_mismatch")
                    return _chat_stream_response_from_submission(claimed_submission)

            if preserve_continuation_skill and admitted_agent_profile is None:
                continuation_prior_runs = await repositories.list_authorized_session_runs(
                    conn,
                    tenant_id=principal.tenant_id,
                    user_id=principal.user_id,
                    session_id=request.session_id,
                    workspace_id=effective_workspace_id,
                    limit=1,
                )
                prior_skill_id = str(
                    _row_value(
                        continuation_prior_runs[0] if continuation_prior_runs else {},
                        "skill_id",
                    )
                    or ""
                ).strip()
                requested_skill_id = prior_skill_id or None

            explicit_payload = _explicit_intent_payload(requested_agent_id, requested_skill_id)
            is_terminal_implicit_decision = False
            if explicit_payload is None:
                continuation_capability = (
                    capability_id_from_skill(None, requested_agent_id)
                    if continuation_session is not None
                    and allowed
                    else None
                )
                decision = route_intent(
                    request.message,
                    await _file_summaries_for_intent(
                        conn,
                        request,
                        principal,
                        workspace_id=effective_workspace_id,
                    )
                    if continuation_capability is None
                    else [],
                    confirmed_capability_id=continuation_capability
                    or request.confirmed_capability_id,
                    execution_polarity=execution_polarity,
                )
                decision_payload = decision.as_payload()
                is_terminal_implicit_decision = (
                    continuation_session is None
                    and selected_skill_for_execution is None
                    and request.skill_id is None
                    and selected_mcp_tool_ids_for_execution is None
                    and not decision.confirmed_by_user
                    and decision.status == "selected"
                )
                if decision.status == "needs_confirmation":
                    agent_rows = await repositories.list_principal_lambchat_agents(
                        conn,
                        tenant_id=principal.tenant_id,
                        actor_user_id=principal.user_id,
                        department_id=principal.department_id,
                        roles=principal.roles,
                        is_admin=is_ai_admin(principal),
                        permissions=principal.permissions,
                    )
                    authorized_capability_ids = {
                        capability_id_from_skill(row.get("default_skill_id"), row.get("id"))
                        for row in agent_rows
                    }
                    decision_payload["suggestions"] = [
                        item
                        for item in decision_payload["suggestions"]
                        if isinstance(item, dict) and item.get("capability_id") in authorized_capability_ids
                    ]
                    suggestions = [
                        CapabilitySuggestionResponse.model_validate(item)
                        for item in decision_payload["suggestions"]
                    ]
                    confirmation_response = ChatStreamResponse(
                        session_id=request.session_id,
                        run_id=None,
                        status="needs_confirmation",
                        submission_id=submission_id,
                        intent_decision=_intent_response(decision_payload, principal),
                        suggestions=suggestions,
                    )
                    if submission_id is not None:
                        await repositories.finalize_chat_submission(
                            conn,
                            tenant_id=principal.tenant_id,
                            user_id=principal.user_id,
                            submission_id=submission_id,
                            state="needs_confirmation",
                            workspace_id=effective_workspace_id,
                            outcome_json=confirmation_response.model_dump(mode="json"),
                        )
                    return confirmation_response
                resolved_agent_id = str(decision.agent_id)
                resolved_skill_id = str(decision.skill_id)
            else:
                decision_payload = explicit_payload
                resolved_agent_id = str(decision_payload["agent_id"])
                resolved_skill_id = str(decision_payload["skill_id"])
            authorization_kwargs = {
                "tenant_id": principal.tenant_id,
                "agent_id": resolved_agent_id,
                "skill_id": resolved_skill_id,
                "normalized_input": run_input,
                "principal_department_id": principal.department_id,
                "principal_roles": principal.roles,
                "is_admin": is_ai_admin(principal),
                "permissions": principal.permissions,
            }
            implicit_skill = None
            if is_terminal_implicit_decision:
                strict_implicit_authorization_kwargs = {
                    **authorization_kwargs,
                    "is_admin": False,
                }
                try:
                    implicit_skill = await repositories.authorize_run_capabilities(
                        conn,
                        **strict_implicit_authorization_kwargs,
                    )
                except repositories.RepositoryAuthorizationError:
                    if decision.selected_capability == "general_chat":
                        raise
                    decision = fallback_to_general_chat()
                    decision_payload = decision.as_payload()
                    resolved_agent_id = str(decision.agent_id)
                    resolved_skill_id = str(decision.skill_id)
                    implicit_skill = await repositories.authorize_run_capabilities(
                        conn,
                        **{
                            **strict_implicit_authorization_kwargs,
                            "agent_id": resolved_agent_id,
                            "skill_id": resolved_skill_id,
                        },
                    )
            if implicit_skill is not None:
                skill = implicit_skill
            elif selected_skill_for_execution is not None:
                skill = await repositories.authorize_selected_run_capabilities(
                    conn,
                    expected_version=selected_skill_for_execution.expected_version,
                    rollout_key=principal.user_id,
                    **authorization_kwargs,
                )
                locked_skill_label = public_skill_display_label(
                    skill.get("skill_display_label")
                )
            else:
                skill = await repositories.authorize_run_capabilities(
                    conn,
                    **authorization_kwargs,
                )
            if (
                repositories.extract_run_mcp_tool_ids(run_input)
                and str(skill.get("executor_type") or "") != "claude-agent-worker"
            ):
                raise repositories.RepositoryAuthorizationError(
                    "mcp_tool_not_available",
                    denial=CapabilityAuthorizationDenial.from_decision(
                        decision=CapabilityAccessDecision(
                            visible=False,
                            usable=False,
                            manageable=False,
                            admin_bypass=False,
                            decision_reason="mcp_sandbox_executor_required",
                        ),
                        actor_department_id=principal.department_id,
                        actor_roles=principal.roles,
                        capability_kind="mcp_tool",
                        capability_id=repositories.extract_run_mcp_tool_ids(run_input)[0],
                    ),
                )
            input_modes = list(skill.get("input_modes") or [])
            reusable_file_rows = []
            if request.session_id and not requested_file_ids and has_file_input_mode(input_modes):
                reusable_file_rows = await repositories.list_authorized_session_input_files(
                    conn,
                    tenant_id=principal.tenant_id,
                    workspace_id=effective_workspace_id,
                    user_id=principal.user_id,
                    session_id=request.session_id,
                )
            primary_file_ids = primary_file_ids_for_run(
                requested_file_ids=requested_file_ids,
                reusable_rows=[dict(row) for row in reusable_file_rows],
                input_modes=input_modes,
            )
            if has_file_input_mode(input_modes) and not primary_file_ids:
                raise RepositoryConflictError("file_required_for_skill")
            await enforce_user_active_run_limit(
                conn,
                tenant_id=principal.tenant_id,
                user_id=principal.user_id,
            )
            release_decision = resolve_rollout_skill_decision(
                skill,
                tenant_id=principal.tenant_id,
                skill_id=resolved_skill_id,
                rollout_key=principal.user_id,
            )
            selected_policy_version = release_decision.selected_version
            release_decision_payload = release_decision.to_payload()
            release_policy_version = selected_policy_version if release_decision.policy_active else None
            skill_manifests = await _governed_skill_manifest_pins(
                conn,
                skill_id=resolved_skill_id,
                input_payload=run_input,
                release_policy_version=release_policy_version,
            )
            skill_version = governed_locked_skill_version(
                skill_id=resolved_skill_id,
                skill_manifests=skill_manifests,
                fallback_version=selected_policy_version,
                release_policy_version=release_policy_version,
            )
            release_decision_payload = release_decision_payload_for_locked_version(
                release_decision,
                locked_version=skill_version,
            )
            skill_manifests = attach_skill_snapshot_governance(
                skill_manifests,
                release_decision=release_decision_payload,
            )
            skill_manifests = repositories.pin_primary_skill_mcp_tool_ids(
                skill_manifests,
                skill_id=resolved_skill_id,
                mcp_tool_ids=repositories.run_mcp_tool_ids_for_skill(skill, run_input),
            )
            if (
                required_tool_declaration is not None
                and required_tool_declaration.capability_kind == "builtin"
            ):
                primary_manifest = next(
                    (
                        manifest
                        for manifest in skill_manifests
                        if isinstance(manifest, dict)
                        and str(manifest.get("skill_id") or "") == resolved_skill_id
                    ),
                    None,
                )
                try:
                    canonical_builtin_identities = (
                        repositories.canonical_builtin_tool_identities(primary_manifest)
                        if isinstance(primary_manifest, dict)
                        else []
                    )
                except RepositoryConflictError:
                    canonical_builtin_identities = []
                if required_tool_declaration.canonical_identity not in canonical_builtin_identities:
                    raise HTTPException(
                        status_code=403,
                        detail=public_required_tool_detail("unavailable"),
                    )
            session_id = request.session_id or repositories.new_id("ses")
            run_id = repositories.new_id("run")
            queue_payload = _validate_queue_payload_for_enqueue(
                {
                    "tenant_id": principal.tenant_id,
                    "workspace_id": effective_workspace_id,
                    "user_id": principal.user_id,
                    "session_id": session_id,
                    "run_id": run_id,
                    "agent_id": resolved_agent_id,
                    "skill_id": resolved_skill_id,
                    "file_ids": primary_file_ids,
                    "input": run_input,
                    "executor_type": skill["executor_type"],
                    "skill_version": skill_version,
                    "release_decision": release_decision_payload,
                    "skill_manifests": skill_manifests,
                    "model_id": requested_model_id,
                    "model_value": requested_model_value,
                    **(
                        {"agent_profile": admitted_agent_profile.private_execution_input}
                        if admitted_agent_profile is not None
                        else {}
                    ),
                }
            )
            await repositories.ensure_workspace_belongs_to_tenant(
                conn,
                tenant_id=principal.tenant_id,
                workspace_id=effective_workspace_id,
            )
            await repositories.authorize_files_for_run(
                conn,
                tenant_id=principal.tenant_id,
                workspace_id=effective_workspace_id,
                user_id=principal.user_id,
                session_id=session_id,
                run_id=run_id,
                file_ids=requested_file_ids,
                input_modes=input_modes,
            )
            if admitted_agent_profile is not None and submission_id is None:
                # Canonical clients claim their own key before routing. A
                # legacy unkeyed Agent request gets the same durable recovery
                # only after every capability and resource check has passed,
                # immediately before the first session/run write.
                submission_id = str(uuid4())
                request_fingerprint = resolved_request_fingerprint
                claimed_submission, created_submission = await repositories.claim_chat_submission(
                    conn,
                    tenant_id=principal.tenant_id,
                    user_id=principal.user_id,
                    submission_id=submission_id,
                    workspace_id=effective_workspace_id,
                    request_fingerprint_sha256=request_fingerprint,
                )
                if not created_submission:
                    if claimed_submission.get("request_fingerprint_sha256") != request_fingerprint:
                        raise HTTPException(status_code=409, detail="submission_payload_mismatch")
                    return _chat_stream_response_from_submission(claimed_submission)
            session_create_kwargs = {
                "tenant_id": principal.tenant_id,
                "workspace_id": effective_workspace_id,
                "user_id": principal.user_id,
                "agent_id": resolved_agent_id,
                "title": request.title or request.message[:80],
                "session_id": session_id,
            }
            if admitted_agent_profile is not None:
                session_create_kwargs.update(
                    {
                        "admitted_agent_profile_revision": admitted_agent_profile.revision,
                        "admitted_agent_profile_hash": admitted_agent_profile.content_hash,
                    }
                )
            session_id = await repositories.create_session(conn, **session_create_kwargs)
            run_create_kwargs = {
                "tenant_id": principal.tenant_id,
                "workspace_id": effective_workspace_id,
                "session_id": session_id,
                "user_id": principal.user_id,
                "agent_id": resolved_agent_id,
                "skill_id": resolved_skill_id,
                "input_json": {
                    "input": run_input,
                    "file_ids": primary_file_ids,
                    "executor_type": skill["executor_type"],
                    "skill_version": skill_version,
                    "release_decision": release_decision_payload,
                    "skill_manifests": queue_payload["skill_manifests"],
                    "intent": decision_payload,
                    "model_id": requested_model_id,
                    "model_value": requested_model_value,
                    **(
                        {"agent_profile": admitted_agent_profile.private_execution_input}
                        if admitted_agent_profile is not None
                        else {}
                    ),
                },
                "principal_roles": principal.roles,
                "principal_department_id": principal.department_id,
                "auth_source": principal.source,
            }
            if admitted_agent_profile is not None:
                run_create_kwargs.update(
                    {
                        "admitted_agent_profile_revision": admitted_agent_profile.revision,
                        "admitted_agent_profile_hash": admitted_agent_profile.content_hash,
                    }
                )
            run_id = await repositories.create_run(conn, **run_create_kwargs)
            await repositories.insert_run_skill_snapshots_at_creation(
                conn,
                tenant_id=principal.tenant_id,
                run_id=run_id,
                skill_manifests=queue_payload["skill_manifests"],
                release_decision=release_decision_payload,
            )
            message_id = await repositories.append_message(
                conn,
                tenant_id=principal.tenant_id,
                session_id=session_id,
                run_id=run_id,
                role="user",
                content=request.message,
                metadata_json=sanitize_user_control_input(
                    {
                        "skill_id": resolved_skill_id,
                        "file_ids": primary_file_ids,
                        "attachments": request.attachments,
                        "intent": decision_payload,
                        **(
                            {"locked_skill": {"label": locked_skill_label}}
                            if locked_skill_label
                            else {}
                        ),
                    }
                )
                if not is_ai_admin(principal)
                else {
                    "skill_id": resolved_skill_id,
                    "file_ids": primary_file_ids,
                    "attachments": request.attachments,
                    "intent": decision_payload,
                    **(
                        {"locked_skill": {"label": locked_skill_label}}
                        if locked_skill_label
                        else {}
                    ),
                },
            )
            await repositories.bind_files_to_run(
                conn,
                tenant_id=principal.tenant_id,
                workspace_id=effective_workspace_id,
                user_id=principal.user_id,
                session_id=session_id,
                run_id=run_id,
                file_ids=requested_file_ids,
            )
            context_ref = await record_initial_context_snapshot(
                conn,
                tenant_id=principal.tenant_id,
                workspace_id=effective_workspace_id,
                user_id=principal.user_id,
                session_id=session_id,
                run_id=run_id,
                trace_id=standard_trace_id(run_id),
                agent_id=resolved_agent_id,
                skill_id=resolved_skill_id,
                input_payload=run_input,
                message_ids=[message_id] if message_id else [],
                file_ids=primary_file_ids,
                source="chat_stream",
                include_session_history=True,
            )
            for event in intent_event_specs(decision_payload):
                await repositories.append_event(
                    conn,
                    tenant_id=principal.tenant_id,
                    run_id=run_id,
                    event_type=event["event_type"],
                    stage=event["stage"],
                    message=event["message"],
                    payload=event["payload"],
                )
            for event in initial_run_event_specs(
                agent_id=resolved_agent_id,
                skill_id=resolved_skill_id,
                skill_version=skill_version,
                executor_type=str(skill["executor_type"]),
                file_ids=primary_file_ids,
                source="chat_stream",
            ):
                await repositories.append_event(
                    conn,
                    tenant_id=principal.tenant_id,
                    run_id=run_id,
                    event_type=event["event_type"],
                    stage=event["stage"],
                    message=event["message"],
                    payload=event["payload"],
                )
            await repositories.append_event(
                conn,
                tenant_id=principal.tenant_id,
                run_id=run_id,
                event_type="skill_release_decision",
                stage="control",
                message="已锁定 Skill 发布决策",
                payload=_release_decision_event_payload(release_decision_payload, skill_id=resolved_skill_id),
            )
            if submission_id is not None:
                pending_submission_response = ChatStreamResponse(
                    session_id=session_id,
                    run_id=run_id,
                    status="accepted_pending_enqueue",
                    submission_id=submission_id,
                    intent_decision=_intent_response(decision_payload, principal),
                )
                await repositories.finalize_chat_submission(
                    conn,
                    tenant_id=principal.tenant_id,
                    user_id=principal.user_id,
                    submission_id=submission_id,
                    state="accepted_pending_enqueue",
                    workspace_id=effective_workspace_id,
                    session_id=session_id,
                    run_id=run_id,
                    outcome_json=pending_submission_response.model_dump(mode="json"),
                )
            queue_payload = _validate_queue_payload_for_enqueue(
                {
                    **queue_payload,
                    "session_id": session_id,
                    "run_id": run_id,
                    "context_snapshot_id": context_ref["context_snapshot_id"],
                    "context_snapshot": context_ref,
                }
            )
    except HTTPException as exc:
        code = _submission_code(exc.detail)
        if 400 <= exc.status_code < 500:
            await _persist_pre_persistence_rejection(
                principal=principal,
                submission_id=submission_id,
                request=request,
                query_agent_id=query_agent_id,
                workspace_id=effective_workspace_id,
                session_id=request.session_id,
                code=code,
            )
        if submission_id is not None and 400 <= exc.status_code < 500:
            raise _chat_submission_http_error(status_code=exc.status_code, code=code) from exc
        raise
    except repositories.RepositoryAuthorizationError as exc:
        await _audit_capability_denial(principal, exc, source="chat_stream")
        denial = getattr(exc, "denial", None)
        error_code = (
            "mcp_tool_not_available"
            if denial is not None and denial.capability_kind == "mcp_tool"
            else "capability_not_authorized"
        )
        await _persist_pre_persistence_rejection(
            principal=principal,
            submission_id=submission_id,
            request=request,
            query_agent_id=query_agent_id,
            workspace_id=effective_workspace_id,
            session_id=request.session_id,
            code=error_code,
        )
        if submission_id is not None:
            raise _chat_submission_http_error(status_code=403, code=error_code) from exc
        raise HTTPException(status_code=403, detail=error_code) from exc
    except RepositoryNotFoundError as exc:
        code = str(exc)
        await _persist_pre_persistence_rejection(
            principal=principal,
            submission_id=submission_id,
            request=request,
            query_agent_id=query_agent_id,
            workspace_id=effective_workspace_id,
            session_id=request.session_id,
            code=code,
        )
        if submission_id is not None:
            raise _chat_submission_http_error(status_code=404, code=code) from exc
        raise HTTPException(status_code=404, detail=code) from exc
    except SkillVersionMaterializationError as exc:
        code = str(exc)
        await _persist_pre_persistence_rejection(
            principal=principal,
            submission_id=submission_id,
            request=request,
            query_agent_id=query_agent_id,
            workspace_id=effective_workspace_id,
            session_id=request.session_id,
            code=code,
        )
        if submission_id is not None:
            raise _chat_submission_http_error(status_code=409, code=code) from exc
        raise HTTPException(status_code=409, detail=code) from exc
    except RepositoryConflictError as exc:
        code = str(exc)
        await _persist_pre_persistence_rejection(
            principal=principal,
            submission_id=submission_id,
            request=request,
            query_agent_id=query_agent_id,
            workspace_id=effective_workspace_id,
            session_id=request.session_id,
            code=code,
        )
        if submission_id is not None:
            raise _chat_submission_http_error(status_code=409, code=code) from exc
        raise HTTPException(status_code=409, detail=code) from exc
    if submission_id is not None:
        try:
            admitted = _require_chat_submission_admitted(
                await _admit_chat_submission(principal=principal, submission_id=submission_id)
            )
        except HTTPException:
            raise
        except Exception:
            if pending_submission_response is None:
                raise
            return pending_submission_response
        return admitted.outcome or pending_submission_response or ChatStreamResponse(
            session_id=session_id,
            run_id=run_id,
            status="accepted_pending_enqueue",
            submission_id=submission_id,
        )
    try:
        queue_admission = await _enqueue_chat_run(queue_payload)
    except Exception as exc:
        async with transaction() as conn:
            await repositories.mark_run_enqueue_failed(
                conn,
                tenant_id=principal.tenant_id,
                user_id=principal.user_id,
                run_id=run_id,
                trace_id=standard_trace_id(run_id),
            )
        raise HTTPException(status_code=503, detail="queue_enqueue_failed") from exc
    queue_position = int(queue_admission.queue_position)
    async with transaction() as conn:
        await repositories.append_event(
            conn,
            tenant_id=principal.tenant_id,
            run_id=run_id,
            event_type="queued",
            stage="queue",
            message="任务队列接纳完成",
            payload={
                "visible_to_user": False,
                "source": "admin_runtime_queue",
                "queue_position": queue_position,
                "queue_admission_ordinal": int(queue_admission.queue_admission_ordinal),
                "queue_probe_source": str(queue_admission.source),
            },
        )
    return ChatStreamResponse(
        session_id=session_id,
        run_id=run_id,
        status="queued",
        queue_position=queue_position,
        queue_insight=await get_queue_insight(principal.tenant_id, user_id=principal.user_id),
        intent_decision=_intent_response(decision_payload, principal),
    )


async def get_chat_submission(
    submission_id: UUID,
    response: Response,
    principal: AuthPrincipal = Depends(require_principal),  # noqa: B008
) -> ChatSubmissionResponse:
    """Resolve a durable client submission without inferring from session history."""

    response.headers["Cache-Control"] = _CHAT_SUBMISSION_RESOLUTION_CACHE_CONTROL
    resolved = await _resolve_chat_submission(
        principal=principal,
        submission_id=str(submission_id),
    )
    if resolved is None:
        raise HTTPException(
            status_code=404,
            detail="chat_submission_not_found",
            headers={"Cache-Control": _CHAT_SUBMISSION_RESOLUTION_CACHE_CONTROL},
        )
    return resolved


async def retry_chat_submission_admission(
    submission_id: UUID,
    response: Response,
    principal: AuthPrincipal = Depends(require_principal),  # noqa: B008
) -> ChatSubmissionResponse | ChatSubmissionPreLedgerAbsenceResponse:
    """Explicitly retry queue admission for one already-created run only."""

    response.headers["Cache-Control"] = _CHAT_SUBMISSION_RESOLUTION_CACHE_CONTROL
    try:
        resolved = await _recover_preledger_chat_submission(
            principal=principal,
            submission_id=str(submission_id),
        )
        if isinstance(resolved, ChatSubmissionPreLedgerAbsenceResponse):
            return resolved
        return _require_chat_submission_admitted(
            await _admit_chat_submission(principal=principal, submission_id=str(submission_id))
        )
    except HTTPException as exc:
        headers = {**(exc.headers or {}), "Cache-Control": _CHAT_SUBMISSION_RESOLUTION_CACHE_CONTROL}
        raise HTTPException(status_code=exc.status_code, detail=exc.detail, headers=headers) from exc


router.add_api_route(
    "/chat/submissions/{submission_id}",
    get_chat_submission,
    methods=["GET"],
    response_model=ChatSubmissionResponse,
    route_class_override=_ChatSubmissionNoStoreRoute,
)
router.add_api_route(
    "/chat/submissions/{submission_id}/retry-admission",
    retry_chat_submission_admission,
    methods=["POST"],
    response_model=ChatSubmissionResponse | ChatSubmissionPreLedgerAbsenceResponse,
    route_class_override=_ChatSubmissionNoStoreRoute,
)
