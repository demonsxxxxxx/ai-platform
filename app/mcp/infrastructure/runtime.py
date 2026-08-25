from __future__ import annotations

import asyncio
import base64
import hashlib
import ipaddress
import json
import secrets
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Awaitable, Callable, Mapping, MutableMapping, Protocol
from urllib.parse import urlsplit, urlunsplit

import httpx
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.mcp.domain.errors import (
    McpRelayError,
    McpRuntimeContextError,
    McpToolSelectionRequired,
)
from app.mcp.domain.headers import (
    MCP_JWT_AUTHORIZATION_HEADER,
    normalize_static_mcp_headers,
)
from app.mcp.domain.identifiers import (
    assert_safe_mcp_id as assert_safe_id,
    assert_safe_mcp_principal_user_id as assert_safe_principal_user_id,
)
from app.mcp.domain.targets import normalize_mcp_targets
from app.mcp.infrastructure.catalog import MCP_DISCOVERY_PAGE_LIMIT


MCP_RUNTIME_LEASE_MAX_SECONDS = 30 * 60
MCP_MAX_TOOLS_PER_RUN = 40
MCP_MAX_SCHEMA_BYTES = 256 * 1024
MCP_RUNTIME_DISCOVERY_MAX_TOOLS = 10_000
MCP_RUNTIME_DISCOVERY_MAX_TOOL_BYTES = 4 * 1024 * 1024
MCP_CAPABILITY_GRANT_KEY_PREFIX = "ai-platform:mcp:run-capability:v1:"
MCP_CAPABILITY_GRANT_LOCK_PREFIX = "ai-platform:mcp:run-capability-lock:v1:"
MCP_PRINCIPAL_JWT_KEY_PREFIX = "ai-platform:mcp:principal-jwt:v1:"
MCP_CAPABILITY_GRANT_LOCK_TTL_SECONDS = 120
MCP_RELAY_AUTH_FAILURE_KEY_PREFIX = "ai-platform:mcp:relay-auth-failure:v1:"
MCP_RELAY_AUTH_FAILURE_CAPABILITY_LIMIT = 10
MCP_RELAY_AUTH_FAILURE_SOURCE_LIMIT = 1000
MCP_RELAY_AUTH_FAILURE_WINDOW_SECONDS = 60
MCP_CAPABILITY_HEADER = "X-MCP-Broker-Capability"
MCP_GATEWAY_SERVICE_TOKEN_HEADER = "X-MCP-Gateway-Service-Token"
_MCP_CAPABILITY_GRANT_AAD_PREFIX = b"ai-platform:mcp-run-capability:v1:"
_MCP_PRINCIPAL_JWT_AAD_PREFIX = b"ai-platform:mcp-principal-jwt:v1:"
_MCP_SERVER_CREDENTIAL_AAD_PREFIX = b"ai-platform:mcp-server-credential:v1:"
_RELAY_FORWARD_HEADERS = frozenset(
    {
        "accept",
        "content-type",
        "mcp-protocol-version",
        "mcp-session-id",
        "last-event-id",
    }
)
_FORBIDDEN_RELAY_HEADERS = frozenset(
    {
        "authorization",
        "jwt-authorization",
        "cookie",
        "host",
        "proxy-authorization",
        "x-forwarded-for",
        "x-forwarded-host",
        "x-forwarded-proto",
        "x-real-ip",
    }
)
_MCP_SESSION_ID_HEADER = "Mcp-Session-Id"
_MCP_SESSION_ID_MAX_LENGTH = 256


class RedisClientHandle(Protocol):
    """Structural Redis client contract supplied by bootstrap."""


class McpPrincipal(Protocol):
    tenant_id: str
    user_id: str


_settings_provider: Callable[[], object] | None = None
_redis_provider: Callable[[], RedisClientHandle] | None = None
_relay_target_reader: Callable[[str, str], Awaitable[dict[str, Any] | None]] | None = None


def configure_runtime_dependencies(
    *,
    settings_provider: Callable[[], object],
    redis_provider: Callable[[], RedisClientHandle],
    relay_target_reader: Callable[[str, str], Awaitable[dict[str, Any] | None]],
) -> None:
    global _settings_provider, _redis_provider, _relay_target_reader
    _settings_provider = settings_provider
    _redis_provider = redis_provider
    _relay_target_reader = relay_target_reader


def get_settings() -> object:
    if _settings_provider is None:
        raise McpRuntimeContextError("mcp_runtime_not_configured", status_code=503)
    return _settings_provider()


def get_redis_client() -> RedisClientHandle:
    if _redis_provider is None:
        raise McpRuntimeContextError("mcp_runtime_not_configured", status_code=503)
    return _redis_provider()


@dataclass(frozen=True)
class McpContextPrincipal:
    tenant_id: str
    user_id: str

    @classmethod
    def from_principal(cls, principal: McpPrincipal) -> "McpContextPrincipal":
        return cls(
            tenant_id=assert_safe_id(principal.tenant_id, "tenant_id"),
            user_id=assert_safe_principal_user_id(principal.user_id),
        )


@dataclass(frozen=True)
class McpBrokerCapability:
    token: str = field(repr=False)
    grant_id: str
    tenant_id: str
    user_id: str
    run_id: str
    attempt_id: str
    expires_at: int
    # Every reachable MCP target is grouped by its registered server ID. The
    # capability never carries endpoints, static headers, or the user JWT.
    targets: dict[str, tuple[str, ...]] = field(default_factory=dict)


@dataclass(frozen=True)
class McpRelayAuthFailureCounts:
    source: int
    capability: int


@dataclass(frozen=True)
class _RunCapabilityGrant:
    grant_id: str
    tenant_id: str
    user_id: str
    run_id: str
    attempt_id: str
    expires_at: int
    token: str
    token_sha256: str
    targets: dict[str, tuple[str, ...]] = field(default_factory=dict)


@dataclass(frozen=True)
class _PrincipalJwtRecord:
    tenant_id: str
    user_id: str
    jwt: str = field(repr=False)
    jwt_exp: int


@dataclass(frozen=True)
class McpRelayTarget:
    """One resolved server-side target, held only for the relay request."""

    endpoint: str
    static_headers: dict[str, str] = field(repr=False)
    active_tool_names: tuple[str, ...]


