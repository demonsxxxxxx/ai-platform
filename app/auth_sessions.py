"""Redis-backed browser auth contexts with operation fencing."""

from dataclasses import dataclass
import base64
import hashlib
import hmac
import json
import secrets
from typing import Any, Mapping

from redis.asyncio import Redis

from app.settings import get_settings


AUTH_CONTEXT_SCHEMA_VERSION = 1
AUTH_CONTEXT_KEY_PREFIX = "ai-platform:auth-context"
AUTH_OAUTH_STATE_KEY_PREFIX = "ai-platform:auth-oauth-state"


BOOTSTRAP_AUTH_CONTEXT_SCRIPT = """
-- ai-platform:auth-context-bootstrap:v1
local key = KEYS[1]
local nonce_binding = ARGV[1]
local ttl_seconds = tonumber(ARGV[2])

local raw = redis.call("GET", key)
if not raw then
  redis.call("SET", key, cjson.encode({
    schema_version = 1,
    nonce_binding = nonce_binding,
    principal = cjson.null,
    tenant_user_subject_epoch = 0,
    operation_epoch = 0,
    operation_token = "",
    operation_kind = "",
    lease_until = 0
  }), "EX", ttl_seconds)
  return cjson.encode({status = "created"})
end

local ok, record = pcall(cjson.decode, raw)
if not ok or type(record) ~= "table" or record["schema_version"] ~= 1 then
  return cjson.encode({status = "corrupt"})
end
if record["nonce_binding"] ~= nonce_binding then
  return cjson.encode({status = "corrupt"})
end
if type(record["operation_epoch"]) ~= "number"
  or type(record["tenant_user_subject_epoch"]) ~= "number"
  or type(record["operation_token"]) ~= "string"
  or type(record["operation_kind"]) ~= "string"
  or type(record["lease_until"]) ~= "number"
then
  return cjson.encode({status = "corrupt"})
end
if record["principal"] ~= cjson.null and type(record["principal"]) ~= "table" then
  return cjson.encode({status = "corrupt"})
end
return cjson.encode({status = "existing"})
"""


BEGIN_AUTH_OPERATION_SCRIPT = """
-- ai-platform:auth-context-begin:v1
local key = KEYS[1]
local lease_seconds = tonumber(ARGV[1])
local operation_token = ARGV[2]
local operation_kind = ARGV[3]
local redis_time = redis.call("TIME")
local now = tonumber(redis_time[1]) + (tonumber(redis_time[2]) / 1000000)

local raw = redis.call("GET", key)
if not raw then
  return cjson.encode({status = "missing"})
end
local ok, record = pcall(cjson.decode, raw)
if not ok or type(record) ~= "table" or record["schema_version"] ~= 1 then
  return cjson.encode({status = "corrupt"})
end
if type(record["operation_epoch"]) ~= "number"
  or type(record["tenant_user_subject_epoch"]) ~= "number"
  or type(record["operation_token"]) ~= "string"
  or type(record["operation_kind"]) ~= "string"
  or type(record["lease_until"]) ~= "number"
then
  return cjson.encode({status = "corrupt"})
end
if record["principal"] ~= cjson.null and type(record["principal"]) ~= "table" then
  return cjson.encode({status = "corrupt"})
end

record["operation_epoch"] = record["operation_epoch"] + 1
record["operation_token"] = operation_token
record["operation_kind"] = operation_kind
record["lease_until"] = now + lease_seconds
local ttl = redis.call("PTTL", key)
if ttl <= 0 then
  return cjson.encode({status = "missing"})
end
redis.call("SET", key, cjson.encode(record), "PX", ttl)
return cjson.encode({status = "begun", operation_epoch = record["operation_epoch"]})
"""


