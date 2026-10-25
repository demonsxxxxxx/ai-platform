"""Bounded Runs-owned backfill and comparison contract for Attempt settlement."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

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


@dataclass(frozen=True)
class AttemptSettlementProjectionService:
    """A read/backfill capability; it does not schedule or settle Runs."""

    ports: AttemptSettlementProjectionPorts

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


__all__ = [
    "ATTEMPT_SETTLEMENT_SCHEMA_VERSION",
    "AttemptSettlementProjection",
    "AttemptSettlementProjectionConflict",
    "AttemptSettlementProjectionError",
    "AttemptSettlementProjectionPorts",
    "AttemptSettlementProjectionService",
    "project_legacy_attempt_settlement",
]

