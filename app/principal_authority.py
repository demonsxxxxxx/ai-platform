from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from dataclasses import replace
import time

import httpx

from app.auth import (
    COMPANY_AUTHZ_POLICY_VERSION,
    AuthPrincipal,
    authority_checked_at_now,
    normalize_roles,
)
from app.bootstrap.department_authority import resolve_department_authority
from app.identity.api import DepartmentAuthority
from app.platform.jwt_validation import JwtValidationError, decode_hs256_jwt, encode_hs256_jwt
from app.settings import get_settings
from app.kernel.identifiers import assert_safe_principal_user_id


CURRENT_PRINCIPAL_DENIAL_REASON = "current_principal_authority_denied"
COMPANY_JWT_MAX_AGE_SECONDS = 8 * 60 * 60

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
    "settings:manage",
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
DepartmentAuthorityAdapter = Callable[[str], Awaitable[DepartmentAuthority]]


async def bind_department_authority(
    principal: AuthPrincipal, *, settings: Any | None = None,
    department_adapter: DepartmentAuthorityAdapter | None = None, fresh: bool = False,
) -> AuthPrincipal:
    try:
        if department_adapter:
            authority = await department_adapter(principal.user_id)
        else:
            authority = await resolve_department_authority(principal.user_id, settings=settings, fresh=fresh)
    except (httpx.HTTPError, OSError, TimeoutError, ValueError, RuntimeError):
        authority = DepartmentAuthority()
    return replace(principal, **authority.principal_fields())

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


class CompanyLoginJwtUnavailable(Exception):
    """Report unavailable company authority or signing configuration without accepting a token."""


_REQUIRED_COMPANY_JWT_CLAIMS = (
    "workid",
    "username",
    "cnname",
    "depart",
    "role",
    "iat",
    "nbf",
    "exp",
    "iss",
    "aud",
)


def read_company_jwt_expiry(
    company_jwt: str,
    *,
    settings: Any | None = None,
) -> int | None:
    """Read a verified company token's expiry for the platform session deadline."""

    effective_settings = settings or get_settings()
    secret = str(getattr(effective_settings, "company_login_jwt_secret", "") or "")
    issuer = str(getattr(effective_settings, "company_login_jwt_issuer", "") or "").strip()
    audience = str(getattr(effective_settings, "company_login_jwt_audience", "") or "").strip()
    if len(secret.encode("utf-8")) < 32 or not issuer or not audience:
        return None
    try:
        claims = decode_hs256_jwt(
            company_jwt,
            secret=secret,
            issuer=issuer,
            audience=audience,
            required_claims=_REQUIRED_COMPANY_JWT_CLAIMS,
            verify_exp=False,
        )
    except JwtValidationError:
        return None
    expiry = claims.get("exp")
    if isinstance(expiry, bool) or not isinstance(expiry, int) or expiry <= 0:
        return None
    return expiry


