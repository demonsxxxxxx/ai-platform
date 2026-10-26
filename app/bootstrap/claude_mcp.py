"""Assemble the Claude adapter's selected, authenticated MCP tool surface."""

from app.execution.infrastructure.claude_mcp import ClaudeMcpRegistration
from app.execution.infrastructure.original_file_delivery import OriginalFileDelivery
from app.mcp.infrastructure.client import list_mcp_tools, open_mcp_session
from app.mcp.infrastructure.credential_callback import McpCredentialCallbackClient


def build_mcp_credential_resolver(**binding):
    return McpCredentialCallbackClient(**binding).get


def build_original_file_delivery(**binding):
    return OriginalFileDelivery(**binding)


def prepare_claude_mcp(subjects, configs, callback_wrapper=None, *, failure_observer=None, credential_resolver=None, original_file_delivery=None):
    return ClaudeMcpRegistration(
        subjects, configs, session_factory=open_mcp_session, list_tools=list_mcp_tools,
        callback_wrapper=callback_wrapper, failure_observer=failure_observer,
        credential_resolver=credential_resolver, original_file_delivery=original_file_delivery,
    )
