from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query

from app import repositories
from app.auth import AuthPrincipal, is_ai_admin, require_principal
from app.control_plane_contracts import sanitize_public_text, standard_trace_id
from app.db import transaction
from app.models import ToolPermissionDecisionRequest, ToolPermissionRequest
from app.repositories import RepositoryConflictError, RepositoryNotFoundError
from app.tool_policy import max_risk
from app.tool_permission_projection import permission_response

router = APIRouter()


def _event_timestamp(value: object) -> object:
    return value.isoformat() if hasattr(value, "isoformat") else value


def _inbox_decision_endpoint(request_id: str) -> str:
    return f"/api/ai/tool-permissions/inbox/{request_id}/decision"


def _permission_response_for_inbox(row: dict[str, object]) -> dict[str, object]:
    return permission_response(row, decision_endpoint=_inbox_decision_endpoint(str(row["id"])))


async def _append_decision_event_and_audit(
    conn: object,
    *,
    principal: AuthPrincipal,
    existing: dict[str, object],
    row: dict[str, object],
    decision: str,
    reason: str,
) -> None:
    run_id = str(row.get("run_id") or existing.get("run_id"))
    request_id = str(row.get("id") or existing.get("id"))
    trace_id = str(row.get("trace_id") or existing.get("trace_id") or standard_trace_id(run_id))
    safe_reason = sanitize_public_text(reason)
    event_payload = {
        "visible_to_user": True,
        "permission_request_id": request_id,
        "tool_id": row.get("tool_id"),
        "tool_call_id": row.get("tool_call_id"),
        "action": row.get("action") or existing.get("action") or "execute",
        "risk_level": row.get("risk_level") or existing.get("risk_level") or "low",
        "write_capable": bool(row.get("write_capable") if row.get("write_capable") is not None else existing.get("write_capable")),
        "decision": decision,
        "reason": safe_reason,
        "status": row.get("status") or "decided",
    }
    if row.get("expires_at") is not None:
        event_payload["expires_at"] = _event_timestamp(row.get("expires_at"))
    await repositories.append_event(
        conn,
        tenant_id=principal.tenant_id,
        run_id=run_id,
        trace_id=trace_id,
        event_type="tool_permission_decided",
        stage="tool_policy",
        message="工具权限已决策",
        payload=event_payload,
    )
    await repositories.append_audit_log(
        conn,
        tenant_id=principal.tenant_id,
        user_id=principal.user_id,
        action="tool.permission.decision",
        target_type="tool_permission_request",
        target_id=request_id,
        trace_id=trace_id,
        payload_json={
            "run_id": run_id,
            "tool_id": row.get("tool_id"),
            "tool_call_id": row.get("tool_call_id"),
            "decision": decision,
        },
    )


@router.post("/runs/{run_id}/tool-permissions/request")
async def request_tool_permission(
    run_id: str,
    request: ToolPermissionRequest,
    principal: AuthPrincipal = Depends(require_principal),
) -> dict[str, object]:
    try:
        async with transaction() as conn:
            run = await repositories.get_authorized_run(
                conn,
                tenant_id=principal.tenant_id,
                user_id=principal.user_id,
                run_id=run_id,
            )
            if run is None:
                raise RepositoryNotFoundError("run_not_found")
            tool = await repositories.ensure_mcp_tool_active(
                conn,
                tenant_id=principal.tenant_id,
                tool_id=request.tool_id,
            )
            risk_level = max_risk(str(tool.get("risk_level") or "low"), request.risk_level)
            write_capable = bool(tool.get("write_capable")) or request.write_capable
            trace_id = str(run.get("trace_id") or standard_trace_id(run_id))
            row = await repositories.create_tool_permission_request(
                conn,
                tenant_id=principal.tenant_id,
                workspace_id=str(run["workspace_id"]),
                user_id=principal.user_id,
                session_id=str(run["session_id"]),
                run_id=run_id,
                trace_id=trace_id,
                tool_id=request.tool_id,
                tool_call_id=request.tool_call_id,
                action=request.action,
                risk_level=risk_level,
                write_capable=write_capable,
                reason=request.reason,
                request_payload_json=request.request_payload,
            )
            await repositories.append_event(
                conn,
                tenant_id=principal.tenant_id,
                run_id=run_id,
                trace_id=trace_id,
                event_type="tool_permission_requested",
                stage="tool_policy",
                message="工具调用需要权限决策",
                payload={
                    "visible_to_user": True,
                    "permission_request_id": row["id"],
                    "tool_id": request.tool_id,
                    "tool_call_id": request.tool_call_id,
                    "action": request.action,
                    "risk_level": risk_level,
                    "write_capable": write_capable,
                    "reason": sanitize_public_text(request.reason),
                    "status": "pending",
                },
            )
    except RepositoryNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RepositoryConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"permission_request": permission_response(row)}


