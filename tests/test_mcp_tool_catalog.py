import asyncio
import ipaddress
import json

import httpx
import pytest

from app.mcp import catalog
from app.mcp.catalog import (
    MCP_TOOL_ANNOTATION_READ_ONLY,
    MCP_TOOL_ANNOTATION_UNKNOWN,
    MCP_TOOL_ANNOTATION_WRITE_CAPABLE,
    McpDiscoveredTool,
    McpToolCatalogSyncCommand,
    McpToolCatalogSynchronizer,
    McpToolDiscoveryError,
    StreamableHttpMcpToolDiscoveryAdapter,
)
from app.mcp.repository import new_mcp_catalog_tool_id
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
        schema_hash=f"hash-{name}",
        read_only=read_only,
    )


def test_catalog_identity_is_opaque_and_accepted_by_the_existing_chat_selector_contract():
    first = new_mcp_catalog_tool_id()
    second = new_mcp_catalog_tool_id()

    assert SAFE_ID_PATTERN.fullmatch(first)
    assert SAFE_ID_PATTERN.fullmatch(second)
    assert first != second


@pytest.mark.parametrize(
    ("annotations", "expected_state", "expected_write_capable", "expected_risk_level"),
    [
        (None, MCP_TOOL_ANNOTATION_UNKNOWN, True, "high"),
        ({}, MCP_TOOL_ANNOTATION_UNKNOWN, True, "high"),
        ({"readOnlyHint": True}, MCP_TOOL_ANNOTATION_READ_ONLY, False, "low"),
        ({"readOnlyHint": False}, MCP_TOOL_ANNOTATION_WRITE_CAPABLE, True, "high"),
        ({"destructiveHint": True}, MCP_TOOL_ANNOTATION_WRITE_CAPABLE, True, "high"),
    ],
)
def test_canonical_tool_keeps_optional_annotation_state_without_claiming_unknown_is_read_only(
    annotations,
    expected_state,
    expected_write_capable,
    expected_risk_level,
):
    raw = {"name": "compatible_tool", "inputSchema": {"type": "object"}}
    if annotations is not None:
        raw["annotations"] = annotations

    tool = catalog._canonical_tool(raw)

    assert tool.annotation_state == expected_state
    assert tool.read_only is (expected_state == MCP_TOOL_ANNOTATION_READ_ONLY)
    assert tool.write_capable is expected_write_capable
    assert tool.risk_level == expected_risk_level


@pytest.mark.asyncio
async def test_streamable_http_discovery_consumes_every_cursor_page(monkeypatch):
    seen_methods: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["X-API-Key"] == "catalog-static-secret"
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

    async def public_dns(hostname, port):
        return (ipaddress.ip_address("8.8.8.8"),)

    monkeypatch.setattr(catalog.httpx, "AsyncClient", client_factory)
    monkeypatch.setattr(catalog, "_resolve_discovery_addresses", public_dns)

    tools = await StreamableHttpMcpToolDiscoveryAdapter().discover(
        "https://mcp.example/tools",
        static_headers={"X-API-Key": "catalog-static-secret"},
    )

    assert [tool.remote_name for tool in tools] == ["search_docs", "get_doc"]
    assert all(tool.read_only for tool in tools)
    assert not hasattr(tools[0], "label")
    assert not hasattr(tools[0], "description")
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
        catalog.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    monkeypatch.setattr(
        catalog,
        "_resolve_discovery_addresses",
        lambda hostname, port: _resolved("8.8.8.8"),
    )

    with pytest.raises(McpToolDiscoveryError, match="protocol_error"):
        await StreamableHttpMcpToolDiscoveryAdapter().discover("https://mcp.example/tools")


async def _resolved(*addresses):
    return tuple(ipaddress.ip_address(address) for address in addresses)


