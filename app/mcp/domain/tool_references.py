from __future__ import annotations

import re

from app.mcp.domain.identifiers import MCP_SAFE_ID_PATTERN


MCP_TOOL_REFERENCE_SEPARATOR = "::"
MCP_PUBLIC_TOOL_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,383}$")


def is_valid_mcp_public_tool_name(value: object) -> bool:
    return (
        isinstance(value, str)
        and MCP_PUBLIC_TOOL_NAME_PATTERN.fullmatch(value) is not None
    )


def build_mcp_tool_reference(server_id: str, public_tool_name: str) -> str:
    server = str(server_id or "").strip()
    tool = str(public_tool_name or "").strip()
    if not MCP_SAFE_ID_PATTERN.fullmatch(server) or MCP_TOOL_REFERENCE_SEPARATOR in server:
        raise ValueError("mcp_server_id_invalid")
    if not is_valid_mcp_public_tool_name(tool):
        raise ValueError("mcp_public_tool_name_invalid")
    return f"{server}{MCP_TOOL_REFERENCE_SEPARATOR}{tool}"


def parse_mcp_tool_reference(value: object) -> tuple[str, str]:
    if not isinstance(value, str) or value != value.strip():
        raise ValueError("mcp_tool_reference_invalid")
    server_id, separator, public_tool_name = value.partition(MCP_TOOL_REFERENCE_SEPARATOR)
    if not separator or build_mcp_tool_reference(server_id, public_tool_name) != value:
        raise ValueError("mcp_tool_reference_invalid")
    return server_id, public_tool_name


def assert_mcp_tool_reference(value: str, field_name: str = "mcp_tool_id") -> str:
    try:
        parse_mcp_tool_reference(value)
    except ValueError as exc:
        raise ValueError(f"{field_name} contains an invalid MCP tool reference") from exc
    return value


def mcp_runtime_metadata_usable(tool: dict[str, object]) -> bool:
    """Accept the code-owned builtin or one lightweight Server-qualified reference."""

    if (
        str(tool.get("tool_id") or tool.get("id") or "")
        == "ragflow-knowledge-search"
        and str(tool.get("server_id") or "") == "ragflow"
        and str(tool.get("transport_type") or "") == "http"
        and str(tool.get("endpoint") or "") == ""
        and str(tool.get("auth_mode") or "") == "platform-managed"
        and tool.get("allowed_tools") == ["ragflow_search"]
        and bool(tool.get("write_capable")) is False
    ):
        return True
    server_id = str(tool.get("server_id") or "")
    tool_id = str(tool.get("tool_id") or "")
    allowed_tools = tool.get("allowed_tools")
    try:
        reference_server_id, reference_tool_name = parse_mcp_tool_reference(tool_id)
    except ValueError:
        return False
    return bool(
        MCP_SAFE_ID_PATTERN.fullmatch(server_id)
        and reference_server_id == server_id
        and isinstance(allowed_tools, list)
        and len(allowed_tools) == 1
        and isinstance(allowed_tools[0], str)
        and is_valid_mcp_public_tool_name(allowed_tools[0])
        and allowed_tools[0] == reference_tool_name
        and str(tool.get("endpoint") or "") == ""
        and str(tool.get("transport_type") or "").lower()
        in {"http", "streamable_http", "sse"}
        and str(tool.get("auth_mode") or "").lower() == "none"
    )
