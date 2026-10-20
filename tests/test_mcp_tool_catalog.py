"""Discovery uses the installed SDK over synthetic, guarded HTTP transports."""

import asyncio
import gzip
import ipaddress
import json
from types import SimpleNamespace

import httpx
import pytest
from mcp.types import LATEST_PROTOCOL_VERSION

from app.mcp.application.live_catalog import LiveMcpCatalogService
from app.mcp.domain.errors import McpRuntimeContextError, McpToolDiscoveryError
from app.mcp.infrastructure import catalog, client, runtime as mcp_runtime
from app.mcp.infrastructure.client import McpToolDiscoveryAdapter


async def _resolved(*addresses):
    return tuple(ipaddress.ip_address(address) for address in addresses)


def _rpc_response(request, result):
    payload = json.loads(request.content)
    return httpx.Response(200, json={"jsonrpc": "2.0", "id": payload["id"], "result": result})


def _http_peer(page_handler, *, session_id=None):
    async def handler(request):
        if request.method == "GET":
            # Streamable HTTP servers need not support a server-initiated stream.
            return httpx.Response(405)
        assert request.method == "POST", "discovery must not terminate the remote session"
        payload = json.loads(request.content)
        if payload["method"] == "initialize":
            response = _rpc_response(request, {
                "protocolVersion": "2025-03-26", "capabilities": {"tools": {}},
                "serverInfo": {"name": "synthetic", "version": "1"},
            })
            if session_id:
                response.headers["Mcp-Session-Id"] = session_id
            return response
        if payload["method"] == "notifications/initialized":
            return httpx.Response(202)
        assert payload["method"] == "tools/list"
        return await page_handler(request, payload.get("params", {}))
    return handler


@pytest.fixture
def mock_transport(monkeypatch):
    async def current_credential(principal):
        assert principal.tenant_id == "tenant" and principal.user_id == "user"
        return "user.jwt"

    monkeypatch.setattr(mcp_runtime, "get_mcp_principal_jwt_store", lambda: SimpleNamespace(get=current_credential))
    real_client = httpx.AsyncClient
    requests, clients = [], []

    def install(handler, *, addresses=("8.8.8.8",)):
        async def record(request):
            requests.append(request)
            return await handler(request)

        def factory(**kwargs):
            http_client = real_client(transport=httpx.MockTransport(record), **kwargs)
            clients.append(http_client)
            return http_client

        monkeypatch.setattr(client.httpx, "AsyncClient", factory)
        monkeypatch.setattr(catalog, "_resolve_discovery_addresses", lambda *_args: _resolved(*addresses))
        return requests, clients
    return install


@pytest.mark.asyncio
async def test_streamable_http_discovery_uses_sdk_handshake_and_preserves_bound_credentials(mock_transport):
    async def page(request, params):
        if not params:
            return _rpc_response(request, {
                "tools": [{"name": "search_docs", "description": "Search permitted docs.",
                           "inputSchema": {"type": "object"}, "annotations": {"readOnlyHint": True}}],
                "nextCursor": "page-2",
            })
        assert params == {"cursor": "page-2"}
        return _rpc_response(request, {"tools": [{"name": "get_doc", "inputSchema": {"type": "object"}}]})

    requests, clients = mock_transport(_http_peer(page, session_id="session-1"))
    definitions = await McpToolDiscoveryAdapter().discover_definitions(
        "https://mcp.example/tools", static_headers={"X-Static-Key": "configured"},
        jwt_authorization="Bearer user.jwt",
    )
    assert [item["name"] for item in definitions] == ["search_docs", "get_doc"]
    posts = [json.loads(request.content) for request in requests if request.method == "POST"]
    assert [payload["method"] for payload in posts] == [
        "initialize", "notifications/initialized", "tools/list", "tools/list",
    ]
    assert posts[0]["params"]["protocolVersion"] == LATEST_PROTOCOL_VERSION
    ids = [payload["id"] for payload in posts if "id" in payload]
    assert len(ids) == len(set(ids)) == 3
    assert any(request.method == "GET" for request in requests)
    for request in requests:
        assert str(request.url) == "https://8.8.8.8/tools"
        assert request.headers["Host"] == "mcp.example"
        assert request.extensions["sni_hostname"] == "mcp.example"
        assert request.headers["JWT-Authorization"] == "Bearer user.jwt"
        assert request.headers["X-Static-Key"] == "configured"
        assert request.headers["Accept-Encoding"] == "identity"
        if request is not requests[0]:
            assert request.headers["Mcp-Session-Id"] == "session-1"
            assert request.headers["Mcp-Protocol-Version"] == "2025-03-26"
    assert "Mcp-Session-Id" not in requests[0].headers
    assert len(clients) == 1 and clients[0].is_closed


