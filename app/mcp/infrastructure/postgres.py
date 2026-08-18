from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Callable, Mapping
from typing import Any
from urllib.parse import urlsplit, urlunsplit


MCP_LEGACY_CREDENTIAL_MIGRATION_LOCK_ID = 7_226_391_831_505_901_105


class McpLegacyCredentialMigrationError(RuntimeError):
    """Legacy MCP connection material cannot be migrated without data loss."""


def _metadata_without_header_names(value: object) -> dict[str, Any]:
    metadata = dict(value) if isinstance(value, Mapping) else {}
    metadata.pop("header_names", None)
    return metadata


def _legacy_public_endpoint(raw_url: str) -> str:
    try:
        parsed = urlsplit(raw_url)
        hostname = parsed.hostname or ""
        port = parsed.port
    except ValueError:
        return raw_url
    if not parsed.scheme or not hostname:
        return raw_url
    netloc = hostname
    if ":" in hostname and not hostname.startswith("["):
        netloc = f"[{hostname}]"
    if port is not None:
        netloc = f"{netloc}:{port}"
    return urlunsplit((parsed.scheme, netloc, parsed.path, "", ""))


def _legacy_endpoint_is_runtime_supported(raw_url: str) -> bool:
    try:
        parsed = urlsplit(raw_url)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError:
        return False
    return bool(
        parsed.scheme in {"http", "https"}
        and parsed.netloc
        and hostname
        and parsed.username is None
        and parsed.password is None
        and not parsed.query
        and not parsed.fragment
        and (port is None or 1 <= port <= 65535)
    )


def _legacy_endpoint_for_server(
    *,
    server_endpoint: str,
    tool_rows: list[dict[str, Any]],
) -> str:
    active_endpoints = {
        str(row.get("endpoint") or "").strip()
        for row in tool_rows
        if str(row.get("status") or "") == "active"
        and str(row.get("endpoint") or "").strip()
    }
    fallback_endpoints = {
        str(row.get("endpoint") or "").strip()
        for row in tool_rows
        if str(row.get("endpoint") or "").strip()
    }
    tool_endpoints = active_endpoints or fallback_endpoints
    public_endpoint = str(server_endpoint or "").strip()
    if public_endpoint:
        matching = {
            endpoint
            for endpoint in tool_endpoints
            if endpoint == public_endpoint
            or _legacy_public_endpoint(endpoint) == public_endpoint
        }
        if tool_endpoints and not matching:
            raise McpLegacyCredentialMigrationError("mcp_legacy_endpoint_conflict")
        candidates = matching or {public_endpoint}
    else:
        candidates = tool_endpoints
    if len(candidates) > 1:
        raise McpLegacyCredentialMigrationError("mcp_legacy_endpoint_conflict")
    endpoint = next(iter(candidates), "")
    if endpoint and not _legacy_endpoint_is_runtime_supported(endpoint):
        raise McpLegacyCredentialMigrationError("mcp_legacy_endpoint_invalid")
    return endpoint


