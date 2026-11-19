"""Pure source-data mapping used only by the one-time settlement upgrade."""
from __future__ import annotations
from collections.abc import Mapping, Sequence
import hashlib
import json
from typing import Any
from app.runs.domain.attempt_settlement_projection import (
    ATTEMPT_SETTLEMENT_SCHEMA_VERSION, AttemptSettlementProjection, CleanupObservation,
)

_ATTEMPT_TERMINAL_TO_RUN = {
    "succeeded": frozenset({"succeeded"}),
    "failed": frozenset({"failed", "dead-letter", "dead_letter", "dead-lettered"}),
    "cancelled": frozenset({"cancelled", "canceled"}),
}


_TERMINAL_ATTEMPTS = frozenset(_ATTEMPT_TERMINAL_TO_RUN)


_SETTLEMENT_PHASES = frozenset(
    {
        "awaiting_receipt",
        "awaiting_context",
        "pending",
        "claimed",
        "retry",
        "settled",
        "failed",
        "unknown",
    }
)


def _canonical_digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _object(value: object) -> dict[str, Any] | None:
    return dict(value) if isinstance(value, Mapping) else None


def _timestamp(value: object) -> object | None:
    return value if value is not None else None


def _positive_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value > 0:
        return value
    if isinstance(value, str) and value.isdigit() and int(value) > 0:
        return int(value)
    return None


def _terminal_pair_is_consistent(attempt_status: str, run_status: str) -> bool:
    return run_status in _ATTEMPT_TERMINAL_TO_RUN.get(attempt_status, frozenset())


def _cleanup_observation(leases: Sequence[Mapping[str, Any]], *, settled: bool) -> CleanupObservation:
    released = 0
    cleanup_pending = 0
    cleanup_failed = 0
    cleanup_claims = 0
    released_pending = 0
    for lease in leases:
        lease_status = str(lease.get("status") or "")
        reconciliation_status = str(lease.get("executor_reconciliation_status") or "")
        released += lease_status == "released"
        cleanup_pending += reconciliation_status == "cleanup_pending"
        cleanup_failed += reconciliation_status == "failed"
        if (
            reconciliation_status in {"failed", "cleanup_pending"}
            and lease.get("executor_reconciliation_claim_token")
        ):
            cleanup_claims += 1
        if lease_status == "released" and not settled:
            released_pending += 1
    return CleanupObservation(
        lease_count=len(leases),
        released_lease_count=released,
        cleanup_pending_count=cleanup_pending,
        cleanup_failed_count=cleanup_failed,
        cleanup_claim_count=cleanup_claims,
        released_pending_settlement_count=released_pending,
    )


