from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel, ValidationError

from app import repositories
from app.auth import AuthPrincipal, is_ai_admin, require_principal
from app.db import transaction
from app.models import (
    ChannelAdminCreateRequest,
    ChannelAdminCredentialsRequest,
    ChannelAdminOperationResponse,
    ChannelAdminRetentionRequest,
    ChannelAdminTestRequest,
    PublicChannelResponse,
    PublicChannelsResponse,
)
from app.settings import get_settings
from app.validation import assert_safe_id

router = APIRouter()

CHANNEL_PERMISSIONS = ("channel:read", "channel:write", "channel:delete", "channel:admin")
ORDERED_CHANNEL_PERMISSIONS = CHANNEL_PERMISSIONS
PUBLIC_CHANNEL_CATALOG = (
    PublicChannelResponse(
        channel_id="default-chat",
        display_name="Default Chat",
        channel_type="chat",
        enabled=True,
        capabilities=["chat:read", "chat:write", "file:upload"],
        connection_state="not_configured",
        redaction_policy="secrets_never_projected",
        retention_policy="tenant_default",
        last_actor=None,
        updated_at=None,
    ),
)


def _effective_permission_set(principal: AuthPrincipal) -> set[str]:
    granted = {item.strip() for item in principal.permissions if item.strip()}
    if is_ai_admin(principal):
        granted.update(ORDERED_CHANNEL_PERMISSIONS)
    if "channel:admin" in granted:
        granted.update(CHANNEL_PERMISSIONS)
    return granted


def _require_permission(principal: AuthPrincipal, permission: str) -> None:
    if permission not in _effective_permission_set(principal):
        raise HTTPException(status_code=403, detail=f"missing_permission:{permission}")


def _request_model(model_type: type[BaseModel], payload: Any) -> BaseModel:
    try:
        return model_type.model_validate(payload)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors(include_input=False, include_url=False)) from exc


def _safe_channel_id(channel_id: str) -> str:
    try:
        return assert_safe_id(channel_id, "channel_id")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _safe_workspace_id(workspace_id: str | None) -> str:
    candidate = workspace_id or get_settings().default_workspace_id
    try:
        return assert_safe_id(candidate, "workspace_id")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _channel_for_workspace(channel: PublicChannelResponse, workspace_id: str) -> PublicChannelResponse:
    return channel.model_copy(update={"workspace_id": workspace_id})


def _known_channel(channel_id: str) -> PublicChannelResponse:
    for channel in PUBLIC_CHANNEL_CATALOG:
        if channel.channel_id == channel_id:
            return channel
    raise HTTPException(status_code=404, detail="channel_not_found")


async def _record_admin_operation(
    *,
    principal: AuthPrincipal,
    channel_id: str,
    workspace_id: str,
    operation: str,
    require_existing: bool = True,
    payload_json: dict[str, Any] | None = None,
) -> ChannelAdminOperationResponse:
    safe_channel_id = _safe_channel_id(channel_id)
    safe_workspace_id = _safe_workspace_id(workspace_id)
    if require_existing:
        _known_channel(safe_channel_id)
    audit_payload = {
        "operation": operation,
        "workspace_id": safe_workspace_id,
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
            action=f"channel.admin.{operation}_requested",
            target_type="channel",
            target_id=safe_channel_id,
            payload_json=audit_payload,
        )
    return ChannelAdminOperationResponse(
        channel_id=safe_channel_id,
        workspace_id=safe_workspace_id,
        operation=operation,
        status="queued",
        audit_id=audit_id,
        message=f"Channel {operation.replace('_', ' ')} accepted for audited execution",
    )


@router.get("/channels/catalog", response_model=PublicChannelsResponse)
async def list_channel_catalog(
    workspace_id: str = Query(default="default"),
    principal: AuthPrincipal = Depends(require_principal),
) -> PublicChannelsResponse:
    """Return tenant-scoped public channel metadata without secrets."""

    _require_permission(principal, "channel:read")
    safe_workspace_id = _safe_workspace_id(workspace_id)
    channels = [_channel_for_workspace(channel, safe_workspace_id) for channel in PUBLIC_CHANNEL_CATALOG]
    return PublicChannelsResponse(
        tenant_id=principal.tenant_id,
        workspace_id=safe_workspace_id,
        channels=channels,
        total=len(channels),
    )


