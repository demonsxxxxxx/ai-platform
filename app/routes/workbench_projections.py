from __future__ import annotations

from typing import NoReturn

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.auth import AuthPrincipal, is_ai_admin, require_principal
from app.models import (
    WorkbenchGovernanceResponse,
    WorkbenchUserListResponse,
    WorkbenchUserResponse,
)
from app.kernel.identifiers import assert_safe_id

router = APIRouter()

DOMAIN_PERMISSIONS = (
    "user:read",
    "user:admin",
    "feedback:read",
    "feedback:admin",
    "notification:read",
    "notification:admin",
)


def _effective_permission_set(principal: AuthPrincipal) -> set[str]:
    granted = {item.strip() for item in principal.permissions if item.strip()}
    if is_ai_admin(principal):
        granted.update(DOMAIN_PERMISSIONS)
    for read_permission, admin_permission in (
        ("user:read", "user:admin"),
        ("feedback:read", "feedback:admin"),
        ("notification:read", "notification:admin"),
    ):
        if admin_permission in granted:
            granted.add(read_permission)
    return granted


def _require_permission(principal: AuthPrincipal, permission: str) -> None:
    if permission not in _effective_permission_set(principal):
        raise HTTPException(status_code=403, detail=f"missing_permission:{permission}")


async def _optional_principal(request: Request) -> AuthPrincipal | None:
    try:
        return await require_principal(request)
    except HTTPException as exc:
        if exc.status_code == 401 and exc.detail == "missing_authenticated_principal":
            return None
        raise


