from __future__ import annotations

import json
from typing import Any


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
    """Resolve an active MCP target and its current tool-name fence."""

    cursor = await conn.execute(
        """
        select credentials.credential_envelope, credentials.metadata_json,
          coalesce(
            array_agg(distinct catalog_entry.remote_tool_name),
            array[]::text[]
          ) as active_tool_names
        from mcp_servers
        join mcp_server_credentials credentials
          on credentials.tenant_id = mcp_servers.tenant_id
         and credentials.server_name = mcp_servers.name
        join tenant_capability_distributions distributions
          on distributions.tenant_id = mcp_servers.tenant_id
         and distributions.capability_kind = 'mcp_server'
         and distributions.capability_id = mcp_servers.name
         and distributions.status = 'active'
        join mcp_tool_catalog_entries catalog_entry
          on catalog_entry.tenant_id = mcp_servers.tenant_id
         and catalog_entry.server_name = mcp_servers.name
         and catalog_entry.catalog_generation = mcp_servers.catalog_generation
         and catalog_entry.status = 'active'
        join mcp_tools
          on catalog_entry.tool_id = mcp_tools.id
         and mcp_tools.server_id = mcp_servers.name
         and mcp_tools.status = 'active'
        join tool_policies
          on tool_policies.tenant_id = mcp_servers.tenant_id
         and tool_policies.tool_id = mcp_tools.id
         and tool_policies.status = 'active'
        where mcp_servers.tenant_id = %s
          and mcp_servers.name = %s
          and mcp_servers.status = 'active'
          and mcp_servers.catalog_status = 'available'
        group by credentials.credential_envelope, credentials.metadata_json
        """,
        (tenant_id, server_name),
    )
    row = await cursor.fetchone()
    if row is None:
        return None
    return dict(row)


async def bind_run_mcp_context(
    conn: Any,
    *,
    tenant_id: str,
    run_id: str,
    mcp_context_id: str,
) -> None:
    """Persist only the opaque context ID after admission has bound it to a Run."""

    await conn.execute(
        """
        update runs
        set mcp_context_id = %s,
            input_json = jsonb_set(
              coalesce(input_json, '{}'::jsonb),
              '{mcp_context_id}',
              to_jsonb(%s::text),
              true
            )
        where tenant_id = %s and id = %s
        """,
        (mcp_context_id, mcp_context_id, tenant_id, run_id),
    )


async def get_run_mcp_context_id(
    conn: Any,
    *,
    tenant_id: str,
    run_id: str,
) -> str | None:
    """Read only the opaque context ID for one exact Run."""

    cursor = await conn.execute(
        "select mcp_context_id from runs where tenant_id = %s and id = %s",
        (tenant_id, run_id),
    )
    row = await cursor.fetchone()
    context_id = row.get("mcp_context_id") if row is not None else None
    return str(context_id) if isinstance(context_id, str) and context_id else None


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
