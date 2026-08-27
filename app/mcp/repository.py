from __future__ import annotations

import json
from typing import Any
import uuid

from psycopg import AsyncConnection

from app.platform.postgres.errors import RepositoryConflictError, RepositoryNotFoundError
from app.mcp.identifiers import is_safe_mcp_id
from app.mcp.tool_references import (
    build_mcp_tool_reference,
    is_valid_mcp_public_tool_name,
    parse_mcp_tool_reference,
)


TRUSTED_BUILTIN_MCP_TOOL_ID = "ragflow-knowledge-search"
TRUSTED_BUILTIN_MCP_SERVER_ID = "ragflow"
TRUSTED_BUILTIN_MCP_REMOTE_NAME = "ragflow_search"
TRUSTED_BUILTIN_MCP_TOOL_REFERENCE = "ragflow::ragflow_search"


def _server_projection(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "tenant_id": str(row.get("tenant_id") or ""),
        "name": str(row.get("name") or ""),
        "transport": str(row.get("transport") or "streamable_http"),
        "endpoint_redacted": str(row.get("endpoint_redacted") or ""),
        "status": str(row.get("status") or "disabled"),
        "is_system": bool(row.get("is_system")),
        "allowed_roles": list(row.get("allowed_roles") or []),
        "role_quotas": dict(row.get("role_quotas_json") or {}),
        "department_ids": list(row.get("department_ids") or []),
        "credential_state": str(row.get("credential_state") or "not_configured"),
        "credential_metadata": dict(row.get("credential_metadata_json") or {}),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


_SERVER_FIELDS = """
  tenant_id, name, transport, endpoint_redacted, status, is_system,
  allowed_roles, role_quotas_json, department_ids, credential_state,
  credential_metadata_json, created_at, updated_at
"""


async def list_mcp_server_registry(
    conn: Any,
    *,
    tenant_id: str,
    department_id: str | None = None,
    include_disabled: bool = True,
) -> list[dict[str, Any]]:
    department_clause = (
        ""
        if department_id is None
        else "and (cardinality(department_ids) = 0 or %s = any(department_ids))"
    )
    params: tuple[Any, ...] = (tenant_id, include_disabled)
    if department_id is not None:
        params = (tenant_id, department_id, include_disabled)
    cursor = await conn.execute(
        f"""
        select {_SERVER_FIELDS}
        from mcp_servers
        where tenant_id = %s
          {department_clause}
          and status <> 'deleted'
          and (%s or status = 'active')
        order by is_system desc, name asc
        """,
        params,
    )
    return [_server_projection(dict(row)) for row in await cursor.fetchall()]


async def upsert_mcp_server_registry(conn: Any, **values: Any) -> dict[str, Any]:
    cursor = await conn.execute(
        f"""
        insert into mcp_servers(
          id, tenant_id, name, transport, endpoint_redacted, status, is_system,
          allowed_roles, role_quotas_json, department_ids, credential_state,
          credential_metadata_json, credential_fingerprint, updated_by, updated_at
        )
        values (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s, %s::jsonb, %s, %s, now())
        on conflict (tenant_id, name) do update
        set transport = excluded.transport,
            endpoint_redacted = excluded.endpoint_redacted,
            status = excluded.status,
            allowed_roles = excluded.allowed_roles,
            role_quotas_json = excluded.role_quotas_json,
            department_ids = excluded.department_ids,
            credential_state = excluded.credential_state,
            credential_metadata_json = excluded.credential_metadata_json,
            credential_fingerprint = excluded.credential_fingerprint,
            updated_by = excluded.updated_by,
            updated_at = now()
        where mcp_servers.is_system = excluded.is_system
        returning {_SERVER_FIELDS}
        """,
        (
            f"mcpsrv-{uuid.uuid4().hex}",
            values["tenant_id"],
            values["name"],
            values["transport"],
            "",
            "active" if values["enabled"] else "disabled",
            values["is_system"],
            json.dumps(values["allowed_roles"], ensure_ascii=False),
            json.dumps(values["role_quotas"], ensure_ascii=False),
            values["department_ids"],
            values["credential_state"],
            json.dumps(values["credential_metadata"], ensure_ascii=False),
            values["credential_fingerprint"],
            values["updated_by"],
        ),
    )
    row = await cursor.fetchone()
    if row is None:
        raise RepositoryConflictError("mcp_server_scope_conflict")
    return _server_projection(dict(row))


async def toggle_mcp_server_registry(
    conn: Any,
    *,
    tenant_id: str,
    name: str,
    enabled: bool | None,
    updated_by: str,
) -> dict[str, Any]:
    cursor = await conn.execute(
        f"""
        update mcp_servers
        set status = case
              when %s::boolean is null then case when status = 'active' then 'disabled' else 'active' end
              when %s::boolean then 'active'
              else 'disabled'
            end,
            updated_by = %s,
            updated_at = now()
        where tenant_id = %s and name = %s and status <> 'deleted'
        returning {_SERVER_FIELDS}
        """,
        (enabled, enabled, updated_by, tenant_id, name),
    )
    row = await cursor.fetchone()
    if row is None:
        raise RepositoryNotFoundError("mcp_server_not_found")
    return _server_projection(dict(row))


async def delete_mcp_server_registry(
    conn: Any,
    *,
    tenant_id: str,
    name: str,
    updated_by: str,
) -> dict[str, Any]:
    cursor = await conn.execute(
        f"""
        update mcp_servers
        set status = 'deleted', updated_by = %s, updated_at = now()
        where tenant_id = %s and name = %s
        returning {_SERVER_FIELDS}
        """,
        (updated_by, tenant_id, name),
    )
    row = await cursor.fetchone()
    if row is None:
        raise RepositoryNotFoundError("mcp_server_not_found")
    return _server_projection(dict(row))


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
    await conn.execute(
        """
        insert into mcp_server_credentials(
          tenant_id, server_name, credential_fingerprint, metadata_json,
          credential_envelope, updated_by, updated_at
        )
        values (%s, %s, %s, %s::jsonb, %s, %s, now())
        on conflict (tenant_id, server_name) do update
        set credential_fingerprint = excluded.credential_fingerprint,
            metadata_json = excluded.metadata_json,
            credential_envelope = excluded.credential_envelope,
            updated_by = excluded.updated_by,
            updated_at = now()
        """,
        (
            tenant_id,
            server_name,
            credential_fingerprint,
            json.dumps(metadata, ensure_ascii=False),
            credential_envelope or "",
            updated_by,
        ),
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
        is_safe_mcp_id(server_id)
        and reference_server_id == server_id
        and isinstance(allowed_tools, list)
        and len(allowed_tools) == 1
        and isinstance(allowed_tools[0], str)
        and is_valid_mcp_public_tool_name(allowed_tools[0])
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

    if tool_id == TRUSTED_BUILTIN_MCP_TOOL_ID:
        repositories = _repositories()
        cursor = await conn.execute(
            """
            select
              mcp_tools.id as tool_id,
              mcp_tools.server_id,
              mcp_tools.name,
              mcp_tools.description,
              mcp_tools.transport_type,
              mcp_tools.endpoint,
              mcp_tools.auth_mode,
              mcp_tools.allowed_tools,
              mcp_tools.status as registry_status,
              tool_policies.status as policy_status,
              mcp_tools.write_capable as registry_write_capable,
              tool_policies.write_capable as policy_write_capable,
              mcp_tools.risk_level as registry_risk_level,
              tool_policies.risk_level as policy_risk_level,
              mcp_tools.visible_to_user as registry_visible_to_user,
              tool_policies.visible_to_user as policy_visible_to_user
            from mcp_tools
            left join tool_policies
              on tool_policies.tenant_id = %s
             and tool_policies.tool_id = mcp_tools.id
            where mcp_tools.id = %s
              and """ + mcp_tool_tenant_authority_sql(),
            (tenant_id, tool_id, tenant_id),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return repositories._tool_policy_projection(dict(row), tenant_id=tenant_id)

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
