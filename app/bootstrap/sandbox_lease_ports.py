"""Bind Sandbox application persistence ports to their PostgreSQL adapter."""

from typing import cast

from app.sandbox.api import (
    SandboxExecutorCallbackLeasePort,
    SandboxExecutorReconciliationPort,
    SandboxLeaseTransportPort,
    SandboxRunLeaseReader,
    SandboxRuntimeLeasePort,
)
from app.sandbox.infrastructure import leases_postgres
from app.bootstrap.attempt_settlement_projection import (
    build_attempt_settlement_projection_service,
)
from app.settings import get_settings


class _SettlementShadowLeaseRepository:
    """Sandbox's legacy writers remain authoritative; Runs receives same-txn shadows."""

    def __init__(self) -> None:
        self._settlement = build_attempt_settlement_projection_service()

    def __getattr__(self, name: str):
        return getattr(leases_postgres, name)

    @staticmethod
    def _enabled() -> bool:
        return bool(get_settings().run_attempt_settlement_shadow_enabled)

    async def _lock_sources(self, conn, scope: dict[str, str]) -> None:
        await self._settlement.lock_sources_in_transaction(conn, **scope)

    async def _refresh(self, conn, scope: dict[str, str]) -> None:
        await self._settlement.refresh_in_transaction(conn, **scope)

    async def _scope_for_lease(self, conn, lease_id: str) -> dict[str, str] | None:
        row = await (
            await conn.execute(
                "select tenant_id, run_id, attempt_id from sandbox_leases where id = %s",
                (lease_id,),
            )
        ).fetchone()
        if row is None or not row.get("attempt_id"):
            return None
        return {key: str(row[key]) for key in ("tenant_id", "run_id", "attempt_id")}

    async def create_sandbox_lease(self, conn, **kwargs):
        row = await leases_postgres.create_sandbox_lease(conn, **kwargs)
        attempt_id = kwargs.get("attempt_id")
        if self._enabled() and attempt_id:
            await self._refresh(conn, {
                "tenant_id": str(kwargs["tenant_id"]),
                "run_id": str(kwargs["run_id"]),
                "attempt_id": str(attempt_id),
            })
        return row

    async def record_sandbox_executor_accepted(self, conn, **kwargs):
        scope = {key: str(kwargs[key]) for key in ("tenant_id", "run_id", "attempt_id")}
        if self._enabled():
            await self._lock_sources(conn, scope)
        row = await leases_postgres.record_sandbox_executor_accepted(conn, **kwargs)
        if self._enabled():
            await self._refresh(conn, scope)
        return row

    async def record_sandbox_executor_terminal(self, conn, **kwargs):
        scope = {key: str(kwargs[key]) for key in ("tenant_id", "run_id", "attempt_id")}
        if self._enabled():
            await self._lock_sources(conn, scope)
        row = await leases_postgres.record_sandbox_executor_terminal(conn, **kwargs)
        if self._enabled():
            await self._refresh(conn, scope)
        return row

    async def claim_sandbox_executor_reconciliations(self, conn, **kwargs):
        rows = await leases_postgres.claim_sandbox_executor_reconciliations(conn, **kwargs)
        if self._enabled():
            # The bounded claim SQL already owns these lease locks. Refresh only
            # reads source facts and writes the Runs detail; it never asks for
            # Run/Attempt locks after the lease locks were acquired.
            for row in rows:
                if row.get("attempt_id"):
                    await self._refresh(conn, {
                        "tenant_id": str(row["tenant_id"]),
                        "run_id": str(row["run_id"]),
                        "attempt_id": str(row["attempt_id"]),
                    })
        return rows

    async def claim_sandbox_executor_suspects(self, conn, **kwargs):
        rows = await leases_postgres.claim_sandbox_executor_suspects(conn, **kwargs)
        if self._enabled():
            for row in rows:
                if row.get("attempt_id"):
                    await self._refresh(conn, {
                        "tenant_id": str(row["tenant_id"]),
                        "run_id": str(row["run_id"]),
                        "attempt_id": str(row["attempt_id"]),
                    })
        return rows

    async def retry_sandbox_executor_reconciliation(self, conn, **kwargs):
        scope = await self._scope_for_lease(conn, str(kwargs["lease_id"])) if self._enabled() else None
        changed = await leases_postgres.retry_sandbox_executor_reconciliation(conn, **kwargs)
        # The claim consumer already owns the lease update. Refresh takes no
        # Run/lease/Attempt locks, so this path cannot reverse the backfill order.
        if changed and scope is not None:
            await self._refresh(conn, scope)
        return changed

    async def mark_sandbox_executor_reconciliation_cleanup_pending(self, conn, **kwargs):
        scope = await self._scope_for_lease(conn, str(kwargs["lease_id"])) if self._enabled() else None
        changed = await leases_postgres.mark_sandbox_executor_reconciliation_cleanup_pending(
            conn, **kwargs
        )
        if changed and scope is not None:
            await self._refresh(conn, scope)
        return changed

    async def finalize_failed_sandbox_executor_reconciliation_cleanup(self, conn, **kwargs):
        scope = await self._scope_for_lease(conn, str(kwargs["lease_id"])) if self._enabled() else None
        row = await leases_postgres.finalize_failed_sandbox_executor_reconciliation_cleanup(
            conn, **kwargs
        )
        if row is not None and scope is not None:
            await self._refresh(conn, scope)
        return row

    async def quarantine_sandbox_executor_reconciliation(self, conn, **kwargs):
        scope = await self._scope_for_lease(conn, str(kwargs["lease_id"])) if self._enabled() else None
        changed = await leases_postgres.quarantine_sandbox_executor_reconciliation(conn, **kwargs)
        if changed and scope is not None:
            await self._refresh(conn, scope)
        return changed


_shadowed_repository = _SettlementShadowLeaseRepository()


def build_sandbox_runtime_lease_port() -> SandboxRuntimeLeasePort:
    return cast(SandboxRuntimeLeasePort, _shadowed_repository)


def build_sandbox_executor_callback_lease_port() -> SandboxExecutorCallbackLeasePort:
    return cast(SandboxExecutorCallbackLeasePort, _shadowed_repository)


def build_sandbox_lease_transport_port() -> SandboxLeaseTransportPort:
    return cast(SandboxLeaseTransportPort, _shadowed_repository)


def build_sandbox_executor_reconciliation_port() -> SandboxExecutorReconciliationPort:
    return cast(SandboxExecutorReconciliationPort, _shadowed_repository)


def build_sandbox_run_lease_reader() -> SandboxRunLeaseReader:
    return cast(SandboxRunLeaseReader, leases_postgres)
