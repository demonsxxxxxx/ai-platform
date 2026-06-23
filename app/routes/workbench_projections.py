from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel, ValidationError

from app import repositories
from app.auth import AuthPrincipal, is_ai_admin, require_principal
from app.db import transaction
from app.models import (
    WorkbenchAuditResponse,
    WorkbenchFeedbackItemResponse,
    WorkbenchFeedbackListResponse,
    WorkbenchFeedbackStatsResponse,
    WorkbenchFeedbackUpdateRequest,
    WorkbenchGovernanceResponse,
    WorkbenchI18nTextResponse,
    WorkbenchNotificationListResponse,
    WorkbenchNotificationResponse,
    WorkbenchNotificationWriteRequest,
    WorkbenchOperationResponse,
    WorkbenchSettingGroupResponse,
    WorkbenchSettingItemResponse,
    WorkbenchSettingResetResponse,
    WorkbenchSettingUpdateRequest,
    WorkbenchSettingWriteResponse,
    WorkbenchSettingsResponse,
    WorkbenchUserListResponse,
    WorkbenchUserResponse,
    WorkbenchUserWriteRequest,
)
from app.validation import assert_safe_id

router = APIRouter()

DOMAIN_PERMISSIONS = (
    "user:read",
    "user:admin",
    "settings:read",
    "settings:admin",
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
        ("settings:read", "settings:admin"),
        ("feedback:read", "feedback:admin"),
        ("notification:read", "notification:admin"),
    ):
        if admin_permission in granted:
            granted.add(read_permission)
    return granted


def _require_permission(principal: AuthPrincipal, permission: str) -> None:
    if permission not in _effective_permission_set(principal):
        raise HTTPException(status_code=403, detail=f"missing_permission:{permission}")


def _request_model(model_type: type[BaseModel], payload: Any) -> BaseModel:
    try:
        return model_type.model_validate(payload)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail="invalid_workbench_payload") from exc


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


async def _record_operation(
    *,
    principal: AuthPrincipal,
    target_type: str,
    target_id: str,
    operation: str,
    payload_json: dict[str, Any] | None = None,
) -> WorkbenchOperationResponse:
    safe_target_id = _safe_id(target_id, f"{target_type}_id")
    audit_payload = {
        "operation": operation,
        "department_id": principal.department_id,
        "secret_material_projected": False,
    }
    if payload_json:
        audit_payload.update(payload_json)
    async with transaction() as conn:
        audit_id = await repositories.append_audit_log(
            conn,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            action=f"{target_type}.admin.{operation}_requested",
            target_type=target_type,
            target_id=safe_target_id,
            payload_json=audit_payload,
        )
    return WorkbenchOperationResponse(
        target_type=target_type,
        target_id=safe_target_id,
        operation=operation,
        status="queued",
        audit_id=audit_id,
        message=f"{target_type} {operation.replace('_', ' ')} accepted for audited execution",
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
    skip: int = 0,
    limit: int = 50,
    search: str | None = None,
    principal: AuthPrincipal = Depends(require_principal),
) -> WorkbenchUserListResponse:
    """Return a safe company user-directory projection."""

    _require_permission(principal, "user:read")
    user = _user_projection(principal)
    users = [user]
    if search and search.lower() not in user.username.lower() and search.lower() not in user.full_name.lower():
        users = []
    return WorkbenchUserListResponse(
        users=users,
        items=users,
        total=len(users),
        skip=skip,
        limit=limit,
        governance=_governance(principal, "safe_user_directory", degraded=not is_ai_admin(principal)),
    )


@router.get("/users/{user_id}", response_model=WorkbenchUserResponse)
async def get_user(user_id: str, principal: AuthPrincipal = Depends(require_principal)) -> WorkbenchUserResponse:
    """Return one safe user projection."""

    _require_permission(principal, "user:read")
    safe_user_id = _safe_id(user_id, "user_id")
    user = _user_projection(principal)
    if safe_user_id != user.id and not is_ai_admin(principal):
        raise HTTPException(status_code=403, detail="missing_permission:user:admin")
    return user.model_copy(update={"id": safe_user_id, "username": safe_user_id}) if is_ai_admin(principal) else user


