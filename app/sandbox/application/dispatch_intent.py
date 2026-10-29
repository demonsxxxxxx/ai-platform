"""Attempt-scoped evidence for handing execution to a sandbox provider."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol


DispatchOutcome = Literal["intent", "accepted", "rejected", "unknown"]


class SandboxDispatchOutcomeUnknown(RuntimeError):
    """Provider dispatch may have started; keep its Run, Attempt and lease live."""

    def __init__(self) -> None:
        super().__init__("sandbox_dispatch_outcome_unknown")


@dataclass(frozen=True, slots=True)
class SandboxDispatchIdentity:
    tenant_id: str
    run_id: str
    attempt_id: str
    owner_generation: int
    lease_id: str
    provider: str
    provider_task_id: str


class SandboxDispatchIntentRepository(Protocol):
    async def persist_intent(
        self, conn: Any, *, identity: SandboxDispatchIdentity,
        reconciliation_context: dict[str, Any],
    ) -> tuple[dict[str, Any], bool]: ...

    async def record_outcome(
        self, conn: Any, *, identity: SandboxDispatchIdentity,
        outcome: DispatchOutcome,
    ) -> dict[str, Any] | None: ...


class SandboxDispatchIntentService:
    """Apply Sandbox dispatch observations without granting new execution authority."""

    def __init__(self, repository: SandboxDispatchIntentRepository) -> None:
        self._repository = repository

    async def persist_intent(
        self, conn: Any, *, identity: SandboxDispatchIdentity,
        reconciliation_context: dict[str, Any],
    ) -> tuple[dict[str, Any], bool]:
        if identity.owner_generation < 1 or not all((
            identity.tenant_id, identity.run_id, identity.attempt_id,
            identity.lease_id, identity.provider, identity.provider_task_id,
        )) or identity.provider_task_id != identity.attempt_id:
            raise ValueError("sandbox_dispatch_identity_invalid")
        return await self._repository.persist_intent(
            conn, identity=identity, reconciliation_context=reconciliation_context,
        )

    async def record_outcome(
        self, conn: Any, *, identity: SandboxDispatchIdentity,
        outcome: DispatchOutcome,
    ) -> dict[str, Any] | None:
        if outcome not in {"accepted", "rejected", "unknown"}:
            raise ValueError("sandbox_dispatch_outcome_invalid")
        return await self._repository.record_outcome(
            conn, identity=identity, outcome=outcome,
        )