def resolve_company_login_jwt(
    company_jwt: str,
    *,
    settings: Any | None = None,
) -> AuthPrincipal:
    """Verify one company JWT and project its signed identity into platform authority."""

    effective_settings = settings or get_settings()
    secret = str(getattr(effective_settings, "company_login_jwt_secret", "") or "")
    issuer = str(getattr(effective_settings, "company_login_jwt_issuer", "") or "").strip()
    audience = str(getattr(effective_settings, "company_login_jwt_audience", "") or "").strip()
    if len(secret.encode("utf-8")) < 32 or not issuer or not audience:
        raise CompanyLoginJwtUnavailable()

    try:
        claims = decode_hs256_jwt(
            company_jwt,
            secret=secret,
            issuer=issuer,
            audience=audience,
            required_claims=_REQUIRED_COMPANY_JWT_CLAIMS,
        )
    except JwtValidationError:
        raise PrincipalAuthorityDenied() from None

    work_id = _required_company_claim(claims, "workid", 128)
    username = _required_company_claim(claims, "username", 128)
    display_name = _required_company_claim(claims, "cnname", 128)
    department = _required_company_claim(claims, "depart", 160)
    role = _required_company_claim(claims, "role", 512)
    company_jwt_expires_at = _required_epoch_claim(claims, "exp")
    try:
        assert_safe_principal_user_id(work_id)
        roles = _normalize_company_record(
            expected_work_id=work_id,
            tenant_id=str(effective_settings.default_tenant_id),
            raw_user_info={
                "workid": work_id,
                "username": username,
                "role": role,
            },
            settings=effective_settings,
        )
    except (PrincipalAuthorityDenied, ValueError):
        raise PrincipalAuthorityDenied() from None

    return AuthPrincipal(
        user_id=work_id,
        display_name=display_name,
        tenant_id=effective_settings.default_tenant_id,
        roles=roles,
        permissions=_effective_permissions(roles),
        source="company-login",
        authz_policy_version=COMPANY_AUTHZ_POLICY_VERSION,
        authority_source="company-login-jwt",
        authority_checked_at=authority_checked_at_now(),
        company_jwt_expires_at=company_jwt_expires_at,
        company_username=username,
        company_department=department,
        company_role=role,
    )


def _required_epoch_claim(claims: dict[str, Any], name: str) -> int:
    value = claims.get(name)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise PrincipalAuthorityDenied()
    return value


def _required_company_claim(claims: dict[str, Any], name: str, max_length: int) -> str:
    value = claims.get(name)
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > max_length
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise PrincipalAuthorityDenied()
    return value


async def fetch_company_user_info(work_id: str, *, settings: Any | None = None) -> object:
    """Fetch one company user-info document without retries or response coercion."""

    effective_settings = settings or get_settings()
    base_url = effective_settings.existing_user_info_base_url.rstrip("/")
    async with httpx.AsyncClient(
        timeout=effective_settings.existing_auth_timeout_seconds,
        trust_env=False,
    ) as client:
        response = await client.get(f"{base_url}/api/userManage/{work_id}/info")
        response.raise_for_status()
        return response.json()


