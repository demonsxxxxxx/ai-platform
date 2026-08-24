"""Compatibility exports for MCP catalog consumers."""

from app.mcp.api import (
    McpDiscoveredTool,
    McpToolDiscoveryError,
    StreamableHttpMcpToolDiscoveryAdapter,
)

__all__ = [
    "McpDiscoveredTool",
    "McpToolDiscoveryError",
    "StreamableHttpMcpToolDiscoveryAdapter",
]
