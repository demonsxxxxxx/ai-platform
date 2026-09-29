from __future__ import annotations

from typing import Any

from psycopg import AsyncConnection

from app.mcp import api as mcp_api


TRUSTED_BUILTIN_MCP_TOOL_ID = "ragflow-knowledge-search"
TRUSTED_BUILTIN_MCP_SERVER_ID = "ragflow"
TRUSTED_BUILTIN_MCP_REMOTE_NAME = "ragflow_search"


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
      and %s::text <> ''
    """


def mcp_runtime_metadata_usable(tool: dict[str, Any]) -> bool:
    """Accept one lightweight Server-qualified reference."""

    return mcp_api.mcp_runtime_metadata_usable(tool)


async def get_mcp_tool_registry_entry(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    tool_id: str,
) -> dict[str, Any] | None:
    """Resolve dynamic references through their registered parent Server."""

    try:
        server_id, public_tool_name = mcp_api.parse_mcp_tool_reference(tool_id)
    except ValueError:
        return None
    cursor = await conn.execute(
        """
        select name, transport, status
        from mcp_servers
        where tenant_id = %s
          and name = %s
          and status <> 'deleted'
        """,
        (tenant_id, server_id),
    )
    row = await cursor.fetchone()
    if row is None:
        return None
    server = dict(row)
    server_status = str(server.get("status") or "disabled")
    return {
        "tool_id": mcp_api.build_mcp_tool_reference(server_id, public_tool_name),
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
    """Authorize a complete canonical Chat MCP selection or fail closed."""

    from app.identity.infrastructure.capability_distributions_postgres import (
        _capability_not_authorized,
    )
    from app.mcp.infrastructure.chat_access_postgres import (
        _authorize_chat_mcp_tool_entry,
        _chat_mcp_access_context,
    )

    if len(tool_ids) != len(set(tool_ids)):
        duplicate_id = next(
            (
                tool_id
                for index, tool_id in enumerate(tool_ids)
                if tool_id in tool_ids[:index]
            ),
            "mcp_tool",
        )
        context = _chat_mcp_access_context(
            tenant_id=tenant_id,
            principal_department_id=principal_department_id,
            principal_roles=principal_roles,
            is_admin=is_admin,
            permissions=permissions,
        )
        raise _capability_not_authorized(
            context=context,
            capability_kind="mcp_tool",
            capability_id=duplicate_id,
        )
    context = _chat_mcp_access_context(
        tenant_id=tenant_id,
        principal_department_id=principal_department_id,
        principal_roles=principal_roles,
        is_admin=is_admin,
        permissions=permissions,
    )
    authorized: list[dict[str, Any]] = []
    for tool_id in tool_ids:
        tool = await get_mcp_tool_registry_entry(
            conn,
            tenant_id=tenant_id,
            tool_id=tool_id,
        )
        if tool is None or str(tool.get("tool_id") or "").strip() != tool_id:
            raise _capability_not_authorized(
                context=context,
                capability_kind="mcp_tool",
                capability_id=tool_id,
            )
        authorized.append(
            await _authorize_chat_mcp_tool_entry(
                conn,
                context=context,
                tenant_id=tenant_id,
                tool=tool,
            )
        )
    return authorized


async def list_authorized_chat_mcp_tools(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    principal_department_id: str,
    principal_roles: list[str] | None,
    is_admin: bool,
    permissions: list[str] | None,
) -> list[dict[str, Any]]:
    """Return only retained builtin entries accepted by Chat admission."""

    from app.mcp.infrastructure.chat_access_postgres import (
        _authorize_chat_mcp_tool_entry,
        _chat_mcp_access_context,
        list_chat_mcp_tool_catalog_entries,
    )
    from app.platform.postgres.errors import RepositoryAuthorizationError

    context = _chat_mcp_access_context(
        tenant_id=tenant_id,
        principal_department_id=principal_department_id,
        principal_roles=principal_roles,
        is_admin=is_admin,
        permissions=permissions,
    )
    authorized: list[dict[str, Any]] = []
    for tool in await list_chat_mcp_tool_catalog_entries(
        conn,
        tenant_id=tenant_id,
    ):
        try:
            authorized.append(
                await _authorize_chat_mcp_tool_entry(
                    conn,
                    context=context,
                    tenant_id=tenant_id,
                    tool=tool,
                )
            )
        except RepositoryAuthorizationError:
            continue
    return authorized
