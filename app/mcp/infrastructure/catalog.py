from __future__ import annotations

import hashlib
import ipaddress
import json
import socket
import asyncio
from dataclasses import dataclass
from typing import Any, Mapping
from urllib.parse import urlsplit, urlunsplit

import httpx

from app.mcp.domain.headers import (
    MCP_JWT_AUTHORIZATION_HEADER,
    normalize_static_mcp_headers,
)
from app.mcp.domain.tool_references import MCP_PUBLIC_TOOL_NAME_PATTERN


MCP_DISCOVERY_PAGE_LIMIT = 100
MCP_PROTOCOL_VERSION = "2025-03-26"
MCP_TOOL_ANNOTATION_READ_ONLY = "read_only"
MCP_TOOL_ANNOTATION_WRITE_CAPABLE = "write_capable"
MCP_TOOL_ANNOTATION_UNKNOWN = "unknown"
MCP_TOOL_ANNOTATION_STATES = frozenset(
    {
        MCP_TOOL_ANNOTATION_READ_ONLY,
        MCP_TOOL_ANNOTATION_WRITE_CAPABLE,
        MCP_TOOL_ANNOTATION_UNKNOWN,
    }
)


class McpToolDiscoveryError(ValueError):
    """Represent a bounded, public-safe MCP discovery failure category."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class McpDiscoveredTool:
    """Ephemeral canonical tool metadata returned by one Gateway discovery."""

    remote_name: str
    schema_hash: str
    read_only: bool
    annotation_state: str | None = None

    def __post_init__(self) -> None:
        state = self.annotation_state or (
            MCP_TOOL_ANNOTATION_READ_ONLY if self.read_only else MCP_TOOL_ANNOTATION_WRITE_CAPABLE
        )
        if state not in MCP_TOOL_ANNOTATION_STATES:
            raise ValueError("mcp_tool_annotation_state_invalid")
        object.__setattr__(self, "annotation_state", state)
        object.__setattr__(self, "read_only", state == MCP_TOOL_ANNOTATION_READ_ONLY)

    @property
    def write_capable(self) -> bool:
        """Treat an absent advisory annotation conservatively without claiming a read-only contract."""

        return self.annotation_state != MCP_TOOL_ANNOTATION_READ_ONLY

    @property
    def risk_level(self) -> str:
        return "low" if self.read_only else "high"

    @property
    def catalog_policy_reason(self) -> str:
        if self.annotation_state == MCP_TOOL_ANNOTATION_READ_ONLY:
            return "mcp_catalog_read_only"
        if self.annotation_state == MCP_TOOL_ANNOTATION_WRITE_CAPABLE:
            return "mcp_catalog_write_capable"
        return "mcp_catalog_annotation_unknown"


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


@dataclass(frozen=True)
class _ValidatedDiscoveryTarget:
    endpoint: str
    connect_url: str
    host_header: str
    sni_hostname: str


async def _validated_discovery_target(endpoint: str | None) -> _ValidatedDiscoveryTarget:
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
    selected = min(addresses, key=lambda address: (address.version, int(address)))
    connect_hostname = (
        f"[{selected.compressed}]"
        if isinstance(selected, ipaddress.IPv6Address)
        else selected.compressed
    )
    connect_netloc = connect_hostname
    if parsed.port is not None:
        connect_netloc = f"{connect_netloc}:{parsed.port}"
    normalized_endpoint = urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path or "/", "", "")
    )
    return _ValidatedDiscoveryTarget(
        endpoint=normalized_endpoint,
        connect_url=urlunsplit(
            (parsed.scheme, connect_netloc, parsed.path or "/", "", "")
        ),
        host_header=parsed.netloc,
        sni_hostname=parsed.hostname or "",
    )


async def _validated_discovery_endpoint(endpoint: str | None) -> str:
    return (await _validated_discovery_target(endpoint)).endpoint


def _normalized_jwt_authorization(value: str | None) -> str:
    raw = str(value or "").strip()
    scheme, separator, token = raw.partition(" ")
    token = token.strip()
    if (
        not separator
        or scheme.casefold() != "bearer"
        or not token
        or len(token) > 16_384
        or any(not 0x21 <= ord(character) <= 0x7E for character in token)
    ):
        raise McpToolDiscoveryError("authorization_required")
    return f"Bearer {token}"


def _annotation_state(annotations: Any) -> str:
    """Classify optional advisory annotations without converting absence into a read-only assertion."""

    if not isinstance(annotations, dict):
        return MCP_TOOL_ANNOTATION_UNKNOWN
    if annotations.get("readOnlyHint") is False or annotations.get("destructiveHint") is True:
        return MCP_TOOL_ANNOTATION_WRITE_CAPABLE
    if annotations.get("readOnlyHint") is True:
        return MCP_TOOL_ANNOTATION_READ_ONLY
    return MCP_TOOL_ANNOTATION_UNKNOWN


def _canonical_tool(raw: Any) -> McpDiscoveredTool:
    if not isinstance(raw, dict):
        raise McpToolDiscoveryError("protocol_error")
    remote_name = str(raw.get("name") or "").strip()
    if not MCP_PUBLIC_TOOL_NAME_PATTERN.fullmatch(remote_name):
        raise McpToolDiscoveryError("protocol_error")
    input_schema = raw.get("inputSchema")
    if input_schema is not None and not isinstance(input_schema, dict):
        raise McpToolDiscoveryError("protocol_error")
    schema_json = json.dumps(input_schema or {}, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    annotation_state = _annotation_state(raw.get("annotations"))
    return McpDiscoveredTool(
        remote_name=remote_name,
        schema_hash=hashlib.sha256(schema_json.encode("utf-8")).hexdigest(),
        read_only=annotation_state == MCP_TOOL_ANNOTATION_READ_ONLY,
        annotation_state=annotation_state,
    )


def _canonical_live_definition(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise McpToolDiscoveryError("protocol_error")
    remote_name = str(raw.get("name") or "").strip()
    if not MCP_PUBLIC_TOOL_NAME_PATTERN.fullmatch(remote_name):
        raise McpToolDiscoveryError("protocol_error")
    description = raw.get("description")
    if description is None:
        description = ""
    if not isinstance(description, str) or len(description) > 2048:
        raise McpToolDiscoveryError("protocol_error")
    input_schema = raw.get("inputSchema")
    if input_schema is not None and not isinstance(input_schema, dict):
        raise McpToolDiscoveryError("protocol_error")
    annotations = raw.get("annotations")
    if annotations is not None and not isinstance(annotations, dict):
        raise McpToolDiscoveryError("protocol_error")
    return {
        "name": remote_name,
        "description": description,
        "inputSchema": dict(input_schema or {}),
        "annotations": dict(annotations or {}),
    }


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

    def __init__(
        self,
        *,
        timeout_seconds: float = 10.0,
        max_response_bytes: int = 1024 * 1024,
    ) -> None:
        self._timeout_seconds = timeout_seconds
        self._max_response_bytes = max(1, int(max_response_bytes))

    def _response_limit(self) -> int:
        return self._max_response_bytes

    @staticmethod
    def _headers(
        target: _ValidatedDiscoveryTarget,
        *,
        static_headers: Mapping[str, str] | None,
        jwt_authorization: str,
        session_id: str | None = None,
    ) -> dict[str, str]:
        headers = {
            **normalize_static_mcp_headers(static_headers),
            "Accept": "application/json, text/event-stream",
            "Host": target.host_header,
            MCP_JWT_AUTHORIZATION_HEADER: _normalized_jwt_authorization(
                jwt_authorization
            ),
        }
        if session_id:
            headers["Mcp-Session-Id"] = session_id
        return headers

    async def _post(
        self,
        client: httpx.AsyncClient,
        target: _ValidatedDiscoveryTarget,
        payload: dict[str, Any],
        *,
        session_id: str | None = None,
        static_headers: Mapping[str, str] | None = None,
        jwt_authorization: str,
    ) -> httpx.Response:
        try:
            async with client.stream(
                "POST",
                target.connect_url,
                json=payload,
                headers=self._headers(
                    target,
                    static_headers=static_headers,
                    jwt_authorization=jwt_authorization,
                    session_id=session_id,
                ),
                extensions={"sni_hostname": target.sni_hostname},
            ) as streamed_response:
                content = bytearray()
                async for chunk in streamed_response.aiter_bytes():
                    content.extend(chunk)
                    if len(content) > self._response_limit():
                        raise McpToolDiscoveryError("response_too_large")
                return httpx.Response(
                    streamed_response.status_code,
                    headers=streamed_response.headers,
                    content=bytes(content),
                    request=streamed_response.request,
                    extensions=streamed_response.extensions,
                )
        except McpToolDiscoveryError:
            raise
        except httpx.HTTPError as exc:
            raise McpToolDiscoveryError("transport_failure") from exc

    async def _request(
        self,
        client: httpx.AsyncClient,
        target: _ValidatedDiscoveryTarget,
        payload: dict[str, Any],
        *,
        session_id: str | None = None,
        static_headers: Mapping[str, str] | None = None,
        jwt_authorization: str,
    ) -> tuple[dict[str, Any], str | None]:
        response = await self._post(
            client,
            target,
            payload,
            session_id=session_id,
            static_headers=static_headers,
            jwt_authorization=jwt_authorization,
        )
        return _json_rpc_result(response), response.headers.get("mcp-session-id") or session_id

    async def discover(
        self,
        endpoint: str,
        *,
        static_headers: Mapping[str, str] | None = None,
        jwt_authorization: str,
    ) -> tuple[McpDiscoveredTool, ...]:
        definitions = await self.discover_definitions(
            endpoint,
            static_headers=static_headers,
            jwt_authorization=jwt_authorization,
        )
        return tuple(_canonical_tool(tool) for tool in definitions)

    async def discover_definitions(
        self,
        endpoint: str,
        *,
        static_headers: Mapping[str, str] | None = None,
        jwt_authorization: str,
    ) -> tuple[dict[str, Any], ...]:
        """Return bounded user-effective definitions without persisting a catalog."""

        target = await _validated_discovery_target(endpoint)
        async with httpx.AsyncClient(timeout=self._timeout_seconds, follow_redirects=False) as client:
            initialize, session_id = await self._request(
                client,
                target,
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
                static_headers=static_headers,
                jwt_authorization=jwt_authorization,
            )
            if not isinstance(initialize.get("protocolVersion"), str):
                raise McpToolDiscoveryError("protocol_error")
            initialized = await self._post(
                client,
                target,
                {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
                session_id=session_id,
                static_headers=static_headers,
                jwt_authorization=jwt_authorization,
            )
            if initialized.status_code >= 400:
                raise McpToolDiscoveryError("transport_failure")

            cursor: str | None = None
            seen_cursors: set[str] = set()
            tools: list[dict[str, Any]] = []
            seen_names: set[str] = set()
            for page_number in range(MCP_DISCOVERY_PAGE_LIMIT):
                params: dict[str, Any] = {} if cursor is None else {"cursor": cursor}
                result, session_id = await self._request(
                    client,
                    target,
                    {
                        "jsonrpc": "2.0",
                        "id": f"ai-platform-catalog-tools-{page_number}",
                        "method": "tools/list",
                        "params": params,
                    },
                    session_id=session_id,
                    static_headers=static_headers,
                    jwt_authorization=jwt_authorization,
                )
                raw_tools = result.get("tools")
                if not isinstance(raw_tools, list):
                    raise McpToolDiscoveryError("protocol_error")
                page_tools = [_canonical_live_definition(item) for item in raw_tools]
                if any(tool["name"] in seen_names for tool in page_tools):
                    raise McpToolDiscoveryError("protocol_error")
                seen_names.update(tool["name"] for tool in page_tools)
                tools.extend(page_tools)
                next_cursor = result.get("nextCursor")
                if next_cursor is None:
                    return tuple(tools)
                if not isinstance(next_cursor, str) or not next_cursor or next_cursor in seen_cursors:
                    raise McpToolDiscoveryError("protocol_error")
                seen_cursors.add(next_cursor)
                cursor = next_cursor
        raise McpToolDiscoveryError("page_limit_exceeded")
