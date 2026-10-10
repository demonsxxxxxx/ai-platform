"""Assemble the Claude adapter's selected, authenticated MCP tool surface."""

from app.execution.infrastructure.claude_mcp import ClaudeMcpRegistration
from app.mcp.infrastructure.client import list_mcp_tools, open_mcp_session


def prepare_claude_mcp(subjects, configs, callback_wrapper=None):
    return ClaudeMcpRegistration(
        subjects, configs, session_factory=open_mcp_session, list_tools=list_mcp_tools,
        callback_wrapper=callback_wrapper,
    )
