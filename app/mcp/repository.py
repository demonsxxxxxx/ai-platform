from __future__ import annotations

import uuid
from typing import Any
from urllib.parse import urlsplit

from psycopg import AsyncConnection

from app.auth import normalize_roles
from app.capability_distribution import (
    CapabilityAccessContext,
    CapabilityDistributionSubject,
    resolve_capability_access,
)
from app.control_plane_contracts import standard_trace_id
from app.db import transaction
from app.mcp.catalog import MCP_PUBLIC_TOOL_DESCRIPTION, MCP_PUBLIC_TOOL_LABEL, MCP_PUBLIC_UNAVAILABLE_LABEL
from app.validation import SAFE_ID_PATTERN


MCP_CATALOG_SYNC_LEASE_SECONDS = 120
TRUSTED_BUILTIN_MCP_TOOL_ID = "ragflow-knowledge-search"
TRUSTED_BUILTIN_MCP_SERVER_ID = "ragflow"
TRUSTED_BUILTIN_MCP_REMOTE_NAME = "ragflow_search"
MCP_CATALOG_MANAGED_POLICY_REASONS = [
    "mcp_catalog_read_only",
    "mcp_catalog_write_capable",
    "mcp_catalog_annotation_unknown",
    "mcp_tool_not_read_only",
]


def mcp_tool_tenant_authority_sql() -> str:
    """Return the fixed tenant authority predicate for builtin or current catalog tools."""

    return f"""
      (
        (
          mcp_tools.id = '{TRUSTED_BUILTIN_MCP_TOOL_ID}'
          and mcp_tools.server_id = '{TRUSTED_BUILTIN_MCP_SERVER_ID}'
          and mcp_tools.transport_type = 'http'
          and mcp_tools.endpoint = ''
          and mcp_tools.auth_mode = 'platform-managed'
          and mcp_tools.allowed_tools = '[\"{TRUSTED_BUILTIN_MCP_REMOTE_NAME}\"]'::jsonb
          and mcp_tools.write_capable = false
        )
        or exists (
          select 1
          from mcp_tool_catalog_entries catalog_entry
          join mcp_servers catalog_server
            on catalog_server.tenant_id = catalog_entry.tenant_id
           and catalog_server.name = catalog_entry.server_name
          where catalog_entry.tool_id = mcp_tools.id
            and catalog_entry.tenant_id = %s
            and catalog_entry.status = 'active'
            and catalog_entry.catalog_generation = catalog_server.catalog_generation
            and catalog_server.status = 'active'
            and catalog_server.catalog_status = 'available'
        )
      )
    """


def is_trusted_builtin_mcp_tool(tool: dict[str, Any]) -> bool:
    """Recognize only the code-owned RAGFlow registry provenance, never a legacy fallback."""

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


def new_mcp_catalog_tool_id() -> str:
    """Create a stable opaque selector valid for the existing Chat ID contract."""

    return f"mcpt-{uuid.uuid4().hex}"


def _catalog_manifest_policy_reason(existing_reason: Any, desired_reason: str) -> str | None:
    """Compare policy provenance only while the catalog still owns that policy row."""

    reason = str(existing_reason or "")
    return desired_reason if not reason or reason in MCP_CATALOG_MANAGED_POLICY_REASONS else None


def mcp_runtime_metadata_usable(tool: dict[str, Any]) -> bool:
    """Return whether one catalog or builtin row can be sandbox-registered."""

    if is_trusted_builtin_mcp_tool(tool):
        return True

    server_id = str(tool.get("server_id") or "")
    tool_id = str(tool.get("tool_id") or "")
    allowed_tools = tool.get("allowed_tools")
    endpoint = str(tool.get("endpoint") or "")
    parsed = urlsplit(endpoint)
    return bool(
        SAFE_ID_PATTERN.fullmatch(server_id)
        and SAFE_ID_PATTERN.fullmatch(tool_id)
        and isinstance(allowed_tools, list)
        and len(allowed_tools) == 1
        and isinstance(allowed_tools[0], str)
        and SAFE_ID_PATTERN.fullmatch(allowed_tools[0])
        and parsed.scheme in {"http", "https"}
        and parsed.netloc
        and not parsed.username
        and not parsed.password
        and not parsed.query
        and not parsed.fragment
        and str(tool.get("transport_type") or "").lower() in {"http", "streamable_http", "sse"}
        and str(tool.get("auth_mode") or "").lower() == "none"
        and str(tool.get("catalog_status") or "legacy") in {"legacy", "active"}
        and str(tool.get("server_catalog_status") or "legacy") in {"legacy", "available"}
    )


