"""Composition of PostgreSQL-backed worker supervision ports."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TypeVar

from app.db import transaction
from app.execution.infrastructure import supervision_postgres
from app.execution.application.supervision import (
    capacities_from_settings,
    observe_supervision_once,
    run_current_domain_work,
    run_scheduled_domain_work,
)
from app.execution.domain.supervision import OwnerMode, SupervisionCandidate, SupervisionDomain


T = TypeVar("T")


async def observe_worker_supervision_candidates(
    settings: object,
) -> tuple[SupervisionCandidate, ...]:
    return await observe_supervision_once(
        transaction_factory=transaction,
        store=supervision_postgres,
        settings=settings,
    )


async def run_worker_domain_operation(
    operation: Callable[[], Awaitable[T]],
    *,
    settings: object,
    domain: SupervisionDomain,
    expected_mode: OwnerMode,
    worker_id: str,
) -> T | None:
    if not bool(getattr(settings, "execution_supervision_enabled", False)):
        return await operation()
    return await run_current_domain_work(
        operation,
        transaction_factory=transaction,
        store=supervision_postgres,
        domain=domain,
        expected_mode=expected_mode,
        worker_id=worker_id,
        ttl_seconds=int(getattr(settings, "supervision_claim_ttl_seconds", 3600)),
        capacities=capacities_from_settings(settings),
    )


async def run_worker_scheduled_domain_operation(
    operation: Callable[[], Awaitable[T]],
    *,
    settings: object,
    domain: SupervisionDomain,
    worker_id: str,
) -> T | None:
    """Run an existing action owner under the persisted legacy/unified domain."""
    return await run_scheduled_domain_work(
        operation,
        transaction_factory=transaction,
        store=supervision_postgres,
        domain=domain,
        worker_id=worker_id,
        ttl_seconds=int(getattr(settings, "supervision_claim_ttl_seconds", 3600)),
        capacities=capacities_from_settings(settings),
        supervision_enabled=bool(
            getattr(settings, "execution_supervision_enabled", False)
        ),
    )


def bind_worker_supervision_operation(
    operation: Callable[[], Awaitable[T]],
    *,
    settings: object,
    domain: SupervisionDomain,
    worker_id: str,
) -> Callable[[], Awaitable[T | None]]:
    async def scheduled() -> T | None:
        return await run_worker_scheduled_domain_operation(
            operation,
            settings=settings,
            domain=domain,
            worker_id=worker_id,
        )

    return scheduled
