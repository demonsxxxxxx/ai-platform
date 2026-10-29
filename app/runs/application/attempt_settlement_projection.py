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
    writer_version: Callable[..., Awaitable[str]]
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
    checkpoint_repair_candidate: Callable[..., Awaitable[dict[str, Any] | None]] | None = None
    claim_checkpoint_repair: Callable[..., Awaitable[bool]] | None = None


class AttemptSettlementReadError(RuntimeError):
    """The active storage version cannot provide an authoritative mapping."""


@dataclass(frozen=True)
class AttemptSettlementProjectionService:
    """A read/backfill capability; it does not schedule or settle Runs."""

    ports: AttemptSettlementProjectionPorts

    async def storage_version(self, conn: Any) -> str:
        return await self.ports.storage_version(conn)

    async def writer_version(self, conn: Any) -> str:
        return await self.ports.writer_version(conn)

    async def attempt_writer_active(self, conn: Any) -> bool:
        return await self.writer_version(conn) == "attempt.v1"

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

    async def checkpoint_repair_candidate(
        self,
        conn: Any,
        *,
        scope: Mapping[str, str],
        lease_id: str,
        writer_generation: int,
    ) -> dict[str, Any] | None:
        """Validate one explicitly requested quarantined checkpoint in Runs."""
        if self.ports.checkpoint_repair_candidate is None:
            raise RuntimeError("attempt_settlement_checkpoint_repair_unconfigured")
        return await self.ports.checkpoint_repair_candidate(
            conn, scope=scope, lease_id=lease_id,
            writer_generation=writer_generation,
        )

    async def claim_checkpoint_repair_in_transaction(
        self,
        conn: Any,
        *,
        scope: Mapping[str, str],
        lease_id: str,
        expected_claim_token: str,
        claim_token: str,
        writer_generation: int,
        cancellation_generation: int,
    ) -> bool:
        """Transfer only the exact repair claim through Runs-owned settlement."""
        if self.ports.claim_checkpoint_repair is None:
            raise RuntimeError("attempt_settlement_checkpoint_repair_unconfigured")
        return await self.ports.claim_checkpoint_repair(
            conn, scope=scope, lease_id=lease_id,
            expected_claim_token=expected_claim_token, claim_token=claim_token,
            writer_generation=writer_generation,
            cancellation_generation=cancellation_generation,
        )

    async def writes_enabled(self, conn: Any, *, shadow_enabled: bool) -> bool:
        """Keep the projection current for either its reader or active writer."""
        return (
            await self.storage_version(conn) == "attempt.v1"
            or await self.attempt_writer_active(conn)
            or shadow_enabled
        )

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
