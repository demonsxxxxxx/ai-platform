"""Redis-backed browser auth contexts with operation fencing."""

from dataclasses import dataclass
import base64
import hashlib
import hmac
import json
import math
import re
import secrets
from typing import Any, Mapping

from redis.asyncio import Redis

from app.settings import get_settings


AUTH_CONTEXT_SCHEMA_VERSION = 1
AUTH_CONTEXT_V2_SCHEMA_VERSION = 2
AUTH_CONTEXT_MAX_EPOCH = (2**53) - 1
AUTH_CONTEXT_KEY_PREFIX = "ai-platform:auth-context"
AUTH_BROWSER_AUTHORITY_KEY_PREFIX = "ai-platform:auth-browser-authority"
AUTH_OAUTH_STATE_KEY_PREFIX = "ai-platform:auth-oauth-state"
AUTH_CONTEXT_V2_COOKIE_PREFIX = "v2"
AUTH_CONTEXT_V2_INCARNATION_LENGTH = 43
AUTH_CONTEXT_V2_TICKET_LENGTH = 43


BOOTSTRAP_AUTH_CONTEXT_SCRIPT = """
-- ai-platform:auth-context-bootstrap:v1
local key = KEYS[1]
local nonce_binding = ARGV[1]
local ttl_seconds = tonumber(ARGV[2])
local request_has_matching_context = ARGV[3] == "1"
local MAX_EPOCH = 9007199254740991

local function is_finite_number(value)
  return type(value) == "number"
    and value == value
    and value ~= math.huge
    and value ~= -math.huge
end

local function is_valid_epoch(value)
  return is_finite_number(value)
    and value >= 0
    and value <= MAX_EPOCH
    and value == math.floor(value)
end

local function is_valid_context_record(record)
  return type(record) == "table"
    and is_valid_epoch(record["schema_version"])
    and record["schema_version"] == 1
    and type(record["nonce_binding"]) == "string"
    and is_valid_epoch(record["operation_epoch"])
    and is_valid_epoch(record["tenant_user_subject_epoch"])
    and type(record["operation_token"]) == "string"
    and type(record["operation_kind"]) == "string"
    and is_finite_number(record["lease_until"])
    and record["lease_until"] >= 0
    and (record["principal"] == cjson.null or type(record["principal"]) == "table")
end

local function is_pristine_anonymous(record)
  return record["principal"] == cjson.null
    and record["tenant_user_subject_epoch"] == 0
    and record["operation_epoch"] == 0
    and record["operation_token"] == ""
    and record["operation_kind"] == ""
    and record["lease_until"] == 0
end

if not is_valid_epoch(ttl_seconds) or ttl_seconds < 1 then
  return cjson.encode({status = "corrupt"})
end

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
if not ok or not is_valid_context_record(record) then
  return cjson.encode({status = "corrupt"})
end
if record["nonce_binding"] ~= nonce_binding then
  return cjson.encode({status = "corrupt"})
end
if request_has_matching_context or is_pristine_anonymous(record) then
  return cjson.encode({status = "existing"})
end
return cjson.encode({status = "rebootstrap_required"})
"""


BEGIN_AUTH_OPERATION_SCRIPT = """
-- ai-platform:auth-context-begin:v1
local key = KEYS[1]
local lease_seconds = tonumber(ARGV[1])
local operation_token = ARGV[2]
local operation_kind = ARGV[3]
local redis_time = redis.call("TIME")
local now = tonumber(redis_time[1]) + (tonumber(redis_time[2]) / 1000000)
local MAX_EPOCH = 9007199254740991

local function is_finite_number(value)
  return type(value) == "number"
    and value == value
    and value ~= math.huge
    and value ~= -math.huge
end

local function is_valid_epoch(value)
  return is_finite_number(value)
    and value >= 0
    and value <= MAX_EPOCH
    and value == math.floor(value)
end

local function is_valid_context_record(record)
  return type(record) == "table"
    and is_valid_epoch(record["schema_version"])
    and record["schema_version"] == 1
    and type(record["nonce_binding"]) == "string"
    and is_valid_epoch(record["operation_epoch"])
    and is_valid_epoch(record["tenant_user_subject_epoch"])
    and type(record["operation_token"]) == "string"
    and type(record["operation_kind"]) == "string"
    and is_finite_number(record["lease_until"])
    and record["lease_until"] >= 0
    and (record["principal"] == cjson.null or type(record["principal"]) == "table")
end

if not is_valid_epoch(lease_seconds) or lease_seconds < 1
  or type(operation_token) ~= "string" or operation_token == ""
  or type(operation_kind) ~= "string" or operation_kind == ""
then
  return cjson.encode({status = "corrupt"})
end

local raw = redis.call("GET", key)
if not raw then
  return cjson.encode({status = "missing"})
end
local ok, record = pcall(cjson.decode, raw)
if not ok or not is_valid_context_record(record) then
  return cjson.encode({status = "corrupt"})
end
if record["operation_epoch"] >= MAX_EPOCH then
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
local MAX_EPOCH = 9007199254740991

local function is_finite_number(value)
  return type(value) == "number"
    and value == value
    and value ~= math.huge
    and value ~= -math.huge
end

local function is_valid_epoch(value)
  return is_finite_number(value)
    and value >= 0
    and value <= MAX_EPOCH
    and value == math.floor(value)
end

local function is_valid_context_record(record)
  return type(record) == "table"
    and is_valid_epoch(record["schema_version"])
    and record["schema_version"] == 1
    and type(record["nonce_binding"]) == "string"
    and is_valid_epoch(record["operation_epoch"])
    and is_valid_epoch(record["tenant_user_subject_epoch"])
    and type(record["operation_token"]) == "string"
    and type(record["operation_kind"]) == "string"
    and is_finite_number(record["lease_until"])
    and record["lease_until"] >= 0
    and (record["principal"] == cjson.null or type(record["principal"]) == "table")
end

if not is_valid_epoch(operation_epoch) or operation_epoch < 1
  or type(operation_token) ~= "string" or operation_token == ""
then
  return cjson.encode({status = "corrupt"})
end

local raw = redis.call("GET", key)
if not raw then
  return cjson.encode({status = "missing"})
end
local ok, record = pcall(cjson.decode, raw)
if not ok or not is_valid_context_record(record) then
  return cjson.encode({status = "corrupt"})
end
if record["tenant_user_subject_epoch"] >= MAX_EPOCH then
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
local MAX_EPOCH = 9007199254740991

local function is_finite_number(value)
  return type(value) == "number"
    and value == value
    and value ~= math.huge
    and value ~= -math.huge
end

local function is_valid_epoch(value)
  return is_finite_number(value)
    and value >= 0
    and value <= MAX_EPOCH
    and value == math.floor(value)
end

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
if not is_valid_epoch(record["operation_epoch"]) or record["operation_epoch"] < 1
  or type(record["operation_token"]) ~= "string" or record["operation_token"] == ""
then
  return cjson.encode({status = "corrupt"})
end
return cjson.encode({
  status = "consumed",
  operation_epoch = record["operation_epoch"],
  operation_token = record["operation_token"]
})
"""


