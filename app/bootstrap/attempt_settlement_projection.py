"""Composition for the Runs-owned Attempt settlement projection API."""

from app.runs.api import (
    AttemptSettlementProjectionPorts,
    AttemptSettlementProjectionService,
)
from app.runs.infrastructure.attempt_settlement_projection_postgres import (
    backfill_attempt_settlement_projections,
    compare_attempt_settlement_projections,
    persist_attempt_settlement_projection,
)


def build_attempt_settlement_projection_service() -> AttemptSettlementProjectionService:
    """Build the bounded N31 backfill/compare capability for an owned PG txn."""

    return AttemptSettlementProjectionService(
        ports=AttemptSettlementProjectionPorts(
            backfill=backfill_attempt_settlement_projections,
            compare=compare_attempt_settlement_projections,
            persist=persist_attempt_settlement_projection,
        )
    )

