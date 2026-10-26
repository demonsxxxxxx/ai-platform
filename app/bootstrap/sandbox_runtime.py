"""Compose the legacy executor-facing Sandbox runtime with its application port."""

from typing import Any

from app.bootstrap.sandbox_lease_ports import build_sandbox_runtime_lease_port
from app.runtime.sandbox.runtime import SandboxRuntime as _SandboxRuntime


class SandboxRuntime(_SandboxRuntime):
    """Bind the Sandbox lease persistence adapter at the executor boundary."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("lease_port", build_sandbox_runtime_lease_port())
        super().__init__(*args, **kwargs)
