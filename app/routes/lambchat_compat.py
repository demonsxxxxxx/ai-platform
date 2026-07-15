import asyncio
import json
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile
from fastapi.responses import StreamingResponse

from app.auth import AuthPrincipal, is_ai_admin, require_principal, sign_principal_session, verify_principal_session
from app.db import transaction
from app.model_catalog import build_model_catalog
from app.models import LoginRequest, SessionRenameRequest
from app import repositories, session_actions
from app.routes.auth import _login_principal
from app.routes.files import upload_file as upload_platform_file
from app.projection_redaction import capability_id_from_skill, public_agent_id_for_projection, redact_raw_skill_references
from app.control_plane_contracts import EVENT_ENVELOPE_SCHEMA_VERSION, sanitize_public_text, standard_trace_id
from app.routes.runs import artifact_card, event_visible_to_principal, run_event_response
from app.settings import get_settings

router = APIRouter()


def _json_default(value: Any) -> str:
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _sse(event: str, data: dict[str, Any], event_id: str | None = None) -> str:
    payload = json.dumps(data, ensure_ascii=False, default=_json_default)
    prefix = f"id: {event_id}\n" if event_id else ""
    return f"{prefix}event: {event}\ndata: {payload}\n\n"


def _session_payload(row: dict[str, Any]) -> dict[str, Any]:
    agent_id = public_agent_id_for_projection(row.get("agent_id"))
    return {
        "id": row["id"],
        "agent_id": agent_id,
        "name": row.get("title") or "新会话",
        "metadata": {"agent_id": agent_id, "workspace_id": row["workspace_id"]},
        "is_active": row.get("status", "active") == "active",
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
        "unread_count": 0,
    }


def _run_answer(run: dict[str, Any]) -> str:
    result = run.get("result_json") or {}
    if isinstance(result, dict):
        message = result.get("message")
        if isinstance(message, str) and message.strip():
            return message
    if run.get("error_message"):
        return str(run["error_message"])
    return ""


def _public_run_answer(run: dict[str, Any], principal: AuthPrincipal) -> str:
    answer = _run_answer(run)
    if is_ai_admin(principal):
        return answer
    return sanitize_public_text(answer)


def _public_terminal_text(run: dict[str, Any], principal: AuthPrincipal) -> str:
    status = _platform_status(str(run.get("status") or ""))
    answer = _public_run_answer(run, principal)
    if answer:
        return answer
    if status == "succeeded":
        return "任务完成"
    if status == "failed":
        return "run_failed"
    return ""


def _terminal_final_payload(
    run: dict[str, Any], principal: AuthPrincipal
) -> tuple[str, dict[str, str], str] | None:
    """Return the safe final user-facing payload that precedes terminal replay."""
    status = _platform_status(str(run.get("status") or ""))
    if status == "succeeded":
        return (
            "message:chunk",
            {"content": _public_terminal_text(run, principal) or "任务完成"},
            "info",
        )
    if status == "failed":
        # Keep final failure presentation code-only.  The frontend maps this
        # controlled marker to its localized product copy and never receives
        # executor/runtime text as a transport-like error frame.
        return (
            "final_detail",
            {"detail_kind": "failed", "detail_code": "run_failed"},
            "error",
        )
    return None


@dataclass(frozen=True)
class _CompatibilityWireEvent:
    """One ordered, public compatibility event for both live and history adapters."""

    id: str
    stream_event_type: str
    stream_data: dict[str, object]
    history_event: dict[str, object]
    terminal: bool = False


def _event_sequence_sort_key(event: dict[str, Any], position: int) -> tuple[int, int]:
    """Keep persisted compatibility playback monotonic even with malformed rows."""
    try:
        return (int(event.get("sequence")), position)
    except (TypeError, ValueError):
        return (2**63 - 1, position)