V2_LUA_HELPERS = """
local MAX_EPOCH = 9007199254740991

local function finite(value)
  return type(value) == "number" and value == value
    and value ~= math.huge and value ~= -math.huge
end

local function epoch(value)
  return finite(value) and value >= 0 and value <= MAX_EPOCH
    and value == math.floor(value)
end

local function valid_v1(record)
  return type(record) == "table" and record["schema_version"] == 1
    and type(record["nonce_binding"]) == "string"
    and epoch(record["operation_epoch"])
    and epoch(record["tenant_user_subject_epoch"])
    and type(record["operation_token"]) == "string"
    and type(record["operation_kind"]) == "string"
    and finite(record["lease_until"]) and record["lease_until"] >= 0
    and (record["principal"] == cjson.null or type(record["principal"]) == "table")
end

local function valid_v2(record)
  return type(record) == "table" and record["schema_version"] == 2
    and record["protocol_version"] == 2
    and type(record["nonce_binding"]) == "string"
    and type(record["incarnation_digest"]) == "string"
    and epoch(record["generation"]) and record["generation"] >= 1
    and epoch(record["operation_epoch"])
    and epoch(record["tenant_user_subject_epoch"])
    and type(record["operation_token"]) == "string"
    and type(record["operation_kind"]) == "string"
    and finite(record["lease_until"]) and record["lease_until"] >= 0
    and (record["principal"] == cjson.null or type(record["principal"]) == "table")
end

local function valid_authority(record)
  if type(record) ~= "table" then return false end
  local has_ticket_digest = record["rotation_ticket_digest"] ~= nil
  local has_ticket_generation = record["rotation_ticket_generation"] ~= nil
  local has_ticket_deadline = record["rotation_ticket_deadline"] ~= nil
  local ticket_absent = not has_ticket_digest and not has_ticket_generation and not has_ticket_deadline
  local ticket_valid = has_ticket_digest and has_ticket_generation and has_ticket_deadline
    and type(record["rotation_ticket_digest"]) == "string"
    and epoch(record["rotation_ticket_generation"])
    and record["rotation_ticket_generation"] >= 1
    and record["rotation_ticket_generation"] == record["generation"]
    and finite(record["rotation_ticket_deadline"])
    and record["rotation_ticket_deadline"] >= 0
  return record["schema_version"] == 2
    and type(record["incarnation_digest"]) == "string"
    and epoch(record["generation"]) and record["generation"] >= 1
    and type(record["context_handle"]) == "string"
    and (ticket_absent or ticket_valid)
end

local function decode(raw)
  if not raw then return nil end
  local ok, record = pcall(cjson.decode, raw)
  if not ok then return false end
  return record
end

local function same_v2(record, incarnation_digest, generation, context_handle)
  return valid_v2(record)
    and record["incarnation_digest"] == incarnation_digest
    and record["generation"] == generation
    and record["context_handle"] == nil -- context records deliberately do not trust a client handle field
end

local function same_authority(authority, context, incarnation_digest, generation, context_handle)
  return valid_authority(authority) and valid_v2(context)
    and authority["incarnation_digest"] == incarnation_digest
    and authority["generation"] == generation
    and authority["context_handle"] == context_handle
    and context["incarnation_digest"] == incarnation_digest
    and context["generation"] == generation
end

local function pristine(record)
  return record["principal"] == cjson.null
    and record["tenant_user_subject_epoch"] == 0
    and record["operation_epoch"] == 0
    and record["operation_token"] == ""
    and record["operation_kind"] == ""
    and record["lease_until"] == 0
end

local function pttl_live(key)
  local ttl = redis.call("PTTL", key)
  if ttl <= 0 then return nil end
  return ttl
end

local function ttl_consistent(authority_key, context_key)
  local authority_ttl = pttl_live(authority_key)
  local context_ttl = pttl_live(context_key)
  if not authority_ttl or not context_ttl then return nil end
  -- Both keys are always written in one Lua invocation. A modest tolerance
  -- avoids false failures from Redis millisecond accounting while still
  -- rejecting independently renewed or corrupt state.
  if math.abs(authority_ttl - context_ttl) > 1000 then return nil end
  return math.min(authority_ttl, context_ttl)
end
"""


