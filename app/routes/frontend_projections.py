from __future__ import annotations

from collections import defaultdict
from pathlib import PurePosixPath
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query

from app import repositories
from app.auth import AuthPrincipal, is_ai_admin, require_principal
from app.control_plane_contracts import sanitize_public_text, standard_trace_id
from app.db import transaction
from app.models import (
    AgentWorkspaceAgentResponse,
    AgentWorkspaceConsoleResponse,
    AgentWorkspaceMemoryContextPolicyResponse,
    AgentWorkspaceProjectionResponse,
    AgentWorkspaceRunSummaryResponse,
    AgentWorkspaceSessionResponse,
    AgentWorkspaceToolPermissionResponse,
    PersonaPresetListResponse,
    PersonaPresetPreferenceRequest,
    PersonaPresetResponse,
    PersonaPresetSnapshotResponse,
    RevealedFileGroupedListResponse,
    RevealedFileItemResponse,
    RevealedFileListResponse,
    RevealedFileSessionGroupResponse,
    RevealedFileSessionResponse,
)
from app.projection_redaction import capability_id_from_skill, internal_agent_id_for_request, public_agent_id_for_projection
from app.public_context_keys import safe_public_context_input_keys
from app.run_projection import artifact_card, progress_for_status, run_event_response, run_step_response
from app.tool_permission_projection import tool_permission_decision_endpoint
from app.validation import assert_safe_id

router = APIRouter()

PERSONA_READ = "persona_preset:read"
PERSONA_WRITE = "persona_preset:write"
PERSONA_ADMIN = "persona_preset:admin"
CHAT_READ = "chat:read"
SESSION_READ = "session:read"
ARTIFACT_READ = "artifact:download"

REVEALED_FILE_STATS_KEYS = ("total", "image", "video", "document", "code", "project", "other")

DEFAULT_PERSONA_CREATED_AT = "2026-06-28T00:00:00Z"
DEFAULT_PERSONA_PRESETS = [
    {
        "id": "default-general-agent",
        "scope": "global",
        "owner_user_id": None,
        "name": "General company assistant",
        "description": "Default governed assistant for internal knowledge, task planning, and daily collaboration.",
        "avatar": "Sparkles",
        "tags": ["general", "enterprise"],
        "system_prompt": "You are a governed company-internal AI assistant. Respect tenant policy, tool permissions, and audit boundaries.",
        "starter_prompts": [
            {"icon": "MessageSquare", "text": "Help me plan the next steps for this task."},
            {"icon": "Search", "text": "Find relevant information from the current context."},
        ],
        "skill_names": ["general-chat"],
        "visibility": "public",
        "status": "published",
        "source_preset_id": None,
        "copied_from_version": None,
        "version": 1,
        "usage_count": 0,
        "is_favorite": False,
        "is_pinned": True,
        "last_used_at": None,
        "created_by": "system",
        "updated_by": "system",
        "created_at": DEFAULT_PERSONA_CREATED_AT,
        "updated_at": DEFAULT_PERSONA_CREATED_AT,
    },
    {
        "id": "default-doc-reviewer",
        "scope": "global",
        "owner_user_id": None,
        "name": "Document review assistant",
        "description": "Default persona for document review, issue extraction, and revision suggestions.",
        "avatar": "FileText",
        "tags": ["document", "review"],
        "system_prompt": "You review documents carefully, produce grounded findings, and keep source evidence explicit.",
        "starter_prompts": [
            {"icon": "FileText", "text": "Review this document and list the main risks."},
        ],
        "skill_names": ["qa-file-reviewer"],
        "visibility": "public",
        "status": "published",
        "source_preset_id": None,
        "copied_from_version": None,
        "version": 1,
        "usage_count": 0,
        "is_favorite": False,
        "is_pinned": False,
        "last_used_at": None,
        "created_by": "system",
        "updated_by": "system",
        "created_at": DEFAULT_PERSONA_CREATED_AT,
        "updated_at": DEFAULT_PERSONA_CREATED_AT,
    },
]


