"""Compose Sandbox resources with the single Runs-owned settlement authority."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.runs.api import AttemptSettlementProjectionConflict
from app.sandbox.domain.lease_errors import SandboxExecutorTerminalConflictError


class AttemptSettlementLeaseRepository:
    """Keep resource I/O in Sandbox and execution settlement in Runs."""

    def __init__(self, *, repository: Any, settlement_projection: Any) -> None:
        self._repository = repository
        self._settlement = settlement_projection

    def __getattr__(self, name: str) -> Any:
        return getattr(self._repository, name)

    @staticmethod
    def _scope(row: Mapping[str, Any]) -> dict[str, str] | None:
        if not all(row.get(key) for key in ("tenant_id", "run_id", "attempt_id")):
            return None
        return {key: str(row[key]) for key in ("tenant_id", "run_id", "attempt_id")}

    async def _lock_sources(self, conn: Any, scope: Mapping[str, str] | None) -> None:
        if scope is not None:
            await self._settlement.lock_sources_in_transaction(conn, **scope)

    async def _scope_for_lease(self, conn: Any, lease_id: str) -> dict[str, str] | None:
        return await self._repository.get_sandbox_lease_scope(conn, lease_id=lease_id)

    async def _settlement_view(
        self, conn: Any, row: Mapping[str, Any], *, require_mapped: bool = False,
    ) -> dict[str, Any]:
        """Assemble the reconciler's resource/receipt view; never read old storage."""
        result = dict(row)
        scope = self._scope(result)
        if scope is None:
            return result
        detail = await self._settlement.read_attempt(conn, **scope, require_mapped=require_mapped)
        if detail is None:
            raise RuntimeError("attempt_settlement_projection_missing")
        phase = str(detail.get("settlement_phase") or "unknown")
        receipt = detail.get("receipt_json")
        result.update(
            settlement_mapping_state=detail.get("mapping_state"),
            settlement_dispatch_state=detail.get("dispatch_state"),
            settlement_receipt_present=receipt is not None,
            receipt_json=receipt,
            accepted_context_json=detail.get("accepted_context_json"),
            intent_recovery_context_json=detail.get("intent_recovery_context_json"),
            settlement_attempt_count=detail.get("settlement_attempt_count"),
            next_retry_at=detail.get("next_retry_at"),
            settlement_phase=phase,
            settlement_claim_token=detail.get("settlement_claim_token"),
            settlement_claimed_at=detail.get("settlement_claimed_at"),
        )
        return result

    async def create_sandbox_lease(self, conn: Any, **kwargs: Any) -> Any:
        scope = self._scope(kwargs)
        await self._lock_sources(conn, scope)
        row = await self._repository.create_sandbox_lease(conn, **kwargs)
        if scope is not None:
            await self._settlement.initialize_attempt_in_transaction(conn, **scope, source_lease_id=str(row["id"]))
        return row

    async def get_sandbox_lease(self, conn: Any, **kwargs: Any) -> Any:
        row = await self._repository.get_sandbox_lease(conn, **kwargs)
        return await self._settlement_view(conn, row) if row is not None else None

    async def get_sandbox_executor_terminal_handoff(self, conn: Any, **kwargs: Any) -> dict[str, Any] | None:
        row = await self._repository.get_sandbox_executor_terminal_handoff(conn, **kwargs)
        return await self._settlement_view(conn, row, require_mapped=True) if row is not None else None

    async def list_current_sandbox_runtime_leases_for_attempt(self, conn: Any, **kwargs: Any) -> list[dict[str, Any]]:
        rows = await self._repository.list_current_sandbox_runtime_leases_for_attempt(conn, **kwargs)
        return [await self._settlement_view(conn, row) for row in rows]

    async def list_cancelling_provider_tail_leases_for_attempt(self, conn: Any, **kwargs: Any) -> list[dict[str, Any]]:
        rows = await self._repository.list_cancelling_provider_tail_leases_for_attempt(conn, **kwargs)
        return [await self._settlement_view(conn, row) for row in rows]

    async def get_resource_sandbox_lease(self, conn: Any, **kwargs: Any) -> Any:
        return await self._repository.get_sandbox_lease(conn, **kwargs)

    async def list_sandbox_leases(self, conn: Any, **kwargs: Any) -> list[dict[str, Any]]:
        return [await self._settlement_view(conn, row) for row in await self._repository.list_sandbox_leases(conn, **kwargs)]

    async def list_sandbox_leases_for_run(self, conn: Any, **kwargs: Any) -> list[dict[str, Any]]:
        return [await self._settlement_view(conn, row) for row in await self._repository.list_sandbox_leases_for_run(conn, **kwargs)]

    async def read_run_runtime_debug(self, conn: Any, *, tenant_id: str, run_id: str) -> dict[str, Any]:
        result = await self._repository.read_run_runtime_debug(conn, tenant_id=tenant_id, run_id=run_id)
        projected = []
        for item in result.get("leases", []):
            source = {**item, "id": item.get("lease_id"), "tenant_id": tenant_id, "run_id": run_id}
            view = await self._settlement_view(conn, source)
            context = view.get("accepted_context_json")
            timings = context.get("dispatch_timings") if isinstance(context, Mapping) else None
            projected.append({
                **item,
                **{key: view.get(key) for key in (
                    "settlement_mapping_state", "settlement_phase", "settlement_dispatch_state",
                    "settlement_receipt_present",
                    "settlement_attempt_count",
                )},
                "dispatch_timings": {
                    name: value for name, value in (timings.items() if isinstance(timings, Mapping) else ())
                    if name in {
                        "sandbox_queue_wait_latency_ms", "sandbox_lease_acquire_latency_ms",
                        "sandbox_container_start_latency_ms", "sandbox_container_cold_start_latency_ms",
                        "sandbox_healthcheck_latency_ms", "sandbox_executor_dispatch_latency_ms",
                        "executor_first_token_latency_ms", "executor_tool_call_latency_ms",
                        "executor_model_latency_ms", "document_processing_latency_ms",
                        "artifact_upload_latency_ms", "sandbox_cleanup_latency_ms", "sandbox_total_latency_ms",
                    } and type(value) in (int, float) and 0 <= value <= 86_400_000
                },
            })
        return {**result, "leases": projected}

    async def record_sandbox_executor_accepted(self, conn: Any, **kwargs: Any) -> Any:
        scope = self._scope(kwargs)
        if scope is None:
            raise RuntimeError("attempt_settlement_scope_missing")
        await self._lock_sources(conn, scope)
        row = await self._repository.record_sandbox_executor_accepted_observation(conn, **kwargs)
        await self._settlement.record_accepted_context_in_transaction(
            conn, **scope, lease_id=str(kwargs["lease_id"]), accepted_context=kwargs["reconciliation_context"],
        )
        return await self._settlement_view(conn, row, require_mapped=True)

    async def record_sandbox_executor_terminal(self, conn: Any, **kwargs: Any) -> Any:
        scope = self._scope(kwargs)
        if scope is None:
            raise RuntimeError("attempt_settlement_scope_missing")
        await self._lock_sources(conn, scope)
        try:
            row = await self._repository.record_sandbox_executor_terminal_observation(conn, **kwargs)
            payload = row.get("lease_payload_json") if isinstance(row, Mapping) else None
            generation = payload.get("owner_generation") if isinstance(payload, Mapping) else None
            await self._settlement.record_terminal_receipt_in_transaction(
                conn, **scope, lease_id=str(kwargs["lease_id"]), receipt=kwargs["terminal_result"],
                receipt_owner_generation=int(generation),
            )
            return await self._settlement_view(conn, row, require_mapped=True)
        except (AttemptSettlementProjectionConflict, TypeError, ValueError) as exc:
            raise SandboxExecutorTerminalConflictError("sandbox_executor_terminal_conflict") from exc

    async def claim_sandbox_executor_reconciliations(self, conn: Any, **kwargs: Any) -> list[dict[str, Any]]:
        return await self._settlement.claim_due_attempts_in_transaction(conn, **kwargs)

    async def claim_sandbox_executor_suspects(self, conn: Any, **kwargs: Any) -> list[dict[str, Any]]:
        candidates = await self._repository.list_sandbox_executor_suspect_candidate_scopes(
            conn, limit=kwargs["limit"], stale_after_seconds=kwargs["stale_after_seconds"],
        )
        scopes = {tuple(scope.values()): scope for row in candidates if (scope := self._scope(row)) is not None}
        for key in sorted(scopes):
            await self._lock_sources(conn, scopes[key])
        rows = await self._repository.claim_sandbox_executor_suspects(
            conn, candidate_lease_ids=[str(row["id"]) for row in candidates], **kwargs,
        )
        return [await self._settlement_view(conn, row, require_mapped=False) for row in rows]

    async def retry_sandbox_executor_reconciliation(self, conn: Any, **kwargs: Any) -> bool:
        scope = await self._scope_for_lease(conn, str(kwargs["lease_id"]))
        if scope is None:
            return False
        await self._lock_sources(conn, scope)
        return await self._settlement.retry_claim_in_transaction(
            conn, **scope, lease_id=str(kwargs["lease_id"]), claim_token=str(kwargs["claim_token"]),
        )

    async def mark_sandbox_executor_reconciliation_cleanup_pending(self, conn: Any, **kwargs: Any) -> bool:
        scope = await self._scope_for_lease(conn, str(kwargs["lease_id"]))
        if scope is None:
            return False
        await self._lock_sources(conn, scope)
        detail = await self._settlement.read_attempt(conn, **scope, require_mapped=True)
        if (
            detail is None or detail.get("source_lease_id") != str(kwargs["lease_id"])
            or int(detail.get("settlement_attempt_count") or 0) < 1 or detail.get("receipt_json") is None
            or detail.get("settlement_phase") not in {"claimed", "settled"}
            or (detail.get("settlement_phase") == "claimed" and detail.get("settlement_claim_token") != str(kwargs["claim_token"]))
        ):
            return False
        if detail.get("settlement_phase") == "claimed":
            await self._settlement.refresh_snapshot_in_transaction(conn, **scope)
        detail = await self._settlement.read_attempt(conn, **scope, require_mapped=True)
        if not self._settled_resource(detail, str(kwargs["lease_id"])):
            return False
        return await self._repository.mark_sandbox_executor_cleanup_pending(conn, **kwargs)

    @staticmethod
    def _settled_resource(detail: Mapping[str, Any] | None, lease_id: str) -> bool:
        return bool(
            detail and detail.get("source_lease_id") == lease_id
            and detail.get("settlement_phase") == "settled"
            and int(detail.get("settlement_attempt_count") or 0) >= 1
            and detail.get("receipt_json") is not None and detail.get("result_json") is not None
            and detail.get("result_status") in {"succeeded", "failed", "cancelled", "canceled"}
            and detail.get("settled_at") is not None
        )

    async def quarantine_sandbox_executor_reconciliation(self, conn: Any, **kwargs: Any) -> bool:
        scope = await self._scope_for_lease(conn, str(kwargs["lease_id"]))
        await self._lock_sources(conn, scope)
        if kwargs.get("probe_claim_token") is None and (
            scope is None or not await self._settlement.settlement_claim_current(
                conn, **scope, lease_id=str(kwargs["lease_id"]), claim_token=str(kwargs["claim_token"]),
            )
        ):
            return False
        return await self._repository.quarantine_sandbox_executor_reconciliation(conn, **kwargs)

    async def finalize_stopped_executor_checkpoint(self, conn: Any, *, scope: Mapping[str, str], **kwargs: Any) -> bool:
        """Release only the exact stopped resource after atomic Attempt settlement."""
        settlement_scope = self._scope(scope)
        if settlement_scope is None:
            return False
        await self._lock_sources(conn, settlement_scope)
        detail = await self._settlement.read_attempt(conn, **settlement_scope, require_mapped=True)
        if (detail and detail.get("source_lease_id") == str(kwargs["lease_id"])
            and detail.get("settlement_phase") == "claimed"
            and detail.get("settlement_claim_token") == str(kwargs["claim_token"])):
            await self._settlement.refresh_snapshot_in_transaction(conn, **settlement_scope)
            detail = await self._settlement.read_attempt(conn, **settlement_scope, require_mapped=True)
        if (not self._settled_resource(detail, str(kwargs["lease_id"]))
            or detail.get("settled_claim_token") != str(kwargs["claim_token"])):
            return False
        return await self._repository.finalize_stopped_executor_checkpoint(conn, scope=scope, **kwargs)

    async def read_unknown_receipt_probe_recovery_facts(self, conn: Any, **kwargs: Any) -> dict[str, Any] | None:
        scope = await self._scope_for_lease(conn, str(kwargs["lease_id"]))
        await self._lock_sources(conn, scope)
        return await self._repository.read_unknown_receipt_probe_recovery_facts(conn, **kwargs)

    async def list_cancel_requested_executor_leases(self, conn: Any, **kwargs: Any) -> list[dict[str, Any]]:
        return [await self._settlement_view(conn, row) for row in await self._repository.list_cancel_requested_executor_leases(conn, **kwargs)]

    async def record_sandbox_reconciliation_stage(self, conn: Any, **kwargs: Any) -> bool:
        scope = await self._scope_for_lease(conn, str(kwargs["lease_id"]))
        await self._lock_sources(conn, scope)
        return await self._repository.record_sandbox_reconciliation_stage(conn, **kwargs)

    async def get_quarantined_checkpoint_for_repair(self, conn: Any, **kwargs: Any) -> dict[str, Any] | None:
        scope = self._scope(kwargs["scope"])
        await self._lock_sources(conn, scope)
        row = await self._repository.get_quarantined_checkpoint_for_repair(conn, **kwargs)
        return await self._settlement_view(conn, row) if row is not None else None

    async def claim_quarantined_checkpoint_for_repair(self, conn: Any, **kwargs: Any) -> dict[str, Any] | None:
        scope = self._scope(kwargs["scope"])
        if scope is None:
            return None
        await self._lock_sources(conn, scope)
        row = await self._repository.claim_quarantined_checkpoint_for_repair(conn, **kwargs)
        if row is None:
            return None
        await self._settlement.claim_checkpoint_repair_in_transaction(
            conn, **scope, lease_id=str(kwargs["lease_id"]), claim_token=str(kwargs["claim_token"]),
        )
        return await self._settlement_view(conn, row, require_mapped=True)

    async def get_sandbox_executor_reconciliation_summary(self, conn: Any, **kwargs: Any) -> dict[str, Any]:
        settlement = await self._settlement.reconciliation_summary(
            conn, tenant_id=str(kwargs["tenant_id"]), slo_seconds=int(kwargs["slo_seconds"]),
        )
        resources = await self._repository.get_sandbox_resource_cleanup_summary(conn, tenant_id=str(kwargs["tenant_id"]))
        return {**settlement, **resources}

    async def next_sandbox_executor_reconciliation_retry_delay(self, conn: Any) -> float | None:
        return await self._settlement.next_retry_delay(conn)

    async def is_sandbox_executor_reconciliation_claim_current(self, conn: Any, *, lease_id: str, claim_token: str) -> bool:
        scope = await self._scope_for_lease(conn, lease_id)
        if scope is None:
            return False
        await self._lock_sources(conn, scope)
        return await self._settlement.settlement_claim_current(conn, **scope, lease_id=lease_id, claim_token=claim_token)

    async def has_sandbox_executor_reconciliation_claim(self, conn: Any, *, lease_id: str, claim_token: str) -> bool:
        return await self.is_sandbox_executor_reconciliation_claim_current(conn, lease_id=lease_id, claim_token=claim_token)

    async def record_sandbox_executor_terminal_diagnostics(self, conn: Any, *, lease_id: str, claim_token: str, diagnostics: list[str]) -> bool:
        scope = await self._scope_for_lease(conn, lease_id)
        if scope is None:
            return False
        await self._lock_sources(conn, scope)
        return await self._settlement.append_claim_diagnostics_in_transaction(
            conn, **scope, lease_id=lease_id, claim_token=claim_token, diagnostics=diagnostics,
        )

    async def lock_scoped_reconciliation_receipt(self, conn: Any, **kwargs: Any) -> bool:
        scope = {key: str(kwargs[key]) for key in ("tenant_id", "run_id", "attempt_id")}
        await self._lock_sources(conn, scope)
        if not await self._repository.lock_scoped_reconciliation_resource(
            conn, **{key: kwargs[key] for key in (
                "tenant_id", "workspace_id", "user_id", "session_id", "run_id", "attempt_id", "lease_id", "expected_owner_generation",
            )},
        ):
            return False
        detail = await self._settlement.read_attempt(conn, **scope, require_mapped=True, for_update=True)
        return bool(
            detail and detail.get("source_lease_id") == kwargs["lease_id"] and detail.get("receipt_json") is not None
            and detail.get("receipt_owner_generation") == kwargs["expected_owner_generation"]
            and detail.get("settlement_phase") == "claimed" and detail.get("settlement_claim_token") == kwargs["claim_token"]
        )