COMMIT_AUTH_OPERATION_SCRIPT = """
-- ai-platform:auth-context-commit:v1
local key = KEYS[1]
local operation_epoch = tonumber(ARGV[1])
local operation_token = ARGV[2]
local principal_json = ARGV[3]
local redis_time = redis.call("TIME")
local now = tonumber(redis_time[1]) + (tonumber(redis_time[2]) / 1000000)

local raw = redis.call("GET", key)
if not raw then
  return cjson.encode({status = "missing"})
end
local ok, record = pcall(cjson.decode, raw)
if not ok or type(record) ~= "table" or record["schema_version"] ~= 1 then
  return cjson.encode({status = "corrupt"})
end
if type(record["operation_epoch"]) ~= "number"
  or type(record["tenant_user_subject_epoch"]) ~= "number"
  or type(record["operation_token"]) ~= "string"
  or type(record["operation_kind"]) ~= "string"
  or type(record["lease_until"]) ~= "number"
then
  return cjson.encode({status = "corrupt"})
end
if record["principal"] ~= cjson.null and type(record["principal"]) ~= "table" then
  return cjson.encode({status = "corrupt"})
end
if record["operation_epoch"] ~= operation_epoch or record["operation_token"] ~= operation_token then
  return cjson.encode({status = "superseded"})
end
if record["lease_until"] <= now then
  return cjson.encode({status = "expired"})
end
local principal_ok, principal = pcall(cjson.decode, principal_json)
if not principal_ok or (principal ~= cjson.null and type(principal) ~= "table") then
  return cjson.encode({status = "corrupt"})
end

record["principal"] = principal
record["tenant_user_subject_epoch"] = record["tenant_user_subject_epoch"] + 1
record["operation_token"] = ""
record["operation_kind"] = ""
record["lease_until"] = 0
local ttl = redis.call("PTTL", key)
if ttl <= 0 then
  return cjson.encode({status = "missing"})
end
redis.call("SET", key, cjson.encode(record), "PX", ttl)
return cjson.encode({
  status = "committed",
  tenant_user_subject_epoch = record["tenant_user_subject_epoch"]
})
"""


CONSUME_OAUTH_STATE_SCRIPT = """
-- ai-platform:auth-oauth-state-consume:v1
local state_key = KEYS[1]
local context_handle = ARGV[1]
local provider = ARGV[2]

local raw = redis.call("GET", state_key)
if not raw then
  return cjson.encode({status = "missing"})
end
redis.call("DEL", state_key)
local ok, record = pcall(cjson.decode, raw)
if not ok or type(record) ~= "table" then
  return cjson.encode({status = "corrupt"})
end
if record["context_handle"] ~= context_handle or record["provider"] ~= provider then
  return cjson.encode({status = "invalid"})
end
if type(record["operation_epoch"]) ~= "number" or type(record["operation_token"]) ~= "string" then
  return cjson.encode({status = "corrupt"})
end
return cjson.encode({
  status = "consumed",
  operation_epoch = record["operation_epoch"],
  operation_token = record["operation_token"]
})
"""


@dataclass(frozen=True)
class AuthOperation:
    """One server-owned mutation lease for a browser auth context."""

    context_handle: str
    epoch: int
    token: str
    kind: str


class AuthContextError(RuntimeError):
    """A safe auth-context failure that can be projected to browser clients."""

    def __init__(self, code: str, status_code: int):
        super().__init__(code)
        self.code = code
        self.status_code = status_code


def get_redis() -> Redis:
    """Return an isolated Redis client for one auth-context operation."""

    return Redis.from_url(get_settings().redis_url, decode_responses=True)


def _settings_value(settings: Any, name: str, default: Any) -> Any:
    return getattr(settings, name, default)


def _context_secret(settings: Any) -> str:
    secret = str(
        _settings_value(settings, "auth_context_secret", "")
        or _settings_value(settings, "ai_session_secret", "")
    ).strip()
    if len(secret.encode("utf-8")) < 32:
        raise AuthContextError("auth_context_unavailable", 503)
    return secret


def _urlsafe_digest(secret: str, label: str, value: str) -> str:
    raw = hmac.new(
        secret.encode("utf-8"),
        f"{label}:{value}".encode("utf-8"),
        hashlib.sha256,
    ).digest()
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def auth_context_handle_for_nonce(nonce: str, settings: Any | None = None) -> str:
    """Derive the stable opaque cookie handle from a browser-generated nonce."""

    current_settings = settings or get_settings()
    return f"v1.{_urlsafe_digest(_context_secret(current_settings), 'handle', nonce)}"


def _nonce_binding(nonce: str, settings: Any) -> str:
    return _urlsafe_digest(_context_secret(settings), "binding", nonce)


def _context_key(context_handle: str) -> str:
    return f"{AUTH_CONTEXT_KEY_PREFIX}:{context_handle}"