async def list_workbench_mcp_tools(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    include_disabled: bool = True,
) -> list[dict[str, Any]]:
    """List tenant-visible builtin or current catalog tools without cross-tenant inventory leakage."""

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
          and """ + mcp_tool_tenant_authority_sql() + """
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
    """Fetch one builtin or current tenant catalog tool for Chat admission and worker reauthorization."""

    cursor = await conn.execute(
        """
        select
          mcp_tools.id as tool_id, mcp_tools.server_id, mcp_tools.name, mcp_tools.description,
          mcp_tools.transport_type, mcp_tools.endpoint, mcp_tools.auth_mode, mcp_tools.allowed_tools,
          mcp_tools.status as registry_status, mcp_servers.status as server_status,
          mcp_servers.catalog_status as server_catalog_status,
          mcp_tool_catalog_entries.status as catalog_status,
          mcp_tools.write_capable as registry_write_capable, mcp_tools.risk_level as registry_risk_level,
          mcp_tools.visible_to_user as registry_visible_to_user, tool_policies.status as policy_status,
          tool_policies.write_capable as policy_write_capable, tool_policies.risk_level as policy_risk_level,
          tool_policies.visible_to_user as policy_visible_to_user
        from mcp_tools
        join mcp_servers
          on mcp_servers.tenant_id = %s
         and mcp_servers.name = mcp_tools.server_id
         and mcp_servers.status <> 'deleted'
        left join mcp_tool_catalog_entries
          on mcp_tool_catalog_entries.tool_id = mcp_tools.id
        left join tool_policies
          on tool_policies.tenant_id = mcp_servers.tenant_id
         and tool_policies.tool_id = mcp_tools.id
        where mcp_tools.id = %s
          and """ + mcp_tool_tenant_authority_sql() + """
        """,
        (tenant_id, tool_id, tenant_id),
    )
    row = await cursor.fetchone()
    if row is None:
        return None
    record = dict(row)
    entry = _repositories()._tool_policy_projection(record, tenant_id=tenant_id)
    entry["server_status"] = str(record.get("server_status") or "disabled")
    entry["server_catalog_status"] = str(record.get("server_catalog_status") or "legacy")
    entry["catalog_status"] = str(record.get("catalog_status") or "legacy")
    entry["transport_type"] = str(record.get("transport_type") or "")
    entry["endpoint"] = str(record.get("endpoint") or "")
    entry["auth_mode"] = str(record.get("auth_mode") or "")
    allowed_tools = record.get("allowed_tools")
    entry["allowed_tools"] = (
        [item for item in allowed_tools if isinstance(item, str) and SAFE_ID_PATTERN.fullmatch(item)]
        if isinstance(allowed_tools, list)
        else []
    )
    return entry


def _catalog_state_projection(row: dict[str, Any], *, published: bool = False) -> dict[str, Any]:
    return {
        "catalog_status": str(row.get("catalog_status") or "unavailable"),
        "catalog_unavailable_reason": str(row.get("catalog_unavailable_reason") or ""),
        "catalog_revision": int(row.get("catalog_revision") or 0),
        "catalog_discovered_count": int(row.get("catalog_discovered_count") or 0),
        "catalog_selectable_count": int(row.get("catalog_selectable_count") or 0),
        "published": published,
    }