def _compatibility_events_for_run(
    run: dict[str, Any],
    run_events: list[dict[str, Any]],
    artifacts: list[dict[str, Any]],
    principal: AuthPrincipal,
    *,
    user_messages: list[dict[str, Any]] | None = None,
) -> list[_CompatibilityWireEvent]:
    """Build the sole public terminal wire, ordered for live and history replay."""
    run_id = str(run["id"])
    trace_id = str(run.get("trace_id") or standard_trace_id(run_id))
    compatibility_events: list[_CompatibilityWireEvent] = []

    for message in user_messages or []:
        message_id = str(message.get("id") or "")
        if (
            not message_id
            or str(message.get("run_id") or "") != run_id
            or str(message.get("role") or "") != "user"
        ):
            continue
        message_data = {
            "message_id": message_id,
            "run_id": run_id,
            "content": str(message.get("content") or ""),
        }
        compatibility_events.append(
            _CompatibilityWireEvent(
                id=message_id,
                stream_event_type="user:message",
                stream_data=message_data,
                history_event={
                    "id": message_id,
                    "type": "user:message",
                    "event_type": "user:message",
                    "timestamp": message.get("created_at"),
                    "run_id": run_id,
                    "data": message_data,
                },
            )
        )

    for position, event in sorted(
        enumerate(run_events),
        key=lambda item: _event_sequence_sort_key(item[1], item[0]),
    ):
        raw_event_type = str(event.get("event_type") or "")
        if (
            raw_event_type in CHAT_STREAM_REPLAY_SKIP_EVENT_TYPES
            or raw_event_type in CHAT_STREAM_TERMINAL_EVENT_TYPES
            or not event_visible_to_principal(event, principal)
        ):
            continue
        envelope = run_event_response(run_id, event, principal=principal)
        history_data = _event_payload(
            event,
            principal,
            envelope["payload"],
            event_type=str(envelope["event_type"]),
            stage=str(envelope["stage"]),
        )
        compatibility_events.append(
            _CompatibilityWireEvent(
                id=str(event["id"]),
                stream_event_type="run_event",
                stream_data=envelope,
                history_event={
                    "id": event["id"],
                    "schema_version": envelope["schema_version"],
                    "trace_id": envelope["trace_id"],
                    # Production history preserves the public persisted event
                    # type at the outer level; it is not a synthetic run_event.
                    "type": envelope["type"],
                    "event_type": envelope["event_type"],
                    "stage": envelope["stage"],
                    "severity": envelope["severity"],
                    "visible_to_user": envelope["visible_to_user"],
                    "payload": envelope["payload"],
                    "sequence": envelope["sequence"],
                    "data": history_data,
                    "timestamp": event.get("created_at"),
                    "run_id": run_id,
                },
            )
        )

    for artifact in sorted(
        artifacts,
        key=lambda row: (str(row.get("created_at") or ""), str(row.get("id") or "")),
    ):
        artifact_id = str(artifact["id"])
        public_artifact = artifact_card(artifact, principal=principal)
        compatibility_events.append(
            _CompatibilityWireEvent(
                id=f"{artifact_id}:artifact",
                stream_event_type="artifact_card",
                stream_data=public_artifact,
                history_event={
                    "id": f"{artifact_id}:artifact",
                    "schema_version": EVENT_ENVELOPE_SCHEMA_VERSION,
                    "trace_id": str(artifact.get("trace_id") or trace_id),
                    "type": "artifact_card",
                    "event_type": "artifact_card",
                    "stage": "artifact",
                    "severity": "info",
                    "visible_to_user": True,
                    "payload": public_artifact,
                    "data": public_artifact,
                    "timestamp": artifact.get("created_at"),
                    "run_id": run_id,
                },
            )
        )

    status = _platform_status(str(run.get("status") or ""))
    final_payload = _terminal_final_payload(run, principal)
    if final_payload is not None:
        event_type, payload, severity = final_payload
        final_data = {"run_id": run_id, **payload}
        compatibility_events.append(
            _CompatibilityWireEvent(
                id=f"{run_id}:final",
                stream_event_type=event_type,
                stream_data=final_data,
                history_event={
                    "id": f"{run_id}:final",
                    "schema_version": EVENT_ENVELOPE_SCHEMA_VERSION,
                    "trace_id": trace_id,
                    "type": event_type,
                    "event_type": event_type,
                    "stage": "answer",
                    "severity": severity,
                    "visible_to_user": True,
                    "payload": final_data,
                    "data": final_data,
                    "timestamp": run.get("finished_at"),
                    "run_id": run_id,
                },
            )
        )

    if status in {"succeeded", "failed", "cancelled"}:
        terminal_data = {"run_id": run_id, "status": status}
        compatibility_events.append(
            _CompatibilityWireEvent(
                id=f"{run_id}:terminal:{status}",
                stream_event_type="done",
                stream_data=terminal_data,
                history_event={
                    "id": f"{run_id}:terminal:{status}",
                    "schema_version": EVENT_ENVELOPE_SCHEMA_VERSION,
                    "trace_id": trace_id,
                    "type": "done",
                    "event_type": "done",
                    "stage": "terminal",
                    "severity": "error" if status == "failed" else "info",
                    "visible_to_user": True,
                    "payload": terminal_data,
                    "data": terminal_data,
                    "timestamp": run.get("finished_at"),
                    "run_id": run_id,
                },
                terminal=True,
            )
        )
    return compatibility_events


