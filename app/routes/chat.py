from fastapi import APIRouter, Depends, HTTPException, Query
from typing import Any

from app import repositories
from app.auth import AuthPrincipal, is_ai_admin, require_principal
from app.context_builder import record_initial_context_snapshot
from app.db import transaction
from app.intent_router import FileSummary, route_intent
from app.models import (
    CapabilitySuggestionResponse,
    ChatMessageResponse,
    ChatMessagesResponse,
    ChatSessionRequest,
    ChatSessionResponse,
    ChatSessionsResponse,
    ChatStreamRequest,
    ChatStreamResponse,
    IntentDecisionResponse,
    QueueRunPayload,
)
from app.product_events import initial_run_event_specs, intent_event_specs
from app.control_plane_contracts import sanitize_public_payload, sanitize_public_text, standard_trace_id
from app.projection_redaction import (
    capability_id_from_skill,
    default_skill_id_for_public_agent,
    internal_agent_id_for_request,
    public_agent_id_for_projection,
    redact_raw_skill_references,
    sanitize_user_control_input,
    strip_server_owned_control_metadata,
)
from app.queue import enqueue_run, get_queue_insight
from app.repositories import RepositoryConflictError, RepositoryNotFoundError
from app.settings import get_settings
from app.skills.pinning import (
    SkillVersionMaterializationError,
    build_skill_manifest_pins,
    build_skill_version_policy_manifest_pins,
    governed_locked_skill_version,
)
from app.skills.release_policy import release_decision_payload_for_locked_version, resolve_rollout_skill_decision
from app.skills.registry import BuiltinSkillRegistry

router = APIRouter()
_MISSING = object()


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
        raise HTTPException(status_code=500, detail="queue_payload_invalid") from exc


def _strip_server_owned_control_metadata(input_payload: object) -> object:
    if not isinstance(input_payload, dict):
        return input_payload
    cleaned = strip_server_owned_control_metadata(input_payload)
    return cleaned if isinstance(cleaned, dict) else {}


def _file_ids_from_request(request: ChatStreamRequest) -> list[str]:
    if request.file_ids:
        return request.file_ids
    file_ids: list[str] = []
    for attachment in request.attachments:
        value = attachment.get("file_id") or attachment.get("key") or attachment.get("id")
        if isinstance(value, str) and value.startswith("file_"):
            file_ids.append(value)
    return file_ids


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


def _file_row_matches_request_scope(row: dict[str, Any], request: ChatStreamRequest, principal: AuthPrincipal) -> bool:
    tenant_id = _row_value(row, "tenant_id", _MISSING)
    if tenant_id != principal.tenant_id:
        return False
    workspace_id = _row_value(row, "workspace_id", _MISSING)
    if workspace_id != request.workspace_id:
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


async def _file_summaries_for_intent(conn, request: ChatStreamRequest, principal: AuthPrincipal) -> list[FileSummary]:
    summaries = _file_summaries_from_request(request)
    for file_id in _file_ids_for_intent_lookup(request):
        existing = next((item for item in summaries if item.file_id == file_id), None)
        if existing and (existing.name or existing.content_type):
            continue
        row = await repositories.get_file(conn, tenant_id=principal.tenant_id, file_id=file_id)
        if not row or not _file_row_matches_request_scope(row, request, principal):
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
    if limit <= 0:
        return
    active_count = await repositories.count_active_runs_for_user(conn, tenant_id=tenant_id, user_id=user_id)
    if active_count >= limit:
        raise RepositoryConflictError("user_active_run_limit_exceeded")


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
    query_agent_id = _normalized_query_agent_id(agent_id)
    requested_agent_id = request.agent_id or query_agent_id or "general-agent"
    requested_skill_id = request.skill_id if is_ai_admin(principal) else None
    requested_agent_id, requested_skill_id = _normalize_request_selector(
        requested_agent_id,
        requested_skill_id,
        allow_raw_skill_agent_id=is_ai_admin(principal),
    )
    explicit_payload = _explicit_intent_payload(requested_agent_id, requested_skill_id)
    resolved_file_ids = _file_ids_from_request(request)
    request_input = request.input if is_ai_admin(principal) else sanitize_user_control_input(request.input)
    run_input = _strip_server_owned_control_metadata({"message": request.message, **request_input})
    try:
        async with transaction() as conn:
            if explicit_payload is None:
                decision = route_intent(
                    request.message,
                    await _file_summaries_for_intent(conn, request, principal),
                    confirmed_capability_id=request.confirmed_capability_id,
                )
                decision_payload = decision.as_payload()
                if decision.status == "needs_confirmation":
                    suggestions = [CapabilitySuggestionResponse.model_validate(item) for item in decision_payload["suggestions"]]
                    return ChatStreamResponse(
                        session_id=request.session_id,
                        run_id=None,
                        status="needs_confirmation",
                        intent_decision=_intent_response(decision_payload, principal),
                        suggestions=suggestions,
                    )
                resolved_agent_id = str(decision.agent_id)
                resolved_skill_id = str(decision.skill_id)
            else:
                decision_payload = explicit_payload
                resolved_agent_id = str(decision_payload["agent_id"])
                resolved_skill_id = str(decision_payload["skill_id"])
            skill = await repositories.resolve_agent_skill(
                conn,
                tenant_id=principal.tenant_id,
                agent_id=resolved_agent_id,
                skill_id=resolved_skill_id,
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
            await repositories.ensure_user(
                conn,
                tenant_id=principal.tenant_id,
                user_id=principal.user_id,
                display_name=principal.display_name,
            )
            session_id = await repositories.create_session(
                conn,
                tenant_id=principal.tenant_id,
                workspace_id=request.workspace_id,
                user_id=principal.user_id,
                agent_id=resolved_agent_id,
                title=request.title or request.message[:80],
                session_id=request.session_id,
            )
            run_id = await repositories.create_run(
                conn,
                tenant_id=principal.tenant_id,
                workspace_id=request.workspace_id,
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
                    "intent": decision_payload,
                },
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
                workspace_id=request.workspace_id,
                user_id=principal.user_id,
                session_id=session_id,
                run_id=run_id,
                file_ids=resolved_file_ids,
            )
            context_ref = await record_initial_context_snapshot(
                conn,
                tenant_id=principal.tenant_id,
                workspace_id=request.workspace_id,
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
    except RepositoryNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SkillVersionMaterializationError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RepositoryConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    queue_payload = _validate_queue_payload_for_enqueue(
        {
            "tenant_id": principal.tenant_id,
            "workspace_id": request.workspace_id,
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
            "context_snapshot_id": context_ref["context_snapshot_id"],
            "context_snapshot": context_ref,
        }
    )
    queue_position = await enqueue_run(queue_payload)
    return ChatStreamResponse(
        session_id=session_id,
        run_id=run_id,
        status="queued",
        queue_position=queue_position,
        queue_insight=await get_queue_insight(principal.tenant_id, user_id=principal.user_id),
        intent_decision=_intent_response(decision_payload, principal),
    )
