"""Composition for the Runs-owned Attempt settlement projection API."""

from app.runs.api import (
    AttemptSettlementProjectionPorts,
    AttemptSettlementProjectionService,
)
from app.runs.infrastructure.attempt_settlement_projection_postgres import (
    backfill_attempt_settlement_projections,
    compare_attempt_settlement_projections,
    compare_attempt_settlement_snapshot,
    lock_attempt_settlement_projection_sources,
    lock_attempt_settlement_writer_fence,
    get_attempt_settlement_storage_version,
    list_due_attempt_settlements,
    next_attempt_settlement_retry_delay,
    persist_attempt_settlement_projection,
    read_attempt_settlement,
    refresh_attempt_settlement_projection,
)


def build_attempt_settlement_projection_service() -> AttemptSettlementProjectionService:
    """Build the bounded N31 backfill/compare capability for an owned PG txn."""

    return AttemptSettlementProjectionService(
        ports=AttemptSettlementProjectionPorts(
            backfill=backfill_attempt_settlement_projections,
            compare=compare_attempt_settlement_projections,
            persist=persist_attempt_settlement_projection,
            lock_sources=lock_attempt_settlement_projection_sources,
            refresh=refresh_attempt_settlement_projection,
            compare_snapshot=compare_attempt_settlement_snapshot,
            storage_version=get_attempt_settlement_storage_version,
            read_attempt=read_attempt_settlement,
            list_due=list_due_attempt_settlements,
            next_retry_delay=next_attempt_settlement_retry_delay,
            writer_fence=lock_attempt_settlement_writer_fence,
        )
    )

