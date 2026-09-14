from __future__ import annotations

import json
import re
import uuid
from datetime import datetime
from typing import Any

from psycopg import AsyncConnection

from app.mcp.domain.tool_references import (
    build_mcp_tool_reference,
    mcp_runtime_metadata_usable,
    parse_mcp_tool_reference,
)
from app.platform.postgres.errors import RepositoryConflictError, RepositoryNotFoundError


TRUSTED_BUILTIN_MCP_TOOL_ID = "ragflow-knowledge-search"
TRUSTED_BUILTIN_MCP_SERVER_ID = "ragflow"
TRUSTED_BUILTIN_MCP_REMOTE_NAME = "ragflow_search"
TRUSTED_BUILTIN_MCP_TOOL_REFERENCE = "ragflow::ragflow_search"
__all__ = ["mcp_runtime_metadata_usable"]

_ARCHIVED_AT_TIMESTAMP_PATTERN = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{3}Z$"
)
_ARCHIVED_AT_TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"


def _json_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return {}
    return value if isinstance(value, dict) else {}


def _json_string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise RepositoryConflictError("capability_distribution_scope_invalid") from exc
    if isinstance(value, (list, tuple)):
        if any(not isinstance(item, str) or not item.strip() for item in value):
            raise RepositoryConflictError("capability_distribution_scope_invalid")
        return list(value)
    raise RepositoryConflictError("capability_distribution_scope_invalid")