def _install_synchronizer_fakes(*, discovery, publish_result=None):
    outcomes: list[dict[str, object]] = []
    publications: list[dict[str, object]] = []
    attempts: list[dict[str, object]] = []

    class FakeStore:
        async def begin(self, command):
            attempts.append(
                {
                    "tenant_id": command.tenant_id,
                    "server_name": command.server_name,
                    "observed_generation": command.observed_generation,
                    "actor_id": command.actor_id,
                }
            )
            return {
                "started": True,
                "catalog_status": "syncing",
                "catalog_unavailable_reason": "refresh_required",
                "catalog_revision": 4,
                "catalog_discovered_count": 0,
                "catalog_selectable_count": 0,
                "catalog_sync_attempt": 2,
            }

        async def record_outcome(self, command, *, observed_attempt, reason):
            outcomes.append({"observed_attempt": observed_attempt, "reason": reason})
            return {
                "catalog_status": "unavailable",
                "catalog_unavailable_reason": reason,
                "catalog_revision": 4,
                "catalog_discovered_count": 0,
                "catalog_selectable_count": 0,
            }

        async def publish(self, command, *, observed_attempt, tools):
            publications.append({"observed_attempt": observed_attempt, "tools": tools})
            return publish_result or {
                "catalog_status": "available",
                "catalog_unavailable_reason": "",
                "catalog_revision": 8,
                "catalog_discovered_count": len(tools),
                "catalog_selectable_count": len(tools),
                "published": True,
            }

    store = FakeStore()
    return McpToolCatalogSynchronizer(discovery=discovery, store=store), outcomes, publications, attempts, store


@pytest.mark.asyncio
async def test_synchronizer_publishes_only_the_complete_multi_tool_manifest(monkeypatch):
    class CompleteDiscovery:
        async def discover(self, endpoint, *, static_headers=None):
            assert endpoint == "https://mcp.example/tools"
            return (_tool("search_docs"), _tool("get_doc"), _tool("write_doc", read_only=False))

    synchronizer, outcomes, publications, attempts, _ = _install_synchronizer_fakes(discovery=CompleteDiscovery())

    result = await synchronizer.synchronize(_command())

    assert result.status == "available"
    assert result.selectable_count == 3
    assert outcomes == []
    assert attempts == [{"tenant_id": "tenant-a", "server_name": "knowledge", "observed_generation": 7, "actor_id": "admin-a"}]
    assert [tool.remote_name for tool in publications[0]["tools"]] == ["search_docs", "get_doc", "write_doc"]
    assert publications[0]["observed_attempt"] == 2


@pytest.mark.asyncio
async def test_synchronizer_publishes_a_truthful_zero_tool_result(monkeypatch):
    class EmptyDiscovery:
        async def discover(self, endpoint, *, static_headers=None):
            return ()

    synchronizer, outcomes, publications, _, _ = _install_synchronizer_fakes(
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
        async def discover(self, endpoint, *, static_headers=None):
            raise McpToolDiscoveryError("transport_failure")

    synchronizer, outcomes, publications, _, _ = _install_synchronizer_fakes(discovery=FailingDiscovery())

    result = await synchronizer.synchronize(_command())

    assert result.status == "unavailable"
    assert result.reason == "transport_failure"
    assert publications == []
    assert outcomes[0]["reason"] == "transport_failure"
    assert outcomes[0]["observed_attempt"] == 2


@pytest.mark.asyncio
async def test_stale_generation_result_cannot_report_publication(monkeypatch):
    class CompleteDiscovery:
        async def discover(self, endpoint, *, static_headers=None):
            return (_tool("search_docs"),)

    synchronizer, _, publications, _, _ = _install_synchronizer_fakes(
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
        async def discover(self, endpoint, *, static_headers=None):
            raise AssertionError("an already claimed generation must not rediscover")

    synchronizer, outcomes, publications, _, store = _install_synchronizer_fakes(discovery=UnexpectedDiscovery())

    async def already_claimed(command):
        return {
            "started": False,
            "catalog_status": "unavailable",
            "catalog_unavailable_reason": "sync_in_progress",
            "catalog_revision": 8,
            "catalog_discovered_count": 2,
            "catalog_selectable_count": 2,
        }

    monkeypatch.setattr(store, "begin", already_claimed)

    result = await synchronizer.synchronize(_command())

    assert result.reason == "sync_in_progress"
    assert result.published is False
    assert outcomes == []
    assert publications == []


@pytest.mark.asyncio
async def test_credentialed_or_invalid_requests_stay_unavailable_without_discovery(monkeypatch):
    class UnexpectedDiscovery:
        async def discover(self, endpoint, *, static_headers=None):
            raise AssertionError("discovery must not run")

    synchronizer, outcomes, publications, _, _ = _install_synchronizer_fakes(discovery=UnexpectedDiscovery())

    credentialed = await synchronizer.synchronize(_command(credentialed=True))
    invalid = await synchronizer.synchronize(_command(endpoint="https://mcp.example/tools?token=secret"))

    assert credentialed.reason == "credentials_not_supported"
    assert invalid.reason == "invalid_endpoint"
    assert publications == []
    assert [row["reason"] for row in outcomes] == ["credentials_not_supported", "invalid_endpoint"]
    assert "mcp.example" not in str(invalid.public_payload())


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
        ("https://[fe80::1]/tools", False),
        ("https://[fc00::1]/tools", False),
        ("https://100.64.0.1/tools", False),
        ("http://100.64.0.1/tools", False),
        ("https://255.255.255.255/tools", False),
    ],
)
async def test_discovery_target_policy_accepts_only_public_https_or_private_rfc1918(endpoint, allowed):
    if allowed:
        assert await catalog._validated_discovery_endpoint(endpoint) == endpoint
    else:
        with pytest.raises(McpToolDiscoveryError, match="invalid_endpoint"):
            await catalog._validated_discovery_endpoint(endpoint)


