"""Pure mapping of legacy Sandbox reconciliation facts onto one RunAttempt."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
from typing import Any


ATTEMPT_SETTLEMENT_SCHEMA_VERSION = "ai-platform.run-attempt-settlement.v1"
ATTEMPT_SETTLEMENT_PROJECTION_FIELDS = (
    "schema_version",
    "tenant_id",
    "run_id",
    "attempt_id",
    "mapping_state",
    "source_lease_id",
    "receipt_identity",
    "receipt_json",
    "receipt_sha256",
    "receipt_owner_generation",
    "dispatch_state",
    "accepted_context_json",
    "attempt_status_snapshot",
    "attempt_owner_generation_snapshot",
    "run_status_snapshot",
    "cancel_requested_at_snapshot",
    "settlement_phase",
    "settlement_claim_token",
    "settlement_claimed_at",
    "next_retry_at",
    "settlement_attempt_count",
    "result_status",
    "result_json",
    "result_sha256",
    "settlement_error_code",
    "settled_at",
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


class AttemptSettlementProjectionError(ValueError):
    """A projection cannot be safely persisted without resolving source identity."""


class AttemptSettlementProjectionConflict(RuntimeError):
    """An immutable receipt or accepted context would be overwritten."""


@dataclass(frozen=True)
class CleanupObservation:
    """Sandbox-owned cleanup facts kept outside the Attempt settlement row."""

    lease_count: int = 0
    released_lease_count: int = 0
    cleanup_pending_count: int = 0
    cleanup_failed_count: int = 0
    cleanup_claim_count: int = 0
    released_pending_settlement_count: int = 0


@dataclass(frozen=True)
class AttemptSettlementProjection:
    """An internal value plus separately owned Sandbox cleanup observations."""

    values: Mapping[str, Any]
    cleanup: CleanupObservation

    def to_mapping(self) -> dict[str, Any]:
        return {field: self.values.get(field) for field in ATTEMPT_SETTLEMENT_PROJECTION_FIELDS}


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

    Receipts are the immutable terminal payload with the mutable root-level
    diagnostics field omitted. The source stores JSONB, so the digest covers
    canonical JSON rather than an unrecoverable original wire byte sequence.
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
        if settled and attempt.get("started_at") is None:
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
        and run_status in {"cancel_requested", "cancelled"}
    )
    terminal_generation = (
        settled
        and attempt_generation is not None
        and lease_generation is not None
        and attempt_generation == lease_generation + 1
        and attempt_status in {"succeeded", "failed"}
        and run.get("cancel_requested_at") is None
    )
    cancelled_terminal_generation = (
        settled
        and attempt_generation is not None
        and lease_generation is not None
        and attempt_generation == lease_generation + 2
        and attempt_status == "cancelled"
        and run_status in {"cancelled", "canceled"}
        and run.get("cancel_requested_at") is not None
    )
    if attempt_generation is None or lease_generation is None or not (
        attempt_generation == lease_generation
        or cancellation_generation
        or terminal_generation
        or cancelled_terminal_generation
    ):
        values["settlement_phase"] = "unknown"
        return AttemptSettlementProjection(values, _cleanup_observation(leases, settled=settled))

    context_value = lease.get("executor_reconciliation_context_json")
    context = _object(context_value) if context_value is not None else None
    receipt_value = lease.get("executor_terminal_json")
    receipt = _object(receipt_value) if receipt_value is not None else None
    if (context_value is not None and context is None) or (
        receipt_value is not None and receipt is None
    ):
        return AttemptSettlementProjection(values, _cleanup_observation(leases, settled=settled))

    reconciliation_status = str(lease.get("executor_reconciliation_status") or "")
    values["source_lease_id"] = str(lease["id"])
    if context is not None:
        values["dispatch_state"] = "accepted"
        values["accepted_context_json"] = context
    elif receipt is not None and reconciliation_status == "waiting_terminal":
        values["dispatch_state"] = "receipt_before_acceptance"

    if receipt is not None:
        immutable_receipt = {key: value for key, value in receipt.items() if key != "diagnostics"}
        if not immutable_receipt:
            return AttemptSettlementProjection(values, _cleanup_observation(leases, settled=settled))
        values.update(
            receipt_identity=f"sandbox_terminal:{lease['id']}:{attempt_id}",
            receipt_json=immutable_receipt,
            receipt_sha256=_canonical_digest(immutable_receipt),
            receipt_owner_generation=lease_generation,
        )

    receipt_attempt_count = lease.get("executor_terminal_reconciliation_attempt_count")
    if isinstance(receipt_attempt_count, bool) or not isinstance(receipt_attempt_count, int):
        receipt_attempt_count = 0
    if receipt_attempt_count < 0:
        return AttemptSettlementProjection(values, _cleanup_observation(leases, settled=settled))
    values["settlement_attempt_count"] = receipt_attempt_count

    phase_by_legacy = {
        "pending": "pending",
        "claimed": "claimed",
        "retry": "retry",
        "waiting_terminal": "awaiting_context" if receipt is not None else "awaiting_receipt",
    }
    if reconciliation_status in phase_by_legacy:
        phase = phase_by_legacy[reconciliation_status]
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

    mapped = values["settlement_phase"] != "unknown" and values["dispatch_state"] != "unknown"
    values["mapping_state"] = "mapped" if mapped else "unknown"
    cleanup = _cleanup_observation(leases, settled=values["settlement_phase"] == "settled")
    return AttemptSettlementProjection(values, cleanup)

