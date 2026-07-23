from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from app.auth import AuthPrincipal, normalize_roles
from app.settings import get_settings
from app.validation import assert_safe_id


CURRENT_PRINCIPAL_DENIAL_REASON = "current_principal_authority_denied"

AI_USER_PERMISSIONS = (
    "agent:use",
    "chat:read",
    "chat:write",
    "session:read",
    "session:write",
    "skill:read",
    "marketplace:read",
    "mcp:read",
    "avatar:upload",
    "feedback:write",
    "notification:read",
    "artifact:download",
    "file:upload",
    "file:upload:document",
)

AI_ADMIN_PERMISSIONS = (
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
)

UserInfoAdapter = Callable[[str], Awaitable[object]]

_ROLE_LIST_KEYS = ("roles", "roleList", "role_list", "RoleList")
_ROLE_KEYS = ("role", "roleName", "role_name")
_IDENTITY_KEYS = ("workId", "workid", "userName", "username", "user_name")
_TENANT_KEYS = ("tenant_id", "tenantId", "tenant")
_ADMIN_UPSTREAM_ROLES = {"admin", "developer"}
_ELIGIBLE_STATUS_VALUES = {"active", "enabled", "normal", "success", "successful", "successfully!"}
_INELIGIBLE_STATUS_VALUES = {
    "disabled",
    "inactive",
    "ineligible",
    "locked",
    "terminated",
    "unsuccessful",
    "unsuccessfully!",
}


class PrincipalAuthorityDenied(Exception):
    """Report one stable fail-closed result without retaining upstream details."""

    def __init__(self) -> None:
        super().__init__(CURRENT_PRINCIPAL_DENIAL_REASON)
        self.reason = CURRENT_PRINCIPAL_DENIAL_REASON


async def fetch_company_user_info(work_id: str, *, settings: Any | None = None) -> object:
    """Fetch one company user-info document without retries or response coercion."""

    effective_settings = settings or get_settings()
    base_url = effective_settings.existing_user_info_base_url.rstrip("/")
    async with httpx.AsyncClient(timeout=effective_settings.existing_auth_timeout_seconds) as client:
        response = await client.get(f"{base_url}/api/userManage/{work_id}/info")
        response.raise_for_status()
        return response.json()


async def resolve_login_principal(
    *,
    work_id: str,
    login_name: str,
    display_name: str,
    user_info_adapter: UserInfoAdapter | None = None,
    settings: Any | None = None,
) -> AuthPrincipal:
    """Resolve the backward-compatible company-login principal projection."""

    effective_settings = settings or get_settings()
    adapter = user_info_adapter or _production_adapter(effective_settings)
    try:
        raw_user_info = await adapter(work_id)
    except Exception:
        raw_user_info = {}
    user_info = raw_user_info if isinstance(raw_user_info, dict) else {}
    roles = _effective_roles(
        work_id,
        login_name,
        user_info,
        settings=effective_settings,
        strict=False,
    )
    return AuthPrincipal(
        user_id=work_id,
        display_name=display_name,
        tenant_id=effective_settings.default_tenant_id,
        department_id=_department_from_user_info(user_info, strict=False),
        roles=roles,
        permissions=_effective_permissions(roles),
        source="company-login",
    )


async def resolve_current_principal(
    *,
    user_id: str,
    tenant_id: str,
    user_info_adapter: UserInfoAdapter | None = None,
    settings: Any | None = None,
) -> AuthPrincipal:
    """Resolve current dispatch authority or raise one stable fail-closed error."""

    effective_settings = settings or get_settings()
    if tenant_id != str(effective_settings.default_tenant_id):
        raise PrincipalAuthorityDenied()
    adapter = user_info_adapter or _production_adapter(effective_settings)
    try:
        raw_user_info = await adapter(user_id)
    except Exception:
        raise PrincipalAuthorityDenied() from None
    if not isinstance(raw_user_info, dict):
        raise PrincipalAuthorityDenied()

    identity_aliases = _validated_identity_aliases(raw_user_info)
    if user_id not in identity_aliases:
        raise PrincipalAuthorityDenied()
    _validate_tenant(raw_user_info, tenant_id)
    _validate_eligibility(raw_user_info)
    roles = _effective_roles(
        user_id,
        "",
        raw_user_info,
        settings=effective_settings,
        strict=True,
        identity_aliases=identity_aliases,
    )
    department_id = _department_from_user_info(raw_user_info, strict=True)
    return AuthPrincipal(
        user_id=user_id,
        display_name=user_id,
        tenant_id=tenant_id,
        department_id=department_id,
        roles=roles,
        permissions=_effective_permissions(roles),
        source="company-user-info-current",
    )