@pytest.mark.asyncio
@pytest.mark.parametrize("annotations", [
    None, {}, {"readOnlyHint": None}, {"readOnlyHint": True}, {"readOnlyHint": False},
    {"destructiveHint": True}, {"readOnlyHint": True, "custom": {"nested": [1, "value"]}},
])
async def test_sdk_discovery_preserves_schemas_and_advisory_annotations(mock_transport, annotations):
    schema = {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}
    async def page(request, _params):
        return _rpc_response(request, {"tools": [{
            "name": "compatible_tool", "description": "x" * 3000,
            "inputSchema": schema, "annotations": annotations,
        }]})
    mock_transport(_http_peer(page))
    definitions = await McpToolDiscoveryAdapter().discover_definitions(
        "https://mcp.example/tools", jwt_authorization="Bearer user.jwt",
    )
    assert definitions[0]["inputSchema"] == schema
    assert definitions[0]["description"] == "x" * catalog.MCP_TOOL_DESCRIPTION_MAX_LENGTH
    assert definitions[0]["annotations"] == {
        key: value for key, value in (annotations or {}).items() if value is not None
    }

    async def target(*_args):
        return SimpleNamespace(endpoint="https://mcp.example/tools", static_headers={}, transport="http")
    service = LiveMcpCatalogService(target_resolver=target, discovery=McpToolDiscoveryAdapter())
    public = await service.list_server_tools(tenant_id="tenant", user_id="user", server_id="gateway", jwt="user.jwt")
    assert public.unavailable_reason is None
    read_only = (annotations or {}).get("readOnlyHint") is True
    assert public.tools[0].write_capable is not read_only
    assert public.tools[0].risk_level == ("low" if read_only else "high")


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [
    ("readOnlyHint", "true"), ("readOnlyHint", 1), ("readOnlyHint", "false"), ("readOnlyHint", 0),
    ("destructiveHint", "false"), ("destructiveHint", 0),
])
async def test_sdk_discovery_never_coerces_tool_authority(mock_transport, field, value):
    async def page(request, _params):
        return _rpc_response(request, {"tools": [{
            "name": "tool", "inputSchema": {}, "annotations": {field: value},
        }]})
    mock_transport(_http_peer(page))
    with pytest.raises(McpToolDiscoveryError, match="^protocol_error$"):
        await McpToolDiscoveryAdapter().discover_definitions(
            "https://mcp.example/tools", jwt_authorization="Bearer user.jwt",
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["same_page", "across_pages", "cursor_loop", "empty_cursor", "invalid_cursor", "page_limit"])
async def test_sdk_discovery_rejects_incomplete_or_ambiguous_catalogs(monkeypatch, mock_transport, case):
    pages = []
    monkeypatch.setattr(client, "MCP_DISCOVERY_PAGE_LIMIT", 3)
    async def page(request, params):
        pages.append(params)
        name = "duplicate" if case in {"same_page", "across_pages"} else f"tool_{len(pages)}"
        tools = [{"name": name, "inputSchema": {}}] * (2 if case == "same_page" else 1)
        cursor = {"empty_cursor": "", "invalid_cursor": 1}.get(case, "same")
        if case == "page_limit":
            cursor = f"page_{len(pages)}"
        return _rpc_response(request, {"tools": tools, "nextCursor": cursor})
    mock_transport(_http_peer(page))
    reason = "page_limit_exceeded" if case == "page_limit" else "protocol_error"
    with pytest.raises(McpToolDiscoveryError, match=f"^{reason}$"):
        await McpToolDiscoveryAdapter().discover_definitions(
            "https://mcp.example/tools", jwt_authorization="Bearer user.jwt",
        )
    assert len(pages) == {"same_page": 1, "across_pages": 2, "cursor_loop": 2,
                          "empty_cursor": 1, "invalid_cursor": 1, "page_limit": 3}[case]


@pytest.mark.asyncio
@pytest.mark.parametrize("replacement", [
    {"name": " tool"}, {"name": "tool\n"}, {"inputSchema": []}, {"description": 5}, {"annotations": []},
])
async def test_sdk_discovery_rejects_malformed_tool_definitions(mock_transport, replacement):
    async def page(request, _params):
        return _rpc_response(request, {"tools": [{"name": "tool", "inputSchema": {}, **replacement}]})
    mock_transport(_http_peer(page))
    with pytest.raises(McpToolDiscoveryError, match="^protocol_error$"):
        await McpToolDiscoveryAdapter().discover_definitions(
            "https://mcp.example/tools", jwt_authorization="Bearer user.jwt",
        )


