from __future__ import annotations

from typing import Any

from psycopg import AsyncConnection

from app.mcp.domain.tool_references import (
    MCP_PUBLIC_TOOL_NAME_PATTERN,
    build_mcp_tool_reference,
    parse_mcp_tool_reference,
)
from app.validation import SAFE_ID_PATTERN


TRUSTED_BUILTIN_MCP_TOOL_ID = "ragflow-knowledge-search"
TRUSTED_BUILTIN_MCP_SERVER_ID = "ragflow"
TRUSTED_BUILTIN_MCP_REMOTE_NAME = "ragflow_search"
TRUSTED_BUILTIN_MCP_TOOL_REFERENCE = "ragflow::ragflow_search"


def mcp_tool_tenant_authority_sql() -> str:
    """Restrict legacy ``mcp_tools`` consumers to the code-owned RAGFlow tool."""

    return f"""
      mcp_tools.id = '{TRUSTED_BUILTIN_MCP_TOOL_ID}'
      and mcp_tools.server_id = '{TRUSTED_BUILTIN_MCP_SERVER_ID}'
      and mcp_tools.transport_type = 'http'
      and mcp_tools.endpoint = ''
      and mcp_tools.auth_mode = 'platform-managed'
      and mcp_tools.allowed_tools = '[\"{TRUSTED_BUILTIN_MCP_REMOTE_NAME}\"]'::jsonb
      and mcp_tools.write_capable = false
    """


def is_trusted_builtin_mcp_tool(tool: dict[str, Any]) -> bool:
    """Recognize only the code-owned RAGFlow registry provenance."""

    return (
        str(tool.get("tool_id") or tool.get("id") or "") == TRUSTED_BUILTIN_MCP_TOOL_ID
        and str(tool.get("server_id") or "") == TRUSTED_BUILTIN_MCP_SERVER_ID
        and str(tool.get("transport_type") or "") == "http"
        and str(tool.get("endpoint") or "") == ""
        and str(tool.get("auth_mode") or "") == "platform-managed"
        and tool.get("allowed_tools") == [TRUSTED_BUILTIN_MCP_REMOTE_NAME]
        and bool(tool.get("write_capable")) is False
    )


def _repositories():
    """Resolve the legacy facade lazily so its compatibility re-exports stay acyclic."""

    from app import repositories

    return repositories


def mcp_runtime_metadata_usable(tool: dict[str, Any]) -> bool:
    """Return whether one builtin row or lightweight Server-qualified ref is usable."""

    if is_trusted_builtin_mcp_tool(tool):
        return True

    server_id = str(tool.get("server_id") or "")
    tool_id = str(tool.get("tool_id") or "")
    allowed_tools = tool.get("allowed_tools")
    try:
        reference_server_id, reference_tool_name = parse_mcp_tool_reference(tool_id)
    except ValueError:
        reference_server_id, reference_tool_name = "", ""
    return bool(
        SAFE_ID_PATTERN.fullmatch(server_id)
        and reference_server_id == server_id
        and isinstance(allowed_tools, list)
        and len(allowed_tools) == 1
        and isinstance(allowed_tools[0], str)
        and MCP_PUBLIC_TOOL_NAME_PATTERN.fullmatch(allowed_tools[0])
        and allowed_tools[0] == reference_tool_name
        and str(tool.get("endpoint") or "") == ""
        and str(tool.get("transport_type") or "").lower() in {"http", "streamable_http", "sse"}
        and str(tool.get("auth_mode") or "").lower() == "none"
    )


async def get_mcp_tool_registry_entry(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    tool_id: str,
) -> dict[str, Any] | None:
    """Resolve a lightweight reference through its registered MCP Server only."""

    try:
        server_id, public_tool_name = parse_mcp_tool_reference(tool_id)
    except ValueError:
        return None
    server = await _repositories().get_mcp_server_registry_entry(
        conn,
        tenant_id=tenant_id,
        name=server_id,
    )
    if server is None:
        return None
    server_status = str(server.get("status") or "disabled")
    return {
        "tool_id": build_mcp_tool_reference(server_id, public_tool_name),
        "server_id": server_id,
        "name": public_tool_name,
        "description": "",
        "transport_type": str(server.get("transport") or "streamable_http"),
        "endpoint": "",
        "auth_mode": "none",
        "allowed_tools": [public_tool_name],
        "registry_status": "active" if server_status == "active" else "disabled",
        "policy_status": "active",
        "server_status": server_status,
        "effective_status": "active" if server_status == "active" else "disabled",
        "visible_to_user": True,
        "write_capable": True,
        "risk_level": "high",
        "discovery_state": "unresolved",
    }


async def authorize_selected_chat_mcp_tools(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    tool_ids: list[str],
    principal_department_id: str,
    principal_roles: list[str] | None,
    is_admin: bool,
    permissions: list[str] | None,
) -> list[dict[str, Any]]:
    """Authorize stable refs against Server distribution, leaving tool ACL to Gateway."""

    repositories = _repositories()
    if len(tool_ids) != len(set(tool_ids)):
        duplicate_id = next(
            (tool_id for index, tool_id in enumerate(tool_ids) if tool_id in tool_ids[:index]),
            "mcp_tool",
        )
        context = repositories._chat_mcp_access_context(
            tenant_id=tenant_id,
            principal_department_id=principal_department_id,
            principal_roles=principal_roles,
            is_admin=is_admin,
            permissions=permissions,
        )
        raise repositories._capability_not_authorized(
            context=context,
            capability_kind="mcp_tool",
            capability_id=duplicate_id,
        )
    context = repositories._chat_mcp_access_context(
        tenant_id=tenant_id,
        principal_department_id=principal_department_id,
        principal_roles=principal_roles,
        is_admin=is_admin,
        permissions=permissions,
    )
    authorized: list[dict[str, Any]] = []
    for tool_id in tool_ids:
        tool = await repositories.get_mcp_tool_registry_entry(
            conn,
            tenant_id=tenant_id,
            tool_id=tool_id,
        )
        if tool is None or str(tool.get("tool_id") or "").strip() != tool_id:
            raise repositories._capability_not_authorized(
                context=context,
                capability_kind="mcp_tool",
                capability_id=tool_id,
            )
        authorized.append(
            await repositories._authorize_chat_mcp_tool_entry(
                conn,
                context=context,
                tenant_id=tenant_id,
                tool=tool,
            )
        )
    return authorized
