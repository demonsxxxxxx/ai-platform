from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from typing import Any, Protocol
from urllib.parse import urlsplit

import httpx

from app import repositories
from app.control_plane_contracts import sanitize_public_text
from app.db import transaction
from app.validation import SAFE_ID_PATTERN


MCP_DISCOVERY_PAGE_LIMIT = 100
MCP_PROTOCOL_VERSION = "2025-03-26"


class McpToolDiscoveryError(ValueError):
    """Represent a bounded, public-safe MCP discovery failure category."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class McpDiscoveredTool:
    """Canonical remote tool metadata retained only long enough to publish a catalog."""

    remote_name: str
    label: str
    description: str
    schema_hash: str
    read_only: bool


@dataclass(frozen=True)
class McpToolCatalogSyncCommand:
    """One bounded catalog refresh tied to a specific persisted server generation."""

    tenant_id: str
    server_name: str
    observed_generation: int
    transport: str
    endpoint: str | None
    credentialed: bool
    actor_id: str
    observed_attempt: int | None = None


@dataclass(frozen=True)
class McpToolCatalogSyncResult:
    """Safe durable outcome of a catalog synchronization attempt."""

    status: str
    reason: str | None
    catalog_revision: int
    discovered_count: int
    selectable_count: int
    published: bool

    def public_payload(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "reason": self.reason,
            "catalog_revision": self.catalog_revision,
            "discovered_count": self.discovered_count,
            "selectable_count": self.selectable_count,
            "published": self.published,
        }


class McpToolDiscoveryAdapter(Protocol):
    """Discover every tool page from one already-authorized remote MCP transport."""

    async def discover(self, endpoint: str) -> tuple[McpDiscoveredTool, ...]:
        """Return the complete remote tool manifest or raise a bounded discovery error."""


def _safe_discovery_endpoint(endpoint: str | None) -> str | None:
    if not endpoint:
        return None
    parsed = urlsplit(endpoint)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        return None
    return endpoint


def _canonical_tool(raw: Any) -> McpDiscoveredTool:
    if not isinstance(raw, dict):
        raise McpToolDiscoveryError("protocol_error")
    remote_name = str(raw.get("name") or "").strip()
    if not SAFE_ID_PATTERN.fullmatch(remote_name):
        raise McpToolDiscoveryError("protocol_error")
    label = sanitize_public_text(raw.get("title") or remote_name)[:120] or remote_name
    description = sanitize_public_text(raw.get("description"))[:500]
    input_schema = raw.get("inputSchema")
    if input_schema is not None and not isinstance(input_schema, dict):
        raise McpToolDiscoveryError("protocol_error")
    schema_json = json.dumps(input_schema or {}, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    annotations = raw.get("annotations")
    read_only = isinstance(annotations, dict) and annotations.get("readOnlyHint") is True
    return McpDiscoveredTool(
        remote_name=remote_name,
        label=label,
        description=description,
        schema_hash=hashlib.sha256(schema_json.encode("utf-8")).hexdigest(),
        read_only=read_only,
    )


def _json_rpc_result(response: httpx.Response) -> dict[str, Any]:
    if response.status_code >= 400:
        raise McpToolDiscoveryError("transport_failure")
    try:
        content_type = response.headers.get("content-type", "")
        if "text/event-stream" in content_type:
            payloads = [
                json.loads(line[5:].strip())
                for line in response.text.splitlines()
                if line.startswith("data:") and line[5:].strip()
            ]
            payload = next((item for item in payloads if isinstance(item, dict) and "result" in item), None)
            if payload is None:
                raise ValueError("missing result")
        else:
            payload = response.json()
    except (ValueError, json.JSONDecodeError) as exc:
        raise McpToolDiscoveryError("protocol_error") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("result"), dict):
        raise McpToolDiscoveryError("protocol_error")
    return payload["result"]


class StreamableHttpMcpToolDiscoveryAdapter:
    """Use the credential-free Streamable HTTP MCP transport for complete tool discovery."""

    def __init__(self, *, timeout_seconds: float = 10.0) -> None:
        self._timeout_seconds = timeout_seconds

    async def _request(
        self,
        client: httpx.AsyncClient,
        endpoint: str,
        payload: dict[str, Any],
        *,
        session_id: str | None = None,
    ) -> tuple[dict[str, Any], str | None]:
        headers = {"Accept": "application/json, text/event-stream"}
        if session_id:
            headers["Mcp-Session-Id"] = session_id
        try:
            response = await client.post(endpoint, json=payload, headers=headers)
        except httpx.HTTPError as exc:
            raise McpToolDiscoveryError("transport_failure") from exc
        return _json_rpc_result(response), response.headers.get("mcp-session-id") or session_id

    async def discover(self, endpoint: str) -> tuple[McpDiscoveredTool, ...]:
        safe_endpoint = _safe_discovery_endpoint(endpoint)
        if safe_endpoint is None:
            raise McpToolDiscoveryError("invalid_endpoint")
        async with httpx.AsyncClient(timeout=self._timeout_seconds, follow_redirects=False) as client:
            initialize, session_id = await self._request(
                client,
                safe_endpoint,
                {
                    "jsonrpc": "2.0",
                    "id": "ai-platform-catalog-initialize",
                    "method": "initialize",
                    "params": {
                        "protocolVersion": MCP_PROTOCOL_VERSION,
                        "capabilities": {},
                        "clientInfo": {"name": "ai-platform", "version": "1"},
                    },
                },
            )
            if not isinstance(initialize.get("protocolVersion"), str):
                raise McpToolDiscoveryError("protocol_error")
            try:
                initialized = await client.post(
                    safe_endpoint,
                    json={"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
                    headers={
                        "Accept": "application/json, text/event-stream",
                        **({"Mcp-Session-Id": session_id} if session_id else {}),
                    },
                )
            except httpx.HTTPError as exc:
                raise McpToolDiscoveryError("transport_failure") from exc
            if initialized.status_code >= 400:
                raise McpToolDiscoveryError("transport_failure")

            cursor: str | None = None
            seen_cursors: set[str] = set()
            tools: list[McpDiscoveredTool] = []
            seen_names: set[str] = set()
            for page_number in range(MCP_DISCOVERY_PAGE_LIMIT):
                params: dict[str, Any] = {}
                if cursor is not None:
                    params["cursor"] = cursor
                result, session_id = await self._request(
                    client,
                    safe_endpoint,
                    {
                        "jsonrpc": "2.0",
                        "id": f"ai-platform-catalog-tools-{page_number}",
                        "method": "tools/list",
                        "params": params,
                    },
                    session_id=session_id,
                )
                raw_tools = result.get("tools")
                if not isinstance(raw_tools, list):
                    raise McpToolDiscoveryError("protocol_error")
                page_tools = [_canonical_tool(item) for item in raw_tools]
                if any(tool.remote_name in seen_names for tool in page_tools):
                    raise McpToolDiscoveryError("protocol_error")
                seen_names.update(tool.remote_name for tool in page_tools)
                tools.extend(page_tools)
                next_cursor = result.get("nextCursor")
                if next_cursor is None:
                    return tuple(tools)
                if not isinstance(next_cursor, str) or not next_cursor or next_cursor in seen_cursors:
                    raise McpToolDiscoveryError("protocol_error")
                seen_cursors.add(next_cursor)
                cursor = next_cursor
        raise McpToolDiscoveryError("page_limit_exceeded")


class McpToolCatalogSynchronizer:
    """Publish a complete, generation-fenced MCP tool catalog through one deep seam."""

    def __init__(self, *, discovery: McpToolDiscoveryAdapter | None = None) -> None:
        self._discovery = discovery or StreamableHttpMcpToolDiscoveryAdapter()

    async def synchronize(self, command: McpToolCatalogSyncCommand) -> McpToolCatalogSyncResult:
        """Discover before transaction, then atomically publish only for the observed server generation."""

        async with transaction() as conn:
            await _ensure_catalog_actor(conn, command)
            started = await repositories.begin_mcp_catalog_sync(
                conn,
                tenant_id=command.tenant_id,
                server_name=command.server_name,
                observed_generation=command.observed_generation,
                actor_id=command.actor_id,
            )
        if not bool(started.get("started")):
            return _result_from_row(started, published=False)
        command = replace(command, observed_attempt=int(started["catalog_sync_attempt"]))
        reason = self._preflight_reason(command)
        if reason is not None:
            async with transaction() as conn:
                await _ensure_catalog_actor(conn, command)
                row = await repositories.record_mcp_catalog_sync_outcome(
                    conn,
                    tenant_id=command.tenant_id,
                    server_name=command.server_name,
                    observed_generation=command.observed_generation,
                    observed_attempt=int(command.observed_attempt or 0),
                    status="unavailable",
                    reason=reason,
                    actor_id=command.actor_id,
                )
            return _result_from_row(row, published=False)
        try:
            tools = await self._discovery.discover(command.endpoint or "")
        except McpToolDiscoveryError as exc:
            async with transaction() as conn:
                await _ensure_catalog_actor(conn, command)
                row = await repositories.record_mcp_catalog_sync_outcome(
                    conn,
                    tenant_id=command.tenant_id,
                    server_name=command.server_name,
                    observed_generation=command.observed_generation,
                    observed_attempt=int(command.observed_attempt or 0),
                    status="unavailable",
                    reason=exc.reason,
                    actor_id=command.actor_id,
                )
            return _result_from_row(row, published=False)
        async with transaction() as conn:
            await _ensure_catalog_actor(conn, command)
            row = await repositories.publish_mcp_tool_catalog(
                conn,
                tenant_id=command.tenant_id,
                server_name=command.server_name,
                observed_generation=command.observed_generation,
                observed_attempt=int(command.observed_attempt or 0),
                endpoint=command.endpoint or "",
                tools=tools,
                actor_id=command.actor_id,
            )
        return _result_from_row(row, published=bool(row.get("published")))

    @staticmethod
    def _preflight_reason(command: McpToolCatalogSyncCommand) -> str | None:
        if command.transport != "streamable_http":
            return "unsupported_transport"
        if command.credentialed:
            return "credentials_not_supported"
        if _safe_discovery_endpoint(command.endpoint) is None:
            return "invalid_endpoint"
        return None


async def _ensure_catalog_actor(conn: Any, command: McpToolCatalogSyncCommand) -> None:
    await repositories.ensure_user(
        conn,
        tenant_id=command.tenant_id,
        user_id=command.actor_id,
        display_name=command.actor_id,
    )


def _result_from_row(row: dict[str, Any], *, published: bool) -> McpToolCatalogSyncResult:
    return McpToolCatalogSyncResult(
        status=str(row.get("catalog_status") or "unavailable"),
        reason=(str(row["catalog_unavailable_reason"]) if row.get("catalog_unavailable_reason") else None),
        catalog_revision=int(row.get("catalog_revision") or 0),
        discovered_count=int(row.get("catalog_discovered_count") or 0),
        selectable_count=int(row.get("catalog_selectable_count") or 0),
        published=published,
    )
