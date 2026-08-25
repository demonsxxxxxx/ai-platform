from __future__ import annotations

import json
from typing import Any
import uuid

from app.platform.postgres.errors import RepositoryConflictError, RepositoryNotFoundError


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
    department_clause = "" if department_id is None else "and (cardinality(department_ids) = 0 or %s = any(department_ids))"
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
    enabled = bool(values["enabled"])
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
            values["endpoint_redacted"],
            "active" if enabled else "disabled",
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


async def upsert_mcp_distribution(conn: Any, **values: Any) -> dict[str, Any]:
    cursor = await conn.execute(
        """
        insert into tenant_capability_distributions(
          id, tenant_id, capability_kind, capability_id, status, visible_to_user,
          scope_mode, department_ids, allowed_roles, metadata_json, updated_by, updated_at
        )
        values (%s, %s, 'mcp_server', %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, now())
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
          scope_mode, department_ids, allowed_roles, metadata_json, updated_by, created_at, updated_at
        """,
        (
            f"capdist-{uuid.uuid4().hex}",
            values["tenant_id"],
            values["capability_id"],
            values["status"],
            values["visible_to_user"],
            values["scope_mode"],
            values["department_ids"],
            json.dumps(values["allowed_roles"], ensure_ascii=False),
            json.dumps(values["metadata_json"], ensure_ascii=False),
            values["updated_by"],
        ),
    )
    row = await cursor.fetchone()
    if row is None:
        raise RepositoryConflictError("capability_distribution_conflict")
    return dict(row)


async def toggle_mcp_distribution(
    conn: Any,
    *,
    tenant_id: str,
    capability_id: str,
    enabled: bool | None,
    updated_by: str,
) -> dict[str, Any]:
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
        where tenant_id = %s and capability_kind = 'mcp_server' and capability_id = %s
        returning id, tenant_id, capability_kind, capability_id, status, visible_to_user,
          scope_mode, department_ids, allowed_roles, metadata_json, updated_by, created_at, updated_at
        """,
        (enabled, enabled, updated_by, tenant_id, capability_id),
    )
    row = await cursor.fetchone()
    if row is None:
        raise RepositoryNotFoundError("capability_distribution_not_found")
    return dict(row)


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
    """Persist public metadata and an opaque encrypted connection envelope."""

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


async def get_mcp_relay_target(
    conn: Any,
    *,
    tenant_id: str,
    server_name: str,
) -> dict[str, Any] | None:
    """Resolve an active MCP target without reading a platform Tool Catalog."""

    cursor = await conn.execute(
        """
        select credentials.credential_envelope, credentials.metadata_json
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
    if row is None:
        return None
    return {**dict(row), "active_tool_names": []}


async def get_run_mcp_identity(
    conn: Any,
    *,
    tenant_id: str,
    run_id: str,
) -> dict[str, str] | None:
    """Read the authoritative identity needed to retire a Run grant."""

    cursor = await conn.execute(
        "select tenant_id, user_id, id as run_id from runs where tenant_id = %s and id = %s",
        (tenant_id, run_id),
    )
    row = await cursor.fetchone()
    if row is None:
        return None
    return {
        "tenant_id": str(row["tenant_id"]),
        "user_id": str(row["user_id"]),
        "run_id": str(row["run_id"]),
    }


class PostgresMcpRelayTargetReader:
    def __init__(self, transaction_factory: Any) -> None:
        self._transaction_factory = transaction_factory

    async def __call__(self, tenant_id: str, server_id: str) -> dict[str, Any] | None:
        async with self._transaction_factory() as conn:
            return await get_mcp_relay_target(
                conn,
                tenant_id=tenant_id,
                server_name=server_id,
            )
