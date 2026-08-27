from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.mcp.errors import McpRuntimeContextError
from app.mcp.headers import normalize_static_mcp_headers
from app.mcp.identifiers import (
    assert_safe_mcp_id,
    assert_safe_mcp_principal_user_id,
)
from app.redis_client import get_redis_client
from app.settings import get_settings


MCP_PRINCIPAL_JWT_KEY_PREFIX = "ai-platform:mcp:principal-jwt:v1:"
_MCP_PRINCIPAL_JWT_AAD_PREFIX = b"ai-platform:mcp-principal-jwt:v1:"
_MCP_SERVER_CREDENTIAL_AAD_PREFIX = b"ai-platform:mcp-server-credential:v1:"


class McpPrincipal(Protocol):
    tenant_id: str
    user_id: str


@dataclass(frozen=True)
class McpContextPrincipal:
    tenant_id: str
    user_id: str

    @classmethod
    def from_principal(cls, principal: McpPrincipal) -> "McpContextPrincipal":
        return cls(
            tenant_id=assert_safe_mcp_id(principal.tenant_id, "tenant_id"),
            user_id=assert_safe_mcp_principal_user_id(principal.user_id),
        )


@dataclass(frozen=True)
class _PrincipalJwtRecord:
    tenant_id: str
    user_id: str
    jwt: str = field(repr=False)
    jwt_exp: int


class McpPrincipalJwtStore:
    """Store one encrypted company JWT per tenant-scoped user in Redis."""

    def __init__(self, *, clock: Any = time.time) -> None:
        self.clock = clock

    def _now(self) -> int:
        return int(self.clock())

    async def put(self, principal: McpPrincipal, jwt: str) -> None:
        binding = McpContextPrincipal.from_principal(principal)
        now = self._now()
        token = str(jwt or "").strip()
        payload = _jwt_payload(token, now=now)
        record = _PrincipalJwtRecord(
            tenant_id=binding.tenant_id,
            user_id=binding.user_id,
            jwt=token,
            jwt_exp=int(payload["exp"]),
        )
        try:
            client = get_redis_client()
            await client.set(
                _principal_jwt_key(binding),
                _seal_principal_jwt(record),
                ex=record.jwt_exp - now,
            )
        except McpRuntimeContextError:
            raise
        except Exception as exc:
            raise McpRuntimeContextError(
                "mcp_principal_jwt_unavailable",
                status_code=503,
            ) from exc
        finally:
            if "client" in locals():
                await client.aclose()

    async def get(self, principal: McpPrincipal) -> str:
        binding = McpContextPrincipal.from_principal(principal)
        key = _principal_jwt_key(binding)
        try:
            client = get_redis_client()
            value = await client.get(key)
        except Exception as exc:
            raise McpRuntimeContextError(
                "mcp_principal_jwt_unavailable",
                status_code=503,
            ) from exc
        finally:
            if "client" in locals():
                await client.aclose()
        if not isinstance(value, str) or not value:
            raise McpRuntimeContextError("mcp_principal_jwt_missing", status_code=401)
        record = _open_principal_jwt(value, binding)
        now = self._now()
        if record.jwt_exp <= now:
            await self.delete(binding)
            raise McpRuntimeContextError("mcp_principal_jwt_expired", status_code=401)
        try:
            payload = _jwt_payload(record.jwt, now=now)
        except McpRuntimeContextError as exc:
            raise McpRuntimeContextError(
                "mcp_principal_jwt_corrupt",
                status_code=503,
            ) from exc
        if int(payload["exp"]) != record.jwt_exp:
            raise McpRuntimeContextError("mcp_principal_jwt_corrupt", status_code=503)
        return record.jwt

    async def delete(self, principal: McpPrincipal) -> None:
        binding = McpContextPrincipal.from_principal(principal)
        try:
            client = get_redis_client()
            await client.delete(_principal_jwt_key(binding))
        except Exception:
            return
        finally:
            if "client" in locals():
                await client.aclose()


_DEFAULT_PRINCIPAL_JWT_STORE: McpPrincipalJwtStore | None = None


