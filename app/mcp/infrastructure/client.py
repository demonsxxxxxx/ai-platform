"""Bounded HTTP/SSE MCP connections with pinned, credential-safe destinations."""

from __future__ import annotations

import asyncio
import re
from builtins import ExceptionGroup
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Any, Mapping

from types import SimpleNamespace

import httpx2 as httpx
from mcp import Client, MCPError
from mcp.client.sse import sse_client
from mcp.client.streamable_http import streamable_http_client
from mcp_types import ListToolsResult
from pydantic import ValidationError

from app.mcp.domain.errors import McpRuntimeContextError, McpToolDiscoveryError
from app.mcp.domain.headers import mcp_user_jwt_header, normalize_static_mcp_headers
from app.mcp.infrastructure import runtime as mcp_runtime
from app.mcp.infrastructure.catalog import (
    MCP_DISCOVERY_PAGE_LIMIT,
    _canonical_live_definition,
    _ValidatedDiscoveryTarget,
    _validated_discovery_target,
)


def _mcp_headers(
    target: _ValidatedDiscoveryTarget,
    *,
    static_headers: Mapping[str, str] | None,
    jwt_authorization: str,
    integration_kind: str = "mcp",
) -> dict[str, str]:
    raw = str(jwt_authorization or "").strip()
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
    return {
        **normalize_static_mcp_headers(static_headers, integration_kind=integration_kind),
        "Accept": "application/json, text/event-stream",
        "Accept-Encoding": "identity",
        "Host": target.host_header,
        mcp_user_jwt_header(integration_kind): f"Bearer {token}",
    }


def _install_strict_tool_list_validation(client) -> None:
    """Reject coercible tool annotations before MCP types normalize them."""
    dispatcher = client.session._dispatcher
    send_raw_request = dispatcher.send_raw_request

    async def validate_tool_list(method, params, opts=None, *, _related_request_id=None):
        result = await send_raw_request(
            method, params, opts, _related_request_id=_related_request_id,
        )
        if method == "tools/list":
            try:
                # ClientSession validates this result again against its negotiated
                # protocol version; this pass only disables Pydantic coercion.
                ListToolsResult.model_validate(result, strict=True)
            except ValidationError:
                raise McpToolDiscoveryError("protocol_error") from None
        return result

    # The v2 Client owns negotiation and typed protocol handling. Its raw
    # dispatcher result is the narrow seam needed to retain strict authority
    # annotations, which the SDK's subsequent model validation otherwise coerces.
    dispatcher.send_raw_request = validate_tool_list


def _bounded_failure(exc: Exception) -> str | None:
    if isinstance(exc, McpToolDiscoveryError):
        return exc.reason
    if isinstance(exc, MCPError):
        return "protocol_error"
    if isinstance(exc, ExceptionGroup):
        for nested in exc.exceptions:
            reason = _bounded_failure(nested)
            if reason is not None:
                return reason
    return None


class _BoundedMcpStream(httpx.AsyncByteStream):
    def __init__(self, stream, *, event_stream: bool, limit: int, total_limit: int | None, fail):
        self._stream = stream
        self._event_stream = event_stream
        self._limit = limit
        self._total_limit = total_limit
        self._fail = fail
        self._pending = b""
        self._total = 0

    def check_chunk(self, chunk):
        self._total += len(chunk)
        if self._total_limit is not None and self._total > self._total_limit:
            self._fail("response_too_large")
        self._pending += chunk
        frames = re.split(br"\r\n\r\n|\n\n|\r\r", self._pending) if self._event_stream else [self._pending]
        if any(len(frame) > self._limit for frame in frames):
            self._fail("response_too_large")
        self._pending = frames[-1]

    async def __aiter__(self):
        async for chunk in self._stream:
            self.check_chunk(chunk)
            yield chunk

    async def aclose(self):
        await self._stream.aclose()


class _SingleDispatchMcpClient(Client):
    async def call_tool(self, name, arguments=None):
        # A native admission authorizes one dispatch. Keep v1's explicit-call
        # semantics instead of v2 Client's schema-refresh or input-required replay.
        return await self.session.call_tool(name, arguments)