@dataclass(frozen=True)
class McpValidatedTarget:
    """A registered endpoint pinned to the exact address validated for dispatch."""

    endpoint: str
    connect_url: str
    host_header: str
    sni_hostname: str


class McpEncryptedStateStore(Protocol):
    async def create(self, key: str, value: str, *, ttl_seconds: int) -> bool:
        """Create one sealed state value only when its opaque key is unused."""

    async def get(self, key: str) -> str | None:
        """Return one sealed state value."""

    async def set(self, key: str, value: str, *, ttl_seconds: int) -> None:
        """Replace one sealed state value with a bounded TTL."""

    async def delete(self, key: str) -> None:
        """Delete one sealed state value."""

    async def acquire_lock(self, key: str, token: str, *, ttl_seconds: int) -> bool:
        """Acquire a distributed mutation lock for one state record."""

    async def release_lock(self, key: str, token: str) -> None:
        """Release the lock only when its value still equals the exact token."""


class InMemoryMcpEncryptedStateStore:
    """Small deterministic store for unit tests and process-local development."""

    def __init__(self, *, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._values: dict[str, tuple[float, str]] = {}
        self._lock = asyncio.Lock()
        self._mutation_locks: dict[str, asyncio.Lock] = {}
        self._mutation_lock_tokens: dict[str, str] = {}

    async def create(self, key: str, value: str, *, ttl_seconds: int) -> bool:
        async with self._lock:
            self._purge(key)
            if key in self._values:
                return False
            self._values[key] = (self._clock() + max(int(ttl_seconds), 1), value)
            return True

    async def get(self, key: str) -> str | None:
        async with self._lock:
            self._purge(key)
            item = self._values.get(key)
            return item[1] if item else None

    async def set(self, key: str, value: str, *, ttl_seconds: int) -> None:
        async with self._lock:
            self._values[key] = (self._clock() + max(int(ttl_seconds), 1), value)

    async def delete(self, key: str) -> None:
        async with self._lock:
            self._values.pop(key, None)

    async def acquire_lock(self, key: str, token: str, *, ttl_seconds: int) -> bool:
        del ttl_seconds
        async with self._lock:
            if key in self._mutation_lock_tokens:
                return False
            lock = self._mutation_locks.setdefault(key, asyncio.Lock())
            self._mutation_lock_tokens[key] = token
        await lock.acquire()
        return True

    async def release_lock(self, key: str, token: str) -> None:
        async with self._lock:
            if self._mutation_lock_tokens.get(key) != token:
                return
            self._mutation_lock_tokens.pop(key, None)
            lock = self._mutation_locks.get(key)
        if lock is not None and lock.locked():
            lock.release()

    def _purge(self, key: str) -> None:
        item = self._values.get(key)
        if item and item[0] <= self._clock():
            self._values.pop(key, None)


class RedisMcpEncryptedStateStore:
    """Redis-backed opaque store. The value is always sealed before Redis sees it."""

    def __init__(self, *, redis: RedisClientHandle | None = None) -> None:
        self._redis = redis

    def _client(self) -> RedisClientHandle:
        return self._redis or get_redis_client()

    async def create(self, key: str, value: str, *, ttl_seconds: int) -> bool:
        return bool(await self._client().set(key, value, ex=max(int(ttl_seconds), 1), nx=True))

    async def get(self, key: str) -> str | None:
        result = await self._client().get(key)
        return str(result) if result is not None else None

    async def set(self, key: str, value: str, *, ttl_seconds: int) -> None:
        await self._client().set(key, value, ex=max(int(ttl_seconds), 1))

    async def delete(self, key: str) -> None:
        await self._client().delete(key)

    async def acquire_lock(self, key: str, token: str, *, ttl_seconds: int) -> bool:
        return bool(await self._client().set(key, token, ex=max(int(ttl_seconds), 1), nx=True))

    async def release_lock(self, key: str, token: str) -> None:
        await self._client().eval(
            "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) else return 0 end",
            1,
            key,
            token,
        )


class McpRelayAuthFailureLimiter:
    """Bound capability failures without coupling callers on shared egress."""

    def __init__(self, *, redis: RedisClientHandle | None = None) -> None:
        self._redis = redis

    def _client(self) -> RedisClientHandle:
        return self._redis or get_redis_client()

    @staticmethod
    def _keys(source_fingerprint: str, capability_fingerprint: str) -> tuple[str, str]:
        return (
            f"{MCP_RELAY_AUTH_FAILURE_KEY_PREFIX}source:{source_fingerprint}",
            f"{MCP_RELAY_AUTH_FAILURE_KEY_PREFIX}capability:{capability_fingerprint}",
        )

    async def ensure_allowed(
        self,
        *,
        source_fingerprint: str,
        capability_fingerprint: str,
    ) -> None:
        try:
            values = await self._client().mget(
                *self._keys(source_fingerprint, capability_fingerprint)
            )
            counts = [int(value or 0) for value in values]
        except Exception as exc:  # noqa: BLE001 - Redis failures must fail closed.
            raise McpRuntimeContextError(
                "mcp_relay_limiter_unavailable",
                status_code=503,
            ) from exc
        source_count, capability_count = counts
        if (
            capability_count >= MCP_RELAY_AUTH_FAILURE_CAPABILITY_LIMIT
            or source_count >= MCP_RELAY_AUTH_FAILURE_SOURCE_LIMIT
        ):
            raise McpRuntimeContextError(
                "mcp_relay_rate_limited",
                status_code=429,
            )

    async def record_failure(
        self,
        *,
        source_fingerprint: str,
        capability_fingerprint: str,
    ) -> McpRelayAuthFailureCounts:
        source_key, capability_key = self._keys(
            source_fingerprint,
            capability_fingerprint,
        )
        try:
            result = await self._client().eval(
                "local counts = {} "
                "for index, key in ipairs(KEYS) do "
                "local count = redis.call('incr', key) "
                "if count == 1 then redis.call('expire', key, ARGV[1]) end "
                "counts[index] = count "
                "end "
                "return counts",
                2,
                source_key,
                capability_key,
                MCP_RELAY_AUTH_FAILURE_WINDOW_SECONDS,
            )
            if not isinstance(result, (list, tuple)) or len(result) != 2:
                raise ValueError("mcp_relay_limiter_result_invalid")
            return McpRelayAuthFailureCounts(
                source=int(result[0] or 0),
                capability=int(result[1] or 0),
            )
        except Exception as exc:  # noqa: BLE001 - Redis failures must fail closed.
            raise McpRuntimeContextError(
                "mcp_relay_limiter_unavailable",
                status_code=503,
            ) from exc


def _b64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode((value + padding).encode("ascii"))


def _decode_key(raw: object) -> bytes:
    if not isinstance(raw, str) or not raw:
        raise McpRuntimeContextError("mcp_context_key_invalid", status_code=503)
    try:
        if len(raw) == 64:
            decoded = bytes.fromhex(raw)
        else:
            decoded = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
    except (ValueError, TypeError) as exc:
        raise McpRuntimeContextError("mcp_context_key_invalid", status_code=503) from exc
    if len(decoded) != 32:
        raise McpRuntimeContextError("mcp_context_key_invalid", status_code=503)
    return decoded


def _configured_keyring() -> tuple[str, dict[str, bytes]]:
    settings = get_settings()
    raw = str(getattr(settings, "mcp_context_encryption_keys_json", "") or "").strip()
    values: dict[str, object] = {}
    if raw:
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise McpRuntimeContextError("mcp_context_keyring_invalid", status_code=503) from exc
        if not isinstance(parsed, dict):
            raise McpRuntimeContextError("mcp_context_keyring_invalid", status_code=503)
        values = parsed
    single_key = str(getattr(settings, "mcp_context_encryption_key", "") or "").strip()
    current_id = str(getattr(settings, "mcp_context_current_key_id", "current") or "current")
    if not values and single_key:
        values = {current_id: single_key}
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
    except (ValueError, UnicodeError, json.JSONDecodeError) as exc:
        raise McpRuntimeContextError("mcp_jwt_invalid", status_code=401) from exc
    if not isinstance(payload, dict):
        raise McpRuntimeContextError("mcp_jwt_invalid", status_code=401)
    exp = payload.get("exp")
    if isinstance(exp, bool) or not isinstance(exp, int) or exp <= int(time.time() if now is None else now):
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


def _open_principal_jwt(value: str, principal: McpContextPrincipal) -> _PrincipalJwtRecord:
    try:
        envelope = json.loads(value)
        if not isinstance(envelope, dict) or envelope.get("v") != 1:
            raise ValueError("principal JWT envelope version is invalid")
        key_id = str(envelope["key_id"])
        nonce = _b64url_decode(str(envelope["nonce"]))
        ciphertext = _b64url_decode(str(envelope["ciphertext"]))
        _, keyring = _configured_keyring()
        plaintext = AESGCM(keyring[key_id]).decrypt(
            nonce,
            ciphertext,
            _principal_jwt_aad(key_id, principal),
        )
        payload = json.loads(plaintext.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("principal JWT payload is not an object")
        record = _PrincipalJwtRecord(
            tenant_id=assert_safe_id(str(payload.get("tenant_id") or ""), "tenant_id"),
            user_id=assert_safe_principal_user_id(str(payload.get("user_id") or "")),
            jwt=str(payload.get("jwt") or ""),
            jwt_exp=int(payload.get("jwt_exp") or 0),
        )
        if (record.tenant_id, record.user_id) != (principal.tenant_id, principal.user_id):
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


class McpPrincipalJwtStore:
    """Own one encrypted company JWT for each tenant-scoped Principal."""

    def __init__(
        self,
        *,
        store: McpEncryptedStateStore | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.store = store or RedisMcpEncryptedStateStore()
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
            await self.store.set(
                _principal_jwt_key(binding),
                _seal_principal_jwt(record),
                ttl_seconds=record.jwt_exp - now,
            )
        except McpRuntimeContextError:
            raise
        except Exception as exc:  # noqa: BLE001 - credential storage must fail closed.
            raise McpRuntimeContextError(
                "mcp_principal_jwt_unavailable",
                status_code=503,
            ) from exc

    async def get(self, principal: McpPrincipal) -> str:
        binding = McpContextPrincipal.from_principal(principal)
        key = _principal_jwt_key(binding)
        try:
            value = await self.store.get(key)
        except Exception as exc:  # noqa: BLE001 - credential reads must fail closed.
            raise McpRuntimeContextError(
                "mcp_principal_jwt_unavailable",
                status_code=503,
            ) from exc
        if value is None:
            raise McpRuntimeContextError("mcp_principal_jwt_missing", status_code=401)
        record = _open_principal_jwt(value, binding)
        now = self._now()
        if record.jwt_exp <= now:
            try:
                await self.store.delete(key)
            except Exception:
                pass
            raise McpRuntimeContextError("mcp_principal_jwt_expired", status_code=401)
        try:
            payload = _jwt_payload(record.jwt, now=now)
        except McpRuntimeContextError as exc:
            raise McpRuntimeContextError("mcp_principal_jwt_corrupt", status_code=503) from exc
        if int(payload["exp"]) != record.jwt_exp:
            raise McpRuntimeContextError("mcp_principal_jwt_corrupt", status_code=503)
        return record.jwt


def _mcp_server_credential_aad(*, tenant_id: str, server_id: str) -> bytes:
    return (
        _MCP_SERVER_CREDENTIAL_AAD_PREFIX
        + assert_safe_id(tenant_id, "tenant_id").encode("utf-8")
        + b":"
        + assert_safe_id(server_id, "mcp_server_id").encode("utf-8")
    )


def seal_mcp_server_credentials(
    *,
    tenant_id: str,
    server_id: str,
    endpoint: str | None,
    static_headers: Mapping[str, str] | None,
) -> str:
    """Encrypt a server's non-public connection material with bound AAD."""

    normalized_headers = normalize_static_mcp_headers(static_headers)
    current_id, keyring = _configured_keyring()
    nonce = secrets.token_bytes(12)
    plaintext = json.dumps(
        {
            "endpoint": str(endpoint or ""),
            "headers": normalized_headers,
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
    """Open one runtime-only credential envelope without exposing its contents."""

    try:
        raw_envelope = json.loads(str(envelope or ""))
        key_id = str(raw_envelope["key_id"])
        nonce = _b64url_decode(str(raw_envelope["nonce"]))
        ciphertext = _b64url_decode(str(raw_envelope["ciphertext"]))
        _, keyring = _configured_keyring()
        plaintext = AESGCM(keyring[key_id]).decrypt(
            nonce,
            ciphertext,
            _mcp_server_credential_aad(tenant_id=tenant_id, server_id=server_id),
        )
        payload = json.loads(plaintext.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("credential payload is not an object")
        endpoint = payload.get("endpoint")
        headers = payload.get("headers")
        if not isinstance(endpoint, str) or not isinstance(headers, dict):
            raise ValueError("credential payload shape is invalid")
        return endpoint, normalize_static_mcp_headers(headers)
    except McpRuntimeContextError as exc:
        if exc.code == "mcp_header_conflict":
            raise McpRelayError("mcp_header_conflict", status_code=409) from exc
        raise McpRelayError("mcp_server_credentials_invalid", status_code=503) from exc
    except (
        InvalidTag,
        KeyError,
        TypeError,
        ValueError,
        UnicodeError,
        json.JSONDecodeError,
    ) as exc:
        raise McpRelayError("mcp_server_credentials_invalid", status_code=503) from exc


def _grant_payload(grant: _RunCapabilityGrant) -> dict[str, Any]:
    return {
        "grant_id": grant.grant_id,
        "tenant_id": grant.tenant_id,
        "user_id": grant.user_id,
        "run_id": grant.run_id,
        "attempt_id": grant.attempt_id,
        "expires_at": grant.expires_at,
        "token": grant.token,
        "token_sha256": grant.token_sha256,
        "targets": {
            server_id: list(tool_names)
            for server_id, tool_names in grant.targets.items()
        },
    }


def _grant_from_payload(payload: object) -> _RunCapabilityGrant:
    if not isinstance(payload, dict):
        raise McpRuntimeContextError("mcp_capability_grant_corrupt", status_code=503)
    raw_targets = payload.get("targets")
    return _RunCapabilityGrant(
        grant_id=assert_safe_id(str(payload.get("grant_id") or ""), "mcp_grant_id"),
        tenant_id=assert_safe_id(str(payload.get("tenant_id") or ""), "tenant_id"),
        user_id=assert_safe_principal_user_id(str(payload.get("user_id") or "")),
        run_id=assert_safe_id(str(payload.get("run_id") or ""), "run_id"),
        attempt_id=assert_safe_id(str(payload.get("attempt_id") or ""), "attempt_id"),
        expires_at=int(payload.get("expires_at") or 0),
        token=str(payload.get("token") or ""),
        token_sha256=str(payload.get("token_sha256") or ""),
        targets=normalize_mcp_targets(raw_targets) if isinstance(raw_targets, dict) else {},
    )


def _grant_aad(key_id: str, grant_id: str) -> bytes:
    return (
        _MCP_CAPABILITY_GRANT_AAD_PREFIX
        + key_id.encode("utf-8")
        + b":"
        + assert_safe_id(grant_id, "mcp_grant_id").encode("utf-8")
    )


def _seal_grant(grant: _RunCapabilityGrant) -> str:
    current_id, keyring = _configured_keyring()
    nonce = secrets.token_bytes(12)
    ciphertext = AESGCM(keyring[current_id]).encrypt(
        nonce,
        json.dumps(_grant_payload(grant), ensure_ascii=True, separators=(",", ":")).encode("utf-8"),
        _grant_aad(current_id, grant.grant_id),
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


def _open_grant(value: str, *, grant_id: str) -> _RunCapabilityGrant:
    try:
        envelope = json.loads(value)
        key_id = str(envelope["key_id"])
        nonce = _b64url_decode(str(envelope["nonce"]))
        ciphertext = _b64url_decode(str(envelope["ciphertext"]))
        _, keyring = _configured_keyring()
        plaintext = AESGCM(keyring[key_id]).decrypt(
            nonce,
            ciphertext,
            _grant_aad(key_id, grant_id),
        )
        grant = _grant_from_payload(json.loads(plaintext.decode("utf-8")))
        if grant.grant_id != grant_id:
            raise McpRuntimeContextError("mcp_capability_grant_corrupt", status_code=503)
        return grant
    except McpRuntimeContextError:
        raise
    except (
        InvalidTag,
        KeyError,
        ValueError,
        TypeError,
        UnicodeError,
        json.JSONDecodeError,
    ) as exc:
        raise McpRuntimeContextError("mcp_capability_grant_corrupt", status_code=503) from exc


def _run_grant_id(*, tenant_id: str, user_id: str, run_id: str) -> str:
    identity = json.dumps(
        [
            assert_safe_id(tenant_id, "tenant_id"),
            assert_safe_principal_user_id(user_id),
            assert_safe_id(run_id, "run_id"),
        ],
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"mcpgrant_{hashlib.sha256(identity).hexdigest()}"


def _grant_key(grant_id: str) -> str:
    return f"{MCP_CAPABILITY_GRANT_KEY_PREFIX}{assert_safe_id(grant_id, 'mcp_grant_id')}"


class McpRunCapabilityManager:
    """Issue one JWT-free, exact Run/attempt MCP capability grant."""

    def __init__(
        self,
        *,
        store: McpEncryptedStateStore | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.store = store or RedisMcpEncryptedStateStore()
        self.clock = clock

    def _now(self) -> int:
        return int(self.clock())

    @asynccontextmanager
    async def _mutation_guard(self, grant_id: str) -> AsyncIterator[None]:
        """Serialize one Run grant across all API and worker processes."""

        lock_key = f"{MCP_CAPABILITY_GRANT_LOCK_PREFIX}{assert_safe_id(grant_id, 'mcp_grant_id')}"
        lock_token = secrets.token_urlsafe(24)
        deadline = time.monotonic() + 5.0
        acquired = False
        while not acquired:
            acquired = await self.store.acquire_lock(
                lock_key,
                lock_token,
                ttl_seconds=MCP_CAPABILITY_GRANT_LOCK_TTL_SECONDS,
            )
            if acquired:
                break
            if time.monotonic() >= deadline:
                raise McpRuntimeContextError("mcp_capability_grant_busy", status_code=503)
            await asyncio.sleep(0.01)
        try:
            yield
        finally:
            await self.store.release_lock(lock_key, lock_token)

    async def _read_current(self, grant_id: str) -> tuple[str, _RunCapabilityGrant]:
        value = await self.store.get(_grant_key(grant_id))
        if value is None:
            raise McpRuntimeContextError("mcp_capability_grant_not_found", status_code=401)
        grant = _open_grant(value, grant_id=grant_id)
        if grant.expires_at <= self._now():
            await self.store.delete(_grant_key(grant_id))
            raise McpRuntimeContextError("mcp_capability_grant_expired", status_code=401)
        return value, grant

    def _bounded_lease_seconds(self) -> int:
        configured = int(
            getattr(get_settings(), "mcp_capability_ttl_seconds", MCP_RUNTIME_LEASE_MAX_SECONDS)
        )
        return max(1, min(configured, MCP_RUNTIME_LEASE_MAX_SECONDS))

    async def claim_attempt_lease(
        self,
        *,
        tenant_id: str,
        user_id: str,
        run_id: str,
        attempt_id: str,
        targets: Mapping[str, object] | None = None,
    ) -> McpBrokerCapability:
        assert_safe_id(tenant_id, "tenant_id")
        assert_safe_principal_user_id(user_id)
        assert_safe_id(run_id, "run_id")
        assert_safe_id(attempt_id, "attempt_id")
        normalized_targets = normalize_mcp_targets(targets)
        if not normalized_targets:
            raise McpRuntimeContextError("mcp_target_selection_required", status_code=409)
        grant_id = _run_grant_id(tenant_id=tenant_id, user_id=user_id, run_id=run_id)
        async with self._mutation_guard(grant_id):
            now = self._now()
            stored = await self.store.get(_grant_key(grant_id))
            if stored is not None:
                active = _open_grant(stored, grant_id=grant_id)
                if active.expires_at > now and active.attempt_id != attempt_id:
                    raise McpRuntimeContextError("mcp_attempt_lease_conflict", status_code=409)
                if active.expires_at > now and active.targets != normalized_targets:
                    raise McpRuntimeContextError("mcp_attempt_lease_conflict", status_code=409)
                if active.expires_at > now:
                    return self._capability(active)
            max_seconds = self._bounded_lease_seconds()
            expires_at = now + max_seconds
            token = f"mcpbrk:{grant_id}:{secrets.token_urlsafe(24)}"
            grant = _RunCapabilityGrant(
                grant_id=grant_id,
                tenant_id=tenant_id,
                user_id=user_id,
                run_id=run_id,
                attempt_id=attempt_id,
                expires_at=expires_at,
                token=token,
                token_sha256=hashlib.sha256(token.encode("utf-8")).hexdigest(),
                targets=dict(normalized_targets),
            )
            await self.store.set(
                _grant_key(grant_id),
                _seal_grant(grant),
                ttl_seconds=max_seconds,
            )
            return self._capability(grant)

    @staticmethod
    def _capability(grant: _RunCapabilityGrant) -> McpBrokerCapability:
        return McpBrokerCapability(
            token=grant.token,
            grant_id=grant.grant_id,
            tenant_id=grant.tenant_id,
            user_id=grant.user_id,
            run_id=grant.run_id,
            attempt_id=grant.attempt_id,
            expires_at=grant.expires_at,
            targets=dict(grant.targets),
        )

    async def resolve_capability(self, token: str) -> McpBrokerCapability:
        raw = str(token or "")
        prefix, separator, remainder = raw.partition(":")
        if prefix != "mcpbrk" or not separator:
            raise McpRelayError("mcp_capability_invalid", status_code=401)
        grant_id, separator, _ = remainder.partition(":")
        if not separator:
            raise McpRelayError("mcp_capability_invalid", status_code=401)
        try:
            _, grant = await self._read_current(grant_id)
        except McpRuntimeContextError as exc:
            if exc.code == "mcp_capability_grant_expired":
                raise McpRelayError("mcp_capability_expired", status_code=401) from exc
            raise McpRelayError("mcp_capability_invalid", status_code=401) from exc
        if grant.expires_at <= self._now():
            raise McpRelayError("mcp_capability_expired", status_code=401)
        if not secrets.compare_digest(grant.token_sha256, hashlib.sha256(raw.encode("utf-8")).hexdigest()):
            raise McpRelayError("mcp_capability_invalid", status_code=401)
        return self._capability(grant)

    async def release_attempt_lease(self, *, token: str) -> None:
        raw = str(token or "")
        try:
            prefix, _, remainder = raw.partition(":")
            grant_id = remainder.partition(":")[0] if prefix == "mcpbrk" else ""
            if not grant_id:
                return
        except McpRuntimeContextError:
            return
        async with self._mutation_guard(grant_id):
            try:
                _, grant = await self._read_current(grant_id)
            except McpRuntimeContextError:
                return
            if secrets.compare_digest(grant.token_sha256, hashlib.sha256(raw.encode()).hexdigest()):
                await self.store.delete(_grant_key(grant_id))

    async def release_run_grant(
        self,
        *,
        tenant_id: str,
        user_id: str,
        run_id: str,
    ) -> None:
        grant_id = _run_grant_id(tenant_id=tenant_id, user_id=user_id, run_id=run_id)
        async with self._mutation_guard(grant_id):
            await self.store.delete(_grant_key(grant_id))


_DEFAULT_PRINCIPAL_JWT_STORE: McpPrincipalJwtStore | None = None
_DEFAULT_RUN_CAPABILITY_MANAGER: McpRunCapabilityManager | None = None


def get_mcp_principal_jwt_store() -> McpPrincipalJwtStore:
    global _DEFAULT_PRINCIPAL_JWT_STORE
    if _DEFAULT_PRINCIPAL_JWT_STORE is None:
        _DEFAULT_PRINCIPAL_JWT_STORE = McpPrincipalJwtStore()
    return _DEFAULT_PRINCIPAL_JWT_STORE


def get_mcp_run_capability_manager() -> McpRunCapabilityManager:
    global _DEFAULT_RUN_CAPABILITY_MANAGER
    if _DEFAULT_RUN_CAPABILITY_MANAGER is None:
        _DEFAULT_RUN_CAPABILITY_MANAGER = McpRunCapabilityManager()
    return _DEFAULT_RUN_CAPABILITY_MANAGER


def _registered_mcp_target(raw: str) -> str:
    parsed = urlsplit(str(raw or "").strip())
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise McpRelayError("mcp_server_target_invalid", status_code=503)
    hostname = str(parsed.hostname or "").casefold().rstrip(".")
    if hostname == "localhost" or hostname.endswith(".localhost"):
        raise McpRelayError("mcp_server_target_invalid", status_code=503)
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        address = None
    if address is not None and (
        address.is_loopback
        or address.is_link_local
        or address.is_unspecified
        or address.is_multicast
        or address.is_reserved
        or not (address.is_private or (parsed.scheme == "https" and address.is_global))
    ):
        raise McpRelayError("mcp_server_target_invalid", status_code=503)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path or "/", "", ""))


async def validate_registered_mcp_target(raw: str) -> McpValidatedTarget:
    """Validate once and pin dispatch to one address from the accepted DNS set."""

    from app.mcp.infrastructure.catalog import (
        McpToolDiscoveryError,
        _address_is_permitted,
        _is_rfc1918,
        _resolve_discovery_addresses,
    )

    normalized = _registered_mcp_target(raw)
    parsed = urlsplit(normalized)
    hostname = str(parsed.hostname or "")
    try:
        addresses = await _resolve_discovery_addresses(
            hostname,
            parsed.port or (443 if parsed.scheme == "https" else 80),
        )
    except McpToolDiscoveryError as exc:
        raise McpRelayError("mcp_server_target_invalid", status_code=503) from exc
    if not all(_address_is_permitted(address, scheme=parsed.scheme) for address in addresses):
        raise McpRelayError("mcp_server_target_invalid", status_code=503)
    if not (
        all(_is_rfc1918(address) for address in addresses)
        or (parsed.scheme == "https" and all(address.is_global for address in addresses))
    ):
        raise McpRelayError("mcp_server_target_invalid", status_code=503)
    selected = min(addresses, key=lambda address: (address.version, int(address)))
    connect_hostname = (
        f"[{selected.compressed}]"
        if isinstance(selected, ipaddress.IPv6Address)
        else selected.compressed
    )
    connect_netloc = connect_hostname
    if parsed.port is not None:
        connect_netloc = f"{connect_netloc}:{parsed.port}"
    return McpValidatedTarget(
        endpoint=normalized,
        connect_url=urlunsplit(
            (parsed.scheme, connect_netloc, parsed.path or "/", "", "")
        ),
        host_header=parsed.netloc,
        sni_hostname=hostname,
    )


async def read_gateway_cache_revisions(
    endpoint: str,
    *,
    service_token: str,
) -> dict[str, Any] | None:
    """Read bounded Gateway revisions through the pinned infrastructure client."""

    token = str(service_token or "").strip()
    if not token:
        return None
    try:
        parsed = urlsplit(_registered_mcp_target(endpoint))
        revision_endpoint = urlunsplit(
            (parsed.scheme, parsed.netloc, "/api/internal/cache-revisions", "", "")
        )
        target = await validate_registered_mcp_target(revision_endpoint)
        async with httpx.AsyncClient(timeout=5.0, follow_redirects=False) as client:
            async with client.stream(
                "GET",
                target.connect_url,
                headers={
                    MCP_GATEWAY_SERVICE_TOKEN_HEADER: token,
                    "Host": target.host_header,
                    "Accept-Encoding": "identity",
                },
                extensions={"sni_hostname": target.sni_hostname},
            ) as response:
                if response.status_code != 200:
                    return None
                content_encoding = response.headers.get("Content-Encoding", "identity")
                if content_encoding.strip().lower() not in {"", "identity"}:
                    return None
                body = bytearray()
                async for chunk in response.aiter_raw():
                    if len(body) + len(chunk) > 16_384:
                        return None
                    body.extend(chunk)
        payload = json.loads(body)
        return payload if isinstance(payload, dict) else None
    except (McpRelayError, httpx.HTTPError, ValueError, json.JSONDecodeError, UnicodeDecodeError):
        return None


def _schema_bytes(tool: Mapping[str, Any]) -> int:
    schema = tool.get("inputSchema")
    if schema is None:
        schema = {}
    try:
        encoded = json.dumps(schema, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise McpRelayError("mcp_tools_schema_invalid", status_code=502) from exc
    return len(encoded)


def _tool_definition_bytes(tool: Mapping[str, Any]) -> int:
    try:
        encoded = json.dumps(
            tool,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise McpRelayError("mcp_tools_schema_invalid", status_code=502) from exc
    return len(encoded)


def bounded_tool_view(
    tools: object,
    *,
    selected_tool_names: list[str] | tuple[str, ...] | None = None,
) -> list[dict[str, Any]]:
    """Return complete concrete tool schemas, or explicitly require selection."""

    if not isinstance(tools, list) or any(not isinstance(tool, dict) for tool in tools):
        raise McpRelayError("mcp_tools_schema_invalid", status_code=502)
    normalized = [dict(tool) for tool in tools]
    normalized_names = [str(tool.get("name") or "") for tool in normalized]
    if (
        any(not name for name in normalized_names)
        or len(normalized_names) != len(set(normalized_names))
    ):
        raise McpRelayError("mcp_tools_schema_invalid", status_code=502)
    if selected_tool_names is not None:
        selected = set(str(item) for item in selected_tool_names)
        if len(selected) != len(selected_tool_names) or "" in selected:
            raise McpRelayError("mcp_tool_selection_invalid", status_code=409)
        normalized = [
            tool
            for tool, name in zip(normalized, normalized_names, strict=True)
            if name in selected
        ]
        if {str(tool["name"]) for tool in normalized} != selected:
            raise McpRelayError("mcp_tool_selection_invalid", status_code=409)
    schema_bytes = sum(_schema_bytes(tool) for tool in normalized)
    if len(normalized) > MCP_MAX_TOOLS_PER_RUN or schema_bytes > MCP_MAX_SCHEMA_BYTES:
        raise McpToolSelectionRequired()
    return normalized


async def resolve_registered_mcp_target(tenant_id: str, server_id: str) -> McpRelayTarget:
    """Resolve one active tenant registration without accepting a caller URL."""

    safe_tenant_id = assert_safe_id(tenant_id, "tenant_id")
    safe_server_id = assert_safe_id(server_id, "mcp_server_id")
    if _relay_target_reader is None:
        raise McpRelayError("mcp_runtime_not_configured", status_code=503)
    row = await _relay_target_reader(safe_tenant_id, safe_server_id)
    if row is None:
        raise McpRelayError("mcp_server_not_available", status_code=403)
    envelope = str(row.get("credential_envelope") or "")
    if not envelope:
        raise McpRelayError("mcp_server_not_available", status_code=503)
    endpoint, static_headers = open_mcp_server_credentials(
        tenant_id=safe_tenant_id,
        server_id=safe_server_id,
        envelope=envelope,
    )
    if not endpoint:
        raise McpRelayError("mcp_server_not_available", status_code=503)
    return McpRelayTarget(
        endpoint=endpoint,
        static_headers=static_headers,
        active_tool_names=tuple(
            str(item)
            for item in row.get("active_tool_names", [])
            if isinstance(item, str) and item
        ),
    )


class HostMcpRelay:
    """Forward JSON-RPC to capability-bound registered MCP targets."""

    def __init__(
        self,
        *,
        capability_manager: McpRunCapabilityManager,
        principal_jwt_store: McpPrincipalJwtStore | None = None,
        target_resolver: Callable[[str, str], Awaitable[McpRelayTarget]] | None = None,
        target_validator: Callable[
            [str], Awaitable[str | McpValidatedTarget]
        ]
        | None = None,
        client_factory: Callable[..., Any] | None = None,
    ) -> None:
        self.capability_manager = capability_manager
        self.principal_jwt_store = principal_jwt_store or get_mcp_principal_jwt_store()
        self.target_resolver = target_resolver or resolve_registered_mcp_target
        self.target_validator = target_validator or validate_registered_mcp_target
        self.client_factory = client_factory

    @staticmethod
    def _headers(
        incoming_headers: Mapping[str, str] | None,
        *,
        jwt: str,
        static_headers: Mapping[str, str] | None,
    ) -> dict[str, str]:
        output = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        }
        for key, value in (incoming_headers or {}).items():
            lowered = str(key).casefold()
            if lowered in _FORBIDDEN_RELAY_HEADERS:
                continue
            if lowered in _RELAY_FORWARD_HEADERS and isinstance(value, str) and len(value) <= 256:
                output[str(key)] = value
        output.update(normalize_static_mcp_headers(static_headers))
        output[MCP_JWT_AUTHORIZATION_HEADER] = f"Bearer {jwt}"
        return output

    @staticmethod
    def _json_response(response: httpx.Response, *, max_bytes: int) -> dict[str, Any]:
        content = response.content
        if len(content) > max_bytes:
            raise McpRelayError("mcp_server_response_too_large", status_code=502)
        try:
            content_type = response.headers.get("content-type", "")
            if "text/event-stream" in content_type:
                items = [
                    json.loads(line[5:].strip())
                    for line in response.text.splitlines()
                    if line.startswith("data:") and line[5:].strip()
                ]
                payload = next((item for item in reversed(items) if isinstance(item, dict)), None)
            else:
                payload = response.json()
        except (ValueError, json.JSONDecodeError) as exc:
            raise McpRelayError("mcp_server_protocol_error", status_code=502) from exc
        if not isinstance(payload, dict):
            raise McpRelayError("mcp_server_protocol_error", status_code=502)
        return payload

    @staticmethod
    def _copy_response_headers(
        response: httpx.Response,
        response_headers: MutableMapping[str, str] | None,
    ) -> None:
        if response_headers is None:
            return
        session_id = HostMcpRelay._response_session_id(response)
        if session_id:
            response_headers[_MCP_SESSION_ID_HEADER] = session_id

    @staticmethod
    def _response_session_id(response: httpx.Response) -> str | None:
        session_id = str(response.headers.get(_MCP_SESSION_ID_HEADER) or "")
        if (
            session_id
            and len(session_id) <= _MCP_SESSION_ID_MAX_LENGTH
            and all(0x21 <= ord(character) <= 0x7E for character in session_id)
        ):
            return session_id
        return None

    @staticmethod
    def _with_session_header(
        headers: Mapping[str, str] | None,
        session_id: str,
    ) -> dict[str, str]:
        output = {
            str(key): value
            for key, value in (headers or {}).items()
            if str(key).casefold() != _MCP_SESSION_ID_HEADER.casefold()
        }
        output[_MCP_SESSION_ID_HEADER] = session_id
        return output

    async def _post(
        self,
        *,
        target: McpRelayTarget,
        jwt: str,
        payload: dict[str, Any],
        incoming_headers: Mapping[str, str] | None = None,
    ) -> httpx.Response:
        timeout = float(getattr(get_settings(), "mcp_relay_timeout_seconds", 30.0))
        validated_target = await self.target_validator(target.endpoint)
        endpoint = (
            validated_target.connect_url
            if isinstance(validated_target, McpValidatedTarget)
            else validated_target
        )
        headers = self._headers(
            incoming_headers,
            jwt=jwt,
            static_headers=target.static_headers,
        )
        extensions: dict[str, Any] | None = None
        if isinstance(validated_target, McpValidatedTarget):
            headers["Host"] = validated_target.host_header
            extensions = {"sni_hostname": validated_target.sni_hostname}
        client = (
            self.client_factory(timeout=timeout, follow_redirects=False)
            if self.client_factory is not None
            else httpx.AsyncClient(timeout=timeout, follow_redirects=False)
        )
        try:
            async with client:
                try:
                    async with client.stream(
                        "POST",
                        endpoint,
                        json=payload,
                        headers=headers,
                        extensions=extensions,
                    ) as streamed_response:
                        content = bytearray()
                        max_bytes = int(
                            getattr(
                                get_settings(),
                                "mcp_relay_max_response_bytes",
                                1024 * 1024,
                            )
                        )
                        async for chunk in streamed_response.aiter_bytes():
                            content.extend(chunk)
                            if len(content) > max_bytes:
                                raise McpRelayError(
                                    "mcp_server_response_too_large",
                                    status_code=502,
                                )
                        response = httpx.Response(
                            streamed_response.status_code,
                            headers=streamed_response.headers,
                            content=bytes(content),
                            request=streamed_response.request,
                            extensions=streamed_response.extensions,
                        )
                except httpx.HTTPError as exc:
                    raise McpRelayError("mcp_server_unavailable", status_code=503) from exc
        finally:
            # The client context owns connection cleanup; no header or payload is logged here.
            pass
        if 300 <= response.status_code < 400:
            raise McpRelayError("mcp_server_redirect_blocked", status_code=502)
        if response.status_code == 401:
            raise McpRelayError("mcp_server_unauthorized", status_code=401)
        if response.status_code == 403:
            raise McpRelayError("mcp_server_forbidden", status_code=403)
        if response.status_code >= 400:
            raise McpRelayError("mcp_server_request_failed", status_code=502)
        return response

    @classmethod
    def _tool_list_result(
        cls,
        response: httpx.Response,
        *,
        max_bytes: int,
    ) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
        result = cls._json_response(response, max_bytes=max_bytes)
        result_body = result.get("result")
        if not isinstance(result_body, dict):
            raise McpRelayError("mcp_server_protocol_error", status_code=502)
        tools = result_body.get("tools")
        if not isinstance(tools, list) or any(not isinstance(tool, dict) for tool in tools):
            raise McpRelayError("mcp_tools_schema_invalid", status_code=502)
        return result, result_body, [dict(tool) for tool in tools]

    @staticmethod
    def _assert_discovery_bounds(
        tools: list[dict[str, Any]],
        *,
        tool_bytes: int,
    ) -> None:
        if (
            len(tools) > MCP_RUNTIME_DISCOVERY_MAX_TOOLS
            or tool_bytes > MCP_RUNTIME_DISCOVERY_MAX_TOOL_BYTES
        ):
            raise McpRelayError("mcp_tool_catalog_unbounded", status_code=502)

    async def forward(
        self,
        *,
        capability_token: str,
        server_id: str,
        payload: dict[str, Any],
        incoming_headers: Mapping[str, str] | None = None,
        response_headers: MutableMapping[str, str] | None = None,
    ) -> dict[str, Any] | None:
        if not isinstance(payload, dict) or payload.get("jsonrpc") != "2.0":
            raise McpRelayError("mcp_jsonrpc_invalid", status_code=422)
        method = payload.get("method")
        if method not in {"initialize", "notifications/initialized", "tools/list", "tools/call"}:
            raise McpRelayError("mcp_method_not_supported", status_code=422)
        capability = await self.capability_manager.resolve_capability(capability_token)
        try:
            safe_server_id = assert_safe_id(server_id, "mcp_server_id")
        except ValueError as exc:
            raise McpRelayError("mcp_server_not_selected", status_code=403) from exc
        allowed_tool_names = capability.targets.get(safe_server_id)
        if not allowed_tool_names:
            raise McpRelayError("mcp_server_not_selected", status_code=403)
        target = await self.target_resolver(capability.tenant_id, safe_server_id)
        jwt = await self.principal_jwt_store.get(
            McpContextPrincipal(capability.tenant_id, capability.user_id)
        )
        max_bytes = int(getattr(get_settings(), "mcp_relay_max_response_bytes", 1024 * 1024))
        if method == "tools/call":
            params = payload.get("params")
            tool_name = params.get("name") if isinstance(params, dict) else None
            if not isinstance(tool_name, str) or tool_name not in set(allowed_tool_names):
                raise McpRelayError("mcp_tool_not_selected", status_code=403)

        response = await self._post(
            target=target,
            jwt=jwt,
            payload=payload,
            incoming_headers=incoming_headers,
        )
        self._copy_response_headers(response, response_headers)
        if method == "notifications/initialized" and (
            response.status_code in {202, 204} or not response.content
        ):
            return None
        if method == "tools/list":
            result, result_body, current_tools = self._tool_list_result(
                response,
                max_bytes=max_bytes,
            )
            pagination_headers = dict(incoming_headers or {})
            session_id = self._response_session_id(response)
            if session_id:
                pagination_headers = self._with_session_header(
                    pagination_headers,
                    session_id,
                )
            first_result_body = dict(result_body)
            all_tools: list[dict[str, Any]] = []
            current_by_name: dict[str, dict[str, Any]] = {}
            seen_cursors: set[str] = set()
            tool_bytes = 0
            page_number = 0
            while True:
                current_names = [str(tool.get("name") or "") for tool in current_tools]
                if (
                    any(not name for name in current_names)
                    or len(current_names) != len(set(current_names))
                    or any(name in current_by_name for name in current_names)
                ):
                    raise McpRelayError("mcp_tools_schema_invalid", status_code=502)
                for tool, name in zip(current_tools, current_names, strict=True):
                    copied = dict(tool)
                    current_by_name[name] = copied
                    all_tools.append(copied)
                    tool_bytes += _tool_definition_bytes(copied)
                self._assert_discovery_bounds(all_tools, tool_bytes=tool_bytes)

                next_cursor = result_body.get("nextCursor")
                if next_cursor is None:
                    break
                if (
                    not isinstance(next_cursor, str)
                    or not next_cursor
                    or next_cursor in seen_cursors
                    or page_number + 1 >= MCP_DISCOVERY_PAGE_LIMIT
                ):
                    raise McpRelayError("mcp_tool_catalog_unbounded", status_code=502)
                seen_cursors.add(next_cursor)
                page_number += 1
                original_params = payload.get("params")
                params = dict(original_params) if isinstance(original_params, dict) else {}
                params["cursor"] = next_cursor
                jwt = await self.principal_jwt_store.get(
                    McpContextPrincipal(capability.tenant_id, capability.user_id)
                )
                page_response = await self._post(
                    target=target,
                    jwt=jwt,
                    payload={
                        **payload,
                        "id": f"{payload.get('id', 'mcp-tools-list')}:{page_number}",
                        "params": params,
                    },
                    incoming_headers=pagination_headers,
                )
                self._copy_response_headers(page_response, response_headers)
                session_id = self._response_session_id(page_response)
                if session_id:
                    pagination_headers = self._with_session_header(
                        pagination_headers,
                        session_id,
                    )
                _, result_body, current_tools = self._tool_list_result(
                    page_response,
                    max_bytes=max_bytes,
                )
            bounded = bounded_tool_view(
                [current_by_name[name] for name in allowed_tool_names if name in current_by_name]
            )
            result["result"] = {
                key: value for key, value in first_result_body.items() if key != "nextCursor"
            }
            result["result"]["tools"] = bounded
            return result
        return self._json_response(response, max_bytes=max_bytes)
