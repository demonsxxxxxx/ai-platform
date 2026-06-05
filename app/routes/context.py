from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query

from app import repositories
from app.auth import BREAK_GLASS_ADMIN_ROLE, PLATFORM_ADMIN_ROLE, TENANT_ADMIN_ROLE, AuthPrincipal, normalized_roles, require_principal
from app.control_plane_contracts import sanitize_public_payload, standard_trace_id
from app.db import transaction
from app.memory_redaction import redact_memory_metadata, redact_memory_text
from app.models import ContextSnapshotRequest, MemoryPolicyRequest, MemoryRecordRequest
from app.projection_redaction import internal_agent_id_for_request, public_agent_id_for_projection
from app.repositories import RepositoryConflictError, RepositoryNotFoundError
from app.validation import assert_safe_id

router = APIRouter()

MEMORY_ADMIN_ROLES = {"admin", TENANT_ADMIN_ROLE, PLATFORM_ADMIN_ROLE, BREAK_GLASS_ADMIN_ROLE}
MEMORY_REDACTION_MODES = {"standard", "strict"}


def _audit_reason(value: str, *, redaction_mode: str = "standard") -> str:
    return redact_memory_text(value, mode=redaction_mode)


def _is_memory_admin(principal: AuthPrincipal) -> bool:
    return bool(normalized_roles(principal.roles).intersection(MEMORY_ADMIN_ROLES))


def _safe_query_id(value: str, field_name: str) -> str:
    try:
        return assert_safe_id(value, field_name)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _snapshot_response(row: dict[str, Any]) -> dict[str, Any]:
    payload = sanitize_public_payload(row.get("payload_json") if isinstance(row.get("payload_json"), dict) else {})
    if not isinstance(payload, dict):
        payload = {}
    redaction_summary = sanitize_public_payload(
        row.get("redaction_summary_json") if isinstance(row.get("redaction_summary_json"), dict) else {}
    )
    if not isinstance(redaction_summary, dict):
        redaction_summary = {}
    return {
        "context_snapshot_id": str(row["id"]),
        "schema_version": str(row.get("schema_version") or "ai-platform.context-snapshot.v1"),
        "tenant_id": str(row["tenant_id"]),
        "workspace_id": str(row["workspace_id"]),
        "user_id": str(row["user_id"]),
        "session_id": str(row["session_id"]),
        "run_id": str(row["run_id"]),
        "trace_id": str(row.get("trace_id") or standard_trace_id(str(row["run_id"]))),
        "context_kind": str(row.get("context_kind") or "executor"),
        "included_message_ids": list(row.get("included_message_ids") or []),
        "included_file_ids": list(row.get("included_file_ids") or []),
        "included_artifact_ids": list(row.get("included_artifact_ids") or []),
        "included_memory_record_ids": list(row.get("included_memory_record_ids") or []),
        "redaction_summary": redaction_summary,
        "payload": payload,
        "created_at": row.get("created_at"),
    }


def _memory_response(row: dict[str, Any]) -> dict[str, Any]:
    redacted_metadata = redact_memory_metadata(row.get("metadata_json") if isinstance(row.get("metadata_json"), dict) else {})
    metadata = sanitize_public_payload(redacted_metadata, preserve_sensitive_keys=True)
    if not isinstance(metadata, dict):
        metadata = {}
    return {
        "memory_record_id": str(row["id"]),
        "tenant_id": str(row["tenant_id"]),
        "workspace_id": str(row["workspace_id"]),
        "user_id": str(row["user_id"]),
        "agent_id": public_agent_id_for_projection(row.get("agent_id")),
        "session_id": row.get("session_id"),
        "record_type": str(row["record_type"]),
        "content": redact_memory_text(row.get("content")),
        "metadata": metadata,
        "status": str(row.get("status") or "active"),
        "expires_at": row.get("expires_at"),
        "deleted_at": row.get("deleted_at"),
        "created_at": row.get("created_at"),
    }


