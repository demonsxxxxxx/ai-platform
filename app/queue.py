from dataclasses import dataclass
import hashlib
import json
import re
import secrets
import time
from typing import Any
import uuid

from pydantic import ValidationError
from redis.asyncio import Redis

from app.models import QueueRunPayload
from app.settings import get_settings


DEFAULT_QUEUE_KEY_PREFIX = "ai-platform:runs"
QUEUE_KEY = f"{DEFAULT_QUEUE_KEY_PREFIX}:queued"
PROCESSING_KEY = f"{DEFAULT_QUEUE_KEY_PREFIX}:processing"
PROCESSING_META_KEY = f"{DEFAULT_QUEUE_KEY_PREFIX}:processing-meta"
RETRY_META_KEY = f"{DEFAULT_QUEUE_KEY_PREFIX}:retry-meta"
DEAD_LETTER_KEY = f"{DEFAULT_QUEUE_KEY_PREFIX}:dead-letter"
WORKER_HEARTBEAT_KEY = f"{DEFAULT_QUEUE_KEY_PREFIX}:worker-heartbeat"
QUEUED_META_KEY = f"{DEFAULT_QUEUE_KEY_PREFIX}:queued-meta"
QUEUED_RUN_INDEX_KEY = f"{DEFAULT_QUEUE_KEY_PREFIX}:queued-run-index"
QUEUED_ORDER_KEY = f"{DEFAULT_QUEUE_KEY_PREFIX}:queued-order"
QUEUED_SEQUENCE_KEY = f"{DEFAULT_QUEUE_KEY_PREFIX}:queued-sequence"
RECONCILIATION_FENCE_PREFIX = f"{DEFAULT_QUEUE_KEY_PREFIX}:reconciliation-fence"
DEFAULT_VISIBILITY_TIMEOUT_SECONDS = 900
DEFAULT_MAX_ATTEMPTS = 3


ENQUEUE_WITH_METADATA_SCRIPT = """
-- ai-platform:enqueue-run-with-metadata:v1
local queued_key = KEYS[1]
local queued_meta_key = KEYS[2]
local queued_run_index_key = KEYS[3]
local queued_order_key = KEYS[4]
local queued_sequence_key = KEYS[5]
local processing_meta_key = KEYS[6]
local retry_meta_key = KEYS[7]
local reconciliation_fence_key = KEYS[8]

local raw = ARGV[1]
local message_id = ARGV[2]
local run_index_field = ARGV[3]
local metadata_json = ARGV[4]

if redis.call("exists", reconciliation_fence_key) == 1 then
  return cjson.encode({status = "reconciliation_fenced"})
end

local message_ids = {}
local raw_index = redis.call("hget", queued_run_index_key, run_index_field)
if raw_index then
  local ok_index, decoded_index = pcall(cjson.decode, raw_index)
  if ok_index and type(decoded_index) == "table" then
    for _, indexed_message_id in ipairs(decoded_index) do
      local candidate = tostring(indexed_message_id or "")
      if candidate ~= "" then
        table.insert(message_ids, candidate)
      end
    end
  else
    local candidate = tostring(raw_index or "")
    if candidate ~= "" then
      table.insert(message_ids, candidate)
    end
  end
end

for _, indexed_message_id in ipairs(message_ids) do
  if indexed_message_id == message_id then
    local raw_metadata = redis.call("hget", queued_meta_key, message_id)
    local rank = redis.call("zrank", queued_order_key, message_id)
    if raw_metadata and rank then
      local ok_existing, existing = pcall(cjson.decode, raw_metadata)
      local sequence = rank + 1
      if ok_existing and type(existing) == "table" then
        sequence = tonumber(existing["sequence"] or sequence) or sequence
      end
      return cjson.encode({
        status = "already_enqueued",
        position = rank + 1,
        sequence = sequence,
      })
    end
  end
end

if redis.call("hget", processing_meta_key, message_id) or redis.call("hget", retry_meta_key, message_id) then
  return cjson.encode({status = "already_leased", position = 0, sequence = 0})
end
local retained_message_ids = {}
for _, indexed_message_id in ipairs(message_ids) do
  if indexed_message_id ~= message_id then
    table.insert(retained_message_ids, indexed_message_id)
  end
end
message_ids = retained_message_ids
table.insert(message_ids, message_id)

local sequence = redis.call("incr", queued_sequence_key)
local position = redis.call("rpush", queued_key, raw)
local ok, metadata = pcall(cjson.decode, metadata_json)
if not ok or type(metadata) ~= "table" then
  metadata = {}
end
metadata["sequence"] = sequence
metadata["raw"] = raw

redis.call("hset", queued_meta_key, message_id, cjson.encode(metadata))
redis.call("hset", queued_run_index_key, run_index_field, cjson.encode(message_ids))
redis.call("zadd", queued_order_key, sequence, message_id)

return cjson.encode({
  status = "enqueued",
  position = position,
  sequence = sequence,
})
"""


REMOVE_QUEUED_WITH_METADATA_SCRIPT = """
-- ai-platform:remove-queued-with-metadata:v1
local queued_key = KEYS[1]
local queued_meta_key = KEYS[2]
local queued_run_index_key = KEYS[3]
local queued_order_key = KEYS[4]

local run_index_field = ARGV[1]
local tenant_id = ARGV[2]
local run_id = ARGV[3]

local raw_index = redis.call("hget", queued_run_index_key, run_index_field)
if not raw_index then
  return cjson.encode({status = "missing_index", removed = 0})
end

local message_ids = {}
local ok_index, decoded_index = pcall(cjson.decode, raw_index)
if ok_index and type(decoded_index) == "table" then
  for _, indexed_message_id in ipairs(decoded_index) do
    local candidate = tostring(indexed_message_id or "")
    if candidate ~= "" then
      table.insert(message_ids, candidate)
    end
  end
else
  table.insert(message_ids, tostring(raw_index or ""))
end

local removed_total = 0
local matched = 0
for _, message_id in ipairs(message_ids) do
  local raw_metadata = redis.call("hget", queued_meta_key, message_id)
  if raw_metadata then
    local ok, metadata = pcall(cjson.decode, raw_metadata)
    if ok and type(metadata) == "table" then
      if tostring(metadata["tenant_id"] or "") == tenant_id and tostring(metadata["run_id"] or "") == run_id then
        matched = matched + 1
        local raw = tostring(metadata["raw"] or "")
        if raw ~= "" then
          removed_total = removed_total + redis.call("lrem", queued_key, 0, raw)
        end
        redis.call("hdel", queued_meta_key, message_id)
        redis.call("zrem", queued_order_key, message_id)
      end
    else
      redis.call("hdel", queued_meta_key, message_id)
      redis.call("zrem", queued_order_key, message_id)
    end
  else
    redis.call("zrem", queued_order_key, message_id)
  end

end

redis.call("hdel", queued_run_index_key, run_index_field)

if matched == 0 then
  return cjson.encode({status = "missing_metadata", removed = 0})
end
return cjson.encode({status = "removed", removed = removed_total})
"""


LEASE_QUOTA_SCRIPT = """
-- ai-platform:lease-run-with-quota:v1
local function remove_run_index_message(queued_run_index_key, run_index_field, message_id)
  local raw_index = redis.call("hget", queued_run_index_key, run_index_field)
  if not raw_index then
    return
  end
  local ok_index, decoded_index = pcall(cjson.decode, raw_index)
  if ok_index and type(decoded_index) == "table" then
    local remaining = {}
    for _, indexed_message_id in ipairs(decoded_index) do
      local candidate = tostring(indexed_message_id or "")
      if candidate ~= "" and candidate ~= message_id then
        table.insert(remaining, candidate)
      end
    end
    if #remaining > 0 then
      redis.call("hset", queued_run_index_key, run_index_field, cjson.encode(remaining))
    else
      redis.call("hdel", queued_run_index_key, run_index_field)
    end
  elseif tostring(raw_index or "") == message_id then
    redis.call("hdel", queued_run_index_key, run_index_field)
  end
end

local queued_key = KEYS[1]
local processing_key = KEYS[2]
local processing_meta_key = KEYS[3]
local retry_meta_key = KEYS[4]
local worker_heartbeat_key = KEYS[5]
local queued_meta_key = KEYS[6]
local queued_run_index_key = KEYS[7]
local queued_order_key = KEYS[8]
local reconciliation_fence_key = KEYS[9]

local raw = ARGV[1]
local scan_limit = tonumber(ARGV[2])
local absolute_index = tonumber(ARGV[3])
local message_id = ARGV[4]
local worker_id = ARGV[5]
local now = tonumber(ARGV[6])
local max_processing_runs = tonumber(ARGV[7])
local tenant_processing_limit = tonumber(ARGV[8])
local user_processing_limit = tonumber(ARGV[9])
local tenant_id = ARGV[10]
local user_id = ARGV[11]
local run_id = ARGV[12]
local attempt_id = ARGV[13]
local owner_token = ARGV[14]

if redis.call("exists", reconciliation_fence_key) == 1 then
  return cjson.encode({status = "reconciliation_fenced"})
end

if max_processing_runs > 0 and redis.call("llen", processing_key) >= max_processing_runs then
  return cjson.encode({status = "capacity_full"})
end

local queue_length = redis.call("llen", queued_key)
if absolute_index < 0 or absolute_index >= queue_length then
  return cjson.encode({status = "conflict"})
end
if redis.call("lindex", queued_key, absolute_index) ~= raw then
  return cjson.encode({status = "conflict"})
end

local tenant_processing = 0
local user_processing = 0
local processing_items = redis.call("lrange", processing_key, 0, -1)
for _, processing_raw in ipairs(processing_items) do
  local ok, payload = pcall(cjson.decode, processing_raw)
  if ok and type(payload) == "table" then
    if tostring(payload["tenant_id"] or "") == tenant_id then
      tenant_processing = tenant_processing + 1
      if tostring(payload["user_id"] or "") == user_id then
        user_processing = user_processing + 1
      end
    end
  end
end

if tenant_processing_limit > 0 and tenant_processing >= tenant_processing_limit then
  return cjson.encode({
    status = "quota_blocked",
    tenant_processing = tenant_processing,
    user_processing = user_processing,
  })
end
if user_processing_limit > 0 and user_processing >= user_processing_limit then
  return cjson.encode({
    status = "quota_blocked",
    tenant_processing = tenant_processing,
    user_processing = user_processing,
  })
end

local attempts = 1
local attempts_source = redis.call("hget", retry_meta_key, message_id)
if not attempts_source then
  attempts_source = redis.call("hget", processing_meta_key, message_id)
end
if attempts_source then
  local ok, meta = pcall(cjson.decode, attempts_source)
  if ok and type(meta) == "table" then
    local parsed_attempts = tonumber(meta["attempts"] or 0)
    if parsed_attempts then
      attempts = parsed_attempts + 1
    else
      attempts = 1
    end
    if attempts < 1 then
      attempts = 1
    end
  end
end

local sentinel = "__ai_platform_queue_move__:" .. message_id .. ":" .. tostring(now) .. ":" .. worker_id
redis.call("lset", queued_key, absolute_index, sentinel)
redis.call("lrem", queued_key, 1, sentinel)
redis.call("hdel", queued_meta_key, message_id)
remove_run_index_message(queued_run_index_key, tenant_id .. ":" .. run_id, message_id)
redis.call("zrem", queued_order_key, message_id)
redis.call("lpush", processing_key, raw)

local quota_snapshot = {
  tenant_processing = tenant_processing,
  tenant_processing_limit = tenant_processing_limit,
  tenant_processing_saturated = tenant_processing_limit > 0 and tenant_processing >= tenant_processing_limit,
  user_processing = user_processing,
  user_processing_limit = user_processing_limit,
  user_processing_saturated = user_processing_limit > 0 and user_processing >= user_processing_limit,
}
local meta = {
  message_id = message_id,
  raw = raw,
  attempts = attempts,
  leased_at = now,
  heartbeat_at = now,
  worker_id = worker_id,
  attempt_id = attempt_id,
  owner_token = owner_token,
  run_id = run_id,
  tenant_id = tenant_id,
  user_id = user_id,
  quota_snapshot = quota_snapshot,
}
local encoded_meta = cjson.encode(meta)
redis.call("hset", processing_meta_key, message_id, encoded_meta)
redis.call("hset", retry_meta_key, message_id, encoded_meta)
redis.call("hset", worker_heartbeat_key, worker_id, tostring(now))

return cjson.encode({
  status = "leased",
  attempts = attempts,
  attempt_id = attempt_id,
  owner_token = owner_token,
  tenant_processing = tenant_processing,
  user_processing = user_processing,
})
"""