async def resolve_login_principal(
    *,
    work_id: str,
    login_name: str,
    display_name: str,
    user_info_adapter: UserInfoAdapter | None = None,
    department_adapter: DepartmentAuthorityAdapter | None = None,
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
    roles = _normalize_company_record(
        expected_work_id=work_id,
        tenant_id=str(effective_settings.default_tenant_id),
        raw_user_info=raw_user_info,
        settings=effective_settings,
    )
    principal = AuthPrincipal(
        user_id=work_id,
        display_name=display_name,
        tenant_id=effective_settings.default_tenant_id,
        roles=roles,
        permissions=_effective_permissions(roles),
        source="company-login",
        authz_policy_version=COMPANY_AUTHZ_POLICY_VERSION,
        authority_source="company-user-info",
        authority_checked_at=authority_checked_at_now(),
        **_company_fields_from_user_info(raw_user_info, work_id),
    )
    return await bind_department_authority(
        principal, settings=effective_settings, department_adapter=department_adapter, fresh=True,
    )


async def resolve_current_principal(
    *,
    user_id: str,
    tenant_id: str,
    user_info_adapter: UserInfoAdapter | None = None,
    department_adapter: DepartmentAuthorityAdapter | None = None,
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
    roles = _normalize_company_record(
        expected_work_id=user_id,
        tenant_id=tenant_id,
        raw_user_info=raw_user_info,
        settings=effective_settings,
    )
    principal = AuthPrincipal(
        user_id=user_id,
        display_name=user_id,
        tenant_id=tenant_id,
        roles=roles,
        permissions=_effective_permissions(roles),
        source="company-user-info-current",
        authz_policy_version=COMPANY_AUTHZ_POLICY_VERSION,
        authority_source="company-user-info",
        authority_checked_at=authority_checked_at_now(),
    )
    return await bind_department_authority(
        principal, settings=effective_settings, department_adapter=department_adapter, fresh=True,
    )


def _company_fields_from_user_info(raw: dict[str, Any], work_id: str) -> dict[str, str]:
    """Retain trusted company claims without replacing platform roles."""
    username = next((raw[name] for name in _OPTIONAL_IDENTITY_ALIAS_KEYS
                     if isinstance(raw.get(name), str) and raw[name].strip()), work_id)
    department = raw.get("department") or raw.get("depart") or ""
    role = next((raw[name] for name in _ROLE_KEYS
                 if isinstance(raw.get(name), str) and raw[name].strip()), None)
    if role is None:
        role = ",".join(next((_as_list(raw[name], strict=True)
                              for name in _ROLE_LIST_KEYS if name in raw), []))
    return {
        "company_username": str(username).strip(),
        "company_department": department.strip() if isinstance(department, str) else "",
        "company_role": role.strip(),
    }


async def issue_refreshed_company_jwt(
    principal: AuthPrincipal,
    *,
    expires_at: int,
    user_info_adapter: UserInfoAdapter | None = None,
    department_adapter: DepartmentAuthorityAdapter | None = None,
    settings: Any | None = None,
) -> tuple[AuthPrincipal, str]:
    """Recheck current company authority and issue an independently bounded JWT."""
    effective_settings = settings or get_settings()
    if principal.tenant_id != str(effective_settings.default_tenant_id):
        raise PrincipalAuthorityDenied()
    adapter = user_info_adapter or _production_adapter(effective_settings)
    # Transport errors remain service failures, not evidence of account revocation.
    try:
        raw = await adapter(principal.user_id)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code in {401, 403, 404}:
            raise PrincipalAuthorityDenied() from None
        raise CompanyLoginJwtUnavailable() from exc
    except (httpx.HTTPError, OSError, TimeoutError, ValueError) as exc:
        raise CompanyLoginJwtUnavailable() from exc
    roles = _normalize_company_record(
        expected_work_id=principal.user_id, tenant_id=principal.tenant_id,
        raw_user_info=raw, settings=effective_settings,
    )
    fields = _company_fields_from_user_info(raw, principal.user_id)
    display_name = raw.get("cnname") or raw.get("cnName") or principal.display_name
    now = int(time.time())
    deadline = min(expires_at, now + COMPANY_JWT_MAX_AGE_SECONDS)
    if deadline <= now:
        raise PrincipalAuthorityDenied()
    claims = {
        "workid": principal.user_id,
        "username": fields["company_username"],
        "cnname": display_name,
        "depart": fields["company_department"],
        "role": fields["company_role"],
        "iat": now, "nbf": now, "exp": deadline,
        "iss": effective_settings.company_login_jwt_issuer,
        "aud": effective_settings.company_login_jwt_audience,
    }
    for name, limit in (("workid", 128), ("username", 128), ("cnname", 128),
                        ("depart", 160), ("role", 512)):
        _required_company_claim(claims, name, limit)
    if len(effective_settings.company_login_jwt_secret.encode("utf-8")) < 32:
        raise CompanyLoginJwtUnavailable()
    if not claims["iss"] or not claims["aud"]:
        raise CompanyLoginJwtUnavailable()
    try:
        token = encode_hs256_jwt(claims, secret=effective_settings.company_login_jwt_secret)
    except JwtValidationError as exc:
        raise CompanyLoginJwtUnavailable() from exc
    refreshed = replace(
        principal, display_name=display_name,
        roles=roles, permissions=_effective_permissions(roles),
        authority_source="company-user-info", authority_checked_at=authority_checked_at_now(),
        company_jwt_expires_at=deadline, **fields,
    )
    refreshed = await bind_department_authority(
        refreshed, settings=effective_settings, department_adapter=department_adapter, fresh=True,
    )
    return refreshed, token


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
) -> list[str]:
    """Validate company eligibility and roles without deriving department authority."""

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
    return roles


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
    username_aliases: set[str] = set()
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
        username_aliases.add(candidate)
    if len(username_aliases) > 1:
        raise PrincipalAuthorityDenied()
    values.update(username_aliases)
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