def _production_adapter(settings: Any) -> UserInfoAdapter:
    async def adapter(work_id: str) -> object:
        return await fetch_company_user_info(work_id, settings=settings)

    return adapter


def _as_list(value: object, *, strict: bool) -> list[str]:
    if isinstance(value, list):
        if strict and any(not isinstance(item, str) for item in value):
            raise PrincipalAuthorityDenied()
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if strict:
        raise PrincipalAuthorityDenied()
    return []


def _roles_from_user_info(payload: dict[str, Any], *, strict: bool) -> list[str]:
    recognized = False
    for key in _ROLE_LIST_KEYS:
        if key not in payload:
            continue
        recognized = True
        roles = _as_list(payload[key], strict=strict)
        if roles:
            return roles
    for key in _ROLE_KEYS:
        if key not in payload:
            continue
        recognized = True
        roles = _as_list(payload[key], strict=strict)
        if roles:
            return roles
    if strict and not recognized:
        raise PrincipalAuthorityDenied()
    return []


def _configured_admin_ids(settings: Any) -> set[str]:
    raw_value = str(getattr(settings, "ai_admin_work_ids", "") or "")
    return {item.strip().casefold() for item in raw_value.split(",") if item.strip()}


def _effective_roles(
    work_id: str,
    login_name: str,
    user_info: dict[str, Any],
    *,
    settings: Any,
    strict: bool,
    identity_aliases: set[str] | None = None,
) -> list[str]:
    upstream_roles = set(normalize_roles(_roles_from_user_info(user_info, strict=strict)))
    configured_admin_ids = _configured_admin_ids(settings)
    candidate_admin_ids = {
        value.strip().casefold()
        for value in (work_id, login_name, *(identity_aliases or set()))
        if value.strip()
    }
    is_admin = bool(
        upstream_roles.intersection(_ADMIN_UPSTREAM_ROLES)
        or configured_admin_ids.intersection(candidate_admin_ids)
    )
    return ["admin" if is_admin else "user"]


def _effective_permissions(roles: list[str]) -> list[str]:
    permissions = list(AI_USER_PERMISSIONS)
    if set(normalize_roles(roles)).intersection(_ADMIN_UPSTREAM_ROLES):
        permissions.extend(AI_ADMIN_PERMISSIONS)
    return permissions


def _department_from_user_info(payload: dict[str, Any], *, strict: bool) -> str:
    if "department" not in payload or payload.get("department") is None:
        return ""
    value = payload.get("department")
    if not isinstance(value, str):
        if strict:
            raise PrincipalAuthorityDenied()
        return ""
    candidate = value.strip()
    if not candidate:
        return ""
    try:
        return assert_safe_id(candidate, "department")
    except ValueError:
        if strict:
            raise PrincipalAuthorityDenied() from None
        return ""


def _validated_identity_aliases(payload: dict[str, Any]) -> set[str]:
    values: set[str] = set()
    for key in _IDENTITY_KEYS:
        if key not in payload:
            continue
        value = payload[key]
        if not isinstance(value, str) or not value.strip():
            raise PrincipalAuthorityDenied()
        values.add(value.strip())
    if not values:
        raise PrincipalAuthorityDenied()
    return values


def _validate_tenant(payload: dict[str, Any], tenant_id: str) -> None:
    for key in _TENANT_KEYS:
        if key not in payload:
            continue
        value = payload[key]
        if not isinstance(value, str) or value.strip() != tenant_id:
            raise PrincipalAuthorityDenied()


def _validate_eligibility(payload: dict[str, Any]) -> None:
    for key in ("active", "enabled", "eligible"):
        if key not in payload:
            continue
        value = payload[key]
        if not isinstance(value, bool) or not value:
            raise PrincipalAuthorityDenied()
    if "status" not in payload:
        return
    status_value = payload["status"]
    if not isinstance(status_value, str):
        raise PrincipalAuthorityDenied()
    normalized = status_value.strip().casefold()
    if normalized in _INELIGIBLE_STATUS_VALUES:
        raise PrincipalAuthorityDenied()
    if normalized not in _ELIGIBLE_STATUS_VALUES:
        raise PrincipalAuthorityDenied()
