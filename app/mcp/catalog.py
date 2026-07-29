from __future__ import annotations

import hashlib
import ipaddress
import json
import socket
import asyncio
from dataclasses import dataclass, replace
from typing import Any, Protocol
from urllib.parse import urlsplit

import httpx

from app.validation import SAFE_ID_PATTERN


MCP_DISCOVERY_PAGE_LIMIT = 100
MCP_PROTOCOL_VERSION = "2025-03-26"
MCP_PUBLIC_TOOL_LABEL = "受管 MCP 工具"
MCP_PUBLIC_TOOL_DESCRIPTION = "由平台治理的工具。"
MCP_PUBLIC_UNAVAILABLE_LABEL = "已配置 MCP 服务"


class McpToolDiscoveryError(ValueError):
    """Represent a bounded, public-safe MCP discovery failure category."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class McpDiscoveredTool:
    """Private canonical tool identity retained only long enough to publish a catalog."""

    remote_name: str
    schema_hash: str
    read_only: bool


@dataclass(frozen=True)
class McpToolCatalogSyncCommand:
    """One bounded catalog refresh tied to a persisted server generation."""

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
        """Return the public lifecycle state without transport or policy internals."""

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
        """Return the complete remote manifest or raise a bounded discovery error."""


class McpCatalogStore(Protocol):
    """Persist catalog state through the generation-fenced catalog seam."""

    async def begin(self, command: McpToolCatalogSyncCommand) -> dict[str, Any]:
        """Claim the observed generation before discovery."""

    async def record_outcome(
        self,
        command: McpToolCatalogSyncCommand,
        *,
        observed_attempt: int,
        reason: str,
    ) -> dict[str, Any]:
        """Durably record a retryable unavailable result."""

    async def publish(
        self,
        command: McpToolCatalogSyncCommand,
        *,
        observed_attempt: int,
        tools: tuple[McpDiscoveredTool, ...],
    ) -> dict[str, Any]:
        """Atomically publish one complete manifest or fail closed."""


def _parsed_discovery_endpoint(endpoint: str | None):
    if not endpoint:
        return None
    try:
        parsed = urlsplit(endpoint)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or not hostname:
        return None
    if (
        parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or port is not None and not 1 <= port <= 65535
    ):
        return None
    return parsed


def _is_rfc1918(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return isinstance(address, ipaddress.IPv4Address) and any(
        address in network
        for network in (
            ipaddress.ip_network("10.0.0.0/8"),
            ipaddress.ip_network("172.16.0.0/12"),
            ipaddress.ip_network("192.168.0.0/16"),
        )
    )


def _address_is_permitted(address: ipaddress.IPv4Address | ipaddress.IPv6Address, *, scheme: str) -> bool:
    if (
        address.is_loopback
        or address.is_link_local
        or address.is_unspecified
        or address.is_multicast
        or address.is_reserved
        or isinstance(address, ipaddress.IPv4Address) and int(address) == 0xFFFFFFFF
    ):
        return False
    # The intranet exception is deliberately narrower than ``is_private``:
    # ULA, CGNAT, and other non-public ranges are not discovery targets.
    return _is_rfc1918(address) or (scheme == "https" and address.is_global)


async def _resolve_discovery_addresses(hostname: str, port: int) -> tuple[ipaddress.IPv4Address | ipaddress.IPv6Address, ...]:
    """Resolve every address that the request hostname may use before dispatch."""

    try:
        literal = ipaddress.ip_address(hostname)
    except ValueError:
        if hostname.casefold() == "localhost" or hostname.casefold().endswith(".localhost"):
            raise McpToolDiscoveryError("invalid_endpoint")
        try:
            records = await asyncio.get_running_loop().getaddrinfo(
                hostname,
                port,
                type=socket.SOCK_STREAM,
            )
        except OSError as exc:
            raise McpToolDiscoveryError("invalid_endpoint") from exc
        addresses: set[ipaddress.IPv4Address | ipaddress.IPv6Address] = set()
        for _, _, _, _, sockaddr in records:
            try:
                addresses.add(ipaddress.ip_address(str(sockaddr[0])))
            except ValueError as exc:
                raise McpToolDiscoveryError("invalid_endpoint") from exc
        if not addresses:
            raise McpToolDiscoveryError("invalid_endpoint")
        return tuple(addresses)
    return (literal,)


async def _validated_discovery_endpoint(endpoint: str | None) -> str:
    parsed = _parsed_discovery_endpoint(endpoint)
    if parsed is None:
        raise McpToolDiscoveryError("invalid_endpoint")
    addresses = await _resolve_discovery_addresses(
        parsed.hostname or "",
        parsed.port or (443 if parsed.scheme == "https" else 80),
    )
    if not all(_address_is_permitted(address, scheme=parsed.scheme) for address in addresses):
        raise McpToolDiscoveryError("invalid_endpoint")
    if not (
        all(_is_rfc1918(address) for address in addresses)
        or (parsed.scheme == "https" and all(address.is_global for address in addresses))
    ):
        raise McpToolDiscoveryError("invalid_endpoint")
    return endpoint or ""


def _canonical_tool(raw: Any) -> McpDiscoveredTool:
    if not isinstance(raw, dict):
        raise McpToolDiscoveryError("protocol_error")
    remote_name = str(raw.get("name") or "").strip()
    if not SAFE_ID_PATTERN.fullmatch(remote_name):
        raise McpToolDiscoveryError("protocol_error")
    input_schema = raw.get("inputSchema")
    if input_schema is not None and not isinstance(input_schema, dict):
        raise McpToolDiscoveryError("protocol_error")
    schema_json = json.dumps(input_schema or {}, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    annotations = raw.get("annotations")
    return McpDiscoveredTool(
        remote_name=remote_name,
        schema_hash=hashlib.sha256(schema_json.encode("utf-8")).hexdigest(),
        read_only=isinstance(annotations, dict) and annotations.get("readOnlyHint") is True,
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
        safe_endpoint = await _validated_discovery_endpoint(endpoint)
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
                params: dict[str, Any] = {} if cursor is None else {"cursor": cursor}
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
    """Publish complete generation-fenced MCP manifests through one deep seam."""

    def __init__(
        self,
        *,
        discovery: McpToolDiscoveryAdapter | None = None,
        store: McpCatalogStore | None = None,
    ) -> None:
        if store is None:
            from app.mcp.repository import PostgresMcpCatalogStore

            store = PostgresMcpCatalogStore()
        self._discovery = discovery or StreamableHttpMcpToolDiscoveryAdapter()
        self._store = store

    async def synchronize(self, command: McpToolCatalogSyncCommand) -> McpToolCatalogSyncResult:
        """Discover outside SQL, then publish only for the claimed generation and attempt."""

        started = await self._store.begin(command)
        if not bool(started.get("started")):
            return _result_from_row(started, published=False)
        attempt = int(started["catalog_sync_attempt"])
        command = replace(command, observed_attempt=attempt)
        reason = self._preflight_reason(command)
        if reason is not None:
            row = await self._store.record_outcome(command, observed_attempt=attempt, reason=reason)
            return _result_from_row(row, published=False)
        try:
            tools = await self._discovery.discover(command.endpoint or "")
        except McpToolDiscoveryError as exc:
            row = await self._store.record_outcome(command, observed_attempt=attempt, reason=exc.reason)
            return _result_from_row(row, published=False)
        except BaseException:
            # A best-effort durable outcome makes cancellation retryable; lease expiry
            # remains the process-loss recovery path when this transaction cannot run.
            try:
                await asyncio.shield(
                    self._store.record_outcome(
                        command,
                        observed_attempt=attempt,
                        reason="discovery_aborted",
                    )
                )
            except Exception:
                pass
            raise
        row = await self._store.publish(command, observed_attempt=attempt, tools=tools)
        return _result_from_row(row, published=bool(row.get("published")))

    @staticmethod
    def _preflight_reason(command: McpToolCatalogSyncCommand) -> str | None:
        if command.transport != "streamable_http":
            return "unsupported_transport"
        if command.credentialed:
            return "credentials_not_supported"
        if _parsed_discovery_endpoint(command.endpoint) is None:
            return "invalid_endpoint"
        return None


def _result_from_row(row: dict[str, Any], *, published: bool) -> McpToolCatalogSyncResult:
    return McpToolCatalogSyncResult(
        status=str(row.get("catalog_status") or "unavailable"),
        reason=(str(row["catalog_unavailable_reason"]) if row.get("catalog_unavailable_reason") else None),
        catalog_revision=int(row.get("catalog_revision") or 0),
        discovered_count=int(row.get("catalog_discovered_count") or 0),
        selectable_count=int(row.get("catalog_selectable_count") or 0),
        published=published,
    )
