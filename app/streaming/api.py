"""Stable Streaming application boundary."""

from typing import Any

from app.streaming.application.live_fanout import (
    LiveSubscription,
    LiveSubscriptionClosed,
    RunStreamHub,
)
from app.streaming.domain.live import (
    REDIS_ID_PATTERN,
    RUN_ID_PATTERN,
    STREAM_KEY_PREFIX,
    TENANT_SCOPE_PATTERN,
    LiveEnvelope,
    LivePublication,
    StreamContractError,
    live_redis_id_is_after,
    redis_id_tuple,
    stream_key,
    stream_live_channel,
)


async def append_artifact_ready_v4_row(
    conn: Any,
    *,
    tenant_id: str,
    run_id: str,
    artifact_id: str,
    filename: str,
    media_type: str | None,
    size_bytes: int,
    execution_lease_id: str,
    trace_ref: str,
) -> dict[str, Any]:
    from app.streaming.v4 import append_artifact_ready_v4_row as _append_artifact_ready_v4_row

    return await _append_artifact_ready_v4_row(
        conn,
        tenant_id=tenant_id,
        run_id=run_id,
        artifact_id=artifact_id,
        filename=filename,
        media_type=media_type,
        size_bytes=size_bytes,
        execution_lease_id=execution_lease_id,
        trace_ref=trace_ref,
    )


__all__ = [
    "REDIS_ID_PATTERN",
    "RUN_ID_PATTERN",
    "STREAM_KEY_PREFIX",
    "TENANT_SCOPE_PATTERN",
    "LiveEnvelope",
    "LivePublication",
    "LiveSubscription",
    "LiveSubscriptionClosed",
    "RunStreamHub",
    "StreamContractError",
    "append_artifact_ready_v4_row",
    "live_redis_id_is_after",
    "redis_id_tuple",
    "stream_key",
    "stream_live_channel",
]