async def get_mcp_server_catalog_sync_snapshot(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    name: str,
) -> dict[str, Any] | None:
    """Load the private lifecycle generation and fingerprint for one explicit refresh."""

    cursor = await conn.execute(
        """
        select mcp_servers.name, mcp_servers.transport, mcp_servers.status,
          mcp_servers.credential_fingerprint, mcp_servers.credential_metadata_json,
          credentials.credential_envelope,
          catalog_generation, catalog_revision, catalog_status, catalog_unavailable_reason,
          catalog_sync_attempt, catalog_sync_lease_expires_at,
          catalog_discovered_count, catalog_selectable_count
        from mcp_servers
        left join mcp_server_credentials credentials
          on credentials.tenant_id = mcp_servers.tenant_id
         and credentials.server_name = mcp_servers.name
        where mcp_servers.tenant_id = %s
          and mcp_servers.name = %s
        """,
        (tenant_id, name),
    )
    row = await cursor.fetchone()
    return dict(row) if row is not None else None


async def _locked_server(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    server_name: str,
) -> dict[str, Any] | None:
    cursor = await conn.execute(
        """
        select tenant_id, name, status, catalog_generation, catalog_sync_attempt,
          catalog_sync_lease_expires_at, catalog_revision, catalog_status,
          catalog_unavailable_reason, catalog_discovered_count, catalog_selectable_count,
          catalog_sync_lease_expires_at > clock_timestamp() as catalog_sync_lease_active
        from mcp_servers
        where tenant_id = %s
          and name = %s
        for update
        """,
        (tenant_id, server_name),
    )
    row = await cursor.fetchone()
    return dict(row) if row is not None else None


async def begin_mcp_catalog_sync(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    server_name: str,
    observed_generation: int,
    actor_id: str,
) -> dict[str, Any]:
    """Claim or expired-takeover one generation-bound discovery attempt before I/O."""

    server = await _locked_server(conn, tenant_id=tenant_id, server_name=server_name)
    if server is None:
        return {
            "started": False,
            "catalog_status": "unavailable",
            "catalog_unavailable_reason": "deleted",
            "catalog_revision": 0,
            "catalog_discovered_count": 0,
            "catalog_selectable_count": 0,
        }
    if int(server.get("catalog_generation") or 0) != observed_generation:
        return {
            "started": False,
            **_catalog_state_projection(server),
            "catalog_status": "unavailable",
            "catalog_unavailable_reason": "stale_generation",
        }
    if str(server.get("status") or "") != "active":
        return {
            "started": False,
            **_catalog_state_projection(server),
            "catalog_status": "disabled" if server.get("status") == "disabled" else "unavailable",
            "catalog_unavailable_reason": "disabled" if server.get("status") == "disabled" else "deleted",
        }
    cursor = await conn.execute(
        """
        update mcp_servers
        set catalog_sync_attempt = catalog_sync_attempt + 1,
            catalog_status = 'syncing',
            catalog_unavailable_reason = 'refresh_required',
            catalog_sync_lease_expires_at = clock_timestamp() + (%s * interval '1 second'),
            updated_by = %s,
            updated_at = now()
        where tenant_id = %s
          and name = %s
          and catalog_generation = %s
          and (
            catalog_status <> 'syncing'
            or catalog_sync_lease_expires_at is null
            or catalog_sync_lease_expires_at <= clock_timestamp()
          )
        returning catalog_generation, catalog_sync_attempt, catalog_revision, catalog_status,
          catalog_unavailable_reason, catalog_discovered_count, catalog_selectable_count
        """,
        (MCP_CATALOG_SYNC_LEASE_SECONDS, actor_id, tenant_id, server_name, observed_generation),
    )
    row = await cursor.fetchone()
    if row is None:
        return {
            "started": False,
            **_catalog_state_projection(server),
            "catalog_status": "unavailable",
            "catalog_unavailable_reason": "sync_in_progress",
        }
    return {
        "started": True,
        **_catalog_state_projection(dict(row)),
        "catalog_sync_attempt": int(row["catalog_sync_attempt"]),
    }


