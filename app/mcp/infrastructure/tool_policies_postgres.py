"""Tool policies persistence. Callers own the connection and transaction."""

from __future__ import annotations

from app.mcp import repository as _mcp_repository
from app.platform.postgres.errors import RepositoryConflictError
from app.platform.postgres.errors import RepositoryNotFoundError
from app.platform.postgres.values import _coerce_bool
from app.tool_policy import max_risk
from psycopg import AsyncConnection
from typing import Any


def _effective_status(registry_status: str, policy_status: str) -> str:
    return "active" if registry_status == "active" and policy_status == "active" else "disabled"


def _tool_policy_projection(row: dict[str, Any], *, tenant_id: str) -> dict[str, Any]:
    has_tenant_policy = row.get("policy_status") is not None
    policy_source = "tenant" if has_tenant_policy else "registry"
    registry_status = str(row.get("registry_status") or row.get("status") or "disabled")
    policy_status = str(row.get("policy_status") or "disabled")
    registry_write_capable = _coerce_bool(row.get("registry_write_capable"), _coerce_bool(row.get("write_capable")))
    policy_write_capable = _coerce_bool(row.get("policy_write_capable"), False)
    registry_risk_level = str(row.get("registry_risk_level") or row.get("risk_level") or "low")
    policy_risk_level = str(row.get("policy_risk_level") or "low")
    registry_visible_to_user = _coerce_bool(row.get("registry_visible_to_user"), _coerce_bool(row.get("visible_to_user"), True))
    policy_visible_to_user = _coerce_bool(row.get("policy_visible_to_user"), False)
    effective_visible_to_user = registry_visible_to_user and policy_visible_to_user
    effective_policy_status = _effective_status(registry_status, policy_status)
    if not effective_visible_to_user:
        effective_policy_status = "disabled"
    return {
        "tenant_id": tenant_id,
        "tool_id": str(row.get("tool_id") or row.get("id") or ""),
        "id": str(row.get("tool_id") or row.get("id") or ""),
        "server_id": str(row.get("server_id") or ""),
        "name": str(row.get("name") or ""),
        "description": str(row.get("description") or ""),
        "registry_status": registry_status,
        "policy_status": policy_status,
        "effective_status": effective_policy_status,
        "status": effective_policy_status,
        "registry_write_capable": registry_write_capable,
        "policy_write_capable": policy_write_capable,
        "write_capable": registry_write_capable or policy_write_capable,
        "registry_risk_level": registry_risk_level,
        "policy_risk_level": policy_risk_level,
        "risk_level": max_risk(registry_risk_level, policy_risk_level),
        "registry_visible_to_user": registry_visible_to_user,
        "policy_visible_to_user": policy_visible_to_user,
        "visible_to_user": effective_visible_to_user,
        "source": policy_source,
        "reason": str(row.get("reason") or ""),
        "updated_by": row.get("updated_by"),
        "updated_at": row.get("updated_at"),
    }


async def ensure_mcp_tool_active(conn: AsyncConnection, *, tenant_id: str, tool_id: str) -> dict[str, Any]:
    cursor = await conn.execute(
        """
        select
          mcp_tools.id,
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
          and """ + _mcp_repository.mcp_tool_tenant_authority_sql() + """
        """,
        (tenant_id, tool_id, tenant_id),
    )
    row = await cursor.fetchone()
    if row is None:
        raise RepositoryNotFoundError("mcp_tool_not_found")
    policy = _tool_policy_projection(dict(row), tenant_id=tenant_id)
    if policy["effective_status"] != "active" or not policy["visible_to_user"]:
        raise RepositoryConflictError("mcp_tool_disabled")
    return policy


