"""Compose the process-wide SSE v3 runtime."""

from __future__ import annotations

from dataclasses import dataclass

from app.streaming.application.live_fanout import RunStreamHub
from app.streaming.infrastructure.redis_live import RedisLiveFanoutSource
from app.streaming.redis import RedisStreamBridge


@dataclass(slots=True)
class RunStreamRuntime:
    bridge: RedisStreamBridge
    hub: RunStreamHub

    async def aclose(self) -> None:
        try:
            await self.hub.aclose()
        finally:
            await self.bridge.aclose()


def build_run_stream_runtime() -> RunStreamRuntime:
    source = RedisLiveFanoutSource()
    return RunStreamRuntime(
        bridge=RedisStreamBridge(),
        hub=RunStreamHub(source=source),
    )