V2_BOOTSTRAP_AUTH_CONTEXT_SCRIPT = """
-- ai-platform:auth-context-bootstrap:v2
""" + V2_LUA_HELPERS + """
local authority_key = KEYS[1]
local context_key = KEYS[2]
local incarnation_digest = ARGV[1]
local generation = tonumber(ARGV[2])
local context_handle = ARGV[3]
local nonce_binding = ARGV[4]
local ttl_seconds = tonumber(ARGV[5])
local cookie_kind = ARGV[6]
local cookie_incarnation_digest = ARGV[7]
local cookie_generation = tonumber(ARGV[8])
local cookie_context_handle = ARGV[9]
local ticket_digest = ARGV[10]
local ticket_seconds = tonumber(ARGV[11])

if type(incarnation_digest) ~= "string" or incarnation_digest == ""
  or not epoch(generation) or generation < 1
  or type(context_handle) ~= "string" or context_handle == ""
  or type(nonce_binding) ~= "string" or nonce_binding == ""
  or not epoch(ttl_seconds) or ttl_seconds < 1
  or (cookie_kind ~= "none" and cookie_kind ~= "v1_matching"
    and cookie_kind ~= "v1_conflict" and cookie_kind ~= "v2"
    and cookie_kind ~= "invalid")
then
  return cjson.encode({status = "corrupt"})
end

local authority = decode(redis.call("GET", authority_key))
if authority == false then return cjson.encode({status = "corrupt"}) end
if not authority then
  if cookie_kind == "v2" or cookie_kind == "v1_conflict" or cookie_kind == "invalid" then
    return cjson.encode({status = "stale"})
  end
  if generation ~= 1 then return cjson.encode({status = "generation_gap"}) end
  local existing = decode(redis.call("GET", context_key))
  if existing == false then return cjson.encode({status = "corrupt"}) end
  local effective_ttl = ttl_seconds * 1000
  if existing then
    if not valid_v1(existing) or existing["nonce_binding"] ~= nonce_binding then
      return cjson.encode({status = "stale"})
    end
    if cookie_kind ~= "v1_matching" and not pristine(existing) then
      return cjson.encode({status = "migration_conflict"})
    end
    effective_ttl = pttl_live(context_key)
    if not effective_ttl then return cjson.encode({status = "missing"}) end
  else
    existing = {
      schema_version = 1,
      nonce_binding = nonce_binding,
      principal = cjson.null,
      tenant_user_subject_epoch = 0,
      operation_epoch = 0,
      operation_token = "",
      operation_kind = "",
      lease_until = 0
    }
  end
  existing["schema_version"] = 2
  existing["protocol_version"] = 2
  existing["incarnation_digest"] = incarnation_digest
  existing["generation"] = generation
  redis.call("SET", context_key, cjson.encode(existing), "PX", effective_ttl)
  redis.call("SET", authority_key, cjson.encode({
    schema_version = 2,
    incarnation_digest = incarnation_digest,
    generation = generation,
    context_handle = context_handle
  }), "PX", effective_ttl)
  return cjson.encode({
    status = existing["principal"] == cjson.null and "created" or "migrated",
    ttl_milliseconds = effective_ttl
  })
end

local context = decode(redis.call("GET", context_key))
if authority == false or not valid_authority(authority) then
  return cjson.encode({status = "corrupt"})
end
if not context then return cjson.encode({status = "generation_conflict"}) end
if context == false or not valid_v2(context) then return cjson.encode({status = "corrupt"}) end
local ttl = ttl_consistent(authority_key, context_key)
if not ttl then return cjson.encode({status = "stale"}) end
if authority["incarnation_digest"] ~= incarnation_digest then
  return cjson.encode({status = "stale"})
end
if generation < authority["generation"] then
  return cjson.encode({status = "stale"})
end
if generation > authority["generation"] then
  return cjson.encode({status = "generation_gap"})
end
if authority["context_handle"] ~= context_handle then
  local current_cookie = cookie_kind == "v2"
    and cookie_incarnation_digest == incarnation_digest
    and cookie_generation == generation
    and cookie_context_handle == authority["context_handle"]
  if not current_cookie or type(ticket_digest) ~= "string" or ticket_digest == ""
    or not epoch(ticket_seconds) or ticket_seconds < 1
  then
    return cjson.encode({status = "generation_conflict"})
  end
  local redis_time = redis.call("TIME")
  authority["rotation_ticket_digest"] = ticket_digest
  authority["rotation_ticket_generation"] = generation
  authority["rotation_ticket_deadline"] = tonumber(redis_time[1]) + ticket_seconds
  redis.call("SET", authority_key, cjson.encode(authority), "PX", ttl)
  return cjson.encode({status = "rebootstrap_required"})
end
if not same_authority(authority, context, incarnation_digest, generation, context_handle)
  or context["nonce_binding"] ~= nonce_binding
then
  return cjson.encode({status = "stale"})
end
local current_cookie = cookie_kind == "v2"
  and cookie_incarnation_digest == incarnation_digest
  and cookie_generation == generation
  and cookie_context_handle == context_handle
return cjson.encode({
  status = current_cookie and "existing" or "repair",
  ttl_milliseconds = ttl
})
"""


V2_ROTATE_AUTH_CONTEXT_SCRIPT = """
-- ai-platform:auth-context-rotate:v2
""" + V2_LUA_HELPERS + """
local authority_key = KEYS[1]
local old_context_key = KEYS[2]
local new_context_key = KEYS[3]
local incarnation_digest = ARGV[1]
local old_generation = tonumber(ARGV[2])
local old_context_handle = ARGV[3]
local new_context_handle = ARGV[4]
local nonce_binding = ARGV[5]
local ttl_seconds = tonumber(ARGV[6])
local ticket_digest = ARGV[7]

if authority_key == old_context_key or authority_key == new_context_key
  or old_context_key == new_context_key
  or type(incarnation_digest) ~= "string" or incarnation_digest == ""
  or not epoch(old_generation) or old_generation < 1
  or type(old_context_handle) ~= "string" or old_context_handle == ""
  or type(new_context_handle) ~= "string" or new_context_handle == ""
  or type(nonce_binding) ~= "string" or nonce_binding == ""
  or not epoch(ttl_seconds) or ttl_seconds < 1
  or type(ticket_digest) ~= "string" or ticket_digest == ""
then
  return cjson.encode({status = "corrupt"})
end

local authority = decode(redis.call("GET", authority_key))
local old_context = decode(redis.call("GET", old_context_key))
if authority == false or old_context == false
  or not valid_authority(authority) or not valid_v2(old_context)
then
  return cjson.encode({status = "stale"})
end
local ttl = ttl_consistent(authority_key, old_context_key)
if not ttl
  or not same_authority(authority, old_context, incarnation_digest, old_generation, old_context_handle)
then
  return cjson.encode({status = "stale"})
end
local redis_time = redis.call("TIME")
local now = tonumber(redis_time[1]) + (tonumber(redis_time[2]) / 1000000)
if authority["rotation_ticket_digest"] ~= ticket_digest
  or authority["rotation_ticket_generation"] ~= old_generation
  or not finite(authority["rotation_ticket_deadline"])
  or authority["rotation_ticket_deadline"] < now
then
  return cjson.encode({status = "invalid_ticket"})
end
if redis.call("GET", new_context_key) then
  return cjson.encode({status = "generation_conflict"})
end
local new_generation = old_generation + 1
if new_generation > MAX_EPOCH then return cjson.encode({status = "corrupt"}) end
local new_context = {
  schema_version = 2,
  protocol_version = 2,
  incarnation_digest = incarnation_digest,
  generation = new_generation,
  nonce_binding = nonce_binding,
  principal = cjson.null,
  tenant_user_subject_epoch = 0,
  operation_epoch = 0,
  operation_token = "",
  operation_kind = "",
  lease_until = 0
}
-- Rotation is a fencing transition, never a session renewal.  Preserve the
-- authoritative remaining PTTL rather than resetting it to the configured
-- maximum age supplied by the caller.
redis.call("SET", new_context_key, cjson.encode(new_context), "PX", ttl)
redis.call("SET", authority_key, cjson.encode({
  schema_version = 2,
  incarnation_digest = incarnation_digest,
  generation = new_generation,
  context_handle = new_context_handle
}), "PX", ttl)
return cjson.encode({
  status = "rotated",
  generation = new_generation,
  ttl_milliseconds = ttl
})
"""


