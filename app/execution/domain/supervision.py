"""Typed, read-only supervision decisions over persisted execution facts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Iterable, Literal


OwnerMode = Literal["legacy", "unified"]


class SupervisionDomain(StrEnum):
    DELIVERY = "delivery"
    PROBE = "probe"
    SETTLEMENT = "settlement"
    CLEANUP = "cleanup"


@dataclass(frozen=True, slots=True)
class SupervisionCandidate:
    domain: SupervisionDomain
    candidate_id: str
    action: str
    deadline_at: datetime
    priority: int = 100
    facts: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if not self.candidate_id or not self.action:
            raise ValueError("supervision_candidate_identity_required")
        if self.deadline_at.tzinfo is None:
            raise ValueError("supervision_deadline_must_be_timezone_aware")


@dataclass(frozen=True, slots=True)
class SupervisionCapacities:
    delivery: int
    probe: int
    settlement: int
    cleanup: int

    def __post_init__(self) -> None:
        if min(self.delivery, self.probe, self.settlement, self.cleanup) < 1:
            raise ValueError("supervision_capacity_must_be_positive")

    def for_domain(self, domain: SupervisionDomain) -> int:
        return int(getattr(self, domain.value))


def select_supervision_candidates(
    candidates: Iterable[SupervisionCandidate],
    *,
    now: datetime,
    capacities: SupervisionCapacities,
) -> tuple[SupervisionCandidate, ...]:
    """Select due observations using independent per-domain capacity budgets."""
    if now.tzinfo is None:
        raise ValueError("supervision_now_must_be_timezone_aware")
    due_by_domain: dict[SupervisionDomain, list[SupervisionCandidate]] = {
        domain: [] for domain in SupervisionDomain
    }
    for candidate in candidates:
        if candidate.deadline_at <= now:
            due_by_domain[candidate.domain].append(candidate)

    selected: list[SupervisionCandidate] = []
    for domain in SupervisionDomain:
        ordered = sorted(
            due_by_domain[domain],
            key=lambda item: (item.priority, item.deadline_at, item.candidate_id),
        )
        selected.extend(ordered[: capacities.for_domain(domain)])
    return tuple(selected)