def get_mcp_principal_jwt_store() -> McpPrincipalJwtStore:
    global _DEFAULT_PRINCIPAL_JWT_STORE
    if _DEFAULT_PRINCIPAL_JWT_STORE is None:
        _DEFAULT_PRINCIPAL_JWT_STORE = McpPrincipalJwtStore()
    return _DEFAULT_PRINCIPAL_JWT_STORE


def _b64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode((value + padding).encode("ascii"))


def _decode_key(raw: object) -> bytes:
    if not isinstance(raw, str) or not raw:
        raise McpRuntimeContextError("mcp_context_key_invalid", status_code=503)
    try:
        decoded = (
            bytes.fromhex(raw)
            if len(raw) == 64
            else base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
        )
    except (TypeError, ValueError) as exc:
        raise McpRuntimeContextError("mcp_context_key_invalid", status_code=503) from exc
    if len(decoded) != 32:
        raise McpRuntimeContextError("mcp_context_key_invalid", status_code=503)
    return decoded


def _configured_keyring() -> tuple[str, dict[str, bytes]]:
    settings = get_settings()
    raw = str(settings.mcp_encryption_keys_json or "").strip()
    values: dict[str, object] = {}
    if raw:
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise McpRuntimeContextError(
                "mcp_context_keyring_invalid",
                status_code=503,
            ) from exc
        if not isinstance(parsed, dict):
            raise McpRuntimeContextError("mcp_context_keyring_invalid", status_code=503)
        values = parsed
    current_id = str(settings.mcp_encryption_current_key_id or "current")
    if not values:
        raise McpRuntimeContextError("mcp_context_key_not_configured", status_code=503)
    keyring = {str(key): _decode_key(value) for key, value in values.items()}
    if current_id not in keyring:
        raise McpRuntimeContextError("mcp_context_current_key_missing", status_code=503)
    return current_id, keyring


