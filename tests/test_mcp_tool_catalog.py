from contextlib import asynccontextmanager
import json

import httpx
import pytest

from app import mcp_tool_catalog
from app.mcp_tool_catalog import (
    McpDiscoveredTool,
    McpToolCatalogSyncCommand,
    McpToolCatalogSynchronizer,
    McpToolDiscoveryError,
    StreamableHttpMcpToolDiscoveryAdapter,
)
from app.repositories import new_mcp_catalog_tool_id
from app.validation import SAFE_ID_PATTERN


def _command(**overrides):
    values = {
        "tenant_id": "tenant-a",
        "server_name": "knowledge",
        "observed_generation": 7,
        "transport": "streamable_http",
        "endpoint": "https://mcp.example/tools",
        "credentialed": False,
        "actor_id": "admin-a",
    }
    values.update(overrides)
    return McpToolCatalogSyncCommand(**values)


def _tool(name: str, *, read_only: bool = True) -> McpDiscoveredTool:
    return McpDiscoveredTool(
        remote_name=name,
        label=name.title(),
        description=f"Read {name}.",
        schema_hash=f"hash-{name}",
        read_only=read_only,
    )


def test_catalog_identity_is_opaque_and_accepted_by_the_existing_chat_selector_contract():
    first = new_mcp_catalog_tool_id()
    second = new_mcp_catalog_tool_id()

    assert SAFE_ID_PATTERN.fullmatch(first)
    assert SAFE_ID_PATTERN.fullmatch(second)
    assert first != second


@pytest.mark.asyncio
async def test_streamable_http_discovery_consumes_every_cursor_page(monkeypatch):
    seen_methods: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        method = payload["method"]
        seen_methods.append(method)
        if method == "initialize":
            return httpx.Response(
                200,
                json={"jsonrpc": "2.0", "id": payload["id"], "result": {"protocolVersion": "2025-03-26"}},
                headers={"mcp-session-id": "session-1"},
            )
        if method == "notifications/initialized":
            return httpx.Response(202)
        assert method == "tools/list"
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
                                "description": "Search private docs without leaking them.",
                                "inputSchema": {"type": "object"},
                                "annotations": {"readOnlyHint": True},
                            }
                        ],
                        "nextCursor": "page-2",
                    },
                },
            )
        assert payload["params"] == {"cursor": "page-2"}
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": payload["id"],
                "result": {
                    "tools": [
                        {
                            "name": "get_doc",
                            "inputSchema": {"type": "object", "properties": {"id": {"type": "string"}}},
                            "annotations": {"readOnlyHint": True},
                        }
                    ]
                },
            },
        )

    real_client = httpx.AsyncClient

    def client_factory(**kwargs):
        return real_client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(mcp_tool_catalog.httpx, "AsyncClient", client_factory)

    tools = await StreamableHttpMcpToolDiscoveryAdapter().discover("https://mcp.example/tools")

    assert [tool.remote_name for tool in tools] == ["search_docs", "get_doc"]
    assert all(tool.read_only for tool in tools)
    assert seen_methods == ["initialize", "notifications/initialized", "tools/list", "tools/list"]


@pytest.mark.asyncio
async def test_streamable_http_discovery_rejects_cursor_loop_before_publication(monkeypatch):
    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        if payload["method"] == "initialize":
            return httpx.Response(200, json={"result": {"protocolVersion": "2025-03-26"}})
        if payload["method"] == "notifications/initialized":
            return httpx.Response(202)
        return httpx.Response(200, json={"result": {"tools": [], "nextCursor": "same"}})

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        mcp_tool_catalog.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )

    with pytest.raises(McpToolDiscoveryError, match="protocol_error"):
        await StreamableHttpMcpToolDiscoveryAdapter().discover("https://mcp.example/tools")


def _install_synchronizer_fakes(monkeypatch, *, discovery, publish_result=None):
    class FakeConnection:
        pass

    @asynccontextmanager
    async def fake_transaction():
        yield FakeConnection()

    outcomes: list[dict[str, object]] = []
    publications: list[dict[str, object]] = []
    attempts: list[dict[str, object]] = []

    async def fake_begin(conn, **kwargs):
        attempts.append(dict(kwargs))
        return {
            "started": True,
            "catalog_status": "syncing",
            "catalog_unavailable_reason": "refresh_required",
            "catalog_revision": 4,
            "catalog_discovered_count": 0,
            "catalog_selectable_count": 0,
            "catalog_sync_attempt": 2,
        }

    async def fake_outcome(conn, **kwargs):
        outcomes.append(dict(kwargs))
        return {
            "catalog_status": "unavailable",
            "catalog_unavailable_reason": kwargs["reason"],
            "catalog_revision": 4,
            "catalog_discovered_count": 0,
            "catalog_selectable_count": 0,
        }

    async def fake_publish(conn, **kwargs):
        publications.append(dict(kwargs))
        return publish_result or {
            "catalog_status": "available",
            "catalog_unavailable_reason": "",
            "catalog_revision": 8,
            "catalog_discovered_count": len(kwargs["tools"]),
            "catalog_selectable_count": sum(tool.read_only for tool in kwargs["tools"]),
            "published": True,
        }

    async def fake_ensure_user(conn, **kwargs):
        return {"id": kwargs["user_id"]}

    monkeypatch.setattr(mcp_tool_catalog, "transaction", fake_transaction)
    monkeypatch.setattr(mcp_tool_catalog.repositories, "begin_mcp_catalog_sync", fake_begin)
    monkeypatch.setattr(mcp_tool_catalog.repositories, "record_mcp_catalog_sync_outcome", fake_outcome)
    monkeypatch.setattr(mcp_tool_catalog.repositories, "publish_mcp_tool_catalog", fake_publish)
    monkeypatch.setattr(mcp_tool_catalog.repositories, "ensure_user", fake_ensure_user)
    return McpToolCatalogSynchronizer(discovery=discovery), outcomes, publications, attempts