def _mcp_server_projection(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "tenant_id": str(row.get("tenant_id") or ""),
        "name": str(row.get("name") or ""),
        "transport": str(row.get("transport") or "streamable_http"),
        "endpoint_redacted": str(row.get("endpoint_redacted") or ""),
        "status": str(row.get("status") or "disabled"),
        "is_system": bool(row.get("is_system")),
        "allowed_roles": _json_string_list(row.get("allowed_roles")),
        "role_quotas": _json_dict(
            row.get("role_quotas_json") or row.get("role_quotas")
        ),
        "department_ids": _json_string_list(row.get("department_ids")),
        "credential_state": str(row.get("credential_state") or "not_configured"),
        "credential_metadata": _json_dict(
            row.get("credential_metadata_json") or row.get("credential_metadata")
        ),
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
    return [_mcp_server_projection(dict(row)) for row in await cursor.fetchall()]


async def list_tenant_mcp_server_registry(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    include_disabled: bool = True,
) -> list[dict[str, Any]]:
    """Return the unfiltered tenant MCP registry for distribution resolution."""

    return await list_mcp_server_registry(
        conn,
        tenant_id=tenant_id,
        include_disabled=include_disabled,
    )


async def list_mcp_server_registry_names(
    conn: AsyncConnection,
    *,
    tenant_id: str,
) -> list[str]:
    """Return non-deleted tenant MCP server names."""

    cursor = await conn.execute(
        """
        select name
        from mcp_servers
        where tenant_id = %s
          and status <> 'deleted'
        order by name asc
        """,
        (tenant_id,),
    )
    return [
        str(row.get("name") or "")
        for row in await cursor.fetchall()
        if row.get("name")
    ]


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
    return _mcp_server_projection(dict(row))


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
    return _mcp_server_projection(dict(row))


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
    return _mcp_server_projection(dict(row))


def _mcp_distribution_projection(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row.get("id") or ""),
        "tenant_id": str(row.get("tenant_id") or ""),
        "capability_kind": str(row.get("capability_kind") or ""),
        "capability_id": str(row.get("capability_id") or ""),
        "status": str(row.get("status") or "disabled"),
        "visible_to_user": bool(row.get("visible_to_user")),
        "scope_mode": str(row.get("scope_mode") or "allowlist"),
        "department_ids": _json_string_list(row.get("department_ids")),
        "allowed_roles": _json_string_list(row.get("allowed_roles")),
        "metadata_json": _json_dict(row.get("metadata_json")),
        "updated_by": row.get("updated_by"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


async def _acquire_mcp_distribution_lock(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    server_name: str,
) -> None:
    lock_scope = json.dumps(
        {
            "capability_id": server_name,
            "capability_kind": "mcp_server",
            "tenant_id": tenant_id,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    await conn.execute(
        "select pg_advisory_xact_lock(hashtextextended(%s::text, 0::bigint))",
        (lock_scope,),
    )


def _is_archived_distribution_metadata(value: Any) -> bool:
    archived_at = _json_dict(value).get("archived_at")
    if (
        not isinstance(archived_at, str)
        or _ARCHIVED_AT_TIMESTAMP_PATTERN.fullmatch(archived_at) is None
    ):
        return False
    try:
        datetime.strptime(archived_at, _ARCHIVED_AT_TIMESTAMP_FORMAT)
    except ValueError:
        return False
    return True


async def _lock_mcp_distribution_metadata(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    server_name: str,
) -> dict[str, Any] | None:
    cursor = await conn.execute(
        """
        select metadata_json
        from tenant_capability_distributions
        where tenant_id = %s
          and capability_kind = 'mcp_server'
          and capability_id = %s
        for update
        """,
        (tenant_id, server_name),
    )
    row = await cursor.fetchone()
    return _json_dict(row.get("metadata_json")) if row is not None else None


async def _require_mutable_mcp_distribution(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    server_name: str,
    allow_missing: bool,
) -> None:
    metadata = await _lock_mcp_distribution_metadata(
        conn,
        tenant_id=tenant_id,
        server_name=server_name,
    )
    if metadata is None:
        if not allow_missing:
            raise RepositoryNotFoundError("capability_distribution_not_found")
        return
    if _is_archived_distribution_metadata(metadata):
        raise RepositoryConflictError("capability_distribution_archived")


async def _raise_mcp_distribution_update_failure(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    server_name: str,
) -> None:
    metadata = await _lock_mcp_distribution_metadata(
        conn,
        tenant_id=tenant_id,
        server_name=server_name,
    )
    if metadata is not None and _is_archived_distribution_metadata(metadata):
        raise RepositoryConflictError("capability_distribution_archived")
    raise RepositoryNotFoundError("capability_distribution_not_found")


async def _require_mcp_server(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    server_name: str,
) -> None:
    cursor = await conn.execute(
        """
        select name
        from mcp_servers
        where tenant_id = %s
          and name = %s
          and status <> 'deleted'
        """,
        (tenant_id, server_name),
    )
    if await cursor.fetchone() is None:
        raise RepositoryNotFoundError("mcp_server_not_found")


async def upsert_mcp_server_distribution(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    server_name: str,
    status: str,
    visible_to_user: bool,
    scope_mode: str,
    department_ids: list[str],
    allowed_roles: list[str],
    metadata_json: dict[str, Any],
    updated_by: str | None,
) -> dict[str, Any]:
    """Persist one MCP Server distribution without touching catalog state."""

    await _require_mcp_server(
        conn,
        tenant_id=tenant_id,
        server_name=server_name,
    )
    await _acquire_mcp_distribution_lock(
        conn,
        tenant_id=tenant_id,
        server_name=server_name,
    )
    await _require_mutable_mcp_distribution(
        conn,
        tenant_id=tenant_id,
        server_name=server_name,
        allow_missing=True,
    )
    cursor = await conn.execute(
        """
        insert into tenant_capability_distributions(
          id, tenant_id, capability_kind, capability_id, status, visible_to_user,
          scope_mode, department_ids, allowed_roles, metadata_json, updated_by, updated_at
        )
        values (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, now())
        on conflict (tenant_id, capability_kind, capability_id) do update
        set status = excluded.status,
            visible_to_user = excluded.visible_to_user,
            scope_mode = excluded.scope_mode,
            department_ids = excluded.department_ids,
            allowed_roles = excluded.allowed_roles,
            metadata_json = excluded.metadata_json,
            updated_by = excluded.updated_by,
            updated_at = now()
        returning id, tenant_id, capability_kind, capability_id, status, visible_to_user,
                  scope_mode, department_ids, allowed_roles, metadata_json, updated_by,
                  created_at, updated_at
        """,
        (
            f"capdist_{uuid.uuid4().hex}",
            tenant_id,
            "mcp_server",
            server_name,
            status,
            visible_to_user,
            scope_mode,
            department_ids,
            json.dumps(allowed_roles, ensure_ascii=False),
            json.dumps(metadata_json, ensure_ascii=False, separators=(",", ":")),
            updated_by,
        ),
    )
    row = await cursor.fetchone()
    if row is None:
        await _raise_mcp_distribution_update_failure(
            conn,
            tenant_id=tenant_id,
            server_name=server_name,
        )
    return _mcp_distribution_projection(dict(row))


async def toggle_mcp_server_distribution(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    server_name: str,
    enabled: bool | None,
    updated_by: str | None,
) -> dict[str, Any]:
    """Toggle one MCP Server distribution without touching catalog state."""

    await _require_mcp_server(
        conn,
        tenant_id=tenant_id,
        server_name=server_name,
    )
    await _acquire_mcp_distribution_lock(
        conn,
        tenant_id=tenant_id,
        server_name=server_name,
    )
    await _require_mutable_mcp_distribution(
        conn,
        tenant_id=tenant_id,
        server_name=server_name,
        allow_missing=False,
    )
    cursor = await conn.execute(
        """
        update tenant_capability_distributions
        set status = case
              when %s::boolean is null then case when status = 'active' then 'disabled' else 'active' end
              when %s::boolean then 'active'
              else 'disabled'
            end,
            updated_by = %s,
            updated_at = now()
        where tenant_id = %s and capability_kind = %s and capability_id = %s
        returning id, tenant_id, capability_kind, capability_id, status, visible_to_user,
                  scope_mode, department_ids, allowed_roles, metadata_json, updated_by,
                  created_at, updated_at
        """,
        (enabled, enabled, updated_by, tenant_id, "mcp_server", server_name),
    )
    row = await cursor.fetchone()
    if row is None:
        await _raise_mcp_distribution_update_failure(
            conn,
            tenant_id=tenant_id,
            server_name=server_name,
        )
    return _mcp_distribution_projection(dict(row))


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


async def get_mcp_server_credential(
    conn: Any,
    *,
    tenant_id: str,
    server_name: str,
) -> dict[str, Any] | None:
    """Return one server's private credential record for an authorized detail read."""

    cursor = await conn.execute(
        """
        select credential_fingerprint, metadata_json, credential_envelope
        from mcp_server_credentials
        where tenant_id = %s and server_name = %s
        """,
        (tenant_id, server_name),
    )
    row = await cursor.fetchone()
    return dict(row) if row is not None else None


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
    return _mcp_server_projection(dict(row)) if row is not None else None