def _public_error_text(run: dict[str, Any], principal: AuthPrincipal) -> str:
    if is_ai_admin(principal):
        return str(run.get("error_message") or run.get("error_code") or "")
    error_message = sanitize_public_text(run.get("error_message"))
    if error_message:
        return error_message
    error_code = sanitize_public_text(run.get("error_code"))
    if error_code:
        return error_code
    return "run_failed" if _platform_status(str(run.get("status") or "")) == "failed" else ""


def _platform_status(status: str) -> str:
    return "cancelled" if status == "canceled" else status


def _lambchat_status(status: str) -> str:
    status = _platform_status(status)
    return {
        "succeeded": "completed",
        "failed": "error",
        "queued": "pending",
        "running": "running",
    }.get(status, status)


def _event_payload(
    row: dict[str, Any],
    principal: AuthPrincipal,
    payload: dict[str, Any] | None = None,
    *,
    event_type: str | None = None,
    stage: str | None = None,
) -> dict[str, Any]:
    if payload is None:
        raw_payload = row.get("payload_json") or {}
        if not isinstance(raw_payload, dict):
            raw_payload = {}
        if not is_ai_admin(principal):
            redacted = redact_raw_skill_references(raw_payload)
            raw_payload = redacted if isinstance(redacted, dict) else {}
        payload = raw_payload
    message = str(row.get("message") or "")
    if not is_ai_admin(principal):
        message = sanitize_public_text(message)
    public_event_type = event_type or str(row.get("event_type") or "")
    public_stage = stage or str(row.get("stage") or "")
    if public_event_type == "error":
        return {"error": message or "run_failed", **payload}
    if public_stage == "queue":
        return {"status": "queued", "message": message, **payload}
    return {"content": message, "status": public_stage, **payload}


@router.post("/auth/login")
async def login(request: LoginRequest, response: Response) -> dict[str, object]:
    principal = await _login_principal(request, response)
    token = sign_principal_session(principal)
    settings = get_settings()
    response.set_cookie(
        settings.ai_session_cookie_name,
        token,
        max_age=settings.ai_session_max_age_seconds,
        httponly=True,
        samesite="lax",
        secure=settings.ai_session_cookie_secure,
        path="/",
    )
    return {
        "access_token": token,
        "refresh_token": token,
        "token_type": "bearer",
        "expires_in": settings.ai_session_max_age_seconds,
    }


@router.get("/auth/me")
async def me(principal: AuthPrincipal = Depends(require_principal)) -> dict[str, object]:
    return {
        "id": principal.user_id,
        "username": principal.user_id,
        "email": "",
        "avatar_url": None,
        "roles": principal.roles,
        "permissions": principal.permissions,
        "is_active": True,
        "metadata": {"display_name": principal.display_name, "source": principal.source},
        "created_at": "",
        "updated_at": "",
    }


