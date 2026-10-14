from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from app.mcp.domain.errors import McpRuntimeContextError, McpToolDiscoveryError
from app.mcp.domain.tool_references import build_mcp_tool_reference


# Only locally owned codes may cross the catalog's private diagnostic boundary.
_DISCOVERY_FAILURE_REASONS = frozenset({
    "authorization_required", "invalid_endpoint", "protocol_error",
    "response_too_large", "unsupported_content_encoding", "transport_failure",
    "connection_failed", "page_limit_exceeded", "unsupported_transport",
})
_RUNTIME_FAILURE_REASONS = frozenset({
    "mcp_runtime_not_configured", "mcp_server_not_available",
    "mcp_server_credentials_invalid", "mcp_header_invalid", "mcp_header_conflict",
    "mcp_header_duplicate", "mcp_principal_jwt_missing", "mcp_principal_jwt_expired",
    "mcp_principal_jwt_unavailable", "mcp_principal_jwt_corrupt",
    "mcp_context_key_invalid", "mcp_context_keyring_invalid",
    "mcp_context_key_not_configured", "mcp_context_current_key_missing",
    "mcp_jwt_invalid", "mcp_jwt_expired_or_missing",
})


@dataclass(frozen=True)
class McpCatalogFailure:
    """Payload-free diagnostic for the route logger, never a public projection."""

    stage: str
    reason: str
    error_type: str

    @classmethod
    def from_error(cls, exc: Exception, *, stage: str) -> McpCatalogFailure:
        reason = "unexpected_error"
        if isinstance(exc, McpToolDiscoveryError) and isinstance(exc.reason, str) and exc.reason in _DISCOVERY_FAILURE_REASONS:
            reason = exc.reason
        elif isinstance(exc, McpRuntimeContextError) and isinstance(exc.code, str) and exc.code in _RUNTIME_FAILURE_REASONS:
            reason = exc.code
        # Use fixed class categories, not exception text or a provider-defined name.
        error_type = next((
            kind.__name__ for kind in (
                McpToolDiscoveryError, McpRuntimeContextError, TimeoutError,
                OSError, ValueError, TypeError, Exception,
            ) if isinstance(exc, kind)
        ))
        return cls(stage=stage, reason=reason, error_type=error_type)


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
    failure: McpCatalogFailure | None = None


class LiveMcpCatalogService:
    """Discover user-effective Gateway tools without platform-side catalog state."""

    def __init__(
        self,
        *,
        target_resolver: Callable[[str, str], Awaitable[Any]],
        discovery: Any,
        sse_discovery: Any = None,
    ) -> None:
        self._target_resolver = target_resolver
        self._discovery = discovery
        self._sse_discovery = sse_discovery

    async def list_server_tools(
        self,
        *,
        tenant_id: str,
        user_id: str,
        server_id: str,
        jwt: str,
    ) -> LiveMcpServerResult:
        stage = "target_resolution"
        try:
            target = await self._target_resolver(tenant_id, server_id)
            transport = getattr(target, "transport", "streamable_http")
            if transport not in {"streamable_http", "http", "sse"}:
                return LiveMcpServerResult(
                    server_id, (), "unsupported_transport",
                    McpCatalogFailure("transport_selection", "unsupported_transport", "McpToolDiscoveryError"),
                )
            discovery = self._sse_discovery if transport == "sse" else self._discovery
            stage = "tool_discovery"
            definitions = await discovery.discover_definitions(
                target.endpoint,
                static_headers=target.static_headers,
                jwt_authorization=f"Bearer {jwt}",
                credential_subject={"tenant_id": tenant_id, "user_id": user_id},
            )
            stage = "tool_projection"
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
        except Exception as exc:  # noqa: BLE001 - one Server must not fail multi-Server discovery.
            return LiveMcpServerResult(
                server_id, (), "discovery_failed",
                McpCatalogFailure.from_error(exc, stage=stage),
            )
        return LiveMcpServerResult(server_id, tools)
