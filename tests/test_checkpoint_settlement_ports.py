from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import unittest
from typing import Any

from app.platform.postgres.limits import RUN_RESULT_MAX_BYTES
from app.sandbox.application.attempt_settlement_shadow import (
    AttemptSettlementShadowLeaseRepository,
)
from app.sandbox.domain.checkpoint_stage import (
    SANDBOX_RECONCILIATION_STAGE_MAX_BYTES,
    SandboxCheckpointSizeLimitError,
    ensure_reconciliation_stage_size,
)
from app.sandbox.infrastructure import checkpoints_postgres


class _Settlement:
    def __init__(self, *, storage: str = "attempt.v1") -> None:
        self.storage = storage
        self.detail: dict[str, Any] = {
            "mapping_state": "mapped",
            "source_lease_id": "lease-1",
            "receipt_json": {"run_id": "run-1", "status": "failed"},
            "receipt_owner_generation": 7,
            "accepted_context_json": {"adapter_name": "claude-agent-worker"},
            "settlement_phase": "claimed",
            "settlement_claim_token": "claim-1",
            "settlement_attempt_count": 1,
        }
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def lock_writer_fence(self, conn: Any) -> None:
        self.calls.append(("writer_fence", {}))

    async def lock_sources_in_transaction(self, conn: Any, **kwargs: Any) -> bool:
        self.calls.append(("lock_sources", kwargs))
        return True

    async def writes_enabled(self, conn: Any, *, shadow_enabled: bool) -> bool:
        return True

    async def storage_version(self, conn: Any) -> str:
        return self.storage

    async def writer_version(self, conn: Any) -> str:
        return "attempt.v1" if self.storage == "attempt.v1" else "sandbox_lease.v1"

    async def attempt_writer_active(self, conn: Any) -> bool:
        return self.storage == "attempt.v1"

    async def read_attempt(self, conn: Any, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("read_attempt", kwargs))
        return deepcopy(self.detail)

    async def refresh_snapshot_in_transaction(self, conn: Any, **kwargs: Any) -> bool:
        self.calls.append(("refresh_snapshot", kwargs))
        self.detail.update(
            settlement_phase="settled",
            settlement_claim_token=None,
            settlement_claimed_at=None,
            settled_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            result_status="cancelled",
            result_json={"message": "cancelled"},
        )
        return True

    async def checkpoint_repair_candidate(self, conn: Any, **kwargs: Any) -> dict[str, Any] | None:
        self.calls.append(("repair_candidate", kwargs))
        if self.detail.get("settlement_phase") != "claimed":
            return None
        return deepcopy(self.detail)

    async def claim_checkpoint_repair_in_transaction(self, conn: Any, **kwargs: Any) -> bool:
        self.calls.append(("claim_repair", kwargs))
        if self.detail.get("settlement_claim_token") != kwargs["expected_claim_token"]:
            return False
        self.detail.update(
            settlement_phase="claimed",
            settlement_claim_token=kwargs["claim_token"],
            settlement_claimed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        return True


class _Repository:
    def __init__(self, row: dict[str, Any]) -> None:
        self.row = deepcopy(row)
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def get_scoped_checkpoint_lease(self, conn: Any, **kwargs: Any) -> dict[str, Any] | None:
        self.calls.append(("get_scoped", kwargs))
        if kwargs["lease_id"] != self.row["id"]:
            return None
        return deepcopy(self.row)


    async def record_sandbox_reconciliation_stage_legacy(self, conn: Any, **kwargs: Any) -> bool:
        self.calls.append(("record_stage_legacy", kwargs))
        return True

    async def record_sandbox_reconciliation_stage_resource(self, conn: Any, **kwargs: Any) -> bool:
        self.calls.append(("record_stage_resource", kwargs))
        payload = self.row["lease_payload_json"]
        payload["terminal_reconciliation"] = deepcopy(kwargs["stage"])
        payload["terminal_reconciliation_claim"] = deepcopy(kwargs["claim_marker"])
        return True

    async def finalize_stopped_executor_checkpoint_legacy(self, conn: Any, **kwargs: Any) -> bool:
        self.calls.append(("finalize_legacy", kwargs))
        return True

    async def finalize_stopped_executor_checkpoint_resource(self, conn: Any, **kwargs: Any) -> bool:
        self.calls.append(("finalize_resource", kwargs))
        self.row.update(
            status="released",
            released_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            executor_cleanup_status="finalized",
        )
        return True

    async def get_quarantined_checkpoint_for_repair_legacy(self, conn: Any, **kwargs: Any) -> dict[str, Any] | None:
        self.calls.append(("get_repair_legacy", kwargs))
        return deepcopy(self.row)

    async def get_quarantined_checkpoint_for_repair_resource(self, conn: Any, **kwargs: Any) -> dict[str, Any] | None:
        self.calls.append(("get_repair_resource", kwargs))
        if self.row.get("status") != "quarantined":
            return None
        return deepcopy(self.row)

    async def claim_quarantined_checkpoint_for_repair_legacy(self, conn: Any, **kwargs: Any) -> dict[str, Any] | None:
        self.calls.append(("claim_repair_legacy", kwargs))
        return deepcopy(self.row)

    async def claim_quarantined_checkpoint_for_repair_resource(self, conn: Any, **kwargs: Any) -> dict[str, Any] | None:
        self.calls.append(("claim_repair_resource", kwargs))
        self.row["status"] = "released"
        self.row["released_at"] = datetime(2026, 1, 1, tzinfo=timezone.utc)
        payload = self.row["lease_payload_json"]
        payload["terminal_reconciliation_claim"] = deepcopy(kwargs["claim_marker"])
        payload["checkpoint_repair"] = {
            "schema_version": "ai-platform.checkpoint-repair.v1",
            "invocation_id": kwargs["invocation_id"],
            "writer_generation": kwargs["writer_generation"],
            "cancellation_generation": kwargs["cancellation_generation"],
            "claim_token": kwargs["claim_token"],
        }
        return deepcopy(self.row)


class _SQLCursor:
    async def fetchone(self) -> dict[str, str]:
        return {"id": "lease-1"}


class _SQLConnection:
    def __init__(self) -> None:
        self.statements: list[str] = []

    async def execute(self, statement: str, params: Any = None) -> _SQLCursor:
        self.statements.append(statement)
        return _SQLCursor()


class CheckpointSettlementPortTests(unittest.IsolatedAsyncioTestCase):
    scope = {
        "tenant_id": "tenant-1",
        "workspace_id": "workspace-1",
        "user_id": "user-1",
        "session_id": "session-1",
        "run_id": "run-1",
        "attempt_id": "attempt-1",
    }
    repair_scope = {**scope, "agent_id": "agent-1"}

    def _stage(self, *, continuation: bool = False) -> dict[str, Any]:
        stage: dict[str, Any] = {
            "schema_version": "ai-platform.terminal-reconciliation.v1",
            "scope": dict(self.scope),
            "owner_generation": 7,
            "container_id": "container-1",
            "artifact_workspace": None,
            "terminal_result": {"run_id": "run-1", "status": "failed"},
        }
        if continuation:
            stage["continuation"] = {
                "tenant_id": "tenant-1",
                "run_id": "run-1",
                "attempt_id": "attempt-1",
                "stop_evidence": "provider_stopped",
                "workspace_receipt": {"state": "saved"},
            }
        return stage

    def _row(self, *, status: str = "active", continuation: bool = False) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "attempt_id": "attempt-1",
            "owner_generation": 7,
        }
        if status == "quarantined" or continuation:
            payload["terminal_reconciliation"] = self._stage(continuation=continuation)
        if continuation:
            payload["terminal_reconciliation_claim"] = {
                "schema_version": "ai-platform.terminal-reconciliation-claim.v1",
                "claim_token": "claim-1",
                "owner_generation": 7,
                "container_id": "container-1",
            }
        if status == "quarantined":
            payload.setdefault("terminal_reconciliation_claim", {
                "schema_version": "ai-platform.terminal-reconciliation-claim.v1",
                "claim_token": "claim-1",
                "owner_generation": 7,
                "container_id": "container-1",
            })
        row = {
            **self.scope,
            "id": "lease-1",
            "status": status,
            "lease_payload_json": payload,
            "runtime_container_id": "container-1",
            "executor_status": "failed",
            "executor_terminal_received_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
            "executor_cleanup_status": "failed" if status == "quarantined" else "waiting_settlement",
            "executor_cleanup_claim_token": None,
            "executor_reconciliation_error": (
                "executor_reconciliation_runtime_handle_invalid" if status == "quarantined" else ""
            ),
            "updated_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
            "released_at": None,
        }
        return row

    def _repository(self, *, status: str = "active", continuation: bool = False):
        settlement = _Settlement()
        repository = _Repository(self._row(status=status, continuation=continuation))
        port = AttemptSettlementShadowLeaseRepository(
            repository=repository, settlement_projection=settlement, enabled=lambda: True,
        )
        return port, repository, settlement

    async def test_handoff_projects_runs_receipt_after_resource_scope_lock(self) -> None:
        port, repository, _settlement = self._repository()
        row = await port.get_sandbox_executor_terminal_handoff(
            None, **self.scope, lease_id="lease-1", owner_generation=7,
        )
        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(row["executor_terminal_json"]["run_id"], "run-1")
        self.assertEqual(row["executor_reconciliation_context_json"]["adapter_name"], "claude-agent-worker")
        self.assertTrue(any(name == "get_scoped" for name, _ in repository.calls))

    async def test_stage_requires_runs_claim_and_persists_resource_cas(self) -> None:
        port, repository, settlement = self._repository()
        recorded = await port.record_sandbox_reconciliation_stage(
            None, lease_id="lease-1", claim_token="claim-1", stage=self._stage(),
        )
        self.assertTrue(recorded)
        call = next(kwargs for name, kwargs in repository.calls if name == "record_stage_resource")
        self.assertIsNone(call["expected_stage"])
        self.assertEqual(call["claim_marker"]["claim_token"], "claim-1")
        self.assertTrue(any(name == "read_attempt" for name, _ in settlement.calls))
        self.assertFalse(any(name == "record_stage_legacy" for name, _ in repository.calls))

    async def test_stage_size_guard_preserves_compact_utf8_byte_boundary(self) -> None:
        self.assertEqual(SANDBOX_RECONCILIATION_STAGE_MAX_BYTES, RUN_RESULT_MAX_BYTES)
        json_overhead = len('{"value":""}'.encode("utf-8"))
        exact_value = "é" * ((SANDBOX_RECONCILIATION_STAGE_MAX_BYTES - json_overhead) // 2)
        ensure_reconciliation_stage_size({"value": exact_value})

        port, repository, _settlement = self._repository()
        oversized_stage = self._stage()
        oversized_stage["extra"] = "x" * SANDBOX_RECONCILIATION_STAGE_MAX_BYTES
        with self.assertRaises(SandboxCheckpointSizeLimitError) as raised:
            await port.record_sandbox_reconciliation_stage(
                None, lease_id="lease-1", claim_token="claim-1", stage=oversized_stage,
            )

        self.assertEqual(raised.exception.code, "sandbox_reconciliation_stage_too_large")
        self.assertFalse(repository.calls)

    def test_stage_size_guard_preserves_invalid_json_error_code(self) -> None:
        for value in ({"value": float("nan")}, {"value": "\ud800"}, {"value": object()}):
            with self.subTest(value_type=type(value["value"]).__name__):
                with self.assertRaises(SandboxCheckpointSizeLimitError) as raised:
                    ensure_reconciliation_stage_size(value)
                self.assertEqual(raised.exception.code, "sandbox_reconciliation_stage_invalid")

    async def test_stage_rejects_stale_runs_claim_without_resource_write(self) -> None:
        port, repository, settlement = self._repository()
        settlement.detail["settlement_claim_token"] = "newer-claim"
        recorded = await port.record_sandbox_reconciliation_stage(
            None, lease_id="lease-1", claim_token="claim-1", stage=self._stage(),
        )
        self.assertFalse(recorded)
        self.assertFalse(any(name == "record_stage_resource" for name, _ in repository.calls))

    async def test_reclaimed_run_claim_transfers_only_the_frozen_stage_marker(self) -> None:
        for marker_present in (True, False):
            with self.subTest(marker_present=marker_present):
                port, repository, settlement = self._repository(continuation=True)
                settlement.detail["settlement_claim_token"] = "claim-2"
                payload = repository.row["lease_payload_json"]
                original_stage = deepcopy(payload["terminal_reconciliation"])
                if not marker_present:
                    payload.pop("terminal_reconciliation_claim")

                recorded = await port.record_sandbox_reconciliation_stage(
                    None,
                    lease_id="lease-1",
                    claim_token="claim-2",
                    stage=original_stage,
                )

                self.assertTrue(recorded)
                call = next(
                    kwargs for name, kwargs in repository.calls if name == "record_stage_resource"
                )
                self.assertEqual(call["stage"], original_stage)
                self.assertEqual(call["expected_stage"], original_stage)
                self.assertEqual(
                    call["expected_claim_marker"]["claim_token"] if marker_present else None,
                    "claim-1" if marker_present else None,
                )
                self.assertEqual(call["claim_marker"]["claim_token"], "claim-2")

    async def test_old_claim_cannot_write_or_finalize_after_reclaim(self) -> None:
        port, repository, settlement = self._repository(continuation=True)
        settlement.detail["settlement_claim_token"] = "claim-2"
        repository.row["lease_payload_json"]["terminal_reconciliation_claim"]["claim_token"] = "claim-2"
        stage = self._stage(continuation=True)

        recorded = await port.record_sandbox_reconciliation_stage(
            None, lease_id="lease-1", claim_token="claim-1", stage=stage,
        )
        finalized = await port.finalize_stopped_executor_checkpoint(
            None,
            scope=self.scope,
            lease_id="lease-1",
            claim_token="claim-1",
            expected_stage=stage,
        )

        self.assertFalse(recorded)
        self.assertFalse(finalized)
        self.assertFalse(any(name == "record_stage_resource" for name, _ in repository.calls))
        self.assertFalse(any(name == "refresh_snapshot" for name, _ in settlement.calls))
        self.assertFalse(any(name == "finalize_resource" for name, _ in repository.calls))

    async def test_finalize_requires_current_claim_then_settled_runs_detail(self) -> None:
        port, repository, settlement = self._repository(continuation=True)
        finalized = await port.finalize_stopped_executor_checkpoint(
            None,
            scope=self.scope,
            lease_id="lease-1",
            claim_token="claim-1",
            expected_stage=self._stage(continuation=True),
        )
        self.assertTrue(finalized)
        self.assertTrue(any(name == "refresh_snapshot" for name, _ in settlement.calls))
        self.assertTrue(any(name == "finalize_resource" for name, _ in repository.calls))

    async def test_finalize_rejects_stale_claim_before_resource_release(self) -> None:
        port, repository, settlement = self._repository(continuation=True)
        settlement.detail["settlement_claim_token"] = "replacement-claim"
        finalized = await port.finalize_stopped_executor_checkpoint(
            None,
            scope=self.scope,
            lease_id="lease-1",
            claim_token="claim-1",
            expected_stage=self._stage(continuation=True),
        )
        self.assertFalse(finalized)
        self.assertFalse(any(name == "refresh_snapshot" for name, _ in settlement.calls))
        self.assertFalse(any(name == "finalize_resource" for name, _ in repository.calls))

    async def test_operator_repair_transfers_runs_claim_and_resource_claim_together(self) -> None:
        port, repository, settlement = self._repository(status="quarantined")
        candidate = await port.get_quarantined_checkpoint_for_repair(
            None, scope=self.repair_scope, lease_id="lease-1",
        )
        self.assertIsNotNone(candidate)
        claimed = await port.claim_quarantined_checkpoint_for_repair(
            None,
            scope=self.repair_scope,
            lease_id="lease-1",
            invocation_id="operator-1",
            claim_token="repair-claim",
            expected_updated_at=repository.row["updated_at"],
            expected_stage=self._stage(),
            writer_generation=7,
            cancellation_generation=8,
        )
        self.assertIsNotNone(claimed)
        self.assertEqual(settlement.detail["settlement_claim_token"], "repair-claim")
        run_claim = next(kwargs for name, kwargs in settlement.calls if name == "claim_repair")
        self.assertEqual(run_claim["expected_claim_token"], "claim-1")
        resource_claim = next(kwargs for name, kwargs in repository.calls if name == "claim_repair_resource")
        self.assertEqual(resource_claim["expected_claim_marker"]["claim_token"], "claim-1")
        self.assertEqual(resource_claim["claim_marker"]["claim_token"], "repair-claim")

    async def test_legacy_stage_and_finalize_keep_lease_owned_claim_path(self) -> None:
        settlement = _Settlement(storage="sandbox_lease.v1")
        repository = _Repository(self._row(continuation=True))
        port = AttemptSettlementShadowLeaseRepository(
            repository=repository, settlement_projection=settlement, enabled=lambda: True,
        )
        self.assertTrue(await port.record_sandbox_reconciliation_stage(
            None, lease_id="lease-1", claim_token="claim-1", stage=self._stage(),
        ))
        self.assertTrue(await port.finalize_stopped_executor_checkpoint(
            None,
            scope=self.scope,
            lease_id="lease-1",
            claim_token="claim-1",
            expected_stage=self._stage(continuation=True),
        ))
        self.assertTrue(any(name == "record_stage_legacy" for name, _ in repository.calls))
        self.assertTrue(any(name == "finalize_legacy" for name, _ in repository.calls))
        self.assertFalse(any(name == "read_attempt" for name, _ in settlement.calls))

    async def test_attempt_resource_sql_does_not_name_retired_settlement_columns(self) -> None:
        conn = _SQLConnection()
        stage = self._stage(continuation=True)
        marker = {
            "schema_version": "ai-platform.terminal-reconciliation-claim.v1",
            "claim_token": "claim-1",
            "owner_generation": 7,
            "container_id": "container-1",
        }
        await checkpoints_postgres.record_sandbox_reconciliation_stage_resource(
            conn,
            scope=self.scope,
            lease_id="lease-1",
            owner_generation=7,
            container_id="container-1",
            stage=stage,
            expected_stage=None,
            expected_claim_marker=None,
            claim_marker=marker,
        )
        await checkpoints_postgres.finalize_stopped_executor_checkpoint_resource(
            conn,
            scope=self.scope,
            lease_id="lease-1",
            claim_token="claim-1",
            expected_stage=stage,
            claim_marker=marker,
        )
        await checkpoints_postgres.claim_quarantined_checkpoint_for_repair_resource(
            conn,
            scope=self.repair_scope,
            lease_id="lease-1",
            invocation_id="operator-1",
            claim_token="repair-claim",
            expected_updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            expected_stage=self._stage(),
            writer_generation=7,
            cancellation_generation=8,
            expected_claim_marker=marker,
            claim_marker={**marker, "claim_token": "repair-claim"},
        )
        repair_statement = conn.statements[-1]
        self.assertIn("'{checkpoint_repair}'", repair_statement)
        self.assertIn("'{terminal_reconciliation_claim}'", repair_statement)
        retired_names = (
            "executor_terminal_json",
            "executor_reconciliation_context_json",
            "executor_reconciliation_intent_context_json",
            "executor_reconciliation_status",
            "executor_reconciliation_claim_token",
            "executor_reconciliation_claimed_at",
            "executor_reconciliation_retry_not_before",
            "executor_terminal_reconciliation_attempt_count",
        )
        for statement in conn.statements:
            for retired_name in retired_names:
                self.assertNotIn(retired_name, statement)


if __name__ == "__main__":
    unittest.main()