@pytest.mark.asyncio
async def test_synchronizer_publishes_only_the_complete_multi_tool_manifest(monkeypatch):
    class CompleteDiscovery:
        async def discover(self, endpoint):
            assert endpoint == "https://mcp.example/tools"
            return (_tool("search_docs"), _tool("get_doc"), _tool("write_doc", read_only=False))

    synchronizer, outcomes, publications, attempts = _install_synchronizer_fakes(monkeypatch, discovery=CompleteDiscovery())

    result = await synchronizer.synchronize(_command())

    assert result.status == "available"
    assert result.selectable_count == 2
    assert outcomes == []
    assert attempts == [{"tenant_id": "tenant-a", "server_name": "knowledge", "observed_generation": 7, "actor_id": "admin-a"}]
    assert [tool.remote_name for tool in publications[0]["tools"]] == ["search_docs", "get_doc", "write_doc"]
    assert publications[0]["observed_attempt"] == 2


@pytest.mark.asyncio
async def test_synchronizer_publishes_a_truthful_zero_tool_result(monkeypatch):
    class EmptyDiscovery:
        async def discover(self, endpoint):
            return ()

    synchronizer, outcomes, publications, _ = _install_synchronizer_fakes(
        monkeypatch,
        discovery=EmptyDiscovery(),
        publish_result={
            "catalog_status": "no_tools",
            "catalog_unavailable_reason": "no_tools",
            "catalog_revision": 2,
            "catalog_discovered_count": 0,
            "catalog_selectable_count": 0,
            "published": True,
        },
    )

    result = await synchronizer.synchronize(_command())

    assert result.status == "no_tools"
    assert result.reason == "no_tools"
    assert outcomes == []
    assert publications[0]["tools"] == ()


@pytest.mark.asyncio
async def test_transport_failure_never_attempts_partial_publication(monkeypatch):
    class FailingDiscovery:
        async def discover(self, endpoint):
            raise McpToolDiscoveryError("transport_failure")

    synchronizer, outcomes, publications, _ = _install_synchronizer_fakes(monkeypatch, discovery=FailingDiscovery())

    result = await synchronizer.synchronize(_command())

    assert result.status == "unavailable"
    assert result.reason == "transport_failure"
    assert publications == []
    assert outcomes[0]["reason"] == "transport_failure"
    assert outcomes[0]["observed_attempt"] == 2


@pytest.mark.asyncio
async def test_stale_generation_result_cannot_report_publication(monkeypatch):
    class CompleteDiscovery:
        async def discover(self, endpoint):
            return (_tool("search_docs"),)

    synchronizer, _, publications, _ = _install_synchronizer_fakes(
        monkeypatch,
        discovery=CompleteDiscovery(),
        publish_result={
            "catalog_status": "unavailable",
            "catalog_unavailable_reason": "stale_generation",
            "catalog_revision": 8,
            "catalog_discovered_count": 1,
            "catalog_selectable_count": 1,
            "published": False,
        },
    )

    result = await synchronizer.synchronize(_command())

    assert result.reason == "stale_generation"
    assert result.published is False
    assert len(publications) == 1


@pytest.mark.asyncio
async def test_concurrent_sync_claim_fails_closed_before_remote_discovery(monkeypatch):
    class UnexpectedDiscovery:
        async def discover(self, endpoint):
            raise AssertionError("an already claimed generation must not rediscover")

    synchronizer, outcomes, publications, _ = _install_synchronizer_fakes(monkeypatch, discovery=UnexpectedDiscovery())

    async def already_claimed(conn, **kwargs):
        return {
            "started": False,
            "catalog_status": "unavailable",
            "catalog_unavailable_reason": "sync_in_progress",
            "catalog_revision": 8,
            "catalog_discovered_count": 2,
            "catalog_selectable_count": 2,
        }

    monkeypatch.setattr(mcp_tool_catalog.repositories, "begin_mcp_catalog_sync", already_claimed)

    result = await synchronizer.synchronize(_command())

    assert result.reason == "sync_in_progress"
    assert result.published is False
    assert outcomes == []
    assert publications == []


@pytest.mark.asyncio
async def test_credentialed_or_invalid_requests_stay_unavailable_without_discovery(monkeypatch):
    class UnexpectedDiscovery:
        async def discover(self, endpoint):
            raise AssertionError("discovery must not run")

    synchronizer, outcomes, publications, _ = _install_synchronizer_fakes(monkeypatch, discovery=UnexpectedDiscovery())

    credentialed = await synchronizer.synchronize(_command(credentialed=True))
    invalid = await synchronizer.synchronize(_command(endpoint="https://mcp.example/tools?token=secret"))

    assert credentialed.reason == "credentials_not_supported"
    assert invalid.reason == "invalid_endpoint"
    assert publications == []
    assert [row["reason"] for row in outcomes] == ["credentials_not_supported", "invalid_endpoint"]
    assert "mcp.example" not in str(invalid.public_payload())
