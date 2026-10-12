from __future__ import annotations

import ipaddress
import socket
import asyncio
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from app.mcp.domain.errors import McpToolDiscoveryError
from app.mcp.domain.tool_references import MCP_PUBLIC_TOOL_NAME_PATTERN


MCP_DISCOVERY_PAGE_LIMIT = 100
MCP_TOOL_DESCRIPTION_MAX_LENGTH = 2048


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


def _canonical_live_definition(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise McpToolDiscoveryError("protocol_error")
    remote_name = raw.get("name")
    if not isinstance(remote_name, str) or not MCP_PUBLIC_TOOL_NAME_PATTERN.fullmatch(remote_name):
        raise McpToolDiscoveryError("protocol_error")
    description = raw.get("description")
    if description is None:
        description = ""
    if not isinstance(description, str):
        raise McpToolDiscoveryError("protocol_error")
    input_schema = raw.get("inputSchema")
    if input_schema is not None and not isinstance(input_schema, dict):
        raise McpToolDiscoveryError("protocol_error")
    annotations = raw.get("annotations")
    if annotations is not None and not isinstance(annotations, dict):
        raise McpToolDiscoveryError("protocol_error")
    return {
        "name": remote_name,
        "description": description[:MCP_TOOL_DESCRIPTION_MAX_LENGTH],
        "inputSchema": dict(input_schema or {}),
        "annotations": dict(annotations or {}),
    }