def _oauth_state_key(state: str) -> str:
    return f"{AUTH_OAUTH_STATE_KEY_PREFIX}:{state}"


def _context_ttl_seconds(settings: Any) -> int:
    return max(
        1,
        int(
            _settings_value(
                settings,
                "auth_context_max_age_seconds",
                _settings_value(settings, "ai_session_max_age_seconds", 8 * 60 * 60),
            )
        ),
    )


def _operation_lease_seconds(settings: Any) -> int:
    return max(1, int(_settings_value(settings, "auth_context_lease_seconds", 90)))


def _decode_script_result(raw_result: object) -> dict[str, Any]:
    if isinstance(raw_result, bytes):
        raw_result = raw_result.decode("utf-8", errors="replace")
    if isinstance(raw_result, dict):
        return raw_result
    if not isinstance(raw_result, str):
        return {"status": "corrupt"}
    try:
        parsed = json.loads(raw_result)
    except json.JSONDecodeError:
        return {"status": "corrupt"}
    return parsed if isinstance(parsed, dict) else {"status": "corrupt"}


def _raise_for_store_status(operation: str, status: str) -> None:
    if status == "missing":
        raise AuthContextError("auth_context_missing", 401)
    if status == "superseded":
        raise AuthContextError("auth_operation_superseded", 409)
    if status == "expired":
        raise AuthContextError("auth_operation_expired", 409)
    if operation == "oauth" and status == "invalid":
        raise AuthContextError("auth_operation_superseded", 409)
    raise AuthContextError("auth_context_unavailable", 503)


async def _eval(script: str, keys: list[str], args: list[object]) -> dict[str, Any]:
    redis = get_redis()
    try:
        return _decode_script_result(await redis.eval(script, len(keys), *keys, *args))
    except AuthContextError:
        raise
    except Exception as exc:
        raise AuthContextError("auth_context_unavailable", 503) from exc
    finally:
        try:
            await redis.aclose()
        except Exception:
            pass


async def bootstrap_auth_context(context_handle: str, nonce: str, settings: Any | None = None) -> str:
    """Create or verify a stable browser context before writing its cookie."""

    current_settings = settings or get_settings()
    result = await _eval(
        BOOTSTRAP_AUTH_CONTEXT_SCRIPT,
        [_context_key(context_handle)],
        [
            _nonce_binding(nonce, current_settings),
            _context_ttl_seconds(current_settings),
        ],
    )
    status = str(result.get("status") or "")
    if status in {"created", "existing"}:
        return status
    _raise_for_store_status("bootstrap", status)
    raise AssertionError("unreachable")


async def begin_auth_operation(
    context_handle: str,
    kind: str,
    settings: Any | None = None,
) -> AuthOperation:
    """Fence prior auth mutations and lease the newest server-owned operation."""

    current_settings = settings or get_settings()
    token = secrets.token_urlsafe(32)
    result = await _eval(
        BEGIN_AUTH_OPERATION_SCRIPT,
        [_context_key(context_handle)],
        [
            _operation_lease_seconds(current_settings),
            token,
            kind,
        ],
    )
    status = str(result.get("status") or "")
    if status != "begun":
        _raise_for_store_status("begin", status)
    try:
        epoch = int(result["operation_epoch"])
    except (KeyError, TypeError, ValueError) as exc:
        raise AuthContextError("auth_context_unavailable", 503) from exc
    if epoch < 1:
        raise AuthContextError("auth_context_unavailable", 503)
    return AuthOperation(context_handle=context_handle, epoch=epoch, token=token, kind=kind)


def principal_snapshot(principal: Any) -> dict[str, object]:
    """Serialize a server-derived principal for the Redis context record."""

    return {
        "user_id": str(principal.user_id),
        "display_name": str(principal.display_name),
        "tenant_id": str(principal.tenant_id),
        "department_id": str(principal.department_id),
        "roles": [str(role) for role in principal.roles],
        "permissions": [str(permission) for permission in principal.permissions],
        "source": str(principal.source),
    }