def _effective_permission_set(principal: AuthPrincipal) -> set[str]:
    granted = {item.strip() for item in principal.permissions if item.strip()}
    if is_ai_admin(principal):
        granted.update({PERSONA_READ, PERSONA_WRITE, PERSONA_ADMIN, CHAT_READ, SESSION_READ, ARTIFACT_READ})
    if PERSONA_ADMIN in granted:
        granted.update({PERSONA_READ, PERSONA_WRITE})
    if PERSONA_WRITE in granted:
        granted.add(PERSONA_READ)
    return granted


def _require_permission(principal: AuthPrincipal, permission: str) -> None:
    if permission not in _effective_permission_set(principal):
        raise HTTPException(status_code=403, detail=f"missing_permission:{permission}")


def _has_permission(principal: AuthPrincipal, permission: str) -> bool:
    return permission in _effective_permission_set(principal)


def _safe_optional_id(value: str | None, field_name: str) -> str | None:
    if value is None or not str(value).strip():
        return None
    try:
        return assert_safe_id(str(value), field_name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _persona_projection(seed: dict[str, Any], principal: AuthPrincipal) -> PersonaPresetResponse:
    item = dict(seed)
    if item["scope"] == "user":
        item["owner_user_id"] = principal.user_id
    return PersonaPresetResponse.model_validate(item)


def _persona_rows(principal: AuthPrincipal) -> list[PersonaPresetResponse]:
    return [_persona_projection(item, principal) for item in DEFAULT_PERSONA_PRESETS]


def _filter_personas(
    rows: list[PersonaPresetResponse],
    *,
    scope: str | None = None,
    status: str | None = None,
    q: str | None = None,
    tag: str | None = None,
    favorite: bool | None = None,
    pinned: bool | None = None,
) -> list[PersonaPresetResponse]:
    query = (q or "").strip().lower()
    normalized_tag = (tag or "").strip().lower()
    filtered: list[PersonaPresetResponse] = []
    for row in rows:
        if scope and row.scope != scope:
            continue
        if status and row.status != status:
            continue
        if favorite is not None and bool(row.is_favorite) != favorite:
            continue
        if pinned is not None and bool(row.is_pinned) != pinned:
            continue
        if normalized_tag and normalized_tag not in {item.lower() for item in row.tags}:
            continue
        if query:
            haystack = " ".join([row.name, row.description, row.system_prompt, " ".join(row.tags)]).lower()
            if query not in haystack:
                continue
        filtered.append(row)
    return filtered


def _find_persona(principal: AuthPrincipal, preset_id: str) -> PersonaPresetResponse:
    safe_id = _safe_optional_id(preset_id, "preset_id")
    for row in _persona_rows(principal):
        if row.id == safe_id:
            return row
    raise HTTPException(status_code=404, detail="persona_preset_not_found")


def _coerce_bool_param(value: bool | None) -> bool | None:
    return value if value is None else bool(value)


@router.get("/persona-presets/", response_model=PersonaPresetListResponse)
async def list_persona_presets(
    scope: str | None = Query(default=None),
    status: str | None = Query(default=None),
    q: str | None = Query(default=None),
    tag: str | None = Query(default=None),
    favorite: bool | None = Query(default=None),
    pinned: bool | None = Query(default=None),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=12, ge=1, le=200),
    principal: AuthPrincipal = Depends(require_principal),
) -> PersonaPresetListResponse:
    """Return governed persona presets for the post-login role workbench."""

    _require_permission(principal, PERSONA_READ)
    filtered = _filter_personas(
        _persona_rows(principal),
        scope=scope,
        status=status,
        q=q,
        tag=tag,
        favorite=_coerce_bool_param(favorite),
        pinned=_coerce_bool_param(pinned),
    )
    return PersonaPresetListResponse(
        presets=filtered[skip : skip + limit],
        total=len(filtered),
        skip=skip,
        limit=limit,
    )


@router.get("/persona-presets/{preset_id}", response_model=PersonaPresetResponse)
async def get_persona_preset(
    preset_id: str,
    principal: AuthPrincipal = Depends(require_principal),
) -> PersonaPresetResponse:
    """Return one governed persona preset projection."""

    _require_permission(principal, PERSONA_READ)
    return _find_persona(principal, preset_id)


