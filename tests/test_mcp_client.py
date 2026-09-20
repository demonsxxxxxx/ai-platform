import asyncio
import gzip
from types import SimpleNamespace

import httpx
import pytest
from mcp.types import ListToolsResult, Tool

from app.mcp.application.live_catalog import LiveMcpCatalogService
from app.mcp.infrastructure import client, catalog
from app.mcp.infrastructure import runtime as mcp_runtime
from app.mcp.infrastructure.runtime import McpRuntimeContextError
from tests.support.mcp_protocol_peers import RAW_TOOL, local_mcp_peers
from tests.test_claude_mcp_registration import allow_local_peer, subject


@pytest.mark.asyncio
@pytest.mark.parametrize("transport", ["streamable_http", "sse", "sandbox"])
async def test_catalog_dispatches_the_configured_transport(monkeypatch, transport):
    with local_mcp_peers() as peer:
        allow_local_peer(monkeypatch, peer.url)

        async def target(*_args):
            return SimpleNamespace(
                endpoint=peer.url + ("/sse" if transport == "sse" else "/mcp"),
                static_headers={"X-Static-Key": "synthetic"}, transport=transport,
            )

        service = LiveMcpCatalogService(
            target_resolver=target, discovery=catalog.StreamableHttpMcpToolDiscoveryAdapter(),
            sse_discovery=client.SseMcpToolDiscoveryAdapter(),
        )
        result = await service.list_server_tools(tenant_id="tenant", user_id="user", server_id="gateway", jwt="synthetic.jwt")
        if transport == "sandbox":
            assert result.unavailable_reason == "unsupported_transport"
            assert peer.requests == []
        else:
            assert result.unavailable_reason is None
            assert result.tools[0].tool_id == "gateway::" + RAW_TOOL
            assert (peer.requests[0][0] == "GET") == (transport == "sse")


@pytest.mark.asyncio
@pytest.mark.parametrize("transport", ["http", "sse"])
async def test_cancellation_closes_real_mcp_sessions(monkeypatch, transport):
    with local_mcp_peers() as peer:
        allow_local_peer(monkeypatch, peer.url)
        config = subject(url=peer.url + ("/sse" if transport == "sse" else "/mcp"))["mcp_server_config"]
        config["type"] = transport
        ready = asyncio.Event()

        async def operation():
            async with client.open_mcp_session(config, timeout_seconds=3) as session:
                await client.list_mcp_tools(session)
                ready.set()
                await asyncio.Event().wait()

        task = asyncio.create_task(operation())
        try:
            await asyncio.wait_for(ready.wait(), timeout=3)
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        if transport == "sse":
            assert await asyncio.to_thread(peer.sse_closed.wait, 2)


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_name", [" tool", "tool ", "tool\n"])
async def test_discovery_rejects_names_instead_of_silently_renaming(bad_name):
    async def list_tools(**_kwargs):
        return ListToolsResult(tools=[Tool(name="tool", inputSchema={}), Tool(name=bad_name, inputSchema={})])
    with pytest.raises(catalog.McpToolDiscoveryError, match="protocol_error"):
        await client.list_mcp_tools(SimpleNamespace(list_tools=list_tools))
    with pytest.raises(catalog.McpToolDiscoveryError, match="protocol_error"):
        catalog._canonical_live_definition({"name": bad_name})


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["compressed", "redirect", "oversize"])
@pytest.mark.parametrize("transport", ["http", "sse"])
async def test_transport_rejects_unsafe_responses_before_followup(monkeypatch, case, transport):
    requests = []

    class ResponseStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"data: " + b"x" * 100 + b"\n\n"

    async def handler(request):
        requests.append(request)
        assert request.url.host == "8.8.8.8"
        assert request.headers["Host"] == "mcp.example"
        assert request.headers["Accept-Encoding"] == "identity"
        if case == "redirect":
            return httpx.Response(307, headers={"Location": "https://other.example/secret"})
        if case == "compressed":
            return httpx.Response(200, headers={"Content-Encoding": "gzip"}, content=gzip.compress(b"x" * 100000))
        return httpx.Response(200, headers={"Content-Type": "text/event-stream"}, stream=ResponseStream())

    async def target(_endpoint):
        return catalog._ValidatedDiscoveryTarget("https://mcp.example/mcp", "https://8.8.8.8/mcp", "mcp.example", "mcp.example")

    real_client = httpx.AsyncClient
    monkeypatch.setattr(client, "_validated_discovery_target", target)
    monkeypatch.setattr(client.httpx, "AsyncClient", lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs))
    config = subject()["mcp_server_config"]
    config["type"] = transport
    with pytest.raises(catalog.McpToolDiscoveryError):
        async with asyncio.timeout(3):
            async with client.open_mcp_session(config, timeout_seconds=1, max_response_bytes=64):
                pytest.fail("unsafe response admitted")
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_runtime_rejects_retained_sandbox_transport_before_credentials(monkeypatch):
    async def runtime_target(*_args, **_kwargs):
        return {"transport": "sandbox", "credential_envelope": "sealed"}

    monkeypatch.setattr(mcp_runtime.mcp_postgres, "get_mcp_server_runtime_target", runtime_target)
    monkeypatch.setattr(
        mcp_runtime,
        "get_mcp_principal_jwt_store",
        lambda: (_ for _ in ()).throw(AssertionError("credentials must not be loaded")),
    )
    payload = SimpleNamespace(
        input={"_runtime_tool_policy_subjects": [{"identity": "mcp__gateway__tool", "mcp_server": "gateway"}]}
    )
    principal = SimpleNamespace(tenant_id="tenant", user_id="user")
    with pytest.raises(McpRuntimeContextError, match="mcp_server_unsupported_transport"):
        await mcp_runtime.attach_mcp_server_configs(object(), principal=principal, run_payload=payload)


@pytest.mark.asyncio
async def test_sse_rejects_static_jwt_override_before_connection():
    with pytest.raises(ValueError, match="mcp_header_conflict"):
        await client.SseMcpToolDiscoveryAdapter().discover_definitions(
            "https://mcp.example/sse", static_headers={"jwt-authorization": "secret"},
            jwt_authorization="Bearer current.jwt",
        )
