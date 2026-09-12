"""Pure Redis Stream identity and cursor rules."""

from __future__ import annotations

import hashlib
import hmac
import re

STREAM_KEY_PREFIX = "ai-platform:sse:v3"
RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
TENANT_SCOPE_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
REDIS_ID_PATTERN = re.compile(r"^(0|[1-9][0-9]*)-(0|[1-9][0-9]*)$")


class StreamContractError(ValueError):
    pass


def tenant_scope(tenant_id: str, *, secret: str) -> str:
    if not tenant_id or not secret:
        raise StreamContractError("stream_tenant_scope_authority_missing")
    return hmac.new(secret.encode(), tenant_id.encode(), hashlib.sha256).hexdigest()[
        :32
    ]


def redis_id_tuple(value: str) -> tuple[int, int]:
    if not REDIS_ID_PATTERN.fullmatch(value):
        raise StreamContractError("stream_redis_id_invalid")
    return tuple(map(int, value.split("-")))  # type: ignore[return-value]


def live_redis_id_is_after(candidate: str, current: str) -> bool:
    return redis_id_tuple(candidate) > redis_id_tuple(current)


def stream_key(*, tenant_scope_value: str, run_id: str, stream_incarnation: int) -> str:
    if (
        not TENANT_SCOPE_PATTERN.fullmatch(tenant_scope_value)
        or not RUN_ID_PATTERN.fullmatch(run_id)
        or isinstance(stream_incarnation, bool)
        or stream_incarnation < 1
    ):
        raise StreamContractError("stream_key_invalid")
    return f"{STREAM_KEY_PREFIX}:{{{tenant_scope_value}:{run_id}}}:{stream_incarnation}:events"