@router.patch("/persona-presets/{preset_id}/preference", response_model=PersonaPresetResponse)
async def update_persona_preference(
    preset_id: str,
    principal: AuthPrincipal = Depends(require_principal),
    payload: Any = Body(default=None),
) -> PersonaPresetResponse:
    """Accept user-local persona preference toggles without exposing admin CRUD."""

    _require_permission(principal, PERSONA_READ)
    request = PersonaPresetPreferenceRequest.model_validate(payload or {})
    row = _find_persona(principal, preset_id).model_copy(deep=True)
    if request.is_favorite is not None:
        row.is_favorite = request.is_favorite
    if request.is_pinned is not None:
        row.is_pinned = request.is_pinned
    return row


@router.post("/persona-presets/{preset_id}/use", response_model=PersonaPresetSnapshotResponse)
async def use_persona_preset(
    preset_id: str,
    principal: AuthPrincipal = Depends(require_principal),
) -> PersonaPresetSnapshotResponse:
    """Return the persona snapshot the chat shell can apply locally."""

    _require_permission(principal, PERSONA_READ)
    row = _find_persona(principal, preset_id)
    return PersonaPresetSnapshotResponse(
        preset_id=row.id,
        name=row.name,
        system_prompt=row.system_prompt,
        starter_prompts=row.starter_prompts,
        skill_names=row.skill_names,
        missing_skill_names=[],
        version=row.version,
        avatar=row.avatar,
    )


@router.post("/persona-presets/{preset_id}/copy", response_model=PersonaPresetResponse)
async def copy_persona_preset(
    preset_id: str,
    principal: AuthPrincipal = Depends(require_principal),
) -> PersonaPresetResponse:
    """Return a user-scoped copy projection when write permission is present."""

    _require_permission(principal, PERSONA_WRITE)
    row = _find_persona(principal, preset_id)
    copied = row.model_copy(deep=True)
    copied.id = f"user-copy-{row.id}"
    copied.scope = "user"
    copied.owner_user_id = principal.user_id
    copied.source_preset_id = row.id
    copied.copied_from_version = row.version
    copied.created_by = principal.user_id
    copied.updated_by = principal.user_id
    return copied


def _persona_write_not_backed(principal: AuthPrincipal) -> None:
    _require_permission(principal, PERSONA_WRITE)
    raise HTTPException(status_code=409, detail="persona_preset_write_contract_not_backed")


@router.post("/persona-presets/", response_model=PersonaPresetResponse)
async def create_persona_preset(
    principal: AuthPrincipal = Depends(require_principal),
    payload: Any = Body(default=None),
) -> PersonaPresetResponse:
    """Fail closed for persona creation until persistent CRUD is backed."""

    _persona_write_not_backed(principal)


@router.post("/persona-presets/batch", response_model=list[PersonaPresetResponse])
async def batch_create_persona_presets(
    principal: AuthPrincipal = Depends(require_principal),
    payload: Any = Body(default=None),
) -> list[PersonaPresetResponse]:
    """Fail closed for persona batch creation until persistent CRUD is backed."""

    _persona_write_not_backed(principal)


@router.put("/persona-presets/{preset_id}", response_model=PersonaPresetResponse)
async def update_persona_preset(
    preset_id: str,
    principal: AuthPrincipal = Depends(require_principal),
    payload: Any = Body(default=None),
) -> PersonaPresetResponse:
    """Fail closed for persona updates until persistent CRUD is backed."""

    _safe_optional_id(preset_id, "preset_id")
    _persona_write_not_backed(principal)


@router.delete("/persona-presets/{preset_id}")
async def delete_persona_preset(
    preset_id: str,
    principal: AuthPrincipal = Depends(require_principal),
) -> dict[str, str]:
    """Fail closed for persona delete until persistent CRUD is backed."""

    _safe_optional_id(preset_id, "preset_id")
    _require_permission(principal, PERSONA_WRITE)
    raise HTTPException(status_code=409, detail="persona_preset_write_contract_not_backed")


def _public_projection_principal(principal: AuthPrincipal) -> AuthPrincipal:
    return AuthPrincipal(
        user_id=principal.user_id,
        display_name=principal.display_name,
        tenant_id=principal.tenant_id,
        department_id=principal.department_id,
        roles=[],
        permissions=principal.permissions,
        source=principal.source,
    )


