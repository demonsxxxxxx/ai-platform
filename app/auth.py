import asyncio
from dataclasses import dataclass, field
import base64
import hashlib
from hmac import compare_digest
import hmac
import json
import time
from datetime import datetime, timezone
from typing import Iterable, Mapping

import httpx
from fastapi import HTTPException, Request, status

from app.auth_sessions import (
    AuthContextError, begin_auth_operation_for_cookie, browser_auth_grant,
    commit_auth_operation, principal_for_context, principal_for_cookie, principal_snapshot,
)
from app.settings import get_settings


ORDINARY_USER_ROLE = "user"
TENANT_ADMIN_ROLE = "tenant_admin"
PLATFORM_ADMIN_ROLE = "platform_admin"
SKILL_DEVELOPER_ROLE = "skill_developer"
RUNTIME_OPERATOR_ROLE = "runtime_operator"
AUDITOR_ROLE = "auditor"
BREAK_GLASS_ADMIN_ROLE = "break_glass_admin"
COMPANY_AUTHZ_POLICY_VERSION = 1
AUTH_REJECTED_HEADER = "X-Auth-Rejected-Before-Execution"
FORCE_RELOGIN_HEADER = "X-Force-Relogin"

ADMIN_ROLE_ALIASES = {"admin", "developer", PLATFORM_ADMIN_ROLE, BREAK_GLASS_ADMIN_ROLE}
PLATFORM_ROLE_TAXONOMY = {
    ORDINARY_USER_ROLE,
    TENANT_ADMIN_ROLE,
    PLATFORM_ADMIN_ROLE,
    SKILL_DEVELOPER_ROLE,
    RUNTIME_OPERATOR_ROLE,
    AUDITOR_ROLE,
    BREAK_GLASS_ADMIN_ROLE,
    "admin",
    "developer",
}


@dataclass(frozen=True)
class AuthPrincipal:
    user_id: str
    display_name: str
    tenant_id: str
    department_id: str = ""
    roles: list[str] = field(default_factory=list)
    permissions: list[str] = field(default_factory=list)
    source: str = "trusted-header"
    authz_policy_version: int = COMPANY_AUTHZ_POLICY_VERSION
    authority_source: str = ""
    authority_checked_at: str = ""
    company_jwt_expires_at: int | None = None
    company_username: str = ""
    company_department: str = ""
    company_role: str = ""


def authority_checked_at_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _split_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def principal_from_trusted_headers(headers: Mapping[str, str]) -> AuthPrincipal | None:
    lowered = {key.lower(): value for key, value in headers.items()}
    user_id = lowered.get("x-ai-user-id", "").strip()
    if not user_id:
        return None
    return AuthPrincipal(
        user_id=user_id,
        display_name=lowered.get("x-ai-user-name", user_id).strip() or user_id,
        tenant_id=lowered.get("x-ai-tenant-id", "default").strip() or "default",
        department_id=lowered.get("x-ai-department-id", "").strip(),
        roles=_split_csv(lowered.get("x-ai-roles")),
        permissions=_split_csv(lowered.get("x-ai-permissions")),
        authority_source="trusted-gateway",
        authority_checked_at=authority_checked_at_now(),
    )


def is_ai_admin(principal: AuthPrincipal) -> bool:
    return bool(set(normalize_roles(principal.roles)).intersection(ADMIN_ROLE_ALIASES))


def normalized_roles(roles: list[str]) -> set[str]:
    return set(normalize_roles(roles))


def normalize_roles(roles: Iterable[object]) -> list[str]:
    """Normalize role identity without creating punctuation aliases."""

    normalized: list[str] = []
    for role in roles:
        candidate = str(role).strip().casefold()
        if candidate and candidate not in normalized:
            normalized.append(candidate)
    return normalized


def principal_to_response(principal: AuthPrincipal) -> dict[str, object]:
    return {
        "user_id": principal.user_id,
        "user_name": principal.user_id,
        "display_name": principal.display_name,
        "tenant_id": principal.tenant_id,
        "department_id": principal.department_id,
        "roles": principal.roles,
        "permissions": principal.permissions,
        "is_admin": is_ai_admin(principal),
        "source": principal.source,
        "authz_policy_version": principal.authz_policy_version,
        "authority_source": principal.authority_source or principal.source,
        "authority_checked_at": principal.authority_checked_at,
        "company_refresh_required": principal.source == "company-login" and (
            principal.company_jwt_expires_at is None or not all((
                principal.company_username, principal.company_department, principal.company_role,
            ))
        ),
    }


