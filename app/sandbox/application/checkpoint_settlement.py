"""Marker-aware Sandbox checkpoint operations over the Runs settlement port."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.sandbox.domain.checkpoint_stage import ensure_reconciliation_stage_size


class SandboxCheckpointSettlementMixin:
    """Checkpoint port methods shared by the Sandbox lease adapter."""

    async def _attempt_settlement_mode(self, conn: Any) -> bool:
        return (
            await self._settlement.storage_version(conn) == "attempt.v1"
            or await self._writer_active(conn)
        )

    @staticmethod
    def _checkpoint_scope(scope: Any) -> dict[str, str] | None:
        required = {
            "tenant_id", "workspace_id", "user_id", "session_id", "run_id", "attempt_id",
        }
        if (
            not isinstance(scope, Mapping)
            or set(scope) != required
            or any(not isinstance(value, str) or not value for value in scope.values())
        ):
            return None
        return {key: str(value) for key, value in scope.items()}

    @staticmethod
    def _checkpoint_repair_scope(scope: Any) -> dict[str, str] | None:
        required = {
            "tenant_id", "workspace_id", "user_id", "session_id", "agent_id",
            "run_id", "attempt_id",
        }
        if (
            not isinstance(scope, Mapping)
            or set(scope) != required
            or any(not isinstance(value, str) or not value for value in scope.values())
        ):
            return None
        return {key: str(value) for key, value in scope.items()}

    @classmethod
    def _valid_checkpoint_stage(
        cls, stage: Any, *, row: Mapping[str, Any] | None = None,
    ) -> tuple[dict[str, str], int, str] | None:
        if not isinstance(stage, Mapping):
            return None
        scope = cls._checkpoint_scope(stage.get("scope"))
        owner_generation = stage.get("owner_generation")
        container_id = stage.get("container_id")
        terminal_result = stage.get("terminal_result")
        payload = (row or {}).get("lease_payload_json")
        if row is not None and not isinstance(payload, Mapping):
            return None
        if (
            stage.get("schema_version") != "ai-platform.terminal-reconciliation.v1"
            or scope is None
            or type(owner_generation) is not int
            or owner_generation < 1
            or not isinstance(container_id, str)
            or not container_id
            or not isinstance(terminal_result, Mapping)
            or terminal_result.get("run_id") != (scope or {}).get("run_id")
            or str(terminal_result.get("status") or "").lower()
            not in {"completed", "succeeded", "failed", "cancelled", "canceled"}
            or (
                row is not None
                and (
                    owner_generation != payload.get("owner_generation")
                    or scope != {key: str(row.get(key) or "") for key in scope}
                    or scope["attempt_id"] != str(payload.get("attempt_id") or "")
                    or container_id != row.get("runtime_container_id")
                )
            )
        ):
            return None
        continuation = stage.get("continuation")
        if continuation is not None and (
            not isinstance(continuation, Mapping)
            or any(continuation.get(key) != scope[key] for key in ("tenant_id", "run_id", "attempt_id"))
            or continuation.get("stop_evidence") not in {"provider_stopped", "provider_not_found"}
        ):
            return None
        return scope, owner_generation, container_id

    @staticmethod
    def _checkpoint_claim_marker(
        *, claim_token: str, owner_generation: int, container_id: str,
    ) -> dict[str, Any]:
        return {
            "schema_version": "ai-platform.terminal-reconciliation-claim.v1",
            "claim_token": claim_token,
            "owner_generation": owner_generation,
            "container_id": container_id,
        }

    @staticmethod
    def _checkpoint_claim_marker_valid(
        value: Any, *, owner_generation: int, container_id: str,
    ) -> bool:
        return (
            isinstance(value, Mapping)
            and value.get("schema_version") == "ai-platform.terminal-reconciliation-claim.v1"
            and isinstance(value.get("claim_token"), str)
            and bool(value.get("claim_token"))
            and value.get("owner_generation") == owner_generation
            and value.get("container_id") == container_id
        )

    async def get_sandbox_executor_terminal_handoff(
        self,
        conn: Any,
        *,
        tenant_id: str,
        workspace_id: str,
        user_id: str,
        session_id: str,
        run_id: str,
        attempt_id: str,
        lease_id: str,
        owner_generation: int,
    ) -> dict[str, Any] | None:
        scope = {
            "tenant_id": tenant_id,
            "workspace_id": workspace_id,
            "user_id": user_id,
            "session_id": session_id,
            "run_id": run_id,
            "attempt_id": attempt_id,
        }
        if (
            any(not isinstance(value, str) or not value for value in scope.values())
            or type(owner_generation) is not int
            or owner_generation < 1
        ):
            return None
        await self._write_enabled(
            conn, {"tenant_id": tenant_id, "run_id": run_id, "attempt_id": attempt_id}
        )
        row = await self._repository.get_scoped_checkpoint_lease(conn, scope=scope, lease_id=lease_id)
        if row is None:
            return None
        payload = row.get("lease_payload_json")
        if (
            not isinstance(payload, Mapping)
            or payload.get("attempt_id") != attempt_id
            or payload.get("owner_generation") != owner_generation
            or row.get("executor_status") not in {"completed", "failed", "cancelled"}
            or row.get("executor_terminal_received_at") is None
        ):
            return None
        if await self._attempt_settlement_mode(conn):
            try:
                detail = await self._settlement.read_attempt(
                    conn,
                    tenant_id=tenant_id,
                    run_id=run_id,
                    attempt_id=attempt_id,
                    require_mapped=True,
                )
            except RuntimeError:
                return None
            receipt = detail.get("receipt_json") if detail else None
            accepted_context = detail.get("accepted_context_json") if detail else None
            phase = str(detail.get("settlement_phase") or "") if detail else ""
            eligible = (
                detail is not None
                and detail.get("source_lease_id") == lease_id
                and detail.get("receipt_owner_generation") == owner_generation
                and isinstance(receipt, Mapping)
                and receipt.get("run_id") == run_id
                and isinstance(accepted_context, Mapping)
                and bool(str(accepted_context.get("adapter_name") or "").strip())
                and (
                    (row.get("status") == "active" and phase in {"pending", "claimed", "retry", "failed"})
                    or (
                        row.get("status") == "released"
                        and row.get("released_at") is not None
                        and phase == "settled"
                        and detail.get("settled_at") is not None
                    )
                )
            )
            return await self._settlement_view(conn, row, require_mapped=True) if eligible else None

        accepted_context = row.get("executor_reconciliation_context_json")
        reconciliation_status = str(row.get("executor_reconciliation_status") or "")
        eligible = (
            row.get("executor_terminal_json") is not None
            and isinstance(accepted_context, Mapping)
            and bool(str(accepted_context.get("adapter_name") or "").strip())
            and (
                (
                    row.get("status") == "active"
                    and reconciliation_status in {"pending", "claimed", "retry", "failed"}
                )
                or (
                    row.get("status") == "released"
                    and row.get("released_at") is not None
                    and reconciliation_status == "finalized"
                    and row.get("executor_reconciled_at") is not None
                )
            )
        )
        return dict(row) if eligible else None

    async def record_sandbox_reconciliation_stage(
        self, conn: Any, *, lease_id: str, claim_token: str, stage: dict[str, Any],
    ) -> bool:
        ensure_reconciliation_stage_size(stage)
        stage_binding = self._valid_checkpoint_stage(stage)
        if stage_binding is None or not isinstance(claim_token, str) or not claim_token:
            return False
        stage_scope, owner_generation, container_id = stage_binding
        scope = {
            "tenant_id": stage_scope["tenant_id"],
            "run_id": stage_scope["run_id"],
            "attempt_id": stage_scope["attempt_id"],
        }
        await self._write_enabled(conn, scope)
        if not await self._attempt_settlement_mode(conn):
            return await self._repository.record_sandbox_reconciliation_stage_legacy(
                conn, lease_id=lease_id, claim_token=claim_token, stage=dict(stage),
            )

        try:
            detail = await self._settlement.read_attempt(conn, **scope, require_mapped=True)
        except RuntimeError:
            return False
        accepted_context = detail.get("accepted_context_json") if detail else None
        if (
            detail is None
            or detail.get("source_lease_id") != lease_id
            or detail.get("settlement_phase") != "claimed"
            or detail.get("settlement_claim_token") != claim_token
            or detail.get("receipt_json") is None
            or detail.get("receipt_owner_generation") != owner_generation
            or not isinstance(accepted_context, Mapping)
            or not str(accepted_context.get("adapter_name") or "").strip()
        ):
            return False
        row = await self._repository.get_scoped_checkpoint_lease(
            conn, scope=stage_scope, lease_id=lease_id,
        )
        if row is None or row.get("status") not in {"active", "released"}:
            return False
        if (
            row.get("executor_status") not in {"completed", "failed", "cancelled"}
            or row.get("executor_terminal_received_at") is None
        ):
            return False
        payload = row.get("lease_payload_json")
        if not isinstance(payload, Mapping):
            return False
        existing_stage: Any = payload.get("terminal_reconciliation")
        if "terminal_reconciliation" not in payload:
            expected_stage = None
        elif existing_stage == stage:
            expected_stage = dict(existing_stage)
        elif "continuation" in stage:
            predecessor = dict(stage)
            predecessor.pop("continuation")
            if existing_stage != predecessor:
                return False
            expected_stage = predecessor
        else:
            return False
        old_claim_marker = payload.get("terminal_reconciliation_claim")
        if "terminal_reconciliation_claim" in payload and not self._checkpoint_claim_marker_valid(
            old_claim_marker, owner_generation=owner_generation, container_id=container_id,
        ):
            return False
        claim_marker = self._checkpoint_claim_marker(
            claim_token=claim_token,
            owner_generation=owner_generation,
            container_id=container_id,
        )
        return await self._repository.record_sandbox_reconciliation_stage_resource(
            conn,
            scope=stage_scope,
            lease_id=lease_id,
            owner_generation=owner_generation,
            container_id=container_id,
            stage=dict(stage),
            expected_stage=expected_stage,
            expected_claim_marker=(
                dict(old_claim_marker) if isinstance(old_claim_marker, Mapping) else None
            ),
            claim_marker=claim_marker,
        )

    async def finalize_stopped_executor_checkpoint(
        self,
        conn: Any,
        *,
        scope: dict[str, str],
        lease_id: str,
        claim_token: str,
        expected_stage: dict[str, Any],
    ) -> bool:
        stage_binding = self._valid_checkpoint_stage(expected_stage)
        normalized_scope = self._checkpoint_scope(scope)
        if (
            stage_binding is None
            or normalized_scope is None
            or stage_binding[0] != normalized_scope
            or expected_stage.get("continuation") is None
            or not isinstance(claim_token, str)
            or not claim_token
        ):
            return False
        _stage_scope, owner_generation, container_id = stage_binding
        settlement_scope = {
            "tenant_id": normalized_scope["tenant_id"],
            "run_id": normalized_scope["run_id"],
            "attempt_id": normalized_scope["attempt_id"],
        }
        await self._write_enabled(conn, settlement_scope)
        if not await self._attempt_settlement_mode(conn):
            return await self._repository.finalize_stopped_executor_checkpoint_legacy(
                conn,
                scope=normalized_scope,
                lease_id=lease_id,
                claim_token=claim_token,
                expected_stage=expected_stage,
            )

        row = await self._repository.get_scoped_checkpoint_lease(
            conn, scope=normalized_scope, lease_id=lease_id,
        )
        if row is None:
            return False
        payload = row.get("lease_payload_json")
        if not isinstance(payload, Mapping) or payload.get("terminal_reconciliation") != expected_stage:
            return False
        claim_marker = self._checkpoint_claim_marker(
            claim_token=claim_token,
            owner_generation=owner_generation,
            container_id=container_id,
        )
        if payload.get("terminal_reconciliation_claim") != claim_marker:
            return False
        repair = payload.get("checkpoint_repair")
        if repair is not None and (
            not isinstance(repair, Mapping)
            or repair.get("schema_version") != "ai-platform.checkpoint-repair.v1"
            or repair.get("claim_token") != claim_token
            or repair.get("writer_generation") != owner_generation
            or repair.get("cancellation_generation") != owner_generation + 1
            or not repair.get("invocation_id")
        ):
            return False
        try:
            detail = await self._settlement.read_attempt(
                conn, **settlement_scope, require_mapped=True,
            )
        except RuntimeError:
            return False
        if (
            detail is None
            or detail.get("source_lease_id") != lease_id
            or detail.get("receipt_json") is None
            or detail.get("receipt_owner_generation") != owner_generation
            or not isinstance(detail.get("accepted_context_json"), Mapping)
            or not str(detail["accepted_context_json"].get("adapter_name") or "").strip()
        ):
            return False
        if detail.get("settlement_phase") == "claimed":
            if detail.get("settlement_claim_token") != claim_token:
                return False
            await self._settlement.refresh_snapshot_in_transaction(conn, **settlement_scope)
            try:
                detail = await self._settlement.read_attempt(
                    conn, **settlement_scope, require_mapped=True,
                )
            except RuntimeError:
                return False
        if (
            detail is None
            or detail.get("settlement_phase") != "settled"
            or detail.get("settled_at") is None
            or detail.get("result_json") is None
            or detail.get("result_status") not in {
                "succeeded", "failed", "cancelled", "canceled",
                "dead-letter", "dead_letter", "dead-lettered",
            }
        ):
            return False
        return await self._repository.finalize_stopped_executor_checkpoint_resource(
            conn,
            scope=normalized_scope,
            lease_id=lease_id,
            claim_token=claim_token,
            expected_stage=expected_stage,
            claim_marker=claim_marker,
        )

    async def get_quarantined_checkpoint_for_repair(
        self, conn: Any, *, scope: dict[str, str], lease_id: str,
    ) -> dict[str, Any] | None:
        repair_scope = self._checkpoint_repair_scope(scope)
        if repair_scope is None:
            return None
        settlement_scope = {
            "tenant_id": repair_scope["tenant_id"],
            "run_id": repair_scope["run_id"],
            "attempt_id": repair_scope["attempt_id"],
        }
        await self._write_enabled(conn, settlement_scope)
        if not await self._attempt_settlement_mode(conn):
            return await self._repository.get_quarantined_checkpoint_for_repair_legacy(
                conn, scope=repair_scope, lease_id=lease_id,
            )
        row = await self._repository.get_quarantined_checkpoint_for_repair_resource(
            conn, scope=repair_scope, lease_id=lease_id,
        )
        if row is None:
            return None
        stage = (row.get("lease_payload_json") or {}).get("terminal_reconciliation")
        stage_binding = self._valid_checkpoint_stage(stage, row=row)
        if stage_binding is None:
            return None
        try:
            authority = await self._settlement.checkpoint_repair_candidate(
                conn,
                scope=repair_scope,
                lease_id=lease_id,
                writer_generation=stage_binding[1],
            )
        except RuntimeError:
            return None
        if (
            authority is None
            or authority.get("source_lease_id") != lease_id
            or authority.get("receipt_owner_generation") != stage_binding[1]
        ):
            return None
        return await self._settlement_view(conn, row, require_mapped=True)

    async def claim_quarantined_checkpoint_for_repair(
        self,
        conn: Any,
        *,
        scope: dict[str, str],
        lease_id: str,
        invocation_id: str,
        claim_token: str,
        expected_updated_at: Any,
        expected_stage: dict[str, Any],
        writer_generation: int,
        cancellation_generation: int,
    ) -> dict[str, Any] | None:
        repair_scope = self._checkpoint_repair_scope(scope)
        if repair_scope is None:
            return None
        settlement_scope = {
            "tenant_id": repair_scope["tenant_id"],
            "run_id": repair_scope["run_id"],
            "attempt_id": repair_scope["attempt_id"],
        }
        await self._write_enabled(conn, settlement_scope)
        if not await self._attempt_settlement_mode(conn):
            return await self._repository.claim_quarantined_checkpoint_for_repair_legacy(
                conn,
                scope=repair_scope,
                lease_id=lease_id,
                invocation_id=invocation_id,
                claim_token=claim_token,
                expected_updated_at=expected_updated_at,
                expected_stage=expected_stage,
                writer_generation=writer_generation,
                cancellation_generation=cancellation_generation,
            )
        row = await self._repository.get_quarantined_checkpoint_for_repair_resource(
            conn, scope=repair_scope, lease_id=lease_id,
        )
        if (
            row is None
            or row.get("updated_at") != expected_updated_at
            or (row.get("lease_payload_json") or {}).get("terminal_reconciliation") != expected_stage
        ):
            return None
        stage_binding = self._valid_checkpoint_stage(expected_stage, row=row)
        if (
            stage_binding is None
            or stage_binding[1] != writer_generation
            or cancellation_generation != writer_generation + 1
            or not isinstance(invocation_id, str)
            or not invocation_id
            or not isinstance(claim_token, str)
            or not claim_token
        ):
            return None
        payload = row.get("lease_payload_json")
        old_claim_marker = payload.get("terminal_reconciliation_claim") if isinstance(payload, Mapping) else None
        if old_claim_marker is not None and not self._checkpoint_claim_marker_valid(
            old_claim_marker,
            owner_generation=writer_generation,
            container_id=stage_binding[2],
        ):
            return None
        try:
            authority = await self._settlement.checkpoint_repair_candidate(
                conn,
                scope=repair_scope,
                lease_id=lease_id,
                writer_generation=writer_generation,
            )
        except RuntimeError:
            return None
        if (
            authority is None
            or authority.get("source_lease_id") != lease_id
            or authority.get("receipt_owner_generation") != writer_generation
        ):
            return None
        expected_claim = authority.get("settlement_claim_token")
        if not isinstance(expected_claim, str) or not expected_claim:
            return None
        claimed_runs = await self._settlement.claim_checkpoint_repair_in_transaction(
            conn,
            scope=repair_scope,
            lease_id=lease_id,
            expected_claim_token=expected_claim,
            claim_token=claim_token,
            writer_generation=writer_generation,
            cancellation_generation=cancellation_generation,
        )
        if not claimed_runs:
            return None
        claim_marker = self._checkpoint_claim_marker(
            claim_token=claim_token,
            owner_generation=writer_generation,
            container_id=stage_binding[2],
        )
        claimed_resource = await self._repository.claim_quarantined_checkpoint_for_repair_resource(
            conn,
            scope=repair_scope,
            lease_id=lease_id,
            invocation_id=invocation_id,
            claim_token=claim_token,
            expected_updated_at=expected_updated_at,
            expected_stage=expected_stage,
            writer_generation=writer_generation,
            cancellation_generation=cancellation_generation,
            expected_claim_marker=(
                dict(old_claim_marker) if isinstance(old_claim_marker, Mapping) else None
            ),
            claim_marker=claim_marker,
        )
        if claimed_resource is None:
            # The caller's transaction must abort the Runs claim with this resource CAS.
            raise RuntimeError("checkpoint_repair_resource_claim_lost")
        return await self._settlement_view(conn, claimed_resource, require_mapped=True)