DEAD_LETTER_INVALID_QUOTA_SCRIPT = """
-- ai-platform:dead-letter-invalid-quota:v1
local function remove_run_index_message(queued_run_index_key, run_index_field, message_id)
  local raw_index = redis.call("hget", queued_run_index_key, run_index_field)
  if not raw_index then
    return
  end
  local ok_index, decoded_index = pcall(cjson.decode, raw_index)
  if ok_index and type(decoded_index) == "table" then
    local remaining = {}
    for _, indexed_message_id in ipairs(decoded_index) do
      local candidate = tostring(indexed_message_id or "")
      if candidate ~= "" and candidate ~= message_id then
        table.insert(remaining, candidate)
      end
    end
    if #remaining > 0 then
      redis.call("hset", queued_run_index_key, run_index_field, cjson.encode(remaining))
    else
      redis.call("hdel", queued_run_index_key, run_index_field)
    end
  elseif tostring(raw_index or "") == message_id then
    redis.call("hdel", queued_run_index_key, run_index_field)
  end
end

local queued_key = KEYS[1]
local processing_meta_key = KEYS[2]
local retry_meta_key = KEYS[3]
local dead_letter_key = KEYS[4]
local queued_meta_key = KEYS[5]
local queued_run_index_key = KEYS[6]
local queued_order_key = KEYS[7]

local raw = ARGV[1]
local scan_limit = tonumber(ARGV[2])
local absolute_index = tonumber(ARGV[3])
local message_id = ARGV[4]
local worker_id = ARGV[5]
local now = tonumber(ARGV[6])
local error_message = ARGV[7]

local queue_length = redis.call("llen", queued_key)
if absolute_index < 0 or absolute_index >= queue_length then
  return cjson.encode({status = "conflict"})
end
if redis.call("lindex", queued_key, absolute_index) ~= raw then
  return cjson.encode({status = "conflict"})
end

local attempts = 1
local attempts_source = redis.call("hget", retry_meta_key, message_id)
if not attempts_source then
  attempts_source = redis.call("hget", processing_meta_key, message_id)
end
if attempts_source then
  local ok, meta = pcall(cjson.decode, attempts_source)
  if ok and type(meta) == "table" then
    local parsed_attempts = tonumber(meta["attempts"] or 0)
    if parsed_attempts then
      attempts = parsed_attempts + 1
    else
      attempts = 1
    end
    if attempts < 1 then
      attempts = 1
    end
  end
end

local sentinel = "__ai_platform_queue_dead_letter__:" .. message_id .. ":" .. tostring(now) .. ":" .. worker_id
redis.call("lset", queued_key, absolute_index, sentinel)
redis.call("lrem", queued_key, 1, sentinel)
local raw_queued_meta = redis.call("hget", queued_meta_key, message_id)
if raw_queued_meta then
  local ok_meta, queued_meta = pcall(cjson.decode, raw_queued_meta)
  if ok_meta and type(queued_meta) == "table" then
    local indexed_tenant_id = tostring(queued_meta["tenant_id"] or "")
    local indexed_run_id = tostring(queued_meta["run_id"] or "")
    if indexed_tenant_id ~= "" and indexed_run_id ~= "" then
      remove_run_index_message(queued_run_index_key, indexed_tenant_id .. ":" .. indexed_run_id, message_id)
    end
  end
end
redis.call("hdel", queued_meta_key, message_id)
redis.call("zrem", queued_order_key, message_id)
redis.call("rpush", dead_letter_key, cjson.encode({
  schema_version = "ai-platform.dead-letter.v1",
  error_code = "invalid_queue_payload",
  error_message = error_message,
  attempts = attempts,
  worker_id = worker_id,
  raw = raw,
  created_at = now,
}))
redis.call("hdel", retry_meta_key, message_id)

return cjson.encode({status = "dead_lettered", attempts = attempts})
"""


ACQUIRE_RECONCILIATION_FENCE_SCRIPT = """
-- ai-platform:acquire-run-reconciliation-fence:v1
local queued_key = KEYS[1]
local processing_key = KEYS[2]
local queued_meta_key = KEYS[3]
local processing_meta_key = KEYS[4]
local retry_meta_key = KEYS[5]
local worker_heartbeat_key = KEYS[6]
local queued_run_index_key = KEYS[7]
local fence_key = KEYS[8]

local tenant_id = ARGV[1]
local run_id = ARGV[2]
local run_index_field = ARGV[3]
local owner_token = ARGV[4]
local now = tonumber(ARGV[5])
local worker_ttl = tonumber(ARGV[6])
local scan_limit = tonumber(ARGV[7])
local fence_ttl_ms = tonumber(ARGV[8])

if redis.call("exists", fence_key) == 1 then
  return cjson.encode({status = "fenced"})
end
if now == nil or worker_ttl == nil or scan_limit == nil or scan_limit < 1 or fence_ttl_ms == nil or fence_ttl_ms < 1 then
  return cjson.encode({status = "inconclusive"})
end

local queued_depth = redis.call("llen", queued_key)
local processing_depth = redis.call("llen", processing_key)
if queued_depth > scan_limit or processing_depth > scan_limit then
  return cjson.encode({status = "inconclusive"})
end

local function list_has_run(list_key)
  local items = redis.call("lrange", list_key, 0, -1)
  for _, raw in ipairs(items) do
    local ok, payload = pcall(cjson.decode, raw)
    if not ok or type(payload) ~= "table" then
      return "inconclusive"
    end
    if tostring(payload["tenant_id"] or "") == tenant_id
       and tostring(payload["run_id"] or "") == run_id then
      return "owned"
    end
  end
  return "absent"
end

local function processing_has_correlated_raw(expected_raw)
  for _, processing_raw in ipairs(redis.call("lrange", processing_key, 0, -1)) do
    if processing_raw == expected_raw then
      return true
    end
  end
  return false
end

local queued_state = list_has_run(queued_key)
if queued_state ~= "absent" then
  return cjson.encode({status = queued_state})
end
local processing_state = list_has_run(processing_key)
if processing_state ~= "absent" then
  return cjson.encode({status = processing_state})
end
if redis.call("hget", queued_run_index_key, run_index_field) then
  return cjson.encode({status = "owned"})
end

local function metadata_state(hash_key, queued_metadata)
  local count = redis.call("hlen", hash_key)
  if count > scan_limit then
    return "inconclusive"
  end
  local items = redis.call("hgetall", hash_key)
  for index = 2, #items, 2 do
    local metadata_message_id = tostring(items[index - 1] or "")
    local ok, metadata = pcall(cjson.decode, items[index])
    if not ok or type(metadata) ~= "table" then
      return "inconclusive"
    end
    if tostring(metadata["tenant_id"] or "") == tenant_id
       and tostring(metadata["run_id"] or "") == run_id then
      if queued_metadata then
        return "owned"
      end
      local worker_id = tostring(metadata["worker_id"] or "")
      if worker_id == "" then
        return "inconclusive"
      end
      local last_activity = nil
      for _, activity_field in ipairs({"heartbeat_at", "leased_at"}) do
        if metadata[activity_field] ~= nil then
          local activity = tonumber(metadata[activity_field])
          if activity == nil then
            return "inconclusive"
          end
          if activity > now then
            return "inconclusive"
          end
          if last_activity == nil or activity > last_activity then
            last_activity = activity
          end
        end
      end
      if last_activity == nil then
        return "inconclusive"
      end
      local raw_worker_heartbeat = redis.call("hget", worker_heartbeat_key, worker_id)
      local worker_active = false
      if raw_worker_heartbeat then
        local worker_heartbeat = tonumber(raw_worker_heartbeat)
        if worker_heartbeat == nil then
          return "inconclusive"
        end
        if worker_heartbeat > now then
          return "inconclusive"
        end
        worker_active = now - worker_heartbeat <= worker_ttl
      end
      local metadata_fresh = now - last_activity <= worker_ttl
      if metadata_fresh and worker_active then
        local metadata_raw = metadata["raw"]
        if type(metadata_raw) ~= "string" or metadata_raw == ""
           or tostring(metadata["message_id"] or "") ~= metadata_message_id then
          return "inconclusive"
        end
        local raw_ok, raw_payload = pcall(cjson.decode, metadata_raw)
        if not raw_ok or type(raw_payload) ~= "table"
           or tostring(raw_payload["tenant_id"] or "") ~= tenant_id
           or tostring(raw_payload["run_id"] or "") ~= run_id
           or not processing_has_correlated_raw(metadata_raw) then
          return "inconclusive"
        end
        return "owned"
      end
    end
  end
  return "absent"
end

for _, state in ipairs({
  metadata_state(queued_meta_key, true),
  metadata_state(processing_meta_key, false),
  metadata_state(retry_meta_key, false)
}) do
  if state ~= "absent" then
    return cjson.encode({status = state})
  end
end

local set_result = redis.call("set", fence_key, owner_token, "NX", "PX", fence_ttl_ms)
if not set_result then
  return cjson.encode({status = "fenced"})
end
return cjson.encode({status = "claimed"})
"""