def _memory_delete_response(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "memory_record_id": str(row["id"]),
        "tenant_id": str(row["tenant_id"]),
        "workspace_id": str(row["workspace_id"]),
        "user_id": str(row["user_id"]),
        "agent_id": public_agent_id_for_projection(row.get("agent_id")),
        "session_id": row.get("session_id"),
        "record_type": str(row["record_type"]),
        "status": str(row.get("status") or "deleted"),
        "deleted_at": row.get("deleted_at"),
        "created_at": row.get("created_at"),
    }


def _memory_operator_response(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "memory_record_id": str(row["id"]),
        "tenant_id": str(row["tenant_id"]),
        "workspace_id": str(row["workspace_id"]),
        "user_id": str(row["user_id"]),
        "agent_id": public_agent_id_for_projection(row.get("agent_id")),
        "session_id": row.get("session_id"),
        "record_type": str(row["record_type"]),
        "status": str(row.get("status") or "active"),
        "expires_at": row.get("expires_at"),
        "deleted_at": row.get("deleted_at"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


def _memory_policy_response(policy: dict[str, Any]) -> dict[str, Any]:
    raw_redaction_mode = policy.get("redaction_mode")
    redaction_mode = str(raw_redaction_mode).strip() if raw_redaction_mode is not None else ""
    if not redaction_mode:
        redaction_mode = "strict"
    if redaction_mode not in MEMORY_REDACTION_MODES:
        redaction_mode = "strict"
    return {
        "tenant_id": str(policy["tenant_id"]),
        "workspace_id": str(policy["workspace_id"]),
        "user_id": str(policy["user_id"]),
        "agent_id": public_agent_id_for_projection(policy.get("agent_id")),
        "memory_enabled": bool(policy.get("memory_enabled", True)),
        "long_term_memory_enabled": False,
        "retention_days": int(policy.get("retention_days") or 90),
        "redaction_mode": redaction_mode,
        "source": str(policy.get("source") or "default"),
        "reason": _audit_reason(str(policy.get("reason") or ""), redaction_mode=redaction_mode),
        "updated_by": str(policy.get("updated_by") or ""),
        "updated_at": policy.get("updated_at"),
    }


async def _effective_session_agent_id(
    conn,
    *,
    principal: AuthPrincipal,
    workspace_id: str,
    session_id: str | None,
    agent_id: str | None,
) -> str | None:
    internal_agent_id = internal_agent_id_for_request(agent_id) if agent_id else None
    if not session_id:
        return internal_agent_id
    session = await repositories.get_authorized_session(
        conn,
        tenant_id=principal.tenant_id,
        user_id=principal.user_id,
        session_id=session_id,
    )
    if session is None or str(session.get("workspace_id")) != workspace_id:
        raise RepositoryNotFoundError("session_not_found")
    session_agent_id = str(session.get("agent_id") or "")
    if not session_agent_id:
        raise RepositoryNotFoundError("session_not_found")
    if internal_agent_id and internal_agent_id != session_agent_id:
        raise RepositoryNotFoundError("session_not_found")
    return internal_agent_id or session_agent_id


@router.post("/runs/{run_id}/context/snapshots")
async def create_run_context_snapshot(
    run_id: str,
    request: ContextSnapshotRequest,
    principal: AuthPrincipal = Depends(require_principal),
) -> dict[str, object]:
    async with transaction() as conn:
        run = await repositories.get_authorized_run(
            conn,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            run_id=run_id,
        )
        if run is None:
            raise HTTPException(status_code=404, detail="run_not_found")
        trace_id = str(run.get("trace_id") or standard_trace_id(run_id))
        redaction_summary = sanitize_public_payload(request.redaction_summary)
        if not isinstance(redaction_summary, dict):
            redaction_summary = {}
        payload = sanitize_public_payload(request.payload)
        if not isinstance(payload, dict):
            payload = {}
        snapshot = await repositories.create_context_snapshot(
            conn,
            tenant_id=principal.tenant_id,
            workspace_id=str(run["workspace_id"]),
            user_id=principal.user_id,
            session_id=str(run["session_id"]),
            run_id=run_id,
            trace_id=trace_id,
            context_kind=request.context_kind,
            included_message_ids=request.included_message_ids,
            included_file_ids=request.included_file_ids,
            included_artifact_ids=request.included_artifact_ids,
            included_memory_record_ids=request.included_memory_record_ids,
            redaction_summary_json=redaction_summary,
            payload_json=payload,
        )
        await repositories.append_event(
            conn,
            tenant_id=principal.tenant_id,
            run_id=run_id,
            trace_id=trace_id,
            event_type="context_snapshot_created",
            stage="context",
            message="已记录运行上下文快照",
            payload={
                "visible_to_user": False,
                "context_snapshot_id": snapshot["id"],
                "context_kind": request.context_kind,
                "message_count": len(request.included_message_ids),
                "file_count": len(request.included_file_ids),
                "artifact_count": len(request.included_artifact_ids),
                "memory_record_count": len(request.included_memory_record_ids),
            },
        )
    return {"context_snapshot": _snapshot_response(snapshot)}


@router.get("/runs/{run_id}/context/snapshots")
async def list_run_context_snapshots(
    run_id: str,
    principal: AuthPrincipal = Depends(require_principal),
) -> dict[str, object]:
    async with transaction() as conn:
        run = await repositories.get_authorized_run(
            conn,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            run_id=run_id,
        )
        if run is None:
            raise HTTPException(status_code=404, detail="run_not_found")
        rows = await repositories.list_context_snapshots(
            conn,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            run_id=run_id,
        )
    return {"run_id": run_id, "context_snapshots": [_snapshot_response(row) for row in rows]}


@router.post("/memory/records")
async def create_memory_record(
    request: MemoryRecordRequest,
    principal: AuthPrincipal = Depends(require_principal),
) -> dict[str, object]:
    if not request.session_id:
        raise HTTPException(status_code=400, detail="memory_session_id_required")
    internal_agent_id = internal_agent_id_for_request(request.agent_id) if request.agent_id else None
    denied_by_policy = False
    try:
        async with transaction() as conn:
            await repositories.ensure_workspace(conn, tenant_id=principal.tenant_id, workspace_id=request.workspace_id)
            await repositories.ensure_user(
                conn,
                tenant_id=principal.tenant_id,
                user_id=principal.user_id,
                display_name=principal.display_name,
            )
            effective_agent_id = await _effective_session_agent_id(
                conn,
                principal=principal,
                workspace_id=request.workspace_id,
                session_id=request.session_id,
                agent_id=internal_agent_id,
            )
            policy = await repositories.get_effective_memory_policy(
                conn,
                tenant_id=principal.tenant_id,
                workspace_id=request.workspace_id,
                user_id=principal.user_id,
                agent_id=effective_agent_id,
            )
            if not bool(policy.get("memory_enabled", True)):
                await repositories.append_audit_log(
                    conn,
                    tenant_id=principal.tenant_id,
                    user_id=principal.user_id,
                    action="memory.record.create_denied",
                    target_type="memory_policy",
                    target_id=principal.user_id,
                    trace_id=standard_trace_id(principal.user_id),
                    payload_json=sanitize_public_payload(
                        {
                            "workspace_id": request.workspace_id,
                            "agent_id": public_agent_id_for_projection(effective_agent_id) or effective_agent_id,
                            "session_id": request.session_id,
                            "record_type": request.record_type,
                            "reason": "memory_policy_disabled",
                        }
                    ),
                )
                denied_by_policy = True
            if denied_by_policy:
                record = None
            else:
                record = await repositories.create_memory_record(
                    conn,
                    tenant_id=principal.tenant_id,
                    workspace_id=request.workspace_id,
                    user_id=principal.user_id,
                    agent_id=effective_agent_id,
                    session_id=request.session_id,
                    record_type=request.record_type,
                    content=request.content,
                    metadata_json=request.metadata,
                    retention_days=int(policy.get("retention_days") or 90),
                    redaction_mode=str(policy.get("redaction_mode") or "standard"),
                )
    except RepositoryNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if denied_by_policy:
        raise HTTPException(status_code=403, detail="memory_policy_disabled")
    return {"memory_record": _memory_response(record)}


@router.get("/memory/records")
async def list_memory_records(
    workspace_id: str = "default",
    agent_id: str | None = None,
    session_id: str | None = None,
    limit: int = 50,
    principal: AuthPrincipal = Depends(require_principal),
) -> dict[str, object]:
    workspace_id = _safe_query_id(workspace_id, "workspace_id")
    agent_id = _safe_query_id(agent_id, "agent_id") if agent_id else None
    internal_agent_id = internal_agent_id_for_request(agent_id) if agent_id else None
    session_id = _safe_query_id(session_id, "session_id") if session_id else None
    if not session_id:
        raise HTTPException(status_code=400, detail="memory_session_id_required")
    try:
        async with transaction() as conn:
            effective_agent_id = await _effective_session_agent_id(
                conn,
                principal=principal,
                workspace_id=workspace_id,
                session_id=session_id,
                agent_id=internal_agent_id,
            )
            policy = await repositories.get_effective_memory_policy(
                conn,
                tenant_id=principal.tenant_id,
                workspace_id=workspace_id,
                user_id=principal.user_id,
                agent_id=effective_agent_id,
            )
            if not bool(policy.get("memory_enabled", True)):
                return {"memory_records": []}
            rows = await repositories.list_memory_records(
                conn,
                tenant_id=principal.tenant_id,
                workspace_id=workspace_id,
                user_id=principal.user_id,
                agent_id=effective_agent_id,
                session_id=session_id,
                limit=max(min(limit, 200), 1),
            )
    except RepositoryNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"memory_records": [_memory_response(row) for row in rows]}


@router.delete("/memory/records/{record_id}")
async def delete_memory_record(
    record_id: str,
    workspace_id: str = "default",
    agent_id: str | None = None,
    session_id: str | None = None,
    reason: str = Query(default="", max_length=2000),
    principal: AuthPrincipal = Depends(require_principal),
) -> dict[str, object]:
    record_id = _safe_query_id(record_id, "record_id")
    workspace_id = _safe_query_id(workspace_id, "workspace_id")
    agent_id = _safe_query_id(agent_id, "agent_id") if agent_id else None
    internal_agent_id = internal_agent_id_for_request(agent_id) if agent_id else None
    session_id = _safe_query_id(session_id, "session_id") if session_id else None
    if not session_id:
        raise HTTPException(status_code=400, detail="memory_session_id_required")
    try:
        async with transaction() as conn:
            effective_agent_id = await _effective_session_agent_id(
                conn,
                principal=principal,
                workspace_id=workspace_id,
                session_id=session_id,
                agent_id=internal_agent_id,
            )
            row = await repositories.delete_memory_record(
                conn,
                tenant_id=principal.tenant_id,
                workspace_id=workspace_id,
                user_id=principal.user_id,
                agent_id=effective_agent_id,
                session_id=session_id,
                record_id=record_id,
            )
            if row is None:
                raise HTTPException(status_code=404, detail="memory_record_not_found")
            await repositories.append_audit_log(
                conn,
                tenant_id=principal.tenant_id,
                user_id=principal.user_id,
                action="memory.record.deleted",
                target_type="memory_record",
                target_id=record_id,
                trace_id=standard_trace_id(record_id),
                payload_json=sanitize_public_payload(
                    {
                        "workspace_id": workspace_id,
                        "agent_id": public_agent_id_for_projection(row.get("agent_id")),
                        "session_id": row.get("session_id"),
                        "record_type": row.get("record_type"),
                        "reason": _audit_reason(reason),
                    }
                ),
            )
    except RepositoryNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"memory_record": _memory_delete_response(row)}