def _historical_terminal_values(
    attempt: Mapping[str, Any], run: Mapping[str, Any], lease: Mapping[str, Any],
    values: dict[str, Any],
) -> dict[str, Any] | None:
    """Archive proven old terminal facts without inventing executor authority.

    Pre-generation runtime projections omitted owner_generation entirely. Old
    cancellation released the producer without synthesizing a terminal callback.
    Neither observation can authorize runtime claims or continuation after upgrade.
    """
    payload = _object(lease.get("lease_payload_json"))
    context = _object(lease.get("executor_reconciliation_context_json"))
    labels = _object(payload.get("labels")) if payload is not None else None
    result = _object(run.get("result_json"))
    finished_at = run.get("finished_at") or attempt.get("finished_at")
    if (
        payload is None or labels is None or context is None or result is None or finished_at is None
        or not _terminal_pair_is_consistent(str(attempt.get("status")), str(run.get("status")))
        or _positive_int(attempt.get("owner_generation")) is None
        or lease.get("status") != "released" or lease.get("released_at") is None
        or payload.get("source") != "sandbox_runtime"
        or payload.get("evidence_class") != "runtime_lease_projection"
        or payload.get("attempt_id") != attempt.get("id")
        or any(labels.get(f"ai-platform.{key}") != value for key, value in (
            ("tenant_id", attempt.get("tenant_id")), ("run_id", attempt.get("run_id")),
            ("attempt_id", attempt.get("id")),
        ))
        or any(lease.get(field) is not None for field in (
            "executor_reconciliation_claim_token", "executor_reconciliation_claimed_at",
            "executor_probe_claim_token", "executor_probe_claimed_at",
            "executor_cleanup_claim_token", "executor_cleanup_claimed_at",
        ))
        or str(lease.get("dispatch_status") or "") not in {"", "accepted"}
    ):
        return None
    generation = _positive_int(payload.get("owner_generation"))
    if generation is not None and generation > int(attempt["owner_generation"]):
        return None
    if "owner_generation" in payload and generation is None:
        return None  # A malformed supplied generation is not an older layout.
    receipt = _object(lease.get("executor_terminal_json"))
    receiptless_cancel = (
        lease.get("executor_terminal_json") is None
        and lease.get("executor_terminal_sql_null", True)
        and lease.get("executor_terminal_received_at") is None
        and attempt.get("status") == "cancelled" and run.get("cancel_requested_at") is not None
        and lease.get("release_reason") in {"cancel_requested", "admin_cancel_requested"}
        and lease.get("executor_status") in {"accepted", "running"}
        and lease.get("executor_reconciliation_status") == "waiting_terminal"
    )
    old_finalized_receipt = (
        "owner_generation" not in payload and receipt is not None
        and bool({key: value for key, value in receipt.items() if key != "diagnostics"})
        and receipt.get("run_id") == attempt.get("run_id")
        and lease.get("executor_status") in {"completed", "failed", "cancelled"}
        and lease.get("executor_reconciliation_status") == "finalized"
        and lease.get("executor_terminal_received_at") is not None
        and lease.get("executor_reconciled_at") is not None
    )
    if not (receiptless_cancel or old_finalized_receipt):
        return None
    count = lease.get("executor_terminal_reconciliation_attempt_count")
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        return None
    intent_value = lease.get("executor_reconciliation_intent_context_json")
    intent = _object(intent_value) if intent_value is not None else {
        key: value for key, value in context.items() if key != "dispatch_timings"
    }
    if intent is None:
        return None
    archived = dict(values)
    archived.update(
        mapping_state="historical", source_lease_id=str(lease["id"]),
        dispatch_state="accepted", intent_recovery_context_json=intent,
        accepted_context_json=context, settlement_phase="settled", settlement_attempt_count=count,
        result_status=run["status"], result_json=result, result_sha256=_canonical_digest(result),
        settlement_error_code=(run.get("error_code") or attempt.get("error_code")), settled_at=finished_at,
    )
    if receipt is not None:
        archived.update(
            receipt_identity=f"sandbox_terminal:{lease['id']}:{attempt['id']}", receipt_json=receipt,
            receipt_sha256=_canonical_digest({key: value for key, value in receipt.items() if key != "diagnostics"}),
            receipt_owner_generation=None,
        )
    return archived


def _base_values(attempt: Mapping[str, Any], run: Mapping[str, Any]) -> dict[str, Any]:
    attempt_status = str(attempt.get("status") or "")
    attempt_generation = _positive_int(attempt.get("owner_generation"))
    run_status = str(run.get("status") or "")
    return {
        "schema_version": ATTEMPT_SETTLEMENT_SCHEMA_VERSION,
        "tenant_id": str(attempt.get("tenant_id") or ""),
        "run_id": str(attempt.get("run_id") or ""),
        "attempt_id": str(attempt.get("id") or ""),
        "mapping_state": "unknown",
        "source_lease_id": None,
        "receipt_identity": None,
        "receipt_json": None,
        "receipt_sha256": None,
        "receipt_owner_generation": None,
        "dispatch_state": "unknown",
        "intent_recovery_context_json": None,
        "accepted_context_json": None,
        "attempt_status_snapshot": attempt_status,
        "attempt_owner_generation_snapshot": attempt_generation,
        "run_status_snapshot": run_status,
        "cancel_requested_at_snapshot": _timestamp(run.get("cancel_requested_at")),
        "settlement_phase": "unknown",
        "settlement_claim_token": None,
        "settlement_claimed_at": None,
        "next_retry_at": None,
        "settlement_attempt_count": 0,
        "result_status": None,
        "result_json": None,
        "result_sha256": None,
        "settlement_error_code": None,
        "settled_at": None,
    }


