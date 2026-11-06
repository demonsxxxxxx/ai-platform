"""Fail an accepted Run after a permanent queue delivery rejection."""

from __future__ import annotations

from typing import Any, Protocol

from app.runs.application.lifecycle import RunLifecycleService
from app.runs.application.run_terminalization import record_run_terminalization
from app.streaming.api import WorkerV4Capabilities


class QueueDeliveryFailurePersistence(Protocol):
    async def lock_queued_run_without_attempt(
        self, conn: Any, *, tenant_id: str, run_id: str
    ) -> bool: ...


class QueueDeliveryFailureOwner:
    """Give a permanent transport rejection the Runs-owned terminal result."""

    def __init__(
        self,
        *,
        persistence: QueueDeliveryFailurePersistence,
        lifecycle: RunLifecycleService,
        capabilities: WorkerV4Capabilities,
    ) -> None:
        self._persistence = persistence
        self._lifecycle = lifecycle
        self._capabilities = capabilities

    async def __call__(self, conn: Any, *, intent: Any, reason: str) -> bool:
        eligible = await self._persistence.lock_queued_run_without_attempt(
            conn, tenant_id=intent.tenant_id, run_id=intent.run_id
        )
        if not eligible:
            return False
        await self._capabilities.pending_admissions.prepare_pending_authority_in_transaction(
            conn,
            tenant_id=intent.tenant_id,
            run_id=intent.run_id,
            attempt_id=f"enqueue_failure_{intent.run_id}",
        )
        progress = await self._lifecycle.mark_run_enqueue_failed(
            conn,
            tenant_id=intent.tenant_id,
            user_id=None,
            run_id=intent.run_id,
        )
        if not progress.did_transition:
            return False
        await record_run_terminalization(
            conn,
            capabilities=self._capabilities,
            tenant_id=intent.tenant_id,
            run_id=intent.run_id,
            progress=progress,
        )
        return True
