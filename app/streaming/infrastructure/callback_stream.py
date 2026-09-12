"""Ordered, idempotent callback batches on the existing Redis Stream."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence

from redis.exceptions import ResponseError

from app.streaming.domain.live import stream_key
from app.streaming.domain.public_events_v4 import validate_internal_envelope_v4
from app.streaming.domain.transport import canonical_json_bytes
from app.streaming.redis import (
    RedisStreamBridge,
    SSE_STREAM_ACTIVE_IDLE_TTL_MS,
    SSE_STREAM_MAXLEN,
    StreamContractError,
    StreamTransportUnavailable,
)

# The existing executor buffer sends the next batch only after acknowledgement.
# The committed callback receipt fixes the batch bytes; Redis owns deduplication.
_APPEND_CALLBACK_BATCH = """
if redis.call('HGET', KEYS[2], 'open_protocol') ~= 'v4' or redis.call('XLEN', KEYS[1]) == 0 then
  return redis.error_reply('stream_callback_authority_unavailable')
end
local sequence = tonumber(ARGV[1])
local delivered = tonumber(redis.call('HGET', KEYS[2], 'callback_sequence') or '0')
if delivered >= sequence then
  if delivered == sequence and redis.call('HGET', KEYS[2], 'callback_digest') ~= ARGV[2] then
    return redis.error_reply('stream_callback_receipt_conflict')
  end
  return redis.call('HGET', KEYS[2], 'callback_redis_id')
end
if redis.call('HGET', KEYS[2], 'phase') ~= 'open' then
  return redis.error_reply('stream_callback_closed')
end
local envelopes = cjson.decode(ARGV[3])
local id
for _, envelope in ipairs(envelopes) do
  id = redis.call('XADD', KEYS[1], 'MAXLEN', '~', ARGV[4], '*', 'envelope', envelope)
end
redis.call('HSET', KEYS[2], 'callback_sequence', ARGV[1], 'callback_digest', ARGV[2], 'callback_redis_id', id)
redis.call('PEXPIRE', KEYS[1], ARGV[5])
redis.call('PEXPIRE', KEYS[2], ARGV[5])
return id
"""


async def append_callback_batch(
    bridge: RedisStreamBridge,
    envelopes: Sequence[Mapping[str, object]],
) -> str:
    if not envelopes:
        raise StreamContractError("stream_callback_batch_invalid")
    validated = [validate_internal_envelope_v4(item) for item in envelopes]
    first = validated[0]
    previous_sequence = 0
    for item in validated:
        if (
            any(item[key] != first[key] for key in ("tenant_scope", "run_id", "attempt_id", "stream_incarnation"))
            or not isinstance(item["seq"], int)
            or item["seq"] <= previous_sequence
            or item["event_type"].startswith("stream.")
            or item["event_type"] in {"run.succeeded", "run.failed", "run.cancelled"}
        ):
            raise StreamContractError("stream_callback_batch_invalid")
        previous_sequence = item["seq"]
    payload = json.dumps([canonical_json_bytes(item).decode() for item in validated], separators=(",", ":"))
    key = stream_key(
        tenant_scope_value=first["tenant_scope"],
        run_id=first["run_id"],
        stream_incarnation=first["stream_incarnation"],
    )
    try:
        return str(await bridge._publish_client.eval(
            _APPEND_CALLBACK_BATCH, 2, key, f"{key}:state",
            previous_sequence, hashlib.sha256(payload.encode()).hexdigest(), payload,
            SSE_STREAM_MAXLEN, SSE_STREAM_ACTIVE_IDLE_TTL_MS,
        ))
    except ResponseError as exc:
        if "stream_callback_receipt_conflict" in str(exc):
            raise StreamContractError("stream_callback_receipt_conflict") from exc
        raise StreamTransportUnavailable("stream_callback_append_unavailable") from exc
    except Exception as exc:
        raise StreamTransportUnavailable("stream_callback_append_unavailable") from exc