def project_legacy_attempt_settlement(
    attempt: Mapping[str, Any],
    run: Mapping[str, Any],
    leases: Sequence[Mapping[str, Any]],
) -> AttemptSettlementProjection:
    """Map current legacy records without granting execution or cleanup authority.

    Keep the complete historical receipt, including mutable diagnostics. Its
    identity digest excludes root diagnostics and covers canonical JSON, since
    the original JSONB source does not retain the original wire bytes.
    """

    values = _base_values(attempt, run)
    attempt_id = str(attempt.get("id") or "")
    run_id = str(attempt.get("run_id") or "")
    tenant_id = str(attempt.get("tenant_id") or "")
    if (
        not attempt_id
        or not run_id
        or not tenant_id
        or str(run.get("id") or "") != run_id
        or str(run.get("tenant_id") or "") != tenant_id
    ):
        values["mapping_state"] = "conflict"
        return AttemptSettlementProjection(values, _cleanup_observation(leases, settled=False))

    if len(leases) > 1:
        values["mapping_state"] = "conflict"
        return AttemptSettlementProjection(values, _cleanup_observation(leases, settled=False))

    attempt_status = str(attempt.get("status") or "")
    run_status = str(run.get("status") or "")
    settled = attempt_status in _TERMINAL_ATTEMPTS and _terminal_pair_is_consistent(
        attempt_status, run_status
    )
    if not leases:
        if settled:
            result = _object(run.get("result_json"))
            finished_at = run.get("finished_at") or attempt.get("finished_at")
            if result is None or finished_at is None:
                return AttemptSettlementProjection(
                    values, _cleanup_observation(leases, settled=False)
                )
            values.update(
                mapping_state="mapped",
                settlement_phase="settled",
                result_status=run_status,
                result_json=result,
                result_sha256=_canonical_digest(result),
                settlement_error_code=(run.get("error_code") or attempt.get("error_code")),
                settled_at=finished_at,
            )
        else:
            values["settlement_phase"] = "awaiting_receipt"
        return AttemptSettlementProjection(values, _cleanup_observation(leases, settled=settled))

    lease = leases[0]
    if (
        str(lease.get("tenant_id") or "") != tenant_id
        or str(lease.get("run_id") or "") != run_id
        or str(lease.get("attempt_id") or "") != attempt_id
        or not str(lease.get("id") or "")
    ):
        values["mapping_state"] = "conflict"
        return AttemptSettlementProjection(values, _cleanup_observation(leases, settled=settled))

    if any(lease.get(field) is None and lease.get(flag, True) is False for field, flag in (
        ("executor_terminal_json", "executor_terminal_sql_null"),
        ("executor_reconciliation_context_json", "executor_context_sql_null"),
        ("executor_reconciliation_intent_context_json", "executor_intent_sql_null"),
    )):
        return AttemptSettlementProjection(values, _cleanup_observation(leases, settled=False))
    historical = _historical_terminal_values(attempt, run, lease, values)
    if historical is not None:
        return AttemptSettlementProjection(historical, _cleanup_observation(leases, settled=True))

    attempt_generation = _positive_int(attempt.get("owner_generation"))
    lease_payload = _object(lease.get("lease_payload_json"))
    lease_generation = _positive_int(
        lease_payload.get("owner_generation") if lease_payload is not None else None
    )
    cancellation_generation = (
        attempt_generation is not None
        and lease_generation is not None
        and attempt_generation == lease_generation + 1
        and run.get("cancel_requested_at") is not None
        and attempt_status in {"cancel_requested", "cancelled"}
        and (
            run_status in {"cancel_requested", "cancelled"}
            or (run_status == "running" and attempt_status == "cancel_requested")
        )
    )
    reconciler_expired_generation = (
        attempt_status == "expired"
        and attempt_generation is not None
        and lease_generation is not None
        and attempt_generation == lease_generation + 1
        and attempt.get("owner_kind") == "reconciler"
        and bool(attempt.get("owner_id"))
    )
    terminal_generation = (
        settled
        and attempt_generation is not None
        and lease_generation is not None
        and attempt_generation == lease_generation + 1
        and attempt_status in {"succeeded", "failed"}
        and run.get("cancel_requested_at") is None
    )
    reconciler_terminal_generation = (
        settled
        and attempt_generation is not None
        and lease_generation is not None
        and attempt_generation == lease_generation + 2
        and attempt_status == "failed"
        and run_status == "failed"
        and attempt.get("owner_kind") == "reconciler"
        and bool(attempt.get("owner_id"))
        and attempt.get("terminal_reason") == "run_failed"
        and attempt.get("error_code") == "stale_run_interrupted"
        and run.get("cancel_requested_at") is None
    )
    cancelled_terminal_generation = (
        settled
        and attempt_generation is not None
        and lease_generation is not None
        and attempt_generation == lease_generation + 2
        and attempt_status in {"cancelled", "succeeded"}
        and run_status in {"cancelled", "canceled", "succeeded"}
        and run.get("cancel_requested_at") is not None
    )
    if attempt_generation is None or lease_generation is None or not (
        attempt_generation == lease_generation
        or cancellation_generation
        or reconciler_expired_generation
        or terminal_generation
        or reconciler_terminal_generation
        or cancelled_terminal_generation
    ):
        values["settlement_phase"] = "unknown"
        return AttemptSettlementProjection(values, _cleanup_observation(leases, settled=settled))

    context_value = lease.get("executor_reconciliation_context_json")
    context = _object(context_value) if context_value is not None else None
    intent_context_value = lease.get("executor_reconciliation_intent_context_json")
    intent_context = _object(intent_context_value) if intent_context_value is not None else None
    receipt_value = lease.get("executor_terminal_json")
    receipt = _object(receipt_value) if receipt_value is not None else None
    if (context_value is not None and context is None) or (
        intent_context_value is not None and intent_context is None
    ) or (
        receipt_value is not None and receipt is None
    ):
        return AttemptSettlementProjection(values, _cleanup_observation(leases, settled=settled))

    reconciliation_status = str(lease.get("executor_reconciliation_status") or "")
    values["source_lease_id"] = str(lease["id"])
    if intent_context is None and context is not None:
        # Compatibility for leases created before the dedicated intent field.
        intent_context = {key: value for key, value in context.items() if key != "dispatch_timings"}
    values["intent_recovery_context_json"] = intent_context

    dispatch_status = str(lease.get("dispatch_status") or "")
    dispatch_accepted = dispatch_status == "accepted" or (
        not dispatch_status
        and context is not None
        and str(lease.get("executor_status") or "")
        in {"accepted", "running", "completed", "failed", "cancelled"}
    )
    if dispatch_accepted:
        values["dispatch_state"] = "accepted"
        if str(lease.get("executor_status") or "") in {
            "accepted", "running", "completed", "failed", "cancelled",
        }:
            values["accepted_context_json"] = context
    elif receipt is not None and reconciliation_status == "waiting_terminal":
        values["dispatch_state"] = "receipt_before_acceptance"

    cancelled_before_dispatch = (
        settled and attempt_status == "cancelled"
        and run.get("cancel_requested_at") is not None
        and lease.get("status") == "released" and lease.get("released_at") is not None
        and lease.get("release_reason") == "run_cancelled"
        and lease.get("executor_status") == "pending" and not dispatch_status
        and reconciliation_status == "waiting_terminal"
        and receipt is None and context is None and intent_context is None
        and lease.get("executor_reconciliation_claim_token") is None
        and lease.get("executor_reconciliation_claimed_at") is None
    )
    if receipt is not None:
        immutable_receipt = {key: value for key, value in receipt.items() if key != "diagnostics"}
        if not immutable_receipt:
            return AttemptSettlementProjection(values, _cleanup_observation(leases, settled=settled))
        values.update(
            receipt_identity=f"sandbox_terminal:{lease['id']}:{attempt_id}",
            receipt_json=receipt,
            receipt_sha256=_canonical_digest(immutable_receipt),
            receipt_owner_generation=lease_generation,
        )

    receipt_attempt_count = lease.get("executor_terminal_reconciliation_attempt_count")
    if isinstance(receipt_attempt_count, bool) or not isinstance(receipt_attempt_count, int):
        receipt_attempt_count = 0
    if receipt_attempt_count < 0:
        return AttemptSettlementProjection(values, _cleanup_observation(leases, settled=settled))
    values["settlement_attempt_count"] = receipt_attempt_count
    if cancelled_before_dispatch:
        result = _object(run.get("result_json"))
        finished_at = run.get("finished_at") or attempt.get("finished_at")
        if result is not None and finished_at is not None:
            # Preserve a committed cancellation, not proof that the executor
            # accepted input. Dispatch stays unknown and no receipt is invented.
            values.update(
                mapping_state="mapped", settlement_phase="settled",
                result_status=run_status, result_json=result,
                result_sha256=_canonical_digest(result),
                settlement_error_code=(run.get("error_code") or attempt.get("error_code")),
                settled_at=finished_at,
            )
            return AttemptSettlementProjection(values, _cleanup_observation(leases, settled=True))

    phase_by_legacy = {
        "pending": "pending",
        "claimed": "claimed",
        "retry": "retry",
        "waiting_terminal": "awaiting_context" if receipt is not None else "awaiting_receipt",
    }
    if reconciliation_status in phase_by_legacy:
        phase = phase_by_legacy[reconciliation_status]
        # A claim with no terminal receipt is a temporary executor probe, not
        # an Attempt settlement claim. Keep its owner token in Sandbox only.
        if reconciliation_status == "claimed" and receipt is None:
            phase = "awaiting_receipt" if dispatch_accepted else "unknown"
        if phase in {"pending", "claimed", "retry"} and (receipt is None or context is None):
            phase = "unknown"
        if phase == "claimed":
            claim_token = lease.get("executor_reconciliation_claim_token")
            claimed_at = lease.get("executor_reconciliation_claimed_at")
            if not claim_token or claimed_at is None:
                phase = "unknown"
            else:
                values["settlement_claim_token"] = str(claim_token)
                values["settlement_claimed_at"] = claimed_at
        if phase == "retry":
            retry_at = lease.get("executor_reconciliation_retry_not_before")
            if retry_at is None:
                phase = "unknown"
            else:
                values["next_retry_at"] = retry_at
    elif reconciliation_status in {"cleanup_pending", "failed", "finalized"}:
        phase = "settled" if settled and receipt is not None else "unknown"
    else:
        phase = "unknown"

    values["settlement_phase"] = phase if phase in _SETTLEMENT_PHASES else "unknown"
    if values["settlement_phase"] == "settled":
        result = _object(run.get("result_json"))
        finished_at = run.get("finished_at") or attempt.get("finished_at")
        if not settled or result is None or finished_at is None:
            values["settlement_phase"] = "unknown"
        else:
            values.update(
                result_status=run_status,
                result_json=result,
                result_sha256=_canonical_digest(result),
                settlement_error_code=(run.get("error_code") or attempt.get("error_code")),
                settled_at=finished_at,
            )

    mapped = (
        values["settlement_phase"] != "unknown"
        and values["dispatch_state"] != "unknown"
        and (
            values["dispatch_state"] != "accepted"
            or values["accepted_context_json"] is not None
        )
    )
    values["mapping_state"] = "mapped" if mapped else "unknown"
    cleanup = _cleanup_observation(leases, settled=values["settlement_phase"] == "settled")
    return AttemptSettlementProjection(values, cleanup)
