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
            credential_envelope,
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
            array_agg(distinct (mcp_tools.allowed_tools ->> 0)) filter (
              where mcp_tools.status = 'active'
                and tool_policies.status = 'active'
                and mcp_tools.allowed_tools ->> 0 is not null
            ),
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
        left join mcp_tools
          on mcp_tools.server_id = mcp_servers.name
        left join tool_policies
          on tool_policies.tenant_id = mcp_servers.tenant_id
         and tool_policies.tool_id = mcp_tools.id
        where mcp_servers.tenant_id = %s
          and mcp_servers.name = %s
          and mcp_servers.status = 'active'
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