V2_RECONCILE_ROTATION_SCRIPT = """
-- ai-platform:auth-context-rotate-reconcile:v2
""" + V2_LUA_HELPERS + """
local authority_key = KEYS[1]
local target_context_key = KEYS[2]
local incarnation_digest = ARGV[1]
local generation = tonumber(ARGV[2])
local context_handle = ARGV[3]
if type(incarnation_digest) ~= "string" or incarnation_digest == ""
  or not epoch(generation) or generation < 1
  or type(context_handle) ~= "string" or context_handle == ""
then return cjson.encode({status = "corrupt"}) end

local authority = decode(redis.call("GET", authority_key))
local context = decode(redis.call("GET", target_context_key))
if authority == false or context == false
  or not valid_authority(authority) or not valid_v2(context)
  or not ttl_consistent(authority_key, target_context_key)
  or not same_authority(authority, context, incarnation_digest, generation, context_handle)
then return cjson.encode({status = "stale"}) end

-- The signed cookie and both Redis records already prove this exact target.
-- Do not consume or reissue a ticket, refresh a TTL, or emit a Set-Cookie.
return cjson.encode({status = "reconciled"})
"""


V2_ISSUE_ROTATION_TICKET_SCRIPT = """
-- ai-platform:auth-context-rotation-ticket:v2
""" + V2_LUA_HELPERS + """
local authority_key = KEYS[1]
local context_key = KEYS[2]
local incarnation_digest = ARGV[1]
local generation = tonumber(ARGV[2])
local context_handle = ARGV[3]
local ticket_digest = ARGV[4]
local ticket_seconds = tonumber(ARGV[5])
if type(incarnation_digest) ~= "string" or incarnation_digest == ""
  or not epoch(generation) or generation < 1
  or type(context_handle) ~= "string" or context_handle == ""
  or type(ticket_digest) ~= "string" or ticket_digest == ""
  or not epoch(ticket_seconds) or ticket_seconds < 1
then return cjson.encode({status = "corrupt"}) end
local authority = decode(redis.call("GET", authority_key))
local context = decode(redis.call("GET", context_key))
if authority == false or context == false
  or not valid_authority(authority) or not valid_v2(context)
  or not same_authority(authority, context, incarnation_digest, generation, context_handle)
then return cjson.encode({status = "stale"}) end
local ttl = ttl_consistent(authority_key, context_key)
if not ttl then return cjson.encode({status = "stale"}) end
local redis_time = redis.call("TIME")
authority["rotation_ticket_digest"] = ticket_digest
authority["rotation_ticket_generation"] = generation
authority["rotation_ticket_deadline"] = tonumber(redis_time[1]) + ticket_seconds
redis.call("SET", authority_key, cjson.encode(authority), "PX", ttl)
return cjson.encode({status = "issued"})
"""


V2_PRINCIPAL_SNAPSHOT_SCRIPT = """
-- ai-platform:auth-context-principal:v2
""" + V2_LUA_HELPERS + """
local authority_key = KEYS[1]
local context_key = KEYS[2]
local incarnation_digest = ARGV[1]
local generation = tonumber(ARGV[2])
local context_handle = ARGV[3]
local authority = decode(redis.call("GET", authority_key))
local context = decode(redis.call("GET", context_key))
if authority == false or context == false
  or not valid_authority(authority) or not valid_v2(context)
  or not ttl_consistent(authority_key, context_key)
  or not same_authority(authority, context, incarnation_digest, generation, context_handle)
then
  return cjson.encode({status = "stale"})
end
return cjson.encode({status = "principal", principal = context["principal"]})
"""


V2_BEGIN_AUTH_OPERATION_SCRIPT = """
-- ai-platform:auth-context-begin:v2
""" + V2_LUA_HELPERS + """
local authority_key = KEYS[1]
local context_key = KEYS[2]
local incarnation_digest = ARGV[1]
local generation = tonumber(ARGV[2])
local context_handle = ARGV[3]
local lease_seconds = tonumber(ARGV[4])
local operation_token = ARGV[5]
local operation_kind = ARGV[6]
if type(incarnation_digest) ~= "string" or incarnation_digest == ""
  or not epoch(generation) or generation < 1
  or type(context_handle) ~= "string" or context_handle == ""
  or not epoch(lease_seconds) or lease_seconds < 1
  or type(operation_token) ~= "string" or operation_token == ""
  or type(operation_kind) ~= "string" or operation_kind == ""
then return cjson.encode({status = "corrupt"}) end
local authority = decode(redis.call("GET", authority_key))
local context = decode(redis.call("GET", context_key))
if authority == false or context == false
  or not valid_authority(authority) or not valid_v2(context)
  or not ttl_consistent(authority_key, context_key)
  or not same_authority(authority, context, incarnation_digest, generation, context_handle)
then return cjson.encode({status = "stale"}) end
if context["operation_epoch"] >= MAX_EPOCH then return cjson.encode({status = "corrupt"}) end
local redis_time = redis.call("TIME")
local now = tonumber(redis_time[1]) + (tonumber(redis_time[2]) / 1000000)
context["operation_epoch"] = context["operation_epoch"] + 1
context["operation_token"] = operation_token
context["operation_kind"] = operation_kind
context["lease_until"] = now + lease_seconds
local ttl = pttl_live(context_key)
if not ttl then return cjson.encode({status = "missing"}) end
redis.call("SET", context_key, cjson.encode(context), "PX", ttl)
return cjson.encode({status = "begun", operation_epoch = context["operation_epoch"]})
"""


V2_COMMIT_AUTH_OPERATION_SCRIPT = """
-- ai-platform:auth-context-commit:v2
""" + V2_LUA_HELPERS + """
local authority_key = KEYS[1]
local context_key = KEYS[2]
local incarnation_digest = ARGV[1]
local generation = tonumber(ARGV[2])
local context_handle = ARGV[3]
local operation_epoch = tonumber(ARGV[4])
local operation_token = ARGV[5]
local principal_json = ARGV[6]
if type(incarnation_digest) ~= "string" or incarnation_digest == ""
  or not epoch(generation) or generation < 1
  or type(context_handle) ~= "string" or context_handle == ""
  or not epoch(operation_epoch) or operation_epoch < 1
  or type(operation_token) ~= "string" or operation_token == ""
then return cjson.encode({status = "corrupt"}) end
local authority = decode(redis.call("GET", authority_key))
local context = decode(redis.call("GET", context_key))
if authority == false or context == false
  or not valid_authority(authority) or not valid_v2(context)
  or not ttl_consistent(authority_key, context_key)
  or not same_authority(authority, context, incarnation_digest, generation, context_handle)
then return cjson.encode({status = "stale"}) end
if context["operation_epoch"] ~= operation_epoch or context["operation_token"] ~= operation_token then
  return cjson.encode({status = "superseded"})
end
local redis_time = redis.call("TIME")
local now = tonumber(redis_time[1]) + (tonumber(redis_time[2]) / 1000000)
if context["lease_until"] <= now then return cjson.encode({status = "expired"}) end
if context["tenant_user_subject_epoch"] >= MAX_EPOCH then return cjson.encode({status = "corrupt"}) end
local ok, principal = pcall(cjson.decode, principal_json)
if not ok or (principal ~= cjson.null and type(principal) ~= "table") then
  return cjson.encode({status = "corrupt"})
end
context["principal"] = principal
context["tenant_user_subject_epoch"] = context["tenant_user_subject_epoch"] + 1
context["operation_token"] = ""
context["operation_kind"] = ""
context["lease_until"] = 0
local ttl = pttl_live(context_key)
if not ttl then return cjson.encode({status = "missing"}) end
redis.call("SET", context_key, cjson.encode(context), "PX", ttl)
return cjson.encode({status = "committed", tenant_user_subject_epoch = context["tenant_user_subject_epoch"]})
"""


