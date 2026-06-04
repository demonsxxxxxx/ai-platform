from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from app import repositories
from app.auth import AuthPrincipal, require_principal
from app.control_plane_contracts import sanitize_public_payload, standard_trace_id
from app.db import transaction
from app.models import SandboxLeaseReleaseRequest, SandboxLeaseRenewRequest, SandboxLeaseRequest
from app.repositories import RepositoryNotFoundError

router = APIRouter()


def lease_response(row: dict[str, Any]) -> dict[str, Any]:
    resource_limits = sanitize_public_payload(row.get("resource_limits_json") if isinstance(row.get("resource_limits_json"), dict) else {})
    user_visible_payload = sanitize_public_payload(
        row.get("user_visible_payload_json") if isinstance(row.get("user_visible_payload_json"), dict) else {}
    )
    lease_payload = sanitize_public_payload(row.get("lease_payload_json") if isinstance(row.get("lease_payload_json"), dict) else {})
    if not isinstance(resource_limits, dict):
        resource_limits = {}
    if not isinstance(user_visible_payload, dict):
        user_visible_payload = {}
    if not isinstance(lease_payload, dict):
        lease_payload = {}
    return {
        "lease_id": str(row["id"]),
        "tenant_id": str(row["tenant_id"]),
        "workspace_id": str(row["workspace_id"]),
        "user_id": str(row["user_id"]),
        "session_id": str(row["session_id"]),
        "run_id": str(row["run_id"]),
        "trace_id": str(row.get("trace_id") or standard_trace_id(str(row["run_id"]))),
        "sandbox_mode": str(row["sandbox_mode"]),
        "provider": str(row.get("provider") or "fake"),
        "status": str(row.get("status") or "active"),
        "browser_enabled": bool(row.get("browser_enabled")),
        "resource_limits": resource_limits,
        "workspace": user_visible_payload,
        "lease_payload": lease_payload,
        "heartbeat_at": row.get("heartbeat_at"),
        "expires_at": row.get("expires_at"),
        "released_at": row.get("released_at"),
        "release_reason": str(row.get("release_reason") or ""),
        "created_at": row.get("created_at"),
    }


def _user_visible_workspace() -> dict[str, str]:
    return {
        "workspace": "/workspace",
        "inputs": "/workspace/inputs",
    }


@router.post("/runs/{run_id}/sandbox/leases")
async def create_sandbox_lease(
    run_id: str,
    request: SandboxLeaseRequest,
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
            trace_id = str(run.get("trace_id") or standard_trace_id(run_id))
            row = await repositories.create_sandbox_lease(
                conn,
                tenant_id=principal.tenant_id,
                workspace_id=str(run["workspace_id"]),
                user_id=principal.user_id,
                session_id=str(run["session_id"]),
                run_id=run_id,
                trace_id=trace_id,
                sandbox_mode=request.sandbox_mode,
                provider=request.provider,
                browser_enabled=request.browser_enabled,
                ttl_seconds=request.ttl_seconds,
                resource_limits_json=request.resource_limits,
                user_visible_payload_json=_user_visible_workspace(),
                lease_payload_json=request.lease_payload,
            )
            await repositories.append_event(
                conn,
                tenant_id=principal.tenant_id,
                run_id=run_id,
                trace_id=trace_id,
                event_type="sandbox_lease_created",
                stage="sandbox",
                message="已创建 Sandbox 租约",
                payload={
                    "visible_to_user": True,
                    "lease_id": row["id"],
                    "sandbox_mode": request.sandbox_mode,
                    "provider": request.provider,
                    "browser_enabled": request.browser_enabled,
                },
            )
    except RepositoryNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"sandbox_lease": lease_response(row)}


@router.post("/runs/{run_id}/sandbox/leases/{lease_id}/renew")
async def renew_sandbox_lease(
    run_id: str,
    lease_id: str,
    request: SandboxLeaseRenewRequest,
    principal: AuthPrincipal = Depends(require_principal),
) -> dict[str, object]:
    async with transaction() as conn:
        existing = await repositories.get_sandbox_lease(
            conn,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            run_id=run_id,
            lease_id=lease_id,
        )
        if existing is None:
            raise HTTPException(status_code=404, detail="sandbox_lease_not_found")
        row = await repositories.renew_sandbox_lease(
            conn,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            run_id=run_id,
            lease_id=lease_id,
            ttl_seconds=request.ttl_seconds,
        )
        if row is None:
            raise HTTPException(status_code=409, detail="sandbox_lease_not_active")
        trace_id = str(row.get("trace_id") or existing.get("trace_id") or standard_trace_id(run_id))
        await repositories.append_event(
            conn,
            tenant_id=principal.tenant_id,
            run_id=run_id,
            trace_id=trace_id,
            event_type="sandbox_lease_renewed",
            stage="sandbox",
            message="已续租 Sandbox",
            payload={"visible_to_user": False, "lease_id": lease_id},
        )
    return {"sandbox_lease": lease_response(row)}


@router.post("/runs/{run_id}/sandbox/leases/{lease_id}/release")
async def release_sandbox_lease(
    run_id: str,
    lease_id: str,
    request: SandboxLeaseReleaseRequest,
    principal: AuthPrincipal = Depends(require_principal),
) -> dict[str, object]:
    async with transaction() as conn:
        existing = await repositories.get_sandbox_lease(
            conn,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            run_id=run_id,
            lease_id=lease_id,
        )
        if existing is None:
            raise HTTPException(status_code=404, detail="sandbox_lease_not_found")
        row = await repositories.release_sandbox_lease(
            conn,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            run_id=run_id,
            lease_id=lease_id,
            reason=request.reason,
        )
        if row is None:
            raise HTTPException(status_code=409, detail="sandbox_lease_not_active")
        trace_id = str(row.get("trace_id") or existing.get("trace_id") or standard_trace_id(run_id))
        await repositories.append_event(
            conn,
            tenant_id=principal.tenant_id,
            run_id=run_id,
            trace_id=trace_id,
            event_type="sandbox_lease_released",
            stage="sandbox",
            message="已释放 Sandbox 租约",
            payload={"visible_to_user": True, "lease_id": lease_id, "reason": request.reason},
        )
    return {"sandbox_lease": lease_response(row)}