@asynccontextmanager
async def open_mcp_session(
    config, *, timeout_seconds=60.0, max_response_bytes=1024 * 1024,
    max_response_total_bytes=None, terminate_on_close=True, credential_resolver=None,
):
    """Keep credentials on one approved origin, including SSE message endpoints."""
    transport = config.get("type")
    if transport not in {"http", "sse"}:
        raise McpToolDiscoveryError("unsupported_transport")
    try:
        target = await _validated_discovery_target(config.get("url"))
    except Exception:
        raise McpToolDiscoveryError("invalid_endpoint") from None
    raw_headers = dict(config.get("headers") or {})
    integration_kind = config.get("integration_kind", "mcp")
    jwt_header = mcp_user_jwt_header(integration_kind)
    jwt_keys = [key for key in raw_headers if key.casefold() == jwt_header.casefold()]
    if len(jwt_keys) != 1:
        raise McpToolDiscoveryError("authorization_required")
    jwt = raw_headers.pop(jwt_keys[0])
    headers = _mcp_headers(
        target, static_headers=raw_headers, jwt_authorization=jwt,
        integration_kind=integration_kind,
    )
    credential_subject = config.get("credential_subject")
    if credential_subject is not None and (not isinstance(credential_subject, dict)
        or set(credential_subject) - {"tenant_id", "user_id", "grant_id", "subject_epoch", "legacy_jwt_id"}
        or not isinstance(credential_subject.get("tenant_id"), str)
        or not isinstance(credential_subject.get("user_id"), str)):
        raise McpToolDiscoveryError("authorization_required")
    credential_principal = SimpleNamespace(**credential_subject) if credential_subject is not None else None
    origin = httpx.URL(target.endpoint)
    pinned = httpx.URL(target.connect_url)
    guard_failure = None

    def fail(reason):
        nonlocal guard_failure
        guard_failure = guard_failure or reason
        raise McpToolDiscoveryError(guard_failure)

    async def pin_request(request):
        # SDK background streams may catch an error and try to reconnect. A
        # rejected response must not permit another credential-bearing dispatch.
        if guard_failure:
            raise McpToolDiscoveryError(guard_failure)
        if (request.url.scheme, request.url.host, request.url.port) != (
            origin.scheme, origin.host, origin.port
        ):
            fail("invalid_endpoint")
        request.url = request.url.copy_with(host=pinned.host)
        request.headers["Host"] = target.host_header
        request.extensions["sni_hostname"] = target.sni_hostname
        if credential_principal is not None:
            try:
                current_jwt = (
                    await credential_resolver() if credential_resolver is not None
                    else await mcp_runtime.get_mcp_principal_jwt_store().get(credential_principal)
                )
                current_header = _mcp_headers(
                    target, static_headers={}, jwt_authorization=f"Bearer {current_jwt}",
                    integration_kind=integration_kind,
                )[jwt_header]
            except McpRuntimeContextError as exc:
                fail("authorization_required" if exc.status_code == 401 else "transport_failure")
            except McpToolDiscoveryError as exc:
                fail(exc.reason)
            except Exception:
                fail("transport_failure")
            # Only subsequent HTTP dispatches change headers; tool effects are never replayed.
            request.headers[jwt_header] = current_header

    async def bound_response(response):
        # HTTPX closes responses rejected by hooks, including unread bodies.
        if response.is_redirect:
            fail("transport_failure")
        # The official v2 transport maps non-2xx JSON-RPC errors and lets auto
        # negotiation fall back when an older endpoint rejects server/discover.
        if response.headers.get("content-encoding", "identity").strip().lower() != "identity":
            fail("unsupported_content_encoding")
        bounded_stream = _BoundedMcpStream(
            response.stream,
            event_stream="text/event-stream" in response.headers.get("content-type", "").lower(),
            limit=max_response_bytes, total_limit=max_response_total_bytes, fail=fail,
        )
        # HTTPX transports may return an already-buffered response. aread() and
        # aiter_bytes() then use cached content instead of the wrapped stream.
        if response.is_stream_consumed:
            bounded_stream.check_chunk(response.content)
        response.stream = bounded_stream

    async def validate_message(message):
        # The SDK reports malformed wire messages through this public callback;
        # its default handler ignores them. Never accept a later successful page
        # after the same response failed protocol validation.
        if isinstance(message, Exception):
            fail(_bounded_failure(message) or "protocol_error")

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
                    protocol_transport = sse_client(
                        target.endpoint, headers=headers, timeout=timeout_seconds,
                        sse_read_timeout=timeout_seconds, httpx_client_factory=client_factory,
                    )
                else:
                    http_client = await stack.enter_async_context(client_factory(headers=headers, timeout=timeout_seconds))
                    protocol_transport = streamable_http_client(
                        target.endpoint, http_client=http_client, terminate_on_close=terminate_on_close,
                    )
                session = await stack.enter_async_context(_SingleDispatchMcpClient(
                    protocol_transport, mode="auto", read_timeout_seconds=timeout_seconds,
                    message_handler=validate_message, cache=None,
                ))
            _install_strict_tool_list_validation(session)
            if guard_failure:
                raise McpToolDiscoveryError(guard_failure)
            yield session
        if guard_failure:
            raise McpToolDiscoveryError(guard_failure)
    except McpToolDiscoveryError:
        raise
    except Exception as exc:
        # AnyIO task groups can wrap platform guard failures. Preserve only their
        # bounded category, never transport/SDK endpoints, payloads or headers.
        raise McpToolDiscoveryError(guard_failure or _bounded_failure(exc) or "connection_failed") from None