class AttemptSettlementDispatchRepository:
    """Persist dispatch evidence and Runs context in the same transaction."""

    def __init__(self, *, repository: Any, settlement_projection: Any) -> None:
        self._repository = repository
        self._settlement = settlement_projection

    async def persist_intent(self, conn: Any, *, identity: Any, reconciliation_context: dict[str, Any]) -> Any:
        scope = {"tenant_id": str(identity.tenant_id), "run_id": str(identity.run_id), "attempt_id": str(identity.attempt_id)}
        await self._settlement.lock_sources_in_transaction(conn, **scope)
        result = await self._repository.persist_intent(conn, identity=identity, reconciliation_context=reconciliation_context)
        await self._settlement.record_intent_in_transaction(conn, **scope, lease_id=str(identity.lease_id), intent_context=reconciliation_context)
        return result

    async def record_outcome(self, conn: Any, *, identity: Any, outcome: Any) -> Any:
        scope = {"tenant_id": str(identity.tenant_id), "run_id": str(identity.run_id), "attempt_id": str(identity.attempt_id)}
        await self._settlement.lock_sources_in_transaction(conn, **scope)
        result = await self._repository.record_outcome(conn, identity=identity, outcome=outcome)
        if result is not None:
            await self._settlement.record_dispatch_outcome_in_transaction(conn, **scope, lease_id=str(identity.lease_id), outcome=outcome)
        return result
