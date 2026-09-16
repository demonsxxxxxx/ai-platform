"""Bounded HTTP/SSE MCP connections with pinned, credential-safe destinations."""

from __future__ import annotations

import asyncio
import re
from contextlib import AsyncExitStack, asynccontextmanager
from datetime import timedelta

import httpx
from mcp import ClientSession
from mcp.client.sse import sse_client
from mcp.client.streamable_http import streamable_http_client

from app.mcp.domain.headers import normalize_static_mcp_headers
from app.mcp.infrastructure.catalog import (
    MCP_DISCOVERY_PAGE_LIMIT,
    McpToolDiscoveryError,
    StreamableHttpMcpToolDiscoveryAdapter,
    _canonical_live_definition,
    _validated_discovery_target,
)


class _BoundedMcpStream(httpx.AsyncByteStream):
    def __init__(self, stream, *, event_stream: bool, limit: int):
        self._stream = stream
        self._event_stream = event_stream
        self._limit = limit

    async def __aiter__(self):
        pending = b""
        async for chunk in self._stream:
            pending += chunk
            frames = re.split(br"\r\n\r\n|\n\n|\r\r", pending) if self._event_stream else [pending]
            if any(len(frame) > self._limit for frame in frames):
                raise McpToolDiscoveryError("response_too_large")
            pending = frames[-1]
            yield chunk

    async def aclose(self):
        await self._stream.aclose()


@asynccontextmanager
async def open_mcp_session(config, *, timeout_seconds=60.0, max_response_bytes=1024 * 1024):
    """Keep credentials on one approved origin, including SSE message endpoints."""
    transport = config.get("type")
    if transport not in {"http", "sse"}:
        raise McpToolDiscoveryError("unsupported_transport")
    try:
        target = await _validated_discovery_target(config.get("url"))
    except Exception:
        raise McpToolDiscoveryError("invalid_endpoint") from None
    raw_headers = dict(config.get("headers") or {})
    jwt_keys = [key for key in raw_headers if key.casefold() == "jwt-authorization"]
    if len(jwt_keys) != 1:
        raise McpToolDiscoveryError("authorization_required")
    jwt = raw_headers.pop(jwt_keys[0])
    headers = StreamableHttpMcpToolDiscoveryAdapter._headers(
        target, static_headers=raw_headers, jwt_authorization=jwt
    )
    headers["Accept-Encoding"] = "identity"
    origin = httpx.URL(target.endpoint)
    pinned = httpx.URL(target.connect_url)

    async def pin_request(request):
        if (request.url.scheme, request.url.host, request.url.port) != (
            origin.scheme, origin.host, origin.port
        ):
            raise McpToolDiscoveryError("invalid_endpoint")
        request.url = request.url.copy_with(host=pinned.host)
        request.headers["Host"] = target.host_header
        request.extensions["sni_hostname"] = target.sni_hostname

    async def bound_response(response):
        response.raise_for_status()
        if response.headers.get("content-encoding", "identity").strip().lower() != "identity":
            raise McpToolDiscoveryError("unsupported_content_encoding")
        response.stream = _BoundedMcpStream(
            response.stream,
            event_stream="text/event-stream" in response.headers.get("content-type", ""),
            limit=max_response_bytes,
        )

    def client_factory(**kwargs):
        # Redirects must never forward the user's JWT to a new destination.
        return httpx.AsyncClient(
            **kwargs, follow_redirects=False, trust_env=False,
            event_hooks={"request": [pin_request], "response": [bound_response]},
        )

    try:
        async with AsyncExitStack() as stack:
            # Bound the handshake too: the SSE client can otherwise wait for an
            # endpoint forever after its reader rejects an early malformed frame.
            async with asyncio.timeout(timeout_seconds):
                if transport == "sse":
                    streams = await stack.enter_async_context(sse_client(
                        target.endpoint, headers=headers, timeout=timeout_seconds,
                        sse_read_timeout=timeout_seconds, httpx_client_factory=client_factory,
                    ))
                else:
                    http_client = await stack.enter_async_context(client_factory(headers=headers, timeout=timeout_seconds))
                    streams = await stack.enter_async_context(streamable_http_client(target.endpoint, http_client=http_client))
                session = await stack.enter_async_context(ClientSession(
                    *streams[:2], read_timeout_seconds=timedelta(seconds=timeout_seconds),
                ))
                await session.initialize()
            yield session
    except McpToolDiscoveryError:
        raise
    except Exception:
        # Transport/SDK exceptions can contain endpoints and credential headers.
        raise McpToolDiscoveryError("connection_failed") from None


async def list_mcp_tools(session):
    """Return the complete current catalog or fail; never a truncated selection."""
    tools = []
    names = set()
    cursors = set()
    cursor = None
    for _ in range(MCP_DISCOVERY_PAGE_LIMIT):
        page = await session.list_tools(cursor=cursor)
        for tool in page.tools:
            _canonical_live_definition(tool.model_dump(by_alias=True, exclude_none=True))
            if tool.name in names:
                raise McpToolDiscoveryError("protocol_error")
            names.add(tool.name)
            tools.append(tool)
        cursor = page.nextCursor
        if cursor is None:
            return tools
        if not cursor or cursor in cursors:
            raise McpToolDiscoveryError("protocol_error")
        cursors.add(cursor)
    raise McpToolDiscoveryError("page_limit_exceeded")


class SseMcpToolDiscoveryAdapter:
    async def discover_definitions(self, endpoint, *, static_headers=None, jwt_authorization):
        async with asyncio.timeout(10):
            async with open_mcp_session({
                "type": "sse", "url": endpoint,
                "headers": {**normalize_static_mcp_headers(static_headers), "JWT-Authorization": jwt_authorization},
            }, timeout_seconds=10) as session:
                return tuple(
                    _canonical_live_definition(tool.model_dump(by_alias=True, exclude_none=True))
                    for tool in await list_mcp_tools(session)
                )