def _workspace_agent_projection(row: dict[str, Any]) -> AgentWorkspaceAgentResponse | None:
    raw_agent_id = str(row.get("id") or "")
    if not raw_agent_id:
        return None
    raw_skill_id = row.get("default_skill_id")
    public_agent_id = public_agent_id_for_projection(raw_agent_id, raw_skill_id) or raw_agent_id
    capability_id = capability_id_from_skill(raw_skill_id, raw_agent_id)
    return AgentWorkspaceAgentResponse(
        agent_id=public_agent_id,
        capability_id=capability_id,
        name=sanitize_public_text(row.get("name")) or public_agent_id,
        description=sanitize_public_text(row.get("description")),
        status=sanitize_public_text(row.get("status")) or "active",
        version=sanitize_public_text(row.get("skill_version")) or "platform-managed",
    )


def _workspace_session_projection(row: dict[str, Any]) -> AgentWorkspaceSessionResponse:
    raw_agent_id = str(row.get("agent_id") or "")
    public_agent_id = public_agent_id_for_projection(raw_agent_id) or raw_agent_id
    return AgentWorkspaceSessionResponse(
        session_id=str(row.get("id") or ""),
        workspace_id=str(row.get("workspace_id") or ""),
        agent_id=public_agent_id,
        capability_id=capability_id_from_skill(None, raw_agent_id),
        title=sanitize_public_text(row.get("title")),
        created_at=row.get("created_at"),
        updated_at=row.get("updated_at"),
    )


def _workspace_run_result_summary(row: dict[str, Any]) -> str:
    result = row.get("result_json") if isinstance(row.get("result_json"), dict) else {}
    for key in ("final_report", "safe_summary", "summary", "message", "output"):
        text = sanitize_public_text(result.get(key))
        if text:
            return text
    return ""


def _workspace_run_projection(row: dict[str, Any]) -> AgentWorkspaceRunSummaryResponse:
    raw_agent_id = str(row.get("agent_id") or "")
    raw_skill_id = str(row.get("skill_id") or "")
    public_agent_id = public_agent_id_for_projection(raw_agent_id, raw_skill_id)
    return AgentWorkspaceRunSummaryResponse(
        run_id=str(row.get("id") or ""),
        session_id=str(row.get("session_id") or ""),
        agent_id=public_agent_id,
        capability_id=capability_id_from_skill(raw_skill_id, raw_agent_id),
        trace_id=str(row.get("trace_id") or standard_trace_id(str(row.get("id") or ""))),
        status=str(row.get("status") or ""),
        progress=progress_for_status(str(row.get("status") or "")),
        result_summary=_workspace_run_result_summary(row),
        error_code="run_failed" if row.get("error_code") else None,
        error_message=sanitize_public_text(row.get("error_message")),
        created_at=row.get("created_at"),
        queued_at=row.get("queued_at"),
        started_at=row.get("started_at"),
        finished_at=row.get("finished_at"),
    )


def _event_visible_to_workspace(row: dict[str, Any]) -> bool:
    if row.get("visible_to_user") is not None:
        return bool(row.get("visible_to_user"))
    payload = row.get("payload_json") if isinstance(row.get("payload_json"), dict) else {}
    return bool(payload.get("visible_to_user", True))


def _workspace_permission_projection(row: dict[str, Any]) -> AgentWorkspaceToolPermissionResponse:
    run_id = str(row.get("run_id") or "")
    request_id = str(row.get("id") or "")
    risk_level = sanitize_public_text(row.get("risk_level")) or "low"
    if risk_level not in {"low", "medium", "high"}:
        risk_level = "low"
    return AgentWorkspaceToolPermissionResponse(
        permission_request_id=request_id,
        session_id=str(row.get("session_id") or ""),
        run_id=run_id,
        trace_id=str(row.get("trace_id") or standard_trace_id(run_id)),
        tool_id=sanitize_public_text(row.get("tool_id")) or "tool",
        tool_call_id=sanitize_public_text(row.get("tool_call_id")),
        action=sanitize_public_text(row.get("action")) or "execute",
        risk_level=risk_level,  # type: ignore[arg-type]
        write_capable=bool(row.get("write_capable")),
        status=sanitize_public_text(row.get("status")) or "pending",
        reason=sanitize_public_text(row.get("reason")),
        decision_endpoint=tool_permission_decision_endpoint(run_id, request_id),
        created_at=row.get("created_at"),
    )