V2_ISSUE_OAUTH_STATE_SCRIPT = """
-- ai-platform:auth-oauth-state-issue:v2
""" + V2_LUA_HELPERS + """
local authority_key = KEYS[1]
local context_key = KEYS[2]
local state_key = KEYS[3]
local incarnation_digest = ARGV[1]
local generation = tonumber(ARGV[2])
local context_handle = ARGV[3]
local provider = ARGV[4]
local operation_epoch = tonumber(ARGV[5])
local operation_token = ARGV[6]
local state_json = ARGV[7]
local state_ttl = tonumber(ARGV[8])
if type(incarnation_digest) ~= "string" or incarnation_digest == ""
  or not epoch(generation) or generation < 1
  or type(context_handle) ~= "string" or context_handle == ""
  or type(provider) ~= "string" or provider == ""
  or not epoch(operation_epoch) or operation_epoch < 1
  or type(operation_token) ~= "string" or operation_token == ""
  or not epoch(state_ttl) or state_ttl < 1
then return cjson.encode({status = "corrupt"}) end
local authority = decode(redis.call("GET", authority_key))
local context = decode(redis.call("GET", context_key))
if authority == false or context == false
  or not valid_authority(authority) or not valid_v2(context)
  or not ttl_consistent(authority_key, context_key)
  or not same_authority(authority, context, incarnation_digest, generation, context_handle)
then return cjson.encode({status = "stale"}) end
if context["operation_epoch"] ~= operation_epoch or context["operation_token"] ~= operation_token then
  return cjson.encode({status = "superseded"})
end
redis.call("SET", state_key, state_json, "EX", state_ttl)
return cjson.encode({status = "issued"})
"""


V2_CONSUME_OAUTH_STATE_SCRIPT = """
-- ai-platform:auth-oauth-state-consume:v2
""" + V2_LUA_HELPERS + """
local authority_key = KEYS[1]
local context_key = KEYS[2]
local state_key = KEYS[3]
local incarnation_digest = ARGV[1]
local generation = tonumber(ARGV[2])
local context_handle = ARGV[3]
local provider = ARGV[4]
if type(incarnation_digest) ~= "string" or incarnation_digest == ""
  or not epoch(generation) or generation < 1
  or type(context_handle) ~= "string" or context_handle == ""
  or type(provider) ~= "string" or provider == ""
then return cjson.encode({status = "corrupt"}) end
local authority = decode(redis.call("GET", authority_key))
local context = decode(redis.call("GET", context_key))
local state = decode(redis.call("GET", state_key))
if authority == false or context == false or state == false
  or not valid_authority(authority) or not valid_v2(context)
  or not ttl_consistent(authority_key, context_key)
  or not same_authority(authority, context, incarnation_digest, generation, context_handle)
then return cjson.encode({status = "stale"}) end
if not state then return cjson.encode({status = "missing"}) end
redis.call("DEL", state_key)
if type(state) ~= "table" or state["context_handle"] ~= context_handle
  or state["provider"] ~= provider
  or state["incarnation_digest"] ~= incarnation_digest
  or state["generation"] ~= generation
  or not epoch(state["operation_epoch"]) or state["operation_epoch"] < 1
  or type(state["operation_token"]) ~= "string" or state["operation_token"] == ""
then return cjson.encode({status = "invalid"}) end
return cjson.encode({status = "consumed", operation_epoch = state["operation_epoch"], operation_token = state["operation_token"]})
"""


@dataclass(frozen=True)
class AuthOperation:
    """One server-owned mutation lease for a browser auth context."""

    context_handle: str
    epoch: int
    token: str
    kind: str
    incarnation_digest: str | None = None
    generation: int | None = None


@dataclass(frozen=True)
class V2AuthContextIdentity:
    """Verified V2 browser cookie identity, without exposing its MAC."""

    incarnation: str
    incarnation_digest: str
    generation: int
    context_handle: str


@dataclass(frozen=True)
class AuthBootstrapResult:
    """Structured bootstrap result controlling whether a route may set a cookie."""

    status: str
    identity: V2AuthContextIdentity | None = None
    set_cookie: bool = False
    rotation_ticket: str | None = None
    cookie_max_age_seconds: int | None = None


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


_B64URL_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_V1_CONTEXT_HANDLE_RE = re.compile(r"^v1\.[A-Za-z0-9_-]{43}$")


def _is_b64url(value: object, *, length: int | None = None) -> bool:
    return (
        isinstance(value, str)
        and (length is None or len(value) == length)
        and bool(_B64URL_RE.fullmatch(value))
    )