@router.post("/auth/refresh")
async def refresh(payload: dict[str, str]) -> dict[str, object]:
    principal = verify_principal_session(payload.get("refresh_token") or "")
    token = sign_principal_session(principal)
    return {
        "access_token": token,
        "refresh_token": token,
        "token_type": "bearer",
        "expires_in": get_settings().ai_session_max_age_seconds,
    }


@router.get("/auth/oauth/providers")
async def oauth_providers() -> dict[str, object]:
    return {
        "providers": [],
        "registration_enabled": False,
        "turnstile": {
            "enabled": False,
            "site_key": "",
            "require_on_login": False,
            "require_on_register": False,
            "require_on_password_change": False,
        },
    }


UI_PERMISSIONS = [
    "agent:use",
    "artifact:download",
    "model:admin",
    "settings:manage",
    "admin:status",
    "chat:read",
    "chat:write",
    "session:read",
    "session:write",
    "file:upload",
    "file:upload:document",
    "skill:read",
    "skill:write",
    "skill:delete",
    "skill:admin",
    "marketplace:read",
    "marketplace:publish",
    "marketplace:admin",
    "user:read",
    "user:admin",
    "settings:read",
    "settings:admin",
    "feedback:read",
    "feedback:admin",
    "notification:read",
    "notification:admin",
]

CHAT_STREAM_REPLAY_SKIP_EVENT_TYPES = {"assistant_delta"}
CHAT_STREAM_TERMINAL_EVENT_TYPES = {"run_succeeded", "run_failed", "run_cancelled", "run_canceled"}


def _profile_payload(principal: AuthPrincipal, metadata: dict[str, Any] | None = None) -> dict[str, object]:
    merged_metadata = {"display_name": principal.display_name, "source": principal.source}
    if metadata:
        merged_metadata.update(metadata)
    return {
        "id": principal.user_id,
        "username": principal.user_id,
        "email": "",
        "avatar_url": None,
        "roles": principal.roles,
        "permissions": principal.permissions,
        "is_active": True,
        "metadata": merged_metadata,
        "created_at": "",
        "updated_at": "",
    }


@router.get("/auth/permissions")
async def permissions() -> dict[str, object]:
    permission_infos = [
        {"value": item, "label": item, "description": item}
        for item in UI_PERMISSIONS
    ]
    return {
        "groups": [{"name": "AI Platform POC", "permissions": permission_infos}],
        "all_permissions": permission_infos,
    }


@router.get("/auth/profile")
async def profile(principal: AuthPrincipal = Depends(require_principal)) -> dict[str, object]:
    return _profile_payload(principal)


@router.put("/auth/profile/metadata")
async def update_profile_metadata(
    payload: dict[str, Any], principal: AuthPrincipal = Depends(require_principal)
) -> dict[str, object]:
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    return _profile_payload(principal, metadata=metadata)


@router.get("/agent/models/available")
async def available_models() -> dict[str, object]:
    return build_model_catalog(get_settings())


@router.get("/agent/models/")
async def model_configs() -> dict[str, object]:
    catalog = build_model_catalog(get_settings())
    models = [{**model, "enabled": True, "order": index} for index, model in enumerate(catalog["models"], start=1)]
    return {**catalog, "models": models}


@router.get("/settings")
@router.get("/settings/")
async def settings() -> dict[str, object]:
    return {"settings": {}}


@router.get("/version")
async def version() -> dict[str, object]:
    return {"version": "ai-platform-poc"}


@router.get("/projects")
@router.get("/projects/")
async def projects() -> list[object]:
    return []


@router.get("/notifications/active")
async def active_notifications() -> dict[str, object]:
    return {"notifications": []}


@router.get("/upload/config")
async def upload_config() -> dict[str, object]:
    max_file_size = 52428800
    return {
        "enabled": True,
        "provider": "ai-platform",
        "uploadLimits": {
            "image": max_file_size,
            "video": max_file_size,
            "audio": max_file_size,
            "document": max_file_size,
            "maxFiles": 10,
        },
        "max_file_size": max_file_size,
        "allowed_extensions": ["docx", "txt", "pdf"],
        "categories": ["document"],
    }