async def list_admin_tool_policies(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    include_disabled: bool = True,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Return tenant-scoped admin tool policy projections without private connection fields."""
    limit = max(min(int(limit), 500), 1)
    cursor = await conn.execute(
        """
        select
          mcp_tools.id as tool_id,
          mcp_tools.server_id,
          mcp_tools.name,
          mcp_tools.description,
          mcp_tools.status as registry_status,
          tool_policies.status as policy_status,
          mcp_tools.write_capable as registry_write_capable,
          tool_policies.write_capable as policy_write_capable,
          mcp_tools.risk_level as registry_risk_level,
          tool_policies.risk_level as policy_risk_level,
          mcp_tools.visible_to_user as registry_visible_to_user,
          tool_policies.visible_to_user as policy_visible_to_user,
          tool_policies.reason,
          tool_policies.updated_by,
          tool_policies.updated_at
        from mcp_tools
        left join tool_policies
          on tool_policies.tenant_id = %s
         and tool_policies.tool_id = mcp_tools.id
        where """ + _mcp_repository.mcp_tool_tenant_authority_sql() + """
          and (
            %s
            or (
              mcp_tools.status = 'active'
              and tool_policies.status = 'active'
              and coalesce(mcp_tools.visible_to_user, false) = true
              and tool_policies.visible_to_user = true
            )
          )
        order by case mcp_tools.id
          when 'ragflow-knowledge-search' then 1
          else 99
        end, mcp_tools.id asc
        limit %s
        """,
        (tenant_id, tenant_id, include_disabled, limit),
    )
    return [_tool_policy_projection(dict(row), tenant_id=tenant_id) for row in await cursor.fetchall()]


async def list_admin_tool_policy_history(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    tool_id: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Return bounded tenant-scoped audit history for admin tool policy updates."""
    bounded_limit = max(min(int(100 if limit is None else limit), 500), 1)
    clauses = [
        "tenant_id = %s",
        "target_type = %s",
        "action = %s",
    ]
    params: list[Any] = [tenant_id, "tool_policy", "admin.tool_policy.updated"]
    if tool_id:
        clauses.append("target_id = %s")
        params.append(tool_id)
    params.append(bounded_limit)
    cursor = await conn.execute(
        f"""
        select id, user_id, action, target_type, target_id, trace_id, schema_version, payload_json, created_at
        from audit_logs
        where {" and ".join(clauses)}
        order by created_at desc, id desc
        limit %s
        """,
        tuple(params),
    )
    return [dict(row) for row in await cursor.fetchall()]


async def upsert_admin_tool_policy(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    tool_id: str,
    status: str,
    risk_level: str,
    write_capable: bool,
    visible_to_user: bool,
    reason: str,
    updated_by: str,
) -> dict[str, Any]:
    """Upsert a tenant-scoped tool policy and return its effective runtime projection."""
    cursor = await conn.execute(
        """
        with upserted as (
          insert into tool_policies(
            tenant_id, tool_id, status, write_capable, risk_level,
            visible_to_user, reason, updated_by, updated_at
          )
          select %s, mcp_tools.id, %s, %s, %s, %s, %s, %s, now()
          from mcp_tools
          where mcp_tools.id = %s
            and """ + _mcp_repository.mcp_tool_tenant_authority_sql() + """
          on conflict (tenant_id, tool_id) do update
          set status = excluded.status,
              write_capable = excluded.write_capable,
              risk_level = excluded.risk_level,
              visible_to_user = excluded.visible_to_user,
              reason = excluded.reason,
              updated_by = excluded.updated_by,
              updated_at = now()
          returning *
        )
        select
          mcp_tools.id as tool_id,
          mcp_tools.server_id,
          mcp_tools.name,
          mcp_tools.description,
          mcp_tools.status as registry_status,
          upserted.status as policy_status,
          mcp_tools.write_capable as registry_write_capable,
          upserted.write_capable as policy_write_capable,
          mcp_tools.risk_level as registry_risk_level,
          upserted.risk_level as policy_risk_level,
          mcp_tools.visible_to_user as registry_visible_to_user,
          upserted.visible_to_user as policy_visible_to_user,
          upserted.reason,
          upserted.updated_by,
          upserted.updated_at
        from upserted
        join mcp_tools on mcp_tools.id = upserted.tool_id
        """,
        (
            tenant_id,
            status,
            write_capable,
            risk_level,
            visible_to_user,
            reason,
            updated_by,
            tool_id,
            tenant_id,
        ),
    )
    row = await cursor.fetchone()
    if row is None:
        raise RepositoryNotFoundError("mcp_tool_not_found")
    return _tool_policy_projection(dict(row), tenant_id=tenant_id)
