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
