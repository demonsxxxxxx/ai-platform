import ipaddress
import json

import httpx
import pytest

from app.mcp.infrastructure import catalog
from app.mcp.infrastructure.catalog import (
    MCP_TOOL_ANNOTATION_READ_ONLY,
    MCP_TOOL_ANNOTATION_UNKNOWN,
    MCP_TOOL_ANNOTATION_WRITE_CAPABLE,
    McpToolDiscoveryError,
    StreamableHttpMcpToolDiscoveryAdapter,
)
from app.mcp.domain.errors import McpRuntimeContextError


async def _resolved(*addresses):
    return tuple(ipaddress.ip_address(address) for address in addresses)


@pytest.mark.parametrize(
    ("annotations", "expected_state", "write_capable", "risk_level"),
    [
        (None, MCP_TOOL_ANNOTATION_UNKNOWN, True, "high"),
        ({}, MCP_TOOL_ANNOTATION_UNKNOWN, True, "high"),
        ({"readOnlyHint": True}, MCP_TOOL_ANNOTATION_READ_ONLY, False, "low"),
        ({"readOnlyHint": False}, MCP_TOOL_ANNOTATION_WRITE_CAPABLE, True, "high"),
        ({"destructiveHint": True}, MCP_TOOL_ANNOTATION_WRITE_CAPABLE, True, "high"),
    ],
)
def test_discovered_tool_annotations_remain_advisory(
    annotations,
    expected_state,
    write_capable,
    risk_level,
):
    raw = {"name": "compatible_tool", "inputSchema": {"type": "object"}}
    if annotations is not None:
        raw["annotations"] = annotations

    tool = catalog._canonical_tool(raw)

    assert tool.annotation_state == expected_state
    assert tool.write_capable is write_capable
    assert tool.risk_level == risk_level


@pytest.mark.asyncio
async def test_streamable_http_discovery_sends_jwt_and_static_headers_on_every_page(
    monkeypatch,
):
    requests: list[tuple[str, str | None, str | None, str | None]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        method = payload["method"]
        requests.append(
            (
                method,
                request.headers.get("JWT-Authorization"),
                request.headers.get("X-Static-Key"),
                request.headers.get("Mcp-Session-Id"),
            )
        )
        if method == "initialize":
            return httpx.Response(
                200,
                json={"result": {"protocolVersion": "2025-03-26"}},
                headers={"Mcp-Session-Id": "session-1"},
            )
        if method == "notifications/initialized":
            return httpx.Response(202)
        if payload["params"] == {}:
            return httpx.Response(
                200,
                json={
                    "result": {
                        "tools": [
                            {
                                "name": "search_docs",
                                "description": "Search permitted docs.",
                                "inputSchema": {"type": "object"},
                                "annotations": {"readOnlyHint": True},
                            }
                        ],
                        "nextCursor": "page-2",
                    }
                },
            )
        return httpx.Response(
            200,
            json={
                "result": {
                    "tools": [
                        {
                            "name": "get_doc",
                            "inputSchema": {"type": "object"},
                        }
                    ]
                }
            },
        )

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        catalog.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    monkeypatch.setattr(
        catalog,
        "_resolve_discovery_addresses",
        lambda _hostname, _port: _resolved("8.8.8.8"),
    )

    definitions = await StreamableHttpMcpToolDiscoveryAdapter().discover_definitions(
        "https://mcp.example/tools",
        static_headers={"X-Static-Key": "configured"},
        jwt_authorization="Bearer user.jwt",
    )

    assert [item["name"] for item in definitions] == ["search_docs", "get_doc"]
    assert [item[0] for item in requests] == [
        "initialize",
        "notifications/initialized",
        "tools/list",
        "tools/list",
    ]
    assert all(item[1] == "Bearer user.jwt" for item in requests)
    assert all(item[2] == "configured" for item in requests)
    assert requests[0][3] is None
    assert all(item[3] == "session-1" for item in requests[1:])


@pytest.mark.asyncio
async def test_streamable_http_discovery_rejects_duplicate_tool_and_cursor_loop(monkeypatch):
    page = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal page
        payload = json.loads(request.content)
        if payload["method"] == "initialize":
            return httpx.Response(200, json={"result": {"protocolVersion": "2025-03-26"}})
        if payload["method"] == "notifications/initialized":
            return httpx.Response(202)
        page += 1
        return httpx.Response(
            200,
            json={
                "result": {
                    "tools": [{"name": "duplicate", "inputSchema": {}}],
                    "nextCursor": "same",
                }
            },
        )

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        catalog.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    monkeypatch.setattr(
        catalog,
        "_resolve_discovery_addresses",
        lambda _hostname, _port: _resolved("8.8.8.8"),
    )

    with pytest.raises(McpToolDiscoveryError, match="protocol_error"):
        await StreamableHttpMcpToolDiscoveryAdapter().discover_definitions(
            "https://mcp.example/tools",
            jwt_authorization="Bearer user.jwt",
        )
    assert page == 2


def test_discovery_request_rejects_static_dynamic_header_collision():
    target = catalog._ValidatedDiscoveryTarget(
        endpoint="https://mcp.example/tools",
        connect_url="https://8.8.8.8/tools",
        host_header="mcp.example",
        sni_hostname="mcp.example",
    )

    with pytest.raises(McpRuntimeContextError, match="mcp_header_conflict"):
        StreamableHttpMcpToolDiscoveryAdapter._headers(
            target,
            static_headers={"JWT-Authorization": "static"},
            jwt_authorization="Bearer user.jwt",
        )
