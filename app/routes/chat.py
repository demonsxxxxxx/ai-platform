from fastapi import APIRouter, Depends, HTTPException, Query
from typing import Any
from uuid import UUID

from app import repositories
from app.auth import AuthPrincipal, is_ai_admin, require_principal
from app.context_builder import record_initial_context_snapshot
from app.db import transaction
from app.intent_router import FileSummary, fallback_to_general_chat, route_intent
from app.model_catalog import resolve_model_selection
from app.models import (
    CapabilitySuggestionResponse,
    ChatMessageResponse,
    ChatMessagesResponse,
    ChatSessionRequest,
    ChatSessionResponse,
    ChatSessionsResponse,
    ChatStreamRequest,
    ChatStreamResponse,
    ChatSubmissionResponse,
    IntentDecisionResponse,
    QueueRunPayload,
)
from app.product_events import initial_run_event_specs, intent_event_specs
from app.queue_payload_validation import queue_payload_invalid_detail
from app.control_plane_contracts import sanitize_public_payload, sanitize_public_text, standard_trace_id
from app.projection_redaction import (
    capability_id_from_skill,
    default_skill_id_for_public_agent,
    internal_agent_id_for_request,
    public_agent_id_for_projection,
    redact_raw_skill_references,
    sanitize_user_control_input,
)
from app.queue import QueueAdmissionMetadata, enqueue_run, enqueue_run_with_metadata, get_queue_insight
from app.repositories import RepositoryConflictError, RepositoryNotFoundError
from app.settings import get_settings
from app.skills.lifecycle import is_user_runnable_status
from app.skills.pinning import (
    SkillVersionMaterializationError,
    attach_skill_snapshot_governance,
    build_skill_manifest_pins,
    build_skill_version_policy_manifest_pins,
    governed_locked_skill_version,
)
from app.skills.release_policy import release_decision_payload_for_locked_version, resolve_rollout_skill_decision
from app.skills.registry import BuiltinSkillRegistry
from app.validation import assert_safe_principal_user_id

router = APIRouter()
_MISSING = object()
_ORIGINAL_ENQUEUE_RUN = enqueue_run


def _chat_submission_http_error(*, status_code: int, code: str) -> HTTPException:
    """Return the sole server-controlled pre-persistence rejection signal."""

    return HTTPException(
        status_code=status_code,
        detail={
            "code": code,
            "submission_disposition": "rejected_before_persist",
        },
    )


def _submission_code(detail: object, fallback: str = "chat_submission_rejected") -> str:
    if isinstance(detail, dict) and isinstance(detail.get("code"), str):
        return str(detail["code"])
    if isinstance(detail, str) and detail:
        return detail
    return fallback


