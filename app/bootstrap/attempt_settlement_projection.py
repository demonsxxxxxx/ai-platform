"""Composition for the Runs-owned Attempt settlement projection API."""

from app.runs.api import (
    AttemptSettlementProjectionPorts,
    AttemptSettlementProjectionService,
)
from app.runs.infrastructure.attempt_settlement_projection_postgres import (
    lock_attempt_settlement_projection_sources,
    list_due_attempt_settlements,
    next_attempt_settlement_retry_delay,
    read_attempt_settlement,
)
from app.runs.infrastructure.attempt_settlement_writer_postgres import (
    append_attempt_settlement_diagnostics,
    claim_attempt_checkpoint_repair,
    claim_due_attempt_settlements,
    get_attempt_settlement_reconciliation_summary,
    initialize_attempt_settlement_detail,
    is_attempt_settlement_claim_current,
    record_attempt_settlement_accepted_context,
    record_attempt_settlement_dispatch_outcome,
    record_attempt_settlement_intent,
    record_attempt_settlement_terminal_receipt,
    refresh_attempt_settlement_snapshot,
    retry_attempt_settlement_claim,
)


def build_attempt_settlement_projection_service() -> AttemptSettlementProjectionService:
    """Bind the sole Runs settlement store to the caller transaction."""

    return AttemptSettlementProjectionService(
        ports=AttemptSettlementProjectionPorts(
            lock_sources=lock_attempt_settlement_projection_sources,
            read_attempt=read_attempt_settlement,
            list_due=list_due_attempt_settlements,
            next_retry_delay=next_attempt_settlement_retry_delay,
            initialize_attempt=initialize_attempt_settlement_detail,
            refresh_snapshot=refresh_attempt_settlement_snapshot,
            record_intent=record_attempt_settlement_intent,
            record_accepted=record_attempt_settlement_accepted_context,
            record_terminal=record_attempt_settlement_terminal_receipt,
            record_dispatch_outcome=record_attempt_settlement_dispatch_outcome,
            claim_due=claim_due_attempt_settlements,
            claim_current=is_attempt_settlement_claim_current,
            retry_claim=retry_attempt_settlement_claim,
            append_diagnostics=append_attempt_settlement_diagnostics,
            summary=get_attempt_settlement_reconciliation_summary,
            claim_checkpoint_repair=claim_attempt_checkpoint_repair,
        )
    )

