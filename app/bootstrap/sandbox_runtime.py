"""Compose the legacy executor-facing Sandbox runtime with its application port."""

from typing import Any

from app.bootstrap.sandbox_lease_ports import build_sandbox_runtime_lease_port
from app.runtime.sandbox.runtime import SandboxRuntime as _SandboxRuntime
from app.sandbox.api import SandboxDispatchIntentService
from app.sandbox.infrastructure import dispatch_postgres
from app.sandbox.application.attempt_settlement import (
    AttemptSettlementDispatchRepository,
)
from app.bootstrap.attempt_settlement_projection import build_attempt_settlement_projection_service


_attempt_dispatch_repository = AttemptSettlementDispatchRepository(
    repository=dispatch_postgres,
    settlement_projection=build_attempt_settlement_projection_service(),
)


class SandboxRuntime(_SandboxRuntime):
    """Bind the Sandbox lease persistence adapter at the executor boundary."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("lease_port", build_sandbox_runtime_lease_port())
        kwargs.setdefault("dispatch_intents", SandboxDispatchIntentService(_attempt_dispatch_repository))
        super().__init__(*args, **kwargs)