def _session_secret() -> str:
    secret = get_settings().ai_session_secret.strip()
    if not secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="ai_session_secret_not_configured",
        )
    return secret


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode((value + padding).encode("ascii"))


def sign_principal_session(principal: AuthPrincipal) -> str:
    settings = get_settings()
    now = int(time.time())
    payload = {
        "user_id": principal.user_id,
        "display_name": principal.display_name,
        "tenant_id": principal.tenant_id,
        "department_id": principal.department_id,
        "roles": principal.roles,
        "permissions": principal.permissions,
        "source": principal.source,
        "authz_policy_version": principal.authz_policy_version,
        "authority_source": principal.authority_source,
        "authority_checked_at": principal.authority_checked_at,
        "company_jwt_expires_at": principal.company_jwt_expires_at,
        "company_username": principal.company_username,
        "company_department": principal.company_department,
        "company_role": principal.company_role,
        "iat": now,
        "exp": now + int(settings.ai_session_max_age_seconds),
    }
    header_part = _b64url_encode(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode("utf-8"))
    payload_part = _b64url_encode(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    signing_input = f"{header_part}.{payload_part}"
    signature = hmac.new(_session_secret().encode("utf-8"), signing_input.encode("ascii"), hashlib.sha256).digest()
    return f"{signing_input}.{_b64url_encode(signature)}"


def verify_principal_session(token: str) -> AuthPrincipal:
    parts = token.split(".")
    if len(parts) == 3:
        header_part, payload_part, signature_part = parts
        signing_input = f"{header_part}.{payload_part}"
    elif len(parts) == 2:
        payload_part, signature_part = parts
        signing_input = payload_part
    else:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_session")
    if not signing_input.isascii():
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_session")
    expected = hmac.new(_session_secret().encode("utf-8"), signing_input.encode("ascii"), hashlib.sha256).digest()
    try:
        provided = _b64url_decode(signature_part)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_session") from exc
    if not compare_digest(provided, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_session")
    try:
        payload = json.loads(_b64url_decode(payload_part).decode("utf-8"))
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_session") from exc
    if int(payload.get("exp") or 0) <= int(time.time()):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="session_expired",
            headers={FORCE_RELOGIN_HEADER: "true"},
        )
    source = str(payload.get("source") or "ai-session")
    raw_company_jwt_expires_at = payload.get("company_jwt_expires_at")
    if raw_company_jwt_expires_at is not None and (
        isinstance(raw_company_jwt_expires_at, bool)
        or not isinstance(raw_company_jwt_expires_at, int)
        or raw_company_jwt_expires_at <= 0
    ):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_session")
    if source == "company-login" and payload.get("authz_policy_version") != COMPANY_AUTHZ_POLICY_VERSION:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="stale_company_session",
            headers={FORCE_RELOGIN_HEADER: "true"},
        )
    return AuthPrincipal(
        user_id=str(payload.get("user_id") or ""),
        display_name=str(payload.get("display_name") or payload.get("user_id") or ""),
        tenant_id=str(payload.get("tenant_id") or "default"),
        department_id=str(payload.get("department_id") or ""),
        roles=[str(item) for item in payload.get("roles") or []],
        permissions=[str(item) for item in payload.get("permissions") or []],
        source=source,
        authz_policy_version=int(payload.get("authz_policy_version") or 0),
        authority_source=str(payload.get("authority_source") or ""),
        authority_checked_at=str(payload.get("authority_checked_at") or ""),
        company_jwt_expires_at=(
            int(raw_company_jwt_expires_at)
            if raw_company_jwt_expires_at is not None
            else None
        ),
        **_company_snapshot_fields(payload),
    )


def _company_snapshot_fields(snapshot: Mapping[str, object]) -> dict[str, str]:
    """Keep original company claims separate from the platform role projection."""
    fields = ("company_username", "company_department", "company_role")
    if any(not isinstance(snapshot.get(name, ""), str) for name in fields):
        raise HTTPException(status_code=401, detail="invalid_session")
    return {name: str(snapshot.get(name, "")) for name in fields}

def _enforce_deployment_scope(principal: AuthPrincipal, settings: object) -> AuthPrincipal:
    if getattr(settings, "deployment_environment", "development") != "production":
        return principal
    configured_scope = getattr(settings, "default_tenant_id", None)
    if configured_scope is not None and principal.tenant_id != str(configured_scope):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="tenant_scope_not_allowed")
    return principal