@router.post("/upload/check")
async def upload_check() -> dict[str, object]:
    return {"exists": False}


@router.post("/upload/file")
async def upload_file(
    file: UploadFile = File(...),
    folder: str = "uploads",
    workspace_id: str = Form("default"),
    session_id: str | None = Form(None),
    principal: AuthPrincipal = Depends(require_principal),
) -> dict[str, object]:
    uploaded = await upload_platform_file(
        file=file,
        workspace_id=workspace_id,
        session_id=session_id,
        principal=principal,
    )
    filename = file.filename or uploaded.file_id
    mime_type = file.content_type or "application/octet-stream"
    return {
        "key": uploaded.file_id,
        "file_id": uploaded.file_id,
        "url": f"/api/ai/files/{uploaded.file_id}",
        "name": filename,
        "type": folder,
        "mime_type": mime_type,
        "mimeType": mime_type,
        "size": uploaded.size_bytes,
        "sha256": uploaded.sha256,
    }


@router.get("/tools")
async def tools() -> dict[str, object]:
    return {"tools": []}


@router.get("/roles")
@router.get("/roles/")
async def roles(skip: int = 0, limit: int = 100, q: str | None = None) -> dict[str, object]:
    limit = max(1, min(limit, 200))
    skip = max(0, skip)
    return {"roles": [], "total": 0, "skip": skip, "limit": limit, "q": q or ""}


@router.get("/sessions")
async def sessions(principal: AuthPrincipal = Depends(require_principal)) -> dict[str, object]:
    async with transaction() as conn:
        rows = await repositories.list_authorized_sessions(
            conn,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
        )
    items = [_session_payload(row) for row in rows]
    return {"sessions": items, "total": len(items), "skip": 0, "limit": 100, "has_more": False}


@router.get("/sessions/{session_id}")
async def get_session(session_id: str, principal: AuthPrincipal = Depends(require_principal)) -> dict[str, object]:
    async with transaction() as conn:
        row = await repositories.get_authorized_lambchat_session(
            conn,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            session_id=session_id,
        )
    if row is None:
        raise HTTPException(status_code=404, detail="session_not_found")
    return _session_payload(row)


@router.patch("/sessions/{session_id}")
async def rename_session(
    session_id: str,
    request: SessionRenameRequest,
    principal: AuthPrincipal = Depends(require_principal),
) -> dict[str, object]:
    try:
        async with transaction() as conn:
            row = await session_actions.rename_session(
                conn,
                principal=principal,
                session_id=session_id,
                title=request.name,
            )
    except session_actions.SessionActionValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except session_actions.SessionActionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="session_not_found") from exc
    return {"status": "updated", "session": _session_payload(row)}


@router.delete("/sessions/{session_id}")
async def delete_session(
    session_id: str,
    principal: AuthPrincipal = Depends(require_principal),
) -> dict[str, object]:
    try:
        async with transaction() as conn:
            result = await session_actions.delete_session(conn, principal=principal, session_id=session_id)
    except session_actions.SessionActionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="session_not_found") from exc
    return {
        "status": "deleted",
        "already_deleted": result["already_deleted"],
        "session": _session_payload(result["session"]),
    }


@router.post("/sessions/{session_id}/messages/{message_id}/fork")
async def fork_session_message(
    session_id: str,
    message_id: str,
    principal: AuthPrincipal = Depends(require_principal),
) -> dict[str, object]:
    try:
        async with transaction() as conn:
            result = await session_actions.fork_session_message(
                conn,
                principal=principal,
                session_id=session_id,
                message_id=message_id,
            )
    except session_actions.SessionActionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="session_not_found") from exc
    return {
        "source_session_id": result["source_session_id"],
        "session": _session_payload(result["session"]),
    }


