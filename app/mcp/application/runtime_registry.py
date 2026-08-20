from __future__ import annotations

from typing import Any, Protocol

from app.mcp.domain.errors import McpRuntimeContextError


class McpRuntimeServices(Protocol):
    context_manager: Any
    principal_jwt_store: Any
    relay_auth_failure_limiter: Any

    def create_host_relay(self, *, context_manager: Any | None = None) -> Any: ...

    def seal_server_credentials(self, **kwargs: Any) -> str: ...

    def open_server_credentials(self, **kwargs: Any) -> tuple[str | None, dict[str, str]]: ...

    async def record_server_credential(self, conn: Any, **kwargs: Any) -> Any: ...

    async def bind_run_context(self, conn: Any, **kwargs: Any) -> None: ...

    async def get_run_context_id(self, conn: Any, **kwargs: Any) -> str | None: ...


_services: McpRuntimeServices | None = None


def configure_mcp_runtime_services(services: McpRuntimeServices) -> None:
    global _services
    _services = services


def mcp_runtime_services() -> McpRuntimeServices:
    if _services is None:
        raise McpRuntimeContextError("mcp_runtime_not_configured", status_code=503)
    return _services


class RuntimeContextManagerProxy:
    def __getattr__(self, name: str) -> Any:
        return getattr(mcp_runtime_services().context_manager, name)


class RelayAuthFailureLimiterProxy:
    def __getattr__(self, name: str) -> Any:
        return getattr(mcp_runtime_services().relay_auth_failure_limiter, name)