@router.post("/admin/channels", response_model=ChannelAdminOperationResponse)
async def create_admin_channel(
    workspace_id: str = Query(default="default"),
    principal: AuthPrincipal = Depends(require_principal),
    payload: Any = Body(default=None),
) -> ChannelAdminOperationResponse:
    """Accept an audited admin channel creation request without credentials."""

    _require_permission(principal, "channel:admin")
    request = _request_model(ChannelAdminCreateRequest, payload or {})
    return await _record_admin_operation(
        principal=principal,
        channel_id=request.channel_id,
        workspace_id=workspace_id,
        operation="create",
        require_existing=False,
        payload_json={"enabled": request.enabled},
    )


@router.post("/admin/channels/{channel_id}/test", response_model=ChannelAdminOperationResponse)
async def test_admin_channel(
    channel_id: str,
    workspace_id: str = Query(default="default"),
    principal: AuthPrincipal = Depends(require_principal),
    payload: Any = Body(default=None),
) -> ChannelAdminOperationResponse:
    """Accept an audited admin channel test request without credential material."""

    _require_permission(principal, "channel:admin")
    request = _request_model(ChannelAdminTestRequest, payload or {})
    response = await _record_admin_operation(
        principal=principal,
        channel_id=channel_id,
        workspace_id=workspace_id,
        operation="test",
        payload_json={"dry_run": request.dry_run},
    )
    return response.model_copy(update={"message": "Channel test accepted for audited execution"})


@router.post("/admin/channels/{channel_id}/enable", response_model=ChannelAdminOperationResponse)
async def enable_admin_channel(
    channel_id: str,
    workspace_id: str = Query(default="default"),
    principal: AuthPrincipal = Depends(require_principal),
) -> ChannelAdminOperationResponse:
    """Accept an audited admin channel enable request."""

    _require_permission(principal, "channel:admin")
    return await _record_admin_operation(
        principal=principal,
        channel_id=channel_id,
        workspace_id=workspace_id,
        operation="enable",
    )


@router.post("/admin/channels/{channel_id}/disable", response_model=ChannelAdminOperationResponse)
async def disable_admin_channel(
    channel_id: str,
    workspace_id: str = Query(default="default"),
    principal: AuthPrincipal = Depends(require_principal),
) -> ChannelAdminOperationResponse:
    """Accept an audited admin channel disable request."""

    _require_permission(principal, "channel:admin")
    return await _record_admin_operation(
        principal=principal,
        channel_id=channel_id,
        workspace_id=workspace_id,
        operation="disable",
    )


@router.put("/admin/channels/{channel_id}/credentials", response_model=ChannelAdminOperationResponse)
async def update_admin_channel_credentials(
    channel_id: str,
    workspace_id: str = Query(default="default"),
    principal: AuthPrincipal = Depends(require_principal),
    payload: Any = Body(default=None),
) -> ChannelAdminOperationResponse:
    """Accept credential reference updates without projecting secret material."""

    _require_permission(principal, "channel:admin")
    _request_model(ChannelAdminCredentialsRequest, payload or {})
    return await _record_admin_operation(
        principal=principal,
        channel_id=channel_id,
        workspace_id=workspace_id,
        operation="update_credentials",
        payload_json={"secret_pointer_received": bool(payload)},
    )


@router.put("/admin/channels/{channel_id}/retention", response_model=ChannelAdminOperationResponse)
async def update_admin_channel_retention(
    channel_id: str,
    workspace_id: str = Query(default="default"),
    principal: AuthPrincipal = Depends(require_principal),
    payload: Any = Body(default=None),
) -> ChannelAdminOperationResponse:
    """Accept audited channel retention policy updates."""

    _require_permission(principal, "channel:admin")
    request = _request_model(ChannelAdminRetentionRequest, payload or {})
    return await _record_admin_operation(
        principal=principal,
        channel_id=channel_id,
        workspace_id=workspace_id,
        operation="update_retention",
        payload_json={"retention_policy": request.retention_policy},
    )
