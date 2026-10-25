"""Bounded Runs-owned backfill and comparison contract for Attempt settlement."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Mapping

from app.runs.domain.attempt_settlement_projection import (
    ATTEMPT_SETTLEMENT_SCHEMA_VERSION,
    AttemptSettlementProjection,
    AttemptSettlementProjectionConflict,
    AttemptSettlementProjectionError,
    project_legacy_attempt_settlement,
)


@dataclass(frozen=True)
class AttemptSettlementProjectionPorts:
    """Persistence ports supplied by the Runs infrastructure adapter."""

    backfill: Callable[..., Awaitable[dict[str, Any]]]
    compare: Callable[..., Awaitable[dict[str, Any]]]
    persist: Callable[..., Awaitable[bool]]
    lock_sources: Callable[..., Awaitable[bool]]
    refresh: Callable[..., Awaitable[AttemptSettlementProjection | None]]
    compare_snapshot: Callable[..., Awaitable[dict[str, Any]]]
    storage_version: Callable[..., Awaitable[str]]
    read_attempt: Callable[..., Awaitable[dict[str, Any] | None]]
    list_due: Callable[..., Awaitable[list[dict[str, Any]]]]
    next_retry_delay: Callable[..., Awaitable[float | None]]
    writer_fence: Callable[..., Awaitable[None]]


class AttemptSettlementReadError(RuntimeError):
    """The active storage version cannot provide an authoritative mapping."""


@dataclass(frozen=True)
class AttemptSettlementProjectionService:
    """A read/backfill capability; it does not schedule or settle Runs."""

    ports: AttemptSettlementProjectionPorts

    async def storage_version(self, conn: Any) -> str:
        return await self.ports.storage_version(conn)

    async def read_attempt(
        self,
        conn: Any,
        *,
        tenant_id: str,
        run_id: str,
        attempt_id: str,
        require_mapped: bool = True,
    ) -> dict[str, Any] | None:
        row = await self.ports.read_attempt(
            conn, tenant_id=tenant_id, run_id=run_id, attempt_id=attempt_id
        )
        if row is None:
            if await self.storage_version(conn) == "attempt.v1":
                raise AttemptSettlementReadError("attempt_settlement_projection_missing")
            return None
        if require_mapped and row.get("mapping_state") != "mapped":
            state = str(row.get("mapping_state") or "unknown")
            if state not in {"unknown", "conflict"}:
                state = "unknown"
            raise AttemptSettlementReadError(f"attempt_settlement_projection_{state}")
        return row

    async def list_due_attempts(
        self, conn: Any, *, limit: int, stale_after_seconds: int
    ) -> list[dict[str, Any]]:
        return await self.ports.list_due(
            conn, limit=limit, stale_after_seconds=stale_after_seconds
        )

    async def next_retry_delay(self, conn: Any) -> float | None:
        return await self.ports.next_retry_delay(conn)

    async def writes_enabled(self, conn: Any, *, shadow_enabled: bool) -> bool:
        """Persistent cutover always enables projection writes after the lock."""
        return (await self.storage_version(conn) == "attempt.v1") or shadow_enabled

    async def lock_writer_fence(self, conn: Any) -> None:
        await self.ports.writer_fence(conn)

    async def backfill(
        self,
        conn: Any,
        *,
        tenant_id: str | None = None,
        after: tuple[str, str, str] | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        return await self.ports.backfill(
            conn, tenant_id=tenant_id, after=after, limit=limit
        )

    async def compare(
        self,
        conn: Any,
        *,
        tenant_id: str | None = None,
        after: tuple[str, str, str] | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        """Compare one page; callers must not use it as a cutover proof."""

        return await self.ports.compare(
            conn, tenant_id=tenant_id, after=after, limit=limit
        )

    async def compare_snapshot(
        self,
        conn: Any,
        *,
        tenant_id: str | None = None,
        page_limit: int = 100,
    ) -> dict[str, Any]:
        """Compare the complete denominator using one stable MVCC snapshot."""

        return await self.ports.compare_snapshot(
            conn, tenant_id=tenant_id, page_limit=page_limit
        )

    async def persist_in_transaction(
        self,
        conn: Any,
        projection: AttemptSettlementProjection,
        *,
        insert_only: bool = False,
    ) -> bool:
        """The single future same-transaction Runs projection port."""

        return await self.ports.persist(
            conn, projection, insert_only=insert_only
        )

    async def lock_sources_in_transaction(
        self,
        conn: Any,
        *,
        tenant_id: str,
        run_id: str,
        attempt_id: str,
    ) -> bool:
        """Lock Run, exact lease set by id, then Attempt before a legacy write."""

        return await self.ports.lock_sources(
            conn, tenant_id=tenant_id, run_id=run_id, attempt_id=attempt_id
        )

    async def refresh_in_transaction(
        self,
        conn: Any,
        *,
        tenant_id: str,
        run_id: str,
        attempt_id: str,
    ) -> bool:
        """Persist the latest source projection through the single write port."""

        projection = await self.ports.refresh(
            conn, tenant_id=tenant_id, run_id=run_id, attempt_id=attempt_id
        )
        if projection is None:
            return False
        await self.persist_in_transaction(conn, projection)
        return True

    async def refresh_accepted_context_in_transaction(
        self,
        conn: Any,
        *,
        tenant_id: str,
        run_id: str,
        attempt_id: str,
        accepted_context: Mapping[str, Any],
    ) -> bool:
        """Record acceptance only from the explicit Sandbox acceptance writer."""
        projection = await self.ports.refresh(
            conn, tenant_id=tenant_id, run_id=run_id, attempt_id=attempt_id
        )
        if projection is None:
            return False
        values = projection.to_mapping()
        values["accepted_context_json"] = dict(accepted_context)
        if (
            values.get("dispatch_state") == "accepted"
            and values.get("settlement_phase") != "unknown"
        ):
            values["mapping_state"] = "mapped"
        await self.persist_in_transaction(
            conn, AttemptSettlementProjection(values, projection.cleanup)
        )
        return True

    async def refresh_dispatch_in_transaction(
        self,
        conn: Any,
        *,
        tenant_id: str,
        run_id: str,
        attempt_id: str,
    ) -> bool:
        """Refresh dispatch outcome without treating intent context as acceptance."""
        projection = await self.ports.refresh(
            conn, tenant_id=tenant_id, run_id=run_id, attempt_id=attempt_id
        )
        if projection is None:
            return False
        values = projection.to_mapping()
        current = await self.ports.read_attempt(
            conn, tenant_id=tenant_id, run_id=run_id, attempt_id=attempt_id
        )
        if current is not None and current.get("accepted_context_json") is None:
            intent_context = current.get("intent_recovery_context_json")
            if intent_context is not None and values.get("accepted_context_json") == intent_context:
                values["accepted_context_json"] = None
                if values.get("dispatch_state") == "accepted":
                    values["mapping_state"] = "unknown"
        await self.persist_in_transaction(
            conn, AttemptSettlementProjection(values, projection.cleanup)
        )
        return True


__all__ = [
    "ATTEMPT_SETTLEMENT_SCHEMA_VERSION",
    "AttemptSettlementProjection",
    "AttemptSettlementProjectionConflict",
    "AttemptSettlementProjectionError",
    "AttemptSettlementProjectionPorts",
    "AttemptSettlementProjectionService",
    "AttemptSettlementReadError",
    "project_legacy_attempt_settlement",
]

