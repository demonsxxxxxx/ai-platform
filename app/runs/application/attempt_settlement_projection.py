"""Runs-owned authoritative Attempt settlement read/write contract."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Mapping

from app.runs.domain.attempt_settlement_projection import (
    ATTEMPT_SETTLEMENT_SCHEMA_VERSION,
    AttemptSettlementProjection,
    AttemptSettlementProjectionConflict,
    AttemptSettlementProjectionError,
)


@dataclass(frozen=True)
class AttemptSettlementProjectionPorts:
    """Persistence ports supplied by the Runs infrastructure adapter."""

    lock_sources: Callable[..., Awaitable[bool]]
    read_attempt: Callable[..., Awaitable[dict[str, Any] | None]]
    list_due: Callable[..., Awaitable[list[dict[str, Any]]]]
    next_retry_delay: Callable[..., Awaitable[float | None]]
    initialize_attempt: Callable[..., Awaitable[bool]]
    refresh_snapshot: Callable[..., Awaitable[bool]]
    record_intent: Callable[..., Awaitable[bool]]
    record_accepted: Callable[..., Awaitable[bool]]
    record_terminal: Callable[..., Awaitable[bool]]
    record_dispatch_outcome: Callable[..., Awaitable[bool]]
    claim_due: Callable[..., Awaitable[list[dict[str, Any]]]]
    claim_current: Callable[..., Awaitable[bool]]
    retry_claim: Callable[..., Awaitable[bool]]
    append_diagnostics: Callable[..., Awaitable[bool]]
    summary: Callable[..., Awaitable[dict[str, Any]]]
    claim_checkpoint_repair: Callable[..., Awaitable[bool]]


class AttemptSettlementReadError(RuntimeError):
    """The active storage version cannot provide an authoritative mapping."""


@dataclass(frozen=True)
class AttemptSettlementProjectionService:
    """The single Attempt settlement authority, sharing the caller transaction."""

    ports: AttemptSettlementProjectionPorts


    async def read_attempt(
        self,
        conn: Any,
        *,
        tenant_id: str,
        run_id: str,
        attempt_id: str,
        require_mapped: bool = True,
        for_update: bool = False,
    ) -> dict[str, Any] | None:
        row = await self.ports.read_attempt(
            conn, tenant_id=tenant_id, run_id=run_id, attempt_id=attempt_id,
            for_update=for_update,
        )
        if row is None:
            raise AttemptSettlementReadError("attempt_settlement_projection_missing")
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

    async def reconciliation_summary(
        self, conn: Any, *, tenant_id: str, slo_seconds: int,
    ) -> dict[str, Any]:
        return await self.ports.summary(
            conn, tenant_id=tenant_id, slo_seconds=slo_seconds,
        )

    async def initialize_attempt_in_transaction(
        self, conn: Any, *, tenant_id: str, run_id: str, attempt_id: str,
        source_lease_id: str | None = None,
    ) -> bool:
        return await self.ports.initialize_attempt(
            conn, tenant_id=tenant_id, run_id=run_id, attempt_id=attempt_id,
            source_lease_id=source_lease_id,
        )

    async def refresh_snapshot_in_transaction(
        self, conn: Any, *, tenant_id: str, run_id: str, attempt_id: str,
    ) -> bool:
        """Refresh Run/Attempt settlement facts without reading Sandbox columns."""
        return await self.ports.refresh_snapshot(
            conn, tenant_id=tenant_id, run_id=run_id, attempt_id=attempt_id,
        )

    async def record_intent_in_transaction(
        self, conn: Any, *, tenant_id: str, run_id: str, attempt_id: str,
        lease_id: str, intent_context: Mapping[str, Any],
    ) -> bool:
        return await self.ports.record_intent(
            conn, tenant_id=tenant_id, run_id=run_id, attempt_id=attempt_id,
            lease_id=lease_id, intent_context=intent_context,
        )

    async def record_accepted_context_in_transaction(
        self, conn: Any, *, tenant_id: str, run_id: str, attempt_id: str,
        lease_id: str, accepted_context: Mapping[str, Any],
    ) -> bool:
        return await self.ports.record_accepted(
            conn, tenant_id=tenant_id, run_id=run_id, attempt_id=attempt_id,
            lease_id=lease_id, accepted_context=accepted_context,
        )

    async def record_terminal_receipt_in_transaction(
        self, conn: Any, *, tenant_id: str, run_id: str, attempt_id: str,
        lease_id: str, receipt: Mapping[str, Any], receipt_owner_generation: int,
    ) -> bool:
        return await self.ports.record_terminal(
            conn, tenant_id=tenant_id, run_id=run_id, attempt_id=attempt_id,
            lease_id=lease_id, receipt=receipt,
            receipt_owner_generation=receipt_owner_generation,
        )

    async def record_dispatch_outcome_in_transaction(
        self, conn: Any, *, tenant_id: str, run_id: str, attempt_id: str,
        lease_id: str, outcome: str,
    ) -> bool:
        return await self.ports.record_dispatch_outcome(
            conn, tenant_id=tenant_id, run_id=run_id, attempt_id=attempt_id,
            lease_id=lease_id, outcome=outcome,
        )

    async def claim_due_attempts_in_transaction(
        self, conn: Any, *, claim_token: str, limit: int, stale_after_seconds: int,
    ) -> list[dict[str, Any]]:
        return await self.ports.claim_due(
            conn, claim_token=claim_token, limit=limit,
            stale_after_seconds=stale_after_seconds,
        )

    async def claim_checkpoint_repair_in_transaction(self, conn: Any, **kwargs: Any) -> bool:
        return await self.ports.claim_checkpoint_repair(conn, **kwargs)

    async def settlement_claim_current(
        self, conn: Any, *, tenant_id: str, run_id: str, attempt_id: str,
        lease_id: str, claim_token: str,
    ) -> bool:
        return await self.ports.claim_current(
            conn, tenant_id=tenant_id, run_id=run_id, attempt_id=attempt_id,
            lease_id=lease_id, claim_token=claim_token,
        )

    async def retry_claim_in_transaction(
        self, conn: Any, *, tenant_id: str, run_id: str, attempt_id: str,
        lease_id: str, claim_token: str,
    ) -> bool:
        return await self.ports.retry_claim(
            conn, tenant_id=tenant_id, run_id=run_id, attempt_id=attempt_id,
            lease_id=lease_id, claim_token=claim_token,
        )

    async def append_claim_diagnostics_in_transaction(
        self, conn: Any, *, tenant_id: str, run_id: str, attempt_id: str,
        lease_id: str, claim_token: str, diagnostics: list[str],
    ) -> bool:
        return await self.ports.append_diagnostics(
            conn, tenant_id=tenant_id, run_id=run_id, attempt_id=attempt_id,
            lease_id=lease_id, claim_token=claim_token, diagnostics=diagnostics,
        )


    async def lock_sources_in_transaction(
        self,
        conn: Any,
        *,
        tenant_id: str,
        run_id: str,
        attempt_id: str,
    ) -> bool:
        """Lock Run, exact lease set by id, then Attempt before a settlement write."""

        return await self.ports.lock_sources(
            conn, tenant_id=tenant_id, run_id=run_id, attempt_id=attempt_id
        )


__all__ = [
    "ATTEMPT_SETTLEMENT_SCHEMA_VERSION",
    "AttemptSettlementProjection",
    "AttemptSettlementProjectionConflict",
    "AttemptSettlementProjectionError",
    "AttemptSettlementProjectionPorts",
    "AttemptSettlementProjectionService",
    "AttemptSettlementReadError",
]
