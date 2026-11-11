"""Resolve administrator defaults without turning selection into authorization."""

from collections.abc import Awaitable, Callable
from typing import Any

from app.mcp.domain.tool_references import parse_mcp_tool_reference


class McpDefaultTools:
    def __init__(
        self, *, list_servers: Callable[..., Awaitable[Any]],
        authorize_available: Callable[..., Awaitable[Any]],
        discover: Callable[..., Awaitable[Any]], is_admin: Callable[[Any], bool],
    ) -> None:
        self._list_servers = list_servers
        self._authorize_available = authorize_available
        self._discover = discover
        self._is_admin = is_admin

    async def resolve(self, conn: Any, *, principal: Any) -> list[str]:
        rows = await self._list_servers(
            conn, tenant_id=principal.tenant_id, include_disabled=False,
        )
        configured: list[str] = []
        for row in rows:
            server = str(row.get("name") or "")
            for tool_id in row.get("default_tool_ids") or []:
                # Persisted settings are not an alternate source of tool identity.
                if parse_mcp_tool_reference(tool_id)[0] != server:
                    raise ValueError("mcp_default_tool_server_mismatch")
                if tool_id not in configured:
                    configured.append(tool_id)
        if not configured:
            return []
        available = await self._authorize_available(
            conn, tenant_id=principal.tenant_id, tool_ids=configured,
            principal_department_id=principal.department_id,
            principal_department_ids=principal.department_ids,
            principal_roles=principal.roles, is_admin=self._is_admin(principal),
            permissions=principal.permissions,
        )
        authorized = {str(tool["tool_id"]) for tool in available}
        candidates = [tool_id for tool_id in configured if tool_id in authorized]
        if not candidates:
            return []
        live = await self._discover(principal=principal, tool_ids=candidates)
        return [tool_id for tool_id in candidates if tool_id in live]
