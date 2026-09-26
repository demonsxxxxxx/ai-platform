"""Chat access persistence. Callers own the connection and transaction."""

from __future__ import annotations

from app.auth import normalize_roles
from app.capability_distribution import CapabilityAccessContext
from app.capability_distribution import CapabilityDistributionSubject
from app.capability_distribution import resolve_capability_access
from app.identity.infrastructure.capability_distributions_postgres import _capability_not_authorized
from app.identity.infrastructure.capability_distributions_postgres import get_capability_distribution_row
from app.mcp import repository as _mcp_repository
from app.mcp.infrastructure.tool_policies_postgres import _tool_policy_projection
from app.mcp.repository import mcp_runtime_metadata_usable
from app.platform.postgres.errors import RepositoryConflictError
from app.tool_policy import evaluate_tool_policy
from psycopg import AsyncConnection
from typing import Any


async def list_chat_mcp_tool_catalog_entries(
    conn: AsyncConnection,
    *,
    tenant_id: str,
) -> list[dict[str, Any]]:
    """Load canonical generic MCP candidates for principal-scoped Chat projection."""

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
          mcp_servers.status as server_status,
          mcp_servers.catalog_status as server_catalog_status,
          mcp_tool_catalog_entries.status as catalog_status,
          mcp_tools.write_capable as registry_write_capable,
          mcp_tools.risk_level as registry_risk_level,
          mcp_tools.visible_to_user as registry_visible_to_user,
          tool_policies.status as policy_status,
          tool_policies.write_capable as policy_write_capable,
          tool_policies.risk_level as policy_risk_level,
          tool_policies.visible_to_user as policy_visible_to_user
        from mcp_tools
        join mcp_servers
          on mcp_servers.tenant_id = %s
         and mcp_servers.name = mcp_tools.server_id
         and mcp_servers.status = 'active'
        left join mcp_tool_catalog_entries
          on mcp_tool_catalog_entries.tool_id = mcp_tools.id
        join tool_policies
          on tool_policies.tenant_id = mcp_servers.tenant_id
         and tool_policies.tool_id = mcp_tools.id
         and tool_policies.status = 'active'
         and tool_policies.visible_to_user = true
        where mcp_tools.status = 'active'
          and mcp_tools.visible_to_user = true
          and """ + _mcp_repository.mcp_tool_tenant_authority_sql() + """
        order by mcp_tools.id asc
        """,
        (tenant_id, tenant_id),
    )
    entries: list[dict[str, Any]] = []
    for row in await cursor.fetchall():
        record = dict(row)
        entry = _tool_policy_projection(record, tenant_id=tenant_id)
        entry.update(
            server_status=str(record.get("server_status") or "disabled"),
            server_catalog_status=str(record.get("server_catalog_status") or "legacy"),
            catalog_status=str(record.get("catalog_status") or "legacy"),
            transport_type=str(record.get("transport_type") or ""),
            endpoint=str(record.get("endpoint") or ""),
            auth_mode=str(record.get("auth_mode") or ""),
            allowed_tools=(
                list(record.get("allowed_tools"))
                if isinstance(record.get("allowed_tools"), list)
                else []
            ),
        )
        entries.append(entry)
    return entries


def _chat_mcp_access_context(
    *,
    tenant_id: str,
    principal_department_id: str,
    principal_roles: list[str] | None,
    is_admin: bool,
    permissions: list[str] | None,
) -> CapabilityAccessContext:
    return CapabilityAccessContext(
        tenant_id=tenant_id,
        department_id=str(principal_department_id or ""),
        roles=normalize_roles(principal_roles or []),
        is_admin=bool(is_admin),
        permissions=[str(item) for item in permissions or [] if str(item)],
    )


async def _authorize_chat_mcp_tool_entry(
    conn: AsyncConnection,
    *,
    context: CapabilityAccessContext,
    tenant_id: str,
    tool: dict[str, Any],
) -> dict[str, Any]:
    tool_id = str(tool.get("tool_id") or "").strip()
    server_id = str(tool.get("server_id") or "").strip()
    if not tool_id or not server_id or not mcp_runtime_metadata_usable(tool):
        raise _capability_not_authorized(
            context=context,
            capability_kind="mcp_tool",
            capability_id=tool_id or "mcp_tool",
        )
    try:
        distribution = await get_capability_distribution_row(
            conn,
            tenant_id=tenant_id,
            capability_kind="mcp_server",
            capability_id=server_id,
        )
    except RepositoryConflictError as exc:
        raise _capability_not_authorized(
            context=context,
            capability_kind="mcp_tool",
            capability_id=tool_id,
        ) from exc
    lifecycle_status = (
        "active"
        if str(tool.get("effective_status") or "disabled") == "active"
        and str(tool.get("server_status") or "disabled") == "active"
        and bool(tool.get("visible_to_user"))
        else "disabled"
    )
    decision = resolve_capability_access(
        context,
        CapabilityDistributionSubject(
            capability_kind="mcp_tool",
            capability_id=tool_id,
            lifecycle_status=lifecycle_status,
            distribution=distribution,
            inherited_distribution_source=f"mcp_server:{server_id}",
        ),
        intent="use",
    )
    allowed_tool_name = str((tool.get("allowed_tools") or [""])[0])
    policy = evaluate_tool_policy(
        tool={
            "mcp_server": server_id,
            "mcp_tool": allowed_tool_name,
            "registered": True,
            "declared": True,
            "active": lifecycle_status == "active",
            "distributed": decision.usable,
            "identity_authorized": True,
            "object_authorized": True,
            "parameters_authorized": True,
            "risk_level": str(tool.get("risk_level") or "low"),
            "write_capable": bool(tool.get("write_capable")),
        }
    )
    if not decision.usable or not policy.allowed:
        raise _capability_not_authorized(
            context=context,
            capability_kind="mcp_tool",
            capability_id=tool_id,
            decision=decision,
        )
    return tool
