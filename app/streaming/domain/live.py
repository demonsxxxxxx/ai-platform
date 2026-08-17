"""Pure values and identity rules for the SSE v3 live fan-out plane."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

STREAM_KEY_PREFIX = "ai-platform:sse:v3"
RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
TENANT_SCOPE_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
REDIS_ID_PATTERN = re.compile(r"^(0|[1-9][0-9]*)-(0|[1-9][0-9]*)$")


class StreamContractError(ValueError):
    pass


class LiveEnvelope(Protocol):
    event_id: str
    run_id: str
    stream_incarnation: int
    emitted_at: str
    event_type: str
    payload: Mapping[str, object]


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


def stream_live_channel(
    *, tenant_scope_value: str, run_id: str, stream_incarnation: int
) -> str:
    return stream_key(
        tenant_scope_value=tenant_scope_value,
        run_id=run_id,
        stream_incarnation=stream_incarnation,
    ).removesuffix(":events") + ":live"


@dataclass(frozen=True, slots=True)
class LivePublication:
    channel: str
    redis_id: str
    envelope_json: str

    @property
    def byte_size(self) -> int:
        return len(self.channel.encode("utf-8")) + len(self.redis_id.encode("utf-8")) + len(
            self.envelope_json.encode("utf-8")
        )