def _chat_stream_response_from_submission(row: dict[str, Any]) -> ChatStreamResponse:
    state = str(row.get("state") or "")
    if state == "rejected_before_persist":
        raise _chat_submission_http_error(
            status_code=409,
            code=str(row.get("rejection_code") or "chat_submission_rejected"),
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


async def _persist_pre_persistence_rejection(
    *,
    principal: AuthPrincipal,
    submission_id: str | None,
    request_fingerprint: str | None,
    workspace_id: str | None,
    session_id: str | None,
    code: str,
) -> None:
    """Record a deterministic rejection after the mutation transaction rolled back."""

    if submission_id is None or request_fingerprint is None:
        return
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
        if not created and row.get("request_fingerprint_sha256") != request_fingerprint:
            return
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
    if existing.get("request_fingerprint_sha256") != request_fingerprint:
        raise HTTPException(status_code=409, detail="submission_payload_mismatch")
    return _chat_stream_response_from_submission(existing)


async def _admit_chat_submission(
    *,
    principal: AuthPrincipal,
    submission_id: str,
) -> ChatSubmissionResponse:
    """Admit one already-persisted run without replaying chat creation work."""

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
        if str(submission.get("state")) in {"rejected_before_persist", "enqueue_failed", "needs_confirmation"}:
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
        execution_snapshot = repositories.copied_run_execution_snapshot(run.get("input_json"))
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
        try:
            queue_admission = await _enqueue_chat_run(queue_payload)
        except Exception:
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
            raise HTTPException(status_code=503, detail="queue_enqueue_failed")
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
    async with transaction() as conn:
        await repositories.append_capability_authorization_denial_audit(
            conn,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            error=error,
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
    try:
        skill_manifests = _skill_manifest_pins(skill_id, input_payload)
    except SkillVersionMaterializationError:
        raise
    return skill_manifests


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
    if run_id is _MISSING or run_id:
        return False
    return True


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
    return ChatSessionResponse(
        session_id=str(row["id"]),
        workspace_id=str(row["workspace_id"]),
        agent_id=public_agent_id_for_projection(raw_agent_id) or raw_agent_id,
        title=str(row.get("title") or ""),
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
    await repositories.enforce_user_active_run_admission(
        conn,
        tenant_id=tenant_id,
        user_id=user_id,
        limit=limit,
    )


@router.get("/chat/sessions", response_model=ChatSessionsResponse)
async def list_sessions(principal: AuthPrincipal = Depends(require_principal)) -> ChatSessionsResponse:
    async with transaction() as conn:
        rows = await repositories.list_authorized_sessions(conn, tenant_id=principal.tenant_id, user_id=principal.user_id)
    return ChatSessionsResponse(sessions=[_session_response(row) for row in rows])


@router.post("/chat/sessions", response_model=ChatSessionResponse)
async def create_chat_session(
    request: ChatSessionRequest,
    principal: AuthPrincipal = Depends(require_principal),
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
    principal: AuthPrincipal = Depends(require_principal),
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
    principal: AuthPrincipal = Depends(require_principal),
) -> ChatStreamResponse:
    try:
        assert_safe_principal_user_id(principal.user_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid_principal_user_id") from exc
    query_agent_id = _normalized_query_agent_id(agent_id)
    submission_id = str(request.submission_id) if request.submission_id is not None else None
    request_fingerprint = (
        repositories.chat_submission_fingerprint(
            {
                "request": request.model_dump(mode="json", exclude={"submission_id"}),
                "query_agent_id": query_agent_id,
            },
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
        )
        if submission_id is not None
        else None
    )
    requested_agent_id = request.agent_id or query_agent_id or "general-agent"
    if request.skill_id and not is_ai_admin(principal):
        await _persist_pre_persistence_rejection(
            principal=principal,
            submission_id=submission_id,
            request_fingerprint=request_fingerprint,
            workspace_id=request.workspace_id,
            session_id=request.session_id,
            code="raw_skill_selector_forbidden",
        )
        if submission_id is not None:
            raise _chat_submission_http_error(status_code=403, code="raw_skill_selector_forbidden")
        raise HTTPException(status_code=403, detail="raw_skill_selector_forbidden")
    requested_skill_id = request.skill_id if is_ai_admin(principal) else None
    if request.selected_skill is not None and request.skill_id:
        await _persist_pre_persistence_rejection(
            principal=principal,
            submission_id=submission_id,
            request_fingerprint=request_fingerprint,
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
    if request.selected_skill is not None:
        requested_skill_id = request.selected_skill.skill_id
    try:
        requested_model_selection = _requested_model_selection(request)
    except HTTPException as exc:
        code = _submission_code(exc.detail)
        await _persist_pre_persistence_rejection(
            principal=principal,
            submission_id=submission_id,
            request_fingerprint=request_fingerprint,
            workspace_id=request.workspace_id,
            session_id=request.session_id,
            code=code,
        )
        if submission_id is not None:
            raise _chat_submission_http_error(status_code=exc.status_code, code=code) from exc
        raise
    requested_model_id = requested_model_selection["id"] if requested_model_selection is not None else None
    requested_model_value = requested_model_selection["value"] if requested_model_selection is not None else None
    resolved_file_ids = _file_ids_from_request(request)
    try:
        run_input = _strip_server_owned_control_metadata(
            {"message": request.message, **request.input},
            redact_public=not is_ai_admin(principal),
        )
    except repositories.RepositoryAuthorizationError as exc:
        await _audit_capability_denial(principal, exc, source="chat_stream")
        await _persist_pre_persistence_rejection(
            principal=principal,
            submission_id=submission_id,
            request_fingerprint=request_fingerprint,
            workspace_id=request.workspace_id,
            session_id=request.session_id,
            code="capability_not_authorized",
        )
        if submission_id is not None:
            raise _chat_submission_http_error(status_code=403, code="capability_not_authorized") from exc
        raise HTTPException(status_code=403, detail="capability_not_authorized") from exc
    existing_submission = await _load_existing_chat_submission(
        principal=principal,
        submission_id=submission_id,
        request_fingerprint=request_fingerprint,
    )
    if existing_submission is not None:
        return existing_submission
    pending_submission_response: ChatStreamResponse | None = None
    effective_workspace_id = request.workspace_id
    try:
        async with transaction() as conn:
            if submission_id is not None and request_fingerprint is not None:
                await repositories.ensure_submission_principal(
                    conn,
                    tenant_id=principal.tenant_id,
                    user_id=principal.user_id,
                    display_name=principal.display_name,
                )
            continuation_session = None
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
                if request.selected_skill is None and request.skill_id is None:
                    requested_skill_id = None

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
                    if claimed_submission.get("request_fingerprint_sha256") != request_fingerprint:
                        raise HTTPException(status_code=409, detail="submission_payload_mismatch")
                    return _chat_stream_response_from_submission(claimed_submission)

            explicit_payload = _explicit_intent_payload(requested_agent_id, requested_skill_id)
            is_terminal_implicit_decision = False
            if explicit_payload is None:
                continuation_capability = (
                    capability_id_from_skill(None, requested_agent_id)
                    if continuation_session is not None
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
                )
                decision_payload = decision.as_payload()
                is_terminal_implicit_decision = (
                    continuation_session is None
                    and request.selected_skill is None
                    and request.skill_id is None
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
            elif request.selected_skill is not None:
                skill = await repositories.authorize_selected_run_capabilities(
                    conn,
                    expected_version=request.selected_skill.expected_version,
                    rollout_key=principal.user_id,
                    **authorization_kwargs,
                )
            else:
                skill = await repositories.authorize_run_capabilities(
                    conn,
                    **authorization_kwargs,
                )
            if "docx" in (skill.get("input_modes") or []) and not resolved_file_ids:
                raise RepositoryConflictError("file_required_for_skill")
            await enforce_user_active_run_limit(conn, tenant_id=principal.tenant_id, user_id=principal.user_id)
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
                    "file_ids": resolved_file_ids,
                    "input": run_input,
                    "executor_type": skill["executor_type"],
                    "skill_version": skill_version,
                    "release_decision": release_decision_payload,
                    "skill_manifests": skill_manifests,
                    "model_id": requested_model_id,
                    "model_value": requested_model_value,
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
                file_ids=resolved_file_ids,
            )
            await repositories.ensure_user(
                conn,
                tenant_id=principal.tenant_id,
                user_id=principal.user_id,
                display_name=principal.display_name,
            )
            session_id = await repositories.create_session(
                conn,
                tenant_id=principal.tenant_id,
                workspace_id=effective_workspace_id,
                user_id=principal.user_id,
                agent_id=resolved_agent_id,
                title=request.title or request.message[:80],
                session_id=session_id,
            )
            run_id = await repositories.create_run(
                conn,
                tenant_id=principal.tenant_id,
                workspace_id=effective_workspace_id,
                session_id=session_id,
                user_id=principal.user_id,
                agent_id=resolved_agent_id,
                skill_id=resolved_skill_id,
                input_json={
                    "input": run_input,
                    "file_ids": resolved_file_ids,
                    "executor_type": skill["executor_type"],
                    "skill_version": skill_version,
                    "release_decision": release_decision_payload,
                    "skill_manifests": queue_payload["skill_manifests"],
                    "intent": decision_payload,
                    "model_id": requested_model_id,
                    "model_value": requested_model_value,
                },
                principal_roles=principal.roles,
                principal_department_id=principal.department_id,
                auth_source=principal.source,
            )
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
                        "file_ids": resolved_file_ids,
                        "attachments": request.attachments,
                        "intent": decision_payload,
                    }
                )
                if not is_ai_admin(principal)
                else {
                    "skill_id": resolved_skill_id,
                    "file_ids": resolved_file_ids,
                    "attachments": request.attachments,
                    "intent": decision_payload,
                },
            )
            await repositories.bind_files_to_run(
                conn,
                tenant_id=principal.tenant_id,
                workspace_id=effective_workspace_id,
                user_id=principal.user_id,
                session_id=session_id,
                run_id=run_id,
                file_ids=resolved_file_ids,
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
                file_ids=resolved_file_ids,
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
                file_ids=resolved_file_ids,
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
    except HTTPException as exc:
        code = _submission_code(exc.detail)
        if 400 <= exc.status_code < 500:
            await _persist_pre_persistence_rejection(
                principal=principal,
                submission_id=submission_id,
                request_fingerprint=request_fingerprint,
                workspace_id=effective_workspace_id,
                session_id=request.session_id,
                code=code,
            )
        if submission_id is not None and 400 <= exc.status_code < 500:
            raise _chat_submission_http_error(status_code=exc.status_code, code=code) from exc
        raise
    except repositories.RepositoryAuthorizationError as exc:
        await _audit_capability_denial(principal, exc, source="chat_stream")
        await _persist_pre_persistence_rejection(
            principal=principal,
            submission_id=submission_id,
            request_fingerprint=request_fingerprint,
            workspace_id=effective_workspace_id,
            session_id=request.session_id,
            code="capability_not_authorized",
        )
        if submission_id is not None:
            raise _chat_submission_http_error(status_code=403, code="capability_not_authorized") from exc
        raise HTTPException(status_code=403, detail="capability_not_authorized") from exc
    except RepositoryNotFoundError as exc:
        code = str(exc)
        await _persist_pre_persistence_rejection(
            principal=principal,
            submission_id=submission_id,
            request_fingerprint=request_fingerprint,
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
            request_fingerprint=request_fingerprint,
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
            request_fingerprint=request_fingerprint,
            workspace_id=effective_workspace_id,
            session_id=request.session_id,
            code=code,
        )
        if submission_id is not None:
            raise _chat_submission_http_error(status_code=409, code=code) from exc
        raise HTTPException(status_code=409, detail=code) from exc
    queue_payload = _validate_queue_payload_for_enqueue(
        {
            **queue_payload,
            "session_id": session_id,
            "run_id": run_id,
            "context_snapshot_id": context_ref["context_snapshot_id"],
            "context_snapshot": context_ref,
        }
    )
    if submission_id is not None:
        try:
            admitted = await _admit_chat_submission(principal=principal, submission_id=submission_id)
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


@router.get("/chat/submissions/{submission_id}", response_model=ChatSubmissionResponse)
async def get_chat_submission(
    submission_id: UUID,
    principal: AuthPrincipal = Depends(require_principal),
) -> ChatSubmissionResponse:
    """Resolve a durable client submission without inferring from session history."""

    async with transaction() as conn:
        submission = await repositories.get_chat_submission(
            conn,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            submission_id=str(submission_id),
        )
    if submission is None:
        raise HTTPException(status_code=404, detail="chat_submission_not_found")
    return _chat_submission_resolution(submission)


@router.post("/chat/submissions/{submission_id}/retry-admission", response_model=ChatSubmissionResponse)
async def retry_chat_submission_admission(
    submission_id: UUID,
    principal: AuthPrincipal = Depends(require_principal),
) -> ChatSubmissionResponse:
    """Explicitly retry queue admission for one already-created run only."""

    return await _admit_chat_submission(principal=principal, submission_id=str(submission_id))
