"""Compose the legacy executor-facing Sandbox runtime with its application port."""

from typing import Any

from app.bootstrap.sandbox_lease_ports import build_sandbox_runtime_lease_port
from app.runtime.sandbox.runtime import SandboxRuntime as _SandboxRuntime
from app.sandbox.api import SandboxDispatchIntentService
from app.sandbox.infrastructure import dispatch_postgres
from app.bootstrap.attempt_settlement_projection import build_attempt_settlement_projection_service
from app.settings import get_settings


class _SettlementShadowDispatchRepository:
    def __init__(self) -> None:
        self._settlement = build_attempt_settlement_projection_service()

    async def persist_intent(self, conn, *, identity, reconciliation_context):
        if get_settings().run_attempt_settlement_shadow_enabled:
            await self._settlement.lock_sources_in_transaction(
                conn,
                tenant_id=identity.tenant_id,
                run_id=identity.run_id,
                attempt_id=identity.attempt_id,
            )
        result = await dispatch_postgres.persist_intent(
            conn, identity=identity, reconciliation_context=reconciliation_context,
        )
        if get_settings().run_attempt_settlement_shadow_enabled:
            await self._settlement.refresh_in_transaction(
                conn,
                tenant_id=identity.tenant_id,
                run_id=identity.run_id,
                attempt_id=identity.attempt_id,
            )
        return result

    async def record_outcome(self, conn, *, identity, outcome):
        return await dispatch_postgres.record_outcome(
            conn, identity=identity, outcome=outcome,
        )


_shadowed_dispatch_repository = _SettlementShadowDispatchRepository()


class SandboxRuntime(_SandboxRuntime):
    """Bind the Sandbox lease persistence adapter at the executor boundary."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("lease_port", build_sandbox_runtime_lease_port())
        kwargs.setdefault("dispatch_intents", SandboxDispatchIntentService(_shadowed_dispatch_repository))
        super().__init__(*args, **kwargs)