async def record_mcp_catalog_sync_outcome(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    server_name: str,
    observed_generation: int,
    observed_attempt: int,
    reason: str,
    actor_id: str,
) -> dict[str, Any]:
    """Persist one retryable outcome only while the claimed generation and attempt match."""

    server = await _locked_server(conn, tenant_id=tenant_id, server_name=server_name)
    if server is None:
        return {
            "catalog_status": "unavailable",
            "catalog_unavailable_reason": "deleted",
            "catalog_revision": 0,
            "catalog_discovered_count": 0,
            "catalog_selectable_count": 0,
        }
    if (
        int(server.get("catalog_generation") or 0) != observed_generation
        or int(server.get("catalog_sync_attempt") or 0) != observed_attempt
        or str(server.get("catalog_status") or "") != "syncing"
        or server.get("catalog_sync_lease_active") is not True
    ):
        return {
            **_catalog_state_projection(server),
            "catalog_status": "unavailable",
            "catalog_unavailable_reason": "stale_generation",
        }
    if str(server.get("status") or "") != "active":
        return {
            **_catalog_state_projection(server),
            "catalog_status": "disabled" if server.get("status") == "disabled" else "unavailable",
            "catalog_unavailable_reason": "disabled" if server.get("status") == "disabled" else "deleted",
        }
    cursor = await conn.execute(
        """
        update mcp_servers
        set catalog_status = 'unavailable',
            catalog_unavailable_reason = %s,
            catalog_discovered_count = 0,
            catalog_selectable_count = 0,
            catalog_sync_lease_expires_at = null,
            catalog_last_synced_at = now(),
            updated_by = %s,
            updated_at = now()
        where tenant_id = %s
          and name = %s
          and catalog_generation = %s
          and catalog_sync_attempt = %s
          and catalog_status = 'syncing'
          and catalog_sync_lease_expires_at > clock_timestamp()
        returning catalog_status, catalog_unavailable_reason, catalog_revision,
          catalog_discovered_count, catalog_selectable_count
        """,
        (reason, actor_id, tenant_id, server_name, observed_generation, observed_attempt),
    )
    row = await cursor.fetchone()
    if row is None:
        return {
            **_catalog_state_projection(server),
            "catalog_status": "unavailable",
            "catalog_unavailable_reason": "stale_generation",
        }
    await _repositories().append_audit_log(
        conn,
        tenant_id=tenant_id,
        user_id=actor_id,
        action="mcp.catalog.sync_unavailable",
        target_type="mcp_server",
        target_id=server_name,
        trace_id=standard_trace_id(server_name),
        payload_json={"reason": reason, "generation": observed_generation},
    )
    return _catalog_state_projection(dict(row))


class _McpCatalogPublicationFenceLost(RuntimeError):
    def __init__(self, catalog_state: dict[str, Any]) -> None:
        super().__init__("mcp_catalog_publication_fence_lost")
        self.catalog_state = catalog_state


