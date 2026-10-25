"""Application policy for shadow supervision and ownership handoff."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, AsyncIterator, TypeVar

from app.execution.domain.supervision import (
    SupervisionCandidate,
    SupervisionCapacities,
    SupervisionDomain,
    select_supervision_candidates,
)
from app.execution.infrastructure import supervision_postgres


T = TypeVar("T")


def capacities_from_settings(settings: object) -> SupervisionCapacities:
    """Use the currently validated owner budgets as independent domain caps."""
    return SupervisionCapacities(
        delivery=int(getattr(settings, "supervision_delivery_capacity", 8)),
        probe=int(getattr(settings, "supervision_probe_capacity", 1)),
        settlement=int(getattr(settings, "supervision_settlement_capacity", 2)),
        cleanup=int(getattr(settings, "supervision_cleanup_capacity", 2)),
    )


def batches_from_settings(settings: object) -> dict[SupervisionDomain, int]:
    return {
        SupervisionDomain.DELIVERY: int(
            getattr(settings, "run_delivery_intent_scan_batch_size", 25)
        ),
        SupervisionDomain.PROBE: 1,
        SupervisionDomain.SETTLEMENT: 1,
        SupervisionDomain.CLEANUP: int(
            getattr(settings, "object_delete_batch_limit", 50)
        ),
    }


async def observe_supervision_once(
    *,
    transaction_factory: Any,
    settings: object,
    now: datetime | None = None,
) -> tuple[SupervisionCandidate, ...]:
    """Observe persisted facts and return typed decisions without executing them."""
    observed_at = now or datetime.now(timezone.utc)
    async with transaction_factory() as connection:
        candidates = await supervision_postgres.observe_persisted_candidates(
            connection,
            now=observed_at,
            batch_limits=batches_from_settings(settings),
            probe_stale_seconds=int(
                getattr(settings, "executor_heartbeat_stale_seconds", 45)
            ),
            settlement_claim_stale_seconds=int(
                getattr(settings, "executor_reconciliation_claim_stale_seconds", 300)
            ),
        )
    return select_supervision_candidates(
        candidates,
        now=observed_at,
        capacities=capacities_from_settings(settings),
    )


@asynccontextmanager
async def claimed_domain_work(
    *,
    transaction_factory: Any,
    domain: SupervisionDomain,
    owner_mode: supervision_postgres.OwnerMode,
    worker_id: str,
    ttl_seconds: int,
    capacity: int = 1,
) -> AsyncIterator[dict[str, Any] | None]:
    """Track a maintenance scan until it ends so activation can drain safely."""
    async with transaction_factory() as connection:
        claim = await supervision_postgres.claim_domain_work(
            connection,
            domain=domain,
            owner_mode=owner_mode,
            worker_id=worker_id,
            ttl_seconds=ttl_seconds,
            capacity=capacity,
        )
    if claim is None:
        yield None
        return
    try:
        async with transaction_factory() as connection:
            current = await supervision_postgres.claim_is_current(connection, claim)
        if not current:
            yield None
            return
        yield claim
    finally:
        async with transaction_factory() as connection:
            await supervision_postgres.release_domain_work(connection, claim)


async def run_claimed_domain_work(
    operation: Callable[[], Awaitable[T]],
    *,
    transaction_factory: Any,
    domain: SupervisionDomain,
    owner_mode: supervision_postgres.OwnerMode,
    worker_id: str,
    ttl_seconds: int,
    capacity: int = 1,
) -> T | None:
    async with claimed_domain_work(
        transaction_factory=transaction_factory,
        domain=domain,
        owner_mode=owner_mode,
        worker_id=worker_id,
        ttl_seconds=ttl_seconds,
        capacity=capacity,
    ) as claim:
        if claim is None:
            return None
        return await operation()


async def run_current_domain_work(
    operation: Callable[[], Awaitable[T]],
    *,
    transaction_factory: Any,
    domain: SupervisionDomain,
    worker_id: str,
    ttl_seconds: int,
    capacities: SupervisionCapacities | None = None,
) -> T | None:
    """Run through the persisted current owner, stopping new claims while draining."""
    async with transaction_factory() as connection:
        control = await supervision_postgres.read_domain_control(connection, domain=domain)
    if control is None:
        raise RuntimeError("execution_supervision_domain_missing")
    mode = str(control.get("mode"))
    if mode == "draining":
        return None
    if mode not in {"legacy", "unified"}:
        raise RuntimeError("execution_supervision_owner_mode_invalid")
    return await run_claimed_domain_work(
        operation,
        transaction_factory=transaction_factory,
        domain=domain,
        owner_mode=mode,  # type: ignore[arg-type]
        worker_id=worker_id,
        ttl_seconds=ttl_seconds,
        capacity=capacities.for_domain(domain) if capacities is not None else 1,
    )