async def migrate_legacy_mcp_credentials(
    conn: Any,
    *,
    seal_credentials: Callable[..., str],
) -> dict[str, int]:
    """Seal legacy endpoints before removing plaintext connection metadata."""

    await conn.execute(
        "select pg_advisory_xact_lock(%s)",
        (MCP_LEGACY_CREDENTIAL_MIGRATION_LOCK_ID,),
    )
    server_cursor = await conn.execute(
        """
        select tenant_id, name, endpoint_redacted, credential_fingerprint,
          credential_metadata_json, updated_by
        from mcp_servers
        order by tenant_id, name
        for update
        """
    )
    servers = [dict(row) for row in await server_cursor.fetchall()]
    tool_cursor = await conn.execute(
        """
        select id, server_id, endpoint, status
        from mcp_tools
        where endpoint <> ''
        order by server_id, id
        for update
        """
    )
    tools = [dict(row) for row in await tool_cursor.fetchall()]
    credential_cursor = await conn.execute(
        """
        select tenant_id, server_name, credential_fingerprint, metadata_json,
          credential_envelope, updated_by
        from mcp_server_credentials
        order by tenant_id, server_name
        for update
        """
    )
    credentials = {
        (str(row["tenant_id"]), str(row["server_name"])): dict(row)
        for row in await credential_cursor.fetchall()
    }

    servers_by_name: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for server in servers:
        servers_by_name[str(server.get("name") or "")].append(server)
    tools_by_server: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for tool in tools:
        server_name = str(tool.get("server_id") or "")
        matching_servers = servers_by_name.get(server_name, [])
        if not matching_servers:
            raise McpLegacyCredentialMigrationError("mcp_legacy_endpoint_orphaned")
        if len(matching_servers) > 1:
            raise McpLegacyCredentialMigrationError("mcp_legacy_endpoint_ambiguous")
        for server in matching_servers:
            key = (str(server["tenant_id"]), str(server["name"]))
            tools_by_server[key].append(tool)

    legacy_endpoints: dict[tuple[str, str], str] = {}
    for server in servers:
        key = (str(server["tenant_id"]), str(server["name"]))
        credential = credentials.get(key)
        if str((credential or {}).get("credential_envelope") or ""):
            continue
        legacy_endpoints[key] = _legacy_endpoint_for_server(
            server_endpoint=str(server.get("endpoint_redacted") or ""),
            tool_rows=tools_by_server.get(key, []),
        )

    sealed_count = 0
    scrubbed_count = 0
    for server in servers:
        key = (str(server["tenant_id"]), str(server["name"]))
        credential = credentials.get(key)
        envelope = str((credential or {}).get("credential_envelope") or "")
        endpoint = legacy_endpoints.get(key, "")
        if endpoint and not envelope:
            envelope = seal_credentials(
                tenant_id=key[0],
                server_id=key[1],
                endpoint=endpoint,
                static_headers={},
            )
            sealed_count += 1

        server_metadata = _metadata_without_header_names(
            server.get("credential_metadata_json")
        )
        if (
            str(server.get("endpoint_redacted") or "")
            or server_metadata != dict(server.get("credential_metadata_json") or {})
        ):
            await conn.execute(
                """
                update mcp_servers
                set endpoint_redacted = '',
                    credential_metadata_json = %s::jsonb,
                    updated_at = now()
                where tenant_id = %s and name = %s
                """,
                (json.dumps(server_metadata, ensure_ascii=False), key[0], key[1]),
            )
            scrubbed_count += 1

        if credential is not None:
            credential_metadata = _metadata_without_header_names(
                credential.get("metadata_json")
            )
            if (
                envelope != str(credential.get("credential_envelope") or "")
                or credential_metadata != dict(credential.get("metadata_json") or {})
            ):
                await conn.execute(
                    """
                    update mcp_server_credentials
                    set metadata_json = %s::jsonb,
                        credential_envelope = %s,
                        updated_at = now()
                    where tenant_id = %s and server_name = %s
                    """,
                    (
                        json.dumps(credential_metadata, ensure_ascii=False),
                        envelope,
                        key[0],
                        key[1],
                    ),
                )
                scrubbed_count += 1
        elif envelope:
            await conn.execute(
                """
                insert into mcp_server_credentials(
                  tenant_id, server_name, credential_fingerprint, metadata_json,
                  credential_envelope, updated_by, updated_at
                ) values (%s, %s, %s, '{}'::jsonb, %s, %s, now())
                """,
                (
                    key[0],
                    key[1],
                    str(server.get("credential_fingerprint") or ""),
                    envelope,
                    server.get("updated_by"),
                ),
            )

    cleared_tools = 0
    if tools:
        cursor = await conn.execute(
            "update mcp_tools set endpoint = '' where endpoint <> '' returning id"
        )
        cleared_tools = len(await cursor.fetchall())
    await conn.execute(
        "alter table mcp_servers validate constraint mcp_servers_endpoint_not_persisted"
    )
    await conn.execute(
        "alter table mcp_tools validate constraint mcp_tools_endpoint_not_persisted"
    )
    return {
        "sealed_credentials": sealed_count,
        "scrubbed_records": scrubbed_count,
        "cleared_tool_endpoints": cleared_tools,
    }


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
