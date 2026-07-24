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
_WORK_ID_KEYS = ("workId", "workid")
_OPTIONAL_IDENTITY_ALIAS_KEYS = ("userName", "username", "user_name")
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
    """Resolve a company-login principal from the current trusted user record."""

    effective_settings = settings or get_settings()
    adapter = user_info_adapter or _production_adapter(effective_settings)
    try:
        raw_user_info = await adapter(work_id)
    except Exception:
        raise PrincipalAuthorityDenied() from None
    # The submitted login name remains an input compatibility field, never an
    # independent identity or configured-admin authority source.
    roles, department_id = _normalize_company_record(
        expected_work_id=work_id,
        tenant_id=str(effective_settings.default_tenant_id),
        raw_user_info=raw_user_info,
        settings=effective_settings,
    )
    return AuthPrincipal(
        user_id=work_id,
        display_name=display_name,
        tenant_id=effective_settings.default_tenant_id,
        department_id=department_id,
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
    roles, department_id = _normalize_company_record(
        expected_work_id=user_id,
        tenant_id=tenant_id,
        raw_user_info=raw_user_info,
        settings=effective_settings,
    )
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


def _normalize_company_record(
    *,
    expected_work_id: str,
    tenant_id: str,
    raw_user_info: object,
    settings: Any,
) -> tuple[list[str], str]:
    """Validate one company record and return its bounded product authority."""

    if not isinstance(raw_user_info, dict):
        raise PrincipalAuthorityDenied()

    identity_aliases = _validated_identity_aliases(raw_user_info, expected_work_id=expected_work_id)
    _validate_tenant(raw_user_info, tenant_id)
    upstream_roles = _roles_from_user_info(raw_user_info, strict=True)
    _validate_eligibility(raw_user_info, legacy_roles=upstream_roles)
    roles = _effective_roles(
        expected_work_id,
        upstream_roles,
        settings=settings,
        identity_aliases=identity_aliases,
    )
    return roles, _department_from_user_info(raw_user_info)


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
    resolved_roles: list[str] = []
    semantic_roles: frozenset[str] | None = None
    for key in (*_ROLE_LIST_KEYS, *_ROLE_KEYS):
        if key not in payload:
            continue
        recognized = True
        normalized_roles = normalize_roles(_as_list(payload[key], strict=strict))
        current_semantics = frozenset(normalized_roles)
        if semantic_roles is None:
            semantic_roles = current_semantics
            resolved_roles = normalized_roles
            continue
        # Every present alias is an authority assertion. Empty, reordered, or
        # differently cased forms are safe only when their normalized sets agree.
        if current_semantics != semantic_roles:
            raise PrincipalAuthorityDenied()
    if strict and not recognized:
        raise PrincipalAuthorityDenied()
    return resolved_roles


def _configured_admin_ids(settings: Any) -> set[str]:
    raw_value = str(getattr(settings, "ai_admin_work_ids", "") or "")
    return {item.strip().casefold() for item in raw_value.split(",") if item.strip()}


def _effective_roles(
    work_id: str,
    upstream_role_values: list[str],
    *,
    settings: Any,
    identity_aliases: set[str],
) -> list[str]:
    upstream_roles = set(normalize_roles(upstream_role_values))
    configured_admin_ids = _configured_admin_ids(settings)
    candidate_admin_ids = {
        value.strip().casefold()
        for value in (work_id, *identity_aliases)
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


def _department_from_user_info(payload: dict[str, Any]) -> str:
    if "department" not in payload or payload.get("department") is None:
        return ""
    value = payload.get("department")
    if not isinstance(value, str):
        return ""
    candidate = value.strip()
    if not candidate:
        return ""
    try:
        return assert_safe_id(candidate, "department")
    except ValueError:
        # Empty is intentionally unscoped: it cannot match a non-empty
        # department allowlist and never invents authority from display data.
        return ""


def _validated_identity_aliases(payload: dict[str, Any], *, expected_work_id: str) -> set[str]:
    work_ids: set[str] = set()
    for key in _WORK_ID_KEYS:
        if key not in payload:
            continue
        value = payload[key]
        if not isinstance(value, str) or not value.strip():
            raise PrincipalAuthorityDenied()
        work_ids.add(value.strip())
    if work_ids != {expected_work_id}:
        raise PrincipalAuthorityDenied()

    values = set(work_ids)
    for key in _OPTIONAL_IDENTITY_ALIAS_KEYS:
        if key not in payload:
            continue
        value = payload[key]
        if value is None:
            continue
        if not isinstance(value, str):
            raise PrincipalAuthorityDenied()
        candidate = value.strip()
        if not candidate:
            continue
        if candidate != expected_work_id:
            raise PrincipalAuthorityDenied()
        values.add(candidate)
    return values


def _validate_tenant(payload: dict[str, Any], tenant_id: str) -> None:
    for key in _TENANT_KEYS:
        if key not in payload:
            continue
        value = payload[key]
        if not isinstance(value, str) or value.strip() != tenant_id:
            raise PrincipalAuthorityDenied()


def _validate_eligibility(payload: dict[str, Any], *, legacy_roles: list[str]) -> None:
    has_eligibility_signal = False
    for key in ("active", "enabled", "eligible"):
        if key not in payload:
            continue
        has_eligibility_signal = True
        value = payload[key]
        if not isinstance(value, bool) or not value:
            raise PrincipalAuthorityDenied()
    if "status" in payload:
        has_eligibility_signal = True
        status_value = payload["status"]
        if not isinstance(status_value, str):
            raise PrincipalAuthorityDenied()
        normalized = status_value.strip().casefold()
        if normalized in _INELIGIBLE_STATUS_VALUES:
            raise PrincipalAuthorityDenied()
        if normalized not in _ELIGIBLE_STATUS_VALUES:
            raise PrincipalAuthorityDenied()
    if not has_eligibility_signal and not legacy_roles:
        raise PrincipalAuthorityDenied()
    # The trusted legacy company endpoint has no eligibility fields. Absence is
    # compatible only after exact work-id validation and strict non-empty roles;
    # every explicit negative or malformed signal above remains fail closed.