def _enforce_company_authority_freshness(
    principal: AuthPrincipal,
    settings: object,
) -> AuthPrincipal:
    """Fail closed when a company-derived authorization snapshot is stale."""

    if principal.source != "company-login":
        return principal
    relogin_headers = {FORCE_RELOGIN_HEADER: "true"}
    if principal.authz_policy_version != COMPANY_AUTHZ_POLICY_VERSION:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="stale_company_session",
            headers=relogin_headers,
        )
    if not principal.authority_source.strip() or not principal.authority_checked_at.strip():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="stale_company_authority",
            headers=relogin_headers,
        )
    if principal.company_jwt_expires_at is not None and principal.company_jwt_expires_at <= int(time.time()):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="company_login_expired",
            headers=relogin_headers,
        )
    try:
        checked_at = datetime.fromisoformat(principal.authority_checked_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="stale_company_authority",
            headers=relogin_headers,
        ) from exc
    if checked_at.tzinfo is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="stale_company_authority",
            headers=relogin_headers,
        )
    age_seconds = (datetime.now(timezone.utc) - checked_at.astimezone(timezone.utc)).total_seconds()
    max_age_seconds = int(getattr(settings, "company_authority_freshness_seconds", 24 * 60 * 60))
    if age_seconds < -60 or age_seconds > max_age_seconds:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="stale_company_authority",
            headers=relogin_headers,
        )
    return principal


async def refresh_company_session(
    cookie_value: str, *, expected_user: tuple[str, str] | None = None,
    expected_subject_epoch: int | None = None,
) -> tuple[AuthPrincipal, bool]:
    """Renew a company credential under a live, absolute browser grant and CAS."""
    from app.mcp.api import McpRuntimeContextError, get_mcp_principal_jwt_store
    from app.principal_authority import (
        CompanyLoginJwtUnavailable, PrincipalAuthorityDenied, issue_refreshed_company_jwt,
    )

    operation = None
    committed = False
    try:
        initial = await browser_auth_grant(cookie_value)
        initial_principal = initial["principal"]
        subject = (initial_principal["tenant_id"], initial_principal["user_id"])
        if expected_user is not None and subject != expected_user:
            raise AuthContextError("auth_operation_superseded", 409)
        if expected_subject_epoch is not None and initial["subject_epoch"] != expected_subject_epoch:
            raise AuthContextError("auth_operation_superseded", 409)
        wait_deadline = time.monotonic() + 5
        while True:
            try:
                operation = await begin_auth_operation_for_cookie(cookie_value, "refresh")
                break
            except AuthContextError as exc:
                if exc.code != "auth_refresh_in_progress" or time.monotonic() >= wait_deadline:
                    raise
                await asyncio.sleep(0.05)
                current = await browser_auth_grant(cookie_value)
                snapshot = current["principal"]
                if (snapshot["tenant_id"], snapshot["user_id"]) != subject:
                    raise AuthContextError("auth_operation_superseded", 409)
                if (snapshot.get("company_jwt_expires_at") or 0) > max(
                    int(time.time()), initial_principal.get("company_jwt_expires_at") or 0,
                ):
                    return AuthPrincipal(**snapshot), current["subject_epoch"] != initial["subject_epoch"]
        previous = operation.principal
        if (previous is None or (previous["tenant_id"], previous["user_id"]) != subject
            or (expected_subject_epoch is not None and operation.subject_epoch != expected_subject_epoch)):
            raise AuthContextError("auth_operation_superseded", 409)
        if (previous["source"] != "company-login"
            or previous["authz_policy_version"] != COMPANY_AUTHZ_POLICY_VERSION
            or type(operation.expires_at) is not int or operation.expires_at <= int(time.time())
            or type(operation.subject_epoch) is not int):
            raise PrincipalAuthorityDenied()
        principal = AuthPrincipal(**previous)
        refreshed, token = await issue_refreshed_company_jwt(principal, expires_at=operation.expires_at)
        snapshot = principal_snapshot(refreshed)
        authorization_fields = (
            "department_id", "authz_policy_version", "company_role", "company_username",
            "company_department", "roles", "permissions",
        )
        changed = any(previous.get(name, "") != snapshot.get(name, "") for name in authorization_fields)
        credential = get_mcp_principal_jwt_store().prepare(
            refreshed, token, refresh_cookie=cookie_value,
            subject_epoch=operation.subject_epoch + int(changed), refresh_expires_at=operation.expires_at,
        )
        result = await commit_auth_operation(
            operation, snapshot, credential=credential, credential_expires_at=operation.expires_at,
        )
        if result != "committed":
            raise AuthContextError("auth_operation_superseded", 409)
        committed = True
        return refreshed, changed
    except PrincipalAuthorityDenied as exc:
        if operation is not None:
            try:
                revoked = await commit_auth_operation(operation, None)
            except AuthContextError as fenced:
                raise HTTPException(status_code=fenced.status_code, detail=fenced.code) from fenced
            if revoked != "committed":
                raise HTTPException(status_code=409, detail="auth_operation_superseded")
            committed = True
        raise HTTPException(
            status_code=401, detail="auth_refresh_authority_denied",
            headers={FORCE_RELOGIN_HEADER: "true"},
        ) from exc
    except AuthContextError as exc:
        status_code = 401 if exc.code in {"auth_context_stale", "auth_context_missing"} else exc.status_code
        raise HTTPException(status_code=status_code, detail=exc.code) from exc
    except (CompanyLoginJwtUnavailable, McpRuntimeContextError) as exc:
        raise HTTPException(status_code=503, detail="auth_refresh_unavailable") from exc
    except (OSError, TimeoutError, ValueError, httpx.HTTPError) as exc:
        raise HTTPException(status_code=503, detail="auth_refresh_unavailable") from exc
    finally:
        if operation is not None and not committed:
            # Only this lease may be released; a newer login/logout is never rolled back.
            try:
                await commit_auth_operation(operation, operation.principal)
            except Exception:
                pass