def _safe_id(value: str, field_name: str) -> str:
    try:
        return assert_safe_id(value, field_name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _governance(
    principal: AuthPrincipal,
    projection: str,
    *,
    workspace_id: str = "default",
    degraded: bool = False,
    audit_required: bool = False,
    rollback_available: bool = False,
) -> WorkbenchGovernanceResponse:
    return WorkbenchGovernanceResponse(
        projection=projection,
        tenant_id=principal.tenant_id,
        workspace_id=workspace_id,
        degraded=degraded,
        audit_required=audit_required,
        rollback_available=rollback_available,
        secret_material_projected=False,
    )


def _user_projection(principal: AuthPrincipal) -> WorkbenchUserResponse:
    return WorkbenchUserResponse(
        id=principal.user_id,
        username=principal.user_id,
        email=None,
        full_name=principal.display_name,
        is_active=True,
        is_superuser=is_ai_admin(principal),
        roles=principal.roles,
        permissions=sorted(_effective_permission_set(principal)),
        tenant_id=principal.tenant_id,
        department_id=principal.department_id,
    )


@router.get("/users/", response_model=WorkbenchUserListResponse)
async def list_users(
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
    search: str | None = None,
    principal: AuthPrincipal = Depends(require_principal),
) -> WorkbenchUserListResponse:
    """Return only the authenticated self; no company directory is available."""

    _require_permission(principal, "user:read")
    user = _user_projection(principal)
    users = [user]
    if search and search.lower() not in user.username.lower() and search.lower() not in user.full_name.lower():
        users = []
    return WorkbenchUserListResponse(
        users=users[skip : skip + limit],
        items=users[skip : skip + limit],
        total=len(users),
        skip=skip,
        limit=limit,
        governance=_governance(principal, "authenticated_self", degraded=True),
    )


@router.get("/users/{user_id}", response_model=WorkbenchUserResponse)
async def get_user(user_id: str, principal: AuthPrincipal = Depends(require_principal)) -> WorkbenchUserResponse:
    """Return one safe user projection."""

    _require_permission(principal, "user:read")
    safe_user_id = _safe_id(user_id, "user_id")
    user = _user_projection(principal)
    if safe_user_id != user.id and not is_ai_admin(principal):
        raise HTTPException(status_code=403, detail="missing_permission:user:admin")
    if safe_user_id != user.id:
        _unavailable("user_directory_unavailable")
    return user


@router.post("/users/", status_code=501, response_model=None)
async def create_user(principal: AuthPrincipal = Depends(require_principal)) -> NoReturn:
    """Reject user creation until an actual lifecycle owner exists."""

    _require_permission(principal, "user:admin")
    _unavailable("user_management_unavailable")


@router.put("/users/{user_id}", status_code=501, response_model=None)
async def update_user(user_id: str, principal: AuthPrincipal = Depends(require_principal)) -> NoReturn:
    _require_permission(principal, "user:admin")
    _safe_id(user_id, "user_id")
    _unavailable("user_management_unavailable")


@router.delete("/users/{user_id}", status_code=501, response_model=None)
async def delete_user(user_id: str, principal: AuthPrincipal = Depends(require_principal)) -> NoReturn:
    _require_permission(principal, "user:admin")
    _safe_id(user_id, "user_id")
    _unavailable("user_management_unavailable")


def _unavailable(detail: str) -> NoReturn:
    # Auditing a request is not an execution queue. These unimplemented surfaces
    # must reject without writing an audit row or claiming domain facts.
    raise HTTPException(status_code=501, detail=detail)


@router.get("/feedback/", status_code=501, response_model=None)
async def list_feedback(principal: AuthPrincipal = Depends(require_principal)) -> NoReturn:
    """Feedback storage and aggregate projections are not implemented."""

    _require_permission(principal, "feedback:read")
    _unavailable("feedback_unavailable")


@router.get("/feedback/stats", status_code=501, response_model=None)
async def feedback_stats(principal: AuthPrincipal = Depends(require_principal)) -> NoReturn:
    _require_permission(principal, "feedback:read")
    _unavailable("feedback_unavailable")


@router.put("/feedback/{feedback_id}", status_code=501, response_model=None)
async def update_feedback(
    feedback_id: str,
    principal: AuthPrincipal = Depends(require_principal),
) -> NoReturn:
    _require_permission(principal, "feedback:admin")
    _safe_id(feedback_id, "feedback_id")
    _unavailable("feedback_unavailable")


@router.delete("/feedback/{feedback_id}", status_code=501, response_model=None)
async def delete_feedback(
    feedback_id: str,
    principal: AuthPrincipal = Depends(require_principal),
) -> NoReturn:
    _require_permission(principal, "feedback:admin")
    _safe_id(feedback_id, "feedback_id")
    _unavailable("feedback_unavailable")


@router.get(
    "/notifications/active",
    response_model=None,
    responses={501: {"description": "Authenticated announcements are not implemented"}},
)
async def active_notifications(request: Request) -> dict[str, list[object]]:
    """Keep the anonymous bootstrap empty; authenticated announcement reads are unavailable."""

    principal = await _optional_principal(request)
    if principal is None:
        return {"notifications": []}
    _require_permission(principal, "notification:read")
    _unavailable("notifications_unavailable")


@router.post("/notifications/{notification_id}/dismiss", status_code=501, response_model=None)
async def dismiss_notification(
    notification_id: str,
    principal: AuthPrincipal = Depends(require_principal),
) -> NoReturn:
    _require_permission(principal, "notification:read")
    _safe_id(notification_id, "notification_id")
    _unavailable("notifications_unavailable")


@router.get("/notifications/admin", status_code=501, response_model=None)
async def admin_notifications(principal: AuthPrincipal = Depends(require_principal)) -> NoReturn:
    _require_permission(principal, "notification:admin")
    _unavailable("notifications_unavailable")


@router.post("/notifications/", status_code=501, response_model=None)
async def create_notification(principal: AuthPrincipal = Depends(require_principal)) -> NoReturn:
    _require_permission(principal, "notification:admin")
    _unavailable("notifications_unavailable")


@router.put("/notifications/{notification_id}", status_code=501, response_model=None)
async def update_notification(
    notification_id: str,
    principal: AuthPrincipal = Depends(require_principal),
) -> NoReturn:
    _require_permission(principal, "notification:admin")
    _safe_id(notification_id, "notification_id")
    _unavailable("notifications_unavailable")


@router.delete("/notifications/{notification_id}", status_code=501, response_model=None)
async def delete_notification(
    notification_id: str,
    principal: AuthPrincipal = Depends(require_principal),
) -> NoReturn:
    _require_permission(principal, "notification:admin")
    _safe_id(notification_id, "notification_id")
    _unavailable("notifications_unavailable")


@router.post("/notifications/{notification_id}/replay", status_code=501, response_model=None)
async def replay_notification(
    notification_id: str,
    principal: AuthPrincipal = Depends(require_principal),
) -> NoReturn:
    _require_permission(principal, "notification:admin")
    _safe_id(notification_id, "notification_id")
    _unavailable("notifications_unavailable")