@router.get("/memory/policy")
async def get_memory_policy(
    workspace_id: str = "default",
    agent_id: str | None = None,
    principal: AuthPrincipal = Depends(require_principal),
) -> dict[str, object]:
    workspace_id = _safe_query_id(workspace_id, "workspace_id")
    agent_id = _safe_query_id(agent_id, "agent_id") if agent_id else None
    internal_agent_id = internal_agent_id_for_request(agent_id) if agent_id else None
    try:
        async with transaction() as conn:
            await repositories.ensure_workspace(conn, tenant_id=principal.tenant_id, workspace_id=workspace_id)
            if internal_agent_id:
                target_agent = await repositories.get_agent(
                    conn,
                    tenant_id=principal.tenant_id,
                    agent_id=internal_agent_id,
                )
                if target_agent is None:
                    raise RepositoryNotFoundError("agent_not_found")
            policy = await repositories.get_effective_memory_policy(
                conn,
                tenant_id=principal.tenant_id,
                workspace_id=workspace_id,
                user_id=principal.user_id,
                agent_id=internal_agent_id,
            )
    except RepositoryNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"memory_policy": _memory_policy_response(policy)}


@router.put("/memory/policy")
async def update_memory_policy(
    request: MemoryPolicyRequest,
    principal: AuthPrincipal = Depends(require_principal),
) -> dict[str, object]:
    """Let the authenticated user manage their own public memory policy."""
    if request.long_term_memory_enabled:
        raise HTTPException(status_code=409, detail="long_term_memory_not_available")
    reason = _audit_reason(request.reason, redaction_mode=request.redaction_mode)
    internal_agent_id = internal_agent_id_for_request(request.agent_id) if request.agent_id else None
    public_agent_id = public_agent_id_for_projection(internal_agent_id) if internal_agent_id else None
    try:
        async with transaction() as conn:
            await repositories.ensure_workspace(conn, tenant_id=principal.tenant_id, workspace_id=request.workspace_id)
            if internal_agent_id:
                target_agent = await repositories.get_agent(
                    conn,
                    tenant_id=principal.tenant_id,
                    agent_id=internal_agent_id,
                )
                if target_agent is None:
                    raise RepositoryNotFoundError("agent_not_found")
            await repositories.ensure_user(
                conn,
                tenant_id=principal.tenant_id,
                user_id=principal.user_id,
                display_name=principal.display_name,
            )
            policy = await repositories.set_memory_policy(
                conn,
                tenant_id=principal.tenant_id,
                workspace_id=request.workspace_id,
                user_id=principal.user_id,
                agent_id=internal_agent_id,
                memory_enabled=request.memory_enabled,
                long_term_memory_enabled=False,
                retention_days=request.retention_days,
                redaction_mode=request.redaction_mode,
                reason=reason,
                updated_by=principal.user_id,
            )
            await repositories.append_audit_log(
                conn,
                tenant_id=principal.tenant_id,
                user_id=principal.user_id,
                action="memory.policy.updated",
                target_type="memory_policy",
                target_id=principal.user_id,
                trace_id=standard_trace_id(principal.user_id),
                payload_json=sanitize_public_payload(
                    {
                        "workspace_id": request.workspace_id,
                        "target_user_id": principal.user_id,
                        "agent_id": public_agent_id,
                        "memory_enabled": request.memory_enabled,
                        "long_term_memory_enabled": False,
                        "retention_days": request.retention_days,
                        "redaction_mode": request.redaction_mode,
                        "reason": reason,
                    }
                ),
            )
    except RepositoryNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RepositoryConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"memory_policy": _memory_policy_response(policy)}