@router.post("/users/", response_model=WorkbenchOperationResponse)
async def create_user(
    principal: AuthPrincipal = Depends(require_principal),
    payload: Any = Body(default=None),
) -> WorkbenchOperationResponse:
    """Queue an audited user creation request."""

    _require_permission(principal, "user:admin")
    request = _request_model(WorkbenchUserWriteRequest, payload or {})
    target_id = request.username or "new-user"
    return await _record_operation(
        principal=principal,
        target_type="user",
        target_id=target_id,
        operation="create",
        payload_json={"role_count": len(request.roles), "permission_count": len(request.permissions)},
    )


@router.put("/users/{user_id}", response_model=WorkbenchOperationResponse)
async def update_user(
    user_id: str,
    principal: AuthPrincipal = Depends(require_principal),
    payload: Any = Body(default=None),
) -> WorkbenchOperationResponse:
    """Queue an audited user update request."""

    _require_permission(principal, "user:admin")
    request = _request_model(WorkbenchUserWriteRequest, payload or {})
    return await _record_operation(
        principal=principal,
        target_type="user",
        target_id=user_id,
        operation="update",
        payload_json={
            "role_count": len(request.roles),
            "permission_count": len(request.permissions),
            "is_active_changed": request.is_active is not None,
        },
    )


@router.delete("/users/{user_id}", response_model=WorkbenchOperationResponse)
async def delete_user(user_id: str, principal: AuthPrincipal = Depends(require_principal)) -> WorkbenchOperationResponse:
    """Queue an audited user deletion request."""

    _require_permission(principal, "user:admin")
    return await _record_operation(principal=principal, target_type="user", target_id=user_id, operation="delete")


@router.get("/settings/", response_model=WorkbenchSettingsResponse)
async def list_settings(principal: AuthPrincipal = Depends(require_principal)) -> WorkbenchSettingsResponse:
    """Return personal preferences and masked system/runtime settings."""

    _require_permission(principal, "settings:read")
    personal = WorkbenchSettingGroupResponse(
        category="personal_preferences",
        items=[
            WorkbenchSettingItemResponse(
                key="ui.locale",
                value="zh-CN",
                type="string",
                category="personal_preferences",
                label="Locale",
            )
        ],
    )
    system = WorkbenchSettingGroupResponse(
        category="system_runtime",
        items=[
            WorkbenchSettingItemResponse(
                key="gateway.api_key",
                value="[redacted]",
                type="secret",
                category="system_runtime",
                label="Gateway API key",
                is_public=False,
                is_secret=True,
                audit_required=True,
                rollback_available=True,
            )
        ],
    )
    return WorkbenchSettingsResponse(
        settings={"personal_preferences": personal, "system_runtime": system},
        governance=_governance(
            principal,
            "safe_settings_split",
            degraded=not is_ai_admin(principal),
            audit_required=True,
            rollback_available=True,
        ),
    )


@router.get("/settings/{key}", response_model=WorkbenchSettingItemResponse)
async def get_setting(key: str, principal: AuthPrincipal = Depends(require_principal)) -> WorkbenchSettingItemResponse:
    """Return one safe settings item."""

    _require_permission(principal, "settings:read")
    safe_key = key.strip()
    if safe_key == "gateway.api_key":
        return WorkbenchSettingItemResponse(
            key=safe_key,
            value="[redacted]",
            type="secret",
            category="system_runtime",
            label="Gateway API key",
            is_public=False,
            is_secret=True,
            audit_required=True,
            rollback_available=True,
        )
    return WorkbenchSettingItemResponse(
        key=safe_key,
        value="zh-CN",
        type="string",
        category="personal_preferences",
        label=safe_key,
    )


@router.put("/settings/{key}", response_model=WorkbenchSettingWriteResponse)
async def update_setting(
    key: str,
    principal: AuthPrincipal = Depends(require_principal),
    payload: Any = Body(default=None),
) -> WorkbenchSettingWriteResponse:
    """Queue an audited settings update with masked value projection."""

    _require_permission(principal, "settings:admin")
    request = _request_model(WorkbenchSettingUpdateRequest, payload or {})
    operation = await _record_operation(
        principal=principal,
        target_type="settings",
        target_id=key,
        operation="update",
        payload_json={"has_rollback_id": request.rollback_id is not None, "value_projected": False},
    )
    return WorkbenchSettingWriteResponse(
        key=key,
        value="[redacted]",
        audit=WorkbenchAuditResponse(audit_id=operation.audit_id, action="settings.admin.update_requested"),
    )


