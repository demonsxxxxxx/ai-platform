"""Composition of PostgreSQL-backed worker supervision ports."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TypeVar

from app.db import transaction
from app.execution.application.supervision import (
    capacities_from_settings,
    observe_supervision_once,
    run_current_domain_work,
)
from app.execution.domain.supervision import SupervisionCandidate, SupervisionDomain


T = TypeVar("T")


async def observe_worker_supervision_candidates(
    settings: object,
) -> tuple[SupervisionCandidate, ...]:
    return await observe_supervision_once(
        transaction_factory=transaction,
        settings=settings,
    )


async def run_worker_domain_operation(
    operation: Callable[[], Awaitable[T]],
    *,
    settings: object,
    domain: SupervisionDomain,
    worker_id: str,
) -> T | None:
    return await run_current_domain_work(
        operation,
        transaction_factory=transaction,
        domain=domain,
        worker_id=worker_id,
        ttl_seconds=int(getattr(settings, "supervision_claim_ttl_seconds", 3600)),
        capacities=capacities_from_settings(settings),
    )
