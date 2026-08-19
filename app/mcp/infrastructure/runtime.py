from __future__ import annotations

import asyncio
import base64
import hashlib
import ipaddress
import json
import secrets
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
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


MCP_RUNTIME_CONTEXT_TTL_SECONDS = 5 * 60
MCP_RUNTIME_LEASE_MAX_SECONDS = 30 * 60
MCP_MAX_TOOLS_PER_RUN = 40
MCP_MAX_SCHEMA_BYTES = 256 * 1024
MCP_CONTEXT_KEY_PREFIX = "ai-platform:mcp:runtime-context:v1:"
MCP_CONTEXT_LOCK_PREFIX = "ai-platform:mcp:runtime-context-lock:v1:"
MCP_CONTEXT_LOCK_TTL_SECONDS = 120
MCP_RELAY_AUTH_FAILURE_KEY_PREFIX = "ai-platform:mcp:relay-auth-failure:v1:"
MCP_RELAY_AUTH_FAILURE_CAPABILITY_LIMIT = 10
MCP_RELAY_AUTH_FAILURE_SOURCE_LIMIT = 1000
MCP_RELAY_AUTH_FAILURE_WINDOW_SECONDS = 60
MCP_CAPABILITY_HEADER = "X-MCP-Broker-Capability"
_MCP_CONTEXT_AAD_PREFIX = b"ai-platform:mcp-runtime-context:v1:"
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
    context_id: str
    tenant_id: str
    user_id: str
    run_id: str
    attempt_id: str
    expires_at: int
    # Every reachable MCP target is grouped by its registered server ID. The
    # capability never carries endpoints, static headers, or the user JWT.
    targets: dict[str, tuple[str, ...]] = field(default_factory=dict)


@dataclass(frozen=True)
class McpResolvedCapability:
    capability: McpBrokerCapability
    jwt: str = field(repr=False)
    jwt_fingerprint: str


@dataclass(frozen=True)
class McpRelayAuthFailureCounts:
    source: int
    capability: int


@dataclass(frozen=True)
class _ActiveLease:
    run_id: str
    attempt_id: str
    expires_at: int
    token: str
    token_sha256: str
    targets: dict[str, tuple[str, ...]] = field(default_factory=dict)


@dataclass(frozen=True)
class _RuntimeContextRecord:
    context_id: str
    tenant_id: str
    user_id: str
    jwt: str = field(repr=False)
    jwt_exp: int
    bind_expires_at: int
    expires_at: int
    bound_run_id: str | None = None
    active_lease: _ActiveLease | None = None


@dataclass(frozen=True)
class McpRuntimePreflight:
    context_id: str
    run_id: str


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


class RuntimeContextStore(Protocol):
    async def create(self, key: str, value: str, *, ttl_seconds: int) -> bool:
        """Create one context value only when its opaque key is unused."""

    async def get(self, key: str) -> str | None:
        """Return one sealed context value."""

    async def set(self, key: str, value: str, *, ttl_seconds: int) -> None:
        """Replace one sealed context value with a bounded TTL."""

    async def compare_and_set(
        self,
        key: str,
        expected: str,
        value: str,
        *,
        ttl_seconds: int,
    ) -> bool:
        """Replace one sealed value only when it is still the expected value."""

    async def delete(self, key: str) -> None:
        """Delete one context value."""

    async def acquire_lock(self, key: str, token: str, *, ttl_seconds: int) -> bool:
        """Acquire a distributed mutation lock for one context."""

    async def release_lock(self, key: str, token: str) -> None:
        """Release the lock only when its value still equals the exact token."""


class InMemoryRuntimeContextStore:
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

    async def compare_and_set(
        self,
        key: str,
        expected: str,
        value: str,
        *,
        ttl_seconds: int,
    ) -> bool:
        async with self._lock:
            self._purge(key)
            item = self._values.get(key)
            if item is None or item[1] != expected:
                return False
            self._values[key] = (self._clock() + max(int(ttl_seconds), 1), value)
            return True

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


class RedisRuntimeContextStore:
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

    async def compare_and_set(
        self,
        key: str,
        expected: str,
        value: str,
        *,
        ttl_seconds: int,
    ) -> bool:
        return bool(
            await self._client().eval(
                "if redis.call('get', KEYS[1]) == ARGV[1] then "
                "return redis.call('set', KEYS[1], ARGV[2], 'EX', ARGV[3]) "
                "else return 0 end",
                1,
                key,
                expected,
                value,
                max(int(ttl_seconds), 1),
            )
        )

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