def _workspace_memory_policy_projection(
    policy: dict[str, Any],
    *,
    public_agent_id: str | None,
    capability_id: str | None,
    latest_context: dict[str, Any] | None,
) -> AgentWorkspaceMemoryContextPolicyResponse:
    return AgentWorkspaceMemoryContextPolicyResponse(
        workspace_id=str(policy.get("workspace_id") or ""),
        agent_id=public_agent_id,
        capability_id=capability_id,
        memory_enabled=bool(policy.get("memory_enabled", True)),
        long_term_memory_enabled=bool(policy.get("long_term_memory_enabled", False)),
        retention_days=int(policy.get("retention_days") or 90),
        redaction_mode=sanitize_public_text(policy.get("redaction_mode")) or "standard",
        source=sanitize_public_text(policy.get("source")) or "default",
        reason=sanitize_public_text(policy.get("reason")),
        updated_at=policy.get("updated_at"),
        latest_context=latest_context,
    )


def _safe_count(value: object) -> int:
    return int(value) if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0


def _safe_context_label(value: object, default: str) -> str:
    label = sanitize_public_text(value)
    if not label:
        return default
    safe_labels = safe_public_context_input_keys([label])
    return safe_labels[0] if safe_labels else default


def _workspace_context_summary(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    payload = row.get("payload_json") if isinstance(row.get("payload_json"), dict) else {}
    used_context = payload.get("used_context_summary") if isinstance(payload.get("used_context_summary"), dict) else {}
    materials = payload.get("referenced_materials") if isinstance(payload.get("referenced_materials"), dict) else {}
    input_keys = used_context.get("input_keys") if isinstance(used_context.get("input_keys"), list) else []
    return {
        "source": _safe_context_label(used_context.get("source"), "not_recorded"),
        "referenced_materials": {
            "message_count": len(row.get("included_message_ids") or []) or _safe_count(materials.get("message_count")),
            "file_count": len(row.get("included_file_ids") or []) or _safe_count(materials.get("file_count")),
            "artifact_count": len(row.get("included_artifact_ids") or []) or _safe_count(materials.get("artifact_count")),
            "memory_record_count": len(row.get("included_memory_record_ids") or []) or _safe_count(materials.get("memory_record_count")),
        },
        "used_context_summary": {
            "source": _safe_context_label(used_context.get("source"), "not_recorded"),
            "input_keys": safe_public_context_input_keys(input_keys),
            "memory_policy_source": _safe_context_label(
                used_context.get("memory_policy_source"),
                "not_recorded",
            ),
            "long_term_memory_read": bool(used_context.get("long_term_memory_read")),
        },
    }


def _select_workspace_agent(
    agents: list[AgentWorkspaceAgentResponse],
    requested_agent_id: str | None,
) -> AgentWorkspaceAgentResponse | None:
    target_agent_id = requested_agent_id or "general-agent"
    for agent in agents:
        if agent.agent_id == target_agent_id:
            return agent
    if requested_agent_id:
        raise HTTPException(status_code=404, detail="agent_not_found")
    return agents[0] if agents else None


@router.get("/agent-workspace", response_model=AgentWorkspaceProjectionResponse)
async def get_agent_workspace_projection(
    workspace_id: str = Query(default="default"),
    agent_id: str | None = Query(default=None),
    session_id: str | None = Query(default=None),
    principal: AuthPrincipal = Depends(require_principal),
) -> AgentWorkspaceProjectionResponse:
    """Return the read-only Agent Workspace V1 projection for the current user."""

    _require_permission(principal, CHAT_READ)
    _require_permission(principal, SESSION_READ)
    safe_workspace_id = _safe_optional_id(workspace_id, "workspace_id") or "default"
    safe_agent_id = _safe_optional_id(agent_id, "agent_id")
    safe_session_id = _safe_optional_id(session_id, "session_id")
    projection_principal = _public_projection_principal(principal)
    artifact_read_allowed = _has_permission(principal, ARTIFACT_READ)

    async with transaction() as conn:
        agent_rows = await repositories.list_principal_lambchat_agents(
            conn,
            tenant_id=principal.tenant_id,
            actor_user_id=principal.user_id,
            department_id=principal.department_id,
            roles=principal.roles,
            is_admin=is_ai_admin(principal),
            permissions=principal.permissions,
        )
        agents = [item for item in (_workspace_agent_projection(row) for row in agent_rows) if item is not None]
        selected_agent = _select_workspace_agent(agents, safe_agent_id)
        internal_agent_id = (
            internal_agent_id_for_request(selected_agent.agent_id)
            if selected_agent is not None
            else None
        )
        selected_capability_id = selected_agent.capability_id if selected_agent else None

        session_rows = await repositories.list_agent_workspace_sessions(
            conn,
            tenant_id=principal.tenant_id,
            workspace_id=safe_workspace_id,
            user_id=principal.user_id,
            agent_id=internal_agent_id,
            limit=20,
        )
        run_rows = await repositories.list_agent_workspace_runs(
            conn,
            tenant_id=principal.tenant_id,
            workspace_id=safe_workspace_id,
            user_id=principal.user_id,
            agent_id=internal_agent_id,
            session_id=safe_session_id,
            limit=10,
        )
        console_run = run_rows[0] if run_rows else None
        console_events: list[dict[str, Any]] = []
        console_steps: list[dict[str, Any]] = []
        artifacts: list[dict[str, Any]] = []
        latest_context: dict[str, Any] | None = None
        if console_run is not None:
            run_id = str(console_run.get("id") or "")
            event_rows = await repositories.list_run_events(
                conn,
                tenant_id=principal.tenant_id,
                run_id=run_id,
                limit=100,
            )
            step_rows = await repositories.list_run_steps(conn, tenant_id=principal.tenant_id, run_id=run_id)
            artifact_rows = (
                await repositories.list_run_artifacts(conn, tenant_id=principal.tenant_id, run_id=run_id)
                if artifact_read_allowed
                else []
            )
            context_row = await repositories.get_latest_authorized_executor_context_snapshot(
                conn,
                tenant_id=principal.tenant_id,
                user_id=principal.user_id,
                run_id=run_id,
            )
            console_events = [
                run_event_response(run_id, row, principal=projection_principal)
                for row in event_rows
                if _event_visible_to_workspace(row)
            ]
            console_steps = [run_step_response(row, principal=projection_principal) for row in step_rows]
            artifacts = [artifact_card(row, principal=projection_principal) for row in artifact_rows]
            latest_context = _workspace_context_summary(context_row)

        permission_rows = await repositories.list_agent_workspace_tool_permissions(
            conn,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            workspace_id=safe_workspace_id,
            agent_id=internal_agent_id,
            session_id=safe_session_id,
            status="pending",
            limit=50,
        )
        pending_permissions = [_workspace_permission_projection(row) for row in permission_rows]
        policy = await repositories.get_effective_memory_policy(
            conn,
            tenant_id=principal.tenant_id,
            workspace_id=safe_workspace_id,
            user_id=principal.user_id,
            agent_id=internal_agent_id,
        )

    latest_runs = [_workspace_run_projection(row) for row in run_rows]
    console_status = latest_runs[0].status if latest_runs else "idle"
    next_after_sequence = max((int(event.get("sequence") or 0) for event in console_events), default=0)
    return AgentWorkspaceProjectionResponse(
        workspace_id=safe_workspace_id,
        selected_agent=selected_agent,
        agents=agents,
        sessions=[_workspace_session_projection(row) for row in session_rows],
        latest_runs=latest_runs,
        run_console=AgentWorkspaceConsoleResponse(
            run_id=latest_runs[0].run_id if latest_runs else None,
            status=console_status,
            next_after_sequence=next_after_sequence,
            events=console_events,
            steps=console_steps,
        ),
        artifacts=artifacts,
        pending_tool_permissions=pending_permissions,
        memory_context_policy=_workspace_memory_policy_projection(
            policy,
            public_agent_id=selected_agent.agent_id if selected_agent else None,
            capability_id=selected_capability_id,
            latest_context=latest_context,
        ),
    )


def _file_type_for(row: dict[str, Any]) -> str:
    content_type = str(row.get("content_type") or "").lower()
    artifact_type = str(row.get("artifact_type") or "").lower()
    suffix = PurePosixPath(str(row.get("storage_key") or "")).suffix.lower()
    if artifact_type in {"project", "reveal_project"}:
        return "project"
    if content_type.startswith("image/") or suffix in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}:
        return "image"
    if content_type.startswith("video/") or suffix in {".mp4", ".webm", ".mov"}:
        return "video"
    if suffix in {".py", ".ts", ".tsx", ".js", ".jsx", ".json", ".yaml", ".yml", ".md", ".css", ".html"}:
        return "code"
    if (
        content_type in {"application/pdf", "application/msword"}
        or content_type.startswith("text/")
        or suffix in {".pdf", ".doc", ".docx", ".txt", ".rtf", ".xlsx", ".pptx"}
    ):
        return "document"
    return "other"