def _canonical_v2_cookie_payload(identity: V2AuthContextIdentity) -> str:
    payload = json.dumps(
        {
            "g": identity.generation,
            "h": identity.context_handle,
            "i": identity.incarnation,
        },
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _browser_authority_digest(incarnation: str, settings: Any) -> str:
    return _urlsafe_digest(_context_secret(settings), "browser-authority", incarnation)


def _rotation_ticket_digest(ticket: str, settings: Any) -> str:
    return _urlsafe_digest(_context_secret(settings), "rotation-ticket", ticket)


def auth_context_v2_cookie_for_identity(
    identity: V2AuthContextIdentity,
    settings: Any | None = None,
) -> str:
    """Return the canonical signed V2 cookie value for a verified identity."""

    current_settings = settings or get_settings()
    payload = _canonical_v2_cookie_payload(identity)
    mac = _urlsafe_digest(_context_secret(current_settings), "v2-cookie", payload)
    return f"{AUTH_CONTEXT_V2_COOKIE_PREFIX}.{payload}.{mac}"


def parse_auth_context_cookie(
    cookie_value: str,
    settings: Any | None = None,
) -> str | V2AuthContextIdentity:
    """Strictly parse a V1 handle or authenticated V2 browser cookie."""

    current_settings = settings or get_settings()
    if _V1_CONTEXT_HANDLE_RE.fullmatch(cookie_value):
        return cookie_value
    parts = cookie_value.split(".")
    if len(parts) != 3 or parts[0] != AUTH_CONTEXT_V2_COOKIE_PREFIX:
        raise AuthContextError("auth_context_stale", 409)
    payload_part, supplied_mac = parts[1], parts[2]
    if not _is_b64url(payload_part) or not _is_b64url(supplied_mac, length=43):
        raise AuthContextError("auth_context_stale", 409)
    expected_mac = _urlsafe_digest(_context_secret(current_settings), "v2-cookie", payload_part)
    if not hmac.compare_digest(supplied_mac, expected_mac):
        raise AuthContextError("auth_context_stale", 409)
    try:
        padded = payload_part + ("=" * (-len(payload_part) % 4))
        decoded = base64.urlsafe_b64decode(padded.encode("ascii"))
        payload = json.loads(decoded.decode("ascii"))
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
        raise AuthContextError("auth_context_stale", 409) from exc
    if not isinstance(payload, dict) or set(payload) != {"i", "g", "h"}:
        raise AuthContextError("auth_context_stale", 409)
    incarnation = payload.get("i")
    generation = payload.get("g")
    context_handle = payload.get("h")
    if (
        not _is_b64url(incarnation, length=AUTH_CONTEXT_V2_INCARNATION_LENGTH)
        or type(generation) is not int
        or not 1 <= generation <= AUTH_CONTEXT_MAX_EPOCH
        or not isinstance(context_handle, str)
        or not _V1_CONTEXT_HANDLE_RE.fullmatch(context_handle)
    ):
        raise AuthContextError("auth_context_stale", 409)
    identity = V2AuthContextIdentity(
        incarnation=incarnation,
        incarnation_digest=_browser_authority_digest(incarnation, current_settings),
        generation=generation,
        context_handle=context_handle,
    )
    if not hmac.compare_digest(payload_part, _canonical_v2_cookie_payload(identity)):
        raise AuthContextError("auth_context_stale", 409)
    return identity


def auth_context_handle_for_nonce(nonce: str, settings: Any | None = None) -> str:
    """Derive the stable opaque cookie handle from a browser-generated nonce."""

    current_settings = settings or get_settings()
    return f"v1.{_urlsafe_digest(_context_secret(current_settings), 'handle', nonce)}"


def _nonce_binding(nonce: str, settings: Any) -> str:
    return _urlsafe_digest(_context_secret(settings), "binding", nonce)


def _context_key(context_handle: str) -> str:
    return f"{AUTH_CONTEXT_KEY_PREFIX}:{context_handle}"


def _authority_key(incarnation_digest: str) -> str:
    return f"{AUTH_BROWSER_AUTHORITY_KEY_PREFIX}:{incarnation_digest}"


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


def _is_valid_epoch(value: object) -> bool:
    if type(value) is int:
        return 0 <= value <= AUTH_CONTEXT_MAX_EPOCH
    if type(value) is float:
        return math.isfinite(value) and 0 <= value <= AUTH_CONTEXT_MAX_EPOCH and value.is_integer()
    return False


def _is_valid_lease(value: object) -> bool:
    if type(value) is int:
        return value >= 0
    if type(value) is float:
        return math.isfinite(value) and value >= 0
    return False


def _is_valid_context_record(record: object) -> bool:
    if not isinstance(record, dict):
        return False
    return (
        _is_valid_epoch(record.get("schema_version"))
        and record.get("schema_version") == AUTH_CONTEXT_SCHEMA_VERSION
        and isinstance(record.get("nonce_binding"), str)
        and _is_valid_epoch(record.get("operation_epoch"))
        and _is_valid_epoch(record.get("tenant_user_subject_epoch"))
        and isinstance(record.get("operation_token"), str)
        and isinstance(record.get("operation_kind"), str)
        and _is_valid_lease(record.get("lease_until"))
        and (record.get("principal") is None or isinstance(record.get("principal"), dict))
    )


def _is_valid_v2_context_record(record: object) -> bool:
    if not isinstance(record, dict):
        return False
    return (
        _is_valid_epoch(record.get("schema_version"))
        and record.get("schema_version") == AUTH_CONTEXT_V2_SCHEMA_VERSION
        and record.get("protocol_version") == AUTH_CONTEXT_V2_SCHEMA_VERSION
        and isinstance(record.get("nonce_binding"), str)
        and _is_b64url(record.get("incarnation_digest"), length=43)
        and _is_valid_epoch(record.get("generation"))
        and int(record["generation"]) >= 1
        and _is_valid_epoch(record.get("operation_epoch"))
        and _is_valid_epoch(record.get("tenant_user_subject_epoch"))
        and isinstance(record.get("operation_token"), str)
        and isinstance(record.get("operation_kind"), str)
        and _is_valid_lease(record.get("lease_until"))
        and (record.get("principal") is None or isinstance(record.get("principal"), dict))
    )


def _is_valid_v2_authority(record: object) -> bool:
    if not isinstance(record, dict):
        return False
    ticket_digest = record.get("rotation_ticket_digest")
    ticket_generation = record.get("rotation_ticket_generation")
    ticket_deadline = record.get("rotation_ticket_deadline")
    ticket_absent = (
        ticket_digest is None
        and ticket_generation is None
        and ticket_deadline is None
    )
    ticket_valid = (
        _is_b64url(ticket_digest, length=43)
        and _is_valid_epoch(ticket_generation)
        and int(ticket_generation) >= 1
        and _is_valid_epoch(record.get("generation"))
        and int(ticket_generation) == int(record["generation"])
        and _is_valid_lease(ticket_deadline)
    )
    return (
        record.get("schema_version") == AUTH_CONTEXT_V2_SCHEMA_VERSION
        and _is_b64url(record.get("incarnation_digest"), length=43)
        and _is_valid_epoch(record.get("generation"))
        and int(record["generation"]) >= 1
        and isinstance(record.get("context_handle"), str)
        and bool(_V1_CONTEXT_HANDLE_RE.fullmatch(record["context_handle"]))
        and (ticket_absent or ticket_valid)
    )


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
    if status in {
        "stale",
        "generation_gap",
        "generation_conflict",
        "migration_conflict",
        "invalid_ticket",
    }:
        raise AuthContextError("auth_context_stale", 409)
    if status == "rebootstrap_required":
        raise AuthContextError("auth_context_rebootstrap_required", 409)
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


async def bootstrap_auth_context(
    context_handle: str,
    nonce: str,
    settings: Any | None = None,
    *,
    request_has_matching_context: bool = False,
) -> str:
    """Create or verify a stable browser context before writing its cookie."""

    current_settings = settings or get_settings()
    result = await _eval(
        BOOTSTRAP_AUTH_CONTEXT_SCRIPT,
        [_context_key(context_handle)],
        [
            _nonce_binding(nonce, current_settings),
            _context_ttl_seconds(current_settings),
            "1" if request_has_matching_context else "0",
        ],
    )
    status = str(result.get("status") or "")
    if status in {"created", "existing"}:
        return status
    _raise_for_store_status("bootstrap", status)
    raise AssertionError("unreachable")


def _bootstrap_cookie_kind(
    supplied_cookie: str,
    context_handle: str,
    settings: Any,
) -> tuple[str, V2AuthContextIdentity | None]:
    if not supplied_cookie:
        return "none", None
    parsed = parse_auth_context_cookie(supplied_cookie, settings)
    if isinstance(parsed, V2AuthContextIdentity):
        return "v2", parsed
    return (
        "v1_matching" if hmac.compare_digest(parsed, context_handle) else "v1_conflict",
        None,
    )


def _cookie_max_age_from_result(result: Mapping[str, object]) -> int:
    ttl_milliseconds = result.get("ttl_milliseconds")
    if not _is_valid_epoch(ttl_milliseconds) or int(ttl_milliseconds) < 1:
        raise AuthContextError("auth_context_unavailable", 503)
    return max(1, math.ceil(int(ttl_milliseconds) / 1000))


async def bootstrap_auth_context_v2(
    nonce: str,
    incarnation: str,
    generation: int,
    supplied_cookie: str,
    settings: Any | None = None,
    *,
    rotation_ticket: str | None = None,
) -> AuthBootstrapResult:
    """Atomically establish, migrate, repair, or rotate one V2 browser context."""

    current_settings = settings or get_settings()
    if (
        not _is_b64url(incarnation, length=AUTH_CONTEXT_V2_INCARNATION_LENGTH)
        or type(generation) is not int
        or not 1 <= generation <= AUTH_CONTEXT_MAX_EPOCH
        or (rotation_ticket is not None and not _is_b64url(rotation_ticket, length=AUTH_CONTEXT_V2_TICKET_LENGTH))
    ):
        raise AuthContextError("auth_context_stale", 409)
    context_handle = auth_context_handle_for_nonce(nonce, current_settings)
    incarnation_digest = _browser_authority_digest(incarnation, current_settings)
    requested_identity = V2AuthContextIdentity(
        incarnation=incarnation,
        incarnation_digest=incarnation_digest,
        generation=generation,
        context_handle=context_handle,
    )
    cookie_kind, supplied_identity = _bootstrap_cookie_kind(
        supplied_cookie,
        context_handle,
        current_settings,
    )

    if rotation_ticket is not None:
        # A response may have completed the rotation server-side while this
        # browser lost its IDB promotion. Only the signed target cookie plus
        # exact authority/context equality can reconcile that local pending
        # record; an old ticket is neither trusted nor consumed here.
        if (
            supplied_identity is not None
            and supplied_identity.incarnation == incarnation
            and generation == supplied_identity.generation
            and context_handle == supplied_identity.context_handle
        ):
            result = await _eval(
                V2_RECONCILE_ROTATION_SCRIPT,
                [
                    _authority_key(supplied_identity.incarnation_digest),
                    _context_key(context_handle),
                ],
                [
                    incarnation_digest,
                    generation,
                    context_handle,
                ],
            )
            status = str(result.get("status") or "")
            if status == "reconciled":
                return AuthBootstrapResult(
                    "ready",
                    requested_identity,
                    set_cookie=False,
                )
            _raise_for_store_status("bootstrap", status)
            raise AssertionError("unreachable")
        if (
            supplied_identity is None
            or supplied_identity.incarnation != incarnation
            or generation != supplied_identity.generation + 1
            or context_handle == supplied_identity.context_handle
        ):
            raise AuthContextError("auth_context_stale", 409)
        result = await _eval(
            V2_ROTATE_AUTH_CONTEXT_SCRIPT,
            [
                _authority_key(supplied_identity.incarnation_digest),
                _context_key(supplied_identity.context_handle),
                _context_key(context_handle),
            ],
            [
                incarnation_digest,
                supplied_identity.generation,
                supplied_identity.context_handle,
                context_handle,
                _nonce_binding(nonce, current_settings),
                _context_ttl_seconds(current_settings),
                _rotation_ticket_digest(rotation_ticket, current_settings),
            ],
        )
        status = str(result.get("status") or "")
        if status == "rotated":
            return AuthBootstrapResult(
                "ready",
                requested_identity,
                set_cookie=True,
                cookie_max_age_seconds=_cookie_max_age_from_result(result),
            )
        _raise_for_store_status("bootstrap", status)
        raise AssertionError("unreachable")

    # A current authenticated V2 cookie plus a different nonce is the sole
    # server-authorized path that may request a generation rotation.  The
    # ticket is digest-only in Redis and can be replaced only at this identity.
    if (
        supplied_identity is not None
        and supplied_identity.incarnation == incarnation
        and supplied_identity.generation == generation
        and supplied_identity.context_handle != context_handle
    ):
        ticket = secrets.token_urlsafe(32)
        result = await _eval(
            V2_ISSUE_ROTATION_TICKET_SCRIPT,
            [
                _authority_key(supplied_identity.incarnation_digest),
                _context_key(supplied_identity.context_handle),
            ],
            [
                supplied_identity.incarnation_digest,
                supplied_identity.generation,
                supplied_identity.context_handle,
                _rotation_ticket_digest(ticket, current_settings),
                _operation_lease_seconds(current_settings),
            ],
        )
        if str(result.get("status") or "") != "issued":
            _raise_for_store_status("bootstrap", str(result.get("status") or ""))
        return AuthBootstrapResult(
            "rebootstrap_required",
            supplied_identity,
            rotation_ticket=ticket,
        )

    result = await _eval(
        V2_BOOTSTRAP_AUTH_CONTEXT_SCRIPT,
        [_authority_key(incarnation_digest), _context_key(context_handle)],
        [
            incarnation_digest,
            generation,
            context_handle,
            _nonce_binding(nonce, current_settings),
            _context_ttl_seconds(current_settings),
            cookie_kind,
            supplied_identity.incarnation_digest if supplied_identity else "",
            supplied_identity.generation if supplied_identity else 0,
            supplied_identity.context_handle if supplied_identity else "",
            "",
            _operation_lease_seconds(current_settings),
        ],
    )
    status = str(result.get("status") or "")
    if status in {"created", "migrated", "existing", "repair"}:
        return AuthBootstrapResult(
            "ready",
            requested_identity,
            set_cookie=status in {"created", "migrated", "repair"},
            cookie_max_age_seconds=_cookie_max_age_from_result(result),
        )
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
    epoch = result.get("operation_epoch")
    if not _is_valid_epoch(epoch) or epoch < 1:
        raise AuthContextError("auth_context_unavailable", 503)
    return AuthOperation(context_handle=context_handle, epoch=int(epoch), token=token, kind=kind)


async def begin_auth_operation_for_cookie(
    cookie_value: str,
    kind: str,
    settings: Any | None = None,
) -> AuthOperation:
    """Begin an auth operation after atomically validating a V1 or V2 cookie."""

    current_settings = settings or get_settings()
    parsed = parse_auth_context_cookie(cookie_value, current_settings)
    if not isinstance(parsed, V2AuthContextIdentity):
        return await begin_auth_operation(parsed, kind, current_settings)
    token = secrets.token_urlsafe(32)
    result = await _eval(
        V2_BEGIN_AUTH_OPERATION_SCRIPT,
        [
            _authority_key(parsed.incarnation_digest),
            _context_key(parsed.context_handle),
        ],
        [
            parsed.incarnation_digest,
            parsed.generation,
            parsed.context_handle,
            _operation_lease_seconds(current_settings),
            token,
            kind,
        ],
    )
    status = str(result.get("status") or "")
    if status != "begun":
        _raise_for_store_status("begin", status)
    epoch = result.get("operation_epoch")
    if not _is_valid_epoch(epoch) or int(epoch) < 1:
        raise AuthContextError("auth_context_unavailable", 503)
    return AuthOperation(
        context_handle=parsed.context_handle,
        epoch=int(epoch),
        token=token,
        kind=kind,
        incarnation_digest=parsed.incarnation_digest,
        generation=parsed.generation,
    )


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

    if operation.incarnation_digest is not None or operation.generation is not None:
        if (
            operation.incarnation_digest is None
            or operation.generation is None
            or not _is_b64url(operation.incarnation_digest, length=43)
            or not _is_valid_epoch(operation.generation)
            or operation.generation < 1
        ):
            raise AuthContextError("auth_context_unavailable", 503)
        result = await _eval(
            V2_COMMIT_AUTH_OPERATION_SCRIPT,
            [
                _authority_key(operation.incarnation_digest),
                _context_key(operation.context_handle),
            ],
            [
                operation.incarnation_digest,
                operation.generation,
                operation.context_handle,
                operation.epoch,
                operation.token,
                json.dumps(principal, ensure_ascii=False, separators=(",", ":")),
            ],
        )
        status = str(result.get("status") or "")
        if status == "committed":
            committed_epoch = result.get("tenant_user_subject_epoch")
            if not _is_valid_epoch(committed_epoch) or int(committed_epoch) < 1:
                raise AuthContextError("auth_context_unavailable", 503)
            return status
        if status in {"superseded", "expired", "missing"}:
            return status
        _raise_for_store_status("commit", status)
        raise AssertionError("unreachable")

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
    if _is_valid_v2_context_record(record):
        # A physically late raw V1 cookie is never a valid downgrade for a
        # migrated context. The V2 owner repairs through its authenticated
        # incarnation/generation bootstrap request instead.
        raise AuthContextError("auth_context_stale", 409)
    if not _is_valid_context_record(record):
        raise AuthContextError("auth_context_unavailable", 503)
    principal = record.get("principal")
    if principal is None:
        return None
    snapshot = _valid_snapshot(principal)
    if snapshot is None:
        raise AuthContextError("auth_context_unavailable", 503)
    return snapshot


async def principal_for_cookie(
    cookie_value: str,
    settings: Any | None = None,
) -> dict[str, object] | None:
    """Read a principal through the V1 path or one atomic V2 authority snapshot."""

    current_settings = settings or get_settings()
    parsed = parse_auth_context_cookie(cookie_value, current_settings)
    if not isinstance(parsed, V2AuthContextIdentity):
        return await principal_for_context(parsed, current_settings)
    result = await _eval(
        V2_PRINCIPAL_SNAPSHOT_SCRIPT,
        [
            _authority_key(parsed.incarnation_digest),
            _context_key(parsed.context_handle),
        ],
        [
            parsed.incarnation_digest,
            parsed.generation,
            parsed.context_handle,
        ],
    )
    if str(result.get("status") or "") != "principal":
        _raise_for_store_status("principal", str(result.get("status") or ""))
    principal = result.get("principal")
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
    if operation.incarnation_digest is not None or operation.generation is not None:
        if operation.incarnation_digest is None or operation.generation is None:
            raise AuthContextError("auth_context_unavailable", 503)
        record.update(
            {
                "incarnation_digest": operation.incarnation_digest,
                "generation": operation.generation,
            }
        )
        result = await _eval(
            V2_ISSUE_OAUTH_STATE_SCRIPT,
            [
                _authority_key(operation.incarnation_digest),
                _context_key(context_handle),
                _oauth_state_key(state),
            ],
            [
                operation.incarnation_digest,
                operation.generation,
                context_handle,
                provider,
                operation.epoch,
                operation.token,
                json.dumps(record, separators=(",", ":")),
                _operation_lease_seconds(current_settings),
            ],
        )
        if str(result.get("status") or "") != "issued":
            _raise_for_store_status("oauth", str(result.get("status") or ""))
        return state
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
    epoch = result.get("operation_epoch")
    token = result.get("operation_token")
    if not _is_valid_epoch(epoch) or epoch < 1 or not isinstance(token, str) or not token:
        raise AuthContextError("auth_context_unavailable", 503)
    return AuthOperation(
        context_handle=context_handle,
        epoch=int(epoch),
        token=token,
        kind=f"oauth:{provider}",
    )


async def consume_oauth_state_for_cookie(
    cookie_value: str,
    provider: str,
    state: str,
    settings: Any | None = None,
) -> AuthOperation:
    """Consume OAuth state only when the current V2 identity still owns it."""

    current_settings = settings or get_settings()
    parsed = parse_auth_context_cookie(cookie_value, current_settings)
    if not isinstance(parsed, V2AuthContextIdentity):
        return await consume_oauth_state(parsed, provider, state, current_settings)
    result = await _eval(
        V2_CONSUME_OAUTH_STATE_SCRIPT,
        [
            _authority_key(parsed.incarnation_digest),
            _context_key(parsed.context_handle),
            _oauth_state_key(state),
        ],
        [
            parsed.incarnation_digest,
            parsed.generation,
            parsed.context_handle,
            provider,
        ],
    )
    if str(result.get("status") or "") != "consumed":
        _raise_for_store_status("oauth", str(result.get("status") or ""))
    epoch = result.get("operation_epoch")
    token = result.get("operation_token")
    if not _is_valid_epoch(epoch) or int(epoch) < 1 or not isinstance(token, str) or not token:
        raise AuthContextError("auth_context_unavailable", 503)
    return AuthOperation(
        context_handle=parsed.context_handle,
        epoch=int(epoch),
        token=token,
        kind=f"oauth:{provider}",
        incarnation_digest=parsed.incarnation_digest,
        generation=parsed.generation,
    )