async def commit_auth_operation(
    operation: AuthOperation,
    principal: Mapping[str, object] | None,
) -> str:
    """Commit a principal only if the operation lease still owns its context."""

    result = await _eval(
        COMMIT_AUTH_OPERATION_SCRIPT,
        [_context_key(operation.context_handle)],
        [
            operation.epoch,
            operation.token,
            json.dumps(principal, ensure_ascii=False, separators=(",", ":")),
        ],
    )
    status = str(result.get("status") or "")
    if status == "committed":
        return status
    if status in {"superseded", "expired", "missing"}:
        return status
    _raise_for_store_status("commit", status)
    raise AssertionError("unreachable")


def _valid_snapshot(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None
    required_strings = ("user_id", "display_name", "tenant_id", "department_id", "source")
    if any(not isinstance(value.get(key), str) for key in required_strings):
        return None
    if not isinstance(value.get("roles"), list) or not isinstance(value.get("permissions"), list):
        return None
    if any(not isinstance(item, str) for item in value["roles"] + value["permissions"]):
        return None
    return {
        "user_id": value["user_id"],
        "display_name": value["display_name"],
        "tenant_id": value["tenant_id"],
        "department_id": value["department_id"],
        "roles": list(value["roles"]),
        "permissions": list(value["permissions"]),
        "source": value["source"],
    }


async def principal_for_context(
    context_handle: str,
    settings: Any | None = None,
) -> dict[str, object] | None:
    """Read the current principal for a browser context without any migration."""

    del settings
    redis = get_redis()
    try:
        raw = await redis.get(_context_key(context_handle))
    except Exception as exc:
        raise AuthContextError("auth_context_unavailable", 503) from exc
    finally:
        try:
            await redis.aclose()
        except Exception:
            pass
    if raw is None:
        raise AuthContextError("auth_context_missing", 401)
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    try:
        record = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        raise AuthContextError("auth_context_unavailable", 503) from exc
    if (
        not isinstance(record, dict)
        or record.get("schema_version") != AUTH_CONTEXT_SCHEMA_VERSION
        or not isinstance(record.get("nonce_binding"), str)
        or not isinstance(record.get("operation_epoch"), int)
        or not isinstance(record.get("tenant_user_subject_epoch"), int)
        or not isinstance(record.get("operation_token"), str)
        or not isinstance(record.get("operation_kind"), str)
        or not isinstance(record.get("lease_until"), (int, float))
    ):
        raise AuthContextError("auth_context_unavailable", 503)
    principal = record.get("principal")
    if principal is None:
        return None
    snapshot = _valid_snapshot(principal)
    if snapshot is None:
        raise AuthContextError("auth_context_unavailable", 503)
    return snapshot


async def issue_oauth_state(
    context_handle: str,
    provider: str,
    operation: AuthOperation,
    settings: Any | None = None,
) -> str:
    """Persist an opaque OAuth callback state bound to one context lease."""

    current_settings = settings or get_settings()
    if operation.context_handle != context_handle or operation.kind != f"oauth:{provider}":
        raise AuthContextError("auth_operation_superseded", 409)
    state = secrets.token_urlsafe(32)
    record = {
        "context_handle": context_handle,
        "provider": provider,
        "operation_epoch": operation.epoch,
        "operation_token": operation.token,
    }
    redis = get_redis()
    try:
        await redis.set(
            _oauth_state_key(state),
            json.dumps(record, separators=(",", ":")),
            ex=_operation_lease_seconds(current_settings),
        )
    except Exception as exc:
        raise AuthContextError("auth_context_unavailable", 503) from exc
    finally:
        try:
            await redis.aclose()
        except Exception:
            pass
    return state


async def consume_oauth_state(
    context_handle: str,
    provider: str,
    state: str,
    settings: Any | None = None,
) -> AuthOperation:
    """Consume callback state once and recover its server-owned operation lease."""

    del settings
    result = await _eval(
        CONSUME_OAUTH_STATE_SCRIPT,
        [_oauth_state_key(state)],
        [context_handle, provider],
    )
    status = str(result.get("status") or "")
    if status != "consumed":
        _raise_for_store_status("oauth", status)
    try:
        epoch = int(result["operation_epoch"])
        token = str(result["operation_token"])
    except (KeyError, TypeError, ValueError) as exc:
        raise AuthContextError("auth_context_unavailable", 503) from exc
    if epoch < 1 or not token:
        raise AuthContextError("auth_context_unavailable", 503)
    return AuthOperation(
        context_handle=context_handle,
        epoch=epoch,
        token=token,
        kind=f"oauth:{provider}",
    )