@pytest.mark.asyncio
async def test_sdk_discovery_rejects_mismatched_response_id(mock_transport):
    async def page(request, _params):
        response = _rpc_response(request, {"tools": [{"name": "unmatched", "inputSchema": {}}]})
        payload = response.json()
        payload["id"] = "unrelated"
        return httpx.Response(200, json=payload)
    mock_transport(_http_peer(page))
    with pytest.raises(McpToolDiscoveryError):
        await McpToolDiscoveryAdapter(timeout_seconds=0.1).discover_definitions(
            "https://mcp.example/tools", jwt_authorization="Bearer user.jwt",
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("transport", ["http", "sse"])
async def test_discovery_rejects_static_dynamic_header_collision_before_connection(transport):
    with pytest.raises(McpRuntimeContextError, match="mcp_header_conflict"):
        await McpToolDiscoveryAdapter(transport=transport).discover_definitions(
            "https://mcp.example/tools", static_headers={"jwt-authorization": "static"},
            jwt_authorization="Bearer user.jwt",
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint,addresses", [
    ("https://mcp.example/tools", ("127.0.0.1",)),
    ("https://mcp.example/tools", ("8.8.8.8", "127.0.0.1")),
    ("https://mcp.example/tools", ("8.8.8.8", "10.0.0.1")),
    ("http://mcp.example/tools", ("8.8.8.8",)),
    ("https://mcp.example/tools", ("169.254.169.254",)),
    ("https://mcp.example/tools", ("100.64.0.1",)),
    ("https://mcp.example/tools", ("fd00::1",)),
    ("https://user:pass@mcp.example/tools", ("8.8.8.8",)),
    ("https://mcp.example/tools?token=synthetic", ("8.8.8.8",)),
    ("https://mcp.example/tools#fragment", ("8.8.8.8",)),
])
async def test_discovery_rejects_forbidden_endpoints_before_network(mock_transport, endpoint, addresses):
    async def handler(_request):
        pytest.fail("forbidden endpoint dispatched")
    requests, _clients = mock_transport(handler, addresses=addresses)
    with pytest.raises(McpToolDiscoveryError, match="^invalid_endpoint$"):
        await McpToolDiscoveryAdapter().discover_definitions(endpoint, jwt_authorization="Bearer user.jwt")
    assert requests == []


@pytest.mark.asyncio
async def test_discovery_pins_the_validated_intranet_address_once(monkeypatch, mock_transport):
    async def page(request, _params):
        return _rpc_response(request, {"tools": []})
    requests, _clients = mock_transport(_http_peer(page))
    resolutions = []
    async def resolve(host, port):
        resolutions.append((host, port))
        return await _resolved("10.0.0.2", "10.0.0.1") if len(resolutions) == 1 else await _resolved("127.0.0.1")
    monkeypatch.setattr(catalog, "_resolve_discovery_addresses", resolve)
    await McpToolDiscoveryAdapter().discover_definitions("http://mcp.example:8080/tools", jwt_authorization="Bearer user.jwt")
    assert resolutions == [("mcp.example", 8080)]
    assert all(str(request.url) == "http://10.0.0.1:8080/tools" for request in requests)
    assert all(request.headers["Host"] == "mcp.example:8080" for request in requests)


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["redirect", "compressed", "json_oversize", "json_stream_oversize", "sse_oversize", "sse_cumulative"])
async def test_discovery_keeps_response_guards_beneath_sdk(mock_transport, case):
    closed = asyncio.Event()
    class ResponseStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            if case == "sse_cumulative":
                # Every frame is below 512 bytes. An event id must not let the
                # SDK resume a response after the cumulative byte guard rejects it.
                yield b"id: resume\nretry: 0\ndata: \n\n"
                for _ in range(10):
                    yield b": " + b"x" * 100 + b"\n\n"
            else:
                yield b"data: " + b"x" * 1024 + b"\n\n"
        async def aclose(self):
            closed.set()

    async def page(request, _params):
        if case == "redirect":
            return httpx.Response(307, headers={"Location": "https://other.example/secret"})
        if case == "compressed":
            return httpx.Response(200, headers={"Content-Encoding": "gzip"}, content=gzip.compress(b"secret" * 1000))
        if case in {"json_oversize", "json_stream_oversize"}:
            response = _rpc_response(request, {"tools": [{"name": "tool", "description": "x" * 1024, "inputSchema": {}}]})
            if case == "json_stream_oversize":
                return httpx.Response(200, headers={"Content-Type": "application/json"}, stream=httpx.ByteStream(response.content))
            return response
        return httpx.Response(200, headers={"Content-Type": "text/event-stream"}, stream=ResponseStream())

    requests, clients = mock_transport(_http_peer(page))
    with pytest.raises(McpToolDiscoveryError):
        await McpToolDiscoveryAdapter(timeout_seconds=0.15, max_response_bytes=512).discover_definitions(
            "https://mcp.example/tools", jwt_authorization="Bearer user.jwt",
        )
    assert len(requests) == 3
    assert all(request.url.host == "8.8.8.8" for request in requests)
    if case.startswith("sse_"):
        assert closed.is_set()
    assert clients[0].is_closed