@router.get("/admin/memory/policies")
async def admin_list_memory_policies(
    workspace_id: str = "default",
    user_id: str | None = None,
    agent_id: str | None = None,
    limit: int = Query(default=50, ge=1, le=500),
    principal: AuthPrincipal = Depends(require_principal),
) -> dict[str, object]:
    """Return same-tenant memory policy inventory for memory admins."""
    if not _is_memory_admin(principal):
        raise HTTPException(status_code=403, detail="not_ai_memory_admin")
    workspace_id = _safe_query_id(workspace_id, "workspace_id")
    user_id = _safe_query_id(user_id, "user_id") if user_id else None
    agent_id = _safe_query_id(agent_id, "agent_id") if agent_id else None
    internal_agent_id = internal_agent_id_for_request(agent_id) if agent_id else None
    try:
        async with transaction() as conn:
            await repositories.ensure_workspace(conn, tenant_id=principal.tenant_id, workspace_id=workspace_id)
            if internal_agent_id:
                target_agent = await repositories.get_agent(
                    conn,
                    tenant_id=principal.tenant_id,
                    agent_id=internal_agent_id,
                )
                if target_agent is None:
                    raise RepositoryNotFoundError("agent_not_found")
            rows = await repositories.list_admin_memory_policies(
                conn,
                tenant_id=principal.tenant_id,
                workspace_id=workspace_id,
                user_id=user_id,
                agent_id=internal_agent_id,
                limit=limit,
            )
    except RepositoryNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "memory_policies": [_memory_policy_response(row) for row in rows],
        "summary": {
            "workspace_id": workspace_id,
            "user_id": user_id,
            "agent_id": public_agent_id_for_projection(internal_agent_id) if internal_agent_id else None,
            "returned_count": len(rows),
            "limit": limit,
        },
    }


