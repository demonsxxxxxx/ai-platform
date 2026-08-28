from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from app.mcp.domain.tool_references import build_mcp_tool_reference


@dataclass(frozen=True)
class LiveMcpTool:
    tool_id: str
    server_id: str
    public_tool_name: str
    label: str
    description: str
    write_capable: bool = True
    risk_level: str = "high"

    def public_payload(self) -> dict[str, Any]:
        return {
            "tool_id": self.tool_id,
            "label": self.label,
            "description": self.description,
            "category": "mcp",
            "server": self.server_id,
            "cached": False,
        }


@dataclass(frozen=True)
class LiveMcpServerResult:
    server_id: str
    tools: tuple[LiveMcpTool, ...]
    unavailable_reason: str | None = None


class LiveMcpCatalogService:
    """Discover user-effective Gateway tools without platform-side catalog state."""

    def __init__(
        self,
        *,
        target_resolver: Callable[[str, str], Awaitable[Any]],
        discovery: Any,
    ) -> None:
        self._target_resolver = target_resolver
        self._discovery = discovery

    async def list_server_tools(
        self,
        *,
        tenant_id: str,
        user_id: str,
        server_id: str,
        jwt: str,
    ) -> LiveMcpServerResult:
        del user_id  # JWT ownership is enforced by the caller's principal store.
        try:
            target = await self._target_resolver(tenant_id, server_id)
            definitions = await self._discovery.discover_definitions(
                target.endpoint,
                static_headers=target.static_headers,
                jwt_authorization=f"Bearer {jwt}",
            )
            tools = tuple(
                LiveMcpTool(
                    tool_id=build_mcp_tool_reference(server_id, definition["name"]),
                    server_id=server_id,
                    public_tool_name=definition["name"],
                    label=definition["name"],
                    description=definition["description"],
                    write_capable=not bool(
                        isinstance(definition.get("annotations"), dict)
                        and definition["annotations"].get("readOnlyHint") is True
                    ),
                    risk_level=(
                        "low"
                        if isinstance(definition.get("annotations"), dict)
                        and definition["annotations"].get("readOnlyHint") is True
                        else "high"
                    ),
                )
                for definition in definitions
            )
        except Exception:  # noqa: BLE001 - one Server must not fail multi-Server discovery.
            return LiveMcpServerResult(server_id, (), "discovery_failed")
        return LiveMcpServerResult(server_id, tools)