RELEASE_RECONCILIATION_FENCE_SCRIPT = """
-- ai-platform:release-run-reconciliation-fence:v1
if redis.call("get", KEYS[1]) ~= ARGV[1] then
  return 0
end
return redis.call("del", KEYS[1])
"""


RENEW_RECONCILIATION_FENCE_SCRIPT = """
-- ai-platform:renew-run-reconciliation-fence:v1
local fence_key = KEYS[1]
local owner_token = ARGV[1]
local fence_ttl_ms = tonumber(ARGV[2])

if fence_ttl_ms == nil or fence_ttl_ms < 1 then
  return cjson.encode({status = "invalid_ttl"})
end
if redis.call("get", fence_key) ~= owner_token then
  return cjson.encode({status = "owner_lost"})
end
if not redis.call("set", fence_key, owner_token, "XX", "PX", fence_ttl_ms) then
  return cjson.encode({status = "owner_lost"})
end
return cjson.encode({status = "renewed"})
"""


REQUEUE_WITH_FENCE_SCRIPT = """
-- ai-platform:requeue-run-with-fence:v1
local queued_key = KEYS[1]
local processing_key = KEYS[2]
local queued_meta_key = KEYS[3]
local queued_run_index_key = KEYS[4]
local queued_order_key = KEYS[5]
local queued_sequence_key = KEYS[6]
local retry_meta_key = KEYS[7]
local processing_meta_key = KEYS[8]
local fence_key = KEYS[9]

if redis.call("exists", fence_key) == 1 then
  return cjson.encode({status = "reconciliation_fenced"})
end

local raw = ARGV[1]
local message_id = ARGV[2]
local run_index_field = ARGV[3]
local metadata_json = ARGV[4]
local retry_metadata_json = ARGV[5]
local remove_processing = ARGV[6]
local expected_attempt_id = ARGV[7]
local expected_owner_token = ARGV[8]
local lease_metadata_json = redis.call("hget", processing_meta_key, message_id)
if not lease_metadata_json then
  lease_metadata_json = redis.call("hget", retry_meta_key, message_id)
end
local lease_ok, lease_metadata = pcall(cjson.decode, lease_metadata_json or "")
if not lease_ok or type(lease_metadata) ~= "table"
  or tostring(lease_metadata["message_id"] or "") ~= message_id
  or tostring(lease_metadata["attempt_id"] or "") ~= expected_attempt_id
  or tostring(lease_metadata["owner_token"] or "") ~= expected_owner_token then
  return cjson.encode({status = "stale_owner"})
end
local sequence = redis.call("incr", queued_sequence_key)
local ok, metadata = pcall(cjson.decode, metadata_json)
if not ok or type(metadata) ~= "table" then
  return cjson.encode({status = "inconclusive"})
end
metadata["sequence"] = sequence
metadata["raw"] = raw

local message_ids = {}
local raw_index = redis.call("hget", queued_run_index_key, run_index_field)
if raw_index then
  local ok_index, decoded_index = pcall(cjson.decode, raw_index)
  if not ok_index or type(decoded_index) ~= "table" then
    return cjson.encode({status = "inconclusive"})
  end
  for _, indexed_message_id in ipairs(decoded_index) do
    local candidate = tostring(indexed_message_id or "")
    if candidate ~= "" and candidate ~= message_id then
      table.insert(message_ids, candidate)
    end
  end
end
table.insert(message_ids, message_id)

if remove_processing == "1" then
  redis.call("lrem", processing_key, 1, raw)
end
redis.call("hdel", processing_meta_key, message_id)
redis.call("rpush", queued_key, raw)
redis.call("hset", queued_meta_key, message_id, cjson.encode(metadata))
redis.call("hset", queued_run_index_key, run_index_field, cjson.encode(message_ids))
redis.call("zadd", queued_order_key, sequence, message_id)
if retry_metadata_json ~= "" then
  redis.call("hset", retry_meta_key, message_id, retry_metadata_json)
end
return cjson.encode({status = "requeued"})
"""


DEAD_LETTER_EXPIRED_LEASE_WITH_FENCE_SCRIPT = """
-- ai-platform:dead-letter-expired-lease-with-fence:v1
local processing_key = KEYS[1]
local processing_meta_key = KEYS[2]
local retry_meta_key = KEYS[3]
local dead_letter_key = KEYS[4]
local fence_key = KEYS[5]

if redis.call("exists", fence_key) == 1 then
  return cjson.encode({status = "reconciliation_fenced"})
end

local raw = ARGV[1]
local message_id = ARGV[2]
local dead_letter_json = ARGV[3]
local remove_processing_meta = ARGV[4]
local expected_attempt_id = ARGV[5]
local expected_owner_token = ARGV[6]
local lease_metadata_json = redis.call("hget", processing_meta_key, message_id)
if not lease_metadata_json then
  lease_metadata_json = redis.call("hget", retry_meta_key, message_id)
end
local lease_ok, lease_metadata = pcall(cjson.decode, lease_metadata_json or "")
if not lease_ok or type(lease_metadata) ~= "table"
  or tostring(lease_metadata["message_id"] or "") ~= message_id
  or tostring(lease_metadata["attempt_id"] or "") ~= expected_attempt_id
  or tostring(lease_metadata["owner_token"] or "") ~= expected_owner_token then
  return cjson.encode({status = "stale_owner"})
end

redis.call("lrem", processing_key, 1, raw)
if remove_processing_meta == "1" then
  redis.call("hdel", processing_meta_key, message_id)
end
redis.call("rpush", dead_letter_key, dead_letter_json)
redis.call("hdel", retry_meta_key, message_id)

return cjson.encode({status = "dead_lettered"})
"""


RECORD_LEGACY_LEASE_WITH_FENCE_SCRIPT = """
-- ai-platform:record-legacy-lease-with-fence:v1
local processing_key = KEYS[1]
local queued_key = KEYS[2]
local processing_meta_key = KEYS[3]
local retry_meta_key = KEYS[4]
local worker_heartbeat_key = KEYS[5]
local queued_meta_key = KEYS[6]
local queued_run_index_key = KEYS[7]
local queued_order_key = KEYS[8]
local queued_sequence_key = KEYS[9]
local fence_key = KEYS[10]

local raw = ARGV[1]
if redis.call("exists", fence_key) == 1 then
  redis.call("lrem", processing_key, 1, raw)
  local message_id = ARGV[2]
  local run_index_field = ARGV[6]
  local ok, metadata = pcall(cjson.decode, ARGV[3])
  if ok and type(metadata) == "table" then
    local sequence = redis.call("incr", queued_sequence_key)
    metadata["sequence"] = sequence
    metadata["raw"] = raw
    redis.call("rpush", queued_key, raw)
    redis.call("hset", queued_meta_key, message_id, cjson.encode(metadata))
    redis.call("hset", queued_run_index_key, run_index_field, cjson.encode({message_id}))
    redis.call("zadd", queued_order_key, sequence, message_id)
  end
  return cjson.encode({status = "reconciliation_fenced"})
end
local message_id = ARGV[2]
local metadata_json = ARGV[3]
local worker_id = ARGV[4]
local now = ARGV[5]
redis.call("hset", processing_meta_key, message_id, metadata_json)
redis.call("hset", retry_meta_key, message_id, metadata_json)
redis.call("hset", worker_heartbeat_key, worker_id, now)
return cjson.encode({status = "leased"})
"""


HEARTBEAT_WITH_FENCE_SCRIPT = """
-- ai-platform:heartbeat-run-with-fence:v1
local raw_metadata = redis.call("hget", KEYS[1], ARGV[1])
if not raw_metadata then
  return cjson.encode({status = "missing"})
end
local ok, metadata = pcall(cjson.decode, raw_metadata)
if not ok or type(metadata) ~= "table" then
  return cjson.encode({status = "inconclusive"})
end
if tostring(metadata["message_id"] or "") ~= ARGV[1]
  or tostring(metadata["attempt_id"] or "") ~= ARGV[2]
  or tostring(metadata["owner_token"] or "") ~= ARGV[3]
  or tostring(metadata["worker_id"] or "") ~= ARGV[4] then
  return cjson.encode({status = "stale_owner"})
end
local fence_key = ARGV[6] .. ":" .. tostring(metadata["tenant_id"] or "") .. ":" .. tostring(metadata["run_id"] or "")
if redis.call("exists", fence_key) == 1 then
  return cjson.encode({status = "reconciliation_fenced"})
end
metadata["heartbeat_at"] = tonumber(ARGV[5])
redis.call("hset", KEYS[1], ARGV[1], cjson.encode(metadata))
redis.call("hset", KEYS[2], ARGV[4], ARGV[5])
return cjson.encode({status = "heartbeat"})
"""