@pytest.mark.asyncio
async def test_http_discovery_total_deadline_includes_dns(monkeypatch):
    cancelled = asyncio.Event()
    async def stalled_resolution(*_args):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()
    monkeypatch.setattr(catalog, "_resolve_discovery_addresses", stalled_resolution)
    with pytest.raises(McpToolDiscoveryError, match="^transport_failure$"):
        await asyncio.wait_for(McpToolDiscoveryAdapter(timeout_seconds=0.02).discover_definitions(
            "https://mcp.example/tools", jwt_authorization="Bearer user.jwt",
        ), timeout=2)
    assert cancelled.is_set()


@pytest.mark.asyncio
@pytest.mark.parametrize("caller_cancel", [False, True])
async def test_http_discovery_deadline_and_cancel_close_partial_page(mock_transport, caller_cancel):
    entered, closed = asyncio.Event(), asyncio.Event()
    class StalledStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'data: {"result":'
            entered.set()
            await asyncio.Event().wait()
        async def aclose(self):
            closed.set()
    async def page(request, params):
        if not params:
            return _rpc_response(request, {"tools": [], "nextCursor": "page-2"})
        assert params == {"cursor": "page-2"}
        return httpx.Response(200, headers={"Content-Type": "text/event-stream"}, stream=StalledStream())
    _requests, clients = mock_transport(_http_peer(page))
    task = asyncio.create_task(McpToolDiscoveryAdapter(timeout_seconds=60 if caller_cancel else 0.05).discover_definitions(
        "https://mcp.example/tools", jwt_authorization="Bearer user.jwt",
    ))
    try:
        await asyncio.wait_for(entered.wait(), timeout=2)
        if caller_cancel:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            with pytest.raises(McpToolDiscoveryError, match="^transport_failure$"):
                await asyncio.wait_for(task, timeout=2)
        assert closed.is_set()
        assert len(clients) == 1 and clients[0].is_closed
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_discovery_rejects_malformed_sse_event_before_valid_result(mock_transport):
    closed = asyncio.Event()
    class ResponseStream(httpx.AsyncByteStream):
        def __init__(self, result):
            self.result = result
        async def __aiter__(self):
            yield b"data: not-json\n\n"
            yield b"data: " + self.result + b"\n\n"
        async def aclose(self):
            closed.set()
    async def page(request, _params):
        result = _rpc_response(request, {"tools": [{"name": "must_not_accept", "inputSchema": {}}]})
        return httpx.Response(200, headers={"Content-Type": "text/event-stream"}, stream=ResponseStream(result.content))
    _requests, clients = mock_transport(_http_peer(page))
    with pytest.raises(McpToolDiscoveryError, match="^protocol_error$"):
        await McpToolDiscoveryAdapter(timeout_seconds=1).discover_definitions(
            "https://mcp.example/tools", jwt_authorization="Bearer user.jwt",
        )
    assert closed.is_set()
    assert clients[0].is_closed


