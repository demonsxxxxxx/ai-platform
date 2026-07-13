from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from app.auth import AuthPrincipal, principal_to_response, require_principal, sign_principal_session, verify_principal_session
from app.db import transaction
from app.models import LoginRequest, PrincipalResponse
from app.repositories import append_audit_log, ensure_user
from app.settings import get_settings
from app.validation import assert_safe_id

router = APIRouter()

AI_USER_PERMISSIONS = [
    "agent:use",
    "chat:read",
    "chat:write",
    "session:read",
    "session:write",
    "skill:read",
    "marketplace:read",
    "mcp:read",
    "persona_preset:read",
    "avatar:upload",
    "feedback:write",
    "notification:read",
    "artifact:download",
    "file:upload",
    "file:upload:document",
]

AI_ADMIN_PERMISSIONS = [
    "agent:read",
    "agent:admin",
    "model:admin",
    "settings:read",
    "settings:manage",
    "settings:admin",
    "admin:status",
    "skill:write",
    "skill:delete",
    "skill:admin",
    "marketplace:publish",
    "marketplace:admin",
    "mcp:write_sse",
    "mcp:write_http",
    "mcp:write_sandbox",
    "mcp:delete",
    "mcp:admin",
    "persona_preset:write",
    "persona_preset:admin",
    "channel:read",
    "channel:write",
    "channel:delete",
    "channel:admin",
    "user:read",
    "user:write",
    "user:delete",
    "user:admin",
    "role:read",
    "role:manage",
    "feedback:read",
    "feedback:admin",
    "notification:admin",
    "notification:manage",
]


async def call_existing_login(username: str, password: str) -> dict[str, Any]:
    settings = get_settings()
    base_url = settings.existing_auth_base_url.rstrip("/")
    body = {
        "userName": username,
        "username": username,
        "user_name": username,
        "password": password,
    }
    async with httpx.AsyncClient(timeout=settings.existing_auth_timeout_seconds) as client:
        response = await client.post(f"{base_url}/api/Login/", json=body)
        response.raise_for_status()
        payload = response.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="invalid_login_response")
    return payload


async def call_existing_user_info(work_id: str) -> dict[str, Any]:
    settings = get_settings()
    base_url = settings.existing_user_info_base_url.rstrip("/")
    async with httpx.AsyncClient(timeout=settings.existing_auth_timeout_seconds) as client:
        response = await client.get(f"{base_url}/api/userManage/{work_id}/info")
        response.raise_for_status()
        payload = response.json()
    return payload if isinstance(payload, dict) else {}


def _as_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return []


def _roles_from_user_info(payload: dict[str, Any]) -> list[str]:
    for key in ("roles", "roleList", "role_list", "RoleList"):
        roles = _as_list(payload.get(key))
        if roles:
            return roles
    role = payload.get("role") or payload.get("roleName") or payload.get("role_name")
    return _as_list(role)


def _permissions_from_user_info(payload: dict[str, Any]) -> list[str]:
    for key in ("permissions", "perms", "permissionList", "permission_list"):
        permissions = _as_list(payload.get(key))
        if permissions:
            return permissions
    return []


def _department_from_user_info(payload: dict[str, Any]) -> str:
    value = payload.get("department")
    if not isinstance(value, str):
        return ""
    candidate = value.strip()
    if not candidate:
        return ""
    try:
        return assert_safe_id(candidate, "department")
    except ValueError:
        return ""


def _has_admin_role(roles: list[str]) -> bool:
    normalized = {role.strip().lower() for role in roles}
    return bool(normalized.intersection({"admin", "developer"}))


def _configured_admin_work_ids() -> set[str]:
    raw_value = getattr(get_settings(), "ai_admin_work_ids", "")
    return {item.strip().lower() for item in raw_value.split(",") if item.strip()}


def _is_configured_admin(work_id: str) -> bool:
    return work_id.strip().lower() in _configured_admin_work_ids()


def _roles_for_login(work_id: str, login_name: str, user_info: dict[str, Any]) -> list[str]:
    upstream_roles = _roles_from_user_info(user_info)
    is_admin = (
        _has_admin_role(upstream_roles)
        or _is_configured_admin(work_id)
        or _is_configured_admin(login_name)
    )
    return ["admin" if is_admin else "user"]


def _is_failed_login_payload(payload: dict[str, Any]) -> bool:
    status_value = str(payload.get("status") or "").strip().lower()
    return status_value in {"unsuccessfully!", "locked", "disabled"}


def _merge_permissions(*permission_groups: list[str]) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for group in permission_groups:
        for permission in group:
            normalized = permission.strip()
            if normalized and normalized not in seen:
                merged.append(normalized)
                seen.add(normalized)
    return merged


def _ai_permissions_for_login(user_info: dict[str, Any], roles: list[str]) -> list[str]:
    admin_permissions = AI_ADMIN_PERMISSIONS if _has_admin_role(roles) else []
    return _merge_permissions(
        AI_USER_PERMISSIONS,
        admin_permissions,
    )


@router.post("/auth/login", response_model=PrincipalResponse)
async def login(request: LoginRequest, response: Response) -> PrincipalResponse:
    principal = await _login_principal(request, response)
    return PrincipalResponse.model_validate(principal_to_response(principal))


async def _login_principal(request: LoginRequest, response: Response) -> AuthPrincipal:
    try:
        login_payload = await call_existing_login(request.user_name, request.password)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="company_login_failed") from exc
    if _is_failed_login_payload(login_payload):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="company_login_failed")
    work_id = str(login_payload.get("workId") or login_payload.get("workid") or login_payload.get("userName") or "").strip()
    if not work_id:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="login_missing_work_id")
    try:
        user_info = await call_existing_user_info(work_id)
    except Exception:
        user_info = {}
    roles = _roles_for_login(work_id, request.user_name, user_info)
    principal = AuthPrincipal(
        user_id=work_id,
        display_name=str(login_payload.get("cnName") or login_payload.get("userName") or work_id),
        tenant_id=get_settings().default_tenant_id,
        department_id=_department_from_user_info(user_info),
        roles=roles,
        permissions=_ai_permissions_for_login(user_info, roles),
        source="company-login",
    )
    async with transaction() as conn:
        await ensure_user(conn, tenant_id=principal.tenant_id, user_id=principal.user_id, display_name=principal.display_name)
        await append_audit_log(
            conn,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            action="auth.login",
            target_type="user",
            target_id=principal.user_id,
            payload_json={
                "source": principal.source,
                "work_id": principal.user_id,
                "roles": principal.roles,
                "permissions": principal.permissions,
                "is_admin": _has_admin_role(principal.roles),
            },
        )
    settings = get_settings()
    response.set_cookie(
        settings.ai_session_cookie_name,
        sign_principal_session(principal),
        max_age=settings.ai_session_max_age_seconds,
        httponly=True,
        samesite="lax",
        secure=settings.ai_session_cookie_secure,
        path="/",
    )
    return principal


@router.get("/auth/me", response_model=PrincipalResponse)
async def me(principal: AuthPrincipal = Depends(require_principal)) -> PrincipalResponse:
    return PrincipalResponse.model_validate(principal_to_response(principal))


@router.post("/auth/logout")
async def logout(response: Response) -> dict[str, str]:
    settings = get_settings()
    response.delete_cookie(settings.ai_session_cookie_name, path="/")
    return {"status": "logged_out"}