async def publish_mcp_tool_catalog(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    server_name: str,
    observed_generation: int,
    observed_attempt: int,
    endpoint: str,
    tools: tuple[Any, ...],
    actor_id: str,
) -> dict[str, Any]:
    """Atomically publish one complete tenant catalog under its claimed attempt."""

    server = await _locked_server(conn, tenant_id=tenant_id, server_name=server_name)
    if server is None:
        return {
            "catalog_status": "unavailable",
            "catalog_unavailable_reason": "deleted",
            "catalog_revision": 0,
            "catalog_discovered_count": 0,
            "catalog_selectable_count": 0,
            "published": False,
        }
    if (
        int(server.get("catalog_generation") or 0) != observed_generation
        or int(server.get("catalog_sync_attempt") or 0) != observed_attempt
        or str(server.get("catalog_status") or "") != "syncing"
        or server.get("catalog_sync_lease_active") is not True
    ):
        return {
            **_catalog_state_projection(server),
            "catalog_status": "unavailable",
            "catalog_unavailable_reason": "stale_generation",
            "published": False,
        }
    if str(server.get("status") or "") != "active":
        return {
            **_catalog_state_projection(server),
            "catalog_status": "disabled" if server.get("status") == "disabled" else "unavailable",
            "catalog_unavailable_reason": "disabled" if server.get("status") == "disabled" else "deleted",
            "published": False,
        }

    existing_cursor = await conn.execute(
        """
        select entries.tool_id, entries.remote_tool_name, entries.schema_hash,
          entries.status as catalog_entry_status, mcp_tools.write_capable,
          mcp_tools.risk_level, tool_policies.reason as policy_reason
        from mcp_tool_catalog_entries entries
        join mcp_tools on mcp_tools.id = entries.tool_id
        left join tool_policies
          on tool_policies.tenant_id = entries.tenant_id
         and tool_policies.tool_id = entries.tool_id
        where entries.tenant_id = %s
          and entries.server_name = %s
        for update
        """,
        (tenant_id, server_name),
    )
    existing = {str(row["remote_tool_name"]): dict(row) for row in await existing_cursor.fetchall()}
    active_existing = {
        remote_name: row
        for remote_name, row in existing.items()
        if str(row.get("catalog_entry_status") or "") == "active"
    }
    desired_names = {str(tool.remote_name) for tool in tools}
    desired_manifest = {
        str(tool.remote_name): (
            str(tool.schema_hash),
            "active",
            bool(tool.write_capable),
            str(tool.risk_level),
            _catalog_manifest_policy_reason(
                existing.get(str(tool.remote_name), {}).get("policy_reason"),
                str(tool.catalog_policy_reason),
            ),
        )
        for tool in tools
    }
    existing_manifest = {
        remote_name: (
            str(row.get("schema_hash") or ""),
            str(row.get("catalog_entry_status") or "disabled"),
            bool(row.get("write_capable")),
            str(row.get("risk_level") or "low"),
            _catalog_manifest_policy_reason(row.get("policy_reason"), str(row.get("policy_reason") or "")),
        )
        for remote_name, row in active_existing.items()
    }
    manifest_changed = desired_manifest != existing_manifest

    for tool in tools:
        remote_name = str(tool.remote_name)
        tool_id = str(existing.get(remote_name, {}).get("tool_id") or new_mcp_catalog_tool_id())
        catalog_status = "active"
        await conn.execute(
            """
            insert into mcp_tools(
              id, server_id, name, description, transport_type, endpoint, auth_mode,
              allowed_tools, status, write_capable, risk_level, visible_to_user
            )
            values (%s, %s, %s, %s, 'streamable_http', %s, 'none', %s::jsonb, %s, %s, %s, true)
            on conflict (id) do update
            set server_id = excluded.server_id,
                name = excluded.name,
                description = excluded.description,
                transport_type = excluded.transport_type,
                endpoint = excluded.endpoint,
                auth_mode = excluded.auth_mode,
                allowed_tools = excluded.allowed_tools,
                status = excluded.status,
                write_capable = excluded.write_capable,
                risk_level = excluded.risk_level,
                visible_to_user = excluded.visible_to_user
            """,
            (
                tool_id,
                server_name,
                MCP_PUBLIC_TOOL_LABEL,
                MCP_PUBLIC_TOOL_DESCRIPTION,
                endpoint,
                _repositories().dumps_json([remote_name]),
                catalog_status,
                tool.write_capable,
                tool.risk_level,
            ),
        )
        await conn.execute(
            """
            insert into mcp_tool_catalog_entries(
              tool_id, tenant_id, server_name, remote_tool_name, catalog_generation,
              schema_hash, status, updated_at
            )
            values (%s, %s, %s, %s, %s, %s, %s, now())
            on conflict (tenant_id, server_name, remote_tool_name) do update
            set tool_id = excluded.tool_id,
                catalog_generation = excluded.catalog_generation,
                schema_hash = excluded.schema_hash,
                status = excluded.status,
                updated_at = now()
            """,
            (tool_id, tenant_id, server_name, remote_name, observed_generation, str(tool.schema_hash), catalog_status),
        )
        await conn.execute(
            """
            insert into tool_policies(
              tenant_id, tool_id, status, write_capable, risk_level, visible_to_user, reason, updated_by
            )
            values (%s, %s, 'active', %s, %s, true, %s, %s)
            on conflict (tenant_id, tool_id) do update
            set status = excluded.status,
                write_capable = excluded.write_capable,
                risk_level = excluded.risk_level,
                visible_to_user = excluded.visible_to_user,
                reason = excluded.reason,
                updated_by = excluded.updated_by,
                updated_at = now()
            where tool_policies.reason = any(%s)
            """,
            (
                tenant_id,
                tool_id,
                tool.write_capable,
                tool.risk_level,
                tool.catalog_policy_reason,
                actor_id,
                MCP_CATALOG_MANAGED_POLICY_REASONS,
            ),
        )

    removed_tool_ids = [
        str(row["tool_id"])
        for remote_name, row in active_existing.items()
        if remote_name not in desired_names
    ]
    if removed_tool_ids:
        await conn.execute(
            """
            update mcp_tool_catalog_entries
            set status = 'stale', updated_at = now()
            where tenant_id = %s
              and server_name = %s
              and tool_id = any(%s)
            """,
            (tenant_id, server_name, removed_tool_ids),
        )
        await conn.execute("update mcp_tools set status = 'disabled' where id = any(%s)", (removed_tool_ids,))

    discovered_count = len(tools)
    selectable_count = len(tools)
    catalog_status = "no_tools" if not tools else "available" if selectable_count else "unavailable"
    reason = "no_tools" if not tools else "" if selectable_count else "no_selectable_tools"
    revision = int(server.get("catalog_revision") or 0) + (1 if manifest_changed or not server.get("catalog_revision") else 0)
    cursor = await conn.execute(
        """
        update mcp_servers
        set catalog_revision = %s,
            catalog_status = %s,
            catalog_unavailable_reason = %s,
            catalog_discovered_count = %s,
            catalog_selectable_count = %s,
            catalog_sync_lease_expires_at = null,
            catalog_last_synced_at = now(),
            updated_by = %s,
            updated_at = now()
        where tenant_id = %s
          and name = %s
          and catalog_generation = %s
          and catalog_sync_attempt = %s
          and catalog_status = 'syncing'
          and catalog_sync_lease_expires_at > clock_timestamp()
        returning catalog_status, catalog_unavailable_reason, catalog_revision,
          catalog_discovered_count, catalog_selectable_count
        """,
        (
            revision,
            catalog_status,
            reason,
            discovered_count,
            selectable_count,
            actor_id,
            tenant_id,
            server_name,
            observed_generation,
            observed_attempt,
        ),
    )
    row = await cursor.fetchone()
    if row is None:
        raise _McpCatalogPublicationFenceLost(_catalog_state_projection(server))
    await _repositories().append_audit_log(
        conn,
        tenant_id=tenant_id,
        user_id=actor_id,
        action="mcp.catalog.published",
        target_type="mcp_server",
        target_id=server_name,
        trace_id=standard_trace_id(server_name),
        payload_json={
            "generation": observed_generation,
            "catalog_revision": revision,
            "discovered_count": discovered_count,
            "selectable_count": selectable_count,
            "status": catalog_status,
        },
    )
    return {**_catalog_state_projection(dict(row), published=manifest_changed), "published": manifest_changed}