@router.put("/admin/memory/policies/{target_user_id}")
async def admin_set_memory_policy(
    target_user_id: str,
    request: MemoryPolicyRequest,
    principal: AuthPrincipal = Depends(require_principal),
) -> dict[str, object]:
    if not _is_memory_admin(principal):
        raise HTTPException(status_code=403, detail="not_ai_memory_admin")
    if request.long_term_memory_enabled:
        raise HTTPException(status_code=409, detail="long_term_memory_not_available")
    target_user_id = assert_safe_id(target_user_id, "target_user_id")
    reason = _audit_reason(request.reason, redaction_mode=request.redaction_mode)
    internal_agent_id = internal_agent_id_for_request(request.agent_id) if request.agent_id else None
    public_agent_id = public_agent_id_for_projection(internal_agent_id) if internal_agent_id else None
    try:
        async with transaction() as conn:
            await repositories.ensure_workspace(conn, tenant_id=principal.tenant_id, workspace_id=request.workspace_id)
            target_user = await repositories.get_user(conn, tenant_id=principal.tenant_id, user_id=target_user_id)
            if target_user is None:
                raise RepositoryNotFoundError("user_not_found")
            if internal_agent_id:
                target_agent = await repositories.get_agent(
                    conn,
                    tenant_id=principal.tenant_id,
                    agent_id=internal_agent_id,
                )
                if target_agent is None:
                    raise RepositoryNotFoundError("agent_not_found")
            policy = await repositories.set_memory_policy(
                conn,
                tenant_id=principal.tenant_id,
                workspace_id=request.workspace_id,
                user_id=target_user_id,
                agent_id=internal_agent_id,
                memory_enabled=request.memory_enabled,
                long_term_memory_enabled=False,
                retention_days=request.retention_days,
                redaction_mode=request.redaction_mode,
                reason=reason,
                updated_by=principal.user_id,
            )
            await repositories.append_audit_log(
                conn,
                tenant_id=principal.tenant_id,
                user_id=principal.user_id,
                action="admin.memory.policy.updated",
                target_type="memory_policy",
                target_id=target_user_id,
                trace_id=standard_trace_id(target_user_id),
                payload_json=sanitize_public_payload(
                    {
                        "workspace_id": request.workspace_id,
                        "target_user_id": target_user_id,
                        "agent_id": public_agent_id,
                        "memory_enabled": request.memory_enabled,
                        "long_term_memory_enabled": False,
                        "retention_days": request.retention_days,
                        "redaction_mode": request.redaction_mode,
                        "reason": reason,
                    }
                ),
            )
    except RepositoryNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RepositoryConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"memory_policy": _memory_policy_response(policy)}


