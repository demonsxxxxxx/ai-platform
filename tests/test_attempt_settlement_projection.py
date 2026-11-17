from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from copy import deepcopy


from app.runs.domain.attempt_settlement_projection import (
    ATTEMPT_SETTLEMENT_PROJECTION_FIELDS,
    AttemptSettlementProjection,
    AttemptSettlementProjectionConflict,
    CleanupObservation,
)
from app.runs.infrastructure import attempt_settlement_projection_postgres as projection
from app.runs.infrastructure import attempt_settlement_writer_postgres as writer



import pytest

from app.runs.infrastructure.attempt_settlement_upgrade_projection import (
    project_legacy_attempt_settlement,
)
from tests.support.db_transactions import event_loop_policy as event_loop_policy


NOW = datetime(2026, 10, 25, 4, 0, tzinfo=timezone.utc)


def attempt(*, status="running", generation=4, started=NOW, **overrides):
    return {
        "id": "attempt-a",
        "tenant_id": "tenant-a",
        "run_id": "run-a",
        "status": status,
        "owner_kind": "queue_worker",
        "owner_id": "worker-a",
        "owner_generation": generation,
        "started_at": started,
        "finished_at": None,
        "error_code": None,
        "terminal_reason": "",
        **overrides,
    }


def run(*, status="running", cancel_requested_at=None, **overrides):
    return {
        "id": "run-a",
        "tenant_id": "tenant-a",
        "status": status,
        "cancel_requested_at": cancel_requested_at,
        "result_json": {},
        "error_code": None,
        "finished_at": None,
        **overrides,
    }


def lease(
    *,
    status="active",
    reconciliation_status="pending",
    generation=4,
    receipt=None,
    context=None,
    **overrides,
):
    return {
        "id": "lease-a",
        "tenant_id": "tenant-a",
        "run_id": "run-a",
        "attempt_id": "attempt-a",
        "status": status,
        "lease_payload_json": {"owner_generation": generation},
        "executor_status": "completed" if receipt else "accepted",
        "executor_terminal_json": receipt,
        "executor_reconciliation_context_json": context,
        "executor_reconciliation_intent_context_json": context,
        "executor_reconciliation_status": reconciliation_status,
        "executor_reconciliation_claim_token": None,
        "executor_reconciliation_claimed_at": None,
        "executor_reconciliation_retry_not_before": None,
        "executor_terminal_reconciliation_attempt_count": 2,
        "released_at": NOW if status == "released" else None,
        **overrides,
    }


def test_cancelled_before_dispatch_preserves_terminal_history_without_inventing_receipt():
    projected = project_legacy_attempt_settlement(
        attempt(status="cancelled", generation=6, finished_at=NOW, terminal_reason="run_cancelled"),
        run(status="cancelled", cancel_requested_at=NOW, finished_at=NOW, result_json={"answer": "stopped"}),
        [lease(status="released", generation=4, reconciliation_status="waiting_terminal",
               executor_status="pending", receipt=None, context=None, release_reason="run_cancelled")],
    )
    values = projected.to_mapping()
    assert values["mapping_state"] == "mapped"
    assert values["settlement_phase"] == "settled"
    assert values["result_json"] == {"answer": "stopped"}
    assert values["settlement_attempt_count"] == 2
    assert values["dispatch_state"] == "unknown"
    assert values["receipt_json"] is None and values["accepted_context_json"] is None
    assert projected.cleanup.released_pending_settlement_count == 0


@pytest.mark.parametrize("override", [
    {"dispatch_status": "unknown"}, {"executor_status": "accepted"},
    {"executor_reconciliation_context_json": {"accepted": True}},
    {"release_reason": "timeout"}, {"released_at": None},
    {"executor_reconciliation_claim_token": "owned"},
])
def test_cancelled_history_does_not_infer_settlement_from_ambiguous_dispatch(override):
    values = project_legacy_attempt_settlement(
        attempt(status="cancelled", generation=6, finished_at=NOW),
        run(status="cancelled", cancel_requested_at=NOW, finished_at=NOW),
        [lease(status="released", generation=4, reconciliation_status="waiting_terminal",
               **({"executor_status": "pending", "release_reason": "run_cancelled"} | override))],
    ).to_mapping()
    assert values["settlement_phase"] != "settled"


