"""Read-only compatibility routes for historical tool-permission evidence."""

from typing import Literal

from fastapi import APIRouter, Body, Depends, HTTPException, Query

from app import repositories
from app.auth import AuthPrincipal, is_ai_admin, require_principal
from app.db import transaction
from app.tool_permission_projection import inbox_permission_response, permission_response

router = APIRouter()
TOOL_PERMISSION_GOVERNANCE_PERMISSION = "settings:manage"
_RUNTIME_APPROVAL_REMOVED = "tool_permission_runtime_approval_removed"


def _require_tool_permission_governance(principal: AuthPrincipal) -> None:
    """Keep historical tenant evidence restricted to the established admins."""

    if not is_ai_admin(principal):
        raise HTTPException(status_code=403, detail="not_ai_admin")
    if TOOL_PERMISSION_GOVERNANCE_PERMISSION not in set(principal.permissions):
        raise HTTPException(status_code=403, detail=f"missing_permission:{TOOL_PERMISSION_GOVERNANCE_PERMISSION}")


def _runtime_approval_removed() -> None:
    raise HTTPException(status_code=410, detail=_RUNTIME_APPROVAL_REMOVED)


@router.post("/runs/{run_id}/tool-permissions/request")
async def request_tool_permission(
    run_id: str,
    request: object | None = Body(default=None),
    principal: AuthPrincipal = Depends(require_principal),
) -> dict[str, object]:
    """Fail closed: model tool calls may not create approval work items."""

    _ = (run_id, request, principal)
    _runtime_approval_removed()


@router.get("/tool-permissions/inbox")
async def list_tool_permission_inbox(
    status: Literal["pending", "decided", "all"] = "all",
    limit: int = Query(default=50, ge=1, le=200),
    principal: AuthPrincipal = Depends(require_principal),
) -> dict[str, object]:
    """Return redacted historical records only; this is not an action inbox."""

    _require_tool_permission_governance(principal)
    async with transaction() as conn:
        rows = await repositories.list_tool_permission_inbox_for_tenant(
            conn,
            tenant_id=principal.tenant_id,
            status=status,
            limit=limit,
        )
    return {
        "permission_requests": [inbox_permission_response(row) for row in rows],
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
    """Keep an authorized run owner's historical record readable and redacted."""

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
    request: object | None = Body(default=None),
    principal: AuthPrincipal = Depends(require_principal),
) -> dict[str, object]:
    """Fail closed: historical rows can never resume a tool invocation."""

    _ = (run_id, request_id, request, principal)
    _runtime_approval_removed()


@router.post("/tool-permissions/inbox/{request_id}/decision")
async def decide_tool_permission_from_inbox(
    request_id: str,
    request: object | None = Body(default=None),
    principal: AuthPrincipal = Depends(require_principal),
) -> dict[str, object]:
    """Fail closed compatibility endpoint for retired administrator Inbox clients."""

    _ = (request_id, request, principal)
    _runtime_approval_removed()
