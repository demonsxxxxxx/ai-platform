from __future__ import annotations

from typing import Any

from psycopg import AsyncConnection

from app.mcp.domain.tool_references import (
    build_mcp_tool_reference,
    is_valid_mcp_public_tool_name,
    mcp_runtime_metadata_usable as _mcp_runtime_metadata_usable,
    parse_mcp_tool_reference,
)


TRUSTED_BUILTIN_MCP_TOOL_ID = "ragflow-knowledge-search"
TRUSTED_BUILTIN_MCP_SERVER_ID = "ragflow"
TRUSTED_BUILTIN_MCP_REMOTE_NAME = "ragflow_search"


def _repositories():
    """Resolve the legacy facade lazily so its compatibility re-exports stay acyclic."""

    from app import repositories

    return repositories


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


def mcp_runtime_metadata_usable(tool: dict[str, Any]) -> bool:
    """Accept the code-owned builtin or one lightweight Server-qualified reference."""

    return _mcp_runtime_metadata_usable(tool)


async def list_workbench_mcp_tools(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    include_disabled: bool = True,
) -> list[dict[str, Any]]:
    """List only the retained code-owned builtin from local MCP tables."""

    cursor = await conn.execute(
        """
        select
          mcp_tools.id as tool_id, mcp_tools.server_id, mcp_tools.name, mcp_tools.description,
          mcp_tools.transport_type, mcp_tools.endpoint, mcp_tools.auth_mode, mcp_tools.allowed_tools,
          mcp_tools.status as registry_status, tool_policies.status as policy_status,
          mcp_tools.write_capable as registry_write_capable, tool_policies.write_capable as policy_write_capable,
          mcp_tools.risk_level as registry_risk_level, tool_policies.risk_level as policy_risk_level,
          mcp_tools.visible_to_user as registry_visible_to_user,
          tool_policies.visible_to_user as policy_visible_to_user
        from mcp_tools
        left join tool_policies
          on tool_policies.tenant_id = %s
         and tool_policies.tool_id = mcp_tools.id
        where mcp_tools.visible_to_user = true
          and tool_policies.visible_to_user = true
          and """
        + mcp_tool_tenant_authority_sql()
        + """
          and (%s or (mcp_tools.status = 'active' and tool_policies.status = 'active'))
        order by case mcp_tools.id when 'ragflow-knowledge-search' then 1 else 99 end, mcp_tools.id asc
        """,
        (tenant_id, tenant_id, include_disabled),
    )
    return [
        {**policy, "allowed_for_user": bool(policy["visible_to_user"])}
        for policy in (
            _repositories()._tool_policy_projection(dict(row), tenant_id=tenant_id)
            for row in await cursor.fetchall()
        )
    ]


async def get_mcp_tool_registry_entry(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    tool_id: str,
) -> dict[str, Any] | None:
    """Resolve dynamic references through their parent Server; retain one builtin."""

    if tool_id != TRUSTED_BUILTIN_MCP_TOOL_ID:
        try:
            server_id, public_tool_name = parse_mcp_tool_reference(tool_id)
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

    cursor = await conn.execute(
        """
        select
          mcp_tools.id as tool_id, mcp_tools.server_id, mcp_tools.name, mcp_tools.description,
          mcp_tools.transport_type, mcp_tools.endpoint, mcp_tools.auth_mode, mcp_tools.allowed_tools,
          mcp_tools.status as registry_status, mcp_servers.status as server_status,
          mcp_tools.write_capable as registry_write_capable, mcp_tools.risk_level as registry_risk_level,
          mcp_tools.visible_to_user as registry_visible_to_user, tool_policies.status as policy_status,
          tool_policies.write_capable as policy_write_capable, tool_policies.risk_level as policy_risk_level,
          tool_policies.visible_to_user as policy_visible_to_user
        from mcp_tools
        join mcp_servers
          on mcp_servers.tenant_id = %s
         and mcp_servers.name = mcp_tools.server_id
         and mcp_servers.status <> 'deleted'
        left join tool_policies
          on tool_policies.tenant_id = mcp_servers.tenant_id
         and tool_policies.tool_id = mcp_tools.id
        where mcp_tools.id = %s
          and """
        + mcp_tool_tenant_authority_sql()
        + """
        """,
        (tenant_id, tool_id, tenant_id),
    )
    row = await cursor.fetchone()
    if row is None:
        return None
    record = dict(row)
    entry = _repositories()._tool_policy_projection(record, tenant_id=tenant_id)
    entry["server_status"] = str(record.get("server_status") or "disabled")
    entry["transport_type"] = str(record.get("transport_type") or "")
    entry["endpoint"] = str(record.get("endpoint") or "")
    entry["auth_mode"] = str(record.get("auth_mode") or "")
    allowed_tools = record.get("allowed_tools")
    entry["allowed_tools"] = (
        [item for item in allowed_tools if is_valid_mcp_public_tool_name(item)]
        if isinstance(allowed_tools, list)
        else []
    )
    return entry


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

    repositories = _repositories()
    if len(tool_ids) != len(set(tool_ids)):
        duplicate_id = next(
            (
                tool_id
                for index, tool_id in enumerate(tool_ids)
                if tool_id in tool_ids[:index]
            ),
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

    repositories = _repositories()
    context = repositories._chat_mcp_access_context(
        tenant_id=tenant_id,
        principal_department_id=principal_department_id,
        principal_roles=principal_roles,
        is_admin=is_admin,
        permissions=permissions,
    )
    authorized: list[dict[str, Any]] = []
    for tool in await repositories.list_chat_mcp_tool_catalog_entries(
        conn,
        tenant_id=tenant_id,
    ):
        try:
            authorized.append(
                await repositories._authorize_chat_mcp_tool_entry(
                    conn,
                    context=context,
                    tenant_id=tenant_id,
                    tool=tool,
                )
            )
        except repositories.RepositoryAuthorizationError:
            continue
    return authorized