@router.get("/sessions/{session_id}/runs")
async def session_runs(
    session_id: str,
    limit: int = 20,
    trace_id: str | None = None,
    principal: AuthPrincipal = Depends(require_principal),
) -> dict[str, object]:
    async with transaction() as conn:
        session = await repositories.get_authorized_lambchat_session(
            conn,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            session_id=session_id,
        )
        if session is None:
            raise HTTPException(status_code=404, detail="session_not_found")
        rows = await repositories.list_authorized_session_runs(
            conn,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            session_id=session_id,
            limit=max(1, min(limit, 100)),
        )
    runs = []
    for row in rows:
        if trace_id and row.get("trace_id") != trace_id:
            continue
        item = {
            "id": row["id"],
            "run_id": row["id"],
            "trace_id": row.get("trace_id") or standard_trace_id(str(row["id"])),
            "agent_id": row["agent_id"]
            if is_ai_admin(principal)
            else public_agent_id_for_projection(row.get("agent_id"), row.get("skill_id")),
            "capability_id": capability_id_from_skill(row["skill_id"], row["agent_id"]),
            "status": _platform_status(str(row["status"])),
            "error": _public_error_text(row, principal),
            "created_at": row.get("created_at"),
            "started_at": row.get("started_at") or row.get("queued_at") or row.get("created_at"),
            "completed_at": row.get("finished_at"),
            "finished_at": row.get("finished_at"),
        }
        if is_ai_admin(principal):
            item["skill_id"] = row["skill_id"]
        runs.append(item)
    return {
        "session_id": session_id,
        "runs": runs,
        "count": len(rows),
    }


@router.get("/sessions/{session_id}/events")
async def session_events(
    session_id: str,
    run_id: str | None = None,
    principal: AuthPrincipal = Depends(require_principal),
) -> dict[str, object]:
    async with transaction() as conn:
        session = await repositories.get_authorized_lambchat_session(
            conn,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            session_id=session_id,
        )
        if session is None:
            raise HTTPException(status_code=404, detail="session_not_found")
        if run_id is not None:
            target = await repositories.get_authorized_run(
                conn,
                tenant_id=principal.tenant_id,
                user_id=principal.user_id,
                run_id=run_id,
            )
            if target is None or target.get("session_id") != session_id:
                raise HTTPException(status_code=404, detail="run_not_found")
            target_runs = [target]
            current_run_id = run_id
        else:
            # Repository order is the authoritative run creation order. Event
            # completion timestamps can arrive late for an older overlapping
            # run and must never select the current subject.
            target_runs = await repositories.list_authorized_session_runs(
                conn,
                tenant_id=principal.tenant_id,
                user_id=principal.user_id,
                session_id=session_id,
                limit=50,
            )
            current_run_id = str(target_runs[0]["id"]) if target_runs else None
        authorized_messages = await repositories.list_authorized_messages(
            conn,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            session_id=session_id,
        )
        target_run_ids = {str(run["id"]) for run in target_runs}
        user_messages_by_run: dict[str, list[dict[str, Any]]] = {
            target_run_id: [] for target_run_id in target_run_ids
        }
        for message in authorized_messages:
            message_run_id = str(message.get("run_id") or "")
            if message_run_id in target_run_ids and str(message.get("role") or "") == "user":
                user_messages_by_run[message_run_id].append(message)
        events = []
        for run in reversed(target_runs):
            run_events = await repositories.list_run_events(conn, tenant_id=principal.tenant_id, run_id=run["id"])
            artifacts = await repositories.list_run_artifacts(
                conn,
                tenant_id=principal.tenant_id,
                run_id=run["id"],
            )
            events.extend(
                record.history_event
                for record in _compatibility_events_for_run(
                    run,
                    run_events,
                    artifacts,
                    principal,
                    user_messages=user_messages_by_run.get(str(run["id"]), []),
                )
            )
    return {
        "session_id": session_id,
        "run_id": run_id,
        "current_run_id": current_run_id,
        "events": events,
    }


@router.post("/sessions/{session_id}/generate-title")
async def generate_title(session_id: str, message: str = "", lang: str = "en") -> dict[str, str]:
    title = (message or "").strip().replace("\n", " ")[:32] or "新会话"
    return {"session_id": session_id, "title": title}