async def mark_mcp_catalog_lifecycle_unavailable(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    server_name: str,
    reason: str,
) -> None:
    """Invalidate every tenant catalog identity before a lifecycle refresh or removal."""

    status = "deleted" if reason == "deleted" else "disabled"
    cursor = await conn.execute(
        """
        update mcp_tool_catalog_entries
        set status = %s, updated_at = now()
        where tenant_id = %s
          and server_name = %s
        returning tool_id
        """,
        (status, tenant_id, server_name),
    )
    tool_ids = [str(row["tool_id"]) for row in await cursor.fetchall()]
    if tool_ids:
        await conn.execute("update mcp_tools set status = 'disabled' where id = any(%s)", (tool_ids,))


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


async def list_chat_mcp_catalog_unavailable(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    principal_department_id: str,
    principal_roles: list[str] | None,
    is_admin: bool,
    permissions: list[str] | None,
    selectable_server_names: set[str] | None = None,
) -> list[dict[str, str]]:
    """Return generic visible unavailable states without server or endpoint identity."""

    cursor = await conn.execute(
        """
        select name, status, catalog_status, catalog_unavailable_reason, catalog_selectable_count
        from mcp_servers
        where tenant_id = %s
          and status <> 'deleted'
          and catalog_status <> 'legacy'
        order by name asc
        """,
        (tenant_id,),
    )
    context = _chat_mcp_access_context(
        tenant_id=tenant_id,
        principal_department_id=principal_department_id,
        principal_roles=principal_roles,
        is_admin=is_admin,
        permissions=permissions,
    )
    unavailable: list[dict[str, str]] = []
    for raw in await cursor.fetchall():
        row = dict(raw)
        server_name = str(row.get("name") or "")
        distribution = await _repositories().get_capability_distribution_row(
            conn,
            tenant_id=tenant_id,
            capability_kind="mcp_server",
            capability_id=server_name,
        )
        decision = resolve_capability_access(
            context,
            CapabilityDistributionSubject(
                capability_kind="mcp_server",
                capability_id=server_name,
                lifecycle_status=str(row.get("status") or "disabled"),
                distribution=distribution,
            ),
            intent="discover",
        )
        if not decision.visible:
            continue
        catalog_status = str(row.get("catalog_status") or "")
        if catalog_status == "available" and server_name in (selectable_server_names or set()):
            continue
        reason = str(row.get("catalog_unavailable_reason") or catalog_status or "unavailable")
        if catalog_status == "available" and int(row.get("catalog_selectable_count") or 0) > 0:
            reason = "policy_blocked"
        unavailable.append({"label": MCP_PUBLIC_UNAVAILABLE_LABEL, "reason": reason})
    return unavailable


