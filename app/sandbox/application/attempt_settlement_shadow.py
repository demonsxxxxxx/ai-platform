"""Coordinate Sandbox-owned writes with the Runs settlement shadow port."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any


class AttemptSettlementShadowLeaseRepository:
    """Wrap Sandbox persistence while preserving the shared source lock order."""

    def __init__(
        self,
        *,
        repository: Any,
        settlement_projection: Any,
        enabled: Callable[[], bool],
    ) -> None:
        self._repository = repository
        self._settlement = settlement_projection
        self._enabled = enabled

    def __getattr__(self, name: str) -> Any:
        return getattr(self._repository, name)

    @staticmethod
    def _scope(row: Mapping[str, Any]) -> dict[str, str] | None:
        if not all(row.get(key) for key in ("tenant_id", "run_id", "attempt_id")):
            return None
        return {key: str(row[key]) for key in ("tenant_id", "run_id", "attempt_id")}

    async def _lock(self, conn: Any, scope: Mapping[str, str] | None) -> None:
        if scope is not None:
            await self._settlement.lock_sources_in_transaction(conn, **scope)

    async def _refresh(self, conn: Any, scope: Mapping[str, str] | None) -> None:
        if scope is not None:
            await self._settlement.refresh_in_transaction(conn, **scope)

    async def _scope_for_lease(self, conn: Any, lease_id: str) -> dict[str, str] | None:
        return await self._repository.get_sandbox_lease_scope(conn, lease_id=lease_id)

    async def create_sandbox_lease(self, conn: Any, **kwargs: Any) -> Any:
        scope = self._scope(kwargs) if self._enabled() else None
        await self._lock(conn, scope)
        row = await self._repository.create_sandbox_lease(conn, **kwargs)
        if scope is None and self._enabled() and kwargs.get("attempt_id"):
            scope = {
                key: str(kwargs[key])
                for key in ("tenant_id", "run_id", "attempt_id")
            }
        await self._refresh(conn, scope)
        return row

    async def record_sandbox_executor_accepted(self, conn: Any, **kwargs: Any) -> Any:
        scope = self._scope(kwargs) if self._enabled() else None
        await self._lock(conn, scope)
        row = await self._repository.record_sandbox_executor_accepted(conn, **kwargs)
        await self._refresh(conn, scope)
        return row

    async def record_sandbox_executor_terminal(self, conn: Any, **kwargs: Any) -> Any:
        scope = self._scope(kwargs) if self._enabled() else None
        await self._lock(conn, scope)
        row = await self._repository.record_sandbox_executor_terminal(conn, **kwargs)
        await self._refresh(conn, scope)
        return row

    async def _claim(
        self,
        conn: Any,
        *,
        claim_method: str,
        candidate_method: str,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        if not self._enabled():
            return await getattr(self._repository, claim_method)(conn, **kwargs)

        candidates = await getattr(self._repository, candidate_method)(
            conn,
            limit=kwargs["limit"],
            stale_after_seconds=kwargs["stale_after_seconds"],
        )
        if not candidates:
            return []
        scopes = {
            tuple(scope.values()): scope
            for row in candidates
            if (scope := self._scope(row)) is not None
        }
        for key in sorted(scopes):
            await self._lock(conn, scopes[key])
        rows = await getattr(self._repository, claim_method)(
            conn,
            candidate_lease_ids=[str(row["id"]) for row in candidates],
            **kwargs,
        )
        actual_scopes = {
            tuple(scope.values()): scope
            for row in rows
            if (scope := self._scope(row)) is not None
        }
        for key in sorted(actual_scopes):
            await self._refresh(conn, actual_scopes[key])
        return rows

    async def claim_sandbox_executor_reconciliations(self, conn: Any, **kwargs: Any) -> list[dict[str, Any]]:
        return await self._claim(
            conn,
            claim_method="claim_sandbox_executor_reconciliations",
            candidate_method="list_sandbox_executor_reconciliation_candidate_scopes",
            **kwargs,
        )

    async def claim_sandbox_executor_suspects(self, conn: Any, **kwargs: Any) -> list[dict[str, Any]]:
        return await self._claim(
            conn,
            claim_method="claim_sandbox_executor_suspects",
            candidate_method="list_sandbox_executor_suspect_candidate_scopes",
            **kwargs,
        )

    async def retry_sandbox_executor_reconciliation(self, conn: Any, **kwargs: Any) -> bool:
        scope = (
            await self._scope_for_lease(conn, str(kwargs["lease_id"]))
            if self._enabled()
            else None
        )
        await self._lock(conn, scope)
        changed = await self._repository.retry_sandbox_executor_reconciliation(conn, **kwargs)
        if changed:
            await self._refresh(conn, scope)
        return changed

    async def mark_sandbox_executor_reconciliation_cleanup_pending(self, conn: Any, **kwargs: Any) -> bool:
        scope = (
            await self._scope_for_lease(conn, str(kwargs["lease_id"]))
            if self._enabled()
            else None
        )
        await self._lock(conn, scope)
        changed = await self._repository.mark_sandbox_executor_reconciliation_cleanup_pending(
            conn, **kwargs
        )
        if changed:
            await self._refresh(conn, scope)
        return changed

    async def finalize_failed_sandbox_executor_reconciliation_cleanup(
        self, conn: Any, **kwargs: Any
    ) -> Any:
        scope = (
            await self._scope_for_lease(conn, str(kwargs["lease_id"]))
            if self._enabled()
            else None
        )
        await self._lock(conn, scope)
        row = await self._repository.finalize_failed_sandbox_executor_reconciliation_cleanup(
            conn, **kwargs
        )
        if row is not None:
            await self._refresh(conn, scope)
        return row

    async def quarantine_sandbox_executor_reconciliation(self, conn: Any, **kwargs: Any) -> bool:
        scope = (
            await self._scope_for_lease(conn, str(kwargs["lease_id"]))
            if self._enabled()
            else None
        )
        await self._lock(conn, scope)
        changed = await self._repository.quarantine_sandbox_executor_reconciliation(conn, **kwargs)
        if changed:
            await self._refresh(conn, scope)
        return changed


class AttemptSettlementShadowDispatchRepository:
    """Publish dispatch intent into the Runs projection in the same transaction."""

    def __init__(
        self,
        *,
        repository: Any,
        settlement_projection: Any,
        enabled: Callable[[], bool],
    ) -> None:
        self._repository = repository
        self._settlement = settlement_projection
        self._enabled = enabled

    async def persist_intent(
        self,
        conn: Any,
        *,
        identity: Any,
        reconciliation_context: dict[str, Any],
    ) -> Any:
        scope = {
            "tenant_id": str(identity.tenant_id),
            "run_id": str(identity.run_id),
            "attempt_id": str(identity.attempt_id),
        }
        if self._enabled():
            await self._settlement.lock_sources_in_transaction(conn, **scope)
        result = await self._repository.persist_intent(
            conn,
            identity=identity,
            reconciliation_context=reconciliation_context,
        )
        if self._enabled():
            await self._settlement.refresh_in_transaction(conn, **scope)
        return result

    async def record_outcome(self, conn: Any, *, identity: Any, outcome: Any) -> Any:
        return await self._repository.record_outcome(conn, identity=identity, outcome=outcome)