@router.post("/admin/memory/retention/cleanup")
async def admin_cleanup_expired_memory_records(
    workspace_id: str = "default",
    limit: int = Query(default=200, ge=1, le=500),
    principal: AuthPrincipal = Depends(require_principal),
) -> dict[str, object]:
    if not _is_memory_admin(principal):
        raise HTTPException(status_code=403, detail="not_ai_memory_admin")
    workspace_id = assert_safe_id(workspace_id, "workspace_id")
    try:
        async with transaction() as conn:
            await repositories.ensure_workspace(conn, tenant_id=principal.tenant_id, workspace_id=workspace_id)
            rows = await repositories.cleanup_expired_memory_records(
                conn,
                tenant_id=principal.tenant_id,
                workspace_id=workspace_id,
                limit=limit,
            )
            await repositories.append_audit_log(
                conn,
                tenant_id=principal.tenant_id,
                user_id=principal.user_id,
                action="admin.memory.retention.cleanup",
                target_type="memory_retention",
                target_id=workspace_id,
                trace_id=standard_trace_id(workspace_id),
                payload_json=sanitize_public_payload(
                    {
                        "workspace_id": workspace_id,
                        "deleted_count": len(rows),
                        "memory_record_ids": [str(row.get("id")) for row in rows],
                        "target_user_ids": sorted({str(row.get("user_id")) for row in rows if row.get("user_id")}),
                        "reason": "retention_expired",
                    }
                ),
            )
    except RepositoryNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RepositoryConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "deleted_count": len(rows),
        "memory_records": [_memory_delete_response(row) for row in rows],
    }