@router.post("/settings/reset", response_model=WorkbenchSettingResetResponse)
async def reset_all_settings(principal: AuthPrincipal = Depends(require_principal)) -> WorkbenchSettingResetResponse:
    """Queue audited reset for all settings."""

    _require_permission(principal, "settings:admin")
    operation = await _record_operation(
        principal=principal,
        target_type="settings",
        target_id="all",
        operation="reset_all",
    )
    return WorkbenchSettingResetResponse(key=None, reset_count=2, audit_id=operation.audit_id)


@router.post("/settings/reset/{key}", response_model=WorkbenchSettingResetResponse)
async def reset_setting(key: str, principal: AuthPrincipal = Depends(require_principal)) -> WorkbenchSettingResetResponse:
    """Queue audited reset for one setting."""

    _require_permission(principal, "settings:admin")
    operation = await _record_operation(principal=principal, target_type="settings", target_id=key, operation="reset")
    return WorkbenchSettingResetResponse(key=key, reset_count=1, audit_id=operation.audit_id)


def _feedback_item() -> WorkbenchFeedbackItemResponse:
    return WorkbenchFeedbackItemResponse(
        id="fb-1",
        user_id="ordinary",
        username="ordinary",
        session_id="session-a",
        run_id="run-a",
        rating="down",
        comment="Needs follow-up",
        assignment_state="unassigned",
        labels=[],
        status="open",
        created_at=None,
    )


@router.get("/feedback/", response_model=WorkbenchFeedbackListResponse)
async def list_feedback(
    skip: int = 0,
    limit: int = 50,
    principal: AuthPrincipal = Depends(require_principal),
) -> WorkbenchFeedbackListResponse:
    """Return a safe aggregate feedback desk projection."""

    _require_permission(principal, "feedback:read")
    items = [_feedback_item()][skip : skip + limit]
    return WorkbenchFeedbackListResponse(
        items=items,
        total=len(items),
        stats=WorkbenchFeedbackStatsResponse(total_count=1, up_count=0, down_count=1, up_percentage=0.0),
        governance=_governance(principal, "safe_feedback_desk", degraded=not is_ai_admin(principal), audit_required=True),
    )


@router.get("/feedback/stats", response_model=WorkbenchFeedbackStatsResponse)
async def feedback_stats(principal: AuthPrincipal = Depends(require_principal)) -> WorkbenchFeedbackStatsResponse:
    _require_permission(principal, "feedback:read")
    return WorkbenchFeedbackStatsResponse(total_count=1, up_count=0, down_count=1, up_percentage=0.0)


@router.put("/feedback/{feedback_id}", response_model=WorkbenchOperationResponse)
async def update_feedback(
    feedback_id: str,
    principal: AuthPrincipal = Depends(require_principal),
    payload: Any = Body(default=None),
) -> WorkbenchOperationResponse:
    """Queue audited feedback assignment/closure/label update."""

    _require_permission(principal, "feedback:admin")
    request = _request_model(WorkbenchFeedbackUpdateRequest, payload or {})
    return await _record_operation(
        principal=principal,
        target_type="feedback",
        target_id=feedback_id,
        operation="update",
        payload_json={
            "assignee_changed": request.assignee_id is not None,
            "assignment_state_changed": request.assignment_state is not None,
            "status_changed": request.status is not None,
            "label_count": len(request.labels),
        },
    )


@router.delete("/feedback/{feedback_id}", response_model=WorkbenchOperationResponse)
async def delete_feedback(
    feedback_id: str,
    principal: AuthPrincipal = Depends(require_principal),
) -> WorkbenchOperationResponse:
    _require_permission(principal, "feedback:admin")
    return await _record_operation(principal=principal, target_type="feedback", target_id=feedback_id, operation="delete")


