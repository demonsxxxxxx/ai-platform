"""Compose the legacy executor-facing Sandbox runtime with its application port."""

from typing import Any

from app.bootstrap.sandbox_lease_ports import (
    build_sandbox_executor_callback_lease_port,
    build_sandbox_runtime_lease_port,
)
from app.runtime.sandbox.runtime import SandboxRuntime as _SandboxRuntime
from app.sandbox.api import SandboxDispatchIntentService
from app.sandbox.infrastructure import dispatch_postgres
from app.sandbox.application.attempt_settlement_shadow import (
    AttemptSettlementShadowDispatchRepository,
)
from app.bootstrap.attempt_settlement_projection import build_attempt_settlement_projection_service
from app.settings import get_settings


_shadowed_dispatch_repository = AttemptSettlementShadowDispatchRepository(
    repository=dispatch_postgres,
    settlement_projection=build_attempt_settlement_projection_service(),
    enabled=lambda: bool(get_settings().run_attempt_settlement_shadow_enabled),
)


class SandboxRuntime(_SandboxRuntime):
    """Bind the Sandbox lease persistence adapter at the executor boundary."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("lease_port", build_sandbox_runtime_lease_port())
        kwargs.setdefault("callback_lease_port", build_sandbox_executor_callback_lease_port())
        kwargs.setdefault("dispatch_intents", SandboxDispatchIntentService(_shadowed_dispatch_repository))
        super().__init__(*args, **kwargs)