def _file_name_for(row: dict[str, Any]) -> str:
    label = str(row.get("label") or "").strip()
    if label:
        return label
    storage_name = PurePosixPath(str(row.get("storage_key") or "")).name
    return storage_name or str(row.get("id") or "artifact")


def _revealed_item(row: dict[str, Any]) -> RevealedFileItemResponse:
    artifact_id = str(row.get("id") or "")
    run_id = str(row.get("run_id") or "")
    session_id = str(row.get("session_id") or "")
    file_type = _file_type_for(row)
    source = "reveal_project" if file_type == "project" else "reveal_file"
    return RevealedFileItemResponse(
        id=artifact_id,
        file_key=artifact_id,
        file_name=_file_name_for(row),
        file_type=file_type,  # type: ignore[arg-type]
        mime_type=str(row.get("content_type") or "") or None,
        file_size=int(row.get("size_bytes") or 0),
        url=f"/api/ai/artifacts/{artifact_id}/download" if artifact_id else None,
        session_id=session_id,
        session_name=row.get("session_name"),
        trace_id=str(row.get("trace_id") or standard_trace_id(run_id or artifact_id)),
        project_id=row.get("workspace_id"),
        user_id=str(row.get("user_id") or ""),
        source=source,  # type: ignore[arg-type]
        description=str(row.get("artifact_type") or "") or None,
        original_path=_file_name_for(row) or None,
        created_at=row.get("created_at"),
        is_favorite=False,
        card_preview={
            "kind": "project" if file_type == "project" else ("document" if file_type == "document" else "fallback"),
            "title": _file_name_for(row),
            "subtitle": str(row.get("artifact_type") or "") or None,
        },
        project_meta=None,
    )


