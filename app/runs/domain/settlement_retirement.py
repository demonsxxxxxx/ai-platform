"""The bounded compatibility surface retired by the Attempt settlement owner."""

RUN_ATTEMPT_SETTLEMENT_RETIREMENT_SCHEMA_VERSION = "2026.10.25.11"
LEGACY_ATTEMPT_SETTLEMENT_COLUMNS = (
    "executor_terminal_json",
    "executor_reconciliation_context_json",
    "executor_reconciliation_intent_context_json",
    "executor_reconciliation_status",
    "executor_reconciliation_claim_token",
    "executor_reconciliation_claimed_at",
    "executor_reconciliation_retry_not_before",
    "executor_terminal_reconciliation_attempt_count",
)
LEGACY_ATTEMPT_SETTLEMENT_INDEXES = frozenset({
    "idx_sandbox_leases_executor_reconcile",
    "idx_sandbox_leases_executor_reconcile_due",
    "idx_sandbox_leases_executor_watch",
})
LEGACY_ATTEMPT_SETTLEMENT_CONSTRAINT = (
    "sandbox_leases", "chk_sandbox_leases_executor_reconciliation_status"
)