@router.get("/admin/memory/records")
async def admin_list_memory_records(
    workspace_id: str = "default",
    user_id: str | None = None,
    status: Literal["active", "deleted", "all"] = "active",
    limit: int = Query(default=50, ge=1, le=500),
    principal: AuthPrincipal = Depends(require_principal),
) -> dict[str, object]:
    if not _is_memory_admin(principal):
        raise HTTPException(status_code=403, detail="not_ai_memory_admin")
    workspace_id = _safe_query_id(workspace_id, "workspace_id")
    user_id = _safe_query_id(user_id, "user_id") if user_id else None
    try:
        async with transaction() as conn:
            await repositories.ensure_workspace(conn, tenant_id=principal.tenant_id, workspace_id=workspace_id)
            rows = await repositories.list_admin_memory_records(
                conn,
                tenant_id=principal.tenant_id,
                workspace_id=workspace_id,
                user_id=user_id,
                status=status,
                limit=limit,
            )
    except RepositoryNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "memory_records": [_memory_operator_response(row) for row in rows],
        "summary": {
            "workspace_id": workspace_id,
            "status": status,
            "returned_count": len(rows),
            "limit": limit,
        },
    }


@router.delete("/admin/memory/records/{record_id}")
async def admin_delete_memory_record(
    record_id: str,
    workspace_id: str = "default",
    reason: str = Query(default="", max_length=2000),
    principal: AuthPrincipal = Depends(require_principal),
) -> dict[str, object]:
    if not _is_memory_admin(principal):
        raise HTTPException(status_code=403, detail="not_ai_memory_admin")
    record_id = assert_safe_id(record_id, "record_id")
    workspace_id = assert_safe_id(workspace_id, "workspace_id")
    async with transaction() as conn:
        row = await repositories.admin_delete_memory_record(
            conn,
            tenant_id=principal.tenant_id,
            workspace_id=workspace_id,
            record_id=record_id,
        )
        if row is None:
            raise HTTPException(status_code=404, detail="memory_record_not_found")
        await repositories.append_audit_log(
            conn,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            action="admin.memory.record.deleted",
            target_type="memory_record",
            target_id=record_id,
            trace_id=standard_trace_id(record_id),
            payload_json=sanitize_public_payload(
                {
                    "workspace_id": workspace_id,
                    "target_user_id": row.get("user_id"),
                    "agent_id": row.get("agent_id"),
                    "session_id": row.get("session_id"),
                    "record_type": row.get("record_type"),
                    "reason": _audit_reason(reason),
                }
            ),
        )
    return {"memory_record": _memory_delete_response(row)}