def extract_bearer_jwt(value: str | None) -> str:
    """Parse only the MCP-specific header; no body or standard Authorization fallback."""

    raw = str(value or "").strip()
    scheme, separator, token = raw.partition(" ")
    if not separator or scheme.casefold() != "bearer" or not token.strip():
        raise McpRuntimeContextError("mcp_jwt_missing", status_code=401)
    return token.strip()


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


def _utc_iso8601(timestamp: int) -> str:
    return datetime.fromtimestamp(int(timestamp), tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _record_payload(record: _RuntimeContextRecord) -> dict[str, Any]:
    active = record.active_lease
    return {
        "context_id": record.context_id,
        "tenant_id": record.tenant_id,
        "user_id": record.user_id,
        "jwt": record.jwt,
        "jwt_exp": record.jwt_exp,
        "bind_expires_at": record.bind_expires_at,
        "expires_at": record.expires_at,
        "bound_run_id": record.bound_run_id,
        "active_lease": (
            {
                "run_id": active.run_id,
                "attempt_id": active.attempt_id,
                "expires_at": active.expires_at,
                "token": active.token,
                "token_sha256": active.token_sha256,
                "targets": {
                    server_id: list(tool_names)
                    for server_id, tool_names in active.targets.items()
                },
            }
            if active is not None
            else None
        ),
    }


def _record_from_payload(payload: object) -> _RuntimeContextRecord:
    if not isinstance(payload, dict):
        raise McpRuntimeContextError("mcp_context_corrupt", status_code=503)
    active_raw = payload.get("active_lease")
    active = None
    if active_raw is not None:
        if not isinstance(active_raw, dict):
            raise McpRuntimeContextError("mcp_context_corrupt", status_code=503)
        raw_targets = active_raw.get("targets")
        active = _ActiveLease(
            run_id=assert_safe_id(str(active_raw.get("run_id") or ""), "run_id"),
            attempt_id=assert_safe_id(str(active_raw.get("attempt_id") or ""), "attempt_id"),
            expires_at=int(active_raw.get("expires_at") or 0),
            token=str(active_raw.get("token") or ""),
            token_sha256=str(active_raw.get("token_sha256") or ""),
            targets=normalize_mcp_targets(raw_targets) if isinstance(raw_targets, dict) else {},
        )
    expires_at = int(payload.get("expires_at") or 0)
    return _RuntimeContextRecord(
        context_id=assert_safe_id(str(payload.get("context_id") or ""), "mcp_context_id"),
        tenant_id=assert_safe_id(str(payload.get("tenant_id") or ""), "tenant_id"),
        user_id=assert_safe_principal_user_id(str(payload.get("user_id") or "")),
        jwt=str(payload.get("jwt") or ""),
        jwt_exp=int(payload.get("jwt_exp") or 0),
        bind_expires_at=int(payload.get("bind_expires_at") or expires_at),
        expires_at=expires_at,
        bound_run_id=(str(payload["bound_run_id"]) if payload.get("bound_run_id") else None),
        active_lease=active,
    )


def _context_aad(key_id: str, context_id: str) -> bytes:
    return (
        _MCP_CONTEXT_AAD_PREFIX
        + key_id.encode("utf-8")
        + b":"
        + assert_safe_id(context_id, "mcp_context_id").encode("utf-8")
    )


def _seal(record: _RuntimeContextRecord) -> str:
    current_id, keyring = _configured_keyring()
    nonce = secrets.token_bytes(12)
    ciphertext = AESGCM(keyring[current_id]).encrypt(
        nonce,
        json.dumps(_record_payload(record), ensure_ascii=True, separators=(",", ":")).encode("utf-8"),
        _context_aad(current_id, record.context_id),
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


def _open(value: str, *, context_id: str) -> _RuntimeContextRecord:
    try:
        envelope = json.loads(value)
        key_id = str(envelope["key_id"])
        nonce = _b64url_decode(str(envelope["nonce"]))
        ciphertext = _b64url_decode(str(envelope["ciphertext"]))
        _, keyring = _configured_keyring()
        plaintext = AESGCM(keyring[key_id]).decrypt(
            nonce,
            ciphertext,
            _context_aad(key_id, context_id),
        )
        record = _record_from_payload(json.loads(plaintext.decode("utf-8")))
        if record.context_id != context_id:
            raise McpRuntimeContextError("mcp_context_corrupt", status_code=503)
        return record
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
        raise McpRuntimeContextError("mcp_context_corrupt", status_code=503) from exc


def _context_key(context_id: str) -> str:
    return f"{MCP_CONTEXT_KEY_PREFIX}{assert_safe_id(context_id, 'mcp_context_id')}"


class McpRuntimeContextManager:
    """Own encrypted JWT contexts and one exact active Broker capability."""

    def __init__(
        self,
        *,
        store: RuntimeContextStore | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.store = store or RedisRuntimeContextStore()
        self.clock = clock

    def _now(self) -> int:
        return int(self.clock())

    @asynccontextmanager
    async def _mutation_guard(self, context_id: str) -> AsyncIterator[None]:
        """Serialize encrypted record updates across all API/worker processes."""

        lock_key = f"{MCP_CONTEXT_LOCK_PREFIX}{assert_safe_id(context_id, 'mcp_context_id')}"
        lock_token = secrets.token_urlsafe(24)
        deadline = time.monotonic() + 5.0
        acquired = False
        while not acquired:
            acquired = await self.store.acquire_lock(
                lock_key,
                lock_token,
                ttl_seconds=MCP_CONTEXT_LOCK_TTL_SECONDS,
            )
            if acquired:
                break
            if time.monotonic() >= deadline:
                raise McpRuntimeContextError("mcp_context_busy", status_code=503)
            await asyncio.sleep(0.01)
        try:
            yield
        finally:
            await self.store.release_lock(lock_key, lock_token)

    async def create_context(self, *, principal: McpPrincipal, bearer_jwt: str) -> dict[str, Any]:
        jwt = extract_bearer_jwt(bearer_jwt)
        now = self._now()
        payload = _jwt_payload(jwt, now=now)
        context_id = f"mcpctx_{secrets.token_urlsafe(24)}"
        principal_binding = McpContextPrincipal.from_principal(principal)
        ttl = max(1, int(getattr(get_settings(), "mcp_context_ttl_seconds", MCP_RUNTIME_CONTEXT_TTL_SECONDS)))
        bind_expires_at = min(now + ttl, int(payload["exp"]))
        if bind_expires_at <= now:
            raise McpRuntimeContextError("mcp_jwt_expired_or_missing", status_code=401)
        record = _RuntimeContextRecord(
            context_id=context_id,
            tenant_id=principal_binding.tenant_id,
            user_id=principal_binding.user_id,
            jwt=jwt,
            jwt_exp=int(payload["exp"]),
            bind_expires_at=bind_expires_at,
            # This field is populated only after exact Run binding. The
            # unbound context lifetime is carried separately by bind_expires_at.
            expires_at=0,
        )
        if not await self.store.create(
            _context_key(context_id),
            _seal(record),
            ttl_seconds=bind_expires_at - now,
        ):
            raise McpRuntimeContextError("mcp_context_create_conflict", status_code=503)
        return {
            "mcp_context_id": context_id,
            "expires_at": _utc_iso8601(bind_expires_at),
        }

    async def _read_current(self, context_id: str) -> tuple[str, _RuntimeContextRecord]:
        value = await self.store.get(_context_key(context_id))
        if value is None:
            raise McpRuntimeContextError("mcp_context_not_found", status_code=401)
        record = _open(value, context_id=context_id)
        deadline = record.expires_at if record.bound_run_id else record.bind_expires_at
        if deadline <= self._now() or record.jwt_exp <= self._now():
            await self.store.delete(_context_key(context_id))
            raise McpRuntimeContextError("mcp_context_expired", status_code=401)
        return value, record

    async def _read(self, context_id: str) -> _RuntimeContextRecord:
        _, record = await self._read_current(context_id)
        return record

    def _bounded_lease_seconds(self) -> int:
        configured = int(
            getattr(get_settings(), "mcp_context_lease_seconds", MCP_RUNTIME_LEASE_MAX_SECONDS)
        )
        return max(1, min(configured, MCP_RUNTIME_LEASE_MAX_SECONDS))

    async def _compare_and_set(
        self,
        context_id: str,
        *,
        expected: str,
        record: _RuntimeContextRecord,
        ttl_seconds: int,
    ) -> None:
        updated = await self.store.compare_and_set(
            _context_key(context_id),
            expected,
            _seal(record),
            ttl_seconds=max(1, int(ttl_seconds)),
        )
        if not updated:
            raise McpRuntimeContextError("mcp_context_busy", status_code=503)

    @staticmethod
    def _assert_principal(record: _RuntimeContextRecord, principal: McpPrincipal) -> None:
        binding = McpContextPrincipal.from_principal(principal)
        if record.tenant_id != binding.tenant_id or record.user_id != binding.user_id:
            raise McpRuntimeContextError("mcp_context_principal_mismatch", status_code=403)

    async def bind_to_run(
        self,
        *,
        context_id: str,
        principal: McpPrincipal,
        run_id: str,
    ) -> _RuntimeContextRecord:
        assert_safe_id(run_id, "run_id")
        async with self._mutation_guard(context_id):
            raw, record = await self._read_current(context_id)
            self._assert_principal(record, principal)
            if record.bound_run_id and record.bound_run_id != run_id:
                raise McpRuntimeContextError("mcp_context_run_mismatch", status_code=403)
            if record.bound_run_id == run_id:
                return record
            now = self._now()
            lease_seconds = self._bounded_lease_seconds()
            credential_expires_at = min(record.jwt_exp, now + lease_seconds)
            if credential_expires_at <= now:
                raise McpRuntimeContextError("mcp_context_expired", status_code=401)
            updated = replace(
                record,
                bound_run_id=run_id,
                expires_at=credential_expires_at,
            )
            await self._compare_and_set(
                context_id,
                expected=raw,
                record=updated,
                ttl_seconds=credential_expires_at - now,
            )
            return updated

    async def claim_attempt_lease(
        self,
        *,
        context_id: str,
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
        async with self._mutation_guard(context_id):
            raw, record = await self._read_current(context_id)
            if record.tenant_id != tenant_id or record.user_id != user_id:
                raise McpRuntimeContextError("mcp_context_principal_mismatch", status_code=403)
            if record.bound_run_id != run_id:
                raise McpRuntimeContextError("mcp_context_run_mismatch", status_code=403)
            now = self._now()
            active = record.active_lease
            if active is not None and active.expires_at > now:
                if active.run_id != run_id or active.attempt_id != attempt_id:
                    raise McpRuntimeContextError("mcp_attempt_lease_conflict", status_code=409)
                if active.targets != normalized_targets:
                    raise McpRuntimeContextError("mcp_attempt_lease_conflict", status_code=409)
                return McpBrokerCapability(
                    token=active.token,
                    context_id=record.context_id,
                    tenant_id=record.tenant_id,
                    user_id=record.user_id,
                    run_id=active.run_id,
                    attempt_id=active.attempt_id,
                    expires_at=active.expires_at,
                    targets=dict(active.targets),
                )
            max_seconds = self._bounded_lease_seconds()
            expires_at = min(record.expires_at, record.jwt_exp, now + max_seconds)
            if expires_at <= now:
                raise McpRuntimeContextError("mcp_context_expired", status_code=401)
            token = f"mcpbrk:{record.context_id}:{secrets.token_urlsafe(24)}"
            active = _ActiveLease(
                run_id=run_id,
                attempt_id=attempt_id,
                expires_at=expires_at,
                token=token,
                token_sha256=hashlib.sha256(token.encode("utf-8")).hexdigest(),
                targets=dict(normalized_targets),
            )
            updated = replace(record, active_lease=active)
            await self._compare_and_set(
                context_id,
                expected=raw,
                record=updated,
                ttl_seconds=updated.expires_at - now,
            )
            return McpBrokerCapability(
                token=token,
                context_id=record.context_id,
                tenant_id=record.tenant_id,
                user_id=record.user_id,
                run_id=run_id,
                attempt_id=attempt_id,
                expires_at=expires_at,
                targets=dict(normalized_targets),
            )

    async def resolve_capability(self, token: str) -> McpResolvedCapability:
        raw = str(token or "")
        prefix, separator, remainder = raw.partition(":")
        if prefix != "mcpbrk" or not separator:
            raise McpRelayError("mcp_capability_invalid", status_code=401)
        context_id, separator, _ = remainder.partition(":")
        if not separator:
            raise McpRelayError("mcp_capability_invalid", status_code=401)
        try:
            record = await self._read(context_id)
        except McpRuntimeContextError as exc:
            raise McpRelayError("mcp_capability_invalid", status_code=401) from exc
        active = record.active_lease
        if active is None or active.expires_at <= self._now():
            raise McpRelayError("mcp_capability_expired", status_code=401)
        if not secrets.compare_digest(active.token_sha256, hashlib.sha256(raw.encode("utf-8")).hexdigest()):
            raise McpRelayError("mcp_capability_invalid", status_code=401)
        capability = McpBrokerCapability(
            token=raw,
            context_id=record.context_id,
            tenant_id=record.tenant_id,
            user_id=record.user_id,
            run_id=active.run_id,
            attempt_id=active.attempt_id,
            expires_at=active.expires_at,
            targets=dict(active.targets),
        )
        return McpResolvedCapability(
            capability=capability,
            jwt=record.jwt,
            jwt_fingerprint=hashlib.sha256(record.jwt.encode("utf-8")).hexdigest(),
        )

    async def release_attempt_lease(self, *, token: str) -> None:
        raw = str(token or "")
        try:
            prefix, _, remainder = raw.partition(":")
            context_id = remainder.partition(":")[0] if prefix == "mcpbrk" else ""
            if not context_id:
                return
        except McpRuntimeContextError:
            return
        async with self._mutation_guard(context_id):
            try:
                stored, record = await self._read_current(context_id)
            except McpRuntimeContextError:
                return
            active = record.active_lease
            if active and secrets.compare_digest(active.token_sha256, hashlib.sha256(raw.encode()).hexdigest()):
                updated = replace(record, active_lease=None)
                await self._compare_and_set(
                    record.context_id,
                    expected=stored,
                    record=updated,
                    ttl_seconds=max(1, updated.expires_at - self._now()),
                )

    async def invalidate_context(self, context_id: str) -> None:
        """Retire a context after MCP authentication failure or explicit logout."""

        await self.store.delete(_context_key(context_id))

    async def discard_unbound_context(
        self,
        context_id: str,
        principal: McpPrincipal,
    ) -> bool:
        """Delete only an unused context owned by the supplied principal."""

        async with self._mutation_guard(context_id):
            try:
                _, record = await self._read_current(context_id)
            except McpRuntimeContextError:
                return False
            try:
                self._assert_principal(record, principal)
            except McpRuntimeContextError:
                return False
            if record.bound_run_id is not None:
                return False
            await self.store.delete(_context_key(context_id))
            return True


_DEFAULT_RUNTIME_CONTEXT_MANAGER: McpRuntimeContextManager | None = None


def get_mcp_runtime_context_manager() -> McpRuntimeContextManager:
    global _DEFAULT_RUNTIME_CONTEXT_MANAGER
    if _DEFAULT_RUNTIME_CONTEXT_MANAGER is None:
        _DEFAULT_RUNTIME_CONTEXT_MANAGER = McpRuntimeContextManager()
    return _DEFAULT_RUNTIME_CONTEXT_MANAGER


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

    from app.mcp.catalog import (
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


def _schema_bytes(tool: Mapping[str, Any]) -> int:
    schema = tool.get("inputSchema")
    if schema is None:
        schema = {}
    try:
        encoded = json.dumps(schema, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
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
        context_manager: McpRuntimeContextManager,
        target_resolver: Callable[[str, str], Awaitable[McpRelayTarget]] | None = None,
        target_validator: Callable[
            [str], Awaitable[str | McpValidatedTarget]
        ]
        | None = None,
        client_factory: Callable[..., Any] | None = None,
    ) -> None:
        self.context_manager = context_manager
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
        session_id = str(response.headers.get(_MCP_SESSION_ID_HEADER) or "")
        if (
            session_id
            and len(session_id) <= _MCP_SESSION_ID_MAX_LENGTH
            and all(0x21 <= ord(character) <= 0x7E for character in session_id)
        ):
            response_headers[_MCP_SESSION_ID_HEADER] = session_id

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
        if result_body.get("nextCursor"):
            raise McpRelayError("mcp_tool_catalog_unbounded", status_code=502)
        tools = result_body.get("tools")
        if not isinstance(tools, list) or any(not isinstance(tool, dict) for tool in tools):
            raise McpRelayError("mcp_tools_schema_invalid", status_code=502)
        return result, result_body, [dict(tool) for tool in tools]

    async def preflight(
        self,
        *,
        context_id: str,
        principal: McpPrincipal,
        run_id: str,
        selected_tool_names: list[str] | tuple[str, ...] | None = None,
    ) -> McpRuntimePreflight:
        """Bind the encrypted JWT context before Run persistence."""

        del selected_tool_names
        await self.context_manager.bind_to_run(
            context_id=context_id,
            principal=principal,
            run_id=run_id,
        )
        return McpRuntimePreflight(context_id=context_id, run_id=run_id)

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
        resolved = await self.context_manager.resolve_capability(capability_token)
        try:
            safe_server_id = assert_safe_id(server_id, "mcp_server_id")
        except ValueError as exc:
            raise McpRelayError("mcp_server_not_selected", status_code=403) from exc
        allowed_tool_names = resolved.capability.targets.get(safe_server_id)
        if not allowed_tool_names:
            raise McpRelayError("mcp_server_not_selected", status_code=403)
        target = await self.target_resolver(resolved.capability.tenant_id, safe_server_id)
        if not set(allowed_tool_names).issubset(set(target.active_tool_names)):
            raise McpRelayError("mcp_tool_revoked", status_code=403)
        max_bytes = int(getattr(get_settings(), "mcp_relay_max_response_bytes", 1024 * 1024))
        if method == "tools/call":
            params = payload.get("params")
            tool_name = params.get("name") if isinstance(params, dict) else None
            if not isinstance(tool_name, str) or tool_name not in set(allowed_tool_names):
                raise McpRelayError("mcp_tool_not_selected", status_code=403)

        try:
            response = await self._post(
                target=target,
                jwt=resolved.jwt,
                payload=payload,
                incoming_headers=incoming_headers,
            )
        except McpRelayError as exc:
            if exc.code == "mcp_server_unauthorized":
                await self.context_manager.invalidate_context(resolved.capability.context_id)
            raise
        self._copy_response_headers(response, response_headers)
        if response.status_code == 401:
            await self.context_manager.invalidate_context(resolved.capability.context_id)
        if method == "notifications/initialized" and (
            response.status_code in {202, 204} or not response.content
        ):
            return None
        if method == "tools/list":
            result, result_body, current_tools = self._tool_list_result(
                response,
                max_bytes=max_bytes,
            )
            current_names = [str(tool.get("name") or "") for tool in current_tools]
            if (
                any(not name for name in current_names)
                or len(current_names) != len(set(current_names))
            ):
                raise McpRelayError("mcp_tools_schema_invalid", status_code=502)
            current_by_name = {
                str(tool["name"]): dict(tool) for tool in current_tools
            }
            if not set(allowed_tool_names).issubset(set(current_by_name)):
                raise McpRelayError("mcp_tool_revoked", status_code=403)
            bounded = bounded_tool_view(
                [current_by_name[name] for name in allowed_tool_names]
            )
            result["result"] = {**result_body, "tools": bounded}
            return result
        return self._json_response(response, max_bytes=max_bytes)


async def preflight_mcp_admission(
    *,
    context_id: str | None,
    principal: McpPrincipal,
    run_id: str,
    selected_tool_names: list[str] | tuple[str, ...] | None,
    mcp_required: bool,
    context_manager: McpRuntimeContextManager | None = None,
) -> McpRuntimePreflight | None:
    """Fail before persistence whenever an admitted Run requires MCP."""

    if not mcp_required:
        if context_id:
            manager = context_manager or get_mcp_runtime_context_manager()
            try:
                await manager.discard_unbound_context(context_id, principal)
            except Exception:  # noqa: BLE001 - expiry remains the final cleanup fence.
                pass
        return None
    if not context_id:
        raise McpRuntimeContextError("mcp_context_required", status_code=409)
    return await HostMcpRelay(
        context_manager=context_manager or get_mcp_runtime_context_manager(),
    ).preflight(
        context_id=context_id,
        principal=principal,
        run_id=run_id,
        selected_tool_names=selected_tool_names,
    )


@asynccontextmanager
async def runtime_context_manager_lock(
    manager: McpRuntimeContextManager,
    context_id: str,
) -> AsyncIterator[None]:
    """Expose the shared Redis mutation lock for one context to admission code."""

    async with manager._mutation_guard(context_id):
        yield