@pytest.mark.asyncio
async def test_discovery_target_policy_rejects_localhost_and_mixed_dns_answers(monkeypatch):
    async def local_answer(hostname, port):
        return await _resolved("127.0.0.1")

    monkeypatch.setattr(catalog, "_resolve_discovery_addresses", local_answer)
    with pytest.raises(McpToolDiscoveryError, match="invalid_endpoint"):
        await catalog._validated_discovery_endpoint("https://localhost/tools")

    async def mixed_answer(hostname, port):
        return await _resolved("10.42.0.12", "8.8.8.8")

    monkeypatch.setattr(catalog, "_resolve_discovery_addresses", mixed_answer)
    with pytest.raises(McpToolDiscoveryError, match="invalid_endpoint"):
        await catalog._validated_discovery_endpoint("http://mcp.corp.example/tools")
    with pytest.raises(McpToolDiscoveryError, match="invalid_endpoint"):
        await catalog._validated_discovery_endpoint("https://mcp.corp.example/tools")


@pytest.mark.asyncio
async def test_discovery_does_not_follow_redirects_after_target_validation(monkeypatch):
    seen_urls: list[str] = []

    async def public_dns(hostname, port):
        return await _resolved("8.8.8.8")

    async def handler(request: httpx.Request) -> httpx.Response:
        seen_urls.append(str(request.url))
        return httpx.Response(302, headers={"location": "http://169.254.169.254/latest/meta-data"})

    real_client = httpx.AsyncClient
    monkeypatch.setattr(catalog, "_resolve_discovery_addresses", public_dns)
    monkeypatch.setattr(
        catalog.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )

    with pytest.raises(McpToolDiscoveryError, match="protocol_error"):
        await StreamableHttpMcpToolDiscoveryAdapter().discover("https://mcp.example/tools")
    assert seen_urls == ["https://mcp.example/tools"]


@pytest.mark.asyncio
async def test_cancelled_discovery_records_retryable_outcome_before_lease_expiry():
    class CancelledDiscovery:
        async def discover(self, endpoint, *, static_headers=None):
            raise asyncio.CancelledError()

    synchronizer, outcomes, publications, _, _ = _install_synchronizer_fakes(discovery=CancelledDiscovery())

    with pytest.raises(asyncio.CancelledError):
        await synchronizer.synchronize(_command())

    assert outcomes == [{"observed_attempt": 2, "reason": "discovery_aborted"}]
    assert publications == []
