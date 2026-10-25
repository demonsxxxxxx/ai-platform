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

    async def _write_enabled(
        self, conn: Any, scope: Mapping[str, str] | None
    ) -> bool:
        # Lock before reading the marker. N40's table-fenced activation then
        # either observes this transaction's complete projection or makes this
        # writer observe the activated version before it changes source rows.
        await self._settlement.lock_writer_fence(conn)
        await self._lock(conn, scope)
        return await self._settlement.writes_enabled(
            conn, shadow_enabled=bool(self._enabled())
        )

    async def _refresh(self, conn: Any, scope: Mapping[str, str] | None) -> None:
        if scope is not None:
            await self._settlement.refresh_in_transaction(conn, **scope)

    async def _scope_for_lease(self, conn: Any, lease_id: str) -> dict[str, str] | None:
        return await self._repository.get_sandbox_lease_scope(conn, lease_id=lease_id)

    async def _settlement_view(
        self, conn: Any, row: Mapping[str, Any], *, require_mapped: bool = False
    ) -> dict[str, Any]:
        result = dict(row)
        if not all(result.get(key) for key in ("tenant_id", "run_id", "attempt_id")):
            return result
        if await self._settlement.storage_version(conn) != "attempt.v1":
            return result
        detail = await self._settlement.read_attempt(
            conn,
            tenant_id=str(result["tenant_id"]),
            run_id=str(result["run_id"]),
            attempt_id=str(result["attempt_id"]),
            require_mapped=require_mapped,
        )
        if detail is None:
            raise RuntimeError("attempt_settlement_projection_missing")
        phase = str(detail.get("settlement_phase") or "unknown")
        legacy_phase = {
            "awaiting_receipt": "waiting_terminal",
            "awaiting_context": "waiting_terminal",
            "pending": "pending",
            "claimed": "claimed",
            "retry": "retry",
            "settled": "finalized",
            "failed": "failed",
            "unknown": "unknown",
        }.get(phase, "unknown")
        result.update(
            settlement_storage_version="attempt.v1",
            settlement_mapping_state=detail.get("mapping_state"),
            settlement_phase=phase,
            settlement_dispatch_state=detail.get("dispatch_state"),
            settlement_receipt_present=detail.get("receipt_json") is not None,
            executor_terminal_json=detail.get("receipt_json"),
            executor_reconciliation_context_json=detail.get("accepted_context_json"),
            executor_reconciliation_intent_context_json=detail.get("intent_recovery_context_json"),
            executor_reconciliation_status=legacy_phase,
            executor_reconciliation_claim_token=detail.get("settlement_claim_token"),
            executor_reconciliation_claimed_at=detail.get("settlement_claimed_at"),
            executor_reconciliation_retry_not_before=detail.get("next_retry_at"),
            executor_terminal_reconciliation_attempt_count=detail.get("settlement_attempt_count"),
        )
        return result

    async def create_sandbox_lease(self, conn: Any, **kwargs: Any) -> Any:
        scope = self._scope(kwargs)
        write_detail = await self._write_enabled(conn, scope)
        row = await self._repository.create_sandbox_lease(conn, **kwargs)
        if write_detail:
            if scope is None and kwargs.get("attempt_id"):
                scope = {key: str(kwargs[key]) for key in ("tenant_id", "run_id", "attempt_id")}
            await self._refresh(conn, scope)
        return row

    async def get_sandbox_lease(self, conn: Any, **kwargs: Any) -> Any:
        row = await self._repository.get_sandbox_lease(conn, **kwargs)
        return await self._settlement_view(conn, row) if row is not None else None

    async def list_sandbox_leases(self, conn: Any, **kwargs: Any) -> list[dict[str, Any]]:
        rows = await self._repository.list_sandbox_leases(conn, **kwargs)
        return [await self._settlement_view(conn, row) for row in rows]

    async def list_sandbox_leases_for_run(self, conn: Any, **kwargs: Any) -> list[dict[str, Any]]:
        rows = await self._repository.list_sandbox_leases_for_run(conn, **kwargs)
        return [await self._settlement_view(conn, row) for row in rows]

    async def read_run_runtime_debug(self, conn: Any, *, tenant_id: str, run_id: str) -> dict[str, Any]:
        result = await self._repository.read_run_runtime_debug(
            conn, tenant_id=tenant_id, run_id=run_id
        )
        projected = []
        for item in result.get("leases", []):
            source = {
                **item,
                "id": item.get("lease_id"),
                "tenant_id": tenant_id,
                "run_id": run_id,
            }
            view = await self._settlement_view(conn, source)
            projected.append(
                {
                    **item,
                    "settlement_storage_version": view.get("settlement_storage_version", "sandbox_lease.v1"),
                    "settlement_mapping_state": view.get("settlement_mapping_state"),
                    "settlement_phase": view.get("settlement_phase"),
                    "settlement_dispatch_state": view.get("settlement_dispatch_state"),
                    "settlement_receipt_present": view.get("settlement_receipt_present"),
                    "executor_reconciliation_status": view.get("executor_reconciliation_status"),
                    "executor_terminal_reconciliation_attempt_count": view.get(
                        "executor_terminal_reconciliation_attempt_count"
                    ),
                }
            )
        return {**result, "leases": projected}

    async def record_sandbox_executor_accepted(self, conn: Any, **kwargs: Any) -> Any:
        scope = self._scope(kwargs)
        write_detail = await self._write_enabled(conn, scope)
        row = await self._repository.record_sandbox_executor_accepted(conn, **kwargs)
        if write_detail:
            if row is not None:
                await self._settlement.refresh_accepted_context_in_transaction(
                    conn,
                    **scope,
                    accepted_context=kwargs["reconciliation_context"],
                )
        return row

    async def record_sandbox_executor_terminal(self, conn: Any, **kwargs: Any) -> Any:
        scope = self._scope(kwargs)
        write_detail = await self._write_enabled(conn, scope)
        row = await self._repository.record_sandbox_executor_terminal(conn, **kwargs)
        if write_detail:
            await self._refresh(conn, scope)
        return row

    async def _claim(
        self,
        conn: Any,
        *,
        claim_method: str,
        candidate_method: str,
        use_attempt_candidates: bool = True,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        # Hold compatible writer table locks before selecting candidates so an
        # activation cannot change the durable marker partway through this claim.
        await self._settlement.lock_writer_fence(conn)
        storage_before = await self._settlement.storage_version(conn)
        candidates = (
            []
            if storage_before == "attempt.v1" and use_attempt_candidates
            else await getattr(self._repository, candidate_method)(
                conn,
                limit=kwargs["limit"],
                stale_after_seconds=kwargs["stale_after_seconds"],
            )
        )
        due_details = (
            await self._settlement.list_due_attempts(
                conn,
                limit=kwargs["limit"],
                stale_after_seconds=kwargs["stale_after_seconds"],
            )
            if storage_before == "attempt.v1" and use_attempt_candidates
            else []
        )
        scopes = {
            tuple(scope.values()): scope
            for row in candidates
            if (scope := self._scope(row)) is not None
        }
        scopes.update(
            {
                tuple(scope.values()): scope
                for row in due_details
                if (scope := self._scope(row)) is not None
            }
        )
        for key in sorted(scopes):
            await self._lock(conn, scopes[key])
        storage_version = await self._settlement.storage_version(conn)
        write_detail = storage_version == "attempt.v1" or bool(self._enabled())
        if storage_version == "attempt.v1" and use_attempt_candidates:
            current_details = await self._settlement.list_due_attempts(
                conn,
                limit=kwargs["limit"],
                stale_after_seconds=kwargs["stale_after_seconds"],
            )
            if any(
                tuple(scope.values()) not in scopes
                for row in current_details
                if (scope := self._scope(row)) is not None
            ):
                raise RuntimeError("attempt_settlement_candidate_scope_unlocked")
            candidate_lease_ids = [
                str(row["source_lease_id"])
                for row in current_details
                if row.get("source_lease_id")
            ]
            if len(candidate_lease_ids) != len(current_details):
                raise RuntimeError("attempt_settlement_source_lease_missing")
            expected_lease_ids = set(candidate_lease_ids)
        else:
            candidate_lease_ids = [str(row["id"]) for row in candidates if row.get("id")]
            expected_lease_ids = set()
        rows = await getattr(self._repository, claim_method)(
            conn,
            candidate_lease_ids=candidate_lease_ids,
            **kwargs,
        )
        if storage_version == "attempt.v1" and use_attempt_candidates:
            claimed_lease_ids = {str(row.get("id") or "") for row in rows}
            if claimed_lease_ids != expected_lease_ids:
                raise RuntimeError("attempt_settlement_source_claim_mismatch")
        if write_detail and use_attempt_candidates:
            actual_scopes = {
                tuple(scope.values()): scope
                for row in rows
                if (scope := self._scope(row)) is not None
            }
            for key in sorted(actual_scopes):
                await self._refresh(conn, actual_scopes[key])
        if storage_version == "attempt.v1" and use_attempt_candidates:
            return [
                await self._settlement_view(conn, row, require_mapped=True)
                for row in rows
            ]
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
            use_attempt_candidates=False,
            **kwargs,
        )

    async def retry_sandbox_executor_reconciliation(self, conn: Any, **kwargs: Any) -> bool:
        scope = await self._scope_for_lease(conn, str(kwargs["lease_id"]))
        write_detail = await self._write_enabled(conn, scope)
        changed = await self._repository.retry_sandbox_executor_reconciliation(conn, **kwargs)
        if changed and write_detail:
            await self._refresh(conn, scope)
        return changed

    async def mark_sandbox_executor_reconciliation_cleanup_pending(self, conn: Any, **kwargs: Any) -> bool:
        scope = await self._scope_for_lease(conn, str(kwargs["lease_id"]))
        write_detail = await self._write_enabled(conn, scope)
        changed = await self._repository.mark_sandbox_executor_reconciliation_cleanup_pending(
            conn, **kwargs
        )
        if changed and write_detail:
            await self._refresh(conn, scope)
        return changed

    async def finalize_failed_sandbox_executor_reconciliation_cleanup(
        self, conn: Any, **kwargs: Any
    ) -> Any:
        scope = await self._scope_for_lease(conn, str(kwargs["lease_id"]))
        write_detail = await self._write_enabled(conn, scope)
        row = await self._repository.finalize_failed_sandbox_executor_reconciliation_cleanup(
            conn, **kwargs
        )
        if row is not None and write_detail:
            await self._refresh(conn, scope)
        return row

    async def quarantine_sandbox_executor_reconciliation(self, conn: Any, **kwargs: Any) -> bool:
        scope = await self._scope_for_lease(conn, str(kwargs["lease_id"]))
        write_detail = await self._write_enabled(conn, scope)
        changed = await self._repository.quarantine_sandbox_executor_reconciliation(conn, **kwargs)
        if changed and write_detail:
            await self._refresh(conn, scope)
        return changed

    async def next_sandbox_executor_reconciliation_retry_delay(self, conn: Any) -> float | None:
        if await self._settlement.storage_version(conn) == "attempt.v1":
            return await self._settlement.next_retry_delay(conn)
        return await self._repository.next_sandbox_executor_reconciliation_retry_delay(conn)

    async def is_sandbox_executor_reconciliation_claim_current(
        self, conn: Any, *, lease_id: str, claim_token: str
    ) -> bool:
        if await self._settlement.storage_version(conn) != "attempt.v1":
            return await self._repository.is_sandbox_executor_reconciliation_claim_current(
                conn, lease_id=lease_id, claim_token=claim_token
            )
        scope = await self._scope_for_lease(conn, lease_id)
        if scope is None:
            return False
        detail = await self._settlement.read_attempt(conn, **scope, require_mapped=True)
        return bool(
            detail
            and detail.get("source_lease_id") == lease_id
            and detail.get("settlement_phase") == "claimed"
            and detail.get("settlement_claim_token") == claim_token
        )

    async def has_sandbox_executor_reconciliation_claim(
        self, conn: Any, *, lease_id: str, claim_token: str
    ) -> bool:
        if await self._settlement.storage_version(conn) != "attempt.v1":
            return await self._repository.has_sandbox_executor_reconciliation_claim(
                conn, lease_id=lease_id, claim_token=claim_token
            )
        return await self.is_sandbox_executor_reconciliation_claim_current(
            conn, lease_id=lease_id, claim_token=claim_token
        )


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

    async def _write_enabled(self, conn: Any, scope: Mapping[str, str]) -> bool:
        await self._settlement.lock_writer_fence(conn)
        await self._settlement.lock_sources_in_transaction(conn, **scope)
        return await self._settlement.writes_enabled(
            conn, shadow_enabled=bool(self._enabled())
        )

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
        write_detail = await self._write_enabled(conn, scope)
        result = await self._repository.persist_intent(
            conn,
            identity=identity,
            reconciliation_context=reconciliation_context,
        )
        if write_detail:
            await self._settlement.refresh_in_transaction(conn, **scope)
        return result

    async def record_outcome(self, conn: Any, *, identity: Any, outcome: Any) -> Any:
        scope = {
            "tenant_id": str(identity.tenant_id),
            "run_id": str(identity.run_id),
            "attempt_id": str(identity.attempt_id),
        }
        write_detail = await self._write_enabled(conn, scope)
        result = await self._repository.record_outcome(conn, identity=identity, outcome=outcome)
        if write_detail and result is not None:
            await self._settlement.refresh_dispatch_in_transaction(conn, **scope)
        return result
