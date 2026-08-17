"""Stable SSE v3 application boundary."""

from app.streaming.application.live_fanout import (
    LiveSubscription,
    LiveSubscriptionClosed,
    RunStreamHub,
)
from app.streaming.contracts import (
    StreamEnvelope,
    _redis_id_tuple,
    stream_live_channel,
)
from app.streaming.domain.live import LivePublication


def live_redis_id_is_after(candidate: str, current: str) -> bool:
    """Return whether one validated Redis cursor follows another."""
    return _redis_id_tuple(candidate) > _redis_id_tuple(current)


__all__ = [
    "LivePublication",
    "LiveSubscription",
    "LiveSubscriptionClosed",
    "RunStreamHub",
    "StreamEnvelope",
    "live_redis_id_is_after",
    "stream_live_channel",
]
