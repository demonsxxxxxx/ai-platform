import base64
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from mcp.client.session import ClientSession
from mcp.shared.exceptions import MCPError
from mcp.shared.memory import create_client_server_memory_streams
from mcp_types import CallToolResult, TextContent, Tool

from app.execution.infrastructure.claude_mcp import ClaudeMcpRegistration
from app.execution.infrastructure.original_file_delivery import OriginalFileDelivery
from app.mcp.infrastructure import client as mcp_client
from app.mcp.infrastructure.catalog import _ValidatedDiscoveryTarget
from tests.support.mcp_protocol_peers import RAW_TOOL, SCHEMA, local_mcp_peers


def _allow_local_peer(monkeypatch, peer, transport):
    endpoint = peer.url + ("/sse" if transport == "sse" else "/mcp")

    async def target(_endpoint):
        return _ValidatedDiscoveryTarget(endpoint, endpoint, "127.0.0.1", "127.0.0.1")

    monkeypatch.setattr(mcp_client, "_validated_discovery_target", target)


def _subject(server: str, tool: str) -> dict:
    return {
        "identity": f"mcp__{server}__{tool}",
        "mcp_server": server,
        "mcp_tool": tool,
        "registered": True,
        "declared": True,
        "active": True,
        "distributed": True,
        "identity_authorized": True,
        "object_authorized": True,
        "parameters_authorized": True,
        "risk_level": "low",
        "write_capable": False,
        "parameter_delegation": "external_mcp",
    }


async def _connected_client(server):
    streams = create_client_server_memory_streams()
    client_streams, server_streams = await streams.__aenter__()
    client_read, client_write = client_streams
    server_read, server_write = server_streams
    server_task = __import__("asyncio").create_task(
        server.run(server_read, server_write, server.create_initialization_options())
    )
    client = ClientSession(client_read, client_write)
    await client.__aenter__()
    await client.initialize()
    return streams, client, server_task


@pytest.mark.asyncio
async def test_budget_is_per_live_original_call_and_shared_config_is_unchanged():
    original = _subject("mixed", "read_original")
    ordinary = _subject("mixed", "lookup")
    ordinary["mcp_tool_schema"] = {
        "type": "object", "x-ai-platform-result-format": "profile-drive.original-file.v1",
    }
    configs = {"mixed": {"type": "http", "url": "https://example.test/mcp"}}
    configs_before = dict(configs["mixed"])
    opened: list[dict] = []
    delivered: list[bytes] = []

    @asynccontextmanager
    async def session_factory(config, **_kwargs):
        opened.append(dict(config))

        async def call_tool(name, _arguments):
            if name == "lookup":
                return CallToolResult(content=[TextContent(type="text", text="ordinary")])
            raw = b"opaque"
            return CallToolResult(content=[
                TextContent(type="text", text=json.dumps({
                    "format": "profile-drive.original-file.v1", "source": "profile",
                    "path": "share/file.bin", "name": "file.bin", "sizeBytes": len(raw),
                    "contentType": "application/octet-stream",
                })),
                {"type": "resource", "resource": {
                    "uri": "profile-drive://original-file", "mimeType": "application/octet-stream",
                    "blob": base64.b64encode(raw).decode("ascii"),
                }},
            ])

        yield SimpleNamespace(call_tool=call_tool)

    async def list_tools(_session):
        return [
            Tool(name="read_original", input_schema={
                "type": "object", "x-ai-platform-result-format": "profile-drive.original-file.v1",
            }),
            Tool(name="lookup", input_schema={"type": "object"}),
        ]

    async def deliver(_metadata, raw):
        delivered.append(raw)
        return {"workspace_path": "inputs/.mcp-original/file.bin"}

    registration = ClaudeMcpRegistration(
        {original["identity"]: original, ordinary["identity"]: ordinary}, configs,
        session_factory=session_factory, list_tools=list_tools,
        original_file_delivery=deliver,
    )
    options = SimpleNamespace()
    async with registration.activate(options):
        server = options.mcp_servers["mixed"]["instance"]
        streams, client, server_task = await _connected_client(server)
        try:
            listed = await client.list_tools()
            aliases = {tool.name for tool in listed.tools}
            assert len(aliases) == 2
            ordinary_alias = registration._tool_aliases[("mixed", "lookup")]
            original_alias = registration._tool_aliases[("mixed", "read_original")]
            await client.call_tool(ordinary_alias, {})
            original_result = await client.call_tool(original_alias, {})
        finally:
            await client.__aexit__(None, None, None)
            server_task.cancel()
            await __import__("asyncio").gather(server_task, return_exceptions=True)
            await streams.__aexit__(None, None, None)
    assert configs["mixed"] == configs_before
    assert opened[0]["max_response_bytes"] == 1 * 1024 * 1024
    assert opened[0]["max_response_total_bytes"] == 1 * 1024 * 1024
    assert opened[-1]["max_response_bytes"] == 16 * 1024 * 1024
    assert delivered == [b"opaque"]
    assert "blob" not in original_result.content[-1].text