def _filter_revealed_items(
    rows: list[dict[str, Any]],
    *,
    file_type: str | None,
    favorites_only: bool,
) -> list[RevealedFileItemResponse]:
    items = [_revealed_item(row) for row in rows]
    if file_type:
        items = [item for item in items if item.file_type == file_type]
    if favorites_only:
        items = [item for item in items if item.is_favorite]
    return items


async def _revealed_items(
    principal: AuthPrincipal,
    *,
    session_id: str | None = None,
    project_id: str | None = None,
    search: str | None = None,
    sort_by: str = "created_at",
    sort_order: str = "desc",
    file_type: str | None = None,
    favorites_only: bool = False,
) -> list[RevealedFileItemResponse]:
    async with transaction() as conn:
        rows = await repositories.list_revealed_artifacts(
            conn,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            session_id=session_id,
            project_id=project_id,
            search=search,
            sort_by=sort_by,
            sort_order=sort_order,
        )
    return _filter_revealed_items(rows, file_type=file_type, favorites_only=favorites_only)


def _page(items: list[Any], *, page: int, page_size: int) -> list[Any]:
    start = (page - 1) * page_size
    return items[start : start + page_size]


@router.get("/files/revealed", response_model=RevealedFileListResponse)
async def list_revealed_files(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    file_type: str | None = Query(default=None),
    session_id: str | None = Query(default=None),
    project_id: str | None = Query(default=None),
    search: str | None = Query(default=None),
    sort_by: str = Query(default="created_at"),
    sort_order: str = Query(default="desc"),
    favorites_only: bool = Query(default=False),
    principal: AuthPrincipal = Depends(require_principal),
) -> RevealedFileListResponse:
    """Return ACL-scoped revealed artifacts as file workbench items."""

    _require_permission(principal, ARTIFACT_READ)
    safe_session_id = _safe_optional_id(session_id, "session_id")
    safe_project_id = _safe_optional_id(project_id, "project_id")
    items = await _revealed_items(
        principal,
        session_id=safe_session_id,
        project_id=safe_project_id,
        search=search,
        sort_by=sort_by,
        sort_order=sort_order,
        file_type=file_type,
        favorites_only=favorites_only,
    )
    return RevealedFileListResponse(items=_page(items, page=page, page_size=page_size), total=len(items), page=page, page_size=page_size)


