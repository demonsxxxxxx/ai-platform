import ipaddress
import json

import httpx
import pytest

from app.mcp import catalog
from app.mcp.catalog import McpToolDiscoveryError, StreamableHttpMcpToolDiscoveryAdapter


async def _resolved(*addresses):
    return tuple(ipaddress.ip_address(address) for address in addresses)


@pytest.mark.parametrize(
    "authorization",
    [
        "Bearer catalog token",
        "Bearer catalog\x7ftoken",
        "Bearer catalog\u0080token",
        "Bearer catalog\u00e9token",
    ],
)
def test_jwt_authorization_rejects_non_sendable_bearer_tokens(authorization):
    with pytest.raises(McpToolDiscoveryError, match="authorization_required"):
        catalog._normalized_jwt_authorization(authorization)


def test_jwt_authorization_accepts_visible_ascii_bearer_tokens():
    assert catalog._normalized_jwt_authorization("Bearer catalog-token~!#") == "Bearer catalog-token~!#"


@pytest.mark.asyncio
async def test_streamable_http_discovery_returns_complete_definitions(monkeypatch):
    seen_methods = []

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["X-API-Key"] == "catalog-static-secret"
        assert request.headers["JWT-Authorization"] == "Bearer catalog.jwt"
        assert request.headers["Host"] == "mcp.example"
        assert request.extensions["sni_hostname"] == "mcp.example"
        assert request.url.host == "8.8.8.8"
        payload = json.loads(request.content)
        seen_methods.append(payload["method"])
        if payload["method"] == "initialize":
            return httpx.Response(
                200,
                json={"jsonrpc": "2.0", "id": payload["id"], "result": {"protocolVersion": "2025-03-26"}},
                headers={"mcp-session-id": "session-1"},
            )
        if payload["method"] == "notifications/initialized":
            return httpx.Response(202)
        if payload["params"] == {}:
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": payload["id"],
                    "result": {
                        "tools": [
                            {
                                "name": "search_docs",
                                "title": "Search docs",
                                "description": "Search permitted docs.",
                                "inputSchema": {"type": "object"},
                            }
                        ],
                        "nextCursor": "page-2",
                    },
                },
            )
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": payload["id"],
                "result": {
                    "tools": [
                        {
                            "name": "get_doc",
                            "description": "Get a permitted doc.",
                            "inputSchema": {"type": "object"},
                        }
                    ]
                },
            },
        )

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        catalog.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    monkeypatch.setattr(catalog, "_resolve_discovery_addresses", lambda *_: _resolved("8.8.8.8"))

    definitions = await StreamableHttpMcpToolDiscoveryAdapter().discover_definitions(
        "https://mcp.example/tools",
        static_headers={"X-API-Key": "catalog-static-secret"},
        jwt_authorization="Bearer catalog.jwt",
    )

    assert [tool["name"] for tool in definitions] == ["search_docs", "get_doc"]
    assert definitions[0]["description"] == "Search permitted docs."
    assert definitions[0]["inputSchema"] == {"type": "object"}
    assert seen_methods == ["initialize", "notifications/initialized", "tools/list", "tools/list"]


@pytest.mark.asyncio
async def test_streamable_http_discovery_rejects_cursor_loop(monkeypatch):
    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        if payload["method"] == "initialize":
            return httpx.Response(200, json={"result": {"protocolVersion": "2025-03-26"}})
        if payload["method"] == "notifications/initialized":
            return httpx.Response(202)
        return httpx.Response(200, json={"result": {"tools": [], "nextCursor": "same"}})

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        catalog.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    monkeypatch.setattr(catalog, "_resolve_discovery_addresses", lambda *_: _resolved("8.8.8.8"))

    with pytest.raises(McpToolDiscoveryError, match="protocol_error"):
        await StreamableHttpMcpToolDiscoveryAdapter().discover_definitions(
            "https://mcp.example/tools",
            jwt_authorization="Bearer catalog.jwt",
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("endpoint", "allowed"),
    [
        ("https://8.8.8.8/tools", True),
        ("http://8.8.8.8/tools", False),
        ("http://10.42.0.12/tools", True),
        ("https://10.42.0.12/tools", True),
        ("http://127.0.0.1/tools", False),
        ("http://169.254.169.254/latest/meta-data", False),
        ("https://[::1]/tools", False),
        ("https://[fc00::1]/tools", False),
        ("https://100.64.0.1/tools", False),
    ],
)
async def test_discovery_target_policy(endpoint, allowed):
    if allowed:
        assert await catalog._validated_discovery_endpoint(endpoint) == endpoint
    else:
        with pytest.raises(McpToolDiscoveryError, match="invalid_endpoint"):
            await catalog._validated_discovery_endpoint(endpoint)


@pytest.mark.asyncio
async def test_discovery_target_policy_rejects_mixed_dns_answers(monkeypatch):
    monkeypatch.setattr(
        catalog,
        "_resolve_discovery_addresses",
        lambda *_: _resolved("10.42.0.12", "8.8.8.8"),
    )

    with pytest.raises(McpToolDiscoveryError, match="invalid_endpoint"):
        await catalog._validated_discovery_endpoint("https://mcp.corp.example/tools")


@pytest.mark.asyncio
async def test_discovery_does_not_follow_redirects(monkeypatch):
    seen_urls = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen_urls.append(str(request.url))
        return httpx.Response(302, headers={"location": "http://169.254.169.254/latest/meta-data"})

    real_client = httpx.AsyncClient
    monkeypatch.setattr(catalog, "_resolve_discovery_addresses", lambda *_: _resolved("8.8.8.8"))
    monkeypatch.setattr(
        catalog.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )

    with pytest.raises(McpToolDiscoveryError, match="protocol_error"):
        await StreamableHttpMcpToolDiscoveryAdapter().discover_definitions(
            "https://mcp.example/tools",
            jwt_authorization="Bearer catalog.jwt",
        )
    assert seen_urls == ["https://8.8.8.8/tools"]


@pytest.mark.asyncio
async def test_discovery_stops_when_response_exceeds_limit(monkeypatch):
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * 65)

    real_client = httpx.AsyncClient
    monkeypatch.setattr(catalog, "_resolve_discovery_addresses", lambda *_: _resolved("8.8.8.8"))
    monkeypatch.setattr(
        catalog.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )

    with pytest.raises(McpToolDiscoveryError, match="response_too_large"):
        await StreamableHttpMcpToolDiscoveryAdapter(max_response_bytes=64).discover_definitions(
            "https://mcp.example/tools",
            jwt_authorization="Bearer catalog.jwt",
        )