@pytest.mark.asyncio
async def test_sse_discovery_shares_sdk_pagination_headers_and_cleanup(mock_transport):
    messages = asyncio.Queue()
    closed = asyncio.Event()
    class EventStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"event: endpoint\ndata: /messages?session_id=synthetic\n\n"
            while True:
                yield b"event: message\ndata: " + await messages.get() + b"\n\n"
        async def aclose(self):
            closed.set()
    async def handler(request):
        if request.method == "GET":
            return httpx.Response(200, headers={"Content-Type": "text/event-stream"}, stream=EventStream())
        assert request.url.path == "/messages"
        payload = json.loads(request.content)
        if payload["method"] == "notifications/initialized":
            return httpx.Response(202)
        if payload["method"] == "initialize":
            result = {"protocolVersion": "2025-03-26", "capabilities": {"tools": {}},
                      "serverInfo": {"name": "synthetic", "version": "1"}}
        elif not payload.get("params"):
            result = {"tools": [{"name": "first", "inputSchema": {}}], "nextCursor": "page-2"}
        else:
            assert payload["params"] == {"cursor": "page-2"}
            result = {"tools": [{"name": "second", "inputSchema": {}}]}
        await messages.put(_rpc_response(request, result).content)
        return httpx.Response(202)
    requests, clients = mock_transport(handler)
    definitions = await McpToolDiscoveryAdapter(transport="sse", timeout_seconds=1).discover_definitions(
        "https://mcp.example/sse", static_headers={"X-Static-Key": "configured"},
        jwt_authorization="Bearer user.jwt",
    )
    assert [tool["name"] for tool in definitions] == ["first", "second"]
    assert len(requests) == 5
    assert all(request.url.host == "8.8.8.8" for request in requests)
    assert all(request.headers["Host"] == "mcp.example" for request in requests)
    assert all(request.extensions["sni_hostname"] == "mcp.example" for request in requests)
    assert all(request.headers["JWT-Authorization"] == "Bearer user.jwt" for request in requests)
    assert all(request.headers["X-Static-Key"] == "configured" for request in requests)
    assert closed.is_set() and clients[0].is_closed


@pytest.mark.asyncio
@pytest.mark.parametrize("message_endpoint", [
    "https://other.example/messages", "http://mcp.example/messages", "https://mcp.example:444/messages",
])
async def test_sse_discovery_never_sends_credentials_to_another_origin(mock_transport, message_endpoint):
    closed = asyncio.Event()
    class EventStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield f"event: endpoint\ndata: {message_endpoint}\n\n".encode()
        async def aclose(self):
            closed.set()
    async def handler(_request):
        return httpx.Response(200, headers={"Content-Type": "text/event-stream"}, stream=EventStream())
    requests, clients = mock_transport(handler)
    with pytest.raises(McpToolDiscoveryError):
        await McpToolDiscoveryAdapter(transport="sse", timeout_seconds=0.1).discover_definitions(
            "https://mcp.example/sse", jwt_authorization="Bearer user.jwt",
        )
    assert len(requests) == 1 and requests[0].method == "GET"
    assert closed.is_set() and clients[0].is_closed


@pytest.mark.asyncio
async def test_discovery_does_not_ignore_a_rejected_optional_sdk_get(mock_transport):
    rejected = asyncio.Event()
    async def page(request, _params):
        await rejected.wait()
        await asyncio.sleep(0)
        return _rpc_response(request, {"tools": [{"name": "must_not_accept", "inputSchema": {}}]})
    peer = _http_peer(page, session_id="session-1")
    async def handler(request):
        if request.method == "GET":
            rejected.set()
            return httpx.Response(307, headers={"Location": "https://other.example/secret"})
        return await peer(request)
    requests, clients = mock_transport(handler)
    with pytest.raises(McpToolDiscoveryError):
        await McpToolDiscoveryAdapter(timeout_seconds=0.1).discover_definitions(
            "https://mcp.example/tools", jwt_authorization="Bearer user.jwt",
        )
    assert all(request.url.host == "8.8.8.8" for request in requests)
    assert sum(request.method == "GET" for request in requests) == 1
    assert clients[0].is_closed


@pytest.mark.asyncio
async def test_discovery_isolates_concurrent_principal_headers(mock_transport):
    async def page(request, _params):
        await asyncio.sleep(0)
        return _rpc_response(request, {"tools": []})
    requests, clients = mock_transport(_http_peer(page))
    await asyncio.gather(*(
        McpToolDiscoveryAdapter().discover_definitions(
            f"https://mcp.example/{user}", static_headers={"X-Static-Key": user},
            jwt_authorization=f"Bearer {user}.jwt",
        ) for user in ("alice", "bob")
    ))
    for request in requests:
        user = request.url.path.removeprefix("/")
        assert request.headers["JWT-Authorization"] == f"Bearer {user}.jwt"
        assert request.headers["X-Static-Key"] == user
    assert len(clients) == 2 and all(http_client.is_closed for http_client in clients)
