"""Compose MCP application ports with concrete platform adapters."""

from __future__ import annotations

from typing import Any

from app.db import transaction
from app.mcp.api import configure_mcp_runtime_services
from app.mcp.infrastructure import postgres as mcp_postgres
from app.mcp.infrastructure import runtime as mcp_runtime
from app.redis_client import get_redis_client
from app.settings import get_settings


class _McpRuntimeServices:
    def __init__(self) -> None:
        target_reader = mcp_postgres.PostgresMcpRelayTargetReader(transaction)
        mcp_runtime.configure_runtime_dependencies(
            settings_provider=get_settings,
            redis_provider=get_redis_client,
            relay_target_reader=target_reader,
        )
        self.context_manager = mcp_runtime.get_mcp_runtime_context_manager()
        self.principal_jwt_store = mcp_runtime.get_mcp_principal_jwt_store()
        self.relay_auth_failure_limiter = mcp_runtime.McpRelayAuthFailureLimiter()

    def create_host_relay(self, *, context_manager: Any | None = None) -> Any:
        return mcp_runtime.HostMcpRelay(
            context_manager=context_manager or self.context_manager,
        )

    def seal_server_credentials(self, **kwargs: Any) -> str:
        return mcp_runtime.seal_mcp_server_credentials(**kwargs)

    def open_server_credentials(
        self,
        **kwargs: Any,
    ) -> tuple[str | None, dict[str, str]]:
        return mcp_runtime.open_mcp_server_credentials(**kwargs)

    async def record_server_credential(self, conn: Any, **kwargs: Any) -> None:
        await mcp_postgres.record_mcp_server_credential(conn, **kwargs)

    async def bind_run_context(self, conn: Any, **kwargs: Any) -> None:
        await mcp_postgres.bind_run_mcp_context(conn, **kwargs)

    async def get_run_context_id(self, conn: Any, **kwargs: Any) -> str | None:
        return await mcp_postgres.get_run_mcp_context_id(conn, **kwargs)


def configure_mcp_runtime() -> None:
    configure_mcp_runtime_services(_McpRuntimeServices())