@router.post("/sessions/{session_id}/mark-read")
async def mark_read(session_id: str) -> dict[str, bool]:
    return {"success": True}


@router.post("/chat/sessions/{session_id}/cancel")
async def cancel_session(session_id: str) -> dict[str, object]:
    raise HTTPException(status_code=410, detail="session_cancel_unsupported_use_run_cancel")


@router.get("/chat/sessions/{session_id}/status")
async def chat_status(
    session_id: str,
    run_id: str | None = None,
    principal: AuthPrincipal = Depends(require_principal),
) -> dict[str, object]:
    async with transaction() as conn:
        session = await repositories.get_authorized_lambchat_session(
            conn,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            session_id=session_id,
        )
        if session is None:
            raise HTTPException(status_code=404, detail="session_not_found")
        if run_id is not None:
            # An explicit id is a precise, principal-scoped lookup: it must
            # not inherit the list endpoint's recency limit or another
            # session's state.
            target = await repositories.get_authorized_run(
                conn,
                tenant_id=principal.tenant_id,
                user_id=principal.user_id,
                run_id=run_id,
            )
            if target is None or target.get("session_id") != session_id:
                raise HTTPException(status_code=404, detail="run_not_found")
        else:
            # Preserve legacy latest-run behavior only for callers that did
            # not identify a run.
            rows = await repositories.list_authorized_session_runs(
                conn,
                tenant_id=principal.tenant_id,
                user_id=principal.user_id,
                session_id=session_id,
                limit=10,
            )
            target = rows[0] if rows else None
    raw_status = _platform_status(str(target["status"])) if target else "idle"
    return {"session_id": session_id, "run_id": run_id, "status": _lambchat_status(raw_status), "raw_status": raw_status}


@router.get("/chat/sessions/{session_id}/stream")
async def chat_session_stream(
    session_id: str,
    run_id: str,
    principal: AuthPrincipal = Depends(require_principal),
) -> StreamingResponse:
    async def stream():
        yield _sse("metadata", {"session_id": session_id, "run_id": run_id})
        last_status = ""
        seen_event_ids: set[str] = set()
        max_heartbeats = max(int(get_settings().run_event_stream_max_heartbeats), 1)
        for _ in range(max_heartbeats):
            async with transaction() as conn:
                run = await repositories.get_authorized_run(
                    conn,
                    tenant_id=principal.tenant_id,
                    user_id=principal.user_id,
                    run_id=run_id,
                )
                run_events = (
                    await repositories.list_run_events(conn, tenant_id=principal.tenant_id, run_id=run_id)
                    if run is not None
                    else []
                )
                artifacts = (
                    await repositories.list_run_artifacts(conn, tenant_id=principal.tenant_id, run_id=run_id)
                    if run is not None
                    else []
                )
            if run is None or run["session_id"] != session_id:
                yield _sse("error", {"error": "run_not_found"})
                yield _sse("done", {})
                return
            status = _platform_status(str(run["status"]))
            try:
                compatibility_events = _compatibility_events_for_run(
                    run,
                    run_events,
                    artifacts,
                    principal,
                )
            except HTTPException as exc:
                yield _sse("error", {"error": str(exc.detail)})
                yield _sse("done", {"status": "error"})
                return
            for record in compatibility_events:
                if record.id in seen_event_ids:
                    continue
                seen_event_ids.add(record.id)
                yield _sse(
                    record.stream_event_type,
                    record.stream_data,
                    event_id=record.id,
                )
                if record.terminal:
                    return
            if status != last_status and status in {"queued", "running"}:
                yield _sse("queue_update", {"status": "processing" if status == "running" else "queued"})
                last_status = status
            await asyncio.sleep(1)
        yield _sse("error", {"error": "stream_timeout"})
        yield _sse("done", {"status": "timeout"}, event_id=f"{run_id}:done")

    return StreamingResponse(stream(), media_type="text/event-stream")