@router.get("/tool-permissions/inbox")
async def list_tool_permission_inbox(
    status: Literal["pending", "decided", "all"] = "pending",
    limit: int = Query(default=50, ge=1, le=200),
    principal: AuthPrincipal = Depends(require_principal),
) -> dict[str, object]:
    """Return a tenant governance inbox for admins, otherwise caller history."""
    async with transaction() as conn:
        if is_ai_admin(principal):
            rows = await repositories.list_tool_permission_inbox_for_tenant(
                conn,
                tenant_id=principal.tenant_id,
                status=status,
                limit=limit,
            )
        else:
            rows = await repositories.list_tool_permission_inbox(
                conn,
                tenant_id=principal.tenant_id,
                user_id=principal.user_id,
                status=status,
                limit=limit,
            )
    return {
        "permission_requests": [_permission_response_for_inbox(row) for row in rows],
        "total": len(rows),
        "status": status,
        "limit": limit,
    }


@router.get("/runs/{run_id}/tool-permissions/{request_id}")
async def get_tool_permission_request(
    run_id: str,
    request_id: str,
    principal: AuthPrincipal = Depends(require_principal),
) -> dict[str, object]:
    async with transaction() as conn:
        row = await repositories.get_tool_permission_request(
            conn,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            run_id=run_id,
            request_id=request_id,
        )
    if row is None:
        raise HTTPException(status_code=404, detail="tool_permission_request_not_found")
    return {"permission_request": permission_response(row)}


@router.post("/runs/{run_id}/tool-permissions/{request_id}/decision")
async def decide_tool_permission(
    run_id: str,
    request_id: str,
    request: ToolPermissionDecisionRequest,
    principal: AuthPrincipal = Depends(require_principal),
) -> dict[str, object]:
    if not is_ai_admin(principal):
        raise HTTPException(status_code=403, detail="not_ai_admin")
    async with transaction() as conn:
        existing = await repositories.get_tool_permission_request_for_tenant(
            conn,
            tenant_id=principal.tenant_id,
            run_id=run_id,
            request_id=request_id,
        )
        if existing is None:
            raise HTTPException(status_code=404, detail="tool_permission_request_not_found")
        row = await repositories.decide_tool_permission_request(
            conn,
            tenant_id=principal.tenant_id,
            user_id=str(existing["user_id"]),
            run_id=run_id,
            request_id=request_id,
            decision=request.decision,
            reason=request.reason,
            decision_payload_json=request.decision_payload,
            expires_in_seconds=request.expires_in_seconds,
        )
        if row is None:
            raise HTTPException(status_code=409, detail="tool_permission_request_not_pending")
        await _append_decision_event_and_audit(
            conn,
            principal=principal,
            existing=existing,
            row=row,
            decision=request.decision,
            reason=request.reason,
        )
    return {"permission_request": permission_response(row)}


@router.post("/tool-permissions/inbox/{request_id}/decision")
async def decide_tool_permission_from_inbox(
    request_id: str,
    request: ToolPermissionDecisionRequest,
    principal: AuthPrincipal = Depends(require_principal),
) -> dict[str, object]:
    """Decide a tenant request from the administrator permission inbox."""
    if not is_ai_admin(principal):
        raise HTTPException(status_code=403, detail="not_ai_admin")
    async with transaction() as conn:
        existing = await repositories.get_tool_permission_request_by_id_for_tenant(
            conn,
            tenant_id=principal.tenant_id,
            request_id=request_id,
        )
        if existing is None:
            raise HTTPException(status_code=404, detail="tool_permission_request_not_found")
        run_id = str(existing["run_id"])
        row = await repositories.decide_tool_permission_request(
            conn,
            tenant_id=principal.tenant_id,
            user_id=str(existing["user_id"]),
            run_id=run_id,
            request_id=request_id,
            decision=request.decision,
            reason=request.reason,
            decision_payload_json=request.decision_payload,
            expires_in_seconds=request.expires_in_seconds,
        )
        if row is None:
            raise HTTPException(status_code=409, detail="tool_permission_request_not_pending")
        await _append_decision_event_and_audit(
            conn,
            principal=principal,
            existing=existing,
            row=row,
            decision=request.decision,
            reason=request.reason,
        )
    return {"permission_request": _permission_response_for_inbox(row)}
