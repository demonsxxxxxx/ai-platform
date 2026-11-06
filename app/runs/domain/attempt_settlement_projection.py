"""Runs-owned authoritative settlement values for one RunAttempt."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
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
    "intent_recovery_context_json",
    "accepted_context_json",
    "attempt_status_snapshot",
    "attempt_owner_generation_snapshot",
    "run_status_snapshot",
    "cancel_requested_at_snapshot",
    "settlement_phase",
    "settlement_claim_token",
    "settlement_claimed_at",
    "settled_claim_token",
    "next_retry_at",
    "settlement_attempt_count",
    "result_status",
    "result_json",
    "result_sha256",
    "settlement_error_code",
    "settled_at",
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
