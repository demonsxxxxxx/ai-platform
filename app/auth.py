from dataclasses import dataclass, field
import base64
import hashlib
from hmac import compare_digest
import hmac
import json
import time
from typing import Mapping

from fastapi import HTTPException, Request, status

from app.settings import get_settings


ORDINARY_USER_ROLE = "user"
TENANT_ADMIN_ROLE = "tenant_admin"
PLATFORM_ADMIN_ROLE = "platform_admin"
SKILL_DEVELOPER_ROLE = "skill_developer"
RUNTIME_OPERATOR_ROLE = "runtime_operator"
AUDITOR_ROLE = "auditor"
BREAK_GLASS_ADMIN_ROLE = "break_glass_admin"

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
    )


def is_ai_admin(principal: AuthPrincipal) -> bool:
    return bool(normalized_roles(principal.roles).intersection(ADMIN_ROLE_ALIASES))


def normalized_roles(roles: list[str]) -> set[str]:
    return {role.strip().lower() for role in roles if role.strip()}


def principal_to_response(principal: AuthPrincipal) -> dict[str, object]:
    return {
        "user_id": principal.user_id,
        "user_name": principal.user_id,
        "display_name": principal.display_name,
        "tenant_id": principal.tenant_id,
        "roles": principal.roles,
        "permissions": principal.permissions,
        "is_admin": is_ai_admin(principal),
        "source": principal.source,
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
    if int(payload.get("exp") or 0) < int(time.time()):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="session_expired")
    return AuthPrincipal(
        user_id=str(payload.get("user_id") or ""),
        display_name=str(payload.get("display_name") or payload.get("user_id") or ""),
        tenant_id=str(payload.get("tenant_id") or "default"),
        department_id=str(payload.get("department_id") or ""),
        roles=[str(item) for item in payload.get("roles") or []],
        permissions=[str(item) for item in payload.get("permissions") or []],
        source=str(payload.get("source") or "ai-session"),
    )


async def require_principal(request: Request) -> AuthPrincipal:
    principal = principal_from_trusted_headers(request.headers)
    if principal is None:
        session_token = request.cookies.get(get_settings().ai_session_cookie_name, "")
        if session_token:
            return verify_principal_session(session_token)
        authorization = request.headers.get("authorization", "")
        if authorization.lower().startswith("bearer "):
            return verify_principal_session(authorization[7:].strip())
    if principal is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing_authenticated_principal",
        )
    settings = get_settings()
    expected_secret = settings.trusted_principal_secret
    if settings.frontend_poc_auth_enabled and not request.headers.get("x-ai-gateway-secret", ""):
        return AuthPrincipal(
            user_id=principal.user_id,
            display_name=principal.display_name,
            tenant_id=principal.tenant_id,
            department_id=principal.department_id,
            roles=principal.roles,
            permissions=principal.permissions,
            source="frontend-poc",
        )
    if not expected_secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="trusted_principal_secret_not_configured",
        )
    provided_secret = request.headers.get("x-ai-gateway-secret", "")
    if not compare_digest(provided_secret, expected_secret):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="invalid_gateway_principal_secret",
        )
    return principal
