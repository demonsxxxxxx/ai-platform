import asyncio
import json
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile
from fastapi.responses import StreamingResponse

from app.auth import AuthPrincipal, is_ai_admin, require_principal, sign_principal_session, verify_principal_session
from app.db import transaction
from app.model_catalog import build_model_catalog
from app.models import LoginRequest
from app import repositories
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
    "agent:admin",
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
    "channel:read",
    "channel:write",
    "channel:delete",
    "channel:admin",
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


@router.get("/agents")
async def agents(principal: AuthPrincipal = Depends(require_principal)) -> dict[str, object]:
    async with transaction() as conn:
        rows = await repositories.list_lambchat_agents(conn, tenant_id=principal.tenant_id)
    items = [
        {
            "id": public_agent_id_for_projection(row.get("id"), row.get("default_skill_id")),
            "name": row["name"],
            "description": row.get("description") or "",
            "version": "platform-managed",
            "sort_order": index,
            "supports_sandbox": False,
            "options": {},
        }
        for index, row in enumerate(rows, start=1)
    ]
    return {
        "agents": items,
        "count": len(items),
        "default_agent": "general-agent",
        "allowed_model_ids": None,
    }


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
        rows = await repositories.list_authorized_session_runs(
            conn,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            session_id=session_id,
            limit=50,
        )
        target_runs = [row for row in rows if run_id is None or row["id"] == run_id]
        events = []
        for run in reversed(target_runs):
            run_events = await repositories.list_run_events(conn, tenant_id=principal.tenant_id, run_id=run["id"])
            for event in run_events:
                if not event_visible_to_principal(event, principal):
                    continue
                envelope = run_event_response(str(run["id"]), event, principal=principal)
                events.append(
                    {
                        "id": event["id"],
                        "schema_version": envelope["schema_version"],
                        "trace_id": envelope["trace_id"],
                        "type": envelope["type"],
                        "event_type": envelope["event_type"],
                        "stage": envelope["stage"],
                        "severity": envelope["severity"],
                        "visible_to_user": envelope["visible_to_user"],
                        "payload": envelope["payload"],
                        "data": _event_payload(
                            event,
                            principal,
                            envelope["payload"],
                            event_type=envelope["event_type"],
                            stage=envelope["stage"],
                        ),
                        "timestamp": event.get("created_at"),
                        "run_id": run["id"],
                    }
                )
            run_status = _platform_status(str(run["status"]))
            if run_status in {"succeeded", "failed"}:
                answer = _public_terminal_text(run, principal)
                if answer:
                    event_type = "message:chunk" if run_status == "succeeded" else "error"
                    payload = {"content": answer} if run_status == "succeeded" else {"error": answer}
                    events.append(
                        {
                            "id": f"{run['id']}:answer",
                            "schema_version": EVENT_ENVELOPE_SCHEMA_VERSION,
                            "trace_id": str(run.get("trace_id") or standard_trace_id(str(run["id"]))),
                            "type": event_type,
                            "event_type": event_type,
                            "stage": "answer",
                            "severity": "info" if run_status == "succeeded" else "error",
                            "visible_to_user": True,
                            "payload": payload,
                            "data": payload,
                            "timestamp": run.get("finished_at"),
                            "run_id": run["id"],
                        }
                    )
    return {"session_id": session_id, "run_id": run_id, "events": events}


@router.post("/sessions/{session_id}/generate-title")
async def generate_title(session_id: str, message: str = "", lang: str = "en") -> dict[str, str]:
    title = (message or "").strip().replace("\n", " ")[:32] or "新会话"
    return {"session_id": session_id, "title": title}


@router.post("/sessions/{session_id}/mark-read")
async def mark_read(session_id: str) -> dict[str, bool]:
    return {"success": True}


@router.post("/chat/sessions/{session_id}/cancel")
async def cancel_session(session_id: str) -> dict[str, object]:
    return {"success": False, "message": "cancel_not_supported_in_poc", "session_id": session_id}


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
        rows = await repositories.list_authorized_session_runs(
            conn,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            session_id=session_id,
            limit=10,
        )
    target = next((row for row in rows if row["id"] == run_id), rows[0] if rows else None)
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
        seen_artifact_ids: set[str] = set()
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
            artifacts_by_id = {str(artifact["id"]): artifact for artifact in artifacts}

            def artifact_chunks(artifact_ids: list[object] | None = None) -> list[str]:
                selected_ids = [str(item) for item in artifact_ids or artifacts_by_id.keys()]
                chunks: list[str] = []
                for artifact_id in selected_ids:
                    if artifact_id in seen_artifact_ids:
                        continue
                    artifact = artifacts_by_id.get(artifact_id)
                    if artifact is None:
                        continue
                    seen_artifact_ids.add(artifact_id)
                    chunks.append(
                        _sse(
                            "artifact_card",
                            artifact_card(artifact, principal=principal),
                            event_id=f"{artifact_id}:artifact",
                        )
                    )
                return chunks

            for event in run_events:
                event_id = str(event["id"])
                if event_id in seen_event_ids:
                    continue
                seen_event_ids.add(event_id)
                if not event_visible_to_principal(event, principal):
                    continue
                if str(event.get("event_type") or "") in CHAT_STREAM_REPLAY_SKIP_EVENT_TYPES:
                    continue
                try:
                    projected = run_event_response(run_id, event, principal=principal)
                except HTTPException as exc:
                    yield _sse("error", {"error": str(exc.detail)})
                    yield _sse("done", {"status": "error"})
                    return
                if str(projected.get("event_type") or "") in CHAT_STREAM_TERMINAL_EVENT_TYPES:
                    for chunk in artifact_chunks():
                        yield chunk
                yield _sse("run_event", projected, event_id=event_id)
                if str(projected.get("event_type") or "") == "artifact_created":
                    payload = projected.get("payload") if isinstance(projected.get("payload"), dict) else {}
                    artifact_id = payload.get("artifact_id")
                    for chunk in artifact_chunks([artifact_id] if artifact_id else None):
                        yield chunk
            for chunk in artifact_chunks():
                yield chunk
            status = _platform_status(str(run["status"]))
            if status != last_status and status in {"queued", "running"}:
                yield _sse("queue_update", {"status": "processing" if status == "running" else "queued"})
                last_status = status
            if status == "succeeded":
                answer = _public_terminal_text(run, principal) or "任务完成"
                yield _sse("message:chunk", {"content": answer}, event_id=f"{run_id}:answer")
                yield _sse("done", {"status": "succeeded"}, event_id=f"{run_id}:done")
                return
            if status == "failed":
                yield _sse("error", {"error": _public_terminal_text(run, principal) or "run_failed"})
                yield _sse("done", {"status": "failed"}, event_id=f"{run_id}:done")
                return
            if status == "cancelled":
                yield _sse("done", {"status": "cancelled"}, event_id=f"{run_id}:done")
                return
            await asyncio.sleep(1)
        yield _sse("error", {"error": "stream_timeout"})
        yield _sse("done", {"status": "timeout"}, event_id=f"{run_id}:done")

    return StreamingResponse(stream(), media_type="text/event-stream")