async def require_principal(request: Request) -> AuthPrincipal:
    """Mark authentication rejection before endpoint business execution."""
    try:
        return await _require_principal(request)
    except HTTPException as exc:
        if exc.status_code == 401:
            exc.headers = {**(exc.headers or {}), AUTH_REJECTED_HEADER: "true"}
        raise


async def _require_principal(request: Request) -> AuthPrincipal:
    settings = get_settings()
    principal = principal_from_trusted_headers(request.headers)
    if principal is None:
        authorization = request.headers.get("authorization", "")
        if authorization.lower().startswith("bearer "):
            bearer_principal = verify_principal_session(authorization[7:].strip())
            return _enforce_deployment_scope(
                _enforce_company_authority_freshness(bearer_principal, settings),
                settings,
            )
        context_handle = request.cookies.get(
            getattr(settings, "auth_context_cookie_name", "ai_platform_auth_context"),
            "",
        )
        if context_handle:
            try:
                # Keep V1 trusted-context behavior intact. V2 parses and
                # snapshots cookie, authority, and context in one Lua read.
                snapshot = await (
                    principal_for_cookie(context_handle, settings)
                    if context_handle.startswith("v2.")
                    else principal_for_context(context_handle, settings)
                )
            except AuthContextError as exc:
                raise HTTPException(status_code=exc.status_code, detail=exc.code) from exc
            if snapshot is not None:
                browser_principal = AuthPrincipal(
                    user_id=str(snapshot["user_id"]),
                    display_name=str(snapshot["display_name"]),
                    tenant_id=str(snapshot["tenant_id"]),
                    department_id=str(snapshot["department_id"]),
                    roles=[str(item) for item in snapshot["roles"]],
                    permissions=[str(item) for item in snapshot["permissions"]],
                    source=str(snapshot["source"]),
                    authz_policy_version=int(snapshot["authz_policy_version"]),
                    authority_source=str(snapshot["authority_source"]),
                    authority_checked_at=str(snapshot["authority_checked_at"]),
                    company_jwt_expires_at=(
                        int(snapshot["company_jwt_expires_at"])
                        if snapshot.get("company_jwt_expires_at") is not None
                        else None
                    ),
                    **_company_snapshot_fields(snapshot),
                )
                return _enforce_deployment_scope(
                    _enforce_company_authority_freshness(browser_principal, settings),
                    settings,
                )
    if principal is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing_authenticated_principal",
        )
    expected_secret = settings.trusted_principal_secret
    if settings.frontend_poc_auth_enabled and not request.headers.get("x-ai-gateway-secret", ""):
        return _enforce_deployment_scope(AuthPrincipal(
            user_id=principal.user_id,
            display_name=principal.display_name,
            tenant_id=principal.tenant_id,
            department_id=principal.department_id,
            roles=principal.roles,
            permissions=principal.permissions,
            source="frontend-poc",
            authz_policy_version=principal.authz_policy_version,
            authority_source="frontend-poc",
            authority_checked_at=principal.authority_checked_at,
        ), settings)
    if not expected_secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="trusted_principal_secret_not_configured",
        )
    provided_secret = request.headers.get("x-ai-gateway-secret", "")
    if not provided_secret.isascii() or not compare_digest(
        provided_secret.encode("ascii"), expected_secret.encode("utf-8")
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="invalid_gateway_principal_secret",
        )
    return _enforce_deployment_scope(principal, settings)