@router.get("/files/revealed/grouped", response_model=RevealedFileGroupedListResponse)
async def list_revealed_files_grouped(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    file_type: str | None = Query(default=None),
    project_id: str | None = Query(default=None),
    search: str | None = Query(default=None),
    sort_by: str = Query(default="created_at"),
    sort_order: str = Query(default="desc"),
    favorites_only: bool = Query(default=False),
    principal: AuthPrincipal = Depends(require_principal),
) -> RevealedFileGroupedListResponse:
    """Return ACL-scoped revealed artifacts grouped by session."""

    _require_permission(principal, ARTIFACT_READ)
    safe_project_id = _safe_optional_id(project_id, "project_id")
    items = await _revealed_items(
        principal,
        project_id=safe_project_id,
        search=search,
        sort_by=sort_by,
        sort_order=sort_order,
        file_type=file_type,
        favorites_only=favorites_only,
    )
    grouped: dict[str, list[RevealedFileItemResponse]] = defaultdict(list)
    for item in items:
        grouped[item.session_id].append(item)
    groups = [
        RevealedFileSessionGroupResponse(
            session_id=session_id,
            session_name=session_items[0].session_name,
            file_count=len(session_items),
            files=session_items,
        )
        for session_id, session_items in grouped.items()
    ]
    return RevealedFileGroupedListResponse(
        sessions=_page(groups, page=page, page_size=page_size),
        total_sessions=len(groups),
        page=page,
        page_size=page_size,
    )


@router.get("/files/revealed/stats")
async def get_revealed_file_stats(
    project_id: str | None = Query(default=None),
    search: str | None = Query(default=None),
    principal: AuthPrincipal = Depends(require_principal),
) -> dict[str, int]:
    """Return type counts for ACL-scoped revealed artifacts."""

    _require_permission(principal, ARTIFACT_READ)
    safe_project_id = _safe_optional_id(project_id, "project_id")
    items = await _revealed_items(principal, project_id=safe_project_id, search=search)
    stats = {key: 0 for key in REVEALED_FILE_STATS_KEYS}
    stats["total"] = len(items)
    for item in items:
        stats[item.file_type] += 1
    return stats


@router.get("/files/revealed/sessions", response_model=list[RevealedFileSessionResponse])
async def list_revealed_file_sessions(
    project_id: str | None = Query(default=None),
    search: str | None = Query(default=None),
    principal: AuthPrincipal = Depends(require_principal),
) -> list[RevealedFileSessionResponse]:
    """Return session filters for ACL-scoped revealed artifacts."""

    _require_permission(principal, ARTIFACT_READ)
    safe_project_id = _safe_optional_id(project_id, "project_id")
    async with transaction() as conn:
        rows = await repositories.list_revealed_artifact_sessions(
            conn,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            project_id=safe_project_id,
            search=search,
        )
    return [
        RevealedFileSessionResponse(
            session_id=str(row.get("session_id") or ""),
            session_name=row.get("session_name"),
            file_count=int(row.get("file_count") or 0),
        )
        for row in rows
    ]


@router.patch("/files/revealed/{file_id}/favorite")
async def toggle_revealed_file_favorite(
    file_id: str,
    principal: AuthPrincipal = Depends(require_principal),
) -> dict[str, bool]:
    """Accept the frontend favorite toggle without persisting unsupported state."""

    _require_permission(principal, ARTIFACT_READ)
    _safe_optional_id(file_id, "file_id")
    return {"is_favorite": False}