def _jwt_payload(token: str, *, now: int | None = None) -> dict[str, Any]:
    parts = str(token or "").split(".")
    if len(parts) != 3:
        raise McpRuntimeContextError("mcp_jwt_invalid", status_code=401)
    try:
        payload = json.loads(_b64url_decode(parts[1]).decode("utf-8"))
    except (UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise McpRuntimeContextError("mcp_jwt_invalid", status_code=401) from exc
    if not isinstance(payload, dict):
        raise McpRuntimeContextError("mcp_jwt_invalid", status_code=401)
    exp = payload.get("exp")
    if isinstance(exp, bool) or not isinstance(exp, int):
        raise McpRuntimeContextError("mcp_jwt_invalid", status_code=401)
    if exp <= int(time.time() if now is None else now):
        raise McpRuntimeContextError("mcp_jwt_expired_or_missing", status_code=401)
    return payload


def _principal_jwt_key(principal: McpContextPrincipal) -> str:
    identity = json.dumps(
        [principal.tenant_id, principal.user_id],
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"{MCP_PRINCIPAL_JWT_KEY_PREFIX}{hashlib.sha256(identity).hexdigest()}"


def _principal_jwt_aad(key_id: str, principal: McpContextPrincipal) -> bytes:
    identity = json.dumps(
        [key_id, principal.tenant_id, principal.user_id],
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return _MCP_PRINCIPAL_JWT_AAD_PREFIX + identity


def _seal_principal_jwt(record: _PrincipalJwtRecord) -> str:
    principal = McpContextPrincipal(record.tenant_id, record.user_id)
    current_id, keyring = _configured_keyring()
    nonce = secrets.token_bytes(12)
    plaintext = json.dumps(
        {
            "tenant_id": record.tenant_id,
            "user_id": record.user_id,
            "jwt": record.jwt,
            "jwt_exp": record.jwt_exp,
        },
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("utf-8")
    ciphertext = AESGCM(keyring[current_id]).encrypt(
        nonce,
        plaintext,
        _principal_jwt_aad(current_id, principal),
    )
    return json.dumps(
        {
            "v": 1,
            "key_id": current_id,
            "nonce": _b64url_encode(nonce),
            "ciphertext": _b64url_encode(ciphertext),
        },
        separators=(",", ":"),
    )


def _open_principal_jwt(
    value: str,
    principal: McpContextPrincipal,
) -> _PrincipalJwtRecord:
    try:
        envelope = json.loads(value)
        if not isinstance(envelope, dict) or envelope.get("v") != 1:
            raise ValueError("principal JWT envelope version is invalid")
        key_id = str(envelope["key_id"])
        _, keyring = _configured_keyring()
        plaintext = AESGCM(keyring[key_id]).decrypt(
            _b64url_decode(str(envelope["nonce"])),
            _b64url_decode(str(envelope["ciphertext"])),
            _principal_jwt_aad(key_id, principal),
        )
        payload = json.loads(plaintext.decode("utf-8"))
        record = _PrincipalJwtRecord(
            tenant_id=assert_safe_mcp_id(str(payload.get("tenant_id") or ""), "tenant_id"),
            user_id=assert_safe_mcp_principal_user_id(str(payload.get("user_id") or "")),
            jwt=str(payload.get("jwt") or ""),
            jwt_exp=int(payload.get("jwt_exp") or 0),
        )
        if (record.tenant_id, record.user_id) != (
            principal.tenant_id,
            principal.user_id,
        ):
            raise ValueError("principal JWT binding does not match")
        return record
    except McpRuntimeContextError:
        raise
    except (
        InvalidTag,
        KeyError,
        TypeError,
        ValueError,
        UnicodeError,
        json.JSONDecodeError,
    ) as exc:
        raise McpRuntimeContextError("mcp_principal_jwt_corrupt", status_code=503) from exc


def _mcp_server_credential_aad(*, tenant_id: str, server_id: str) -> bytes:
    return (
        _MCP_SERVER_CREDENTIAL_AAD_PREFIX
        + assert_safe_mcp_id(tenant_id, "tenant_id").encode("utf-8")
        + b":"
        + assert_safe_mcp_id(server_id, "mcp_server_id").encode("utf-8")
    )


def seal_mcp_server_credentials(
    *,
    tenant_id: str,
    server_id: str,
    endpoint: str | None,
    static_headers: Mapping[str, str] | None,
) -> str:
    """Encrypt runtime-only MCP connection material with tenant/server AAD."""

    current_id, keyring = _configured_keyring()
    nonce = secrets.token_bytes(12)
    plaintext = json.dumps(
        {
            "endpoint": str(endpoint or ""),
            "headers": normalize_static_mcp_headers(static_headers),
        },
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("utf-8")
    ciphertext = AESGCM(keyring[current_id]).encrypt(
        nonce,
        plaintext,
        _mcp_server_credential_aad(tenant_id=tenant_id, server_id=server_id),
    )
    return json.dumps(
        {
            "v": 1,
            "key_id": current_id,
            "nonce": _b64url_encode(nonce),
            "ciphertext": _b64url_encode(ciphertext),
        },
        separators=(",", ":"),
    )


def open_mcp_server_credentials(
    *,
    tenant_id: str,
    server_id: str,
    envelope: str,
) -> tuple[str, dict[str, str]]:
    """Decrypt one server credential envelope for discovery or execution."""

    try:
        raw_envelope = json.loads(str(envelope or ""))
        key_id = str(raw_envelope["key_id"])
        _, keyring = _configured_keyring()
        plaintext = AESGCM(keyring[key_id]).decrypt(
            _b64url_decode(str(raw_envelope["nonce"])),
            _b64url_decode(str(raw_envelope["ciphertext"])),
            _mcp_server_credential_aad(tenant_id=tenant_id, server_id=server_id),
        )
        payload = json.loads(plaintext.decode("utf-8"))
        endpoint = payload.get("endpoint")
        headers = payload.get("headers")
        if not isinstance(endpoint, str) or not isinstance(headers, dict):
            raise ValueError("credential payload shape is invalid")
        return endpoint, normalize_static_mcp_headers(headers)
    except McpRuntimeContextError:
        raise
    except (
        InvalidTag,
        KeyError,
        TypeError,
        ValueError,
        UnicodeError,
        json.JSONDecodeError,
    ) as exc:
        raise McpRuntimeContextError(
            "mcp_server_credentials_invalid",
            status_code=503,
        ) from exc
