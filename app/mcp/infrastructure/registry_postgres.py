"""PostgreSQL persistence for the MCP server registry.

This module owns registry row projection and lifecycle writes. The legacy
repository facade and MCP runtime adapter delegate here while callers migrate
to the MCP API.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from psycopg import AsyncConnection

from app.platform.postgres.errors import (
    RepositoryConflictError,
    RepositoryNotFoundError,
)


def dumps_json(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False)


def _runtime_json_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return {}
    return value if isinstance(value, dict) else {}


def _runtime_json_string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise RepositoryConflictError(
                "capability_distribution_scope_invalid"
            ) from exc
    if isinstance(value, (list, tuple)):
        if any(not isinstance(item, str) or not item.strip() for item in value):
            raise RepositoryConflictError("capability_distribution_scope_invalid")
        return list(value)
    raise RepositoryConflictError("capability_distribution_scope_invalid")


def _dynamic_server_projection(row: dict[str, Any]) -> dict[str, Any]:
    """Project the runtime registry contract without legacy catalog state."""
    return {
        "tenant_id": str(row.get("tenant_id") or ""),
        "name": str(row.get("name") or ""),
        "transport": str(row.get("transport") or "streamable_http"),
        "endpoint_redacted": str(row.get("endpoint_redacted") or ""),
        "status": str(row.get("status") or "disabled"),
        "is_system": bool(row.get("is_system")),
        "allowed_roles": _runtime_json_string_list(row.get("allowed_roles")),
        "role_quotas": _runtime_json_dict(
            row.get("role_quotas_json") or row.get("role_quotas")
        ),
        "department_ids": _runtime_json_string_list(row.get("department_ids")),
        "credential_state": str(row.get("credential_state") or "not_configured"),
        "credential_metadata": _runtime_json_dict(
            row.get("credential_metadata_json") or row.get("credential_metadata")
        ),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


_DYNAMIC_SERVER_FIELDS = """
  tenant_id, name, transport, endpoint_redacted, status, is_system,
  allowed_roles, role_quotas_json, department_ids, credential_state,
  credential_metadata_json, created_at, updated_at
"""


async def list_runtime_mcp_server_registry(
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
        select {_DYNAMIC_SERVER_FIELDS}
        from mcp_servers
        where tenant_id = %s
          {department_clause}
          and status <> 'deleted'
          and (%s or status = 'active')
        order by is_system desc, name asc
        """,
        params,
    )
    return [_dynamic_server_projection(dict(row)) for row in await cursor.fetchall()]


async def list_runtime_tenant_mcp_server_registry(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    include_disabled: bool = True,
) -> list[dict[str, Any]]:
    return await list_runtime_mcp_server_registry(
        conn,
        tenant_id=tenant_id,
        include_disabled=include_disabled,
    )


async def list_runtime_mcp_server_registry_names(
    conn: AsyncConnection,
    *,
    tenant_id: str,
) -> list[str]:
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
        str(row.get("name") or "") for row in await cursor.fetchall() if row.get("name")
    ]


async def upsert_runtime_mcp_server_registry(
    conn: Any, **values: Any
) -> dict[str, Any]:
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
        returning {_DYNAMIC_SERVER_FIELDS}
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
    return _dynamic_server_projection(dict(row))


async def toggle_runtime_mcp_server_registry(
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
        returning {_DYNAMIC_SERVER_FIELDS}
        """,
        (enabled, enabled, updated_by, tenant_id, name),
    )
    row = await cursor.fetchone()
    if row is None:
        raise RepositoryNotFoundError("mcp_server_not_found")
    return _dynamic_server_projection(dict(row))


async def delete_runtime_mcp_server_registry(
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
        returning {_DYNAMIC_SERVER_FIELDS}
        """,
        (updated_by, tenant_id, name),
    )
    row = await cursor.fetchone()
    if row is None:
        raise RepositoryNotFoundError("mcp_server_not_found")
    return _dynamic_server_projection(dict(row))


async def record_runtime_mcp_server_credential(
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


async def record_mcp_server_credential(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    server_name: str,
    credential_fingerprint: str,
    metadata: dict[str, Any],
    updated_by: str,
) -> None:
    """Record credential fingerprint metadata without storing raw credential values."""

    await conn.execute(
        """
        insert into mcp_server_credentials(
          tenant_id, server_name, credential_fingerprint, metadata_json, updated_by, updated_at
        )
        values (%s, %s, %s, %s::jsonb, %s, now())
        on conflict (tenant_id, server_name) do update
        set credential_fingerprint = excluded.credential_fingerprint,
            metadata_json = excluded.metadata_json,
            updated_by = excluded.updated_by,
            updated_at = now()
        """,
        (
            tenant_id,
            server_name,
            credential_fingerprint,
            dumps_json(metadata),
            updated_by,
        ),
    )
