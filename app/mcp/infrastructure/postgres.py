from __future__ import annotations

from typing import Any

from psycopg import AsyncConnection

from app.mcp.domain.tool_references import (
    build_mcp_tool_reference,
    mcp_runtime_metadata_usable,
    parse_mcp_tool_reference,
)
from app.mcp.infrastructure.registry_postgres import (
    _DYNAMIC_SERVER_FIELDS as _SERVER_FIELDS,
    _dynamic_server_projection as _server_projection,
)


TRUSTED_BUILTIN_MCP_TOOL_ID = "ragflow-knowledge-search"
TRUSTED_BUILTIN_MCP_SERVER_ID = "ragflow"
TRUSTED_BUILTIN_MCP_REMOTE_NAME = "ragflow_search"
TRUSTED_BUILTIN_MCP_TOOL_REFERENCE = "ragflow::ragflow_search"
__all__ = ["mcp_runtime_metadata_usable"]


async def list_mcp_server_registry(
    conn: Any,
    *,
    tenant_id: str,
    department_id: str | None = None,
    include_disabled: bool = True,
) -> list[dict[str, Any]]:
    from app.mcp.infrastructure import registry_postgres

    return await registry_postgres.list_runtime_mcp_server_registry(
        conn,
        tenant_id=tenant_id,
        department_id=department_id,
        include_disabled=include_disabled,
    )


async def upsert_mcp_server_registry(conn: Any, **values: Any) -> dict[str, Any]:
    from app.mcp.infrastructure import registry_postgres

    return await registry_postgres.upsert_runtime_mcp_server_registry(conn, **values)


async def toggle_mcp_server_registry(
    conn: Any,
    *,
    tenant_id: str,
    name: str,
    enabled: bool | None,
    updated_by: str,
) -> dict[str, Any]:
    from app.mcp.infrastructure import registry_postgres

    return await registry_postgres.toggle_runtime_mcp_server_registry(
        conn,
        tenant_id=tenant_id,
        name=name,
        enabled=enabled,
        updated_by=updated_by,
    )


async def delete_mcp_server_registry(
    conn: Any,
    *,
    tenant_id: str,
    name: str,
    updated_by: str,
) -> dict[str, Any]:
    from app.mcp.infrastructure import registry_postgres

    return await registry_postgres.delete_runtime_mcp_server_registry(
        conn,
        tenant_id=tenant_id,
        name=name,
        updated_by=updated_by,
    )


async def record_mcp_server_credential(
    conn: Any,
    *,
    tenant_id: str,
    server_name: str,
    credential_fingerprint: str,
    metadata: dict[str, Any],
    credential_envelope: str | None,
    updated_by: str,
) -> None:
    from app.mcp.infrastructure import registry_postgres

    await registry_postgres.record_runtime_mcp_server_credential(
        conn,
        tenant_id=tenant_id,
        server_name=server_name,
        credential_fingerprint=credential_fingerprint,
        metadata=metadata,
        credential_envelope=credential_envelope,
        updated_by=updated_by,
    )


async def get_mcp_server_runtime_target(
    conn: Any,
    *,
    tenant_id: str,
    server_name: str,
) -> dict[str, Any] | None:
    """Return one active Server's encrypted runtime connection material."""

    cursor = await conn.execute(
        """
        select mcp_servers.transport, credentials.credential_envelope
        from mcp_servers
        join mcp_server_credentials credentials
          on credentials.tenant_id = mcp_servers.tenant_id
         and credentials.server_name = mcp_servers.name
        join tenant_capability_distributions distributions
          on distributions.tenant_id = mcp_servers.tenant_id
         and distributions.capability_kind = 'mcp_server'
         and distributions.capability_id = mcp_servers.name
         and distributions.status = 'active'
        where mcp_servers.tenant_id = %s
          and mcp_servers.name = %s
          and mcp_servers.status = 'active'
        """,
        (tenant_id, server_name),
    )
    row = await cursor.fetchone()
    return dict(row) if row is not None else None


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


async def get_mcp_tool_registry_entry(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    tool_id: str,
) -> dict[str, Any] | None:
    """Resolve a lightweight reference through its registered MCP Server only."""

    if tool_id == TRUSTED_BUILTIN_MCP_TOOL_ID:
        return None

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


async def get_mcp_server_registry_entry(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    name: str,
) -> dict[str, Any] | None:
    cursor = await conn.execute(
        f"""
        select {_SERVER_FIELDS}
        from mcp_servers
        where tenant_id = %s and name = %s and status <> 'deleted'
        """,
        (tenant_id, name),
    )
    row = await cursor.fetchone()
    return _server_projection(dict(row)) if row is not None else None