ACK_LEASE_SCRIPT = """
-- ai-platform:ack-run-lease:v1
local metadata_json = redis.call("hget", KEYS[2], ARGV[2])
local ok, metadata = pcall(cjson.decode, metadata_json or "")
if not ok or type(metadata) ~= "table"
  or tostring(metadata["message_id"] or "") ~= ARGV[2]
  or tostring(metadata["attempt_id"] or "") ~= ARGV[3]
  or tostring(metadata["owner_token"] or "") ~= ARGV[4]
  or tostring(metadata["raw"] or "") ~= ARGV[1] then
  return cjson.encode({status = "stale_owner"})
end
local expected_fence_key = ARGV[5] .. ":" .. tostring(metadata["tenant_id"] or "") .. ":" .. tostring(metadata["run_id"] or "")
if KEYS[4] ~= expected_fence_key then
  return cjson.encode({status = "stale_owner"})
end
if redis.call("exists", KEYS[4]) == 1 then
  return cjson.encode({status = "reconciliation_fenced"})
end
redis.call("lrem", KEYS[1], 1, ARGV[1])
redis.call("hdel", KEYS[2], ARGV[2])
redis.call("hdel", KEYS[3], ARGV[2])
return cjson.encode({status = "acked"})
"""


FAIL_LEASE_SCRIPT = """
-- ai-platform:fail-run-lease:v1
local metadata_json = redis.call("hget", KEYS[2], ARGV[2])
local ok, metadata = pcall(cjson.decode, metadata_json or "")
if not ok or type(metadata) ~= "table"
  or tostring(metadata["message_id"] or "") ~= ARGV[2]
  or tostring(metadata["attempt_id"] or "") ~= ARGV[3]
  or tostring(metadata["owner_token"] or "") ~= ARGV[4]
  or tostring(metadata["raw"] or "") ~= ARGV[1] then
  return cjson.encode({status = "stale_owner"})
end
local expected_fence_key = ARGV[6] .. ":" .. tostring(metadata["tenant_id"] or "") .. ":" .. tostring(metadata["run_id"] or "")
if KEYS[5] ~= expected_fence_key then
  return cjson.encode({status = "stale_owner"})
end
if redis.call("exists", KEYS[5]) == 1 then
  return cjson.encode({status = "reconciliation_fenced"})
end
local dead_letter = cjson.decode(ARGV[5])
dead_letter["attempts"] = tonumber(metadata["attempts"] or 0)
redis.call("lrem", KEYS[1], 1, ARGV[1])
redis.call("hdel", KEYS[2], ARGV[2])
redis.call("hdel", KEYS[3], ARGV[2])
redis.call("rpush", KEYS[4], cjson.encode(dead_letter))
return cjson.encode({status = "failed"})
"""


@dataclass(frozen=True)
class QueueKeys:
    queued: str
    processing: str
    processing_meta: str
    retry_meta: str
    dead_letter: str
    worker_heartbeat: str
    queued_meta: str
    queued_run_index: str
    queued_order: str
    queued_sequence: str
    reconciliation_fence_prefix: str


@dataclass(frozen=True)
class QueueMessage:
    raw: str
    payload: dict[str, Any]
    message_id: str
    queue_message_id: str
    attempt_id: str
    owner_token: str


QUEUE_ATTEMPT_ID_FIELD = "_queue_attempt_id"


def _new_lease_secret(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(32)}"


def _lease_handle(message_id: str, attempt_id: str, owner_token: str) -> str:
    return f"qls1:{message_id}:{attempt_id}:{owner_token}"


def _parse_lease_handle(value: str) -> tuple[str, str, str] | None:
    parts = str(value or "").split(":")
    if (
        len(parts) != 4
        or parts[0] != "qls1"
        or not re.fullmatch(r"[0-9a-f]{64}", parts[1])
        or not re.fullmatch(r"qat_[0-9a-f]{64}", parts[2])
        or not re.fullmatch(r"qown_[0-9a-f]{64}", parts[3])
    ):
        return None
    return parts[1], parts[2], parts[3]


def _leased_payload(payload: dict[str, Any], *, attempt_id: str) -> dict[str, Any]:
    leased_payload = dict(payload)
    leased_payload[QUEUE_ATTEMPT_ID_FIELD] = attempt_id
    return leased_payload


def _lease_fence_key(keys: QueueKeys, raw: str) -> str | None:
    try:
        payload = QueueRunPayload.model_validate_json(raw)
    except ValidationError:
        return None
    return f"{keys.reconciliation_fence_prefix}:{payload.tenant_id}:{payload.run_id}"


@dataclass(frozen=True)
class QueueAdmissionMetadata:
    """Trusted queue admission metadata returned from Redis enqueue state."""

    queue_position: int
    queue_admission_ordinal: int
    message_id: str
    source: str = "redis_metadata"


@dataclass(frozen=True)
class RunReconciliationFence:
    """Opaque exact-run Redis ownership fence held across DB terminalization."""

    tenant_id: str
    run_id: str
    owner_token: str
    fence_key: str


class QueueAdmissionRejected(ValueError):
    """A deterministic local rejection that occurs before Redis admission begins."""


async def get_redis() -> Redis:
    settings = get_settings()
    return Redis.from_url(settings.redis_url, decode_responses=True)


def get_queue_keys() -> QueueKeys:
    prefix = get_settings().queue_key_prefix.strip().rstrip(":") or DEFAULT_QUEUE_KEY_PREFIX
    return QueueKeys(
        queued=f"{prefix}:queued",
        processing=f"{prefix}:processing",
        processing_meta=f"{prefix}:processing-meta",
        retry_meta=f"{prefix}:retry-meta",
        dead_letter=f"{prefix}:dead-letter",
        worker_heartbeat=f"{prefix}:worker-heartbeat",
        queued_meta=f"{prefix}:queued-meta",
        queued_run_index=f"{prefix}:queued-run-index",
        queued_order=f"{prefix}:queued-order",
        queued_sequence=f"{prefix}:queued-sequence",
        reconciliation_fence_prefix=f"{prefix}:reconciliation-fence",
    )


