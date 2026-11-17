"""Application policy for shadow supervision and ownership handoff."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Protocol, TypeVar

from app.execution.domain.supervision import (
    SupervisionCandidate,
    SupervisionCapacities,
    SupervisionDomain,
    OwnerMode,
)


T = TypeVar("T")


class SupervisionStore(Protocol):
    """Persistence port used by supervision policy and ownership coordination."""

    async def observe_persisted_candidates(
        self,
        connection: Any,
        *,
        now: datetime,
        batch_limits: dict[SupervisionDomain, int],
        probe_stale_seconds: int,
        settlement_claim_stale_seconds: int,
    ) -> tuple[SupervisionCandidate, ...]: ...

    async def read_domain_control(
        self, connection: Any, *, domain: SupervisionDomain
    ) -> dict[str, Any] | None: ...

    async def claim_domain_work(
        self,
        connection: Any,
        *,
        domain: SupervisionDomain,
        owner_mode: OwnerMode,
        worker_id: str,
        ttl_seconds: int,
        capacity: int = 1,
    ) -> dict[str, Any] | None: ...

    async def claim_is_current(self, connection: Any, claim: dict[str, Any]) -> bool: ...

    async def release_domain_work(self, connection: Any, claim: dict[str, Any]) -> bool: ...


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
        # Probe observation gives this page budget independently to the
        # cancellation and ordinary lanes, so neither lane can hide the other.
        SupervisionDomain.PROBE: 1,
        SupervisionDomain.SETTLEMENT: 1,
        SupervisionDomain.CLEANUP: int(
            getattr(settings, "object_delete_batch_limit", 50)
        ),
    }


async def observe_supervision_once(
    *,
    transaction_factory: Any,
    store: SupervisionStore,
    settings: object,
    now: datetime | None = None,
) -> tuple[SupervisionCandidate, ...]:
    """Observe persisted facts and return typed decisions without executing them."""
    observed_at = now or datetime.now(timezone.utc)
    async with transaction_factory() as connection:
        candidates = await store.observe_persisted_candidates(
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
    # Observation reports the complete persisted scan page. Capacity is applied
    # only when an action owner attempts to claim work.
    return candidates


@asynccontextmanager
async def claimed_domain_work(
    *,
    transaction_factory: Any,
    store: SupervisionStore,
    domain: SupervisionDomain,
    owner_mode: OwnerMode,
    worker_id: str,
    ttl_seconds: int,
    capacity: int = 1,
) -> AsyncIterator[dict[str, Any] | None]:
    """Track a maintenance scan until it ends so activation can drain safely."""
    async with transaction_factory() as connection:
        claim = await store.claim_domain_work(
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
            current = await store.claim_is_current(connection, claim)
        if not current:
            yield None
            return
        yield claim
    finally:
        async with transaction_factory() as connection:
            await store.release_domain_work(connection, claim)


async def run_claimed_domain_work(
    operation: Callable[[], Awaitable[T]],
    *,
    transaction_factory: Any,
    store: SupervisionStore,
    domain: SupervisionDomain,
    owner_mode: OwnerMode,
    worker_id: str,
    ttl_seconds: int,
    capacity: int = 1,
) -> T | None:
    async with claimed_domain_work(
        transaction_factory=transaction_factory,
        store=store,
        domain=domain,
        owner_mode=owner_mode,
        worker_id=worker_id,
        ttl_seconds=ttl_seconds,
        capacity=capacity,
    ) as claim:
        if claim is None:
            return None
        task = asyncio.create_task(operation())
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            # The action owner can be awaiting remote I/O that continues after
            # its caller is cancelled. Keep the durable domain slot until that
            # action has actually finished, then let the claim context release.
            current = asyncio.current_task()
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    if current is not None:
                        current.uncancel()
                except BaseException:
                    break
            try:
                task.result()
            except BaseException:
                pass
            raise


async def run_scheduled_domain_work(
    operation: Callable[[], Awaitable[T]],
    *,
    transaction_factory: Any,
    store: SupervisionStore,
    domain: SupervisionDomain,
    worker_id: str,
    ttl_seconds: int,
    capacities: SupervisionCapacities | None = None,
    unified_execution_enabled: bool,
) -> T | None:
    """Require a PostgreSQL claim in every mode; the local switch gates unified work."""
    async with transaction_factory() as connection:
        control = await store.read_domain_control(connection, domain=domain)
    if control is None:
        raise RuntimeError("execution_supervision_domain_missing")
    mode = str(control.get("mode"))
    if mode == "draining":
        return None
    if mode not in {"legacy", "unified"}:
        raise RuntimeError("execution_supervision_owner_mode_invalid")
    if mode == "unified" and not unified_execution_enabled:
        return None
    return await run_claimed_domain_work(
        operation,
        transaction_factory=transaction_factory,
        store=store,
        domain=domain,
        owner_mode=mode,  # type: ignore[arg-type]
        worker_id=worker_id,
        ttl_seconds=ttl_seconds,
        capacity=capacities.for_domain(domain) if capacities is not None else 1,
    )


async def run_current_domain_work(
    operation: Callable[[], Awaitable[T]],
    *,
    transaction_factory: Any,
    store: SupervisionStore,
    domain: SupervisionDomain,
    expected_mode: OwnerMode,
    worker_id: str,
    ttl_seconds: int,
    capacities: SupervisionCapacities | None = None,
    unified_execution_enabled: bool,
) -> T | None:
    """Run through the persisted owner, requiring unified permission on this replica."""
    async with transaction_factory() as connection:
        control = await store.read_domain_control(connection, domain=domain)
    if control is None:
        raise RuntimeError("execution_supervision_domain_missing")
    mode = str(control.get("mode"))
    if mode == "draining" or mode != expected_mode:
        return None
    if mode not in {"legacy", "unified"}:
        raise RuntimeError("execution_supervision_owner_mode_invalid")
    if mode == "unified" and not unified_execution_enabled:
        return None
    return await run_claimed_domain_work(
        operation,
        transaction_factory=transaction_factory,
        store=store,
        domain=domain,
        owner_mode=mode,  # type: ignore[arg-type]
        worker_id=worker_id,
        ttl_seconds=ttl_seconds,
        capacity=capacities.for_domain(domain) if capacities is not None else 1,
    )