async def list_mcp_tools(session):
    """Return the complete current catalog or fail; never a truncated selection."""
    tools = []
    names = set()
    cursors = set()
    cursor = None
    for _ in range(MCP_DISCOVERY_PAGE_LIMIT):
        page = await session.list_tools(cursor=cursor, cache_mode="bypass")
        for tool in page.tools:
            definition = _canonical_live_definition(
                tool.model_dump(by_alias=True, exclude_none=True)
            )
            if tool.name in names:
                raise McpToolDiscoveryError("protocol_error")
            names.add(tool.name)
            tools.append(tool.model_copy(update={"description": definition["description"]}))
        cursor = page.next_cursor
        if cursor is None:
            return tools
        if not cursor or cursor in cursors:
            raise McpToolDiscoveryError("protocol_error")
        cursors.add(cursor)
    raise McpToolDiscoveryError("page_limit_exceeded")


class McpToolDiscoveryAdapter:
    """Discover through the same guarded official SDK session used for execution."""

    def __init__(
        self, *, transport: str = "http", timeout_seconds: float = 10.0,
        max_response_bytes: int = 1024 * 1024,
    ) -> None:
        self._transport = transport
        self._timeout_seconds = timeout_seconds
        self._max_response_bytes = max(1, int(max_response_bytes))

    async def discover_definitions(
        self, endpoint: str, *, static_headers: Mapping[str, str] | None = None,
        jwt_authorization: str,
        credential_subject: dict[str, Any] | None = None,
        integration_kind: str = "mcp",
    ) -> tuple[dict[str, Any], ...]:
        try:
            # One deadline owns DNS, handshake, all pages and transport cleanup.
            async with asyncio.timeout(self._timeout_seconds):
                async with open_mcp_session({
                    "type": self._transport, "url": endpoint,
                    "credential_subject": credential_subject,
                    "integration_kind": integration_kind,
                    "headers": {
                        **normalize_static_mcp_headers(static_headers, integration_kind=integration_kind),
                        mcp_user_jwt_header(integration_kind): jwt_authorization,
                    },
                }, timeout_seconds=self._timeout_seconds,
                    max_response_bytes=self._max_response_bytes,
                    max_response_total_bytes=(
                        self._max_response_bytes if self._transport == "http" else None
                    ),
                    # Discovery never terminated a potentially shared remote
                    # session. Close local resources without adding a DELETE.
                    terminate_on_close=False,
                ) as session:
                    return tuple(
                        _canonical_live_definition(tool.model_dump(by_alias=True, exclude_none=True))
                        for tool in await list_mcp_tools(session)
                    )
        except TimeoutError:
            raise McpToolDiscoveryError("transport_failure") from None