def message_id_for_raw(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def queued_run_index_field(*, tenant_id: str, run_id: str) -> str:
    return f"{tenant_id}:{run_id}"


def reconciliation_fence_key(*, tenant_id: str, run_id: str) -> str:
    keys = get_queue_keys()
    return f"{keys.reconciliation_fence_prefix}:{tenant_id}:{run_id}"


def _decode_run_index_message_ids(raw_index: Any) -> list[str]:
    if not raw_index:
        return []
    if isinstance(raw_index, list):
        return [str(item) for item in raw_index if str(item)]
    if not isinstance(raw_index, str):
        return [str(raw_index)]
    try:
        decoded = json.loads(raw_index)
    except json.JSONDecodeError:
        return [raw_index]
    if isinstance(decoded, list):
        return [str(item) for item in decoded if str(item)]
    return [raw_index]


def _now() -> float:
    return time.time()


def _dead_letter_json(
    *,
    raw: str,
    error_code: str,
    error_message: str,
    attempts: int | None = None,
    worker_id: str | None = None,
) -> str:
    return json.dumps(
        {
            "schema_version": "ai-platform.dead-letter.v1",
            "error_code": error_code,
            "error_message": error_message,
            "attempts": attempts,
            "worker_id": worker_id,
            "raw": raw,
            "created_at": _now(),
        },
        ensure_ascii=False,
    )


async def enqueue_run_with_metadata(payload: dict[str, Any]) -> QueueAdmissionMetadata:
    """Enqueue a run and return the Redis-derived admission ordinal."""

    try:
        validated = QueueRunPayload.model_validate(payload)
    except ValidationError as exc:
        raise QueueAdmissionRejected("queue_payload_invalid") from exc
    keys = get_queue_keys()
    redis = await get_redis()
    raw = validated.model_dump_json()
    message_id = message_id_for_raw(raw)
    metadata = {
        "run_id": validated.run_id,
        "tenant_id": validated.tenant_id,
        "workspace_id": validated.workspace_id,
        "user_id": validated.user_id,
        "enqueued_at": _now(),
    }
    try:
        result = _decode_redis_script_result(
            await redis.eval(
                ENQUEUE_WITH_METADATA_SCRIPT,
                8,
                keys.queued,
                keys.queued_meta,
                keys.queued_run_index,
                keys.queued_order,
                keys.queued_sequence,
                keys.processing_meta,
                keys.retry_meta,
                reconciliation_fence_key(tenant_id=validated.tenant_id, run_id=validated.run_id),
                raw,
                message_id,
                queued_run_index_field(tenant_id=validated.tenant_id, run_id=validated.run_id),
                json.dumps(metadata, ensure_ascii=False),
            )
        )
        status = str(result.get("status") or "")
        if status == "reconciliation_fenced":
            raise QueueAdmissionRejected("run_reconciliation_in_progress")
        if status == "already_leased":
            return QueueAdmissionMetadata(
                queue_position=0,
                queue_admission_ordinal=0,
                message_id=message_id,
                source="redis_existing_lease",
            )
        return QueueAdmissionMetadata(
            queue_position=int(result.get("position") or 1),
            queue_admission_ordinal=int(result.get("sequence") or result.get("position") or 1),
            message_id=message_id,
        )
    finally:
        await redis.aclose()


async def enqueue_run(payload: dict[str, Any]) -> int:
    metadata = await enqueue_run_with_metadata(payload)
    return metadata.queue_position


def _queue_metadata_matches_run(
    raw_metadata: object,
    *,
    tenant_id: str,
    run_id: str,
) -> dict[str, Any] | None:
    """Decode one bounded Redis metadata row only when it belongs to this run."""

    try:
        metadata = json.loads(raw_metadata)
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(metadata, dict):
        return None
    if metadata.get("tenant_id") != tenant_id or metadata.get("run_id") != run_id:
        return None
    return metadata


async def read_queue_admission(payload: dict[str, Any]) -> QueueAdmissionMetadata | None:
    """Boundedly reconcile one immutable enqueue attempt without enqueuing again.

    The deterministic payload hash identifies the exact Redis message.  The
    read checks queued, leased, and retry metadata only; it never scans queue
    contents or sends another enqueue command.
    """

    try:
        validated = QueueRunPayload.model_validate(payload)
    except ValidationError as exc:
        raise QueueAdmissionRejected("queue_payload_invalid") from exc
    keys = get_queue_keys()
    raw = validated.model_dump_json()
    message_id = message_id_for_raw(raw)
    redis = await get_redis()
    try:
        queued_metadata = _queue_metadata_matches_run(
            await redis.hget(keys.queued_meta, message_id),
            tenant_id=validated.tenant_id,
            run_id=validated.run_id,
        )
        if queued_metadata is not None:
            rank = await redis.zrank(keys.queued_order, message_id)
            if rank is not None:
                position = int(rank) + 1
                sequence = int(queued_metadata.get("sequence") or position)
                return QueueAdmissionMetadata(
                    queue_position=position,
                    queue_admission_ordinal=sequence,
                    message_id=message_id,
                    source="redis_readback_queued",
                )
        for metadata_key, source in (
            (keys.processing_meta, "redis_readback_processing"),
            (keys.retry_meta, "redis_readback_retry"),
        ):
            metadata = _queue_metadata_matches_run(
                await redis.hget(metadata_key, message_id),
                tenant_id=validated.tenant_id,
                run_id=validated.run_id,
            )
            if metadata is not None:
                return QueueAdmissionMetadata(
                    queue_position=0,
                    queue_admission_ordinal=0,
                    message_id=message_id,
                    source=source,
                )
        return None
    finally:
        await redis.aclose()


async def acquire_run_reconciliation_fence(
    *,
    tenant_id: str,
    run_id: str,
    scan_limit: int,
    ttl_seconds: int,
    owner_token: str | None = None,
) -> RunReconciliationFence | None:
    """Atomically fence one exact run only when no authoritative owner is live."""

    bounded_limit = max(int(scan_limit), 1)
    bounded_ttl = max(int(ttl_seconds), 1)
    resolved_token = owner_token or uuid.uuid4().hex
    keys = get_queue_keys()
    fence_key = reconciliation_fence_key(tenant_id=tenant_id, run_id=run_id)
    settings = get_settings()
    redis = await get_redis()
    try:
        result = _decode_redis_script_result(
            await redis.eval(
                ACQUIRE_RECONCILIATION_FENCE_SCRIPT,
                8,
                keys.queued,
                keys.processing,
                keys.queued_meta,
                keys.processing_meta,
                keys.retry_meta,
                keys.worker_heartbeat,
                keys.queued_run_index,
                fence_key,
                tenant_id,
                run_id,
                queued_run_index_field(tenant_id=tenant_id, run_id=run_id),
                resolved_token,
                _now(),
                float(settings.worker_heartbeat_ttl_seconds),
                bounded_limit,
                bounded_ttl * 1000,
            )
        )
        if str(result.get("status") or "") != "claimed":
            return None
        return RunReconciliationFence(
            tenant_id=tenant_id,
            run_id=run_id,
            owner_token=resolved_token,
            fence_key=fence_key,
        )
    finally:
        await redis.aclose()


async def release_run_reconciliation_fence(fence: RunReconciliationFence) -> bool:
    """Release an exact-run fence only when the opaque owner token still matches."""

    redis = await get_redis()
    try:
        released = await redis.eval(
            RELEASE_RECONCILIATION_FENCE_SCRIPT,
            1,
            fence.fence_key,
            fence.owner_token,
        )
        return bool(int(released or 0))
    finally:
        await redis.aclose()


async def renew_run_reconciliation_fence(fence: RunReconciliationFence, *, ttl_seconds: int) -> bool:
    """Extend a fence only when its opaque owner token still matches."""

    bounded_ttl = max(int(ttl_seconds), 1)
    redis = await get_redis()
    try:
        result = _decode_redis_script_result(
            await redis.eval(
                RENEW_RECONCILIATION_FENCE_SCRIPT,
                1,
                fence.fence_key,
                fence.owner_token,
                bounded_ttl * 1000,
            )
        )
        return str(result.get("status") or "") == "renewed"
    finally:
        await redis.aclose()


async def remove_queued_run(*, tenant_id: str, run_id: str) -> int:
    keys = get_queue_keys()
    redis = await get_redis()
    try:
        result = _decode_redis_script_result(
            await redis.eval(
                REMOVE_QUEUED_WITH_METADATA_SCRIPT,
                4,
                keys.queued,
                keys.queued_meta,
                keys.queued_run_index,
                keys.queued_order,
                queued_run_index_field(tenant_id=tenant_id, run_id=run_id),
                tenant_id,
                run_id,
            )
        )
        removed = int(result.get("removed") or 0)
        status = str(result.get("status") or "")
        if removed > 0 or status not in {"missing_index", "missing_metadata"}:
            return removed
        return await _remove_queued_run_bounded_fallback(
            redis,
            keys,
            tenant_id=tenant_id,
            run_id=run_id,
            scan_limit=int(getattr(get_settings(), "queue_metadata_fallback_scan_limit", 500)),
        )
    finally:
        await redis.aclose()


async def _remove_queued_run_bounded_fallback(
    redis: Redis,
    keys: QueueKeys,
    *,
    tenant_id: str,
    run_id: str,
    scan_limit: int,
) -> int:
    if scan_limit <= 0:
        return 0
    queued_items = await redis.lrange(keys.queued, -int(scan_limit), -1)
    removed_total = 0
    for raw in queued_items:
        try:
            payload = QueueRunPayload.model_validate_json(raw)
        except Exception:
            continue
        if payload.tenant_id != tenant_id or payload.run_id != run_id:
            continue
        message_id = message_id_for_raw(raw)
        removed_total += int(await redis.lrem(keys.queued, 0, raw) or 0)
        await _delete_queued_metadata_for_payload(
            redis,
            keys,
            message_id=message_id,
            tenant_id=payload.tenant_id,
            run_id=payload.run_id,
        )
    return removed_total


async def get_queue_status() -> dict[str, Any]:
    keys = get_queue_keys()
    settings = get_settings()
    redis = await get_redis()
    try:
        queued = await redis.llen(keys.queued)
        processing = await redis.llen(keys.processing)
        dead_letter = await redis.llen(keys.dead_letter)
        processing_items = await redis.lrange(keys.processing, 0, -1)
        processing_meta = await redis.hgetall(keys.processing_meta)
        worker_heartbeats = await redis.hgetall(keys.worker_heartbeat)
        now = _now()
        active_worker_heartbeats = _active_worker_heartbeats(
            worker_heartbeats,
            now=now,
            ttl_seconds=float(getattr(settings, "worker_heartbeat_ttl_seconds", 60.0)),
        )
        processing_state = _processing_state_snapshot(
            processing_items,
            processing_meta,
            active_worker_heartbeats=active_worker_heartbeats,
            now=now,
            visibility_timeout_seconds=int(
                getattr(settings, "queue_lease_visibility_timeout_seconds", DEFAULT_VISIBILITY_TIMEOUT_SECONDS)
            ),
        )
        return {
            "depths": {
                "queued": int(queued),
                "processing": int(processing),
                "dead_letter": int(dead_letter),
            },
            "processing_state": processing_state,
            "keys": {
                "queued": keys.queued,
                "processing": keys.processing,
                "processing_meta": keys.processing_meta,
                "retry_meta": keys.retry_meta,
                "dead_letter": keys.dead_letter,
                "worker_heartbeat": keys.worker_heartbeat,
            },
            "workers": sorted(active_worker_heartbeats.keys()),
        }
    finally:
        await redis.aclose()


def _count_queued_for_tenant(raw_items: list[str], tenant_id: str) -> int:
    count = 0
    for raw in raw_items:
        try:
            payload = QueueRunPayload.model_validate_json(raw)
        except Exception:
            continue
        if payload.tenant_id == tenant_id:
            count += 1
    return count


def _count_processing_for_tenant(meta_items: dict[str, str], tenant_id: str) -> int:
    count = 0
    for raw_meta in meta_items.values():
        try:
            meta = json.loads(raw_meta)
        except (TypeError, json.JSONDecodeError):
            continue
        if meta.get("tenant_id") == tenant_id:
            count += 1
    return count


def _queued_quota_counts(raw_items: list[str], tenant_id: str) -> tuple[int, dict[str, int]]:
    tenant_queued = 0
    user_queued: dict[str, int] = {}
    for raw in raw_items:
        try:
            payload = QueueRunPayload.model_validate_json(raw)
        except Exception:
            continue
        if payload.tenant_id != tenant_id:
            continue
        tenant_queued += 1
        user_queued[payload.user_id] = user_queued.get(payload.user_id, 0) + 1
    return tenant_queued, user_queued


def _processing_quota_counts_from_raw_items(
    raw_items: list[str],
) -> tuple[dict[str, int], dict[tuple[str, str], int]]:
    tenant_counts: dict[str, int] = {}
    user_counts: dict[tuple[str, str], int] = {}
    for raw in raw_items:
        try:
            payload = QueueRunPayload.model_validate_json(raw)
        except Exception:
            continue
        tenant_counts[payload.tenant_id] = tenant_counts.get(payload.tenant_id, 0) + 1
        key = (payload.tenant_id, payload.user_id)
        user_counts[key] = user_counts.get(key, 0) + 1
    return tenant_counts, user_counts


def _processing_quota_counts(meta_items: dict[str, str]) -> tuple[dict[str, int], dict[tuple[str, str], int]]:
    tenant_counts: dict[str, int] = {}
    user_counts: dict[tuple[str, str], int] = {}
    for raw_meta in meta_items.values():
        try:
            meta = json.loads(raw_meta)
        except (TypeError, json.JSONDecodeError):
            continue
        tenant_id = str(meta.get("tenant_id") or "")
        user_id = str(meta.get("user_id") or "")
        if tenant_id:
            tenant_counts[tenant_id] = tenant_counts.get(tenant_id, 0) + 1
        if tenant_id and user_id:
            key = (tenant_id, user_id)
            user_counts[key] = user_counts.get(key, 0) + 1
    return tenant_counts, user_counts


def _quota_snapshot(
    payload: QueueRunPayload,
    *,
    tenant_counts: dict[str, int],
    user_counts: dict[tuple[str, str], int],
    tenant_processing_limit: int,
    user_processing_limit: int,
) -> dict[str, Any]:
    tenant_processing = tenant_counts.get(payload.tenant_id, 0)
    user_processing = user_counts.get((payload.tenant_id, payload.user_id), 0)
    return {
        "tenant_processing": tenant_processing,
        "tenant_processing_limit": tenant_processing_limit,
        "tenant_processing_saturated": tenant_processing_limit > 0 and tenant_processing >= tenant_processing_limit,
        "user_processing": user_processing,
        "user_processing_limit": user_processing_limit,
        "user_processing_saturated": user_processing_limit > 0 and user_processing >= user_processing_limit,
    }


def _quota_allows(snapshot: dict[str, Any]) -> bool:
    return not bool(snapshot["tenant_processing_saturated"] or snapshot["user_processing_saturated"])


def _next_attempts(*, retry_meta: str | None, existing_meta: str | None) -> int:
    attempts = 1
    if retry_meta:
        try:
            attempts = int(json.loads(retry_meta).get("attempts", 0)) + 1
        except (TypeError, ValueError, json.JSONDecodeError):
            attempts = 1
    elif existing_meta:
        try:
            attempts = int(json.loads(existing_meta).get("attempts", 0)) + 1
        except (TypeError, ValueError, json.JSONDecodeError):
            attempts = 1
    return attempts


def _decode_redis_script_result(raw_result: object) -> dict[str, Any]:
    if isinstance(raw_result, dict):
        return raw_result
    if isinstance(raw_result, bytes):
        raw_result = raw_result.decode("utf-8", errors="replace")
    if not isinstance(raw_result, str):
        return {"status": "invalid_result"}
    try:
        result = json.loads(raw_result)
    except json.JSONDecodeError:
        return {"status": "invalid_result"}
    return result if isinstance(result, dict) else {"status": "invalid_result"}


def _active_worker_heartbeats(
    worker_heartbeats: dict[str, str],
    *,
    now: float,
    ttl_seconds: float,
) -> dict[str, str]:
    if ttl_seconds <= 0:
        return dict(worker_heartbeats)
    active = {}
    for worker_id, raw_timestamp in worker_heartbeats.items():
        try:
            heartbeat_at = float(raw_timestamp)
        except (TypeError, ValueError):
            continue
        if now - heartbeat_at <= ttl_seconds:
            active[worker_id] = raw_timestamp
    return active


def _capacity_snapshot(*, processing: int, max_active_worker_runs: int) -> dict[str, Any]:
    if max_active_worker_runs <= 0:
        return {
            "max_active_worker_runs": max_active_worker_runs,
            "processing_saturated": False,
            "available_worker_slots": None,
        }
    return {
        "max_active_worker_runs": max_active_worker_runs,
        "processing_saturated": processing >= max_active_worker_runs,
        "available_worker_slots": max(max_active_worker_runs - processing, 0),
    }


def _processing_state_snapshot(
    raw_items: list[str],
    meta_items: dict[str, str],
    *,
    active_worker_heartbeats: dict[str, str],
    now: float,
    visibility_timeout_seconds: int,
    tenant_id: str | None = None,
) -> dict[str, int]:
    active = 0
    stale = 0
    reclaimable = 0
    missing_metadata = 0
    for raw in raw_items:
        message_id = message_id_for_raw(raw)
        raw_meta = meta_items.get(message_id)
        payload_tenant_id = ""
        if tenant_id:
            try:
                payload_tenant_id = QueueRunPayload.model_validate_json(raw).tenant_id
            except Exception:
                payload_tenant_id = ""
        if not raw_meta:
            if tenant_id and payload_tenant_id != tenant_id:
                continue
            missing_metadata += 1
            stale += 1
            reclaimable += 1
            continue
        try:
            meta = json.loads(raw_meta)
        except (TypeError, json.JSONDecodeError):
            meta = {}
        meta_tenant_id = str(meta.get("tenant_id") or "")
        if tenant_id and (meta_tenant_id or payload_tenant_id) != tenant_id:
            continue
        worker_id = str(meta.get("worker_id") or "")
        try:
            heartbeat_at = float(meta.get("heartbeat_at") or meta.get("leased_at") or 0)
        except (TypeError, ValueError):
            heartbeat_at = 0.0
        lease_expired = visibility_timeout_seconds > 0 and now - heartbeat_at > visibility_timeout_seconds
        worker_active = bool(worker_id and worker_id in active_worker_heartbeats)
        if lease_expired:
            reclaimable += 1
        if lease_expired or not worker_active:
            stale += 1
        else:
            active += 1
    return {
        "active": active,
        "stale": stale,
        "reclaimable": reclaimable,
        "missing_metadata": missing_metadata,
    }


def _queue_throttling_snapshot(
    *,
    tenant_id: str,
    tenant_counts: dict[str, int],
    user_counts: dict[tuple[str, str], int],
    user_queued_counts: dict[str, int],
    tenant_processing_limit: int,
    user_processing_limit: int,
    user_id: str | None,
    include_user_breakdown: bool,
) -> dict[str, Any]:
    tenant_processing = tenant_counts.get(tenant_id, 0)
    snapshot: dict[str, Any] = {
        "tenant_processing": tenant_processing,
        "tenant_processing_limit": tenant_processing_limit,
        "tenant_processing_saturated": tenant_processing_limit > 0 and tenant_processing >= tenant_processing_limit,
        "user_processing_limit": user_processing_limit,
    }
    if user_id:
        user_processing = user_counts.get((tenant_id, user_id), 0)
        snapshot["current_user"] = {
            "queued": user_queued_counts.get(user_id, 0),
            "processing": user_processing,
            "processing_saturated": user_processing_limit > 0 and user_processing >= user_processing_limit,
        }
    if include_user_breakdown:
        user_ids = sorted(
            {
                user
                for candidate_tenant_id, user in user_counts
                if candidate_tenant_id == tenant_id
            }
            | set(user_queued_counts)
        )
        snapshot["users"] = {
            user: {
                "queued": user_queued_counts.get(user, 0),
                "processing": user_counts.get((tenant_id, user), 0),
                "processing_saturated": user_processing_limit > 0
                and user_counts.get((tenant_id, user), 0) >= user_processing_limit,
            }
            for user in user_ids
        }
    else:
        snapshot["users"] = {}
    return snapshot


def _queue_reason(
    *,
    queued: int,
    processing: int,
    active_workers: int,
    max_active_worker_runs: int,
    processing_state: dict[str, int],
) -> str:
    if processing_state.get("reclaimable", 0) > 0:
        return "processing_lease_reclaimable"
    if processing_state.get("stale", 0) > 0:
        return "processing_lease_stale"
    if queued <= 0 and processing <= 0:
        return "worker_available"
    if max_active_worker_runs > 0 and processing >= max_active_worker_runs:
        return "worker_capacity_full"
    if active_workers > processing:
        return "worker_available"
    if active_workers > 0 and processing >= active_workers:
        return "workers_busy"
    return "queued_behind_existing_work"


async def get_queue_insight(
    tenant_id: str,
    *,
    user_id: str | None = None,
    include_user_breakdown: bool = False,
) -> dict[str, Any]:
    keys = get_queue_keys()
    settings = get_settings()
    redis = await get_redis()
    try:
        queued_depth = int(await redis.llen(keys.queued))
        processing_depth = int(await redis.llen(keys.processing))
        dead_letter_depth = int(await redis.llen(keys.dead_letter))
        queued_scan_limit = int(getattr(settings, "queue_insight_scan_limit", 500))
        queued_items = (
            await redis.lrange(keys.queued, -queued_scan_limit, -1)
            if queued_scan_limit > 0
            else []
        )
        processing_items = await redis.lrange(keys.processing, 0, -1)
        worker_heartbeats = await redis.hgetall(keys.worker_heartbeat)
        now = _now()
        active_worker_heartbeats = _active_worker_heartbeats(
            worker_heartbeats,
            now=now,
            ttl_seconds=float(getattr(settings, "worker_heartbeat_ttl_seconds", 60.0)),
        )
        active_workers = len(active_worker_heartbeats)
        processing_meta = await redis.hgetall(keys.processing_meta)
        processing_state = _processing_state_snapshot(
            processing_items,
            processing_meta,
            active_worker_heartbeats=active_worker_heartbeats,
            now=now,
            visibility_timeout_seconds=int(
                getattr(settings, "queue_lease_visibility_timeout_seconds", DEFAULT_VISIBILITY_TIMEOUT_SECONDS)
            ),
            tenant_id=tenant_id,
        )
        max_active_worker_runs = int(settings.max_active_worker_runs)
        capacity = _capacity_snapshot(
            processing=processing_depth,
            max_active_worker_runs=max_active_worker_runs,
        )
        tenant_processing_limit = int(getattr(settings, "queue_tenant_processing_limit", 0))
        user_processing_limit = int(getattr(settings, "queue_user_processing_limit", 0))
        lease_scan_limit = int(getattr(settings, "queue_lease_scan_limit", 50))
        queue_sample = {
            "queued_scan_limit": queued_scan_limit,
            "queued_sampled": len(queued_items),
            "queued_sample_complete": queued_depth <= len(queued_items),
        }
        capacity.update(
            {
                "queue_tenant_processing_limit": tenant_processing_limit,
                "queue_user_processing_limit": user_processing_limit,
                "queue_lease_scan_limit": lease_scan_limit,
            }
        )
        tenant_counts, user_counts = _processing_quota_counts_from_raw_items(processing_items)
        tenant_queued, user_queued_counts = _queued_quota_counts(queued_items, tenant_id)
        throttling = _queue_throttling_snapshot(
            tenant_id=tenant_id,
            tenant_counts=tenant_counts,
            user_counts=user_counts,
            user_queued_counts=user_queued_counts,
            tenant_processing_limit=tenant_processing_limit,
            user_processing_limit=user_processing_limit,
            user_id=user_id,
            include_user_breakdown=include_user_breakdown,
        )
        reason = _queue_reason(
            queued=queued_depth,
            processing=processing_depth,
            active_workers=active_workers,
            max_active_worker_runs=max_active_worker_runs,
            processing_state=processing_state,
        )
        if tenant_queued > 0 and throttling["tenant_processing_saturated"]:
            reason = "tenant_quota_full"
        elif user_id:
            current_user = throttling.get("current_user") if isinstance(throttling.get("current_user"), dict) else {}
            if current_user.get("queued", 0) > 0 and current_user.get("processing_saturated"):
                reason = "user_quota_full"
        elif include_user_breakdown:
            users = throttling.get("users") if isinstance(throttling.get("users"), dict) else {}
            if any(
                isinstance(user_state, dict)
                and user_state.get("queued", 0) > 0
                and user_state.get("processing_saturated")
                for user_state in users.values()
            ):
                reason = "user_quota_full"
        return {
            "tenant_id": tenant_id,
            "reason": reason,
            "depths": {
                "queued": queued_depth,
                "processing": processing_depth,
                "dead_letter": dead_letter_depth,
                "tenant_queued": tenant_queued,
                "tenant_processing": tenant_counts.get(tenant_id, 0),
            },
            "workers": {"active": active_workers},
            "processing_state": processing_state,
            "capacity": capacity,
            "queue_sample": queue_sample,
            "throttling": throttling,
        }
    finally:
        await redis.aclose()


async def get_run_queue_position(*, tenant_id: str, run_id: str) -> int | None:
    keys = get_queue_keys()
    redis = await get_redis()
    try:
        index_field = queued_run_index_field(tenant_id=tenant_id, run_id=run_id)
        raw_index = await redis.hget(keys.queued_run_index, index_field)
        message_ids = _decode_run_index_message_ids(raw_index)
        if not message_ids:
            return None
        valid_message_ids: list[str] = []
        best_rank: int | None = None
        for message_id in message_ids:
            raw_metadata = await redis.hget(keys.queued_meta, message_id)
            if not raw_metadata:
                await redis.zrem(keys.queued_order, message_id)
                continue
            try:
                metadata = json.loads(raw_metadata)
            except (TypeError, json.JSONDecodeError):
                await redis.hdel(keys.queued_meta, message_id)
                await redis.zrem(keys.queued_order, message_id)
                continue
            if metadata.get("tenant_id") != tenant_id or metadata.get("run_id") != run_id:
                continue
            rank = await redis.zrank(keys.queued_order, message_id)
            if rank is None:
                continue
            valid_message_ids.append(message_id)
            rank_int = int(rank)
            if best_rank is None or rank_int < best_rank:
                best_rank = rank_int
        if valid_message_ids:
            if valid_message_ids != message_ids:
                await redis.hset(keys.queued_run_index, index_field, json.dumps(valid_message_ids, ensure_ascii=False))
            return int(best_rank or 0) + 1
        if raw_index:
            await redis.hdel(keys.queued_run_index, index_field)
        return None
    finally:
        await redis.aclose()


async def _record_legacy_lease_with_fence(
    redis: Redis,
    keys: QueueKeys,
    *,
    raw: str,
    message_id: str,
    payload: dict[str, Any],
    attempts: int,
    worker_id: str,
    now: float,
) -> bool:
    """Atomically record legacy lease ownership or yield to a reconciliation fence."""

    metadata = {
        "message_id": message_id,
        "raw": raw,
        "attempts": attempts,
        "leased_at": now,
        "heartbeat_at": now,
        "worker_id": worker_id,
        "run_id": payload["run_id"],
        "tenant_id": payload["tenant_id"],
        "user_id": payload["user_id"],
    }
    result = _decode_redis_script_result(
        await redis.eval(
            RECORD_LEGACY_LEASE_WITH_FENCE_SCRIPT,
            10,
            keys.processing,
            keys.queued,
            keys.processing_meta,
            keys.retry_meta,
            keys.worker_heartbeat,
            keys.queued_meta,
            keys.queued_run_index,
            keys.queued_order,
            keys.queued_sequence,
            reconciliation_fence_key(tenant_id=str(payload["tenant_id"]), run_id=str(payload["run_id"])),
            raw,
            message_id,
            json.dumps(metadata, ensure_ascii=False),
            worker_id,
            now,
            queued_run_index_field(tenant_id=str(payload["tenant_id"]), run_id=str(payload["run_id"])),
        )
    )
    return str(result.get("status") or "") == "leased"


async def _requeue_run_with_fence(
    redis: Redis,
    keys: QueueKeys,
    *,
    raw: str,
    retry_metadata: dict[str, Any],
    expected_attempt_id: str,
    expected_owner_token: str,
    remove_processing: bool = False,
) -> bool:
    """Atomically requeue retry ownership only when the exact run is unfenced."""

    payload = QueueRunPayload.model_validate_json(raw)
    message_id = message_id_for_raw(raw)
    metadata = {
        "run_id": payload.run_id,
        "tenant_id": payload.tenant_id,
        "workspace_id": payload.workspace_id,
        "user_id": payload.user_id,
        "enqueued_at": _now(),
    }
    result = _decode_redis_script_result(
        await redis.eval(
            REQUEUE_WITH_FENCE_SCRIPT,
            9,
            keys.queued,
            keys.processing,
            keys.queued_meta,
            keys.queued_run_index,
            keys.queued_order,
            keys.queued_sequence,
            keys.retry_meta,
            keys.processing_meta,
            reconciliation_fence_key(tenant_id=payload.tenant_id, run_id=payload.run_id),
            raw,
            message_id,
            queued_run_index_field(tenant_id=payload.tenant_id, run_id=payload.run_id),
            json.dumps(metadata, ensure_ascii=False),
            json.dumps(retry_metadata, ensure_ascii=False),
            "1" if remove_processing else "0",
            expected_attempt_id,
            expected_owner_token,
        )
    )
    return str(result.get("status") or "") == "requeued"


async def _dead_letter_expired_lease_with_fence(
    redis: Redis,
    keys: QueueKeys,
    *,
    raw: str,
    message_id: str,
    error_code: str,
    error_message: str,
    attempts: int,
    worker_id: str | None,
    remove_processing_meta: bool,
    expected_attempt_id: str,
    expected_owner_token: str,
) -> bool:
    """Atomically dead-letter an expired lease unless its exact run is fenced."""

    try:
        payload = QueueRunPayload.model_validate_json(raw)
    except ValidationError:
        fence_key = f"{keys.reconciliation_fence_prefix}:invalid:{message_id}"
    else:
        fence_key = reconciliation_fence_key(tenant_id=payload.tenant_id, run_id=payload.run_id)
    result = _decode_redis_script_result(
        await redis.eval(
            DEAD_LETTER_EXPIRED_LEASE_WITH_FENCE_SCRIPT,
            5,
            keys.processing,
            keys.processing_meta,
            keys.retry_meta,
            keys.dead_letter,
            fence_key,
            raw,
            message_id,
            _dead_letter_json(
                raw=raw,
                error_code=error_code,
                error_message=error_message,
                attempts=attempts,
                worker_id=worker_id,
            ),
            "1" if remove_processing_meta else "0",
            expected_attempt_id,
            expected_owner_token,
        )
    )
    return str(result.get("status") or "") == "dead_lettered"


async def _remove_message_id_from_run_index(
    redis: Redis,
    keys: QueueKeys,
    *,
    tenant_id: str,
    run_id: str,
    message_id: str,
) -> None:
    index_field = queued_run_index_field(tenant_id=tenant_id, run_id=run_id)
    message_ids = _decode_run_index_message_ids(await redis.hget(keys.queued_run_index, index_field))
    if not message_ids:
        return
    remaining = [candidate for candidate in message_ids if candidate != message_id]
    if remaining:
        await redis.hset(keys.queued_run_index, index_field, json.dumps(remaining, ensure_ascii=False))
    else:
        await redis.hdel(keys.queued_run_index, index_field)


async def _delete_queued_metadata_for_payload(
    redis: Redis,
    keys: QueueKeys,
    *,
    message_id: str,
    tenant_id: str,
    run_id: str,
) -> None:
    await redis.hdel(keys.queued_meta, message_id)
    await _remove_message_id_from_run_index(
        redis,
        keys,
        tenant_id=tenant_id,
        run_id=run_id,
        message_id=message_id,
    )
    await redis.zrem(keys.queued_order, message_id)


async def _delete_queued_metadata_for_message_id(redis: Redis, keys: QueueKeys, *, message_id: str) -> None:
    raw_metadata = await redis.hget(keys.queued_meta, message_id)
    if raw_metadata:
        try:
            metadata = json.loads(raw_metadata)
        except (TypeError, json.JSONDecodeError):
            metadata = {}
        tenant_id = str(metadata.get("tenant_id") or "")
        run_id = str(metadata.get("run_id") or "")
        if tenant_id and run_id:
            await _remove_message_id_from_run_index(
                redis,
                keys,
                tenant_id=tenant_id,
                run_id=run_id,
                message_id=message_id,
            )
    await redis.hdel(keys.queued_meta, message_id)
    await redis.zrem(keys.queued_order, message_id)


async def _dead_letter_invalid_queue_payload(
    redis: Redis,
    keys: QueueKeys,
    *,
    raw: str,
    message_id: str,
    attempts: int,
    worker_id: str,
    remove_from_key: str,
    error_message: str,
) -> None:
    await redis.lrem(remove_from_key, 1, raw)
    await redis.rpush(
        keys.dead_letter,
        _dead_letter_json(
            raw=raw,
            error_code="invalid_queue_payload",
            error_message=error_message,
            attempts=attempts,
            worker_id=worker_id,
        ),
    )
    await redis.hdel(keys.retry_meta, message_id)
    await _delete_queued_metadata_for_message_id(redis, keys, message_id=message_id)


async def _dead_letter_invalid_queued_payload_atomic(
    redis: Redis,
    keys: QueueKeys,
    *,
    raw: str,
    message_id: str,
    worker_id: str,
    lease_scan_limit: int,
    absolute_index: int,
    error_message: str,
) -> dict[str, Any]:
    result = await redis.eval(
        DEAD_LETTER_INVALID_QUOTA_SCRIPT,
        7,
        keys.queued,
        keys.processing_meta,
        keys.retry_meta,
        keys.dead_letter,
        keys.queued_meta,
        keys.queued_run_index,
        keys.queued_order,
        raw,
        lease_scan_limit,
        absolute_index,
        message_id,
        worker_id,
        _now(),
        error_message,
    )
    return _decode_redis_script_result(result)


async def _lease_run_legacy(
    redis: Redis,
    keys: QueueKeys,
    *,
    timeout_seconds: int = 5,
    worker_id: str = "worker",
    max_processing_runs: int | None = None,
) -> QueueMessage | None:
    """Fail closed: non-atomic BRPOPLPUSH leasing is intentionally disabled."""

    del redis, keys, timeout_seconds, worker_id, max_processing_runs
    return None


async def _lease_run_with_quota(
    redis: Redis,
    keys: QueueKeys,
    *,
    worker_id: str,
    max_processing_runs: int | None,
    tenant_processing_limit: int,
    user_processing_limit: int,
    lease_scan_limit: int,
) -> QueueMessage | None:
    if max_processing_runs is not None and max_processing_runs > 0:
        processing_depth = int(await redis.llen(keys.processing))
        if processing_depth >= max_processing_runs:
            return None
    if lease_scan_limit <= 0:
        return None

    queued_depth = int(await redis.llen(keys.queued))
    if queued_depth <= 0:
        return None

    window_size = max(int(lease_scan_limit), 1)
    fairness_horizon = min(queued_depth, max(window_size * 4, window_size))
    scanned = 0
    while scanned < fairness_horizon:
        end_index = queued_depth - scanned - 1
        if end_index < 0:
            break
        min_index = max(queued_depth - fairness_horizon, 0)
        scan_start = max(end_index - window_size + 1, min_index)
        queued_items = await redis.lrange(keys.queued, scan_start, end_index)
        if not queued_items:
            break
        for raw_index, raw in reversed(list(enumerate(queued_items))):
            absolute_index = scan_start + raw_index
            message_id = message_id_for_raw(raw)
            try:
                payload_model = QueueRunPayload.model_validate_json(raw)
            except Exception as exc:
                await _dead_letter_invalid_queued_payload_atomic(
                    redis,
                    keys,
                    raw=raw,
                    message_id=message_id,
                    worker_id=worker_id,
                    lease_scan_limit=lease_scan_limit,
                    absolute_index=absolute_index,
                    error_message=str(exc),
                )
                continue

            attempt_id = _new_lease_secret("qat")
            owner_token = _new_lease_secret("qown")
            result = _decode_redis_script_result(
                await redis.eval(
                    LEASE_QUOTA_SCRIPT,
                    9,
                    keys.queued,
                    keys.processing,
                    keys.processing_meta,
                    keys.retry_meta,
                    keys.worker_heartbeat,
                    keys.queued_meta,
                    keys.queued_run_index,
                    keys.queued_order,
                    reconciliation_fence_key(
                        tenant_id=payload_model.tenant_id,
                        run_id=payload_model.run_id,
                    ),
                    raw,
                    lease_scan_limit,
                    absolute_index,
                    message_id,
                    worker_id,
                    _now(),
                    int(max_processing_runs or 0),
                    tenant_processing_limit,
                    user_processing_limit,
                    payload_model.tenant_id,
                    payload_model.user_id,
                    payload_model.run_id,
                    attempt_id,
                    owner_token,
                )
            )
            status = str(result.get("status") or "")
            if status == "capacity_full":
                return None
            if status == "reconciliation_fenced":
                continue
            if status in {"conflict", "quota_blocked"}:
                continue
            if status != "leased":
                continue
            attempts = result.get("attempts")
            if isinstance(attempts, bool) or not isinstance(attempts, int) or attempts < 1:
                continue
            if result.get("attempt_id") != attempt_id or result.get("owner_token") != owner_token:
                continue
            payload = _leased_payload(payload_model.model_dump(), attempt_id=attempt_id)
            return QueueMessage(
                raw=raw,
                payload=payload,
                message_id=_lease_handle(message_id, attempt_id, owner_token),
                queue_message_id=message_id,
                attempt_id=attempt_id,
                owner_token=owner_token,
            )
        scanned += end_index - scan_start + 1
    return None


async def lease_run(
    timeout_seconds: int = 5,
    *,
    worker_id: str = "worker",
    max_processing_runs: int | None = None,
    tenant_processing_limit: int | None = None,
    user_processing_limit: int | None = None,
    lease_scan_limit: int | None = None,
) -> QueueMessage | None:
    keys = get_queue_keys()
    redis = await get_redis()
    try:
        tenant_limit = int(tenant_processing_limit or 0)
        user_limit = int(user_processing_limit or 0)
        del timeout_seconds
        if lease_scan_limit is None:
            lease_scan_limit = int(getattr(get_settings(), "queue_lease_scan_limit", 50))
        return await _lease_run_with_quota(
            redis,
            keys,
            worker_id=worker_id,
            max_processing_runs=max_processing_runs,
            tenant_processing_limit=tenant_limit,
            user_processing_limit=user_limit,
            lease_scan_limit=int(lease_scan_limit),
        )
    finally:
        await redis.aclose()


async def ack_run(raw: str, *, message_id: str | None = None) -> bool:
    keys = get_queue_keys()
    redis = await get_redis()
    try:
        lease = _parse_lease_handle(str(message_id or ""))
        if lease is None:
            return False
        queue_message_id, attempt_id, owner_token = lease
        fence_key = _lease_fence_key(keys, raw)
        if fence_key is None:
            return False
        result = _decode_redis_script_result(
            await redis.eval(
                ACK_LEASE_SCRIPT,
                4,
                keys.processing,
                keys.processing_meta,
                keys.retry_meta,
                fence_key,
                raw,
                queue_message_id,
                attempt_id,
                owner_token,
                keys.reconciliation_fence_prefix,
            )
        )
        return str(result.get("status") or "") == "acked"
    finally:
        await redis.aclose()


async def fail_leased_run(
    raw: str,
    *,
    error_code: str,
    error_message: str,
    message_id: str | None = None,
    worker_id: str | None = None,
) -> bool:
    keys = get_queue_keys()
    redis = await get_redis()
    try:
        lease = _parse_lease_handle(str(message_id or ""))
        if lease is None:
            return False
        queue_message_id, attempt_id, owner_token = lease
        fence_key = _lease_fence_key(keys, raw)
        if fence_key is None:
            return False
        result = _decode_redis_script_result(
            await redis.eval(
                FAIL_LEASE_SCRIPT,
                5,
                keys.processing,
                keys.processing_meta,
                keys.retry_meta,
                keys.dead_letter,
                fence_key,
                raw,
                queue_message_id,
                attempt_id,
                owner_token,
                _dead_letter_json(
                    raw=raw,
                    error_code=error_code,
                    error_message=error_message,
                    attempts=None,
                    worker_id=worker_id,
                ),
                keys.reconciliation_fence_prefix,
            )
        )
        return str(result.get("status") or "") == "failed"
    finally:
        await redis.aclose()


async def heartbeat_run(message_id: str, *, worker_id: str) -> bool:
    keys = get_queue_keys()
    redis = await get_redis()
    try:
        lease = _parse_lease_handle(message_id)
        if lease is None:
            return False
        queue_message_id, attempt_id, owner_token = lease
        result = _decode_redis_script_result(
            await redis.eval(
            HEARTBEAT_WITH_FENCE_SCRIPT,
            2,
            keys.processing_meta,
            keys.worker_heartbeat,
            queue_message_id,
            attempt_id,
            owner_token,
            worker_id,
            _now(),
            keys.reconciliation_fence_prefix,
            )
        )
        return str(result.get("status") or "") == "heartbeat"
    finally:
        await redis.aclose()


async def reclaim_expired_leases(
    *,
    visibility_timeout_seconds: int = DEFAULT_VISIBILITY_TIMEOUT_SECONDS,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    now: float | None = None,
) -> dict[str, int]:
    redis = await get_redis()
    reclaimed = 0
    dead_lettered = 0
    checked_at = _now() if now is None else now
    keys = get_queue_keys()
    try:
        processing = await redis.lrange(keys.processing, 0, -1)
        for raw in processing:
            message_id = message_id_for_raw(raw)
            raw_meta = await redis.hget(keys.processing_meta, message_id)
            if not raw_meta:
                retry_meta = await redis.hget(keys.retry_meta, message_id)
                retry_payload: dict[str, Any] = {}
                attempts = 0
                if retry_meta:
                    try:
                        retry_payload = json.loads(retry_meta)
                        attempts = int(retry_payload.get("attempts") or 0)
                    except (TypeError, ValueError, json.JSONDecodeError):
                        retry_payload = {}
                        attempts = 0
                attempt_id = str(retry_payload.get("attempt_id") or "")
                owner_token = str(retry_payload.get("owner_token") or "")
                if not attempt_id or not owner_token or attempts < 1:
                    continue
                if attempts >= max_attempts:
                    if await _dead_letter_expired_lease_with_fence(
                        redis,
                        keys,
                        raw=raw,
                        message_id=message_id,
                        error_code="lease_expired_max_attempts",
                        error_message="Leased queue message exceeded max attempts",
                        attempts=attempts,
                        worker_id=retry_payload.get("worker_id"),
                        remove_processing_meta=False,
                        expected_attempt_id=attempt_id,
                        expected_owner_token=owner_token,
                    ):
                        dead_lettered += 1
                else:
                    requeued = await _requeue_run_with_fence(
                        redis,
                        keys,
                        raw=raw,
                        retry_metadata={
                            **retry_payload,
                            "attempts": attempts,
                            "requeued_at": checked_at,
                        },
                        expected_attempt_id=attempt_id,
                        expected_owner_token=owner_token,
                        remove_processing=True,
                    )
                    if requeued:
                        reclaimed += 1
                continue
            try:
                meta = json.loads(raw_meta)
            except json.JSONDecodeError:
                meta = {}
            heartbeat_at = float(meta.get("heartbeat_at") or meta.get("leased_at") or 0)
            if checked_at - heartbeat_at <= visibility_timeout_seconds:
                continue
            attempts = int(meta.get("attempts") or 0)
            attempt_id = str(meta.get("attempt_id") or "")
            owner_token = str(meta.get("owner_token") or "")
            if not attempt_id or not owner_token or attempts < 1:
                continue
            if attempts >= max_attempts:
                if await _dead_letter_expired_lease_with_fence(
                    redis,
                    keys,
                    raw=raw,
                    message_id=message_id,
                    error_code="lease_expired_max_attempts",
                    error_message="Leased queue message exceeded max attempts",
                    attempts=attempts,
                    worker_id=meta.get("worker_id"),
                    remove_processing_meta=True,
                    expected_attempt_id=attempt_id,
                    expected_owner_token=owner_token,
                ):
                    dead_lettered += 1
            else:
                requeued = await _requeue_run_with_fence(
                    redis,
                    keys,
                    raw=raw,
                    retry_metadata={
                        **meta,
                        "attempts": attempts,
                        "requeued_at": checked_at,
                    },
                    expected_attempt_id=attempt_id,
                    expected_owner_token=owner_token,
                    remove_processing=True,
                )
                if requeued:
                    reclaimed += 1
        return {"reclaimed": reclaimed, "dead_lettered": dead_lettered}
    finally:
        await redis.aclose()


async def dequeue_run(timeout_seconds: int = 5) -> dict[str, Any] | None:
    message = await lease_run(timeout_seconds=timeout_seconds)
    if message is None:
        return None
    await ack_run(message.raw, message_id=message.message_id)
    return message.payload