def test_pending_projection_preserves_receipt_context_retry_and_owner_generation():
    receipt = {
        "status": "succeeded",
        "answer": "complete",
        "diagnostics": ["mutable reconciliation note"],
    }
    retry_at = datetime(2026, 10, 25, 4, 1, tzinfo=timezone.utc)
    projected = project_legacy_attempt_settlement(
        attempt(),
        run(),
        [
            lease(
                reconciliation_status="retry",
                receipt=receipt,
                context={"snapshot": "accepted-context"},
                executor_reconciliation_retry_not_before=retry_at,
            )
        ],
    )

    values = projected.to_mapping()
    canonical_receipt = {"answer": "complete", "status": "succeeded"}
    assert values["mapping_state"] == "mapped"
    assert values["dispatch_state"] == "accepted"
    assert values["accepted_context_json"] == {"snapshot": "accepted-context"}
    assert values["intent_recovery_context_json"] == {"snapshot": "accepted-context"}
    assert values["receipt_json"] == receipt
    assert values["receipt_identity"] == "sandbox_terminal:lease-a:attempt-a"
    assert values["receipt_sha256"] == hashlib.sha256(
        json.dumps(canonical_receipt, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    assert values["settlement_phase"] == "retry"
    assert values["settlement_attempt_count"] == 2
    assert values["next_retry_at"] == retry_at
    assert values["settlement_claim_token"] is None
    assert values["attempt_owner_generation_snapshot"] == 4
    assert values["receipt_owner_generation"] == 4


def test_terminal_receipt_before_accepted_context_remains_explicit():
    projected = project_legacy_attempt_settlement(
        attempt(),
        run(),
        [
            lease(
                reconciliation_status="waiting_terminal",
                receipt={"status": "succeeded"},
                context=None,
            )
        ],
    )

    values = projected.to_mapping()
    assert values["mapping_state"] == "mapped"
    assert values["dispatch_state"] == "receipt_before_acceptance"
    assert values["settlement_phase"] == "awaiting_context"
    assert values["accepted_context_json"] is None


def test_accepted_context_without_terminal_receipt_keeps_receipt_tuple_empty():
    projected = project_legacy_attempt_settlement(
        attempt(),
        run(),
        [
            lease(
                reconciliation_status="waiting_terminal",
                receipt=None,
                context={"snapshot": "accepted-context"},
            )
        ],
    )

    values = projected.to_mapping()
    assert values["mapping_state"] == "mapped"
    assert values["dispatch_state"] == "accepted"
    assert values["settlement_phase"] == "awaiting_receipt"
    assert values["accepted_context_json"] == {"snapshot": "accepted-context"}
    assert values["receipt_identity"] is None
    assert values["receipt_json"] is None
    assert values["receipt_sha256"] is None
    assert values["receipt_owner_generation"] is None


@pytest.mark.parametrize("legacy_phase", ["cleanup_pending", "failed", "finalized"])
def test_settled_result_keeps_sandbox_cleanup_claim_outside_attempt_projection(legacy_phase):
    terminal_attempt_status = "failed" if legacy_phase == "failed" else "succeeded"
    terminal_run_status = terminal_attempt_status
    projected = project_legacy_attempt_settlement(
        attempt(
            status=terminal_attempt_status,
            generation=5,
            finished_at=NOW,
            error_code="terminal_reconciliation_failed" if legacy_phase == "failed" else None,
        ),
        run(
            status=terminal_run_status,
            result_json={"answer": "committed"},
            error_code="terminal_reconciliation_failed" if legacy_phase == "failed" else None,
            finished_at=NOW,
        ),
        [
            lease(
                status="released",
                reconciliation_status=legacy_phase,
                generation=4,
                receipt={"status": "succeeded"},
                context={"snapshot": "accepted-context"},
                executor_reconciliation_claim_token=(
                    "cleanup-claim" if legacy_phase in {"cleanup_pending", "failed"} else None
                ),
                executor_reconciliation_claimed_at=(
                    NOW if legacy_phase in {"cleanup_pending", "failed"} else None
                ),
            )
        ],
    )

    values = projected.to_mapping()
    assert values["mapping_state"] == "mapped"
    assert values["settlement_phase"] == "settled"
    assert values["result_json"] == {"answer": "committed"}
    assert values["result_status"] == terminal_run_status
    assert values["settlement_claim_token"] is None
    assert projected.cleanup.released_lease_count == 1
    assert projected.cleanup.cleanup_claim_count == (
        1 if legacy_phase in {"cleanup_pending", "failed"} else 0
    )
    assert projected.cleanup.released_pending_settlement_count == 0
    assert "cleanup_claim_token" not in values


def test_released_lease_can_still_be_pending_settlement():
    projected = project_legacy_attempt_settlement(
        attempt(),
        run(),
        [
            lease(
                status="released",
                reconciliation_status="claimed",
                receipt={"status": "succeeded"},
                context={"snapshot": "accepted-context"},
                executor_reconciliation_claim_token="settlement-claim",
                executor_reconciliation_claimed_at=NOW,
            )
        ],
    )

    values = projected.to_mapping()
    assert values["mapping_state"] == "mapped"
    assert values["settlement_phase"] == "claimed"
    assert values["settlement_claim_token"] == "settlement-claim"
    assert projected.cleanup.released_pending_settlement_count == 1


def test_unknown_and_conflicting_attempts_are_never_hidden():
    unknown = project_legacy_attempt_settlement(
        attempt(), run(), [lease(generation=9, receipt={"status": "succeeded"}, context={})]
    )
    conflict = project_legacy_attempt_settlement(
        attempt(),
        run(),
        [
            lease(receipt={"status": "succeeded"}, context={}),
            lease(id="lease-b", receipt={"status": "failed"}, context={}),
        ],
    )

    assert unknown.to_mapping()["mapping_state"] == "unknown"
    assert conflict.to_mapping()["mapping_state"] == "conflict"
    assert conflict.cleanup.lease_count == 2


def test_stale_maintenance_generation_gap_requires_reconciler_transition_facts():
    receipt = {"status": "failed", "answer": "interrupted"}
    recovered = project_legacy_attempt_settlement(
        attempt(
            status="failed",
            generation=6,
            started=NOW,
            finished_at=NOW,
            owner_kind="reconciler",
            owner_id="stale-run-maintenance",
            terminal_reason="run_failed",
            error_code="stale_run_interrupted",
        ),
        run(
            status="failed",
            result_json={"answer": "interrupted"},
            error_code="stale_run_interrupted",
            finished_at=NOW,
        ),
        [lease(generation=4, receipt=receipt, context={"snapshot": "accepted"}, reconciliation_status="failed")],
    )
    assert recovered.to_mapping()["mapping_state"] == "mapped"
    assert recovered.to_mapping()["settlement_phase"] == "settled"

    old_owner = project_legacy_attempt_settlement(
        attempt(
            status="failed",
            generation=6,
            finished_at=NOW,
            owner_kind="queue_worker",
            owner_id="old-worker",
            terminal_reason="run_failed",
            error_code="stale_run_interrupted",
        ),
        run(
            status="failed",
            result_json={"answer": "interrupted"},
            error_code="stale_run_interrupted",
            finished_at=NOW,
        ),
        [lease(generation=4, receipt=receipt, context={"snapshot": "accepted"}, reconciliation_status="failed")],
    )
    assert old_owner.to_mapping()["mapping_state"] == "unknown"


def test_probe_claim_without_receipt_does_not_become_settlement_claim():
    projected = project_legacy_attempt_settlement(
        attempt(),
        run(),
        [
            lease(
                reconciliation_status="claimed",
                receipt=None,
                context={"snapshot": "accepted"},
                executor_reconciliation_claim_token="probe-token",
                executor_reconciliation_claimed_at=NOW,
            )
        ],
    )

    values = projected.to_mapping()
    assert values["mapping_state"] == "mapped"
    assert values["settlement_phase"] == "awaiting_receipt"
    assert values["settlement_claim_token"] is None
    assert values["settlement_claimed_at"] is None


@pytest.mark.asyncio
async def test_settlement_view_preserves_sandbox_probe_and_cleanup_claims():
    from app.sandbox.application.attempt_settlement import (
        AttemptSettlementLeaseRepository,
    )

    probe_source = lease(
        reconciliation_status="claimed",
        receipt=None,
        context={"snapshot": "accepted"},
        executor_probe_claim_token="probe-token",
        executor_probe_claimed_at=NOW,
    )
    cleanup_source = lease(
        status="released",
        run_id="run-b",
        attempt_id="attempt-b",
        reconciliation_status="cleanup_pending",
        receipt={"status": "succeeded"},
        context={"snapshot": "accepted"},
        executor_cleanup_status="cleanup_pending",
        executor_cleanup_claim_token="cleanup-token",
        executor_cleanup_claimed_at=NOW,
    )
    cleanup_projection = project_legacy_attempt_settlement(
        attempt(
            id="attempt-b", run_id="run-b", status="succeeded", generation=5,
            finished_at=NOW,
        ),
        run(id="run-b", status="succeeded", result_json={"answer": "done"}, finished_at=NOW),
        [cleanup_source],
    ).to_mapping()
    probe_projection = project_legacy_attempt_settlement(
        attempt(), run(), [probe_source]
    ).to_mapping()

    class Repository:
        async def get_sandbox_lease(self, _conn, *, run_id, **_kwargs):
            return dict({"run-a": probe_source, "run-b": cleanup_source}[run_id])

    class Settlement:
        async def read_attempt(self, _conn, *, run_id, **_kwargs):
            return {"run-a": probe_projection, "run-b": cleanup_projection}[run_id]

    reader = AttemptSettlementLeaseRepository(
        repository=Repository(),
        settlement_projection=Settlement(),
    )
    probe = await reader.get_sandbox_lease(None, run_id="run-a")
    cleanup = await reader.get_sandbox_lease(None, run_id="run-b")

    assert probe["settlement_phase"] == "awaiting_receipt"
    assert probe["executor_probe_claim_token"] == "probe-token"
    assert probe["executor_probe_claimed_at"] == NOW
    assert cleanup["settlement_phase"] == "settled"
    assert cleanup["executor_cleanup_status"] == "cleanup_pending"
    assert cleanup["executor_cleanup_claim_token"] == "cleanup-token"


@pytest.mark.asyncio
async def test_resource_lease_reader_does_not_require_attempt_settlement_detail():
    from app.sandbox.application.attempt_settlement import (
        AttemptSettlementLeaseRepository,
    )

    source = lease()

    class Repository:
        async def get_sandbox_lease(self, _conn, **_kwargs):
            return dict(source)

    class MissingSettlement:
        async def read_attempt(self, _conn, **_kwargs):
            raise RuntimeError("attempt_settlement_projection_missing")

    reader = AttemptSettlementLeaseRepository(
        repository=Repository(),
        settlement_projection=MissingSettlement(),
    )

    with pytest.raises(RuntimeError, match="attempt_settlement_projection_missing"):
        await reader.get_sandbox_lease(None, lease_id="lease-a")
    resource = await reader.get_resource_sandbox_lease(None, lease_id="lease-a")
    assert resource["id"] == "lease-a"


class MemoryConnection:
    def __init__(self):
        self.row = dict.fromkeys(ATTEMPT_SETTLEMENT_PROJECTION_FIELDS)
        self.row.update(tenant_id="tenant-a", run_id="run-a", attempt_id="attempt-a",
                        mapping_state="mapped", settlement_attempt_count=0)

    async def execute(self, sql, params=()):
        if sql.lstrip().startswith("update run_attempt_settlements"):
            fields = [f for f in ATTEMPT_SETTLEMENT_PROJECTION_FIELDS
                      if f not in {"tenant_id", "run_id", "attempt_id", "schema_version"}]
            for field, value in zip(fields, params):
                self.row[field] = json.loads(value) if field in projection._JSON_FIELDS and value else value
        return self

    async def fetchone(self):
        return deepcopy(self.row)


@pytest.mark.asyncio
async def test_writer_and_projection_allow_only_ack_timing_enrichment(monkeypatch):
    conn = MemoryConnection()

    async def no_insert(*args):
        return False

    async def detail(*args, **kwargs):
        return {}, {}, {}, deepcopy(conn.row)

    monkeypatch.setattr(projection, "_insert_projection", no_insert)
    monkeypatch.setattr(writer, "_current_owned_detail", detail)
    scope = dict(tenant_id="tenant-a", run_id="run-a", attempt_id="attempt-a", lease_id="lease-a")
    facts = {"provider_session_id": "session-a", "snapshot": {"version": 1}}
    enriched = {**facts, "dispatch_timings": {"schema_version": "v1", "dispatch_ms": 12}}
    for context in (facts, enriched, enriched, facts):
        await writer.record_attempt_settlement_accepted_context(conn, **scope, accepted_context=context)
    assert conn.row["accepted_context_json"] == enriched
    assert conn.row["dispatch_state"] == "accepted"
    for context in ({**enriched, "snapshot": {"version": 2}},
                    {**facts, "dispatch_timings": {"dispatch_ms": 13}}):
        with pytest.raises(AttemptSettlementProjectionConflict):
            await writer.record_attempt_settlement_accepted_context(conn, **scope, accepted_context=context)
        # Bypassing the writer must still preserve the same immutable facts.
        values = {**conn.row, "accepted_context_json": context}
        with pytest.raises(AttemptSettlementProjectionConflict):
            await projection.persist_attempt_settlement_projection(
                conn, AttemptSettlementProjection(values, CleanupObservation()))
    assert conn.row["accepted_context_json"] == enriched
    await writer.record_attempt_settlement_intent(conn, **scope, intent_context=facts)
    with pytest.raises(AttemptSettlementProjectionConflict):
        await writer.record_attempt_settlement_intent(conn, **scope, intent_context=enriched)


@pytest.mark.asyncio
async def test_projection_direct_enrichment_and_stale_observation_preserve_timings(monkeypatch):
    conn = MemoryConnection()
    async def no_insert(*args):
        return False
    monkeypatch.setattr(projection, "_insert_projection", no_insert)
    facts = {"provider_session_id": "session-a"}
    enriched = {**facts, "dispatch_timings": {"dispatch_ms": 12}}
    for context in (facts, enriched, facts, None):
        await projection.persist_attempt_settlement_projection(
            conn, AttemptSettlementProjection({**conn.row, "accepted_context_json": context}, CleanupObservation()))
    assert conn.row["accepted_context_json"] == enriched


@pytest.mark.parametrize("incoming", [
    {"identity": "a", "generation": True},
    {"identity": "a"},
    {"identity": "a", "generation": 1, "extra": "new"},
    {"identity": "a", "generation": 1, "dispatch_timings": None},
    {"identity": "a", "generation": 1, "dispatch_timings": []},
])
def test_context_enrichment_rejects_changed_facts_or_non_object_timings(incoming):
    from app.runs.domain.attempt_settlement_projection import merge_accepted_context
    with pytest.raises(AttemptSettlementProjectionConflict):
        merge_accepted_context({"identity": "a", "generation": 1}, incoming)


def test_empty_timings_are_an_observation_not_permission_to_replace():
    from app.runs.domain.attempt_settlement_projection import merge_accepted_context
    original = {"identity": "a", "dispatch_timings": {}}
    assert merge_accepted_context(original, {"identity": "a"}) == original
    with pytest.raises(AttemptSettlementProjectionConflict):
        merge_accepted_context(original, {"identity": "a", "dispatch_timings": {"ms": 1}})