def _notification(*, include_admin: bool = False, read_state: str | None = "unread") -> WorkbenchNotificationResponse:
    return WorkbenchNotificationResponse(
        id="platform-announcement",
        title_i18n=WorkbenchI18nTextResponse(en="Platform announcement", zh="平台公告"),
        content_i18n=WorkbenchI18nTextResponse(en="Workbench governance is active.", zh="工作台治理已启用。"),
        type="info",
        start_time=None,
        end_time=None,
        expires_at=None,
        is_active=True,
        read_state=read_state,
        audience={"tenant_id": "default", "departments": []} if include_admin else None,
        audit_history=[],
        created_at=None,
        updated_at=None,
        created_by="system",
    )


@router.get("/notifications/active", response_model=list[WorkbenchNotificationResponse], response_model_exclude_none=True)
async def active_notifications(principal: AuthPrincipal = Depends(require_principal)) -> list[WorkbenchNotificationResponse]:
    """Return active notifications with per-user read state only."""

    _require_permission(principal, "notification:read")
    return [_notification(include_admin=False, read_state="unread")]


@router.post("/notifications/{notification_id}/dismiss", response_model=WorkbenchOperationResponse)
async def dismiss_notification(
    notification_id: str,
    principal: AuthPrincipal = Depends(require_principal),
) -> WorkbenchOperationResponse:
    _require_permission(principal, "notification:read")
    return await _record_operation(
        principal=principal,
        target_type="notification",
        target_id=notification_id,
        operation="dismiss",
    )


@router.get("/notifications/admin", response_model=WorkbenchNotificationListResponse)
async def admin_notifications(
    skip: int = 0,
    limit: int = 50,
    principal: AuthPrincipal = Depends(require_principal),
) -> WorkbenchNotificationListResponse:
    """Return notification management projection."""

    _require_permission(principal, "notification:admin")
    items = [_notification(include_admin=True, read_state=None)][skip : skip + limit]
    return WorkbenchNotificationListResponse(
        items=items,
        total=len(items),
        governance=_governance(principal, "safe_notification_management", audit_required=True),
    )


@router.post("/notifications/", response_model=WorkbenchOperationResponse)
async def create_notification(
    principal: AuthPrincipal = Depends(require_principal),
    payload: Any = Body(default=None),
) -> WorkbenchOperationResponse:
    _require_permission(principal, "notification:admin")
    request = _request_model(WorkbenchNotificationWriteRequest, payload or {})
    return await _record_operation(
        principal=principal,
        target_type="notification",
        target_id="new-notification",
        operation="create",
        payload_json={
            "audience_department_count": len(request.audience.get("departments") or []),
            "has_tenant_audience": bool(request.audience.get("tenant_id")),
            "replay": request.replay,
            "has_expires_at": request.expires_at is not None,
        },
    )


@router.put("/notifications/{notification_id}", response_model=WorkbenchOperationResponse)
async def update_notification(
    notification_id: str,
    principal: AuthPrincipal = Depends(require_principal),
    payload: Any = Body(default=None),
) -> WorkbenchOperationResponse:
    _require_permission(principal, "notification:admin")
    request = _request_model(WorkbenchNotificationWriteRequest, payload or {})
    return await _record_operation(
        principal=principal,
        target_type="notification",
        target_id=notification_id,
        operation="update",
        payload_json={
            "audience_department_count": len(request.audience.get("departments") or []),
            "has_tenant_audience": bool(request.audience.get("tenant_id")),
            "has_expires_at": request.expires_at is not None,
        },
    )


@router.delete("/notifications/{notification_id}", response_model=WorkbenchOperationResponse)
async def delete_notification(
    notification_id: str,
    principal: AuthPrincipal = Depends(require_principal),
) -> WorkbenchOperationResponse:
    _require_permission(principal, "notification:admin")
    return await _record_operation(
        principal=principal,
        target_type="notification",
        target_id=notification_id,
        operation="delete",
    )


@router.post("/notifications/{notification_id}/replay", response_model=WorkbenchOperationResponse)
async def replay_notification(
    notification_id: str,
    principal: AuthPrincipal = Depends(require_principal),
) -> WorkbenchOperationResponse:
    _require_permission(principal, "notification:admin")
    return await _record_operation(
        principal=principal,
        target_type="notification",
        target_id=notification_id,
        operation="replay",
    )
