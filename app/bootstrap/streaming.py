"""Compose the process-wide SSE v4 runtime."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app import repositories
from app.runs.infrastructure.postgres import load_current_terminal_event_fact
from app.settings import get_settings
from app.streaming.application.live_fanout import RunStreamHub
from app.streaming.application.worker_publication_v4 import WorkerV4Capabilities
from app.streaming.infrastructure.postgres_v4 import (
    PostgresV4PublicationClaims,
    PostgresV4SuccessorActivations,
    PostgresV4SuccessorRebuilds,
)
from app.streaming.infrastructure.redis_live import RedisLiveFanoutSource
from app.streaming.infrastructure.redis_v4_rebuild import RedisV4SuccessorRebuildTransport
from app.streaming.infrastructure.worker_v4 import (
    PostgresV4PendingAdmissions,
    PostgresWorkerEventPersistence,
    RedisV4PublicationTransport,
    RedisV4StreamAuthorityLookup,
)
from app.streaming.infrastructure.v4 import V4RedisStreamBridge


@dataclass(slots=True)
class WorkerV4Runtime:
    bridge: V4RedisStreamBridge
    capabilities: WorkerV4Capabilities

    async def aclose(self) -> None:
        await self.bridge.aclose()


def build_worker_v4_capabilities(
    bridge: V4RedisStreamBridge,
    transaction_factory: Any,
) -> WorkerV4Capabilities:
    pending_admissions = PostgresV4PendingAdmissions(
        transaction_factory,
        authority_secret=get_settings().ai_session_secret,
    )
    return WorkerV4Capabilities(
        authority=RedisV4StreamAuthorityLookup(transaction_factory),
        pending_admissions=pending_admissions,
        event_persistence=PostgresWorkerEventPersistence(
            transaction_factory,
            append_event=repositories.append_event,
            is_cancel_requested=repositories.is_cancel_requested,
            load_terminal_event_fact=load_current_terminal_event_fact,
        ),
        publication_claims=PostgresV4PublicationClaims(transaction_factory),
        publication_transport=RedisV4PublicationTransport(bridge),
    )


def build_worker_v4_runtime(
    transaction_factory: Any,
) -> WorkerV4Runtime:
    bridge = V4RedisStreamBridge()
    return WorkerV4Runtime(
        bridge=bridge,
        capabilities=build_worker_v4_capabilities(bridge, transaction_factory),
    )


@dataclass(slots=True)
class RunStreamRuntime:
    bridge: V4RedisStreamBridge
    hub: RunStreamHub
    successor_rebuilds: PostgresV4SuccessorRebuilds
    successor_activations: PostgresV4SuccessorActivations
    rebuild_transport: RedisV4SuccessorRebuildTransport
    worker_capabilities: WorkerV4Capabilities

    async def aclose(self) -> None:
        try:
            await self.hub.aclose()
        finally:
            try:
                await self.rebuild_transport.aclose()
            finally:
                await self.bridge.aclose()


def build_run_stream_runtime(
    transaction_factory: Any,
) -> RunStreamRuntime:
    settings = get_settings()
    source = RedisLiveFanoutSource(redis_url=str(settings.redis_url))
    bridge = V4RedisStreamBridge()
    return RunStreamRuntime(
        bridge=bridge,
        hub=RunStreamHub(source=source),
        successor_rebuilds=PostgresV4SuccessorRebuilds(transaction_factory),
        successor_activations=PostgresV4SuccessorActivations(transaction_factory),
        rebuild_transport=RedisV4SuccessorRebuildTransport(),
        worker_capabilities=build_worker_v4_capabilities(bridge, transaction_factory),
    )
