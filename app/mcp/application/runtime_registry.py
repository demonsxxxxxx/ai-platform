from __future__ import annotations

from typing import Any, Protocol

from app.mcp.domain.errors import McpRuntimeContextError


class McpRuntimeServices(Protocol):
    live_catalog: Any
    principal_jwt_store: Any

    def seal_server_credentials(self, **kwargs: Any) -> str: ...

    def open_server_credentials(self, **kwargs: Any) -> tuple[str | None, dict[str, str]]: ...

    async def repository_call(self, operation: str, conn: Any, **kwargs: Any) -> Any: ...

    async def attach_server_configs(
        self,
        conn: Any,
        *,
        principal: Any,
        run_payload: Any,
    ) -> Any: ...


_services: McpRuntimeServices | None = None


def configure_mcp_runtime_services(services: McpRuntimeServices) -> None:
    global _services
    _services = services


def mcp_runtime_services() -> McpRuntimeServices:
    if _services is None:
        raise McpRuntimeContextError("mcp_runtime_not_configured", status_code=503)
    return _services


class LiveMcpCatalogProxy:
    def __getattr__(self, name: str) -> Any:
        return getattr(mcp_runtime_services().live_catalog, name)