class PostgresMcpCatalogStore:
    """PostgreSQL adapter for the synchronizer's small persistence interface."""

    async def begin(self, command: Any) -> dict[str, Any]:
        """Claim a lease-backed catalog attempt in one short transaction."""

        async with transaction() as conn:
            await _repositories().ensure_user(
                conn,
                tenant_id=command.tenant_id,
                user_id=command.actor_id,
                display_name=command.actor_id,
            )
            return await begin_mcp_catalog_sync(
                conn,
                tenant_id=command.tenant_id,
                server_name=command.server_name,
                observed_generation=command.observed_generation,
                actor_id=command.actor_id,
            )

    async def record_outcome(
        self,
        command: Any,
        *,
        observed_attempt: int,
        reason: str,
    ) -> dict[str, Any]:
        """Record a bounded unavailable result and clear the active lease."""

        async with transaction() as conn:
            await _repositories().ensure_user(
                conn,
                tenant_id=command.tenant_id,
                user_id=command.actor_id,
                display_name=command.actor_id,
            )
            return await record_mcp_catalog_sync_outcome(
                conn,
                tenant_id=command.tenant_id,
                server_name=command.server_name,
                observed_generation=command.observed_generation,
                observed_attempt=observed_attempt,
                reason=reason,
                actor_id=command.actor_id,
            )

    async def publish(
        self,
        command: Any,
        *,
        observed_attempt: int,
        tools: tuple[Any, ...],
    ) -> dict[str, Any]:
        """Publish a complete manifest in one transaction tied to the claimed lease."""

        try:
            async with transaction() as conn:
                await _repositories().ensure_user(
                    conn,
                    tenant_id=command.tenant_id,
                    user_id=command.actor_id,
                    display_name=command.actor_id,
                )
                return await publish_mcp_tool_catalog(
                    conn,
                    tenant_id=command.tenant_id,
                    server_name=command.server_name,
                    observed_generation=command.observed_generation,
                    observed_attempt=observed_attempt,
                    endpoint=command.endpoint or "",
                    tools=tools,
                    actor_id=command.actor_id,
                )
        except _McpCatalogPublicationFenceLost as exc:
            return {
                **exc.catalog_state,
                "catalog_status": "unavailable",
                "catalog_unavailable_reason": "stale_generation",
                "published": False,
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

    repositories = _repositories()
    if len(tool_ids) != len(set(tool_ids)):
        duplicate_id = next((tool_id for index, tool_id in enumerate(tool_ids) if tool_id in tool_ids[:index]), "mcp_tool")
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
        tool = await repositories.get_mcp_tool_registry_entry(conn, tenant_id=tenant_id, tool_id=tool_id)
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
    """Return only current-principal entries that pass the existing Chat admission resolver."""

    repositories = _repositories()
    context = repositories._chat_mcp_access_context(
        tenant_id=tenant_id,
        principal_department_id=principal_department_id,
        principal_roles=principal_roles,
        is_admin=is_admin,
        permissions=permissions,
    )
    authorized: list[dict[str, Any]] = []
    for tool in await repositories.list_chat_mcp_tool_catalog_entries(conn, tenant_id=tenant_id):
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