@pytest.mark.asyncio
@pytest.mark.parametrize("transport", ["http", "sse"])
async def test_real_original_file_http_and_sse_are_single_dispatch_and_preserve_large_bytes(
    monkeypatch, tmp_path, transport,
):
    raw = bytes(range(256)) * 6_500
    with local_mcp_peers() as peer:
        peer.original_file = True
        peer.original_file_bytes = raw
        peer.tools[0]["inputSchema"] = {
            **SCHEMA, "x-ai-platform-result-format": "profile-drive.original-file.v1",
        }
        _allow_local_peer(monkeypatch, peer, transport)
        item = _subject("files", RAW_TOOL)
        item["mcp_server_config"] = {
            "type": transport,
            "url": peer.url + ("/sse" if transport == "sse" else "/mcp"),
            "headers": {"JWT-Authorization": "Bearer synthetic.jwt"},
        }
        delivered = []

        async def deliver(metadata, received):
            delivered.append((metadata, received))
            return {"workspace_path": "inputs/.mcp-original/large.bin"}

        from app.bootstrap.claude_mcp import prepare_claude_mcp
        registration = prepare_claude_mcp(
            {item["identity"]: item}, {"files": item["mcp_server_config"]},
            original_file_delivery=deliver,
        )
        options = SimpleNamespace()
        async with registration.activate(options):
            server = options.mcp_servers["files"]["instance"]
            streams, client, server_task = await _connected_client(server)
            try:
                listed = await client.list_tools()
                try:
                    await client.call_tool(listed.tools[0].name, {"query": "synthetic"})
                except Exception as exc:
                    raise AssertionError((type(exc).__name__, str(exc), peer.requests, peer.calls)) from exc
            finally:
                await client.__aexit__(None, None, None)
                server_task.cancel()
                await __import__("asyncio").gather(server_task, return_exceptions=True)
                await streams.__aexit__(None, None, None)
        assert delivered and delivered[0][1] == raw
        assert len(peer.calls) == 1
        assert sum(method == "tools/list" for method, _headers in peer.requests) == 1
        assert sum(method == "tools/call" for method, _headers in peer.requests) == 1
        # Auto negotiation is allowed once per physical session; the no-replay
        # assertion is the single catalog plus single tools/call above.


@pytest.mark.asyncio
async def test_local_delivery_failure_is_mapped_without_remote_error_receipt(tmp_path):
    item = _subject("files", "read_original")
    raw = b"bytes"

    @asynccontextmanager
    async def session_factory(_config, **_kwargs):
        async def call_tool(_name, _arguments):
            return CallToolResult(content=[
                TextContent(type="text", text=json.dumps({
                    "format": "profile-drive.original-file.v1", "source": "profile",
                    "path": "share/file.bin", "name": "file.bin", "sizeBytes": len(raw),
                    "contentType": "application/octet-stream",
                })),
                {"type": "resource", "resource": {
                    "uri": "profile-drive://original-file", "mimeType": "application/octet-stream",
                    "blob": base64.b64encode(raw).decode("ascii"),
                }},
            ], is_error=False)

        yield SimpleNamespace(call_tool=call_tool)

    async def list_tools(_session):
        return [Tool(name="read_original", input_schema={
            "type": "object", "x-ai-platform-result-format": "profile-drive.original-file.v1",
        })]

    fail_delivery = OriginalFileDelivery(
        cwd=tmp_path, tenant_id="tenant", workspace_id="workspace", user_id="user",
        session_id="session", run_id="run", attempt_id="attempt", lease_id="lease",
        max_total_bytes=0,
    )

    registration = ClaudeMcpRegistration(
        {item["identity"]: item}, {"files": {"type": "http", "url": "https://example.test/mcp"}},
        session_factory=session_factory, list_tools=list_tools,
        original_file_delivery=fail_delivery,
    )
    options = SimpleNamespace()
    async with registration.activate(options):
        server = options.mcp_servers["files"]["instance"]
        streams, client, server_task = await _connected_client(server)
        try:
            with pytest.raises(MCPError) as error:
                await client.call_tool("read_original", {})
        finally:
            await client.__aexit__(None, None, None)
            server_task.cancel()
            await __import__("asyncio").gather(server_task, return_exceptions=True)
            await streams.__aexit__(None, None, None)
    assert error.value.code == -32001
    assert error.value.message == "original_file_delivery_failed"
